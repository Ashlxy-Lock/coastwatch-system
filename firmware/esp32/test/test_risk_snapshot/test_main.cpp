#include <unity.h>

#include <cstring>
#include <string>

#include "risk_snapshot.h"

namespace {

const char *kFullRiskJson = R"json({
  "device_id":"COAST_01",
  "location":"Brighton",
  "risk_level":2,
  "risk_name":"warning",
  "risk_score":0.83,
  "environmental_level":2,
  "environmental_probability":0.73,
  "local_alarm_level":1,
  "data_quality":"ok",
  "model_source":"model",
  "deployment_mode":"shadow",
  "model_version":"coastal-risk-logreg-v1",
  "forecast_horizon_hours":6,
  "degraded":false,
  "reason_codes":["WAVE_HEIGHT_SIGNAL"],
  "missing_features":[],
  "telemetry_id":42,
  "predicted_at":"2026-08-13T10:11:12.123456Z",
  "environment_updated_at":"2026-08-13T10:10:00+00:00"
})json";

std::string replaceOnce(std::string json, const std::string &from,
                        const std::string &to) {
  const size_t position = json.find(from);
  TEST_ASSERT_NOT_EQUAL(std::string::npos, position);
  json.replace(position, from.size(), to);
  return json;
}

}  // namespace

void test_complete_risk_response_is_parsed() {
  RiskSnapshot snapshot{};
  resetRiskSnapshot(&snapshot);
  TEST_ASSERT_TRUE(snapshot.stale);

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(RiskParseResult::kOk),
      static_cast<int>(
          parseRiskJson(kFullRiskJson, std::strlen(kFullRiskJson), &snapshot)));

  TEST_ASSERT_EQUAL_UINT8(2U, snapshot.risk_level);
  TEST_ASSERT_EQUAL_STRING("warning", snapshot.risk_name);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 0.83F, snapshot.risk_score);
  TEST_ASSERT_EQUAL_UINT8(2U, snapshot.environmental_level);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 0.73F,
                           snapshot.environmental_probability);
  TEST_ASSERT_EQUAL_UINT8(1U, snapshot.local_alarm_level);
  TEST_ASSERT_EQUAL_STRING("ok", snapshot.data_quality);
  TEST_ASSERT_EQUAL_STRING("model", snapshot.model_source);
  TEST_ASSERT_EQUAL_STRING("shadow", snapshot.deployment_mode);
  TEST_ASSERT_EQUAL_STRING("coastal-risk-logreg-v1", snapshot.model_version);
  TEST_ASSERT_EQUAL_UINT8(6U, snapshot.forecast_horizon_hours);
  TEST_ASSERT_FALSE(snapshot.degraded);
  TEST_ASSERT_EQUAL_UINT32(42U, snapshot.telemetry_id);
  TEST_ASSERT_EQUAL_STRING("2026-08-13T10:11:12.123456Z",
                           snapshot.predicted_at);
  TEST_ASSERT_EQUAL_STRING("2026-08-13T10:10:00+00:00",
                           snapshot.environment_updated_at);
  TEST_ASSERT_FALSE(snapshot.stale);
  TEST_ASSERT_EQUAL_UINT32(0U, snapshot.fetched_at_ms);
  TEST_ASSERT_EQUAL_UINT32(0U, snapshot.fetched_at_unix_time);
}

void test_missing_required_field_does_not_replace_last_good_snapshot() {
  const std::string missing = replaceOnce(
      kFullRiskJson,
      "\"predicted_at\":\"2026-08-13T10:11:12.123456Z\",", "");
  RiskSnapshot snapshot{};
  resetRiskSnapshot(&snapshot);
  std::strcpy(snapshot.model_version, "last-good");
  snapshot.telemetry_id = 7U;

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(RiskParseResult::kMissingRequiredField),
      static_cast<int>(
          parseRiskJson(missing.c_str(), missing.size(), &snapshot)));
  TEST_ASSERT_EQUAL_STRING("last-good", snapshot.model_version);
  TEST_ASSERT_EQUAL_UINT32(7U, snapshot.telemetry_id);
}

void test_numeric_ranges_and_types_are_enforced() {
  RiskSnapshot snapshot{};
  const std::string bad_probability = replaceOnce(
      kFullRiskJson, "\"environmental_probability\":0.73",
      "\"environmental_probability\":1.01");
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(RiskParseResult::kNumberOutOfRange),
      static_cast<int>(parseRiskJson(bad_probability.c_str(),
                                     bad_probability.size(), &snapshot)));

  const std::string bad_alarm = replaceOnce(
      kFullRiskJson, "\"local_alarm_level\":1",
      "\"local_alarm_level\":5");
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(RiskParseResult::kNumberOutOfRange),
      static_cast<int>(
          parseRiskJson(bad_alarm.c_str(), bad_alarm.size(), &snapshot)));

  const std::string bool_score = replaceOnce(
      kFullRiskJson, "\"risk_score\":0.83", "\"risk_score\":true");
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(RiskParseResult::kInvalidFieldType),
      static_cast<int>(
          parseRiskJson(bool_score.c_str(), bool_score.size(), &snapshot)));
}

void test_invalid_enums_and_level_name_mismatch_are_rejected() {
  RiskSnapshot snapshot{};
  const std::string bad_quality = replaceOnce(
      kFullRiskJson, "\"data_quality\":\"ok\"",
      "\"data_quality\":\"unknown\"");
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(RiskParseResult::kInvalidEnumValue),
      static_cast<int>(
          parseRiskJson(bad_quality.c_str(), bad_quality.size(), &snapshot)));

  const std::string mismatched_name = replaceOnce(
      kFullRiskJson, "\"risk_name\":\"warning\"",
      "\"risk_name\":\"critical\"");
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(RiskParseResult::kInconsistentFields),
      static_cast<int>(parseRiskJson(mismatched_name.c_str(),
                                     mismatched_name.size(), &snapshot)));

  const std::string mismatched_fallback = replaceOnce(
      kFullRiskJson, "\"deployment_mode\":\"shadow\"",
      "\"deployment_mode\":\"fallback\"");
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(RiskParseResult::kInconsistentFields),
      static_cast<int>(parseRiskJson(mismatched_fallback.c_str(),
                                     mismatched_fallback.size(), &snapshot)));
}

void test_oversized_and_non_object_payloads_are_rejected() {
  RiskSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(RiskParseResult::kPayloadTooLarge),
      static_cast<int>(
          parseRiskJson("{}", kRiskMaxJsonBytes + 1U, &snapshot)));

  const char *array = "[]";
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(RiskParseResult::kRootNotObject),
      static_cast<int>(parseRiskJson(array, std::strlen(array), &snapshot)));
  TEST_ASSERT_EQUAL_STRING(
      "payload-too-large",
      riskParseResultName(RiskParseResult::kPayloadTooLarge));
}

int runRiskSnapshotTests() {
  UNITY_BEGIN();
  RUN_TEST(test_complete_risk_response_is_parsed);
  RUN_TEST(test_missing_required_field_does_not_replace_last_good_snapshot);
  RUN_TEST(test_numeric_ranges_and_types_are_enforced);
  RUN_TEST(test_invalid_enums_and_level_name_mismatch_are_rejected);
  RUN_TEST(test_oversized_and_non_object_payloads_are_rejected);
  return UNITY_END();
}

#if defined(ARDUINO)
void setup() { runRiskSnapshotTests(); }
void loop() {}
#else
int main(int, char **) { return runRiskSnapshotTests(); }
#endif
