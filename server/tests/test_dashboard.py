import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from app.dashboard import DASHBOARD_HTML, render_dashboard


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value is not None:
                self.ids.append(value)


def test_dashboard_has_unique_interactive_element_ids() -> None:
    parser = _IdCollector()
    parser.feed(DASHBOARD_HTML)
    duplicates = {value for value in parser.ids if parser.ids.count(value) > 1}
    assert not duplicates
    assert '<html lang="en-GB">' in DASHBOARD_HTML
    assert "CoastWatch Great Yarmouth Monitoring Console" in DASHBOARD_HTML
    assert not re.search(r"[\u3400-\u9fff]", DASHBOARD_HTML)


def test_dashboard_removes_location_configuration_and_fixes_monitoring_site() -> None:
    removed = (
        "locationPreset",
        "locationQuery",
        "searchLocation",
        "displayLocation",
        "saveLocation",
        "/api/v1/locations/presets",
        "/api/v1/locations/search",
        "/api/v1/device-location",
    )
    assert all(value not in DASHBOARD_HTML for value in removed)
    assert "Great Yarmouth, England" in DASHBOARD_HTML
    assert "function isGreatYarmouthSite(site)" in DASHBOARD_HTML


def test_dashboard_javascript_references_only_existing_dom_ids() -> None:
    declared = set(re.findall(r'id="([A-Za-z0-9_-]+)"', DASHBOARD_HTML))
    referenced = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", DASHBOARD_HTML))
    assert referenced <= declared


def test_dashboard_consumes_server_side_dataset_and_readiness_contracts() -> None:
    required_routes = (
        "/api/v1/telemetry/latest?device_id=",
        "/api/v1/telemetry?device_id=",
        "/api/v1/simulations/overview?device_id=",
        "/timeline?device_id=",
        "/stop`,'POST'",
        "/api/v1/simulations/labels",
    )
    assert all(route in DASHBOARD_HTML for route in required_routes)
    assert "ESP32 ultrasonic measurement → server storage" in DASHBOARD_HTML
    assert "STM32 measurement" not in DASHBOARD_HTML
    assert "['Power monitoring',4,false]" in DASHBOARD_HTML
    assert "${name} NOT CONFIGURED" in DASHBOARD_HTML
    assert "Unlabelled samples remain UNKNOWN" in DASHBOARD_HTML


def test_dashboard_exposes_all_returned_core_evaluation_metrics() -> None:
    assert all(
        f'id="{element_id}"' in DASHBOARD_HTML
        for element_id in (
            "officialMetricPRAuc",
            "officialMetricRecall",
            "officialMetricPrecision",
            "officialMetricF1",
            "officialMetricRocAuc",
            "officialMetricBrier",
        )
    )
    assert "baselines.water_level_threshold||baselines.threshold" in DASHBOARD_HTML
    assert "Persistence Baseline" in DASHBOARD_HTML


def test_dashboard_keeps_simulation_research_boundary_visible() -> None:
    assert "DEVICE-MEASURED" in DASHBOARD_HTML
    assert "NO LOCAL TRAINING" in DASHBOARD_HTML
    assert "does not generate synthetic readings" in DASHBOARD_HTML
    assert "/api/v1/simulations/train" not in DASHBOARD_HTML


def test_failed_training_can_be_retried_when_server_readiness_stays_true() -> None:
    assert "/api/v1/simulations/train" not in DASHBOARD_HTML
    assert "simulationReadiness" not in DASHBOARD_HTML


def test_dashboard_requires_explicit_completed_session_selection() -> None:
    assert 'id="simulationSession"' in DASHBOARD_HTML
    assert "session.state==='completed'" in DASHBOARD_HTML
    assert "trainingSelectionCount" not in DASHBOARD_HTML
    assert "selectedTrainingSessionIds" not in DASHBOARD_HTML


