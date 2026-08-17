"""Transparent weak labels for the first historical-data experiment.

The thresholds below are project heuristics, not official danger thresholds.
Their purpose is to create a reproducible baseline while locally observed and
operator-reviewed incident labels are collected.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Mapping, Sequence

from .constants import (
    FORECAST_HORIZON_HOURS,
    LABEL_RULE_VERSION,
    WEAK_LABEL_THRESHOLDS,
)
from .features import parse_timestamp


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _threshold_level(value: float | None, thresholds: Sequence[float]) -> int:
    if value is None:
        return 0
    if value >= thresholds[2]:
        return 3
    if value >= thresholds[1]:
        return 2
    if value >= thresholds[0]:
        return 1
    return 0


def instant_weak_label(record: Mapping[str, object]) -> tuple[int, list[str]]:
    """Return the current environmental level and auditable reason codes."""

    wave = _finite_number(record.get("wave_height_m"))
    wind = _finite_number(record.get("wind_speed_kmh"))
    wave_level = _threshold_level(
        wave, WEAK_LABEL_THRESHOLDS["wave_height_m"]
    )
    wind_level = _threshold_level(
        wind, WEAK_LABEL_THRESHOLDS["wind_speed_kmh"]
    )

    level = max(wave_level, wind_level)
    reasons: list[str] = []
    if wave_level:
        reasons.append(("HIGH_WAVE", "VERY_HIGH_WAVE", "EXTREME_WAVE")[wave_level - 1])
    if wind_level:
        reasons.append(("STRONG_WIND", "GALE_WIND", "SEVERE_WIND")[wind_level - 1])

    # Two simultaneous advisory-or-higher conditions are more informative than
    # either condition alone.  The bump is capped at the top class.
    if wave_level >= 1 and wind_level >= 1:
        level = min(3, level + 1)
        reasons.append("COMPOUND_WAVE_WIND")
    if not reasons:
        reasons.append("LOW_ENVIRONMENTAL_SIGNAL")
    return level, reasons


def add_future_targets(
    records: Sequence[Mapping[str, object]],
    horizon_hours: int = FORECAST_HORIZON_HOURS,
) -> list[dict[str, object]]:
    """Label each row with the maximum weak risk in the following N hours.

    Rows without a complete, contiguous future horizon are discarded.  This
    prevents the final hours of a downloaded interval from being incorrectly
    labelled safe merely because future data is absent.
    """

    if horizon_hours < 1:
        raise ValueError("horizon_hours must be positive")

    ordered = sorted(records, key=lambda row: parse_timestamp(row["timestamp"]))
    result: list[dict[str, object]] = []
    for index, source in enumerate(ordered):
        future = ordered[index + 1 : index + 1 + horizon_hours]
        if len(future) != horizon_hours:
            continue
        current_time = parse_timestamp(source["timestamp"])
        expected_end = current_time + timedelta(hours=horizon_hours)
        if parse_timestamp(future[-1]["timestamp"]) != expected_end:
            continue
        if any(
            parse_timestamp(future[offset]["timestamp"])
            != current_time + timedelta(hours=offset + 1)
            for offset in range(horizon_hours)
        ):
            continue

        instant_level, instant_reasons = instant_weak_label(source)
        future_labels = [instant_weak_label(row) for row in future]
        target_level = max(level for level, _ in future_labels)
        target_reasons = next(
            reasons for level, reasons in future_labels if level == target_level
        )

        row = dict(source)
        row.update(
            {
                "instant_risk_level": instant_level,
                "instant_reason_codes": "|".join(instant_reasons),
                "target_risk_level": target_level,
                "target_reason_codes": "|".join(target_reasons),
                "forecast_horizon_hours": horizon_hours,
                "label_rule_version": LABEL_RULE_VERSION,
            }
        )
        result.append(row)
    return result

