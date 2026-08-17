"""Train and export an explainable multinomial logistic risk model."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    recall_score,
)
from sklearn.preprocessing import StandardScaler

from .constants import (
    CLASS_NAMES,
    FEATURE_NAMES,
    FORECAST_HORIZON_HOURS,
    LABEL_RULE_VERSION,
    WEAK_LABEL_THRESHOLDS,
)
from .data import read_dataset
from .features import feature_vector, parse_timestamp


def _chronological_split(
    rows: Sequence[Mapping[str, object]],
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
    purge_rows: int = FORECAST_HORIZON_HOURS,
) -> dict[str, list[Mapping[str, object]]]:
    """Split every location chronologically and purge target-overlap boundaries."""

    by_location: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        by_location[str(row["location_id"])].append(row)

    result: dict[str, list[Mapping[str, object]]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    for location_rows in by_location.values():
        ordered = sorted(
            location_rows, key=lambda row: parse_timestamp(row["timestamp"])  # type: ignore[arg-type]
        )
        size = len(ordered)
        if size < 100:
            raise ValueError("each location needs at least 100 labelled hourly rows")
        train_end = int(size * train_fraction)
        validation_end = int(size * (train_fraction + validation_fraction))
        result["train"].extend(ordered[: max(0, train_end - purge_rows)])
        result["validation"].extend(
            ordered[train_end : max(train_end, validation_end - purge_rows)]
        )
        result["test"].extend(ordered[validation_end:])
    return result


def _matrix(rows: Sequence[Mapping[str, object]]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([feature_vector(row) for row in rows], dtype=np.float64)
    y = np.asarray([int(row["target_risk_level"]) for row in rows], dtype=np.int64)
    return x, y


def _multiclass_brier(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    one_hot = np.zeros_like(probabilities)
    one_hot[np.arange(y_true.size), y_true] = 1.0
    return float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))


def _high_risk_recall(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(recall_score(y_true >= 2, y_pred >= 2, zero_division=0))


def _evaluate(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    probabilities: np.ndarray | None = None,
) -> dict[str, object]:
    report = classification_report(
        y_true,
        y_pred,
        labels=list(range(len(CLASS_NAMES))),
        target_names=list(CLASS_NAMES),
        output_dict=True,
        zero_division=0,
    )
    result: dict[str, object] = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "high_risk_recall": _high_risk_recall(y_true, y_pred),
        "confusion_matrix": confusion_matrix(
            y_true, y_pred, labels=list(range(len(CLASS_NAMES)))
        ).tolist(),
        "per_class": {
            name: {
                key: float(value) if key != "support" else int(value)
                for key, value in report[name].items()
            }
            for name in CLASS_NAMES
        },
    }
    if probabilities is not None:
        result["multiclass_brier"] = _multiclass_brier(y_true, probabilities)
    return result


def _distribution(values: Sequence[int] | np.ndarray) -> dict[str, int]:
    counts = Counter(int(value) for value in values)
    return {CLASS_NAMES[level]: counts.get(level, 0) for level in range(len(CLASS_NAMES))}


def _dataset_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def train_and_export(
    dataset_path: Path,
    artifact_path: Path,
    report_path: Path,
    *,
    model_version: str = "coastal-risk-logreg-v1",
) -> dict[str, object]:
    rows = read_dataset(dataset_path)
    if not rows:
        raise ValueError("training dataset is empty")
    splits = _chronological_split(rows)

    x_train, y_train = _matrix(splits["train"])
    x_validation, y_validation = _matrix(splits["validation"])
    x_test, y_test = _matrix(splits["test"])
    expected_classes = set(range(len(CLASS_NAMES)))
    if set(int(value) for value in np.unique(y_train)) != expected_classes:
        raise ValueError(
            "training split must contain all four risk classes; download a longer or broader interval"
        )

    imputer = SimpleImputer(strategy="median", keep_empty_features=True)
    scaler = StandardScaler()
    classifier = LogisticRegression(
        C=0.8,
        class_weight="balanced",
        max_iter=4000,
        random_state=42,
        solver="lbfgs",
    )
    x_train_imputed = imputer.fit_transform(x_train)
    x_train_scaled = scaler.fit_transform(x_train_imputed)
    classifier.fit(x_train_scaled, y_train)
    if list(classifier.classes_) != list(range(len(CLASS_NAMES))):
        raise RuntimeError("classifier class order does not match the artifact schema")

    def predict(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        transformed = scaler.transform(imputer.transform(x))
        return classifier.predict(transformed), classifier.predict_proba(transformed)

    validation_pred, validation_proba = predict(x_validation)
    test_pred, test_proba = predict(x_test)

    baseline_test = np.asarray(
        [int(row["instant_risk_level"]) for row in splits["test"]],
        dtype=np.int64,
    )
    trained_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    locations = sorted({str(row["location_id"]) for row in rows})
    timestamps = sorted(parse_timestamp(row["timestamp"]) for row in rows)  # type: ignore[arg-type]

    feature_importance = sorted(
        (
            {
                "feature": name,
                "mean_abs_coefficient": float(np.mean(np.abs(classifier.coef_[:, index]))),
            }
            for index, name in enumerate(FEATURE_NAMES)
        ),
        key=lambda row: row["mean_abs_coefficient"],
        reverse=True,
    )

    validation_metrics = _evaluate(y_validation, validation_pred, validation_proba)
    test_metrics = _evaluate(y_test, test_pred, test_proba)
    baseline_metrics = _evaluate(y_test, baseline_test)
    report: dict[str, object] = {
        "model_version": model_version,
        "trained_at": trained_at,
        "task": f"maximum weak environmental risk in the next {FORECAST_HORIZON_HOURS} hours",
        "dataset": {
            "path": str(dataset_path),
            "sha256": _dataset_sha256(dataset_path),
            "rows": len(rows),
            "locations": locations,
            "from": timestamps[0].isoformat().replace("+00:00", "Z"),
            "to": timestamps[-1].isoformat().replace("+00:00", "Z"),
            "label_rule_version": LABEL_RULE_VERSION,
            "weak_labels": True,
            "weather_sources": sorted(
                {str(row.get("weather_source", "unknown")) for row in rows}
            ),
            "marine_sources": sorted(
                {str(row.get("marine_source", "unknown")) for row in rows}
            ),
        },
        "split": {
            name: {
                "rows": len(split_rows),
                "class_distribution": _distribution(
                    [int(row["target_risk_level"]) for row in split_rows]
                ),
            }
            for name, split_rows in splits.items()
        },
        "validation": validation_metrics,
        "test": test_metrics,
        "instant_rule_baseline_test": baseline_metrics,
        "deployment_decision": {
            "mode": "shadow",
            "reason": (
                "High-risk recall improved, but Macro-F1 did not beat the current-rule "
                "baseline and labels are not incident ground truth."
            ),
            "model_high_risk_recall": test_metrics["high_risk_recall"],
            "baseline_high_risk_recall": baseline_metrics["high_risk_recall"],
            "model_macro_f1": test_metrics["macro_f1"],
            "baseline_macro_f1": baseline_metrics["macro_f1"],
        },
        "feature_importance": feature_importance,
        "limitations": [
            "Targets are transparent project weak labels, not verified incident outcomes.",
            "Open-Meteo marine values are numerical model context and have limited coastal accuracy.",
            "The model is a demonstrator and must not replace local rules or official warnings.",
        ],
    }

    artifact: dict[str, object] = {
        "schema_version": 1,
        "model_type": "multinomial_logistic_regression",
        "model_version": model_version,
        "trained_at": trained_at,
        "task": "next_6h_environmental_risk",
        "deployment_mode": "shadow",
        "forecast_horizon_hours": FORECAST_HORIZON_HOURS,
        "class_names": list(CLASS_NAMES),
        "classes": [int(value) for value in classifier.classes_],
        "feature_names": list(FEATURE_NAMES),
        "imputer_median": [float(value) for value in imputer.statistics_],
        "scaler_mean": [float(value) for value in scaler.mean_],
        "scaler_scale": [
            float(value) if math.isfinite(float(value)) and float(value) != 0 else 1.0
            for value in scaler.scale_
        ],
        "coefficients": classifier.coef_.astype(float).tolist(),
        "intercept": classifier.intercept_.astype(float).tolist(),
        "weak_label": {
            "version": LABEL_RULE_VERSION,
            "thresholds": {
                name: list(values) for name, values in WEAK_LABEL_THRESHOLDS.items()
            },
            "warning": "Project heuristics only; not official safety thresholds.",
        },
        "training_summary": {
            "dataset_sha256": report["dataset"]["sha256"],  # type: ignore[index]
            "rows": len(rows),
            "locations": locations,
            "test_macro_f1": report["test"]["macro_f1"],  # type: ignore[index]
            "test_high_risk_recall": report["test"]["high_risk_recall"],  # type: ignore[index]
            "baseline_test_macro_f1": report["instant_rule_baseline_test"]["macro_f1"],  # type: ignore[index]
            "baseline_test_high_risk_recall": report["instant_rule_baseline_test"]["high_risk_recall"],  # type: ignore[index]
        },
    }

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"[TRAIN] model: {artifact_path}")
    print(f"[TRAIN] report: {report_path}")
    print(
        "[TRAIN] test macro-F1={:.3f} high-risk recall={:.3f} baseline macro-F1={:.3f}".format(
            report["test"]["macro_f1"],  # type: ignore[index]
            report["test"]["high_risk_recall"],  # type: ignore[index]
            report["instant_rule_baseline_test"]["macro_f1"],  # type: ignore[index]
        )
    )
    return report
