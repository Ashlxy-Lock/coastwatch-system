#include "model_control.h"

#include <ArduinoJson.h>

#include <cstring>
#include <type_traits>

static_assert(std::is_trivially_copyable<ModelCatalog>::value,
              "model catalogue must remain a fixed-size POD snapshot");
static_assert(std::is_trivially_copyable<SimulationSnapshot>::value,
              "simulation state must remain a fixed-size POD snapshot");

namespace {

bool hasText(const char *value) { return value != nullptr && value[0] != '\0'; }

bool validModelIdentifier(const char *value, size_t capacity) {
  if (!hasText(value)) {
    return false;
  }
  size_t length = 0U;
  for (; value[length] != '\0'; ++length) {
    if (length + 1U >= capacity) {
      return false;
    }
    const char character = value[length];
    if (!((character >= 'a' && character <= 'z') ||
          (character >= '0' && character <= '9') || character == '-' ||
          character == '_')) {
      return false;
    }
  }
  return length > 0U;
}

bool validSimulationSessionId(const char *value, size_t capacity) {
  if (value == nullptr || std::strncmp(value, "sim_", 4U) != 0) {
    return false;
  }
  size_t length = 0U;
  for (; value[length] != '\0'; ++length) {
    if (length + 1U >= capacity) {
      return false;
    }
    const char character = value[length];
    if (!((character >= 'a' && character <= 'z') ||
          (character >= 'A' && character <= 'Z') ||
          (character >= '0' && character <= '9') || character == '-' ||
          character == '_')) {
      return false;
    }
  }
  return length >= 8U;
}

template <size_t Capacity>
bool copyRequiredText(JsonObjectConst object, const char *key,
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

bool parseModelStatus(const char *value, ModelStatus *status) {
  if (value == nullptr || status == nullptr) {
    return false;
  }
  if (std::strcmp(value, "ready") == 0) {
    *status = ModelStatus::kReady;
    return true;
  }
  if (std::strcmp(value, "unavailable") == 0) {
    *status = ModelStatus::kUnavailable;
    return true;
  }
  if (std::strcmp(value, "not_trained") == 0) {
    *status = ModelStatus::kNotTrained;
    return true;
  }
  return false;
}

}  // namespace

ModelParseResult parseModelCatalogJson(const char *json, size_t length,
                                       ModelCatalog *catalog) {
  if (json == nullptr || catalog == nullptr) {
    return ModelParseResult::kNullArgument;
  }
  if (length > kModelCatalogMaxJsonBytes) {
    return ModelParseResult::kPayloadTooLarge;
  }

  JsonDocument document;
  const DeserializationError error = deserializeJson(document, json, length);
  if (error || !document.is<JsonObject>()) {
    return ModelParseResult::kInvalidJson;
  }
  const JsonObjectConst root = document.as<JsonObjectConst>();
  const JsonVariantConst selected = root["selected_model_id"];
  const JsonVariantConst models_value = root["models"];
  if (!selected.is<const char *>() || !models_value.is<JsonArrayConst>()) {
    return ModelParseResult::kInvalidSchema;
  }
  const char *selected_id = selected.as<const char *>();
  if (!validModelIdentifier(selected_id, kModelIdBytes)) {
    return ModelParseResult::kInvalidSchema;
  }
  const JsonArrayConst rows = models_value.as<JsonArrayConst>();
  if (rows.size() == 0U) {
    return ModelParseResult::kInvalidSchema;
  }
  if (rows.size() > kModelCatalogCapacity) {
    return ModelParseResult::kTooManyModels;
  }

  ModelCatalog parsed{};
  std::memcpy(parsed.selected_model_id, selected_id,
              std::strlen(selected_id) + 1U);
  for (JsonObjectConst row : rows) {
    ModelOption option{};
    char status_text[20]{};
    if (!copyRequiredText(row, "model_id", option.model_id) ||
        !validModelIdentifier(option.model_id, sizeof(option.model_id)) ||
        !copyRequiredText(row, "display_name", option.display_name) ||
        !copyRequiredText(row, "status", status_text) ||
        !parseModelStatus(status_text, &option.status) ||
        !copyRequiredText(row, "mode", option.mode) ||
        !copyRequiredText(row, "description", option.description)) {
      return ModelParseResult::kInvalidModel;
    }
    for (size_t index = 0U; index < parsed.count; ++index) {
      if (std::strcmp(parsed.models[index].model_id, option.model_id) == 0) {
        return ModelParseResult::kDuplicateModel;
      }
    }
    parsed.models[parsed.count++] = option;
  }
  if (findModel(parsed, parsed.selected_model_id) == nullptr) {
    return ModelParseResult::kUnknownSelection;
  }
  parsed.state = ModelCatalogState::kReady;
  *catalog = parsed;
  return ModelParseResult::kOk;
}

SimulationParseResult parseSimulationStartJson(
    const char *json, size_t length, SimulationSnapshot *snapshot) {
  if (json == nullptr || snapshot == nullptr) {
    return SimulationParseResult::kNullArgument;
  }
  if (length > kSimulationResponseMaxJsonBytes) {
    return SimulationParseResult::kPayloadTooLarge;
  }
  JsonDocument document;
  const DeserializationError error = deserializeJson(document, json, length);
  if (error || !document.is<JsonObject>()) {
    return SimulationParseResult::kInvalidJson;
  }
  const JsonObjectConst root = document.as<JsonObjectConst>();
  char session_id[kSimulationSessionIdBytes]{};
  char state_text[24]{};
  char started_at[kSimulationStartedAtBytes]{};
  if (!copyRequiredText(root, "session_id", session_id) ||
      !validSimulationSessionId(session_id, sizeof(session_id)) ||
      !copyRequiredText(root, "state", state_text) ||
      std::strcmp(state_text, "active") != 0 ||
      !copyRequiredText(root, "started_at", started_at)) {
    return SimulationParseResult::kInvalidSchema;
  }

  SimulationSnapshot parsed{};
  parsed.state = SimulationState::kActive;
  std::memcpy(parsed.session_id, session_id, std::strlen(session_id) + 1U);
  std::memcpy(parsed.started_at, started_at, std::strlen(started_at) + 1U);
  const JsonVariantConst sample_count = root["sample_count"];
  if (!sample_count.is<uint32_t>()) {
    return SimulationParseResult::kInvalidSchema;
  }
  parsed.server_stored_sample_count = sample_count.as<uint32_t>();
  *snapshot = parsed;
  return SimulationParseResult::kOk;
}

const char *modelStatusName(ModelStatus status) {
  switch (status) {
    case ModelStatus::kReady:
      return "READY";
    case ModelStatus::kUnavailable:
      return "UNAVAILABLE";
    case ModelStatus::kNotTrained:
      return "NOT TRAINED";
    default:
      return "UNKNOWN";
  }
}

const char *modelParseResultName(ModelParseResult result) {
  switch (result) {
    case ModelParseResult::kOk:
      return "ok";
    case ModelParseResult::kNullArgument:
      return "null-argument";
    case ModelParseResult::kPayloadTooLarge:
      return "payload-too-large";
    case ModelParseResult::kInvalidJson:
      return "invalid-json";
    case ModelParseResult::kInvalidSchema:
      return "invalid-schema";
    case ModelParseResult::kInvalidModel:
      return "invalid-model";
    case ModelParseResult::kTooManyModels:
      return "too-many-models";
    case ModelParseResult::kDuplicateModel:
      return "duplicate-model";
    case ModelParseResult::kUnknownSelection:
      return "unknown-selection";
    default:
      return "unknown";
  }
}

const char *simulationStateName(SimulationState state) {
  switch (state) {
    case SimulationState::kIdle:
      return "IDLE";
    case SimulationState::kStarting:
      return "STARTING";
    case SimulationState::kActive:
      return "COLLECTING";
    case SimulationState::kStopping:
      return "STOPPING";
    case SimulationState::kStopped:
      return "STOPPED";
    case SimulationState::kStartFailed:
      return "START FAILED";
    case SimulationState::kStopFailed:
      return "STOP FAILED";
    default:
      return "UNKNOWN";
  }
}

const char *simulationParseResultName(SimulationParseResult result) {
  switch (result) {
    case SimulationParseResult::kOk:
      return "ok";
    case SimulationParseResult::kNullArgument:
      return "null-argument";
    case SimulationParseResult::kPayloadTooLarge:
      return "payload-too-large";
    case SimulationParseResult::kInvalidJson:
      return "invalid-json";
    case SimulationParseResult::kInvalidSchema:
      return "invalid-schema";
    default:
      return "unknown";
  }
}

bool modelStatusSelectable(ModelStatus status) {
  return status == ModelStatus::kReady;
}

bool simulationSessionOpen(SimulationState state) {
  return state == SimulationState::kActive ||
         state == SimulationState::kStopping ||
         state == SimulationState::kStopFailed;
}

bool simulationCanStart(SimulationState state) {
  return state == SimulationState::kIdle ||
         state == SimulationState::kStopped ||
         state == SimulationState::kStartFailed;
}

bool simulationCanStop(SimulationState state) {
  return state == SimulationState::kActive ||
         state == SimulationState::kStopFailed;
}

bool simulationTelemetryIsValid(const TelemetryFrame &telemetry) {
  return (telemetry.health_flags & kTelemetryHealthUltrasonicOk) != 0U &&
         telemetry.distance_mm >= kTelemetryUltrasonicMinimumMm &&
         telemetry.distance_mm <= kTelemetryUltrasonicMaximumMm;
}

bool simulationRecordLocalTelemetry(SimulationSnapshot *snapshot,
                                    const TelemetryFrame &telemetry,
                                    uint32_t received_at_ms) {
  if (snapshot == nullptr || !simulationSessionOpen(snapshot->state)) {
    return false;
  }
  snapshot->latest = telemetry;
  snapshot->has_telemetry = true;
  snapshot->last_sample_ms = received_at_ms;
  ++snapshot->local_tel_sample_count;
  if (simulationTelemetryIsValid(telemetry)) {
    ++snapshot->local_valid_ultrasonic_sample_count;
  }
  ++snapshot->revision;
  return true;
}

bool simulationRecordUploadAck(SimulationSnapshot *snapshot,
                               const char *session_id, uint32_t seq,
                               bool succeeded, int http_status,
                               uint32_t attempted_at_ms) {
  if (snapshot == nullptr || session_id == nullptr || session_id[0] == '\0' ||
      !simulationSessionOpen(snapshot->state) ||
      std::strcmp(snapshot->session_id, session_id) != 0) {
    return false;
  }
  snapshot->has_upload_ack = true;
  snapshot->last_upload_ack_succeeded = succeeded;
  snapshot->last_upload_ack_http_status = http_status;
  snapshot->last_upload_ack_ms = attempted_at_ms;
  snapshot->last_upload_ack_seq = seq;
  if (succeeded) {
    ++snapshot->upload_ack_success_count;
  } else {
    ++snapshot->upload_ack_failure_count;
  }
  ++snapshot->revision;
  return true;
}

SimulationUltrasonicQuality simulationUltrasonicQuality(
    const SimulationSnapshot &snapshot, uint32_t now_ms,
    uint32_t maximum_age_ms) {
  if (!snapshot.has_telemetry) {
    return SimulationUltrasonicQuality::kWaiting;
  }
  if (static_cast<uint32_t>(now_ms - snapshot.last_sample_ms) >
      maximum_age_ms) {
    return SimulationUltrasonicQuality::kStale;
  }
  return simulationTelemetryIsValid(snapshot.latest)
             ? SimulationUltrasonicQuality::kValid
             : SimulationUltrasonicQuality::kNoEcho;
}

const char *simulationUltrasonicQualityName(
    SimulationUltrasonicQuality quality) {
  switch (quality) {
    case SimulationUltrasonicQuality::kWaiting:
      return "WAITING";
    case SimulationUltrasonicQuality::kValid:
      return "VALID";
    case SimulationUltrasonicQuality::kNoEcho:
      return "NO ECHO";
    case SimulationUltrasonicQuality::kStale:
      return "STALE";
    default:
      return "UNKNOWN";
  }
}

CollectionButtonAction collectionButtonAction(
    SimulationState state, bool stop_confirmation_pending) {
  if (simulationCanStart(state)) {
    return CollectionButtonAction::kStart;
  }
  if (!simulationCanStop(state)) {
    return CollectionButtonAction::kNone;
  }
  return stop_confirmation_pending
             ? CollectionButtonAction::kStop
             : CollectionButtonAction::kArmStopConfirmation;
}

const ModelOption *findModel(const ModelCatalog &catalog,
                             const char *model_id) {
  if (model_id == nullptr) {
    return nullptr;
  }
  for (size_t index = 0U; index < catalog.count; ++index) {
    if (std::strcmp(catalog.models[index].model_id, model_id) == 0) {
      return &catalog.models[index];
    }
  }
  return nullptr;
}
