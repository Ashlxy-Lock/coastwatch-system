#pragma once

#include <cstddef>
#include <cstdint>

#include "app_config.h"

namespace coastwatch {

enum class AlarmLevel : std::uint8_t {
  kSafe = 0U,
  kAdvisory = 1U,
  kWarning = 2U,
  kCritical = 3U,
  kFault = 4U,
};

enum HealthFlag : std::uint32_t {
  kHealthUltrasonicOk = 1U << 0U,
  kHealthOpenMvOk = 1U << 1U,
  kHealthPowerOk = 1U << 2U,
  kHealthNetworkOk = 1U << 3U,
};

struct TelemetrySnapshot {
  std::uint32_t distance_mm{};
  std::int32_t water_rise_mm{};
  std::int32_t rise_rate_mm_s{};
  bool person_detected{};
  AlarmLevel alarm{AlarmLevel::kFault};
  std::uint32_t health_flags{};
};

enum class PinStartupAction : std::uint8_t {
  kWait = 0U,
  kClaimTrigger,
  kArmEchoCapture,
};

struct PinSafetyGate {
  bool trigger_claimed{};
  bool low_window_started{};
  std::uint32_t low_since_ms{};
  bool armed{};
};

enum class EchoRecoveryAction : std::uint8_t {
  kReady = 0U,
  kWaitForLow,
  kRecovered,
};

// An HC-SR04-style ECHO can remain high briefly after a missed/late echo.
// Keep TRIG safely claimed low while this gate waits for ECHO to return low;
// an ECHO anomaly alone must not tear down the already verified pin setup.
struct EchoRecoveryGate {
  bool waiting_for_low{};
};

struct SensorState {
  std::uint32_t baseline_candidates[config::kBaselineWindow]{};
  std::size_t baseline_candidate_count{};
  std::size_t baseline_candidate_next{};

  std::uint32_t filter_samples[config::kMedianWindow]{};
  std::size_t filter_count{};
  std::size_t filter_next{};

  bool baseline_ready{};
  std::uint32_t baseline_distance_mm{};
  bool filtered_ready{};
  std::int64_t filtered_distance_q8{};
  std::uint32_t filtered_distance_mm{};
  std::int32_t water_rise_mm{};
  std::int32_t rise_rate_mm_s{};
  std::int32_t previous_rise_mm{};
  std::uint32_t previous_rise_ms{};
  bool previous_rise_ready{};
  bool ultrasonic_healthy{};
  bool ultrasonic_seen{};
  std::uint32_t last_ultrasonic_ms{};

  bool vision_seen{};
  std::uint32_t last_vision_ms{};
  bool person_detected{};
  bool person_in_zone{};

  bool network_seen{};
  std::uint32_t last_network_ms{};
  bool wifi_connected{};
  bool server_reachable{};
};

void reset(SensorState* state);
void reset_pin_safety_gate(PinSafetyGate* gate);
void confirm_trigger_claim(PinSafetyGate* gate);
PinStartupAction observe_pin_safety_gate(PinSafetyGate* gate,
                                         std::uint32_t now_ms,
                                         bool echo_is_low);
void begin_echo_recovery(EchoRecoveryGate* gate);
EchoRecoveryAction observe_echo_recovery(EchoRecoveryGate* gate,
                                         bool echo_is_low);
bool accept_distance(SensorState* state, std::uint32_t now_ms,
                     std::uint32_t distance_mm);
void note_ultrasonic_timeout(SensorState* state, std::uint32_t now_ms);
void note_ultrasonic_hardware_fault(SensorState* state);
void accept_vision(SensorState* state, std::uint32_t now_ms,
                   bool person_detected, bool person_in_zone);
void accept_network(SensorState* state, std::uint32_t now_ms,
                    bool wifi_connected, bool server_reachable);
void tick(SensorState* state, std::uint32_t now_ms);
TelemetrySnapshot snapshot(const SensorState& state, std::uint32_t now_ms);

}  // namespace coastwatch
