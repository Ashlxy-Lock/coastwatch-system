from __future__ import annotations

import json
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pandas as pd

from coastwatch_impact.data import sha256_file
from coastwatch_impact.synthetic_e2e import (
    SyntheticE2EConfig,
    _write_run_manifest,
    replot_synthetic_run_artifacts,
    verify_synthetic_run_manifest,
)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _prediction_rows(split: str, start: pd.Timestamp) -> pd.DataFrame:
    event_onsets = {
        "synthetic_site_01": start + pd.Timedelta(hours=8),
        "synthetic_site_02": start + pd.Timedelta(hours=10),
    }
    rows: list[dict[str, object]] = []
    for site_number, (site_id, onset) in enumerate(event_onsets.items(), start=1):
        for hour in range(12):
            origin = start + pd.Timedelta(hours=hour)
            lead = int((onset - origin) / pd.Timedelta(hours=1))
            hazards = np.zeros(24, dtype=np.float64)
            if 1 <= lead <= 24:
                hazards[lead - 1] = 1.0
            final_probability = 0.85 if 2 <= lead <= 8 else 0.15
            cumulative_probability = np.linspace(
                min(0.04 + site_number * 0.01, final_probability),
                final_probability,
                24,
            )
            target = np.sin(np.arange(1, 25) / 3.0) + site_number * 0.1
            rows.append(
                {
                    "site_id": site_id,
                    "prediction_time_utc": origin,
                    "split": split,
                    "event_id": None,
                    "storm_group_id": None,
                    "event_probability": final_probability,
                    "hazard_logits": np.zeros(24).tolist(),
                    "cumulative_event_probability": cumulative_probability.tolist(),
                    "hazard_target": hazards.tolist(),
                    "hazard_mask": np.ones(24, dtype=bool).tolist(),
                    "water_p10": (target - 0.25).tolist(),
                    "water_p50": (target + 0.03).tolist(),
                    "water_p90": (target + 0.25).tolist(),
                    "water_target": target.tolist(),
                    "water_mask": np.ones(24, dtype=bool).tolist(),
                    "synthetic_only": True,
                }
            )
    return pd.DataFrame(rows)


