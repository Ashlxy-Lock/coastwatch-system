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


def test_dashboard_consumes_server_side_dataset_and_readiness_contracts() -> None:
    required_routes = (
        "/api/v1/simulations/overview?device_id=",
        "/timeline?device_id=",
        "/api/v1/simulations/training-readiness?device_id=",
        "/api/v1/simulations/model",
        "/api/v1/simulations/train",
    )
    assert all(route in DASHBOARD_HTML for route in required_routes)
    assert "不会在浏览器端猜测或放宽训练要求" in DASHBOARD_HTML
    assert "未标注/清除的样本保持 UNKNOWN" in DASHBOARD_HTML
    assert "trainingReadinessUrl(sessionIds,version)" in DASHBOARD_HTML
    assert "&session_id=${encodeURIComponent(sessionId)}" in DASHBOARD_HTML


def test_dashboard_exposes_all_returned_core_evaluation_metrics() -> None:
    metric_fields = (
        "balanced_accuracy",
        "danger_precision",
        "danger_recall",
        "danger_f1",
        "specificity",
        "negative_predictive_value",
        "roc_auc",
        "false_positive_rate",
        "false_negative_rate",
        "decision_threshold",
        "brier_score",
        "log_loss",
        "true_safe",
        "false_danger",
        "false_safe",
        "true_danger",
    )
    assert all(field in DASHBOARD_HTML for field in metric_fields)
    assert "metrics.baselines?.water_rise_threshold" in DASHBOARD_HTML
    assert "Baseline unavailable" in DASHBOARD_HTML


def test_dashboard_keeps_simulation_research_boundary_visible() -> None:
    assert "RESEARCH" in DASHBOARD_HTML
    assert "SIMULATION DATA" in DASHBOARD_HTML
    assert "SHADOW ONLY" in DASHBOARD_HTML
    assert "模型输出也不代表真实海岸灾害概率" in DASHBOARD_HTML
    assert "不会按水位阈值自动生成灾害标签" in DASHBOARD_HTML


def test_failed_training_can_be_retried_when_server_readiness_stays_true() -> None:
    assert (
        "button.disabled=!simulationReadiness?.ready"
        "||!selectedTrainingSessionIdsInOrder().length" in DASHBOARD_HTML
    )


def test_dashboard_requires_explicit_completed_session_selection() -> None:
    required_ids = (
        "trainingSelectionCount",
        "selectAllTrainingSessions",
        "clearTrainingSessionSelection",
    )
    assert all(f'id="{element_id}"' in DASHBOARD_HTML for element_id in required_ids)
    assert "let selectedTrainingSessionIds=new Set()" in DASHBOARD_HTML
    assert "session.state==='completed'" in DASHBOARD_HTML
    assert "checkbox.type='checkbox'" in DASHBOARD_HTML
    assert "不会默认使用全部数据" in DASHBOARD_HTML
    assert (
        "if(!sessionIds.length){ renderNoTrainingSelection(); return; }"
        in DASHBOARD_HTML
    )
    assert "session_ids:sessionIds" in DASHBOARD_HTML


def test_dashboard_deletes_only_completed_unused_sessions_with_confirmation() -> None:
    assert 'id="sessionDeletionHelp"' in DASHBOARD_HTML
    assert 'id="sessionDeletionStatus"' in DASHBOARD_HTML
    assert 'role="status" aria-live="polite"' in DASHBOARD_HTML
    assert "只能删除已结束且尚未被任何训练模型工件使用的会话" in DASHBOARD_HTML
    assert "已被训练工件使用的会话为保证模型可追溯性不可删除" in DASHBOARD_HTML
    assert "if(session.state!=='completed')" in DASHBOARD_HTML
    assert "deleteButton.disabled=true; deleteButton.textContent='采集中，不能删除'" in DASHBOARD_HTML
    assert "pendingSessionDeletionExpiresAt=Date.now()+5000" in DASHBOARD_HTML
    assert "window.setTimeout" in DASHBOARD_HTML
    assert "function sessionDeletionDescriptor(session)" in DASHBOARD_HTML
    assert "会话 ID ${session.session_id}，${formatCount(session.sample_count)} 个样本" in DASHBOARD_HTML
    assert "确认删除 …${shortSessionId} · ${formatCount(session.sample_count)} 样本（5 秒内）" in DASHBOARD_HTML
    assert "deleteButton.setAttribute('aria-pressed',String(pending))" in DASHBOARD_HTML
    assert "确认永久删除 ${session.name}，会话 ID ${session.session_id}" in DASHBOARD_HTML
    assert "删除已结束会话 ${session.name}，会话 ID ${session.session_id}" in DASHBOARD_HTML


