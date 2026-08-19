#include <unity.h>

#include "sensor_logic.h"

void setUp() {}
void tearDown() {}

namespace {

using sensor_logic::AlarmLevel;
using sensor_logic::SensorState;

void make_ready(SensorState *state, uint32_t start_ms,
                uint32_t distance_mm = 800U) {
  sensor_logic::reset(state);
  sensor_logic::accept_vision(state, start_ms, false, false);
  TEST_ASSERT_TRUE(
      sensor_logic::accept_distance(state, start_ms, distance_mm));
  TEST_ASSERT_TRUE(sensor_logic::accept_distance(
      state, start_ms + 100U, distance_mm + 4U));
  TEST_ASSERT_TRUE(sensor_logic::accept_distance(
      state, start_ms + 200U, distance_mm - 3U));
}

void test_stable_baseline_builds_wire_compatible_snapshot() {
  SensorState state{};
  sensor_logic::reset(&state);
  sensor_logic::accept_vision(&state, 0U, false, false);

  TEST_ASSERT_TRUE(sensor_logic::accept_distance(&state, 0U, 800U));
  TEST_ASSERT_TRUE(sensor_logic::accept_distance(&state, 100U, 805U));
  TEST_ASSERT_FALSE(state.baseline_ready);
  TEST_ASSERT_TRUE(sensor_logic::accept_distance(&state, 200U, 798U));

  const TelemetryFrame frame = sensor_logic::snapshot(state, 200U, 42U);
  TEST_ASSERT_TRUE(state.baseline_ready);
  TEST_ASSERT_EQUAL_UINT32(800U, state.baseline_distance_mm);
  TEST_ASSERT_EQUAL_UINT32(42U, frame.seq);
  TEST_ASSERT_EQUAL_UINT32(200U, frame.uptime_ms);
  TEST_ASSERT_EQUAL_UINT32(800U, frame.distance_mm);
  TEST_ASSERT_EQUAL_INT32(0, frame.water_rise_mm);
  TEST_ASSERT_EQUAL_INT32(0, frame.rise_rate_mm_s);
  TEST_ASSERT_FALSE(frame.person_detected);
  TEST_ASSERT_EQUAL_UINT8(static_cast<uint8_t>(AlarmLevel::kSafe),
                          frame.alarm_level);
  TEST_ASSERT_BITS_HIGH(sensor_logic::kHealthUltrasonicOk |
                            sensor_logic::kHealthOpenMvOk,
                        frame.health_flags);
  TEST_ASSERT_BITS_LOW(sensor_logic::kHealthPowerOk |
                           sensor_logic::kHealthNetworkOk,
                       frame.health_flags);
}

void test_unstable_echoes_do_not_become_baseline() {
  SensorState state{};
  sensor_logic::reset(&state);
  sensor_logic::accept_distance(&state, 0U, 800U);
  sensor_logic::accept_distance(&state, 100U, 900U);
  sensor_logic::accept_distance(&state, 200U, 800U);
  TEST_ASSERT_FALSE(state.baseline_ready);
  sensor_logic::accept_distance(&state, 300U, 805U);
  TEST_ASSERT_FALSE(state.baseline_ready);
  sensor_logic::accept_distance(&state, 400U, 810U);
  TEST_ASSERT_TRUE(state.baseline_ready);
  TEST_ASSERT_EQUAL_UINT32(805U, state.baseline_distance_mm);
}

void test_median_then_q8_filter_updates_rise_and_rate() {
  SensorState state{};
  make_ready(&state, 0U);

  sensor_logic::accept_distance(&state, 300U, 700U);
  sensor_logic::accept_distance(&state, 400U, 700U);
  sensor_logic::accept_distance(&state, 500U, 700U);

  const TelemetryFrame frame = sensor_logic::snapshot(state, 500U, 7U);
  TEST_ASSERT_EQUAL_UINT32(780U, frame.distance_mm);
  TEST_ASSERT_EQUAL_INT32(20, frame.water_rise_mm);
  TEST_ASSERT_EQUAL_INT32(190, frame.rise_rate_mm_s);
  TEST_ASSERT_EQUAL_UINT8(static_cast<uint8_t>(AlarmLevel::kWarning),
                          frame.alarm_level);
}

void test_timeout_grace_then_zeroes_and_resets_reference() {
  SensorState state{};
  make_ready(&state, 0U, 900U);

  sensor_logic::note_timeout(&state, 300U);
  TelemetryFrame frame = sensor_logic::snapshot(state, 300U);
  TEST_ASSERT_EQUAL_UINT32(900U, frame.distance_mm);
  TEST_ASSERT_BITS_HIGH(sensor_logic::kHealthUltrasonicOk,
                        frame.health_flags);

  const uint32_t stale_ms =
      200U + sensor_logic::config::kUltrasonicFreshMs + 1U;
  sensor_logic::tick(&state, stale_ms);
  frame = sensor_logic::snapshot(state, stale_ms);
  TEST_ASSERT_EQUAL_UINT32(0U, frame.distance_mm);
  TEST_ASSERT_EQUAL_INT32(0, frame.water_rise_mm);
  TEST_ASSERT_EQUAL_INT32(0, frame.rise_rate_mm_s);
  TEST_ASSERT_BITS_LOW(sensor_logic::kHealthUltrasonicOk,
                       frame.health_flags);
  TEST_ASSERT_EQUAL_UINT8(static_cast<uint8_t>(AlarmLevel::kFault),
                          frame.alarm_level);

  const uint32_t reset_ms =
      200U + sensor_logic::config::kUltrasonicBaselineResetMs + 1U;
  sensor_logic::tick(&state, reset_ms);
  TEST_ASSERT_FALSE(state.baseline_ready);
  TEST_ASSERT_FALSE(state.filtered_ready);
}

void test_out_of_range_sample_is_rejected_without_refreshing_age() {
  SensorState state{};
  make_ready(&state, 0U);
  TEST_ASSERT_FALSE(sensor_logic::accept_distance(
      &state, 250U, sensor_logic::config::kDistanceMinMm - 1U));
  TEST_ASSERT_EQUAL_UINT32(200U, state.last_ultrasonic_ms);
  TEST_ASSERT_FALSE(sensor_logic::accept_distance(
      &state, 300U, sensor_logic::config::kDistanceMaxMm + 1U));
  TEST_ASSERT_EQUAL_UINT32(200U, state.last_ultrasonic_ms);
}

void test_hardware_fault_invalidates_immediately() {
  SensorState state{};
  make_ready(&state, 0U);
  sensor_logic::note_hardware_fault(&state);

  const TelemetryFrame frame = sensor_logic::snapshot(state, 201U);
  TEST_ASSERT_FALSE(state.ultrasonic_seen);
  TEST_ASSERT_FALSE(state.baseline_ready);
  TEST_ASSERT_EQUAL_UINT32(0U, frame.distance_mm);
  TEST_ASSERT_BITS_LOW(sensor_logic::kHealthUltrasonicOk,
                       frame.health_flags);
  TEST_ASSERT_EQUAL_UINT8(static_cast<uint8_t>(AlarmLevel::kFault),
                          frame.alarm_level);
}

void test_vision_is_fail_closed_and_network_is_health_only() {
  SensorState state{};
  make_ready(&state, 0U);
  sensor_logic::accept_network(&state, 200U, false, false);

  TelemetryFrame frame = sensor_logic::snapshot(state, 200U);
  TEST_ASSERT_EQUAL_UINT8(static_cast<uint8_t>(AlarmLevel::kSafe),
                          frame.alarm_level);
  TEST_ASSERT_BITS_LOW(sensor_logic::kHealthNetworkOk, frame.health_flags);

  sensor_logic::accept_network(&state, 300U, true, true);
  frame = sensor_logic::snapshot(state, 300U);
  TEST_ASSERT_BITS_HIGH(sensor_logic::kHealthNetworkOk, frame.health_flags);

  const uint32_t vision_stale_ms =
      sensor_logic::config::kVisionFreshMs + 1U;
  sensor_logic::accept_distance(&state, vision_stale_ms, 800U);
  frame = sensor_logic::snapshot(state, vision_stale_ms);
  TEST_ASSERT_BITS_HIGH(sensor_logic::kHealthUltrasonicOk,
                        frame.health_flags);
  TEST_ASSERT_BITS_LOW(sensor_logic::kHealthOpenMvOk, frame.health_flags);
  TEST_ASSERT_FALSE(frame.person_detected);
  TEST_ASSERT_EQUAL_UINT8(static_cast<uint8_t>(AlarmLevel::kFault),
                          frame.alarm_level);
}

void test_alarm_policy_matches_existing_local_rules() {
  SensorState state{};
  make_ready(&state, 0U);

  state.water_rise_mm = sensor_logic::config::kWaterAdvisoryMm;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<uint8_t>(AlarmLevel::kAdvisory),
      sensor_logic::snapshot(state, 200U).alarm_level);

