#include <unity.h>

#include <cstring>
#include <string>

#include "environment.h"

namespace {

const char *kFullEnvironmentJson = R"json({
  "location":"Qingdao Coast",
  "display_location":"QINGDAO COAST",
  "kind":"coast",
  "weather":"Partly cloudy",
  "weather_code":2,
  "air_temperature_c":27.5,
  "humidity_percent":81,
  "wind_speed_kmh":18.4,
  "wind_direction_deg":135,
  "water_temperature_c":24.2,
  "wave_height_m":1.3,
  "wave_period_s":6.8,
  "sea_level_height_m":0.42,
  "tide_status":"Rising",
  "ocean_current_velocity_kmh":2.1,
  "ocean_current_direction_deg":98,
  "source":"open-meteo",
  "provider":"open-meteo",
  "stale":false,
  "updated_at":"2026-08-02T05:23:41Z"
})json";

std::string requiredOnlyJson(const std::string &location = "Coast") {
  return "{\"location\":\"" + location +
         "\",\"weather\":\"Unknown\",\"source\":\"stale\"," 
         "\"provider\":\"open-meteo\",\"stale\":true,"
         "\"updated_at\":\"2026-08-02T05:23:41+00:00\"}";
}

}  // namespace

void test_full_environment_payload() {
  EnvironmentSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentParseResult::kOk),
      static_cast<int>(parseEnvironmentJson(
          kFullEnvironmentJson, std::strlen(kFullEnvironmentJson), &snapshot)));

  TEST_ASSERT_EQUAL_STRING("Qingdao Coast", snapshot.location);
  TEST_ASSERT_EQUAL_STRING("QINGDAO COAST", snapshot.display_location);
  TEST_ASSERT_EQUAL_STRING("Partly cloudy", snapshot.weather);
  TEST_ASSERT_EQUAL_STRING("open-meteo", snapshot.source);
  TEST_ASSERT_EQUAL_STRING("open-meteo", snapshot.provider);
  TEST_ASSERT_EQUAL_STRING("Rising", snapshot.tide_status);
  TEST_ASSERT_EQUAL_STRING("2026-08-02T05:23:41Z", snapshot.updated_at);
  TEST_ASSERT_FALSE(snapshot.stale);
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentLocationKind::kCoast),
      static_cast<int>(snapshot.location_kind));
  TEST_ASSERT_EQUAL_INT32(2, snapshot.weather_code);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 27.5F, snapshot.air_temperature_c);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 81.0F, snapshot.humidity_percent);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 1.3F, snapshot.wave_height_m);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 0.42F, snapshot.sea_level_height_m);
  TEST_ASSERT_TRUE(
      environmentHasValue(snapshot, kEnvironmentHasAirTemperature));
  TEST_ASSERT_TRUE(environmentHasValue(snapshot, kEnvironmentHasWaveHeight));
  TEST_ASSERT_TRUE(environmentHasValue(snapshot, kEnvironmentHasTideStatus));
  TEST_ASSERT_TRUE(
      environmentHasValue(snapshot, kEnvironmentHasCurrentDirection));
}

void test_null_and_missing_optional_values_are_not_marked_valid() {
  const char *json = R"json({
    "location":"Coast",
    "kind":"place",
    "weather":"Unavailable",
    "weather_code":null,
    "air_temperature_c":null,
    "wave_height_m":null,
    "tide_status":null,
    "source":"stale",
    "provider":"open-meteo",
    "stale":true,
    "updated_at":"2026-08-02T05:23:41+00:00"
  })json";
  EnvironmentSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentParseResult::kOk),
      static_cast<int>(
          parseEnvironmentJson(json, std::strlen(json), &snapshot)));
  TEST_ASSERT_TRUE(snapshot.stale);
  TEST_ASSERT_EQUAL_UINT16(0U, snapshot.valid_fields);
  TEST_ASSERT_EQUAL_STRING("", snapshot.tide_status);
  TEST_ASSERT_EQUAL_STRING("", snapshot.display_location);
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentLocationKind::kPlace),
      static_cast<int>(snapshot.location_kind));
}

