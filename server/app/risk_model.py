"""Pure-Python inference for the exported coastal risk JSON artifact.

Training uses scikit-learn, but the public gateway intentionally does not.  The
small multinomial logistic model is exported as validated numbers and evaluated
here, keeping deployment lightweight and avoiding unsafe pickle loading.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import EnvironmentResponse

logger = logging.getLogger(__name__)
DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[1] / "models" / "coastal_risk_v1.json"
)
EXPECTED_FEATURES: tuple[str, ...] = (
    "air_temperature_c",
    "humidity_percent",
    "wind_speed_kmh",
    "wave_height_m",
    "wave_period_s",
    "water_temperature_c",
    "sea_level_height_m",
    "ocean_current_velocity_kmh",
    "hour_sin",
    "hour_cos",
    "day_of_year_sin",
    "day_of_year_cos",
    "latitude",
    "longitude",
)
CLASS_NAMES: tuple[str, ...] = ("safe", "advisory", "warning", "critical")
DEFAULT_THRESHOLDS: dict[str, tuple[float, float, float]] = {
    "wave_height_m": (1.5, 2.5, 4.0),
    "wind_speed_kmh": (30.0, 50.0, 70.0),
}


@dataclass(frozen=True)
class EnvironmentalPrediction:
    level: int
    probability: float
    probabilities: tuple[float, ...]
    reason_codes: tuple[str, ...]
    missing_features: tuple[str, ...]
    model_version: str
    model_source: str
    deployment_mode: str
    forecast_horizon_hours: int


@dataclass(frozen=True)
class LoadedRiskModel:
    model_version: str
    forecast_horizon_hours: int
    deployment_mode: str
    feature_names: tuple[str, ...]
    class_names: tuple[str, ...]
    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[tuple[float, ...], ...]
    intercept: tuple[float, ...]

    def predict(
        self,
        environment: EnvironmentResponse,
        location: Mapping[str, Any] | None,
        prediction_time: datetime | None = None,
    ) -> EnvironmentalPrediction:
        timestamp = prediction_time or datetime.now(timezone.utc)
        raw, missing = _live_features(environment, location, timestamp)
        imputed = [
            self.medians[index] if value is None else value
            for index, value in enumerate(raw)
        ]
        standardized = [
            (value - self.means[index]) / self.scales[index]
            for index, value in enumerate(imputed)
        ]
        logits = [
            self.intercept[class_index]
            + sum(
                coefficient * value
                for coefficient, value in zip(
                    self.coefficients[class_index], standardized, strict=True
                )
            )
            for class_index in range(len(self.class_names))
        ]
        probabilities = _softmax(logits)
        level = max(range(len(probabilities)), key=probabilities.__getitem__)
        reasons = _model_reason_codes(level, standardized, self.coefficients)
        if missing:
            reasons.append("MISSING_ENVIRONMENT_FEATURES")
        return EnvironmentalPrediction(
            level=level,
            probability=probabilities[level],
            probabilities=tuple(probabilities),
            reason_codes=tuple(reasons),
            missing_features=tuple(missing),
            model_version=self.model_version,
            model_source="model",
            deployment_mode=self.deployment_mode,
            forecast_horizon_hours=self.forecast_horizon_hours,
        )


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _numeric_sequence(
    payload: Mapping[str, Any], name: str, expected_size: int
) -> tuple[float, ...]:
    values = payload.get(name)
    if not isinstance(values, list) or len(values) != expected_size:
        raise ValueError(f"artifact {name} must contain {expected_size} numbers")
    result = tuple(_finite(value) for value in values)
    if any(value is None for value in result):
        raise ValueError(f"artifact {name} contains a non-finite value")
    return tuple(float(value) for value in result if value is not None)


def load_risk_model(path: Path | None = None) -> LoadedRiskModel | None:
    model_path = path or DEFAULT_MODEL_PATH
    if not model_path.exists():
        logger.warning("Risk model not found at %s; using rule fallback", model_path)
        return None
    payload = json.loads(model_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(  # noqa: TRY004 - artifact contents are invalid input.
            "risk model artifact must be a JSON object"
        )
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported risk model schema_version")
    if payload.get("model_type") != "multinomial_logistic_regression":
        raise ValueError("unsupported risk model type")

    features = payload.get("feature_names")
    classes = payload.get("class_names")
    if features != list(EXPECTED_FEATURES):
        raise ValueError("risk model feature order does not match the server")
    if classes != list(CLASS_NAMES):
        raise ValueError("risk model class order does not match the server")
    size = len(EXPECTED_FEATURES)
    medians = _numeric_sequence(payload, "imputer_median", size)
    means = _numeric_sequence(payload, "scaler_mean", size)
    scales = _numeric_sequence(payload, "scaler_scale", size)
    if any(value <= 0 for value in scales):
        raise ValueError("artifact scaler_scale values must be positive")

    raw_coefficients = payload.get("coefficients")
    if not isinstance(raw_coefficients, list) or len(raw_coefficients) != len(
        CLASS_NAMES
    ):
        raise ValueError("artifact coefficients must contain one row per class")
    coefficients = tuple(
        _numeric_sequence({"row": row}, "row", size) for row in raw_coefficients
    )
    intercept = _numeric_sequence(payload, "intercept", len(CLASS_NAMES))
    model_version = payload.get("model_version")
    deployment_mode = payload.get("deployment_mode")
    horizon = payload.get("forecast_horizon_hours")
    if not isinstance(model_version, str) or not model_version.strip():
        raise ValueError("artifact model_version is required")
    if deployment_mode not in {"shadow", "active"}:
        raise ValueError("artifact deployment_mode must be shadow or active")
    if (
        isinstance(horizon, bool)
        or not isinstance(horizon, int)
        or not 1 <= horizon <= 72
    ):
        raise ValueError("artifact forecast_horizon_hours is invalid")
    return LoadedRiskModel(
        model_version=model_version,
        forecast_horizon_hours=horizon,
        deployment_mode=str(deployment_mode),
        feature_names=tuple(features),
        class_names=tuple(classes),
        medians=medians,
        means=means,
        scales=scales,
        coefficients=coefficients,
        intercept=intercept,
    )


def _live_features(
    environment: EnvironmentResponse,
    location: Mapping[str, Any] | None,
    timestamp: datetime,
) -> tuple[list[float | None], list[str]]:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    hour_angle = 2.0 * math.pi * (timestamp.hour + timestamp.minute / 60.0) / 24.0
    days_in_year = 366.0 if _is_leap_year(timestamp.year) else 365.0
    day_angle = 2.0 * math.pi * (timestamp.timetuple().tm_yday - 1) / days_in_year
    location = location or {}
    values: dict[str, float | None] = {
        "air_temperature_c": _finite(environment.air_temperature_c),
        "humidity_percent": _finite(environment.humidity_percent),
        "wind_speed_kmh": _finite(environment.wind_speed_kmh),
        "wave_height_m": _finite(environment.wave_height_m),
        "wave_period_s": _finite(environment.wave_period_s),
        "water_temperature_c": _finite(environment.water_temperature_c),
        "sea_level_height_m": _finite(environment.sea_level_height_m),
        "ocean_current_velocity_kmh": _finite(environment.ocean_current_velocity_kmh),
        "hour_sin": math.sin(hour_angle),
        "hour_cos": math.cos(hour_angle),
        "day_of_year_sin": math.sin(day_angle),
        "day_of_year_cos": math.cos(day_angle),
        "latitude": _finite(location.get("latitude")),
        "longitude": _finite(location.get("longitude")),
    }
    missing = [name for name in EXPECTED_FEATURES if values[name] is None]
    return [values[name] for name in EXPECTED_FEATURES], missing


def _softmax(values: Sequence[float]) -> list[float]:
    maximum = max(values)
    exponents = [math.exp(value - maximum) for value in values]
    total = sum(exponents)
    return [value / total for value in exponents]


def _model_reason_codes(
    level: int,
    standardized: Sequence[float],
    coefficients: Sequence[Sequence[float]],
) -> list[str]:
    if level == 0:
        return ["MODEL_LOW_RISK"]
    comparison_level = 0
    contributions = [
        (
            (coefficients[level][index] - coefficients[comparison_level][index])
            * standardized[index],
            EXPECTED_FEATURES[index],
        )
        for index in range(len(EXPECTED_FEATURES))
    ]
    code_by_feature = {
        "air_temperature_c": "AIR_TEMPERATURE_SIGNAL",
        "humidity_percent": "HUMIDITY_SIGNAL",
        "wind_speed_kmh": "WIND_SIGNAL",
        "wave_height_m": "WAVE_HEIGHT_SIGNAL",
        "wave_period_s": "WAVE_PERIOD_SIGNAL",
        "water_temperature_c": "WATER_TEMPERATURE_SIGNAL",
        "sea_level_height_m": "SEA_LEVEL_CONTEXT",
        "ocean_current_velocity_kmh": "OCEAN_CURRENT_SIGNAL",
        "hour_sin": "TIME_OF_DAY_CONTEXT",
        "hour_cos": "TIME_OF_DAY_CONTEXT",
        "day_of_year_sin": "SEASONAL_CONTEXT",
        "day_of_year_cos": "SEASONAL_CONTEXT",
        "latitude": "LOCATION_CONTEXT",
        "longitude": "LOCATION_CONTEXT",
    }
    result: list[str] = []
    for contribution, name in sorted(contributions, reverse=True):
        if contribution <= 0:
            continue
        code = code_by_feature[name]
        if code not in result:
            result.append(code)
        if len(result) == 3:
            break
    return result or ["MODEL_COMBINED_SIGNAL"]


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


def rule_fallback(environment: EnvironmentResponse) -> EnvironmentalPrediction:
    wave_level = _threshold_level(
        _finite(environment.wave_height_m), DEFAULT_THRESHOLDS["wave_height_m"]
    )
    wind_level = _threshold_level(
        _finite(environment.wind_speed_kmh), DEFAULT_THRESHOLDS["wind_speed_kmh"]
    )
    level = max(wave_level, wind_level)
    reasons: list[str] = []
    if wave_level:
        reasons.append("RULE_WAVE_THRESHOLD")
    if wind_level:
        reasons.append("RULE_WIND_THRESHOLD")
    if wave_level and wind_level:
        level = min(3, level + 1)
        reasons.append("RULE_COMPOUND_WAVE_WIND")
    if not reasons:
        reasons.append("RULE_LOW_ENVIRONMENTAL_SIGNAL")
    probability = (0.80, 0.65, 0.72, 0.80)[level]
    probabilities = [0.0] * len(CLASS_NAMES)
    probabilities[level] = probability
    remainder = (1.0 - probability) / (len(CLASS_NAMES) - 1)
    for index in range(len(probabilities)):
        if index != level:
            probabilities[index] = remainder
    missing = tuple(
        name
        for name, value in (
            ("wave_height_m", environment.wave_height_m),
            ("wind_speed_kmh", environment.wind_speed_kmh),
        )
        if _finite(value) is None
    )
    return EnvironmentalPrediction(
        level=level,
        probability=probability,
        probabilities=tuple(probabilities),
        reason_codes=tuple(reasons),
        missing_features=missing,
        model_version="rule-fallback-v1",
        model_source="rule-fallback",
        deployment_mode="fallback",
        forecast_horizon_hours=0,
    )


def build_risk_result(
    model: LoadedRiskModel | None,
    telemetry: Mapping[str, Any],
    environment: EnvironmentResponse,
    location: Mapping[str, Any] | None,
) -> dict[str, Any]:
    environmental = (
        model.predict(environment, location)
        if model is not None
        else rule_fallback(environment)
    )
    return build_risk_result_from_prediction(
        environmental,
        telemetry,
        environment,
        extra_degraded=model is None,
    )


def build_risk_result_from_prediction(
    environmental: EnvironmentalPrediction,
    telemetry: Mapping[str, Any],
    environment: EnvironmentResponse,
    *,
    environment_required: bool = True,
    extra_degraded: bool = False,
    data_quality_override: str | None = None,
) -> dict[str, Any]:
    """Merge one research-model result with the independent device alarm floor."""

    local_alarm_level = int(telemetry["alarm_level"])
    sensor_fault = local_alarm_level == 4
    local_risk_level = 0 if sensor_fault else min(3, max(0, local_alarm_level))
    risk_level = max(environmental.level, local_risk_level)
    reasons = list(environmental.reason_codes)
    if local_risk_level > environmental.level:
        reasons.append("LOCAL_RULE_FLOOR")
    if bool(telemetry.get("person_detected")):
        reasons.append("PERSON_PRESENT")
    if sensor_fault:
        reasons.append("SENSOR_FAULT")

    stale_environment = environment_required and (
        environment.stale or environment.source != "open-meteo"
    )
    if data_quality_override is not None:
        data_quality = data_quality_override
    elif sensor_fault:
        data_quality = "fault"
    elif stale_environment:
        data_quality = "stale"
    else:
        data_quality = "ok"
    degraded = (
        extra_degraded
        or sensor_fault
        or stale_environment
        or len(environmental.missing_features) > 2
    )
    risk_score = (
        1.0 if local_risk_level > environmental.level else environmental.probability
    )
    return {
        "device_id": telemetry["device_id"],
        "location": environment.location,
        "risk_level": risk_level,
        "risk_name": CLASS_NAMES[risk_level],
        "risk_score": risk_score,
        "environmental_level": environmental.level,
        "environmental_probability": environmental.probability,
        "local_alarm_level": local_alarm_level,
        "data_quality": data_quality,
        "model_source": environmental.model_source,
        "deployment_mode": environmental.deployment_mode,
        "model_version": environmental.model_version,
        "forecast_horizon_hours": environmental.forecast_horizon_hours,
        "degraded": degraded,
        "reason_codes": list(dict.fromkeys(reasons)),
        "missing_features": list(environmental.missing_features),
        "telemetry_id": telemetry["id"],
        "predicted_at": datetime.now(timezone.utc),
        "environment_updated_at": environment.updated_at,
    }


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
