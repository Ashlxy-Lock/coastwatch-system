from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.simulation_model import (
    DEFAULT_MODEL_ID,
    FEATURE_ORDER,
    SIMULATED_ENVIRONMENT_FEATURE_ORDER,
    ULTRASONIC_FEATURE_ORDER,
    WINDOW_EPOCH_FIELD,
    SimulationModelError,
    assess_simulation_training_data,
    build_simulation_features,
    load_simulation_model,
    predict_simulation,
    train_simulation_model,
)

FIXED_CREATED_AT = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)


def simulated_environment(session_index: int) -> dict[str, float]:
    return {
        "sim_air_temperature_c": 8.0 + session_index,
        "sim_humidity_percent": 60.0 + session_index,
        "sim_wind_speed_kmh": 10.0 + session_index * 2.0,
        "sim_wave_height_m": 0.5 + session_index * 0.2,
        "sim_wave_period_s": 4.0 + session_index * 0.3,
        "sim_water_temperature_c": 11.0 + session_index * 0.4,
        "sim_sea_level_height_m": -0.5 + session_index * 0.1,
        "sim_ocean_current_velocity_kmh": 0.5 + session_index * 0.1,
        "sim_hour_sin": -0.8 + session_index * 0.2,
        "sim_hour_cos": 0.8 - session_index * 0.15,
        "sim_day_of_year_sin": -0.6 + session_index * 0.15,
        "sim_day_of_year_cos": 0.7 - session_index * 0.12,
        "sim_latitude": 50.0,
        "sim_longitude": -1.0,
    }


def simulation_rows(*, sessions: int = 6) -> list[dict]:
    rows: list[dict] = []
    for session_index in range(sessions):
        baseline = 1_000.0 + session_index * 2.0
        for sample_index in range(12):
            dangerous = sample_index >= 6
            water_rise = (
                8.0 + sample_index * 1.5 + session_index
                if not dangerous
                else 85.0 + (sample_index - 6) * 18.0 + session_index
            )
            rows.append(
                {
                    "session_id": f"session-{session_index}",
                    "received_at": f"2026-08-14T09:{session_index:02d}:{sample_index:02d}Z",
                    "distance_mm": baseline - water_rise,
                    "baseline_mm": baseline,
                    "water_rise_mm": water_rise,
                    "rise_rate_mm_s": 1.5 if not dangerous else 18.0,
                    "label": "safe" if not dangerous else "danger",
                    **simulated_environment(session_index),
                }
            )
        rows.append(
            {
                "session_id": f"session-{session_index}",
                "received_at": f"2026-08-14T09:{session_index:02d}:13Z",
                "distance_mm": baseline - 40.0,
                "baseline_mm": baseline,
                "water_rise_mm": 40.0,
                "rise_rate_mm_s": 0.0,
                "label": "unknown",
                **simulated_environment(session_index),
            }
        )
    return rows


def test_training_is_deterministic_for_fixed_seed_and_timestamp():
    first = train_simulation_model(
        simulation_rows(), random_state=19, created_at=FIXED_CREATED_AT
    )
    second = train_simulation_model(
        simulation_rows(), random_state=19, created_at=FIXED_CREATED_AT
    )
    assert first == second
    assert first["model_id"] == DEFAULT_MODEL_ID
    assert first["data_kind"] == "simulation"
    assert first["deployment_mode"] == "shadow"
    assert first["metrics"]["excluded_unknown_samples"] == 6
    assert (
        first["source_manifest"]["dataset_hash"]
        == second["source_manifest"]["dataset_hash"]
    )
    assert first["training_config"]["window_size"] == 5
    assert first["training_config"]["random_state"] == 19
    assert first["training_config"]["penalty"] == "l2"
    assert first["training_config"]["l1_ratio"] == 0.0
    assert first["training_config"]["regularization_C"] == 1.0
    assert first["training_config"]["fit_intercept"] is True
    assert first["training_config"]["sample_weight_strategy"].startswith("equal_total")
    assert first["training_config"]["split_strategy"] == (
        "whole_session_and_simulated_environment_group_holdout"
    )


