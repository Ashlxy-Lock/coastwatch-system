#pragma once

#include <Arduino.h>

#include <stdint.h>

// HC-SR04-style ECHO is commonly a 5 V signal. ESP32-S3 GPIO is not 5 V
// tolerant: ECHO must pass through a resistor divider or a unidirectional
// level shifter before it reaches echo_pin. This driver cannot detect or make
// an electrically unsafe connection safe.
struct UltrasonicSensorConfig {
  int trigger_pin{-1};
  int echo_pin{-1};
  uint32_t ping_interval_ms{100U};
  uint32_t echo_quiet_ms{50U};
  uint32_t echo_timeout_us{30000U};
  uint16_t trigger_pulse_us{10U};
  uint32_t healthy_freshness_ms{1000U};
  uint32_t minimum_distance_mm{20U};
  uint32_t maximum_distance_mm{4000U};
};

enum class UltrasonicSensorState : uint8_t {
  kUninitialized = 0U,
  kSafeInputs,
  kQuieting,
  kArmed,
  kWaitingForRise,
  kWaitingForFall,
  kRecovering,
  kFault,
};

enum class UltrasonicSensorFault : uint8_t {
  kNone = 0U,
  kInvalidConfiguration,
  kTriggerHeldHigh,
  kTriggerDriveConflict,
};

enum class UltrasonicSensorEvent : uint8_t {
  kNone = 0U,
  kArmed,
  kSample,
  kOutOfRange,
  kTimeout,
  kRecovered,
  kFault,
};

struct UltrasonicSensorResult {
  UltrasonicSensorState state{UltrasonicSensorState::kUninitialized};
  UltrasonicSensorFault fault{UltrasonicSensorFault::kNone};
  UltrasonicSensorEvent event{UltrasonicSensorEvent::kNone};
  uint32_t pulse_width_us{0U};
  uint32_t distance_mm{0U};
  uint32_t sample_sequence{0U};
  uint32_t timeout_count{0U};
  uint32_t last_valid_age_ms{UINT32_MAX};
  bool armed{false};
  bool healthy{false};
  bool sample_ready{false};
};

namespace ultrasonic_sensor {

// HC-SR04 nominal conversion: distance_cm = echo_us / 58. The integer result
// is rounded to the nearest millimetre.
constexpr uint32_t pulseWidthToDistanceMm(uint32_t pulse_width_us) {
  return (pulse_width_us * 10U + 29U) / 58U;
}

static_assert(pulseWidthToDistanceMm(580U) == 100U,
              "HC-SR04 pulse conversion changed");
static_assert(pulseWidthToDistanceMm(2900U) == 500U,
              "HC-SR04 pulse conversion changed");

}  // namespace ultrasonic_sensor

class UltrasonicSensor {
 public:
  explicit UltrasonicSensor(const UltrasonicSensorConfig &config);

  // begin() and reset() leave both signal pins as pulled-down inputs first.
  // poll() later claims TRIG only after observing that no external source is
  // holding it high, then requires an uninterrupted ECHO-low quiet window.
  UltrasonicSensorResult begin();
  UltrasonicSensorResult poll();
  UltrasonicSensorResult reset();

  UltrasonicSensorResult result() const;
  bool healthy() const;
  UltrasonicSensorFault fault() const { return fault_; }

 private:
  enum class CapturePhase : uint8_t {
    kIdle = 0U,
    kWaitRise,
    kWaitFall,
  };

  static void IRAM_ATTR echoInterruptThunk(void *argument);
  void IRAM_ATTR handleEchoInterrupt();

  bool configurationValid() const;
  bool deadlineReached(uint32_t now_ms, uint32_t deadline_ms) const;
  void configureSafeInputs();
  bool claimTrigger(uint32_t now_ms);
  bool armEchoCapture(uint32_t now_ms);
  bool startPing(uint32_t now_ms, uint32_t now_us);
  void startRecovery(uint32_t now_ms);
  void latchFault(UltrasonicSensorFault fault);
  bool copyCapture(uint32_t *sequence, uint32_t *pulse_width_us,
                   CapturePhase *phase) const;
  void setCapturePhase(CapturePhase phase);
  UltrasonicSensorResult makeResult(UltrasonicSensorEvent event) const;

  const UltrasonicSensorConfig config_;
  mutable portMUX_TYPE capture_mux_ = portMUX_INITIALIZER_UNLOCKED;
  volatile CapturePhase capture_phase_{CapturePhase::kIdle};
  volatile uint32_t echo_rise_us_{0U};
  volatile uint32_t captured_pulse_us_{0U};
  volatile uint32_t captured_sequence_{0U};

  UltrasonicSensorState state_{UltrasonicSensorState::kUninitialized};
  UltrasonicSensorFault fault_{UltrasonicSensorFault::kNone};
  bool begun_{false};
  bool trigger_claimed_{false};
  bool echo_interrupt_attached_{false};
  bool quiet_window_started_{false};
  bool have_valid_sample_{false};
  uint32_t quiet_low_since_ms_{0U};
  uint32_t next_ping_ms_{0U};
  uint32_t ping_started_us_{0U};
  uint32_t consumed_capture_sequence_{0U};
  uint32_t last_pulse_width_us_{0U};
  uint32_t last_distance_mm_{0U};
  uint32_t last_valid_sample_ms_{0U};
  uint32_t valid_sample_sequence_{0U};
  uint32_t timeout_count_{0U};
};
