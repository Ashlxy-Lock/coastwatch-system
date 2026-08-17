#pragma once

#include <stdint.h>

struct TelemetryFrame {
  uint32_t seq;
  uint32_t uptime_ms;
  uint32_t distance_mm;
  int32_t water_rise_mm;
  int32_t rise_rate_mm_s;
  bool person_detected;
  uint8_t alarm_level;
  uint32_t health_flags;
};

// Keep the LCD meaning explicit and testable.  distance_mm is the physical
// sensor-to-target gap; water_rise_mm is the relative level change from the
// reference established by the STM32.  Presenting these through named fields
// avoids accidentally promoting the raw gap as a water/sea level again.
namespace ultrasonic_ui {

constexpr char kLevelChangeLabel[] = "LEVEL CHANGE";
constexpr char kSensorGapLabel[] = "SENSOR GAP";

struct Presentation {
  int32_t level_change_mm;
  uint32_t sensor_gap_mm;
};

constexpr Presentation presentation(const TelemetryFrame &telemetry) {
  return {telemetry.water_rise_mm, telemetry.distance_mm};
}

}  // namespace ultrasonic_ui

// STM32 health_flags bit 0 is the authoritative indication that the
// ultrasonic value in a TEL frame is valid.  Keep the received timestamp on
// the ESP32 as well: a formerly healthy value must never stay visible after
// the UART stream stops.
constexpr uint32_t kTelemetryHealthUltrasonicOk = 1U << 0U;
constexpr uint32_t kTelemetryUltrasonicMinimumMm = 20U;
constexpr uint32_t kTelemetryUltrasonicMaximumMm = 4000U;

struct TelemetrySnapshot {
  TelemetryFrame latest;
  uint32_t received_at_ms;
  bool has_telemetry;
  bool telemetry_fresh;
  bool ultrasonic_available;
};

inline TelemetrySnapshot makeTelemetrySnapshot(
    const TelemetryFrame &latest, bool has_telemetry,
    uint32_t received_at_ms, uint32_t now_ms, uint32_t maximum_age_ms) {
  TelemetrySnapshot snapshot{latest, received_at_ms, has_telemetry, false,
                             false};
  const bool fresh =
      has_telemetry &&
      static_cast<uint32_t>(now_ms - received_at_ms) <= maximum_age_ms;
  snapshot.telemetry_fresh = fresh;
  snapshot.ultrasonic_available =
      fresh &&
      (latest.health_flags & kTelemetryHealthUltrasonicOk) != 0U &&
      latest.distance_mm >= kTelemetryUltrasonicMinimumMm &&
      latest.distance_mm <= kTelemetryUltrasonicMaximumMm;
  return snapshot;
}
