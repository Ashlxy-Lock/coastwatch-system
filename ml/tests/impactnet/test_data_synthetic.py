from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from coastwatch_impact.data.dataset import CoastWatchWindowDataset, collate_window_batch
from coastwatch_impact.data.synthetic import (
    build_synthetic_sample_index,
    generate_synthetic_dataset,
)


def test_synthetic_generator_is_deterministic_and_explicitly_marked() -> None:
    first = generate_synthetic_dataset(duration_days=5, seed=42)
    second = generate_synthetic_dataset(duration_days=5, seed=42)
    assert len(first.sites) == 3
    pd.testing.assert_frame_equal(first.observations_hourly, second.observations_hourly)
    pd.testing.assert_frame_equal(first.forecasts_hourly, second.forecasts_hourly)
    assert first.metadata["synthetic_data"] is True
    assert first.metadata["scientific_use_allowed"] is False
    assert first.observations_hourly["quality_flag"].isin(["stale"]).any()
    assert first.observations_hourly["significant_wave_height_m"].isna().any()
    assert (
        pd.to_datetime(first.forecasts_hourly["valid_time_utc"], utc=True)
        > pd.to_datetime(first.forecasts_hourly["issue_time_utc"], utc=True)
    ).all()


def test_synthetic_bundle_writes_auditable_marker(tmp_path: Path) -> None:
    bundle = generate_synthetic_dataset(duration_days=5)
    output = bundle.write(tmp_path / "synthetic")
    marker = json.loads((output / "SYNTHETIC_ONLY.json").read_text(encoding="utf-8"))
    assert marker["synthetic_data"] is True
    assert marker["scientific_use_allowed"] is False
    assert set(marker["tables"]) == set(bundle.tables)


def test_window_dataset_returns_72h_history_and_24h_issued_forecast() -> None:
    bundle = generate_synthetic_dataset(duration_days=10)
    index = build_synthetic_sample_index(bundle, stride_hours=12)
    dataset = CoastWatchWindowDataset(
        bundle.observations_hourly,
        bundle.forecasts_hourly,
        bundle.static_features,
        index,
        past_feature_names=[
            "water_level_m_aod",
            "significant_wave_height_m",
            "wind_speed_m_s",
        ],
        future_feature_names=[
            "forecast_total_water_level_m_aod",
            "forecast_wave_height_m",
            "forecast_wind_speed_m_s",
        ],
        static_feature_names=["ground_elevation_m_aod", "defence_crest_height_m_aod"],
    )
    sample = dataset[0]
    assert sample["past_values"].shape == (72, 3)
    assert sample["past_mask"].shape == (72, 3)
    assert sample["future_values"].shape == (24, 3)
    assert sample["future_mask"].shape == (24, 3)
    assert sample["lead_features"].shape == (24, 5)
    assert sample["physics_baseline"].shape == (24,)
    assert sample["physics_mask"].shape == (24,)
    assert sample["hazard_target"].shape == (24,)
    assert sample["hazard_mask"].shape == (24,)
    assert sample["prediction_time_utc"].tzinfo is not None

    batch = collate_window_batch([dataset[0], dataset[1]])
    assert batch["past_values"].shape == (2, 72, 3)
    assert batch["physics_baseline"].shape == (2, 24)
    assert batch["warning_target"] is None
    assert len(batch["site_id"]) == 2
