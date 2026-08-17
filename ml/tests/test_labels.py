from datetime import datetime, timedelta, timezone

from coastal_risk.constants import LABEL_RULE_VERSION
from coastal_risk.features import feature_mapping
from coastal_risk.labels import add_future_targets, instant_weak_label


def row(hour: int, *, wave: float = 0.5, wind: float = 10.0) -> dict:
    timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hour)
    return {
        "location_id": "uk_brighton",
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "latitude": 50.8,
        "longitude": -0.1,
        "wave_height_m": wave,
        "wind_speed_kmh": wind,
    }


def test_compound_weak_label_is_versioned_and_explainable():
    level, reasons = instant_weak_label(row(0, wave=1.6, wind=31.0))
    assert level == 2
    assert reasons == ["HIGH_WAVE", "STRONG_WIND", "COMPOUND_WAVE_WIND"]
    assert LABEL_RULE_VERSION == "demo_environment_rule_v1"


def test_future_target_uses_next_six_hours_and_drops_incomplete_tail():
    records = [row(hour) for hour in range(10)]
    records[4] = row(4, wave=4.2)
    labelled = add_future_targets(records, horizon_hours=6)
    assert len(labelled) == 4
    assert labelled[0]["target_risk_level"] == 3
    assert labelled[-1]["target_risk_level"] == 3
    assert labelled[0]["instant_risk_level"] == 0


def test_cyclic_features_are_finite_and_in_expected_range():
    features = feature_mapping(row(0))
    for name in ("hour_sin", "hour_cos", "day_of_year_sin", "day_of_year_cos"):
        assert -1.0 <= features[name] <= 1.0

