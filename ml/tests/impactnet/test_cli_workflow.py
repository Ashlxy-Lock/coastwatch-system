from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from coastwatch_impact.cli import app

RUNNER = CliRunner()
ML_ROOT = Path(__file__).resolve().parents[2]


def _last_record(result: Any) -> dict[str, Any]:
    lines = [line for line in result.output.splitlines() if line.strip().startswith("{")]
    assert lines, result.output
    payload = json.loads(lines[-1])
    assert isinstance(payload, dict)
    return payload


def _invoke(arguments: list[str]) -> dict[str, Any]:
    result = RUNNER.invoke(app, arguments)
    assert result.exit_code == 0, result.output
    return _last_record(result)


@pytest.fixture()
def workflow_inputs(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "canonical-source"
    generated = _invoke(
        [
            "data",
            "synthetic",
            "--output",
            str(source),
            "--duration-days",
            "12",
            "--no-sample-index",
        ]
    )
    assert generated["synthetic_data"] is True

    marker = json.loads((source / "SYNTHETIC_ONLY.json").read_text(encoding="utf-8"))
    boundaries = marker["default_split_boundaries"]
    config_payload = yaml.safe_load(
        (ML_ROOT / "configs" / "synthetic_phase1.yaml").read_text(encoding="utf-8")
    )
    config_payload["split"].update(
        {
            "train_end": boundaries["train_end_utc"],
            "validation_end": boundaries["validation_end_utc"],
            "test_end": boundaries["test_end_utc"],
            "event_buffer_hours": 0,
            "target_purge_hours": 1,
        }
    )
    config_payload["windows"] = {
        "history_hours": 2,
        "forecast_hours": 1,
        "output_horizons_hours": [1],
    }
    config_payload["features"] = {
        "include_missing_masks": True,
        "include_source_age": True,
        "include_static_features": True,
        "use_site_embedding": False,
        "water_target_mode": "absolute",
    }
    config_payload["model"] = {
        "hidden_channels": 4,
        "num_blocks": 1,
        "kernel_size": 2,
        "dilations": [1],
        "dropout": 0.0,
        "decoder_hidden_dim": 8,
        "lead_embedding_dim": 2,
    }
    config_payload["training"].update(
        {
            "batch_size": 128,
            "max_epochs": 1,
            "early_stopping_patience": 1,
            "mixed_precision": False,
        }
    )
    config_payload["sampling"] = {
        "negative_min_spacing_hours": 6,
        "negative_to_positive_target_ratio": 2.0,
        "normalize_positive_weight_per_event": True,
    }
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")

    feature_schema = tmp_path / "feature-schema.json"
    feature_schema.write_text(
        json.dumps(
            {
                "past_feature_names": [
                    "water_level_m_aod",
                    "predicted_tide_m_aod",
                    "wind_speed_m_s",
                ],
                "future_feature_names": ["forecast_total_water_level_m_aod"],
                "static_feature_names": [
                    "latitude",
                    "longitude",
                    "ground_elevation_m_aod",
                ],
                "water_target_column": "water_level_m_aod",
                "physics_baseline_column": "forecast_total_water_level_m_aod",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "root": tmp_path,
        "source": source,
        "config": config,
        "feature_schema": feature_schema,
    }


def test_cli_label_and_dataset_contracts(workflow_inputs: dict[str, Path]) -> None:
    source = workflow_inputs["source"]
    root = workflow_inputs["root"]
    reviewed = source / "event_catalog.parquet"
    validated = _invoke(["labels", "validate", "--input", str(reviewed)])
    assert validated["valid"] is True

    canonical = root / "reviewed-events.parquet"
    built_labels = _invoke(
        [
            "labels",
            "build-event-catalog",
            "--input",
            str(reviewed),
            "--output",
            str(canonical),
        ]
    )
    assert built_labels["impact_events_inferred"] == 0

    dataset = root / "dataset"
    built = _invoke(
        [
            "dataset",
            "build",
            "--input",
            str(source),
            "--config",
            str(workflow_inputs["config"]),
            "--feature-schema",
            str(workflow_inputs["feature_schema"]),
            "--output",
            str(dataset),
        ]
    )
    assert built["synthetic_data"] is True
    samples = pd.read_parquet(dataset / "sample_index.parquet")
    train = samples[samples["split"].astype(str).eq("train")]
    positive = train[train["hazard_target"].map(lambda value: np.max(value) > 0.0)]
    negative = train.drop(positive.index)
    assert 0 < len(negative) <= 2 * len(positive)
    weighted = positive[positive["event_id"].notna()].groupby("event_id")["sample_weight"].sum()
    assert np.allclose(weighted.to_numpy(), 1.0)
    for group_column in ("event_id", "storm_group_id"):
        linked = samples[samples[group_column].notna()]
        assert linked.groupby(group_column)["split"].nunique().max() == 1

    summary = _invoke(["dataset", "summary", "--dataset", str(dataset)])
    assert summary["integrity_valid"] is True
    workflow_inputs["dataset"] = dataset


def test_cli_training_selection_final_test_and_export(
    workflow_inputs: dict[str, Path],
) -> None:
    root = workflow_inputs["root"]
    dataset = root / "dataset"
    _invoke(
        [
            "dataset",
            "build",
            "--input",
            str(workflow_inputs["source"]),
            "--config",
            str(workflow_inputs["config"]),
            "--feature-schema",
            str(workflow_inputs["feature_schema"]),
            "--output",
            str(dataset),
        ]
    )

    baseline = root / "baseline"
    baseline_result = _invoke(
        [
            "train",
            "baseline-logistic",
            "--dataset",
            str(dataset),
            "--config",
            str(workflow_inputs["config"]),
            "--output",
            str(baseline),
            "--dry-run",
        ]
    )
    assert baseline_result["status"] == "planned"
    assert baseline_result["model_kind"] == "baseline_logistic"
    assert not baseline.exists()

    hybrid_run = root / "hybrid-run"
    hybrid_trained = _invoke(
        [
            "train",
            "impactnet",
            "--dataset",
            str(dataset),
            "--config",
            str(workflow_inputs["config"]),
            "--output",
            str(hybrid_run),
            "--variant",
            "hybrid_tcn",
            "--max-epochs",
            "1",
            "--batch-size",
            "128",
        ]
    )
    assert hybrid_trained["variant"] == "hybrid_tcn"
    hybrid_history = json.loads((hybrid_run / "training_history.json").read_text(encoding="utf-8"))
    assert hybrid_history["sample_weight_distribution"]["positive"]["count"] > 0
    assert hybrid_history["sample_weight_distribution"]["negative"]["count"] > 0

    run = root / "obs-run"
    trained = _invoke(
        [
            "train",
            "impactnet",
            "--dataset",
            str(dataset),
            "--config",
            str(workflow_inputs["config"]),
            "--output",
            str(run),
            "--variant",
            "obs_only_tcn",
            "--max-epochs",
            "1",
            "--batch-size",
            "128",
        ]
    )
    assert trained["variant"] == "obs_only_tcn"

    calibration = _invoke(["calibrate", "temperature", "--run", str(run)])
    assert calibration["fitted_split"] == "validation"
    thresholds = _invoke(
        [
            "thresholds",
            "select",
            "--run",
            str(run),
            "--candidates",
            "0.2,0.5,0.8",
        ]
    )
    assert thresholds["fitted_split"] == "validation"
    evaluated = _invoke(
        [
            "evaluate",
            "run",
            "--run",
            str(run),
            "--calibration",
            str(run / "calibration.json"),
            "--thresholds",
            str(run / "thresholds.json"),
        ]
    )
    assert evaluated["split"] == "validation"
    final = _invoke(["evaluate", "final-test", "--run", str(run)])
    assert final["split"] == "test"

    bundle = root / "bundle"
    exported = _invoke(
        [
            "export",
            "bundle",
            "--run",
            str(run),
            "--output",
            str(bundle),
            "--model-version",
            "synthetic-test-v1",
            "--coverage-scope",
            "Synthetic engineering fixture only",
        ]
    )
    assert exported["shadow_mode"] is True
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["deployable_as_real"] is False
    _invoke(["export", "verify", "--bundle", str(bundle)])

    architecture = json.loads((bundle / "architecture.json").read_text(encoding="utf-8"))
    feature_contract = json.loads((bundle / "feature_schema.json").read_text(encoding="utf-8"))
    sites = json.loads((bundle / "sites.json").read_text(encoding="utf-8"))
    replay_input = root / "requests.jsonl"
    replay_input.write_text(
        json.dumps(
            {
                "site_id": sites[0]["site_id"],
                "prediction_time_utc": "2025-01-10T00:00:00Z",
                "past_values": [
                    [0.0] * architecture["past_feature_dim"]
                    for _ in range(architecture["history_hours"])
                ],
                "past_mask": [
                    [True] * architecture["past_feature_dim"]
                    for _ in range(architecture["history_hours"])
                ],
                "future_values": [
                    [0.0] * architecture["forecast_feature_dim"]
                    for _ in range(architecture["forecast_hours"])
                ],
                "future_mask": [
                    [True] * architecture["forecast_feature_dim"]
                    for _ in range(architecture["forecast_hours"])
                ],
                "static_values": [0.0] * architecture["static_feature_dim"],
                "future_time_features": [
                    [0.0] * architecture["time_feature_dim"]
                    for _ in range(architecture["forecast_hours"])
                ],
                "physics_baseline": [0.0] * architecture["forecast_hours"],
                "feature_manifest_hash": feature_contract["dataset_manifest_hash"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    replay_output = root / "replay.jsonl"
    replay = _invoke(
        [
            "replay",
            "shadow",
            "--bundle",
            str(bundle),
            "--input",
            str(replay_input),
            "--output",
            str(replay_output),
        ]
    )
    assert replay["response_count"] == 1
    assert replay_output.is_file()
    assert replay_output.with_suffix(".jsonl.manifest.json").is_file()

    second_final = RUNNER.invoke(app, ["evaluate", "final-test", "--run", str(run)])
    assert second_final.exit_code == 1
    assert _last_record(second_final)["status"] == "error"

    loso = root / "loso"
    loso_result = _invoke(
        [
            "evaluate",
            "leave-one-site-out",
            "--dataset",
            str(dataset),
            "--config",
            str(workflow_inputs["config"]),
            "--output",
            str(loso),
            "--variant",
            "obs_only_tcn",
            "--max-epochs",
            "1",
            "--batch-size",
            "128",
            "--dry-run",
        ]
    )
    assert loso_result["fold_count"] == 3
    assert loso_result["final_test_evaluated"] is False
    assert not loso.exists()


def test_cli_workflow_missing_inputs_fail_closed(tmp_path: Path) -> None:
    missing = RUNNER.invoke(
        app,
        [
            "train",
            "impactnet",
            "--dataset",
            str(tmp_path / "missing"),
            "--config",
            str(tmp_path / "missing.yaml"),
            "--output",
            str(tmp_path / "run"),
        ],
    )
    assert missing.exit_code == 1
    assert _last_record(missing)["error_type"] == "FileNotFoundError"

    replay = RUNNER.invoke(
        app,
        [
            "replay",
            "shadow",
            "--bundle",
            str(tmp_path / "missing-bundle"),
            "--input",
            str(tmp_path / "missing.jsonl"),
            "--output",
            str(tmp_path / "output.jsonl"),
        ],
    )
    assert replay.exit_code == 1
    assert _last_record(replay)["error_type"] == "FileNotFoundError"
