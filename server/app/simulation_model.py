"""Leakage-safe training and pure-Python inference for simulated water levels.

The model in this module is intentionally scoped to user-run tabletop water
level simulations.  Labels are supplied by an operator after collection, and
the exported artifact is always marked ``simulation`` and ``shadow``.  Training
uses scikit-learn, while loading and inference use only the Python standard
library so the API process does not need to unpickle executable model objects.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARTIFACT_SCHEMA = "coastwatch.simulation-water-logreg"
ARTIFACT_SCHEMA_VERSION = 2
DEFAULT_MODEL_ID = "custom-water-logreg-v1"
CLASS_ORDER: tuple[str, ...] = ("safe", "danger")
ULTRASONIC_FEATURE_ORDER: tuple[str, ...] = (
    "distance_mm_current",
    "baseline_mm",
    "water_rise_mm_current",
    "rise_rate_mm_s_current",
    "water_rise_delta_mm",
    "water_rise_slope_mm_s",
    "water_rise_rolling_mean_mm",
    "water_rise_rolling_std_mm",
)
SIMULATED_ENVIRONMENT_FEATURE_ORDER: tuple[str, ...] = (
    "sim_air_temperature_c",
    "sim_humidity_percent",
    "sim_wind_speed_kmh",
    "sim_wave_height_m",
    "sim_wave_period_s",
    "sim_water_temperature_c",
    "sim_sea_level_height_m",
    "sim_ocean_current_velocity_kmh",
    "sim_hour_sin",
    "sim_hour_cos",
    "sim_day_of_year_sin",
    "sim_day_of_year_cos",
    "sim_latitude",
    "sim_longitude",
)
RAW_SIMULATED_SCENARIO_FIELDS: tuple[str, ...] = (
    "sim_air_temperature_c",
    "sim_humidity_percent",
    "sim_wind_speed_kmh",
    "sim_wave_height_m",
    "sim_wave_period_s",
    "sim_water_temperature_c",
    "sim_sea_level_height_m",
    "sim_ocean_current_velocity_kmh",
    "sim_latitude",
    "sim_longitude",
)
FEATURE_ORDER: tuple[str, ...] = (
    *ULTRASONIC_FEATURE_ORDER,
    *SIMULATED_ENVIRONMENT_FEATURE_ORDER,
)
UNKNOWN_LABELS = {"", "unknown", "transition", "unlabelled", "unlabeled", "skip"}
# Internal-only marker supplied by the dataset builder.  It resets rolling
# feature state after an invalid sensor row without changing the original
# session_id used by the leakage-safe group split.
WINDOW_EPOCH_FIELD = "_window_epoch"
SIMULATION_DATA_WARNING = (
    "Operator-supplied tabletop simulation only; not real coastal observations "
    "and not suitable for public-safety decisions."
)


class SimulationModelError(ValueError):
    """Raised when simulation data or an artifact fails a safety contract."""


@dataclass(frozen=True)
class _Measurement:
    distance_mm: float
    baseline_mm: float
    water_rise_mm: float
    rise_rate_mm_s: float
    time_seconds: float


@dataclass(frozen=True)
class LoadedSimulationModel:
    """Validated binary logistic model suitable for standard-library inference."""

    model_id: str
    version: str
    feature_order: tuple[str, ...]
    window_size: int
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    coefs: tuple[float, ...]
    intercept: float
    metrics: Mapping[str, Any]
    created_at: str
    artifact_hash: str
    data_kind: str = "simulation"
    data_origin: str = "operator_supplied_simulation"
    deployment_mode: str = "shadow"

    def predict(
        self,
        samples: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        window_size: int | None = None,
    ) -> dict[str, Any]:
        """Predict the latest sample in a session without importing sklearn."""

        requested_window = self.window_size if window_size is None else window_size
        if requested_window != self.window_size:
            raise SimulationModelError(
                f"artifact requires window_size={self.window_size}, got {requested_window}"
            )
        features, quality = _latest_feature_vector(samples, requested_window)
        standardized = [
            (value - self.scaler_mean[index]) / self.scaler_scale[index]
            for index, value in enumerate(features)
        ]
        logit = self.intercept + sum(
            coefficient * value
            for coefficient, value in zip(self.coefs, standardized, strict=True)
        )
        danger_probability = _sigmoid(logit)
        safe_probability = 1.0 - danger_probability
        predicted_label = "danger" if danger_probability >= 0.5 else "safe"
        return {
            "model_id": self.model_id,
            "version": self.version,
            "data_kind": self.data_kind,
            "data_origin": self.data_origin,
            "deployment_mode": self.deployment_mode,
            "warning": SIMULATION_DATA_WARNING,
            "predicted_label": predicted_label,
            "safe_probability": safe_probability,
            "danger_probability": danger_probability,
            "probabilities": {
                "safe": safe_probability,
                "danger": danger_probability,
            },
            "quality": quality["status"],
            "quality_details": quality,
            "window_size": self.window_size,
            "feature_order": list(self.feature_order),
            "feature_vector": features,
            "features": dict(zip(self.feature_order, features, strict=True)),
        }


def build_simulation_features(
    samples: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    window_size: int = 5,
) -> dict[str, float]:
    """Build the latest auditable rolling-window feature mapping for a session."""

    features, _ = _latest_feature_vector(samples, window_size)
    return dict(zip(FEATURE_ORDER, features, strict=True))


def assess_simulation_training_data(
    rows_or_sessions: Any,
    *,
    window_size: int = 5,
    test_fraction: float = 0.25,
    random_state: int = 42,
) -> dict[str, Any]:
    """Assess split feasibility without fitting a model or importing sklearn."""

    blockers: list[str] = []
    try:
        _validate_training_options(
            DEFAULT_MODEL_ID, "readiness", window_size, test_fraction
        )
        sessions = _normalise_sessions(rows_or_sessions, minimum_sessions=1)
    except SimulationModelError as exc:
        return {"ready": False, "blockers": [str(exc)], "planned_split": None}

    try:
        scenario_groups = _scenario_group_ids(sessions)
        labelled, _unknown_count, manifest = _prepare_training_rows(
            sessions, window_size=window_size, source_context=None
        )
    except SimulationModelError as exc:
        return {"ready": False, "blockers": [str(exc)], "planned_split": None}
    if not labelled:
        blockers.append("no labelled simulation samples were supplied")
    elif {label for _, _, label in labelled} != {0, 1}:
        blockers.append("training requires both safe and danger labelled samples")

    planned_split: dict[str, Any] | None = None
    if not blockers:
        try:
            train_sessions, test_sessions, split_strategy = _group_holdout(
                labelled,
                test_fraction=test_fraction,
                random_state=random_state,
                session_groups=scenario_groups,
            )
        except SimulationModelError as exc:
            blockers.append(str(exc))
        else:
            train_scenario_groups = {
                scenario_groups[session_id] for session_id in train_sessions
            }
            test_scenario_groups = {
                scenario_groups[session_id] for session_id in test_sessions
            }
            scenario_group_overlap = sorted(
                train_scenario_groups & test_scenario_groups
            )
            scenario_generalization_evaluable = (
                split_strategy
                == "whole_session_and_simulated_environment_group_holdout"
            )
            train_scenario_group_count = len(train_scenario_groups)
            test_scenario_group_count = len(test_scenario_groups)
            environment_effects_learnable = train_scenario_group_count >= 2
            planned_split = {
                "strategy": split_strategy,
                "secondary_group": (
                    "simulated_environment_feature_hash"
                    if scenario_generalization_evaluable
                    else None
                ),
                "random_state": random_state,
                "test_fraction_requested": float(test_fraction),
                "train_sessions": train_sessions,
                "test_sessions": test_sessions,
                "session_overlap": [],
                "scenario_group_overlap": scenario_group_overlap,
                "scenario_group_overlap_expected": (
                    not scenario_generalization_evaluable
                ),
                "scenario_generalization_evaluable": (
                    scenario_generalization_evaluable
                ),
                "train_scenario_group_count": train_scenario_group_count,
                "test_scenario_group_count": test_scenario_group_count,
                "environment_effects_learnable": environment_effects_learnable,
            }
    return {
        "ready": not blockers,
        "blockers": blockers,
        "planned_split": planned_split,
        "labelled_sample_count": len(labelled),
        "class_counts": _class_counts(label for _, _, label in labelled),
        "excluded_unknown_samples": manifest["excluded_unknown_samples"],
        "excluded_warmup_samples": manifest["excluded_warmup_samples"],
        "session_label_counts": {
            session["session_id"]: session["label_counts"]
            for session in manifest["sessions"]
        },
    }


def train_simulation_model(
    rows_or_sessions: Any,
    *,
    output_path: Path | str | None = None,
    model_id: str = DEFAULT_MODEL_ID,
    version: str = "1",
    window_size: int = 5,
    test_fraction: float = 0.25,
    random_state: int = 42,
    created_at: datetime | str | None = None,
    source_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Train a binary model with a whole-session holdout and export pure JSON.

    Accepted inputs are flat rows containing ``session_id``; session objects of
    the form ``{"session_id": ..., "samples": [...]}``; or a mapping from
    session id to sample rows.  ``unknown`` and ``transition`` labels provide
    rolling context but are never silently treated as safe targets.
    """

    _validate_training_options(model_id, version, window_size, test_fraction)
    sessions = _normalise_sessions(rows_or_sessions)
    scenario_groups = _scenario_group_ids(sessions)
    labelled, unknown_count, manifest = _prepare_training_rows(
        sessions, window_size=window_size, source_context=source_context
    )
    manifest.update(
        {
            "data_origin": "operator_supplied_simulation",
            "scenario_schema": "coastwatch.operator-simulated-coast",
            "scenario_schema_version": 1,
            "feature_count": len(FEATURE_ORDER),
            "feature_order": list(FEATURE_ORDER),
            "simulated_environment_feature_groups": {
                session_id: scenario_groups[session_id]
                for session_id in sorted(scenario_groups)
            },
            "simulated_environment_feature_group_count": len(
                set(scenario_groups.values())
            ),
            "warning": SIMULATION_DATA_WARNING,
        }
    )

    if not labelled:
        raise SimulationModelError("no labelled simulation samples were supplied")
    observed_classes = {label for _, _, label in labelled}
    if observed_classes != {0, 1}:
        raise SimulationModelError(
            "training requires both safe and danger labelled samples"
        )

    train_sessions, test_sessions, split_strategy = _group_holdout(
        labelled,
        test_fraction=test_fraction,
        random_state=random_state,
        session_groups=scenario_groups,
    )
    scenario_generalization_evaluable = (
        split_strategy == "whole_session_and_simulated_environment_group_holdout"
    )
    train_scenario_group_count = len(
        {scenario_groups[session_id] for session_id in train_sessions}
    )
    test_scenario_group_count = len(
        {scenario_groups[session_id] for session_id in test_sessions}
    )
    environment_effects_learnable = train_scenario_group_count >= 2
    manifest.update(
        {
            "split_strategy": split_strategy,
            "scenario_generalization_evaluable": scenario_generalization_evaluable,
            "train_scenario_group_count": train_scenario_group_count,
            "test_scenario_group_count": test_scenario_group_count,
            "environment_effects_learnable": environment_effects_learnable,
        }
    )
    train_session_set = set(train_sessions)
    test_session_set = set(test_sessions)
    train_rows = [row for row in labelled if row[0] in train_session_set]
    test_rows = [row for row in labelled if row[0] in test_session_set]
    if not train_rows or not test_rows:
        raise SimulationModelError("session holdout produced an empty split")
    if {label for _, _, label in train_rows} != {0, 1}:
        raise SimulationModelError(
            "no leakage-safe session split leaves both classes in training"
        )

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:  # pragma: no cover - exercised by minimal deployment
        raise RuntimeError(
            "training requires numpy and scikit-learn; inference does not"
        ) from exc

    train_y: Any = np.asarray([label for _, _, label in train_rows], dtype=int)
    test_y: Any = np.asarray([label for _, _, label in test_rows], dtype=int)
    session_sample_counts = {
        session_id: sum(row[0] == session_id for row in train_rows)
        for session_id in train_sessions
    }
    train_sample_weights: Any = np.asarray(
        [1.0 / session_sample_counts[session_id] for session_id, _, _ in train_rows],
        dtype=float,
    )

    def fit_variant(feature_indices: Sequence[int]) -> dict[str, Any]:
        train_x: Any = np.asarray(
            [
                [features[index] for index in feature_indices]
                for _, features, _ in train_rows
            ],
            dtype=float,
        )
        test_x: Any = np.asarray(
            [
                [features[index] for index in feature_indices]
                for _, features, _ in test_rows
            ],
            dtype=float,
        )
        variant_scaler = StandardScaler()
        scaled_train_x = variant_scaler.fit_transform(train_x)
        variant_classifier = LogisticRegression(
            l1_ratio=0.0,
            C=1.0,
            fit_intercept=True,
            class_weight="balanced",
            max_iter=2_000,
            random_state=random_state,
            solver="lbfgs",
            tol=1e-10,
        )
        variant_classifier.fit(
            scaled_train_x,
            train_y,
            sample_weight=train_sample_weights,
        )
        if variant_classifier.classes_.tolist() != [0, 1]:
            raise SimulationModelError(
                "unexpected class order from logistic regression"
            )
        train_probabilities = variant_classifier.predict_proba(scaled_train_x)[:, 1]
        test_probabilities = variant_classifier.predict_proba(
            variant_scaler.transform(test_x)
        )[:, 1]
        return {
            "scaler": variant_scaler,
            "classifier": variant_classifier,
            "train_probabilities": train_probabilities.tolist(),
            "train_predictions": (train_probabilities >= 0.5).astype(int).tolist(),
            "test_probabilities": test_probabilities.tolist(),
            "test_predictions": (test_probabilities >= 0.5).astype(int).tolist(),
        }

    full_fit = fit_variant(range(len(FEATURE_ORDER)))
    ultrasonic_fit = fit_variant(range(len(ULTRASONIC_FEATURE_ORDER)))
    environment_fit = fit_variant(
        range(len(ULTRASONIC_FEATURE_ORDER), len(FEATURE_ORDER))
    )
    scaler = full_fit["scaler"]
    classifier = full_fit["classifier"]

    baseline_threshold = _fit_water_rise_threshold(train_rows)
    baseline_train_predictions = _threshold_predictions(train_rows, baseline_threshold)
    baseline_test_predictions = _threshold_predictions(test_rows, baseline_threshold)

    metrics = _build_metrics(
        labelled=labelled,
        train_rows=train_rows,
        test_rows=test_rows,
        train_sessions=train_sessions,
        test_sessions=test_sessions,
        test_y=test_y.tolist(),
        test_predictions=full_fit["test_predictions"],
        danger_probabilities=full_fit["test_probabilities"],
        baseline_threshold=baseline_threshold,
        baseline_train_predictions=baseline_train_predictions,
        baseline_test_predictions=baseline_test_predictions,
        ablation_results={
            "ultrasonic_only_logistic_regression": {
                "feature_order": list(ULTRASONIC_FEATURE_ORDER),
                **ultrasonic_fit,
            },
            "environment_only_logistic_regression": {
                "feature_order": list(SIMULATED_ENVIRONMENT_FEATURE_ORDER),
                **environment_fit,
            },
        },
        scenario_groups=scenario_groups,
        unknown_count=unknown_count,
        excluded_warmup_samples=int(manifest["excluded_warmup_samples"]),
        window_size=window_size,
        test_fraction=test_fraction,
        random_state=random_state,
        split_strategy=split_strategy,
    )
    training_config = {
        "window_size": window_size,
        "test_fraction": float(test_fraction),
        "random_state": random_state,
        "decision_threshold": 0.5,
        "penalty": "l2",
        "l1_ratio": 0.0,
        "regularization_C": 1.0,
        "fit_intercept": True,
        "class_weight": "balanced",
        "sample_weight_strategy": "equal_total_base_weight_per_training_session",
        "solver": "lbfgs",
        "max_iter": 2_000,
        "tolerance": 1e-10,
        "split_strategy": split_strategy,
        "scenario_generalization_evaluable": scenario_generalization_evaluable,
        "train_scenario_group_count": train_scenario_group_count,
        "test_scenario_group_count": test_scenario_group_count,
        "environment_effects_learnable": environment_effects_learnable,
        "scientific_warnings": [
            "Environmental inputs are operator supplied and are not observations.",
            (
                "distance, baseline distance, and derived water rise are collinear; "
                "individual coefficients are not independently identifiable."
            ),
            (
                "Repeated 0.5-second samples are autocorrelated; session-macro "
                "metrics must be considered alongside sample-weighted metrics."
            ),
            *(
                [
                    (
                        "Only one simulated scenario is represented; cross-scenario "
                        "generalization cannot be evaluated."
                    )
                ]
                if not scenario_generalization_evaluable
                else []
            ),
            *(
                [
                    (
                        "The training split has fewer than two scenario groups; "
                        "environment coefficients are not interpretable."
                    )
                ]
                if not environment_effects_learnable
                else []
            ),
        ],
    }
    payload: dict[str, Any] = {
        "schema": ARTIFACT_SCHEMA,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": model_id.strip(),
        "version": version.strip(),
        "model_type": "binary_logistic_regression",
        "class_order": list(CLASS_ORDER),
        "feature_order": list(FEATURE_ORDER),
        "window_size": window_size,
        "scaler": {
            "mean": [float(value) for value in scaler.mean_.tolist()],
            "scale": [float(value) for value in scaler.scale_.tolist()],
        },
        "coefs": [float(value) for value in classifier.coef_[0].tolist()],
        "intercept": float(classifier.intercept_[0]),
        "metrics": metrics,
        "training_config": training_config,
        "source_manifest": manifest,
        "data_kind": "simulation",
        "data_origin": "operator_supplied_simulation",
        "intended_use": "machine_learning_course_demonstration",
        "real_coast_claim_allowed": False,
        "warning": SIMULATION_DATA_WARNING,
        "deployment_mode": "shadow",
        "created_at": _normalise_created_at(created_at),
    }
    payload["hash"] = _artifact_hash(payload)
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return payload