def test_dashboard_deletes_only_completed_unused_sessions_with_confirmation() -> None:
    assert 'id="sessionDeletionHelp"' in DASHBOARD_HTML
    assert 'id="sessionDeletionStatus"' in DASHBOARD_HTML
    assert 'role="status" aria-live="polite"' in DASHBOARD_HTML
    assert "Only completed, unreferenced sessions can be deleted" in DASHBOARD_HTML
    assert (
        "Active collection and telemetry transmission are never deleted here"
        in DASHBOARD_HTML
    )
    assert "if(session.state!=='completed')" in DASHBOARD_HTML
    assert (
        "deleteButton.disabled=true; deleteButton.textContent='Collecting — cannot delete'"
        in DASHBOARD_HTML
    )
    assert "pendingSessionDeletionExpiresAt=Date.now()+5000" in DASHBOARD_HTML
    assert "window.setTimeout" in DASHBOARD_HTML
    assert "function sessionDeletionDescriptor(session)" in DASHBOARD_HTML
    assert (
        "session ID ${session.session_id}, ${formatCount(session.sample_count)} samples"
        in DASHBOARD_HTML
    )
    assert (
        "Confirm delete …${shortSessionId} · ${formatCount(session.sample_count)} samples"
        in DASHBOARD_HTML
    )
    assert "deleteButton.setAttribute('aria-pressed',String(pending))" in DASHBOARD_HTML
    assert "Confirm permanent deletion of ${session.name}" in DASHBOARD_HTML
    assert "Delete completed session ${session.name}" in DASHBOARD_HTML


def test_dashboard_uses_session_delete_contract_and_refreshes_all_views() -> None:
    route = (
        "/api/v1/simulations/sessions/${encodeURIComponent(session.session_id)}"
        "?device_id=${encodeURIComponent(DEVICE)}"
    )
    assert route in DASHBOARD_HTML
    assert f"sendJson(`{route}`,'DELETE',undefined)" in DASHBOARD_HTML
    assert "selectedTrainingSessionIds.delete(session.session_id)" not in DASHBOARD_HTML
    assert (
        "simulationRequestSerial+=1; selectedSimulationSession=null; emptyTimeline()"
        in DASHBOARD_HTML
    )
    assert "Refreshing the overview and timeline" in DASHBOARD_HTML
    assert "await loadSimulationSessions({throwOnError:true})" in DASHBOARD_HTML
    assert "if(throwOnError) throw error" in DASHBOARD_HTML
    assert "Deletion succeeded, but refresh failed" in DASHBOARD_HTML
    assert "result?.deleted_counts" in DASHBOARD_HTML
    assert "result?.detached_telemetry_count" in DASHBOARD_HTML
    assert "is referenced by training artifact" in DASHBOARD_HTML
    assert "referenced by a model artifact" in DASHBOARD_HTML
    assert "cannot be verified; session deletion is blocked" in DASHBOARD_HTML


def test_dashboard_displays_server_selection_and_training_provenance() -> None:
    assert "officialReadiness" in DASHBOARD_HTML
    assert "officialRunProvenance" in DASHBOARD_HTML
    assert "officialArtifactProvenance" in DASHBOARD_HTML
    assert "selectedTrainingSessionIds" not in DASHBOARD_HTML


def test_gateway_admin_dashboard_uses_one_prefixed_api_and_csrf_contract() -> None:
    rendered = render_dashboard(api_prefix="/admin", admin_mode=True)
    assert "const ADMIN_MODE=true" in rendered
    assert 'const ADMIN_BASE="/admin"' in rendered
    assert "/admin/api/v1/simulations/overview?device_id=" in rendered
    assert "/admin/api/v1/simulations/train" not in rendered
    assert "/admin/api/v1/telemetry/latest?device_id=" in rendered
    assert (
        "/admin/api/v1/simulations/sessions/"
        "${encodeURIComponent(session.session_id)}"
        "?device_id=${encodeURIComponent(DEVICE)}" in rendered
    )
    assert "headers['X-CSRF-Token']=adminCsrfToken" in rendered
    assert "`${ADMIN_BASE}/api/auth/session`" in rendered
    assert "`${ADMIN_BASE}/api/auth/logout`" in rendered


