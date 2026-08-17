"""Cluster bootstrap confidence intervals using whole storm groups."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

MetricValue = float | int | np.floating[Any] | np.integer[Any] | None
MetricFunction = Callable[[pd.DataFrame], MetricValue | Mapping[str, MetricValue]]


def _normalise_metrics(
    value: MetricValue | Mapping[str, MetricValue],
) -> dict[str, float | None]:
    values = value if isinstance(value, Mapping) else {"value": value}
    output: dict[str, float | None] = {}
    for name, metric in values.items():
        if metric is None:
            output[str(name)] = None
            continue
        numeric = float(metric)
        output[str(name)] = numeric if np.isfinite(numeric) else None
    return output


def bootstrap_storm_group_ci(
    frame: pd.DataFrame,
    metric_fn: MetricFunction,
    *,
    storm_group_col: str = "storm_group_id",
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 20260813,
    minimum_groups_for_evidence: int = 5,
) -> dict[str, Any]:
    """Return percentile CIs after resampling complete storm groups.

    Rows are never sampled independently.  Every draw selects storm IDs with
    replacement and includes all rows belonging to each selected storm.
    """

    if storm_group_col not in frame:
        raise ValueError(f"frame lacks {storm_group_col!r}")
    if n_resamples < 1:
        raise ValueError("n_resamples must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must lie strictly between 0 and 1")
    if minimum_groups_for_evidence < 1:
        raise ValueError("minimum_groups_for_evidence must be positive")
    if frame[storm_group_col].isna().any():
        raise ValueError("storm_group_id cannot be missing for cluster bootstrap")
    groups = list(pd.unique(frame[storm_group_col].astype(str)))
    if not groups:
        return {
            "bootstrap_unit": storm_group_col,
            "confidence": confidence,
            "n_storm_groups": 0,
            "n_resamples": n_resamples,
            "insufficient_evidence": True,
            "metrics": {},
        }

    source = frame.copy()
    source[storm_group_col] = source[storm_group_col].astype(str)
    by_group = {group: source.loc[source[storm_group_col] == group].copy() for group in groups}
    point = _normalise_metrics(metric_fn(source))
    samples: dict[str, list[float]] = {name: [] for name in point}
    rng = np.random.default_rng(seed)
    for _ in range(n_resamples):
        selected = rng.choice(groups, size=len(groups), replace=True)
        chunks: list[pd.DataFrame] = []
        for draw_index, group in enumerate(selected):
            chunk = by_group[str(group)].copy()
            chunk[storm_group_col] = f"bootstrap-{draw_index:06d}"
            chunk["bootstrap_source_storm_group_id"] = str(group)
            chunks.append(chunk)
        sampled = pd.concat(chunks, ignore_index=True)
        measured = _normalise_metrics(metric_fn(sampled))
        if measured.keys() != point.keys():
            raise ValueError("metric_fn returned inconsistent metric names")
        for name, value in measured.items():
            if value is not None:
                samples[name].append(value)

    alpha = (1.0 - confidence) / 2.0
    intervals: dict[str, dict[str, Any]] = {}
    for name, estimate in point.items():
        values: NDArray[np.float64] = np.asarray(samples[name], dtype=np.float64)
        intervals[name] = {
            "estimate": estimate,
            "lower": (float(np.quantile(values, alpha)) if values.size else None),
            "upper": (float(np.quantile(values, 1.0 - alpha)) if values.size else None),
            "valid_resamples": int(values.size),
        }
    insufficient = len(groups) < minimum_groups_for_evidence or any(
        row["valid_resamples"] == 0 for row in intervals.values()
    )
    return {
        "bootstrap_unit": storm_group_col,
        "confidence": confidence,
        "n_storm_groups": len(groups),
        "n_resamples": n_resamples,
        "seed": seed,
        "insufficient_evidence": insufficient,
        "metrics": intervals,
    }


def bootstrap_event_metrics(
    event_matches: pd.DataFrame,
    *,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 20260813,
    minimum_groups_for_evidence: int = 5,
) -> dict[str, Any]:
    """Convenience storm bootstrap for event recall and detected lead time."""

    required = {"storm_group_id", "detected", "lead_time_hours"}
    missing = required.difference(event_matches.columns)
    if missing:
        raise ValueError(f"event match table is missing columns: {sorted(missing)}")

    def metrics(sample: pd.DataFrame) -> Mapping[str, MetricValue]:
        detected = sample["detected"].astype(bool).to_numpy()
        lead = pd.to_numeric(sample.loc[detected, "lead_time_hours"], errors="coerce").to_numpy(
            dtype=np.float64
        )
        lead = lead[np.isfinite(lead)]
        return {
            "event_recall": float(detected.mean()) if detected.size else None,
            "median_lead_time_hours": float(np.median(lead)) if lead.size else None,
        }

    return bootstrap_storm_group_ci(
        event_matches,
        metrics,
        n_resamples=n_resamples,
        confidence=confidence,
        seed=seed,
        minimum_groups_for_evidence=minimum_groups_for_evidence,
    )


storm_group_bootstrap = bootstrap_storm_group_ci


__all__ = [
    "bootstrap_event_metrics",
    "bootstrap_storm_group_ci",
    "storm_group_bootstrap",
]
