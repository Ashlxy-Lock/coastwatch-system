"""Masked event and water-level objectives for ImpactNet."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

import torch
from torch import Tensor, nn
from torch.nn import functional as F


def _as_broadcast_tensor(value: Tensor, reference: Tensor, *, name: str) -> Tensor:
    value = value.to(device=reference.device, dtype=reference.dtype)
    if value.ndim == 1 and reference.ndim >= 2 and value.shape[0] == reference.shape[0]:
        value = value.reshape(value.shape[0], *([1] * (reference.ndim - 1)))
    try:
        return torch.broadcast_to(value, reference.shape)
    except RuntimeError as error:
        raise ValueError(f"{name} cannot broadcast to loss shape") from error


def masked_mean(
    loss: Tensor,
    mask: Tensor,
    weight: Tensor | None = None,
    *,
    eps: float = 1e-8,
) -> Tensor:
    """Mean over valid entries, returning differentiable zero if none exist."""

    valid_weight = _as_broadcast_tensor(mask, loss, name="mask")
    valid_weight = (valid_weight > 0).to(loss.dtype)
    if weight is not None:
        broadcast_weight = _as_broadcast_tensor(weight, loss, name="weight")
        if torch.any(broadcast_weight < 0):
            raise ValueError("weight cannot contain negative values")
        valid_weight = valid_weight * broadcast_weight
    safe_loss = torch.where(valid_weight > 0, loss, torch.zeros_like(loss))
    denominator = valid_weight.sum().clamp_min(eps)
    return (safe_loss * valid_weight).sum() / denominator


def masked_bce_hazard_loss(
    hazard_logits: Tensor,
    hazard_target: Tensor,
    hazard_mask: Tensor,
    *,
    pos_weight: Tensor | float | None = None,
    sample_weight: Tensor | None = None,
) -> Tensor:
    """Masked ``BCEWithLogits`` for discrete conditional onset hazards."""

    if tuple(hazard_logits.shape) != tuple(hazard_target.shape):
        raise ValueError("hazard_logits and hazard_target must have equal shapes")
    if tuple(hazard_mask.shape) != tuple(hazard_logits.shape):
        try:
            torch.broadcast_shapes(hazard_mask.shape, hazard_logits.shape)
        except RuntimeError as error:
            raise ValueError("hazard_mask cannot broadcast to logits") from error
    mask = torch.broadcast_to(
        hazard_mask.to(device=hazard_logits.device, dtype=torch.bool),
        hazard_logits.shape,
    )
    # Missing targets are often NaN.  Replacing invalid positions before BCE
    # avoids the undefined ``NaN * 0`` pattern.
    clean_logits = torch.where(mask, hazard_logits, torch.zeros_like(hazard_logits))
    clean_target = torch.where(
        mask,
        hazard_target.to(device=hazard_logits.device, dtype=hazard_logits.dtype),
        torch.zeros_like(hazard_logits),
    )
    pos_weight_tensor: Tensor | None
    if pos_weight is None:
        pos_weight_tensor = None
    else:
        pos_weight_tensor = torch.as_tensor(
            pos_weight,
            device=hazard_logits.device,
            dtype=hazard_logits.dtype,
        )
    raw = F.binary_cross_entropy_with_logits(
        clean_logits,
        clean_target,
        reduction="none",
        pos_weight=pos_weight_tensor,
    )
    return masked_mean(raw, mask, sample_weight)


def masked_quantile_loss(
    water_quantiles: Tensor,
    water_target: Tensor,
    water_mask: Tensor,
    *,
    quantiles: Sequence[float] = (0.1, 0.5, 0.9),
    sample_weight: Tensor | None = None,
) -> Tensor:
    """Masked pinball loss for a final quantile dimension."""

    if water_quantiles.ndim < 2:
        raise ValueError("water_quantiles must include a quantile dimension")
    if water_quantiles.shape[-1] != len(quantiles):
        raise ValueError("water_quantiles final dimension must match quantiles")
    if any(not 0.0 < float(value) < 1.0 for value in quantiles):
        raise ValueError("quantiles must lie strictly between zero and one")

    expected_target_shape = water_quantiles.shape[:-1]
    if water_target.ndim == water_quantiles.ndim and water_target.shape[-1] == 1:
        water_target = water_target.squeeze(-1)
    if tuple(water_target.shape) != tuple(expected_target_shape):
        raise ValueError("water_target must match water_quantiles without final axis")

    target = water_target.to(device=water_quantiles.device, dtype=water_quantiles.dtype)
    try:
        base_mask = torch.broadcast_to(
            water_mask.to(device=water_quantiles.device, dtype=torch.bool),
            expected_target_shape,
        )
    except RuntimeError as error:
        raise ValueError("water_mask cannot broadcast to target shape") from error
    clean_target = torch.where(base_mask, target, torch.zeros_like(target))
    clean_predictions = torch.where(
        base_mask.unsqueeze(-1),
        water_quantiles,
        torch.zeros_like(water_quantiles),
    )
    residual = clean_target.unsqueeze(-1) - clean_predictions
    tau = torch.as_tensor(
        quantiles,
        device=water_quantiles.device,
        dtype=water_quantiles.dtype,
    )
    raw = torch.maximum(tau * residual, (tau - 1.0) * residual)
    expanded_mask = base_mask.unsqueeze(-1).expand_as(raw)

    expanded_weight: Tensor | None = None
    if sample_weight is not None:
        weight = sample_weight
        if weight.ndim == len(expected_target_shape) - 1:
            weight = weight.unsqueeze(-1)
        try:
            weight = torch.broadcast_to(weight, expected_target_shape)
        except RuntimeError as error:
            raise ValueError("sample_weight cannot broadcast to water target") from error
        expanded_weight = weight.unsqueeze(-1).expand_as(raw)
    return masked_mean(raw, expanded_mask, expanded_weight)


def multitask_loss(
    *,
    hazard_logits: Tensor,
    hazard_target: Tensor,
    hazard_mask: Tensor,
    water_quantiles: Tensor,
    water_target: Tensor,
    water_mask: Tensor,
    event_weight: float = 1.0,
    water_weight: float = 0.4,
    pos_weight: Tensor | float | None = None,
    event_sample_weight: Tensor | None = None,
    water_sample_weight: Tensor | None = None,
    auxiliary_losses: Mapping[str, Tensor] | None = None,
    auxiliary_weights: Mapping[str, float] | None = None,
) -> dict[str, Tensor]:
    """Combine masked event, water and optional already-computed aux losses."""

    if event_weight < 0 or water_weight < 0:
        raise ValueError("task weights cannot be negative")
    event = masked_bce_hazard_loss(
        hazard_logits,
        hazard_target,
        hazard_mask,
        pos_weight=pos_weight,
        sample_weight=event_sample_weight,
    )
    water = masked_quantile_loss(
        water_quantiles,
        water_target,
        water_mask,
        sample_weight=water_sample_weight,
    )
    total = event_weight * event + water_weight * water
    auxiliary_total = total.new_zeros(())
    for name, aux_loss in (auxiliary_losses or {}).items():
        weight = float((auxiliary_weights or {}).get(name, 1.0))
        if weight < 0:
            raise ValueError(f"auxiliary weight for {name!r} cannot be negative")
        auxiliary_total = auxiliary_total + weight * aux_loss
    total = total + auxiliary_total
    return {
        "loss": total,
        "total_loss": total,
        "event_loss": event,
        "water_loss": water,
        "auxiliary_loss": auxiliary_total,
    }


class MultiTaskLoss(nn.Module):
    """Configurable module wrapper around :func:`multitask_loss`."""

    def __init__(
        self,
        *,
        event_weight: float = 1.0,
        water_weight: float = 0.4,
        pos_weight: Tensor | float | None = None,
    ) -> None:
        super().__init__()
        self.event_weight = event_weight
        self.water_weight = water_weight
        if pos_weight is None:
            self.register_buffer("pos_weight", None)
        else:
            self.register_buffer("pos_weight", torch.as_tensor(pos_weight))

    def forward(
        self,
        *,
        hazard_logits: Tensor,
        hazard_target: Tensor,
        hazard_mask: Tensor,
        water_quantiles: Tensor,
        water_target: Tensor,
        water_mask: Tensor,
        event_sample_weight: Tensor | None = None,
        water_sample_weight: Tensor | None = None,
    ) -> dict[str, Tensor]:
        return multitask_loss(
            hazard_logits=hazard_logits,
            hazard_target=hazard_target,
            hazard_mask=hazard_mask,
            water_quantiles=water_quantiles,
            water_target=water_target,
            water_mask=water_mask,
            event_sample_weight=event_sample_weight,
            water_sample_weight=water_sample_weight,
            event_weight=self.event_weight,
            water_weight=self.water_weight,
            pos_weight=cast(Tensor | None, self.pos_weight),
        )


# Descriptive aliases retained for callers that use noun-first naming.
masked_hazard_bce_loss = masked_bce_hazard_loss
quantile_loss = masked_quantile_loss
ImpactNetLoss = MultiTaskLoss


__all__ = [
    "ImpactNetLoss",
    "MultiTaskLoss",
    "masked_bce_hazard_loss",
    "masked_hazard_bce_loss",
    "masked_mean",
    "masked_quantile_loss",
    "multitask_loss",
    "quantile_loss",
]
