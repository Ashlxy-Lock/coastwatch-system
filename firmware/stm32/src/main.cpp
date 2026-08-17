#include "stm32f1xx_hal.h"

#include <cstddef>
#include <cstdint>
#include <cstring>

#include "app_config.h"
#include "coastwatch_logic.h"
#include "protocol.h"

namespace {

using coastwatch::SensorState;
using coastwatch::TelemetrySnapshot;

UART_HandleTypeDef g_uart2{};
UART_HandleTypeDef g_uart3{};
TIM_HandleTypeDef g_timer2{};

constexpr std::uint16_t kRingCapacity = 256U;
struct ByteRing {
  std::uint8_t bytes[kRingCapacity]{};
  volatile std::uint16_t head{};
  volatile std::uint16_t tail{};
};

ByteRing g_esp32_rx{};
ByteRing g_openmv_rx{};
std::uint8_t g_uart2_rx_byte{};
std::uint8_t g_uart3_rx_byte{};

coastwatch::protocol::LineAccumulator g_net_line{};
coastwatch::protocol::LineAccumulator g_vis_line{};
SensorState g_sensor_state{};
coastwatch::PinSafetyGate g_pin_safety_gate{};
coastwatch::EchoRecoveryGate g_echo_recovery_gate{};
bool g_ultrasonic_pins_armed{};
bool g_trigger_drive_fault_latched{};

char g_telemetry_tx[coastwatch::protocol::kMaxFrameBytes + 1U]{};
volatile bool g_telemetry_tx_busy{};
std::uint32_t g_telemetry_sequence{};
std::uint32_t g_last_telemetry_ms{};

enum class EchoPhase : std::uint8_t {
  kIdle = 0U,
  kTriggerHigh,
  kWaitRise,
  kWaitFall,
  kSampleReady,
};

volatile EchoPhase g_echo_phase{EchoPhase::kIdle};
volatile std::uint16_t g_echo_rise_us{};
volatile std::uint16_t g_echo_pulse_us{};
std::uint16_t g_trigger_started_us{};
std::uint32_t g_echo_phase_started_ms{};
std::uint32_t g_next_ping_ms{};

bool g_alarm_initialized{};
coastwatch::AlarmLevel g_last_alarm{coastwatch::AlarmLevel::kFault};

void error_handler();
void system_clock_config();
void gpio_init();
void timer2_init();
void uart2_init();
void uart3_init();
void configure_ultrasonic_safe_inputs();

bool deadline_reached(std::uint32_t now_ms, std::uint32_t deadline_ms) {
  return static_cast<std::int32_t>(now_ms - deadline_ms) >= 0;
}

std::uint16_t timer_us() {
  return static_cast<std::uint16_t>(__HAL_TIM_GET_COUNTER(&g_timer2));
}

void ring_push_from_isr(ByteRing* ring, std::uint8_t byte) {
  const std::uint16_t next =
      static_cast<std::uint16_t>((ring->head + 1U) & (kRingCapacity - 1U));
  if (next == ring->tail) {
    return;
  }
  ring->bytes[ring->head] = byte;
  ring->head = next;
}

bool ring_pop(ByteRing* ring, std::uint8_t* byte) {
  if (ring->tail == ring->head) {
    return false;
  }
  *byte = ring->bytes[ring->tail];
  ring->tail =
      static_cast<std::uint16_t>((ring->tail + 1U) & (kRingCapacity - 1U));
  return true;
}

void drain_openmv(std::uint32_t now_ms) {
  std::uint8_t byte = 0U;
  char frame[coastwatch::protocol::kMaxFrameBytes + 1U]{};
  while (ring_pop(&g_openmv_rx, &byte)) {
    if (coastwatch::protocol::feed(&g_vis_line, static_cast<char>(byte), frame,
                                  sizeof(frame)) !=
        coastwatch::protocol::FeedResult::kFrameReady) {
      continue;
    }
    coastwatch::protocol::VisionFrame vision{};
    if (coastwatch::protocol::parse_vision(frame, &vision) ==
        coastwatch::protocol::ParseResult::kOk) {
      coastwatch::accept_vision(&g_sensor_state, now_ms,
                               vision.person_detected, vision.in_zone);
    }
  }
}

void drain_esp32(std::uint32_t now_ms) {
  std::uint8_t byte = 0U;
  char frame[coastwatch::protocol::kMaxFrameBytes + 1U]{};
  while (ring_pop(&g_esp32_rx, &byte)) {
    if (coastwatch::protocol::feed(&g_net_line, static_cast<char>(byte), frame,
                                  sizeof(frame)) !=
        coastwatch::protocol::FeedResult::kFrameReady) {
      continue;
    }
    coastwatch::protocol::NetworkFrame network{};
    if (coastwatch::protocol::parse_network(frame, &network) ==
        coastwatch::protocol::ParseResult::kOk) {
      coastwatch::accept_network(&g_sensor_state, now_ms,
                                network.wifi_connected,
                                network.server_reachable);
    }
  }
}

void disarm_ultrasonic_pins(std::uint32_t now_ms) {
  g_ultrasonic_pins_armed = false;
  g_echo_phase = EchoPhase::kIdle;
  HAL_NVIC_DisableIRQ(EXTI15_10_IRQn);
  __HAL_GPIO_EXTI_CLEAR_IT(GPIO_PIN_11);
  configure_ultrasonic_safe_inputs();
  coastwatch::reset_pin_safety_gate(&g_pin_safety_gate);
  g_echo_recovery_gate = coastwatch::EchoRecoveryGate{};
  coastwatch::note_ultrasonic_timeout(&g_sensor_state, now_ms);
}

void latch_trigger_drive_fault(std::uint32_t now_ms) {
  // A failed output readback can indicate a wiring conflict. Return both pins
  // to high-impedance inputs and do not repeatedly retry until the next reset.
  g_trigger_drive_fault_latched = true;
  coastwatch::note_ultrasonic_hardware_fault(&g_sensor_state);
  disarm_ultrasonic_pins(now_ms);
}

bool claim_ultrasonic_trigger(std::uint32_t now_ms) {
  // Claim TRIG as soon as ECHO is presently idle. Holding TRIG low prevents a
  // sensor-side pull-up from retriggering ECHO while its quiet window is timed.
  if (HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_11) != GPIO_PIN_RESET) {
    return false;
  }
  // PC10 is still a high-impedance input here. Refuse to drive it low if an
  // external source is holding it high; checking only after OUTPUT_PP would
  // detect the fault too late, after a possible electrical conflict.
  if (HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_10) != GPIO_PIN_RESET) {
    latch_trigger_drive_fault(now_ms);
    return false;
  }

  HAL_GPIO_WritePin(GPIOC, GPIO_PIN_10, GPIO_PIN_RESET);
  GPIO_InitTypeDef gpio{};
  gpio.Pin = GPIO_PIN_10;
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOC, &gpio);
  __DSB();
  if (HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_10) != GPIO_PIN_RESET) {
    latch_trigger_drive_fault(now_ms);
    return false;
  }

  coastwatch::confirm_trigger_claim(&g_pin_safety_gate);
  return true;
}