  state.water_rise_mm = sensor_logic::config::kWaterWarningMm;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<uint8_t>(AlarmLevel::kWarning),
      sensor_logic::snapshot(state, 200U).alarm_level);

  state.water_rise_mm = 0;
  state.rise_rate_mm_s = sensor_logic::config::kRiseRateWarningMmS;
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<uint8_t>(AlarmLevel::kWarning),
      sensor_logic::snapshot(state, 200U).alarm_level);

  state.rise_rate_mm_s = 0;
  state.water_rise_mm = sensor_logic::config::kWaterDangerMm;
  sensor_logic::accept_vision(&state, 200U, true, true);
  TEST_ASSERT_EQUAL_UINT8(
      static_cast<uint8_t>(AlarmLevel::kCritical),
      sensor_logic::snapshot(state, 200U).alarm_level);
}

void test_millis_wraparound_preserves_freshness() {
  SensorState state{};
  sensor_logic::reset(&state);
  const uint32_t near_wrap = UINT32_MAX - 100U;
  sensor_logic::accept_vision(&state, near_wrap, false, false);
  sensor_logic::accept_distance(&state, near_wrap, 700U);
  sensor_logic::accept_distance(&state, near_wrap + 50U, 702U);
  sensor_logic::accept_distance(&state, 25U, 699U);

  const TelemetryFrame frame = sensor_logic::snapshot(state, 50U);
  TEST_ASSERT_BITS_HIGH(sensor_logic::kHealthUltrasonicOk |
                            sensor_logic::kHealthOpenMvOk,
                        frame.health_flags);
}

}  // namespace

int runSensorLogicTests() {
  UNITY_BEGIN();
  RUN_TEST(test_stable_baseline_builds_wire_compatible_snapshot);
  RUN_TEST(test_unstable_echoes_do_not_become_baseline);
  RUN_TEST(test_median_then_q8_filter_updates_rise_and_rate);
  RUN_TEST(test_timeout_grace_then_zeroes_and_resets_reference);
  RUN_TEST(test_out_of_range_sample_is_rejected_without_refreshing_age);
  RUN_TEST(test_hardware_fault_invalidates_immediately);
  RUN_TEST(test_vision_is_fail_closed_and_network_is_health_only);
  RUN_TEST(test_alarm_policy_matches_existing_local_rules);
  RUN_TEST(test_millis_wraparound_preserves_freshness);
  return UNITY_END();
}

#if defined(ARDUINO)
void setup() { runSensorLogicTests(); }
void loop() {}
#else
int main(int, char **) { return runSensorLogicTests(); }
#endif
