"""Canonical, auditable data contracts for CoastWatch ImpactNet v2.

The models in this module deliberately reject naive timestamps.  A timezone-aware
timestamp in another zone is accepted, but is normalised to UTC before it can be
stored.  Data-frame validators return a normalised copy and never mutate the
caller's frame.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum
from typing import Any, ClassVar, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SchemaValidationError(ValueError):
    """Raised when a canonical table violates its data contract."""


class LabelConfidence(StrEnum):
    """Auditable event-label confidence defined by the v2 specification."""

    A = "A"
    B = "B"
    C = "C"
    U = "U"
    N = "N"


class OnsetPrecision(StrEnum):
    EXACT_HOUR = "exact_hour"
    INTERVAL = "interval"
    DATE_ONLY = "date_only"
    UNKNOWN = "unknown"


class VerticalDatum(StrEnum):
    MAOD = "mAOD"
    LOCAL_STATION_DATUM = "local_station_datum"
    CHART_DATUM = "chart_datum"
    UNKNOWN = "unknown"


def utc_datetime(value: Any, *, name: str = "timestamp") -> datetime:
    """Return ``value`` as an aware UTC ``datetime`` or fail loudly.

    Naive datetimes are ambiguous around BST transitions and therefore are never
    guessed.  Aware timestamps retain their instant when converted to UTC.
    """

    if value is None or value is pd.NaT:
        raise ValueError(f"{name} must not be null")
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not a valid timestamp: {value!r}") from exc
    if stamp.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware; naive timestamps are forbidden")
    return stamp.tz_convert("UTC").to_pydatetime()


def _optional_utc(value: Any, *, name: str) -> datetime | None:
    if value is None or value is pd.NaT or (isinstance(value, float) and pd.isna(value)):
        return None
    return utc_datetime(value, name=name)


def _none_if_missing(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    return value


class CanonicalRecord(BaseModel):
    """Base class that permits provenance/missingness extension columns."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)


class SiteRecord(CanonicalRecord):
    site_id: str = Field(min_length=1)
    coastal_zone_id: str = Field(min_length=1)
    site_name: str = Field(min_length=1)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    ea_warning_area_code: str | None = None
    tide_station_ids: list[str] = Field(default_factory=list)
    wave_station_ids: list[str] = Field(default_factory=list)
    timezone_display: str = "Europe/London"
    active: bool = True
    exclusion_reason: str | None = None
    coordinate_reference_system: str = "EPSG:4326"

    @field_validator("timezone_display")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @field_validator("coordinate_reference_system")
    @classmethod
    def _wgs84_only(cls, value: str) -> str:
        canonical = value.strip().upper().replace(" ", "")
        if canonical not in {"EPSG:4326", "WGS84", "WGS-84"}:
            raise ValueError("site coordinates must use WGS84 / EPSG:4326")
        return "EPSG:4326"

    @model_validator(mode="after")
    def _active_exclusion_consistency(self) -> SiteRecord:
        if self.active and self.exclusion_reason:
            raise ValueError("active sites cannot have an exclusion_reason")
        if not self.active and not self.exclusion_reason:
            raise ValueError("inactive sites require an exclusion_reason")
        return self


class ObservationRecord(CanonicalRecord):
    site_id: str = Field(min_length=1)
    coastal_zone_id: str = Field(min_length=1)
    timestamp_utc: datetime
    water_level_m_aod: float | None = None
    water_level_local_m: float | None = None
    water_level_datum: VerticalDatum = VerticalDatum.UNKNOWN
    predicted_tide_m_aod: float | None = None
    surge_residual_m: float | None = None
    significant_wave_height_m: float | None = Field(default=None, ge=0.0)
    maximum_wave_height_m: float | None = Field(default=None, ge=0.0)
    wave_period_s: float | None = Field(default=None, ge=0.0)
    wave_direction_deg_true: float | None = Field(default=None, ge=0.0, lt=360.0)
    wind_speed_m_s: float | None = Field(default=None, ge=0.0)
    wind_gust_m_s: float | None = Field(default=None, ge=0.0)
    wind_direction_deg_true: float | None = Field(default=None, ge=0.0, lt=360.0)
    surface_pressure_hpa: float | None = Field(default=None, gt=0.0)
    rainfall_mm_h: float | None = Field(default=None, ge=0.0)
    air_temperature_c: float | None = None
    humidity_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    source_age_minutes: float | None = Field(default=None, ge=0.0)
    quality_flag: str | None = None

    @field_validator("timestamp_utc", mode="before")
    @classmethod
    def _utc_timestamp(cls, value: Any) -> datetime:
        return utc_datetime(value, name="timestamp_utc")

    @model_validator(mode="after")
    def _datum_matches_named_field(self) -> ObservationRecord:
        if self.water_level_m_aod is not None and self.water_level_datum != VerticalDatum.MAOD:
            raise ValueError("water_level_m_aod requires water_level_datum='mAOD'")
        return self


