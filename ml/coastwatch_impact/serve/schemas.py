"""Strict request and response contracts for shadow inference."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _require_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value


class IssuedForecastProvenance(StrictModel):
    """Operational provenance for one source/model run and one valid lead."""

    source_model: str = Field(min_length=1)
    model_run_id: str = Field(min_length=1)
    issue_time_utc: datetime
    valid_time_utc: datetime

    @field_validator("issue_time_utc", "valid_time_utc")
    @classmethod
    def validate_utc_times(cls, value: datetime, info: Any) -> datetime:
        return _require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_issue_precedes_valid(self) -> IssuedForecastProvenance:
        if self.issue_time_utc >= self.valid_time_utc:
            raise ValueError("issued forecast valid_time_utc must be after issue_time_utc")
        return self


class FeaturePredictionRequest(StrictModel):
    """Pre-built feature window; masks use ``true`` for an observed value."""

    site_id: str = Field(min_length=1)
    prediction_time_utc: datetime
    past_values: list[list[float | None]]
    past_mask: list[list[bool]]
    future_values: list[list[float | None]] | None = None
    future_mask: list[list[bool]] | None = None
    static_values: list[float | None] = Field(default_factory=list)
    future_time_features: list[list[float]] | None = None
    physics_baseline: list[float] | None = None
    source_issue_times: dict[str, datetime] = Field(default_factory=dict)
    issued_forecast_provenance: list[IssuedForecastProvenance] = Field(default_factory=list)
    feature_manifest_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
    )

    @field_validator("prediction_time_utc")
    @classmethod
    def validate_prediction_time(cls, value: datetime) -> datetime:
        return _require_utc(value, "prediction_time_utc")

    @field_validator("source_issue_times")
    @classmethod
    def validate_issue_time_zones(cls, values: dict[str, datetime]) -> dict[str, datetime]:
        for source, value in values.items():
            _require_utc(value, f"source_issue_times[{source!r}]")
        return values

    @model_validator(mode="after")
    def validate_aligned_masks_and_asof(self) -> FeaturePredictionRequest:
        if len(self.past_values) != len(self.past_mask) or any(
            len(values) != len(mask)
            for values, mask in zip(self.past_values, self.past_mask, strict=True)
        ):
            raise ValueError("past_mask must match past_values")
        if (self.future_values is None) != (self.future_mask is None):
            raise ValueError("future_values and future_mask must be provided together")
        if (
            self.future_values is not None
            and self.future_mask is not None
            and (
                len(self.future_values) != len(self.future_mask)
                or any(
                    len(values) != len(mask)
                    for values, mask in zip(self.future_values, self.future_mask, strict=True)
                )
            )
        ):
            raise ValueError("future_mask must match future_values")
        if self.issued_forecast_provenance and self.future_values is None:
            raise ValueError("issued forecast provenance requires future_values")
        if self.issued_forecast_provenance:
            source_lead_keys = [
                (row.source_model, row.valid_time_utc) for row in self.issued_forecast_provenance
            ]
            if len(source_lead_keys) != len(set(source_lead_keys)):
                raise ValueError(
                    "issued forecast provenance contains duplicate source/valid-time rows"
                )
            invalid_asof = [
                row
                for row in self.issued_forecast_provenance
                if not row.issue_time_utc <= self.prediction_time_utc < row.valid_time_utc
            ]
            if invalid_asof:
                raise ValueError(
                    "issued forecast provenance must satisfy "
                    "issue_time_utc <= prediction_time_utc < valid_time_utc"
                )
            assert self.future_values is not None
            expected_valid_times = {
                self.prediction_time_utc + timedelta(hours=lead)
                for lead in range(1, len(self.future_values) + 1)
            }
            supplied_valid_times = {row.valid_time_utc for row in self.issued_forecast_provenance}
            missing_leads = sorted(expected_valid_times - supplied_valid_times)
            unexpected_valid_times = sorted(supplied_valid_times - expected_valid_times)
            if missing_leads or unexpected_valid_times:
                raise ValueError(
                    "issued forecast provenance must cover every future hourly lead exactly; "
                    f"missing={missing_leads}, unexpected={unexpected_valid_times}"
                )
        future_issue_times = [
            (source, issue_time)
            for source, issue_time in self.source_issue_times.items()
            if issue_time > self.prediction_time_utc
        ]
        if future_issue_times:
            sources = ", ".join(source for source, _ in future_issue_times)
            raise ValueError(f"forecast issue_time is after prediction_time for: {sources}")
        return self


class WaterLevelQuantile(StrictModel):
    valid_time_utc: datetime
    p10: float
    p50: float
    p90: float

    @model_validator(mode="after")
    def validate_order(self) -> WaterLevelQuantile:
        _require_utc(self.valid_time_utc, "valid_time_utc")
        if not self.p10 <= self.p50 <= self.p90:
            raise ValueError("water quantiles must be non-crossing")
        return self


class DataQuality(StrictModel):
    status: Literal[
        "normal",
        "degraded_obs_only",
        "degraded_physics_only",
        "out_of_domain",
    ]
    missing_fraction: float = Field(ge=0.0, le=1.0)
    stale_sources: list[str] = Field(default_factory=list)
    out_of_domain: bool = False


class PredictionResponse(StrictModel):
    request_id: str
    model_name: str
    model_version: str
    model_variant: Literal["hybrid_tcn", "obs_only_tcn", "physics_baseline"]
    label_mode: str
    prediction_time_utc: datetime
    site_id: str
    coverage_scope: str
    event_probability: dict[str, float]
    water_level_quantiles_m_aod: list[WaterLevelQuantile]
    research_band: str
    data_quality: DataQuality
    source_issue_times: dict[str, datetime]
    issued_forecast_provenance: list[IssuedForecastProvenance] = Field(default_factory=list)
    calibrated: bool
    shadow_mode: Literal[True] = True
    synthetic_data: bool = False
    disclaimer: str = "Research output only; official warnings remain authoritative."


class InsufficientDataResponse(StrictModel):
    request_id: str
    status: Literal["insufficient_data"] = "insufficient_data"
    site_id: str | None = None
    shadow_mode: Literal[True] = True
    reason: str
    event_probability: None = None
    disclaimer: str = "Research output only; official warnings remain authoritative."


class HealthResponse(StrictModel):
    status: Literal["ok", "not_ready"]
    shadow_mode: Literal[True] = True
    loaded_models: list[str] = Field(default_factory=list)


class ModelInfoResponse(StrictModel):
    manifest: dict[str, Any]
    shadow_mode: Literal[True] = True


__all__ = [
    "DataQuality",
    "FeaturePredictionRequest",
    "HealthResponse",
    "InsufficientDataResponse",
    "IssuedForecastProvenance",
    "ModelInfoResponse",
    "PredictionResponse",
    "WaterLevelQuantile",
]
