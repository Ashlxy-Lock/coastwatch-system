#include "network_uplink.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>
#include <esp_wifi.h>
#include <algorithm>
#include <cstddef>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <time.h>

#include "app_config.h"

namespace {

// Arduino-ESP32 links this Mozilla-compatible root CA bundle from its SDK.
// WiFiClientSecure performs normal certificate-chain and hostname validation.
extern const uint8_t x509_crt_bundle_start[]
    asm("_binary_x509_crt_bundle_start");

constexpr size_t kLocationCatalogMaxJsonBytes = 4096U;
constexpr uint32_t kWifiCandidateTimeoutMs = 30U * 1000U;
constexpr uint32_t kWifiCandidateDisconnectTimeoutMs = 3U * 1000U;
constexpr uint32_t kWifiStaResetMs = 100U;
constexpr uint32_t kWifiScanDisconnectSettleMs = 400U;
constexpr uint32_t kWifiScanRetryDelayMs = 300U;
constexpr uint32_t kWifiScanQuietMs = 100U;
constexpr uint32_t kWifiScanDrainTimeoutMs = 2U * 1000U;
constexpr uint32_t kWifiScanHardTimeoutMs = 6U * 1000U;
constexpr uint32_t kWifiScanMaxMsPerChannel = 200U;
constexpr uint8_t kWifiScanMaxAttempts = 3U;
constexpr uint8_t kWifiScanMaxStartRecoveries = 3U;
constexpr char kWifiPreferencesNamespace[] = "coast-net";
constexpr char kWifiPreferencesKey[] = "profile";
constexpr uint32_t kWifiProfileMagic = 0x434F4153U;  // "COAS"
constexpr uint16_t kWifiProfileVersion = 1U;

enum class WifiScanPhase : uint8_t {
  kIdle = 0,
  kSettling,
  kRunning,
  kDraining,
};

struct WifiEventSnapshot {
  uint32_t disconnected_revision;
  uint32_t connected_revision;
  uint32_t got_ip_revision;
  uint32_t scan_done_revision;
  uint8_t disconnect_reason;
  int8_t disconnect_rssi;
};

portMUX_TYPE g_wifi_event_mux = portMUX_INITIALIZER_UNLOCKED;
WifiEventSnapshot g_wifi_events{};

void handleWifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  portENTER_CRITICAL(&g_wifi_event_mux);
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    ++g_wifi_events.disconnected_revision;
    g_wifi_events.disconnect_reason = info.wifi_sta_disconnected.reason;
    g_wifi_events.disconnect_rssi = info.wifi_sta_disconnected.rssi;
  } else if (event == ARDUINO_EVENT_WIFI_STA_CONNECTED) {
    ++g_wifi_events.connected_revision;
  } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    ++g_wifi_events.got_ip_revision;
  } else if (event == ARDUINO_EVENT_WIFI_SCAN_DONE) {
    ++g_wifi_events.scan_done_revision;
  }
  portEXIT_CRITICAL(&g_wifi_event_mux);
}

WifiEventSnapshot wifiEventSnapshot() {
  portENTER_CRITICAL(&g_wifi_event_mux);
  const WifiEventSnapshot snapshot = g_wifi_events;
  portEXIT_CRITICAL(&g_wifi_event_mux);
  return snapshot;
}

bool wifiAuthModeSupported(wifi_auth_mode_t auth_mode) {
  switch (auth_mode) {
    case WIFI_AUTH_OPEN:
    case WIFI_AUTH_WPA_PSK:
    case WIFI_AUTH_WPA2_PSK:
    case WIFI_AUTH_WPA_WPA2_PSK:
    case WIFI_AUTH_WPA3_PSK:
    case WIFI_AUTH_WPA2_WPA3_PSK:
      return true;
    case WIFI_AUTH_WEP:
    case WIFI_AUTH_ENTERPRISE:
    case WIFI_AUTH_WAPI_PSK:
    case WIFI_AUTH_WPA3_ENT_192:
    case WIFI_AUTH_MAX:
    default:
      return false;
  }
}

void resetWifiStationForSetup(const char *active_password) {
  // esp_wifi_scan_start() is rejected with ESP_ERR_WIFI_STATE while the STA
  // driver is still connecting.  WiFi.disconnect() alone is asynchronous, so
  // fully cycle STA mode before a user-requested scan or a scan-start retry.
  esp_wifi_disconnect();
  WiFi.mode(WIFI_OFF);
  vTaskDelay(pdMS_TO_TICKS(kWifiStaResetMs));
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
  WiFi.setMinSecurity(active_password != nullptr && active_password[0] != '\0'
                          ? WIFI_AUTH_WPA_PSK
                          : WIFI_AUTH_OPEN);
}

wifi_auth_mode_t minimumSecurityForAuth(wifi_auth_mode_t auth_mode) {
  if (auth_mode == WIFI_AUTH_OPEN) {
    return WIFI_AUTH_OPEN;
  }
  if (auth_mode == WIFI_AUTH_WPA_PSK ||
      auth_mode == WIFI_AUTH_WPA_WPA2_PSK) {
    return WIFI_AUTH_WPA_PSK;
  }
  return WIFI_AUTH_WPA2_PSK;
}

WifiSetupError classifyWifiConnectionFailure(uint8_t reason,
                                             bool was_associated) {
  switch (reason) {
    case WIFI_REASON_NO_AP_FOUND:
    case WIFI_REASON_BEACON_TIMEOUT:
      return WifiSetupError::kNetworkNotFound;
    case WIFI_REASON_AUTH_EXPIRE:
    case WIFI_REASON_NOT_AUTHED:
    case WIFI_REASON_ASSOC_NOT_AUTHED:
    case WIFI_REASON_MIC_FAILURE:
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_GROUP_KEY_UPDATE_TIMEOUT:
    case WIFI_REASON_AUTH_FAIL:
    case WIFI_REASON_HANDSHAKE_TIMEOUT:
      return WifiSetupError::kAuthenticationFailed;
    case WIFI_REASON_IE_INVALID:
    case WIFI_REASON_IE_IN_4WAY_DIFFERS:
    case WIFI_REASON_GROUP_CIPHER_INVALID:
    case WIFI_REASON_PAIRWISE_CIPHER_INVALID:
    case WIFI_REASON_AKMP_INVALID:
    case WIFI_REASON_UNSUPP_RSN_IE_VERSION:
    case WIFI_REASON_INVALID_RSN_IE_CAP:
    case WIFI_REASON_802_1X_AUTH_FAILED:
    case WIFI_REASON_CIPHER_SUITE_REJECTED:
    case WIFI_REASON_BAD_CIPHER_OR_AKM:
      return WifiSetupError::kUnsupportedSecurity;
    case WIFI_REASON_ASSOC_TOOMANY:
    case WIFI_REASON_ASSOC_FAIL:
    case WIFI_REASON_CONNECTION_FAIL:
      return WifiSetupError::kAssociationFailed;
    default:
      return was_associated ? WifiSetupError::kDhcpFailed
                            : WifiSetupError::kConnectionTimeout;
  }
}

struct StoredWifiProfile {
  uint32_t magic;
  uint16_t version;
  uint16_t size;
  char ssid[kWifiSsidBytes];
  char password[kWifiPasswordBytes];
  uint32_t checksum;
};

enum class StoredWifiProfileState : uint8_t {
  kAbsent = 0,
  kConfigured,
  kBlocked,
};

static_assert(sizeof(StoredWifiProfile) <= 128U,
              "Wi-Fi NVS profile unexpectedly large");

bool hasText(const char *value) { return value != nullptr && value[0] != '\0'; }

size_t boundedTextLength(const char *value, size_t capacity) {
  if (value == nullptr) {
    return 0U;
  }
  size_t length = 0U;
  while (length < capacity && value[length] != '\0') {
    ++length;
  }
  return length;
}

bool validWifiSsid(const char *ssid) {
  const size_t length = boundedTextLength(ssid, kWifiSsidBytes);
  return length > 0U && length < kWifiSsidBytes;
}

bool validStoredWifiPassword(const char *password) {
  return password != nullptr &&
         boundedTextLength(password, kWifiPasswordBytes) < kWifiPasswordBytes;
}

bool validSecuredWifiPassword(const char *password) {
  const size_t length = boundedTextLength(password, kWifiPasswordBytes);
  return length >= 8U && length <= 63U;
}

void copyFixedText(char *destination, size_t destination_size,
                   const char *source) {
  if (destination == nullptr || destination_size == 0U) {
    return;
  }
  destination[0] = '\0';
  if (source == nullptr) {
    return;
  }
  const size_t length = boundedTextLength(source, destination_size - 1U);
  std::memcpy(destination, source, length);
  destination[length] = '\0';
}

void clearSensitiveText(char *text, size_t length) {
  if (text == nullptr) {
    return;
  }
  volatile char *cursor = text;
  while (length-- > 0U) {
    *cursor++ = '\0';
  }
}

uint32_t profileChecksum(const StoredWifiProfile &profile) {
  const uint8_t *bytes = reinterpret_cast<const uint8_t *>(&profile);
  uint32_t hash = 2166136261U;
  for (size_t index = 0U; index < offsetof(StoredWifiProfile, checksum);
       ++index) {
    hash ^= bytes[index];
    hash *= 16777619U;
  }
  return hash;
}

StoredWifiProfileState loadStoredWifiProfile(
    char *ssid, size_t ssid_size, char *password, size_t password_size) {
  Preferences preferences;
  // Read-write mode creates the namespace on a brand-new board, which lets
  // isKey() distinguish a genuine first boot from an unreadable NVS failure.
  if (!preferences.begin(kWifiPreferencesNamespace, false)) {
    // Fail closed: an unreadable NVS namespace must not silently revive the
    // firmware's build-time Wi-Fi credentials.
    return StoredWifiProfileState::kBlocked;
  }
  const bool profile_present = preferences.isKey(kWifiPreferencesKey);
  if (!profile_present) {
    preferences.end();
    return StoredWifiProfileState::kAbsent;
  }
  const size_t stored_size = preferences.getBytesLength(kWifiPreferencesKey);
  StoredWifiProfile profile{};
  const size_t read_size =
      stored_size == sizeof(profile)
          ? preferences.getBytes(kWifiPreferencesKey, &profile, sizeof(profile))
          : 0U;
  preferences.end();

  const bool valid_envelope =
      read_size == sizeof(profile) && profile.magic == kWifiProfileMagic &&
      profile.version == kWifiProfileVersion &&
      profile.size == sizeof(profile) &&
      validStoredWifiPassword(profile.password) &&
      profile.checksum == profileChecksum(profile);
  const bool configured = valid_envelope && validWifiSsid(profile.ssid);
  if (!configured) {
    clearSensitiveText(profile.password, sizeof(profile.password));
    return StoredWifiProfileState::kBlocked;
  }

  copyFixedText(ssid, ssid_size, profile.ssid);
  copyFixedText(password, password_size, profile.password);
  clearSensitiveText(profile.password, sizeof(profile.password));
  return StoredWifiProfileState::kConfigured;
}

bool saveStoredWifiProfile(const char *ssid, const char *password) {
  if (!validWifiSsid(ssid) || !validStoredWifiPassword(password)) {
    return false;
  }

  StoredWifiProfile profile{};
  profile.magic = kWifiProfileMagic;
  profile.version = kWifiProfileVersion;
  profile.size = sizeof(profile);
  copyFixedText(profile.ssid, sizeof(profile.ssid), ssid);
  copyFixedText(profile.password, sizeof(profile.password), password);
  profile.checksum = profileChecksum(profile);

  Preferences preferences;
  if (!preferences.begin(kWifiPreferencesNamespace, false)) {
    clearSensitiveText(profile.password, sizeof(profile.password));
    return false;
  }
  const size_t written =
      preferences.putBytes(kWifiPreferencesKey, &profile, sizeof(profile));
  preferences.end();
  clearSensitiveText(profile.password, sizeof(profile.password));
  return written == sizeof(profile);
}

bool saveForgottenWifiProfile() {
  // A valid empty record is a persistent tombstone. Removing the key would
  // make the next boot fall back to app_config::kWifiSsid and resurrect the
  // very network the user asked the device to forget.
  StoredWifiProfile tombstone{};
  tombstone.magic = kWifiProfileMagic;
  tombstone.version = kWifiProfileVersion;
  tombstone.size = sizeof(tombstone);
  tombstone.checksum = profileChecksum(tombstone);

  Preferences preferences;
  if (!preferences.begin(kWifiPreferencesNamespace, false)) {
    return false;
  }
  const size_t written = preferences.putBytes(
      kWifiPreferencesKey, &tombstone, sizeof(tombstone));
  preferences.end();
  return written == sizeof(tombstone);
}

void addWifiScanResult(WifiCatalog *catalog, const String &ssid, int32_t rssi,
                       wifi_auth_mode_t auth_mode) {
  if (catalog == nullptr || ssid.length() == 0U ||
      ssid.length() >= kWifiSsidBytes) {
    return;
  }

  for (size_t index = 0U; index < catalog->count; ++index) {
    if (std::strcmp(catalog->options[index].ssid, ssid.c_str()) == 0) {
      if (rssi > catalog->options[index].rssi) {
        catalog->options[index].rssi = rssi;
        catalog->options[index].auth_mode =
            static_cast<uint8_t>(auth_mode);
        catalog->options[index].secured = auth_mode != WIFI_AUTH_OPEN;
        catalog->options[index].supported =
            wifiAuthModeSupported(auth_mode);
      }
      return;
    }
  }

  if (catalog->count >= kWifiCatalogCapacity) {
    // Keep the strongest twelve entries without allocating a dynamic list.
    size_t weakest = 0U;
    for (size_t index = 1U; index < catalog->count; ++index) {
      if (catalog->options[index].rssi < catalog->options[weakest].rssi) {
        weakest = index;
      }
    }
    if (rssi <= catalog->options[weakest].rssi) {
      return;
    }
    copyFixedText(catalog->options[weakest].ssid,
                  sizeof(catalog->options[weakest].ssid), ssid.c_str());
    catalog->options[weakest].rssi = rssi;
    catalog->options[weakest].auth_mode = static_cast<uint8_t>(auth_mode);
    catalog->options[weakest].secured = auth_mode != WIFI_AUTH_OPEN;
    catalog->options[weakest].supported = wifiAuthModeSupported(auth_mode);
    return;
  }

  WifiNetworkOption &option = catalog->options[catalog->count++];
  copyFixedText(option.ssid, sizeof(option.ssid), ssid.c_str());
  option.rssi = rssi;
  option.auth_mode = static_cast<uint8_t>(auth_mode);
  option.secured = auth_mode != WIFI_AUTH_OPEN;
  option.supported = wifiAuthModeSupported(auth_mode);
}

