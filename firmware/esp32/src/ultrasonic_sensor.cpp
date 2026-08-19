#include "ultrasonic_sensor.h"

#include "driver/gpio.h"

namespace {

constexpr uint32_t kMaximumTriggerPulseUs = 1000U;

}  // namespace

UltrasonicSensor::UltrasonicSensor(const UltrasonicSensorConfig &config)
    : config_(config) {}

bool UltrasonicSensor::configurationValid() const {
  if (config_.trigger_pin < 0 || config_.echo_pin < 0 ||
      config_.trigger_pin >= GPIO_NUM_MAX ||
      config_.echo_pin >= GPIO_NUM_MAX ||
      !GPIO_IS_VALID_OUTPUT_GPIO(config_.trigger_pin) ||
      !GPIO_IS_VALID_GPIO(config_.echo_pin) ||
      config_.trigger_pin == config_.echo_pin) {
    return false;
  }
  if (config_.ping_interval_ms == 0U || config_.echo_quiet_ms == 0U ||
      config_.echo_timeout_us == 0U || config_.trigger_pulse_us == 0U ||
      config_.trigger_pulse_us > kMaximumTriggerPulseUs ||
      config_.healthy_freshness_ms == 0U ||
      config_.minimum_distance_mm == 0U ||
      config_.minimum_distance_mm > config_.maximum_distance_mm) {
    return false;
  }
  const uint64_t interval_us =
      static_cast<uint64_t>(config_.ping_interval_ms) * 1000ULL;
  return static_cast<uint64_t>(config_.echo_timeout_us) < interval_us;
}

bool UltrasonicSensor::deadlineReached(uint32_t now_ms,
                                       uint32_t deadline_ms) const {
  return static_cast<int32_t>(now_ms - deadline_ms) >= 0;
}

void UltrasonicSensor::configureSafeInputs() {
  if (!configurationValid()) {
    return;
  }
  if (echo_interrupt_attached_) {
    detachInterrupt(config_.echo_pin);
    echo_interrupt_attached_ = false;
  }

  // INPUT_PULLDOWN is intentional for both pins. TRIG is not changed to an
  // output until poll() has verified that an external source is not holding
  // it high. ECHO must already have been level-shifted to the 3.3 V domain.
  pinMode(config_.trigger_pin, INPUT_PULLDOWN);
  pinMode(config_.echo_pin, INPUT_PULLDOWN);
}

UltrasonicSensorResult UltrasonicSensor::begin() { return reset(); }

UltrasonicSensorResult UltrasonicSensor::reset() {
  if (configurationValid()) {
    configureSafeInputs();
  }

  portENTER_CRITICAL(&capture_mux_);
  capture_phase_ = CapturePhase::kIdle;
  echo_rise_us_ = 0U;
  captured_pulse_us_ = 0U;
  captured_sequence_ = 0U;
  portEXIT_CRITICAL(&capture_mux_);

  begun_ = true;
  trigger_claimed_ = false;
  quiet_window_started_ = false;
  have_valid_sample_ = false;
  quiet_low_since_ms_ = 0U;
  next_ping_ms_ = 0U;
  ping_started_us_ = 0U;
  consumed_capture_sequence_ = 0U;
  last_pulse_width_us_ = 0U;
  last_distance_mm_ = 0U;
  last_valid_sample_ms_ = 0U;
  valid_sample_sequence_ = 0U;
  timeout_count_ = 0U;
  fault_ = UltrasonicSensorFault::kNone;
  state_ = UltrasonicSensorState::kSafeInputs;

  if (!configurationValid()) {
    fault_ = UltrasonicSensorFault::kInvalidConfiguration;
    state_ = UltrasonicSensorState::kFault;
    return makeResult(UltrasonicSensorEvent::kFault);
  }
  return makeResult(UltrasonicSensorEvent::kNone);
}

