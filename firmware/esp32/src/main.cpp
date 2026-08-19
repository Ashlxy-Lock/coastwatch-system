#include <Arduino.h>

#include <cstdio>
#include <cstring>

#include "app_config.h"
#include "display.h"
#include "network_uplink.h"
#include "openmv_control.h"
#include "persistent_sequence.h"
#include "protocol.h"
#include "ring_buffer.h"
#include "sensor_logic.h"
#include "touch.h"
#include "ultrasonic_sensor.h"

namespace {

UltrasonicSensorConfig makeUltrasonicConfig() {
  UltrasonicSensorConfig config{};
  config.trigger_pin = app_config::kUltrasonicTriggerPin;
  config.echo_pin = app_config::kUltrasonicEchoPin;
  config.ping_interval_ms = 100U;
  config.echo_quiet_ms = 50U;
  config.echo_timeout_us = 30000U;
  config.trigger_pulse_us = 10U;
  config.healthy_freshness_ms = sensor_logic::config::kUltrasonicFreshMs;
  config.minimum_distance_mm = sensor_logic::config::kDistanceMinMm;
  config.maximum_distance_mm = sensor_logic::config::kDistanceMaxMm;
  return config;
}

HardwareSerial openmv_uart(1);
ByteRingBuffer<app_config::kUartRingCapacity> uart_ring;
LineReader<app_config::kMaxFrameBytes> line_reader;
NetworkUplink network_uplink;
CoastalDisplay coastal_display;
Ft5x06Touch coastal_touch;
UltrasonicSensor ultrasonic_device(makeUltrasonicConfig());
sensor_logic::SensorState sensor_state{};
PersistentSequence telemetry_sequence;

constexpr TouchRegion kSelectedAreaRegion{28U, 88U, 314U, 330U};
constexpr TouchRegion kNetworkWifiHeaderRegion{450U, 15U, 130U, 38U};
constexpr TouchRegion kEnvironmentWifiRegion{480U, 15U, 130U, 38U};
constexpr TouchRegion kEnvironmentRiskRegion{625U, 15U, 145U, 38U};
constexpr TouchRegion kRiskModelsRegion{450U, 15U, 110U, 38U};
constexpr TouchRegion kRiskWeatherRegion{574U, 15U, 92U, 38U};
constexpr TouchRegion kRiskWifiRegion{680U, 15U, 92U, 38U};
constexpr TouchRegion kNetworkWifiCardRegion{28U, 270U, 236U, 138U};
constexpr int kBootButtonPin = 0;
constexpr uint32_t kBootHoldForWifiMs = 1500U;
constexpr uint32_t kWifiKeyFeedbackDurationMs = 650U;
constexpr uint32_t kCollectionActionCooldownMs = 250U;
constexpr uint32_t kStopConfirmationWindowMs = 5000U;
constexpr uint32_t kCollectionUploadMaximumAgeMs = 2500U;
// The local sensor runtime publishes every 500 ms. Five missed publications
// mark the dashboard value offline.
constexpr uint32_t kTelemetryDisplayMaximumAgeMs = 2500U;

enum class ScreenMode : uint8_t {
  kWeather = 0,
  kLocationPicker,
  kLocationSearch,
  kWifiPicker,
  kWifiForgetConfirm,
  kWifiPassword,
  kModels,
  kCollection,
};

enum class DisplayedWeatherPage : uint8_t {
  kNone = 0,
  kNetworkStatus,
  kRiskOverview,
  kEnvironment,
};

uint32_t valid_vision_frames = 0U;
uint32_t invalid_vision_frames = 0U;
uint32_t uart_overflows = 0U;
uint32_t last_local_telemetry_ms = 0U;
uint32_t last_openmv_control_ms = 0U;
uint16_t openmv_control_sequence = 0U;
bool have_logged_openmv_control = false;
OpenMvControlDecision logged_openmv_control{};
uint32_t fallback_local_sequence = 0U;
bool sequence_failure_logged = false;
UltrasonicSensorState logged_ultrasonic_state =
    UltrasonicSensorState::kUninitialized;
UltrasonicSensorFault logged_ultrasonic_fault = UltrasonicSensorFault::kNone;
uint32_t last_display_poll_ms = 0U;
bool have_displayed_environment = false;
EnvironmentSnapshot displayed_environment{};
bool have_displayed_network_status = false;
NetworkStatus displayed_network_status{false, false, false, -127, 0U};
bool have_displayed_risk = false;
RiskSnapshot displayed_risk{};
RiskFetchStatus displayed_risk_status{RiskAvailability::kWaiting, 0};
TelemetryFrame latest_telemetry{};
uint32_t latest_telemetry_received_ms = 0U;
bool have_latest_telemetry = false;
TelemetrySnapshot displayed_telemetry{};
bool have_displayed_telemetry = false;
ModelCatalog displayed_models{};
ModelCatalog model_catalog_snapshot{};
uint32_t model_rendered_revision = UINT32_MAX;
uint32_t collection_rendered_revision = UINT32_MAX;
uint32_t collection_model_rendered_revision = UINT32_MAX;
bool collection_stop_confirmation_pending = false;
bool collection_rendered_stop_confirmation = false;
SimulationUltrasonicQuality collection_rendered_ultrasonic_quality =
    static_cast<SimulationUltrasonicQuality>(UINT8_MAX);
bool collection_rendered_upload_delayed = false;
bool have_collection_rendered_timed_status = false;
uint32_t collection_stop_confirmation_started_ms = 0U;
uint32_t last_collection_action_ms = 0U;
bool have_collection_action = false;
bool show_weather_detail = false;
// The catalogue is about 2.5 KB. Keep its UI snapshot in static storage so
// opening or touching the picker never places it on Arduino loop-task stack.
LocationCatalog picker_catalog_snapshot{};
ScreenMode screen_mode = ScreenMode::kWeather;
DisplayedWeatherPage displayed_weather_page = DisplayedWeatherPage::kNone;
size_t picker_page = 0U;
int picker_selected_index = -1;
bool picker_selection_initialized = false;
uint32_t picker_rendered_revision = UINT32_MAX;
size_t picker_rendered_page = SIZE_MAX;
int picker_rendered_selection = -2;
char location_search_query[kLocationSearchQueryBytes]{};
size_t location_search_query_length = 0U;
WifiKeyboardMode location_search_keyboard_mode = WifiKeyboardMode::kUpper;
size_t location_search_rendered_length = SIZE_MAX;
WifiKeyboardMode location_search_rendered_keyboard_mode =
    static_cast<WifiKeyboardMode>(UINT8_MAX);
WifiCatalog wifi_catalog_snapshot{};
size_t wifi_page = 0U;
size_t wifi_selected_index = 0U;
WifiNetworkOption wifi_selected_network{};
char wifi_forget_ssid[kWifiSsidBytes]{};
bool wifi_forget_submitted = false;
char wifi_password[kWifiPasswordBytes]{};
size_t wifi_password_length = 0U;
char wifi_key_feedback[12]{};
uint32_t wifi_key_feedback_started_ms = 0U;
WifiKeyboardMode wifi_keyboard_mode = WifiKeyboardMode::kLower;
bool wifi_connect_submitted = false;
uint32_t wifi_connected_since_ms = 0U;
uint32_t wifi_rendered_revision = UINT32_MAX;
size_t wifi_rendered_page = SIZE_MAX;
size_t wifi_rendered_password_length = SIZE_MAX;
WifiKeyboardMode wifi_rendered_keyboard_mode =
    static_cast<WifiKeyboardMode>(UINT8_MAX);
WifiSetupState wifi_rendered_state = static_cast<WifiSetupState>(UINT8_MAX);
WifiSetupError wifi_rendered_error = static_cast<WifiSetupError>(UINT8_MAX);
bool boot_button_was_down = false;
bool boot_button_handled = false;
uint32_t boot_button_down_since_ms = 0U;

void closeWifiSetup();

void clearLocationSearchQuery() {
  std::memset(location_search_query, 0, sizeof(location_search_query));
  location_search_query_length = 0U;
}

void invalidateLocationSearchRendering() {
  location_search_rendered_length = SIZE_MAX;
  location_search_rendered_keyboard_mode =
      static_cast<WifiKeyboardMode>(UINT8_MAX);
}

bool sameDisplayedEnvironment(const EnvironmentSnapshot &left,
                              const EnvironmentSnapshot &right) {
  return std::strcmp(left.location, right.location) == 0 &&
         std::strcmp(left.display_location, right.display_location) == 0 &&
         std::strcmp(left.weather, right.weather) == 0 &&
         std::strcmp(left.source, right.source) == 0 &&
         std::strcmp(left.provider, right.provider) == 0 &&
         std::strcmp(left.tide_status, right.tide_status) == 0 &&
         std::strcmp(left.updated_at, right.updated_at) == 0 &&
         left.location_kind == right.location_kind &&
         left.weather_code == right.weather_code &&
         left.air_temperature_c == right.air_temperature_c &&
         left.humidity_percent == right.humidity_percent &&
         left.wind_speed_kmh == right.wind_speed_kmh &&
         left.wind_direction_deg == right.wind_direction_deg &&
         left.water_temperature_c == right.water_temperature_c &&
         left.wave_height_m == right.wave_height_m &&
         left.wave_period_s == right.wave_period_s &&
         left.sea_level_height_m == right.sea_level_height_m &&
         left.ocean_current_velocity_kmh == right.ocean_current_velocity_kmh &&
         left.ocean_current_direction_deg == right.ocean_current_direction_deg &&
         left.valid_fields == right.valid_fields && left.stale == right.stale;
}

bool sameDisplayedNetworkStatus(const NetworkStatus &left,
                                const NetworkStatus &right) {
  // Deliberately ignore RSSI and time so normal signal jitter and the ticking
  // clock do not trigger a full-screen DMA transfer.
  return left.wifi_connected == right.wifi_connected &&
         left.server_reachable == right.server_reachable &&
         left.environment_reachable == right.environment_reachable;
}

bool sameDisplayedRisk(const RiskSnapshot &left, const RiskSnapshot &right,
                       const RiskFetchStatus &left_status,
                       const RiskFetchStatus &right_status) {
  return left_status.availability == right_status.availability &&
         left_status.http_status == right_status.http_status &&
         std::strcmp(left.risk_name, right.risk_name) == 0 &&
         std::strcmp(left.data_quality, right.data_quality) == 0 &&
         std::strcmp(left.deployment_mode, right.deployment_mode) == 0 &&
         std::strcmp(left.model_version, right.model_version) == 0 &&
         left.risk_level == right.risk_level &&
         left.environmental_probability == right.environmental_probability &&
         left.local_alarm_level == right.local_alarm_level &&
         left.forecast_horizon_hours == right.forecast_horizon_hours &&
         left.degraded == right.degraded && left.stale == right.stale;
}

bool sameDisplayedTelemetry(const TelemetrySnapshot &left,
                            const TelemetrySnapshot &right) {
  return left.has_telemetry == right.has_telemetry &&
         left.telemetry_fresh == right.telemetry_fresh &&
         left.ultrasonic_available == right.ultrasonic_available &&
         left.latest.distance_mm == right.latest.distance_mm &&
         left.latest.water_rise_mm == right.latest.water_rise_mm &&
         left.latest.rise_rate_mm_s == right.latest.rise_rate_mm_s &&
         left.latest.person_detected == right.latest.person_detected &&
         left.latest.alarm_level == right.latest.alarm_level &&
         left.latest.health_flags == right.latest.health_flags;
}

void clearWifiPassword() {
  volatile char *cursor = wifi_password;
  for (size_t index = 0U; index < sizeof(wifi_password); ++index) {
    cursor[index] = '\0';
  }
  wifi_password_length = 0U;
}

void clearWifiKeyFeedback() {
  std::memset(wifi_key_feedback, 0, sizeof(wifi_key_feedback));
  wifi_key_feedback_started_ms = 0U;
}

void setWifiKeyFeedback(const char *label) {
  if (label == nullptr || label[0] == '\0') {
    clearWifiKeyFeedback();
    return;
  }
  std::snprintf(wifi_key_feedback, sizeof(wifi_key_feedback), "%s", label);
  wifi_key_feedback_started_ms = millis();
  wifi_rendered_password_length = SIZE_MAX;
}

bool wifiPasswordReady() {
  return wifi_selected_network.supported &&
         (!wifi_selected_network.secured ||
          (wifi_password_length >= 8U && wifi_password_length <= 63U));
}

void invalidateWifiRendering() {
  wifi_rendered_revision = UINT32_MAX;
  wifi_rendered_page = SIZE_MAX;
  wifi_rendered_password_length = SIZE_MAX;
  wifi_rendered_keyboard_mode = static_cast<WifiKeyboardMode>(UINT8_MAX);
  wifi_rendered_state = static_cast<WifiSetupState>(UINT8_MAX);
  wifi_rendered_error = static_cast<WifiSetupError>(UINT8_MAX);
}

void logInvalidVisionFrame(TelParseResult result) {
  ++invalid_vision_frames;
  if (invalid_vision_frames <= 5U || invalid_vision_frames % 25U == 0U) {
    Serial.printf("[OPENMV] dropped VIS frame reason=%s invalid_total=%lu\n",
                  telParseResultName(result),
                  static_cast<unsigned long>(invalid_vision_frames));
  }
}

void processVisionLine(const char *line) {
  VisionFrame vision{};
  const TelParseResult result = parseVisionFrame(line, &vision);
  if (result != TelParseResult::kOk) {
    logInvalidVisionFrame(result);
    return;
  }

  ++valid_vision_frames;
  sensor_logic::accept_vision(&sensor_state, millis(), vision.person_detected,
                              vision.in_zone);
}

void pollOpenMvUart() {
  while (openmv_uart.available() > 0) {
    const int value = openmv_uart.read();
    if (value < 0) {
      break;
    }
    if (!uart_ring.push(static_cast<uint8_t>(value))) {
      ++uart_overflows;
      uart_ring.clear();
      line_reader.discardUntilNewline();
      Serial.printf("[OPENMV] ERROR RX ring overflow total=%lu\n",
                    static_cast<unsigned long>(uart_overflows));
    }
  }

  uint8_t value = 0U;
  while (uart_ring.pop(&value)) {
    const char *line = nullptr;
    const LineEvent event = line_reader.push(static_cast<char>(value), &line);
    if (event == LineEvent::kReady) {
      processVisionLine(line);
    } else if (event == LineEvent::kDroppedOversize) {
      ++invalid_vision_frames;
      Serial.printf("[OPENMV] dropped frame longer than %u bytes\n",
                    static_cast<unsigned int>(app_config::kMaxFrameBytes));
    }
  }
}

void pollUltrasonicSensor(uint32_t now_ms) {
  if (!app_config::kUltrasonicEchoLevelShiftVerified) {
    return;
  }

  const UltrasonicSensorResult result = ultrasonic_device.poll();
  if (result.event == UltrasonicSensorEvent::kSample) {
    (void)sensor_logic::accept_distance(&sensor_state, now_ms,
                                        result.distance_mm);
  } else if (result.event == UltrasonicSensorEvent::kTimeout ||
             result.event == UltrasonicSensorEvent::kOutOfRange) {
    sensor_logic::note_timeout(&sensor_state, now_ms);
  } else if (result.event == UltrasonicSensorEvent::kFault) {
    sensor_logic::note_hardware_fault(&sensor_state);
  }

  if (result.state != logged_ultrasonic_state ||
      result.fault != logged_ultrasonic_fault) {
    logged_ultrasonic_state = result.state;
    logged_ultrasonic_fault = result.fault;
    Serial.printf("[ULTRASONIC] state=%u fault=%u armed=%u healthy=%u\n",
                  static_cast<unsigned>(result.state),
                  static_cast<unsigned>(result.fault), result.armed ? 1U : 0U,
                  result.healthy ? 1U : 0U);
  }
}

bool sameOpenMvControl(const OpenMvControlDecision &left,
                       const OpenMvControlDecision &right) {
  return left.trusted_model_result == right.trusted_model_result &&
         left.fail_safe == right.fail_safe &&
         left.green_safe == right.green_safe &&
         left.model_danger == right.model_danger &&
         left.local_water_danger == right.local_water_danger &&
         left.danger == right.danger &&
         left.person_enable == right.person_enable &&
         left.environmental_level == right.environmental_level;
}

void sendOpenMvControlIfDue(uint32_t now_ms) {
  if (static_cast<uint32_t>(now_ms - last_openmv_control_ms) <
      app_config::kOpenMvControlIntervalMs) {
    return;
  }
  last_openmv_control_ms = now_ms;

  const RiskSnapshot risk = network_uplink.risk();
  const RiskFetchStatus status = network_uplink.riskStatus();
  // publishLocalTelemetryIfDue() runs immediately before this function and
  // publishes the ESP32's current local alarm. The server's
  // RiskSnapshot::local_alarm_level is an asynchronous echo and can lag a
  // real local warning by multiple polling intervals.
  const uint8_t live_local_alarm_level =
      have_latest_telemetry
          ? latest_telemetry.alarm_level
          : static_cast<uint8_t>(sensor_logic::AlarmLevel::kFault);
  const bool live_ultrasonic_health_ok =
      have_latest_telemetry &&
      (latest_telemetry.health_flags & sensor_logic::kHealthUltrasonicOk) !=
          0U;
  const bool live_openmv_health_ok =
      have_latest_telemetry &&
      (latest_telemetry.health_flags & sensor_logic::kHealthOpenMvOk) != 0U;
  const OpenMvControlDecision decision = decideOpenMvControl(
      risk, status.availability == RiskAvailability::kReady,
      live_local_alarm_level,
      have_latest_telemetry ? latest_telemetry.water_rise_mm : 0,
      have_latest_telemetry ? latest_telemetry.rise_rate_mm_s : 0,
      live_ultrasonic_health_ok,
      live_openmv_health_ok, now_ms,
      app_config::kOpenMvRiskMaximumAgeMs);

  char frame[64]{};
  if (!buildOpenMvControlFrame(
          frame, sizeof(frame), openmv_control_sequence, decision.danger,
          decision.person_enable, decision.environmental_level)) {
    Serial.println("[OPENMV] ERROR failed to build CTL frame");
    return;
  }

  const size_t frame_length = std::strlen(frame);
  const size_t written = openmv_uart.write(
      reinterpret_cast<const uint8_t *>(frame), frame_length);
  if (written != frame_length) {
    Serial.printf("[OPENMV] ERROR partial CTL write=%u/%u\n",
                  static_cast<unsigned>(written),
                  static_cast<unsigned>(frame_length));
    return;
  }
  ++openmv_control_sequence;

  if (!have_logged_openmv_control ||
      !sameOpenMvControl(decision, logged_openmv_control)) {
    Serial.printf(
        "[OPENMV] CTL model_trusted=%u fail_safe=%u green_safe=%u "
        "model_danger=%u water_danger=%u danger=%u person_enable=%u "
        "environmental_level=%u\n",
        decision.trusted_model_result ? 1U : 0U,
        decision.fail_safe ? 1U : 0U, decision.green_safe ? 1U : 0U,
        decision.model_danger ? 1U : 0U,
        decision.local_water_danger ? 1U : 0U,
        decision.danger ? 1U : 0U,
        decision.person_enable ? 1U : 0U,
        static_cast<unsigned>(decision.environmental_level));
    logged_openmv_control = decision;
    have_logged_openmv_control = true;
  }
}

void publishLocalTelemetryIfDue(uint32_t now_ms) {
  if (static_cast<uint32_t>(now_ms - last_local_telemetry_ms) <
      app_config::kLocalTelemetryPeriodMs) {
    return;
  }
  last_local_telemetry_ms = now_ms;

  const NetworkStatus network = network_uplink.status();
  sensor_logic::accept_network(&sensor_state, now_ms,
                               network.wifi_connected,
                               network.server_reachable);
  sensor_logic::tick(&sensor_state, now_ms);

  uint32_t sequence = fallback_local_sequence++;
  const bool sequence_is_persistent = telemetry_sequence.next(&sequence);
  const TelemetryFrame telemetry =
      sensor_logic::snapshot(sensor_state, now_ms, sequence);
  latest_telemetry = telemetry;
  latest_telemetry_received_ms = now_ms;
  have_latest_telemetry = true;

  Serial.printf(
      "[LOCAL] TEL seq=%lu distance=%lu rise=%ld rate=%ld person=%u "
      "alarm=%u health=0x%lX\n",
      static_cast<unsigned long>(telemetry.seq),
      static_cast<unsigned long>(telemetry.distance_mm),
      static_cast<long>(telemetry.water_rise_mm),
      static_cast<long>(telemetry.rise_rate_mm_s),
      telemetry.person_detected ? 1U : 0U,
      static_cast<unsigned>(telemetry.alarm_level),
      static_cast<unsigned long>(telemetry.health_flags));

  if (!sequence_is_persistent) {
    if (!sequence_failure_logged) {
      sequence_failure_logged = true;
      Serial.println(
          "[LOCAL] ERROR NVS sequence unavailable; server upload disabled");
    }
    return;
  }
  if (!network_uplink.submit(telemetry)) {
    Serial.println("[NET] WARN telemetry queue unavailable");
  }
}

void pollLocalSensorRuntime() {
  const uint32_t now_ms = millis();
  pollOpenMvUart();
  pollUltrasonicSensor(now_ms);
  publishLocalTelemetryIfDue(now_ms);
  sendOpenMvControlIfDue(now_ms);
}

void refreshDisplayIfNeeded() {
  if (!coastal_display.ready()) {
    return;
  }

  const uint32_t now_ms = millis();
  const uint32_t poll_interval_ms =
      (screen_mode == ScreenMode::kWeather ||
       screen_mode == ScreenMode::kCollection)
          ? 250U
          : 50U;
  if (static_cast<uint32_t>(now_ms - last_display_poll_ms) <
      poll_interval_ms) {
    return;
  }
  last_display_poll_ms = now_ms;

  if (screen_mode == ScreenMode::kModels) {
    network_uplink.copyModelCatalog(&model_catalog_snapshot);
    if (model_catalog_snapshot.revision != model_rendered_revision) {
      if (coastal_display.showModelCatalog(model_catalog_snapshot)) {
        model_rendered_revision = model_catalog_snapshot.revision;
      }
    }
    return;
  }

  if (screen_mode == ScreenMode::kCollection) {
    network_uplink.copyModelCatalog(&model_catalog_snapshot);
    const SimulationSnapshot simulation = network_uplink.simulation();
    if (collection_stop_confirmation_pending &&
        (!simulationCanStop(simulation.state) ||
         static_cast<uint32_t>(now_ms -
                               collection_stop_confirmation_started_ms) >=
             kStopConfirmationWindowMs)) {
      collection_stop_confirmation_pending = false;
      Serial.println("[UI] stop confirmation expired or no longer valid");
    }
    const SimulationUltrasonicQuality ultrasonic_quality =
        simulationUltrasonicQuality(simulation, now_ms,
                                    kTelemetryDisplayMaximumAgeMs);
    const bool upload_delayed =
        simulationSessionOpen(simulation.state) &&
        simulation.has_upload_ack && simulation.last_upload_ack_succeeded &&
        static_cast<uint32_t>(now_ms - simulation.last_upload_ack_ms) >
            kCollectionUploadMaximumAgeMs;
    if (simulation.revision != collection_rendered_revision ||
        model_catalog_snapshot.revision !=
            collection_model_rendered_revision ||
        collection_stop_confirmation_pending !=
            collection_rendered_stop_confirmation ||
        !have_collection_rendered_timed_status ||
        ultrasonic_quality != collection_rendered_ultrasonic_quality ||
        upload_delayed != collection_rendered_upload_delayed) {
      if (coastal_display.showSimulationCollection(simulation,
                                                   model_catalog_snapshot,
                                                   collection_stop_confirmation_pending)) {
        collection_rendered_revision = simulation.revision;
        collection_model_rendered_revision =
            model_catalog_snapshot.revision;
        collection_rendered_stop_confirmation =
            collection_stop_confirmation_pending;
        collection_rendered_ultrasonic_quality = ultrasonic_quality;
        collection_rendered_upload_delayed = upload_delayed;
        have_collection_rendered_timed_status = true;
      }
    }
    return;
  }

  if (screen_mode == ScreenMode::kWifiPicker) {
    network_uplink.copyWifiCatalog(&wifi_catalog_snapshot);
    const WifiCatalog &catalog = wifi_catalog_snapshot;
    const size_t page_count =
        catalog.count == 0U
            ? 1U
            : (catalog.count + wifi_setup_ui::kPageSize - 1U) /
                  wifi_setup_ui::kPageSize;
    if (wifi_page >= page_count) {
      wifi_page = page_count - 1U;
    }
    if (catalog.revision != wifi_rendered_revision ||
        wifi_page != wifi_rendered_page) {
      if (coastal_display.showWifiPicker(catalog, wifi_page)) {
        wifi_rendered_revision = catalog.revision;
        wifi_rendered_page = wifi_page;
      }
    }
    return;
  }

  if (screen_mode == ScreenMode::kWifiForgetConfirm) {
    network_uplink.copyWifiCatalog(&wifi_catalog_snapshot);
    WifiSetupState visible_state = wifi_catalog_snapshot.state;
    WifiSetupError visible_error = wifi_catalog_snapshot.error;
    const bool forget_succeeded =
        wifi_forget_submitted && visible_state == WifiSetupState::kReady &&
        wifi_catalog_snapshot.active_ssid[0] == '\0';
    if (forget_succeeded) {
      wifi_forget_submitted = false;
      wifi_forget_ssid[0] = '\0';
      screen_mode = ScreenMode::kWifiPicker;
      invalidateWifiRendering();
      Serial.println("[UI] saved wifi forgotten; returning to picker");
      return;
    }
    if (visible_state == WifiSetupState::kError &&
        visible_error == WifiSetupError::kForgetFailed) {
      wifi_forget_submitted = false;
    } else if (!wifi_forget_submitted) {
      // A previous scan error is unrelated to this confirmation screen.
      visible_state = WifiSetupState::kReady;
      visible_error = WifiSetupError::kNone;
    }
    if (wifi_catalog_snapshot.revision != wifi_rendered_revision ||
        visible_state != wifi_rendered_state ||
        visible_error != wifi_rendered_error) {
      if (coastal_display.showWifiForgetConfirm(
              wifi_forget_ssid, visible_state, visible_error)) {
        wifi_rendered_revision = wifi_catalog_snapshot.revision;
        wifi_rendered_state = visible_state;
        wifi_rendered_error = visible_error;
      }
    }
    return;
  }

  if (screen_mode == ScreenMode::kWifiPassword) {
    network_uplink.copyWifiCatalog(&wifi_catalog_snapshot);
    bool key_feedback_expired = false;
    if (wifi_key_feedback[0] != '\0' &&
        static_cast<uint32_t>(now_ms - wifi_key_feedback_started_ms) >=
            kWifiKeyFeedbackDurationMs) {
      clearWifiKeyFeedback();
      key_feedback_expired = true;
    }
    WifiSetupState visible_state = wifi_catalog_snapshot.state;
    WifiSetupError visible_error = wifi_catalog_snapshot.error;
    if (wifi_connect_submitted && visible_state == WifiSetupState::kReady) {
      visible_state = WifiSetupState::kConnecting;
    }
    if (visible_state == WifiSetupState::kError) {
      wifi_connect_submitted = false;
      wifi_connected_since_ms = 0U;
    } else if (visible_state == WifiSetupState::kConnected) {
      if (wifi_connected_since_ms == 0U) {
        wifi_connected_since_ms = now_ms;
      }
      if (static_cast<uint32_t>(now_ms - wifi_connected_since_ms) >= 900U) {
        closeWifiSetup();
        return;
      }
    } else {
      wifi_connected_since_ms = 0U;
    }

    if (wifi_catalog_snapshot.revision != wifi_rendered_revision ||
        wifi_password_length != wifi_rendered_password_length ||
        wifi_keyboard_mode != wifi_rendered_keyboard_mode ||
        visible_state != wifi_rendered_state ||
        visible_error != wifi_rendered_error) {
      if (coastal_display.showWifiPassword(
              wifi_selected_network, wifi_password, wifi_password_length,
              wifi_keyboard_mode, visible_state, visible_error,
              wifi_key_feedback)) {
        wifi_rendered_revision = wifi_catalog_snapshot.revision;
        wifi_rendered_password_length = wifi_password_length;
        wifi_rendered_keyboard_mode = wifi_keyboard_mode;
        wifi_rendered_state = visible_state;
        wifi_rendered_error = visible_error;
      }
    } else if (key_feedback_expired) {
      coastal_display.updateWifiKeyFeedback(nullptr);
    }
    return;
  }

  if (screen_mode == ScreenMode::kLocationSearch) {
    if (location_search_query_length != location_search_rendered_length ||
        location_search_keyboard_mode !=
            location_search_rendered_keyboard_mode) {
      if (coastal_display.showLocationSearch(
              location_search_query, location_search_query_length,
              location_search_keyboard_mode)) {
        location_search_rendered_length = location_search_query_length;
        location_search_rendered_keyboard_mode =
            location_search_keyboard_mode;
      }
    }
    return;
  }

  if (screen_mode == ScreenMode::kLocationPicker) {
    network_uplink.copyLocationCatalog(&picker_catalog_snapshot);
    const LocationCatalog &catalog = picker_catalog_snapshot;
    const size_t picker_page_count =
        catalog.count == 0U
            ? 1U
            : (catalog.count + location_picker_ui::kPageSize - 1U) /
                  location_picker_ui::kPageSize;
    if (picker_page >= picker_page_count) {
      picker_page = picker_page_count - 1U;
    }
    if (!picker_selection_initialized && catalog.count > 0U &&
        (catalog.state == LocationCatalogState::kReady ||
         catalog.state == LocationCatalogState::kSaved)) {
      const EnvironmentSnapshot current = network_uplink.environment();
      for (size_t index = 0U; index < catalog.count; ++index) {
        if ((current.display_location[0] != '\0' &&
             std::strcmp(catalog.options[index].display_location,
                         current.display_location) == 0) ||
            std::strcmp(catalog.options[index].location, current.location) ==
                0) {
          picker_selected_index = static_cast<int>(index);
          picker_page = index / location_picker_ui::kPageSize;
          break;
        }
      }
      picker_selection_initialized = true;
    }

    if (catalog.state == LocationCatalogState::kSaved) {
      const EnvironmentSnapshot refreshed = network_uplink.environment();
      const bool selection_valid =
          picker_selected_index >= 0 &&
          static_cast<size_t>(picker_selected_index) < catalog.count;
      const bool environment_matches =
          selection_valid &&
          ((refreshed.display_location[0] != '\0' &&
            std::strcmp(
                refreshed.display_location,
                catalog.options[picker_selected_index].display_location) ==
                0) ||
           std::strcmp(refreshed.location,
                       catalog.options[picker_selected_index].location) == 0);
      if (environment_matches) {
        screen_mode = ScreenMode::kWeather;
        show_weather_detail = false;
        have_displayed_environment = false;
        have_displayed_risk = false;
        have_displayed_network_status = false;
        last_display_poll_ms = 0U;
        return;
      }
    }

    if (catalog.revision != picker_rendered_revision ||
        picker_page != picker_rendered_page ||
        picker_selected_index != picker_rendered_selection) {
      if (coastal_display.showLocationPicker(catalog, picker_page,
                                             picker_selected_index)) {
        picker_rendered_revision = catalog.revision;
        picker_rendered_page = picker_page;
        picker_rendered_selection = picker_selected_index;
      }
    }
    return;
  }

  const EnvironmentSnapshot snapshot = network_uplink.environment();
  if (snapshot.fetched_at_ms == 0U) {
    const NetworkStatus status = network_uplink.status();
    if (have_displayed_network_status &&
        sameDisplayedNetworkStatus(status, displayed_network_status)) {
      return;
    }
    if (coastal_display.showNetworkStatus(
            status.wifi_connected, status.server_reachable,
            status.environment_reachable)) {
      displayed_network_status = status;
      have_displayed_network_status = true;
      have_displayed_environment = false;
      displayed_weather_page = DisplayedWeatherPage::kNetworkStatus;
    }
    return;
  }
  if (!show_weather_detail) {
    const RiskSnapshot risk = network_uplink.risk();
    const RiskFetchStatus risk_status = network_uplink.riskStatus();
    const TelemetrySnapshot telemetry = makeTelemetrySnapshot(
        latest_telemetry, have_latest_telemetry,
        latest_telemetry_received_ms, now_ms,
        kTelemetryDisplayMaximumAgeMs);
    network_uplink.copyModelCatalog(&model_catalog_snapshot);
    if (have_displayed_risk &&
        sameDisplayedRisk(risk, displayed_risk, risk_status,
                          displayed_risk_status) &&
        have_displayed_telemetry &&
        sameDisplayedTelemetry(telemetry, displayed_telemetry) &&
        have_displayed_environment &&
        sameDisplayedEnvironment(snapshot, displayed_environment) &&
        model_catalog_snapshot.revision == displayed_models.revision) {
      return;
    }
    if (coastal_display.showRiskOverview(
            risk, snapshot, model_catalog_snapshot, telemetry,
            static_cast<uint8_t>(risk_status.availability),
            risk_status.http_status)) {
      displayed_risk = risk;
      displayed_risk_status = risk_status;
      displayed_telemetry = telemetry;
      have_displayed_telemetry = true;
      displayed_environment = snapshot;
      displayed_models = model_catalog_snapshot;
      have_displayed_risk = true;
      have_displayed_environment = true;
      have_displayed_network_status = false;
      displayed_weather_page = DisplayedWeatherPage::kRiskOverview;
    }
    return;
  }

  if (have_displayed_environment &&
      sameDisplayedEnvironment(snapshot, displayed_environment)) {
    const bool loading_weather =
        std::strcmp(snapshot.weather, "UPDATING") == 0 &&
        (!environmentHasValue(snapshot, kEnvironmentHasAirTemperature) ||
         !environmentHasValue(snapshot, kEnvironmentHasWeatherCode));
    if (loading_weather) {
      coastal_display.animateEnvironmentLoading();
    }
    return;
  }

  if (coastal_display.showEnvironment(snapshot)) {
    displayed_environment = snapshot;
    have_displayed_environment = true;
    have_displayed_network_status = false;
    displayed_weather_page = DisplayedWeatherPage::kEnvironment;
  }
}

void openLocationPicker() {
  screen_mode = ScreenMode::kLocationPicker;
  picker_page = 0U;
  picker_selected_index = -1;
  picker_selection_initialized = false;
  picker_rendered_revision = UINT32_MAX;
  picker_rendered_page = SIZE_MAX;
  picker_rendered_selection = -2;
  last_display_poll_ms = 0U;
  network_uplink.requestLocationCatalog();
  Serial.println("[UI] opening on-device region picker");
}

void closeLocationPicker() {
  screen_mode = ScreenMode::kWeather;
  show_weather_detail = false;
  have_displayed_environment = false;
  have_displayed_risk = false;
  have_displayed_network_status = false;
  last_display_poll_ms = 0U;
  Serial.println("[UI] closing region picker");
}

void showWeatherDetail() {
  show_weather_detail = true;
  have_displayed_environment = false;
  have_displayed_network_status = false;
  last_display_poll_ms = 0U;
  Serial.println("[UI] opening weather detail");
}

void showRiskOverview() {
  show_weather_detail = false;
  have_displayed_environment = false;
  have_displayed_risk = false;
  have_displayed_network_status = false;
  last_display_poll_ms = 0U;
  Serial.println("[UI] returning to research risk overview");
}

void openModels() {
  screen_mode = ScreenMode::kModels;
  model_rendered_revision = UINT32_MAX;
  last_display_poll_ms = 0U;
  network_uplink.requestModelCatalog();
  Serial.println("[UI] opening server model library");
}

void closeModels() {
  screen_mode = ScreenMode::kWeather;
  show_weather_detail = false;
  have_displayed_environment = false;
  have_displayed_risk = false;
  have_displayed_network_status = false;
  last_display_poll_ms = 0U;
  Serial.println("[UI] closing model library");
}

void openCollection() {
  screen_mode = ScreenMode::kCollection;
  collection_stop_confirmation_pending = false;
  collection_rendered_stop_confirmation = false;
  collection_stop_confirmation_started_ms = 0U;
  have_collection_action = false;
  have_collection_rendered_timed_status = false;
  collection_rendered_revision = UINT32_MAX;
  collection_model_rendered_revision = UINT32_MAX;
  last_display_poll_ms = 0U;
  Serial.println("[UI] opening simulation data collection");
}

void closeCollection() {
  collection_stop_confirmation_pending = false;
  collection_stop_confirmation_started_ms = 0U;
  screen_mode = ScreenMode::kModels;
  model_rendered_revision = UINT32_MAX;
  last_display_poll_ms = 0U;
  Serial.println("[UI] closing simulation collection; session unchanged");
}

void handleModelPress(const TouchEvent &event) {
  const int16_t x = static_cast<int16_t>(event.point.x);
  const int16_t y = static_cast<int16_t>(event.point.y);
  if (model_ui::kBackButton.contains(x, y)) {
    closeModels();
    return;
  }
  if (model_ui::kCollectionButton.contains(x, y)) {
    openCollection();
    return;
  }

  network_uplink.copyModelCatalog(&model_catalog_snapshot);
  if (model_catalog_snapshot.state == ModelCatalogState::kSelecting) {
    return;
  }
  for (size_t index = 0U; index < model_ui::kCardCount; ++index) {
    if (!model_ui::cardRect(index).contains(x, y) ||
        index >= model_catalog_snapshot.count) {
      continue;
    }
    const ModelOption &model = model_catalog_snapshot.models[index];
    if (!modelStatusSelectable(model.status)) {
      Serial.printf("[UI] model not selectable id=%s status=%s\n",
                    model.model_id, modelStatusName(model.status));
      return;
    }
    if (std::strcmp(model_catalog_snapshot.selected_model_id,
                    model.model_id) == 0) {
      Serial.printf("[UI] model already selected id=%s\n", model.model_id);
      return;
    }
    if (network_uplink.selectModel(model.model_id)) {
      model_rendered_revision = UINT32_MAX;
      last_display_poll_ms = 0U;
      Serial.printf("[UI] model selection submitted id=%s\n",
                    model.model_id);
    }
    return;
  }
}

void handleCollectionPress(const TouchEvent &event) {
  const int16_t x = static_cast<int16_t>(event.point.x);
  const int16_t y = static_cast<int16_t>(event.point.y);
  if (model_ui::kBackButton.contains(x, y)) {
    closeCollection();
    return;
  }
  if (!model_ui::kSessionButton.contains(x, y)) {
    return;
  }
  const uint32_t now_ms = millis();
  if (have_collection_action &&
      static_cast<uint32_t>(now_ms - last_collection_action_ms) <
          kCollectionActionCooldownMs) {
    Serial.println("[UI] collection action ignored by cooldown");
    return;
  }
  have_collection_action = true;
  last_collection_action_ms = now_ms;

  const SimulationSnapshot simulation = network_uplink.simulation();
  bool accepted = false;
  const CollectionButtonAction action = collectionButtonAction(
      simulation.state, collection_stop_confirmation_pending);
  if (action == CollectionButtonAction::kArmStopConfirmation) {
    collection_stop_confirmation_pending = true;
    collection_stop_confirmation_started_ms = now_ms;
    collection_rendered_revision = UINT32_MAX;
    last_display_poll_ms = 0U;
    Serial.println("[UI] simulation stop confirmation armed");
    return;
  }
  if (action == CollectionButtonAction::kStart) {
    collection_stop_confirmation_pending = false;
    accepted = network_uplink.requestSimulationStart();
    Serial.println(accepted ? "[UI] simulation start submitted"
                            : "[UI] simulation start rejected");
  } else if (action == CollectionButtonAction::kStop) {
    collection_stop_confirmation_pending = false;
    accepted = network_uplink.requestSimulationStop();
    Serial.println(accepted ? "[UI] simulation stop submitted"
                            : "[UI] simulation stop rejected");
  }
  if (accepted) {
    collection_rendered_revision = UINT32_MAX;
    last_display_poll_ms = 0U;
  }
}

void openLocationSearch() {
  clearLocationSearchQuery();
  location_search_keyboard_mode = WifiKeyboardMode::kUpper;
  invalidateLocationSearchRendering();
  screen_mode = ScreenMode::kLocationSearch;
  last_display_poll_ms = 0U;
  Serial.println("[UI] opening global location search");
}

void closeLocationSearch() {
  clearLocationSearchQuery();
  invalidateLocationSearchRendering();
  screen_mode = ScreenMode::kLocationPicker;
  picker_rendered_revision = UINT32_MAX;
  picker_rendered_page = SIZE_MAX;
  picker_rendered_selection = -2;
  last_display_poll_ms = 0U;
  Serial.println("[UI] closing global location search");
}

bool pickerBusy(LocationCatalogState state) {
  return state == LocationCatalogState::kLoading ||
         state == LocationCatalogState::kSaving;
}

void handleLocationPickerPress(const TouchEvent &event) {
  network_uplink.copyLocationCatalog(&picker_catalog_snapshot);
  const LocationCatalog &catalog = picker_catalog_snapshot;
  const bool busy = pickerBusy(catalog.state);
  const int16_t x = static_cast<int16_t>(event.point.x);
  const int16_t y = static_cast<int16_t>(event.point.y);
  const size_t page_count =
      catalog.count == 0U
          ? 1U
          : (catalog.count + location_picker_ui::kPageSize - 1U) /
                location_picker_ui::kPageSize;
  if (picker_page >= page_count) {
    picker_page = page_count - 1U;
  }

  if (location_picker_ui::kBackButton.contains(x, y)) {
    closeLocationPicker();
    return;
  }
  if (!busy && location_picker_ui::kSearchButton.contains(x, y)) {
    openLocationSearch();
    return;
  }

  for (size_t slot = 0U; slot < location_picker_ui::kPageSize; ++slot) {
    if (!location_picker_ui::cardRect(slot).contains(x, y)) {
      continue;
    }
    const size_t index = picker_page * location_picker_ui::kPageSize + slot;
    if (!busy && index < catalog.count) {
      picker_selected_index = static_cast<int>(index);
      picker_selection_initialized = true;
      picker_rendered_selection = -2;
      Serial.printf("[UI] region highlighted index=%u id=%s\n",
                    static_cast<unsigned int>(index),
                    catalog.options[index].id);
    }
    return;
  }

  if (!busy && location_picker_ui::kPreviousButton.contains(x, y) &&
      picker_page > 0U) {
    --picker_page;
    picker_rendered_page = SIZE_MAX;
    return;
  }
  if (!busy && location_picker_ui::kNextButton.contains(x, y) &&
      picker_page + 1U < page_count) {
    ++picker_page;
    picker_rendered_page = SIZE_MAX;
    return;
  }
  if (!busy && location_picker_ui::kApplyButton.contains(x, y) &&
      picker_selected_index >= 0 &&
      static_cast<size_t>(picker_selected_index) < catalog.count) {
    if (!network_uplink.selectLocation(
            static_cast<size_t>(picker_selected_index))) {
      Serial.println("[UI] WARN region selection request was rejected");
    } else {
      last_display_poll_ms = 0U;
    }
  }
}

void appendLocationSearchCharacter(char character) {
  if (character < 0x20 || character > 0x7E ||
      location_search_query_length >= kLocationSearchQueryBytes - 1U) {
    return;
  }
  location_search_query[location_search_query_length++] = character;
  location_search_query[location_search_query_length] = '\0';
  location_search_rendered_length = SIZE_MAX;
}

void handleLocationSearchPress(const TouchEvent &event) {
  const int16_t x = static_cast<int16_t>(event.point.x);
  const int16_t y = static_cast<int16_t>(event.point.y);

  if (wifi_keyboard_ui::kCancelButton.contains(x, y)) {
    closeLocationSearch();
    return;
  }
  if (wifi_keyboard_ui::kSpaceButton.contains(x, y)) {
    appendLocationSearchCharacter(' ');
    return;
  }
  if (wifi_keyboard_ui::kConnectButton.contains(x, y)) {
    if (location_search_query_length >= 2U &&
        network_uplink.requestLocationSearch(location_search_query)) {
      screen_mode = ScreenMode::kLocationPicker;
      picker_page = 0U;
      picker_selected_index = -1;
      picker_selection_initialized = false;
      picker_rendered_revision = UINT32_MAX;
      picker_rendered_page = SIZE_MAX;
      picker_rendered_selection = -2;
      last_display_poll_ms = 0U;
      last_display_poll_ms = 0U;
      Serial.printf("[UI] global location search submitted query='%s'\n",
                    location_search_query);
    } else {
      Serial.println("[UI] WARN location search needs 2-48 ASCII characters");
    }
    return;
  }

  for (size_t row = 0U; row < wifi_keyboard_ui::kRows; ++row) {
    for (size_t column = 0U; column < wifi_keyboard_ui::kColumns; ++column) {
      if (!wifi_keyboard_ui::keyRect(row, column).contains(x, y)) {
        continue;
      }
      if (wifi_keyboard_ui::isBackspaceCell(row, column)) {
        if (location_search_query_length > 0U) {
          location_search_query[--location_search_query_length] = '\0';
          location_search_rendered_length = SIZE_MAX;
        }
      } else if (wifi_keyboard_ui::isCaseCell(row, column)) {
        if (location_search_keyboard_mode == WifiKeyboardMode::kLower) {
          location_search_keyboard_mode = WifiKeyboardMode::kUpper;
        } else if (location_search_keyboard_mode == WifiKeyboardMode::kUpper) {
          location_search_keyboard_mode = WifiKeyboardMode::kLower;
        }
        location_search_rendered_keyboard_mode =
            static_cast<WifiKeyboardMode>(UINT8_MAX);
      } else if (wifi_keyboard_ui::isClearCell(row, column)) {
        clearLocationSearchQuery();
        location_search_rendered_length = SIZE_MAX;
      } else if (wifi_keyboard_ui::isModeCell(row, column)) {
        location_search_keyboard_mode =
            location_search_keyboard_mode == WifiKeyboardMode::kSymbols
                ? WifiKeyboardMode::kLower
                : WifiKeyboardMode::kSymbols;
        location_search_rendered_keyboard_mode =
            static_cast<WifiKeyboardMode>(UINT8_MAX);
      } else {
        appendLocationSearchCharacter(wifi_keyboard_ui::keyCharacter(
            location_search_keyboard_mode, row, column));
      }
      return;
    }
  }
}

void openWifiPicker() {
  screen_mode = ScreenMode::kWifiPicker;
  wifi_page = 0U;
  wifi_selected_index = 0U;
  wifi_selected_network = WifiNetworkOption{};
  wifi_keyboard_mode = WifiKeyboardMode::kLower;
  wifi_connect_submitted = false;
  wifi_forget_submitted = false;
  wifi_forget_ssid[0] = '\0';
  wifi_connected_since_ms = 0U;
  clearWifiPassword();
  clearWifiKeyFeedback();
  invalidateWifiRendering();
  network_uplink.requestWifiScan();
  Serial.println("[UI] opening on-device wifi picker");
}

void closeWifiSetup() {
  network_uplink.endWifiSetup();
  clearWifiPassword();
  clearWifiKeyFeedback();
  wifi_selected_network = WifiNetworkOption{};
  wifi_connect_submitted = false;
  wifi_forget_submitted = false;
  wifi_forget_ssid[0] = '\0';
  wifi_connected_since_ms = 0U;
  invalidateWifiRendering();
  screen_mode = ScreenMode::kWeather;
  show_weather_detail = false;
  have_displayed_environment = false;
  have_displayed_risk = false;
  have_displayed_network_status = false;
  displayed_weather_page = DisplayedWeatherPage::kNone;
  last_display_poll_ms = 0U;
  Serial.println("[UI] closing wifi setup");
}

void openWifiForgetConfirm(const WifiCatalog &catalog) {
  if (catalog.active_ssid[0] == '\0') {
    return;
  }
  std::snprintf(wifi_forget_ssid, sizeof(wifi_forget_ssid), "%s",
                catalog.active_ssid);
  wifi_forget_submitted = false;
  screen_mode = ScreenMode::kWifiForgetConfirm;
  invalidateWifiRendering();
  Serial.printf("[UI] wifi forget confirmation opened ssid='%s'\n",
                wifi_forget_ssid);
}

void openWifiPassword(size_t index, const WifiCatalog &catalog) {
  if (index >= catalog.count || index >= kWifiCatalogCapacity) {
    return;
  }
  wifi_selected_index = index;
  wifi_selected_network = catalog.options[index];
  wifi_keyboard_mode = WifiKeyboardMode::kLower;
  wifi_connect_submitted = false;
  wifi_connected_since_ms = 0U;
  clearWifiPassword();
  clearWifiKeyFeedback();
  invalidateWifiRendering();
  screen_mode = ScreenMode::kWifiPassword;
  Serial.printf(
      "[UI] wifi selected index=%u ssid='%s' secured=%u auth=%u "
      "supported=%u\n",
                static_cast<unsigned>(index), wifi_selected_network.ssid,
                wifi_selected_network.secured ? 1U : 0U,
                static_cast<unsigned>(wifi_selected_network.auth_mode),
                wifi_selected_network.supported ? 1U : 0U);
}

void handleWifiPickerPress(const TouchEvent &event) {
  network_uplink.copyWifiCatalog(&wifi_catalog_snapshot);
  const WifiCatalog &catalog = wifi_catalog_snapshot;
  const bool busy = catalog.state == WifiSetupState::kScanning ||
                    catalog.state == WifiSetupState::kConnecting ||
                    catalog.state == WifiSetupState::kForgetting;
  const int16_t x = static_cast<int16_t>(event.point.x);
  const int16_t y = static_cast<int16_t>(event.point.y);
  const size_t page_count =
      catalog.count == 0U
          ? 1U
          : (catalog.count + wifi_setup_ui::kPageSize - 1U) /
                wifi_setup_ui::kPageSize;
  if (wifi_page >= page_count) {
    wifi_page = page_count - 1U;
  }

  if (wifi_setup_ui::kBackButton.contains(x, y)) {
    closeWifiSetup();
    return;
  }
  if (!busy && wifi_setup_ui::kRescanButton.contains(x, y)) {
    wifi_page = 0U;
    invalidateWifiRendering();
    network_uplink.requestWifiScan();
    return;
  }
  if (!busy && wifi_setup_ui::kForgetButton.contains(x, y) &&
      catalog.active_ssid[0] != '\0') {
    openWifiForgetConfirm(catalog);
    return;
  }
  if (!busy && wifi_setup_ui::kPreviousButton.contains(x, y) &&
      wifi_page > 0U) {
    --wifi_page;
    wifi_rendered_page = SIZE_MAX;
    return;
  }
  if (!busy && wifi_setup_ui::kNextButton.contains(x, y) &&
      wifi_page + 1U < page_count) {
    ++wifi_page;
    wifi_rendered_page = SIZE_MAX;
    return;
  }
  if (busy) {
    return;
  }

  for (size_t slot = 0U; slot < wifi_setup_ui::kPageSize; ++slot) {
    if (!wifi_setup_ui::cardRect(slot).contains(x, y)) {
      continue;
    }
    const size_t index = wifi_page * wifi_setup_ui::kPageSize + slot;
    if (index < catalog.count) {
      openWifiPassword(index, catalog);
    }
    return;
  }
}

void handleWifiForgetConfirmPress(const TouchEvent &event) {
  network_uplink.copyWifiCatalog(&wifi_catalog_snapshot);
  const bool busy = wifi_forget_submitted ||
                    wifi_catalog_snapshot.state ==
                        WifiSetupState::kForgetting;
  const int16_t x = static_cast<int16_t>(event.point.x);
  const int16_t y = static_cast<int16_t>(event.point.y);
  if (!busy && wifi_setup_ui::kForgetCancelButton.contains(x, y)) {
    network_uplink.dismissWifiForgetError();
    wifi_forget_submitted = false;
    wifi_forget_ssid[0] = '\0';
    screen_mode = ScreenMode::kWifiPicker;
    invalidateWifiRendering();
    Serial.println("[UI] wifi forget cancelled");
    return;
  }
  if (!busy && wifi_setup_ui::kForgetConfirmButton.contains(x, y)) {
    if (network_uplink.requestWifiForget()) {
      wifi_forget_submitted = true;
      invalidateWifiRendering();
      Serial.printf("[UI] wifi forget submitted ssid='%s'\n",
                    wifi_forget_ssid);
    } else {
      Serial.println("[UI] WARN wifi forget request was rejected");
    }
  }
}

bool appendWifiPasswordCharacter(char character) {
  if (character < 0x20 || character > 0x7E ||
      wifi_password_length >= kWifiPasswordBytes - 2U) {
    return false;
  }
  wifi_password[wifi_password_length++] = character;
  wifi_password[wifi_password_length] = '\0';
  wifi_rendered_password_length = SIZE_MAX;
  return true;
}

void handleWifiPasswordPress(const TouchEvent &event) {
  network_uplink.copyWifiCatalog(&wifi_catalog_snapshot);
  const bool busy = wifi_connect_submitted ||
                    wifi_catalog_snapshot.state ==
                        WifiSetupState::kConnecting ||
                    wifi_catalog_snapshot.state == WifiSetupState::kConnected;
  const int16_t x = static_cast<int16_t>(event.point.x);
  const int16_t y = static_cast<int16_t>(event.point.y);

  if (wifi_keyboard_ui::kCancelButton.contains(x, y) && !busy) {
    clearWifiPassword();
    clearWifiKeyFeedback();
    screen_mode = ScreenMode::kWifiPicker;
    invalidateWifiRendering();
    return;
  }
  if (busy) {
    return;
  }
  if (wifi_keyboard_ui::kSpaceButton.contains(x, y) &&
      wifi_selected_network.secured && wifi_selected_network.supported) {
    setWifiKeyFeedback(appendWifiPasswordCharacter(' ') ? "SPACE" : "FULL");
    return;
  }
  if (wifi_keyboard_ui::kConnectButton.contains(x, y)) {
    setWifiKeyFeedback("CONNECT");
    if (wifiPasswordReady()) {
      if (network_uplink.requestWifiConnect(wifi_selected_network.ssid,
                                            wifi_password)) {
        wifi_connect_submitted = true;
        invalidateWifiRendering();
        Serial.printf("[UI] wifi connection submitted ssid='%s'\n",
                      wifi_selected_network.ssid);
      } else {
        Serial.println("[UI] WARN wifi connection request was rejected");
      }
    }
    return;
  }
  if (!wifi_selected_network.secured || !wifi_selected_network.supported) {
    return;
  }

  for (size_t row = 0U; row < wifi_keyboard_ui::kRows; ++row) {
    for (size_t column = 0U; column < wifi_keyboard_ui::kColumns; ++column) {
      if (!wifi_keyboard_ui::keyRect(row, column).contains(x, y)) {
        continue;
      }
      if (wifi_keyboard_ui::isBackspaceCell(row, column)) {
        if (wifi_password_length > 0U) {
          wifi_password[--wifi_password_length] = '\0';
        }
        setWifiKeyFeedback("DEL");
      } else if (wifi_keyboard_ui::isCaseCell(row, column)) {
        if (wifi_keyboard_mode == WifiKeyboardMode::kLower) {
          wifi_keyboard_mode = WifiKeyboardMode::kUpper;
          setWifiKeyFeedback("UP");
        } else if (wifi_keyboard_mode == WifiKeyboardMode::kUpper) {
          wifi_keyboard_mode = WifiKeyboardMode::kLower;
          setWifiKeyFeedback("LOW");
        } else {
          return;
        }
        wifi_rendered_keyboard_mode =
            static_cast<WifiKeyboardMode>(UINT8_MAX);
      } else if (wifi_keyboard_ui::isClearCell(row, column)) {
        clearWifiPassword();
        setWifiKeyFeedback("CLR");
      } else if (wifi_keyboard_ui::isModeCell(row, column)) {
        if (wifi_keyboard_mode == WifiKeyboardMode::kSymbols) {
          wifi_keyboard_mode = WifiKeyboardMode::kLower;
          setWifiKeyFeedback("ABC");
        } else {
          wifi_keyboard_mode = WifiKeyboardMode::kSymbols;
          setWifiKeyFeedback("SYM");
        }
        wifi_rendered_keyboard_mode =
            static_cast<WifiKeyboardMode>(UINT8_MAX);
      } else {
        const char character = wifi_keyboard_ui::keyCharacter(
            wifi_keyboard_mode, row, column);
        if (character != '\0') {
          char label[2]{character, '\0'};
          setWifiKeyFeedback(appendWifiPasswordCharacter(character) ? label
                                                                    : "FULL");
        }
      }
      return;
    }
  }
}

void pollBootButton() {
  const uint32_t now_ms = millis();
  const bool down = digitalRead(kBootButtonPin) == LOW;
  if (!down) {
    boot_button_was_down = false;
    boot_button_handled = false;
    boot_button_down_since_ms = 0U;
    return;
  }
  if (!boot_button_was_down) {
    boot_button_was_down = true;
    boot_button_down_since_ms = now_ms;
    return;
  }
  if (!boot_button_handled && screen_mode == ScreenMode::kWeather &&
      static_cast<uint32_t>(now_ms - boot_button_down_since_ms) >=
          kBootHoldForWifiMs) {
    boot_button_handled = true;
    openWifiPicker();
    Serial.println("[UI] BOOT hold opened wifi setup");
  }
}

void pollTouch() {
  TouchEvent event{};
  if (!coastal_touch.poll(&event)) {
    return;
  }

  const bool wifi_entry =
      screen_mode == ScreenMode::kWeather &&
      ((displayed_weather_page == DisplayedWeatherPage::kRiskOverview &&
        event.inside(kRiskWifiRegion)) ||
       (displayed_weather_page == DisplayedWeatherPage::kEnvironment &&
        event.inside(kEnvironmentWifiRegion)) ||
       (displayed_weather_page == DisplayedWeatherPage::kNetworkStatus &&
        (event.inside(kNetworkWifiHeaderRegion) ||
         event.inside(kNetworkWifiCardRegion))));
  const bool weather_entry =
      screen_mode == ScreenMode::kWeather &&
      displayed_weather_page == DisplayedWeatherPage::kRiskOverview &&
      event.inside(kRiskWeatherRegion);
  const bool models_entry =
      screen_mode == ScreenMode::kWeather &&
      displayed_weather_page == DisplayedWeatherPage::kRiskOverview &&
      event.inside(kRiskModelsRegion);
  const bool risk_entry =
      screen_mode == ScreenMode::kWeather &&
      displayed_weather_page == DisplayedWeatherPage::kEnvironment &&
      event.inside(kEnvironmentRiskRegion);
  const bool selected_area =
      screen_mode == ScreenMode::kWeather &&
      displayed_weather_page == DisplayedWeatherPage::kEnvironment &&
      event.inside(kSelectedAreaRegion);
  const char *target = "none";
  if (wifi_entry) {
    target = "wifi_entry";
  } else if (models_entry) {
    target = "models";
  } else if (weather_entry) {
    target = "weather_detail";
  } else if (risk_entry) {
    target = "risk_overview";
  } else if (selected_area) {
    target = "selected_area";
  } else if (screen_mode == ScreenMode::kLocationPicker) {
    target = "region_picker";
  } else if (screen_mode == ScreenMode::kLocationSearch) {
    target = "location_search";
  } else if (screen_mode == ScreenMode::kWifiPicker) {
    target = "wifi_picker";
  } else if (screen_mode == ScreenMode::kWifiForgetConfirm) {
    target = "wifi_forget_confirm";
  } else if (screen_mode == ScreenMode::kWifiPassword) {
    target = "wifi_keyboard";
  } else if (screen_mode == ScreenMode::kModels) {
    target = "model_library";
  } else if (screen_mode == ScreenMode::kCollection) {
    target = "data_collection";
  }
  Serial.printf(
      "[TOUCH] event=%s x=%u y=%u raw_x=%u raw_y=%u points=%u target=%s\n",
      Ft5x06Touch::eventTypeName(event.type), event.point.x, event.point.y,
      event.point.raw_x, event.point.raw_y, event.point_count, target);

  if (event.type != TouchEventType::kPressed) {
    return;
  }
  if (screen_mode == ScreenMode::kWeather && wifi_entry) {
    openWifiPicker();
  } else if (screen_mode == ScreenMode::kWeather && models_entry) {
    openModels();
  } else if (screen_mode == ScreenMode::kWeather && weather_entry) {
    showWeatherDetail();
  } else if (screen_mode == ScreenMode::kWeather && risk_entry) {
    showRiskOverview();
  } else if (screen_mode == ScreenMode::kWeather && selected_area) {
    openLocationPicker();
  } else if (screen_mode == ScreenMode::kLocationPicker) {
    handleLocationPickerPress(event);
  } else if (screen_mode == ScreenMode::kLocationSearch) {
    handleLocationSearchPress(event);
  } else if (screen_mode == ScreenMode::kWifiPicker) {
    handleWifiPickerPress(event);
  } else if (screen_mode == ScreenMode::kWifiForgetConfirm) {
    handleWifiForgetConfirmPress(event);
  } else if (screen_mode == ScreenMode::kWifiPassword) {
    handleWifiPasswordPress(event);
  } else if (screen_mode == ScreenMode::kModels) {
    handleModelPress(event);
  } else if (screen_mode == ScreenMode::kCollection) {
    handleCollectionPress(event);
  }
}

}  // namespace

