#pragma once

#include <stddef.h>
#include <stdint.h>

#include "telemetry.h"

namespace sensor_logic {

namespace config {

constexpr uint32_t kDistanceMinMm = 20U;
constexpr uint32_t kDistanceMaxMm = 4000U;
constexpr uint32_t kUltrasonicFreshMs = 1000U;
constexpr uint32_t kUltrasonicBaselineResetMs = 3000U;
constexpr uint32_t kVisionFreshMs = 1000U;
constexpr uint32_t kNetworkFreshMs = 2500U;

constexpr size_t kBaselineWindow = 3U;
constexpr uint32_t kBaselineStableSpanMm = 20U;
constexpr size_t kMedianWindow = 5U;

// These are explicit prototype rules, not learned model probabilities.
constexpr int32_t kWaterAdvisoryMm = 50;
constexpr int32_t kWaterWarningMm = 100;
constexpr int32_t kWaterDangerMm = 180;
constexpr int32_t kRiseRateWarningMmS = 25;

}  // namespace config

enum class AlarmLevel : uint8_t {
  kSafe = 0U,
  kAdvisory = 1U,
  kWarning = 2U,
  kCritical = 3U,
  kFault = 4U,
};

enum HealthFlag : uint32_t {
  kHealthUltrasonicOk = 1U << 0U,
  kHealthOpenMvOk = 1U << 1U,
  kHealthPowerOk = 1U << 2U,
  kHealthNetworkOk = 1U << 3U,
};

static_assert(kHealthUltrasonicOk == kTelemetryHealthUltrasonicOk,
              "local and wire ultrasonic health bits must match");

struct SensorState {
  uint32_t baseline_candidates[config::kBaselineWindow]{};
  size_t baseline_candidate_count{};
  size_t baseline_candidate_next{};

  uint32_t filter_samples[config::kMedianWindow]{};
  size_t filter_count{};
  size_t filter_next{};

  bool baseline_ready{};
  uint32_t baseline_distance_mm{};
  bool filtered_ready{};
  int64_t filtered_distance_q8{};
  uint32_t filtered_distance_mm{};
  int32_t water_rise_mm{};
  int32_t rise_rate_mm_s{};
  int32_t previous_rise_mm{};
  uint32_t previous_rise_ms{};
  bool previous_rise_ready{};
  bool ultrasonic_healthy{};
  bool ultrasonic_seen{};
  uint32_t last_ultrasonic_ms{};

  bool vision_seen{};
  uint32_t last_vision_ms{};
  bool person_detected{};
  bool person_in_zone{};

  bool network_seen{};
  uint32_t last_network_ms{};
  bool wifi_connected{};
  bool server_reachable{};
};

void reset(SensorState *state);
bool accept_distance(SensorState *state, uint32_t now_ms,
                     uint32_t distance_mm);
void note_timeout(SensorState *state, uint32_t now_ms);
void note_hardware_fault(SensorState *state);
void accept_vision(SensorState *state, uint32_t now_ms,
                   bool person_detected, bool person_in_zone);
void accept_network(SensorState *state, uint32_t now_ms,
                    bool wifi_connected, bool server_reachable);
void tick(SensorState *state, uint32_t now_ms);

// now_ms is also the device uptime carried by the legacy TEL contract. The
// caller owns sequence allocation so a future sensor task remains the only
// writer of the counter.
TelemetryFrame snapshot(const SensorState &state, uint32_t now_ms,
                        uint32_t sequence = 0U);

}  // namespace sensor_logic
