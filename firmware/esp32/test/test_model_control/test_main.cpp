#include <unity.h>

#include <cstring>
#include <string>

#include "model_control.h"

void setUp() {}
void tearDown() {}

namespace {

constexpr char kCatalogJson[] = R"json({
  "selected_model_id":"coastal-risk-logreg-v1",
  "models":[
    {"model_id":"coastal-risk-logreg-v1","display_name":"Environment V1","status":"ready","mode":"shadow","description":"UK coastal environment baseline"},
    {"model_id":"impactnet-v2","display_name":"ImpactNet V2","status":"ready","mode":"synthetic_demo","description":"Synthetic temporal research model"},
    {"model_id":"custom-water-v1","display_name":"Custom Water","status":"not_trained","mode":"simulation","description":"User simulation water model"}
  ]
})json";

void test_catalog_contract_is_parsed_into_fixed_pod() {
  ModelCatalog catalog{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(ModelParseResult::kOk),
      static_cast<int>(
          parseModelCatalogJson(kCatalogJson, std::strlen(kCatalogJson),
                                &catalog)));
  TEST_ASSERT_EQUAL_UINT32(3U, catalog.count);
  TEST_ASSERT_EQUAL_STRING("coastal-risk-logreg-v1",
                           catalog.selected_model_id);
  TEST_ASSERT_EQUAL_STRING("ImpactNet V2", catalog.models[1].display_name);
  TEST_ASSERT_TRUE(modelStatusSelectable(catalog.models[0].status));
  TEST_ASSERT_FALSE(modelStatusSelectable(catalog.models[2].status));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModelCatalogState::kReady),
                        static_cast<int>(catalog.state));
}

void test_unknown_status_and_unknown_selection_are_rejected() {
  std::string unknown_status(kCatalogJson);
  const size_t status = unknown_status.find("not_trained");
  TEST_ASSERT_NOT_EQUAL(std::string::npos, status);
  unknown_status.replace(status, std::strlen("not_trained"), "training");
  ModelCatalog catalog{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(ModelParseResult::kInvalidModel),
      static_cast<int>(parseModelCatalogJson(unknown_status.c_str(),
                                             unknown_status.size(),
                                             &catalog)));

  std::string unknown_selection(kCatalogJson);
  const size_t selected =
      unknown_selection.find("coastal-risk-logreg-v1");
  TEST_ASSERT_NOT_EQUAL(std::string::npos, selected);
  unknown_selection.replace(selected, std::strlen("coastal-risk-logreg-v1"),
                            "missing-model");
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(ModelParseResult::kUnknownSelection),
      static_cast<int>(parseModelCatalogJson(unknown_selection.c_str(),
                                             unknown_selection.size(),
                                             &catalog)));
}

void test_catalog_rejects_more_than_three_models() {
  constexpr char json[] = R"json({
    "selected_model_id":"m1",
    "models":[
      {"model_id":"m1","display_name":"1","status":"ready","mode":"research","description":"one"},
      {"model_id":"m2","display_name":"2","status":"ready","mode":"research","description":"two"},
      {"model_id":"m3","display_name":"3","status":"ready","mode":"research","description":"three"},
      {"model_id":"m4","display_name":"4","status":"ready","mode":"research","description":"four"}
    ]
  })json";
  ModelCatalog catalog{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(ModelParseResult::kTooManyModels),
      static_cast<int>(
          parseModelCatalogJson(json, std::strlen(json), &catalog)));
}

void test_simulation_start_contract_and_state_guards() {
  constexpr char json[] = R"json({
    "session_id":"sim_2c6f6a5e",
    "state":"active",
    "started_at":"2026-08-14T10:15:30Z",
    "device_id":"COAST_01",
    "sample_count":37
  })json";
  SimulationSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SimulationParseResult::kOk),
      static_cast<int>(parseSimulationStartJson(
          json, std::strlen(json), &snapshot)));
  TEST_ASSERT_EQUAL_STRING("sim_2c6f6a5e", snapshot.session_id);
  TEST_ASSERT_EQUAL_UINT32(37U, snapshot.server_stored_sample_count);
  TEST_ASSERT_EQUAL_UINT32(0U, snapshot.local_tel_sample_count);
  TEST_ASSERT_EQUAL_UINT32(0U,
                           snapshot.local_valid_ultrasonic_sample_count);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(SimulationState::kActive),
                        static_cast<int>(snapshot.state));
  TEST_ASSERT_TRUE(simulationSessionOpen(SimulationState::kActive));
  TEST_ASSERT_TRUE(simulationSessionOpen(SimulationState::kStopFailed));
  TEST_ASSERT_FALSE(simulationSessionOpen(SimulationState::kStopped));
  TEST_ASSERT_TRUE(simulationCanStart(SimulationState::kIdle));
  TEST_ASSERT_TRUE(simulationCanStart(SimulationState::kStartFailed));
  TEST_ASSERT_FALSE(simulationCanStart(SimulationState::kStarting));
  TEST_ASSERT_TRUE(simulationCanStop(SimulationState::kActive));
  TEST_ASSERT_TRUE(simulationCanStop(SimulationState::kStopFailed));
  TEST_ASSERT_FALSE(simulationCanStop(SimulationState::kStopping));
}

