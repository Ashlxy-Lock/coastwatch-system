from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from coastwatch_impact.data.spatial import (
    SpatialMappingError,
    build_site_mapping_review,
    validate_site_mappings,
)

ML_ROOT = Path(__file__).resolve().parents[2]


def test_review_uses_exact_legacy_sites_and_approves_none():
    frame = build_site_mapping_review(
        ML_ROOT / "coastal_risk" / "locations.json",
        ML_ROOT / "configs" / "sites.yaml",
    )
    assert len(frame) == 6
    brighton = frame.set_index("site_id").loc["uk_brighton"]
    assert brighton["latitude"] == pytest.approx(50.82838)
    assert brighton["longitude"] == pytest.approx(-0.13947)
    assert frame["approved"].sum() == 0
    assert validate_site_mappings(frame) == []


def _approved() -> pd.DataFrame:
    frame = build_site_mapping_review(
        ML_ROOT / "coastal_risk" / "locations.json",
        ML_ROOT / "configs" / "sites.yaml",
    )
    index = frame.index[frame["site_id"] == "uk_brighton"][0]
    updates = {
        "approved": True,
        "coastal_zone_id": "reviewed-zone",
        "warning_area_code": "reviewed-area",
        "tide_station_id": "reviewed-tide",
        "tide_station_distance_m": 1000.0,
        "wave_station_id": "reviewed-wave",
        "wave_station_distance_m": 20_000.0,
        "exposure_coast_id": "south-coast-a",
        "wave_station_exposure_coast_id": "south-coast-a",
        "coast_type": "open_coast",
        "tide_station_coast_type": "open_coast",
        "wave_direction_reference": "true",
        "wave_direction_convention": "coming_from",
        "selection_reason": "Reviewed test fixture only.",
        "reviewer": "test-reviewer",
    }
    for column, value in updates.items():
        frame.at[index, column] = value
    return frame


def test_approved_mapping_requires_same_exposed_coast():
    frame = _approved()
    assert [record.site_id for record in validate_site_mappings(frame)] == ["uk_brighton"]
    frame.loc[frame["site_id"] == "uk_brighton", "wave_station_exposure_coast_id"] = "other"
    with pytest.raises(SpatialMappingError, match="different exposed coast"):
        validate_site_mappings(frame)


def test_approved_mapping_rejects_unresolved_direction_metadata():
    frame = _approved()
    frame.loc[frame["site_id"] == "uk_brighton", "wave_direction_reference"] = "unknown"
    with pytest.raises(SpatialMappingError, match="direction metadata"):
        validate_site_mappings(frame)
