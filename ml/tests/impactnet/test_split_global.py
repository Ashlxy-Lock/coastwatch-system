from __future__ import annotations

import pandas as pd
import pytest

from coastwatch_impact.data.split import (
    GlobalSplitConfig,
    SplitLeakageError,
    assign_global_time_split,
    audit_group_exclusivity,
)
from coastwatch_impact.data.synthetic import (
    build_synthetic_sample_index,
    default_synthetic_split_config,
    generate_synthetic_dataset,
)


def _config() -> GlobalSplitConfig:
    return GlobalSplitConfig(
        train_end_utc="2025-01-10T00:00:00Z",
        validation_end_utc="2025-01-20T00:00:00Z",
        test_end_utc="2025-01-30T00:00:00Z",
        forecast_horizon_hours=24,
    )


def test_global_split_purges_target_window_at_boundaries_for_all_sites() -> None:
    samples = pd.DataFrame(
        {
            "site_id": ["s1", "s2", "s1", "s2"],
            "prediction_time_utc": [
                "2025-01-09T00:00:00Z",  # target ends exactly at train boundary
                "2025-01-09T12:00:00Z",  # crosses train boundary
                "2025-01-11T00:00:00Z",
                "2025-01-19T12:00:00Z",  # crosses validation boundary
            ],
        }
    )
    split = assign_global_time_split(samples, _config())
    assert split["split"].tolist() == ["train", "purged", "validation", "purged"]


def test_event_and_storm_group_cannot_cross_splits() -> None:
    samples = pd.DataFrame(
        {
            "event_id": ["e1", "e1"],
            "storm_group_id": ["storm", "storm"],
            "split": ["train", "test"],
        }
    )
    with pytest.raises(SplitLeakageError, match="multiple splits"):
        audit_group_exclusivity(samples)


def test_event_buffer_purges_correlated_rows_from_adjacent_split() -> None:
    config = _config().model_copy(update={"event_buffer_hours": 48})
    samples = pd.DataFrame(
        {
            "site_id": ["s1", "s2", "s1"],
            "prediction_time_utc": [
                "2025-01-08T12:00:00Z",
                "2025-01-09T00:00:00Z",
                "2025-01-11T00:00:00Z",
            ],
            "storm_group_id": [None, None, "storm-validation"],
        }
    )
    split = assign_global_time_split(samples, config)
    assert split["split"].tolist() == ["train", "purged", "validation"]
    assert split.loc[1, "split_purge_reason"] == "storm_group_id_buffer_crosses_split"


def test_short_synthetic_fixture_keeps_a_nonzero_audited_event_buffer() -> None:
    bundle = generate_synthetic_dataset(duration_days=12)
    default_config = default_synthetic_split_config(bundle)
    smoke_config = default_config.model_copy(update={"event_buffer_hours": 24})

    unbuffered = build_synthetic_sample_index(
        bundle,
        split_config=default_config.model_copy(update={"event_buffer_hours": 0}),
        stride_hours=1,
    )
    buffered = build_synthetic_sample_index(
        bundle,
        split_config=smoke_config,
        stride_hours=1,
    )

    assert default_config.event_buffer_hours == 72
    assert smoke_config.event_buffer_hours == 24
    assert len(buffered) < len(unbuffered)
    assert set(buffered["split"].astype(str)) == {"train", "validation", "test"}
    assert audit_group_exclusivity(buffered)["valid"] is True
