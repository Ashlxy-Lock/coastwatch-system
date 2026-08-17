"""Historic flood-outline importer with evidence-only time semantics."""

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


class FloodOutlinesManualAdapter(SourceAdapter):
    """Parse GeoJSON outline evidence without manufacturing onset timestamps."""

    metadata = source_metadata("ea_flood_outlines")
    parser_version = "ea-flood-outlines-1"
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
        if path.name.lower().endswith((".gpkg", ".shp", ".shapefile.zip")):
            return self._normalise_geospatial(read_geospatial(path), path.name)
        if path.name.lower().endswith(".zip"):
            try:
                frame = read_geospatial(path)
            except Exception as geo_error:
                try:
                    list(payloads_from_file(path, allowed_suffixes=(".geojson", ".json")))
                except Exception:
                    raise geo_error from None
            else:
                return self._normalise_geospatial(frame, path.name)

        rows: list[dict[str, Any]] = []
        for payload in payloads_from_file(path, allowed_suffixes=(".geojson", ".json")):
            assert_geojson_wgs84(payload.payload, source_name=payload.name)
            frame = read_tabular_payload(payload)
            id_column = pick_column(
                frame,
                ("outline_id", "flood_event_id", "event_id", "objectid", "id"),
                required=True,
                meaning="outline identifier",
            )
            date_column = pick_column(
                frame,
                ("event_date", "flood_date", "start_date", "date"),
            )
            geometry_column = pick_column(frame, ("geometry",), required=True, meaning="geometry")
            for raw in frame.to_dict(orient="records"):
                raw_date = None if date_column is None else raw.get(date_column)
                event_date = None
                if raw_date is not None and not (isinstance(raw_date, float) and pd.isna(raw_date)):
                    event_date = pd.Timestamp(raw_date).date()
                rows.append(
                    {
                        "outline_id": str(raw[id_column]).strip(),  # type: ignore[index]
                        "event_date": event_date,
                        "onset_time_utc": None,
                        "onset_precision": "date_only" if event_date else "unknown",
                        "geometry_json": json_dumps_canonical(raw.get(geometry_column)),
                        "evidence_only": True,
                        "source_member": payload.name,
                        "raw_fields_json": json_dumps_canonical(raw),
                    }
                )
        if not rows:
            raise NoSourceDataError("historic flood outline import contains no features")
        return pd.DataFrame.from_records(rows)

    @staticmethod
    def _normalise_geospatial(frame: pd.DataFrame, source_member: str) -> pd.DataFrame:
        normalised = normalised_columns(frame)
        id_column = pick_column(
            normalised,
            ("outline_id", "flood_event_id", "event_id", "objectid", "id"),
            required=True,
            meaning="outline identifier",
        )
        date_column = pick_column(
            normalised,
            ("event_date", "flood_date", "start_date", "date"),
        )
        rows: list[dict[str, Any]] = []
        for raw in normalised.to_dict(orient="records"):
            raw_date = None if date_column is None else raw.get(date_column)
            event_date = None if raw_date is None else pd.Timestamp(raw_date).date()
            rows.append(
                {
                    "outline_id": str(raw[id_column]).strip(),  # type: ignore[index]
                    "event_date": event_date,
                    "onset_time_utc": None,
                    "onset_precision": "date_only" if event_date else "unknown",
                    "geometry_json": raw["geometry_json"],
                    "evidence_only": True,
                    "source_member": source_member,
                    "raw_fields_json": json_dumps_canonical(raw),
                }
            )
        return pd.DataFrame.from_records(rows)


__all__ = ["FloodOutlinesManualAdapter"]
