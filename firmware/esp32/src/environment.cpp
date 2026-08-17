#include "environment.h"

#include <ArduinoJson.h>

#include <cmath>
#include <cstring>

namespace {

EnvironmentParseResult copyRequiredString(JsonObjectConst root, const char *key,
                                          char *destination,
                                          size_t destination_size) {
  const JsonVariantConst value = root[key];
  if (value.isNull()) {
    return EnvironmentParseResult::kMissingRequiredField;
  }
  if (!value.is<const char *>()) {
    return EnvironmentParseResult::kInvalidFieldType;
  }
  const char *text = value.as<const char *>();
  if (text == nullptr || text[0] == '\0') {
    return EnvironmentParseResult::kMissingRequiredField;
  }
  const size_t length = std::strlen(text);
  if (length >= destination_size) {
    return EnvironmentParseResult::kStringTooLong;
  }
  std::memcpy(destination, text, length + 1U);
  return EnvironmentParseResult::kOk;
}

EnvironmentParseResult copyOptionalString(JsonObjectConst root, const char *key,
                                          char *destination,
                                          size_t destination_size,
                                          uint16_t flag,
                                          uint16_t *valid_fields) {
  const JsonVariantConst value = root[key];
  if (value.isNull()) {
    destination[0] = '\0';
    return EnvironmentParseResult::kOk;
  }
  if (!value.is<const char *>()) {
    return EnvironmentParseResult::kInvalidFieldType;
  }
  const char *text = value.as<const char *>();
  if (text == nullptr) {
    return EnvironmentParseResult::kInvalidFieldType;
  }
  const size_t length = std::strlen(text);
  if (length >= destination_size) {
    return EnvironmentParseResult::kStringTooLong;
  }
  std::memcpy(destination, text, length + 1U);
  *valid_fields |= flag;
  return EnvironmentParseResult::kOk;
}

EnvironmentParseResult parseOptionalFloat(JsonObjectConst root,
                                          const char *key, float minimum,
                                          float maximum, uint16_t flag,
                                          float *destination,
                                          uint16_t *valid_fields) {
  const JsonVariantConst value = root[key];
  if (value.isNull()) {
    *destination = 0.0F;
    return EnvironmentParseResult::kOk;
  }
  if (!value.is<float>()) {
    return EnvironmentParseResult::kInvalidFieldType;
  }
  const float parsed = value.as<float>();
  if (!std::isfinite(parsed) || parsed < minimum || parsed > maximum) {
    return EnvironmentParseResult::kNumberOutOfRange;
  }
  *destination = parsed;
  *valid_fields |= flag;
  return EnvironmentParseResult::kOk;
}

bool isSupportedSource(const char *source) {
  return std::strcmp(source, "open-meteo") == 0 ||
         std::strcmp(source, "demo") == 0 ||
         std::strcmp(source, "stale") == 0 ||
         std::strcmp(source, "manual") == 0;
}

EnvironmentParseResult parseLocationKind(
    JsonObjectConst root, EnvironmentLocationKind *destination) {
  const JsonVariantConst value = root["kind"];
  if (value.isNull()) {
    *destination = EnvironmentLocationKind::kUnknown;
    return EnvironmentParseResult::kOk;
  }
  if (!value.is<const char *>()) {
    return EnvironmentParseResult::kInvalidFieldType;
  }
  const char *kind = value.as<const char *>();
  if (kind != nullptr && std::strcmp(kind, "coast") == 0) {
    *destination = EnvironmentLocationKind::kCoast;
    return EnvironmentParseResult::kOk;
  }
  if (kind != nullptr && std::strcmp(kind, "place") == 0) {
    *destination = EnvironmentLocationKind::kPlace;
    return EnvironmentParseResult::kOk;
  }
  return EnvironmentParseResult::kInvalidFieldType;
}

}  // namespace

void resetEnvironmentSnapshot(EnvironmentSnapshot *snapshot) {
  if (snapshot == nullptr) {
    return;
  }
  std::memset(snapshot, 0, sizeof(*snapshot));
  snapshot->stale = true;
}

bool environmentHasValue(const EnvironmentSnapshot &snapshot,
                         EnvironmentValueFlag flag) {
  return (snapshot.valid_fields & static_cast<uint16_t>(flag)) != 0U;
}