void UltrasonicSensor::latchFault(UltrasonicSensorFault fault) {
  if (fault_ != UltrasonicSensorFault::kNone) {
    return;
  }
  fault_ = fault;
  state_ = UltrasonicSensorState::kFault;

  if (echo_interrupt_attached_) {
    detachInterrupt(config_.echo_pin);
    echo_interrupt_attached_ = false;
  }
  setCapturePhase(CapturePhase::kIdle);

  // Return to high impedance after a possible wiring conflict. Do not keep
  // retrying against an externally driven TRIG line; reset() is required.
  pinMode(config_.trigger_pin, INPUT_PULLDOWN);
  pinMode(config_.echo_pin, INPUT_PULLDOWN);
  trigger_claimed_ = false;
  quiet_window_started_ = false;
}

bool UltrasonicSensor::claimTrigger(uint32_t now_ms) {
  // TRIG is still a high-impedance pulled-down input at this point. Refuse to
  // drive it if another board or a wiring error is holding the line high.
  if (digitalRead(config_.trigger_pin) != LOW) {
    latchFault(UltrasonicSensorFault::kTriggerHeldHigh);
    return false;
  }

  digitalWrite(config_.trigger_pin, LOW);
  pinMode(config_.trigger_pin, OUTPUT);
  if (digitalRead(config_.trigger_pin) != LOW) {
    latchFault(UltrasonicSensorFault::kTriggerDriveConflict);
    return false;
  }

  trigger_claimed_ = true;
  quiet_window_started_ = true;
  quiet_low_since_ms_ = now_ms;
  state_ = UltrasonicSensorState::kQuieting;
  return true;
}

bool UltrasonicSensor::armEchoCapture(uint32_t now_ms) {
  if (!trigger_claimed_ || digitalRead(config_.trigger_pin) != LOW) {
    latchFault(UltrasonicSensorFault::kTriggerDriveConflict);
    return false;
  }

  setCapturePhase(CapturePhase::kIdle);
  attachInterruptArg(config_.echo_pin, &UltrasonicSensor::echoInterruptThunk,
                     this, CHANGE);
  echo_interrupt_attached_ = true;

  // An edge can occur between the final quiet-window sample and interrupt
  // installation. Treat a newly high ECHO as recoverable, not as a pulse.
  if (digitalRead(config_.echo_pin) != LOW) {
    startRecovery(now_ms);
    return false;
  }

  state_ = UltrasonicSensorState::kArmed;
  next_ping_ms_ = now_ms + config_.ping_interval_ms;
  return true;
}

void UltrasonicSensor::setCapturePhase(CapturePhase phase) {
  portENTER_CRITICAL(&capture_mux_);
  capture_phase_ = phase;
  portEXIT_CRITICAL(&capture_mux_);
}

bool UltrasonicSensor::copyCapture(uint32_t *sequence,
                                   uint32_t *pulse_width_us,
                                   CapturePhase *phase) const {
  if (sequence == nullptr || pulse_width_us == nullptr || phase == nullptr) {
    return false;
  }
  portENTER_CRITICAL(&capture_mux_);
  *sequence = captured_sequence_;
  *pulse_width_us = captured_pulse_us_;
  *phase = capture_phase_;
  portEXIT_CRITICAL(&capture_mux_);
  return true;
}

void UltrasonicSensor::startRecovery(uint32_t now_ms) {
  setCapturePhase(CapturePhase::kIdle);
  digitalWrite(config_.trigger_pin, LOW);
  if (digitalRead(config_.trigger_pin) != LOW) {
    latchFault(UltrasonicSensorFault::kTriggerDriveConflict);
    return;
  }
  state_ = UltrasonicSensorState::kRecovering;
  next_ping_ms_ = now_ms + config_.ping_interval_ms;
}

