import numpy as np
import pandas as pd
import pytest

from coastwatch_impact.evaluation import (
    bootstrap_event_metrics,
    water_quantile_metrics,
)


def test_storm_group_bootstrap_is_reproducible_and_flags_small_evidence() -> None:
    matches = pd.DataFrame(
        {
            "storm_group_id": [f"storm-{i}" for i in range(4)],
            "detected": [True, False, True, True],
            "lead_time_hours": [8.0, np.nan, 4.0, 2.0],
        }
    )
    first = bootstrap_event_metrics(
        matches, n_resamples=100, seed=42, minimum_groups_for_evidence=5
    )
    second = bootstrap_event_metrics(
        matches, n_resamples=100, seed=42, minimum_groups_for_evidence=5
    )
    assert first == second
    assert first["bootstrap_unit"] == "storm_group_id"
    assert first["n_storm_groups"] == 4
    assert first["insufficient_evidence"] is True
    assert first["metrics"]["event_recall"]["estimate"] == pytest.approx(0.75)


def test_water_metrics_cover_quantiles_leads_and_peaks() -> None:
    target = np.array([[1.0, 2.0, 3.0], [2.0, 1.0, 0.0]])
    p50 = target + np.array([[0.1, -0.1, 0.0], [0.0, 0.2, 0.1]])
    predictions = np.stack((p50 - 0.5, p50, p50 + 0.5), axis=-1)
    mask = np.array([[True, True, True], [True, True, False]])
    result = water_quantile_metrics(target, predictions, mask, lead_hours=(1, 2, 3))
    assert result["valid_points"] == 5
    assert result["p50_mae"] == pytest.approx(0.08)
    assert result["p10_p90_empirical_coverage"] == pytest.approx(1.0)
    assert result["mean_interval_width"] == pytest.approx(1.0)
    assert result["peak_samples"] == 2
    assert result["by_lead"]["3h"]["valid_points"] == 1


def test_water_metrics_reject_crossing_quantiles() -> None:
    target = np.array([[1.0]])
    crossing = np.array([[[2.0, 1.0, 3.0]]])
    with pytest.raises(ValueError, match="cross"):
        water_quantile_metrics(target, crossing)