void sortWifiCatalog(WifiCatalog *catalog) {
  if (catalog == nullptr) {
    return;
  }
  std::sort(catalog->options, catalog->options + catalog->count,
            [](const WifiNetworkOption &left,
               const WifiNetworkOption &right) {
              if (left.rssi != right.rssi) {
                return left.rssi > right.rssi;
              }
              return std::strcmp(left.ssid, right.ssid) < 0;
            });
}

uint32_t unixTimeNow() {
  const time_t now = time(nullptr);
  if (now < app_config::kMinimumValidUnixTime) {
    return 0U;
  }
  return static_cast<uint32_t>(now);
}

uint32_t nextBackoff(size_t *index) {
  const size_t bounded =
      *index < app_config::kRetryBackoffCount
          ? *index
          : app_config::kRetryBackoffCount - 1U;
  const uint32_t result = app_config::kRetryBackoffMs[bounded];
  if (*index + 1U < app_config::kRetryBackoffCount) {
    ++(*index);
  }
  return result;
}

bool beginHttpRequest(HTTPClient *http, WiFiClient *plain_client,
                      WiFiClientSecure *secure_client, const String &url) {
  if (http == nullptr || plain_client == nullptr || secure_client == nullptr) {
    return false;
  }

  if (!url.startsWith("https://")) {
    return http->begin(*plain_client, url);
  }

  if (unixTimeNow() == 0U) {
    static bool waiting_logged = false;
    if (!waiting_logged) {
      Serial.println("[TLS] waiting for valid NTP time");
      waiting_logged = true;
    }
    return false;
  }

  secure_client->setCACertBundle(x509_crt_bundle_start);
  return http->begin(*secure_client, url);
}

void addDeviceTokenHeader(HTTPClient *http) {
  if (http != nullptr && hasText(app_config::kDeviceToken)) {
    http->addHeader("X-Device-Token", app_config::kDeviceToken);
  }
}

bool validLocationId(const char *value) {
  if (!hasText(value)) {
    return false;
  }
  for (size_t index = 0U; value[index] != '\0'; ++index) {
    const char character = value[index];
    if (!((character >= 'a' && character <= 'z') ||
          (character >= '0' && character <= '9') || character == '-' ||
          character == '_')) {
      return false;
    }
  }
  return true;
}

bool validLocationSearchQuery(const char *query) {
  const size_t length = boundedTextLength(query, kLocationSearchQueryBytes);
  if (length < 2U || length >= kLocationSearchQueryBytes) {
    return false;
  }
  for (size_t index = 0U; index < length; ++index) {
    const unsigned char character =
        static_cast<unsigned char>(query[index]);
    if (character < 0x20U || character > 0x7EU) {
      return false;
    }
  }
  size_t first = 0U;
  while (first < length && query[first] == ' ') {
    ++first;
  }
  size_t last = length;
  while (last > first && query[last - 1U] == ' ') {
    --last;
  }
  return last - first >= 2U;
}

String percentEncodeQuery(const char *query) {
  static constexpr char kHex[] = "0123456789ABCDEF";
  String encoded;
  const size_t length = boundedTextLength(query, kLocationSearchQueryBytes);
  encoded.reserve(length * 3U);
  for (size_t index = 0U; index < length; ++index) {
    const uint8_t character = static_cast<uint8_t>(query[index]);
    const bool unreserved =
        (character >= 'a' && character <= 'z') ||
        (character >= 'A' && character <= 'Z') ||
        (character >= '0' && character <= '9') || character == '-' ||
        character == '_' || character == '.' || character == '~';
    if (unreserved) {
      encoded += static_cast<char>(character);
    } else {
      encoded += '%';
      encoded += kHex[(character >> 4U) & 0x0FU];
      encoded += kHex[character & 0x0FU];
    }
  }
  return encoded;
}

template <size_t Capacity>
bool copyRequiredJsonText(JsonObjectConst object, const char *key,
                          char (&destination)[Capacity]) {
  const JsonVariantConst value = object[key];
  if (!value.is<const char *>()) {
    return false;
  }
  const char *text = value.as<const char *>();
  if (!hasText(text)) {
    return false;
  }
  const size_t length = std::strlen(text);
  if (length >= Capacity) {
    return false;
  }
  std::memcpy(destination, text, length + 1U);
  return true;
}

bool parseLocationCatalogJson(const char *json, size_t length,
                              LocationCatalog *catalog) {
  if (json == nullptr || catalog == nullptr ||
      length > kLocationCatalogMaxJsonBytes) {
    return false;
  }

  JsonDocument document;
  const DeserializationError error = deserializeJson(document, json, length);
  if (error || !document.is<JsonArray>()) {
    return false;
  }

  const JsonArrayConst rows = document.as<JsonArrayConst>();
  // An empty array is a valid "no matching place" search result. The picker
  // renders it as an empty result set instead of reporting a transport error.
  if (rows.size() > kLocationCatalogCapacity) {
    return false;
  }

  std::memset(catalog, 0, sizeof(*catalog));
  for (JsonObjectConst row : rows) {
    if (catalog->count >= kLocationCatalogCapacity) {
      return false;
    }
    LocationOption &option = catalog->options[catalog->count];
    if (!copyRequiredJsonText(row, "id", option.id) ||
        !validLocationId(option.id) ||
        !copyRequiredJsonText(row, "name", option.location) ||
        !copyRequiredJsonText(row, "display_location",
                              option.display_location)) {
      return false;
    }

    const JsonVariantConst latitude = row["lat"];
    const JsonVariantConst longitude = row["lon"];
    if (!latitude.is<double>() || !longitude.is<double>()) {
      return false;
    }
    option.lat = latitude.as<double>();
    option.lon = longitude.as<double>();
    if (!std::isfinite(option.lat) || !std::isfinite(option.lon) ||
        option.lat < -90.0 || option.lat > 90.0 || option.lon < -180.0 ||
        option.lon > 180.0) {
      return false;
    }
    const JsonVariantConst kind = row["kind"];
    if (kind.isNull()) {
      // Compatibility with the previous server schema: global search IDs
      // always start with geo_, while the built-in catalogue contains coasts.
      option.is_coastal = std::strncmp(option.id, "geo_", 4U) != 0;
    } else if (!kind.is<const char *>()) {
      return false;
    } else {
      const char *kind_text = kind.as<const char *>();
      if (std::strcmp(kind_text, "coast") == 0) {
        option.is_coastal = true;
      } else if (std::strcmp(kind_text, "place") == 0) {
        option.is_coastal = false;
      } else {
        return false;
      }
    }
    ++catalog->count;
  }

  catalog->state = LocationCatalogState::kReady;
  return true;
}

}  // namespace

bool NetworkUplink::begin() {
  EnvironmentSnapshot empty{};
  resetEnvironmentSnapshot(&empty);
  publishEnvironment(empty);
  RiskSnapshot empty_risk{};
  resetRiskSnapshot(&empty_risk);
  publishRisk(empty_risk, RiskAvailability::kWaiting, 0);
  ModelCatalog empty_models{};
  empty_models.state = ModelCatalogState::kLoading;
  publishModelCatalog(empty_models);
  portENTER_CRITICAL(&model_mux_);
  model_catalog_requested_ = true;
  portEXIT_CRITICAL(&model_mux_);
  SimulationSnapshot empty_simulation{};
  empty_simulation.state = SimulationState::kIdle;
  publishSimulation(empty_simulation);
  portENTER_CRITICAL(&simulation_mux_);
  simulation_recovery_requested_ = true;
  portEXIT_CRITICAL(&simulation_mux_);
  publishLocationCatalog(LocationCatalog{});
  publishWifiCatalog(WifiCatalog{});

  queue_ = xQueueCreate(app_config::kTelemetryQueueDepth, sizeof(TelemetryFrame));
  if (queue_ == nullptr) {
    Serial.println("[NET] ERROR telemetry queue allocation failed");
    return false;
  }

  const BaseType_t created = xTaskCreatePinnedToCore(
      taskEntry, "network-uplink", app_config::kNetworkTaskStackBytes, this,
      app_config::kNetworkTaskPriority, &task_, app_config::kNetworkTaskCore);
  if (created != pdPASS) {
    vQueueDelete(queue_);
    queue_ = nullptr;
    Serial.println("[NET] ERROR network task creation failed");
    return false;
  }
  return true;
}

bool NetworkUplink::submit(const TelemetryFrame &telemetry) {
  const uint32_t received_at_ms = millis();
  portENTER_CRITICAL(&simulation_mux_);
  simulationRecordLocalTelemetry(&simulation_, telemetry, received_at_ms);
  portEXIT_CRITICAL(&simulation_mux_);

  if (queue_ == nullptr) {
    return false;
  }
  if (xQueueSend(queue_, &telemetry, 0U) == pdTRUE) {
    return true;
  }

  // Preserve the freshest telemetry instead of blocking the UART loop.
  TelemetryFrame discarded{};
  xQueueReceive(queue_, &discarded, 0U);
  return xQueueSend(queue_, &telemetry, 0U) == pdTRUE;
}

NetworkStatus NetworkUplink::status() const {
  portENTER_CRITICAL(&status_mux_);
  const NetworkStatus snapshot = status_;
  portEXIT_CRITICAL(&status_mux_);
  return snapshot;
}

EnvironmentSnapshot NetworkUplink::environment() const {
  portENTER_CRITICAL(&environment_mux_);
  const EnvironmentSnapshot snapshot = environment_;
  portEXIT_CRITICAL(&environment_mux_);
  return snapshot;
}

RiskSnapshot NetworkUplink::risk() const {
  portENTER_CRITICAL(&risk_mux_);
  const RiskSnapshot snapshot = risk_;
  portEXIT_CRITICAL(&risk_mux_);
  return snapshot;
}

RiskFetchStatus NetworkUplink::riskStatus() const {
  portENTER_CRITICAL(&risk_mux_);
  const RiskFetchStatus snapshot = risk_status_;
  portEXIT_CRITICAL(&risk_mux_);
  return snapshot;
}

void NetworkUplink::copyModelCatalog(ModelCatalog *catalog) const {
  if (catalog == nullptr) {
    return;
  }
  portENTER_CRITICAL(&model_mux_);
  *catalog = model_catalog_;
  portEXIT_CRITICAL(&model_mux_);
}

void NetworkUplink::requestModelCatalog() {
  portENTER_CRITICAL(&model_mux_);
  if (!model_selection_requested_) {
    model_catalog_requested_ = true;
    model_catalog_.state = ModelCatalogState::kLoading;
    model_catalog_.http_status = 0;
    ++model_catalog_.revision;
  }
  portEXIT_CRITICAL(&model_mux_);
}

bool NetworkUplink::selectModel(const char *model_id) {
  bool accepted = false;
  portENTER_CRITICAL(&model_mux_);
  const ModelOption *option = findModel(model_catalog_, model_id);
  if (!model_selection_requested_ && option != nullptr &&
      modelStatusSelectable(option->status)) {
    copyFixedText(pending_model_id_, sizeof(pending_model_id_), model_id);
    copyFixedText(model_catalog_.pending_model_id,
                  sizeof(model_catalog_.pending_model_id), model_id);
    model_selection_requested_ = true;
    model_catalog_.state = ModelCatalogState::kSelecting;
    model_catalog_.http_status = 0;
    ++model_catalog_.revision;
    accepted = true;
  }
  portEXIT_CRITICAL(&model_mux_);
  return accepted;
}

SimulationSnapshot NetworkUplink::simulation() const {
  portENTER_CRITICAL(&simulation_mux_);
  const SimulationSnapshot snapshot = simulation_;
  portEXIT_CRITICAL(&simulation_mux_);
  return snapshot;
}

bool NetworkUplink::requestSimulationStart() {
  bool accepted = false;
  portENTER_CRITICAL(&simulation_mux_);
  if (!simulation_start_requested_ && !simulation_stop_requested_ &&
      simulationCanStart(simulation_.state)) {
    simulation_ = SimulationSnapshot{};
    simulation_.state = SimulationState::kStarting;
    ++simulation_.revision;
    simulation_start_requested_ = true;
    accepted = true;
  }
  portEXIT_CRITICAL(&simulation_mux_);
  return accepted;
}

bool NetworkUplink::requestSimulationStop() {
  bool accepted = false;
  portENTER_CRITICAL(&simulation_mux_);
  if (!simulation_start_requested_ && !simulation_stop_requested_ &&
      simulationCanStop(simulation_.state) &&
      simulation_.session_id[0] != '\0') {
    simulation_.state = SimulationState::kStopping;
    simulation_.http_status = 0;
    ++simulation_.revision;
    simulation_stop_requested_ = true;
    accepted = true;
  }
  portEXIT_CRITICAL(&simulation_mux_);
  return accepted;
}

void NetworkUplink::copyLocationCatalog(LocationCatalog *catalog) const {
  if (catalog == nullptr) {
    return;
  }
  portENTER_CRITICAL(&location_mux_);
  *catalog = location_catalog_;
  portEXIT_CRITICAL(&location_mux_);
}

