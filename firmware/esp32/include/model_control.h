#pragma once

#include <stddef.h>
#include <stdint.h>

#include "telemetry.h"

constexpr size_t kModelCatalogCapacity = 3U;
// Capacities mirror the server schema maxima plus the NUL terminator.
constexpr size_t kModelIdBytes = 49U;
constexpr size_t kModelDisplayNameBytes = 49U;
constexpr size_t kModelModeBytes = 33U;
constexpr size_t kModelDescriptionBytes = 121U;
constexpr size_t kSimulationSessionIdBytes = 49U;
constexpr size_t kSimulationStartedAtBytes = 40U;
constexpr size_t kModelCatalogMaxJsonBytes = 4096U;
constexpr size_t kSimulationResponseMaxJsonBytes = 512U;

enum class ModelStatus : uint8_t {
  kReady = 0,
  kUnavailable,
  kNotTrained,
};

enum class ModelCatalogState : uint8_t {
  kIdle = 0,
  kLoading,
  kReady,
  kSelecting,
  kError,
};

struct ModelOption {
  char model_id[kModelIdBytes];
  char display_name[kModelDisplayNameBytes];
  ModelStatus status;
  char mode[kModelModeBytes];
  char description[kModelDescriptionBytes];
};

struct ModelCatalog {
  ModelOption models[kModelCatalogCapacity];
  size_t count;
  char selected_model_id[kModelIdBytes];
  char pending_model_id[kModelIdBytes];
  ModelCatalogState state;
  int http_status;
  uint32_t revision;
};

enum class ModelParseResult : uint8_t {
  kOk = 0,
  kNullArgument,
  kPayloadTooLarge,
  kInvalidJson,
  kInvalidSchema,
  kInvalidModel,
  kTooManyModels,
  kDuplicateModel,
  kUnknownSelection,
};

enum class SimulationState : uint8_t {
  kIdle = 0,
  kStarting,
  kActive,
  kStopping,
  kStopped,
  kStartFailed,
  kStopFailed,
};

struct SimulationSnapshot {
  SimulationState state;
  char session_id[kSimulationSessionIdBytes];
  char started_at[kSimulationStartedAtBytes];
  TelemetryFrame latest;
  // Exact sample count reported by the server when the session was opened or
  // recovered.  Telemetry POST acknowledgements deliberately do not mutate
  // this value because a 2xx response does not carry a refreshed session
  // count (and may acknowledge an idempotent retry).
  uint32_t server_stored_sample_count;
  // TEL frames observed by this ESP32 runtime after the session snapshot was
  // published.  These are local capture counters, not database counters.
  uint32_t local_tel_sample_count;
  uint32_t local_valid_ultrasonic_sample_count;
  uint32_t last_sample_ms;
  uint32_t upload_ack_success_count;
  uint32_t upload_ack_failure_count;
  uint32_t last_upload_ack_ms;
  uint32_t last_upload_ack_seq;
  bool has_telemetry;
  bool last_upload_ack_succeeded;
  bool has_upload_ack;
  int http_status;
  int last_upload_ack_http_status;
  uint32_t revision;
};

enum class SimulationUltrasonicQuality : uint8_t {
  kWaiting = 0,
  kValid,
  kNoEcho,
  kStale,
};

enum class CollectionButtonAction : uint8_t {
  kNone = 0,
  kStart,
  kArmStopConfirmation,
  kStop,
};

enum class SimulationParseResult : uint8_t {
  kOk = 0,
  kNullArgument,
  kPayloadTooLarge,
  kInvalidJson,
  kInvalidSchema,
};

ModelParseResult parseModelCatalogJson(const char *json, size_t length,
                                       ModelCatalog *catalog);
SimulationParseResult parseSimulationStartJson(
    const char *json, size_t length, SimulationSnapshot *snapshot);

const char *modelStatusName(ModelStatus status);
const char *modelParseResultName(ModelParseResult result);
const char *simulationStateName(SimulationState state);
const char *simulationParseResultName(SimulationParseResult result);

bool modelStatusSelectable(ModelStatus status);
bool simulationSessionOpen(SimulationState state);
bool simulationCanStart(SimulationState state);
bool simulationCanStop(SimulationState state);
bool simulationTelemetryIsValid(const TelemetryFrame &telemetry);
bool simulationRecordLocalTelemetry(SimulationSnapshot *snapshot,
                                    const TelemetryFrame &telemetry,
                                    uint32_t received_at_ms);
bool simulationRecordUploadAck(SimulationSnapshot *snapshot,
                               const char *session_id, uint32_t seq,
                               bool succeeded, int http_status,
                               uint32_t attempted_at_ms);
SimulationUltrasonicQuality simulationUltrasonicQuality(
    const SimulationSnapshot &snapshot, uint32_t now_ms,
    uint32_t maximum_age_ms);
const char *simulationUltrasonicQualityName(
    SimulationUltrasonicQuality quality);
CollectionButtonAction collectionButtonAction(
    SimulationState state, bool stop_confirmation_pending);

const ModelOption *findModel(const ModelCatalog &catalog,
                             const char *model_id);

namespace model_ui {

struct Rect {
  int16_t x;
  int16_t y;
  int16_t width;
  int16_t height;

  constexpr bool contains(int16_t point_x, int16_t point_y) const {
    return point_x >= x && point_y >= y && point_x < x + width &&
           point_y < y + height;
  }
};

constexpr Rect kBackButton{28, 15, 110, 38};
constexpr Rect kCollectionButton{602, 15, 170, 38};
constexpr Rect kSessionButton{602, 15, 170, 38};
constexpr size_t kCardCount = 3U;

constexpr Rect cardRect(size_t index) {
  return {static_cast<int16_t>(28 + static_cast<int>(index) * 252), 96,
          236, 286};
}

}  // namespace model_ui
