#include "touch.h"

#include <algorithm>

namespace {

constexpr uint8_t kTouchPointStatusRegister = 0x02;
constexpr uint8_t kChipIdRegister = 0xA8;
constexpr uint8_t kFirmwareIdRegister = 0xA6;

constexpr uint16_t kRawWidth = 480;
constexpr uint16_t kRawHeight = 800;
constexpr uint16_t kDisplayWidth = 800;
constexpr uint16_t kDisplayHeight = 480;

constexpr uint32_t kSampleIntervalMs = 20U;
constexpr uint32_t kMoveEventIntervalMs = 60U;
constexpr uint32_t kDiagnosticLogIntervalMs = 10000U;
constexpr uint32_t kRecoveryIntervalMs = 1000U;
constexpr uint32_t kCandidateRadiusSquared = 30U * 30U;
constexpr uint32_t kMoveRadiusSquared = 4U * 4U;
constexpr uint8_t kPressSamplesRequired = 2U;
constexpr uint8_t kReleaseSamplesRequired = 2U;
constexpr uint8_t kRecoveryErrorThreshold = 3U;
constexpr uint32_t kWireTimeoutMs = 25U;
constexpr uint32_t kRecoveryWireTimeoutMs = 5U;

}  // namespace

bool TouchEvent::inside(const TouchRegion &region) const {
  if (region.width == 0U || region.height == 0U) {
    return false;
  }
  const uint32_t right = static_cast<uint32_t>(region.x) + region.width;
  const uint32_t bottom = static_cast<uint32_t>(region.y) + region.height;
  return point.x >= region.x && point.y >= region.y && point.x < right &&
         point.y < bottom;
}

Ft5x06Touch::Ft5x06Touch() : wire_(1) {}

bool Ft5x06Touch::begin(int sda_pin, int scl_pin, uint8_t address,
                       uint32_t clock_hz) {
  sda_pin_ = sda_pin;
  scl_pin_ = scl_pin;
  clock_hz_ = clock_hz;
  address_ = address;
  ready_ = false;
  pressed_ = false;
  waiting_for_clear_ = false;
  clear_samples_ = 0U;
  candidate_samples_ = 0U;
  release_samples_ = 0U;
  read_errors_ = 0U;
  consecutive_read_errors_ = 0U;
  recovery_attempts_ = 0U;
  last_recovery_attempt_ms_ = 0U;
  last_recovery_log_ms_ = 0U;
  invalid_samples_ = 0U;
  last_error_log_ms_ = 0U;
  last_invalid_log_ms_ = 0U;

  if (!bus_started_) {
    wire_.setTimeOut(kWireTimeoutMs);
    if (!wire_.begin(sda_pin, scl_pin, clock_hz)) {
      Serial.printf("[TOUCH] ERROR I2C begin failed sda=GPIO%d scl=GPIO%d\n",
                    sda_pin, scl_pin);
      return false;
    }
    bus_started_ = true;
  } else {
    wire_.setClock(clock_hz);
  }

  if (!probe()) {
    Serial.printf(
        "[TOUCH] not detected addr=0x%02X sda=GPIO%d scl=GPIO%d; "
        "display/network continue\n",
        address_, sda_pin, scl_pin);
    return false;
  }

  ready_ = true;
  last_sample_ms_ = millis();
  Serial.printf(
      "[TOUCH] ready FT5x06-family addr=0x%02X bus=%luHz poll=50Hz\n",
      address_, static_cast<unsigned long>(clock_hz));
  return true;
}