bool UltrasonicSensor::startPing(uint32_t now_ms, uint32_t now_us) {
  if (digitalRead(config_.trigger_pin) != LOW) {
    latchFault(UltrasonicSensorFault::kTriggerDriveConflict);
    return false;
  }
  if (digitalRead(config_.echo_pin) != LOW) {
    ++timeout_count_;
    startRecovery(now_ms);
    return false;
  }

  portENTER_CRITICAL(&capture_mux_);
  capture_phase_ = CapturePhase::kWaitRise;
  portEXIT_CRITICAL(&capture_mux_);
  ping_started_us_ = now_us;

  // This is the only deliberate busy wait in the driver. Ten microseconds is
  // the sensor's trigger requirement; all echo waiting and recovery happens in
  // later poll() calls.
  digitalWrite(config_.trigger_pin, HIGH);
  delayMicroseconds(config_.trigger_pulse_us);
  digitalWrite(config_.trigger_pin, LOW);
  if (digitalRead(config_.trigger_pin) != LOW) {
    latchFault(UltrasonicSensorFault::kTriggerDriveConflict);
    return false;
  }

  CapturePhase phase = CapturePhase::kIdle;
  uint32_t ignored_sequence = 0U;
  uint32_t ignored_pulse = 0U;
  (void)copyCapture(&ignored_sequence, &ignored_pulse, &phase);
  state_ = phase == CapturePhase::kWaitFall
               ? UltrasonicSensorState::kWaitingForFall
               : UltrasonicSensorState::kWaitingForRise;
  return true;
}

UltrasonicSensorResult UltrasonicSensor::poll() {
  if (!begun_) {
    return makeResult(UltrasonicSensorEvent::kNone);
  }
  if (fault_ != UltrasonicSensorFault::kNone) {
    return makeResult(UltrasonicSensorEvent::kFault);
  }

  const uint32_t now_ms = millis();
  const uint32_t now_us = micros();

  if (!trigger_claimed_) {
    if (digitalRead(config_.trigger_pin) != LOW) {
      latchFault(UltrasonicSensorFault::kTriggerHeldHigh);
      return makeResult(UltrasonicSensorEvent::kFault);
    }
    if (digitalRead(config_.echo_pin) != LOW) {
      state_ = UltrasonicSensorState::kSafeInputs;
      return makeResult(UltrasonicSensorEvent::kNone);
    }
    (void)claimTrigger(now_ms);
    return makeResult(fault_ == UltrasonicSensorFault::kNone
                          ? UltrasonicSensorEvent::kNone
                          : UltrasonicSensorEvent::kFault);
  }

  if (digitalRead(config_.trigger_pin) != LOW) {
    latchFault(UltrasonicSensorFault::kTriggerDriveConflict);
    return makeResult(UltrasonicSensorEvent::kFault);
  }

  if (!echo_interrupt_attached_) {
    state_ = UltrasonicSensorState::kQuieting;
    if (digitalRead(config_.echo_pin) != LOW) {
      quiet_window_started_ = false;
      return makeResult(UltrasonicSensorEvent::kNone);
    }
    if (!quiet_window_started_) {
      quiet_window_started_ = true;
      quiet_low_since_ms_ = now_ms;
      return makeResult(UltrasonicSensorEvent::kNone);
    }
    if (static_cast<uint32_t>(now_ms - quiet_low_since_ms_) <
        config_.echo_quiet_ms) {
      return makeResult(UltrasonicSensorEvent::kNone);
    }
    if (armEchoCapture(now_ms)) {
      return makeResult(UltrasonicSensorEvent::kArmed);
    }
    return makeResult(fault_ == UltrasonicSensorFault::kNone
                          ? UltrasonicSensorEvent::kTimeout
                          : UltrasonicSensorEvent::kFault);
  }

  uint32_t capture_sequence = 0U;
  uint32_t pulse_width_us = 0U;
  CapturePhase capture_phase = CapturePhase::kIdle;
  (void)copyCapture(&capture_sequence, &pulse_width_us, &capture_phase);

  if (capture_sequence != consumed_capture_sequence_) {
    consumed_capture_sequence_ = capture_sequence;
    last_pulse_width_us_ = pulse_width_us;
    const uint32_t distance_mm =
        ultrasonic_sensor::pulseWidthToDistanceMm(pulse_width_us);
    state_ = UltrasonicSensorState::kArmed;
    next_ping_ms_ = now_ms + config_.ping_interval_ms;

    if (distance_mm < config_.minimum_distance_mm ||
        distance_mm > config_.maximum_distance_mm) {
      return makeResult(UltrasonicSensorEvent::kOutOfRange);
    }

    last_distance_mm_ = distance_mm;
    last_valid_sample_ms_ = now_ms;
    have_valid_sample_ = true;
    ++valid_sample_sequence_;
    return makeResult(UltrasonicSensorEvent::kSample);
  }

  if (state_ == UltrasonicSensorState::kRecovering) {
    if (digitalRead(config_.echo_pin) == LOW) {
      state_ = UltrasonicSensorState::kArmed;
      next_ping_ms_ = now_ms + config_.ping_interval_ms;
      return makeResult(UltrasonicSensorEvent::kRecovered);
    }
    return makeResult(UltrasonicSensorEvent::kNone);
  }

  if (capture_phase == CapturePhase::kWaitRise ||
      capture_phase == CapturePhase::kWaitFall) {
    state_ = capture_phase == CapturePhase::kWaitFall
                 ? UltrasonicSensorState::kWaitingForFall
                 : UltrasonicSensorState::kWaitingForRise;
    if (static_cast<uint32_t>(now_us - ping_started_us_) >=
        config_.echo_timeout_us) {
      ++timeout_count_;
      startRecovery(now_ms);
      return makeResult(fault_ == UltrasonicSensorFault::kNone
                            ? UltrasonicSensorEvent::kTimeout
                            : UltrasonicSensorEvent::kFault);
    }
    return makeResult(UltrasonicSensorEvent::kNone);
  }

  state_ = UltrasonicSensorState::kArmed;
  if (!deadlineReached(now_ms, next_ping_ms_)) {
    return makeResult(UltrasonicSensorEvent::kNone);
  }

  const uint32_t timeouts_before = timeout_count_;
  if (!startPing(now_ms, now_us)) {
    if (fault_ != UltrasonicSensorFault::kNone) {
      return makeResult(UltrasonicSensorEvent::kFault);
    }
    if (timeout_count_ != timeouts_before) {
      return makeResult(UltrasonicSensorEvent::kTimeout);
    }
  }
  return makeResult(UltrasonicSensorEvent::kNone);
}