void NetworkUplink::requestLocationCatalog() {
  portENTER_CRITICAL(&location_mux_);
  location_search_requested_ = false;
  location_catalog_showing_search_ = false;
  pending_location_query_[0] = '\0';
  if (location_presets_cache_.count > 0U) {
    const uint32_t next_revision = location_catalog_.revision + 1U;
    location_catalog_ = location_presets_cache_;
    location_catalog_.state = LocationCatalogState::kReady;
    location_catalog_.http_status = 0;
    location_catalog_.revision = next_revision;
    location_catalog_requested_ = false;
  } else {
    location_catalog_requested_ = true;
    location_catalog_.count = 0U;
    location_catalog_.state = LocationCatalogState::kLoading;
    location_catalog_.http_status = 0;
    ++location_catalog_.revision;
  }
  portEXIT_CRITICAL(&location_mux_);
}

bool NetworkUplink::requestLocationSearch(const char *query) {
  if (!validLocationSearchQuery(query)) {
    return false;
  }

  bool accepted = false;
  portENTER_CRITICAL(&location_mux_);
  if (!location_selection_pending_ &&
      location_catalog_.state != LocationCatalogState::kSaving) {
    location_catalog_requested_ = false;
    location_search_requested_ = true;
    location_catalog_showing_search_ = true;
    copyFixedText(pending_location_query_, sizeof(pending_location_query_),
                  query);
    location_catalog_.count = 0U;
    location_catalog_.state = LocationCatalogState::kLoading;
    location_catalog_.http_status = 0;
    ++location_catalog_.revision;
    accepted = true;
  }
  portEXIT_CRITICAL(&location_mux_);
  return accepted;
}

bool NetworkUplink::selectLocation(size_t index) {
  bool accepted = false;
  portENTER_CRITICAL(&location_mux_);
  if (index < location_catalog_.count &&
      validLocationId(location_catalog_.options[index].id) &&
      !location_selection_pending_) {
    std::memcpy(pending_location_id_, location_catalog_.options[index].id,
                sizeof(pending_location_id_));
    pending_location_id_[sizeof(pending_location_id_) - 1U] = '\0';
    pending_location_option_ = location_catalog_.options[index];
    location_selection_pending_ = true;
    location_catalog_.state = LocationCatalogState::kSaving;
    location_catalog_.http_status = 0;
    ++location_catalog_.revision;
    accepted = true;
  }
  portEXIT_CRITICAL(&location_mux_);
  return accepted;
}

void NetworkUplink::copyWifiCatalog(WifiCatalog *catalog) const {
  if (catalog == nullptr) {
    return;
  }
  portENTER_CRITICAL(&wifi_mux_);
  *catalog = wifi_catalog_;
  portEXIT_CRITICAL(&wifi_mux_);
}

void NetworkUplink::requestWifiScan() {
  portENTER_CRITICAL(&wifi_mux_);
  wifi_setup_active_ = true;
  wifi_scan_cancel_requested_ = false;
  if (wifi_catalog_.state != WifiSetupState::kConnecting &&
      wifi_catalog_.state != WifiSetupState::kForgetting &&
      wifi_catalog_.state != WifiSetupState::kScanning) {
    wifi_scan_requested_ = true;
    wifi_catalog_.count = 0U;
    wifi_catalog_.state = WifiSetupState::kScanning;
    wifi_catalog_.error = WifiSetupError::kNone;
    ++wifi_catalog_.revision;
  }
  portEXIT_CRITICAL(&wifi_mux_);
}

void NetworkUplink::endWifiSetup() {
  portENTER_CRITICAL(&wifi_mux_);
  wifi_setup_active_ = false;
  wifi_scan_requested_ = false;
  wifi_scan_cancel_requested_ = true;
  wifi_forget_requested_ = false;
  wifi_catalog_.state = WifiSetupState::kIdle;
  wifi_catalog_.error = WifiSetupError::kNone;
  ++wifi_catalog_.revision;
  portEXIT_CRITICAL(&wifi_mux_);
}

bool NetworkUplink::requestWifiConnect(const char *ssid,
                                       const char *password) {
  bool accepted = false;
  portENTER_CRITICAL(&wifi_mux_);
  if (wifi_catalog_.state != WifiSetupState::kConnecting &&
      wifi_catalog_.state != WifiSetupState::kForgetting &&
      wifi_catalog_.state != WifiSetupState::kScanning &&
      !wifi_connect_requested_) {
    const WifiNetworkOption *option = nullptr;
    for (size_t index = 0U; index < wifi_catalog_.count; ++index) {
      if (ssid != nullptr &&
          std::strcmp(wifi_catalog_.options[index].ssid, ssid) == 0) {
        option = &wifi_catalog_.options[index];
        break;
      }
    }
    const bool password_valid =
        option != nullptr &&
        (!option->secured || validSecuredWifiPassword(password));
    if (option != nullptr && option->supported &&
        validWifiSsid(option->ssid) && password_valid) {
      copyFixedText(pending_wifi_ssid_, sizeof(pending_wifi_ssid_),
                    option->ssid);
      clearSensitiveText(pending_wifi_password_,
                         sizeof(pending_wifi_password_));
      if (option->secured) {
        copyFixedText(pending_wifi_password_,
                      sizeof(pending_wifi_password_), password);
      }
      pending_wifi_auth_mode_ = option->auth_mode;
      pending_wifi_secured_ = option->secured;
      wifi_setup_active_ = true;
      wifi_connect_requested_ = true;
      wifi_catalog_.state = WifiSetupState::kConnecting;
      wifi_catalog_.error = WifiSetupError::kNone;
      ++wifi_catalog_.revision;
      accepted = true;
    } else {
      wifi_catalog_.state = WifiSetupState::kError;
      if (option == nullptr) {
        wifi_catalog_.error = WifiSetupError::kInvalidSelection;
      } else if (!option->supported) {
        wifi_catalog_.error = WifiSetupError::kUnsupportedSecurity;
      } else {
        wifi_catalog_.error = WifiSetupError::kInvalidPassword;
      }
      ++wifi_catalog_.revision;
    }
  }
  portEXIT_CRITICAL(&wifi_mux_);
  return accepted;
}

bool NetworkUplink::requestWifiForget() {
  bool accepted = false;
  portENTER_CRITICAL(&wifi_mux_);
  const bool busy = wifi_catalog_.state == WifiSetupState::kScanning ||
                    wifi_catalog_.state == WifiSetupState::kConnecting ||
                    wifi_catalog_.state == WifiSetupState::kForgetting;
  if (!busy && !wifi_forget_requested_ &&
      validWifiSsid(wifi_catalog_.active_ssid)) {
    wifi_setup_active_ = true;
    wifi_forget_requested_ = true;
    wifi_catalog_.state = WifiSetupState::kForgetting;
    wifi_catalog_.error = WifiSetupError::kNone;
    ++wifi_catalog_.revision;
    accepted = true;
  }
  portEXIT_CRITICAL(&wifi_mux_);
  return accepted;
}

void NetworkUplink::dismissWifiForgetError() {
  portENTER_CRITICAL(&wifi_mux_);
  if (wifi_catalog_.state == WifiSetupState::kError &&
      wifi_catalog_.error == WifiSetupError::kForgetFailed &&
      !wifi_forget_requested_) {
    wifi_catalog_.state = WifiSetupState::kReady;
    wifi_catalog_.error = WifiSetupError::kNone;
    ++wifi_catalog_.revision;
  }
  portEXIT_CRITICAL(&wifi_mux_);
}

void NetworkUplink::taskEntry(void *context) {
  static_cast<NetworkUplink *>(context)->taskLoop();
}

void NetworkUplink::updateStatus(bool wifi_connected, bool server_reachable,
                                 bool environment_reachable, int32_t rssi,
                                 uint32_t unix_time) {
  portENTER_CRITICAL(&status_mux_);
  status_ = {wifi_connected, server_reachable, environment_reachable, rssi,
             unix_time};
  portEXIT_CRITICAL(&status_mux_);
}

void NetworkUplink::publishEnvironment(const EnvironmentSnapshot &snapshot) {
  portENTER_CRITICAL(&environment_mux_);
  environment_ = snapshot;
  portEXIT_CRITICAL(&environment_mux_);
}

void NetworkUplink::publishRisk(const RiskSnapshot &snapshot,
                                RiskAvailability availability,
                                int http_status) {
  portENTER_CRITICAL(&risk_mux_);
  risk_ = snapshot;
  risk_status_ = {availability, http_status};
  portEXIT_CRITICAL(&risk_mux_);
}

void NetworkUplink::publishModelCatalog(const ModelCatalog &catalog) {
  portENTER_CRITICAL(&model_mux_);
  const uint32_t next_revision = model_catalog_.revision + 1U;
  model_catalog_ = catalog;
  model_catalog_.revision = next_revision;
  portEXIT_CRITICAL(&model_mux_);
}

void NetworkUplink::setModelCatalogState(ModelCatalogState state,
                                         int http_status) {
  portENTER_CRITICAL(&model_mux_);
  model_catalog_.state = state;
  model_catalog_.http_status = http_status;
  model_catalog_.pending_model_id[0] = '\0';
  ++model_catalog_.revision;
  portEXIT_CRITICAL(&model_mux_);
}

void NetworkUplink::setModelSelectionFailed(const char *model_id,
                                            int http_status) {
  portENTER_CRITICAL(&model_mux_);
  copyFixedText(model_catalog_.pending_model_id,
                sizeof(model_catalog_.pending_model_id), model_id);
  model_catalog_.state = ModelCatalogState::kError;
  model_catalog_.http_status = http_status;
  ++model_catalog_.revision;
  portEXIT_CRITICAL(&model_mux_);
}

void NetworkUplink::publishSimulation(const SimulationSnapshot &snapshot) {
  portENTER_CRITICAL(&simulation_mux_);
  const uint32_t next_revision = simulation_.revision + 1U;
  simulation_ = snapshot;
  simulation_.revision = next_revision;
  portEXIT_CRITICAL(&simulation_mux_);
}

void NetworkUplink::setSimulationState(SimulationState state,
                                       int http_status) {
  portENTER_CRITICAL(&simulation_mux_);
  simulation_.state = state;
  simulation_.http_status = http_status;
  ++simulation_.revision;
  portEXIT_CRITICAL(&simulation_mux_);
}

void NetworkUplink::recordSimulationUpload(const char *session_id,
                                           uint32_t seq, bool succeeded,
                                           int http_status,
                                           uint32_t attempted_at_ms) {
  if (session_id == nullptr || session_id[0] == '\0') {
    return;
  }
  portENTER_CRITICAL(&simulation_mux_);
  simulationRecordUploadAck(&simulation_, session_id, seq, succeeded,
                            http_status, attempted_at_ms);
  portEXIT_CRITICAL(&simulation_mux_);
}

void NetworkUplink::publishPendingLocationEnvironment(
    const LocationOption &option) {
  EnvironmentSnapshot pending{};
  resetEnvironmentSnapshot(&pending);
  copyFixedText(pending.location, sizeof(pending.location), option.location);
  copyFixedText(pending.display_location, sizeof(pending.display_location),
                option.display_location);
  copyFixedText(pending.weather, sizeof(pending.weather), "UPDATING");
  copyFixedText(pending.source, sizeof(pending.source), "manual");
  copyFixedText(pending.provider, sizeof(pending.provider), "server");
  copyFixedText(pending.updated_at, sizeof(pending.updated_at), "PENDING");
  pending.location_kind = option.is_coastal
                              ? EnvironmentLocationKind::kCoast
                              : EnvironmentLocationKind::kPlace;
  pending.stale = true;
  pending.fetched_at_ms = millis();
  pending.fetched_at_unix_time = unixTimeNow();
  publishEnvironment(pending);
}

void NetworkUplink::markEnvironmentStale() {
  portENTER_CRITICAL(&environment_mux_);
  environment_.stale = true;
  portEXIT_CRITICAL(&environment_mux_);
}

void NetworkUplink::markRiskStale(RiskAvailability availability,
                                  int http_status) {
  portENTER_CRITICAL(&risk_mux_);
  risk_.stale = true;
  risk_status_ = {availability, http_status};
  portEXIT_CRITICAL(&risk_mux_);
}

void NetworkUplink::publishLocationCatalog(const LocationCatalog &catalog) {
  portENTER_CRITICAL(&location_mux_);
  const uint32_t next_revision = location_catalog_.revision + 1U;
  location_catalog_ = catalog;
  location_catalog_.revision = next_revision;
  portEXIT_CRITICAL(&location_mux_);
}

void NetworkUplink::cacheAndPublishLocationPresets(
    const LocationCatalog &catalog) {
  portENTER_CRITICAL(&location_mux_);
  location_presets_cache_ = catalog;
  location_presets_cache_.state = LocationCatalogState::kReady;
  location_presets_cache_.http_status = 0;
  if (!location_catalog_showing_search_) {
    const uint32_t next_revision = location_catalog_.revision + 1U;
    location_catalog_ = location_presets_cache_;
    location_catalog_.revision = next_revision;
  }
  portEXIT_CRITICAL(&location_mux_);
}

void NetworkUplink::setLocationPresetError(int http_status) {
  portENTER_CRITICAL(&location_mux_);
  if (!location_catalog_showing_search_ &&
      location_presets_cache_.count == 0U) {
    location_catalog_.state = LocationCatalogState::kError;
    location_catalog_.http_status = http_status;
    ++location_catalog_.revision;
  }
  portEXIT_CRITICAL(&location_mux_);
}

void NetworkUplink::setLocationCatalogState(LocationCatalogState state,
                                             int http_status) {
  portENTER_CRITICAL(&location_mux_);
  location_catalog_.state = state;
  location_catalog_.http_status = http_status;
  ++location_catalog_.revision;
  portEXIT_CRITICAL(&location_mux_);
}

