"""Lazy 72-hour history / 24-hour forecast PyTorch dataset."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

from .preprocessing import TrainOnlyPreprocessor, assert_train_only_provenance
from .schemas import utc_datetime
from .temporal import assert_observation_history, select_forecast_horizon


class WindowDatasetError(ValueError):
    """Raised when a sample cannot satisfy temporal or shape invariants."""


DEFAULT_LEAD_FEATURES = (
    "hour_sin",
    "hour_cos",
    "day_of_year_sin",
    "day_of_year_cos",
    "lead_hour_normalized",
)


def _utc_stamp(value: Any, *, name: str) -> pd.Timestamp:
    return pd.Timestamp(utc_datetime(value, name=name))


def _normalise_time_column(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    result = frame.copy()
    result[column] = [
        _utc_stamp(value, name=f"{column}[{row}]") for row, value in enumerate(result[column])
    ]
    result[column] = pd.to_datetime(result[column], utc=True)
    return result


def _array(value: Any, length: int, *, dtype: Any, default: float | bool) -> np.ndarray:
    if value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value)):
        return np.full(length, default, dtype=dtype)
    result = np.asarray(value, dtype=dtype)
    if result.shape != (length,):
        raise WindowDatasetError(f"expected label shape ({length},), got {result.shape}")
    return result


def _explicit_observed_mask(frame: pd.DataFrame, features: Sequence[str]) -> np.ndarray:
    numeric = frame.loc[:, list(features)].apply(pd.to_numeric, errors="coerce")
    observed = (
        numeric.replace([np.inf, -np.inf], np.nan).notna().to_numpy(dtype=np.bool_, copy=True)
    )
    for index, feature in enumerate(features):
        missing_column = f"{feature}__missing"
        if missing_column in frame:
            explicit_missing = frame[missing_column].fillna(True).astype(bool).to_numpy()
            observed[:, index] &= ~explicit_missing
    if "quality_flag" in frame:
        invalid = (
            frame["quality_flag"]
            .fillna("")
            .astype(str)
            .str.lower()
            .isin({"stale", "invalid", "rejected", "missing"})
        )
        observed[invalid.to_numpy(), :] = False
    return observed


def _dense_values(
    frame: pd.DataFrame,
    features: Sequence[str],
    preprocessor: TrainOnlyPreprocessor | None,
) -> tuple[np.ndarray, np.ndarray]:
    absent = set(features).difference(frame.columns)
    if absent:
        # Reindexed all-missing forecast/history windows may not carry every
        # column; add them explicitly instead of treating zero as observed.
        frame = frame.copy()
        for column in absent:
            frame[column] = np.nan
    explicit_observed = _explicit_observed_mask(frame, features)
    if preprocessor is not None:
        assert_train_only_provenance(preprocessor)
        batch = preprocessor.transform(frame)
        observed = batch.observed_mask & explicit_observed
        return batch.values.astype(np.float32, copy=False), observed
    numeric = frame.loc[:, list(features)].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.replace([np.inf, -np.inf], np.nan)
    values = numeric.fillna(0.0).to_numpy(dtype=np.float32, copy=True)
    return values, explicit_observed


def build_lead_features(prediction_time_utc: Any, horizon_hours: int = 24) -> np.ndarray:
    prediction = _utc_stamp(prediction_time_utc, name="prediction_time_utc")
    rows: list[list[float]] = []
    for lead in range(1, horizon_hours + 1):
        valid = prediction + pd.Timedelta(hours=lead)
        hour_angle = 2.0 * np.pi * valid.hour / 24.0
        year_days = 366.0 if valid.is_leap_year else 365.0
        day_angle = 2.0 * np.pi * (valid.dayofyear - 1) / year_days
        rows.append(
            [
                float(np.sin(hour_angle)),
                float(np.cos(hour_angle)),
                float(np.sin(day_angle)),
                float(np.cos(day_angle)),
                float(lead / horizon_hours),
            ]
        )
    return np.asarray(rows, dtype=np.float32)


class CoastWatchWindowDataset(Dataset[dict[str, Any]]):
    """Build overlapping windows on demand from continuous canonical tables.

    ``past_mask``, ``future_mask`` and ``static_mask`` use ``True`` for an
    observed/usable value.  Missing or stale values are numerically filled but
    always have mask=False, so a hybrid model cannot mistake fallback zeros for a
    normal issued forecast.
    """

    def __init__(
        self,
        observations: pd.DataFrame,
        forecasts: pd.DataFrame,
        static_features: pd.DataFrame,
        sample_index: pd.DataFrame,
        *,
        past_feature_names: Sequence[str],
        future_feature_names: Sequence[str],
        static_feature_names: Sequence[str],
        past_hours: int = 72,
        horizon_hours: int = 24,
        source_model: str | None = None,
        past_preprocessor: TrainOnlyPreprocessor | None = None,
        future_preprocessor: TrainOnlyPreprocessor | None = None,
        static_preprocessor: TrainOnlyPreprocessor | None = None,
        water_target_column: str = "water_level_m_aod",
        physics_baseline_column: str | None = "forecast_total_water_level_m_aod",
        reject_future_only_forecasts: bool = True,
    ) -> None:
        if past_hours <= 0 or horizon_hours <= 0:
            raise ValueError("past_hours and horizon_hours must be positive")
        if not past_feature_names or not future_feature_names or not static_feature_names:
            raise ValueError("past, future and static feature lists must be non-empty")
        required_obs = {"site_id", "coastal_zone_id", "timestamp_utc"}
        required_forecast = {"site_id", "issue_time_utc", "valid_time_utc", "source_model"}
        required_static = {"coastal_zone_id"}
        required_index = {"site_id", "prediction_time_utc", "split"}
        for name, frame, required in (
            ("observations", observations, required_obs),
            ("forecasts", forecasts, required_forecast),
            ("static_features", static_features, required_static),
            ("sample_index", sample_index, required_index),
        ):
            missing = required.difference(frame.columns)
            if missing:
                raise KeyError(f"{name} missing columns: {sorted(missing)}")

        self.past_hours = int(past_hours)
        self.horizon_hours = int(horizon_hours)
        self.past_feature_names = tuple(past_feature_names)
        self.future_feature_names = tuple(future_feature_names)
        self.static_feature_names = tuple(static_feature_names)
        self.source_model = source_model
        self.past_preprocessor = past_preprocessor
        self.future_preprocessor = future_preprocessor
        self.static_preprocessor = static_preprocessor
        self.water_target_column = water_target_column
        self.physics_baseline_column = physics_baseline_column
        self.reject_future_only_forecasts = reject_future_only_forecasts

        self.observations = _normalise_time_column(observations, "timestamp_utc")
        duplicate_obs = self.observations.duplicated(["site_id", "timestamp_utc"], keep=False)
        if duplicate_obs.any():
            raise WindowDatasetError("observations contain duplicate site_id/timestamp rows")
        self.forecasts = _normalise_time_column(
            _normalise_time_column(forecasts, "issue_time_utc"),
            "valid_time_utc",
        )
        self.sample_index = _normalise_time_column(sample_index, "prediction_time_utc")
        self.static_features = static_features.copy()
        duplicate_static = self.static_features.duplicated("coastal_zone_id", keep=False)
        if duplicate_static.any():
            raise WindowDatasetError("static_features contains duplicate coastal_zone_id rows")

        self._observations_by_site: dict[str, pd.DataFrame] = {}
        self._zone_by_site: dict[str, str] = {}
        for site_id, group in self.observations.groupby("site_id", sort=False):
            indexed = group.sort_values("timestamp_utc").set_index("timestamp_utc", drop=False)
            self._observations_by_site[str(site_id)] = indexed
            zones = group["coastal_zone_id"].dropna().astype(str).unique()
            if len(zones) != 1:
                raise WindowDatasetError(f"site {site_id!r} maps to {len(zones)} coastal zones")
            self._zone_by_site[str(site_id)] = zones[0]
        self._static_by_zone = self.static_features.set_index("coastal_zone_id", drop=False)

    def __len__(self) -> int:
        return len(self.sample_index)

    def _history_window(self, site_id: str, prediction: pd.Timestamp) -> pd.DataFrame:
        if site_id not in self._observations_by_site:
            raise WindowDatasetError(f"unknown site_id {site_id!r}")
        site = self._observations_by_site[site_id]
        times = pd.date_range(end=prediction, periods=self.past_hours, freq="h", tz="UTC")
        window = site.reindex(times)
        window.index.name = "timestamp_utc_index"
        window["timestamp_utc"] = times
        assert_observation_history(window, prediction)
        return window

    def _future_window(self, site_id: str, prediction: pd.Timestamp) -> pd.DataFrame:
        selected = select_forecast_horizon(
            self.forecasts,
            prediction,
            site_id=site_id,
            horizon_hours=self.horizon_hours,
            source_model=self.source_model,
            require_complete=False,
            reject_future_only=self.reject_future_only_forecasts,
        )
        if selected.empty:
            return pd.DataFrame(index=range(self.horizon_hours), columns=self.future_feature_names)
        selected = selected.set_index("requested_lead_hour", drop=False).reindex(
            range(1, self.horizon_hours + 1)
        )
        return selected.reset_index(drop=True)

    def _water_targets(
        self, site_id: str, prediction: pd.Timestamp
    ) -> tuple[np.ndarray, np.ndarray]:
        site = self._observations_by_site[site_id]
        times = pd.date_range(
            start=prediction + pd.Timedelta(hours=1),
            periods=self.horizon_hours,
            freq="h",
            tz="UTC",
        )
        if self.water_target_column not in site:
            return (
                np.zeros(self.horizon_hours, dtype=np.float32),
                np.zeros(self.horizon_hours, dtype=np.bool_),
            )
        values = pd.to_numeric(site[self.water_target_column].reindex(times), errors="coerce")
        mask = values.notna().to_numpy(dtype=np.bool_, copy=True)
        return values.fillna(0.0).to_numpy(dtype=np.float32, copy=True), mask

    def _physics_baseline(self, future: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        if self.physics_baseline_column is None or self.physics_baseline_column not in future:
            return (
                np.zeros(self.horizon_hours, dtype=np.float32),
                np.zeros(self.horizon_hours, dtype=np.bool_),
            )
        values = pd.to_numeric(future[self.physics_baseline_column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        mask = values.notna().to_numpy(dtype=np.bool_, copy=True)
        missing_column = f"{self.physics_baseline_column}__missing"
        if missing_column in future:
            mask &= ~future[missing_column].fillna(True).astype(bool).to_numpy(copy=True)
        if "quality_flag" in future:
            invalid = (
                future["quality_flag"]
                .fillna("")
                .astype(str)
                .str.lower()
                .isin({"stale", "invalid", "rejected", "missing"})
            )
            mask &= ~invalid.to_numpy(copy=True)
        return values.fillna(0.0).to_numpy(dtype=np.float32, copy=True), mask

    def __getitem__(self, index: int) -> dict[str, Any]:
        sample = self.sample_index.iloc[index]
        site_id = str(sample["site_id"])
        prediction = _utc_stamp(sample["prediction_time_utc"], name="prediction_time_utc")
        zone_id = (
            str(sample["coastal_zone_id"])
            if "coastal_zone_id" in sample and pd.notna(sample["coastal_zone_id"])
            else self._zone_by_site[site_id]
        )
        if zone_id not in self._static_by_zone.index:
            raise WindowDatasetError(f"no static feature row for coastal zone {zone_id!r}")

        history = self._history_window(site_id, prediction)
        future = self._future_window(site_id, prediction)
        static = self._static_by_zone.loc[[zone_id]].copy()
        past_values, past_mask = _dense_values(
            history,
            self.past_feature_names,
            self.past_preprocessor,
        )
        future_values, future_mask = _dense_values(
            future,
            self.future_feature_names,
            self.future_preprocessor,
        )
        static_values_2d, static_mask_2d = _dense_values(
            static,
            self.static_feature_names,
            self.static_preprocessor,
        )
        physics_baseline, physics_mask = self._physics_baseline(future)

        hazard_target = _array(
            sample.get("hazard_target"),
            self.horizon_hours,
            dtype=np.float32,
            default=0.0,
        )
        hazard_mask = _array(
            sample.get("hazard_mask"),
            self.horizon_hours,
            dtype=np.bool_,
            default=False,
        )
        if "water_target" in sample and sample.get("water_target") is not None:
            water_target = _array(
                sample.get("water_target"),
                self.horizon_hours,
                dtype=np.float32,
                default=0.0,
            )
            water_mask = _array(
                sample.get("water_mask"),
                self.horizon_hours,
                dtype=np.bool_,
                default=False,
            )
        else:
            water_target, water_mask = self._water_targets(site_id, prediction)

        warning_value = sample.get("warning_target")
        severity_value = sample.get("severity_target")
        warning_target = (
            torch.tensor(int(warning_value), dtype=torch.long)
            if warning_value is not None and pd.notna(warning_value)
            else None
        )
        severity_target = (
            torch.tensor(int(severity_value), dtype=torch.long)
            if severity_value is not None and pd.notna(severity_value)
            else None
        )
        warning_mask = (
            torch.tensor(bool(sample.get("warning_mask")), dtype=torch.bool)
            if warning_target is not None
            else None
        )
        severity_mask = (
            torch.tensor(bool(sample.get("severity_mask")), dtype=torch.bool)
            if severity_target is not None
            else None
        )
        event_id = sample.get("event_id")
        storm_group_id = sample.get("storm_group_id")
        return {
            "past_values": torch.from_numpy(past_values),
            "past_mask": torch.from_numpy(past_mask),
            "future_values": torch.from_numpy(future_values),
            "future_mask": torch.from_numpy(future_mask),
            "static_values": torch.from_numpy(static_values_2d[0]),
            "static_mask": torch.from_numpy(static_mask_2d[0]),
            "lead_features": torch.from_numpy(build_lead_features(prediction, self.horizon_hours)),
            "physics_baseline": torch.from_numpy(physics_baseline),
            "physics_mask": torch.from_numpy(physics_mask),
            "hazard_target": torch.from_numpy(hazard_target),
            "hazard_mask": torch.from_numpy(hazard_mask),
            "water_target": torch.from_numpy(water_target),
            "water_mask": torch.from_numpy(water_mask),
            "warning_target": warning_target,
            "warning_mask": warning_mask,
            "severity_target": severity_target,
            "severity_mask": severity_mask,
            "sample_weight": torch.tensor(
                [float(sample.get("sample_weight", 1.0))],
                dtype=torch.float32,
            ),
            "site_id": site_id,
            "prediction_time_utc": prediction.to_pydatetime(),
            "event_id": str(event_id) if event_id is not None and pd.notna(event_id) else None,
            "storm_group_id": (
                str(storm_group_id)
                if storm_group_id is not None and pd.notna(storm_group_id)
                else None
            ),
            "split": str(sample["split"]),
        }


_METADATA_KEYS = {
    "site_id",
    "prediction_time_utc",
    "event_id",
    "storm_group_id",
    "split",
}


def collate_window_batch(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Collate window samples without choking on optional heads or metadata.

    Required tensors are stacked.  Human-readable identifiers/timestamps remain
    Python lists.  Optional heads become ``None`` when absent from the whole
    batch; a mixed optional field remains a list so absence is never silently
    converted to a numeric target.
    """

    if not samples:
        raise ValueError("cannot collate an empty window batch")
    keys = set(samples[0])
    if any(set(sample) != keys for sample in samples[1:]):
        raise WindowDatasetError("all samples in a batch must have identical keys")
    batch: dict[str, Any] = {}
    for key in samples[0]:
        values = [sample[key] for sample in samples]
        if key in _METADATA_KEYS:
            batch[key] = values
        elif all(value is None for value in values):
            batch[key] = None
        elif all(isinstance(value, torch.Tensor) for value in values):
            batch[key] = torch.stack(values)
        else:
            # Mixed optional tensors/None retain their explicit absence.  The
            # corresponding optional mask tells a trainer which rows are usable.
            batch[key] = values
    return batch


def dataset_cache_key(
    *,
    dataset_manifest_hash: str,
    feature_config_hash: str,
    label_config_hash: str,
    split_config_hash: str,
) -> str:
    payload = {
        "dataset_manifest_hash": dataset_manifest_hash,
        "feature_config_hash": feature_config_hash,
        "label_config_hash": label_config_hash,
        "split_config_hash": split_config_hash,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


ImpactWindowDataset = CoastWatchWindowDataset


__all__ = [
    "CoastWatchWindowDataset",
    "DEFAULT_LEAD_FEATURES",
    "ImpactWindowDataset",
    "WindowDatasetError",
    "build_lead_features",
    "collate_window_batch",
    "dataset_cache_key",
]
