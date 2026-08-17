from __future__ import annotations

import pandas as pd

from coastwatch_impact.data import sample_training_rows


def _row(
    time: str,
    split: str,
    *,
    positive: bool = False,
    event_id: str | None = None,
) -> dict[str, object]:
    target = [0.0] * 24
    if positive:
        target[0] = 1.0
    return {
        "site_id": "s1",
        "prediction_time_utc": time,
        "split": split,
        "hazard_target": target,
        "hazard_mask": [True] * 24,
        "event_id": event_id,
        "sample_weight": 1.0,
    }


def test_train_sampling_keeps_all_positive_and_continuous_evaluation_rows() -> None:
    rows = [
        _row("2025-01-01T00:00:00Z", "train", positive=True, event_id="e1"),
        _row("2025-01-01T01:00:00Z", "train", positive=True, event_id="e1"),
        *[_row(f"2025-01-02T{hour:02d}:00:00Z", "train") for hour in range(12)],
        *[_row(f"2025-02-01T{hour:02d}:00:00Z", "validation") for hour in range(6)],
        *[_row(f"2025-03-01T{hour:02d}:00:00Z", "test") for hour in range(6)],
    ]
    sampled = sample_training_rows(
        pd.DataFrame(rows),
        negative_min_spacing_hours=3,
        negative_to_positive_target_ratio=2.0,
    )
    train = sampled[sampled["split"].eq("train")]
    positive = train[train["event_id"].eq("e1")]
    assert len(positive) == 2
    assert positive["sample_weight"].sum() == 1.0
    assert len(train) == 6  # two positives plus a maximum of four negatives
    assert sampled["split"].eq("validation").sum() == 6
    assert sampled["split"].eq("test").sum() == 6