def test_dashboard_configures_device_scenario_before_collection() -> None:
    required_fields = (
        "scenarioName",
        "scenarioSimulatedAt",
        "scenarioLatitude",
        "scenarioLongitude",
        "scenarioAirTemperature",
        "scenarioHumidity",
        "scenarioWindSpeed",
        "scenarioWaveHeight",
        "scenarioWavePeriod",
        "scenarioWaterTemperature",
        "scenarioSeaLevel",
        "scenarioCurrentVelocity",
    )
    assert all(f'id="{field}"' not in DASHBOARD_HTML for field in required_fields)
    assert "/api/v1/simulations/device-scenario" not in DASHBOARD_HTML
    assert "saveDeviceScenario" not in DASHBOARD_HTML
    expected_bounds = (
        'id="scenarioAirTemperature" type="number" min="-80" max="60"',
        'id="scenarioHumidity" type="number" min="0" max="100"',
        'id="scenarioWindSpeed" type="number" min="0" max="400"',
        'id="scenarioWaveHeight" type="number" min="0" max="40"',
        'id="scenarioWavePeriod" type="number" min="0.1" max="60"',
        'id="scenarioWaterTemperature" type="number" min="-5" max="45"',
        'id="scenarioSeaLevel" type="number" min="-20" max="20"',
        'id="scenarioCurrentVelocity" type="number" min="0" max="50"',
    )
    assert all(bounds not in DASHBOARD_HTML for bounds in expected_bounds)


def test_dashboard_uses_the_server_reported_environment_location() -> None:
    assert "const location=String(e.location||e.display_location" in DASHBOARD_HTML
    assert "const parts=[location" in DASHBOARD_HTML
    assert "const parts=['Great Yarmouth'" not in DASHBOARD_HTML
    assert "Sea level ${metric(e.sea_level_height_m,' m',3)}" in DASHBOARD_HTML
    assert "OPERATOR-SUPPLIED" not in DASHBOARD_HTML


def test_dashboard_shows_immutable_session_scenario_provenance() -> None:
    assert "/scenario?device_id=" not in DASHBOARD_HTML
    assert "IMMUTABLE SNAPSHOT" not in DASHBOARD_HTML
    assert "scenario.scenario_hash" not in DASHBOARD_HTML
    assert "DEVICE-MEASURED" in DASHBOARD_HTML


def test_dashboard_documents_22_features_and_dynamic_training_readiness() -> None:
    assert "22-feature fusion" not in DASHBOARD_HTML
    assert "training-readiness" not in DASHBOARD_HTML
    assert "scenario_distinct_values" not in DASHBOARD_HTML
    assert "NO LOCAL TRAINING" in DASHBOARD_HTML


def test_dashboard_supports_multi_coast_training_and_great_yarmouth_external_test() -> (
    None
):
    assert "SINGLE-COAST exploratory scope" in DASHBOARD_HTML
    assert "MULTI-COAST scope" in DASHBOARD_HTML
    assert "function isGreatYarmouthSite(site)" in DASHBOARD_HTML
    assert "const sensorSites=sites.filter(site=>site.greatYarmouth)" in DASHBOARD_HTML
    assert (
        "option.selected=previousSites.size?previousSites.has(site.id):true"
        in DASHBOARD_HTML
    )
    assert "single_scenario_session_holdout" not in DASHBOARD_HTML


def test_dashboard_compares_combined_model_with_two_ablations_and_threshold() -> None:
    assert "Combined Logistic Regression · 22 features" not in DASHBOARD_HTML
    assert "ultrasonic_only_logistic_regression" not in DASHBOARD_HTML
    assert "Water-level Threshold Baseline" in DASHBOARD_HTML
    assert "Persistence Baseline" in DASHBOARD_HTML


def test_hard_threshold_baseline_omits_probability_metrics_and_deltas() -> None:
    assert "thresholdSelectionSplit" in DASHBOARD_HTML
    assert "thresholdMacro?metricText(thresholdMacro.recall):'N/A'" in DASHBOARD_HTML
    assert "Brier, ROC AUC, and PR AUC are not compared" in DASHBOARD_HTML


def test_dashboard_exposes_session_macro_as_primary_scientific_view() -> None:
    assert "const macro=completeMacroCoverage?macroCandidate:null" in DASHBOARD_HTML
    assert "site-macro (PRIMARY)" in DASHBOARD_HTML
    assert "Row-level companion (NOT PRIMARY)" in DASHBOARD_HTML


def test_dashboard_imports_json_or_one_row_csv_without_automatic_upload() -> None:
    assert "function parseScenarioImport(text)" not in DASHBOARD_HTML
    assert "scenarioImport" not in DASHBOARD_HTML


def test_prefixed_admin_dashboard_rewrites_simulated_scenario_routes() -> None:
    rendered = render_dashboard(api_prefix="/admin", admin_mode=True)
    assert "/admin/api/v1/simulations/device-scenario" not in rendered
    assert "/admin/api/v1/simulations/overview?device_id=" in rendered