void test_simulation_start_requires_all_contract_fields() {
  constexpr char json[] =
      "{\"session_id\":\"sim_0001\",\"state\":\"active\"}";
  SimulationSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SimulationParseResult::kInvalidSchema),
      static_cast<int>(parseSimulationStartJson(
          json, std::strlen(json), &snapshot)));
}

void test_simulation_start_requires_server_sample_count() {
  constexpr char json[] = R"json({
    "session_id":"sim_0001",
    "state":"active",
    "started_at":"2026-08-14T10:15:30Z"
  })json";
  SimulationSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SimulationParseResult::kInvalidSchema),
      static_cast<int>(parseSimulationStartJson(
          json, std::strlen(json), &snapshot)));
}

void test_active_session_recovery_rejects_completed_record() {
  constexpr char json[] = R"json({
    "session_id":"sim_12345678",
    "state":"completed",
    "started_at":"2026-08-14T10:15:30Z",
    "sample_count":12
  })json";
  SimulationSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SimulationParseResult::kInvalidSchema),
      static_cast<int>(parseSimulationStartJson(
          json, std::strlen(json), &snapshot)));
}

void test_recovered_server_count_local_tel_and_upload_ack_stay_separate() {
  constexpr char json[] = R"json({
    "session_id":"sim_recovered_100",
    "state":"active",
    "started_at":"2026-08-14T10:15:30Z",
    "sample_count":100
  })json";
  SimulationSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SimulationParseResult::kOk),
      static_cast<int>(parseSimulationStartJson(
          json, std::strlen(json), &snapshot)));
  TEST_ASSERT_EQUAL_UINT32(100U, snapshot.server_stored_sample_count);
  TEST_ASSERT_EQUAL_UINT32(0U, snapshot.local_tel_sample_count);
  TEST_ASSERT_EQUAL_UINT32(0U,
                           snapshot.local_valid_ultrasonic_sample_count);
  TEST_ASSERT_EQUAL_UINT32(0U, snapshot.upload_ack_success_count);
  TEST_ASSERT_EQUAL_UINT32(0U, snapshot.upload_ack_failure_count);

  TelemetryFrame valid{};
  valid.seq = 101U;
  valid.distance_mm = 500U;
  valid.health_flags = kTelemetryHealthUltrasonicOk;
  TEST_ASSERT_TRUE(simulationRecordLocalTelemetry(&snapshot, valid, 5000U));
  TEST_ASSERT_EQUAL_UINT32(100U, snapshot.server_stored_sample_count);
  TEST_ASSERT_EQUAL_UINT32(1U, snapshot.local_tel_sample_count);
  TEST_ASSERT_EQUAL_UINT32(1U,
                           snapshot.local_valid_ultrasonic_sample_count);
  TEST_ASSERT_EQUAL_UINT32(0U, snapshot.upload_ack_success_count);

  TEST_ASSERT_TRUE(simulationRecordUploadAck(
      &snapshot, "sim_recovered_100", valid.seq, true, 201, 5100U));
  TEST_ASSERT_EQUAL_UINT32(100U, snapshot.server_stored_sample_count);
  TEST_ASSERT_EQUAL_UINT32(1U, snapshot.local_tel_sample_count);
  TEST_ASSERT_EQUAL_UINT32(1U,
                           snapshot.local_valid_ultrasonic_sample_count);
  TEST_ASSERT_EQUAL_UINT32(1U, snapshot.upload_ack_success_count);
  TEST_ASSERT_EQUAL_UINT32(0U, snapshot.upload_ack_failure_count);
  TEST_ASSERT_TRUE(snapshot.has_upload_ack);
  TEST_ASSERT_TRUE(snapshot.last_upload_ack_succeeded);
  TEST_ASSERT_EQUAL_UINT32(101U, snapshot.last_upload_ack_seq);
  TEST_ASSERT_EQUAL_INT(201, snapshot.last_upload_ack_http_status);

  TEST_ASSERT_TRUE(simulationRecordUploadAck(
      &snapshot, "sim_recovered_100", 102U, false, 503, 5200U));
  TEST_ASSERT_EQUAL_UINT32(100U, snapshot.server_stored_sample_count);
  TEST_ASSERT_EQUAL_UINT32(1U, snapshot.local_tel_sample_count);
  TEST_ASSERT_EQUAL_UINT32(1U, snapshot.upload_ack_success_count);
  TEST_ASSERT_EQUAL_UINT32(1U, snapshot.upload_ack_failure_count);
  TEST_ASSERT_FALSE(snapshot.last_upload_ack_succeeded);
  TEST_ASSERT_EQUAL_UINT32(102U, snapshot.last_upload_ack_seq);
  TEST_ASSERT_EQUAL_INT(503, snapshot.last_upload_ack_http_status);

  TEST_ASSERT_FALSE(simulationRecordUploadAck(
      &snapshot, "sim_other_session", 103U, false, 409, 5300U));
  TEST_ASSERT_EQUAL_UINT32(1U, snapshot.upload_ack_success_count);
  TEST_ASSERT_EQUAL_UINT32(1U, snapshot.upload_ack_failure_count);
  TEST_ASSERT_EQUAL_UINT32(100U, snapshot.server_stored_sample_count);
}