def test_dashboard_uses_session_delete_contract_and_refreshes_all_views() -> None:
    route = (
        "/api/v1/simulations/sessions/${encodeURIComponent(session.session_id)}"
        "?device_id=${encodeURIComponent(DEVICE)}"
    )
    assert route in DASHBOARD_HTML
    assert f"sendJson(`{route}`,'DELETE',undefined)" in DASHBOARD_HTML
    assert "selectedTrainingSessionIds.delete(session.session_id)" in DASHBOARD_HTML
    assert "simulationRequestSerial+=1; selectedSimulationSession=null; emptyTimeline()" in DASHBOARD_HTML
    assert "正在刷新总览、时间线和训练条件" in DASHBOARD_HTML
    assert "await loadSimulationSessions({throwOnError:true})" in DASHBOARD_HTML
    assert "if(throwOnError) throw error" in DASHBOARD_HTML
    assert "删除成功但刷新失败，将自动重试" in DASHBOARD_HTML
    assert "result?.deleted_counts" in DASHBOARD_HTML
    assert "result?.detached_telemetry_count" in DASHBOARD_HTML
    assert "is referenced by training artifact" in DASHBOARD_HTML
    assert "该会话已被训练模型工件使用，为保持模型可追溯性不可删除" in DASHBOARD_HTML
    assert "cannot be verified; session deletion is blocked" in DASHBOARD_HTML


def test_dashboard_displays_server_selection_and_training_provenance() -> None:
    assert "readiness?.selection" in DASHBOARD_HTML
    assert "selection.selected_session_ids" in DASHBOARD_HTML
    assert "selection.effective_session_ids" in DASHBOARD_HTML
    assert "selection.requested_session_ids" in DASHBOARD_HTML
    assert "selection.selection_hash" in DASHBOARD_HTML
    assert "选择 provenance" in DASHBOARD_HTML


def test_gateway_admin_dashboard_uses_one_prefixed_api_and_csrf_contract() -> None:
    rendered = render_dashboard(api_prefix="/admin", admin_mode=True)
    assert "const ADMIN_MODE=true" in rendered
    assert 'const ADMIN_BASE="/admin"' in rendered
    assert "/admin/api/v1/simulations/overview?device_id=" in rendered
    assert "/admin/api/v1/simulations/train" in rendered
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
    assert all(f'id="{field}"' in DASHBOARD_HTML for field in required_fields)
    assert "/api/v1/simulations/device-scenario?device_id=" in DASHBOARD_HTML
    assert "sendJson('/api/v1/simulations/device-scenario','PUT'" in DASHBOARD_HTML
    assert "'DELETE',undefined" in DASHBOARD_HTML
    assert "先在这里保存并激活场景，再到 ESP32 点击 START" in DASHBOARD_HTML
    assert "服务器不会改用 Open-Meteo 或最近一次会话" in DASHBOARD_HTML
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
    assert all(bounds in DASHBOARD_HTML for bounds in expected_bounds)


def test_dashboard_marks_manual_environment_as_simulated() -> None:
    assert "e.source==='manual'?'SIMULATED · OPERATOR-SUPPLIED'" in DASHBOARD_HTML
    assert "海平面 ${metric(e.sea_level_height_m,' m',3)}" in DASHBOARD_HTML