UltrasonicSensorResult UltrasonicSensor::makeResult(
    UltrasonicSensorEvent event) const {
  UltrasonicSensorResult output{};
  output.state = state_;
  output.fault = fault_;
  output.event = event;
  output.pulse_width_us = last_pulse_width_us_;
  output.distance_mm = last_distance_mm_;
  output.sample_sequence = valid_sample_sequence_;
  output.timeout_count = timeout_count_;
  output.armed = echo_interrupt_attached_ &&
                 fault_ == UltrasonicSensorFault::kNone;
  output.healthy = healthy();
  output.sample_ready = event == UltrasonicSensorEvent::kSample;
  if (have_valid_sample_) {
    output.last_valid_age_ms = millis() - last_valid_sample_ms_;
  }
  return output;
}

UltrasonicSensorResult UltrasonicSensor::result() const {
  return makeResult(UltrasonicSensorEvent::kNone);
}

bool UltrasonicSensor::healthy() const {
  return begun_ && fault_ == UltrasonicSensorFault::kNone &&
         have_valid_sample_ &&
         static_cast<uint32_t>(millis() - last_valid_sample_ms_) <=
             config_.healthy_freshness_ms;
}

void IRAM_ATTR UltrasonicSensor::echoInterruptThunk(void *argument) {
  if (argument != nullptr) {
    static_cast<UltrasonicSensor *>(argument)->handleEchoInterrupt();
  }
}

void IRAM_ATTR UltrasonicSensor::handleEchoInterrupt() {
  // The ISR only timestamps ECHO edges and publishes a completed pulse. It
  // never changes GPIO modes, calculates distance, logs, or waits.
  const uint32_t now_us = micros();
  const int level = gpio_get_level(static_cast<gpio_num_t>(config_.echo_pin));

  portENTER_CRITICAL_ISR(&capture_mux_);
  if (level != 0 && capture_phase_ == CapturePhase::kWaitRise) {
    echo_rise_us_ = now_us;
    capture_phase_ = CapturePhase::kWaitFall;
  } else if (level == 0 && capture_phase_ == CapturePhase::kWaitFall) {
    captured_pulse_us_ = now_us - echo_rise_us_;
    ++captured_sequence_;
    capture_phase_ = CapturePhase::kIdle;
  }
  portEXIT_CRITICAL_ISR(&capture_mux_);
}
