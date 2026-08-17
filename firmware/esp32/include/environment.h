#pragma once

#include <stddef.h>
#include <stdint.h>
#include <type_traits>

// Fixed-size storage lets the network task publish a snapshot without sharing
// Arduino String/JSON objects with the UI task.
constexpr size_t kEnvironmentLocationBytes = 64;
constexpr size_t kEnvironmentDisplayLocationBytes = 36;
constexpr size_t kEnvironmentWeatherBytes = 48;
constexpr size_t kEnvironmentSourceBytes = 16;
constexpr size_t kEnvironmentProviderBytes = 32;
constexpr size_t kEnvironmentTideStatusBytes = 24;
constexpr size_t kEnvironmentUpdatedAtBytes = 40;
constexpr size_t kEnvironmentMaxJsonBytes = 2048;

enum EnvironmentValueFlag : uint16_t {
  kEnvironmentHasWeatherCode = 1U << 0,
  kEnvironmentHasAirTemperature = 1U << 1,
  kEnvironmentHasHumidity = 1U << 2,
  kEnvironmentHasWindSpeed = 1U << 3,
  kEnvironmentHasWindDirection = 1U << 4,
  kEnvironmentHasWaterTemperature = 1U << 5,
  kEnvironmentHasWaveHeight = 1U << 6,
  kEnvironmentHasWavePeriod = 1U << 7,
  kEnvironmentHasSeaLevelHeight = 1U << 8,
  kEnvironmentHasTideStatus = 1U << 9,
  kEnvironmentHasCurrentVelocity = 1U << 10,
  kEnvironmentHasCurrentDirection = 1U << 11,
};

enum class EnvironmentLocationKind : uint8_t {
  kUnknown = 0,
  kCoast,
  kPlace,
};

struct EnvironmentSnapshot {
  char location[kEnvironmentLocationBytes];
  char display_location[kEnvironmentDisplayLocationBytes];
  char weather[kEnvironmentWeatherBytes];
  char source[kEnvironmentSourceBytes];
  char provider[kEnvironmentProviderBytes];
  char tide_status[kEnvironmentTideStatusBytes];
  char updated_at[kEnvironmentUpdatedAtBytes];

  int32_t weather_code;
  float air_temperature_c;
  float humidity_percent;
  float wind_speed_kmh;
  float wind_direction_deg;
  float water_temperature_c;
  float wave_height_m;
  float wave_period_s;
  float sea_level_height_m;
  float ocean_current_velocity_kmh;
  float ocean_current_direction_deg;

  uint16_t valid_fields;
  EnvironmentLocationKind location_kind;
  bool stale;

  // Local receipt times. fetched_at_ms is always available after a successful
  // fetch; fetched_at_unix_time remains zero until NTP has synchronized.
  uint32_t fetched_at_ms;
  uint32_t fetched_at_unix_time;
};

static_assert(std::is_standard_layout<EnvironmentSnapshot>::value,
              "EnvironmentSnapshot must remain a POD-like value type");
static_assert(std::is_trivial<EnvironmentSnapshot>::value,
              "EnvironmentSnapshot must remain a POD value type");
static_assert(std::is_trivially_copyable<EnvironmentSnapshot>::value,
              "EnvironmentSnapshot must be safe to copy as one snapshot");

enum class EnvironmentParseResult : uint8_t {
  kOk = 0,
  kNullArgument,
  kPayloadTooLarge,
  kInvalidJson,
  kRootNotObject,
  kMissingRequiredField,
  kInvalidFieldType,
  kStringTooLong,
  kNumberOutOfRange,
  kUnsupportedSource,
};

void resetEnvironmentSnapshot(EnvironmentSnapshot *snapshot);
bool environmentHasValue(const EnvironmentSnapshot &snapshot,
                         EnvironmentValueFlag flag);
EnvironmentParseResult parseEnvironmentJson(const char *json, size_t length,
                                            EnvironmentSnapshot *snapshot);
const char *environmentParseResultName(EnvironmentParseResult result);
