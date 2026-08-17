"""Fail-closed schemas for historical Shadow Mode audit records."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value


class FeatureDistributionSummary(StrictModel):
    """Non-sensitive numeric summary optionally attached by a feature provider.

    ``distribution_sample`` is optional because current API audit logs deliberately
    do not contain input values. PSI and Wasserstein are reported only when both
    reference and live records contain an explicit sample.
    """

    observed_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    minimum: float | None = None
    maximum: float | None = None
    distribution_sample: list[float] | None = None

    @field_validator("minimum", "maximum")
    @classmethod
    def finite_bound(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("feature bounds must be finite")
        return value

    @field_validator("distribution_sample")
    @classmethod
    def finite_sample(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and any(not math.isfinite(item) for item in value):
            raise ValueError("distribution_sample values must be finite")
        return value

    @model_validator(mode="after")
    def consistent_summary(self) -> FeatureDistributionSummary:
        if (self.minimum is None) != (self.maximum is None):
            raise ValueError("minimum and maximum must be provided together")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
        if self.distribution_sample is not None:
            if len(self.distribution_sample) > self.observed_count:
                raise ValueError("distribution_sample cannot exceed observed_count")
            if (
                self.distribution_sample
                and self.minimum is not None
                and (
                    min(self.distribution_sample) < self.minimum
                    or max(self.distribution_sample) > self.maximum  # type: ignore[operator]
                )
            ):
                raise ValueError("distribution_sample lies outside declared bounds")
        return self


class InputSummary(StrictModel):
    features: dict[str, FeatureDistributionSummary] = Field(default_factory=dict)
    ood_score: float | None = None

    @field_validator("ood_score")
    @classmethod
    def finite_ood_score(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("ood_score must be finite")
        return value


class DataQualitySummary(StrictModel):
    status: Literal[
        "normal",
        "degraded_obs_only",
        "degraded_physics_only",
        "out_of_domain",
    ]
    missing_fraction: float = Field(ge=0.0, le=1.0)
    stale_sources: list[str] = Field(default_factory=list)
    out_of_domain: bool = False

    @model_validator(mode="after")
    def consistent_ood(self) -> DataQualitySummary:
        if (self.status == "out_of_domain") != self.out_of_domain:
            raise ValueError("out_of_domain status and flag must agree")
        return self


class ForecastProvenance(StrictModel):
    source_model: str = Field(min_length=1)
    model_run_id: str = Field(min_length=1)
    issue_time_utc: datetime
    valid_time_utc: datetime

    @field_validator("issue_time_utc", "valid_time_utc")
    @classmethod
    def utc_times(cls, value: datetime, info: Any) -> datetime:
        return _utc(value, info.field_name)

    @model_validator(mode="after")
    def issue_before_valid(self) -> ForecastProvenance:
        if self.issue_time_utc >= self.valid_time_utc:
            raise ValueError("forecast issue_time_utc must precede valid_time_utc")
        return self


class PredictionAuditRecord(StrictModel):
    timestamp_utc: datetime
    level: str | None = None
    logger: str | None = None
    message: Literal["shadow_prediction"]
    request_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    model_variant: Literal["hybrid_tcn", "obs_only_tcn", "physics_baseline"]
    site_id: str = Field(min_length=1)
    prediction_time_utc: datetime
    feature_manifest_hash: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    source_issue_times: dict[str, datetime]
    issued_forecast_provenance: list[ForecastProvenance]
    data_quality: DataQualitySummary
    raw_logits: list[float] | None
    calibrated_probabilities: dict[str, float]
    research_band: str
    calibrated: bool
    latency_ms: float = Field(ge=0.0)
    shadow_mode: Literal[True]
    synthetic_data: bool | None = None
    input_summary: InputSummary | None = None

    @field_validator("timestamp_utc", "prediction_time_utc")
    @classmethod
    def utc_times(cls, value: datetime, info: Any) -> datetime:
        return _utc(value, info.field_name)

    @field_validator("source_issue_times")
    @classmethod
    def source_times_utc(cls, values: dict[str, datetime]) -> dict[str, datetime]:
        for source, value in values.items():
            _utc(value, f"source_issue_times[{source!r}]")
        return values

    @field_validator("raw_logits")
    @classmethod
    def finite_logits(cls, values: list[float] | None) -> list[float] | None:
        if values is not None and any(not math.isfinite(value) for value in values):
            raise ValueError("raw_logits must be finite")
        return values

    @field_validator("calibrated_probabilities")
    @classmethod
    def valid_probabilities(cls, values: dict[str, float]) -> dict[str, float]:
        if not values:
            raise ValueError("calibrated_probabilities cannot be empty")
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values.values()):
            raise ValueError("calibrated probabilities must be finite and lie in [0, 1]")
        return values

    @model_validator(mode="after")
    def asof_contract(self) -> PredictionAuditRecord:
        future_sources = [
            source
            for source, issue_time in self.source_issue_times.items()
            if issue_time > self.prediction_time_utc
        ]
        if future_sources:
            raise ValueError(f"source issue times are after prediction time: {future_sources}")
        return self


class RequestAuditRecord(StrictModel):
    timestamp_utc: datetime
    level: str | None = None
    logger: str | None = None
    message: Literal["shadow_request"]
    request_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    status_code: int = Field(ge=100, le=599)
    latency_ms: float = Field(ge=0.0)
    shadow_mode: Literal[True]

    @field_validator("timestamp_utc")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return _utc(value, "timestamp_utc")


class InternalErrorAuditRecord(StrictModel):
    timestamp_utc: datetime
    level: str | None = None
    logger: str | None = None
    message: Literal["shadow_internal_error"]
    exception: str | None = None

    @field_validator("timestamp_utc")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return _utc(value, "timestamp_utc")


__all__ = [
    "FeatureDistributionSummary",
    "ForecastProvenance",
    "InputSummary",
    "InternalErrorAuditRecord",
    "PredictionAuditRecord",
    "RequestAuditRecord",
]
