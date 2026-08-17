"""Deterministic train-only negative sampling and event-weight normalisation."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .schemas import utc_datetime


def _has_positive(value: Any) -> bool:
    array = np.asarray(value, dtype=np.float64)
    return bool(array.size and np.isfinite(array).any() and np.nanmax(array) > 0.0)


def _has_known_target(value: Any) -> bool:
    array = np.asarray(value, dtype=np.bool_)
    return bool(array.size and array.any())


def sample_training_rows(
    samples: pd.DataFrame,
    *,
    negative_min_spacing_hours: int = 6,
    negative_to_positive_target_ratio: float = 4.0,
    normalize_positive_weight_per_event: bool = True,
) -> pd.DataFrame:
    """Downsample only train negatives; validation/test remain continuous.

    Positive onset windows are always retained. Clean known-negative candidates
    are selected deterministically by site, calendar month, season, and time,
    first enforcing minimum temporal spacing and then a global negative:positive
    cap. Unknown-only train rows are omitted because they carry no main-head
    supervision. Per-event positive weights sum to one when an event ID exists.
    """

    if negative_min_spacing_hours < 1:
        raise ValueError("negative_min_spacing_hours must be positive")
    if not np.isfinite(negative_to_positive_target_ratio) or (
        negative_to_positive_target_ratio <= 0
    ):
        raise ValueError("negative_to_positive_target_ratio must be positive")
    required = {
        "site_id",
        "prediction_time_utc",
        "split",
        "hazard_target",
        "hazard_mask",
    }
    missing = required.difference(samples.columns)
    if missing:
        raise KeyError(f"sample table missing sampling columns: {sorted(missing)}")

    frame = samples.copy()
    frame["prediction_time_utc"] = [
        pd.Timestamp(utc_datetime(value, name="prediction_time_utc"))
        for value in frame["prediction_time_utc"]
    ]
    if "sample_weight" not in frame:
        frame["sample_weight"] = 1.0
    else:
        frame["sample_weight"] = pd.to_numeric(frame["sample_weight"], errors="raise").astype(float)
    split = frame["split"].astype(str)
    train = split.eq("train")
    positive = frame["hazard_target"].map(_has_positive)
    known = frame["hazard_mask"].map(_has_known_target)

    positive_train = frame.loc[train & positive].copy()
    negative_train = frame.loc[train & ~positive & known].copy()
    untouched = frame.loc[~train].copy()

    if normalize_positive_weight_per_event and not positive_train.empty:
        if "event_id" not in positive_train:
            raise ValueError("positive event weight normalisation requires event_id")
        identified = positive_train["event_id"].notna() & (
            positive_train["event_id"].astype(str).str.strip() != ""
        )
        counts = positive_train.loc[identified].groupby("event_id")["event_id"].transform("size")
        positive_train.loc[identified, "sample_weight"] = 1.0 / counts.to_numpy(dtype=float)

    if not negative_train.empty:
        month = negative_train["prediction_time_utc"].dt.strftime("%Y-%m")
        negative_train["_sampling_month"] = month
        negative_train["_sampling_season"] = (
            negative_train["prediction_time_utc"].dt.month % 12
        ) // 3
        negative_train = negative_train.sort_values(
            ["site_id", "_sampling_month", "_sampling_season", "prediction_time_utc"],
            kind="stable",
        )
        spacing = pd.Timedelta(hours=negative_min_spacing_hours)
        selected_indices: list[Any] = []
        last_by_stratum: dict[tuple[str, str, int], pd.Timestamp] = {}
        for index, row in negative_train.iterrows():
            key = (
                str(row["site_id"]),
                str(row["_sampling_month"]),
                int(row["_sampling_season"]),
            )
            timestamp = pd.Timestamp(row["prediction_time_utc"])
            previous = last_by_stratum.get(key)
            if previous is None or timestamp - previous >= spacing:
                selected_indices.append(index)
                last_by_stratum[key] = timestamp
        negative_train = negative_train.loc[selected_indices]
        maximum_negatives = int(np.floor(len(positive_train) * negative_to_positive_target_ratio))
        if positive_train.empty:
            maximum_negatives = len(negative_train)
        negative_train = negative_train.head(maximum_negatives)
        negative_train = negative_train.drop(columns=["_sampling_month", "_sampling_season"])

    result = pd.concat([positive_train, negative_train, untouched], ignore_index=True)
    return result.sort_values(["prediction_time_utc", "site_id"], kind="stable").reset_index(
        drop=True
    )


__all__ = ["sample_training_rows"]