bool arm_ultrasonic_echo_capture(std::uint32_t now_ms) {
  if (!g_pin_safety_gate.trigger_claimed ||
      HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_10) != GPIO_PIN_RESET) {
    latch_trigger_drive_fault(now_ms);
    return false;
  }
  if (HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_11) != GPIO_PIN_RESET) {
    coastwatch::confirm_trigger_claim(&g_pin_safety_gate);
    return false;
  }

  // ECHO EXTI is installed only after TRIG has been held low throughout a
  // complete quiet window. Re-check once more before enabling its IRQ.
  GPIO_InitTypeDef gpio{};
  gpio.Pin = GPIO_PIN_11;
  gpio.Mode = GPIO_MODE_IT_RISING_FALLING;
  gpio.Pull = GPIO_PULLDOWN;
  HAL_GPIO_Init(GPIOC, &gpio);
  __HAL_GPIO_EXTI_CLEAR_IT(GPIO_PIN_11);
  if (HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_11) != GPIO_PIN_RESET) {
    coastwatch::confirm_trigger_claim(&g_pin_safety_gate);
    return false;
  }
  HAL_NVIC_EnableIRQ(EXTI15_10_IRQn);

  g_echo_phase = EchoPhase::kIdle;
  g_next_ping_ms = now_ms + coastwatch::config::kUltrasonicPeriodMs;
  g_echo_recovery_gate = coastwatch::EchoRecoveryGate{};
  g_ultrasonic_pins_armed = true;
  return true;
}

