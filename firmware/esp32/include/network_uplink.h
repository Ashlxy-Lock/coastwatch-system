#pragma once

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

#include "environment.h"
#include "location_selection.h"
#include "model_control.h"
#include "risk_snapshot.h"
#include "telemetry.h"
#include "wifi_setup.h"

struct NetworkStatus {
  bool wifi_connected;
  bool server_reachable;
  bool environment_reachable;
  int32_t rssi;
  uint32_t unix_time;
};

enum class RiskAvailability : uint8_t {
  kWaiting = 0,
  kReady,
  kNoTelemetry,
  kUnavailable,
};

struct RiskFetchStatus {
  RiskAvailability availability;
  int http_status;
};

class NetworkUplink {
 public:
  bool begin();
  bool submit(const TelemetryFrame &telemetry);
  NetworkStatus status() const;
  EnvironmentSnapshot environment() const;
  RiskSnapshot risk() const;
  RiskFetchStatus riskStatus() const;
  void copyModelCatalog(ModelCatalog *catalog) const;
  void requestModelCatalog();
  bool selectModel(const char *model_id);
  SimulationSnapshot simulation() const;
  bool requestSimulationStart();
  bool requestSimulationStop();
  void copyLocationCatalog(LocationCatalog *catalog) const;
  void requestLocationCatalog();
  bool requestLocationSearch(const char *query);
  bool selectLocation(size_t index);
  void copyWifiCatalog(WifiCatalog *catalog) const;
  void requestWifiScan();
  bool requestWifiConnect(const char *ssid, const char *password);
  bool requestWifiForget();
  void dismissWifiForgetError();
  void endWifiSetup();

 private:
  static void taskEntry(void *context);
  void taskLoop();
  void updateStatus(bool wifi_connected, bool server_reachable,
                    bool environment_reachable, int32_t rssi,
                    uint32_t unix_time);
  bool postTelemetry(const TelemetryFrame &telemetry, int *http_status);
  bool fetchEnvironment(EnvironmentSnapshot *snapshot,
                        uint32_t read_timeout_ms);
  bool fetchRisk(RiskSnapshot *snapshot, int *http_status);
  bool fetchModelCatalog(ModelCatalog *catalog, int *http_status);
  bool putDeviceModel(const char *model_id, int *http_status);
  bool postSimulationStart(SimulationSnapshot *snapshot, int *http_status);
  bool fetchActiveSimulation(SimulationSnapshot *snapshot, int *http_status);
  bool postSimulationStop(const char *session_id, int *http_status);
  bool fetchLocationCatalog(LocationCatalog *catalog, int *http_status);
  bool fetchLocationSearch(const char *query, LocationCatalog *catalog,
                           int *http_status);
  bool putDeviceLocation(const char *location_id, int *http_status);
  void publishEnvironment(const EnvironmentSnapshot &snapshot);
  void publishRisk(const RiskSnapshot &snapshot, RiskAvailability availability,
                   int http_status);
  void publishModelCatalog(const ModelCatalog &catalog);
  void setModelCatalogState(ModelCatalogState state, int http_status);
  void setModelSelectionFailed(const char *model_id, int http_status);
  void publishSimulation(const SimulationSnapshot &snapshot);
  void setSimulationState(SimulationState state, int http_status);
  void recordSimulationUpload(const char *session_id, uint32_t seq,
                              bool succeeded, int http_status,
                              uint32_t attempted_at_ms);
  void publishPendingLocationEnvironment(const LocationOption &option);
  void markEnvironmentStale();
  void markRiskStale(RiskAvailability availability, int http_status);
  void publishLocationCatalog(const LocationCatalog &catalog);
  void cacheAndPublishLocationPresets(const LocationCatalog &catalog);
  void setLocationPresetError(int http_status);
  void setLocationCatalogState(LocationCatalogState state, int http_status);
  void publishWifiCatalog(const WifiCatalog &catalog);
  void setWifiSetupState(WifiSetupState state, WifiSetupError error);

  QueueHandle_t queue_{nullptr};
  TaskHandle_t task_{nullptr};
  mutable portMUX_TYPE status_mux_ = portMUX_INITIALIZER_UNLOCKED;
  NetworkStatus status_{false, false, false, -127, 0U};
  mutable portMUX_TYPE environment_mux_ = portMUX_INITIALIZER_UNLOCKED;
  EnvironmentSnapshot environment_{};
  mutable portMUX_TYPE risk_mux_ = portMUX_INITIALIZER_UNLOCKED;
  RiskSnapshot risk_{};
  RiskFetchStatus risk_status_{RiskAvailability::kWaiting, 0};
  mutable portMUX_TYPE model_mux_ = portMUX_INITIALIZER_UNLOCKED;
  ModelCatalog model_catalog_{};
  bool model_catalog_requested_{false};
  bool model_selection_requested_{false};
  char pending_model_id_[kModelIdBytes]{};
  mutable portMUX_TYPE simulation_mux_ = portMUX_INITIALIZER_UNLOCKED;
  SimulationSnapshot simulation_{};
  bool simulation_start_requested_{false};
  bool simulation_stop_requested_{false};
  bool simulation_recovery_requested_{false};
  mutable portMUX_TYPE location_mux_ = portMUX_INITIALIZER_UNLOCKED;
  LocationCatalog location_catalog_{};
  // Search results are transient. Keep the verified coast presets separately
  // so reopening the picker never waits for another HTTPS request.
  LocationCatalog location_presets_cache_{};
  // Network-only scratch space. Keeping this out of taskLoop() prevents the
  // full catalogue from consuming TLS handshake stack.
  LocationCatalog location_working_{};
  bool location_catalog_requested_{false};
  bool location_search_requested_{false};
  bool location_catalog_showing_search_{false};
  char pending_location_query_[kLocationSearchQueryBytes]{};
  bool location_selection_pending_{false};
  char pending_location_id_[24]{};
  LocationOption pending_location_option_{};
  mutable portMUX_TYPE wifi_mux_ = portMUX_INITIALIZER_UNLOCKED;
  WifiCatalog wifi_catalog_{};
  bool wifi_setup_active_{false};
  bool wifi_scan_requested_{false};
  bool wifi_scan_cancel_requested_{false};
  bool wifi_connect_requested_{false};
  bool wifi_forget_requested_{false};
  char pending_wifi_ssid_[kWifiSsidBytes]{};
  char pending_wifi_password_[kWifiPasswordBytes]{};
  uint8_t pending_wifi_auth_mode_{0U};
  bool pending_wifi_secured_{true};
};
