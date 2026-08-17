#pragma once

#include <cstdint>

namespace coastwatch::config {

// Physical connections fixed for this prototype.
constexpr std::uint32_t kUartBaud = 115200U;
constexpr std::uint32_t kTelemetryPeriodMs = 500U;
constexpr std::uint32_t kUltrasonicPeriodMs = 100U;
constexpr std::uint32_t kEchoTimeoutMs = 30U;
constexpr std::uint16_t kTriggerPulseUs = 10U;
constexpr std::uint32_t kPinSafetyConfirmMs = 50U;

// HC-SR04 data outside this envelope is rejected, never clipped into a
// plausible-looking water level.
constexpr std::uint32_t kDistanceMinMm = 20U;
constexpr std::uint32_t kDistanceMaxMm = 4000U;
// The sensor is pinged every 100 ms. Keep the last valid sample through a few
// isolated misses, but fail closed after about one second without a valid echo.
constexpr std::uint32_t kUltrasonicFreshMs = 1000U;
constexpr std::uint32_t kUltrasonicBaselineResetMs = 3000U;
constexpr std::uint32_t kVisionFreshMs = 1000U;
constexpr std::uint32_t kNetworkFreshMs = 2500U;

// A baseline is accepted only after three consecutive readings fit in this
// span. This prevents the first stray echo from becoming the water reference.
constexpr std::size_t kBaselineWindow = 3U;
constexpr std::uint32_t kBaselineStableSpanMm = 20U;
constexpr std::size_t kMedianWindow = 5U;

// Desktop demonstration thresholds from the project specification. They are
// explicit configuration, not learned probabilities.
constexpr std::int32_t kWaterAdvisoryMm = 50;
constexpr std::int32_t kWaterWarningMm = 100;
constexpr std::int32_t kWaterDangerMm = 180;
constexpr std::int32_t kRiseRateWarningMmS = 25;

}  // namespace coastwatch::config
