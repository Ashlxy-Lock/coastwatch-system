#include <unity.h>

#include <cstring>

#include "openmv_control.h"

namespace {

constexpr uint32_t kMaximumAgeMs = 25000U;

RiskSnapshot modelRisk(uint8_t environmental_level,
                       uint32_t fetched_at_ms = 1000U) {
  RiskSnapshot risk{};
  std::strcpy(risk.model_source, "model");
  std::strcpy(risk.data_quality, "ok");
  risk.environmental_level = environmental_level;
  risk.local_alarm_level = 0U;
  risk.degraded = false;
  risk.fetched_at_ms = fetched_at_ms;
  risk.stale = false;
  return risk;
}

OpenMvControlDecision decisionFor(const RiskSnapshot &risk,
                                 bool risk_ready = true,
                                 uint32_t now_ms = 2000U,
                                 uint8_t live_local_alarm_level = 0U,
                                 bool live_ultrasonic_health_ok = true,
                                 bool live_openmv_health_ok = true,
                                 int32_t live_water_rise_mm = 0,
                                 int32_t live_rise_rate_mm_s = 0) {
  return decideOpenMvControl(risk, risk_ready, live_local_alarm_level,
                             live_water_rise_mm, live_rise_rate_mm_s,
                             live_ultrasonic_health_ok,
                             live_openmv_health_ok, now_ms, kMaximumAgeMs);
}

}  // namespace

void setUp() {}
void tearDown() {}

void test_fresh_environmental_safe_ignores_high_combined_level_for_danger() {
  RiskSnapshot risk = modelRisk(0U);
  // The combined presentation field must never manufacture model danger.
  risk.risk_level = 3U;

  const OpenMvControlDecision decision =
      decisionFor(risk);
  TEST_ASSERT_TRUE(decision.trusted_model_result);
  TEST_ASSERT_FALSE(decision.fail_safe);
  TEST_ASSERT_TRUE(decision.green_safe);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_FALSE(decision.person_enable);
  TEST_ASSERT_EQUAL_UINT8(0U, decision.environmental_level);
}

void test_fresh_model_warning_enables_monitoring_and_danger() {
  const OpenMvControlDecision decision =
      decisionFor(modelRisk(2U));
  TEST_ASSERT_TRUE(decision.trusted_model_result);
  TEST_ASSERT_FALSE(decision.fail_safe);
  TEST_ASSERT_TRUE(decision.model_danger);
  TEST_ASSERT_FALSE(decision.local_water_danger);
  TEST_ASSERT_TRUE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);
  TEST_ASSERT_EQUAL_UINT8(2U, decision.environmental_level);
}

void test_fresh_model_advisory_uses_full_rate_without_danger() {
  const OpenMvControlDecision decision =
      decisionFor(modelRisk(1U));
  TEST_ASSERT_TRUE(decision.trusted_model_result);
  TEST_ASSERT_FALSE(decision.green_safe);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);
  TEST_ASSERT_EQUAL_UINT8(1U, decision.environmental_level);
}

void test_delayed_server_safe_echo_cannot_override_live_alarm() {
  RiskSnapshot risk = modelRisk(0U);
  risk.local_alarm_level = 0U;

  const OpenMvControlDecision decision =
      decisionFor(risk, true, 2000U, 3U, true, true, 180, 0);
  TEST_ASSERT_TRUE(decision.trusted_model_result);
  TEST_ASSERT_FALSE(decision.fail_safe);
  TEST_ASSERT_FALSE(decision.green_safe);
  TEST_ASSERT_FALSE(decision.model_danger);
  TEST_ASSERT_TRUE(decision.local_water_danger);
  TEST_ASSERT_TRUE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);
  TEST_ASSERT_EQUAL_UINT8(0U, decision.environmental_level);
}

void test_delayed_server_alarm_echo_does_not_override_live_safe_state() {
  RiskSnapshot risk = modelRisk(0U);
  risk.local_alarm_level = 3U;

  const OpenMvControlDecision decision =
      decisionFor(risk, true, 2000U, 0U, true);
  TEST_ASSERT_TRUE(decision.trusted_model_result);
  TEST_ASSERT_FALSE(decision.fail_safe);
  TEST_ASSERT_TRUE(decision.green_safe);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_FALSE(decision.person_enable);
}

void test_live_sensor_fault_cannot_authorize_green_even_with_alarm_zero() {
  const RiskSnapshot risk = modelRisk(0U);
  const OpenMvControlDecision decision =
      decisionFor(risk, true, 2000U, 0U, false);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_FALSE(decision.green_safe);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);
}

void test_person_only_warning_does_not_manufacture_water_danger() {
  const RiskSnapshot risk = modelRisk(0U);
  const OpenMvControlDecision decision =
      decisionFor(risk, true, 2000U, 2U, true, false);
  TEST_ASSERT_TRUE(decision.trusted_model_result);
  TEST_ASSERT_FALSE(decision.model_danger);
  TEST_ASSERT_FALSE(decision.local_water_danger);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);
}

void test_water_warning_or_fast_rate_starts_local_danger() {
  const RiskSnapshot risk = modelRisk(0U);
  OpenMvControlDecision decision =
      decisionFor(risk, true, 2000U, 2U, true, true, 100, 0);
  TEST_ASSERT_TRUE(decision.local_water_danger);
  TEST_ASSERT_TRUE(decision.danger);

  decision = decisionFor(risk, true, 2000U, 2U, true, true, -10, 25);
  TEST_ASSERT_TRUE(decision.local_water_danger);
  TEST_ASSERT_TRUE(decision.danger);
}