bool ultrasonic_pin_guard_task(std::uint32_t now_ms) {
  if (g_trigger_drive_fault_latched) {
    return false;
  }

  if (g_ultrasonic_pins_armed) {
    if (g_echo_phase != EchoPhase::kTriggerHigh &&
        HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_10) != GPIO_PIN_RESET) {
      latch_trigger_drive_fault(now_ms);
      return false;
    }
    // A late/stuck ECHO is recovered below while PC10 remains a verified-low
    // output. Do not repeatedly de-initialize and re-arm the pins merely
    // because the acoustic input is high.
    return true;
  }

  if (g_pin_safety_gate.trigger_claimed &&
      HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_10) != GPIO_PIN_RESET) {
    latch_trigger_drive_fault(now_ms);
    return false;
  }

  const bool echo_low =
      HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_11) == GPIO_PIN_RESET;
  switch (coastwatch::observe_pin_safety_gate(
      &g_pin_safety_gate, now_ms, echo_low)) {
    case coastwatch::PinStartupAction::kClaimTrigger:
      claim_ultrasonic_trigger(now_ms);
      return false;
    case coastwatch::PinStartupAction::kArmEchoCapture:
      return arm_ultrasonic_echo_capture(now_ms);
    case coastwatch::PinStartupAction::kWait:
    default:
      return false;
  }
}

