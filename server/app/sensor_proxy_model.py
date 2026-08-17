"""Frozen affine ESP32 ultrasonic proxy for post-training external tests.

This module never fits a model, scaler, decision threshold, or calibration on
official validation/test outcomes.  A sensor profile replaces exactly the
``relative_water_level_m`` channel while all other features come from one
artifact-pinned official holdout row.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .official_dataset import (
    NON_WATER_FEATURE_ORDER,
    OFFICIAL_FEATURE_ORDER,
    canonical_json_bytes,
)
from .official_model import LoadedOfficialModel

PROFILE_SCHEMA = "coastwatch.sensor-proxy-profile"
PROFILE_SCHEMA_VERSION = 1


class SensorProxyError(ValueError):
    """Raised when a profile or external sensor test violates isolation rules."""


@dataclass(frozen=True)
class LoadedSensorProxyProfile:
    profile_id: str
    mode: str
    exploratory: bool
    model_id: str
    model_version: str
    official_model_artifact_sha256: str
    site_id: str
    datum: str
    context_id: str
    context_timestamp: str
    context_source_split: str
    context_source_row_sha256: str
    context_features: Mapping[str, float]
    gain_m_per_m: float
    reference_level_m: float
    official_train_q05_m: float | None
    official_train_q95_m: float | None
    calibration_rise_q05_mm: float | None
    calibration_rise_q95_mm: float | None
    calibration_session_id: str | None
    calibration_device_id: str | None
    calibration_sample_count: int | None
    calibration_samples_sha256: str | None
    created_at: str
    profile_sha256: str

    def map_water_rise_mm(self, value: Any) -> float:
        rise_mm = _finite_number(value, "water_rise_mm")
        return self.reference_level_m + self.gain_m_per_m * (rise_mm / 1000.0)


def build_sensor_proxy_profile(
    *,
    profile_id: str,
    official_model: LoadedOfficialModel,
    official_context: Mapping[str, Any],
    calibration_water_rise_mm: Sequence[float] | None = None,
    calibration_source: Mapping[str, Any] | None = None,
    manual_gain: float | None = None,
    manual_reference_level_m: float | None = None,
    exploratory: bool = False,
    created_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Build a formal quantile mapping or clearly marked exploratory mapping.

    Formal mode derives gain from the artifact-pinned official TRAIN Q05/Q95
    and an independent sensor calibration sequence.  Manual gain/reference are
    accepted only when ``exploratory=True`` and are excluded from formal
    evidence by construction.
    """

    if not isinstance(profile_id, str) or not profile_id.strip():
        raise SensorProxyError("profile_id must be a non-empty string")
    context = _match_artifact_context(official_model, official_context)
    reference = _mapping_reference(official_model, context["site_id"])

    if exploratory:
        if calibration_water_rise_mm is not None or calibration_source is not None:
            raise SensorProxyError(
                "exploratory manual mapping cannot also use calibration samples"
            )
        gain = _finite_number(manual_gain, "manual_gain")
        base = _finite_number(manual_reference_level_m, "manual_reference_level_m")
        if gain <= 0:
            raise SensorProxyError("manual_gain must be greater than zero")
        mode = "exploratory_manual_linear"
        calibration_low = None
        calibration_high = None
        official_low = None
        official_high = None
        calibration_record = None
    else:
        if manual_gain is not None or manual_reference_level_m is not None:
            raise SensorProxyError("manual gain/reference require exploratory=True")
        if (
            isinstance(calibration_water_rise_mm, (str, bytes))
            or calibration_water_rise_mm is None
            or len(calibration_water_rise_mm) < 5
        ):
            raise SensorProxyError(
                "formal mapping requires at least five independent calibration samples"
            )
        calibration = sorted(
            _finite_number(value, "calibration_water_rise_mm")
            for value in calibration_water_rise_mm
        )
        calibration_low = _linear_quantile(calibration, 0.05)
        calibration_high = _linear_quantile(calibration, 0.95)
        if calibration_high <= calibration_low:
            raise SensorProxyError("calibration Q95 must exceed Q05")
        official_low = _finite_number(
            reference["official_train_q05_m"], "official_train_q05_m"
        )
        official_high = _finite_number(
            reference["official_train_q95_m"], "official_train_q95_m"
        )
        gain = (official_high - official_low) / (
            (calibration_high - calibration_low) / 1000.0
        )
        base = official_low - gain * (calibration_low / 1000.0)
        mode = "formal_train_quantile_linear"
        if not isinstance(calibration_source, Mapping):
            raise SensorProxyError(
                "formal mapping requires calibration_source provenance"
            )
        calibration_session_id = _required_string(calibration_source, "session_id")
        calibration_device_id = _required_string(calibration_source, "device_id")
        calibration_record = {
            "session_id": calibration_session_id,
            "device_id": calibration_device_id,
            "purpose": "independent_sensor_calibration",
            "sample_count": len(calibration),
            "samples_sha256": hashlib.sha256(
                json.dumps(
                    calibration,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
        }
        for key in ("started_at", "ended_at"):
            if key in calibration_source:
                calibration_record[key] = _required_string(calibration_source, key)

    profile: dict[str, Any] = {
        "schema": PROFILE_SCHEMA,
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": profile_id.strip(),
        "mode": mode,
        "exploratory": bool(exploratory),
        "formal_metrics_eligible": not exploratory,
        "model_id": official_model.model_id,
        "model_version": official_model.version,
        "official_model_artifact_sha256": official_model.artifact_sha256,
        "site_id": context["site_id"],
        "datum": context["datum"],
        "official_context": {
            "context_id": context["context_id"],
            "timestamp": context["timestamp"],
            "site_id": context["site_id"],
            "datum": context["datum"],
            "source_split": context["source_split"],
            "source_row_sha256": context["source_row_sha256"],
            "dataset_id": context["dataset_id"],
            "dataset_version": context["dataset_version"],
            "dataset_registration_sha256": context["dataset_registration_sha256"],
            "features": context["features"],
        },
        "mapping": {
            "formula": (
                "proxy_relative_water_level_m = reference_level_m + "
                "gain_m_per_m * (water_rise_mm / 1000)"
            ),
            "gain_m_per_m": gain,
            "reference_level_m": base,
            "official_train_q05_m": official_low,
            "official_train_q95_m": official_high,
            "calibration_rise_q05_mm": calibration_low,
            "calibration_rise_q95_mm": calibration_high,
            "clipping": False,
        },
        "calibration_source": calibration_record,
        "data_contract": {
            "replaced_feature": "relative_water_level_m",
            "frozen_context_features": list(NON_WATER_FEATURE_ORDER),
            "sensor_rows_used_for_fit": 0,
            "sensor_rows_used_for_scaler": 0,
            "sensor_rows_used_for_threshold": 0,
            "model_or_threshold_update_allowed": False,
        },
        "created_at": _normalise_created_at(created_at),
    }
    profile["profile_sha256"] = _mapping_hash(profile)
    load_sensor_proxy_profile(profile, official_model=official_model)
    return profile


def load_sensor_proxy_profile(
    path_or_mapping: Path | str | Mapping[str, Any],
    *,
    official_model: LoadedOfficialModel,
) -> LoadedSensorProxyProfile:
    """Load a profile and bind it to one exact official artifact hash."""

    payload = _load_mapping(path_or_mapping)
    if payload.get("schema") != PROFILE_SCHEMA:
        raise SensorProxyError("unsupported sensor profile schema")
    if payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise SensorProxyError("unsupported sensor profile schema_version")
    supplied_hash = payload.pop("profile_sha256", None)
    if not isinstance(supplied_hash, str) or supplied_hash != _mapping_hash(payload):
        raise SensorProxyError("sensor profile sha256 is invalid")
    payload["profile_sha256"] = supplied_hash
    if payload.get("model_id") != official_model.model_id:
        raise SensorProxyError("sensor profile model_id does not match artifact")
    if payload.get("model_version") != official_model.version:
        raise SensorProxyError("sensor profile model_version does not match artifact")
    if payload.get("official_model_artifact_sha256") != official_model.artifact_sha256:
        raise SensorProxyError("sensor profile is pinned to a different artifact hash")

    profile_id = _required_string(payload, "profile_id")
    mode = _required_string(payload, "mode")
    exploratory = payload.get("exploratory")
    if not isinstance(exploratory, bool):
        raise SensorProxyError("sensor profile exploratory flag is invalid")
    if exploratory != (mode == "exploratory_manual_linear"):
        raise SensorProxyError("sensor profile mode/exploratory flags disagree")
    if payload.get("formal_metrics_eligible") is not (not exploratory):
        raise SensorProxyError("formal_metrics_eligible is invalid")

    context = payload.get("official_context")
    if not isinstance(context, Mapping):
        raise SensorProxyError("official_context is required")
    matched_context = _match_artifact_context(official_model, context)
    site_id = _required_string(payload, "site_id")
    datum = _required_string(payload, "datum")
    if site_id != matched_context["site_id"] or datum != matched_context["datum"]:
        raise SensorProxyError("profile site/datum does not match official context")

    mapping = payload.get("mapping")
    if not isinstance(mapping, Mapping) or mapping.get("clipping") is not False:
        raise SensorProxyError("sensor mapping is invalid or clipping was enabled")
    gain = _finite_number(mapping.get("gain_m_per_m"), "gain_m_per_m")
    base = _finite_number(mapping.get("reference_level_m"), "reference_level_m")
    if gain <= 0:
        raise SensorProxyError("gain_m_per_m must be greater than zero")
    if exploratory:
        official_low = official_high = calibration_low = calibration_high = None
        if payload.get("calibration_source") is not None:
            raise SensorProxyError(
                "exploratory profile cannot claim formal calibration"
            )
        calibration_session_id = None
        calibration_device_id = None
        calibration_sample_count = None
        calibration_samples_sha256 = None
    else:
        official_low = _finite_number(
            mapping.get("official_train_q05_m"), "official_train_q05_m"
        )
        official_high = _finite_number(
            mapping.get("official_train_q95_m"), "official_train_q95_m"
        )
        calibration_low = _finite_number(
            mapping.get("calibration_rise_q05_mm"), "calibration_rise_q05_mm"
        )
        calibration_high = _finite_number(
            mapping.get("calibration_rise_q95_mm"), "calibration_rise_q95_mm"
        )
        if official_high <= official_low or calibration_high <= calibration_low:
            raise SensorProxyError("formal profile quantile ranges are invalid")
        expected_gain = (official_high - official_low) / (
            (calibration_high - calibration_low) / 1000.0
        )
        expected_base = official_low - expected_gain * (calibration_low / 1000.0)
        if not math.isclose(gain, expected_gain, rel_tol=1e-12, abs_tol=1e-12):
            raise SensorProxyError(
                "formal profile gain was not derived from pinned ranges"
            )
        if not math.isclose(base, expected_base, rel_tol=1e-12, abs_tol=1e-12):
            raise SensorProxyError(
                "formal profile reference was not derived from pinned ranges"
            )
        reference = _mapping_reference(official_model, site_id)
        if official_low != float(
            reference["official_train_q05_m"]
        ) or official_high != float(reference["official_train_q95_m"]):
            raise SensorProxyError("formal profile official train range is not pinned")
        calibration_source = payload.get("calibration_source")
        if not isinstance(calibration_source, Mapping):
            raise SensorProxyError("formal profile calibration_source is required")
        calibration_session_id = _required_string(calibration_source, "session_id")
        calibration_device_id = _required_string(calibration_source, "device_id")
        if calibration_source.get("purpose") != "independent_sensor_calibration":
            raise SensorProxyError("formal profile calibration purpose is invalid")
        calibration_sample_count = calibration_source.get("sample_count")
        if (
            isinstance(calibration_sample_count, bool)
            or not isinstance(calibration_sample_count, int)
            or calibration_sample_count < 5
        ):
            raise SensorProxyError("formal profile calibration sample_count is invalid")
        calibration_samples_sha256 = calibration_source.get("samples_sha256")
        if (
            not isinstance(calibration_samples_sha256, str)
            or len(calibration_samples_sha256) != 64
        ):
            raise SensorProxyError("formal profile calibration sample hash is invalid")

    contract = payload.get("data_contract")
    if not isinstance(contract, Mapping):
        raise SensorProxyError("sensor profile data_contract is required")
    if contract.get("replaced_feature") != "relative_water_level_m":
        raise SensorProxyError("sensor profile may replace only water level")
    if contract.get("frozen_context_features") != list(NON_WATER_FEATURE_ORDER):
        raise SensorProxyError("sensor profile frozen context feature order is invalid")
    for key in (
        "sensor_rows_used_for_fit",
        "sensor_rows_used_for_scaler",
        "sensor_rows_used_for_threshold",
    ):
        if contract.get(key) != 0:
            raise SensorProxyError(
                "sensor rows must never enter fitting or calibration"
            )
    if contract.get("model_or_threshold_update_allowed") is not False:
        raise SensorProxyError("sensor profile must forbid model/threshold updates")
    created_at = _required_string(payload, "created_at")
    return LoadedSensorProxyProfile(
        profile_id=profile_id,
        mode=mode,
        exploratory=exploratory,
        model_id=official_model.model_id,
        model_version=official_model.version,
        official_model_artifact_sha256=official_model.artifact_sha256,
        site_id=site_id,
        datum=datum,
        context_id=matched_context["context_id"],
        context_timestamp=matched_context["timestamp"],
        context_source_split=matched_context["source_split"],
        context_source_row_sha256=matched_context["source_row_sha256"],
        context_features={
            name: float(matched_context["features"][name])
            for name in NON_WATER_FEATURE_ORDER
        },
        gain_m_per_m=gain,
        reference_level_m=base,
        official_train_q05_m=official_low,
        official_train_q95_m=official_high,
        calibration_rise_q05_mm=calibration_low,
        calibration_rise_q95_mm=calibration_high,
        calibration_session_id=calibration_session_id,
        calibration_device_id=calibration_device_id,
        calibration_sample_count=calibration_sample_count,
        calibration_samples_sha256=calibration_samples_sha256,
        created_at=created_at,
        profile_sha256=supplied_hash,
    )


def run_sensor_proxy_external_test(
    official_model: LoadedOfficialModel,
    profile: LoadedSensorProxyProfile | Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
) -> dict[str, Any]:
    """Run an isolated hardware-in-the-loop test with no model mutation."""

    if not isinstance(profile, LoadedSensorProxyProfile):
        profile = load_sensor_proxy_profile(profile, official_model=official_model)
    if profile.official_model_artifact_sha256 != official_model.artifact_sha256:
        raise SensorProxyError("profile/model artifact hash mismatch")
    if not isinstance(session_id, str) or not session_id.strip():
        raise SensorProxyError("session_id must be a non-empty string")
    if profile.calibration_session_id == session_id.strip():
        raise SensorProxyError(
            "external test session must be independent of calibration session"
        )
    if isinstance(samples, (str, bytes)) or not samples:
        raise SensorProxyError("external test requires at least one sensor sample")
    artifact_hash_before = official_model.artifact_sha256
    results: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            raise SensorProxyError(f"samples[{index}] must be an object")
        rise_mm = _finite_number(sample.get("water_rise_mm"), "water_rise_mm")
        proxy_level = profile.map_water_rise_mm(rise_mm)
        features = dict(profile.context_features)
        features["relative_water_level_m"] = proxy_level
        # This is the only model call.  No fit, transform update, threshold
        # selection, or artifact write is exposed by this module.
        prediction = official_model.predict_features(features)
        ood_features = [
            name
            for name in OFFICIAL_FEATURE_ORDER
            if features[name] < official_model.training_feature_ranges[name]["min"]
            or features[name] > official_model.training_feature_ranges[name]["max"]
        ]
        results.append(
            {
                "index": index,
                "captured_at": sample.get("captured_at"),
                "water_rise_mm": rise_mm,
                "proxy_relative_water_level_m": proxy_level,
                "extreme_water_probability": prediction["extreme_water_probability"],
                "predicted_label": prediction["predicted_label"],
                "out_of_distribution": bool(ood_features),
                "ood_features": ood_features,
            }
        )
    artifact_hash_after = official_model.artifact_sha256
    if artifact_hash_after != artifact_hash_before:
        raise SensorProxyError("official artifact changed during external test")
    probabilities = [row["extreme_water_probability"] for row in results]
    return {
        "schema": "coastwatch.sensor-proxy-external-test",
        "schema_version": 1,
        "session_id": session_id.strip(),
        "profile_id": profile.profile_id,
        "profile_sha256": profile.profile_sha256,
        "mode": profile.mode,
        "formal_metrics_eligible": not profile.exploratory,
        "model_id": official_model.model_id,
        "model_version": official_model.version,
        "official_model_artifact_sha256_before": artifact_hash_before,
        "official_model_artifact_sha256_after": artifact_hash_after,
        "model_artifact_unchanged": True,
        "site_id": profile.site_id,
        "datum": profile.datum,
        "context_id": profile.context_id,
        "mapping": {
            "gain_m_per_m": profile.gain_m_per_m,
            "reference_level_m": profile.reference_level_m,
            "clipping": False,
        },
        "sample_count": len(results),
        "predicted_extreme_count": sum(
            row["predicted_label"] == "extreme_water" for row in results
        ),
        "out_of_distribution_count": sum(
            bool(row["out_of_distribution"]) for row in results
        ),
        "mean_extreme_water_probability": sum(probabilities) / len(probabilities),
        "max_extreme_water_probability": max(probabilities),
        "reason_codes": ["SENSOR_PROXY_EXTERNAL_TEST", "LINEAR_GAIN_V1"],
        "official_frozen_test_metrics_modified": False,
        "rows": results,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _match_artifact_context(
    model: LoadedOfficialModel, supplied: Mapping[str, Any]
) -> Mapping[str, Any]:
    if not isinstance(supplied, Mapping):
        raise SensorProxyError("official_context must be an object")
    context_id = supplied.get("context_id")
    matches = [
        context
        for context in model.sensor_test_contexts
        if context["context_id"] == context_id
    ]
    if len(matches) != 1:
        raise SensorProxyError("official_context is not pinned in model artifact")
    expected = matches[0]
    for key in (
        "context_id",
        "timestamp",
        "site_id",
        "datum",
        "source_split",
        "source_row_sha256",
        "dataset_id",
        "dataset_version",
        "dataset_registration_sha256",
    ):
        if supplied.get(key) != expected.get(key):
            raise SensorProxyError(f"official_context {key} does not match artifact")
    features = supplied.get("features")
    if not isinstance(features, Mapping) or set(features) != set(
        NON_WATER_FEATURE_ORDER
    ):
        raise SensorProxyError("official_context features are incomplete")
    for name in NON_WATER_FEATURE_ORDER:
        if _finite_number(features[name], name) != float(expected["features"][name]):
            raise SensorProxyError(
                "official_context feature values do not match artifact"
            )
    return expected


def _mapping_reference(model: LoadedOfficialModel, site_id: str) -> Mapping[str, Any]:
    matches = [
        item for item in model.sensor_mapping_references if item["site_id"] == site_id
    ]
    if len(matches) != 1:
        raise SensorProxyError("model has no unique TRAIN mapping reference for site")
    return matches[0]


def _load_mapping(path_or_mapping: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(path_or_mapping, Mapping):
        value = path_or_mapping
    else:
        try:
            value = json.loads(Path(path_or_mapping).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SensorProxyError("sensor profile is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise SensorProxyError("sensor profile must be a JSON object")
    try:
        decoded = json.loads(canonical_json_bytes(value))
    except (TypeError, ValueError) as exc:
        raise SensorProxyError("sensor profile is not canonical JSON data") from exc
    if not isinstance(decoded, dict):
        raise SensorProxyError("sensor profile must be a JSON object")
    return decoded


def _mapping_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _linear_quantile(sorted_values: Sequence[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return float(
        sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction
    )


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SensorProxyError(f"{path} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SensorProxyError(f"{path} must be a finite number")
    return result


def _required_string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise SensorProxyError(f"{key} is required")
    return item.strip()


def _normalise_created_at(value: datetime | str | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise SensorProxyError("created_at must be ISO-8601") from exc
    else:
        raise SensorProxyError("created_at must be a datetime or ISO-8601 string")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


__all__ = [
    "PROFILE_SCHEMA",
    "LoadedSensorProxyProfile",
    "SensorProxyError",
    "build_sensor_proxy_profile",
    "load_sensor_proxy_profile",
    "run_sensor_proxy_external_test",
]
