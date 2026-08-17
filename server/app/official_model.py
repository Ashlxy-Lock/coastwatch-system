"""Leakage-safe logistic regression trained only on registered UK official data.

The frozen test split is never used to fit the scaler, classifier, decision
threshold, or baseline threshold.  ESP32/session rows are not accepted by any
training API in this module and the exported artifact records zero sensor rows
for every fitting stage.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .official_dataset import (
    OFFICIAL_DATA_ORIGIN,
    OFFICIAL_FEATURE_ORDER,
    RegisteredOfficialDataset,
    canonical_json_bytes,
    freeze_official_sensor_context,
    rows_for_split,
)

ARTIFACT_SCHEMA = "coastwatch.uk-official-logreg"
ARTIFACT_SCHEMA_VERSION = 1
MODEL_ID = "uk-official-coast-logreg-v2"
CLASS_ORDER: tuple[str, ...] = ("safe", "extreme_water")
MIN_TRAIN_ROWS_PER_SPLIT = 8
MIN_ACTIVATION_ROWS_PER_SPLIT = 200
MIN_ACTIVATION_SITES = 3

_TRAINING_LOCK = threading.Lock()


class OfficialModelError(ValueError):
    """Raised when fitting or loading violates the official-model contract."""


@dataclass(frozen=True)
class LoadedOfficialModel:
    """Validated, pure-Python binary logistic-regression artifact."""

    model_id: str
    version: str
    feature_order: tuple[str, ...]
    class_order: tuple[str, ...]
    decision_threshold: float
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    metrics: Mapping[str, Any]
    source_manifest: Mapping[str, Any]
    sensor_test_contexts: tuple[Mapping[str, Any], ...]
    sensor_mapping_references: tuple[Mapping[str, Any], ...]
    training_feature_ranges: Mapping[str, Mapping[str, float]]
    created_at: str
    artifact_sha256: str
    activatable: bool
    deployment_mode: str

    def predict_features(self, features: Mapping[str, Any]) -> dict[str, Any]:
        """Evaluate one complete official-schema feature vector."""

        if not isinstance(features, Mapping):
            raise OfficialModelError("features must be a mapping")
        values = tuple(
            _finite_number(features.get(name), f"features.{name}")
            for name in self.feature_order
        )
        standardized = tuple(
            (value - self.scaler_mean[index]) / self.scaler_scale[index]
            for index, value in enumerate(values)
        )
        logit = self.intercept + sum(
            coefficient * value
            for coefficient, value in zip(self.coefficients, standardized, strict=True)
        )
        probability = _sigmoid(logit)
        predicted_label = (
            "extreme_water" if probability >= self.decision_threshold else "safe"
        )
        return {
            "model_id": self.model_id,
            "version": self.version,
            "schema": ARTIFACT_SCHEMA,
            "deployment_mode": self.deployment_mode,
            "predicted_label": predicted_label,
            "safe_probability": 1.0 - probability,
            "extreme_water_probability": probability,
            "probabilities": {
                "safe": 1.0 - probability,
                "extreme_water": probability,
            },
            "decision_threshold": self.decision_threshold,
            "reason_codes": ["UK_OFFICIAL_MODEL", "SHADOW_ONLY"],
            "features": dict(zip(self.feature_order, values, strict=True)),
        }


def assess_official_training_data(
    dataset: RegisteredOfficialDataset,
    *,
    selected_site_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Inspect a registered dataset without fitting or allocating sklearn arrays."""

    sites = _normalise_selected_sites(dataset, selected_site_ids)
    split_rows = {
        name: rows_for_split(dataset, name, selected_site_ids=sites)
        for name in ("train", "validation", "frozen_test")
    }
    blockers: list[str] = []
    activation_blockers: list[str] = []
    evidence_warnings: list[str] = []
    split_summary: dict[str, Any] = {}
    for name, rows in split_rows.items():
        counts = _class_counts(row["target_extreme_water"] for row in rows)
        represented_sites = sorted({row["site_id"] for row in rows})
        per_site_class_counts = {
            site_id: _class_counts(
                row["target_extreme_water"] for row in rows if row["site_id"] == site_id
            )
            for site_id in sites
        }
        macro_eligible_site_ids = [
            site_id
            for site_id, site_counts in per_site_class_counts.items()
            if site_counts["safe"] > 0 and site_counts["extreme_water"] > 0
        ]
        macro_ineligible_site_ids = [
            site_id for site_id in sites if site_id not in macro_eligible_site_ids
        ]
        split_summary[name] = {
            "row_count": len(rows),
            "class_counts": counts,
            "site_ids": represented_sites,
            "per_site_class_counts": per_site_class_counts,
            "macro_eligible_site_ids": macro_eligible_site_ids,
            "macro_ineligible_site_ids": macro_ineligible_site_ids,
            "macro_eligible_site_count": len(macro_eligible_site_ids),
            "selected_site_count": len(sites),
            "complete_macro_coverage": not macro_ineligible_site_ids,
            "start": rows[0]["timestamp"].isoformat() if rows else None,
            "end": rows[-1]["timestamp"].isoformat() if rows else None,
        }
        if len(rows) < MIN_TRAIN_ROWS_PER_SPLIT:
            blockers.append(f"{name} requires at least {MIN_TRAIN_ROWS_PER_SPLIT} rows")
        if counts["safe"] == 0 or counts["extreme_water"] == 0:
            blockers.append(f"{name} must contain both target classes")
        missing_sites = sorted(set(sites) - set(represented_sites))
        if missing_sites:
            blockers.append(
                f"{name} has no rows for selected sites: {', '.join(missing_sites)}"
            )
        if len(rows) < MIN_ACTIVATION_ROWS_PER_SPLIT:
            message = f"{name} has fewer than {MIN_ACTIVATION_ROWS_PER_SPLIT} rows"
            evidence_warnings.append(message)
            activation_blockers.append(message)
        if macro_ineligible_site_ids:
            message = f"{name} sites lack both target classes: " + ", ".join(
                macro_ineligible_site_ids
            )
            evidence_warnings.append(message)
            if name in {"validation", "frozen_test"}:
                activation_blockers.append(message)

    storm_groups = {
        name: _event_storm_groups(rows) for name, rows in split_rows.items()
    }
    overlaps = {
        "train_validation": sorted(storm_groups["train"] & storm_groups["validation"]),
        "train_frozen_test": sorted(
            storm_groups["train"] & storm_groups["frozen_test"]
        ),
        "validation_frozen_test": sorted(
            storm_groups["validation"] & storm_groups["frozen_test"]
        ),
    }
    if any(overlaps.values()):
        blockers.append("identified storm_group_id values overlap chronological splits")

    if len(sites) == 1:
        evidence_tier = "exploratory_single_site"
        evidence_warnings.append("single-site result cannot support a cross-site claim")
    elif len(sites) == 2:
        evidence_tier = "preliminary_two_site"
        evidence_warnings.append("two-site result provides limited cross-site evidence")
    else:
        evidence_tier = "course_demo_three_plus_sites"
    if len(sites) < MIN_ACTIVATION_SITES:
        activation_blockers.append(
            f"activation requires at least {MIN_ACTIVATION_SITES} selected sites"
        )
    if dataset.activatable:
        provenance_assurance = "operator_attested_raw_hash_verified"
        evidence_warnings.append(
            "Raw source hashes are verified, but harmonisation and labels are "
            "operator-attested and were not independently replayed."
        )
    else:
        provenance_assurance = "synthetic_test_fixture_nonactivatable"
    if not dataset.activatable:
        activation_blockers.append("synthetic test fixtures are never activatable")
    activation_blockers.extend(blockers)

    total_rows = sum(len(rows) for rows in split_rows.values())
    frozen_summary = split_summary["frozen_test"]
    # Two dense float arrays plus labels and temporary standardized data.  This
    # is an intentionally conservative user-facing estimate, not an allocator.
    estimated_peak_mb = round(
        max(1.0, total_rows * len(OFFICIAL_FEATURE_ORDER) * 8 * 4 / 1024 / 1024),
        2,
    )
    return {
        "ready": not blockers,
        "activation_ready": not activation_blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "activation_blockers": list(dict.fromkeys(activation_blockers)),
        "evidence_tier": evidence_tier,
        "evidence_warnings": list(dict.fromkeys(evidence_warnings)),
        "provenance_assurance": provenance_assurance,
        "deterministic_importer_replay_verified": False,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "data_origin": dataset.data_origin,
        "activatable_dataset": dataset.activatable,
        "selected_site_ids": list(sites),
        "split_summary": split_summary,
        "site_macro_evaluation": {
            "selected_site_count": len(sites),
            "eligible_site_count": frozen_summary["macro_eligible_site_count"],
            "eligible_site_ids": frozen_summary["macro_eligible_site_ids"],
            "ineligible_site_ids": frozen_summary["macro_ineligible_site_ids"],
            "complete_coverage": frozen_summary["complete_macro_coverage"],
        },
        "storm_group_overlap": overlaps,
        "label_definition": dataset.manifest["label_definition"],
        "resource_estimate": {
            "row_count": total_rows,
            "feature_count": len(OFFICIAL_FEATURE_ORDER),
            "estimated_peak_memory_mb": estimated_peak_mb,
            "cpu_threads": 1,
            "gpu": False,
            "automatic_training": False,
        },
        "recommended_minimum": {
            "site_count": MIN_ACTIVATION_SITES,
            "rows_per_split": MIN_ACTIVATION_ROWS_PER_SPLIT,
        },
    }


