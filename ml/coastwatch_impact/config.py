"""Validated and fully resolved ImpactNet configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

LabelMode = Literal["weak_rule", "official_warning", "confirmed_impact"]
DataMode = Literal["hindcast_research", "operational_backtest", "live_shadow"]
ModelVariant = Literal["obs_only_tcn", "hybrid_tcn"]


def _default_allowed_confidence() -> list[Literal["A", "B", "C"]]:
    return ["A"]


def model_name_for_label_mode(label_mode: LabelMode, *, synthetic_data: bool = False) -> str:
    """Return the only model name permitted by the label semantics."""

    if synthetic_data:
        return "CoastWatch Synthetic-Test TCN"
    return {
        "weak_rule": "CoastWatch Proxy-TCN",
        "official_warning": "CoastWatch WarningNet",
        "confirmed_impact": "CoastWatch ImpactNet",
    }[label_mode]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectConfig(StrictModel):
    name: str = "coastwatch-impactnet"
    timezone_storage: Literal["UTC"] = "UTC"
    shadow_mode: Literal[True] = True
    synthetic_data: bool = False


class ScopeConfig(StrictModel):
    country: str
    hazard: str
    label_mode: LabelMode
    allowed_label_confidence: list[Literal["A", "B", "C"]] = Field(
        default_factory=_default_allowed_confidence
    )

    @model_validator(mode="after")
    def validate_label_evidence_semantics(self) -> ScopeConfig:
        confidences = set(self.allowed_label_confidence)
        if not confidences:
            raise ValueError("allowed_label_confidence must not be empty")
        if self.label_mode == "official_warning":
            if confidences != {"C"}:
                raise ValueError("official_warning trains WarningNet from C warning evidence only")
        elif "C" in confidences:
            raise ValueError(
                "C is warning-only evidence and cannot train a weak_rule or "
                "confirmed_impact primary head"
            )
        return self


class WindowsConfig(StrictModel):
    history_hours: int = Field(default=72, ge=1)
    forecast_hours: int = Field(default=24, ge=1)
    output_horizons_hours: list[int] = Field(default_factory=lambda: [1, 3, 6, 12, 24])

    @model_validator(mode="after")
    def validate_horizons(self) -> WindowsConfig:
        if sorted(set(self.output_horizons_hours)) != self.output_horizons_hours:
            raise ValueError("output horizons must be unique and increasing")
        if self.output_horizons_hours[-1] > self.forecast_hours:
            raise ValueError("output horizon cannot exceed forecast_hours")
        return self


class ModeConfig(StrictModel):
    data_mode: DataMode
    model_variant: ModelVariant


class FeaturesConfig(StrictModel):
    include_missing_masks: bool = True
    include_source_age: bool = True
    include_static_features: bool = True
    use_site_embedding: bool = False
    water_target_mode: Literal["absolute", "residual"] = "residual"


class SplitConfig(StrictModel):
    train_end: datetime
    validation_end: datetime
    test_end: datetime
    target_purge_hours: int = Field(default=24, ge=1)
    event_buffer_hours: int = Field(default=72, ge=0)
    context_mode: Literal["operational_context", "strict_no_overlap"] = "operational_context"

    @model_validator(mode="after")
    def validate_utc_order(self) -> SplitConfig:
        values = (self.train_end, self.validation_end, self.test_end)
        if any(
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value) for value in values
        ):
            raise ValueError("split boundaries must be timezone-aware UTC")
        if not self.train_end < self.validation_end < self.test_end:
            raise ValueError("split boundaries must be strictly increasing")
        return self


class ModelConfig(StrictModel):
    hidden_channels: int = Field(default=64, ge=1)
    num_blocks: int = Field(default=5, ge=1)
    kernel_size: int = Field(default=3, ge=2)
    dilations: list[int] = Field(default_factory=lambda: [1, 2, 4, 8, 16])
    dropout: float = Field(default=0.2, ge=0.0, lt=1.0)
    decoder_hidden_dim: int = Field(default=128, ge=1)
    lead_embedding_dim: int = Field(default=8, ge=1)

    @model_validator(mode="after")
    def validate_blocks(self) -> ModelConfig:
        if len(self.dilations) != self.num_blocks:
            raise ValueError("dilations length must equal num_blocks")
        if any(value < 1 for value in self.dilations):
            raise ValueError("dilations must be positive")
        return self


class TrainingConfig(StrictModel):
    seed: int = 20260813
    batch_size: int = Field(default=128, ge=1)
    max_epochs: int = Field(default=100, ge=1)
    learning_rate: float = Field(default=0.001, gt=0)
    weight_decay: float = Field(default=0.0001, ge=0)
    grad_clip_norm: float = Field(default=1.0, gt=0)
    early_stopping_patience: int = Field(default=12, ge=1)
    mixed_precision: bool = True


class SamplingConfig(StrictModel):
    negative_min_spacing_hours: int = Field(default=6, ge=1)
    negative_to_positive_target_ratio: float = Field(default=4.0, gt=0)
    normalize_positive_weight_per_event: bool = True


class LossConfig(StrictModel):
    event_weight: float = Field(default=1.0, ge=0)
    water_weight: float = Field(default=0.4, ge=0)
    warning_aux_weight: float = Field(default=0.2, ge=0)
    severity_aux_weight: float = Field(default=0.2, ge=0)
    max_pos_weight: float = Field(default=20.0, gt=0)


class CalibrationConfig(StrictModel):
    method: Literal["global_temperature"] = "global_temperature"


class AlertsConfig(StrictModel):
    merge_gap_hours: int = Field(default=2, ge=0)
    cooldown_hours: int = Field(default=6, ge=0)
    match_lookahead_hours: int = Field(default=24, ge=1)


class QualityConfig(StrictModel):
    max_missing_fraction_past: float = Field(default=0.25, ge=0, le=1)
    max_missing_fraction_future: float = Field(default=0.25, ge=0, le=1)
    max_tide_age_minutes: int = Field(default=90, ge=0)
    max_weather_forecast_age_hours: int = Field(default=12, ge=0)
    max_wave_age_hours: int = Field(default=6, ge=0)


class ImpactConfig(StrictModel):
    project: ProjectConfig
    scope: ScopeConfig
    windows: WindowsConfig
    mode: ModeConfig
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    split: SplitConfig
    model: ModelConfig
    training: TrainingConfig
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    loss: LossConfig
    calibration: CalibrationConfig
    alerts: AlertsConfig
    quality: QualityConfig = Field(default_factory=QualityConfig)

    @model_validator(mode="after")
    def validate_scientific_contract(self) -> ImpactConfig:
        if self.split.target_purge_hours < self.windows.forecast_hours:
            raise ValueError("target purge must cover the complete forecast horizon")
        if self.mode.data_mode == "live_shadow" and not self.project.shadow_mode:
            raise ValueError("live_shadow always requires shadow_mode=true")
        if (
            self.mode.model_variant == "obs_only_tcn"
            and self.features.water_target_mode != "absolute"
        ):
            raise ValueError(
                "obs_only_tcn requires water_target_mode='absolute'; residual targets "
                "depend on a future physics baseline"
            )
        return self

    @property
    def model_name(self) -> str:
        return model_name_for_label_mode(
            self.scope.label_mode, synthetic_data=self.project.synthetic_data
        )

    def resolved_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def load_config(path: str | Path) -> ImpactConfig:
    """Load a YAML config and reject unresolved or unsafe values."""

    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a mapping")
    return ImpactConfig.model_validate(payload)


def write_resolved_config(config: ImpactConfig, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        yaml.safe_dump(config.resolved_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
