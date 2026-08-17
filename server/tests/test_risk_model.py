import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.risk_model import (
    CLASS_NAMES,
    EXPECTED_FEATURES,
    build_risk_result,
    load_risk_model,
    rule_fallback,
)
from app.schemas import EnvironmentResponse


def environment(**overrides) -> EnvironmentResponse:
    values = {
        "location": "Brighton, England, United Kingdom",
        "display_location": "BRIGHTON ENGLAND GB",
        "kind": "coast",
        "weather": "Cloudy",
        "weather_code": 3,
        "air_temperature_c": 12.0,
        "humidity_percent": 75.0,
        "wind_speed_kmh": 20.0,
        "wind_direction_deg": 220.0,
        "water_temperature_c": 11.0,
        "wave_height_m": 0.8,
        "wave_period_s": 6.0,
        "sea_level_height_m": 0.2,
        "tide_status": "rising",
        "ocean_current_velocity_kmh": 0.7,
        "ocean_current_direction_deg": 90.0,
        "source": "open-meteo",
        "provider": "open-meteo",
        "stale": False,
        "updated_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return EnvironmentResponse(**values)


def artifact() -> dict:
    size = len(EXPECTED_FEATURES)
    # A deterministic test model whose critical logit increases with wave height.
    coefficients = [[0.0] * size for _ in CLASS_NAMES]
    coefficients[3][EXPECTED_FEATURES.index("wave_height_m")] = 5.0
    return {
        "schema_version": 1,
        "model_type": "multinomial_logistic_regression",
        "model_version": "test-model-v1",
        "forecast_horizon_hours": 6,
        "deployment_mode": "shadow",
        "class_names": list(CLASS_NAMES),
        "feature_names": list(EXPECTED_FEATURES),
        "imputer_median": [0.0] * size,
        "scaler_mean": [0.0] * size,
        "scaler_scale": [1.0] * size,
        "coefficients": coefficients,
        "intercept": [1.0, 0.0, 0.0, 0.0],
    }


def test_json_model_loads_and_predicts_without_sklearn(tmp_path: Path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact()), encoding="utf-8")
    model = load_risk_model(path)
    assert model is not None
    prediction = model.predict(
        environment(wave_height_m=2.0),
        {"latitude": 50.8, "longitude": -0.1},
        datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    assert prediction.level == 3
    assert prediction.probability > 0.99
    assert "WAVE_HEIGHT_SIGNAL" in prediction.reason_codes


def test_model_loader_rejects_feature_order_mismatch(tmp_path: Path):
    payload = artifact()
    payload["feature_names"] = list(reversed(payload["feature_names"]))
    path = tmp_path / "bad-model.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="feature order"):
        load_risk_model(path)


def test_rule_fallback_is_explicit_and_compound():
    prediction = rule_fallback(
        environment(wave_height_m=2.0, wind_speed_kmh=35.0)
    )
    assert prediction.level == 2
    assert prediction.model_source == "rule-fallback"
    assert "RULE_COMPOUND_WAVE_WIND" in prediction.reason_codes


def test_local_fault_is_quality_state_not_a_fifth_risk_class():
    telemetry = {
        "id": 9,
        "device_id": "COAST_01",
        "alarm_level": 4,
        "person_detected": False,
    }
    result = build_risk_result(None, telemetry, environment(), None)
    assert 0 <= result["risk_level"] <= 3
    assert result["data_quality"] == "fault"
    assert result["degraded"] is True
    assert "SENSOR_FAULT" in result["reason_codes"]
