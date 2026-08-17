"""Leakage-safe temporal selection and history guards."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import numpy as np
import pandas as pd

from .schemas import utc_datetime


class ForecastLeakageError(ValueError):
    """Raised when a future-issued forecast could enter an input window."""


class ObservationLeakageError(ValueError):
    """Raised when observations later than prediction time enter history."""


def _utc_stamp(value: datetime | str | pd.Timestamp, *, name: str) -> pd.Timestamp:
    return pd.Timestamp(utc_datetime(value, name=name))


def _normalise_time_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        raise KeyError(f"missing time column {column!r}")
    values: list[pd.Timestamp] = []
    for row, value in enumerate(frame[column].tolist()):
        try:
            values.append(_utc_stamp(value, name=f"{column}[{row}]"))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    return pd.Series(values, index=frame.index, dtype="datetime64[ns, UTC]")


def select_asof_forecast(
    forecasts: pd.DataFrame,
    prediction_time_utc: datetime | str | pd.Timestamp,
    valid_time_utc: datetime | str | pd.Timestamp,
    *,
    site_id: str | None = None,
    source_model: str | None = None,
    reject_future_only: bool = False,
) -> pd.Series | None:
    """Select the latest forecast that was available at prediction time.

    Eligibility is exactly ``issue_time <= prediction_time < valid_time``.
    Future-issued rows are never selected.  ``reject_future_only`` is useful in
    leakage tests and build pipelines: if rows exist for the requested valid time
    but all were issued in the future, a hard error is raised instead of silently
    treating the lead as unavailable.
    """

    prediction = _utc_stamp(prediction_time_utc, name="prediction_time_utc")
    valid = _utc_stamp(valid_time_utc, name="valid_time_utc")
    if prediction >= valid:
        raise ValueError("prediction_time_utc must be earlier than valid_time_utc")
    if forecasts.empty:
        return None
    required = {"site_id", "issue_time_utc", "valid_time_utc", "source_model"}
    missing = required.difference(forecasts.columns)
    if missing:
        raise KeyError(f"forecast frame missing columns: {sorted(missing)}")
    work = forecasts.copy()
    work["issue_time_utc"] = _normalise_time_column(work, "issue_time_utc")
    work["valid_time_utc"] = _normalise_time_column(work, "valid_time_utc")
    matching = work[work["valid_time_utc"] == valid]
    if site_id is not None:
        matching = matching[matching["site_id"] == site_id]
    if source_model is not None:
        matching = matching[matching["source_model"] == source_model]
    eligible = matching[matching["issue_time_utc"] <= prediction]
    if eligible.empty:
        if reject_future_only and not matching.empty:
            earliest = matching["issue_time_utc"].min()
            raise ForecastLeakageError(
                f"only future-issued forecasts exist for valid time {valid.isoformat()}; "
                f"earliest issue is {earliest.isoformat()} after prediction "
                f"{prediction.isoformat()}"
            )
        return None
    # Stable secondary keys make equal issue-time selection deterministic.
    sort_columns = ["issue_time_utc"]
    for column in ("model_run_id", "ensemble_member", "quantile"):
        if column in eligible:
            sort_columns.append(column)
    selected = eligible.sort_values(sort_columns, kind="mergesort").iloc[-1].copy()
    issue = selected["issue_time_utc"]
    selected_valid = selected["valid_time_utc"]
    if not (issue <= prediction < selected_valid):  # defensive postcondition
        raise ForecastLeakageError(
            "as-of selector postcondition failed: issue_time <= prediction_time < valid_time"
        )
    return selected


def select_forecast_horizon(
    forecasts: pd.DataFrame,
    prediction_time_utc: datetime | str | pd.Timestamp,
    *,
    site_id: str,
    horizon_hours: int = 24,
    source_model: str | None = None,
    valid_times_utc: Sequence[datetime | str | pd.Timestamp] | None = None,
    require_complete: bool = False,
    reject_future_only: bool = True,
) -> pd.DataFrame:
    """Return at most one leakage-safe issued forecast for every requested lead."""

    if horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive")
    prediction = _utc_stamp(prediction_time_utc, name="prediction_time_utc")
    if valid_times_utc is None:
        valid_times = [
            prediction + pd.Timedelta(hours=lead) for lead in range(1, horizon_hours + 1)
        ]
    else:
        valid_times = [_utc_stamp(value, name="valid_time_utc") for value in valid_times_utc]
        if len(valid_times) != horizon_hours:
            raise ValueError("valid_times_utc length must equal horizon_hours")
    selected_rows: list[pd.Series] = []
    missing_leads: list[int] = []
    for lead, valid in enumerate(valid_times, start=1):
        row = select_asof_forecast(
            forecasts,
            prediction,
            valid,
            site_id=site_id,
            source_model=source_model,
            reject_future_only=reject_future_only,
        )
        if row is None:
            missing_leads.append(lead)
            continue
        row = row.copy()
        row["requested_lead_hour"] = lead
        row["prediction_time_utc"] = prediction
        selected_rows.append(row)
    if require_complete and missing_leads:
        raise ForecastLeakageError(f"missing issued forecasts for lead hours: {missing_leads}")
    if not selected_rows:
        columns = list(forecasts.columns) + ["requested_lead_hour", "prediction_time_utc"]
        return pd.DataFrame(columns=list(dict.fromkeys(columns)))
    result = pd.DataFrame(selected_rows).reset_index(drop=True)
    audit_forecast_asof(result, prediction_time_col="prediction_time_utc")
    return result


def audit_forecast_asof(
    selected: pd.DataFrame,
    *,
    prediction_time_col: str = "prediction_time_utc",
) -> dict[str, int | bool]:
    """Assert every selected row satisfies the operational availability rule."""

    required = {"issue_time_utc", "valid_time_utc", prediction_time_col}
    missing = required.difference(selected.columns)
    if missing:
        raise KeyError(f"selected forecast frame missing columns: {sorted(missing)}")
    if selected.empty:
        return {"rows": 0, "valid": True}
    issue = _normalise_time_column(selected, "issue_time_utc")
    valid = _normalise_time_column(selected, "valid_time_utc")
    prediction = _normalise_time_column(selected, prediction_time_col)
    bad = ~((issue <= prediction) & (prediction < valid))
    if bad.any():
        examples = selected.loc[bad].head(5).to_dict(orient="records")
        raise ForecastLeakageError(
            f"forecast availability violation (required issue <= prediction < valid): {examples}"
        )
    return {"rows": int(len(selected)), "valid": True}


def asof_join_forecasts(
    requests: pd.DataFrame,
    forecasts: pd.DataFrame,
    *,
    prediction_time_col: str = "prediction_time_utc",
    valid_time_col: str = "valid_time_utc",
    source_model: str | None = None,
    reject_future_only: bool = True,
) -> pd.DataFrame:
    """Leakage-safe row-wise join for a request table.

    Request columns are preserved.  Missing forecasts remain missing; a
    future-issued row is never used as a substitute.
    """

    required = {"site_id", prediction_time_col, valid_time_col}
    missing = required.difference(requests.columns)
    if missing:
        raise KeyError(f"request frame missing columns: {sorted(missing)}")
    joined: list[dict[str, object]] = []
    forecast_columns = [column for column in forecasts.columns if column not in {"site_id"}]
    for request in requests.to_dict(orient="records"):
        row = select_asof_forecast(
            forecasts,
            request[prediction_time_col],
            request[valid_time_col],
            site_id=str(request["site_id"]),
            source_model=source_model,
            reject_future_only=reject_future_only,
        )
        merged = dict(request)
        if row is None:
            for column in forecast_columns:
                merged.setdefault(column, np.nan)
            merged["forecast_available"] = False
        else:
            for column, value in row.items():
                if column == "site_id":
                    continue
                merged[column] = value
            merged["forecast_available"] = True
        joined.append(merged)
    result = pd.DataFrame(joined)
    available = result.get("forecast_available", pd.Series(False, index=result.index)).astype(bool)
    if available.any():
        audit_forecast_asof(result.loc[available], prediction_time_col=prediction_time_col)
    return result


def assert_observation_history(
    observations: pd.DataFrame,
    prediction_time_utc: datetime | str | pd.Timestamp,
    *,
    timestamp_col: str = "timestamp_utc",
) -> None:
    """Reject future observations from a historical input frame."""

    if observations.empty:
        return
    prediction = _utc_stamp(prediction_time_utc, name="prediction_time_utc")
    timestamps = _normalise_time_column(observations, timestamp_col)
    future = timestamps > prediction
    if future.any():
        latest = timestamps[future].max()
        raise ObservationLeakageError(
            f"observation at {latest.isoformat()} is later than prediction time "
            f"{prediction.isoformat()}"
        )


# Compatibility alias matching terminology in the development specification.
forecast_asof_join = asof_join_forecasts


__all__ = [
    "ForecastLeakageError",
    "ObservationLeakageError",
    "asof_join_forecasts",
    "assert_observation_history",
    "audit_forecast_asof",
    "forecast_asof_join",
    "select_asof_forecast",
    "select_forecast_horizon",
]