def _make_run_fixture(root: Path) -> None:
    data = root / "data"
    data.mkdir(parents=True)
    start = pd.Timestamp("2025-06-01T00:00:00Z")
    validation = _prediction_rows("validation", start - pd.Timedelta(days=2))
    test = _prediction_rows("test", start)
    validation.to_parquet(root / "validation_predictions.parquet", index=False)
    test.to_parquet(root / "test_predictions.parquet", index=False)

    sites = pd.DataFrame(
        {
            "site_id": ["synthetic_site_01", "synthetic_site_02"],
            "coastal_zone_id": ["synthetic_zone_01", "synthetic_zone_02"],
            "synthetic_only": [True, True],
        }
    )
    sites.to_parquet(data / "sites.parquet", index=False)
    events = pd.DataFrame(
        {
            "event_id": ["synthetic_event_01", "synthetic_event_02"],
            "storm_group_id": ["synthetic_storm", "synthetic_storm"],
            "coastal_zone_id": ["synthetic_zone_01", "synthetic_zone_02"],
            "onset_time_utc": [start + pd.Timedelta(hours=8), start + pd.Timedelta(hours=10)],
            "onset_precision": ["exact_hour", "exact_hour"],
            "impact_confirmed": [True, True],
            "label_confidence": ["A", "B"],
            "synthetic_only": [True, True],
        }
    )
    events.to_parquet(data / "event_catalog.parquet", index=False)

    valid_times = pd.date_range(start - pd.Timedelta(days=2), periods=96, freq="h")
    observation_rows: list[dict[str, object]] = []
    forecast_rows: list[dict[str, object]] = []
    for site_id in sites["site_id"]:
        for index, timestamp in enumerate(valid_times):
            level = float(np.sin(index / 3.0))
            observation_rows.append(
                {
                    "site_id": site_id,
                    "timestamp_utc": timestamp,
                    "water_level_m_aod": level,
                    "quality_flag": "good",
                    "synthetic_only": True,
                }
            )
            forecast_rows.append(
                {
                    "site_id": site_id,
                    "issue_time_utc": start - pd.Timedelta(days=3),
                    "valid_time_utc": timestamp,
                    "forecast_total_water_level_m_aod": level + 0.08,
                    "quality_flag": (
                        "missing"
                        if site_id == "synthetic_site_01"
                        and timestamp == start + pd.Timedelta(hours=4)
                        else "synthetic_good"
                    ),
                    "synthetic_only": True,
                }
            )
    pd.DataFrame(observation_rows).to_parquet(data / "observations_hourly.parquet", index=False)
    pd.DataFrame(forecast_rows).to_parquet(data / "forecasts_hourly.parquet", index=False)

    reliability = [
        {
            "bin": 1,
            "lower": 0.1,
            "upper": 0.2,
            "count": 12,
            "mean_probability": 0.15,
            "observed_frequency": 0.0,
        },
        {
            "bin": 8,
            "lower": 0.8,
            "upper": 0.9,
            "count": 12,
            "mean_probability": 0.85,
            "observed_frequency": 0.5,
        },
    ]
    metrics: dict[str, object] = {
        "synthetic_only": True,
        "scientific_result": False,
        "horizon_metrics": {
            "1h": {"reliability_bins": reliability},
            "24h": {"reliability_bins": reliability},
        },
    }
    validation_metrics = {
        **metrics,
        "threshold_selection": {
            "selected": {"balanced": {"threshold": 0.5}},
        },
    }
    _write_json(root / "validation_metrics.json", validation_metrics)
    _write_json(root / "test_metrics.json", {**metrics, "frozen_test": True})
    _write_json(
        root / "training_history.json",
        {
            "synthetic_only": True,
            "epochs": [
                {"epoch": 1, "train_loss": 0.8, "validation_loss": 0.9},
                {"epoch": 2, "train_loss": 0.6, "validation_loss": 0.7},
            ],
        },
    )
    _write_json(
        root / "resolved_config.json",
        {
            "synthetic_only": True,
            "seed": 37,
            "duration_days": 12,
            "horizons_hours": [1, 24],
        },
    )


def test_replot_closes_appendix_b_and_preserves_run_sources(tmp_path: Path) -> None:
    run = tmp_path / "synthetic-run"
    _make_run_fixture(run)
    _write_run_manifest(
        run,
        SyntheticE2EConfig(seed=37, duration_days=12, horizons_hours=(1, 24)),
    )
    metrics_hash = sha256_file(run / "test_metrics.json")
    predictions_hash = sha256_file(run / "test_predictions.parquet")

    outputs = replot_synthetic_run_artifacts(run)

    expected = {
        "training_loss.png",
        "validation_pr_curve_1h.png",
        "validation_pr_curve_24h.png",
        "validation_reliability_1h.png",
        "validation_reliability_24h.png",
        "test_reliability_1h.png",
        "test_reliability_24h.png",
        "lead_time_distribution.png",
        "false_alerts_by_site.png",
        "event_recall_by_site.png",
        "water_error_by_lead.png",
        "water_interval_coverage.png",
        "prediction_timeline_examples/test_synthetic_site_01_synthetic_event_01.png",
        "prediction_timeline_examples/test_synthetic_site_02_synthetic_event_02.png",
        "plot_provenance.json",
    }
    relative_outputs = {path.relative_to(run / "plots").as_posix() for path in outputs}
    assert expected == relative_outputs
    for path in outputs:
        if path.suffix == ".png":
            image = mpimg.imread(path)
            assert path.stat().st_size > 5_000
            assert float(np.nanstd(image)) > 0.01

    provenance = json.loads((run / "plots" / "plot_provenance.json").read_text("utf-8"))
    assert provenance["synthetic_only"] is True
    assert provenance["official_warning_intervals"]["available"] is False
    assert "none were fabricated" in provenance["official_warning_intervals"]["reason"]
    assert sha256_file(run / "test_metrics.json") == metrics_hash
    assert sha256_file(run / "test_predictions.parquet") == predictions_hash
    verification = verify_synthetic_run_manifest(run, minimum_files=25)
    assert verification["verified"] is True
