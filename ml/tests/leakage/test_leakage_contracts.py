"""Adversarial tests for the five non-negotiable leakage boundaries."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from coastwatch_impact.data.preprocessing import (
    TrainingDataLeakageError,
    TrainOnlyPreprocessor,
    assert_train_only_provenance,
)
from coastwatch_impact.data.split import SplitLeakageError, audit_group_exclusivity
from coastwatch_impact.data.temporal import (
    ForecastLeakageError,
    ObservationLeakageError,
    assert_observation_history,
    select_asof_forecast,
)
from coastwatch_impact.evaluation import fit_global_temperature


def test_future_issued_forecast_is_rejected() -> None:
    forecasts = pd.DataFrame(
        {
            "site_id": ["zone-1"],
            "issue_time_utc": ["2025-01-01T05:00:00Z"],
            "valid_time_utc": ["2025-01-01T06:00:00Z"],
            "source_model": ["fixture"],
            "model_run_id": ["future-run"],
        }
    )
    with pytest.raises(ForecastLeakageError, match="future-issued"):
        select_asof_forecast(
            forecasts,
            "2025-01-01T04:00:00Z",
            "2025-01-01T06:00:00Z",
            site_id="zone-1",
            reject_future_only=True,
        )


def test_future_observation_is_rejected() -> None:
    observations = pd.DataFrame({"timestamp_utc": ["2025-01-01T03:00:00Z", "2025-01-01T05:00:00Z"]})
    with pytest.raises(ObservationLeakageError):
        assert_observation_history(observations, "2025-01-01T04:00:00Z")


def test_event_or_storm_group_cannot_cross_train_and_test() -> None:
    injected = pd.DataFrame(
        {
            "event_id": ["event-1", "event-1"],
            "storm_group_id": ["storm-1", "storm-1"],
            "split": ["train", "test"],
        }
    )
    with pytest.raises(SplitLeakageError, match="multiple splits"):
        audit_group_exclusivity(injected)


def test_preprocessor_rejects_full_data_fit_and_manifest_mismatch() -> None:
    mixed = pd.DataFrame({"split": ["train", "test"], "x": [0.0, 100.0]})
    with pytest.raises(TrainingDataLeakageError, match="only split='train'"):
        TrainOnlyPreprocessor(["x"]).fit(mixed)

    train = pd.DataFrame({"split": ["train", "train"], "x": [0.0, 1.0]})
    preprocessor = TrainOnlyPreprocessor(["x"], dataset_manifest_hash="a" * 64).fit(train)
    with pytest.raises(TrainingDataLeakageError, match="manifest differs"):
        assert_train_only_provenance(
            preprocessor,
            expected_dataset_manifest_hash="b" * 64,
        )


def test_test_split_cannot_be_used_for_calibration() -> None:
    with pytest.raises(ValueError, match="validation-only"):
        fit_global_temperature(
            np.array([[0.0, 1.0]]),
            np.array([[0.0, 1.0]]),
            split="test",
        )