def test_dashboard_has_separate_official_training_and_sensor_test_consoles() -> None:
    assert 'id="officialTrainingConsole"' in DASHBOARD_HTML
    assert 'id="sensorExternalTestConsole"' in DASHBOARD_HTML
    assert "Official coastal model training" in DASHBOARD_HTML
    assert "Ultrasonic external-test console" in DASHBOARD_HTML
    assert "SENSOR ROWS USED FOR FIT = 0" in DASHBOARD_HTML
    assert "SCALER = 0" in DASHBOARD_HTML
    assert "THRESHOLD = 0" in DASHBOARD_HTML
    assert "ESP32 is excluded from training" in DASHBOARD_HTML


def test_official_console_uses_registered_manifest_and_readonly_time_splits() -> None:
    required_ids = (
        "officialDataset",
        "officialSites",
        "officialTrainStart",
        "officialTrainEnd",
        "officialValidationStart",
        "officialValidationEnd",
        "officialTestStart",
        "officialTestEnd",
        "officialSource",
        "officialLicense",
        "officialDatasetHash",
        "officialLabelDefinition",
        "officialSplitDefinition",
        "officialDatasetProvenance",
        "officialDatasetImporterReplay",
        "officialReadiness",
        "officialBlockers",
        "trainOfficialModel",
    )
    assert all(f'id="{element_id}"' in DASHBOARD_HTML for element_id in required_ids)
    for element_id in (
        "officialTrainStart",
        "officialTrainEnd",
        "officialValidationStart",
        "officialValidationEnd",
        "officialTestStart",
        "officialTestEnd",
    ):
        assert re.search(rf'id="{element_id}"[^>]*readonly', DASHBOARD_HTML)
    assert "Manifest splits are read-only" in DASHBOARD_HTML
    assert "selected_site_ids=sites" in DASHBOARD_HTML
    assert "const payload={dataset_id:$('officialDataset').value}" in DASHBOARD_HTML
    assert "train_start" not in DASHBOARD_HTML
    assert "validation_start" not in DASHBOARD_HTML
    assert "test_start" not in DASHBOARD_HTML


def test_official_console_calls_manual_training_and_activation_api_contracts() -> None:
    routes = (
        "/api/v1/official-datasets",
        "/api/v1/official-datasets/rescan",
        "/api/v1/official-datasets/${encodeURIComponent(datasetId)}",
        "/api/v1/official-training/readiness?${params.toString()}",
        "/api/v1/official-training/runs",
        "/api/v1/official-training/runs?limit=20",
        "/api/v1/official-training/runs/${encodeURIComponent(runId)}",
        "/api/v1/official-training/runs/${encodeURIComponent(runId)}/activate",
        "/api/v1/official-model",
    )
    assert all(route in DASHBOARD_HTML for route in routes)
    assert (
        "selectedOfficialSiteIds().forEach(siteId=>params.append('site_id',siteId))"
        in DASHBOARD_HTML
    )
    assert (
        "sendJson('/api/v1/official-training/runs','POST',officialTrainingPayload())"
        in DASHBOARD_HTML
    )
    assert "activateOfficialRun" in DASHBOARD_HTML


def test_rescan_surfaces_http_200_bundle_errors_and_preserves_old_selection() -> None:
    assert 'id="officialRescanStatus"' in DASHBOARD_HTML
    assert "Bundle validation errors will be reported here" in DASHBOARD_HTML
    assert (
        "const scan=await sendJson('/api/v1/official-datasets/rescan','POST',undefined)"
        in DASHBOARD_HTML
    )
    assert "scan?.error_count" in DASHBOARD_HTML
    assert "item?.bundle" in DASHBOARD_HTML
    assert "item?.detail||item?.message" in DASHBOARD_HTML
    assert "const fullyAccepted=errorCount===0" in DASHBOARD_HTML
    assert (
        "Scan complete with ${formatCount(errorCount)} rejected bundles"
        in DASHBOARD_HTML
    )
    assert "loadOfficialDatasets({preserveSelection:true})" in DASHBOARD_HTML
    assert "The previous selection ${previousSelection} was retained" in DASHBOARD_HTML
    assert (
        "try { await sendJson('/api/v1/official-datasets/rescan','POST',undefined);"
        not in DASHBOARD_HTML
    )


