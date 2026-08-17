#include "risk_snapshot.h"

#include <ArduinoJson.h>

#include <cmath>
#include <cstring>

namespace {

RiskParseResult copyRequiredString(JsonObjectConst root, const char *key,
                                   char *destination,
                                   size_t destination_size) {
  const JsonVariantConst value = root[key];
  if (value.isNull()) {
    return RiskParseResult::kMissingRequiredField;
  }
  if (!value.is<const char *>()) {
    return RiskParseResult::kInvalidFieldType;
  }
  const char *text = value.as<const char *>();
  if (text == nullptr || text[0] == '\0') {
    return RiskParseResult::kMissingRequiredField;
  }
  const size_t length = std::strlen(text);
  if (length >= destination_size) {
    return RiskParseResult::kStringTooLong;
  }
  std::memcpy(destination, text, length + 1U);
  return RiskParseResult::kOk;
}

RiskParseResult parseRequiredUInt(JsonObjectConst root, const char *key,
                                  uint32_t maximum, uint32_t *destination) {
  const JsonVariantConst value = root[key];
  if (value.isNull()) {
    return RiskParseResult::kMissingRequiredField;
  }
  if (!value.is<uint32_t>()) {
    return RiskParseResult::kInvalidFieldType;
  }
  const uint32_t parsed = value.as<uint32_t>();
  if (parsed > maximum) {
    return RiskParseResult::kNumberOutOfRange;
  }
  *destination = parsed;
  return RiskParseResult::kOk;
}

RiskParseResult parseRequiredFloat(JsonObjectConst root, const char *key,
                                   float minimum, float maximum,
                                   float *destination) {
  const JsonVariantConst value = root[key];
  if (value.isNull()) {
    return RiskParseResult::kMissingRequiredField;
  }
  if (value.is<bool>() || !value.is<float>()) {
    return RiskParseResult::kInvalidFieldType;
  }
  const float parsed = value.as<float>();
  if (!std::isfinite(parsed) || parsed < minimum || parsed > maximum) {
    return RiskParseResult::kNumberOutOfRange;
  }
  *destination = parsed;
  return RiskParseResult::kOk;
}

bool isOneOf(const char *value, const char *const *allowed, size_t count) {
  for (size_t index = 0U; index < count; ++index) {
    if (std::strcmp(value, allowed[index]) == 0) {
      return true;
    }
  }
  return false;
}

RiskParseResult validateEnumsAndRelationships(const RiskSnapshot &snapshot) {
  constexpr const char *kRiskNames[] = {"safe", "advisory", "warning",
                                        "critical"};
  constexpr const char *kDataQualities[] = {"ok", "fault", "stale"};
  constexpr const char *kModelSources[] = {"model", "rule-fallback"};
  constexpr const char *kDeploymentModes[] = {"shadow", "active", "fallback"};

  if (!isOneOf(snapshot.risk_name, kRiskNames, 4U) ||
      !isOneOf(snapshot.data_quality, kDataQualities, 3U) ||
      !isOneOf(snapshot.model_source, kModelSources, 2U) ||
      !isOneOf(snapshot.deployment_mode, kDeploymentModes, 3U)) {
    return RiskParseResult::kInvalidEnumValue;
  }

  if (std::strcmp(snapshot.risk_name, kRiskNames[snapshot.risk_level]) != 0) {
    return RiskParseResult::kInconsistentFields;
  }
  const bool fallback_source =
      std::strcmp(snapshot.model_source, "rule-fallback") == 0;
  const bool fallback_mode =
      std::strcmp(snapshot.deployment_mode, "fallback") == 0;
  if (fallback_source != fallback_mode) {
    return RiskParseResult::kInconsistentFields;
  }
  return RiskParseResult::kOk;
}

}  // namespace

void resetRiskSnapshot(RiskSnapshot *snapshot) {
  if (snapshot == nullptr) {
    return;
  }
  std::memset(snapshot, 0, sizeof(*snapshot));
  snapshot->stale = true;
}