void setup() {
  Serial.begin(app_config::kDebugBaud);
  pinMode(kBootButtonPin, INPUT_PULLUP);
  Serial.println();
  Serial.println("[BOOT] Coastal Warning ESP32-S3 single-board controller");
  Serial.printf("[BOOT] OpenMV UART1 RX=GPIO%d TX=GPIO%d baud=%lu 8N1\n",
                app_config::kOpenMvUartRxPin,
                app_config::kOpenMvUartTxPin,
                static_cast<unsigned long>(app_config::kOpenMvUartBaud));
  Serial.printf("[BOOT] ultrasonic TRIG=GPIO%d ECHO=GPIO%d level_shift=%u\n",
                app_config::kUltrasonicTriggerPin,
                app_config::kUltrasonicEchoPin,
                app_config::kUltrasonicEchoLevelShiftVerified ? 1U : 0U);
  Serial.println(
      "[BOOT] deterministic local alarm is owned by this ESP32; remote "
      "models may request OpenMV monitoring but cannot replace local rules");

  sensor_logic::reset(&sensor_state);
  if (!telemetry_sequence.begin()) {
    sequence_failure_logged = true;
    Serial.println(
        "[BOOT] ERROR persistent telemetry sequence unavailable; uploads "
        "will stay disabled");
  }

  if (coastal_display.begin()) {
    const NetworkStatus status = network_uplink.status();
    if (coastal_display.showNetworkStatus(
            status.wifi_connected, status.server_reachable,
            status.environment_reachable)) {
      displayed_network_status = status;
      have_displayed_network_status = true;
      displayed_weather_page = DisplayedWeatherPage::kNetworkStatus;
    }
  } else {
    Serial.println("[BOOT] LCD unavailable; network startup will continue");
  }

  if (!coastal_touch.begin(app_config::kTouchSdaPin,
                           app_config::kTouchSclPin,
                           app_config::kTouchI2cAddress,
                           app_config::kTouchI2cClockHz)) {
    Serial.println("[BOOT] touch unavailable; display/network continue");
  }

  openmv_uart.setRxBufferSize(app_config::kUartHardwareRxBufferBytes);
  openmv_uart.begin(app_config::kOpenMvUartBaud, SERIAL_8N1,
                    app_config::kOpenMvUartRxPin,
                    app_config::kOpenMvUartTxPin);

  if (app_config::kUltrasonicEchoLevelShiftVerified) {
    const UltrasonicSensorResult ultrasonic = ultrasonic_device.begin();
    if (ultrasonic.fault != UltrasonicSensorFault::kNone) {
      sensor_logic::note_hardware_fault(&sensor_state);
      Serial.printf("[BOOT] ultrasonic initialization fault=%u\n",
                    static_cast<unsigned>(ultrasonic.fault));
    }
  } else {
    Serial.println(
        "[BOOT] ultrasonic disabled: install and verify 5V->3.3V ECHO "
        "conversion, then set ULTRASONIC_ECHO_LEVEL_SHIFT_VERIFIED=1");
  }

  if (!network_uplink.begin()) {
    Serial.println(
        "[BOOT] network worker unavailable; local sensing/display continue");
  }
}

void loop() {
  pollLocalSensorRuntime();
  pollTouch();
  pollBootButton();
  refreshDisplayIfNeeded();
  yield();
}