def test_holdout_is_by_whole_session_with_no_neighbour_leakage():
    artifact = train_simulation_model(
        simulation_rows(), test_fraction=0.34, created_at=FIXED_CREATED_AT
    )
    train_sessions = set(artifact["metrics"]["train_sessions"])
    test_sessions = set(artifact["metrics"]["test_sessions"])
    assert train_sessions
    assert test_sessions
    assert train_sessions.isdisjoint(test_sessions)
    assert train_sessions | test_sessions == {f"session-{index}" for index in range(6)}
    assert artifact["metrics"]["split_strategy"] == (
        "whole_session_and_simulated_environment_group_holdout"
    )
    assert artifact["metrics"]["session_overlap"] == []
    assert artifact["metrics"]["scenario_group_overlap"] == []


def test_identical_environment_vectors_are_kept_on_one_side_of_holdout():
    rows = simulation_rows(sessions=8)
    for row in rows:
        session_index = int(str(row["session_id"]).rsplit("-", 1)[1])
        row.update(simulated_environment(session_index // 2))

    artifact = train_simulation_model(
        rows, test_fraction=0.34, created_at=FIXED_CREATED_AT
    )
    train_sessions = set(artifact["metrics"]["train_sessions"])
    test_sessions = set(artifact["metrics"]["test_sessions"])
    environment_groups = artifact["source_manifest"][
        "simulated_environment_feature_groups"
    ]

    for left_index in range(0, 8, 2):
        pair = {f"session-{left_index}", f"session-{left_index + 1}"}
        assert pair <= train_sessions or pair <= test_sessions
        assert (
            environment_groups[f"session-{left_index}"]
            == environment_groups[f"session-{left_index + 1}"]
        )
    assert artifact["metrics"]["scenario_group_overlap"] == []


def test_single_scenario_uses_explicit_session_holdout_and_limits_claims():
    rows = simulation_rows(sessions=2)
    for row in rows:
        row.update(simulated_environment(0))

    artifact = train_simulation_model(rows, created_at=FIXED_CREATED_AT)
    metrics = artifact["metrics"]

    assert metrics["split_strategy"] == "whole_session_holdout_single_scenario"
    assert metrics["session_overlap"] == []
    assert len(metrics["scenario_group_overlap"]) == 1
    assert metrics["scenario_group_overlap_expected"] is True
    assert metrics["scenario_generalization_evaluable"] is False
    assert metrics["environment_effects_learnable"] is False
    assert artifact["source_manifest"]["scenario_generalization_evaluable"] is False
    assert artifact["training_config"]["environment_effects_learnable"] is False


def test_window_epoch_resets_features_without_changing_session_identity():
    rows = [
        {
            "session_id": "session-reset",
            "received_at": "2026-08-14T09:00:00Z",
            "distance_mm": 1_000,
            "baseline_mm": 1_000,
            "water_rise_mm": 0,
            "rise_rate_mm_s": 0,
            WINDOW_EPOCH_FIELD: 0,
            **simulated_environment(0),
        },
        {
            "session_id": "session-reset",
            "received_at": "2026-08-14T09:00:01Z",
            "distance_mm": 980,
            "baseline_mm": 1_000,
            "water_rise_mm": 20,
            "rise_rate_mm_s": 20,
            WINDOW_EPOCH_FIELD: 0,
            **simulated_environment(0),
        },
        {
            "session_id": "session-reset",
            "received_at": "2026-08-14T09:00:03Z",
            "distance_mm": 900,
            "baseline_mm": 1_000,
            "water_rise_mm": 100,
            "rise_rate_mm_s": 7,
            WINDOW_EPOCH_FIELD: 1,
            **simulated_environment(0),
        },
    ]

    features = build_simulation_features(rows, window_size=5)

    assert {row["session_id"] for row in rows} == {"session-reset"}
    assert features["water_rise_delta_mm"] == 0
    assert features["water_rise_slope_mm_s"] == 7
    assert features["water_rise_rolling_mean_mm"] == 100
    assert features["water_rise_rolling_std_mm"] == 0
    assert len(features) == 22


def test_rule_baseline_is_fitted_on_training_sessions_only():
    rows = simulation_rows()
    first = train_simulation_model(
        rows, test_fraction=0.34, created_at=FIXED_CREATED_AT
    )
    test_sessions = set(first["metrics"]["test_sessions"])
    changed_test_rows = [dict(row) for row in rows]
    for row in changed_test_rows:
        if row["session_id"] in test_sessions:
            row["water_rise_mm"] = float(row["water_rise_mm"]) + 50_000.0
            row["distance_mm"] = float(row["distance_mm"]) - 50_000.0

    second = train_simulation_model(
        changed_test_rows, test_fraction=0.34, created_at=FIXED_CREATED_AT
    )
    first_baseline = first["metrics"]["baselines"]["water_rise_threshold"]
    second_baseline = second["metrics"]["baselines"]["water_rise_threshold"]
    assert first_baseline["selection"]["fit_on"] == "train_sessions_only"
    assert first_baseline["selection"]["sample_weighting"] == "equal_session"
    assert first_baseline["threshold_mm"] == second_baseline["threshold_mm"]
    assert first["metrics"]["test_sessions"] == second["metrics"]["test_sessions"]
    assert "balanced_accuracy" in first["metrics"]["delta_vs_baseline"]
    assert first["metrics"]["delta_vs_baseline"]["brier_score_reduction"] is None
    assert (
        first_baseline["probabilistic_metrics_supported"] is False
        and first_baseline["test"]["brier_score"] is None
    )
    assert set(first["metrics"]["baselines"]) == {
        "water_rise_threshold",
        "ultrasonic_only_logistic_regression",
        "environment_only_logistic_regression",
    }
    assert "roc_auc" in first["metrics"]["test"]


def test_single_class_is_rejected_fail_closed():
    rows = simulation_rows()
    for row in rows:
        if row["label"] != "unknown":
            row["label"] = "safe"
    with pytest.raises(SimulationModelError, match="both safe and danger"):
        train_simulation_model(rows, created_at=FIXED_CREATED_AT)


def test_holdout_is_blocked_when_train_and_test_cannot_both_have_both_classes():
    rows = simulation_rows(sessions=2)
    for row in rows:
        if row["label"] != "unknown":
            row["label"] = "safe" if row["session_id"] == "session-0" else "danger"

    assessment = assess_simulation_training_data(rows)

    assert assessment["ready"] is False
    assert assessment["planned_split"] is None
    assert any(
        "both classes in both training and test" in blocker
        for blocker in assessment["blockers"]
    )


def test_json_artifact_roundtrip_and_hash_validation(tmp_path: Path):
    destination = tmp_path / "custom-water-logreg-v1.json"
    artifact = train_simulation_model(
        simulation_rows(), output_path=destination, created_at=FIXED_CREATED_AT
    )
    model = load_simulation_model(destination)
    assert model.model_id == artifact["model_id"]
    assert model.artifact_hash == artifact["hash"]
    assert json.loads(destination.read_text(encoding="utf-8")) == artifact

    tampered = dict(artifact)
    tampered["intercept"] += 1.0
    with pytest.raises(SimulationModelError, match="hash mismatch"):
        load_simulation_model(tampered)


def test_pure_python_prediction_has_probabilities_features_and_quality(tmp_path: Path):
    artifact_path = tmp_path / "model.json"
    train_simulation_model(
        simulation_rows(), output_path=artifact_path, created_at=FIXED_CREATED_AT
    )
    latest_window = [
        row for row in simulation_rows() if row["session_id"] == "session-0"
    ][-6:-1]
    result = predict_simulation(artifact_path, latest_window)
    assert result["predicted_label"] in {"safe", "danger"}
    assert 0.0 <= result["danger_probability"] <= 1.0
    assert 0.0 <= result["safe_probability"] <= 1.0
    assert result["safe_probability"] + result["danger_probability"] == pytest.approx(
        1.0
    )
    assert result["quality"] == "ok"
    assert result["quality_details"]["window_complete"] is True
    assert result["window_size"] == 5
    assert result["feature_order"] == list(FEATURE_ORDER)
    assert len(result["feature_vector"]) == len(FEATURE_ORDER)
    assert list(FEATURE_ORDER[:8]) == list(ULTRASONIC_FEATURE_ORDER)
    assert list(FEATURE_ORDER[8:]) == list(SIMULATED_ENVIRONMENT_FEATURE_ORDER)


def test_short_prediction_window_is_explicitly_lower_quality():
    artifact = train_simulation_model(simulation_rows(), created_at=FIXED_CREATED_AT)
    row = simulation_rows()[0]
    result = predict_simulation(artifact, row)
    assert result["quality"] == "limited_history"
    assert result["quality_details"] == {
        "status": "limited_history",
        "samples_available": 1,
        "samples_required": 5,
        "window_complete": False,
        "uses_reported_or_derived_rate": True,
    }
