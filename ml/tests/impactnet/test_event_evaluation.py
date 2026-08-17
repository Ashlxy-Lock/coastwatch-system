from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from coastwatch_impact.evaluation import (
    evaluate_alert_events,
    match_alerts_to_events,
    merge_alert_episodes,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def prediction_frame() -> pd.DataFrame:
    probabilities = {0: 0.8, 3: 0.7, 10: 0.9, 15: 0.7, 23: 0.8}
    return pd.DataFrame(
        {
            "site_id": ["synthetic-a"] * 25,
            "prediction_time_utc": [START + timedelta(hours=i) for i in range(25)],
            "event_probability": [probabilities.get(i, 0.05) for i in range(25)],
        }
    )


def event_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["event-1", "event-2", "date-only"],
            "storm_group_id": ["storm-1", "storm-2", "storm-x"],
            "site_id": ["synthetic-a"] * 3,
            "onset_time_utc": [
                START + timedelta(hours=5),
                START + timedelta(hours=12),
                START + timedelta(hours=24),
            ],
            "impact_confirmed": [True, True, True],
            "label_confidence": ["A", "B", "A"],
            "onset_precision": ["exact_hour", "exact_hour", "date_only"],
        }
    )


def test_gap_and_cooldown_merge_alert_hours_into_episodes() -> None:
    episodes = merge_alert_episodes(prediction_frame(), 0.5, merge_gap_hours=2, cooldown_hours=6)
    assert len(episodes) == 3
    assert episodes["above_threshold_hours"].tolist() == [2, 2, 1]
    assert episodes.iloc[0]["start_time_utc"] == START
    assert episodes.iloc[0]["end_time_utc"] == START + timedelta(hours=3)
    assert episodes.iloc[1]["end_time_utc"] == START + timedelta(hours=15)


def test_events_match_once_and_only_to_pre_onset_alerts() -> None:
    episodes = merge_alert_episodes(prediction_frame(), 0.5)
    result = match_alerts_to_events(episodes, event_frame(), lookahead_hours=24)
    assert len(result.event_matches) == 2  # date-only evidence is not timed truth
    assert result.event_matches["detected"].tolist() == [True, True]
    assert result.event_matches["lead_time_hours"].tolist() == [5.0, 2.0]
    assert result.episode_matches["matched_event_id"].notna().sum() == 2
    assert len(result.false_episodes) == 1


def test_complete_event_evaluation_counts_false_episode_per_site_month() -> None:
    result = evaluate_alert_events(prediction_frame(), event_frame(), 0.5)
    assert result.metrics["confirmed_events"] == 2
    assert result.metrics["detected_events"] == 2
    assert result.metrics["false_alert_episodes"] == 1
    assert result.metrics["event_recall"] == pytest.approx(1.0)
    assert result.metrics["event_precision"] == pytest.approx(2 / 3)
    assert result.metrics["median_lead_time_hours"] == pytest.approx(3.5)
    assert result.metrics["evaluated_site_months"] == 1
    assert result.metrics["false_alert_episodes_per_site_month"] == pytest.approx(1.0)


def test_naive_prediction_times_are_rejected() -> None:
    predictions = prediction_frame()
    predictions["prediction_time_utc"] = predictions["prediction_time_utc"].dt.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        merge_alert_episodes(predictions, 0.5)