void test_collection_ultrasonic_quality_is_fail_closed_and_freshness_aware() {
  SimulationSnapshot snapshot{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SimulationUltrasonicQuality::kWaiting),
      static_cast<int>(simulationUltrasonicQuality(snapshot, 1000U, 2500U)));

  snapshot.has_telemetry = true;
  snapshot.last_sample_ms = 900U;
  snapshot.latest.distance_mm = 500U;
  snapshot.latest.health_flags = 0U;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SimulationUltrasonicQuality::kNoEcho),
      static_cast<int>(simulationUltrasonicQuality(snapshot, 1000U, 2500U)));

  snapshot.latest.health_flags = kTelemetryHealthUltrasonicOk;
  TEST_ASSERT_TRUE(simulationTelemetryIsValid(snapshot.latest));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SimulationUltrasonicQuality::kValid),
      static_cast<int>(simulationUltrasonicQuality(snapshot, 1000U, 2500U)));

  snapshot.latest.distance_mm = 0U;
  TEST_ASSERT_FALSE(simulationTelemetryIsValid(snapshot.latest));
  snapshot.latest.distance_mm = 500U;
  snapshot.last_sample_ms = 1000U;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SimulationUltrasonicQuality::kStale),
      static_cast<int>(simulationUltrasonicQuality(snapshot, 3501U, 2500U)));
}

void test_ultrasonic_presentation_promotes_relative_level_not_raw_gap() {
  TelemetryFrame telemetry{};
  telemetry.distance_mm = 520U;
  telemetry.water_rise_mm = 49;

  const ultrasonic_ui::Presentation values =
      ultrasonic_ui::presentation(telemetry);
  TEST_ASSERT_EQUAL_INT32(49, values.level_change_mm);
  TEST_ASSERT_EQUAL_UINT32(520U, values.sensor_gap_mm);
  TEST_ASSERT_EQUAL_STRING("LEVEL CHANGE", ultrasonic_ui::kLevelChangeLabel);
  TEST_ASSERT_EQUAL_STRING("SENSOR GAP", ultrasonic_ui::kSensorGapLabel);
}

void test_collection_button_requires_second_stop_press() {
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(CollectionButtonAction::kStart),
      static_cast<int>(
          collectionButtonAction(SimulationState::kIdle, false)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(CollectionButtonAction::kNone),
      static_cast<int>(
          collectionButtonAction(SimulationState::kStarting, false)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(CollectionButtonAction::kArmStopConfirmation),
      static_cast<int>(
          collectionButtonAction(SimulationState::kActive, false)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(CollectionButtonAction::kStop),
      static_cast<int>(
          collectionButtonAction(SimulationState::kActive, true)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(CollectionButtonAction::kArmStopConfirmation),
      static_cast<int>(
          collectionButtonAction(SimulationState::kStopFailed, false)));
}

void test_collection_touch_regions_use_half_open_edges() {
  TEST_ASSERT_TRUE(model_ui::kSessionButton.contains(602, 15));
  TEST_ASSERT_TRUE(model_ui::kSessionButton.contains(771, 52));
  TEST_ASSERT_FALSE(model_ui::kSessionButton.contains(772, 52));
  TEST_ASSERT_FALSE(model_ui::kSessionButton.contains(771, 53));
  TEST_ASSERT_TRUE(model_ui::cardRect(0U).contains(28, 96));
  TEST_ASSERT_FALSE(model_ui::cardRect(0U).contains(264, 96));
  TEST_ASSERT_TRUE(model_ui::cardRect(1U).contains(280, 96));
}

}  // namespace

int runTests() {
  UNITY_BEGIN();
  RUN_TEST(test_catalog_contract_is_parsed_into_fixed_pod);
  RUN_TEST(test_unknown_status_and_unknown_selection_are_rejected);
  RUN_TEST(test_catalog_rejects_more_than_three_models);
  RUN_TEST(test_simulation_start_contract_and_state_guards);
  RUN_TEST(test_simulation_start_requires_all_contract_fields);
  RUN_TEST(test_simulation_start_requires_server_sample_count);
  RUN_TEST(test_active_session_recovery_rejects_completed_record);
  RUN_TEST(
      test_recovered_server_count_local_tel_and_upload_ack_stay_separate);
  RUN_TEST(
      test_collection_ultrasonic_quality_is_fail_closed_and_freshness_aware);
  RUN_TEST(
      test_ultrasonic_presentation_promotes_relative_level_not_raw_gap);
  RUN_TEST(test_collection_button_requires_second_stop_press);
  RUN_TEST(test_collection_touch_regions_use_half_open_edges);
  return UNITY_END();
}

#if defined(ARDUINO)
void setup() { runTests(); }
void loop() {}
#else
int main(int, char **) { return runTests(); }
#endif
