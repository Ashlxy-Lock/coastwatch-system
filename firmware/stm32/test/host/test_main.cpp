#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "coastwatch_logic.h"
#include "protocol.h"

namespace {

int failures = 0;

#define CHECK(condition)                                                       \
  do {                                                                         \
    if (!(condition)) {                                                        \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__,           \
                   #condition);                                                \
      ++failures;                                                              \
    }                                                                          \
  } while (false)

std::string frame_for(const char* payload) {
  char frame[coastwatch::protocol::kMaxFrameBytes + 1U]{};
  const std::uint8_t checksum =
      coastwatch::protocol::xor_checksum(payload, std::strlen(payload));
  std::snprintf(frame, sizeof(frame), "$%s*%02X", payload,
                static_cast<unsigned int>(checksum));
  return frame;
}

void make_ready(coastwatch::SensorState* state, std::uint32_t start_ms,
                std::uint32_t distance_mm = 800U) {
  coastwatch::reset(state);
  coastwatch::accept_vision(state, start_ms, false, false);
  CHECK(coastwatch::accept_distance(state, start_ms, distance_mm));
  CHECK(coastwatch::accept_distance(state, start_ms + 100U, distance_mm + 4U));
  CHECK(coastwatch::accept_distance(state, start_ms + 200U, distance_mm - 3U));
}

void test_stable_baseline_and_health() {
  coastwatch::SensorState state{};
  coastwatch::reset(&state);
  CHECK(coastwatch::snapshot(state, 0U).alarm ==
        coastwatch::AlarmLevel::kFault);

  coastwatch::accept_vision(&state, 0U, false, false);
  coastwatch::accept_distance(&state, 0U, 800U);
  coastwatch::accept_distance(&state, 100U, 805U);
  CHECK(!state.baseline_ready);
  CHECK((coastwatch::snapshot(state, 100U).health_flags &
         coastwatch::kHealthUltrasonicOk) == 0U);

  coastwatch::accept_distance(&state, 200U, 798U);
  const coastwatch::TelemetrySnapshot value =
      coastwatch::snapshot(state, 200U);
  CHECK(state.baseline_ready);
  CHECK(state.baseline_distance_mm == 800U);
  CHECK(value.distance_mm == 800U);
  CHECK(value.water_rise_mm == 0);
  CHECK(value.rise_rate_mm_s == 0);
  CHECK(value.alarm == coastwatch::AlarmLevel::kSafe);
  CHECK((value.health_flags & coastwatch::kHealthUltrasonicOk) != 0U);
  CHECK((value.health_flags & coastwatch::kHealthOpenMvOk) != 0U);
  CHECK((value.health_flags & coastwatch::kHealthPowerOk) == 0U);
}

void test_two_stage_startup_safety_gate() {
  using coastwatch::PinStartupAction;
  coastwatch::PinSafetyGate gate{};
  coastwatch::reset_pin_safety_gate(&gate);
  CHECK(coastwatch::observe_pin_safety_gate(&gate, 0U, false) ==
        PinStartupAction::kWait);
  CHECK(coastwatch::observe_pin_safety_gate(&gate, 100U, true) ==
        PinStartupAction::kClaimTrigger);
  CHECK(coastwatch::observe_pin_safety_gate(&gate, 200U, true) ==
        PinStartupAction::kClaimTrigger);
  CHECK(!gate.low_window_started);

  // Only a successful hardware takeover starts the post-claim quiet stage;
  // time spent low before the takeover must not count toward the 50 ms window.
  coastwatch::confirm_trigger_claim(&gate);
  CHECK(gate.trigger_claimed);
  CHECK(coastwatch::observe_pin_safety_gate(&gate, 200U, true) ==
        PinStartupAction::kWait);
  CHECK(coastwatch::observe_pin_safety_gate(
            &gate, 200U + coastwatch::config::kPinSafetyConfirmMs - 1U,
            true) == PinStartupAction::kWait);
  CHECK(coastwatch::observe_pin_safety_gate(
            &gate, 200U + coastwatch::config::kPinSafetyConfirmMs, true) ==
        PinStartupAction::kArmEchoCapture);
  CHECK(gate.armed);

  // A high ECHO restarts the full quiet window but keeps TRIG claimed.
  coastwatch::reset_pin_safety_gate(&gate);
  coastwatch::confirm_trigger_claim(&gate);
  CHECK(coastwatch::observe_pin_safety_gate(&gate, 300U, true) ==
        PinStartupAction::kWait);
  CHECK(coastwatch::observe_pin_safety_gate(&gate, 325U, false) ==
        PinStartupAction::kWait);
  CHECK(gate.trigger_claimed);
  CHECK(coastwatch::observe_pin_safety_gate(&gate, 350U, true) ==
        PinStartupAction::kWait);
  CHECK(coastwatch::observe_pin_safety_gate(
            &gate, 350U + coastwatch::config::kPinSafetyConfirmMs - 1U,
            true) == PinStartupAction::kWait);
  CHECK(coastwatch::observe_pin_safety_gate(
            &gate, 350U + coastwatch::config::kPinSafetyConfirmMs, true) ==
        PinStartupAction::kArmEchoCapture);

  coastwatch::reset_pin_safety_gate(&gate);
  coastwatch::confirm_trigger_claim(&gate);
  CHECK(coastwatch::observe_pin_safety_gate(&gate, UINT32_MAX - 20U, true) ==
        PinStartupAction::kWait);
  CHECK(coastwatch::observe_pin_safety_gate(
            &gate, coastwatch::config::kPinSafetyConfirmMs - 21U, true) ==
        PinStartupAction::kArmEchoCapture);
}

void test_echo_recovery_holds_until_low_then_rearms() {
  using coastwatch::EchoRecoveryAction;
  coastwatch::EchoRecoveryGate gate{};

  CHECK(coastwatch::observe_echo_recovery(&gate, false) ==
        EchoRecoveryAction::kReady);
  coastwatch::begin_echo_recovery(&gate);
  CHECK(gate.waiting_for_low);
  CHECK(coastwatch::observe_echo_recovery(&gate, false) ==
        EchoRecoveryAction::kWaitForLow);
  CHECK(gate.waiting_for_low);
  CHECK(coastwatch::observe_echo_recovery(&gate, true) ==
        EchoRecoveryAction::kRecovered);
  CHECK(!gate.waiting_for_low);
  CHECK(coastwatch::observe_echo_recovery(&gate, true) ==
        EchoRecoveryAction::kReady);
}

void test_unstable_echo_is_not_baseline() {
  coastwatch::SensorState state{};
  coastwatch::reset(&state);
  coastwatch::accept_distance(&state, 0U, 800U);
  coastwatch::accept_distance(&state, 100U, 900U);
  coastwatch::accept_distance(&state, 200U, 800U);
  CHECK(!state.baseline_ready);
  coastwatch::accept_distance(&state, 300U, 805U);
  CHECK(!state.baseline_ready);
  coastwatch::accept_distance(&state, 400U, 810U);
  CHECK(state.baseline_ready);
  CHECK(state.baseline_distance_mm == 805U);
}

void test_timeout_grace_then_zeros_measurements_and_resets_baseline() {
  coastwatch::SensorState state{};
  make_ready(&state, 0U, 900U);
  CHECK(state.baseline_ready);
  coastwatch::note_ultrasonic_timeout(&state, 300U);
  const coastwatch::TelemetrySnapshot transient =
      coastwatch::snapshot(state, 300U);
  CHECK(transient.distance_mm == 900U);
  CHECK((transient.health_flags & coastwatch::kHealthUltrasonicOk) != 0U);

  coastwatch::tick(
      &state, 200U + coastwatch::config::kUltrasonicFreshMs + 1U);
  const coastwatch::TelemetrySnapshot failed = coastwatch::snapshot(
      state, 200U + coastwatch::config::kUltrasonicFreshMs + 1U);
  CHECK(failed.distance_mm == 0U);
  CHECK(failed.water_rise_mm == 0);
  CHECK(failed.rise_rate_mm_s == 0);
  CHECK((failed.health_flags & coastwatch::kHealthUltrasonicOk) == 0U);
  CHECK(failed.alarm == coastwatch::AlarmLevel::kFault);

  coastwatch::tick(
      &state, 200U + coastwatch::config::kUltrasonicBaselineResetMs + 1U);
  CHECK(!state.baseline_ready);
}

void test_hardware_fault_invalidates_immediately() {
  coastwatch::SensorState state{};
  make_ready(&state, 0U, 900U);
  CHECK((coastwatch::snapshot(state, 200U).health_flags &
         coastwatch::kHealthUltrasonicOk) != 0U);

  coastwatch::note_ultrasonic_hardware_fault(&state);
  const coastwatch::TelemetrySnapshot failed = coastwatch::snapshot(state, 201U);
  CHECK(failed.distance_mm == 0U);
  CHECK((failed.health_flags & coastwatch::kHealthUltrasonicOk) == 0U);
  CHECK(!state.ultrasonic_seen);
  CHECK(!state.baseline_ready);
}

void test_vision_timeout_is_fail_closed_but_network_is_not_a_gate() {
  coastwatch::SensorState state{};
  make_ready(&state, 0U);
  coastwatch::accept_network(&state, 200U, false, false);
  coastwatch::TelemetrySnapshot current = coastwatch::snapshot(state, 200U);
  CHECK(current.alarm == coastwatch::AlarmLevel::kSafe);
  CHECK((current.health_flags & coastwatch::kHealthNetworkOk) == 0U);

  coastwatch::accept_distance(
      &state, coastwatch::config::kVisionFreshMs + 1U, 800U);
  current = coastwatch::snapshot(state, coastwatch::config::kVisionFreshMs + 1U);
  CHECK((current.health_flags & coastwatch::kHealthUltrasonicOk) != 0U);
  CHECK((current.health_flags & coastwatch::kHealthOpenMvOk) == 0U);
  CHECK(!current.person_detected);
  CHECK(current.alarm == coastwatch::AlarmLevel::kFault);
}

void test_alarm_policy_and_tick_wrap() {
  coastwatch::SensorState state{};
  make_ready(&state, 0U);
  state.filtered_distance_mm = 620U;
  state.water_rise_mm = 180;
  coastwatch::accept_vision(&state, 200U, true, true);
  CHECK(coastwatch::snapshot(state, 200U).alarm ==
        coastwatch::AlarmLevel::kCritical);

  coastwatch::reset(&state);
  const std::uint32_t near_wrap = UINT32_MAX - 100U;
  coastwatch::accept_vision(&state, near_wrap, false, false);
  coastwatch::accept_distance(&state, near_wrap, 700U);
  coastwatch::accept_distance(&state, near_wrap + 50U, 702U);
  coastwatch::accept_distance(&state, 25U, 699U);
  CHECK((coastwatch::snapshot(state, 50U).health_flags &
         coastwatch::kHealthUltrasonicOk) != 0U);
}

void test_protocol_frames() {
  coastwatch::protocol::VisionFrame vision{};
  std::string frame = frame_for("VIS,17,1,90,0,0,1");
  CHECK(coastwatch::protocol::parse_vision(frame.c_str(), &vision) ==
        coastwatch::protocol::ParseResult::kOk);
  CHECK(vision.sequence == 17U);
  CHECK(vision.person_detected);
  CHECK(vision.score == 90U);
  CHECK(vision.in_zone);

  frame.back() = frame.back() == '0' ? '1' : '0';
  CHECK(coastwatch::protocol::parse_vision(frame.c_str(), &vision) ==
        coastwatch::protocol::ParseResult::kBadChecksum);

  frame = frame_for("VIS,18,0,50,0,0,0");
  CHECK(coastwatch::protocol::parse_vision(frame.c_str(), &vision) ==
        coastwatch::protocol::ParseResult::kOutOfRange);

  coastwatch::protocol::NetworkFrame network{};
  CHECK(coastwatch::protocol::parse_network(
            "$NET,1,1,-55,1785398400*7F\r\n", &network) ==
        coastwatch::protocol::ParseResult::kOk);
  CHECK(network.wifi_connected);
  CHECK(network.server_reachable);
  CHECK(network.rssi == -55);

  frame = frame_for("NET,0,1,-127,0");
  CHECK(coastwatch::protocol::parse_network(frame.c_str(), &network) ==
        coastwatch::protocol::ParseResult::kOutOfRange);
}

void test_telemetry_compatibility_and_line_resync() {
  coastwatch::TelemetrySnapshot telemetry{};
  telemetry.distance_mm = 815U;
  telemetry.water_rise_mm = 126;
  telemetry.rise_rate_mm_s = 21;
  telemetry.person_detected = true;
  telemetry.alarm = coastwatch::AlarmLevel::kCritical;
  telemetry.health_flags = 7U;
  char encoded[coastwatch::protocol::kMaxFrameBytes + 1U]{};
  CHECK(coastwatch::protocol::build_telemetry(encoded, sizeof(encoded), 42U,
                                              123456U, telemetry));
  CHECK(std::strcmp(encoded, "$TEL,42,123456,815,126,21,1,3,7*63\n") == 0);

  coastwatch::protocol::LineAccumulator accumulator{};
  char decoded[coastwatch::protocol::kMaxFrameBytes + 1U]{};
  const std::string stream = "noise" + frame_for("NET,0,0,-127,0") + "\r\n";
  coastwatch::protocol::FeedResult result =
      coastwatch::protocol::FeedResult::kNone;
  for (char byte : stream) {
    result = coastwatch::protocol::feed(&accumulator, byte, decoded,
                                        sizeof(decoded));
  }
  CHECK(result == coastwatch::protocol::FeedResult::kFrameReady);
  CHECK(std::strcmp(decoded, frame_for("NET,0,0,-127,0").c_str()) == 0);

  std::string overflow = "$";
  overflow.append(coastwatch::protocol::kMaxFrameBytes + 5U, 'X');
  bool dropped = false;
  for (char byte : overflow) {
    dropped = coastwatch::protocol::feed(&accumulator, byte, decoded,
                                         sizeof(decoded)) ==
                  coastwatch::protocol::FeedResult::kDropped ||
              dropped;
  }
  CHECK(dropped);
  const std::string recovery = frame_for("NET,1,1,-55,1") + "\n";
  for (char byte : recovery) {
    result = coastwatch::protocol::feed(&accumulator, byte, decoded,
                                        sizeof(decoded));
  }
  CHECK(result == coastwatch::protocol::FeedResult::kFrameReady);
}

}  // namespace

int main() {
  test_two_stage_startup_safety_gate();
  test_echo_recovery_holds_until_low_then_rearms();
  test_stable_baseline_and_health();
  test_unstable_echo_is_not_baseline();
  test_timeout_grace_then_zeros_measurements_and_resets_baseline();
  test_hardware_fault_invalidates_immediately();
  test_vision_timeout_is_fail_closed_but_network_is_not_a_gate();
  test_alarm_policy_and_tick_wrap();
  test_protocol_frames();
  test_telemetry_compatibility_and_line_resync();
  if (failures != 0) {
    std::fprintf(stderr, "%d host test(s) failed\n", failures);
    return 1;
  }
  std::puts("All STM32 pure-logic host tests passed.");
  return 0;
}