void ultrasonic_task(std::uint32_t now_ms) {
  if (!ultrasonic_pin_guard_task(now_ms)) {
    return;
  }

  const bool echo_is_low =
      HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_11) == GPIO_PIN_RESET;
  switch (coastwatch::observe_echo_recovery(&g_echo_recovery_gate,
                                            echo_is_low)) {
    case coastwatch::EchoRecoveryAction::kWaitForLow:
      // TRIG stays driven low. The main loop continues polling, so recovery is
      // automatic as soon as ECHO returns to its idle level.
      return;
    case coastwatch::EchoRecoveryAction::kRecovered:
      g_echo_phase = EchoPhase::kIdle;
      g_next_ping_ms = now_ms + coastwatch::config::kUltrasonicPeriodMs;
      return;
    case coastwatch::EchoRecoveryAction::kReady:
    default:
      break;
  }

  if (g_echo_phase == EchoPhase::kSampleReady) {
    const std::uint16_t pulse_us = g_echo_pulse_us;
    g_echo_phase = EchoPhase::kIdle;
    g_next_ping_ms = now_ms + coastwatch::config::kUltrasonicPeriodMs;

    // HC-SR04 nominal conversion: distance_cm = echo_us / 58. Rounded mm.
    const std::uint32_t distance_mm =
        (static_cast<std::uint32_t>(pulse_us) * 10U + 29U) / 58U;
    coastwatch::accept_distance(&g_sensor_state, now_ms, distance_mm);
  }

  if ((g_echo_phase == EchoPhase::kWaitRise ||
       g_echo_phase == EchoPhase::kWaitFall) &&
      now_ms - g_echo_phase_started_ms >
          coastwatch::config::kEchoTimeoutMs) {
    g_echo_phase = EchoPhase::kIdle;
    HAL_GPIO_WritePin(GPIOC, GPIO_PIN_10, GPIO_PIN_RESET);
    coastwatch::begin_echo_recovery(&g_echo_recovery_gate);
    coastwatch::note_ultrasonic_timeout(&g_sensor_state, now_ms);
    return;
  }

  if (g_echo_phase == EchoPhase::kTriggerHigh &&
      static_cast<std::uint16_t>(timer_us() - g_trigger_started_us) >=
          coastwatch::config::kTriggerPulseUs) {
    g_echo_phase = EchoPhase::kWaitRise;
    HAL_GPIO_WritePin(GPIOC, GPIO_PIN_10, GPIO_PIN_RESET);
    if (HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_10) != GPIO_PIN_RESET) {
      latch_trigger_drive_fault(now_ms);
      return;
    }
  }

  if (g_echo_phase == EchoPhase::kIdle &&
      deadline_reached(now_ms, g_next_ping_ms)) {
    if (HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_11) != GPIO_PIN_RESET) {
      coastwatch::begin_echo_recovery(&g_echo_recovery_gate);
      coastwatch::note_ultrasonic_timeout(&g_sensor_state, now_ms);
      return;
    }
    g_echo_phase_started_ms = now_ms;
    g_trigger_started_us = timer_us();
    g_echo_phase = EchoPhase::kTriggerHigh;
    HAL_GPIO_WritePin(GPIOC, GPIO_PIN_10, GPIO_PIN_SET);
    if (HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_10) != GPIO_PIN_SET) {
      latch_trigger_drive_fault(now_ms);
    }
  }
}

void telemetry_task(std::uint32_t now_ms) {
  if (now_ms - g_last_telemetry_ms <
      coastwatch::config::kTelemetryPeriodMs) {
    return;
  }
  g_last_telemetry_ms = now_ms;
  if (g_telemetry_tx_busy) {
    return;
  }

  const TelemetrySnapshot current =
      coastwatch::snapshot(g_sensor_state, now_ms);
  if (!coastwatch::protocol::build_telemetry(
          g_telemetry_tx, sizeof(g_telemetry_tx), g_telemetry_sequence,
          now_ms, current)) {
    return;
  }
  g_telemetry_tx_busy = true;
  const HAL_StatusTypeDef started = HAL_UART_Transmit_IT(
      &g_uart2, reinterpret_cast<std::uint8_t*>(g_telemetry_tx),
      static_cast<std::uint16_t>(std::strlen(g_telemetry_tx)));
  if (started == HAL_OK) {
    ++g_telemetry_sequence;
  } else {
    g_telemetry_tx_busy = false;
  }
}

}  // namespace

// This is intentionally a no-op integration hook. A later board-specific
// module may override it to drive an electrically verified buzzer/light
// circuit. This firmware does not claim that any actuator is connected.
extern "C" __weak void Coastwatch_AlarmOutput(std::uint8_t level) {
  (void)level;
}