def test_official_console_exposes_frozen_test_metrics_and_required_baselines() -> None:
    metric_ids = (
        "officialMetricPRAuc",
        "officialMetricRecall",
        "officialMetricPrecision",
        "officialMetricF1",
        "officialMetricRocAuc",
        "officialMetricBrier",
        "officialMetricFalsePositiveRows",
        "officialMetricThreshold",
        "officialMetricSiteCoverage",
    )
    assert all(f'id="{element_id}"' in DASHBOARD_HTML for element_id in metric_ids)
    assert "metrics.pr_auc??metrics.average_precision" in DASHBOARD_HTML
    assert "metrics.false_positive_rows_per_day" in DASHBOARD_HTML
    assert "False-positive rows / day" in DASHBOARD_HTML
    assert "Misclassified rows, not warning events" in DASHBOARD_HTML
    assert "metrics.decision_threshold" in DASHBOARD_HTML
    assert "Water-level Threshold Baseline" in DASHBOARD_HTML
    assert "Persistence Baseline" in DASHBOARD_HTML
    assert "baselines.water_level_threshold||baselines.threshold" in DASHBOARD_HTML
    assert "baselines.persistence" in DASHBOARD_HTML


def test_site_selection_changes_evidence_scope_without_browser_side_training_threshold() -> (
    None
):
    assert 'id="officialEvidenceScope"' in DASHBOARD_HTML
    assert "SINGLE-COAST exploratory scope" in DASHBOARD_HTML
    assert "multi-coast exploration" in DASHBOARD_HTML
    assert "MULTI-COAST scope" in DASHBOARD_HTML
    assert "at least three sites, 200 rows per split" in DASHBOARD_HTML
    assert "count<3" in DASHBOARD_HTML
    assert "officialReadiness?.ready" in DASHBOARD_HTML


def test_official_console_keeps_site_macro_primary_and_row_level_companion_separate() -> (
    None
):
    assert "const macro=completeMacroCoverage?macroCandidate:null" in DASHBOARD_HTML
    assert (
        "const macroMetric=name=>macro?metricText(macro[name]):'N/A'" in DASHBOARD_HTML
    )
    assert "eligible / selected" in DASHBOARD_HTML
    assert "site-macro (PRIMARY) N/A" in DASHBOARD_HTML
    assert "Row-level companion (NOT PRIMARY)" in DASHBOARD_HTML
    assert "macro.pr_auc??metrics.pr_auc" not in DASHBOARD_HTML
    assert "run.activatable!==false&&completeMacroCoverage" in DASHBOARD_HTML


def test_official_console_discloses_provenance_limits_at_every_stage() -> None:
    assert "operator-attested" in DASHBOARD_HTML
    assert "deterministic_importer_replay_verified=" in DASHBOARD_HTML
    assert (
        "the server verifies raw bytes, SHA-256 values, and manifest structure"
        in DASHBOARD_HTML
    )
    assert (
        "Ownership, licensing, harmonisation, and label derivation remain operator-attested"
        in DASHBOARD_HTML
    )
    assert 'id="officialDatasetProvenance"' in DASHBOARD_HTML
    assert 'id="officialRunProvenance"' in DASHBOARD_HTML
    assert 'id="officialArtifactProvenance"' in DASHBOARD_HTML
    assert "run.source_manifest?.provenance_limitation" in DASHBOARD_HTML


def test_threshold_baseline_is_validation_selected_and_per_site() -> None:
    assert "Water-level Threshold Baseline" in DASHBOARD_HTML
    assert "threshold.threshold_selection_split" in DASHBOARD_HTML
    assert "thresholds_by_site" in DASHBOARD_HTML
    assert "threshold.per_site_frozen_test" in DASHBOARD_HTML
    assert "threshold.selected_site_coverage" in DASHBOARD_HTML
    assert "thresholdSelectionSplit" in DASHBOARD_HTML
    assert "thresholdCoverage" in DASHBOARD_HTML
    assert "thresholdMacro?metricText(thresholdMacro.recall):'N/A'" in DASHBOARD_HTML
    assert "train-only" not in DASHBOARD_HTML