void NetworkUplink::publishWifiCatalog(const WifiCatalog &catalog) {
  portENTER_CRITICAL(&wifi_mux_);
  const uint32_t next_revision = wifi_catalog_.revision + 1U;
  wifi_catalog_ = catalog;
  wifi_catalog_.revision = next_revision;
  portEXIT_CRITICAL(&wifi_mux_);
}

void NetworkUplink::setWifiSetupState(WifiSetupState state,
                                      WifiSetupError error) {
  portENTER_CRITICAL(&wifi_mux_);
  wifi_catalog_.state = state;
  wifi_catalog_.error = error;
  ++wifi_catalog_.revision;
  portEXIT_CRITICAL(&wifi_mux_);
}

void NetworkUplink::taskLoop() {
  const bool server_configured = hasText(app_config::kServerBaseUrl);
  const bool secure_server =
      server_configured &&
      std::strncmp(app_config::kServerBaseUrl, "https://", 8U) == 0;

  char active_wifi_ssid[kWifiSsidBytes]{};
  char active_wifi_password[kWifiPasswordBytes]{};
  const StoredWifiProfileState stored_profile_state = loadStoredWifiProfile(
      active_wifi_ssid, sizeof(active_wifi_ssid), active_wifi_password,
      sizeof(active_wifi_password));
  const bool loaded_saved_profile =
      stored_profile_state == StoredWifiProfileState::kConfigured;
  if (stored_profile_state == StoredWifiProfileState::kAbsent &&
      validWifiSsid(app_config::kWifiSsid) &&
      validStoredWifiPassword(app_config::kWifiPassword)) {
    copyFixedText(active_wifi_ssid, sizeof(active_wifi_ssid),
                  app_config::kWifiSsid);
    copyFixedText(active_wifi_password, sizeof(active_wifi_password),
                  app_config::kWifiPassword);
  }
  bool wifi_configured = validWifiSsid(active_wifi_ssid);
  const char *wifi_configuration_source =
      loaded_saved_profile
          ? "nvs"
          : (stored_profile_state == StoredWifiProfileState::kBlocked
                 ? "forgotten-or-blocked"
                 : (wifi_configured ? "firmware" : "none"));

  WiFi.persistent(false);
  WiFi.onEvent(handleWifiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
  WiFi.setMinSecurity(hasText(active_wifi_password) ? WIFI_AUTH_WPA_PSK
                                                    : WIFI_AUTH_OPEN);

  portENTER_CRITICAL(&wifi_mux_);
  copyFixedText(wifi_catalog_.active_ssid,
                sizeof(wifi_catalog_.active_ssid), active_wifi_ssid);
  ++wifi_catalog_.revision;
  portEXIT_CRITICAL(&wifi_mux_);

  Serial.printf(
      "[WIFI] configuration: %s source=%s\n",
      wifi_configured ? "present" : "not set (UART-only mode)",
      wifi_configuration_source);
  Serial.printf("[HTTP] server: %s\n",
                server_configured ? app_config::kServerBaseUrl
                                  : "not set (uploads disabled)");
  Serial.printf("[HTTP] device token: %s\n",
                hasText(app_config::kDeviceToken) ? "present" : "not set");

  uint32_t next_wifi_attempt_ms = 0U;
  uint32_t next_http_attempt_ms = 0U;
  uint32_t next_environment_attempt_ms = 0U;
  uint32_t next_risk_attempt_ms = 0U;
  uint32_t next_simulation_recovery_ms = 0U;
  uint32_t last_upload_ms = 0U;
  size_t wifi_retry_index = 0U;
  size_t http_retry_index = 0U;
  size_t environment_retry_index = 0U;
  size_t risk_retry_index = 0U;
  size_t simulation_recovery_retry_index = 0U;
  bool was_connected = false;
  bool server_reachable = false;
  bool environment_reachable = false;
  bool selection_environment_refresh_pending = false;
  bool preset_prefetch_queued_once = false;
  bool have_pending = false;
  bool urgent_pending = false;
  bool have_alarm = false;
  uint8_t last_alarm = 0U;
  TelemetryFrame pending{};
  WifiScanPhase scan_phase = WifiScanPhase::kIdle;
  uint8_t scan_attempt = 0U;
  uint8_t scan_start_failures = 0U;
  uint32_t scan_not_before_ms = 0U;
  uint32_t scan_deadline_ms = 0U;
  uint32_t scan_done_baseline = 0U;
  bool scan_cancelled = false;
  bool scan_retry_after_drain = false;
  bool wifi_setup_was_active = false;
  bool forget_disconnect_in_progress = false;
  uint32_t forget_disconnect_deadline_ms = 0U;
  bool candidate_in_progress = false;
  bool candidate_connect_started = false;
  uint32_t candidate_started_ms = 0U;
  uint32_t candidate_deadline_ms = 0U;
  uint32_t candidate_not_before_ms = 0U;
  uint32_t candidate_release_revision = 0U;
  bool candidate_wait_for_disconnect_event = false;
  char candidate_wifi_ssid[kWifiSsidBytes]{};
  char candidate_wifi_password[kWifiPasswordBytes]{};
  uint8_t candidate_wifi_auth_mode = static_cast<uint8_t>(WIFI_AUTH_OPEN);
  bool candidate_wifi_secured = true;
  bool candidate_was_associated = false;
  uint32_t candidate_disconnect_revision = 0U;
  uint32_t candidate_connected_revision = 0U;
  uint8_t candidate_last_disconnect_reason = 0U;
  uint32_t last_logged_disconnect_revision =
      wifiEventSnapshot().disconnected_revision;

  while (true) {
    const uint32_t now_ms = millis();
    const WifiEventSnapshot current_wifi_events = wifiEventSnapshot();
    if (current_wifi_events.disconnected_revision !=
        last_logged_disconnect_revision) {
      last_logged_disconnect_revision =
          current_wifi_events.disconnected_revision;
      Serial.printf(
          "[WIFI_EVENT] disconnected reason=%u name=%s rssi=%d\n",
          static_cast<unsigned>(current_wifi_events.disconnect_reason),
          WiFi.disconnectReasonName(static_cast<wifi_err_reason_t>(
              current_wifi_events.disconnect_reason)),
          static_cast<int>(current_wifi_events.disconnect_rssi));
    }

    bool start_scan = false;
    bool start_candidate = false;
    bool start_forget = false;
    bool cancel_scan = false;
    bool wifi_setup_active = false;
    portENTER_CRITICAL(&wifi_mux_);
    if (wifi_scan_cancel_requested_) {
      wifi_scan_cancel_requested_ = false;
      cancel_scan = true;
    }
    if (!cancel_scan && wifi_forget_requested_ && wifi_setup_active_ &&
        scan_phase == WifiScanPhase::kIdle && !candidate_in_progress) {
      wifi_forget_requested_ = false;
      wifi_connect_requested_ = false;
      clearSensitiveText(pending_wifi_password_,
                         sizeof(pending_wifi_password_));
      start_forget = true;
    } else if (!cancel_scan && wifi_scan_requested_ && wifi_setup_active_ &&
        scan_phase == WifiScanPhase::kIdle &&
        !candidate_in_progress) {
      wifi_scan_requested_ = false;
      start_scan = true;
    } else if (!cancel_scan && wifi_connect_requested_ &&
               scan_phase == WifiScanPhase::kIdle &&
               !candidate_in_progress) {
      wifi_connect_requested_ = false;
      copyFixedText(candidate_wifi_ssid, sizeof(candidate_wifi_ssid),
                    pending_wifi_ssid_);
      copyFixedText(candidate_wifi_password,
                    sizeof(candidate_wifi_password),
                    pending_wifi_password_);
      candidate_wifi_auth_mode = pending_wifi_auth_mode_;
      candidate_wifi_secured = pending_wifi_secured_;
      clearSensitiveText(pending_wifi_password_,
                         sizeof(pending_wifi_password_));
      start_candidate = true;
    }
    wifi_setup_active = wifi_setup_active_;
    portEXIT_CRITICAL(&wifi_mux_);

    if (wifi_setup_was_active && !wifi_setup_active) {
      wifi_retry_index = 0U;
      next_wifi_attempt_ms = now_ms + kWifiScanDisconnectSettleMs;
      Serial.println("[WIFI_SETUP] closed; saved-network reconnect resumed");
    }
    wifi_setup_was_active = wifi_setup_active;

    if (start_forget) {
      if (saveForgottenWifiProfile()) {
        const bool disconnect_requested = WiFi.disconnect(false, true);
        active_wifi_ssid[0] = '\0';
        clearSensitiveText(active_wifi_password,
                           sizeof(active_wifi_password));
        clearSensitiveText(candidate_wifi_password,
                           sizeof(candidate_wifi_password));
        wifi_configured = false;
        wifi_retry_index = 0U;
        server_reachable = false;
        environment_reachable = false;
        markEnvironmentStale();
        portENTER_CRITICAL(&wifi_mux_);
        wifi_catalog_.active_ssid[0] = '\0';
        wifi_catalog_.state = WifiSetupState::kForgetting;
        wifi_catalog_.error = WifiSetupError::kNone;
        ++wifi_catalog_.revision;
        portEXIT_CRITICAL(&wifi_mux_);
        forget_disconnect_in_progress = true;
        forget_disconnect_deadline_ms =
            now_ms + kWifiCandidateDisconnectTimeoutMs;
        Serial.printf(
            "[WIFI_SETUP] tombstone saved; disconnecting old wifi requested=%u\n",
            disconnect_requested ? 1U : 0U);
      } else {
        setWifiSetupState(WifiSetupState::kError,
                          WifiSetupError::kForgetFailed);
        Serial.println("[WIFI_SETUP] ERROR failed to persist forget tombstone");
      }
    }

    if (forget_disconnect_in_progress) {
      if (WiFi.status() != WL_CONNECTED) {
        forget_disconnect_in_progress = false;
        was_connected = false;
        setWifiSetupState(WifiSetupState::kReady, WifiSetupError::kNone);
        Serial.println(
            "[WIFI_SETUP] saved network forgotten; firmware fallback blocked");
      } else if (static_cast<int32_t>(
                     now_ms - forget_disconnect_deadline_ms) >= 0) {
        WiFi.mode(WIFI_OFF);
        vTaskDelay(pdMS_TO_TICKS(50U));
        WiFi.mode(WIFI_STA);
        WiFi.setAutoReconnect(false);
        WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
        WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
        WiFi.setMinSecurity(WIFI_AUTH_OPEN);
        forget_disconnect_in_progress = false;
        was_connected = false;
        setWifiSetupState(WifiSetupState::kReady, WifiSetupError::kNone);
        Serial.println(
            "[WIFI_SETUP] WARN forced STA reset after forget disconnect timeout");
      }
    }

    if (cancel_scan) {
      scan_cancelled = true;
      scan_retry_after_drain = false;
      if (scan_phase == WifiScanPhase::kRunning) {
        const int16_t terminal_scan = WiFi.scanComplete();
        if (terminal_scan >= 0) {
          WiFi.scanDelete();
          scan_phase = WifiScanPhase::kIdle;
        } else {
          scan_done_baseline = wifiEventSnapshot().scan_done_revision;
          const esp_err_t stop_result = esp_wifi_scan_stop();
          scan_phase = WifiScanPhase::kDraining;
          scan_deadline_ms = now_ms + kWifiScanDrainTimeoutMs;
          Serial.printf(
              "[WIFI_SETUP] scan cancel draining code=%d stop=%d rev=%lu\n",
              static_cast<int>(terminal_scan), static_cast<int>(stop_result),
              static_cast<unsigned long>(scan_done_baseline));
        }
      } else if (scan_phase != WifiScanPhase::kDraining) {
        scan_phase = WifiScanPhase::kIdle;
      }
      if (scan_phase == WifiScanPhase::kIdle) {
        scan_cancelled = false;
        wifi_retry_index = 0U;
        next_wifi_attempt_ms = now_ms + kWifiScanDisconnectSettleMs;
      }
    }

    if (start_scan) {
      // A saved but unavailable AP may still be inside esp_wifi_connect() when
      // the picker opens. Own the radio for the entire setup scan. If there is
      // no scan to drain, cycle STA mode so an in-flight connect cannot make
      // esp_wifi_scan_start() fail with ESP_ERR_WIFI_STATE.
      const int16_t stale_scan = WiFi.scanComplete();
      const bool had_connection = WiFi.status() == WL_CONNECTED;
      bool radio_reset = false;
      scan_attempt = 0U;
      scan_start_failures = 0U;
      scan_cancelled = false;
      scan_retry_after_drain = false;
      esp_err_t stop_result = ESP_OK;
      if (stale_scan == WIFI_SCAN_RUNNING) {
        scan_done_baseline = wifiEventSnapshot().scan_done_revision;
        stop_result = esp_wifi_scan_stop();
        scan_phase = WifiScanPhase::kDraining;
        scan_retry_after_drain = true;
        scan_deadline_ms = now_ms + kWifiScanDrainTimeoutMs;
      } else {
        if (stale_scan >= 0) {
          WiFi.scanDelete();
        }
        resetWifiStationForSetup(active_wifi_password);
        radio_reset = true;
        was_connected = false;
        server_reachable = false;
        environment_reachable = false;
        markEnvironmentStale();
        scan_phase = WifiScanPhase::kSettling;
        scan_not_before_ms = millis() + kWifiScanDisconnectSettleMs;
      }
      Serial.printf(
          "[WIFI_SETUP] scan requested stale=%d stop=%d "
          "had_connection=%u radio_reset=%u\n",
          static_cast<int>(stale_scan), static_cast<int>(stop_result),
          had_connection ? 1U : 0U, radio_reset ? 1U : 0U);
    }

    if (scan_phase == WifiScanPhase::kDraining) {
      const WifiEventSnapshot drain_events = wifiEventSnapshot();
      const int16_t terminal_scan = WiFi.scanComplete();
      const bool done_event_seen =
          drain_events.scan_done_revision != scan_done_baseline;
      const bool safely_drained = terminal_scan >= 0 ||
                                  (done_event_seen &&
                                   terminal_scan != WIFI_SCAN_RUNNING);
      if (safely_drained) {
        WiFi.scanDelete();
        if (scan_cancelled || !wifi_setup_active) {
          scan_phase = WifiScanPhase::kIdle;
          next_wifi_attempt_ms = now_ms + kWifiScanDisconnectSettleMs;
          Serial.println("[WIFI_SETUP] cancelled scan drained");
        } else if (scan_retry_after_drain &&
                   scan_attempt < kWifiScanMaxAttempts) {
          resetWifiStationForSetup(active_wifi_password);
          was_connected = false;
          server_reachable = false;
          environment_reachable = false;
          markEnvironmentStale();
          scan_phase = WifiScanPhase::kSettling;
          scan_not_before_ms = millis() + kWifiScanDisconnectSettleMs;
          Serial.printf(
              "[WIFI_SETUP] old scan drained; STA reset; retry attempt=%u\n",
              static_cast<unsigned>(scan_attempt + 1U));
        } else {
          scan_phase = WifiScanPhase::kIdle;
          setWifiSetupState(WifiSetupState::kError,
                            WifiSetupError::kScanFailed);
          Serial.println("[WIFI_SETUP] scan drained without retry budget");
        }
        scan_cancelled = false;
        scan_retry_after_drain = false;
      } else if (static_cast<int32_t>(now_ms - scan_deadline_ms) >= 0) {
        // A missing SCAN_DONE means the Arduino wrapper and IDF driver can no
        // longer be paired safely.  Reset only the STA radio before exposing an
        // error; this prevents a late event from corrupting the next request.
        WiFi.mode(WIFI_OFF);
        vTaskDelay(pdMS_TO_TICKS(50U));
        WiFi.scanComplete();
        WiFi.scanDelete();
        WiFi.mode(WIFI_STA);
        WiFi.setAutoReconnect(false);
        WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
        WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
        WiFi.setMinSecurity(hasText(active_wifi_password)
                                ? WIFI_AUTH_WPA_PSK
                                : WIFI_AUTH_OPEN);
        scan_phase = WifiScanPhase::kIdle;
        was_connected = false;
        server_reachable = false;
        environment_reachable = false;
        markEnvironmentStale();
        if (scan_cancelled || !wifi_setup_active) {
          next_wifi_attempt_ms = now_ms + kWifiScanDisconnectSettleMs;
          Serial.println("[WIFI_SETUP] cancelled scan forced radio recovery");
        } else {
          setWifiSetupState(WifiSetupState::kError,
                            WifiSetupError::kScanFailed);
          Serial.printf(
              "[WIFI_SETUP] ERROR scan drain timeout baseline=%lu current=%lu\n",
              static_cast<unsigned long>(scan_done_baseline),
              static_cast<unsigned long>(drain_events.scan_done_revision));
        }
        scan_cancelled = false;
        scan_retry_after_drain = false;
      }
    }

    int16_t completed_scan_result = INT16_MIN;
    if (scan_phase == WifiScanPhase::kSettling &&
        static_cast<int32_t>(now_ms - scan_not_before_ms) >= 0) {
      // scanDelete() does not clear WIFI_SCANNING_BIT in Arduino-ESP32 2.x.
      // scanComplete() does, so wait for a stale scan to finish or time out
      // before starting the next asynchronous attempt.
      const int16_t previous_scan = WiFi.scanComplete();
      if (previous_scan == WIFI_SCAN_RUNNING) {
        scan_done_baseline = wifiEventSnapshot().scan_done_revision;
        const esp_err_t stop_result = esp_wifi_scan_stop();
        scan_phase = WifiScanPhase::kDraining;
        scan_retry_after_drain = true;
        scan_deadline_ms = now_ms + kWifiScanDrainTimeoutMs;
        Serial.printf(
            "[WIFI_SETUP] stale wrapper scan draining stop=%d rev=%lu\n",
            static_cast<int>(stop_result),
            static_cast<unsigned long>(scan_done_baseline));
      } else {
        if (previous_scan >= 0) {
          WiFi.scanDelete();
          scan_not_before_ms = now_ms + kWifiScanQuietMs;
        } else {
          // Arduino-ESP32 2.0.17 leaves the final
          // wifi_scan_config_t::home_chan_dwell_time field uninitialized in
          // WiFi.scanNetworks(). Build a zero-initialized IDF config here so
          // scanning is deterministic on the ESP32-S3. Arduino's event handler
          // still receives SCAN_DONE first and populates WiFi.SSID/RSSI data.
          WiFi.scanDelete();
          wifi_scan_config_t scan_config{};
          scan_config.ssid = nullptr;
          scan_config.bssid = nullptr;
          scan_config.channel = 0U;
          scan_config.show_hidden = true;
          scan_config.scan_type = WIFI_SCAN_TYPE_ACTIVE;
          scan_config.scan_time.active.min = 100U;
          scan_config.scan_time.active.max = kWifiScanMaxMsPerChannel;
          scan_config.home_chan_dwell_time = 0U;
          scan_done_baseline = wifiEventSnapshot().scan_done_revision;
          const esp_err_t start_result =
              esp_wifi_scan_start(&scan_config, false);
          if (start_result == ESP_OK) {
            ++scan_attempt;
            scan_start_failures = 0U;
            scan_phase = WifiScanPhase::kRunning;
            scan_deadline_ms = millis() + kWifiScanHardTimeoutMs;
            Serial.printf(
                "[WIFI_SETUP] radio scan started attempt=%u rev=%lu\n",
                static_cast<unsigned>(scan_attempt),
                static_cast<unsigned long>(scan_done_baseline));
          } else if (++scan_start_failures <=
                     kWifiScanMaxStartRecoveries) {
            resetWifiStationForSetup(active_wifi_password);
            was_connected = false;
            server_reachable = false;
            environment_reachable = false;
            markEnvironmentStale();
            scan_not_before_ms = millis() + kWifiScanDisconnectSettleMs;
            Serial.printf(
                "[WIFI_SETUP] scan start rejected; STA reset; retry "
                "recovery=%u code=%d\n",
                static_cast<unsigned>(scan_start_failures),
                static_cast<int>(start_result));
          } else {
            scan_phase = WifiScanPhase::kIdle;
            setWifiSetupState(WifiSetupState::kError,
                              WifiSetupError::kScanFailed);
            Serial.printf(
                "[WIFI_SETUP] radio scan could not start recoveries=%u "
                "code=%d\n",
                static_cast<unsigned>(scan_start_failures),
                static_cast<int>(start_result));
          }
        }
      }
    }

    if (scan_phase == WifiScanPhase::kRunning) {
      const WifiEventSnapshot scan_events = wifiEventSnapshot();
      const bool done_event_seen =
          scan_events.scan_done_revision != scan_done_baseline;
      if (done_event_seen) {
        const int16_t scan_result = WiFi.scanComplete();
        if (scan_result >= 0) {
          completed_scan_result = scan_result;
        } else {
          WiFi.scanDelete();
          if (scan_attempt < kWifiScanMaxAttempts) {
            scan_phase = WifiScanPhase::kSettling;
            scan_not_before_ms = now_ms + kWifiScanRetryDelayMs;
          } else {
            scan_phase = WifiScanPhase::kIdle;
            setWifiSetupState(WifiSetupState::kError,
                              WifiSetupError::kScanFailed);
          }
          Serial.printf(
              "[WIFI_SETUP] scan event had no readable results code=%d\n",
              static_cast<int>(scan_result));
        }
      } else if (static_cast<int32_t>(now_ms - scan_deadline_ms) >= 0) {
        scan_done_baseline = scan_events.scan_done_revision;
        const esp_err_t stop_result = esp_wifi_scan_stop();
        if (stop_result == ESP_OK) {
          scan_phase = WifiScanPhase::kDraining;
          scan_retry_after_drain = true;
          scan_deadline_ms = now_ms + kWifiScanDrainTimeoutMs;
        } else if (scan_attempt < kWifiScanMaxAttempts) {
          resetWifiStationForSetup(active_wifi_password);
          was_connected = false;
          server_reachable = false;
          environment_reachable = false;
          markEnvironmentStale();
          scan_phase = WifiScanPhase::kSettling;
          scan_not_before_ms = millis() + kWifiScanDisconnectSettleMs;
        } else {
          scan_phase = WifiScanPhase::kIdle;
          setWifiSetupState(WifiSetupState::kError,
                            WifiSetupError::kScanFailed);
        }
        Serial.printf(
            "[WIFI_SETUP] running scan timed out stop=%d rev=%lu\n",
            static_cast<int>(stop_result),
            static_cast<unsigned long>(scan_done_baseline));
      }
    }

    if (completed_scan_result >= 0) {
      if (completed_scan_result == 0 &&
          scan_attempt < kWifiScanMaxAttempts) {
        WiFi.scanDelete();
        scan_phase = WifiScanPhase::kSettling;
        scan_not_before_ms = now_ms + kWifiScanRetryDelayMs;
        Serial.printf(
            "[WIFI_SETUP] empty scan retry scheduled attempt=%u\n",
            static_cast<unsigned>(scan_attempt + 1U));
        completed_scan_result = INT16_MIN;
      }
    }

    if (completed_scan_result >= 0) {
      WifiCatalog scanned{};
      scanned.state = WifiSetupState::kReady;
      scanned.error = WifiSetupError::kNone;
      copyFixedText(scanned.active_ssid, sizeof(scanned.active_ssid),
                    active_wifi_ssid);
      for (int index = 0; index < completed_scan_result; ++index) {
        const wifi_auth_mode_t authentication = WiFi.encryptionType(index);
        addWifiScanResult(&scanned, WiFi.SSID(index), WiFi.RSSI(index),
                          authentication);
      }
      sortWifiCatalog(&scanned);
      WiFi.scanDelete();
      scan_phase = WifiScanPhase::kIdle;
      publishWifiCatalog(scanned);
      Serial.printf("[WIFI_SETUP] scan complete visible=%u retained=%u\n",
                    static_cast<unsigned>(completed_scan_result),
                    static_cast<unsigned>(scanned.count));
      for (size_t index = 0U; index < scanned.count; ++index) {
        const WifiNetworkOption &option = scanned.options[index];
        Serial.printf(
            "[WIFI_SETUP] option ssid='%s' rssi=%ld auth=%u "
            "supported=%u\n",
            option.ssid, static_cast<long>(option.rssi),
            static_cast<unsigned>(option.auth_mode),
            option.supported ? 1U : 0U);
      }
    }

    if (start_candidate) {
      const wl_status_t old_link_status = WiFi.status();
      const WifiEventSnapshot before_disconnect = wifiEventSnapshot();
      const bool disconnect_requested = WiFi.disconnect(false, false);
      candidate_started_ms = now_ms;
      candidate_deadline_ms = now_ms + kWifiCandidateDisconnectTimeoutMs;
      candidate_not_before_ms = now_ms + kWifiScanDisconnectSettleMs;
      candidate_release_revision = before_disconnect.disconnected_revision;
      candidate_wait_for_disconnect_event =
          old_link_status == WL_CONNECTED && disconnect_requested;
      candidate_in_progress = true;
      candidate_connect_started = false;
      candidate_was_associated = false;
      candidate_last_disconnect_reason = 0U;
      was_connected = false;
      server_reachable = false;
      environment_reachable = false;
      markEnvironmentStale();
      Serial.printf(
          "[WIFI_SETUP] disconnecting old link before ssid='%s' secured=%u "
          "wait_event=%u rev=%lu\n",
          candidate_wifi_ssid, candidate_wifi_secured ? 1U : 0U,
          candidate_wait_for_disconnect_event ? 1U : 0U,
          static_cast<unsigned long>(candidate_release_revision));
    }

    // WiFi.disconnect() reports completion asynchronously.  Do not call
    // WiFi.begin(), or accept WL_CONNECTED, until this task has observed the
    // previous station link leave WL_CONNECTED.  Otherwise the old link's
    // status can be mistaken for a successful candidate and bad credentials
    // can be committed to NVS.
    if (candidate_in_progress && !candidate_connect_started) {
      const WifiEventSnapshot release_events = wifiEventSnapshot();
      const bool disconnect_event_seen =
          !candidate_wait_for_disconnect_event ||
          release_events.disconnected_revision != candidate_release_revision;
      const bool radio_settled =
          static_cast<int32_t>(now_ms - candidate_not_before_ms) >= 0;
      if (WiFi.status() != WL_CONNECTED && disconnect_event_seen &&
          radio_settled) {
        const wifi_auth_mode_t candidate_auth_mode =
            static_cast<wifi_auth_mode_t>(candidate_wifi_auth_mode);
        WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
        WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
        WiFi.setMinSecurity(minimumSecurityForAuth(candidate_auth_mode));
        const WifiEventSnapshot baseline = wifiEventSnapshot();
        candidate_disconnect_revision = baseline.disconnected_revision;
        candidate_connected_revision = baseline.connected_revision;
        WiFi.begin(candidate_wifi_ssid,
                   candidate_wifi_secured ? candidate_wifi_password : "");
        candidate_connect_started = true;
        candidate_started_ms = now_ms;
        candidate_deadline_ms = now_ms + kWifiCandidateTimeoutMs;
        Serial.printf(
            "[WIFI_SETUP] connecting ssid='%s' secured=%u auth=%u\n",
                      candidate_wifi_ssid,
                      candidate_wifi_secured ? 1U : 0U,
                      static_cast<unsigned>(candidate_wifi_auth_mode));
      } else if (static_cast<int32_t>(now_ms - candidate_deadline_ms) >= 0) {
        candidate_in_progress = false;
        clearSensitiveText(candidate_wifi_password,
                           sizeof(candidate_wifi_password));
        setWifiSetupState(WifiSetupState::kError,
                          WifiSetupError::kOldLinkBusy);
        Serial.println(
            "[WIFI_SETUP] old link did not disconnect; candidate not started");
      }
    }

    if (candidate_in_progress && candidate_connect_started) {
      const WifiEventSnapshot candidate_events = wifiEventSnapshot();
      if (candidate_events.connected_revision !=
          candidate_connected_revision) {
        candidate_connected_revision = candidate_events.connected_revision;
        candidate_was_associated = true;
      }
      if (candidate_events.disconnected_revision !=
          candidate_disconnect_revision) {
        candidate_disconnect_revision =
            candidate_events.disconnected_revision;
        const uint8_t reason = candidate_events.disconnect_reason;
        if (reason != WIFI_REASON_ASSOC_LEAVE &&
            reason != WIFI_REASON_AUTH_LEAVE) {
          candidate_last_disconnect_reason = reason;
        }
      }
    }

    bool connected = WiFi.status() == WL_CONNECTED;
    const bool candidate_ssid_matches =
        candidate_connect_started && connected &&
        WiFi.SSID().equals(candidate_wifi_ssid);
    if (candidate_in_progress && candidate_ssid_matches) {
      const bool saved = saveStoredWifiProfile(
          candidate_wifi_ssid,
          candidate_wifi_secured ? candidate_wifi_password : "");
      if (saved) {
        copyFixedText(active_wifi_ssid, sizeof(active_wifi_ssid),
                      candidate_wifi_ssid);
        clearSensitiveText(active_wifi_password,
                           sizeof(active_wifi_password));
        if (candidate_wifi_secured) {
          copyFixedText(active_wifi_password, sizeof(active_wifi_password),
                        candidate_wifi_password);
        }
        wifi_configured = true;
        portENTER_CRITICAL(&wifi_mux_);
        copyFixedText(wifi_catalog_.active_ssid,
                      sizeof(wifi_catalog_.active_ssid), active_wifi_ssid);
        wifi_catalog_.state = WifiSetupState::kConnected;
        wifi_catalog_.error = WifiSetupError::kNone;
        ++wifi_catalog_.revision;
        portEXIT_CRITICAL(&wifi_mux_);
        candidate_in_progress = false;
        candidate_connect_started = false;
        clearSensitiveText(candidate_wifi_password,
                           sizeof(candidate_wifi_password));
        wifi_retry_index = 0U;
        http_retry_index = 0U;
        environment_retry_index = 0U;
        next_http_attempt_ms = now_ms;
        next_environment_attempt_ms = now_ms;
        Serial.printf("[WIFI_SETUP] connected and saved ssid='%s'\n",
                      active_wifi_ssid);
      } else {
        WiFi.disconnect(false, false);
        candidate_in_progress = false;
        candidate_connect_started = false;
        clearSensitiveText(candidate_wifi_password,
                           sizeof(candidate_wifi_password));
        next_wifi_attempt_ms = now_ms + app_config::kRetryBackoffMs[0];
        setWifiSetupState(WifiSetupState::kError,
                          WifiSetupError::kStorageFailed);
        connected = false;
        Serial.println(
            "[WIFI_SETUP] profile save failed; previous profile retained");
      }
    } else if (candidate_in_progress && candidate_connect_started &&
               !candidate_ssid_matches) {
      const wl_status_t candidate_status = WiFi.status();
      const bool timed_out =
          static_cast<int32_t>(now_ms - candidate_deadline_ms) >= 0;
      if (timed_out) {
        const WifiSetupError failure = classifyWifiConnectionFailure(
            candidate_last_disconnect_reason, candidate_was_associated);
        WiFi.disconnect(false, false);
        candidate_in_progress = false;
        candidate_connect_started = false;
        clearSensitiveText(candidate_wifi_password,
                           sizeof(candidate_wifi_password));
        next_wifi_attempt_ms = now_ms + kWifiScanDisconnectSettleMs;
        setWifiSetupState(WifiSetupState::kError, failure);
        Serial.printf(
            "[WIFI_SETUP] connection failed error=%u status=%d "
            "reason=%u name=%s associated=%u elapsed_ms=%lu\n",
            static_cast<unsigned>(failure),
            static_cast<int>(candidate_status),
            static_cast<unsigned>(candidate_last_disconnect_reason),
            WiFi.disconnectReasonName(static_cast<wifi_err_reason_t>(
                candidate_last_disconnect_reason)),
            candidate_was_associated ? 1U : 0U,
            static_cast<unsigned long>(now_ms - candidate_started_ms));
      }
    }

    connected = WiFi.status() == WL_CONNECTED;
    const bool wifi_radio_busy = scan_phase != WifiScanPhase::kIdle ||
                                 candidate_in_progress || start_forget ||
                                 forget_disconnect_in_progress;

    // Re-read this immediately before WiFi.begin(). The earlier loop snapshot
    // may predate a UI scan request; using it here could launch one final saved
    // network connection just as setup takes ownership of the radio.
    bool saved_reconnect_allowed = false;
    portENTER_CRITICAL(&wifi_mux_);
    saved_reconnect_allowed =
        !wifi_setup_active_ && !wifi_scan_requested_ &&
        !wifi_connect_requested_ && !wifi_forget_requested_;
    portEXIT_CRITICAL(&wifi_mux_);

    if (wifi_configured && !connected && !wifi_radio_busy &&
        saved_reconnect_allowed &&
        static_cast<int32_t>(now_ms - next_wifi_attempt_ms) >= 0) {
      Serial.printf("[WIFI] connecting to SSID '%s'\n", active_wifi_ssid);
      WiFi.setMinSecurity(hasText(active_wifi_password) ? WIFI_AUTH_WPA_PSK
                                                       : WIFI_AUTH_OPEN);
      WiFi.begin(active_wifi_ssid, active_wifi_password);
      bool setup_started_during_begin = false;
      portENTER_CRITICAL(&wifi_mux_);
      setup_started_during_begin =
          wifi_setup_active_ || wifi_scan_requested_ ||
          wifi_connect_requested_ || wifi_forget_requested_;
      portEXIT_CRITICAL(&wifi_mux_);
      if (setup_started_during_begin) {
        WiFi.disconnect(false, false);
        next_wifi_attempt_ms = now_ms + kWifiScanDisconnectSettleMs;
        Serial.println(
            "[WIFI_SETUP] cancelled saved reconnect that raced with setup");
      } else {
        next_wifi_attempt_ms = now_ms + nextBackoff(&wifi_retry_index);
      }
    }

    if (connected && !was_connected) {
      wifi_retry_index = 0U;
      next_http_attempt_ms = now_ms;
      next_environment_attempt_ms = now_ms;
      next_simulation_recovery_ms = now_ms;
      portENTER_CRITICAL(&simulation_mux_);
      if (!simulationSessionOpen(simulation_.state)) {
        simulation_recovery_requested_ = true;
      }
      portEXIT_CRITICAL(&simulation_mux_);
      Serial.printf("[WIFI] connected ip=%s rssi=%ld dBm\n",
                    WiFi.localIP().toString().c_str(),
                    static_cast<long>(WiFi.RSSI()));
      configTime(0, 0, app_config::kNtpServer1, app_config::kNtpServer2);
      portENTER_CRITICAL(&wifi_mux_);
      if (!wifi_setup_active_) {
        copyFixedText(wifi_catalog_.active_ssid,
                      sizeof(wifi_catalog_.active_ssid), active_wifi_ssid);
        wifi_catalog_.state = WifiSetupState::kConnected;
        wifi_catalog_.error = WifiSetupError::kNone;
        ++wifi_catalog_.revision;
      }
      portEXIT_CRITICAL(&wifi_mux_);
    } else if (!connected && was_connected) {
      server_reachable = false;
      environment_reachable = false;
      markEnvironmentStale();
      markRiskStale(RiskAvailability::kUnavailable, 0);
      Serial.println("[WIFI] disconnected; UART gateway remains active");
    }
    was_connected = connected;

    const bool server_clock_ready = !secure_server || unixTimeNow() != 0U;
    if (connected && server_configured && server_clock_ready &&
        !wifi_radio_busy && !preset_prefetch_queued_once) {
      bool queued = false;
      portENTER_CRITICAL(&location_mux_);
      if (location_presets_cache_.count == 0U &&
          !location_catalog_requested_ && !location_search_requested_ &&
          !location_selection_pending_) {
        location_catalog_requested_ = true;
        queued = true;
      }
      portEXIT_CRITICAL(&location_mux_);
      if (queued) {
        preset_prefetch_queued_once = true;
        Serial.println("[LOCATION] startup preset prefetch queued");
      }
    }

    TelemetryFrame incoming{};
    while (queue_ != nullptr && xQueueReceive(queue_, &incoming, 0U) == pdTRUE) {
      const bool alarm_changed = !have_alarm || incoming.alarm_level != last_alarm;
      pending = incoming;
      have_pending = true;
      urgent_pending = urgent_pending || alarm_changed;
      have_alarm = true;
      last_alarm = incoming.alarm_level;
    }

    bool model_catalog_request = false;
    bool model_selection_request = false;
    char requested_model_id[kModelIdBytes]{};
    if (connected && server_configured && server_clock_ready &&
        !wifi_radio_busy) {
      portENTER_CRITICAL(&model_mux_);
      if (model_selection_requested_) {
        model_selection_requested_ = false;
        model_selection_request = true;
        copyFixedText(requested_model_id, sizeof(requested_model_id),
                      pending_model_id_);
      } else if (model_catalog_requested_) {
        model_catalog_requested_ = false;
        model_catalog_request = true;
      }
      portEXIT_CRITICAL(&model_mux_);
    }

    if (model_catalog_request) {
      ModelCatalog catalog{};
      int http_status = 0;
      if (fetchModelCatalog(&catalog, &http_status)) {
        catalog.http_status = http_status;
        publishModelCatalog(catalog);
        server_reachable = true;
        Serial.printf("[MODEL] catalog loaded count=%u selected=%s\n",
                      static_cast<unsigned>(catalog.count),
                      catalog.selected_model_id);
      } else {
        setModelCatalogState(ModelCatalogState::kError, http_status);
        server_reachable = false;
        Serial.printf("[MODEL] catalog GET failed code=%d\n", http_status);
      }
    }

    if (model_selection_request) {
      int http_status = 0;
      if (putDeviceModel(requested_model_id, &http_status)) {
        portENTER_CRITICAL(&model_mux_);
        copyFixedText(model_catalog_.selected_model_id,
                      sizeof(model_catalog_.selected_model_id),
                      requested_model_id);
        model_catalog_.pending_model_id[0] = '\0';
        model_catalog_.state = ModelCatalogState::kReady;
        model_catalog_.http_status = http_status;
        ++model_catalog_.revision;
        portEXIT_CRITICAL(&model_mux_);
        RiskSnapshot pending_risk{};
        resetRiskSnapshot(&pending_risk);
        publishRisk(pending_risk, RiskAvailability::kWaiting, 0);
        next_risk_attempt_ms = now_ms;
        server_reachable = true;
        Serial.printf("[MODEL] selected id=%s code=%d\n",
                      requested_model_id, http_status);
      } else {
        setModelSelectionFailed(requested_model_id, http_status);
        server_reachable = false;
        Serial.printf("[MODEL] selection failed id=%s code=%d\n",
                      requested_model_id, http_status);
      }
    }

    bool simulation_recovery_request = false;
    bool simulation_start_request = false;
    bool simulation_stop_request = false;
    char stopping_session_id[kSimulationSessionIdBytes]{};
    if (connected && server_configured && server_clock_ready &&
        !wifi_radio_busy) {
      portENTER_CRITICAL(&simulation_mux_);
      if (simulation_recovery_requested_ &&
          static_cast<int32_t>(now_ms - next_simulation_recovery_ms) >= 0) {
        simulation_recovery_requested_ = false;
        simulation_recovery_request = true;
      } else if (simulation_stop_requested_) {
        simulation_stop_requested_ = false;
        simulation_stop_request = true;
        copyFixedText(stopping_session_id, sizeof(stopping_session_id),
                      simulation_.session_id);
      } else if (simulation_start_requested_) {
        simulation_start_requested_ = false;
        simulation_start_request = true;
      }
      portEXIT_CRITICAL(&simulation_mux_);
    }

    if (simulation_recovery_request) {
      SimulationSnapshot recovered{};
      int http_status = 0;
      if (fetchActiveSimulation(&recovered, &http_status)) {
        portENTER_CRITICAL(&simulation_mux_);
        simulation_start_requested_ = false;
        simulation_stop_requested_ = false;
        portEXIT_CRITICAL(&simulation_mux_);
        recovered.http_status = http_status;
        publishSimulation(recovered);
        simulation_recovery_retry_index = 0U;
        server_reachable = true;
        Serial.printf("[SIMULATION] recovered active session id=%s\n",
                      recovered.session_id);
      } else if (http_status == 404) {
        const SimulationSnapshot current = simulation();
        if (current.state == SimulationState::kIdle) {
          setSimulationState(SimulationState::kIdle, http_status);
        }
        simulation_recovery_retry_index = 0U;
        server_reachable = true;
        Serial.println("[SIMULATION] no active session to recover");
      } else {
        const uint32_t backoff_ms =
            nextBackoff(&simulation_recovery_retry_index);
        next_simulation_recovery_ms = now_ms + backoff_ms;
        portENTER_CRITICAL(&simulation_mux_);
        simulation_recovery_requested_ = true;
        portEXIT_CRITICAL(&simulation_mux_);
        server_reachable = false;
        Serial.printf(
            "[SIMULATION] active-session recovery retry in %lu ms code=%d\n",
            static_cast<unsigned long>(backoff_ms), http_status);
      }
    }

    if (simulation_start_request) {
      SimulationSnapshot started{};
      int http_status = 0;
      if (postSimulationStart(&started, &http_status)) {
        started.http_status = http_status;
        publishSimulation(started);
        portENTER_CRITICAL(&simulation_mux_);
        simulation_recovery_requested_ = false;
        portEXIT_CRITICAL(&simulation_mux_);
        have_pending = false;
        urgent_pending = false;
        last_upload_ms = now_ms;
        server_reachable = true;
        Serial.printf("[SIMULATION] session started id=%s\n",
                      started.session_id);
      } else if (http_status == 409) {
        SimulationSnapshot recovered{};
        int recovery_http_status = 0;
        if (fetchActiveSimulation(&recovered, &recovery_http_status)) {
          recovered.http_status = recovery_http_status;
          publishSimulation(recovered);
          portENTER_CRITICAL(&simulation_mux_);
          simulation_recovery_requested_ = false;
          portEXIT_CRITICAL(&simulation_mux_);
          simulation_recovery_retry_index = 0U;
          server_reachable = true;
          Serial.printf(
              "[SIMULATION] start conflict recovered active id=%s\n",
              recovered.session_id);
        } else {
          setSimulationState(SimulationState::kStartFailed, http_status);
          if (recovery_http_status != 404) {
            next_simulation_recovery_ms =
                now_ms + app_config::kRetryBackoffMs[0];
            portENTER_CRITICAL(&simulation_mux_);
            simulation_recovery_requested_ = true;
            portEXIT_CRITICAL(&simulation_mux_);
          }
          server_reachable = recovery_http_status == 404;
          Serial.printf(
              "[SIMULATION] start conflict recovery failed code=%d\n",
              recovery_http_status);
        }
      } else {
        setSimulationState(SimulationState::kStartFailed, http_status);
        server_reachable = false;
        Serial.printf("[SIMULATION] start failed code=%d\n", http_status);
      }
    }

    if (simulation_stop_request) {
      int http_status = 0;
      if (postSimulationStop(stopping_session_id, &http_status)) {
        setSimulationState(SimulationState::kStopped, http_status);
        have_pending = false;
        urgent_pending = false;
        last_upload_ms = now_ms;
        server_reachable = true;
        Serial.printf("[SIMULATION] session stopped id=%s\n",
                      stopping_session_id);
      } else {
        setSimulationState(SimulationState::kStopFailed, http_status);
        server_reachable = false;
        Serial.printf("[SIMULATION] stop failed id=%s code=%d\n",
                      stopping_session_id, http_status);
      }
    }

    const SimulationSnapshot simulation_status = simulation();
    const uint32_t telemetry_interval_ms =
        simulationSessionOpen(simulation_status.state)
            ? app_config::kSimulationTelemetryUploadIntervalMs
            : app_config::kTelemetryUploadIntervalMs;
    const bool upload_due =
        urgent_pending ||
        static_cast<uint32_t>(now_ms - last_upload_ms) >=
            telemetry_interval_ms;
    const bool retry_due =
        static_cast<int32_t>(now_ms - next_http_attempt_ms) >= 0;

    bool location_selection_queued = false;
    portENTER_CRITICAL(&location_mux_);
    location_selection_queued = location_selection_pending_;
    portEXIT_CRITICAL(&location_mux_);

    if (have_pending && connected && server_configured && !wifi_radio_busy &&
        !location_selection_queued && upload_due && retry_due) {
      int telemetry_http_status = 0;
      const bool posted = postTelemetry(pending, &telemetry_http_status);
      if (simulationSessionOpen(simulation_status.state)) {
        recordSimulationUpload(simulation_status.session_id, pending.seq,
                               posted, telemetry_http_status, millis());
      }
      if (posted) {
        server_reachable = true;
        have_pending = false;
        urgent_pending = false;
        http_retry_index = 0U;
        last_upload_ms = now_ms;
        next_http_attempt_ms = now_ms;
      } else {
        server_reachable = false;
        const uint32_t backoff_ms = nextBackoff(&http_retry_index);
        next_http_attempt_ms = now_ms + backoff_ms;
        Serial.printf("[HTTP] retry scheduled in %lu ms\n",
                      static_cast<unsigned long>(backoff_ms));
      }
    }

    bool catalog_requested = false;
    bool search_requested = false;
    bool selection_requested = false;
    char location_query[kLocationSearchQueryBytes]{};
    char selected_location_id[sizeof(pending_location_id_)]{};
    LocationOption selected_location_option{};
    if (connected && server_configured && !wifi_radio_busy) {
      portENTER_CRITICAL(&location_mux_);
      if (location_search_requested_) {
        location_search_requested_ = false;
        search_requested = true;
        std::memcpy(location_query, pending_location_query_,
                    sizeof(location_query));
        pending_location_query_[0] = '\0';
      } else if (location_catalog_requested_) {
        location_catalog_requested_ = false;
        catalog_requested = true;
      }
      if (location_selection_pending_) {
        location_selection_pending_ = false;
        selection_requested = true;
        std::memcpy(selected_location_id, pending_location_id_,
                    sizeof(selected_location_id));
        selected_location_option = pending_location_option_;
      }
      portEXIT_CRITICAL(&location_mux_);
    }

    if (catalog_requested || search_requested) {
      int http_status = 0;
      Serial.printf("[STACK] network before catalog high_water=%u\n",
                    static_cast<unsigned int>(
                        uxTaskGetStackHighWaterMark(nullptr)));
      std::memset(&location_working_, 0, sizeof(location_working_));
      const bool fetched =
          search_requested
              ? fetchLocationSearch(location_query, &location_working_,
                                    &http_status)
              : fetchLocationCatalog(&location_working_, &http_status);
      if (fetched) {
        location_working_.state = LocationCatalogState::kReady;
        location_working_.http_status = http_status;
        if (search_requested) {
          publishLocationCatalog(location_working_);
        } else {
          cacheAndPublishLocationPresets(location_working_);
        }
        server_reachable = true;
        if (search_requested) {
          Serial.printf("[LOCATION] search query='%s' results=%u\n",
                        location_query,
                        static_cast<unsigned int>(location_working_.count));
        } else {
          Serial.printf("[LOCATION] loaded presets=%u\n",
                        static_cast<unsigned int>(location_working_.count));
        }
      } else {
        server_reachable = false;
        if (search_requested) {
          setLocationCatalogState(LocationCatalogState::kError, http_status);
        } else {
          setLocationPresetError(http_status);
        }
        Serial.printf("[LOCATION] %s GET failed code=%d\n",
                      search_requested ? "search" : "preset", http_status);
      }
      Serial.printf("[STACK] network after catalog high_water=%u\n",
                    static_cast<unsigned int>(
                        uxTaskGetStackHighWaterMark(nullptr)));
    }

    if (selection_requested) {
      int http_status = 0;
      if (putDeviceLocation(selected_location_id, &http_status)) {
        server_reachable = true;
        publishPendingLocationEnvironment(selected_location_option);
        RiskSnapshot pending_risk{};
        resetRiskSnapshot(&pending_risk);
        publishRisk(pending_risk, RiskAvailability::kWaiting, 0);
        setLocationCatalogState(LocationCatalogState::kSaved, http_status);
        environment_retry_index = 0U;
        next_environment_attempt_ms = now_ms;
        next_risk_attempt_ms = now_ms;
        selection_environment_refresh_pending = true;
        Serial.printf("[LOCATION] selected id=%s code=%d\n",
                      selected_location_id, http_status);
      } else {
        server_reachable = false;
        setLocationCatalogState(LocationCatalogState::kError, http_status);
        Serial.printf("[LOCATION] selection failed id=%s code=%d\n",
                      selected_location_id, http_status);
      }
    }

    const bool environment_due =
        static_cast<int32_t>(now_ms - next_environment_attempt_ms) >= 0;
    if (connected && server_configured && server_clock_ready &&
        !wifi_radio_busy && environment_due) {
      EnvironmentSnapshot snapshot{};
      const uint32_t environment_timeout_ms =
          (selection_environment_refresh_pending || !environment_reachable)
              ? app_config::kSelectedEnvironmentReadTimeoutMs
              : app_config::kHttpReadTimeoutMs;
      if (fetchEnvironment(&snapshot, environment_timeout_ms)) {
        snapshot.fetched_at_ms = millis();
        snapshot.fetched_at_unix_time = unixTimeNow();
        publishEnvironment(snapshot);
        server_reachable = true;
        environment_reachable = true;
        environment_retry_index = 0U;
        selection_environment_refresh_pending = false;
        next_environment_attempt_ms =
            now_ms + app_config::kEnvironmentRefreshIntervalMs;
        bool queued_preset_prefetch = false;
        portENTER_CRITICAL(&location_mux_);
        if (location_presets_cache_.count == 0U &&
            !location_catalog_requested_ && !location_search_requested_ &&
            !location_selection_pending_) {
          location_catalog_requested_ = true;
          queued_preset_prefetch = true;
        }
        portEXIT_CRITICAL(&location_mux_);
        if (queued_preset_prefetch) {
          Serial.println("[LOCATION] background preset prefetch queued");
        }
        Serial.printf(
            "[ENV] updated location='%s' weather='%s' source=%s stale=%u\n",
            snapshot.location, snapshot.weather, snapshot.source,
            snapshot.stale ? 1U : 0U);
      } else {
        server_reachable = false;
        environment_reachable = false;
        markEnvironmentStale();
        const uint32_t backoff_ms = nextBackoff(&environment_retry_index);
        next_environment_attempt_ms = now_ms + backoff_ms;
        Serial.printf("[ENV] retry scheduled in %lu ms\n",
                      static_cast<unsigned long>(backoff_ms));
      }
    }

    const bool risk_due =
        static_cast<int32_t>(now_ms - next_risk_attempt_ms) >= 0;
    if (connected && server_configured && server_clock_ready &&
        !wifi_radio_busy && risk_due) {
      RiskSnapshot snapshot{};
      int http_status = 0;
      if (fetchRisk(&snapshot, &http_status)) {
        snapshot.fetched_at_ms = millis();
        snapshot.fetched_at_unix_time = unixTimeNow();
        snapshot.stale = false;
        publishRisk(snapshot, RiskAvailability::kReady, http_status);
        server_reachable = true;
        risk_retry_index = 0U;
        next_risk_attempt_ms = now_ms + app_config::kRiskRefreshIntervalMs;
        Serial.printf(
            "[RISK] updated class=%s confidence=%.3f mode=%s quality=%s\n",
            snapshot.risk_name,
            static_cast<double>(snapshot.environmental_probability),
            snapshot.deployment_mode, snapshot.data_quality);
      } else if (http_status == 404) {
        RiskSnapshot waiting{};
        resetRiskSnapshot(&waiting);
        publishRisk(waiting, RiskAvailability::kNoTelemetry, http_status);
        server_reachable = true;
        risk_retry_index = 0U;
        next_risk_attempt_ms = now_ms + app_config::kRiskNoTelemetryRetryMs;
        Serial.println("[RISK] waiting for first STM32 telemetry");
      } else {
        markRiskStale(RiskAvailability::kUnavailable, http_status);
        const uint32_t backoff_ms = nextBackoff(&risk_retry_index);
        next_risk_attempt_ms = now_ms + backoff_ms;
        Serial.printf("[RISK] retry scheduled in %lu ms code=%d\n",
                      static_cast<unsigned long>(backoff_ms), http_status);
      }
    }

    updateStatus(connected, connected && server_reachable,
                 connected && environment_reachable,
                 connected ? WiFi.RSSI() : app_config::kOfflineRssi,
                 connected ? unixTimeNow() : 0U);
    vTaskDelay(pdMS_TO_TICKS(20U));
  }
}

bool NetworkUplink::fetchEnvironment(EnvironmentSnapshot *snapshot,
                                     uint32_t read_timeout_ms) {
  if (snapshot == nullptr) {
    return false;
  }

  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kEnvironmentPath;
  url += "?device_id=";
  // DEVICE_ID is constrained to ASCII letters, digits, '-' and '_'.
  url += app_config::kDeviceId;

  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(read_timeout_ms);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    Serial.println("[ENV] ERROR failed to initialize client");
    return false;
  }
  addDeviceTokenHeader(&http);

  const int response_code = http.GET();
  if (response_code < 200 || response_code >= 300) {
    http.end();
    Serial.printf("[ENV] GET failed code=%d\n", response_code);
    return false;
  }

  const int content_length = http.getSize();
  if (content_length > static_cast<int>(kEnvironmentMaxJsonBytes)) {
    http.end();
    Serial.printf("[ENV] response too large bytes=%d\n", content_length);
    return false;
  }

  const String payload = http.getString();
  http.end();
  if (payload.length() > kEnvironmentMaxJsonBytes) {
    Serial.printf("[ENV] response too large bytes=%u\n",
                  static_cast<unsigned int>(payload.length()));
    return false;
  }

  EnvironmentSnapshot parsed{};
  const EnvironmentParseResult result = parseEnvironmentJson(
      payload.c_str(), static_cast<size_t>(payload.length()), &parsed);
  if (result != EnvironmentParseResult::kOk) {
    Serial.printf("[ENV] invalid response reason=%s\n",
                  environmentParseResultName(result));
    return false;
  }

  *snapshot = parsed;
  return true;
}