bool Ft5x06Touch::recoverBus(uint32_t now_ms) {
  if (static_cast<uint32_t>(now_ms - last_recovery_attempt_ms_) <
      kRecoveryIntervalMs) {
    return false;
  }
  last_recovery_attempt_ms_ = now_ms;
  ++recovery_attempts_;

  const bool log_attempt =
      recovery_attempts_ == 1U ||
      static_cast<uint32_t>(now_ms - last_recovery_log_ms_) >=
          kDiagnosticLogIntervalMs;
  if (log_attempt) {
    last_recovery_log_ms_ = now_ms;
    Serial.printf("[TOUCH] recovering I2C attempt=%lu\n",
                  static_cast<unsigned long>(recovery_attempts_));
  }

  if (bus_started_) {
    wire_.end();
    bus_started_ = false;
  }

  // Keep a failed recovery tightly bounded. Normal polling returns to the
  // original timeout after the controller has been reprobed.
  wire_.setTimeOut(kRecoveryWireTimeoutMs);
  if (!wire_.begin(sda_pin_, scl_pin_, clock_hz_)) {
    wire_.setTimeOut(kWireTimeoutMs);
    if (log_attempt) {
      Serial.println("[TOUCH] recovery failed: I2C begin");
    }
    return false;
  }
  bus_started_ = true;

  const bool probe_ok = probe();
  wire_.setTimeOut(kWireTimeoutMs);
  if (!probe_ok) {
    if (log_attempt) {
      Serial.println("[TOUCH] recovery failed: controller not found");
    }
    return false;
  }

  consecutive_read_errors_ = 0U;
  candidate_samples_ = 0U;
  release_samples_ = 0U;
  pressed_ = false;
  ready_ = true;
  waiting_for_clear_ = true;
  clear_samples_ = 0U;
  Serial.printf("[TOUCH] recovery succeeded attempt=%lu\n",
                static_cast<unsigned long>(recovery_attempts_));
  return true;
}

bool Ft5x06Touch::probe() {
  wire_.beginTransmission(address_);
  if (wire_.endTransmission(true) != 0U) {
    return false;
  }

  uint8_t chip_id = 0U;
  uint8_t firmware_id = 0U;
  if (!readRegisters(kChipIdRegister, &chip_id, 1U) ||
      !readRegisters(kFirmwareIdRegister, &firmware_id, 1U)) {
    return false;
  }

  uint8_t point_status = 0U;
  if (!readRegisters(kTouchPointStatusRegister, &point_status, 1U)) {
    return false;
  }

  Serial.printf(
      "[TOUCH] probe ack chip_id=0x%02X firmware_id=0x%02X status=0x%02X\n",
      chip_id, firmware_id, point_status);
  return true;
}

bool Ft5x06Touch::readRegisters(uint8_t first_register, uint8_t *data,
                                size_t length) {
  if (data == nullptr || length == 0U || length > 32U) {
    return false;
  }

  wire_.beginTransmission(address_);
  wire_.write(first_register);
  if (wire_.endTransmission(false) != 0U) {
    return false;
  }

  const size_t received = wire_.requestFrom(
      static_cast<uint16_t>(address_), length, static_cast<bool>(true));
  if (received != length) {
    while (wire_.available() > 0) {
      wire_.read();
    }
    return false;
  }

  for (size_t index = 0; index < length; ++index) {
    const int value = wire_.read();
    if (value < 0) {
      return false;
    }
    data[index] = static_cast<uint8_t>(value);
  }
  return true;
}