RiskParseResult parseRiskJson(const char *json, size_t length,
                              RiskSnapshot *snapshot) {
  if (json == nullptr || snapshot == nullptr) {
    return RiskParseResult::kNullArgument;
  }
  if (length > kRiskMaxJsonBytes) {
    return RiskParseResult::kPayloadTooLarge;
  }

  JsonDocument document;
  const DeserializationError error = deserializeJson(document, json, length);
  if (error) {
    return RiskParseResult::kInvalidJson;
  }
  if (!document.is<JsonObject>()) {
    return RiskParseResult::kRootNotObject;
  }

  const JsonObjectConst root = document.as<JsonObjectConst>();
  RiskSnapshot parsed{};
  resetRiskSnapshot(&parsed);

  uint32_t numeric = 0U;
  RiskParseResult result =
      parseRequiredUInt(root, "risk_level", 3U, &numeric);
  if (result != RiskParseResult::kOk) return result;
  parsed.risk_level = static_cast<uint8_t>(numeric);

  result = copyRequiredString(root, "risk_name", parsed.risk_name,
                              sizeof(parsed.risk_name));
  if (result != RiskParseResult::kOk) return result;
  result = parseRequiredFloat(root, "risk_score", 0.0F, 1.0F,
                              &parsed.risk_score);
  if (result != RiskParseResult::kOk) return result;

  result = parseRequiredUInt(root, "environmental_level", 3U, &numeric);
  if (result != RiskParseResult::kOk) return result;
  parsed.environmental_level = static_cast<uint8_t>(numeric);
  result = parseRequiredFloat(root, "environmental_probability", 0.0F, 1.0F,
                              &parsed.environmental_probability);
  if (result != RiskParseResult::kOk) return result;

  result = parseRequiredUInt(root, "local_alarm_level", 4U, &numeric);
  if (result != RiskParseResult::kOk) return result;
  parsed.local_alarm_level = static_cast<uint8_t>(numeric);

  result = copyRequiredString(root, "data_quality", parsed.data_quality,
                              sizeof(parsed.data_quality));
  if (result != RiskParseResult::kOk) return result;
  result = copyRequiredString(root, "model_source", parsed.model_source,
                              sizeof(parsed.model_source));
  if (result != RiskParseResult::kOk) return result;
  result = copyRequiredString(root, "deployment_mode", parsed.deployment_mode,
                              sizeof(parsed.deployment_mode));
  if (result != RiskParseResult::kOk) return result;
  result = copyRequiredString(root, "model_version", parsed.model_version,
                              sizeof(parsed.model_version));
  if (result != RiskParseResult::kOk) return result;

  result = parseRequiredUInt(root, "forecast_horizon_hours", 72U, &numeric);
  if (result != RiskParseResult::kOk) return result;
  parsed.forecast_horizon_hours = static_cast<uint8_t>(numeric);

  const JsonVariantConst degraded = root["degraded"];
  if (degraded.isNull()) {
    return RiskParseResult::kMissingRequiredField;
  }
  if (!degraded.is<bool>()) {
    return RiskParseResult::kInvalidFieldType;
  }
  parsed.degraded = degraded.as<bool>();

  result = parseRequiredUInt(root, "telemetry_id", UINT32_MAX,
                             &parsed.telemetry_id);
  if (result != RiskParseResult::kOk) return result;
  result = copyRequiredString(root, "predicted_at", parsed.predicted_at,
                              sizeof(parsed.predicted_at));
  if (result != RiskParseResult::kOk) return result;
  result = copyRequiredString(root, "environment_updated_at",
                              parsed.environment_updated_at,
                              sizeof(parsed.environment_updated_at));
  if (result != RiskParseResult::kOk) return result;

  result = validateEnumsAndRelationships(parsed);
  if (result != RiskParseResult::kOk) return result;

  // Receipt timestamps belong to the network task, not the remote JSON.
  parsed.fetched_at_ms = 0U;
  parsed.fetched_at_unix_time = 0U;
  parsed.stale = false;

  // Preserve the last known good public snapshot after malformed responses.
  *snapshot = parsed;
  return RiskParseResult::kOk;
}

const char *riskParseResultName(RiskParseResult result) {
  switch (result) {
    case RiskParseResult::kOk:
      return "ok";
    case RiskParseResult::kNullArgument:
      return "null-argument";
    case RiskParseResult::kPayloadTooLarge:
      return "payload-too-large";
    case RiskParseResult::kInvalidJson:
      return "invalid-json";
    case RiskParseResult::kRootNotObject:
      return "root-not-object";
    case RiskParseResult::kMissingRequiredField:
      return "missing-required-field";
    case RiskParseResult::kInvalidFieldType:
      return "invalid-field-type";
    case RiskParseResult::kStringTooLong:
      return "string-too-long";
    case RiskParseResult::kNumberOutOfRange:
      return "number-out-of-range";
    case RiskParseResult::kInvalidEnumValue:
      return "invalid-enum-value";
    case RiskParseResult::kInconsistentFields:
      return "inconsistent-fields";
  }
  return "unknown";
}
