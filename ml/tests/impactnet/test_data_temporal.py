from __future__ import annotations

import pandas as pd
import pytest

from coastwatch_impact.data.temporal import (
    ForecastLeakageError,
    ObservationLeakageError,
    assert_observation_history,
    select_asof_forecast,
)


def _forecasts() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "site_id": "s1",
                "issue_time_utc": "2025-01-01T00:00:00Z",
                "valid_time_utc": "2025-01-01T06:00:00Z",
                "source_model": "m",
                "model_run_id": "old",
                "value": 1.0,
            },
            {
                "site_id": "s1",
                "issue_time_utc": "2025-01-01T03:00:00Z",
                "valid_time_utc": "2025-01-01T06:00:00Z",
                "source_model": "m",
                "model_run_id": "latest-available",
                "value": 2.0,
            },
            {
                "site_id": "s1",
                "issue_time_utc": "2025-01-01T05:00:00Z",
                "valid_time_utc": "2025-01-01T06:00:00Z",
                "source_model": "m",
                "model_run_id": "future-at-t",
                "value": 99.0,
            },
        ]
    )


def test_asof_forecast_uses_latest_issue_available_at_prediction_time() -> None:
    row = select_asof_forecast(
        _forecasts(),
        "2025-01-01T04:00:00Z",
        "2025-01-01T06:00:00Z",
        site_id="s1",
    )
    assert row is not None
    assert row["model_run_id"] == "latest-available"
    assert row["value"] == 2.0


def test_future_only_forecast_can_be_rejected_for_leakage_audit() -> None:
    with pytest.raises(ForecastLeakageError, match="only future-issued"):
        select_asof_forecast(
            _forecasts().tail(1),
            "2025-01-01T04:00:00Z",
            "2025-01-01T06:00:00Z",
            site_id="s1",
            reject_future_only=True,
        )


def test_future_observation_is_rejected_from_history() -> None:
    observations = pd.DataFrame({"timestamp_utc": ["2025-01-01T03:00:00Z", "2025-01-01T05:00:00Z"]})
    with pytest.raises(ObservationLeakageError):
        assert_observation_history(observations, "2025-01-01T04:00:00Z")
