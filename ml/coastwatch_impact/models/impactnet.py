"""ImpactNet v2 multi-task TCN model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import torch
from torch import Tensor, nn

from .heads import (
    HazardHead,
    LeadDecoder,
    StaticEncoder,
    WaterQuantileHead,
    cumulative_event_probability,
)
from .tcn import TCNEncoder

ModelVariant = Literal["obs_only_tcn", "hybrid_tcn"]
WaterTargetMode = Literal["absolute", "residual"]


class MissingFutureForecastError(ValueError):
    """Raised when a hybrid sample has no usable issued forecast values."""


@dataclass
class ImpactNetConfig:
    """Self-contained, JSON-serialisable ImpactNet architecture config.

    The ``history_*``/``future_*`` fields are accepted as aliases to make the
    model boundary easy to adapt to dataset builders without coupling this
    package to a particular configuration framework.
    """

    past_feature_dim: int = 0
    forecast_feature_dim: int = 0
    static_feature_dim: int = 0
    time_feature_dim: int = 0
    variant: ModelVariant = "obs_only_tcn"
    history_hours: int = 72
    forecast_hours: int = 24
    hidden_channels: int = 64
    num_blocks: int = 5
    kernel_size: int = 3
    dilations: tuple[int, ...] | None = None
    dropout: float = 0.2
    normalization: str = "group_norm"
    activation: str = "gelu"
    static_hidden_dim: int = 64
    static_context_dim: int = 32
    decoder_hidden_dim: int = 128
    decoder_layers: int = 2
    lead_embedding_dim: int = 8
    include_missing_masks: bool = True
    water_target_mode: WaterTargetMode | None = None

    # Compatibility aliases.  They are resolved into the canonical fields in
    # ``__post_init__`` and remain serialisable for auditability.
    history_feature_dim: int | None = None
    future_feature_dim: int | None = None
    lead_time_feature_dim: int | None = None
    model_variant: ModelVariant | None = None

    def __post_init__(self) -> None:
        self.past_feature_dim = self._resolve_alias(
            "past_feature_dim", self.past_feature_dim, self.history_feature_dim
        )
        self.forecast_feature_dim = self._resolve_alias(
            "forecast_feature_dim", self.forecast_feature_dim, self.future_feature_dim
        )
        self.time_feature_dim = self._resolve_alias(
            "time_feature_dim", self.time_feature_dim, self.lead_time_feature_dim
        )
        if self.model_variant is not None:
            if self.variant != "obs_only_tcn" and self.variant != self.model_variant:
                raise ValueError("variant and model_variant aliases disagree")
            self.variant = self.model_variant

        if self.past_feature_dim < 1:
            raise ValueError("past_feature_dim must be a positive integer")
        if self.forecast_feature_dim < 0:
            raise ValueError("forecast_feature_dim cannot be negative")
        if self.static_feature_dim < 0:
            raise ValueError("static_feature_dim cannot be negative")
        if self.time_feature_dim < 0:
            raise ValueError("time_feature_dim cannot be negative")
        if self.variant not in {"obs_only_tcn", "hybrid_tcn"}:
            raise ValueError(f"unsupported ImpactNet variant: {self.variant!r}")
        if self.variant == "hybrid_tcn" and self.forecast_feature_dim < 1:
            raise ValueError("hybrid_tcn requires forecast_feature_dim > 0")
        for name in (
            "history_hours",
            "forecast_hours",
            "hidden_channels",
            "num_blocks",
            "kernel_size",
            "decoder_hidden_dim",
            "decoder_layers",
            "lead_embedding_dim",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")

        if self.dilations is None:
            self.dilations = tuple(2**index for index in range(self.num_blocks))
        else:
            self.dilations = tuple(int(value) for value in self.dilations)
            if len(self.dilations) != self.num_blocks:
                raise ValueError("num_blocks must equal len(dilations)")
            if any(value < 1 for value in self.dilations):
                raise ValueError("all dilations must be positive")

        if self.water_target_mode is None:
            self.water_target_mode = "residual" if self.variant == "hybrid_tcn" else "absolute"
        if self.water_target_mode not in {"absolute", "residual"}:
            raise ValueError("water_target_mode must be 'absolute' or 'residual'")

        self.history_feature_dim = self.past_feature_dim
        self.future_feature_dim = self.forecast_feature_dim
        self.lead_time_feature_dim = self.time_feature_dim
        self.model_variant = self.variant

    @staticmethod
    def _resolve_alias(name: str, canonical: int, alias: int | None) -> int:
        if alias is None:
            return canonical
        # Zero is the default for optional dimensions, so a positive alias may
        # replace it.  Two non-default values must agree.
        if canonical != 0 and canonical != alias:
            raise ValueError(f"{name} and its alias disagree")
        return alias

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe resolved architecture mapping."""

        result = asdict(self)
        result["dilations"] = list(self.dilations or ())
        return result

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> ImpactNetConfig:
        values = dict(values)
        if values.get("dilations") is not None:
            values["dilations"] = tuple(values["dilations"])
        return cls(**values)


