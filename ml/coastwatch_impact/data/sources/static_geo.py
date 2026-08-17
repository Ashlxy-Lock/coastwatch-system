"""Manual importer for reviewed static coastal-zone features."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ..schemas import validate_static_features_frame
from ._geo import assert_geojson_wgs84
from ._parsing import pick_column, read_tabular_payload
from .base import NoSourceDataError, SourceAdapter, json_dumps_canonical, payloads_from_file
from .registry import source_metadata


class StaticGeoManualAdapter(SourceAdapter):
    metadata = source_metadata("static_geo")
    parser_version = "static-geo-1"
    supported_suffixes = frozenset({".csv", ".json", ".geojson", ".zip"})

    OPTIONAL_NUMERIC_FIELDS = (
        "coastal_orientation_sin",
        "coastal_orientation_cos",
        "ground_elevation_m_aod",
        "defence_crest_height_m_aod",
        "distance_to_coast_m",
        "historic_flood_fraction",
        "low_lying_area_fraction",
        "road_exposure_count",
        "building_exposure_count",
    )

    def parse(
        self,
        raw_path: str | Path,
        *,
        source_version: str | None = None,
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for payload in payloads_from_file(
            raw_path,
            allowed_suffixes=(".csv", ".json", ".geojson"),
        ):
            if payload.name.lower().endswith((".geojson", ".json")):
                assert_geojson_wgs84(payload.payload, source_name=payload.name)
            frame = read_tabular_payload(payload)
            required = {
                "coastal_zone_id": pick_column(
                    frame,
                    ("coastal_zone_id", "warning_area_code", "zone_id"),
                    required=True,
                    meaning="coastal zone identifier",
                ),
                "latitude": pick_column(frame, ("latitude", "lat"), required=True),
                "longitude": pick_column(frame, ("longitude", "lon", "lng"), required=True),
                "static_snapshot_date": pick_column(
                    frame,
                    ("static_snapshot_date", "snapshot_date", "as_of_date"),
                    required=True,
                    meaning="static snapshot date",
                ),
                "vertical_datum": pick_column(
                    frame,
                    ("vertical_datum", "datum"),
                    required=True,
                    meaning="vertical datum",
                ),
            }
            for raw in frame.to_dict(orient="records"):
                row: dict[str, Any] = {
                    name: raw[column]
                    for name, column in required.items()  # type: ignore[index]
                }
                for field in self.OPTIONAL_NUMERIC_FIELDS:
                    column = pick_column(frame, (field,))
                    row[field] = None if column is None else raw.get(column)
                    row[f"{field}__missing"] = row[field] is None or pd.isna(row[field])
                for field in ("defence_condition_code", "rofrs_risk_category"):
                    column = pick_column(frame, (field,))
                    row[field] = None if column is None else raw.get(column)
                    row[f"{field}__missing"] = row[field] is None or pd.isna(row[field])
                versions_column = pick_column(frame, ("source_versions_json",))
                row["source_versions_json"] = (
                    raw.get(versions_column)
                    if versions_column
                    else json_dumps_canonical(
                        {"source_version": source_version or Path(raw_path).name}
                    )
                )
                row["coordinate_reference_system"] = "EPSG:4326"
                row["source_member"] = payload.name
                row["raw_fields_json"] = json_dumps_canonical(raw)
                rows.append(row)
        if not rows:
            raise NoSourceDataError("static geo import contains no zone records")
        return validate_static_features_frame(pd.DataFrame.from_records(rows))


class AIMSStaticGeoAdapter(StaticGeoManualAdapter):
    """Static importer whose manifest points to the EA AIMS Asset Bundle."""

    metadata = source_metadata("ea_aims_assets")


class RoFRSStaticGeoAdapter(StaticGeoManualAdapter):
    """Static importer whose manifest points to the EA RoFRS dataset."""

    metadata = source_metadata("ea_rofrs")


__all__ = ["AIMSStaticGeoAdapter", "RoFRSStaticGeoAdapter", "StaticGeoManualAdapter"]