class ForecastRecord(CanonicalRecord):
    LEAD_TOLERANCE_HOURS: ClassVar[float] = 1.0 / 60.0

    site_id: str = Field(min_length=1)
    issue_time_utc: datetime
    valid_time_utc: datetime
    lead_hours: float = Field(gt=0.0)
    source_model: str = Field(min_length=1)
    model_run_id: str = Field(min_length=1)
    forecast_total_water_level_m_aod: float | None = None
    forecast_tide_m_aod: float | None = None
    forecast_surge_m: float | None = None
    forecast_wave_height_m: float | None = Field(default=None, ge=0.0)
    forecast_wave_period_s: float | None = Field(default=None, ge=0.0)
    forecast_wave_direction_deg_true: float | None = Field(default=None, ge=0.0, lt=360.0)
    forecast_wind_speed_m_s: float | None = Field(default=None, ge=0.0)
    forecast_wind_gust_m_s: float | None = Field(default=None, ge=0.0)
    forecast_wind_direction_deg_true: float | None = Field(default=None, ge=0.0, lt=360.0)
    forecast_pressure_hpa: float | None = Field(default=None, gt=0.0)
    forecast_rainfall_mm_h: float | None = Field(default=None, ge=0.0)
    ensemble_member: str | int | None = None
    quantile: float | None = Field(default=None, ge=0.0, le=1.0)
    quality_flag: str | None = None

    @field_validator("issue_time_utc", "valid_time_utc", mode="before")
    @classmethod
    def _utc_times(cls, value: Any, info: Any) -> datetime:
        return utc_datetime(value, name=info.field_name)

    @model_validator(mode="after")
    def _valid_issue_and_lead(self) -> ForecastRecord:
        if self.valid_time_utc <= self.issue_time_utc:
            raise ValueError("valid_time_utc must be later than issue_time_utc")
        actual = (self.valid_time_utc - self.issue_time_utc).total_seconds() / 3600.0
        if abs(actual - self.lead_hours) > self.LEAD_TOLERANCE_HOURS:
            raise ValueError(
                f"lead_hours={self.lead_hours} disagrees with issue/valid interval {actual:.6g}h"
            )
        return self


class StaticFeatureRecord(CanonicalRecord):
    coastal_zone_id: str = Field(min_length=1)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    coastal_orientation_sin: float | None = Field(default=None, ge=-1.0, le=1.0)
    coastal_orientation_cos: float | None = Field(default=None, ge=-1.0, le=1.0)
    ground_elevation_m_aod: float | None = None
    defence_crest_height_m_aod: float | None = None
    defence_condition_code: str | None = None
    distance_to_coast_m: float | None = Field(default=None, ge=0.0)
    rofrs_risk_category: str | None = None
    historic_flood_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    low_lying_area_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    road_exposure_count: float | None = Field(default=None, ge=0.0)
    building_exposure_count: float | None = Field(default=None, ge=0.0)
    static_snapshot_date: date
    vertical_datum: VerticalDatum
    source_versions_json: str = "{}"
    coordinate_reference_system: str = "EPSG:4326"

    @field_validator("coordinate_reference_system")
    @classmethod
    def _wgs84_only(cls, value: str) -> str:
        canonical = value.strip().upper().replace(" ", "")
        if canonical not in {"EPSG:4326", "WGS84", "WGS-84"}:
            raise ValueError("static feature coordinates must use WGS84 / EPSG:4326")
        return "EPSG:4326"

    @model_validator(mode="after")
    def _aod_fields_have_aod_datum(self) -> StaticFeatureRecord:
        aod_value_present = any(
            value is not None
            for value in (self.ground_elevation_m_aod, self.defence_crest_height_m_aod)
        )
        if aod_value_present and self.vertical_datum != VerticalDatum.MAOD:
            raise ValueError("*_m_aod static fields require vertical_datum='mAOD'")
        return self