class ImpactNet(nn.Module):
    """Causal TCN with event-hazard and water-quantile outputs."""

    def __init__(self, config: ImpactNetConfig | dict[str, Any]) -> None:
        super().__init__()
        if isinstance(config, dict):
            config = ImpactNetConfig.from_dict(config)
        self.config = config

        past_encoder_dim = config.past_feature_dim
        if config.include_missing_masks:
            past_encoder_dim *= 2
        self.history_encoder = TCNEncoder(
            past_encoder_dim,
            config.hidden_channels,
            dilations=config.dilations or (),
            kernel_size=config.kernel_size,
            dropout=config.dropout,
            normalization=config.normalization,
            activation=config.activation,
        )

        if config.static_feature_dim:
            static_encoder_dim = config.static_feature_dim
            if config.include_missing_masks:
                static_encoder_dim *= 2
            self.static_encoder: StaticEncoder | None = StaticEncoder(
                static_encoder_dim,
                hidden_dim=config.static_hidden_dim,
                output_dim=config.static_context_dim,
                dropout=config.dropout,
            )
            static_context_dim = config.static_context_dim
        else:
            self.static_encoder = None
            static_context_dim = 0

        decoder_forecast_dim = 0
        if config.variant == "hybrid_tcn":
            decoder_forecast_dim = config.forecast_feature_dim
            if config.include_missing_masks:
                decoder_forecast_dim *= 2
        self.lead_decoder = LeadDecoder(
            config.hidden_channels,
            forecast_feature_dim=decoder_forecast_dim,
            static_context_dim=static_context_dim,
            time_feature_dim=config.time_feature_dim,
            max_leads=config.forecast_hours,
            lead_embedding_dim=config.lead_embedding_dim,
            hidden_dim=config.decoder_hidden_dim,
            layers=config.decoder_layers,
            dropout=config.dropout,
        )
        self.hazard_head = HazardHead(config.decoder_hidden_dim)
        self.water_head = WaterQuantileHead(config.decoder_hidden_dim)

    @property
    def variant(self) -> ModelVariant:
        return self.config.variant

    def _clean_with_missing_mask(
        self,
        values: Tensor,
        supplied_missing_mask: Tensor | None,
        *,
        name: str,
    ) -> tuple[Tensor, Tensor]:
        parameter = next(self.parameters())
        values = values.to(device=parameter.device, dtype=parameter.dtype)
        inferred_missing = ~torch.isfinite(values)
        if supplied_missing_mask is not None:
            if tuple(supplied_missing_mask.shape) != tuple(values.shape):
                raise ValueError(f"{name}_missing_mask must match {name} shape")
            inferred_missing = inferred_missing | supplied_missing_mask.to(
                device=values.device, dtype=torch.bool
            )
        clean = torch.where(inferred_missing, torch.zeros_like(values), values)
        return clean, inferred_missing

    @staticmethod
    def _resolve_missing_mask(
        *,
        explicit_missing: Tensor | None,
        observed_aliases: tuple[Tensor | None, ...],
        name: str,
    ) -> Tensor | None:
        observed = [value for value in observed_aliases if value is not None]
        if explicit_missing is not None and observed:
            raise ValueError(f"provide either {name}_missing_mask or an observed mask")
        if len(observed) > 1:
            raise ValueError(f"provide only one observed mask alias for {name}")
        return explicit_missing if not observed else ~observed[0].to(dtype=torch.bool)

    def forward(
        self,
        past_observations: Tensor,
        future_forecasts: Tensor | None = None,
        static_features: Tensor | None = None,
        future_time_features: Tensor | None = None,
        physics_baseline: Tensor | None = None,
        *,
        past_missing_mask: Tensor | None = None,
        future_missing_mask: Tensor | None = None,
        forecast_missing_mask: Tensor | None = None,
        static_missing_mask: Tensor | None = None,
        past_observed_mask: Tensor | None = None,
        future_observed_mask: Tensor | None = None,
        static_observed_mask: Tensor | None = None,
        past_mask: Tensor | None = None,
        future_mask: Tensor | None = None,
        static_mask: Tensor | None = None,
    ) -> dict[str, Tensor]:
        """Run one batch.

        ``*_missing_mask`` uses ``True``/1 for missing values. The
        ``*_observed_mask`` aliases (and dataset-compatible ``past_mask``,
        ``future_mask``, ``static_mask``) use ``True`` for usable values. For
        hybrid inference, every sample must contain at least one usable
        forecast value; an absent forecast must be routed to a separately
        trained ``obs_only_tcn`` checkpoint instead of being represented by
        zeros.
        """

        config = self.config
        if past_observations.ndim != 3:
            raise ValueError("past_observations must have shape [batch, time, features]")
        expected_past = (
            past_observations.shape[0],
            config.history_hours,
            config.past_feature_dim,
        )
        if tuple(past_observations.shape) != expected_past:
            raise ValueError(f"past_observations must have shape {expected_past}")

        resolved_past_missing = self._resolve_missing_mask(
            explicit_missing=past_missing_mask,
            observed_aliases=(past_observed_mask, past_mask),
            name="past",
        )
        past, past_missing = self._clean_with_missing_mask(
            past_observations,
            resolved_past_missing,
            name="past",
        )
        if config.include_missing_masks:
            past = torch.cat((past, past_missing.to(past.dtype)), dim=-1)
        history_context = self.history_encoder.context(past)

        batch = past_observations.shape[0]
        static_context: Tensor | None = None
        if self.static_encoder is not None:
            if static_features is None:
                raise ValueError("static_features are required by this ImpactNet config")
            expected_static = (batch, config.static_feature_dim)
            if tuple(static_features.shape) != expected_static:
                raise ValueError(f"static_features must have shape {expected_static}")
            resolved_static_missing = self._resolve_missing_mask(
                explicit_missing=static_missing_mask,
                observed_aliases=(static_observed_mask, static_mask),
                name="static",
            )
            static, static_missing = self._clean_with_missing_mask(
                static_features,
                resolved_static_missing,
                name="static",
            )
            if config.include_missing_masks:
                static = torch.cat((static, static_missing.to(static.dtype)), dim=-1)
            static_context = self.static_encoder(static)

        time_features: Tensor | None = None
        if config.time_feature_dim:
            if future_time_features is None:
                raise ValueError("future_time_features are required by this config")
            expected_time = (batch, config.forecast_hours, config.time_feature_dim)
            if tuple(future_time_features.shape) != expected_time:
                raise ValueError(f"future_time_features must have shape {expected_time}")
            parameter = next(self.parameters())
            time_features = torch.nan_to_num(
                future_time_features.to(
                    device=parameter.device,
                    dtype=parameter.dtype,
                )
            )

        decoder_forecasts: Tensor | None = None
        if config.variant == "hybrid_tcn":
            if future_forecasts is None:
                raise MissingFutureForecastError(
                    "hybrid_tcn requires issued future forecasts; route absent "
                    "forecasts to an obs_only_tcn checkpoint"
                )
            expected_forecasts = (
                batch,
                config.forecast_hours,
                config.forecast_feature_dim,
            )
            if tuple(future_forecasts.shape) != expected_forecasts:
                raise ValueError(f"future_forecasts must have shape {expected_forecasts}")
            if future_missing_mask is not None and forecast_missing_mask is not None:
                raise ValueError(
                    "provide only one of future_missing_mask and forecast_missing_mask"
                )
            explicit_future_missing = (
                future_missing_mask if future_missing_mask is not None else forecast_missing_mask
            )
            supplied_mask = self._resolve_missing_mask(
                explicit_missing=explicit_future_missing,
                observed_aliases=(future_observed_mask, future_mask),
                name="future",
            )
            if supplied_mask is None:
                # Filled forecast windows commonly use all-zero sentinels. A
                # hybrid model cannot distinguish that sentinel from valid
                # standardised zeros unless availability is explicit, so fail
                # closed and require a mask for zero-only samples.
                finite_zero = torch.isfinite(future_forecasts) & (future_forecasts == 0)
                zero_only = finite_zero.flatten(start_dim=1).all(dim=1)
                if zero_only.any():
                    sample_indices = zero_only.nonzero(as_tuple=False).flatten().tolist()
                    raise MissingFutureForecastError(
                        "hybrid_tcn received zero-only forecasts without an availability "
                        f"mask for batch samples {sample_indices}; use degraded_obs_only "
                        "or provide future_mask"
                    )
            forecasts, missing = self._clean_with_missing_mask(
                future_forecasts,
                supplied_mask,
                name="future",
            )
            unavailable = missing.flatten(start_dim=1).all(dim=1)
            if unavailable.any():
                sample_indices = unavailable.nonzero(as_tuple=False).flatten().tolist()
                raise MissingFutureForecastError(
                    "hybrid_tcn received completely absent forecasts for batch "
                    f"samples {sample_indices}; use degraded_obs_only"
                )
            decoder_forecasts = forecasts
            if config.include_missing_masks:
                decoder_forecasts = torch.cat(
                    (decoder_forecasts, missing.to(decoder_forecasts.dtype)), dim=-1
                )

        lead_hidden = self.lead_decoder(
            history_context,
            leads=config.forecast_hours,
            forecast_features=decoder_forecasts,
            static_context=static_context,
            time_features=time_features,
        )
        hazard_logits = self.hazard_head(lead_hidden)

        baseline: Tensor | None = None
        if config.water_target_mode == "residual":
            if physics_baseline is None:
                raise ValueError("physics_baseline is required when water_target_mode='residual'")
            baseline = physics_baseline.to(
                device=lead_hidden.device,
                dtype=lead_hidden.dtype,
            )
        water_quantiles = self.water_head(lead_hidden, baseline=baseline)
        return {
            "hazard_logits": hazard_logits,
            "cumulative_event_probability": cumulative_event_probability(hazard_logits),
            "water_quantiles": water_quantiles,
        }


def build_obs_only_tcn(**config_values: Any) -> ImpactNet:
    """Construct an observation-only ImpactNet with resolved config."""

    return ImpactNet(ImpactNetConfig(variant="obs_only_tcn", **config_values))


def build_hybrid_tcn(**config_values: Any) -> ImpactNet:
    """Construct a hybrid ImpactNet with resolved config."""

    return ImpactNet(ImpactNetConfig(variant="hybrid_tcn", **config_values))


__all__ = [
    "ImpactNet",
    "ImpactNetConfig",
    "MissingFutureForecastError",
    "ModelVariant",
    "WaterTargetMode",
    "build_hybrid_tcn",
    "build_obs_only_tcn",
]
