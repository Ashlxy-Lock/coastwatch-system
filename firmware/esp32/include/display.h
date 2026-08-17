#pragma once

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include "environment.h"
#include "esp_lcd_panel_io.h"
#include "location_selection.h"
#include "model_control.h"
#include "risk_snapshot.h"
#include "telemetry.h"
#include "wifi_setup.h"

class CoastalDisplay {
 public:
  bool begin();
  bool showNetworkStatus(bool wifi_connected, bool server_reachable,
                         bool environment_reachable);
  bool showEnvironment(const EnvironmentSnapshot &snapshot);
  bool showRiskOverview(const RiskSnapshot &risk,
                        const EnvironmentSnapshot &environment,
                        const ModelCatalog &models,
                        const TelemetrySnapshot &telemetry,
                        uint8_t availability, int http_status);
  bool showModelCatalog(const ModelCatalog &catalog);
  bool showSimulationCollection(const SimulationSnapshot &simulation,
                                const ModelCatalog &models,
                                bool stop_confirmation_pending);
  bool animateEnvironmentLoading();
  bool showLocationPicker(const LocationCatalog &catalog, size_t page,
                          int selected_index);
  bool showLocationSearch(const char *query, size_t query_length,
                          WifiKeyboardMode mode);
  bool showWifiPicker(const WifiCatalog &catalog, size_t page);
  bool showWifiForgetConfirm(const char *ssid, WifiSetupState state,
                             WifiSetupError error);
  bool showWifiPassword(const WifiNetworkOption &network,
                        const char *password, size_t password_length,
                        WifiKeyboardMode mode, WifiSetupState state,
                        WifiSetupError error, const char *key_feedback);
  bool updateWifiKeyFeedback(const char *key_feedback);
  bool ready() const { return ready_; }

 private:
  static bool IRAM_ATTR onTransferDone(
      esp_lcd_panel_io_handle_t panel_io,
      esp_lcd_panel_io_event_data_t *event_data, void *user_context);

  bool initializeController();
  bool writeRegister(uint16_t command, uint16_t value);
  bool writeCommand(uint16_t command);
  bool setWindow(uint16_t x_start, uint16_t y_start, uint16_t x_end,
                 uint16_t y_end);
  bool flushFramebuffer();
  bool flushRegion(int x, int y, int width, int height);

  esp_lcd_i80_bus_handle_t bus_{nullptr};
  esp_lcd_panel_io_handle_t io_{nullptr};
  SemaphoreHandle_t transfer_done_{nullptr};
  uint16_t *framebuffer_{nullptr};
  uint16_t *partial_transfer_buffer_{nullptr};
  size_t loading_spinner_phase_{0U};
  bool ready_{false};
};