class EventCatalogRecord(CanonicalRecord):
    event_id: str = Field(min_length=1)
    storm_group_id: str | None = None
    coastal_zone_id: str = Field(min_length=1)
    onset_time_utc: datetime | None = None
    peak_time_utc: datetime | None = None
    end_time_utc: datetime | None = None
    onset_precision: OnsetPrecision
    impact_confirmed: bool | None = None
    impact_severity: int | None = Field(default=None, ge=0, le=3)
    label_confidence: LabelConfidence
    warning_max_severity: int | None = Field(default=None, ge=0, le=3)
    spatial_evidence: bool = False
    observational_evidence: bool = False
    human_reviewed: bool = False
    primary_source: str = Field(min_length=1)
    source_references_json: str = "[]"
    review_notes: str = ""
    created_at_utc: datetime
    updated_at_utc: datetime

    @field_validator(
        "onset_time_utc",
        "peak_time_utc",
        "end_time_utc",
        "created_at_utc",
        "updated_at_utc",
        mode="before",
    )
    @classmethod
    def _utc_event_times(cls, value: Any, info: Any) -> datetime | None:
        return _optional_utc(value, name=info.field_name)

    @model_validator(mode="after")
    def _event_semantics(self) -> EventCatalogRecord:
        if self.updated_at_utc < self.created_at_utc:
            raise ValueError("updated_at_utc cannot precede created_at_utc")
        if self.onset_time_utc and self.peak_time_utc and self.peak_time_utc < self.onset_time_utc:
            raise ValueError("peak_time_utc cannot precede onset_time_utc")
        if self.onset_time_utc and self.end_time_utc and self.end_time_utc < self.onset_time_utc:
            raise ValueError("end_time_utc cannot precede onset_time_utc")
        if self.label_confidence == LabelConfidence.A:
            if self.impact_confirmed is not True or not self.human_reviewed:
                raise ValueError("A labels require confirmed impact and human review")
            if self.onset_precision not in {OnsetPrecision.EXACT_HOUR, OnsetPrecision.INTERVAL}:
                raise ValueError("A labels require exact-hour or interval onset precision")
        if self.label_confidence == LabelConfidence.B and self.impact_confirmed is not True:
            raise ValueError("B labels require confirmed impact")
        if (
            self.label_confidence in {LabelConfidence.A, LabelConfidence.B}
            and not self.onset_time_utc
        ):
            raise ValueError("A/B positive labels require onset_time_utc")
        if self.label_confidence == LabelConfidence.N and self.impact_confirmed is not False:
            raise ValueError("N labels require impact_confirmed=false")
        return self


RecordT = TypeVar("RecordT", bound=CanonicalRecord)


TABLE_MODELS: dict[str, type[CanonicalRecord]] = {
    "sites": SiteRecord,
    "observations_hourly": ObservationRecord,
    "forecasts_hourly": ForecastRecord,
    "static_features": StaticFeatureRecord,
    "event_catalog": EventCatalogRecord,
}

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "sites": ("site_id",),
    "observations_hourly": ("site_id", "timestamp_utc"),
    "forecasts_hourly": ("site_id", "issue_time_utc", "valid_time_utc", "source_model"),
    "static_features": ("coastal_zone_id",),
    "event_catalog": ("event_id",),
}


def _model_required_columns(model: type[CanonicalRecord]) -> set[str]:
    return {name for name, field in model.model_fields.items() if field.is_required()}


