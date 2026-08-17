"""Scientific evaluation primitives for CoastWatch ImpactNet v2."""

from .bootstrap import (
    bootstrap_event_metrics,
    bootstrap_storm_group_ci,
    storm_group_bootstrap,
)
from .calibration import (
    TemperatureScaler,
    cumulative_event_probability,
    fit_global_temperature,
    fit_temperature,
    hazard_logits_to_cumulative,
    stable_sigmoid,
)
from .events import (
    EventEvaluation,
    EventMatchResult,
    evaluate_alert_events,
    match_alert_episodes,
    match_alerts_to_events,
    merge_alert_episodes,
    merge_alert_hours,
)
from .loso import summarize_leave_one_site_out
from .metrics import (
    compute_horizon_metrics,
    cumulative_targets_from_hazards,
    expected_calibration_error,
    horizon_metrics,
)
from .thresholds import select_operating_thresholds, select_thresholds
from .water import compute_water_metrics, water_quantile_metrics

__all__ = [
    "EventEvaluation",
    "EventMatchResult",
    "TemperatureScaler",
    "bootstrap_event_metrics",
    "bootstrap_storm_group_ci",
    "compute_horizon_metrics",
    "compute_water_metrics",
    "cumulative_event_probability",
    "cumulative_targets_from_hazards",
    "evaluate_alert_events",
    "expected_calibration_error",
    "fit_global_temperature",
    "fit_temperature",
    "hazard_logits_to_cumulative",
    "horizon_metrics",
    "match_alert_episodes",
    "match_alerts_to_events",
    "merge_alert_episodes",
    "merge_alert_hours",
    "select_operating_thresholds",
    "select_thresholds",
    "stable_sigmoid",
    "summarize_leave_one_site_out",
    "storm_group_bootstrap",
    "water_quantile_metrics",
]