def test_professor_facing_baseline_verdict_is_server_sourced_and_probability_safe() -> (
    None
):
    assert 'id="officialBaselineVerdict"' in DASHBOARD_HTML
    assert "run?.metrics?.delta_vs_water_level_threshold" in DASHBOARD_HTML
    assert "This interface does not invent a claim" in DASHBOARD_HTML
    assert "ML improves baseline" in DASHBOARD_HTML
    assert "No demonstrated improvement; prefer simple rule" in DASHBOARD_HTML
    assert "Classification-only" in DASHBOARD_HTML
    assert "Brier, ROC AUC, and PR AUC are not compared" in DASHBOARD_HTML
    assert "Object.entries(labels)" in DASHBOARD_HTML
    assert "pr_auc:'" not in DASHBOARD_HTML
    assert "comparison.available===false||!comparison.verdict" in DASHBOARD_HTML
    assert "Row-level or eligible-subset metrics are not substituted" in DASHBOARD_HTML


def test_official_model_language_does_not_claim_disaster_probability() -> None:
    assert "extreme sea-level condition probability" in DASHBOARD_HTML
    assert "not a tsunami, flood, or public-warning probability" in DASHBOARD_HTML
    assert "Model output, not a disaster probability" in DASHBOARD_HTML
    assert (
        "does not replace field validation with coastal hydrology instruments"
        in DASHBOARD_HTML
    )


def test_sensor_console_freezes_context_and_keeps_exploratory_mode_separate() -> None:
    required_ids = (
        "sensorOfficialModel",
        "sensorStation",
        "sensorContextId",
        "sensorProfileMode",
        "sensorGain",
        "sensorReferenceLevel",
        "sensorDatum",
        "sensorCalibrationSession",
        "freezeSensorProfile",
        "clearSensorProfile",
        "sensorTestSession",
        "runSensorExternalTest",
    )
    assert all(f'id="{element_id}"' in DASHBOARD_HTML for element_id in required_ids)
    assert "FORMAL — derived from an independent calibration session" in DASHBOARD_HTML
    assert "EXPLORATORY — manual gain, excluded from formal metrics" in DASHBOARD_HTML
    assert "payload.calibration_session_id" in DASHBOARD_HTML
    assert "payload.manual_gain" in DASHBOARD_HTML
    assert "payload.manual_reference_level_m" in DASHBOARD_HTML
    assert "EXCLUDED FROM FORMAL METRICS" in DASHBOARD_HTML
    assert "source_manifest?.frozen_sensor_contexts" in DASHBOARD_HTML
    assert (
        "one sensor-mapped level feature and 17 frozen official context features"
        in DASHBOARD_HTML
    )
    assert 'id="sensorProfileProvenance"' in DASHBOARD_HTML
    assert "mapping.official_train_q05_m" in DASHBOARD_HTML
    assert "mapping.official_train_q95_m" in DASHBOARD_HTML
    assert "mapping.calibration_rise_q05_mm" in DASHBOARD_HTML
    assert "mapping.calibration_rise_q95_mm" in DASHBOARD_HTML
    assert "artifact.calibration_source" in DASHBOARD_HTML
    assert "calibration.sample_count" in DASHBOARD_HTML
    assert "calibration.samples_sha256" in DASHBOARD_HTML
    assert "gain_m_per_m" in DASHBOARD_HTML
    assert "reference_level_m" in DASHBOARD_HTML


def test_official_sensor_contract_lists_all_18_features_without_residual_duplicate() -> (
    None
):
    assert "18-feature contract" in DASHBOARD_HTML
    assert (
        "one relative-water-level feature may be replaced by the sensor mapping"
        in DASHBOARD_HTML
    )
    assert (
        "one sensor-mapped level feature and 17 frozen official context features"
        in DASHBOARD_HTML
    )
    assert "surge_residual" not in DASHBOARD_HTML


def test_sensor_console_displays_affine_mapping_raw_units_and_ood_without_clipping() -> (
    None
):
    assert (
        "mapped_level_m = reference_level_m + gain × (water_rise_mm / 1000)"
        in DASHBOARD_HTML
    )
    assert 'id="sensorRawRise"' in DASHBOARD_HTML
    assert 'id="sensorMappedLevel"' in DASHBOARD_HTML
    assert 'id="sensorOodState"' in DASHBOARD_HTML
    assert "Out-of-distribution values are not clipped" in DASHBOARD_HTML
    assert "const mapped=reference+gain*(rise/1000)" in DASHBOARD_HTML
    assert "mapped<range.min" in DASHBOARD_HTML
    assert "mapped>range.max" in DASHBOARD_HTML
    assert "Math.min(range" not in DASHBOARD_HTML
    assert "Math.max(range" not in DASHBOARD_HTML


