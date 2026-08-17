from __future__ import annotations

import json
import os

import pandas as pd
import pytest

from coastwatch_impact.data import sha256_file
from coastwatch_impact.export.model_bundle import load_model_bundle
from coastwatch_impact.synthetic_e2e import (
    SYNTHETIC_ONLY_NOTICE,
    SyntheticE2EConfig,
    run_synthetic_e2e,
)


def _fast_config() -> SyntheticE2EConfig:
    return SyntheticE2EConfig(
        duration_days=12,
        epochs=1,
        batch_size=32,
        train_stride_hours=6,
        # A 72h production-style embargo is longer than either 20% holdout in
        # this deliberately tiny fixture. Keep a real, auditable 24h embargo.
        event_buffer_hours=24,
        hidden_channels=4,
        num_blocks=1,
        kernel_size=2,
        decoder_hidden_dim=8,
        decoder_layers=1,
        lead_embedding_dim=2,
        temperature_iterations=16,
        threshold_candidates=(0.2, 0.5, 0.8),
    )


@pytest.fixture(scope="module")
def e2e_run(tmp_path_factory: pytest.TempPathFactory):
    output = tmp_path_factory.mktemp("synthetic-e2e") / "run"
    return run_synthetic_e2e(output, _fast_config())


def test_default_configuration_keeps_two_epoch_180_day_cpu_contract() -> None:
    config = SyntheticE2EConfig()
    assert config.duration_days == 180
    assert config.epochs == 2
    assert config.event_buffer_hours == 72
    assert config.device == "cpu"
    assert "not evidence" in SYNTHETIC_ONLY_NOTICE


@pytest.mark.full_e2e
def test_full_180_day_two_epoch_acceptance_when_enabled(tmp_path) -> None:
    if os.environ.get("COASTWATCH_FULL_E2E") != "1":
        pytest.skip("set COASTWATCH_FULL_E2E=1 in the scheduled/full CI job")
    result = run_synthetic_e2e(tmp_path / "full-acceptance", SyntheticE2EConfig())
    progress = json.loads(result.stage_timings_path.read_text(encoding="utf-8"))
    assert progress["status"] == "complete"
    assert result.api_status_code == 200
    assert load_model_bundle(result.bundle_directory).manifest["deployable_as_real"] is False


def test_synthetic_e2e_writes_frozen_auditable_artifacts(e2e_run) -> None:
    assert e2e_run.synthetic_only is True
    assert e2e_run.api_status_code == 200
    assert e2e_run.elapsed_seconds > 0.0
    progress = json.loads(e2e_run.stage_timings_path.read_text(encoding="utf-8"))
    assert progress["status"] == "complete"
    assert progress["current_stage"] is None
    stages = [row["stage"] for row in progress["completed_stages"]]
    assert "build_shared_lazy_dataset" in stages
    assert "train_obs_only_tcn" in stages
    assert "frozen_test_evaluation" in stages
    assert "shadow_api_smoke" in stages
    assert all(row["elapsed_seconds"] >= 0.0 for row in progress["completed_stages"])
    split_sensitivity = json.loads(
        (e2e_run.run_directory / "split_sensitivity.json").read_text(encoding="utf-8")
    )
    assert split_sensitivity["event_buffer_hours"] == 24
    assert split_sensitivity["target_purge_hours"] == 24
    assert all(
        split_sensitivity["primary_counts"].get(split_name, 0) > 0
        for split_name in ("train", "validation", "test")
    )
    metrics = json.loads(e2e_run.test_metrics_path.read_text(encoding="utf-8"))
    assert metrics["synthetic_only"] is True
    assert metrics["scientific_result"] is False
    assert metrics["frozen_test"] is True
    assert metrics["test_used_for_tuning"] is False
    assert metrics["calibration_fitted_split"] == "validation"
    assert metrics["thresholds_fitted_split"] == "validation"
    assert set(metrics["horizon_metrics"]) == {"1h", "3h", "6h", "12h", "24h"}
    required_run_artifacts = {
        "resolved_config.yaml",
        "dataset_manifest.json",
        "dataset_manifest.sha256",
        "git_state.json",
        "environment.json",
        "feature_schema.json",
        "label_schema.json",
        "preprocessing.json",
        "preprocessing_arrays.npz",
        "model.safetensors",
        "calibration.json",
        "thresholds.json",
        "validation_predictions.parquet",
        "test_predictions.parquet",
        "hourly_metrics.json",
        "event_metrics.json",
        "water_metrics.json",
        "bootstrap_intervals.json",
        "MODEL_CARD.md",
        "DATA_CARD.md",
        "LABEL_CARD.md",
        "run.log",
    }
    assert all((e2e_run.run_directory / name).is_file() for name in required_run_artifacts)
    label_schema = json.loads(
        (e2e_run.run_directory / "label_schema.json").read_text(encoding="utf-8")
    )
    assert label_schema["unknown_is_negative"] is False
    assert label_schema["synthetic_only"] is True

    validation = pd.read_parquet(e2e_run.validation_predictions_path)
    test = pd.read_parquet(e2e_run.test_predictions_path)
    assert validation["split"].eq("validation").all()
    assert test["split"].eq("test").all()
    assert validation["synthetic_only"].all()
    assert test["synthetic_only"].all()


def test_bundle_api_and_run_hash_inventory_remain_synthetic_only(e2e_run) -> None:
    bundle = load_model_bundle(e2e_run.bundle_directory)
    assert bundle.manifest["shadow_mode"] is True
    assert bundle.manifest["synthetic_data"] is True
    assert bundle.manifest["deployable_as_real"] is False
    assert bundle.manifest["model_name"] == "CoastWatch Synthetic-Test TCN"
    assert sha256_file(e2e_run.bundle_directory / "model.safetensors") == e2e_run.model_sha256

    smoke = json.loads(e2e_run.api_smoke_path.read_text(encoding="utf-8"))
    assert smoke["prediction_status_code"] == 200
    assert smoke["prediction"]["shadow_mode"] is True
    assert smoke["prediction"]["synthetic_data"] is True

    manifest = json.loads(e2e_run.run_manifest_path.read_text(encoding="utf-8"))
    assert manifest["synthetic_only"] is True
    assert manifest["scientific_use_allowed"] is False
    for relative, expected in manifest["files"].items():
        assert sha256_file(e2e_run.run_directory / relative) == expected
    checksum_line = (e2e_run.run_directory / "run_manifest.sha256").read_text("ascii")
    assert checksum_line.startswith(sha256_file(e2e_run.run_manifest_path))


def test_e2e_refuses_to_overwrite_a_completed_run(e2e_run) -> None:
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        run_synthetic_e2e(e2e_run.run_directory, SyntheticE2EConfig(duration_days=12))


def test_seed_reproduces_model_and_frozen_predictions(e2e_run, tmp_path) -> None:
    repeated = run_synthetic_e2e(tmp_path / "repeat", _fast_config())
    assert repeated.model_sha256 == e2e_run.model_sha256
    first = pd.read_parquet(e2e_run.test_predictions_path)
    second = pd.read_parquet(repeated.test_predictions_path)
    pd.testing.assert_frame_equal(first, second)