bool NetworkUplink::fetchRisk(RiskSnapshot *snapshot, int *http_status) {
  if (snapshot == nullptr || http_status == nullptr) {
    return false;
  }
  *http_status = 0;

  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kRiskPath;
  url += "?device_id=";
  url += app_config::kDeviceId;

  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(app_config::kHttpReadTimeoutMs);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    Serial.println("[RISK] ERROR failed to initialize client");
    return false;
  }
  addDeviceTokenHeader(&http);

  const int response_code = http.GET();
  *http_status = response_code;
  if (response_code < 200 || response_code >= 300) {
    http.end();
    if (response_code != 404) {
      Serial.printf("[RISK] GET failed code=%d\n", response_code);
    }
    return false;
  }

  const int content_length = http.getSize();
  if (content_length > static_cast<int>(kRiskMaxJsonBytes)) {
    http.end();
    Serial.printf("[RISK] response too large bytes=%d\n", content_length);
    return false;
  }

  const String payload = http.getString();
  http.end();
  if (payload.length() > kRiskMaxJsonBytes) {
    Serial.printf("[RISK] response too large bytes=%u\n",
                  static_cast<unsigned int>(payload.length()));
    return false;
  }

  RiskSnapshot parsed{};
  const RiskParseResult result = parseRiskJson(
      payload.c_str(), static_cast<size_t>(payload.length()), &parsed);
  if (result != RiskParseResult::kOk) {
    Serial.printf("[RISK] invalid response reason=%s\n",
                  riskParseResultName(result));
    return false;
  }

  *snapshot = parsed;
  return true;
}