Ft5x06Touch::SampleResult Ft5x06Touch::readSample(TouchPoint *point,
                                                  uint8_t *point_count) {
  uint8_t count = 0U;
  if (!readRegisters(kTouchPointStatusRegister, &count, 1U)) {
    return SampleResult::kBusError;
  }
  last_status_ = count;
  count &= 0x0FU;
  if (point_count != nullptr) {
    *point_count = count;
  }
  // Espressif's FT5x06 driver treats both zero and out-of-range point counts
  // as an empty sample. Some FT5436 firmware returns a reserved count while
  // idle; that is not an I2C transaction failure.
  if (count == 0U || count > 5U) {
    return SampleResult::kNoTouch;
  }
  if (point == nullptr) {
    return SampleResult::kInvalidData;
  }

  // The official driver reads six bytes for every reported point, starting at
  // TOUCH1_XH (0x03). Reading the complete report keeps the controller's
  // register pointer and the point-count snapshot consistent for multi-touch.
  uint8_t data[6U * 5U]{};
  const size_t report_length = static_cast<size_t>(count) * 6U;
  if (!readRegisters(0x03, data, report_length)) {
    return SampleResult::kBusError;
  }
  const uint16_t raw_x =
      static_cast<uint16_t>(((data[0] & 0x0FU) << 8U) | data[1]);
  const uint16_t raw_y =
      static_cast<uint16_t>(((data[2] & 0x0FU) << 8U) | data[3]);
  last_raw_x_ = raw_x;
  last_raw_y_ = raw_y;
  if (raw_x >= kRawWidth || raw_y >= kRawHeight) {
    return SampleResult::kInvalidData;
  }

  *point = mapToDisplay(raw_x, raw_y);
  return SampleResult::kTouch;
}

TouchPoint Ft5x06Touch::mapToDisplay(uint16_t raw_x, uint16_t raw_y) {
  // This is the same transform as the vendor sample's FT5x06 settings:
  // mirror raw X first, then swap X/Y for the 800x480 landscape display.
  TouchPoint point{};
  point.raw_x = raw_x;
  point.raw_y = raw_y;
  point.x = std::min<uint16_t>(raw_y, kDisplayWidth - 1U);
  point.y = std::min<uint16_t>(kRawWidth - 1U - raw_x,
                               kDisplayHeight - 1U);
  return point;
}

uint32_t Ft5x06Touch::distanceSquared(const TouchPoint &left,
                                      const TouchPoint &right) {
  const int32_t dx = static_cast<int32_t>(left.x) - right.x;
  const int32_t dy = static_cast<int32_t>(left.y) - right.y;
  return static_cast<uint32_t>(dx * dx + dy * dy);
}

TouchPoint Ft5x06Touch::average(const TouchPoint &left,
                               const TouchPoint &right) {
  TouchPoint point{};
  point.x = static_cast<uint16_t>((left.x + right.x) / 2U);
  point.y = static_cast<uint16_t>((left.y + right.y) / 2U);
  point.raw_x = static_cast<uint16_t>((left.raw_x + right.raw_x) / 2U);
  point.raw_y = static_cast<uint16_t>((left.raw_y + right.raw_y) / 2U);
  return point;
}

TouchPoint Ft5x06Touch::smooth(const TouchPoint &previous,
                              const TouchPoint &sample) {
  TouchPoint point{};
  point.x = static_cast<uint16_t>((previous.x * 3U + sample.x + 2U) / 4U);
  point.y = static_cast<uint16_t>((previous.y * 3U + sample.y + 2U) / 4U);
  point.raw_x = static_cast<uint16_t>(
      (previous.raw_x * 3U + sample.raw_x + 2U) / 4U);
  point.raw_y = static_cast<uint16_t>(
      (previous.raw_y * 3U + sample.raw_y + 2U) / 4U);
  return point;
}

