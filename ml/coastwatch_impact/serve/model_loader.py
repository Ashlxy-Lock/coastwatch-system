"""Verified bundle loading, input validation, and degraded routing."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
import torch

from coastwatch_impact.evaluation.calibration import cumulative_event_probability
from coastwatch_impact.export.model_bundle import LoadedBundle, load_model_bundle

from .schemas import DataQuality, FeaturePredictionRequest, PredictionResponse, WaterLevelQuantile

LOGGER = logging.getLogger("coastwatch_impact.serve")


class InsufficientDataError(ValueError):
    """No scientifically defensible prediction path is available."""


class PhysicsFallback(Protocol):
    def __call__(self, request: FeaturePredictionRequest) -> dict[str, Any]: ...


def _finite_array(
    values: list[list[float | None]] | list[float | None],
    observed_mask: list[list[bool]] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    try:
        raw = np.asarray(values, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise InsufficientDataError(
            "feature values must form a rectangular numeric array"
        ) from error
    finite = np.isfinite(raw)
    observed = finite if observed_mask is None else np.asarray(observed_mask, dtype=bool) & finite
    clean = np.where(observed, raw, 0.0).astype(np.float32, copy=False)
    return clean, observed


def _scale(
    values: np.ndarray,
    observed: np.ndarray,
    preprocessing: dict[str, Any],
    arrays: dict[str, np.ndarray],
    prefix: str,
) -> np.ndarray:
    section = preprocessing.get(prefix)
    if isinstance(section, dict) and section.get("version") == "impactnet-train-preprocessor-v1":
        names = section.get("feature_names")
        if not isinstance(names, list) or len(names) != values.shape[-1]:
            raise ValueError(f"bundle {prefix} feature schema is inconsistent")
        try:
            median = np.asarray([section["medians"][name] for name in names], dtype=np.float32)
            lower = np.asarray([section["clip_lower"][name] for name in names], dtype=np.float32)
            upper = np.asarray([section["clip_upper"][name] for name in names], dtype=np.float32)
            mean = np.asarray([section["means"][name] for name in names], dtype=np.float32)
            scale = np.asarray([section["scales"][name] for name in names], dtype=np.float32)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"bundle {prefix} preprocessing statistics are invalid") from error
        if not all(np.isfinite(item).all() for item in (median, lower, upper, mean, scale)):
            raise ValueError(f"bundle {prefix} preprocessing statistics are non-finite")
        safe_scale = np.where(scale == 0, 1.0, scale)
        filled = np.where(observed, values, median)
        clipped = np.clip(filled, lower, upper)
        return ((clipped - mean) / safe_scale).astype(np.float32, copy=False)
    array_mean = arrays.get(f"{prefix}_mean")
    array_scale = arrays.get(f"{prefix}_scale")
    if array_mean is None or array_scale is None:
        return values
    if array_mean.shape[-1:] != values.shape[-1:] or array_scale.shape[-1:] != values.shape[-1:]:
        raise ValueError(f"bundle {prefix} preprocessing dimensions are inconsistent")
    safe_scale = np.where(array_scale == 0, 1.0, array_scale)
    transformed = (values - array_mean) / safe_scale
    return np.where(observed, transformed, 0.0).astype(np.float32, copy=False)


def _research_band(probability_24h: float, thresholds: dict[str, Any]) -> str:
    bands = thresholds.get("research_bands")
    if not isinstance(bands, dict):
        return "unconfigured"
    ordered = [
        ("critical", bands.get("critical")),
        ("warning", bands.get("warning")),
        ("advisory", bands.get("advisory")),
    ]
    for name, value in ordered:
        if isinstance(value, (int, float)) and probability_24h >= float(value):
            return name
    return "safe"


def _expected_feature_manifest_hash(bundle: LoadedBundle) -> str | None:
    declared = {
        name: bundle.feature_schema[name]
        for name in ("feature_manifest_hash", "dataset_manifest_hash")
        if bundle.feature_schema.get(name) is not None
    }
    if not declared:
        return None
    normalised: dict[str, str] = {}
    for name, value in declared.items():
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
            raise ValueError(f"bundle {name} must be a SHA-256 hex digest")
        normalised[name] = value.lower()
    if len(set(normalised.values())) != 1:
        raise ValueError("bundle feature and dataset manifest hashes conflict")
    return next(iter(normalised.values()))


def _validate_feature_manifest_hash(
    bundle: LoadedBundle,
    request: FeaturePredictionRequest,
) -> None:
    expected = _expected_feature_manifest_hash(bundle)
    supplied = request.feature_manifest_hash
    if expected is None:
        if supplied is not None:
            raise InsufficientDataError(
                "caller supplied feature_manifest_hash but the bundle declares no expected hash"
            )
        return
    if supplied is None:
        raise InsufficientDataError("feature_manifest_hash is required by this model bundle")
    if not hmac.compare_digest(supplied.lower(), expected):
        raise InsufficientDataError("feature_manifest_hash does not match the model bundle")


def _effective_source_issue_times(request: FeaturePredictionRequest) -> dict[str, datetime]:
    """Merge legacy observation ages with conservative issued-forecast ages."""

    issue_times = dict(request.source_issue_times)
    for provenance in request.issued_forecast_provenance:
        previous = issue_times.get(provenance.source_model)
        if previous is None or provenance.issue_time_utc < previous:
            issue_times[provenance.source_model] = provenance.issue_time_utc
    return issue_times


def _required_future_sources(bundle: LoadedBundle) -> set[str]:
    """Validate and return the immutable feature-column/source binding."""

    if bundle.architecture.variant != "hybrid_tcn":
        return set()
    names = bundle.feature_schema.get("future_feature_names")
    mapping = bundle.feature_schema.get("future_feature_sources")
    if (
        not isinstance(names, list)
        or len(names) != bundle.architecture.forecast_feature_dim
        or not isinstance(mapping, dict)
        or set(mapping) != set(names)
        or any(not isinstance(value, str) or not value for value in mapping.values())
    ):
        raise ValueError("hybrid bundle must bind every future feature column to a source_model")
    return {str(value) for value in mapping.values()}


def _validate_required_forecast_provenance(
    bundle: LoadedBundle,
    request: FeaturePredictionRequest,
) -> None:
    expected_times = {
        request.prediction_time_utc + timedelta(hours=lead)
        for lead in range(1, bundle.architecture.forecast_hours + 1)
    }
    for source in sorted(_required_future_sources(bundle)):
        supplied = {
            row.valid_time_utc
            for row in request.issued_forecast_provenance
            if row.source_model == source
        }
        if supplied != expected_times:
            raise InsufficientDataError(
                f"issued forecast provenance for source {source!r} must cover every future lead"
            )


class BundlePredictor:
    """Primary model plus optional independently trained degraded paths."""

    def __init__(
        self,
        primary: str | Path | LoadedBundle,
        *,
        obs_only: str | Path | LoadedBundle | None = None,
        physics_fallback: PhysicsFallback | None = None,
        device: str = "cpu",
    ) -> None:
        self.primary = (
            primary
            if isinstance(primary, LoadedBundle)
            else load_model_bundle(primary, device=device)
        )
        self.obs_only = (
            obs_only
            if isinstance(obs_only, LoadedBundle) or obs_only is None
            else load_model_bundle(obs_only, device=device)
        )
        self.physics_fallback = physics_fallback
        self.device = device
        if self.primary.manifest.get("shadow_mode") is not True:
            raise ValueError("primary model is not a shadow bundle")
        if self.obs_only is not None and self.obs_only.architecture.variant != "obs_only_tcn":
            raise ValueError("degraded neural bundle must be obs_only_tcn")
        # Fail at service initialisation, before any request can reach a bundle
        # whose immutable feature identity is malformed or contradictory.
        _expected_feature_manifest_hash(self.primary)
        _required_future_sources(self.primary)
        if self.obs_only is not None:
            _expected_feature_manifest_hash(self.obs_only)

    @property
    def loaded_versions(self) -> list[str]:
        versions = [str(self.primary.manifest["model_version"])]
        if self.obs_only is not None:
            versions.append(str(self.obs_only.manifest["model_version"]))
        return versions

    def model_info(self) -> dict[str, Any]:
        return dict(self.primary.manifest)

    @staticmethod
    def _log_prediction(
        response: PredictionResponse,
        request: FeaturePredictionRequest,
        *,
        raw_logits: list[float] | None,
        started: float,
    ) -> None:
        """Write the minimum auditable inference record without feature values or secrets."""

        LOGGER.info(
            "shadow_prediction",
            extra={
                "request_id": response.request_id,
                "model_version": response.model_version,
                "model_variant": response.model_variant,
                "site_id": response.site_id,
                "prediction_time_utc": response.prediction_time_utc.isoformat(),
                "feature_manifest_hash": request.feature_manifest_hash,
                "source_issue_times": {
                    name: value.isoformat() for name, value in response.source_issue_times.items()
                },
                "issued_forecast_provenance": [
                    value.model_dump(mode="json") for value in response.issued_forecast_provenance
                ],
                "data_quality": response.data_quality.model_dump(mode="json"),
                "raw_logits": raw_logits,
                "calibrated_probabilities": response.event_probability,
                "research_band": response.research_band,
                "calibrated": response.calibrated,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "shadow_mode": True,
            },
        )

    def _validate_site(self, bundle: LoadedBundle, site_id: str) -> bool:
        sites = bundle.sites
        if not sites:
            return True
        records = sites.values() if isinstance(sites, dict) else sites
        ids = {
            str(record.get("site_id"))
            for record in records
            if isinstance(record, dict) and record.get("site_id") is not None
        }
        return bool(ids) and site_id not in ids

    def _run_bundle(
        self,
        bundle: LoadedBundle,
        request: FeaturePredictionRequest,
        *,
        status: Literal["normal", "degraded_obs_only"],
        request_id: str | None,
        started: float,
    ) -> PredictionResponse:
        config = bundle.architecture
        _validate_feature_manifest_hash(bundle, request)
        past, past_observed = _finite_array(request.past_values, request.past_mask)
        expected_past = (config.history_hours, config.past_feature_dim)
        if past.shape != expected_past:
            raise InsufficientDataError(f"past_values must have shape {expected_past}")
        missing_past = 1.0 - float(past_observed.mean())

        maximum_past_missing = float(bundle.feature_schema.get("max_missing_fraction_past", 0.25))
        if missing_past > maximum_past_missing:
            raise InsufficientDataError(
                f"past missing fraction {missing_past:.3f} exceeds {maximum_past_missing:.3f}"
            )
        past = _scale(
            past,
            past_observed,
            bundle.preprocessing,
            bundle.preprocessing_arrays,
            "past",
        )

        static, static_observed = _finite_array(request.static_values)
        if static.shape != (config.static_feature_dim,):
            raise InsufficientDataError(
                f"static_values must have shape ({config.static_feature_dim},)"
            )
        static = _scale(
            static,
            static_observed,
            bundle.preprocessing,
            bundle.preprocessing_arrays,
            "static",
        )

        future: np.ndarray | None = None
        future_observed: np.ndarray | None = None
        missing_future = 0.0
        if config.variant == "hybrid_tcn":
            if request.future_values is None or request.future_mask is None:
                raise InsufficientDataError("hybrid forecast features are absent")
            if not request.issued_forecast_provenance:
                raise InsufficientDataError(
                    "hybrid issued forecast provenance is absent; each future lead requires "
                    "source_model, model_run_id, issue_time_utc, and valid_time_utc"
                )
            _validate_required_forecast_provenance(bundle, request)
            expected_valid_times = {
                request.prediction_time_utc + timedelta(hours=lead)
                for lead in range(1, config.forecast_hours + 1)
            }
            supplied_valid_times = {
                row.valid_time_utc for row in request.issued_forecast_provenance
            }
            if supplied_valid_times != expected_valid_times:
                raise InsufficientDataError(
                    "hybrid issued forecast provenance does not cover every configured lead"
                )
            stale_limits = bundle.feature_schema.get("max_source_age_hours", {})
            stale_forecasts = (
                sorted(
                    f"{row.source_model}:{row.model_run_id}@{row.valid_time_utc.isoformat()}"
                    for row in request.issued_forecast_provenance
                    if row.source_model in stale_limits
                    and (request.prediction_time_utc - row.issue_time_utc).total_seconds() / 3600
                    > float(stale_limits[row.source_model])
                )
                if isinstance(stale_limits, dict)
                else []
            )
            if stale_forecasts:
                raise InsufficientDataError(
                    "hybrid forecast sources are stale: " + ", ".join(stale_forecasts)
                )
            future, future_observed = _finite_array(request.future_values, request.future_mask)
            expected_future = (config.forecast_hours, config.forecast_feature_dim)
            if future.shape != expected_future:
                raise InsufficientDataError(f"future_values must have shape {expected_future}")
            missing_future = 1.0 - float(future_observed.mean())
            maximum_future_missing = float(
                bundle.feature_schema.get("max_missing_fraction_future", 0.25)
            )
            if missing_future > maximum_future_missing:
                raise InsufficientDataError(
                    f"future missing fraction {missing_future:.3f} exceeds "
                    f"{maximum_future_missing:.3f}"
                )
            future = _scale(
                future,
                future_observed,
                bundle.preprocessing,
                bundle.preprocessing_arrays,
                "future",
            )

        time_features: np.ndarray | None = None
        if config.time_feature_dim:
            if request.future_time_features is None:
                raise InsufficientDataError("future_time_features are absent")
            time_features = np.asarray(request.future_time_features, dtype=np.float32)
            expected_time = (config.forecast_hours, config.time_feature_dim)
            if time_features.shape != expected_time or not np.isfinite(time_features).all():
                raise InsufficientDataError(
                    f"future_time_features must be finite with shape {expected_time}"
                )

        physics: np.ndarray | None = None
        if config.water_target_mode == "residual":
            if request.physics_baseline is None:
                raise InsufficientDataError(
                    "physics_baseline is required for residual water output"
                )
            physics = np.asarray(request.physics_baseline, dtype=np.float32)
            if physics.shape != (config.forecast_hours,) or not np.isfinite(physics).all():
                raise InsufficientDataError(
                    f"physics_baseline must be finite with shape ({config.forecast_hours},)"
                )

        tensor = lambda value: torch.from_numpy(value).unsqueeze(0).to(self.device)  # noqa: E731
        kwargs: dict[str, Any] = {
            "past_observations": tensor(past),
            "past_missing_mask": tensor(~past_observed).bool(),
        }
        if config.static_feature_dim:
            kwargs["static_features"] = tensor(static)
        if future is not None and future_observed is not None:
            kwargs["future_forecasts"] = tensor(future)
            kwargs["future_missing_mask"] = tensor(~future_observed).bool()
        if time_features is not None:
            kwargs["future_time_features"] = tensor(time_features)
        if physics is not None:
            kwargs["physics_baseline"] = tensor(physics)

        with torch.inference_mode():
            raw = bundle.model(**kwargs)
        logits = raw["hazard_logits"].detach().cpu().numpy()[0]
        temperature = float(bundle.calibration.get("temperature", 1.0))
        if not math.isfinite(temperature) or temperature <= 0:
            raise ValueError("bundle calibration temperature is invalid")
        cumulative = cumulative_event_probability(logits / temperature)
        water = raw["water_quantiles"].detach().cpu().numpy()[0]
        horizons = [int(value) for value in bundle.manifest["horizons_hours"]]
        event_probability = {f"{lead}h": float(cumulative[lead - 1]) for lead in horizons}
        quantiles = [
            WaterLevelQuantile(
                valid_time_utc=request.prediction_time_utc + timedelta(hours=lead),
                p10=float(values[0]),
                p50=float(values[1]),
                p90=float(values[2]),
            )
            for lead, values in enumerate(water, 1)
        ]

        effective_issue_times = _effective_source_issue_times(request)
        stale_limits = bundle.feature_schema.get("max_source_age_hours", {})
        stale_sources = sorted(
            source
            for source, issue_time in effective_issue_times.items()
            if isinstance(stale_limits, dict)
            and source in stale_limits
            and (request.prediction_time_utc - issue_time).total_seconds() / 3600
            > float(stale_limits[source])
        )
        missing_fraction = max(missing_past, missing_future)
        request_payload_hash = hashlib.sha256(
            json.dumps(request.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        calibrated = (
            bundle.calibration.get("fitted_split") == "validation"
            and bundle.calibration.get("calibrated") is True
            and bundle.calibration.get("method") != "identity"
        )
        out_of_domain = self._validate_site(bundle, request.site_id)
        response = PredictionResponse(
            request_id=request_id or f"{uuid.uuid4()}-{request_payload_hash}",
            model_name=str(bundle.manifest["model_name"]),
            model_version=str(bundle.manifest["model_version"]),
            model_variant=config.variant,
            label_mode=str(bundle.manifest["label_mode"]),
            prediction_time_utc=request.prediction_time_utc,
            site_id=request.site_id,
            coverage_scope=str(bundle.manifest["coverage_scope"]),
            event_probability=event_probability,
            water_level_quantiles_m_aod=quantiles,
            research_band=_research_band(float(cumulative[-1]), bundle.thresholds),
            data_quality=DataQuality(
                status="out_of_domain" if out_of_domain else status,
                missing_fraction=missing_fraction,
                stale_sources=stale_sources,
                out_of_domain=out_of_domain,
            ),
            source_issue_times=effective_issue_times,
            issued_forecast_provenance=request.issued_forecast_provenance,
            calibrated=calibrated,
            shadow_mode=True,
            synthetic_data=bool(bundle.manifest.get("synthetic_data", False)),
        )
        self._log_prediction(
            response,
            request,
            raw_logits=[float(value) for value in logits],
            started=started,
        )
        return response

    def _physics_response(
        self,
        request: FeaturePredictionRequest,
        *,
        request_id: str | None,
        started: float,
    ) -> PredictionResponse:
        if self.physics_fallback is None:
            raise InsufficientDataError("no physics fallback is configured")
        output = self.physics_fallback(request)
        probabilities = np.asarray(output["cumulative_event_probability"], dtype=float)
        water = np.asarray(output["water_quantiles"], dtype=float)
        if probabilities.shape != (24,) or water.shape != (24, 3):
            raise InsufficientDataError("physics fallback returned invalid shapes")
        if (
            not np.isfinite(probabilities).all()
            or np.any((probabilities < 0) | (probabilities > 1))
            or np.any(np.diff(probabilities) < -1e-12)
            or not np.isfinite(water).all()
            or np.any(water[:, 0] > water[:, 1])
            or np.any(water[:, 1] > water[:, 2])
        ):
            raise InsufficientDataError("physics fallback returned invalid values")
        horizons = [1, 3, 6, 12, 24]
        out_of_domain = self._validate_site(self.primary, request.site_id)
        response = PredictionResponse(
            request_id=request_id or str(uuid.uuid4()),
            model_name="CoastWatch Physics Baseline",
            model_version=str(output.get("model_version", "physics-local")),
            model_variant="physics_baseline",
            label_mode=str(self.primary.manifest["label_mode"]),
            prediction_time_utc=request.prediction_time_utc,
            site_id=request.site_id,
            coverage_scope=str(self.primary.manifest["coverage_scope"]),
            event_probability={f"{lead}h": float(probabilities[lead - 1]) for lead in horizons},
            water_level_quantiles_m_aod=[
                WaterLevelQuantile(
                    valid_time_utc=request.prediction_time_utc + timedelta(hours=lead),
                    p10=float(values[0]),
                    p50=float(values[1]),
                    p90=float(values[2]),
                )
                for lead, values in enumerate(water, 1)
            ],
            research_band="unconfigured",
            data_quality=DataQuality(
                status=("out_of_domain" if out_of_domain else "degraded_physics_only"),
                missing_fraction=1.0,
                stale_sources=[],
                out_of_domain=out_of_domain,
            ),
            source_issue_times=_effective_source_issue_times(request),
            issued_forecast_provenance=request.issued_forecast_provenance,
            calibrated=bool(output.get("calibrated", False)),
            shadow_mode=True,
            synthetic_data=bool(self.primary.manifest.get("synthetic_data", False)),
        )
        self._log_prediction(response, request, raw_logits=None, started=started)
        return response

    def predict(
        self,
        request: FeaturePredictionRequest,
        *,
        request_id: str | None = None,
    ) -> PredictionResponse:
        """Route hybrid -> independently trained obs-only -> physics -> no probability."""

        started = time.perf_counter()
        try:
            return self._run_bundle(
                self.primary,
                request,
                status="normal",
                request_id=request_id,
                started=started,
            )
        except InsufficientDataError as primary_error:
            if self.obs_only is not None:
                try:
                    return self._run_bundle(
                        self.obs_only,
                        request,
                        status="degraded_obs_only",
                        request_id=request_id,
                        started=started,
                    )
                except InsufficientDataError:
                    pass
            if self.physics_fallback is not None:
                return self._physics_response(
                    request,
                    request_id=request_id,
                    started=started,
                )
            raise InsufficientDataError(str(primary_error)) from primary_error


__all__ = ["BundlePredictor", "InsufficientDataError", "PhysicsFallback"]
