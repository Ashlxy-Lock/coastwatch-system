from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from coastwatch_impact.data.quality import DatumMismatchError, compute_overtopping_margin
from coastwatch_impact.data.schemas import (
    EventCatalogRecord,
    ForecastRecord,
    LabelConfidence,
    ObservationRecord,
    SiteRecord,
)


def test_aware_bst_timestamp_is_normalised_without_changing_instant() -> None:
    record = ObservationRecord(
        site_id="s1",
        coastal_zone_id="z1",
        timestamp_utc="2025-07-01T13:00:00+01:00",
        water_level_m_aod=1.2,
        water_level_datum="mAOD",
    )
    assert record.timestamp_utc == datetime(2025, 7, 1, 12, tzinfo=UTC)


def test_naive_timestamp_and_invalid_crs_are_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ObservationRecord(
            site_id="s1",
            coastal_zone_id="z1",
            timestamp_utc="2025-01-01T00:00:00",
        )
    with pytest.raises(ValidationError, match="EPSG:4326"):
        SiteRecord(
            site_id="s1",
            coastal_zone_id="z1",
            site_name="Site",
            latitude=51.0,
            longitude=0.0,
            coordinate_reference_system="EPSG:27700",
        )


def test_forecast_issue_valid_and_lead_contract() -> None:
    valid = ForecastRecord(
        site_id="s1",
        issue_time_utc="2025-01-01T00:00:00Z",
        valid_time_utc="2025-01-01T06:00:00Z",
        lead_hours=6,
        source_model="issued-test",
        model_run_id="run-00",
    )
    assert valid.lead_hours == 6
    with pytest.raises(ValidationError, match="disagrees"):
        ForecastRecord(
            site_id="s1",
            issue_time_utc="2025-01-01T00:00:00Z",
            valid_time_utc="2025-01-01T06:00:00Z",
            lead_hours=5,
            source_model="issued-test",
            model_run_id="run-00",
        )


def test_a_label_requires_confirmation_and_human_review() -> None:
    common = dict(
        event_id="e1",
        coastal_zone_id="z1",
        onset_time_utc="2025-01-01T01:00:00Z",
        peak_time_utc="2025-01-01T02:00:00Z",
        end_time_utc="2025-01-01T03:00:00Z",
        onset_precision="exact_hour",
        impact_severity=2,
        label_confidence=LabelConfidence.A,
        primary_source="review",
        created_at_utc="2025-01-02T00:00:00Z",
        updated_at_utc="2025-01-02T00:00:00Z",
    )
    with pytest.raises(ValidationError, match="human review"):
        EventCatalogRecord(**common, impact_confirmed=True, human_reviewed=False)
    record = EventCatalogRecord(**common, impact_confirmed=True, human_reviewed=True)
    assert record.label_confidence == LabelConfidence.A


def test_vertical_difference_requires_compatible_known_datum() -> None:
    assert compute_overtopping_margin(3.1, "mAOD", 2.8, "mAOD") == pytest.approx(0.3)
    with pytest.raises(DatumMismatchError):
        compute_overtopping_margin(3.1, "local_station_datum", 2.8, "mAOD")
    with pytest.raises(DatumMismatchError):
        compute_overtopping_margin(3.1, "unknown", 2.8, "mAOD")
