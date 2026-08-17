"""Validation-only temperature calibration for discrete-time hazards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray: TypeAlias = NDArray[np.float64]


def stable_sigmoid(values: ArrayLike) -> FloatArray:
    """Return a finite sigmoid without overflowing for extreme logits."""

    logits = np.asarray(values, dtype=np.float64)
    result = np.empty_like(logits)
    positive = logits >= 0
    result[positive] = 1.0 / (1.0 + np.exp(-logits[positive]))
    exp_values = np.exp(logits[~positive])
    result[~positive] = exp_values / (1.0 + exp_values)
    return result


def cumulative_event_probability(hazard_logits: ArrayLike) -> FloatArray:
    """Convert conditional hazard logits to stable cumulative probabilities.

    The final axis is interpreted as lead time.  Computing log survival with
    ``-logaddexp(0, logit)`` avoids cancellation and guarantees that the
    cumulative probability is monotone apart from harmless floating-point
    round-off, which is removed explicitly before returning.
    """

    logits = np.asarray(hazard_logits, dtype=np.float64)
    if logits.ndim < 1 or logits.shape[-1] < 1:
        raise ValueError("hazard_logits must have a non-empty lead dimension")
    if not np.isfinite(logits).all():
        raise ValueError("hazard_logits must be finite")
    log_survival = np.cumsum(-np.logaddexp(0.0, logits), axis=-1)
    cumulative = -np.expm1(log_survival)
    cumulative = np.maximum.accumulate(cumulative, axis=-1)
    return np.clip(cumulative, 0.0, 1.0)


def _masked_binary_nll(
    logits: FloatArray,
    targets: FloatArray,
    mask: NDArray[np.bool_],
) -> float:
    selected_logits = logits[mask]
    selected_targets = targets[mask]
    # softplus(x) - y*x is BCEWithLogits and remains stable at extreme x.
    losses = np.logaddexp(0.0, selected_logits) - selected_targets * selected_logits
    return float(np.mean(losses))


def _prepare_calibration_arrays(
    hazard_logits: ArrayLike,
    hazard_targets: ArrayLike,
    hazard_mask: ArrayLike | None,
) -> tuple[FloatArray, FloatArray, NDArray[np.bool_]]:
    logits = np.asarray(hazard_logits, dtype=np.float64)
    targets = np.asarray(hazard_targets, dtype=np.float64)
    if logits.shape != targets.shape or logits.ndim < 1:
        raise ValueError("hazard_logits and hazard_targets must have identical shapes")
    mask = (
        np.ones(logits.shape, dtype=np.bool_)
        if hazard_mask is None
        else np.asarray(hazard_mask, dtype=np.bool_)
    )
    if mask.shape != logits.shape:
        raise ValueError("hazard_mask must match hazard_logits")
    mask &= np.isfinite(logits) & np.isfinite(targets)
    if not np.any(mask):
        raise ValueError("temperature calibration has no valid observations")
    if np.any((targets[mask] < 0.0) | (targets[mask] > 1.0)):
        raise ValueError("hazard targets must lie in [0, 1]")
    return logits, targets, mask


@dataclass(frozen=True)
class TemperatureScaler:
    """A single positive temperature fitted on validation hazard logits."""

    temperature: float
    fitted_split: str = "validation"
    method: str = "global_temperature"

    def __post_init__(self) -> None:
        if not np.isfinite(self.temperature) or self.temperature <= 0.0:
            raise ValueError("temperature must be finite and positive")
        if self.fitted_split != "validation":
            raise ValueError("temperature scalers may only be fitted on validation")

    def transform_logits(self, hazard_logits: ArrayLike) -> FloatArray:
        logits = np.asarray(hazard_logits, dtype=np.float64)
        if not np.isfinite(logits).all():
            raise ValueError("hazard_logits must be finite")
        return logits / self.temperature

    def hazard_probabilities(self, hazard_logits: ArrayLike) -> FloatArray:
        return stable_sigmoid(self.transform_logits(hazard_logits))

    def cumulative_probabilities(self, hazard_logits: ArrayLike) -> FloatArray:
        return cumulative_event_probability(self.transform_logits(hazard_logits))

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "temperature": self.temperature,
            "fitted_split": self.fitted_split,
        }


def fit_global_temperature(
    hazard_logits: ArrayLike,
    hazard_targets: ArrayLike,
    hazard_mask: ArrayLike | None = None,
    *,
    split: str,
    minimum_temperature: float = 0.05,
    maximum_temperature: float = 20.0,
    iterations: int = 96,
) -> TemperatureScaler:
    """Fit one temperature by minimizing masked validation hazard NLL.

    The API intentionally requires an explicit split and rejects every split
    except validation.  In particular, test predictions can never be used to
    fit or revise the calibrator.
    """

    if split != "validation":
        raise ValueError("global temperature fitting is validation-only")
    if (
        not np.isfinite(minimum_temperature)
        or not np.isfinite(maximum_temperature)
        or minimum_temperature <= 0.0
        or maximum_temperature <= minimum_temperature
    ):
        raise ValueError("temperature bounds must be finite, positive, and ordered")
    if iterations < 8:
        raise ValueError("iterations must be at least 8")

    logits, targets, mask = _prepare_calibration_arrays(hazard_logits, hazard_targets, hazard_mask)
    lower = float(np.log(minimum_temperature))
    upper = float(np.log(maximum_temperature))
    inverse_phi = (np.sqrt(5.0) - 1.0) / 2.0

    def objective(log_temperature: float) -> float:
        return _masked_binary_nll(logits / float(np.exp(log_temperature)), targets, mask)

    left = upper - inverse_phi * (upper - lower)
    right = lower + inverse_phi * (upper - lower)
    left_value = objective(left)
    right_value = objective(right)
    for _ in range(iterations):
        if left_value <= right_value:
            upper = right
            right = left
            right_value = left_value
            left = upper - inverse_phi * (upper - lower)
            left_value = objective(left)
        else:
            lower = left
            left = right
            left_value = right_value
            right = lower + inverse_phi * (upper - lower)
            right_value = objective(right)

    fitted = float(np.exp((lower + upper) / 2.0))
    # A numerical optimizer must never make the persisted calibrator worse than
    # the identity transform on the data it was fitted to.
    if objective(0.0) <= objective(float(np.log(fitted))) + 1e-12:
        fitted = 1.0
    return TemperatureScaler(temperature=fitted)


# Concise aliases for report/training callers.
fit_temperature = fit_global_temperature
hazard_logits_to_cumulative = cumulative_event_probability


__all__ = [
    "TemperatureScaler",
    "cumulative_event_probability",
    "fit_global_temperature",
    "fit_temperature",
    "hazard_logits_to_cumulative",
    "stable_sigmoid",
]