EnvironmentParseResult parseEnvironmentJson(const char *json, size_t length,
                                            EnvironmentSnapshot *snapshot) {
  if (json == nullptr || snapshot == nullptr) {
    return EnvironmentParseResult::kNullArgument;
  }
  if (length > kEnvironmentMaxJsonBytes) {
    return EnvironmentParseResult::kPayloadTooLarge;
  }

  JsonDocument document;
  const DeserializationError error = deserializeJson(document, json, length);
  if (error) {
    return EnvironmentParseResult::kInvalidJson;
  }
  if (!document.is<JsonObject>()) {
    return EnvironmentParseResult::kRootNotObject;
  }

  const JsonObjectConst root = document.as<JsonObjectConst>();
  EnvironmentSnapshot parsed{};
  resetEnvironmentSnapshot(&parsed);

  EnvironmentParseResult result = copyRequiredString(
      root, "location", parsed.location, sizeof(parsed.location));
  if (result != EnvironmentParseResult::kOk) return result;
  result = copyOptionalString(root, "display_location",
                              parsed.display_location,
                              sizeof(parsed.display_location), 0U,
                              &parsed.valid_fields);
  if (result != EnvironmentParseResult::kOk) return result;
  result = parseLocationKind(root, &parsed.location_kind);
  if (result != EnvironmentParseResult::kOk) return result;
  result = copyRequiredString(root, "weather", parsed.weather,
                              sizeof(parsed.weather));
  if (result != EnvironmentParseResult::kOk) return result;
  result = copyRequiredString(root, "source", parsed.source,
                              sizeof(parsed.source));
  if (result != EnvironmentParseResult::kOk) return result;
  if (!isSupportedSource(parsed.source)) {
    return EnvironmentParseResult::kUnsupportedSource;
  }
  result = copyRequiredString(root, "provider", parsed.provider,
                              sizeof(parsed.provider));
  if (result != EnvironmentParseResult::kOk) return result;
  result = copyRequiredString(root, "updated_at", parsed.updated_at,
                              sizeof(parsed.updated_at));
  if (result != EnvironmentParseResult::kOk) return result;

  const JsonVariantConst stale = root["stale"];
  if (stale.isNull()) {
    return EnvironmentParseResult::kMissingRequiredField;
  }
  if (!stale.is<bool>()) {
    return EnvironmentParseResult::kInvalidFieldType;
  }
  parsed.stale = stale.as<bool>();

  const JsonVariantConst weather_code = root["weather_code"];
  if (!weather_code.isNull()) {
    if (!weather_code.is<int32_t>()) {
      return EnvironmentParseResult::kInvalidFieldType;
    }
    parsed.weather_code = weather_code.as<int32_t>();
    parsed.valid_fields |= kEnvironmentHasWeatherCode;
  }

#define PARSE_FLOAT(KEY, MINIMUM, MAXIMUM, FLAG, MEMBER)                 \
  result = parseOptionalFloat(root, KEY, MINIMUM, MAXIMUM, FLAG,        \
                              &parsed.MEMBER, &parsed.valid_fields);     \
  if (result != EnvironmentParseResult::kOk) return result

  PARSE_FLOAT("air_temperature_c", -100.0F, 100.0F,
              kEnvironmentHasAirTemperature, air_temperature_c);
  PARSE_FLOAT("humidity_percent", 0.0F, 100.0F, kEnvironmentHasHumidity,
              humidity_percent);
  PARSE_FLOAT("wind_speed_kmh", 0.0F, 500.0F, kEnvironmentHasWindSpeed,
              wind_speed_kmh);
  PARSE_FLOAT("wind_direction_deg", 0.0F, 360.0F,
              kEnvironmentHasWindDirection, wind_direction_deg);
  PARSE_FLOAT("water_temperature_c", -10.0F, 60.0F,
              kEnvironmentHasWaterTemperature, water_temperature_c);
  PARSE_FLOAT("wave_height_m", 0.0F, 50.0F, kEnvironmentHasWaveHeight,
              wave_height_m);
  PARSE_FLOAT("wave_period_s", 0.0F, 120.0F, kEnvironmentHasWavePeriod,
              wave_period_s);
  PARSE_FLOAT("sea_level_height_m", -20.0F, 20.0F,
              kEnvironmentHasSeaLevelHeight, sea_level_height_m);
  PARSE_FLOAT("ocean_current_velocity_kmh", 0.0F, 100.0F,
              kEnvironmentHasCurrentVelocity, ocean_current_velocity_kmh);
  PARSE_FLOAT("ocean_current_direction_deg", 0.0F, 360.0F,
              kEnvironmentHasCurrentDirection,
              ocean_current_direction_deg);

#undef PARSE_FLOAT

  result = copyOptionalString(
      root, "tide_status", parsed.tide_status, sizeof(parsed.tide_status),
      kEnvironmentHasTideStatus, &parsed.valid_fields);
  if (result != EnvironmentParseResult::kOk) return result;

  // Compatibility with older servers that did not send `kind`: a response
  // carrying actual marine measurements was necessarily a coastal view.
  if (parsed.location_kind == EnvironmentLocationKind::kUnknown) {
    constexpr uint16_t kMarineFlags =
        kEnvironmentHasWaterTemperature | kEnvironmentHasWaveHeight |
        kEnvironmentHasWavePeriod | kEnvironmentHasSeaLevelHeight |
        kEnvironmentHasTideStatus | kEnvironmentHasCurrentVelocity |
        kEnvironmentHasCurrentDirection;
    parsed.location_kind = (parsed.valid_fields & kMarineFlags) != 0U
                               ? EnvironmentLocationKind::kCoast
                               : EnvironmentLocationKind::kPlace;
  }

  // Do not overwrite the last good public snapshot after a malformed reply.
  *snapshot = parsed;
  return EnvironmentParseResult::kOk;
}

const char *environmentParseResultName(EnvironmentParseResult result) {
  switch (result) {
    case EnvironmentParseResult::kOk:
      return "ok";
    case EnvironmentParseResult::kNullArgument:
      return "null-argument";
    case EnvironmentParseResult::kPayloadTooLarge:
      return "payload-too-large";
    case EnvironmentParseResult::kInvalidJson:
      return "invalid-json";
    case EnvironmentParseResult::kRootNotObject:
      return "root-not-object";
    case EnvironmentParseResult::kMissingRequiredField:
      return "missing-required-field";
    case EnvironmentParseResult::kInvalidFieldType:
      return "invalid-field-type";
    case EnvironmentParseResult::kStringTooLong:
      return "string-too-long";
    case EnvironmentParseResult::kNumberOutOfRange:
      return "number-out-of-range";
    case EnvironmentParseResult::kUnsupportedSource:
      return "unsupported-source";
  }
  return "unknown";
}
