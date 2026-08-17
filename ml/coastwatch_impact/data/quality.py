"""Physical-quality and vertical-datum safety guards."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .schemas import VerticalDatum


class DatumMismatchError(ValueError):
    """Raised when arithmetic mixes incompatible or unknown vertical datums."""


_DATUM_ALIASES = {
    "maod": VerticalDatum.MAOD,
    "m_aod": VerticalDatum.MAOD,
    "ordnance_datum_newlyn": VerticalDatum.MAOD,
    "local_station_datum": VerticalDatum.LOCAL_STATION_DATUM,
    "local": VerticalDatum.LOCAL_STATION_DATUM,
    "chart_datum": VerticalDatum.CHART_DATUM,
    "cd": VerticalDatum.CHART_DATUM,
    "unknown": VerticalDatum.UNKNOWN,
}


def normalise_datum(value: str | VerticalDatum | None) -> VerticalDatum:
    if isinstance(value, VerticalDatum):
        return value
    if value is None:
        return VerticalDatum.UNKNOWN
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return _DATUM_ALIASES[key]
    except KeyError as exc:
        raise DatumMismatchError(f"unsupported vertical datum: {value!r}") from exc


def assert_datum_compatible(
    left_datum: str | VerticalDatum | None,
    right_datum: str | VerticalDatum | None,
    *,
    conversion_verified: bool = False,
) -> VerticalDatum:
    """Return the common datum or reject unsafe vertical arithmetic."""

    left = normalise_datum(left_datum)
    right = normalise_datum(right_datum)
    if VerticalDatum.UNKNOWN in {left, right}:
        raise DatumMismatchError("unknown vertical datum cannot be used in arithmetic")
    if left != right and not conversion_verified:
        raise DatumMismatchError(
            f"incompatible vertical datums: {left.value!r} and {right.value!r}"
        )
    return left if left == right else right


def safe_vertical_difference(
    left_value: float | None,
    left_datum: str | VerticalDatum | None,
    right_value: float | None,
    right_datum: str | VerticalDatum | None,
    *,
    conversion_verified: bool = False,
) -> float | None:
    """Compute ``left - right`` only when values and datums are usable."""

    if left_value is None or right_value is None or pd.isna(left_value) or pd.isna(right_value):
        return None
    assert_datum_compatible(
        left_datum,
        right_datum,
        conversion_verified=conversion_verified,
    )
    return float(left_value) - float(right_value)


def compute_overtopping_margin(
    water_level: float | None,
    water_level_datum: str | VerticalDatum | None,
    defence_crest_height: float | None,
    defence_datum: str | VerticalDatum | None,
    *,
    conversion_verified: bool = False,
) -> float | None:
    """Alias with domain language for water level minus defence crest height."""

    return safe_vertical_difference(
        water_level,
        water_level_datum,
        defence_crest_height,
        defence_datum,
        conversion_verified=conversion_verified,
    )


@dataclass(frozen=True)
class StalenessAssessment:
    stale: bool
    source_age_minutes: float | None
    maximum_age_minutes: float
    reason: str | None


def assess_staleness(
    source_age_minutes: float | None,
    *,
    maximum_age_minutes: float,
) -> StalenessAssessment:
    if maximum_age_minutes < 0:
        raise ValueError("maximum_age_minutes must be non-negative")
    if source_age_minutes is None or pd.isna(source_age_minutes):
        return StalenessAssessment(True, None, maximum_age_minutes, "source age is unknown")
    age = float(source_age_minutes)
    if age < 0:
        raise ValueError("source_age_minutes cannot be negative")
    stale = age > maximum_age_minutes
    return StalenessAssessment(
        stale,
        age,
        maximum_age_minutes,
        "source is older than the configured maximum" if stale else None,
    )


__all__ = [
    "DatumMismatchError",
    "StalenessAssessment",
    "assert_datum_compatible",
    "assess_staleness",
    "compute_overtopping_margin",
    "normalise_datum",
    "safe_vertical_difference",
]
