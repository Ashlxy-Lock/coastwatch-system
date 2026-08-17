"""Auditable baselines with JSON/NPZ state (never pickle)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray


def _as_float_array(values: ArrayLike, *, name: str) -> NDArray[np.float64]:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        raise ValueError(f"{name} cannot be empty")
    return array


def _safe_feature_statistics(
    features: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    finite = np.isfinite(features)
    counts = finite.sum(axis=0)
    sums = np.where(finite, features, 0.0).sum(axis=0)
    mean = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    centred = np.where(finite, features - mean, 0.0)
    variance = np.divide(
        np.square(centred).sum(axis=0),
        counts,
        out=np.ones_like(sums),
        where=counts > 0,
    )
    scale = np.sqrt(variance)
    scale[~np.isfinite(scale) | (scale < 1e-12)] = 1.0
    return mean, scale


def build_logistic_summary_features(
    past_observations: ArrayLike,
    future_forecasts: ArrayLike | None = None,
) -> NDArray[np.float64]:
    """Build an interpretable last/mean/std/min/max feature summary."""

    past = _as_float_array(past_observations, name="past_observations")
    if past.ndim != 3:
        raise ValueError("past_observations must have shape [sample, time, feature]")

    finite = np.isfinite(past)
    count = finite.sum(axis=1)
    total = np.where(finite, past, 0.0).sum(axis=1)
    mean = np.divide(total, count, out=np.zeros_like(total), where=count > 0)
    centred = np.where(finite, past - mean[:, None, :], 0.0)
    std = np.sqrt(
        np.divide(
            np.square(centred).sum(axis=1),
            count,
            out=np.zeros_like(total),
            where=count > 0,
        )
    )
    minimum = np.where(finite, past, np.inf).min(axis=1)
    maximum = np.where(finite, past, -np.inf).max(axis=1)
    minimum[~np.isfinite(minimum)] = 0.0
    maximum[~np.isfinite(maximum)] = 0.0

    # Last finite value for every feature, without assuming filled zeros are
    # observations.
    reversed_finite = finite[:, ::-1, :]
    reverse_index = reversed_finite.argmax(axis=1)
    has_value = reversed_finite.any(axis=1)
    source_index = past.shape[1] - 1 - reverse_index
    sample_index = np.arange(past.shape[0])[:, None]
    feature_index = np.arange(past.shape[2])[None, :]
    last = past[sample_index, source_index, feature_index]
    last[~has_value] = 0.0

    pieces = [last, mean, std, minimum, maximum, 1.0 - count / past.shape[1]]
    if future_forecasts is not None:
        future = _as_float_array(future_forecasts, name="future_forecasts")
        if future.ndim != 3 or future.shape[0] != past.shape[0]:
            raise ValueError("future_forecasts must have shape [sample, lead, feature]")
        future_finite = np.isfinite(future)
        future_count = future_finite.sum(axis=1)
        future_total = np.where(future_finite, future, 0.0).sum(axis=1)
        future_mean = np.divide(
            future_total,
            future_count,
            out=np.zeros_like(future_total),
            where=future_count > 0,
        )
        future_max = np.where(future_finite, future, -np.inf).max(axis=1)
        future_max[~np.isfinite(future_max)] = 0.0
        pieces.extend(
            [
                future_mean,
                future_max,
                1.0 - future_count / future.shape[1],
            ]
        )
    return np.concatenate(pieces, axis=1)


class LogisticEventBaseline:
    """Independent logistic event heads with train-only standardisation.

    ``fit`` accepts either one target ``[N]`` or several lead/horizon targets
    ``[N, H]``.  Degenerate targets are represented by a finite constant logit
    rather than making scikit-learn fail.
    """

    state_version = 1

    def __init__(
        self,
        *,
        c: float = 1.0,
        max_iter: int = 500,
        class_weight: str | dict[int, float] | None = "balanced",
        random_state: int = 20260813,
    ) -> None:
        if c <= 0 or max_iter < 1:
            raise ValueError("c and max_iter must be positive")
        self.c = float(c)
        self.max_iter = int(max_iter)
        self.class_weight = class_weight
        self.random_state = int(random_state)
        self.feature_mean_: NDArray[np.float64] | None = None
        self.feature_scale_: NDArray[np.float64] | None = None
        self.coef_: NDArray[np.float64] | None = None
        self.intercept_: NDArray[np.float64] | None = None
        self.constant_probability_: NDArray[np.float64] | None = None
        self.squeeze_output_: bool = True

    @property
    def fitted(self) -> bool:
        return self.coef_ is not None

    def _standardise_fit(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
        self.feature_mean_, self.feature_scale_ = _safe_feature_statistics(features)
        filled = np.where(np.isfinite(features), features, self.feature_mean_)
        return (filled - self.feature_mean_) / self.feature_scale_

    def _standardise_predict(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
        if self.feature_mean_ is None or self.feature_scale_ is None:
            raise RuntimeError("LogisticEventBaseline has not been fitted")
        if features.ndim != 2 or features.shape[1] != self.feature_mean_.shape[0]:
            raise ValueError(f"features must have shape [sample, {self.feature_mean_.shape[0]}]")
        filled = np.where(np.isfinite(features), features, self.feature_mean_)
        return (filled - self.feature_mean_) / self.feature_scale_

    def fit(
        self,
        features: ArrayLike,
        target: ArrayLike,
        *,
        mask: ArrayLike | None = None,
        sample_weight: ArrayLike | None = None,
    ) -> LogisticEventBaseline:
        from sklearn.linear_model import LogisticRegression

        x = _as_float_array(features, name="features")
        if x.ndim != 2:
            raise ValueError("features must have shape [sample, feature]")
        y = _as_float_array(target, name="target")
        self.squeeze_output_ = y.ndim == 1
        if y.ndim == 1:
            y = y[:, None]
        if y.ndim != 2 or y.shape[0] != x.shape[0]:
            raise ValueError("target must have shape [sample] or [sample, output]")
        if mask is None:
            valid_mask = np.isfinite(y)
        else:
            raw_mask = np.asarray(mask, dtype=bool)
            if raw_mask.ndim == 1 and raw_mask.shape[0] == y.shape[0]:
                raw_mask = raw_mask[:, None]
            valid_mask = np.broadcast_to(raw_mask, y.shape).copy()
            valid_mask &= np.isfinite(y)
        if sample_weight is None:
            weights = np.ones_like(y)
        else:
            raw_weights = np.asarray(sample_weight, dtype=np.float64)
            if raw_weights.ndim == 1 and raw_weights.shape[0] == x.shape[0]:
                raw_weights = raw_weights[:, None]
            weights = np.broadcast_to(raw_weights, y.shape)
            if np.any(weights < 0):
                raise ValueError("sample_weight cannot be negative")

        scaled = self._standardise_fit(x)
        outputs = y.shape[1]
        self.coef_ = np.zeros((outputs, x.shape[1]), dtype=np.float64)
        self.intercept_ = np.zeros(outputs, dtype=np.float64)
        self.constant_probability_ = np.full(outputs, np.nan, dtype=np.float64)
        for output_index in range(outputs):
            valid = valid_mask[:, output_index]
            if not valid.any():
                raise ValueError(f"target output {output_index} has no valid samples")
            output_target = y[valid, output_index]
            if not np.isin(output_target, [0.0, 1.0]).all():
                raise ValueError("logistic targets must be binary")
            classes = np.unique(output_target)
            if classes.size == 1:
                self.constant_probability_[output_index] = float(classes[0])
                continue
            model = LogisticRegression(
                C=self.c,
                max_iter=self.max_iter,
                class_weight=self.class_weight,
                random_state=self.random_state,
                solver="lbfgs",
            )
            model.fit(
                scaled[valid],
                output_target.astype(np.int64),
                sample_weight=weights[valid, output_index],
            )
            self.coef_[output_index] = model.coef_[0]
            self.intercept_[output_index] = model.intercept_[0]
        return self

    def predict_logits(self, features: ArrayLike) -> NDArray[np.float64]:
        if self.coef_ is None or self.intercept_ is None:
            raise RuntimeError("LogisticEventBaseline has not been fitted")
        x = self._standardise_predict(_as_float_array(features, name="features"))
        logits = x @ self.coef_.T + self.intercept_
        if self.constant_probability_ is not None:
            constant = np.isfinite(self.constant_probability_)
            clipped = np.clip(self.constant_probability_, 1e-7, 1.0 - 1e-7)
            constant_logits = np.log(clipped) - np.log1p(-clipped)
            logits[:, constant] = constant_logits[constant]
        return logits[:, 0] if self.squeeze_output_ else logits

    def predict_proba(self, features: ArrayLike) -> NDArray[np.float64]:
        logits = self.predict_logits(features)
        # Stable sigmoid in both tails.
        return np.exp(-np.logaddexp(0.0, -logits))

    predict_hazard_logits = predict_logits

    def predict_cumulative_event_probability(self, features: ArrayLike) -> NDArray[np.float64]:
        """Accumulate multi-lead conditional hazards without cancellation."""

        logits = self.predict_logits(features)
        if logits.ndim == 1:
            return self.predict_proba(features)
        log_survival = np.cumsum(-np.logaddexp(0.0, logits), axis=1)
        return 1.0 - np.exp(log_survival)

    def to_state(self) -> dict[str, Any]:
        if not self.fitted:
            raise RuntimeError("cannot serialise an unfitted LogisticEventBaseline")
        assert self.feature_mean_ is not None
        assert self.feature_scale_ is not None
        assert self.coef_ is not None
        assert self.intercept_ is not None
        assert self.constant_probability_ is not None
        return {
            "type": "logistic_event_baseline",
            "state_version": self.state_version,
            "c": self.c,
            "max_iter": self.max_iter,
            "class_weight": self.class_weight,
            "random_state": self.random_state,
            "squeeze_output": self.squeeze_output_,
            "feature_mean": self.feature_mean_.tolist(),
            "feature_scale": self.feature_scale_.tolist(),
            "coef": self.coef_.tolist(),
            "intercept": self.intercept_.tolist(),
            "constant_probability": [
                None if not np.isfinite(value) else float(value)
                for value in self.constant_probability_
            ],
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> LogisticEventBaseline:
        if state.get("type") != "logistic_event_baseline":
            raise ValueError("not a LogisticEventBaseline state")
        if int(state.get("state_version", -1)) != cls.state_version:
            raise ValueError("unsupported LogisticEventBaseline state version")
        model = cls(
            c=float(state["c"]),
            max_iter=int(state["max_iter"]),
            class_weight=state.get("class_weight"),
            random_state=int(state["random_state"]),
        )
        model.squeeze_output_ = bool(state["squeeze_output"])
        model.feature_mean_ = np.asarray(state["feature_mean"], dtype=np.float64)
        model.feature_scale_ = np.asarray(state["feature_scale"], dtype=np.float64)
        model.coef_ = np.asarray(state["coef"], dtype=np.float64)
        model.intercept_ = np.asarray(state["intercept"], dtype=np.float64)
        model.constant_probability_ = np.asarray(
            [np.nan if value is None else float(value) for value in state["constant_probability"]],
            dtype=np.float64,
        )
        return model

    def save_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_state(), indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )

    @classmethod
    def load_json(cls, path: str | Path) -> LogisticEventBaseline:
        return cls.from_state(json.loads(Path(path).read_text(encoding="utf-8")))

    def save_npz(self, path: str | Path) -> None:
        """Save numeric state in an ``allow_pickle=False`` compatible NPZ."""

        state = self.to_state()
        metadata = {
            key: value
            for key, value in state.items()
            if key
            not in {
                "feature_mean",
                "feature_scale",
                "coef",
                "intercept",
                "constant_probability",
            }
        }
        np.savez_compressed(
            path,
            metadata=np.asarray(json.dumps(metadata)),
            feature_mean=np.asarray(state["feature_mean"]),
            feature_scale=np.asarray(state["feature_scale"]),
            coef=np.asarray(state["coef"]),
            intercept=np.asarray(state["intercept"]),
            constant_probability=np.asarray(
                np.nan if self.constant_probability_ is None else self.constant_probability_
            ),
        )

    @classmethod
    def load_npz(cls, path: str | Path) -> LogisticEventBaseline:
        with np.load(path, allow_pickle=False) as archive:
            state = json.loads(str(archive["metadata"].item()))
            for name in (
                "feature_mean",
                "feature_scale",
                "coef",
                "intercept",
                "constant_probability",
            ):
                state[name] = archive[name].tolist()
        return cls.from_state(state)

    def save(self, path: str | Path) -> None:
        """Save to JSON or NPZ based on the explicit file extension."""

        destination = Path(path)
        if destination.suffix.lower() == ".json":
            self.save_json(destination)
        elif destination.suffix.lower() == ".npz":
            self.save_npz(destination)
        else:
            raise ValueError("logistic baseline state must use .json or .npz")

    @classmethod
    def load(cls, path: str | Path) -> LogisticEventBaseline:
        source = Path(path)
        if source.suffix.lower() == ".json":
            return cls.load_json(source)
        if source.suffix.lower() == ".npz":
            return cls.load_npz(source)
        raise ValueError("logistic baseline state must use .json or .npz")


class PersistenceWaterBaseline:
    """Repeat the latest available observed water level over all leads."""

    state_version = 1

    def __init__(self, forecast_hours: int = 24, water_feature_index: int = 0) -> None:
        if forecast_hours < 1:
            raise ValueError("forecast_hours must be positive")
        self.forecast_hours = int(forecast_hours)
        self.water_feature_index = int(water_feature_index)

    def _water_history(self, history: ArrayLike) -> NDArray[np.float64]:
        values = _as_float_array(history, name="history")
        if values.ndim == 1:
            values = values[:, None]
        if values.ndim == 3:
            if not -values.shape[2] <= self.water_feature_index < values.shape[2]:
                raise ValueError("water_feature_index is outside history features")
            values = values[..., self.water_feature_index]
        if values.ndim != 2:
            raise ValueError("history must have shape [sample, time] or [sample, time, feature]")
        return values

    def predict_water(
        self,
        history: ArrayLike,
        *,
        missing_mask: ArrayLike | None = None,
    ) -> NDArray[np.float64]:
        values = self._water_history(history)
        valid = np.isfinite(values)
        if missing_mask is not None:
            mask = np.asarray(missing_mask, dtype=bool)
            if mask.ndim == 3:
                mask = mask[..., self.water_feature_index]
            valid &= ~np.broadcast_to(mask, values.shape)
        if np.any(~valid.any(axis=1)):
            missing_samples = np.flatnonzero(~valid.any(axis=1)).tolist()
            raise ValueError(f"no water observation for samples {missing_samples}")
        reverse_index = valid[:, ::-1].argmax(axis=1)
        source_index = values.shape[1] - 1 - reverse_index
        last = values[np.arange(values.shape[0]), source_index]
        return np.repeat(last[:, None], self.forecast_hours, axis=1)

    def predict_quantiles(self, history: ArrayLike, **kwargs: Any) -> NDArray[np.float64]:
        water = self.predict_water(history, **kwargs)
        return np.repeat(water[..., None], 3, axis=-1)

    predict = predict_water

    def to_state(self) -> dict[str, Any]:
        return {
            "type": "persistence_water_baseline",
            "state_version": self.state_version,
            "forecast_hours": self.forecast_hours,
            "water_feature_index": self.water_feature_index,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> PersistenceWaterBaseline:
        if state.get("type") != "persistence_water_baseline":
            raise ValueError("not a PersistenceWaterBaseline state")
        if int(state.get("state_version", -1)) != cls.state_version:
            raise ValueError("unsupported PersistenceWaterBaseline state version")
        return cls(
            forecast_hours=int(state["forecast_hours"]),
            water_feature_index=int(state["water_feature_index"]),
        )

    def save_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_state(), indent=2), encoding="utf-8")

    @classmethod
    def load_json(cls, path: str | Path) -> PersistenceWaterBaseline:
        return cls.from_state(json.loads(Path(path).read_text(encoding="utf-8")))


class PhysicsBaseline:
    """Physical water forecast plus a fitted logistic hazard mapping."""

    state_version = 1

    def __init__(self, forecast_hours: int = 24) -> None:
        if forecast_hours < 1:
            raise ValueError("forecast_hours must be positive")
        self.forecast_hours = int(forecast_hours)
        self.event_mapping = LogisticEventBaseline()
        self.water_offsets_ = np.zeros(3, dtype=np.float64)

    def _forecast(self, forecast: ArrayLike) -> NDArray[np.float64]:
        values = _as_float_array(forecast, name="physics_forecast")
        if values.ndim == 3 and values.shape[-1] == 1:
            values = values[..., 0]
        if values.ndim != 2 or values.shape[1] != self.forecast_hours:
            raise ValueError(f"physics_forecast must have shape [sample, {self.forecast_hours}]")
        return values

    def _event_features(self, forecast: NDArray[np.float64]) -> NDArray[np.float64]:
        leads = np.linspace(0.0, 1.0, self.forecast_hours, dtype=np.float64)
        return np.column_stack((forecast.reshape(-1), np.tile(leads, forecast.shape[0])))

    def fit_event_mapping(
        self,
        physics_forecast: ArrayLike,
        hazard_target: ArrayLike,
        *,
        hazard_mask: ArrayLike | None = None,
        sample_weight: ArrayLike | None = None,
    ) -> PhysicsBaseline:
        forecast = self._forecast(physics_forecast)
        target = np.asarray(hazard_target, dtype=np.float64)
        if target.shape != forecast.shape:
            raise ValueError("hazard_target must match physics_forecast")
        mask = None
        if hazard_mask is not None:
            raw_mask = np.asarray(hazard_mask, dtype=bool)
            if raw_mask.ndim == 1 and raw_mask.shape[0] == forecast.shape[0]:
                raw_mask = raw_mask[:, None]
            mask = np.broadcast_to(raw_mask, forecast.shape)
        weight = None
        if sample_weight is not None:
            raw_weight = np.asarray(sample_weight, dtype=np.float64)
            if raw_weight.ndim == 1 and raw_weight.shape[0] == forecast.shape[0]:
                raw_weight = raw_weight[:, None]
            weight = np.broadcast_to(raw_weight, forecast.shape).reshape(-1)
        self.event_mapping.fit(
            self._event_features(forecast),
            target.reshape(-1),
            mask=None if mask is None else mask.reshape(-1),
            sample_weight=weight,
        )
        return self

    def fit_water_offsets(
        self,
        physics_forecast: ArrayLike,
        observed_water: ArrayLike,
        *,
        mask: ArrayLike | None = None,
    ) -> PhysicsBaseline:
        forecast = self._forecast(physics_forecast)
        observed = np.asarray(observed_water, dtype=np.float64)
        if observed.shape != forecast.shape:
            raise ValueError("observed_water must match physics_forecast")
        valid = np.isfinite(forecast) & np.isfinite(observed)
        if mask is not None:
            valid &= np.broadcast_to(np.asarray(mask, dtype=bool), forecast.shape)
        if not valid.any():
            raise ValueError("no valid water residuals")
        self.water_offsets_ = np.quantile((observed - forecast)[valid], [0.1, 0.5, 0.9])
        return self

    def predict_water_quantiles(self, physics_forecast: ArrayLike) -> NDArray[np.float64]:
        forecast = self._forecast(physics_forecast)
        return forecast[..., None] + self.water_offsets_

    def predict(self, physics_forecast: ArrayLike) -> dict[str, NDArray[np.float64]]:
        forecast = self._forecast(physics_forecast)
        if not self.event_mapping.fitted:
            raise RuntimeError("fit_event_mapping must be called before event prediction")
        logits = self.event_mapping.predict_logits(self._event_features(forecast)).reshape(
            forecast.shape
        )
        log_survival = np.cumsum(-np.logaddexp(0.0, logits), axis=1)
        cumulative = 1.0 - np.exp(log_survival)
        return {
            "hazard_logits": logits,
            "cumulative_event_probability": cumulative,
            "water_quantiles": self.predict_water_quantiles(forecast),
        }

    def to_state(self) -> dict[str, Any]:
        if not self.event_mapping.fitted:
            raise RuntimeError("cannot serialise an unfitted PhysicsBaseline")
        return {
            "type": "physics_baseline",
            "state_version": self.state_version,
            "forecast_hours": self.forecast_hours,
            "water_offsets": self.water_offsets_.tolist(),
            "event_mapping": self.event_mapping.to_state(),
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> PhysicsBaseline:
        if state.get("type") != "physics_baseline":
            raise ValueError("not a PhysicsBaseline state")
        if int(state.get("state_version", -1)) != cls.state_version:
            raise ValueError("unsupported PhysicsBaseline state version")
        model = cls(forecast_hours=int(state["forecast_hours"]))
        model.water_offsets_ = np.asarray(state["water_offsets"], dtype=np.float64)
        model.event_mapping = LogisticEventBaseline.from_state(state["event_mapping"])
        return model

    def save_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_state(), indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )

    @classmethod
    def load_json(cls, path: str | Path) -> PhysicsBaseline:
        return cls.from_state(json.loads(Path(path).read_text(encoding="utf-8")))

    # Registry-friendly concise method names.
    fit = fit_event_mapping

    def predict_water(self, physics_forecast: ArrayLike) -> NDArray[np.float64]:
        return self._forecast(physics_forecast) + self.water_offsets_[1]


# Short aliases used in configuration/model registries.
PersistenceBaseline = PersistenceWaterBaseline
LogisticBaseline = LogisticEventBaseline


__all__ = [
    "LogisticBaseline",
    "LogisticEventBaseline",
    "PersistenceBaseline",
    "PersistenceWaterBaseline",
    "PhysicsBaseline",
    "build_logistic_summary_features",
]
