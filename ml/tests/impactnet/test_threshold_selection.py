from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from coastwatch_impact.evaluation import select_operating_thresholds


def tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    alert_probabilities = {5: 0.80, 15: 0.50, 25: 0.75}
    predictions = pd.DataFrame(
        {
            "site_id": ["site-a"] * 40,
            "prediction_time_utc": [start + timedelta(hours=i) for i in range(40)],
            "event_probability": [alert_probabilities.get(i, 0.05) for i in range(40)],
            "split": ["validation"] * 40,
        }
    )
    events = pd.DataFrame(
        {
            "event_id": ["event-1", "event-2"],
            "storm_group_id": ["storm-1", "storm-2"],
            "site_id": ["site-a", "site-a"],
            "onset_time_utc": [
                start + timedelta(hours=10),
                start + timedelta(hours=30),
            ],
            "impact_confirmed": [True, True],
            "label_confidence": ["A", "A"],
            "onset_precision": ["exact_hour", "exact_hour"],
        }
    )
    return predictions, events


def test_selects_three_event_level_validation_operating_points() -> None:
    predictions, events = tables()
    result = select_operating_thresholds(
        predictions,
        events,
        split="validation",
        candidate_thresholds=(0.4, 0.7, 0.9),
        max_false_alert_episodes=0,
        conservative_minimum_recall=0.5,
    )
    selected = result["selected"]
    assert selected["sensitive"]["threshold"] == pytest.approx(0.7)
    assert selected["balanced"]["threshold"] == pytest.approx(0.7)
    assert selected["conservative"]["threshold"] == pytest.approx(0.7)
    assert all(row["constraint_met"] for row in selected.values())
    assert result["fitted_split"] == "validation"


def test_threshold_selection_rejects_test_or_mixed_predictions() -> None:
    predictions, events = tables()
    with pytest.raises(ValueError, match="validation"):
        select_operating_thresholds(predictions, events, split="test")
    predictions.loc[0, "split"] = "test"
    with pytest.raises(ValueError, match="non-validation"):
        select_operating_thresholds(predictions, events, split="validation")