def test_dashboard_shows_immutable_session_scenario_provenance() -> None:
    assert "/scenario?device_id=${DEVICE}`)" in DASHBOARD_HTML
    assert "IMMUTABLE SNAPSHOT" in DASHBOARD_HTML
    assert "scenario.scenario_hash" in DASHBOARD_HTML
    assert "SIMULATED / OPERATOR-SUPPLIED" in DASHBOARD_HTML
    assert "水位来源：DEVICE-MEASURED" in DASHBOARD_HTML
    assert "CUSTOM PREDICTION BLOCKED · NO ACTIVE SIMULATED SCENARIO" in DASHBOARD_HTML


def test_dashboard_documents_22_features_and_dynamic_training_readiness() -> None:
    assert "合计 8 + 8 + 6 = 22 项" in DASHBOARD_HTML
    assert "至少 2 个独立已结束会话" in DASHBOARD_HTML
    assert "训练集和测试集都同时含 SAFE/DANGER" in DASHBOARD_HTML
    assert "仅用于 COURSE DEMO 的建议，不再是固定训练门槛" in DASHBOARD_HTML
    assert "30 个会话是 STRONGER DEMO 建议" in DASHBOARD_HTML
    assert "有效实验样本量以独立会话为主" in DASHBOARD_HTML
    assert "scenario_configured_session_count" in DASHBOARD_HTML
    assert "scenario_distinct_values" in DASHBOARD_HTML
    assert "quality.eligible_class_counts||counts" in DASHBOARD_HTML
    assert "可训练 SAFE" in DASHBOARD_HTML
    assert "独立 14-feature 场景" in DASHBOARD_HTML
    assert "独立场景 hash" not in DASHBOARD_HTML


def test_dashboard_explains_evidence_tiers_and_single_scenario_scope() -> None:
    assert "BLOCKED / EXPLORATORY / COURSE DEMO / STRONGER DEMO" in DASHBOARD_HTML
    assert "readiness?.evidence_quality" in DASHBOARD_HTML
    assert "evidence.criteria" in DASHBOARD_HTML
    assert "evidence.evaluation_scope" in DASHBOARD_HTML
    assert "evidence.scenario_generalization_evaluable" in DASHBOARD_HTML
    assert "evidence.environment_effects_learnable" in DASHBOARD_HTML
    assert "single_scenario_session_holdout" in DASHBOARD_HTML
    assert "SINGLE SCENARIO" in DASHBOARD_HTML
    assert "环境效应与环境系数不可解释" in DASHBOARD_HTML
    assert "readiness.planned_split.strategy" in DASHBOARD_HTML
    assert "readiness.planned_split.scenario_group_overlap" in DASHBOARD_HTML
    assert "readiness.planned_split.train_scenario_group_count" in DASHBOARD_HTML
    assert "readiness.planned_split.environment_effects_learnable" in DASHBOARD_HTML


def test_dashboard_compares_combined_model_with_two_ablations_and_threshold() -> None:
    assert "Combined Logistic Regression · 22 features" in DASHBOARD_HTML
    assert "Ultrasonic-only Logistic Ablation · 8 features" in DASHBOARD_HTML
    assert "Environment-only Logistic Ablation · 14 features" in DASHBOARD_HTML
    assert "ultrasonic_only_logistic_regression" in DASHBOARD_HTML
    assert "environment_only_logistic_regression" in DASHBOARD_HTML
    assert "delta_vs_ultrasonic_only" in DASHBOARD_HTML
    assert "delta_vs_environment_only" in DASHBOARD_HTML
    assert "metrics.delta_vs_baseline" in DASHBOARD_HTML


def test_hard_threshold_baseline_omits_probability_metrics_and_deltas() -> None:
    assert (
        "twoLevelMetricSummary(baselineMetrics,baselineSessionMacro,false)"
        in DASHBOARD_HTML
    )
    assert "compactDeltaSummary(delta,false)" in DASHBOARD_HTML
    assert "compactDeltaSummary(metrics.delta_vs_baseline,false)" in DASHBOARD_HTML
    assert "hard classifier · Brier / log loss / ROC AUC 不适用" in DASHBOARD_HTML
    assert "'brier_score_reduction'" in DASHBOARD_HTML
    assert "'log_loss_reduction'" in DASHBOARD_HTML
    assert (
        "twoLevelMetricSummary(ultrasonicTest,ultrasonicSessionMacro)" in DASHBOARD_HTML
    )
    assert (
        "twoLevelMetricSummary(environmentTest,environmentSessionMacro)"
        in DASHBOARD_HTML
    )