def validate_frame(
    frame: pd.DataFrame,
    model: type[RecordT],
    *,
    primary_key: Iterable[str] = (),
) -> pd.DataFrame:
    """Validate every record, primary-key uniqueness and return normalised data."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    missing = _model_required_columns(model).difference(frame.columns)
    if missing:
        raise SchemaValidationError(f"missing required columns: {sorted(missing)}")
    normalised: list[dict[str, Any]] = []
    for row_number, raw in enumerate(frame.to_dict(orient="records")):
        cleaned = {key: _none_if_missing(value) for key, value in raw.items()}
        try:
            record = model.model_validate(cleaned)
        except Exception as exc:  # Pydantic supplies the detailed field path.
            raise SchemaValidationError(f"row {row_number}: {exc}") from exc
        normalised.append(record.model_dump(mode="python"))
    result = pd.DataFrame(normalised)
    keys = tuple(primary_key)
    if keys:
        absent = set(keys).difference(result.columns)
        if absent:
            raise SchemaValidationError(f"missing primary-key columns: {sorted(absent)}")
        duplicated = result.duplicated(list(keys), keep=False)
        if duplicated.any():
            examples = result.loc[duplicated, list(keys)].head(5).to_dict(orient="records")
            raise SchemaValidationError(f"duplicate primary key {keys}: {examples}")
    return result


def validate_table(frame: pd.DataFrame, table_name: str) -> pd.DataFrame:
    try:
        model = TABLE_MODELS[table_name]
    except KeyError as exc:
        raise KeyError(f"unknown canonical table {table_name!r}") from exc
    return validate_frame(frame, model, primary_key=PRIMARY_KEYS[table_name])


def validate_sites_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return validate_table(frame, "sites")


def validate_observations_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return validate_table(frame, "observations_hourly")


def validate_forecasts_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return validate_table(frame, "forecasts_hourly")


def validate_static_features_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return validate_table(frame, "static_features")


def validate_event_catalog_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return validate_table(frame, "event_catalog")


# Arrow contracts are intentionally explicit so Parquet writers cannot silently
# downgrade UTC timestamps to naive values.  Optional extension columns are
# allowed by the Pydantic validators and can be appended by feature builders.
ARROW_SCHEMAS: dict[str, pa.Schema] = {
    "sites": pa.schema(
        [
            ("site_id", pa.string()),
            ("coastal_zone_id", pa.string()),
            ("site_name", pa.string()),
            ("latitude", pa.float64()),
            ("longitude", pa.float64()),
            ("ea_warning_area_code", pa.string()),
            ("tide_station_ids", pa.list_(pa.string())),
            ("wave_station_ids", pa.list_(pa.string())),
            ("timezone_display", pa.string()),
            ("active", pa.bool_()),
            ("exclusion_reason", pa.string()),
            ("coordinate_reference_system", pa.string()),
        ]
    ),
    "observations_hourly": pa.schema(
        [
            ("site_id", pa.string()),
            ("coastal_zone_id", pa.string()),
            ("timestamp_utc", pa.timestamp("us", tz="UTC")),
        ]
    ),
    "forecasts_hourly": pa.schema(
        [
            ("site_id", pa.string()),
            ("issue_time_utc", pa.timestamp("us", tz="UTC")),
            ("valid_time_utc", pa.timestamp("us", tz="UTC")),
            ("lead_hours", pa.float64()),
            ("source_model", pa.string()),
            ("model_run_id", pa.string()),
        ]
    ),
    "static_features": pa.schema(
        [
            ("coastal_zone_id", pa.string()),
            ("latitude", pa.float64()),
            ("longitude", pa.float64()),
            ("static_snapshot_date", pa.date32()),
            ("vertical_datum", pa.string()),
        ]
    ),
    "event_catalog": pa.schema(
        [
            ("event_id", pa.string()),
            ("storm_group_id", pa.string()),
            ("coastal_zone_id", pa.string()),
            ("onset_time_utc", pa.timestamp("us", tz="UTC")),
            ("peak_time_utc", pa.timestamp("us", tz="UTC")),
            ("end_time_utc", pa.timestamp("us", tz="UTC")),
            ("onset_precision", pa.string()),
            ("label_confidence", pa.string()),
        ]
    ),
}


__all__ = [
    "ARROW_SCHEMAS",
    "EventCatalogRecord",
    "ForecastRecord",
    "LabelConfidence",
    "ObservationRecord",
    "OnsetPrecision",
    "SchemaValidationError",
    "SiteRecord",
    "StaticFeatureRecord",
    "VerticalDatum",
    "utc_datetime",
    "validate_event_catalog_frame",
    "validate_forecasts_frame",
    "validate_frame",
    "validate_observations_frame",
    "validate_sites_frame",
    "validate_static_features_frame",
    "validate_table",
]
