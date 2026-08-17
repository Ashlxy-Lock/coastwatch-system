#pragma once

#include <Arduino.h>
#include <Wire.h>

enum class TouchEventType : uint8_t {
  kNone = 0,
  kPressed,
  kMoved,
  kReleased,
};

struct TouchPoint {
  uint16_t x{0};
  uint16_t y{0};
  uint16_t raw_x{0};
  uint16_t raw_y{0};
};

struct TouchRegion {
  uint16_t x{0};
  uint16_t y{0};
  uint16_t width{0};
  uint16_t height{0};

  constexpr TouchRegion() = default;
  constexpr TouchRegion(uint16_t x_value, uint16_t y_value,
                        uint16_t width_value, uint16_t height_value)
      : x(x_value),
        y(y_value),
        width(width_value),
        height(height_value) {}
};

struct TouchEvent {
  TouchEventType type{TouchEventType::kNone};
  TouchPoint point{};
  uint8_t point_count{0};
  uint32_t timestamp_ms{0};

  bool inside(const TouchRegion &region) const;
};

// Minimal FT5x06-family polling driver for the capacitive panel fitted to the
// TK043F1509 adapter. It deliberately performs only reads after probing the
// controller, so a missing or different touch panel cannot alter its state.
class Ft5x06Touch {
 public:
  Ft5x06Touch();

  bool begin(int sda_pin, int scl_pin, uint8_t address = 0x38,
             uint32_t clock_hz = 400000U);
  bool poll(TouchEvent *event);

  bool ready() const { return ready_; }
  uint8_t address() const { return address_; }
  uint32_t readErrors() const { return read_errors_; }

  static const char *eventTypeName(TouchEventType type);

 private:
  enum class SampleResult : uint8_t {
    kBusError = 0,
    kInvalidData,
    kNoTouch,
    kTouch,
  };

  bool probe();
  bool recoverBus(uint32_t now_ms);
  bool readRegisters(uint8_t first_register, uint8_t *data, size_t length);
  SampleResult readSample(TouchPoint *point, uint8_t *point_count);
  static TouchPoint mapToDisplay(uint16_t raw_x, uint16_t raw_y);
  static uint32_t distanceSquared(const TouchPoint &left,
                                  const TouchPoint &right);
  static TouchPoint average(const TouchPoint &left, const TouchPoint &right);
  static TouchPoint smooth(const TouchPoint &previous,
                           const TouchPoint &sample);

  TwoWire wire_;
  int sda_pin_{-1};
  int scl_pin_{-1};
  uint32_t clock_hz_{400000U};
  uint8_t address_{0x38};
  bool bus_started_{false};
  bool ready_{false};
  uint32_t read_errors_{0};
  uint32_t consecutive_read_errors_{0};
  uint32_t recovery_attempts_{0};
  uint32_t last_recovery_attempt_ms_{0};
  uint32_t last_recovery_log_ms_{0};
  uint32_t invalid_samples_{0};
  uint32_t last_error_log_ms_{0};
  uint32_t last_invalid_log_ms_{0};
  uint32_t last_sample_ms_{0};
  uint32_t last_event_ms_{0};
  uint8_t last_status_{0};
  uint16_t last_raw_x_{0};
  uint16_t last_raw_y_{0};
  bool pressed_{false};
  bool waiting_for_clear_{false};
  uint8_t clear_samples_{0};
  uint8_t candidate_samples_{0};
  uint8_t release_samples_{0};
  TouchPoint candidate_point_{};
  TouchPoint stable_point_{};
  TouchPoint last_event_point_{};
};
