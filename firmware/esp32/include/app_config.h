#pragma once

#include <stddef.h>
#include <stdint.h>

#if __has_include("secrets.h")
#include "secrets.h"
#endif

#if __has_include("tunnel_secret.h")
#include "tunnel_secret.h"
#endif

// Empty values intentionally keep local sensing/display available without
// Wi-Fi.
#ifndef WIFI_SSID
#define WIFI_SSID ""
#endif

#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD ""
#endif

#ifndef SERVER_BASE_URL
#define SERVER_BASE_URL ""
#endif

#ifndef DEVICE_TOKEN
#define DEVICE_TOKEN ""
#endif

#ifndef DEVICE_ID
#define DEVICE_ID "COAST_01"
#endif

// This must be explicitly enabled in the ignored local secrets.h only after
// a physical 5 V -> 3.3 V ECHO divider/level shifter has been installed and
// checked. Keeping the default at 0 makes an accidental firmware flash leave
// the ultrasonic pins unclaimed.
#ifndef ULTRASONIC_ECHO_LEVEL_SHIFT_VERIFIED
#define ULTRASONIC_ECHO_LEVEL_SHIFT_VERIFIED 0
#endif

namespace app_config {

// USB serial console.
constexpr uint32_t kDebugBaud = 115200;

// Capacitive touch controller on the TK043F1509 ESP32 adapter. GPIO12 is
// physically routed as touch INT even though this firmware polls over I2C.
constexpr int kTouchSclPin = 13;
constexpr int kTouchSdaPin = 20;
constexpr uint8_t kTouchI2cAddress = 0x38;
// Espressif's FT5x06 component defaults to 100 kHz. This is deliberately kept
// below the controller's 400 kHz maximum for better margin on the adapter.
constexpr uint32_t kTouchI2cClockHz = 100000U;

// OpenMV VIS input and CTL output on ESP32-S3 UART1. P4/TX connects to
// GPIO8/RX; P5/RX connects to GPIO14/TX.
// GPIO12 remains reserved by the panel's TOUCH_INT connection.
constexpr int kOpenMvUartRxPin = 8;
constexpr int kOpenMvUartTxPin = 14;
constexpr uint32_t kOpenMvUartBaud = 115200;
constexpr size_t kUartHardwareRxBufferBytes = 512;
constexpr size_t kUartRingCapacity = 512;
constexpr size_t kMaxFrameBytes = 160;
constexpr uint32_t kOpenMvControlIntervalMs = 500U;
// The risk endpoint normally refreshes every 10 seconds. More than 25 seconds
// without a fresh result switches OpenMV to fail-safe full-rate monitoring.
constexpr uint32_t kOpenMvRiskMaximumAgeMs = 25U * 1000U;

// Single-board ultrasonic input. GPIO42 is reserved by the LCD D/C signal;
// the nearby free GPIO40 receives the divided ECHO signal. HC-SR04 ECHO is
// 5 V and MUST reach GPIO40 only through a verified 5 V -> 3.3 V divider or
// level shifter. No ESP32-S3 GPIO is 5 V tolerant.
constexpr int kUltrasonicTriggerPin = 10;
constexpr int kUltrasonicEchoPin = 40;
constexpr bool kUltrasonicEchoLevelShiftVerified =
    ULTRASONIC_ECHO_LEVEL_SHIFT_VERIFIED == 1;
constexpr uint32_t kLocalTelemetryPeriodMs = 500U;

constexpr const char *kWifiSsid = WIFI_SSID;
constexpr const char *kWifiPassword = WIFI_PASSWORD;
constexpr const char *kServerBaseUrl = SERVER_BASE_URL;
constexpr const char *kDeviceToken = DEVICE_TOKEN;
constexpr const char *kDeviceId = DEVICE_ID;
constexpr const char *kTelemetryPath = "/api/v1/telemetry";
constexpr const char *kEnvironmentPath = "/api/v1/environment";
constexpr const char *kRiskPath = "/api/v1/risk";
constexpr const char *kModelsPath = "/api/v1/models";
constexpr const char *kDeviceModelPath = "/api/v1/device-model";
constexpr const char *kSimulationSessionsPath =
    "/api/v1/simulations/sessions";
constexpr const char *kActiveSimulationSessionPath =
    "/api/v1/simulations/sessions/active";
constexpr const char *kLocationPresetsPath = "/api/v1/locations/presets";
constexpr const char *kLocationSearchPath = "/api/v1/locations/search";
constexpr const char *kDeviceLocationPath = "/api/v1/device-location";

constexpr uint32_t kTelemetryUploadIntervalMs = 2000;
// Simulation collection is deliberately faster, but still uploads only the
// freshest complete local sensor frame. Sensing never waits for this cadence.
constexpr uint32_t kSimulationTelemetryUploadIntervalMs = 500;
// A short poll makes a region selected on the web page appear on the LCD
// promptly. The server owns the slower upstream weather-provider cache.
constexpr uint32_t kEnvironmentRefreshIntervalMs = 30U * 1000U;
// Risk remains a research-only, read-only display channel. It must never feed
// the ESP32 local alarm path. Poll a little faster than environment data so a
// freshly uploaded local alarm is reflected on the overview without turning
// the public gateway into a high-frequency stream.
constexpr uint32_t kRiskRefreshIntervalMs = 10U * 1000U;
constexpr uint32_t kRiskNoTelemetryRetryMs = 15U * 1000U;
constexpr uint32_t kHttpConnectTimeoutMs = 1500;
constexpr uint32_t kHttpReadTimeoutMs = 1500;
// Global geocoding may require an upstream provider round trip. Keep this
// independent from the short periodic weather/telemetry HTTP timeout.
constexpr uint32_t kLocationSearchReadTimeoutMs = 12000;
// Selecting a global geo_<id> makes the gateway re-resolve canonical
// coordinates upstream before committing. It needs the same provider-aware
// timeout as search; the old 1.5 s generic timeout caused a false LCD ERROR
// even though the server completed the save moments later.
constexpr uint32_t kLocationSelectionReadTimeoutMs = 12000;
// A newly selected coast can require both weather and marine upstream calls.
// Give that one foreground refresh the provider-aware budget; ordinary polls
// keep using the short generic timeout above.
constexpr uint32_t kSelectedEnvironmentReadTimeoutMs = 12000;

constexpr uint32_t kRetryBackoffMs[] = {5000, 10000, 30000, 60000};
constexpr size_t kRetryBackoffCount =
    sizeof(kRetryBackoffMs) / sizeof(kRetryBackoffMs[0]);

constexpr uint8_t kTelemetryQueueDepth = 8;
// HTTPS plus the fixed-size location catalogue and transactional Wi-Fi profile
// clone briefly coexist on this task. Keep comfortable headroom for mbedTLS.
constexpr uint32_t kNetworkTaskStackBytes = 24576;
constexpr uint8_t kNetworkTaskPriority = 1;
constexpr int kNetworkTaskCore = 0;

constexpr long kOfflineRssi = -127;
constexpr uint32_t kMinimumValidUnixTime = 1700000000U;
constexpr const char *kNtpServer1 = "pool.ntp.org";
constexpr const char *kNtpServer2 = "time.nist.gov";

}  // namespace app_config