namespace {

void alarm_task(std::uint32_t now_ms) {
  const coastwatch::AlarmLevel level =
      coastwatch::snapshot(g_sensor_state, now_ms).alarm;
  if (!g_alarm_initialized || level != g_last_alarm) {
    g_last_alarm = level;
    g_alarm_initialized = true;
    Coastwatch_AlarmOutput(static_cast<std::uint8_t>(level));
  }
}

void system_clock_config() {
  RCC_OscInitTypeDef oscillator{};
  oscillator.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  oscillator.HSEState = RCC_HSE_ON;
  oscillator.HSEPredivValue = RCC_HSE_PREDIV_DIV1;
  oscillator.HSIState = RCC_HSI_ON;
  oscillator.PLL.PLLState = RCC_PLL_ON;
  oscillator.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  oscillator.PLL.PLLMUL = RCC_PLL_MUL9;
  if (HAL_RCC_OscConfig(&oscillator) != HAL_OK) {
    error_handler();
  }

  RCC_ClkInitTypeDef clocks{};
  clocks.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                     RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  clocks.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  clocks.AHBCLKDivider = RCC_SYSCLK_DIV1;
  clocks.APB1CLKDivider = RCC_HCLK_DIV2;
  clocks.APB2CLKDivider = RCC_HCLK_DIV1;
  if (HAL_RCC_ClockConfig(&clocks, FLASH_LATENCY_2) != HAL_OK) {
    error_handler();
  }
}

void gpio_init() {
  __HAL_RCC_AFIO_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();

  configure_ultrasonic_safe_inputs();
  HAL_NVIC_SetPriority(EXTI15_10_IRQn, 1U, 0U);
}

void configure_ultrasonic_safe_inputs() {
  // This is the permanent reset state. On STM32F1, GPIO_PULLDOWN also clears
  // the corresponding ODR bits while the pins remain high impedance inputs.
  HAL_GPIO_DeInit(GPIOC, GPIO_PIN_10 | GPIO_PIN_11);
  GPIO_InitTypeDef gpio{};
  gpio.Pin = GPIO_PIN_10 | GPIO_PIN_11;
  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pull = GPIO_PULLDOWN;
  HAL_GPIO_Init(GPIOC, &gpio);
}

void timer2_init() {
  __HAL_RCC_TIM2_CLK_ENABLE();
  // APB1 is 36 MHz, but its timer clock is doubled to 72 MHz. /72 = 1 MHz.
  g_timer2.Instance = TIM2;
  g_timer2.Init.Prescaler = 71U;
  g_timer2.Init.CounterMode = TIM_COUNTERMODE_UP;
  g_timer2.Init.Period = 0xFFFFU;
  g_timer2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  g_timer2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&g_timer2) != HAL_OK ||
      HAL_TIM_Base_Start(&g_timer2) != HAL_OK) {
    error_handler();
  }
}

void uart2_init() {
  __HAL_RCC_USART2_CLK_ENABLE();

  GPIO_InitTypeDef gpio{};
  gpio.Pin = GPIO_PIN_2;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOA, &gpio);
  gpio = GPIO_InitTypeDef{};
  gpio.Pin = GPIO_PIN_3;
  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOA, &gpio);

  g_uart2.Instance = USART2;
  g_uart2.Init.BaudRate = coastwatch::config::kUartBaud;
  g_uart2.Init.WordLength = UART_WORDLENGTH_8B;
  g_uart2.Init.StopBits = UART_STOPBITS_1;
  g_uart2.Init.Parity = UART_PARITY_NONE;
  g_uart2.Init.Mode = UART_MODE_TX_RX;
  g_uart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  g_uart2.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&g_uart2) != HAL_OK) {
    error_handler();
  }
  HAL_NVIC_SetPriority(USART2_IRQn, 2U, 0U);
  HAL_NVIC_EnableIRQ(USART2_IRQn);
}

void uart3_init() {
  __HAL_RCC_USART3_CLK_ENABLE();

  GPIO_InitTypeDef gpio{};
  gpio.Pin = GPIO_PIN_10;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOB, &gpio);
  gpio = GPIO_InitTypeDef{};
  gpio.Pin = GPIO_PIN_11;
  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOB, &gpio);

  g_uart3.Instance = USART3;
  g_uart3.Init.BaudRate = coastwatch::config::kUartBaud;
  g_uart3.Init.WordLength = UART_WORDLENGTH_8B;
  g_uart3.Init.StopBits = UART_STOPBITS_1;
  g_uart3.Init.Parity = UART_PARITY_NONE;
  g_uart3.Init.Mode = UART_MODE_TX_RX;
  g_uart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  g_uart3.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&g_uart3) != HAL_OK) {
    error_handler();
  }
  HAL_NVIC_SetPriority(USART3_IRQn, 2U, 0U);
  HAL_NVIC_EnableIRQ(USART3_IRQn);
}

