from __future__ import annotations

import json
from pathlib import Path

import pytest

from coastwatch_impact.data.schemas import SchemaValidationError
from coastwatch_impact.data.sources import (
    FloodOutlinesManualAdapter,
    StaticGeoManualAdapter,
    WarningAreasManualAdapter,
    WaveNetManualArchiveAdapter,
)


def test_wavenet_does_not_guess_magnetic_direction(tmp_path: Path) -> None:
    source = tmp_path / "waves.csv"
    source.write_text(
        "station_id,timestamp_utc,hs,tp,wave_direction_deg\n"
        "buoy-1,2026-01-01T00:00:00Z,1.2,8.0,45\n",
        encoding="utf-8",
    )
    adapter = WaveNetManualArchiveAdapter(tmp_path / "data")
    raw = adapter.import_file(source).raw_path

    unknown = adapter.parse_csv(
        raw,
        direction_reference="magnetic",
        direction_convention="coming_from",
    )
    assert unknown.loc[0, "wave_direction_deg_true"] is None

    confirmed = adapter.parse_csv(
        raw,
        direction_reference="true",
        direction_convention="going_to",
    )
    assert confirmed.loc[0, "wave_direction_deg_true"] == 225.0


def test_geojson_adapters_keep_geometry_and_never_invent_hourly_onset(tmp_path: Path) -> None:
    areas_path = tmp_path / "areas.geojson"
    areas_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"warning_area_code": "AREA-1", "name": "Coast"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 50], [1, 50], [1, 51], [0, 50]]],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    area_adapter = WarningAreasManualAdapter(tmp_path / "data")
    areas = area_adapter.parse(area_adapter.import_file(areas_path).raw_path)
    assert areas.loc[0, "warning_area_code"] == "AREA-1"
    assert json.loads(areas.loc[0, "geometry_json"])["type"] == "Polygon"

    outlines_path = tmp_path / "outlines.geojson"
    payload = json.loads(areas_path.read_text(encoding="utf-8"))
    payload["features"][0]["properties"] = {
        "outline_id": "OUT-1",
        "event_date": "2025-12-31",
    }
    outlines_path.write_text(json.dumps(payload), encoding="utf-8")
    outline_adapter = FloodOutlinesManualAdapter(tmp_path / "data")
    outlines = outline_adapter.parse(outline_adapter.import_file(outlines_path).raw_path)
    assert outlines.loc[0, "onset_time_utc"] is None
    assert outlines.loc[0, "onset_precision"] == "date_only"
    assert bool(outlines.loc[0, "evidence_only"]) is True


def test_warning_areas_reject_explicit_non_wgs84_geojson(tmp_path: Path) -> None:
    source = tmp_path / "areas.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:27700"}},
                "features": [],
            }
        ),
        encoding="utf-8",
    )
    adapter = WarningAreasManualAdapter(tmp_path / "data")
    raw = adapter.import_file(source).raw_path
    with pytest.raises(ValueError, match="not WGS84"):
        adapter.parse(raw)


def test_static_geo_rejects_aod_height_without_aod_datum(tmp_path: Path) -> None:
    source = tmp_path / "static.csv"
    source.write_text(
        "coastal_zone_id,latitude,longitude,static_snapshot_date,vertical_datum,"
        "defence_crest_height_m_aod\n"
        "zone-1,50.8,-0.1,2026-01-01,unknown,4.2\n",
        encoding="utf-8",
    )
    adapter = StaticGeoManualAdapter(tmp_path / "data")
    with pytest.raises(SchemaValidationError, match="static fields require"):
        adapter.parse(adapter.import_file(source).raw_path)
