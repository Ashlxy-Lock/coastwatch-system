"""Dispatch a server model without changing ESP32-local safety logic."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .experiment_store import (
    get_active_official_model,
    get_sensor_session_snapshot,
    get_sensor_test_profile,
)
from .model_registry import (
    CUSTOM_MODEL_ID,
    DEFAULT_MODEL_ID,
    OFFICIAL_MODEL_ID,
    custom_model_path,
)
from .official_model import OfficialModelError, load_official_model
from .risk_model import (
    EnvironmentalPrediction,
    LoadedRiskModel,
    build_risk_result,
    build_risk_result_from_prediction,
)
from .schemas import EnvironmentResponse
from .sensor_proxy_model import (
    SensorProxyError,
    load_sensor_proxy_profile,
    run_sensor_proxy_external_test,
)
from .simulation_model import (
    SIMULATED_ENVIRONMENT_FEATURE_ORDER,
    load_simulation_model,
    predict_simulation,
)
from .simulation_store import (
    get_device_simulation_scenario,
    get_simulation_scenario,
    get_simulation_session,
)
from .telemetry_quality import (
    TELEMETRY_WINDOW_MAX_RECEIVED_GAP_SECONDS,
    TELEMETRY_WINDOW_MAX_SEQ_GAP,
    TELEMETRY_WINDOW_MAX_UPTIME_GAP_MS,
    ULTRASONIC_HEALTH_BIT,
    ULTRASONIC_MAX_DISTANCE_MM,
    ULTRASONIC_MIN_DISTANCE_MM,
    telemetry_samples_are_contiguous,
    telemetry_timestamp,
    ultrasonic_sample_is_valid,
)

CUSTOM_DANGER_LEVEL = 2
TELEMETRY_STALE_SECONDS = 10.0
TELEMETRY_FUTURE_TOLERANCE_SECONDS = 1.0
# ESP32 normally uploads every 2 s (500 ms while collecting).  These limits
# tolerate one missed normal upload but never join a model window across a
# reboot, a long outage, or a different collection-session epoch.
CUSTOM_WINDOW_MAX_SAMPLE_AGE_SECONDS = TELEMETRY_STALE_SECONDS
CUSTOM_WINDOW_MAX_RECEIVED_GAP_SECONDS = TELEMETRY_WINDOW_MAX_RECEIVED_GAP_SECONDS
CUSTOM_WINDOW_MAX_UPTIME_GAP_MS = TELEMETRY_WINDOW_MAX_UPTIME_GAP_MS
CUSTOM_WINDOW_MAX_SEQ_GAP = TELEMETRY_WINDOW_MAX_SEQ_GAP


def build_official_sensor_environment(device_id: str) -> EnvironmentResponse:
    """Expose the profile's frozen UK row through the existing ESP schema."""

    stored = get_sensor_test_profile(device_id)
    if stored is None:
        raise ValueError("official model requires a sensor test profile")
    active = get_active_official_model()
    if active is None:
        raise ValueError("official model is not activated")
    profile = stored.get("profile")
    if not isinstance(profile, Mapping):
        raise SensorProxyError("sensor test profile is invalid")
    try:
        official_model = load_official_model(
            str(active["artifact_path"]), require_activatable=True
        )
        load_sensor_proxy_profile(profile, official_model=official_model)
    except (OSError, UnicodeError, OfficialModelError, SensorProxyError) as exc:
        raise ValueError("sensor test profile is invalid") from exc
    if (
        official_model.artifact_sha256 != str(active["artifact_sha256"])
        or str(stored.get("artifact_sha256", ""))
        != official_model.artifact_sha256
    ):
        raise ValueError("sensor test profile does not match active artifact")
    context = profile.get("official_context")
    mapping = profile.get("mapping")
    if not isinstance(context, Mapping) or not isinstance(mapping, Mapping):
        raise SensorProxyError("sensor test profile is incomplete")
    features = context.get("features")
    if not isinstance(features, Mapping):
        raise SensorProxyError("sensor test profile context is incomplete")
    site_id = str(profile.get("site_id", ""))
    if not site_id:
        raise ValueError("sensor test profile station is missing")
    display = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in site_id.upper()
    )[:32]
    return EnvironmentResponse(
        location=site_id,
        display_location=display or "UK_OFFICIAL_COAST",
        kind="coast",
        weather="OFFICIAL SENSOR TEST",
        weather_code=None,
        air_temperature_c=float(features["air_temperature_c"]),
        humidity_percent=float(features["relative_humidity_percent"]),
        wind_speed_kmh=float(features["wind_speed_m_s"]) * 3.6,
        wind_direction_deg=None,
        water_temperature_c=float(features["water_temperature_c"]),
        wave_height_m=float(features["significant_wave_height_m"]),
        wave_period_s=float(features["wave_period_s"]),
        # The profile intercept is a datum-relative affine mapping constant, not
        # a live sea-level observation.  Exposing it here would also let a valid
        # research profile exceed the ESP32 environment parser's physical range
        # and make the whole payload fail closed.
        sea_level_height_m=None,
        tide_status="SENSOR PROXY",
        ocean_current_velocity_kmh=(
            float(features["ocean_current_velocity_m_s"]) * 3.6
        ),
        ocean_current_direction_deg=None,
        source="manual",
        provider="UK OFFICIAL FROZEN CONTEXT",
        stale=False,
        updated_at=datetime.fromisoformat(
            str(context["timestamp"]).replace("Z", "+00:00")
        ),
    )


