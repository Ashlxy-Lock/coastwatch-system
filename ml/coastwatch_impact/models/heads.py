"""Shared lead decoder and constrained ImpactNet output heads."""

from __future__ import annotations

import torch
from torch import Tensor, nn
from torch.nn import functional as F


def cumulative_event_probability(hazard_logits: Tensor) -> Tensor:
    """Convert conditional onset logits into stable cumulative probabilities.

    ``logsigmoid(-x)`` is ``log(1 - sigmoid(x))`` without the cancellation
    that makes the direct product unstable for extreme logits.
    """

    if hazard_logits.ndim < 1:
        raise ValueError("hazard_logits must have at least one dimension")
    log_survival = torch.cumsum(F.logsigmoid(-hazard_logits), dim=-1)
    return 1.0 - torch.exp(log_survival)


class StaticEncoder(nn.Module):
    """Small MLP for site/zone attributes."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 64,
        output_dim: int = 32,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        if input_dim < 1:
            raise ValueError("StaticEncoder input_dim must be positive")
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, features: Tensor) -> Tensor:
        if features.ndim != 2 or features.shape[-1] != self.input_dim:
            raise ValueError(f"static features must have shape [batch, {self.input_dim}]")
        return self.network(features)


class LeadDecoder(nn.Module):
    """Decode shared history/static context separately for every future lead."""

    def __init__(
        self,
        history_dim: int,
        *,
        forecast_feature_dim: int = 0,
        static_context_dim: int = 0,
        time_feature_dim: int = 0,
        max_leads: int = 24,
        lead_embedding_dim: int = 8,
        hidden_dim: int = 128,
        layers: int = 2,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        if history_dim < 1:
            raise ValueError("history_dim must be positive")
        if min(forecast_feature_dim, static_context_dim, time_feature_dim) < 0:
            raise ValueError("feature dimensions cannot be negative")
        if max_leads < 1 or lead_embedding_dim < 1 or hidden_dim < 1:
            raise ValueError("lead and hidden dimensions must be positive")
        if layers < 1:
            raise ValueError("layers must be at least one")
        self.forecast_feature_dim = forecast_feature_dim
        self.static_context_dim = static_context_dim
        self.time_feature_dim = time_feature_dim
        self.max_leads = max_leads
        self.lead_embedding = nn.Embedding(max_leads, lead_embedding_dim)

        input_dim = (
            history_dim
            + forecast_feature_dim
            + static_context_dim
            + time_feature_dim
            + lead_embedding_dim
        )
        modules: list[nn.Module] = []
        current_dim = input_dim
        for _ in range(layers):
            modules.extend(
                [
                    nn.Linear(current_dim, hidden_dim),
                    nn.GELU(),
                    nn.Dropout(dropout),
                ]
            )
            current_dim = hidden_dim
        self.network = nn.Sequential(*modules)
        self.output_dim = hidden_dim

    def forward(
        self,
        history_context: Tensor,
        *,
        leads: int,
        forecast_features: Tensor | None = None,
        static_context: Tensor | None = None,
        time_features: Tensor | None = None,
    ) -> Tensor:
        if history_context.ndim != 2:
            raise ValueError("history_context must have shape [batch, channels]")
        if not 1 <= leads <= self.max_leads:
            raise ValueError(f"leads must be between 1 and {self.max_leads}")
        batch = history_context.shape[0]
        pieces = [history_context.unsqueeze(1).expand(-1, leads, -1)]

        if self.static_context_dim:
            if static_context is None:
                raise ValueError("static_context is required by this decoder")
            expected_static = (batch, self.static_context_dim)
            if tuple(static_context.shape) != expected_static:
                raise ValueError(f"static_context must have shape {expected_static}")
            pieces.append(static_context.unsqueeze(1).expand(-1, leads, -1))

        if self.forecast_feature_dim:
            if forecast_features is None:
                raise ValueError("forecast_features are required by this decoder")
            expected_forecast = (batch, leads, self.forecast_feature_dim)
            if tuple(forecast_features.shape) != expected_forecast:
                raise ValueError(f"forecast_features must have shape {expected_forecast}")
            pieces.append(forecast_features)

        if self.time_feature_dim:
            if time_features is None:
                raise ValueError("time_features are required by this decoder")
            expected_time = (batch, leads, self.time_feature_dim)
            if tuple(time_features.shape) != expected_time:
                raise ValueError(f"time_features must have shape {expected_time}")
            pieces.append(time_features)

        lead_indices = torch.arange(leads, device=history_context.device)
        lead_embedding = self.lead_embedding(lead_indices)
        pieces.append(lead_embedding.unsqueeze(0).expand(batch, -1, -1))
        return self.network(torch.cat(pieces, dim=-1))


class HazardHead(nn.Module):
    """Conditional onset hazard logits for each decoded lead."""

    def __init__(self, input_dim: int) -> None:
        super().__init__()
        self.projection = nn.Linear(input_dim, 1)

    def forward(self, lead_hidden: Tensor) -> Tensor:
        if lead_hidden.ndim != 3:
            raise ValueError("lead_hidden must have shape [batch, lead, features]")
        return self.projection(lead_hidden).squeeze(-1)


class WaterQuantileHead(nn.Module):
    """Non-crossing P10/P50/P90 water-level head."""

    quantiles = (0.1, 0.5, 0.9)

    def __init__(self, input_dim: int) -> None:
        super().__init__()
        self.projection = nn.Linear(input_dim, 3)

    def forward(
        self,
        lead_hidden: Tensor,
        *,
        baseline: Tensor | None = None,
    ) -> Tensor:
        if lead_hidden.ndim != 3:
            raise ValueError("lead_hidden must have shape [batch, lead, features]")
        raw = self.projection(lead_hidden)
        q50 = raw[..., 0]
        q10 = q50 - F.softplus(raw[..., 1])
        q90 = q50 + F.softplus(raw[..., 2])
        quantiles = torch.stack((q10, q50, q90), dim=-1)
        if baseline is not None:
            if baseline.ndim == 3 and baseline.shape[-1] == 1:
                baseline = baseline.squeeze(-1)
            if tuple(baseline.shape) != tuple(lead_hidden.shape[:2]):
                raise ValueError("water baseline must have shape [batch, lead] or [batch, lead, 1]")
            quantiles = quantiles + baseline.unsqueeze(-1)
        return quantiles


__all__ = [
    "HazardHead",
    "LeadDecoder",
    "StaticEncoder",
    "WaterQuantileHead",
    "cumulative_event_probability",
]