bool NetworkUplink::fetchModelCatalog(ModelCatalog *catalog,
                                      int *http_status) {
  if (catalog == nullptr || http_status == nullptr) {
    return false;
  }
  *http_status = 0;
  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kModelsPath;
  url += "?device_id=";
  url += app_config::kDeviceId;

  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(app_config::kHttpReadTimeoutMs);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    return false;
  }
  addDeviceTokenHeader(&http);
  const int response_code = http.GET();
  *http_status = response_code;
  if (response_code < 200 || response_code >= 300) {
    http.end();
    return false;
  }
  const int content_length = http.getSize();
  if (content_length > static_cast<int>(kModelCatalogMaxJsonBytes)) {
    http.end();
    return false;
  }
  const String payload = http.getString();
  http.end();
  if (payload.length() > kModelCatalogMaxJsonBytes) {
    return false;
  }
  ModelCatalog parsed{};
  const ModelParseResult result = parseModelCatalogJson(
      payload.c_str(), static_cast<size_t>(payload.length()), &parsed);
  if (result != ModelParseResult::kOk) {
    Serial.printf("[MODEL] invalid catalog reason=%s\n",
                  modelParseResultName(result));
    return false;
  }
  *catalog = parsed;
  return true;
}

bool NetworkUplink::putDeviceModel(const char *model_id, int *http_status) {
  if (model_id == nullptr || model_id[0] == '\0' || http_status == nullptr) {
    return false;
  }
  *http_status = 0;
  char json[160]{};
  const int length = std::snprintf(
      json, sizeof(json),
      "{\"device_id\":\"%s\",\"model_id\":\"%s\"}",
      app_config::kDeviceId, model_id);
  if (length <= 0 || static_cast<size_t>(length) >= sizeof(json)) {
    return false;
  }

  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kDeviceModelPath;
  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(app_config::kHttpReadTimeoutMs);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  addDeviceTokenHeader(&http);
  const int response_code = http.sendRequest(
      "PUT", reinterpret_cast<uint8_t *>(json), static_cast<size_t>(length));
  *http_status = response_code;
  http.end();
  return response_code >= 200 && response_code < 300;
}