void test_water_thresholds_are_strict_and_advisory_is_not_danger() {
  const RiskSnapshot risk = modelRisk(0U);
  OpenMvControlDecision decision =
      decisionFor(risk, true, 2000U, 1U, true, true, 50, 0);
  TEST_ASSERT_FALSE(decision.local_water_danger);
  TEST_ASSERT_FALSE(decision.danger);

  decision = decisionFor(risk, true, 2000U, 0U, true, true, 99, 24);
  TEST_ASSERT_FALSE(decision.local_water_danger);
  TEST_ASSERT_FALSE(decision.danger);
}

void test_live_water_critical_starts_danger_but_fault_does_not() {
  const RiskSnapshot risk = modelRisk(0U);
  OpenMvControlDecision decision =
      decisionFor(risk, true, 2000U, 3U, true, true, 180, 0);
  TEST_ASSERT_TRUE(decision.local_water_danger);
  TEST_ASSERT_TRUE(decision.danger);

  decision = decisionFor(risk, true, 2000U, 4U, true, false, 180, 30);
  TEST_ASSERT_FALSE(decision.local_water_danger);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_TRUE(decision.person_enable);
}

void test_live_warning_requires_healthy_ultrasonic() {
  const RiskSnapshot risk = modelRisk(0U);
  const OpenMvControlDecision decision =
      decisionFor(risk, true, 2000U, 2U, false, true, 100, 25);
  TEST_ASSERT_FALSE(decision.local_water_danger);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_TRUE(decision.person_enable);
}

void test_level_zero_with_bad_quality_cannot_authorize_green() {
  RiskSnapshot risk = modelRisk(0U);
  std::strcpy(risk.data_quality, "fault");

  OpenMvControlDecision decision =
      decisionFor(risk);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_FALSE(decision.green_safe);
  TEST_ASSERT_TRUE(decision.person_enable);

  std::strcpy(risk.data_quality, "stale");
  decision = decisionFor(risk);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_FALSE(decision.green_safe);
  TEST_ASSERT_TRUE(decision.person_enable);
}

void test_level_zero_degraded_result_cannot_authorize_green() {
  RiskSnapshot risk = modelRisk(0U);
  risk.degraded = true;

  const OpenMvControlDecision decision =
      decisionFor(risk);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_FALSE(decision.green_safe);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);
}

void test_rule_fallback_never_claims_model_danger() {
  RiskSnapshot risk = modelRisk(3U);
  std::strcpy(risk.model_source, "rule-fallback");

  const OpenMvControlDecision decision =
      decisionFor(risk);
  TEST_ASSERT_FALSE(decision.trusted_model_result);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);
  TEST_ASSERT_EQUAL_UINT8(3U, decision.environmental_level);
}

void test_stale_or_unavailable_result_fails_safe_without_led_danger() {
  RiskSnapshot risk = modelRisk(3U);
  risk.stale = true;
  OpenMvControlDecision decision =
      decisionFor(risk);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);

  risk.stale = false;
  decision = decisionFor(risk, false);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);
}

void test_result_older_than_25_seconds_fails_safe() {
  const RiskSnapshot risk = modelRisk(2U, 1000U);
  OpenMvControlDecision decision =
      decisionFor(risk, true, 26000U);
  TEST_ASSERT_TRUE(decision.trusted_model_result);
  TEST_ASSERT_TRUE(decision.danger);

  decision = decisionFor(risk, true, 26001U);
  TEST_ASSERT_FALSE(decision.trusted_model_result);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);
}

void test_invalid_environmental_level_fails_safe_and_is_not_forwarded() {
  const OpenMvControlDecision decision =
      decisionFor(modelRisk(4U));
  TEST_ASSERT_FALSE(decision.trusted_model_result);
  TEST_ASSERT_TRUE(decision.fail_safe);
  TEST_ASSERT_FALSE(decision.danger);
  TEST_ASSERT_TRUE(decision.person_enable);
  TEST_ASSERT_EQUAL_UINT8(0U, decision.environmental_level);
}

int runOpenMvControlTests() {
  UNITY_BEGIN();
  RUN_TEST(
      test_fresh_environmental_safe_ignores_high_combined_level_for_danger);
  RUN_TEST(test_fresh_model_warning_enables_monitoring_and_danger);
  RUN_TEST(test_fresh_model_advisory_uses_full_rate_without_danger);
  RUN_TEST(test_delayed_server_safe_echo_cannot_override_live_alarm);
  RUN_TEST(test_delayed_server_alarm_echo_does_not_override_live_safe_state);
  RUN_TEST(test_live_sensor_fault_cannot_authorize_green_even_with_alarm_zero);
  RUN_TEST(test_person_only_warning_does_not_manufacture_water_danger);
  RUN_TEST(test_water_warning_or_fast_rate_starts_local_danger);
  RUN_TEST(test_water_thresholds_are_strict_and_advisory_is_not_danger);
  RUN_TEST(test_live_water_critical_starts_danger_but_fault_does_not);
  RUN_TEST(test_live_warning_requires_healthy_ultrasonic);
  RUN_TEST(test_level_zero_with_bad_quality_cannot_authorize_green);
  RUN_TEST(test_level_zero_degraded_result_cannot_authorize_green);
  RUN_TEST(test_rule_fallback_never_claims_model_danger);
  RUN_TEST(test_stale_or_unavailable_result_fails_safe_without_led_danger);
  RUN_TEST(test_result_older_than_25_seconds_fails_safe);
  RUN_TEST(test_invalid_environmental_level_fails_safe_and_is_not_forwarded);
  return UNITY_END();
}

#if defined(ARDUINO)
void setup() { runOpenMvControlTests(); }
void loop() {}
#else
int main(int, char **) { return runOpenMvControlTests(); }
#endif