def load_simulation_model(
    path_or_mapping: Path | str | Mapping[str, Any],
) -> LoadedSimulationModel:
    """Load and validate a JSON artifact without importing sklearn."""

    if isinstance(path_or_mapping, Mapping):
        payload = dict(path_or_mapping)
    else:
        payload = json.loads(Path(path_or_mapping).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise SimulationModelError("simulation model artifact must be a JSON object")
    if payload.get("schema") != ARTIFACT_SCHEMA:
        raise SimulationModelError("unsupported simulation model schema")
    if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise SimulationModelError("unsupported simulation model schema_version")
    if payload.get("model_type") != "binary_logistic_regression":
        raise SimulationModelError("unsupported simulation model type")
    if payload.get("data_kind") != "simulation":
        raise SimulationModelError("simulation artifact data_kind must be simulation")
    if payload.get("data_origin") != "operator_supplied_simulation":
        raise SimulationModelError(
            "simulation artifact data_origin must be operator_supplied_simulation"
        )
    if payload.get("intended_use") != "machine_learning_course_demonstration":
        raise SimulationModelError("simulation artifact intended_use is invalid")
    if payload.get("real_coast_claim_allowed") is not False:
        raise SimulationModelError("simulation artifact cannot allow real coast claims")
    if payload.get("warning") != SIMULATION_DATA_WARNING:
        raise SimulationModelError("simulation artifact warning is invalid")
    if payload.get("deployment_mode") != "shadow":
        raise SimulationModelError("simulation artifact deployment_mode must be shadow")
    if payload.get("class_order") != list(CLASS_ORDER):
        raise SimulationModelError("simulation artifact class order is invalid")
    if payload.get("feature_order") != list(FEATURE_ORDER):
        raise SimulationModelError("simulation artifact feature order is invalid")

    model_id = _required_text(payload, "model_id")
    version = _required_text(payload, "version")
    created_at = _required_text(payload, "created_at")
    window_size = payload.get("window_size")
    if (
        isinstance(window_size, bool)
        or not isinstance(window_size, int)
        or window_size < 2
    ):
        raise SimulationModelError("simulation artifact window_size is invalid")
    scaler = payload.get("scaler")
    if not isinstance(scaler, Mapping):
        raise SimulationModelError("simulation artifact scaler is invalid")
    size = len(FEATURE_ORDER)
    means = _numeric_sequence(scaler.get("mean"), "scaler.mean", size)
    scales = _numeric_sequence(scaler.get("scale"), "scaler.scale", size)
    if any(value <= 0.0 for value in scales):
        raise SimulationModelError("simulation artifact scaler scales must be positive")
    coefs = _numeric_sequence(payload.get("coefs"), "coefs", size)
    intercept = _finite_number(payload.get("intercept"), "intercept")
    metrics = payload.get("metrics")
    if not isinstance(metrics, Mapping):
        raise SimulationModelError("simulation artifact metrics must be an object")
    artifact_hash = _required_text(payload, "hash")
    if len(artifact_hash) != 64 or any(
        character not in "0123456789abcdef" for character in artifact_hash
    ):
        raise SimulationModelError("simulation artifact hash is invalid")
    hash_payload = dict(payload)
    del hash_payload["hash"]
    if not _constant_time_equal(artifact_hash, _artifact_hash(hash_payload)):
        raise SimulationModelError("simulation artifact hash mismatch")

    return LoadedSimulationModel(
        model_id=model_id,
        version=version,
        feature_order=tuple(payload["feature_order"]),
        window_size=window_size,
        scaler_mean=means,
        scaler_scale=scales,
        coefs=coefs,
        intercept=intercept,
        metrics=dict(metrics),
        created_at=created_at,
        artifact_hash=artifact_hash,
    )


def predict_simulation(
    model_or_artifact: LoadedSimulationModel | Path | str | Mapping[str, Any],
    samples: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    window_size: int | None = None,
) -> dict[str, Any]:
    """Convenience wrapper around validated standard-library inference."""

    model = (
        model_or_artifact
        if isinstance(model_or_artifact, LoadedSimulationModel)
        else load_simulation_model(model_or_artifact)
    )
    return model.predict(samples, window_size=window_size)


def _validate_training_options(
    model_id: str, version: str, window_size: int, test_fraction: float
) -> None:
    if not isinstance(model_id, str) or not model_id.strip():
        raise SimulationModelError("model_id is required")
    if not isinstance(version, str) or not version.strip():
        raise SimulationModelError("version is required")
    if (
        isinstance(window_size, bool)
        or not isinstance(window_size, int)
        or window_size < 2
    ):
        raise SimulationModelError("window_size must be an integer of at least 2")
    if (
        isinstance(test_fraction, bool)
        or not isinstance(test_fraction, (int, float))
        or not math.isfinite(float(test_fraction))
        or not 0.0 < float(test_fraction) < 1.0
    ):
        raise SimulationModelError("test_fraction must be between 0 and 1")


def _normalise_sessions(
    rows_or_sessions: Any, *, minimum_sessions: int = 2
) -> dict[str, list[dict[str, Any]]]:
    sessions: dict[str, list[dict[str, Any]]] = {}
    if isinstance(rows_or_sessions, Mapping):
        if "samples" in rows_or_sessions:
            wrappers = [rows_or_sessions]
        else:
            wrappers = [
                {"session_id": session_id, "samples": samples}
                for session_id, samples in rows_or_sessions.items()
            ]
        _append_session_wrappers(sessions, wrappers)
    elif isinstance(rows_or_sessions, Sequence) and not isinstance(
        rows_or_sessions, (str, bytes, bytearray)
    ):
        values = list(rows_or_sessions)
        if not values:
            raise SimulationModelError("at least two simulation sessions are required")
        if all(isinstance(value, Mapping) and "samples" in value for value in values):
            _append_session_wrappers(sessions, values)
        elif all(
            isinstance(value, Mapping) and "samples" not in value for value in values
        ):
            for raw_row in values:
                row = dict(raw_row)
                session_id = _session_id(row.get("session_id"))
                row["session_id"] = session_id
                sessions.setdefault(session_id, []).append(row)
        else:
            raise SimulationModelError(
                "simulation input mixes session objects and rows"
            )
    else:
        raise SimulationModelError(
            "simulation input must contain session sample mappings"
        )
    if len(sessions) < minimum_sessions:
        noun = "session" if minimum_sessions == 1 else "sessions"
        raise SimulationModelError(
            f"at least {minimum_sessions} {noun} are required for leakage-safe evaluation"
        )
    if any(not rows for rows in sessions.values()):
        raise SimulationModelError("simulation sessions must not be empty")
    return sessions


def _append_session_wrappers(
    destination: dict[str, list[dict[str, Any]]], wrappers: Sequence[Mapping[str, Any]]
) -> None:
    for wrapper in wrappers:
        session_id = _session_id(wrapper.get("session_id"))
        samples = wrapper.get("samples")
        if not isinstance(samples, Sequence) or isinstance(
            samples, (str, bytes, bytearray)
        ):
            raise SimulationModelError(f"session {session_id} samples must be a list")
        session_baseline = _optional_number(
            wrapper.get("baseline_mm", wrapper.get("baseline_distance_mm"))
        )
        for raw_row in samples:
            if not isinstance(raw_row, Mapping):
                raise SimulationModelError(
                    f"session {session_id} contains a non-object row"
                )
            row = dict(raw_row)
            row["session_id"] = session_id
            if session_baseline is not None and not _has_baseline(row):
                row["baseline_mm"] = session_baseline
            destination.setdefault(session_id, []).append(row)


def _prepare_training_rows(
    sessions: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    window_size: int,
    source_context: Mapping[str, Any] | None,
) -> tuple[list[tuple[str, list[float], int]], int, dict[str, Any]]:
    labelled: list[tuple[str, list[float], int]] = []
    unknown_count = 0
    fingerprint_rows: list[dict[str, Any]] = []
    session_summaries: list[dict[str, Any]] = []
    excluded_warmup_samples = 0
    for session_id in sorted(sessions):
        safe_count = 0
        danger_count = 0
        session_unknown_count = 0
        session_warmup_count = 0
        feature_rows = _session_feature_rows(sessions[session_id], window_size)
        for index, (row, feature_vector, window_complete) in enumerate(feature_rows):
            label = _normalise_label(_label_from_row(row))
            label_name = "unknown"
            target_eligible = window_complete
            if not target_eligible:
                excluded_warmup_samples += 1
                session_warmup_count += 1
                if label is not None:
                    label_name = CLASS_ORDER[label]
            elif label is None:
                unknown_count += 1
                session_unknown_count += 1
            else:
                label_name = CLASS_ORDER[label]
                safe_count += int(label == 0)
                danger_count += int(label == 1)
                labelled.append((session_id, feature_vector, label))
            fingerprint_rows.append(
                {
                    "session_id": session_id,
                    "sample_index": index,
                    "features": [float(value) for value in feature_vector],
                    "label": label_name,
                    "target_eligible": target_eligible,
                }
            )
        session_summaries.append(
            {
                "session_id": session_id,
                "sample_count": len(feature_rows),
                "excluded_warmup_samples": session_warmup_count,
                "label_counts": {
                    "safe": safe_count,
                    "danger": danger_count,
                    "unknown": session_unknown_count,
                },
            }
        )

    canonical = json.dumps(
        fingerprint_rows,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    context = dict(source_context or {})
    try:
        # Validate that caller provenance can be embedded and hashed safely.
        json.dumps(context, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SimulationModelError("source_context must be JSON serializable") from exc
    manifest = {
        **context,
        "dataset_hash": hashlib.sha256(canonical).hexdigest(),
        "session_ids": sorted(sessions),
        "session_count": len(sessions),
        "sample_count": len(fingerprint_rows),
        "labelled_sample_count": len(labelled),
        "excluded_unknown_samples": unknown_count,
        "excluded_warmup_samples": excluded_warmup_samples,
        "sessions": session_summaries,
    }
    return labelled, unknown_count, manifest


def _session_id(value: Any) -> str:
    if value is None or isinstance(value, bool):
        raise SimulationModelError("every simulation row requires session_id")
    result = str(value).strip()
    if not result:
        raise SimulationModelError("every simulation row requires session_id")
    return result


def _session_feature_rows(
    rows: Sequence[Mapping[str, Any]], window_size: int
) -> list[tuple[Mapping[str, Any], list[float], bool]]:
    ordered = _ordered_rows(rows)
    measurements: list[_Measurement] = []
    result: list[tuple[Mapping[str, Any], list[float], bool]] = []
    previous: _Measurement | None = None
    active_epoch: Any = object()
    scenario_vector: list[float] | None = None
    for index, row in enumerate(ordered):
        row_epoch = row.get(WINDOW_EPOCH_FIELD)
        if measurements and row_epoch != active_epoch:
            measurements = []
            previous = None
        active_epoch = row_epoch
        measurement = _measurement(row, index=index, previous=previous)
        row_scenario_vector = _simulated_environment_vector(row)
        if scenario_vector is None:
            scenario_vector = row_scenario_vector
        elif row_scenario_vector != scenario_vector:
            raise SimulationModelError(
                "simulated environment must remain constant within a session"
            )
        measurements.append(measurement)
        window = measurements[max(0, len(measurements) - window_size) :]
        result.append(
            (
                row,
                [*_features_from_window(window), *row_scenario_vector],
                len(window) == window_size,
            )
        )
        previous = measurement
    return result


def _latest_feature_vector(
    samples: Mapping[str, Any] | Sequence[Mapping[str, Any]], window_size: int
) -> tuple[list[float], dict[str, Any]]:
    if (
        isinstance(window_size, bool)
        or not isinstance(window_size, int)
        or window_size < 2
    ):
        raise SimulationModelError("window_size must be an integer of at least 2")
    if isinstance(samples, Mapping):
        rows: list[Mapping[str, Any]] = [samples]
    elif isinstance(samples, Sequence) and not isinstance(
        samples, (str, bytes, bytearray)
    ):
        rows = list(samples)
    else:
        raise SimulationModelError("prediction samples must be a row or a row sequence")
    if not rows or any(not isinstance(row, Mapping) for row in rows):
        raise SimulationModelError("prediction requires at least one valid sample row")
    if len({_optional_session_id(row.get("session_id")) for row in rows}) > 1:
        raise SimulationModelError(
            "a prediction window cannot cross session boundaries"
        )
    feature_rows = _session_feature_rows(rows, window_size)
    latest_epoch = feature_rows[-1][0].get(WINDOW_EPOCH_FIELD)
    available = 0
    for row, _features, _complete in reversed(feature_rows):
        if row.get(WINDOW_EPOCH_FIELD) != latest_epoch:
            break
        available += 1
        if available == window_size:
            break
    status = "ok" if available == window_size else "limited_history"
    return feature_rows[-1][1], {
        "status": status,
        "samples_available": available,
        "samples_required": window_size,
        "window_complete": available == window_size,
        "uses_reported_or_derived_rate": True,
    }


def _ordered_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    indexed = list(enumerate(rows))
    times = [_row_time(row) for row in rows]
    if all(value is not None for value in times):
        finite_times = [float(value) for value in times if value is not None]
        indexed.sort(key=lambda item: (finite_times[item[0]], item[0]))
    return [row for _, row in indexed]


def _measurement(
    row: Mapping[str, Any], *, index: int, previous: _Measurement | None
) -> _Measurement:
    distance = _required_measurement(row, "distance_mm")
    baseline = _first_number(row, "baseline_mm", "baseline_distance_mm")
    reported_rise = _first_number(row, "water_rise_mm", "water_height_mm")
    rise = baseline - distance if baseline is not None else reported_rise
    if baseline is None and rise is None:
        raise SimulationModelError("each sample requires baseline_mm or water_rise_mm")
    if baseline is None:
        if rise is None:  # pragma: no cover - guarded above
            raise SimulationModelError("water rise is unavailable")
        baseline = distance + rise
    if rise is None:
        rise = baseline - distance
    time_seconds = _row_time(row)
    if time_seconds is None:
        time_seconds = float(index)
    rate = _first_number(row, "rise_rate_mm_s", "water_rise_rate_mm_s")
    if rate is None:
        if previous is None:
            rate = 0.0
        else:
            elapsed = time_seconds - previous.time_seconds
            rate = 0.0 if elapsed <= 0.0 else (rise - previous.water_rise_mm) / elapsed
    return _Measurement(
        distance_mm=distance,
        baseline_mm=baseline,
        water_rise_mm=rise,
        rise_rate_mm_s=rate,
        time_seconds=time_seconds,
    )


def _features_from_window(window: Sequence[_Measurement]) -> list[float]:
    current = window[-1]
    previous = window[-2] if len(window) > 1 else current
    first = window[0]
    delta = current.water_rise_mm - previous.water_rise_mm
    elapsed = current.time_seconds - first.time_seconds
    slope = (
        current.rise_rate_mm_s
        if len(window) == 1 or elapsed <= 0.0
        else (current.water_rise_mm - first.water_rise_mm) / elapsed
    )
    rises = [measurement.water_rise_mm for measurement in window]
    mean = sum(rises) / len(rises)
    variance = sum((value - mean) ** 2 for value in rises) / len(rises)
    return [
        current.distance_mm,
        current.baseline_mm,
        current.water_rise_mm,
        current.rise_rate_mm_s,
        delta,
        slope,
        mean,
        math.sqrt(variance),
    ]


def _simulated_environment_vector(row: Mapping[str, Any]) -> list[float]:
    return [
        _required_measurement(row, name) for name in SIMULATED_ENVIRONMENT_FEATURE_ORDER
    ]


def _scenario_group_ids(
    sessions: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, str]:
    """Hash the 14 model inputs so identical scenarios cannot cross a split."""

    result: dict[str, str] = {}
    for session_id, rows in sessions.items():
        vectors = {tuple(_simulated_environment_vector(row)) for row in rows}
        if len(vectors) != 1:
            raise SimulationModelError(
                "simulated environment must remain constant within a session"
            )
        vector = next(iter(vectors))
        canonical = json.dumps(
            dict(zip(SIMULATED_ENVIRONMENT_FEATURE_ORDER, vector, strict=True)),
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        result[session_id] = hashlib.sha256(canonical).hexdigest()
    return result


def _group_holdout(
    labelled: Sequence[tuple[str, list[float], int]],
    *,
    test_fraction: float,
    random_state: int,
    session_groups: Mapping[str, str],
) -> tuple[list[str], list[str], str]:
    session_ids = sorted({session_id for session_id, _, _ in labelled})
    if len(session_ids) < 2:
        raise SimulationModelError(
            "at least two labelled sessions are required for group holdout"
        )
    target_test_session_count = max(
        1, min(len(session_ids) - 1, math.ceil(len(session_ids) * test_fraction))
    )
    group_to_sessions: dict[str, list[str]] = {}
    for session_id in session_ids:
        group_id = session_groups.get(session_id)
        if group_id is None:
            raise SimulationModelError(
                f"session {session_id} has no simulated environment group"
            )
        group_to_sessions.setdefault(group_id, []).append(session_id)
    group_ids = sorted(group_to_sessions)
    single_scenario = len(group_ids) == 1
    if single_scenario:
        # Repeated sessions remain independent split units, but this fallback
        # cannot measure environmental effects or cross-scenario generalization.
        split_unit_to_sessions = {
            session_id: [session_id] for session_id in session_ids
        }
        split_strategy = "whole_session_holdout_single_scenario"
    else:
        split_unit_to_sessions = group_to_sessions
        split_strategy = "whole_session_and_simulated_environment_group_holdout"
    rng = random.Random(random_state)
    split_unit_ids = sorted(split_unit_to_sessions)
    shuffled = list(split_unit_ids)
    rng.shuffle(shuffled)

    raw_candidates: Iterable[tuple[str, ...]]
    if len(split_unit_ids) <= 14:
        raw_candidates = itertools.chain.from_iterable(
            itertools.combinations(shuffled, size)
            for size in range(1, len(split_unit_ids))
        )
    else:
        generated: set[tuple[str, ...]] = set()
        test_split_unit_count = max(
            1,
            min(
                len(split_unit_ids) - 1,
                math.ceil(len(split_unit_ids) * test_fraction),
            ),
        )
        for _ in range(1_024):
            sampled = list(split_unit_ids)
            rng.shuffle(sampled)
            generated.add(tuple(sorted(sampled[:test_split_unit_count])))
        raw_candidates = iter(sorted(generated))

    global_danger_fraction = sum(label for _, _, label in labelled) / len(labelled)
    best: tuple[tuple[float, ...], tuple[str, ...]] | None = None
    for candidate_split_units in raw_candidates:
        test_split_unit_set = set(candidate_split_units)
        test_set = {
            session_id
            for split_unit_id in test_split_unit_set
            for session_id in split_unit_to_sessions[split_unit_id]
        }
        train_labels = [label for sid, _, label in labelled if sid not in test_set]
        test_labels = [label for sid, _, label in labelled if sid in test_set]
        if set(train_labels) != {0, 1} or set(test_labels) != {0, 1}:
            continue
        test_class_count = len(set(test_labels))
        test_danger_fraction = sum(test_labels) / len(test_labels)
        score = (
            float(test_class_count),
            -abs(len(test_set) - target_test_session_count),
            -abs(test_danger_fraction - global_danger_fraction),
            -float(sum(1 for value in test_labels if value == 1) == 0),
        )
        canonical_candidate = tuple(sorted(test_set))
        if (
            best is None
            or score > best[0]
            or (score == best[0] and canonical_candidate < best[1])
        ):
            best = (score, canonical_candidate)
    if best is None:
        raise SimulationModelError(
            "no leakage-safe session split leaves both classes in both training and test"
        )
    test_sessions = list(best[1])
    train_sessions = [sid for sid in session_ids if sid not in set(test_sessions)]
    return train_sessions, test_sessions, split_strategy


def _build_metrics(
    *,
    labelled: Sequence[tuple[str, list[float], int]],
    train_rows: Sequence[tuple[str, list[float], int]],
    test_rows: Sequence[tuple[str, list[float], int]],
    train_sessions: Sequence[str],
    test_sessions: Sequence[str],
    test_y: Sequence[int],
    test_predictions: Sequence[int],
    danger_probabilities: Sequence[float],
    baseline_threshold: float,
    baseline_train_predictions: Sequence[int],
    baseline_test_predictions: Sequence[int],
    ablation_results: Mapping[str, Mapping[str, Any]],
    scenario_groups: Mapping[str, str],
    unknown_count: int,
    excluded_warmup_samples: int,
    window_size: int,
    test_fraction: float,
    random_state: int,
    split_strategy: str,
) -> dict[str, Any]:
    model_test_metrics = _binary_metrics(
        test_y, test_predictions, danger_probabilities=danger_probabilities
    )
    model_test_session_macro = _session_macro_metrics(
        test_rows, test_predictions, danger_probabilities
    )
    train_y = [label for _, _, label in train_rows]
    baseline_train_metrics = _classification_only_metrics(
        train_y,
        baseline_train_predictions,
        danger_probabilities=[float(value) for value in baseline_train_predictions],
        decision_threshold=baseline_threshold,
    )
    baseline_test_metrics = _classification_only_metrics(
        test_y,
        baseline_test_predictions,
        danger_probabilities=[float(value) for value in baseline_test_predictions],
        decision_threshold=baseline_threshold,
    )
    baseline_test_session_macro = _session_macro_metrics(
        test_rows,
        baseline_test_predictions,
        [float(value) for value in baseline_test_predictions],
        decision_threshold=baseline_threshold,
    )
    _remove_probabilistic_metrics(baseline_test_session_macro)
    for session_metrics in baseline_test_session_macro["per_session"].values():
        _remove_probabilistic_metrics(session_metrics)
    ablation_metrics: dict[str, Any] = {}
    for name, result in ablation_results.items():
        train_probabilities = list(result["train_probabilities"])
        train_predictions = list(result["train_predictions"])
        test_probabilities = list(result["test_probabilities"])
        variant_test_predictions = list(result["test_predictions"])
        ablation_metrics[name] = {
            "feature_order": list(result["feature_order"]),
            "fit_on": "train_sessions_only",
            "split_strategy": "identical_to_22_feature_fusion",
            "train": _binary_metrics(
                train_y,
                train_predictions,
                danger_probabilities=train_probabilities,
            ),
            "test": _binary_metrics(
                test_y,
                variant_test_predictions,
                danger_probabilities=test_probabilities,
            ),
            "test_session_macro": _session_macro_metrics(
                test_rows,
                variant_test_predictions,
                test_probabilities,
            ),
        }
    train_scenario_groups = {scenario_groups[session] for session in train_sessions}
    test_scenario_groups = {scenario_groups[session] for session in test_sessions}
    scenario_group_overlap = sorted(train_scenario_groups & test_scenario_groups)
    scenario_generalization_evaluable = (
        split_strategy == "whole_session_and_simulated_environment_group_holdout"
    )
    train_scenario_group_count = len(train_scenario_groups)
    test_scenario_group_count = len(test_scenario_groups)
    environment_effects_learnable = train_scenario_group_count >= 2
    if scenario_generalization_evaluable and scenario_group_overlap:
        raise SimulationModelError("simulated environment groups leaked across split")
    if not scenario_generalization_evaluable and len(scenario_group_overlap) != 1:
        raise SimulationModelError(
            "single-scenario holdout must record its intentional scenario overlap"
        )
    delta_vs_baseline = _metric_delta(model_test_metrics, baseline_test_metrics)
    ultrasonic_test = ablation_metrics["ultrasonic_only_logistic_regression"]["test"]
    environment_test = ablation_metrics["environment_only_logistic_regression"]["test"]
    return {
        "split_strategy": split_strategy,
        "secondary_group": (
            "simulated_environment_feature_hash"
            if scenario_generalization_evaluable
            else None
        ),
        "train_sessions": list(train_sessions),
        "test_sessions": list(test_sessions),
        "session_overlap": [],
        "scenario_group_overlap": scenario_group_overlap,
        "scenario_group_overlap_expected": not scenario_generalization_evaluable,
        "scenario_generalization_evaluable": scenario_generalization_evaluable,
        "train_scenario_group_count": train_scenario_group_count,
        "test_scenario_group_count": test_scenario_group_count,
        "environment_effects_learnable": environment_effects_learnable,
        "test_fraction_requested": float(test_fraction),
        "random_state": random_state,
        "window_size": window_size,
        "labelled_samples": len(labelled),
        "excluded_unknown_samples": unknown_count,
        "excluded_warmup_samples": excluded_warmup_samples,
        "train_samples": len(train_rows),
        "test_samples": len(test_rows),
        "class_counts": {
            "all": _class_counts(label for _, _, label in labelled),
            "train": _class_counts(label for _, _, label in train_rows),
            "test": _class_counts(label for _, _, label in test_rows),
        },
        "test": model_test_metrics,
        "test_session_macro": model_test_session_macro,
        "baselines": {
            "water_rise_threshold": {
                "rule": "danger if water_rise_mm_current >= threshold_mm",
                "probabilistic_metrics_supported": False,
                "threshold_mm": baseline_threshold,
                "selection": {
                    "fit_on": "train_sessions_only",
                    "objective": "session_macro_balanced_accuracy",
                    "sample_weighting": "equal_session",
                    "tie_break": [
                        "danger_recall",
                        "specificity",
                        "lower_threshold_mm",
                    ],
                },
                "train": baseline_train_metrics,
                "test": baseline_test_metrics,
                "test_session_macro": baseline_test_session_macro,
            },
            **ablation_metrics,
        },
        "delta_vs_baseline": delta_vs_baseline,
        "delta_vs_ultrasonic_only": _metric_delta(model_test_metrics, ultrasonic_test),
        "delta_vs_environment_only": _metric_delta(
            model_test_metrics, environment_test
        ),
    }


def _binary_metrics(
    targets: Sequence[int],
    predictions: Sequence[int],
    *,
    danger_probabilities: Sequence[float],
    decision_threshold: float = 0.5,
) -> dict[str, Any]:
    if (
        not targets
        or len(targets) != len(predictions)
        or len(targets) != len(danger_probabilities)
    ):
        raise SimulationModelError("metric inputs must be non-empty and aligned")
    true_positive = sum(
        target == 1 and prediction == 1
        for target, prediction in zip(targets, predictions, strict=True)
    )
    true_negative = sum(
        target == 0 and prediction == 0
        for target, prediction in zip(targets, predictions, strict=True)
    )
    false_positive = sum(
        target == 0 and prediction == 1
        for target, prediction in zip(targets, predictions, strict=True)
    )
    false_negative = sum(
        target == 1 and prediction == 0
        for target, prediction in zip(targets, predictions, strict=True)
    )
    accuracy = (true_positive + true_negative) / len(targets)
    danger_precision = _safe_ratio(true_positive, true_positive + false_positive)
    danger_recall = _safe_ratio(true_positive, true_positive + false_negative)
    danger_f1 = _safe_ratio(
        2.0 * danger_precision * danger_recall,
        danger_precision + danger_recall,
    )
    specificity = _safe_ratio(true_negative, true_negative + false_positive)
    negative_predictive_value = _safe_ratio(
        true_negative, true_negative + false_negative
    )
    false_positive_rate = _safe_ratio(false_positive, false_positive + true_negative)
    false_negative_rate = _safe_ratio(false_negative, false_negative + true_positive)
    recalls = [
        recall
        for recall, count in (
            (specificity, true_negative + false_positive),
            (danger_recall, true_positive + false_negative),
        )
        if count > 0
    ]
    epsilon = 1e-15
    log_loss = -sum(
        target * math.log(min(1.0 - epsilon, max(epsilon, probability)))
        + (1 - target) * math.log(min(1.0 - epsilon, max(epsilon, 1.0 - probability)))
        for target, probability in zip(targets, danger_probabilities, strict=True)
    ) / len(targets)
    brier_score = sum(
        (probability - target) ** 2
        for target, probability in zip(targets, danger_probabilities, strict=True)
    ) / len(targets)
    return {
        "decision_threshold": decision_threshold,
        "accuracy": accuracy,
        "balanced_accuracy": sum(recalls) / len(recalls),
        "danger_precision": danger_precision,
        "danger_recall": danger_recall,
        "danger_f1": danger_f1,
        "specificity": specificity,
        "negative_predictive_value": negative_predictive_value,
        "false_positive_rate": false_positive_rate,
        "false_negative_rate": false_negative_rate,
        "roc_auc": _roc_auc(targets, danger_probabilities),
        "brier_score": brier_score,
        "log_loss": log_loss,
        "confusion": {
            "true_safe": true_negative,
            "false_danger": false_positive,
            "false_safe": false_negative,
            "true_danger": true_positive,
        },
    }


def _metric_delta(
    model_metrics: Mapping[str, Any], comparator_metrics: Mapping[str, Any]
) -> dict[str, Any]:
    result = {
        "positive_means_model_better": True,
        "accuracy": model_metrics["accuracy"] - comparator_metrics["accuracy"],
        "balanced_accuracy": model_metrics["balanced_accuracy"]
        - comparator_metrics["balanced_accuracy"],
        "danger_precision": model_metrics["danger_precision"]
        - comparator_metrics["danger_precision"],
        "danger_recall": model_metrics["danger_recall"]
        - comparator_metrics["danger_recall"],
        "danger_f1": model_metrics["danger_f1"] - comparator_metrics["danger_f1"],
        "brier_score_reduction": None,
        "log_loss_reduction": None,
    }
    if (
        comparator_metrics.get("brier_score") is not None
        and model_metrics.get("brier_score") is not None
    ):
        result["brier_score_reduction"] = (
            comparator_metrics["brier_score"] - model_metrics["brier_score"]
        )
    if (
        comparator_metrics.get("log_loss") is not None
        and model_metrics.get("log_loss") is not None
    ):
        result["log_loss_reduction"] = (
            comparator_metrics["log_loss"] - model_metrics["log_loss"]
        )
    model_auc = model_metrics.get("roc_auc")
    comparator_auc = comparator_metrics.get("roc_auc")
    result["roc_auc"] = (
        model_auc - comparator_auc
        if model_auc is not None and comparator_auc is not None
        else None
    )
    return result


def _classification_only_metrics(
    targets: Sequence[int],
    predictions: Sequence[int],
    *,
    danger_probabilities: Sequence[float],
    decision_threshold: float,
) -> dict[str, Any]:
    result = _binary_metrics(
        targets,
        predictions,
        danger_probabilities=danger_probabilities,
        decision_threshold=decision_threshold,
    )
    _remove_probabilistic_metrics(result)
    return result


def _remove_probabilistic_metrics(metrics: dict[str, Any]) -> None:
    metrics.update(
        {
            "probabilistic_metrics_supported": False,
            "roc_auc": None,
            "brier_score": None,
            "log_loss": None,
        }
    )


def _session_macro_metrics(
    rows: Sequence[tuple[str, list[float], int]],
    predictions: Sequence[int],
    probabilities: Sequence[float],
    *,
    decision_threshold: float = 0.5,
) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[Any]]] = {}
    for (session_id, _features, label), prediction, probability in zip(
        rows, predictions, probabilities, strict=True
    ):
        bucket = grouped.setdefault(
            session_id, {"targets": [], "predictions": [], "probabilities": []}
        )
        bucket["targets"].append(label)
        bucket["predictions"].append(prediction)
        bucket["probabilities"].append(probability)
    per_session = {
        session_id: _binary_metrics(
            bucket["targets"],
            bucket["predictions"],
            danger_probabilities=bucket["probabilities"],
            decision_threshold=decision_threshold,
        )
        for session_id, bucket in sorted(grouped.items())
    }
    metric_names = (
        "accuracy",
        "balanced_accuracy",
        "danger_precision",
        "danger_recall",
        "danger_f1",
        "specificity",
        "negative_predictive_value",
        "false_positive_rate",
        "false_negative_rate",
        "roc_auc",
        "brier_score",
        "log_loss",
    )
    result: dict[str, Any] = {
        "aggregation": "unweighted_mean_across_test_sessions",
        "session_count": len(per_session),
        "per_session": per_session,
    }
    for name in metric_names:
        values = [
            float(metrics[name])
            for metrics in per_session.values()
            if metrics[name] is not None
        ]
        result[name] = sum(values) / len(values) if values else None
        if name == "roc_auc":
            result["roc_auc_evaluable_session_count"] = len(values)
    return result


def _fit_water_rise_threshold(
    train_rows: Sequence[tuple[str, list[float], int]],
) -> float:
    """Fit a one-feature rule on training sessions only."""

    rises = sorted({float(features[2]) for _, features, _ in train_rows})
    if not rises:
        raise SimulationModelError("threshold baseline requires training samples")
    candidates = [rises[0]]
    candidates.extend((left + right) / 2.0 for left, right in itertools.pairwise(rises))
    candidates.append(rises[-1] + max(1e-9, abs(rises[-1]) * 1e-12))
    best: tuple[tuple[float, float, float, float], float] | None = None
    for threshold in candidates:
        predictions = _threshold_predictions(train_rows, threshold)
        metrics = _session_macro_metrics(
            train_rows,
            predictions,
            [float(value) for value in predictions],
            decision_threshold=threshold,
        )
        score = (
            float(metrics["balanced_accuracy"]),
            float(metrics["danger_recall"]),
            float(metrics["specificity"]),
            -threshold,
        )
        if best is None or score > best[0]:
            best = (score, threshold)
    if best is None:  # pragma: no cover - candidates is never empty
        raise SimulationModelError("threshold baseline selection failed")
    return float(best[1])


def _threshold_predictions(
    rows: Sequence[tuple[str, list[float], int]], threshold: float
) -> list[int]:
    return [int(float(features[2]) >= threshold) for _, features, _ in rows]


def _roc_auc(targets: Sequence[int], scores: Sequence[float]) -> float | None:
    positives = sum(target == 1 for target in targets)
    negatives = sum(target == 0 for target in targets)
    if positives == 0 or negatives == 0:
        return None
    ordered = sorted(enumerate(scores), key=lambda item: (float(item[1]), item[0]))
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and float(ordered[end][1]) == float(ordered[index][1]):
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        rank_sum += average_rank * sum(
            targets[original_index] == 1
            for original_index, _score in ordered[index:end]
        )
        index = end
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def _class_counts(labels: Any) -> dict[str, int]:
    values = list(labels)
    return {
        "safe": sum(value == 0 for value in values),
        "danger": sum(value == 1 for value in values),
    }


def _safe_ratio(numerator: float, denominator: float) -> float:
    return 0.0 if denominator == 0.0 else numerator / denominator


def _label_from_row(row: Mapping[str, Any]) -> Any:
    for name in ("label", "simulation_label", "risk_label"):
        if name in row:
            return row[name]
    return None


def _normalise_label(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise SimulationModelError("boolean simulation labels are not accepted")
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in UNKNOWN_LABELS:
            return None
        if normalised in {"safe", "0"}:
            return 0
        if normalised in {"danger", "1"}:
            return 1
    elif isinstance(value, (int, float)) and math.isfinite(float(value)):
        if float(value) == 0.0:
            return 0
        if float(value) == 1.0:
            return 1
    raise SimulationModelError(f"unsupported simulation label: {value!r}")


def _row_time(row: Mapping[str, Any]) -> float | None:
    for name in ("timestamp", "recorded_at", "received_at", "created_at"):
        if name not in row or row[name] is None:
            continue
        value = row[name]
        if isinstance(value, datetime):
            current = value
        elif isinstance(value, str):
            try:
                current = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise SimulationModelError(
                    f"invalid sample timestamp: {value!r}"
                ) from exc
        elif not isinstance(value, bool) and isinstance(value, (int, float)):
            result = float(value)
            if math.isfinite(result):
                return result
            raise SimulationModelError(f"invalid sample timestamp: {value!r}")
        else:
            raise SimulationModelError(f"invalid sample timestamp: {value!r}")
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.timestamp()
    if "uptime_ms" in row:
        value = _optional_number(row.get("uptime_ms"))
        if value is not None:
            return value / 1_000.0
    return None


def _has_baseline(row: Mapping[str, Any]) -> bool:
    return "baseline_mm" in row or "baseline_distance_mm" in row


def _first_number(row: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        if name in row and row[name] is not None:
            return _finite_number(row[name], name)
    return None


def _required_measurement(row: Mapping[str, Any], name: str) -> float:
    if name not in row:
        raise SimulationModelError(f"each sample requires {name}")
    return _finite_number(row[name], name)


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    return _finite_number(value, "numeric value")


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SimulationModelError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SimulationModelError(f"{name} must be a finite number")
    return result


def _numeric_sequence(value: Any, name: str, expected: int) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != expected:
        raise SimulationModelError(f"{name} must contain {expected} numbers")
    return tuple(_finite_number(item, name) for item in value)


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SimulationModelError(f"simulation artifact {name} is required")
    return value.strip()


def _normalise_created_at(value: datetime | str | None) -> str:
    if value is None:
        timestamp = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SimulationModelError(
                "created_at must be an ISO-8601 timestamp"
            ) from exc
    else:
        raise SimulationModelError("created_at must be a datetime or ISO-8601 string")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _artifact_hash(payload_without_hash: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload_without_hash,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        exponential = math.exp(-value)
        return 1.0 / (1.0 + exponential)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _optional_session_id(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


__all__ = [
    "ARTIFACT_SCHEMA",
    "CLASS_ORDER",
    "DEFAULT_MODEL_ID",
    "FEATURE_ORDER",
    "WINDOW_EPOCH_FIELD",
    "LoadedSimulationModel",
    "SimulationModelError",
    "assess_simulation_training_data",
    "build_simulation_features",
    "load_simulation_model",
    "predict_simulation",
    "train_simulation_model",
]
