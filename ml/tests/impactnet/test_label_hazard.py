from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from coastwatch_impact.data.labels import build_hazard_label


def _event(
    *,
    confidence: str = "A",
    onset: str = "2025-01-01T05:00:00Z",
    end: str = "2025-01-01T08:00:00Z",
    precision: str = "exact_hour",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_id": "e1",
                "storm_group_id": "storm1",
                "coastal_zone_id": "z1",
                "onset_time_utc": onset,
                "peak_time_utc": "2025-01-01T06:00:00Z",
                "end_time_utc": end,
                "onset_precision": precision,
                "label_confidence": confidence,
            }
        ]
    )


def test_exact_onset_builds_right_closed_lead_and_risk_set_mask() -> None:
    label = build_hazard_label("2025-01-01T00:00:00Z", "z1", _event())
    assert label.hazard_target[4] == 1.0
    assert label.hazard_target.sum() == 1.0
    assert label.hazard_mask[:5].all()
    assert not label.hazard_mask[5:].any()


def test_active_event_masks_entire_primary_onset_head() -> None:
    label = build_hazard_label("2025-01-01T06:00:00Z", "z1", _event())
    assert label.active_event
    assert not label.hazard_mask.any()


def test_unknown_interval_is_masked_not_converted_to_negative() -> None:
    unknown = _event(
        confidence="U",
        onset="2025-01-01T03:00:00Z",
        end="2025-01-01T06:00:00Z",
        precision="interval",
    )
    label = build_hazard_label(
        "2025-01-01T00:00:00Z",
        "z1",
        unknown,
        known_negative_mask=True,
    )
    assert np.all(label.hazard_target == 0.0)
    assert not label.hazard_mask[2:6].any()


def test_warningnet_can_train_only_from_precise_c_warning_evidence() -> None:
    warning = _event(confidence="C")
    label = build_hazard_label(
        "2025-01-01T00:00:00Z",
        "z1",
        warning,
        positive_confidences=("C",),
        label_mode="official_warning",
    )
    assert label.hazard_target[4] == 1.0
    assert label.hazard_mask[:5].all()

    with pytest.raises(ValueError, match="confirmed_impact primary head"):
        build_hazard_label(
            "2025-01-01T00:00:00Z",
            "z1",
            warning,
            positive_confidences=("C",),
            label_mode="confirmed_impact",
        )