def test_dashboard_exposes_session_macro_as_primary_scientific_view() -> None:
    assert "metrics.test_session_macro" in DASHBOARD_HTML
    assert "ultrasonicAblation?.test_session_macro" in DASHBOARD_HTML
    assert "environmentAblation?.test_session_macro" in DASHBOARD_HTML
    assert "baseline?.test_session_macro" in DASHBOARD_HTML
    assert "row-level:" in DASHBOARD_HTML
    assert "session-macro (PRIMARY SCIENTIFIC VIEW" in DASHBOARD_HTML
    assert "Row-level deltas only" in DASHBOARD_HTML


def test_dashboard_imports_json_or_one_row_csv_without_automatic_upload() -> None:
    assert "function parseScenarioImport(text)" in DASHBOARD_HTML
    assert "JSON.parse(trimmed)" in DASHBOARD_HTML
    assert "CSV 必须恰好包含一行表头和一行数据" in DASHBOARD_HTML
    assert "尚未上传" in DASHBOARD_HTML
    assert (
        "$('importScenario').addEventListener('click',importScenarioIntoForm)"
        in DASHBOARD_HTML
    )


def test_prefixed_admin_dashboard_rewrites_simulated_scenario_routes() -> None:
    rendered = render_dashboard(api_prefix="/admin", admin_mode=True)
    assert "/admin/api/v1/simulations/device-scenario?device_id=" in rendered
    assert (
        "/admin/api/v1/simulations/sessions/${encodeURIComponent(sessionId)}"
        "/scenario?device_id=" in rendered
    )


def test_dashboard_has_separate_official_training_and_sensor_test_consoles() -> None:
    assert 'id="officialTrainingConsole"' in DASHBOARD_HTML
    assert 'id="sensorExternalTestConsole"' in DASHBOARD_HTML
    assert "英国官方海岸模型训练台" in DASHBOARD_HTML
    assert "超声波线性映射外部测试台" in DASHBOARD_HTML
    assert "SENSOR ROWS USED FOR FIT = 0" in DASHBOARD_HTML
    assert "SCALER = 0" in DASHBOARD_HTML
    assert "THRESHOLD = 0" in DASHBOARD_HTML
    assert "ESP32 不参与训练" in DASHBOARD_HTML


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
    assert "时间切分只能来自受审计 manifest" in DASHBOARD_HTML
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
    assert "selectedOfficialSiteIds().forEach(siteId=>params.append('site_id',siteId))" in DASHBOARD_HTML
    assert "sendJson('/api/v1/official-training/runs','POST',officialTrainingPayload())" in DASHBOARD_HTML
    assert "activateOfficialRun" in DASHBOARD_HTML


def test_rescan_surfaces_http_200_bundle_errors_and_preserves_old_selection() -> None:
    assert 'id="officialRescanStatus"' in DASHBOARD_HTML
    assert "HTTP 200 不等于所有 bundle 注册成功" in DASHBOARD_HTML
    assert "const scan=await sendJson('/api/v1/official-datasets/rescan','POST',undefined)" in DASHBOARD_HTML
    assert "scan?.error_count" in DASHBOARD_HTML
    assert "item?.bundle" in DASHBOARD_HTML
    assert "item?.detail||item?.message" in DASHBOARD_HTML
    assert "const fullyAccepted=errorCount===0" in DASHBOARD_HTML
    assert "扫描完成但有 ${formatCount(errorCount)} 个数据包被拒绝" in DASHBOARD_HTML
    assert "loadOfficialDatasets({preserveSelection:true})" in DASHBOARD_HTML
    assert "原选择 ${previousSelection} 已明确保留" in DASHBOARD_HTML
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
    assert "不是事件级告警数" in DASHBOARD_HTML
    assert "metrics.decision_threshold" in DASHBOARD_HTML
    assert "Water-level Threshold Baseline" in DASHBOARD_HTML
    assert "Persistence Baseline" in DASHBOARD_HTML
    assert "baselines.water_level_threshold||baselines.threshold" in DASHBOARD_HTML
    assert "baselines.persistence" in DASHBOARD_HTML