bool NetworkUplink::postSimulationStart(SimulationSnapshot *snapshot,
                                        int *http_status) {
  if (snapshot == nullptr || http_status == nullptr) {
    return false;
  }
  *http_status = 0;
  char json[160]{};
  const int length = std::snprintf(
      json, sizeof(json),
      "{\"device_id\":\"%s\",\"name\":\"ESP32 WATER SIMULATION\"}",
      app_config::kDeviceId);
  if (length <= 0 || static_cast<size_t>(length) >= sizeof(json)) {
    return false;
  }
  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kSimulationSessionsPath;
  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(app_config::kHttpReadTimeoutMs);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  addDeviceTokenHeader(&http);
  const int response_code =
      http.POST(reinterpret_cast<uint8_t *>(json), static_cast<size_t>(length));
  *http_status = response_code;
  if (response_code < 200 || response_code >= 300) {
    http.end();
    return false;
  }
  const int content_length = http.getSize();
  if (content_length > static_cast<int>(kSimulationResponseMaxJsonBytes)) {
    http.end();
    return false;
  }
  const String payload = http.getString();
  http.end();
  if (payload.length() > kSimulationResponseMaxJsonBytes) {
    return false;
  }
  SimulationSnapshot parsed{};
  const SimulationParseResult result = parseSimulationStartJson(
      payload.c_str(), static_cast<size_t>(payload.length()), &parsed);
  if (result != SimulationParseResult::kOk) {
    Serial.printf("[SIMULATION] invalid start response reason=%s\n",
                  simulationParseResultName(result));
    return false;
  }
  *snapshot = parsed;
  return true;
}

