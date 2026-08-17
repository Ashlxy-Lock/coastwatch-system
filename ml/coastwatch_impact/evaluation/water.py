"""Masked metrics for non-crossing P10/P50/P90 water forecasts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

QUANTILES = (0.1, 0.5, 0.9)


def _pinball(target: np.ndarray, prediction: np.ndarray, quantile: float) -> float:
    residual = target - prediction
    return float(np.mean(np.maximum(quantile * residual, (quantile - 1.0) * residual)))


def _point_metrics(target: np.ndarray, prediction: np.ndarray) -> dict[str, Any]:
    if target.size == 0:
        return {
            "valid_points": 0,
            "p50_mae": None,
            "p50_rmse": None,
            "pinball_p10": None,
            "pinball_p50": None,
            "pinball_p90": None,
            "p10_p90_empirical_coverage": None,
            "mean_interval_width": None,
        }
    q10, q50, q90 = (prediction[:, index] for index in range(3))
    error = q50 - target
    return {
        "valid_points": int(target.size),
        "p50_mae": float(np.mean(np.abs(error))),
        "p50_rmse": float(np.sqrt(np.mean(error**2))),
        "pinball_p10": _pinball(target, q10, 0.1),
        "pinball_p50": _pinball(target, q50, 0.5),
        "pinball_p90": _pinball(target, q90, 0.9),
        "p10_p90_empirical_coverage": float(np.mean((target >= q10) & (target <= q90))),
        "mean_interval_width": float(np.mean(q90 - q10)),
    }


def water_quantile_metrics(
    water_targets: ArrayLike,
    water_quantiles: ArrayLike,
    water_mask: ArrayLike | None = None,
    *,
    lead_hours: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Compute point, interval, peak-level, and peak-time water metrics."""

    targets = np.asarray(water_targets, dtype=np.float64)
    predictions = np.asarray(water_quantiles, dtype=np.float64)
    if targets.ndim != 2:
        raise ValueError("water_targets must have shape [sample, lead]")
    if predictions.shape != (*targets.shape, 3):
        raise ValueError("water_quantiles must have shape [sample, lead, 3]")
    mask = (
        np.ones(targets.shape, dtype=np.bool_)
        if water_mask is None
        else np.asarray(water_mask, dtype=np.bool_)
    )
    if mask.shape != targets.shape:
        raise ValueError("water_mask must match water_targets")
    if np.any(mask & ~np.isfinite(targets)):
        raise ValueError("masked-in water targets must be finite")
    if np.any(mask[..., None] & ~np.isfinite(predictions)):
        raise ValueError("masked-in water quantiles must be finite")
    if np.any(mask & (predictions[..., 0] > predictions[..., 1] + 1e-12)) or np.any(
        mask & (predictions[..., 1] > predictions[..., 2] + 1e-12)
    ):
        raise ValueError("water quantiles cross: expected P10 <= P50 <= P90")

    leads = (
        tuple(range(1, targets.shape[1] + 1))
        if lead_hours is None
        else tuple(int(value) for value in lead_hours)
    )
    if len(leads) != targets.shape[1] or any(value < 1 for value in leads):
        raise ValueError("lead_hours must contain one positive value per lead")

    valid_targets = targets[mask]
    valid_predictions = predictions[mask]
    overall = _point_metrics(valid_targets, valid_predictions)
    by_lead: dict[str, dict[str, Any]] = {}
    for index, lead in enumerate(leads):
        selected = mask[:, index]
        by_lead[f"{lead}h"] = _point_metrics(
            targets[selected, index], predictions[selected, index, :]
        )

    peak_level_errors: list[float] = []
    peak_time_errors: list[float] = []
    for sample_index in range(targets.shape[0]):
        valid_indices = np.flatnonzero(mask[sample_index])
        if valid_indices.size == 0:
            continue
        observed_values = targets[sample_index, valid_indices]
        predicted_values = predictions[sample_index, valid_indices, 1]
        observed_peak_offset = int(np.argmax(observed_values))
        predicted_peak_offset = int(np.argmax(predicted_values))
        observed_peak_index = int(valid_indices[observed_peak_offset])
        predicted_peak_index = int(valid_indices[predicted_peak_offset])
        peak_level_errors.append(
            abs(
                float(predictions[sample_index, predicted_peak_index, 1])
                - float(targets[sample_index, observed_peak_index])
            )
        )
        peak_time_errors.append(
            abs(float(leads[predicted_peak_index] - leads[observed_peak_index]))
        )

    return {
        **overall,
        "quantiles": list(QUANTILES),
        "samples": int(targets.shape[0]),
        "peak_samples": len(peak_level_errors),
        "peak_water_level_mae": (float(np.mean(peak_level_errors)) if peak_level_errors else None),
        "peak_time_mae_hours": (float(np.mean(peak_time_errors)) if peak_time_errors else None),
        "by_lead": by_lead,
        "insufficient_evidence": valid_targets.size == 0,
    }


compute_water_metrics = water_quantile_metrics


__all__ = ["compute_water_metrics", "water_quantile_metrics"]
