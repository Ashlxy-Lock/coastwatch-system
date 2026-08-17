#pragma once

#include <stddef.h>
#include <stdint.h>
#include <type_traits>

// The gateway response is intentionally copied into fixed-size storage before
// it crosses from the network task to the UI task. No Arduino String or JSON
// object is shared between tasks.
constexpr size_t kRiskNameBytes = 12;
constexpr size_t kRiskDataQualityBytes = 8;
constexpr size_t kRiskModelSourceBytes = 16;
constexpr size_t kRiskDeploymentModeBytes = 12;
constexpr size_t kRiskModelVersionBytes = 64;
constexpr size_t kRiskTimestampBytes = 40;
constexpr size_t kRiskMaxJsonBytes = 2048;

struct RiskSnapshot {
  char risk_name[kRiskNameBytes];
  char data_quality[kRiskDataQualityBytes];
  char model_source[kRiskModelSourceBytes];
  char deployment_mode[kRiskDeploymentModeBytes];
  char model_version[kRiskModelVersionBytes];
  char predicted_at[kRiskTimestampBytes];
  char environment_updated_at[kRiskTimestampBytes];

  // risk_score is the combined server score. It is retained for protocol
  // completeness, but must not be labelled as a disaster probability in UI.
  float risk_score;
  float environmental_probability;
  uint32_t telemetry_id;
  uint32_t fetched_at_ms;
  uint32_t fetched_at_unix_time;
  uint8_t risk_level;
  uint8_t environmental_level;
  uint8_t local_alarm_level;
  uint8_t forecast_horizon_hours;
  bool degraded;
  bool stale;
};

static_assert(std::is_standard_layout<RiskSnapshot>::value,
              "RiskSnapshot must remain a POD-like value type");
static_assert(std::is_trivial<RiskSnapshot>::value,
              "RiskSnapshot must remain a POD value type");
static_assert(std::is_trivially_copyable<RiskSnapshot>::value,
              "RiskSnapshot must be safe to publish as one value");

enum class RiskParseResult : uint8_t {
  kOk = 0,
  kNullArgument,
  kPayloadTooLarge,
  kInvalidJson,
  kRootNotObject,
  kMissingRequiredField,
  kInvalidFieldType,
  kStringTooLong,
  kNumberOutOfRange,
  kInvalidEnumValue,
  kInconsistentFields,
};

void resetRiskSnapshot(RiskSnapshot *snapshot);
RiskParseResult parseRiskJson(const char *json, size_t length,
                              RiskSnapshot *snapshot);
const char *riskParseResultName(RiskParseResult result);