def build_selected_risk_result(
    selected_model_id: str,
    legacy_model: LoadedRiskModel | None,
    telemetry: Mapping[str, Any],
    telemetry_window: Sequence[Mapping[str, Any]],
    environment: EnvironmentResponse,
    location: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if selected_model_id == DEFAULT_MODEL_ID:
        return build_risk_result(legacy_model, telemetry, environment, location)
    if selected_model_id == OFFICIAL_MODEL_ID:
        return _build_official_sensor_risk_result(
            telemetry, environment, now=now
        )
    if selected_model_id != CUSTOM_MODEL_ID:
        raise ValueError(f"selected model {selected_model_id} cannot serve live risk")

    if not ultrasonic_sample_is_valid(telemetry):
        raise ValueError("custom water model requires a healthy ultrasonic sample")
    if environment.source != "manual":
        raise ValueError("custom water model requires explicit simulated environment")

    device_id = str(telemetry.get("device_id", ""))
    scenario = get_device_simulation_scenario(device_id)
    if scenario is None:
        raise ValueError("custom water model requires an active operator scenario")

    loaded = load_simulation_model(custom_model_path())
    window_size = loaded.window_size
    ordered_window = _continuous_custom_window(
        telemetry,
        telemetry_window,
        window_size=window_size,
        now=now,
    )
    session_id = _simulation_epoch(telemetry)
    baseline_distance_mm: Any = None
    if session_id is not None:
        session_scenario = get_simulation_scenario(session_id, device_id)
        if session_scenario is None:
            raise ValueError("active collection has no immutable scenario snapshot")
        if session_scenario["scenario_hash"] != scenario["scenario_hash"]:
            raise ValueError("active device scenario does not match session snapshot")
        session = get_simulation_session(session_id, device_id)
        baseline_distance_mm = (
            session.get("baseline_distance_mm") if session is not None else None
        )
    context = {
        name: float(scenario[name]) for name in SIMULATED_ENVIRONMENT_FEATURE_ORDER
    }
    prediction_window: list[dict[str, Any]] = []
    for row in ordered_window:
        item = dict(row)
        item.update(context)
        item["scenario_hash"] = scenario["scenario_hash"]
        if baseline_distance_mm is not None:
            item["baseline_distance_mm"] = baseline_distance_mm
        prediction_window.append(item)

    prediction = predict_simulation(loaded, prediction_window)
    danger_probability = float(prediction["danger_probability"])
    predicted_danger = prediction["predicted_label"] == "danger"
    selected_probability = (
        danger_probability if predicted_danger else 1.0 - danger_probability
    )
    limited_history = prediction["quality"] != "ok"
    environmental = EnvironmentalPrediction(
        level=CUSTOM_DANGER_LEVEL if predicted_danger else 0,
        probability=selected_probability,
        probabilities=(1.0 - danger_probability, danger_probability),
        reason_codes=(
            "SIMULATION_DANGER_PATTERN"
            if predicted_danger
            else "SIMULATION_SAFE_PATTERN",
            "SIMULATION_DATA_ONLY",
            "OPERATOR_SUPPLIED_SIMULATION",
        ),
        missing_features=("history_window",) if limited_history else (),
        model_version=f"{loaded.model_id}-{loaded.version}",
        model_source="model",
        deployment_mode="shadow",
        forecast_horizon_hours=0,
    )
    stale = _telemetry_is_stale(telemetry, now=now)
    result = build_risk_result_from_prediction(
        environmental,
        telemetry,
        environment,
        environment_required=False,
        extra_degraded=True,
        data_quality_override="stale" if stale else None,
    )
    if limited_history:
        result["reason_codes"].append("LIMITED_HISTORY")
    return result


def _build_official_sensor_risk_result(
    telemetry: Mapping[str, Any],
    environment: EnvironmentResponse,
    *,
    now: datetime | None,
) -> dict[str, Any]:
    if not ultrasonic_sample_is_valid(telemetry):
        raise ValueError("official sensor test requires a healthy ultrasonic sample")
    active = get_active_official_model()
    if active is None:
        raise ValueError("official model is not activated")
    try:
        official_model = load_official_model(
            str(active["artifact_path"]), require_activatable=True
        )
    except (OSError, UnicodeError, OfficialModelError) as exc:
        raise ValueError("active official artifact is invalid") from exc
    if official_model.artifact_sha256 != str(active["artifact_sha256"]):
        raise ValueError("active official artifact hash mismatch")

    device_id = str(telemetry.get("device_id", ""))
    session_id = _simulation_epoch(telemetry)
    stored: Mapping[str, Any] | None = None
    if session_id is not None:
        snapshot = get_sensor_session_snapshot(session_id)
        if snapshot is not None and snapshot.get("device_id") == device_id:
            stored = snapshot
    if stored is None:
        stored = get_sensor_test_profile(device_id)
    if stored is None or not isinstance(stored.get("profile"), Mapping):
        raise ValueError("official model requires a sensor test profile")
    if str(stored.get("artifact_sha256", "")) != official_model.artifact_sha256:
        raise ValueError("sensor profile does not match active official artifact")
    try:
        profile = load_sensor_proxy_profile(
            stored["profile"], official_model=official_model
        )
        test = run_sensor_proxy_external_test(
            official_model,
            profile,
            [telemetry],
            session_id=session_id or f"live-{device_id}",
        )
    except SensorProxyError as exc:
        raise ValueError("sensor proxy inference failed") from exc
    row = test["rows"][0]
    extreme_probability = float(row["extreme_water_probability"])
    predicted_extreme = row["predicted_label"] == "extreme_water"
    selected_probability = (
        extreme_probability if predicted_extreme else 1.0 - extreme_probability
    )
    reason_codes = ["UK_OFFICIAL_MODEL", "SENSOR_PROXY_EXTERNAL_TEST", "LINEAR_GAIN_V1", "SHADOW_ONLY"]
    if row["out_of_distribution"]:
        reason_codes.append("OUT_OF_DISTRIBUTION")
    horizon = round(
        float(
            official_model.source_manifest["label_definition"][
                "forecast_horizon_hours"
            ]
        )
    )
    environmental = EnvironmentalPrediction(
        level=2 if predicted_extreme else 0,
        probability=selected_probability,
        probabilities=(1.0 - extreme_probability, extreme_probability),
        reason_codes=tuple(reason_codes),
        missing_features=(),
        model_version=f"{official_model.model_id}-{official_model.version}",
        model_source="model",
        deployment_mode="shadow",
        forecast_horizon_hours=max(0, horizon),
    )
    stale = _telemetry_is_stale(telemetry, now=now)
    return build_risk_result_from_prediction(
        environmental,
        telemetry,
        environment,
        environment_required=False,
        extra_degraded=True,
        data_quality_override="stale" if stale else None,
    )


def _continuous_custom_window(
    telemetry: Mapping[str, Any],
    telemetry_window: Sequence[Mapping[str, Any]],
    *,
    window_size: int,
    now: datetime | None,
) -> list[Mapping[str, Any]]:
    """Return a complete oldest-to-newest suffix from one fresh device epoch.

    The history supplied by the database is newest-first.  Boundaries are
    fail-closed: an invalid echo, collection-session switch, restart, duplicate,
    or excessive time/sequence gap stops the suffix rather than being skipped.
    """

    current = _normalise_now(now)
    if not _window_sample_is_fresh(telemetry, now=current):
        raise ValueError("custom water model requires fresh telemetry")

    newest_to_oldest: list[Mapping[str, Any]] = [telemetry]
    anchor_id = _optional_integer(telemetry.get("id"))
    for candidate in telemetry_window:
        if len(newest_to_oldest) >= window_size:
            break
        if _same_telemetry_record(candidate, telemetry):
            continue
        candidate_id = _optional_integer(candidate.get("id"))
        if (
            anchor_id is not None
            and candidate_id is not None
            and candidate_id > anchor_id
        ):
            # A row can arrive between latest_telemetry() and telemetry_history().
            # It is newer than the anchor used for this request, so ignore it.
            continue
        newer = newest_to_oldest[-1]
        if not _custom_window_pair_is_contiguous(candidate, newer, now=current):
            break
        newest_to_oldest.append(candidate)

    if len(newest_to_oldest) < window_size:
        raise ValueError(
            "custom water model requires a complete contiguous fresh history window"
        )
    return list(reversed(newest_to_oldest))


def _custom_window_pair_is_contiguous(
    older: Mapping[str, Any],
    newer: Mapping[str, Any],
    *,
    now: datetime,
) -> bool:
    if not telemetry_samples_are_contiguous(older, newer):
        return False
    return _window_sample_is_fresh(older, now=now)


def _window_sample_is_fresh(sample: Mapping[str, Any], *, now: datetime) -> bool:
    timestamp = telemetry_timestamp(sample)
    if timestamp is None:
        return False
    age = (now - timestamp).total_seconds()
    return (
        -TELEMETRY_FUTURE_TOLERANCE_SECONDS
        <= age
        <= CUSTOM_WINDOW_MAX_SAMPLE_AGE_SECONDS
    )


def _normalise_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _optional_integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _simulation_epoch(sample: Mapping[str, Any]) -> str | None:
    value = sample.get("simulation_session_id")
    return None if value is None else str(value)


def _same_telemetry_record(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_id = _optional_integer(left.get("id"))
    right_id = _optional_integer(right.get("id"))
    if left_id is not None and right_id is not None:
        return left_id == right_id
    return all(
        left.get(field) == right.get(field)
        for field in ("device_id", "seq", "uptime_ms", "received_at")
    )


def _telemetry_is_stale(
    telemetry: Mapping[str, Any], *, now: datetime | None = None
) -> bool:
    timestamp = telemetry_timestamp(telemetry)
    if timestamp is None:
        return True
    age = (_normalise_now(now) - timestamp).total_seconds()
    return age < -TELEMETRY_FUTURE_TOLERANCE_SECONDS or age > TELEMETRY_STALE_SECONDS


__all__ = [
    "CUSTOM_WINDOW_MAX_RECEIVED_GAP_SECONDS",
    "CUSTOM_WINDOW_MAX_SAMPLE_AGE_SECONDS",
    "CUSTOM_WINDOW_MAX_SEQ_GAP",
    "CUSTOM_WINDOW_MAX_UPTIME_GAP_MS",
    "ULTRASONIC_HEALTH_BIT",
    "ULTRASONIC_MAX_DISTANCE_MM",
    "ULTRASONIC_MIN_DISTANCE_MM",
    "build_official_sensor_environment",
    "build_selected_risk_result",
    "ultrasonic_sample_is_valid",
]
