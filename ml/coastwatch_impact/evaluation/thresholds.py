"""Validation-only selection of research alert operating points."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from .events import evaluate_alert_events


def _finite_score(value: Any, *, default: float = -1.0) -> float:
    if value is None:
        return default
    result = float(value)
    return result if np.isfinite(result) else default


def _candidate_thresholds(
    probabilities: np.ndarray, candidates: Sequence[float] | None
) -> list[float]:
    if candidates is None:
        quantiles = np.quantile(probabilities, np.linspace(0.0, 1.0, 101))
        values = np.concatenate(([0.0], quantiles, [1.0]))
    else:
        values = np.asarray(tuple(candidates), dtype=np.float64)
    if values.size == 0 or not np.isfinite(values).all():
        raise ValueError("candidate thresholds must be non-empty and finite")
    if np.any((values < 0.0) | (values > 1.0)):
        raise ValueError("candidate thresholds must lie in [0, 1]")
    return sorted(set(float(value) for value in values))


def _constraint_excess(
    row: dict[str, Any],
    max_false_alert_episodes: int | None,
    max_false_alerts_per_site_month: float | None,
) -> float:
    excess = 0.0
    if max_false_alert_episodes is not None:
        excess += max(
            0.0,
            row["false_alert_episodes"] - max_false_alert_episodes,
        ) / max(1.0, float(max_false_alert_episodes))
    if max_false_alerts_per_site_month is not None:
        false_rate = _finite_score(row["false_alert_episodes_per_site_month"], default=np.inf)
        excess += max(0.0, false_rate - max_false_alerts_per_site_month) / max(
            1e-12, max_false_alerts_per_site_month
        )
    return float(excess)


def select_operating_thresholds(
    predictions: pd.DataFrame,
    events: pd.DataFrame,
    *,
    split: str,
    probability_col: str = "event_probability",
    candidate_thresholds: Sequence[float] | None = None,
    max_false_alert_episodes: int | None = None,
    max_false_alerts_per_site_month: float | None = None,
    conservative_minimum_recall: float = 0.5,
    merge_gap_hours: int = 2,
    cooldown_hours: int = 6,
    lookahead_hours: int = 24,
) -> dict[str, Any]:
    """Choose sensitive, balanced, and conservative validation thresholds.

    Selection is based on event episodes rather than independent hours.  If a
    configured constraint cannot be met, the best-effort point is returned
    with ``constraint_met=false`` instead of silently weakening the rule.
    """

    if split != "validation":
        raise ValueError("operating thresholds may only be selected on validation")
    if "split" in predictions and not predictions["split"].eq("validation").all():
        raise ValueError("threshold selection received non-validation predictions")
    if probability_col not in predictions:
        raise ValueError(f"prediction table lacks {probability_col!r}")
    if predictions.empty:
        raise ValueError("threshold selection requires prediction rows")
    probabilities = pd.to_numeric(predictions[probability_col], errors="coerce").to_numpy(
        dtype=np.float64
    )
    if not np.isfinite(probabilities).all() or np.any(
        (probabilities < 0.0) | (probabilities > 1.0)
    ):
        raise ValueError("event probabilities must be finite and lie in [0, 1]")
    if max_false_alert_episodes is not None and max_false_alert_episodes < 0:
        raise ValueError("max_false_alert_episodes must be non-negative")
    if max_false_alerts_per_site_month is not None and max_false_alerts_per_site_month < 0.0:
        raise ValueError("max false-alert rate must be non-negative")
    if not 0.0 <= conservative_minimum_recall <= 1.0:
        raise ValueError("conservative minimum recall must lie in [0, 1]")

    curve: list[dict[str, Any]] = []
    for threshold in _candidate_thresholds(probabilities, candidate_thresholds):
        evaluation = evaluate_alert_events(
            predictions,
            events,
            threshold,
            probability_col=probability_col,
            merge_gap_hours=merge_gap_hours,
            cooldown_hours=cooldown_hours,
            lookahead_hours=lookahead_hours,
        )
        metrics = evaluation.metrics
        curve.append(
            {
                "threshold": threshold,
                "event_recall": metrics["event_recall"],
                "event_precision": metrics["event_precision"],
                "event_f1": metrics["event_f1"],
                "detected_events": metrics["detected_events"],
                "missed_events": metrics["missed_events"],
                "alert_episodes": metrics["alert_episodes"],
                "false_alert_episodes": metrics["false_alert_episodes"],
                "false_alert_episodes_per_site_month": metrics[
                    "false_alert_episodes_per_site_month"
                ],
                "median_lead_time_hours": metrics["median_lead_time_hours"],
            }
        )

    sensitive_eligible = [
        row
        for row in curve
        if _constraint_excess(
            row,
            max_false_alert_episodes,
            max_false_alerts_per_site_month,
        )
        <= 1e-12
    ]
    sensitive_constraint_met = bool(sensitive_eligible)
    if sensitive_eligible:
        sensitive = min(
            sensitive_eligible,
            key=lambda row: (
                -_finite_score(row["event_recall"]),
                row["false_alert_episodes"],
                -_finite_score(row["event_precision"]),
                -row["threshold"],
            ),
        )
    else:
        sensitive = min(
            curve,
            key=lambda row: (
                _constraint_excess(
                    row,
                    max_false_alert_episodes,
                    max_false_alerts_per_site_month,
                ),
                -_finite_score(row["event_recall"]),
                row["false_alert_episodes"],
                -row["threshold"],
            ),
        )

    balanced = min(
        curve,
        key=lambda row: (
            -_finite_score(row["event_f1"]),
            -_finite_score(row["event_recall"]),
            row["false_alert_episodes"],
            -row["threshold"],
        ),
    )

    conservative_eligible = [
        row for row in curve if _finite_score(row["event_recall"]) >= conservative_minimum_recall
    ]
    conservative_constraint_met = bool(conservative_eligible)
    if conservative_eligible:
        conservative = min(
            conservative_eligible,
            key=lambda row: (
                -_finite_score(row["event_precision"]),
                row["false_alert_episodes"],
                -_finite_score(row["event_recall"]),
                -row["threshold"],
            ),
        )
    else:
        conservative = min(
            curve,
            key=lambda row: (
                -_finite_score(row["event_recall"]),
                -_finite_score(row["event_precision"]),
                row["false_alert_episodes"],
                -row["threshold"],
            ),
        )

    def selected(row: dict[str, Any], constraint_met: bool) -> dict[str, Any]:
        return {**row, "constraint_met": constraint_met}

    return {
        "fitted_split": "validation",
        "selection_basis": "event_episodes",
        "constraints": {
            "sensitive_max_false_alert_episodes": max_false_alert_episodes,
            "sensitive_max_false_alerts_per_site_month": (max_false_alerts_per_site_month),
            "conservative_minimum_recall": conservative_minimum_recall,
        },
        "selected": {
            "sensitive": selected(sensitive, sensitive_constraint_met),
            "balanced": selected(balanced, True),
            "conservative": selected(conservative, conservative_constraint_met),
        },
        "candidate_metrics": curve,
    }


select_thresholds = select_operating_thresholds


__all__ = ["select_operating_thresholds", "select_thresholds"]