void test_bad_payload_does_not_overwrite_last_snapshot() {
  EnvironmentSnapshot snapshot{};
  resetEnvironmentSnapshot(&snapshot);
  std::strcpy(snapshot.location, "last-good-location");
  snapshot.air_temperature_c = 26.0F;
  snapshot.valid_fields = kEnvironmentHasAirTemperature;

  const char *bad_json = R"json({
    "location":"Coast",
    "weather":"Clear",
    "humidity_percent":101,
    "source":"open-meteo",
    "provider":"open-meteo",
    "stale":false,
    "updated_at":"2026-08-02T05:23:41Z"
  })json";
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentParseResult::kNumberOutOfRange),
      static_cast<int>(parseEnvironmentJson(
          bad_json, std::strlen(bad_json), &snapshot)));
  TEST_ASSERT_EQUAL_STRING("last-good-location", snapshot.location);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 26.0F, snapshot.air_temperature_c);
  TEST_ASSERT_TRUE(
      environmentHasValue(snapshot, kEnvironmentHasAirTemperature));
}

void test_required_string_capacity_boundary() {
  const std::string maximum(kEnvironmentLocationBytes - 1U, 'A');
  const std::string accepted = requiredOnlyJson(maximum);
  EnvironmentSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentParseResult::kOk),
      static_cast<int>(parseEnvironmentJson(accepted.c_str(), accepted.size(),
                                            &snapshot)));
  TEST_ASSERT_EQUAL_UINT(kEnvironmentLocationBytes - 1U,
                         std::strlen(snapshot.location));

  const std::string too_long(kEnvironmentLocationBytes, 'B');
  const std::string rejected = requiredOnlyJson(too_long);
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentParseResult::kStringTooLong),
      static_cast<int>(parseEnvironmentJson(rejected.c_str(), rejected.size(),
                                            &snapshot)));
  TEST_ASSERT_EQUAL_STRING(maximum.c_str(), snapshot.location);
}

void test_invalid_types_source_and_payload_size_are_rejected() {
  const char *bad_stale =
      "{\"location\":\"Coast\",\"weather\":\"Clear\","
      "\"source\":\"demo\",\"provider\":\"manual\","
      "\"stale\":0,\"updated_at\":\"now\"}";
  EnvironmentSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentParseResult::kInvalidFieldType),
      static_cast<int>(parseEnvironmentJson(
          bad_stale, std::strlen(bad_stale), &snapshot)));

  const char *bad_kind =
      "{\"location\":\"Coast\",\"kind\":\"river\","
      "\"weather\":\"Clear\",\"source\":\"demo\","
      "\"provider\":\"manual\",\"stale\":false,"
      "\"updated_at\":\"now\"}";
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentParseResult::kInvalidFieldType),
      static_cast<int>(parseEnvironmentJson(
          bad_kind, std::strlen(bad_kind), &snapshot)));

  std::string unsupported = requiredOnlyJson();
  const size_t source_position = unsupported.find("stale");
  unsupported.replace(source_position, 5U, "other");
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentParseResult::kUnsupportedSource),
      static_cast<int>(parseEnvironmentJson(
          unsupported.c_str(), unsupported.size(), &snapshot)));

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(EnvironmentParseResult::kPayloadTooLarge),
      static_cast<int>(parseEnvironmentJson(
          "{}", kEnvironmentMaxJsonBytes + 1U, &snapshot)));
}

int runEnvironmentTests() {
  UNITY_BEGIN();
  RUN_TEST(test_full_environment_payload);
  RUN_TEST(test_null_and_missing_optional_values_are_not_marked_valid);
  RUN_TEST(test_bad_payload_does_not_overwrite_last_snapshot);
  RUN_TEST(test_required_string_capacity_boundary);
  RUN_TEST(test_invalid_types_source_and_payload_size_are_rejected);
  return UNITY_END();
}

#if defined(ARDUINO)
void setup() { runEnvironmentTests(); }
void loop() {}
#else
int main(int, char **) { return runEnvironmentTests(); }
#endif
