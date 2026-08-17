"""Probability and classification metrics at configured event horizons."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias

import numpy as np
from numpy.typing import ArrayLike, NDArray
from sklearn.metrics import average_precision_score, roc_auc_score

FloatArray: TypeAlias = NDArray[np.float64]
BoolArray: TypeAlias = NDArray[np.bool_]


def expected_calibration_error(
    targets: ArrayLike,
    probabilities: ArrayLike,
    *,
    bins: int = 10,
) -> tuple[float, list[dict[str, Any]]]:
    """Return equal-width ECE and auditable reliability-bin contents."""

    if bins < 2:
        raise ValueError("ECE requires at least two bins")
    truth = np.asarray(targets, dtype=np.float64).reshape(-1)
    probability = np.asarray(probabilities, dtype=np.float64).reshape(-1)
    if truth.shape != probability.shape or truth.size == 0:
        raise ValueError("targets and probabilities must be non-empty and aligned")
    if not np.isfinite(truth).all() or not np.isfinite(probability).all():
        raise ValueError("ECE inputs must be finite")
    if np.any((truth < 0.0) | (truth > 1.0)):
        raise ValueError("targets must lie in [0, 1]")
    if np.any((probability < 0.0) | (probability > 1.0)):
        raise ValueError("probabilities must lie in [0, 1]")

    assignments = np.minimum((probability * bins).astype(np.int64), bins - 1)
    details: list[dict[str, Any]] = []
    ece = 0.0
    for index in range(bins):
        selected = assignments == index
        count = int(selected.sum())
        confidence = float(probability[selected].mean()) if count else None
        observed = float(truth[selected].mean()) if count else None
        if count:
            assert confidence is not None and observed is not None
            ece += count / truth.size * abs(confidence - observed)
        details.append(
            {
                "bin": index,
                "lower": index / bins,
                "upper": (index + 1) / bins,
                "count": count,
                "mean_probability": confidence,
                "observed_frequency": observed,
            }
        )
    return float(ece), details


def cumulative_targets_from_hazards(
    hazard_targets: ArrayLike,
    hazard_mask: ArrayLike | None = None,
) -> tuple[FloatArray, BoolArray]:
    """Build horizon event targets while preserving unknown intervals.

    A horizon is known positive as soon as a known onset is observed.  A known
    negative requires every lead through that horizon to be observed and zero.
    This prevents an unknown hour from silently becoming a negative target.
    """

    targets = np.asarray(hazard_targets, dtype=np.float64)
    if targets.ndim != 2 or targets.shape[1] < 1:
        raise ValueError("hazard_targets must have shape [sample, lead]")
    mask = (
        np.ones(targets.shape, dtype=np.bool_)
        if hazard_mask is None
        else np.asarray(hazard_mask, dtype=np.bool_)
    )
    if mask.shape != targets.shape:
        raise ValueError("hazard_mask must match hazard_targets")
    finite = np.isfinite(targets)
    mask &= finite
    observed = targets[mask]
    if np.any((observed != 0.0) & (observed != 1.0)):
        raise ValueError("evaluation hazard targets must be binary where observed")

    positive = mask & (targets == 1.0)
    positive_seen = np.maximum.accumulate(positive, axis=1)
    all_known = np.logical_and.accumulate(mask, axis=1)
    cumulative_mask = positive_seen | all_known
    cumulative_target = positive_seen.astype(np.float64)
    return cumulative_target, cumulative_mask


def _threshold_for_horizon(threshold: float | Mapping[int | str, float], horizon: int) -> float:
    if isinstance(threshold, Mapping):
        if horizon in threshold:
            value = threshold[horizon]
        elif f"{horizon}h" in threshold:
            value = threshold[f"{horizon}h"]
        else:
            raise KeyError(f"no classification threshold for {horizon}h")
    else:
        value = threshold
    value = float(value)
    if not 0.0 <= value <= 1.0 or not np.isfinite(value):
        raise ValueError("classification thresholds must lie in [0, 1]")
    return value


def _binary_metrics(
    targets: FloatArray,
    probabilities: FloatArray,
    *,
    threshold: float,
    ece_bins: int,
) -> dict[str, Any]:
    labels = targets.astype(np.int8)
    predicted = probabilities >= threshold
    positives = int(labels.sum())
    negatives = int(labels.size - positives)
    true_positive = int(np.sum(predicted & (labels == 1)))
    false_positive = int(np.sum(predicted & (labels == 0)))
    false_negative = int(np.sum(~predicted & (labels == 1)))

    precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    )
    recall = true_positive / positives if positives else None
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if recall is not None and precision + recall > 0.0
        else (0.0 if recall is not None else None)
    )
    clipped = np.clip(probabilities, 1e-12, 1.0 - 1e-12)
    nll = -np.mean(labels * np.log(clipped) + (1 - labels) * np.log1p(-clipped))
    ece, reliability = expected_calibration_error(labels, probabilities, bins=ece_bins)

    pr_auc = float(average_precision_score(labels, probabilities)) if positives else None
    roc_auc = float(roc_auc_score(labels, probabilities)) if positives and negatives else None
    return {
        "samples": int(labels.size),
        "positives": positives,
        "negatives": negatives,
        "pr_auc": pr_auc,
        "roc_auc": roc_auc,
        "brier": float(np.mean((probabilities - labels) ** 2)),
        "nll": float(nll),
        "ece": ece,
        "threshold": threshold,
        "precision": float(precision),
        "recall": None if recall is None else float(recall),
        "f1": None if f1 is None else float(f1),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "reliability_bins": reliability,
    }


def compute_horizon_metrics(
    cumulative_probabilities: ArrayLike,
    hazard_targets: ArrayLike,
    hazard_mask: ArrayLike | None = None,
    *,
    horizons: Sequence[int] = (1, 3, 6, 12, 24),
    threshold: float | Mapping[int | str, float] = 0.5,
    ece_bins: int = 10,
) -> dict[str, dict[str, Any]]:
    """Evaluate cumulative event probabilities at configured lead horizons."""

    probabilities = np.asarray(cumulative_probabilities, dtype=np.float64)
    if probabilities.ndim != 2 or probabilities.shape[1] < 1:
        raise ValueError("cumulative_probabilities must have shape [sample, lead]")
    if not np.isfinite(probabilities).all():
        raise ValueError("cumulative probabilities must be finite")
    if np.any((probabilities < 0.0) | (probabilities > 1.0)):
        raise ValueError("cumulative probabilities must lie in [0, 1]")
    if np.any(np.diff(probabilities, axis=1) < -1e-12):
        raise ValueError("cumulative probabilities must not decrease with horizon")
    cumulative_target, cumulative_mask = cumulative_targets_from_hazards(
        hazard_targets, hazard_mask
    )
    if cumulative_target.shape != probabilities.shape:
        raise ValueError("probabilities and hazard targets must have identical shapes")

    requested = tuple(int(value) for value in horizons)
    if not requested or tuple(sorted(set(requested))) != requested:
        raise ValueError("horizons must be unique and increasing")
    if requested[0] < 1 or requested[-1] > probabilities.shape[1]:
        raise ValueError("horizon is outside the model lead range")

    output: dict[str, dict[str, Any]] = {}
    for horizon in requested:
        index = horizon - 1
        selected = cumulative_mask[:, index]
        if not np.any(selected):
            output[f"{horizon}h"] = {
                "samples": 0,
                "positives": 0,
                "negatives": 0,
                "insufficient_evidence": True,
            }
            continue
        result = _binary_metrics(
            cumulative_target[selected, index],
            probabilities[selected, index],
            threshold=_threshold_for_horizon(threshold, horizon),
            ece_bins=ece_bins,
        )
        result["insufficient_evidence"] = result["positives"] == 0
        output[f"{horizon}h"] = result
    return output


horizon_metrics = compute_horizon_metrics


__all__ = [
    "compute_horizon_metrics",
    "cumulative_targets_from_hazards",
    "expected_calibration_error",
    "horizon_metrics",
]