def test_sensor_console_calls_profile_and_external_test_api_contracts() -> None:
    routes = (
        "/api/v1/sensor-test/device-profile?device_id=",
        "/api/v1/sensor-test/device-profile",
        "/api/v1/sensor-test/runs?device_id=",
        "/api/v1/sensor-test/runs/${encodeURIComponent(runId)}",
        "/api/v1/sensor-test/runs",
    )
    assert all(route in DASHBOARD_HTML for route in routes)
    assert (
        "{device_id:DEVICE,context_id:$('sensorContextId').value,mode}"
        in DASHBOARD_HTML
    )
    assert "{device_id:DEVICE,session_id:sessionId}" in DASHBOARD_HTML
    assert "Model parameters, scaler, and threshold remain unchanged" in DASHBOARD_HTML


def test_sensor_external_test_separates_full_aggregates_from_bounded_preview() -> None:
    metric_ids = (
        "sensorMetricInputSamples",
        "sensorMetricValidSamples",
        "sensorMetricSamples",
        "sensorMetricInvalidSamples",
        "sensorMetricResultRows",
        "sensorMetricPreviewRows",
        "sensorTestEvaluationPolicy",
    )
    assert all(f'id="{element_id}"' in DASHBOARD_HTML for element_id in metric_ids)
    for field in (
        "input_sample_count",
        "valid_input_sample_count",
        "excluded_invalid_ultrasonic_samples",
        "evaluated_sample_count",
        "truncated_valid_sample_count",
        "evaluation_truncated",
        "result_row_count",
        "preview_row_count",
        "preview_row_limit",
        "preview_sampling_policy",
        "evaluated_samples_sha256",
    ):
        assert field in DASHBOARD_HTML
    assert (
        "OOD, min/max, and mean risk are aggregated from all evaluated rows"
        in DASHBOARD_HTML
    )


def test_legacy_operator_labelled_workspace_is_folded_and_train_button_retired() -> (
    None
):
    assert (
        '<details class="retired-workspace" id="legacySimulationArchive" open>'
        in DASHBOARD_HTML
    )
    assert "Ultrasonic sensor collection" in DASHBOARD_HTML
    assert 'id="trainSimulationModel"' not in DASHBOARD_HTML
    assert "/api/v1/simulations/train" not in DASHBOARD_HTML
    assert 'id="simulationSessionList"' in DASHBOARD_HTML
    assert 'id="sessionDeletionStatus"' in DASHBOARD_HTML


def test_new_admin_routes_are_prefixed_and_keep_csrf_protection() -> None:
    rendered = render_dashboard(api_prefix="/admin", admin_mode=True)
    required = (
        "/admin/api/v1/official-datasets",
        "/admin/api/v1/official-training/readiness?",
        "/admin/api/v1/official-training/runs",
        "/admin/api/v1/official-model",
        "/admin/api/v1/sensor-test/device-profile",
        "/admin/api/v1/sensor-test/runs",
    )
    assert all(route in rendered for route in required)
    assert "headers['X-CSRF-Token']=adminCsrfToken" in rendered
    assert "if(!ADMIN_MODE){ $('officialTrainingConsole').hidden=true" in rendered


def test_new_consoles_are_keyboard_and_responsive_friendly() -> None:
    assert ":focus-visible" in DASHBOARD_HTML
    assert ".console-grid { grid-template-columns:1fr; }" in DASHBOARD_HTML
    assert 'aria-labelledby="officialTrainingHeading"' in DASHBOARD_HTML
    assert 'aria-labelledby="sensorExternalTestHeading"' in DASHBOARD_HTML
    assert 'aria-live="polite"' in DASHBOARD_HTML
    assert 'type="button"' in DASHBOARD_HTML


def test_dashboard_embedded_javascript_has_valid_syntax() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    match = re.search(r"<script>(.*)</script>", DASHBOARD_HTML, re.DOTALL)
    assert match is not None
    result = subprocess.run(
        [node, "--check", "-"],
        input=match.group(1).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