bool NetworkUplink::fetchActiveSimulation(SimulationSnapshot *snapshot,
                                          int *http_status) {
  if (snapshot == nullptr || http_status == nullptr) {
    return false;
  }
  *http_status = 0;
  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kActiveSimulationSessionPath;
  url += "?device_id=";
  url += app_config::kDeviceId;
  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(app_config::kHttpReadTimeoutMs);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    return false;
  }
  addDeviceTokenHeader(&http);
  const int response_code = http.GET();
  *http_status = response_code;
  if (response_code < 200 || response_code >= 300) {
    http.end();
    return false;
  }
  const int content_length = http.getSize();
  if (content_length > static_cast<int>(kSimulationResponseMaxJsonBytes)) {
    http.end();
    return false;
  }
  const String payload = http.getString();
  http.end();
  if (payload.length() > kSimulationResponseMaxJsonBytes) {
    return false;
  }
  SimulationSnapshot parsed{};
  const SimulationParseResult result = parseSimulationStartJson(
      payload.c_str(), static_cast<size_t>(payload.length()), &parsed);
  if (result != SimulationParseResult::kOk) {
    Serial.printf("[SIMULATION] invalid active response reason=%s\n",
                  simulationParseResultName(result));
    return false;
  }
  *snapshot = parsed;
  return true;
}

bool NetworkUplink::postSimulationStop(const char *session_id,
                                       int *http_status) {
  if (session_id == nullptr || session_id[0] == '\0' ||
      http_status == nullptr) {
    return false;
  }
  *http_status = 0;
  char json[96]{};
  const int length = std::snprintf(
      json, sizeof(json), "{\"device_id\":\"%s\"}",
      app_config::kDeviceId);
  if (length <= 0 || static_cast<size_t>(length) >= sizeof(json)) {
    return false;
  }
  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kSimulationSessionsPath;
  url += "/";
  url += session_id;
  url += "/stop";
  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(app_config::kHttpReadTimeoutMs);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  addDeviceTokenHeader(&http);
  const int response_code = http.POST(
      reinterpret_cast<uint8_t *>(json), static_cast<size_t>(length));
  *http_status = response_code;
  http.end();
  return response_code >= 200 && response_code < 300;
}

bool NetworkUplink::fetchLocationCatalog(LocationCatalog *catalog,
                                         int *http_status) {
  if (catalog == nullptr || http_status == nullptr) {
    return false;
  }
  *http_status = -1;

  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kLocationPresetsPath;

  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(app_config::kHttpReadTimeoutMs);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    return false;
  }
  addDeviceTokenHeader(&http);

  const int response_code = http.GET();
  *http_status = response_code;
  if (response_code < 200 || response_code >= 300) {
    http.end();
    return false;
  }

  const int content_length = http.getSize();
  if (content_length > static_cast<int>(kLocationCatalogMaxJsonBytes)) {
    http.end();
    return false;
  }
  const String payload = http.getString();
  http.end();
  if (payload.length() > kLocationCatalogMaxJsonBytes) {
    return false;
  }

  if (!parseLocationCatalogJson(payload.c_str(), payload.length(), catalog)) {
    return false;
  }
  catalog->http_status = response_code;
  return true;
}

bool NetworkUplink::fetchLocationSearch(const char *query,
                                        LocationCatalog *catalog,
                                        int *http_status) {
  if (!validLocationSearchQuery(query) || catalog == nullptr ||
      http_status == nullptr) {
    return false;
  }
  *http_status = -1;

  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kLocationSearchPath;
  url += "?q=";
  url += percentEncodeQuery(query);
  url += "&count=8";

  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(app_config::kLocationSearchReadTimeoutMs);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    return false;
  }
  addDeviceTokenHeader(&http);

  const int response_code = http.GET();
  *http_status = response_code;
  if (response_code < 200 || response_code >= 300) {
    http.end();
    return false;
  }

  const int content_length = http.getSize();
  if (content_length > static_cast<int>(kLocationCatalogMaxJsonBytes)) {
    http.end();
    return false;
  }
  const String payload = http.getString();
  http.end();
  if (payload.length() > kLocationCatalogMaxJsonBytes ||
      !parseLocationCatalogJson(payload.c_str(), payload.length(), catalog)) {
    return false;
  }
  catalog->http_status = response_code;
  return true;
}

bool NetworkUplink::putDeviceLocation(const char *location_id,
                                      int *http_status) {
  if (!validLocationId(location_id) || http_status == nullptr) {
    return false;
  }
  *http_status = -1;

  char json[128]{};
  const int length = std::snprintf(
      json, sizeof(json),
      "{\"device_id\":\"%s\",\"location_id\":\"%s\"}",
      app_config::kDeviceId, location_id);
  if (length <= 0 || static_cast<size_t>(length) >= sizeof(json)) {
    return false;
  }

  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kDeviceLocationPath;

  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(app_config::kLocationSelectionReadTimeoutMs);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  addDeviceTokenHeader(&http);
  const int response_code = http.sendRequest(
      "PUT", reinterpret_cast<uint8_t *>(json), static_cast<size_t>(length));
  *http_status = response_code;
  http.end();
  return response_code >= 200 && response_code < 300;
}

bool NetworkUplink::postTelemetry(const TelemetryFrame &telemetry,
                                  int *http_status) {
  if (http_status != nullptr) {
    *http_status = 0;
  }
  const SimulationSnapshot collection = simulation();
  const bool attach_session = simulationSessionOpen(collection.state) &&
                              collection.session_id[0] != '\0';
  char json[512]{};
  const char *format =
      attach_session
          ? "{\"device_id\":\"%s\",\"seq\":%lu,\"uptime_ms\":%lu,"
            "\"distance_mm\":%lu,\"water_rise_mm\":%ld,"
            "\"rise_rate_mm_s\":%ld,\"person_detected\":%s,"
            "\"alarm_level\":%u,\"health_flags\":%lu,\"wifi_rssi\":%ld,"
            "\"simulation_session_id\":\"%s\"}"
          : "{\"device_id\":\"%s\",\"seq\":%lu,\"uptime_ms\":%lu,"
            "\"distance_mm\":%lu,\"water_rise_mm\":%ld,"
            "\"rise_rate_mm_s\":%ld,\"person_detected\":%s,"
            "\"alarm_level\":%u,\"health_flags\":%lu,\"wifi_rssi\":%ld}";
  const int length = attach_session
                         ? snprintf(
                               json, sizeof(json), format,
                               app_config::kDeviceId,
                               static_cast<unsigned long>(telemetry.seq),
                               static_cast<unsigned long>(telemetry.uptime_ms),
                               static_cast<unsigned long>(telemetry.distance_mm),
                               static_cast<long>(telemetry.water_rise_mm),
                               static_cast<long>(telemetry.rise_rate_mm_s),
                               telemetry.person_detected ? "true" : "false",
                               static_cast<unsigned>(telemetry.alarm_level),
                               static_cast<unsigned long>(telemetry.health_flags),
                               static_cast<long>(WiFi.RSSI()),
                               collection.session_id)
                         : snprintf(
                               json, sizeof(json), format,
                               app_config::kDeviceId,
                               static_cast<unsigned long>(telemetry.seq),
                               static_cast<unsigned long>(telemetry.uptime_ms),
                               static_cast<unsigned long>(telemetry.distance_mm),
                               static_cast<long>(telemetry.water_rise_mm),
                               static_cast<long>(telemetry.rise_rate_mm_s),
                               telemetry.person_detected ? "true" : "false",
                               static_cast<unsigned>(telemetry.alarm_level),
                               static_cast<unsigned long>(telemetry.health_flags),
                               static_cast<long>(WiFi.RSSI()));
  if (length <= 0 || static_cast<size_t>(length) >= sizeof(json)) {
    Serial.println("[HTTP] ERROR telemetry JSON buffer too small");
    return false;
  }

  String url(app_config::kServerBaseUrl);
  while (url.endsWith("/")) {
    url.remove(url.length() - 1U);
  }
  url += app_config::kTelemetryPath;

  WiFiClient plain_client;
  WiFiClientSecure secure_client;
  HTTPClient http;
  http.setConnectTimeout(app_config::kHttpConnectTimeoutMs);
  http.setTimeout(app_config::kHttpReadTimeoutMs);
  if (!beginHttpRequest(&http, &plain_client, &secure_client, url)) {
    Serial.println("[HTTP] ERROR failed to initialize client");
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  addDeviceTokenHeader(&http);
  const int response_code =
      http.POST(reinterpret_cast<uint8_t *>(json), static_cast<size_t>(length));
  if (http_status != nullptr) {
    *http_status = response_code;
  }
  http.end();

  if (response_code >= 200 && response_code < 300) {
    Serial.printf("[HTTP] POST seq=%lu -> %d\n",
                  static_cast<unsigned long>(telemetry.seq), response_code);
    return true;
  }
  Serial.printf("[HTTP] POST seq=%lu failed code=%d\n",
                static_cast<unsigned long>(telemetry.seq), response_code);
  return false;
}