def test_site_selection_changes_evidence_scope_without_browser_side_training_threshold() -> None:
    assert 'id="officialEvidenceScope"' in DASHBOARD_HTML
    assert "1 站点 · EXPLORATORY / SINGLE-COAST" in DASHBOARD_HTML
    assert "2 站点 · PRELIMINARY" in DASHBOARD_HTML
    assert "COURSE DEMO / MULTI-COAST" in DASHBOARD_HTML
    assert "NOT ACTIVATABLE" in DASHBOARD_HTML
    assert "至少 3 站点" in DASHBOARD_HTML
    assert "每个 split 至少 200 行" in DASHBOARD_HTML
    assert "浏览器只显示服务器策略，不自行放宽门槛" in DASHBOARD_HTML
    assert "officialReadiness?.ready" in DASHBOARD_HTML


def test_official_console_keeps_site_macro_primary_and_row_level_companion_separate() -> None:
    assert "const macro=completeMacroCoverage?macroCandidate:null" in DASHBOARD_HTML
    assert "const macroMetric=name=>macro?metricText(macro[name]):'N/A'" in DASHBOARD_HTML
    assert "eligible / selected" in DASHBOARD_HTML
    assert "site-macro (PRIMARY) N/A" in DASHBOARD_HTML
    assert "Row-level companion (NOT PRIMARY)" in DASHBOARD_HTML
    assert "macro.pr_auc??metrics.pr_auc" not in DASHBOARD_HTML
    assert "run.activatable!==false&&completeMacroCoverage" in DASHBOARD_HTML


def test_official_console_discloses_provenance_limits_at_every_stage() -> None:
    assert "operator_attested_raw_hash_verified" in DASHBOARD_HTML
    assert "deterministic_importer_replay_verified=false" in DASHBOARD_HTML
    assert "服务器验证 raw 原始字节、SHA-256 与 manifest 结构" in DASHBOARD_HTML
    assert "官方归属、许可和标签派生仍由操作者声明" in DASHBOARD_HTML
    assert 'id="officialDatasetProvenance"' in DASHBOARD_HTML
    assert 'id="officialRunProvenance"' in DASHBOARD_HTML
    assert 'id="officialArtifactProvenance"' in DASHBOARD_HTML
    assert "run.source_manifest?.provenance_limitation" in DASHBOARD_HTML


def test_threshold_baseline_is_validation_selected_and_per_site() -> None:
    assert "Water-level Threshold Baseline · validation-selected per-site" in DASHBOARD_HTML
    assert "threshold.threshold_selection_split" in DASHBOARD_HTML
    assert "thresholds_by_site" in DASHBOARD_HTML
    assert "threshold.per_site_frozen_test" in DASHBOARD_HTML
    assert "threshold.selected_site_coverage" in DASHBOARD_HTML
    assert "thresholdSelectionSplit" in DASHBOARD_HTML
    assert "thresholdCoverage" in DASHBOARD_HTML
    assert "thresholdMacro?metricText(thresholdMacro.recall):'N/A'" in DASHBOARD_HTML
    assert "train-only" not in DASHBOARD_HTML


def test_professor_facing_baseline_verdict_is_server_sourced_and_probability_safe() -> None:
    assert 'id="officialBaselineVerdict"' in DASHBOARD_HTML
    assert "run?.metrics?.delta_vs_water_level_threshold" in DASHBOARD_HTML
    assert "界面不自行发明" in DASHBOARD_HTML
    assert "ML improves baseline" in DASHBOARD_HTML
    assert "No demonstrated improvement; prefer simple rule" in DASHBOARD_HTML
    assert "classification-only" in DASHBOARD_HTML
    assert "不将 Brier / ROC AUC / PR AUC 与硬阈值对比" in DASHBOARD_HTML
    assert "Object.entries(labels)" in DASHBOARD_HTML
    assert "pr_auc:'" not in DASHBOARD_HTML
    assert "comparison.available===false||!comparison.verdict" in DASHBOARD_HTML
    assert "不会用 row-level 或 eligible-subset 指标代替主结论" in DASHBOARD_HTML


