"""Manual importer for Environment Agency Flood Warning Areas."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ._geo import assert_geojson_wgs84, package_shapefile, read_geospatial
from ._parsing import normalised_columns, pick_column, read_tabular_payload
from .base import (
    NoSourceDataError,
    RawImportResult,
    SourceAdapter,
    json_dumps_canonical,
    payloads_from_file,
)
from .registry import source_metadata


class WarningAreasManualAdapter(SourceAdapter):
    metadata = source_metadata("ea_warning_areas")
    parser_version = "ea-warning-areas-1"
    supported_suffixes = frozenset({".geojson", ".json", ".gpkg", ".shp", ".zip"})

    def import_file(self, source: str | Path, **kwargs: object) -> RawImportResult:
        path = Path(source)
        if path.suffix.lower() == ".shp":
            options = dict(kwargs)
            options.setdefault(
                "notes",
                "Deterministic ZIP containing the exact .shp/.shx/.dbf and optional sidecar bytes.",
            )
            return self.import_bytes(
                package_shapefile(path),
                original_filename=f"{path.stem}.shapefile.zip",
                **options,  # type: ignore[arg-type]
            )
        return super().import_file(path, **kwargs)  # type: ignore[arg-type]

    def parse(self, raw_path: str | Path) -> pd.DataFrame:
        path = Path(raw_path)
        original_lower = self._original_filename(path).lower()
        if original_lower.endswith((".gpkg", ".shp", ".shapefile.zip")):
            return self._normalise_geospatial(self._parse_with_geopandas(path), path.name)
        if original_lower.endswith(".zip"):
            try:
                return self._normalise_geospatial(self._parse_with_geopandas(path), path.name)
            except Exception as geo_error:
                try:
                    list(payloads_from_file(path, allowed_suffixes=(".geojson", ".json")))
                except Exception:
                    raise geo_error from None
        rows: list[dict[str, Any]] = []
        for payload in payloads_from_file(path, allowed_suffixes=(".geojson", ".json")):
            assert_geojson_wgs84(payload.payload, source_name=payload.name)
            frame = read_tabular_payload(payload)
            code_column = pick_column(
                frame,
                ("warning_area_code", "ta_code", "fwa_code", "flood_area_id", "code"),
                required=True,
                meaning="warning-area code",
            )
            name_column = pick_column(
                frame,
                ("warning_area_name", "flood_area_name", "label", "name"),
            )
            geometry_column = pick_column(frame, ("geometry",))
            if geometry_column is None:
                raise ValueError(f"{payload.name}: GeoJSON features require geometry")
            for raw in frame.to_dict(orient="records"):
                geometry = raw.get(geometry_column)
                rows.append(
                    {
                        "warning_area_code": str(raw[code_column]).strip(),  # type: ignore[index]
                        "warning_area_name": None if name_column is None else raw.get(name_column),
                        "geometry_json": json_dumps_canonical(geometry),
                        "coordinate_reference_system": "EPSG:4326",
                        "source_member": payload.name,
                        "raw_fields_json": json_dumps_canonical(raw),
                    }
                )
        if not rows:
            raise NoSourceDataError("warning-area import contains no features")
        result = pd.DataFrame.from_records(rows)
        if result["warning_area_code"].duplicated().any():
            raise ValueError("warning-area codes must be unique within an imported snapshot")
        return result

    @staticmethod
    def _original_filename(raw_path: Path) -> str:
        # Content-addressed names retain the original suffix.
        return raw_path.name

    @staticmethod
    def _parse_with_geopandas(path: Path) -> pd.DataFrame:
        return read_geospatial(path)

    @staticmethod
    def _normalise_geospatial(frame: pd.DataFrame, source_member: str) -> pd.DataFrame:
        normalised = normalised_columns(frame)
        code_column = pick_column(
            normalised,
            ("warning_area_code", "ta_code", "fwa_code", "flood_area_id", "code"),
            required=True,
            meaning="warning-area code",
        )
        name_column = pick_column(
            normalised,
            ("warning_area_name", "flood_area_name", "label", "name"),
        )
        rows = []
        for raw in normalised.to_dict(orient="records"):
            rows.append(
                {
                    "warning_area_code": str(raw[code_column]).strip(),  # type: ignore[index]
                    "warning_area_name": None if name_column is None else raw.get(name_column),
                    "geometry_json": raw["geometry_json"],
                    "coordinate_reference_system": "EPSG:4326",
                    "source_member": source_member,
                    "raw_fields_json": json_dumps_canonical(raw),
                }
            )
        result = pd.DataFrame.from_records(rows)
        if result["warning_area_code"].duplicated().any():
            raise ValueError("warning-area codes must be unique within an imported snapshot")
        return result


__all__ = ["WarningAreasManualAdapter"]
