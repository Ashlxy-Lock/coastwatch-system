"""Leakage guards shared by every issued-forecast adapter."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pandas as pd

from ..schemas import utc_datetime
from .base import ForecastAvailabilityError, NoSourceDataError

ISSUE_COLUMN = "issue_time_utc"
VALID_COLUMN = "valid_time_utc"


def validate_issued_forecasts(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate UTC issue/valid times and positive, accurate lead hours."""

    missing = {ISSUE_COLUMN, VALID_COLUMN}.difference(frame.columns)
    if missing:
        raise ForecastAvailabilityError(
            f"issued forecast archive is missing required columns: {sorted(missing)}"
        )
    if frame.empty:
        raise NoSourceDataError("issued forecast archive contains no records")
    result = frame.copy()
    try:
        result[ISSUE_COLUMN] = [
            utc_datetime(value, name=ISSUE_COLUMN) for value in result[ISSUE_COLUMN]
        ]
        result[VALID_COLUMN] = [
            utc_datetime(value, name=VALID_COLUMN) for value in result[VALID_COLUMN]
        ]
    except ValueError as exc:
        raise ForecastAvailabilityError(str(exc)) from exc
    invalid = result[VALID_COLUMN] <= result[ISSUE_COLUMN]
    if invalid.any():
        examples = result.loc[invalid, [ISSUE_COLUMN, VALID_COLUMN]].head(3).to_dict("records")
        raise ForecastAvailabilityError(
            f"valid_time_utc must be later than issue_time_utc: {examples}"
        )
    actual_lead = (
        pd.to_datetime(result[VALID_COLUMN], utc=True)
        - pd.to_datetime(result[ISSUE_COLUMN], utc=True)
    ).dt.total_seconds() / 3600.0
    if "lead_hours" in result:
        claimed = pd.to_numeric(result["lead_hours"], errors="coerce")
        mismatch = claimed.isna() | ((claimed - actual_lead).abs() > 1.0 / 60.0)
        if mismatch.any():
            raise ForecastAvailabilityError(
                "lead_hours disagrees with issue_time_utc/valid_time_utc by more than one minute"
            )
    result["lead_hours"] = actual_lead.astype(float)
    return result


def assert_available_at_prediction_time(
    frame: pd.DataFrame,
    prediction_time: datetime | str,
) -> pd.DataFrame:
    """Require ``issue_time <= prediction_time < valid_time`` for every row."""

    result = validate_issued_forecasts(frame)
    prediction = utc_datetime(prediction_time, name="prediction_time")
    invalid = (result[ISSUE_COLUMN] > prediction) | (result[VALID_COLUMN] <= prediction)
    if invalid.any():
        examples = result.loc[invalid, [ISSUE_COLUMN, VALID_COLUMN]].head(3).to_dict("records")
        raise ForecastAvailabilityError(
            f"forecast violates issue_time <= prediction_time < valid_time: {examples}"
        )
    return result


def select_latest_as_of(
    frame: pd.DataFrame,
    *,
    prediction_time: datetime | str,
    valid_times: Sequence[datetime | str] | None = None,
    group_columns: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Select the latest issued run available at a historical prediction time.

    Rows issued in the future are filtered before selection.  Requested valid
    times at or before prediction time are rejected instead of silently dropped.
    """

    result = validate_issued_forecasts(frame)
    prediction = utc_datetime(prediction_time, name="prediction_time")
    if valid_times is not None:
        requested = [utc_datetime(value, name="valid_time") for value in valid_times]
        if any(value <= prediction for value in requested):
            raise ForecastAvailabilityError(
                "every requested valid_time must be strictly later than prediction_time"
            )
        result = result[result[VALID_COLUMN].isin(requested)]
    candidates = result[
        (result[ISSUE_COLUMN] <= prediction) & (result[VALID_COLUMN] > prediction)
    ].copy()
    if candidates.empty:
        raise NoSourceDataError(
            f"no issued forecast was available at prediction_time={prediction.isoformat()}"
        )
    groups = group_columns or ("site_id", "valid_time_utc", "source_model")
    groups += tuple(
        column for column in ("ensemble_member", "quantile") if column in candidates.columns
    )
    missing_groups = set(groups).difference(candidates.columns)
    if missing_groups:
        raise ForecastAvailabilityError(
            f"cannot select latest run; missing grouping columns: {sorted(missing_groups)}"
        )
    candidates = candidates.sort_values(ISSUE_COLUMN)
    selected = candidates.groupby(list(groups), as_index=False, dropna=False).tail(1)
    return assert_available_at_prediction_time(selected.reset_index(drop=True), prediction)


__all__ = [
    "assert_available_at_prediction_time",
    "select_latest_as_of",
    "validate_issued_forecasts",
]
