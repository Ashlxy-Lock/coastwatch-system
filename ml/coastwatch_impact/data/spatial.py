"""Human-reviewed site-to-zone/station mapping contracts.

This module intentionally does not infer coastal mappings from longitude and
latitude alone. Geometry work must happen in EPSG:27700 through the optional
geo stack, then the auditable candidate measurements are validated here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpatialMappingError(ValueError):
    """A mapping is ambiguous, unreviewed, or violates a coastal guard."""


class SiteMappingRecord(BaseModel):
    """One reviewed mapping candidate measured in an explicit projected CRS."""

    model_config = ConfigDict(extra="forbid")

    site_id: str = Field(min_length=1)
    site_name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    country_scope: str
    active_candidate: bool
    approved: bool = False
    coastal_zone_id: str | None = None
    warning_area_code: str | None = None
    tide_station_id: str | None = None
    tide_station_distance_m: float | None = Field(default=None, ge=0)
    wave_station_id: str | None = None
    wave_station_distance_m: float | None = Field(default=None, ge=0)
    exposure_coast_id: str | None = None
    wave_station_exposure_coast_id: str | None = None
    coast_type: Literal["open_coast", "estuary", "unknown"] = "unknown"
    tide_station_coast_type: Literal["open_coast", "estuary", "unknown"] = "unknown"
    wave_direction_reference: Literal["true", "magnetic", "unknown"] = "unknown"
    wave_direction_convention: Literal["coming_from", "going_to", "unknown"] = "unknown"
    storage_crs: Literal["EPSG:4326"] = "EPSG:4326"
    distance_crs: Literal["EPSG:27700"] = "EPSG:27700"
    selection_reason: str = ""
    exclusion_reason: str = ""
    reviewer: str = ""

    @model_validator(mode="after")
    def validate_approval_completeness(self) -> SiteMappingRecord:
        if self.approved and not self.active_candidate:
            raise ValueError("an inactive candidate cannot be approved")
        if self.approved:
            required = {
                "coastal_zone_id": self.coastal_zone_id,
                "warning_area_code": self.warning_area_code,
                "tide_station_id": self.tide_station_id,
                "tide_station_distance_m": self.tide_station_distance_m,
                "wave_station_id": self.wave_station_id,
                "wave_station_distance_m": self.wave_station_distance_m,
                "exposure_coast_id": self.exposure_coast_id,
                "wave_station_exposure_coast_id": self.wave_station_exposure_coast_id,
            }
            missing = sorted(name for name, value in required.items() if value in {None, ""})
            if missing:
                raise ValueError(f"approved mapping is incomplete: {missing}")
            if not self.selection_reason.strip() or not self.reviewer.strip():
                raise ValueError("approved mapping requires a selection reason and reviewer")
        return self


def build_site_mapping_review(
    legacy_locations: str | Path,
    sites_config: str | Path,
) -> pd.DataFrame:
    """Build a review template from exact legacy locations without inventing mappings."""

    locations_payload = json.loads(Path(legacy_locations).read_text(encoding="utf-8"))
    if not isinstance(locations_payload, list):
        raise SpatialMappingError("legacy locations root must be a list")
    locations = {str(item["id"]): item for item in locations_payload}
    config = yaml.safe_load(Path(sites_config).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("sites"), dict):
        raise SpatialMappingError("sites config must contain a sites mapping")
    if config.get("storage_crs") != "EPSG:4326" or config.get("distance_crs") != "EPSG:27700":
        raise SpatialMappingError(
            "site registry requires EPSG:4326 storage and EPSG:27700 distance"
        )

    records: list[dict[str, object]] = []
    for site_id, choice in config["sites"].items():
        if site_id not in locations:
            raise SpatialMappingError(f"configured legacy site is missing: {site_id}")
        if not isinstance(choice, dict):
            raise SpatialMappingError(f"site config must be a mapping: {site_id}")
        location = locations[site_id]
        name = str(location["name"])
        if ", England," in name:
            country_scope = "England"
        elif ", Scotland," in name:
            country_scope = "Scotland"
        elif ", Wales," in name:
            country_scope = "Wales"
        elif ", Northern Ireland," in name:
            country_scope = "Northern Ireland"
        else:
            country_scope = "Out of UK v1 scope"
        record = SiteMappingRecord(
            site_id=site_id,
            site_name=name,
            latitude=float(location["latitude"]),
            longitude=float(location["longitude"]),
            country_scope=country_scope,
            active_candidate=bool(choice.get("active_candidate", False)),
            approved=bool(choice.get("approved", False)),
            storage_crs=config["storage_crs"],
            distance_crs=config["distance_crs"],
            selection_reason=str(choice.get("review_note", "")),
            exclusion_reason=str(choice.get("exclusion_reason", "")),
        )
        records.append(record.model_dump())
    return pd.DataFrame(records).sort_values("site_id", kind="stable").reset_index(drop=True)


def validate_site_mappings(
    frame: pd.DataFrame,
    *,
    maximum_tide_distance_m: float = 50_000.0,
    maximum_wave_distance_m: float = 100_000.0,
) -> list[SiteMappingRecord]:
    """Validate reviewed candidates and return only approved England mappings."""

    if frame.empty:
        raise SpatialMappingError("site mapping review is empty")
    if frame["site_id"].astype(str).duplicated().any():
        raise SpatialMappingError("site_id is duplicated in the mapping review")
    records: list[SiteMappingRecord] = []
    for payload in frame.to_dict(orient="records"):
        cleaned = {key: (None if pd.isna(value) else value) for key, value in payload.items()}
        record = SiteMappingRecord.model_validate(cleaned)
        if not record.approved:
            continue
        if record.country_scope != "England":
            raise SpatialMappingError(f"v2 MVP cannot approve non-England site {record.site_id!r}")
        if float(record.tide_station_distance_m or 0) > maximum_tide_distance_m:
            raise SpatialMappingError(f"tide station is too distant for {record.site_id!r}")
        if float(record.wave_station_distance_m or 0) > maximum_wave_distance_m:
            raise SpatialMappingError(f"wave station is too distant for {record.site_id!r}")
        if record.exposure_coast_id != record.wave_station_exposure_coast_id:
            raise SpatialMappingError(
                f"wave station is on a different exposed coast for {record.site_id!r}"
            )
        if (
            record.coast_type != "unknown"
            and record.tide_station_coast_type != "unknown"
            and record.coast_type != record.tide_station_coast_type
        ):
            raise SpatialMappingError(
                f"estuary/open-coast mismatch for tide station at {record.site_id!r}"
            )
        if "unknown" in {
            record.wave_direction_reference,
            record.wave_direction_convention,
        }:
            raise SpatialMappingError(
                f"wave direction metadata is unresolved for {record.site_id!r}"
            )
        records.append(record)

    station_zones: dict[str, set[str]] = {}
    for record in records:
        station_zones.setdefault(str(record.tide_station_id), set()).add(
            str(record.coastal_zone_id)
        )
    ambiguous = {station: zones for station, zones in station_zones.items() if len(zones) > 1}
    if ambiguous:
        raise SpatialMappingError(
            "a tide station serves multiple approved zones without an explicit shared-station "
            f"review contract: {ambiguous}"
        )
    return records


def write_site_mapping_review(frame: pd.DataFrame, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite site mapping review: {path}")
    frame.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
    return path


__all__ = [
    "SiteMappingRecord",
    "SpatialMappingError",
    "build_site_mapping_review",
    "validate_site_mappings",
    "write_site_mapping_review",
]