def test_official_model_language_does_not_claim_disaster_probability() -> None:
    assert "Extreme sea-level condition probability" in DASHBOARD_HTML
    assert "不是海啸、洪水或自然灾害概率" in DASHBOARD_HTML
    assert "模型输出，不是灾害概率" in DASHBOARD_HTML
    assert "不能代替真实海岸水文仪器的现场验证" in DASHBOARD_HTML


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
    assert "FORMAL — 由独立校准会话和官方训练分位数派生" in DASHBOARD_HTML
    assert "EXPLORATORY — 手动 gain，不进入正式指标" in DASHBOARD_HTML
    assert "payload.calibration_session_id" in DASHBOARD_HTML
    assert "payload.manual_gain" in DASHBOARD_HTML
    assert "payload.manual_reference_level_m" in DASHBOARD_HTML
    assert "EXCLUDED FROM FORMAL METRICS" in DASHBOARD_HTML
    assert "source_manifest?.frozen_sensor_contexts" in DASHBOARD_HTML
    assert "18 features = 1 个被传感器替换的水位特征 + 17 个官方冻结上下文值" in DASHBOARD_HTML
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


def test_official_sensor_contract_lists_all_18_features_without_residual_duplicate() -> None:
    assert "18-feature contract" in DASHBOARD_HTML
    assert "1 个可由传感器线性映射替换的相对水位 + 17 个冻结官方上下文" in DASHBOARD_HTML
    for feature_label in (
        "预测潮位",
        "有效波高",
        "波周期",
        "风速",
        "阵风",
        "气压",
        "降雨",
        "气温",
        "相对湿度",
        "水温",
        "流速",
        "小时 sin/cos",
        "年周期 sin/cos",
        "纬度",
        "经度",
    ):
        assert feature_label in DASHBOARD_HTML
    assert "surge_residual" not in DASHBOARD_HTML


def test_sensor_console_displays_affine_mapping_raw_units_and_ood_without_clipping() -> None:
    assert "mapped_level_m = reference_level_m + gain × (water_rise_mm / 1000)" in DASHBOARD_HTML
    assert 'id="sensorRawRise"' in DASHBOARD_HTML
    assert 'id="sensorMappedLevel"' in DASHBOARD_HTML
    assert 'id="sensorOodState"' in DASHBOARD_HTML
    assert "OUT-OF-DISTRIBUTION 不会被裁剪" in DASHBOARD_HTML
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
    assert "{device_id:DEVICE,context_id:$('sensorContextId').value,mode}" in DASHBOARD_HTML
    assert "{device_id:DEVICE,session_id:sessionId}" in DASHBOARD_HTML
    assert "模型参数、标准化器和阈值均不会改变" in DASHBOARD_HTML


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
    assert "OOD、min/max 与风险均值聚合自全部 evaluated rows，不由 preview 反推" in DASHBOARD_HTML


def test_legacy_operator_labelled_workspace_is_folded_and_train_button_retired() -> None:
    legacy_index = DASHBOARD_HTML.index('id="legacySimulationArchive"')
    legacy_train_index = DASHBOARD_HTML.index('id="trainSimulationModel"')
    assert legacy_train_index > legacy_index
    assert '<details class="retired-workspace" id="legacySimulationArchive">' in DASHBOARD_HTML
    assert "Legacy archive" in DASHBOARD_HTML
    assert "RETIRED FROM PRIMARY TRAINING" in DASHBOARD_HTML
    assert re.search(
        r'<button id="trainSimulationModel"[^>]*hidden[^>]*disabled',
        DASHBOARD_HTML,
    )
    assert "旧版训练已从主流程退役" in DASHBOARD_HTML
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