void error_handler() {
  HAL_GPIO_WritePin(GPIOC, GPIO_PIN_10, GPIO_PIN_RESET);
  Coastwatch_AlarmOutput(
      static_cast<std::uint8_t>(coastwatch::AlarmLevel::kFault));
  __disable_irq();
  while (true) {
  }
}

}  // namespace

extern "C" int main(void) {
  HAL_Init();
  system_clock_config();
  gpio_init();
  timer2_init();
  uart2_init();
  uart3_init();
  coastwatch::reset(&g_sensor_state);
  coastwatch::reset_pin_safety_gate(&g_pin_safety_gate);

  if (HAL_UART_Receive_IT(&g_uart2, &g_uart2_rx_byte, 1U) != HAL_OK ||
      HAL_UART_Receive_IT(&g_uart3, &g_uart3_rx_byte, 1U) != HAL_OK) {
    error_handler();
  }

  g_next_ping_ms = HAL_GetTick() + 100U;
  g_last_telemetry_ms = HAL_GetTick();
  while (true) {
    const std::uint32_t now_ms = HAL_GetTick();
    ultrasonic_task(now_ms);
    drain_openmv(now_ms);
    drain_esp32(now_ms);
    coastwatch::tick(&g_sensor_state, now_ms);
    alarm_task(now_ms);
    telemetry_task(now_ms);
  }
}

extern "C" void HAL_GPIO_EXTI_Callback(std::uint16_t pin) {
  if (pin != GPIO_PIN_11) {
    return;
  }
  const GPIO_PinState echo = HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_11);
  const std::uint16_t now_us = timer_us();
  if (echo == GPIO_PIN_SET && g_echo_phase == EchoPhase::kWaitRise) {
    g_echo_rise_us = now_us;
    g_echo_phase = EchoPhase::kWaitFall;
  } else if (echo == GPIO_PIN_RESET &&
             g_echo_phase == EchoPhase::kWaitFall) {
    g_echo_pulse_us =
        static_cast<std::uint16_t>(now_us - g_echo_rise_us);
    g_echo_phase = EchoPhase::kSampleReady;
  }
}

extern "C" void HAL_UART_RxCpltCallback(UART_HandleTypeDef* uart) {
  if (uart == &g_uart2) {
    ring_push_from_isr(&g_esp32_rx, g_uart2_rx_byte);
    (void)HAL_UART_Receive_IT(&g_uart2, &g_uart2_rx_byte, 1U);
  } else if (uart == &g_uart3) {
    ring_push_from_isr(&g_openmv_rx, g_uart3_rx_byte);
    (void)HAL_UART_Receive_IT(&g_uart3, &g_uart3_rx_byte, 1U);
  }
}

extern "C" void HAL_UART_TxCpltCallback(UART_HandleTypeDef* uart) {
  if (uart == &g_uart2) {
    g_telemetry_tx_busy = false;
  }
}

extern "C" void HAL_UART_ErrorCallback(UART_HandleTypeDef* uart) {
  if (uart == &g_uart2) {
    (void)HAL_UART_Receive_IT(&g_uart2, &g_uart2_rx_byte, 1U);
  } else if (uart == &g_uart3) {
    (void)HAL_UART_Receive_IT(&g_uart3, &g_uart3_rx_byte, 1U);
  }
}

extern "C" void USART2_IRQHandler(void) { HAL_UART_IRQHandler(&g_uart2); }
extern "C" void USART3_IRQHandler(void) { HAL_UART_IRQHandler(&g_uart3); }
extern "C" void EXTI15_10_IRQHandler(void) {
  HAL_GPIO_EXTI_IRQHandler(GPIO_PIN_11);
}
extern "C" void SysTick_Handler(void) {
  HAL_IncTick();
  HAL_SYSTICK_IRQHandler();
}