bool Ft5x06Touch::poll(TouchEvent *event) {
  if (event == nullptr) {
    return false;
  }
  *event = TouchEvent{};
  const uint32_t now_ms = millis();
  if (!ready_) {
    // A brief power-up or connector transient must not disable the touchscreen
    // for the whole boot. Reuse the bounded recovery path until it probes.
    recoverBus(now_ms);
    return false;
  }

  if (static_cast<uint32_t>(now_ms - last_sample_ms_) < kSampleIntervalMs) {
    return false;
  }
  last_sample_ms_ = now_ms;

  TouchPoint sample{};
  uint8_t point_count = 0U;
  const SampleResult result = readSample(&sample, &point_count);
  if (result == SampleResult::kBusError) {
    ++read_errors_;
    ++consecutive_read_errors_;
    if (read_errors_ == 1U ||
        static_cast<uint32_t>(now_ms - last_error_log_ms_) >=
            kDiagnosticLogIntervalMs) {
      last_error_log_ms_ = now_ms;
      Serial.printf(
          "[TOUCH] WARN I2C transaction failed total=%lu status=0x%02X\n",
          static_cast<unsigned long>(read_errors_), last_status_);
    }
    if (consecutive_read_errors_ >= kRecoveryErrorThreshold) {
      recoverBus(now_ms);
    }
    return false;
  }
  // Any completed I2C sample proves the bus is responsive, including an idle
  // report or a syntactically invalid controller payload.
  consecutive_read_errors_ = 0U;
  if (result == SampleResult::kInvalidData) {
    ++invalid_samples_;
    if (invalid_samples_ == 1U ||
        static_cast<uint32_t>(now_ms - last_invalid_log_ms_) >=
            kDiagnosticLogIntervalMs) {
      last_invalid_log_ms_ = now_ms;
      Serial.printf(
          "[TOUCH] WARN invalid sample total=%lu status=0x%02X raw=(%u,%u)\n",
          static_cast<unsigned long>(invalid_samples_), last_status_,
          last_raw_x_, last_raw_y_);
    }
    return false;
  }

  // Bus recovery can happen while a finger is still down. Suppress all input
  // until two clean idle samples are seen, otherwise that same physical press
  // would be emitted again and could duplicate a password character/action.
  if (waiting_for_clear_) {
    if (result == SampleResult::kNoTouch) {
      if (++clear_samples_ >= kReleaseSamplesRequired) {
        waiting_for_clear_ = false;
        clear_samples_ = 0U;
        Serial.println("[TOUCH] input re-armed after idle");
      }
    } else {
      clear_samples_ = 0U;
    }
    return false;
  }

  if (result == SampleResult::kNoTouch) {
    candidate_samples_ = 0U;
    if (!pressed_) {
      return false;
    }
    if (++release_samples_ < kReleaseSamplesRequired) {
      return false;
    }

    pressed_ = false;
    release_samples_ = 0U;
    event->type = TouchEventType::kReleased;
    event->point = stable_point_;
    event->point_count = 0U;
    event->timestamp_ms = now_ms;
    last_event_ms_ = now_ms;
    last_event_point_ = stable_point_;
    return true;
  }

  release_samples_ = 0U;
  if (!pressed_) {
    if (candidate_samples_ == 0U ||
        distanceSquared(candidate_point_, sample) >
            kCandidateRadiusSquared) {
      candidate_point_ = sample;
      candidate_samples_ = 1U;
      return false;
    }

    candidate_point_ = average(candidate_point_, sample);
    if (++candidate_samples_ < kPressSamplesRequired) {
      return false;
    }

    pressed_ = true;
    candidate_samples_ = 0U;
    stable_point_ = candidate_point_;
    last_event_point_ = stable_point_;
    last_event_ms_ = now_ms;
    event->type = TouchEventType::kPressed;
    event->point = stable_point_;
    event->point_count = point_count;
    event->timestamp_ms = now_ms;
    return true;
  }

  stable_point_ = smooth(stable_point_, sample);
  if (distanceSquared(stable_point_, last_event_point_) <
          kMoveRadiusSquared ||
      static_cast<uint32_t>(now_ms - last_event_ms_) <
          kMoveEventIntervalMs) {
    return false;
  }

  last_event_point_ = stable_point_;
  last_event_ms_ = now_ms;
  event->type = TouchEventType::kMoved;
  event->point = stable_point_;
  event->point_count = point_count;
  event->timestamp_ms = now_ms;
  return true;
}

const char *Ft5x06Touch::eventTypeName(TouchEventType type) {
  switch (type) {
    case TouchEventType::kPressed:
      return "pressed";
    case TouchEventType::kMoved:
      return "moved";
    case TouchEventType::kReleased:
      return "released";
    case TouchEventType::kNone:
    default:
      return "none";
  }
}