def train_official_model(
    dataset: RegisteredOfficialDataset,
    *,
    output_path: Path | str | None = None,
    selected_site_ids: Sequence[str] | None = None,
    version: str = "1",
    random_state: int = 42,
    created_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Fit scaler/model on train, select threshold on validation, test once."""

    if not isinstance(version, str) or not version.strip():
        raise OfficialModelError("version must be a non-empty string")
    if isinstance(random_state, bool) or not isinstance(random_state, int):
        raise OfficialModelError("random_state must be an integer")
    readiness = assess_official_training_data(
        dataset, selected_site_ids=selected_site_ids
    )
    if not readiness["ready"]:
        raise OfficialModelError(
            "official dataset is not training-ready: "
            + "; ".join(readiness["blockers"])
        )
    sites = tuple(readiness["selected_site_ids"])
    train_rows = rows_for_split(dataset, "train", selected_site_ids=sites)
    validation_rows = rows_for_split(dataset, "validation", selected_site_ids=sites)
    test_rows = rows_for_split(dataset, "frozen_test", selected_site_ids=sites)

    if not _TRAINING_LOCK.acquire(blocking=False):
        raise OfficialModelError("another official model training run is in progress")
    try:
        fit = _fit_single_threaded(
            train_rows,
            validation_rows,
            test_rows,
            random_state=random_state,
        )
    finally:
        _TRAINING_LOCK.release()

    water_baseline = _per_site_water_level_baseline(
        validation_rows,
        test_rows,
        selected_site_ids=sites,
    )
    persistence = {
        "available": False,
        "reason": (
            "not reported: without a separate official per-site extreme-water "
            "threshold, level persistence would duplicate the water-level rule"
        ),
        "uses_future_target_as_input": False,
    }
    contexts = tuple(
        freeze_official_sensor_context(dataset, site_id=site_id, split="frozen_test")
        for site_id in sites
    )
    mapping_references = tuple(
        _sensor_mapping_reference(train_rows, site_id) for site_id in sites
    )
    training_feature_ranges = {
        name: {
            "min": min(float(row[name]) for row in train_rows),
            "max": max(float(row[name]) for row in train_rows),
        }
        for name in OFFICIAL_FEATURE_ORDER
    }
    timestamp = _normalise_created_at(created_at)
    split_counts = {
        "train": len(train_rows),
        "validation": len(validation_rows),
        "frozen_test": len(test_rows),
    }
    per_site_test_metrics = _per_site_metrics(
        test_rows, fit["test_probabilities"], fit["decision_threshold"]
    )
    fair_baseline_complete = bool(
        water_baseline["selected_site_coverage"]["complete_coverage"]
    )
    site_macro_available = bool(
        per_site_test_metrics["complete_coverage"] and fair_baseline_complete
    )
    delta_vs_threshold = _metric_delta_vs_threshold(
        fit["test_metrics"],
        water_baseline["frozen_test"],
        per_site_test_metrics["macro_average"],
        water_baseline["per_site_frozen_test"]["macro_average"],
        complete_coverage=site_macro_available,
    )
    if not per_site_test_metrics["complete_coverage"]:
        primary_unavailable_reason = (
            "not every selected site has both classes in frozen_test"
        )
    elif not fair_baseline_complete:
        primary_unavailable_reason = (
            "fair validation-selected per-site water-level baseline lacks "
            "complete selected-site coverage"
        )
    else:
        primary_unavailable_reason = None
    artifact: dict[str, Any] = {
        "schema": ARTIFACT_SCHEMA,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "version": version.strip(),
        "model_type": "binary_logistic_regression",
        "deployment_mode": "shadow",
        "activatable": bool(readiness["activation_ready"] and fair_baseline_complete),
        "feature_order": list(OFFICIAL_FEATURE_ORDER),
        "class_order": list(CLASS_ORDER),
        "decision_threshold": fit["decision_threshold"],
        "scaler_mean": fit["scaler_mean"],
        "scaler_scale": fit["scaler_scale"],
        "coefficients": fit["coefficients"],
        "intercept": fit["intercept"],
        "metrics": {
            "primary_metric": (
                "site_macro_frozen_test_pr_auc" if site_macro_available else None
            ),
            "primary_metric_available": site_macro_available,
            "primary_metric_unavailable_reason": primary_unavailable_reason,
            "row_level_companion_metric": "frozen_test_pr_auc",
            "validation": fit["validation_metrics"],
            "frozen_test": fit["test_metrics"],
            "per_site_frozen_test": per_site_test_metrics,
            "baselines": {
                "water_level_threshold": water_baseline,
                "observable_water_level_persistence": persistence,
            },
            "delta_vs_water_level_threshold": delta_vs_threshold,
        },
        "training_config": {
            "random_state": random_state,
            "scaler_fit_split": "train",
            "classifier_fit_split": "train",
            "decision_threshold_selected_on": "validation",
            "water_level_threshold_selection_split": "validation",
            "final_metrics_split": "frozen_test",
            "class_weight": None,
            "sample_weight": "equal_total_weight_per_site_then_equal_class_weight_within_site",
            "penalty": "l2",
            "regularization_C": 1.0,
            "solver": "lbfgs",
            "max_iter": 2000,
            "cpu_thread_limit": 1,
            "gpu_used": False,
            "split_definitions": dataset.manifest["splits"],
            "storm_group_overlap": readiness["storm_group_overlap"],
        },
        "source_manifest": {
            "data_origin": dataset.data_origin,
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.version,
            "dataset_registration_sha256": dataset.registration_sha256,
            "dataset_manifest_sha256": dataset.manifest_sha256,
            "harmonised_table_sha256": dataset.table_sha256,
            "sources": dataset.manifest["sources"],
            "site_ids": list(sites),
            "site_metadata": {
                site_id: dataset.manifest["site_metadata"][site_id] for site_id in sites
            },
            "date_range": dataset.manifest["date_range"],
            "splits": dataset.manifest["splits"],
            "label_definition": dataset.manifest["label_definition"],
            "row_counts": split_counts,
            "provenance_assurance": readiness["provenance_assurance"],
            "deterministic_importer_replay_verified": False,
            "provenance_limitation": (
                "Raw archive bytes and hashes are verified; transformation and "
                "target derivation remain operator-attested, not independently replayed."
            ),
        },
        "data_contract": {
            "fit_data_kinds": (
                ["uk_official"]
                if dataset.data_origin == OFFICIAL_DATA_ORIGIN
                else ["synthetic_test_fixture"]
            ),
            "sensor_session_ids_used_for_fit": [],
            "sensor_rows_used_for_fit": 0,
            "sensor_rows_used_for_scaler": 0,
            "sensor_rows_used_for_threshold": 0,
            "operator_labels_used_for_fit": 0,
            "frozen_test_rows_used_for_fit": 0,
            "frozen_test_rows_used_for_threshold": 0,
            "baseline_frozen_test_rows_used_for_threshold": 0,
            "deterministic_importer_replay_verified": False,
            "surge_residual_representation": (
                "implicit_relative_water_level_minus_predicted_tide_not_a_model_feature"
            ),
        },
        "sensor_test_contexts": list(contexts),
        "sensor_mapping_references": list(mapping_references),
        "training_feature_ranges": training_feature_ranges,
        "readiness_snapshot": readiness,
        "created_at": timestamp,
    }
    artifact["artifact_sha256"] = _mapping_hash(artifact)
    # Validate before publishing; synthetic fixtures are deliberately loadable
    # only with require_activatable=False in focused tests.
    load_official_model(artifact, require_activatable=False)
    if output_path is not None:
        _write_artifact_atomic(Path(output_path), artifact)
    return artifact


def load_official_model(
    path_or_mapping: Path | str | Mapping[str, Any],
    *,
    require_activatable: bool = True,
) -> LoadedOfficialModel:
    """Load and fully validate a portable JSON official-model artifact."""

    if isinstance(path_or_mapping, Mapping):
        payload = _json_mapping_copy(path_or_mapping, "artifact")
    else:
        path = Path(path_or_mapping)
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OfficialModelError("official artifact is not valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise OfficialModelError("official artifact must be a JSON object")
        payload = _json_mapping_copy(decoded, "artifact")

    if payload.get("schema") != ARTIFACT_SCHEMA:
        raise OfficialModelError("unsupported official artifact schema")
    if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise OfficialModelError("unsupported official artifact schema_version")
    if payload.get("model_id") != MODEL_ID:
        raise OfficialModelError("unexpected official artifact model_id")
    if payload.get("model_type") != "binary_logistic_regression":
        raise OfficialModelError("unsupported official artifact model_type")
    if payload.get("deployment_mode") != "shadow":
        raise OfficialModelError("official research artifact must remain shadow-only")
    activatable = payload.get("activatable")
    if not isinstance(activatable, bool):
        raise OfficialModelError("artifact activatable must be boolean")
    if require_activatable and not activatable:
        raise OfficialModelError("artifact is not activatable")
    if payload.get("feature_order") != list(OFFICIAL_FEATURE_ORDER):
        raise OfficialModelError("official artifact feature order is invalid")
    if payload.get("class_order") != list(CLASS_ORDER):
        raise OfficialModelError("official artifact class order is invalid")

    supplied_hash = payload.pop("artifact_sha256", None)
    expected_hash = _mapping_hash(payload)
    if not isinstance(supplied_hash, str) or supplied_hash != expected_hash:
        raise OfficialModelError("official artifact sha256 is invalid")
    payload["artifact_sha256"] = supplied_hash

    size = len(OFFICIAL_FEATURE_ORDER)
    means = _number_sequence(payload.get("scaler_mean"), size, "scaler_mean")
    scales = _number_sequence(payload.get("scaler_scale"), size, "scaler_scale")
    if any(value <= 0 for value in scales):
        raise OfficialModelError("scaler_scale values must be positive")
    coefficients = _number_sequence(payload.get("coefficients"), size, "coefficients")
    intercept = _finite_number(payload.get("intercept"), "intercept")
    threshold = _finite_number(payload.get("decision_threshold"), "decision_threshold")
    if not 0 < threshold < 1:
        raise OfficialModelError("decision_threshold must be between zero and one")
    version = payload.get("version")
    created_at = payload.get("created_at")
    if not isinstance(version, str) or not version.strip():
        raise OfficialModelError("artifact version is required")
    if not isinstance(created_at, str) or not created_at.strip():
        raise OfficialModelError("artifact created_at is required")

    source = payload.get("source_manifest")
    if not isinstance(source, Mapping):
        raise OfficialModelError("artifact source manifest is required")
    source_origin = source.get("data_origin")
    if source_origin not in {OFFICIAL_DATA_ORIGIN, "synthetic_test_fixture"}:
        raise OfficialModelError("artifact source data_origin is invalid")
    if source_origin == "synthetic_test_fixture" and activatable:
        raise OfficialModelError("synthetic test artifacts are never activatable")
    if require_activatable and source_origin != OFFICIAL_DATA_ORIGIN:
        raise OfficialModelError("activatable artifact must use UK official data")
    expected_assurance = (
        "operator_attested_raw_hash_verified"
        if source_origin == OFFICIAL_DATA_ORIGIN
        else "synthetic_test_fixture_nonactivatable"
    )
    if source.get("provenance_assurance") != expected_assurance:
        raise OfficialModelError("artifact provenance assurance is invalid")
    if source.get("deterministic_importer_replay_verified") is not False:
        raise OfficialModelError(
            "artifact must not claim deterministic importer replay verification"
        )
    label = source.get("label_definition")
    if (
        not isinstance(label, Mapping)
        or label.get("target_time_relation") != "future"
        or not isinstance(label.get("forecast_horizon_hours"), (int, float))
        or isinstance(label.get("forecast_horizon_hours"), bool)
        or float(label["forecast_horizon_hours"]) <= 0
    ):
        raise OfficialModelError("artifact must contain a future target definition")
    contract = payload.get("data_contract")
    required_zeroes = (
        "sensor_rows_used_for_fit",
        "sensor_rows_used_for_scaler",
        "sensor_rows_used_for_threshold",
        "operator_labels_used_for_fit",
        "frozen_test_rows_used_for_fit",
        "frozen_test_rows_used_for_threshold",
    )
    if not isinstance(contract, Mapping):
        raise OfficialModelError("artifact data_contract is required")
    expected_fit_kind = (
        ["uk_official"]
        if source_origin == OFFICIAL_DATA_ORIGIN
        else ["synthetic_test_fixture"]
    )
    if contract.get("fit_data_kinds") != expected_fit_kind:
        raise OfficialModelError("artifact fit_data_kinds is invalid")
    if contract.get("sensor_session_ids_used_for_fit") != []:
        raise OfficialModelError("sensor session ids must never enter model fitting")
    if any(contract.get(key) != 0 for key in required_zeroes):
        raise OfficialModelError("artifact records forbidden non-official fitting rows")

    metrics = payload.get("metrics")
    if not isinstance(metrics, Mapping):
        raise OfficialModelError("artifact metrics are required")
    test_metrics = metrics.get("frozen_test")
    if not isinstance(test_metrics, Mapping) or not isinstance(
        test_metrics.get("pr_auc"), (int, float)
    ):
        raise OfficialModelError("artifact frozen-test PR-AUC is required")
    contexts = _validate_sensor_contexts(payload.get("sensor_test_contexts"), source)
    mapping_references = _validate_mapping_references(
        payload.get("sensor_mapping_references"), source
    )
    feature_ranges = _validate_feature_ranges(payload.get("training_feature_ranges"))
    return LoadedOfficialModel(
        model_id=MODEL_ID,
        version=version,
        feature_order=OFFICIAL_FEATURE_ORDER,
        class_order=CLASS_ORDER,
        decision_threshold=threshold,
        scaler_mean=means,
        scaler_scale=scales,
        coefficients=coefficients,
        intercept=intercept,
        metrics=metrics,
        source_manifest=source,
        sensor_test_contexts=contexts,
        sensor_mapping_references=mapping_references,
        training_feature_ranges=feature_ranges,
        created_at=created_at,
        artifact_sha256=supplied_hash,
        activatable=activatable,
        deployment_mode="shadow",
    )


def artifact_sha256(path_or_mapping: Path | str | Mapping[str, Any]) -> str:
    """Return the verified artifact hash (and reject tampered content)."""

    return load_official_model(
        path_or_mapping, require_activatable=False
    ).artifact_sha256


def training_in_progress() -> bool:
    acquired = _TRAINING_LOCK.acquire(blocking=False)
    if acquired:
        _TRAINING_LOCK.release()
        return False
    return True


def _fit_single_threaded(
    train_rows: Sequence[Mapping[str, Any]],
    validation_rows: Sequence[Mapping[str, Any]],
    test_rows: Sequence[Mapping[str, Any]],
    *,
    random_state: int,
) -> dict[str, Any]:
    try:
        import numpy as np
        from sklearn.linear_model import (  # type: ignore[import-untyped]
            LogisticRegression,
        )
        from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - minimal inference deployment
        raise RuntimeError(
            "official model training requires numpy and scikit-learn"
        ) from exc
    try:
        from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

        limit_context: Any = threadpool_limits(limits=1)
    except ImportError:  # pragma: no cover - sklearn normally installs it
        limit_context = nullcontext()

    train_x = np.asarray(
        [[row[name] for name in OFFICIAL_FEATURE_ORDER] for row in train_rows],
        dtype=float,
    )
    validation_x = np.asarray(
        [[row[name] for name in OFFICIAL_FEATURE_ORDER] for row in validation_rows],
        dtype=float,
    )
    test_x = np.asarray(
        [[row[name] for name in OFFICIAL_FEATURE_ORDER] for row in test_rows],
        dtype=float,
    )
    train_y = np.asarray([row["target_extreme_water"] for row in train_rows], dtype=int)
    sample_weights: Any = np.asarray(
        _site_class_balanced_weights(train_rows), dtype=float
    )
    validation_y = np.asarray(
        [row["target_extreme_water"] for row in validation_rows], dtype=int
    )
    test_y = np.asarray([row["target_extreme_water"] for row in test_rows], dtype=int)
    with limit_context:
        scaler = StandardScaler()
        scaled_train = scaler.fit_transform(train_x)
        classifier = LogisticRegression(
            C=1.0,
            class_weight=None,
            fit_intercept=True,
            max_iter=2000,
            random_state=random_state,
            solver="lbfgs",
            l1_ratio=0.0,
            tol=1e-8,
        )
        classifier.fit(scaled_train, train_y, sample_weight=sample_weights)
        validation_probabilities = classifier.predict_proba(
            scaler.transform(validation_x)
        )[:, 1]
        threshold = _select_validation_threshold(
            validation_y.tolist(), validation_probabilities.tolist()
        )
        test_probabilities = classifier.predict_proba(scaler.transform(test_x))[:, 1]
    validation_predictions = (validation_probabilities >= threshold).astype(int)
    test_predictions = (test_probabilities >= threshold).astype(int)
    return {
        "scaler_mean": scaler.mean_.astype(float).tolist(),
        "scaler_scale": scaler.scale_.astype(float).tolist(),
        "coefficients": classifier.coef_[0].astype(float).tolist(),
        "intercept": float(classifier.intercept_[0]),
        "decision_threshold": threshold,
        "validation_metrics": _classification_metrics(
            validation_y.tolist(),
            validation_predictions.tolist(),
            validation_probabilities.tolist(),
            timestamps=[row["timestamp"] for row in validation_rows],
        ),
        "test_metrics": _classification_metrics(
            test_y.tolist(),
            test_predictions.tolist(),
            test_probabilities.tolist(),
            timestamps=[row["timestamp"] for row in test_rows],
        ),
        "test_probabilities": test_probabilities.astype(float).tolist(),
    }


def _classification_metrics(
    labels: Sequence[int],
    predictions: Sequence[int],
    probabilities: Sequence[float],
    *,
    timestamps: Sequence[datetime] | None = None,
) -> dict[str, Any]:
    try:
        import numpy as np
        from sklearn.metrics import (  # type: ignore[import-untyped]
            average_precision_score,
            brier_score_loss,
            confusion_matrix,
            f1_score,
            log_loss,
            precision_score,
            recall_score,
            roc_auc_score,
        )
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "metric calculation requires numpy and scikit-learn"
        ) from exc
    y: Any = np.asarray(labels, dtype=int)
    predicted: Any = np.asarray(predictions, dtype=int)
    probability: Any = np.asarray(probabilities, dtype=float)
    matrix = confusion_matrix(y, predicted, labels=[0, 1])
    tn, fp, fn, tp = (int(value) for value in matrix.ravel())
    specificity = tn / (tn + fp) if tn + fp else 0.0
    sensitivity = tp / (tp + fn) if tp + fn else 0.0
    return {
        "sample_count": len(y),
        "positive_count": int(y.sum()),
        "pr_auc": float(average_precision_score(y, probability)),
        "roc_auc": float(roc_auc_score(y, probability)),
        "precision": float(precision_score(y, predicted, zero_division=0)),
        "recall": float(recall_score(y, predicted, zero_division=0)),
        "f1": float(f1_score(y, predicted, zero_division=0)),
        "specificity": float(specificity),
        "balanced_accuracy": float((sensitivity + specificity) / 2.0),
        "brier": float(brier_score_loss(y, probability)),
        "log_loss": float(log_loss(y, probability, labels=[0, 1])),
        "confusion": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        "false_positive_rows_per_day": _false_positive_rows_per_day(fp, timestamps),
        "reliability": _reliability_bins(labels, probabilities),
    }


def _classification_only_metrics(
    labels: Sequence[int],
    predictions: Sequence[int],
    *,
    timestamps: Sequence[datetime] | None = None,
) -> dict[str, Any]:
    try:
        from sklearn.metrics import (  # type: ignore[import-untyped]
            confusion_matrix,
            f1_score,
            precision_score,
            recall_score,
        )
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("metric calculation requires scikit-learn") from exc
    matrix = confusion_matrix(labels, predictions, labels=[0, 1])
    tn, fp, fn, tp = (int(value) for value in matrix.ravel())
    specificity = tn / (tn + fp) if tn + fp else 0.0
    sensitivity = tp / (tp + fn) if tp + fn else 0.0
    return {
        "sample_count": len(labels),
        "positive_count": sum(int(item) for item in labels),
        "precision": float(precision_score(labels, predictions, zero_division=0)),
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
        "specificity": float(specificity),
        "balanced_accuracy": float((sensitivity + specificity) / 2.0),
        "confusion": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        "false_positive_rows_per_day": _false_positive_rows_per_day(fp, timestamps),
        "pr_auc": None,
        "roc_auc": None,
        "brier": None,
        "log_loss": None,
        "reliability": None,
    }


def _false_positive_rows_per_day(
    false_positives: int, timestamps: Sequence[datetime] | None
) -> float | None:
    if timestamps is None or len(timestamps) < 2:
        return None
    ordered = sorted(timestamps)
    duration_days = (ordered[-1] - ordered[0]).total_seconds() / 86400.0
    if duration_days <= 0:
        return None
    return float(false_positives / duration_days)


def _reliability_bins(
    labels: Sequence[int], probabilities: Sequence[float], bins: int = 10
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        members = [
            (int(label), float(probability))
            for label, probability in zip(labels, probabilities, strict=True)
            if low <= probability < high or (index == bins - 1 and probability == 1.0)
        ]
        if not members:
            result.append(
                {
                    "lower": low,
                    "upper": high,
                    "count": 0,
                    "mean_probability": None,
                    "observed_fraction": None,
                }
            )
            continue
        result.append(
            {
                "lower": low,
                "upper": high,
                "count": len(members),
                "mean_probability": sum(item[1] for item in members) / len(members),
                "observed_fraction": sum(item[0] for item in members) / len(members),
            }
        )
    return result


def _select_validation_threshold(
    labels: Sequence[int], probabilities: Sequence[float]
) -> float:
    best: tuple[float, float, float, float] | None = None
    for index in range(10, 191):
        threshold = index / 200.0
        predictions = [int(value >= threshold) for value in probabilities]
        tp = sum(y == 1 and p == 1 for y, p in zip(labels, predictions, strict=True))
        fp = sum(y == 0 and p == 1 for y, p in zip(labels, predictions, strict=True))
        fn = sum(y == 1 and p == 0 for y, p in zip(labels, predictions, strict=True))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )
        score = (f1, recall, -abs(threshold - 0.5), -threshold)
        if best is None or score > best:
            best = score
            selected = threshold
    return float(selected)


def _select_water_level_threshold(rows: Sequence[Mapping[str, Any]]) -> float:
    values = sorted({float(row["relative_water_level_m"]) for row in rows})
    if len(values) > 500:
        values = [
            values[round(index * (len(values) - 1) / 499)] for index in range(500)
        ]
    labels = [int(row["target_extreme_water"]) for row in rows]
    best: tuple[float, float] | None = None
    selected = values[0]
    for threshold in values:
        predictions = [
            int(float(row["relative_water_level_m"]) >= threshold) for row in rows
        ]
        tp = sum(y == 1 and p == 1 for y, p in zip(labels, predictions, strict=True))
        fp = sum(y == 0 and p == 1 for y, p in zip(labels, predictions, strict=True))
        fn = sum(y == 1 and p == 0 for y, p in zip(labels, predictions, strict=True))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )
        score = (f1, -threshold)
        if best is None or score > best:
            best = score
            selected = threshold
    return float(selected)


def _per_site_water_level_baseline(
    validation_rows: Sequence[Mapping[str, Any]],
    test_rows: Sequence[Mapping[str, Any]],
    *,
    selected_site_ids: Sequence[str],
) -> dict[str, Any]:
    """Tune one datum-local hard threshold per site on validation only."""

    sites = tuple(selected_site_ids)
    thresholds: dict[str, float] = {}
    site_results: dict[str, Any] = {}
    threshold_ineligible_site_ids: list[str] = []
    evaluation_ineligible_site_ids: list[str] = []
    all_labels: list[int] = []
    all_predictions: list[int] = []
    all_timestamps: list[datetime] = []
    subset_labels: list[int] = []
    subset_predictions: list[int] = []
    subset_timestamps: list[datetime] = []

    for site_id in sites:
        site_validation = [
            row for row in validation_rows if str(row["site_id"]) == site_id
        ]
        site_test = [row for row in test_rows if str(row["site_id"]) == site_id]
        validation_labels = {
            int(row["target_extreme_water"]) for row in site_validation
        }
        if validation_labels != {0, 1}:
            threshold_ineligible_site_ids.append(site_id)
            site_results[site_id] = {
                "available": False,
                "threshold_available": False,
                "sample_count": len(site_test),
                "reason": (
                    "both classes are required in validation to select this "
                    "site's water-level threshold"
                ),
            }
            continue

        threshold = _select_water_level_threshold(site_validation)
        thresholds[site_id] = threshold
        labels = [int(row["target_extreme_water"]) for row in site_test]
        predictions = [
            int(float(row["relative_water_level_m"]) >= threshold) for row in site_test
        ]
        timestamps = [row["timestamp"] for row in site_test]
        all_labels.extend(labels)
        all_predictions.extend(predictions)
        all_timestamps.extend(timestamps)
        subset_labels.extend(labels)
        subset_predictions.extend(predictions)
        subset_timestamps.extend(timestamps)
        if set(labels) != {0, 1}:
            evaluation_ineligible_site_ids.append(site_id)
            site_results[site_id] = {
                "available": False,
                "threshold_available": True,
                "threshold_m": threshold,
                "threshold_selection_split": "validation",
                "sample_count": len(labels),
                "reason": (
                    "both classes are required in frozen_test for fair "
                    "per-site evaluation"
                ),
            }
            continue
        site_results[site_id] = {
            "available": True,
            "threshold_available": True,
            "threshold_m": threshold,
            "threshold_selection_split": "validation",
            **_classification_only_metrics(
                labels,
                predictions,
                timestamps=timestamps,
            ),
        }

    threshold_complete = not threshold_ineligible_site_ids
    eligible_site_ids = [
        site_id for site_id in sites if bool(site_results[site_id].get("available"))
    ]
    ineligible_site_ids = [
        site_id for site_id in sites if site_id not in eligible_site_ids
    ]
    available = [site_results[site_id] for site_id in eligible_site_ids]
    eligible_subset_macro = _macro_average_metrics(
        available,
        (
            "precision",
            "recall",
            "f1",
            "specificity",
            "balanced_accuracy",
            "false_positive_rows_per_day",
        ),
    )
    if eligible_subset_macro is not None:
        eligible_subset_macro.update(
            {
                "pr_auc": None,
                "roc_auc": None,
                "brier": None,
                "log_loss": None,
                "reliability": None,
            }
        )
    complete_coverage = threshold_complete and not evaluation_ineligible_site_ids
    frozen_test = (
        _classification_only_metrics(
            all_labels,
            all_predictions,
            timestamps=all_timestamps,
        )
        if threshold_complete
        else None
    )
    eligible_subset_frozen_test = None
    if subset_labels:
        eligible_subset_frozen_test = _classification_only_metrics(
            subset_labels,
            subset_predictions,
            timestamps=subset_timestamps,
        )
    coverage = {
        "selected_site_count": len(sites),
        "eligible_site_count": len(eligible_site_ids),
        "eligible_site_ids": eligible_site_ids,
        "ineligible_site_ids": ineligible_site_ids,
        "threshold_ineligible_site_ids": threshold_ineligible_site_ids,
        "evaluation_ineligible_site_ids": evaluation_ineligible_site_ids,
        "coverage_fraction": len(eligible_site_ids) / len(sites) if sites else 0.0,
        "complete_coverage": complete_coverage,
    }
    return {
        "name": "validation_selected_per_site_water_level_threshold",
        "threshold_selection_split": "validation",
        "threshold_selected_on": "validation",
        "per_site_thresholds": thresholds,
        "frozen_test_rows_used_for_threshold": 0,
        "probability_metrics": "not_applicable_hard_classifier",
        "selected_site_coverage": coverage,
        "frozen_test": frozen_test,
        "eligible_subset_frozen_test": eligible_subset_frozen_test,
        "per_site_frozen_test": {
            "sites": site_results,
            **coverage,
            "macro_average": (eligible_subset_macro if complete_coverage else None),
            "eligible_subset_macro_average": eligible_subset_macro,
            "subset_macro_is_primary": False,
        },
    }


def _metric_delta_vs_threshold(
    row_model_metrics: Mapping[str, Any],
    row_threshold_metrics: Mapping[str, Any] | None,
    model_site_macro: Mapping[str, Any] | None,
    threshold_site_macro: Mapping[str, Any] | None,
    *,
    complete_coverage: bool,
) -> dict[str, Any]:
    probability_note = (
        "PR-AUC, ROC-AUC, Brier, log-loss and reliability are not compared "
        "because the threshold baseline emits no probabilities."
    )
    if (
        not complete_coverage
        or row_threshold_metrics is None
        or model_site_macro is None
        or threshold_site_macro is None
    ):
        return {
            "available": False,
            "comparison_level": "site_macro_frozen_test",
            "selected_site_coverage_complete": False,
            "comparable_metric_deltas_model_minus_threshold": None,
            "site_macro_comparable_metric_deltas_model_minus_threshold": None,
            "row_level_comparable_metric_deltas_model_minus_threshold": None,
            "probability_metric_deltas": None,
            "probability_metric_note": probability_note,
            "verdict": None,
            "professor_summary": (
                "No fair model-versus-threshold verdict is available because "
                "the validation-selected per-site baseline does not cover every "
                "selected site on the frozen test."
            ),
        }

    higher_is_better = (
        "precision",
        "recall",
        "f1",
        "specificity",
        "balanced_accuracy",
    )
    site_macro_deltas: dict[str, float] = {
        name: float(model_site_macro[name]) - float(threshold_site_macro[name])
        for name in higher_is_better
    }
    row_level_deltas: dict[str, float | None] = {
        name: float(row_model_metrics[name]) - float(row_threshold_metrics[name])
        for name in higher_is_better
    }
    site_macro_delta_values: dict[str, float | None] = dict(site_macro_deltas)
    model_false_alarms = model_site_macro.get("false_positive_rows_per_day")
    threshold_false_alarms = threshold_site_macro.get("false_positive_rows_per_day")
    site_macro_delta_values["false_positive_rows_per_day"] = (
        None
        if model_false_alarms is None or threshold_false_alarms is None
        else float(model_false_alarms) - float(threshold_false_alarms)
    )
    row_model_false_alarms = row_model_metrics.get("false_positive_rows_per_day")
    row_threshold_false_alarms = row_threshold_metrics.get(
        "false_positive_rows_per_day"
    )
    row_level_deltas["false_positive_rows_per_day"] = (
        None
        if row_model_false_alarms is None or row_threshold_false_alarms is None
        else float(row_model_false_alarms) - float(row_threshold_false_alarms)
    )
    false_alarm_not_worse = (
        site_macro_delta_values["false_positive_rows_per_day"] is None
        or float(site_macro_delta_values["false_positive_rows_per_day"]) <= 0
    )
    f1_delta = float(site_macro_delta_values["f1"] or 0.0)
    recall_delta = float(site_macro_delta_values["recall"] or 0.0)
    if f1_delta > 0 and recall_delta >= 0 and false_alarm_not_worse:
        verdict = "outperforms_threshold_on_comparable_frozen_test_metrics"
        professor_summary = (
            "The logistic model demonstrates added value over the water-level "
            "threshold on this frozen test, subject to the stated evidence tier."
        )
    elif all(value <= 0 for value in site_macro_deltas.values()):
        verdict = "no_demonstrated_gain_threshold_is_sufficient"
        professor_summary = (
            "No added value is demonstrated here; the simpler water-level "
            "threshold is sufficient on this frozen test."
        )
    else:
        verdict = "mixed_no_clear_demonstrated_gain"
        professor_summary = (
            "Results are mixed, so this run does not yet demonstrate that the "
            "logistic model is preferable to the simpler threshold."
        )
    return {
        "available": True,
        "comparison_level": "site_macro_frozen_test",
        "selected_site_coverage_complete": True,
        "comparable_metric_deltas_model_minus_threshold": site_macro_delta_values,
        "site_macro_comparable_metric_deltas_model_minus_threshold": (
            site_macro_delta_values
        ),
        "row_level_comparable_metric_deltas_model_minus_threshold": (row_level_deltas),
        "probability_metric_deltas": None,
        "probability_metric_note": probability_note,
        "verdict": verdict,
        "professor_summary": professor_summary,
    }


def _per_site_metrics(
    test_rows: Sequence[Mapping[str, Any]],
    probabilities: Sequence[float],
    threshold: float,
) -> dict[str, Any]:
    site_results: dict[str, Any] = {}
    selected_site_ids = sorted({str(row["site_id"]) for row in test_rows})
    for site_id in selected_site_ids:
        indexes = [
            index for index, row in enumerate(test_rows) if row["site_id"] == site_id
        ]
        labels = [int(test_rows[index]["target_extreme_water"]) for index in indexes]
        site_probabilities = [float(probabilities[index]) for index in indexes]
        if set(labels) != {0, 1}:
            site_results[site_id] = {
                "available": False,
                "sample_count": len(labels),
                "reason": "both classes are required for per-site ROC-AUC",
            }
            continue
        predictions = [int(value >= threshold) for value in site_probabilities]
        site_results[site_id] = {
            "available": True,
            **_classification_metrics(
                labels,
                predictions,
                site_probabilities,
                timestamps=[test_rows[index]["timestamp"] for index in indexes],
            ),
        }
    eligible_site_ids = [
        site_id for site_id, item in site_results.items() if item.get("available")
    ]
    ineligible_site_ids = [
        site_id for site_id in selected_site_ids if site_id not in eligible_site_ids
    ]
    available = [site_results[site_id] for site_id in eligible_site_ids]
    eligible_subset_macro = _macro_average_metrics(
        available,
        (
            "pr_auc",
            "roc_auc",
            "precision",
            "recall",
            "f1",
            "specificity",
            "balanced_accuracy",
            "false_positive_rows_per_day",
        ),
    )
    complete_coverage = not ineligible_site_ids
    return {
        "sites": site_results,
        "selected_site_count": len(selected_site_ids),
        "eligible_site_count": len(eligible_site_ids),
        "eligible_site_ids": eligible_site_ids,
        "ineligible_site_ids": ineligible_site_ids,
        "coverage_fraction": (
            len(eligible_site_ids) / len(selected_site_ids)
            if selected_site_ids
            else 0.0
        ),
        "complete_coverage": complete_coverage,
        "macro_average": eligible_subset_macro if complete_coverage else None,
        "eligible_subset_macro_average": eligible_subset_macro,
        "subset_macro_is_primary": False,
    }


def _macro_average_metrics(
    metrics: Sequence[Mapping[str, Any]], metric_names: Sequence[str]
) -> dict[str, float | None] | None:
    if not metrics:
        return None
    result: dict[str, float | None] = {}
    for name in metric_names:
        values = [item.get(name) for item in metrics]
        if any(value is None for value in values):
            result[name] = None
            continue
        total = 0.0
        for value in values:
            if value is None:  # narrowed above; retained for static type checkers
                raise AssertionError("unreachable missing macro metric")
            total += float(value)
        result[name] = total / len(values)
    return result


def _validate_sensor_contexts(
    value: Any, source_manifest: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    site_ids = source_manifest.get("site_ids")
    if not isinstance(site_ids, list) or not site_ids:
        raise OfficialModelError("source_manifest.site_ids is invalid")
    if not isinstance(value, list) or len(value) != len(site_ids):
        raise OfficialModelError("artifact requires one sensor context per site")
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for context in value:
        if not isinstance(context, Mapping):
            raise OfficialModelError("sensor test context must be an object")
        site_id = context.get("site_id")
        if not isinstance(site_id, str) or site_id not in site_ids or site_id in seen:
            raise OfficialModelError("sensor test context site coverage is invalid")
        seen.add(site_id)
        if context.get("source_split") not in {"validation", "frozen_test"}:
            raise OfficialModelError("sensor context must come from a holdout split")
        for key in (
            "context_id",
            "timestamp",
            "datum",
            "source_row_sha256",
            "dataset_registration_sha256",
        ):
            if not isinstance(context.get(key), str) or not context[key]:
                raise OfficialModelError(f"sensor context {key} is required")
        features = context.get("features")
        expected = set(OFFICIAL_FEATURE_ORDER) - {"relative_water_level_m"}
        if not isinstance(features, Mapping) or set(features) != expected:
            raise OfficialModelError("sensor context non-water feature set is invalid")
        for name in expected:
            _finite_number(features[name], f"sensor context features.{name}")
        result.append(context)
    return tuple(result)


def _sensor_mapping_reference(
    train_rows: Sequence[Mapping[str, Any]], site_id: str
) -> dict[str, Any]:
    levels = sorted(
        float(row["relative_water_level_m"])
        for row in train_rows
        if row["site_id"] == site_id
    )
    if not levels:
        raise OfficialModelError(f"no official training levels for site {site_id}")
    encoded = json.dumps(levels, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {
        "site_id": site_id,
        "official_train_q05_m": _linear_quantile(levels, 0.05),
        "official_train_q95_m": _linear_quantile(levels, 0.95),
        "official_train_level_count": len(levels),
        "official_train_levels_sha256": hashlib.sha256(encoded).hexdigest(),
        "source_split": "train",
    }


def _validate_mapping_references(
    value: Any, source_manifest: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    site_ids = source_manifest.get("site_ids")
    if not isinstance(site_ids, list) or not isinstance(value, list):
        raise OfficialModelError("sensor mapping references are invalid")
    if len(value) != len(site_ids):
        raise OfficialModelError("artifact requires one mapping reference per site")
    seen: set[str] = set()
    result: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise OfficialModelError("sensor mapping reference must be an object")
        site_id = item.get("site_id")
        if not isinstance(site_id, str) or site_id not in site_ids or site_id in seen:
            raise OfficialModelError(
                "sensor mapping reference site coverage is invalid"
            )
        seen.add(site_id)
        low = _finite_number(item.get("official_train_q05_m"), "mapping q05")
        high = _finite_number(item.get("official_train_q95_m"), "mapping q95")
        if high <= low:
            raise OfficialModelError("official train q95 must exceed q05")
        count = item.get("official_train_level_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise OfficialModelError("official train level count is invalid")
        digest = item.get("official_train_levels_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise OfficialModelError("official train level hash is invalid")
        if item.get("source_split") != "train":
            raise OfficialModelError("sensor mapping range must come from train split")
        result.append(item)
    return tuple(result)


def _validate_feature_ranges(value: Any) -> Mapping[str, Mapping[str, float]]:
    if not isinstance(value, Mapping) or set(value) != set(OFFICIAL_FEATURE_ORDER):
        raise OfficialModelError("training_feature_ranges is invalid")
    result: dict[str, Mapping[str, float]] = {}
    for name in OFFICIAL_FEATURE_ORDER:
        bounds = value[name]
        if not isinstance(bounds, Mapping):
            raise OfficialModelError(f"training_feature_ranges.{name} is invalid")
        minimum = _finite_number(bounds.get("min"), f"{name}.min")
        maximum = _finite_number(bounds.get("max"), f"{name}.max")
        if maximum < minimum:
            raise OfficialModelError(f"training_feature_ranges.{name} is reversed")
        result[name] = {"min": minimum, "max": maximum}
    return result


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


def _normalise_selected_sites(
    dataset: RegisteredOfficialDataset,
    selected_site_ids: Sequence[str] | None,
) -> tuple[str, ...]:
    available = tuple(dataset.manifest["site_ids"])
    if selected_site_ids is None:
        return available
    if isinstance(selected_site_ids, (str, bytes)) or not selected_site_ids:
        raise OfficialModelError("selected_site_ids must be a non-empty sequence")
    sites = tuple(dict.fromkeys(str(item).strip() for item in selected_site_ids))
    unknown = sorted(set(sites) - set(available))
    if any(not item for item in sites) or unknown:
        raise OfficialModelError(
            "selected_site_ids contains unknown or empty values"
            + (f": {', '.join(unknown)}" if unknown else "")
        )
    return sites


def _event_storm_groups(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    background = {"", "none", "non-event", "non_event", "background", "normal"}
    return {
        str(row["storm_group_id"]).strip()
        for row in rows
        if str(row["storm_group_id"]).strip().lower() not in background
    }


def _class_counts(labels: Any) -> dict[str, int]:
    safe = 0
    extreme = 0
    for label in labels:
        if int(label) == 1:
            extreme += 1
        else:
            safe += 1
    return {"safe": safe, "extreme_water": extreme}


def _site_class_balanced_weights(
    rows: Sequence[Mapping[str, Any]],
) -> list[float]:
    """Give every site equal mass, and both classes equal mass within a site."""

    counts: dict[tuple[str, int], int] = {}
    classes_by_site: dict[str, set[int]] = {}
    for row in rows:
        site_id = str(row["site_id"])
        label = int(row["target_extreme_water"])
        counts[(site_id, label)] = counts.get((site_id, label), 0) + 1
        classes_by_site.setdefault(site_id, set()).add(label)
    site_mass = 1.0 / len(classes_by_site)
    return [
        site_mass
        / len(classes_by_site[str(row["site_id"])])
        / counts[(str(row["site_id"]), int(row["target_extreme_water"]))]
        for row in rows
    ]


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
            raise OfficialModelError("created_at must be ISO-8601") from exc
    else:
        raise OfficialModelError("created_at must be a datetime or ISO-8601 string")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _number_sequence(value: Any, size: int, path: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != size:
        raise OfficialModelError(f"{path} must contain {size} numbers")
    return tuple(
        _finite_number(item, f"{path}[{index}]") for index, item in enumerate(value)
    )


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OfficialModelError(f"{path} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise OfficialModelError(f"{path} must be a finite number")
    return result


def _mapping_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _json_mapping_copy(value: Mapping[str, Any], path: str) -> dict[str, Any]:
    try:
        decoded = json.loads(canonical_json_bytes(value))
    except (TypeError, ValueError) as exc:
        raise OfficialModelError(f"{path} is not canonical JSON data") from exc
    if not isinstance(decoded, dict):
        raise OfficialModelError(f"{path} must be an object")
    return decoded


def _write_artifact_atomic(path: Path, artifact: Mapping[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


__all__ = [
    "ARTIFACT_SCHEMA",
    "CLASS_ORDER",
    "MODEL_ID",
    "LoadedOfficialModel",
    "OfficialModelError",
    "artifact_sha256",
    "assess_official_training_data",
    "load_official_model",
    "train_official_model",
    "training_in_progress",
]
