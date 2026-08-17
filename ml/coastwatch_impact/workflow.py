"""Fail-closed artifact workflows used by the general ``cwml`` commands.

The contracts in this module deliberately keep dataset construction, fitting,
validation selection, frozen testing, and bundle export as separate steps.  A
missing parent artifact or a mismatched hash is an error; no step invents data,
labels, probabilities, calibration, or model state.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import numpy as np
import pandas as pd
import torch
from numpy.typing import NDArray
from safetensors.torch import load_file, save_file
from torch.utils.data import DataLoader

from .config import ImpactConfig, load_config
from .data import (
    CoastWatchWindowDataset,
    GlobalSplitConfig,
    TrainOnlyPreprocessor,
    assign_global_time_split,
    audit_global_split,
    build_hazard_labels,
    build_leave_one_site_out_folds,
    collate_window_batch,
    sample_training_rows,
    validate_event_catalog_frame,
    validate_forecasts_frame,
    validate_observations_frame,
    validate_sites_frame,
    validate_static_features_frame,
)
from .evaluation import (
    TemperatureScaler,
    compute_horizon_metrics,
    cumulative_event_probability,
    evaluate_alert_events,
    fit_global_temperature,
    select_operating_thresholds,
    summarize_leave_one_site_out,
    water_quantile_metrics,
)
from .export.model_bundle import create_model_bundle
from .models.baselines import LogisticEventBaseline, build_logistic_summary_features
from .models.impactnet import ImpactNet, ImpactNetConfig
from .training import ImpactTrainer, PredictionBatch

DATASET_SCHEMA_VERSION = "impactnet-dataset-v1"
RUN_SCHEMA_VERSION = "impactnet-run-v1"
SELECTION_SCHEMA_VERSION = "impactnet-selection-v1"
FINAL_TEST_SCHEMA_VERSION = "impactnet-final-test-v1"

CANONICAL_TABLES = (
    "sites",
    "observations_hourly",
    "forecasts_hourly",
    "static_features",
    "event_catalog",
)
LEAD_FEATURE_NAMES = (
    "hour_sin",
    "hour_cos",
    "day_of_year_sin",
    "day_of_year_cos",
    "lead_hour_normalized",
)


class ArtifactContractError(ValueError):
    """An artifact is missing, mutable, incompatible, or scientifically unsafe."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if value is pd.NA or (isinstance(value, float) and not np.isfinite(value)):
        return None
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ArtifactContractError(f"JSON root must be an object: {path}")
    return payload


def _new_staging(destination: Path) -> Path:
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))


def _commit_staging(staging: Path, destination: Path) -> Path:
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {destination}")
    staging.rename(destination)
    return destination


def _read_frame(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(source)
    if suffix == ".csv":
        return pd.read_csv(source)
    if suffix == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("records")
        if not isinstance(payload, list):
            raise ArtifactContractError("JSON event catalog must be a list or {records: [...]} ")
        return pd.DataFrame(payload)
    raise ArtifactContractError(f"unsupported table extension: {source.suffix}")


def validate_event_catalog_artifact(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    frame = validate_event_catalog_frame(_read_frame(source))
    counts = frame["label_confidence"].astype(str).value_counts().sort_index().to_dict()
    primary_sources = sorted(frame["primary_source"].astype(str).unique())
    return {
        "path": str(source),
        "sha256": sha256_file(source),
        "rows": int(len(frame)),
        "confidence_counts": {str(key): int(value) for key, value in counts.items()},
        "human_reviewed_rows": int(frame["human_reviewed"].astype(bool).sum()),
        "confirmed_impact_rows": int(frame["impact_confirmed"].eq(True).sum()),  # noqa: E712
        "primary_sources": primary_sources,
        "valid": True,
    }


def build_event_catalog_artifact(
    input_path: str | Path,
    output_path: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Normalise a manually curated canonical catalogue; never infer impacts."""

    source = Path(input_path).resolve()
    destination = Path(output_path).resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite event catalog: {destination}")
    frame = validate_event_catalog_frame(_read_frame(source))
    frame = frame.sort_values(
        ["coastal_zone_id", "onset_time_utc", "event_id"],
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)
    if dry_run:
        return {
            "planned": True,
            "input": str(source),
            "output": str(destination),
            "rows": int(len(frame)),
            "inferred_events": 0,
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.{uuid4().hex}")
    try:
        frame.to_parquet(staging, index=False, engine="pyarrow")
        staging.replace(destination)
    finally:
        staging.unlink(missing_ok=True)
    manifest = {
        "schema_version": "impactnet-event-catalog-v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_path": str(source),
        "source_sha256": sha256_file(source),
        "output_sha256": sha256_file(destination),
        "rows": int(len(frame)),
        "impact_events_inferred": 0,
        "construction": "normalised_manually_curated_canonical_records",
    }
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    _write_json(manifest_path, manifest)
    return {**manifest, "output": str(destination), "manifest": str(manifest_path)}


def _feature_schema(payload: dict[str, Any], tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    fields = {
        "past_feature_names": "observations_hourly",
        "future_feature_names": "forecasts_hourly",
        "static_feature_names": "static_features",
    }
    for key, table_name in fields.items():
        values = payload.get(key)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise ArtifactContractError(f"feature schema {key} must be a non-empty string list")
        names = [str(value) for value in values]
        if len(set(names)) != len(names):
            raise ArtifactContractError(f"feature schema {key} contains duplicates")
        missing = set(names).difference(tables[table_name].columns)
        if missing:
            raise ArtifactContractError(
                f"{table_name} is missing configured features: {sorted(missing)}"
            )
        for name in names:
            numeric = pd.to_numeric(tables[table_name][name], errors="coerce")
            if numeric.notna().sum() == 0:
                raise ArtifactContractError(
                    f"configured feature {table_name}.{name} has no numeric observations"
                )
        output[key] = names
    output["water_target_column"] = str(payload.get("water_target_column", "water_level_m_aod"))
    if output["water_target_column"] not in tables["observations_hourly"]:
        raise ArtifactContractError("water_target_column is absent from observations_hourly")
    physics = payload.get("physics_baseline_column", "forecast_total_water_level_m_aod")
    if physics is not None and str(physics) not in tables["forecasts_hourly"]:
        raise ArtifactContractError("physics_baseline_column is absent from forecasts_hourly")
    output["physics_baseline_column"] = None if physics is None else str(physics)
    source_model = payload.get("source_model")
    if source_model is not None:
        source_model = str(source_model)
        if source_model not in set(tables["forecasts_hourly"]["source_model"].astype(str)):
            raise ArtifactContractError(f"configured source_model is absent: {source_model!r}")
    output["source_model"] = source_model
    future_sources = payload.get("future_feature_sources")
    if future_sources is None:
        available_source_names = sorted(
            tables["forecasts_hourly"]["source_model"].dropna().astype(str).unique()
        )
        resolved_source = source_model or (
            available_source_names[0] if len(available_source_names) == 1 else None
        )
        if resolved_source is None:
            raise ArtifactContractError(
                "multiple forecast sources require explicit future_feature_sources mapping"
            )
        future_sources = {name: resolved_source for name in output["future_feature_names"]}
    if not isinstance(future_sources, dict) or set(future_sources) != set(
        output["future_feature_names"]
    ):
        raise ArtifactContractError(
            "future_feature_sources must map every configured future feature exactly once"
        )
    available_source_set = set(tables["forecasts_hourly"]["source_model"].astype(str))
    if any(
        not isinstance(value, str) or not value or value not in available_source_set
        for value in future_sources.values()
    ):
        raise ArtifactContractError(
            "future_feature_sources references an empty or unavailable source_model"
        )
    output["future_feature_sources"] = {
        str(name): str(value) for name, value in future_sources.items()
    }
    output["lead_feature_names"] = list(LEAD_FEATURE_NAMES)
    return output


def _load_canonical_source(input_directory: Path) -> dict[str, pd.DataFrame]:
    validators = {
        "sites": validate_sites_frame,
        "observations_hourly": validate_observations_frame,
        "forecasts_hourly": validate_forecasts_frame,
        "static_features": validate_static_features_frame,
        "event_catalog": validate_event_catalog_frame,
    }
    tables: dict[str, pd.DataFrame] = {}
    for name, validator in validators.items():
        path = input_directory / f"{name}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"canonical source table is missing: {path}")
        tables[name] = validator(pd.read_parquet(path))
    active_sites = tables["sites"].loc[tables["sites"]["active"].astype(bool)].copy()
    if active_sites.empty:
        raise ArtifactContractError("canonical source has no active sites")
    site_to_zone = active_sites.set_index("site_id")["coastal_zone_id"].astype(str).to_dict()
    observations = tables["observations_hourly"]
    unknown_observation_sites = set(observations["site_id"].astype(str)) - set(site_to_zone)
    if unknown_observation_sites:
        raise ArtifactContractError(
            f"observations reference inactive/unknown sites: {sorted(unknown_observation_sites)}"
        )
    mapped_zones = observations["site_id"].astype(str).map(site_to_zone)
    mismatch = mapped_zones != observations["coastal_zone_id"].astype(str)
    if mismatch.any():
        raise ArtifactContractError("observation site/coastal-zone mapping disagrees with sites")
    unknown_forecast_sites = set(tables["forecasts_hourly"]["site_id"].astype(str)) - set(
        site_to_zone
    )
    if unknown_forecast_sites:
        raise ArtifactContractError(
            f"forecasts reference inactive/unknown sites: {sorted(unknown_forecast_sites)}"
        )
    zones = set(active_sites["coastal_zone_id"].astype(str))
    missing_static = zones - set(tables["static_features"]["coastal_zone_id"].astype(str))
    if missing_static:
        raise ArtifactContractError(f"active zones lack static features: {sorted(missing_static)}")
    unknown_event_zones = set(tables["event_catalog"]["coastal_zone_id"].astype(str)) - zones
    if unknown_event_zones:
        raise ArtifactContractError(
            f"event catalog references unknown zones: {sorted(unknown_event_zones)}"
        )
    return tables


def _global_split_config(config: ImpactConfig) -> GlobalSplitConfig:
    return GlobalSplitConfig(
        train_end_utc=config.split.train_end,
        validation_end_utc=config.split.validation_end,
        test_end_utc=config.split.test_end,
        forecast_horizon_hours=config.windows.forecast_hours,
        event_buffer_hours=config.split.event_buffer_hours,
        history_hours=config.windows.history_hours,
        context_mode=config.split.context_mode,
    )


def _build_sample_index(
    tables: dict[str, pd.DataFrame],
    config: ImpactConfig,
    *,
    stride_hours: int,
) -> pd.DataFrame:
    if stride_hours < 1:
        raise ValueError("stride_hours must be positive")
    observations = tables["observations_hourly"].copy()
    observations["timestamp_utc"] = pd.to_datetime(
        observations["timestamp_utc"], utc=True, errors="raise"
    )
    off_hour = (
        observations["timestamp_utc"].dt.minute.ne(0)
        | observations["timestamp_utc"].dt.second.ne(0)
        | observations["timestamp_utc"].dt.microsecond.ne(0)
    )
    if off_hour.any():
        raise ArtifactContractError(
            "observations_hourly contains non-hour-aligned timestamps; resampling must be reviewed"
        )
    rows: list[dict[str, Any]] = []
    test_end = pd.Timestamp(config.split.test_end)
    for (site_id, zone_id), group in observations.groupby(
        ["site_id", "coastal_zone_id"], sort=True
    ):
        first = group["timestamp_utc"].min() + pd.Timedelta(hours=config.windows.history_hours - 1)
        last = min(
            group["timestamp_utc"].max() - pd.Timedelta(hours=config.windows.forecast_hours),
            test_end - pd.Timedelta(hours=config.windows.forecast_hours),
        )
        if first > last:
            continue
        for prediction in pd.date_range(
            first,
            last,
            freq=f"{stride_hours}h",
            tz="UTC",
        ):
            rows.append(
                {
                    "site_id": str(site_id),
                    "coastal_zone_id": str(zone_id),
                    "prediction_time_utc": prediction,
                    "sample_weight": 1.0,
                }
            )
    if not rows:
        raise ArtifactContractError("no complete history/target windows can be constructed")
    samples = build_hazard_labels(
        pd.DataFrame(rows),
        tables["event_catalog"],
        horizon_hours=config.windows.forecast_hours,
        positive_confidences=config.scope.allowed_label_confidence,
        label_mode=config.scope.label_mode,
    )
    samples = assign_global_time_split(
        samples,
        _global_split_config(config),
        drop_purged=True,
    )
    samples = sample_training_rows(
        samples,
        negative_min_spacing_hours=config.sampling.negative_min_spacing_hours,
        negative_to_positive_target_ratio=config.sampling.negative_to_positive_target_ratio,
        normalize_positive_weight_per_event=(config.sampling.normalize_positive_weight_per_event),
    )
    counts = samples["split"].astype(str).value_counts()
    missing_splits = {"train", "validation", "test"} - set(counts.index)
    if missing_splits:
        raise ArtifactContractError(f"dataset has empty required splits: {sorted(missing_splits)}")
    audit_global_split(samples, _global_split_config(config))
    return samples.reset_index(drop=True)


def build_dataset_artifact(
    input_directory: str | Path,
    config_path: str | Path,
    feature_schema_path: str | Path,
    output_directory: str | Path,
    *,
    stride_hours: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    source = Path(input_directory).resolve()
    destination = Path(output_directory).resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite dataset: {destination}")
    config = load_config(config_path)
    tables = _load_canonical_source(source)
    schema_payload = _read_json(Path(feature_schema_path).resolve())
    schema = _feature_schema(schema_payload, tables)
    synthetic_marker = source / "SYNTHETIC_ONLY.json"
    source_is_synthetic = synthetic_marker.is_file()
    if source_is_synthetic != config.project.synthetic_data:
        raise ArtifactContractError(
            "config project.synthetic_data disagrees with the source dataset marker"
        )
    samples = _build_sample_index(tables, config, stride_hours=stride_hours)
    strict_samples = assign_global_time_split(
        samples.drop(columns=["split", "split_purge_reason", "target_end_time_utc"]),
        _global_split_config(config).model_copy(update={"context_mode": "strict_no_overlap"}),
        drop_purged=False,
    )
    split_sensitivity = {
        "primary_mode": config.split.context_mode,
        "strict_mode": "strict_no_overlap",
        "primary_counts": {
            str(key): int(value)
            for key, value in samples["split"].astype(str).value_counts().sort_index().items()
        },
        "strict_counts_on_retained_primary_rows": {
            str(key): int(value)
            for key, value in strict_samples["split"]
            .astype(str)
            .value_counts()
            .sort_index()
            .items()
        },
        "strict_additional_purged_rows": int(
            strict_samples["split"].astype(str).eq("purged").sum()
        ),
        "note": (
            "Operational context permits pre-boundary observations available at prediction "
            "time; strict_no_overlap additionally purges one complete history window."
        ),
    }
    plan = {
        "input": str(source),
        "output": str(destination),
        "synthetic_data": source_is_synthetic,
        "stride_hours": stride_hours,
        "split_counts": {
            str(key): int(value)
            for key, value in samples["split"].astype(str).value_counts().sort_index().items()
        },
        "feature_schema": schema,
        "split_sensitivity": split_sensitivity,
    }
    if dry_run:
        return {**plan, "planned": True}

    staging = _new_staging(destination)
    try:
        for name, frame in tables.items():
            frame.to_parquet(staging / f"{name}.parquet", index=False, engine="pyarrow")
        samples.to_parquet(staging / "sample_index.parquet", index=False, engine="pyarrow")
        _write_json(staging / "resolved_config.json", config.resolved_dict())
        _write_json(staging / "feature_schema.json", schema)
        _write_json(staging / "split_sensitivity.json", split_sensitivity)
        parquet_inventory = {
            path.stem: {
                "filename": path.name,
                "rows": int(len(pd.read_parquet(path, columns=[]))),
                "sha256": sha256_file(path),
            }
            for path in sorted(staging.glob("*.parquet"))
        }
        # pandas cannot report rows when reading zero columns on every engine;
        # replace with the already known counts.
        for name, frame in {**tables, "sample_index": samples}.items():
            parquet_inventory[name]["rows"] = int(len(frame))
        if source_is_synthetic:
            _write_json(
                staging / "SYNTHETIC_ONLY.json",
                {
                    "synthetic_data": True,
                    "synthetic_only": True,
                    "scientific_use_allowed": False,
                    "public_warning_use_allowed": False,
                    "tables": parquet_inventory,
                    "warning": "Synthetic engineering artifact; not real coastal evidence.",
                },
            )
        identity = {
            "source_table_sha256": {
                name: parquet_inventory[name]["sha256"] for name in CANONICAL_TABLES
            },
            "sample_index_sha256": parquet_inventory["sample_index"]["sha256"],
            "resolved_config": config.resolved_dict(),
            "feature_schema": schema,
            "stride_hours": stride_hours,
        }
        dataset_id = _canonical_hash(identity)
        files = {
            path.relative_to(staging).as_posix(): sha256_file(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file() and path.name != "dataset_manifest.json"
        }
        manifest = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "dataset_id": dataset_id,
            "synthetic_data": source_is_synthetic,
            "scientific_use_allowed": False,
            "research_only": True,
            "label_mode": config.scope.label_mode,
            "model_name": config.model_name,
            "stride_hours": stride_hours,
            "split_counts": plan["split_counts"],
            "resolved_config": config.resolved_dict(),
            "feature_schema": schema,
            "files": files,
        }
        _write_json(staging / "dataset_manifest.json", manifest)
        _commit_staging(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        **plan,
        "planned": False,
        "dataset_id": dataset_id,
        "manifest": str(destination / "dataset_manifest.json"),
        "manifest_sha256": sha256_file(destination / "dataset_manifest.json"),
    }


def verify_dataset_artifact(directory: str | Path) -> dict[str, Any]:
    root = Path(directory).resolve()
    manifest = _read_json(root / "dataset_manifest.json")
    if manifest.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise ArtifactContractError("unsupported dataset artifact schema")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ArtifactContractError("dataset manifest has no immutable file inventory")
    required = {f"{name}.parquet" for name in (*CANONICAL_TABLES, "sample_index")}
    missing = required - set(files)
    if missing:
        raise ArtifactContractError(f"dataset manifest lacks files: {sorted(missing)}")
    for relative, expected in files.items():
        path = root / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ArtifactContractError(f"dataset file is missing or changed: {relative}")
    if bool(manifest.get("synthetic_data")) != (root / "SYNTHETIC_ONLY.json").is_file():
        raise ArtifactContractError("dataset synthetic marker disagrees with manifest")
    if manifest.get("scientific_use_allowed") is not False:
        raise ArtifactContractError("unreviewed dataset artifact must remain research-only")
    return manifest


def _load_dataset_context(
    directory: str | Path,
) -> tuple[Path, dict[str, Any], ImpactConfig, dict[str, Any], dict[str, pd.DataFrame]]:
    root = Path(directory).resolve()
    manifest = verify_dataset_artifact(root)
    config = ImpactConfig.model_validate(manifest["resolved_config"])
    schema = dict(manifest["feature_schema"])
    tables = {name: pd.read_parquet(root / f"{name}.parquet") for name in CANONICAL_TABLES}
    tables["sample_index"] = pd.read_parquet(root / "sample_index.parquet")
    return root, manifest, config, schema, tables


def _config_compatible(config: ImpactConfig, dataset_config: ImpactConfig) -> None:
    checks = {
        "project.synthetic_data": (
            config.project.synthetic_data,
            dataset_config.project.synthetic_data,
        ),
        "scope.label_mode": (config.scope.label_mode, dataset_config.scope.label_mode),
        "scope.allowed_label_confidence": (
            config.scope.allowed_label_confidence,
            dataset_config.scope.allowed_label_confidence,
        ),
        "windows": (config.windows.model_dump(), dataset_config.windows.model_dump()),
        "split": (config.split.model_dump(), dataset_config.split.model_dump()),
        "sampling": (config.sampling.model_dump(), dataset_config.sampling.model_dump()),
    }
    mismatched = [name for name, (left, right) in checks.items() if left != right]
    if mismatched:
        raise ArtifactContractError(
            f"training config disagrees with dataset construction: {mismatched}"
        )


def _split_index(samples: pd.DataFrame, split: str) -> pd.DataFrame:
    result = samples.loc[samples["split"].astype(str) == split].reset_index(drop=True)
    if result.empty:
        raise ArtifactContractError(f"dataset has no {split} samples")
    return result


def _positive_weight(samples: pd.DataFrame, maximum: float) -> float:
    target = np.stack(samples["hazard_target"].map(np.asarray))
    mask = np.stack(samples["hazard_mask"].map(np.asarray)).astype(bool)
    values = target[mask]
    if values.size == 0:
        raise ArtifactContractError("training split has no supervised hazard cells")
    positives = float(values.sum())
    if positives <= 0.0:
        raise ArtifactContractError("training split has no positive hazard evidence")
    negatives = max(0.0, float(values.size) - positives)
    return float(min(maximum, max(1.0, negatives / positives)))


def _sample_weight_summary(samples: pd.DataFrame) -> dict[str, Any]:
    weights = pd.to_numeric(samples.get("sample_weight", 1.0), errors="raise")
    target = samples["hazard_target"].map(
        lambda value: bool(np.nanmax(np.asarray(value, dtype=np.float64)) > 0.0)
    )
    positive = weights[target].to_numpy(dtype=np.float64)
    negative = weights[~target].to_numpy(dtype=np.float64)
    return {
        "all": {
            "count": int(len(weights)),
            "minimum": float(weights.min()),
            "median": float(weights.median()),
            "maximum": float(weights.max()),
            "sum": float(weights.sum()),
        },
        "positive": {
            "count": int(positive.size),
            "minimum": None if positive.size == 0 else float(np.min(positive)),
            "median": None if positive.size == 0 else float(np.median(positive)),
            "maximum": None if positive.size == 0 else float(np.max(positive)),
            "sum": float(np.sum(positive)),
        },
        "negative": {
            "count": int(negative.size),
            "minimum": None if negative.size == 0 else float(np.min(negative)),
            "median": None if negative.size == 0 else float(np.median(negative)),
            "maximum": None if negative.size == 0 else float(np.max(negative)),
            "sum": float(np.sum(negative)),
        },
    }


def _fit_preprocessors(
    tables: dict[str, pd.DataFrame],
    train_index: pd.DataFrame,
    schema: dict[str, Any],
    *,
    dataset_manifest_hash: str,
    variant: str,
) -> tuple[TrainOnlyPreprocessor, TrainOnlyPreprocessor | None, TrainOnlyPreprocessor]:
    train_sites = set(train_index["site_id"].astype(str))
    train_zones = set(train_index["coastal_zone_id"].astype(str))
    latest = pd.to_datetime(train_index["prediction_time_utc"], utc=True).max()
    observations = tables["observations_hourly"].copy()
    observations["timestamp_utc"] = pd.to_datetime(observations["timestamp_utc"], utc=True)
    observations = observations.loc[
        observations["site_id"].astype(str).isin(train_sites)
        & (observations["timestamp_utc"] <= latest)
    ].copy()
    observations["split"] = "train"
    past = TrainOnlyPreprocessor(
        schema["past_feature_names"], dataset_manifest_hash=dataset_manifest_hash
    ).fit(observations, timestamp_col="timestamp_utc")

    future: TrainOnlyPreprocessor | None = None
    if variant == "hybrid_tcn":
        forecasts = tables["forecasts_hourly"].copy()
        forecasts["issue_time_utc"] = pd.to_datetime(forecasts["issue_time_utc"], utc=True)
        forecasts["valid_time_utc"] = pd.to_datetime(forecasts["valid_time_utc"], utc=True)
        forecasts = forecasts.loc[
            forecasts["site_id"].astype(str).isin(train_sites)
            & (forecasts["issue_time_utc"] <= latest)
            & (forecasts["valid_time_utc"] <= latest + pd.Timedelta(hours=24))
        ].copy()
        if forecasts.empty:
            raise ArtifactContractError("hybrid training has no train-period issued forecasts")
        forecasts["split"] = "train"
        future = TrainOnlyPreprocessor(
            schema["future_feature_names"], dataset_manifest_hash=dataset_manifest_hash
        ).fit(forecasts, timestamp_col="valid_time_utc")

    static = (
        tables["static_features"]
        .loc[tables["static_features"]["coastal_zone_id"].astype(str).isin(train_zones)]
        .copy()
    )
    static["split"] = "train"
    static_preprocessor = TrainOnlyPreprocessor(
        schema["static_feature_names"], dataset_manifest_hash=dataset_manifest_hash
    ).fit(static, timestamp_col=None)
    return past, future, static_preprocessor


def _window_dataset(
    tables: dict[str, pd.DataFrame],
    index: pd.DataFrame,
    config: ImpactConfig,
    schema: dict[str, Any],
    *,
    past: TrainOnlyPreprocessor | None,
    future: TrainOnlyPreprocessor | None,
    static: TrainOnlyPreprocessor | None,
) -> CoastWatchWindowDataset:
    return CoastWatchWindowDataset(
        tables["observations_hourly"],
        tables["forecasts_hourly"],
        tables["static_features"],
        index,
        past_feature_names=schema["past_feature_names"],
        future_feature_names=schema["future_feature_names"],
        static_feature_names=schema["static_feature_names"],
        past_hours=config.windows.history_hours,
        horizon_hours=config.windows.forecast_hours,
        source_model=schema.get("source_model"),
        past_preprocessor=past,
        future_preprocessor=future,
        static_preprocessor=static,
        water_target_column=schema["water_target_column"],
        physics_baseline_column=schema.get("physics_baseline_column"),
        reject_future_only_forecasts=True,
    )


def _loader(
    dataset: CoastWatchWindowDataset,
    *,
    batch_size: int,
    shuffle: bool,
    seed: int,
) -> DataLoader[Any]:
    generator = torch.Generator().manual_seed(seed)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=0,
        collate_fn=collate_window_batch,
        generator=generator,
    )


def _prediction_frame(
    index: pd.DataFrame,
    prediction: PredictionBatch,
    *,
    model_kind: str,
) -> pd.DataFrame:
    if len(index) != len(prediction.hazard_logits):
        raise ArtifactContractError("prediction count does not match sample index")
    frame = index[
        [
            "site_id",
            "coastal_zone_id",
            "prediction_time_utc",
            "split",
            "event_id",
            "storm_group_id",
        ]
    ].copy()
    frame["hazard_logits"] = [value.tolist() for value in prediction.hazard_logits]
    frame["hazard_target"] = [value.tolist() for value in prediction.hazard_targets]
    frame["hazard_mask"] = [value.tolist() for value in prediction.hazard_masks]
    frame["cumulative_event_probability"] = [
        value.tolist() for value in prediction.cumulative_probabilities
    ]
    frame["event_probability"] = prediction.cumulative_probabilities[:, -1]
    frame["water_quantiles"] = [value.tolist() for value in prediction.water_quantiles]
    frame["water_target"] = [value.tolist() for value in prediction.water_targets]
    frame["water_mask"] = [value.tolist() for value in prediction.water_masks]
    frame["calibrated"] = False
    frame["model_kind"] = model_kind
    return frame


def _inventory(directory: Path, *, excluded: set[str]) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256_file(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name not in excluded
    }


def _train_tcn_at(
    directory: Path,
    *,
    run_id: str,
    dataset_root: Path,
    dataset_manifest: dict[str, Any],
    config: ImpactConfig,
    schema: dict[str, Any],
    tables: dict[str, pd.DataFrame],
    train_index: pd.DataFrame,
    validation_index: pd.DataFrame,
    variant: Literal["obs_only_tcn", "hybrid_tcn"],
    max_epochs: int | None,
    batch_size: int | None,
    device: str,
    run_kind: str = "impactnet",
    held_out_site_id: str | None = None,
) -> dict[str, Any]:
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ArtifactContractError("CUDA was requested but is unavailable")
    directory.mkdir(parents=True, exist_ok=False)
    dataset_manifest_hash = sha256_file(dataset_root / "dataset_manifest.json")
    past, future, static = _fit_preprocessors(
        tables,
        train_index,
        schema,
        dataset_manifest_hash=dataset_manifest_hash,
        variant=variant,
    )
    train_dataset = _window_dataset(
        tables, train_index, config, schema, past=past, future=future, static=static
    )
    validation_dataset = _window_dataset(
        tables, validation_index, config, schema, past=past, future=future, static=static
    )
    architecture = ImpactNetConfig(
        past_feature_dim=len(schema["past_feature_names"]),
        forecast_feature_dim=(
            len(schema["future_feature_names"]) if variant == "hybrid_tcn" else 0
        ),
        static_feature_dim=len(schema["static_feature_names"]),
        time_feature_dim=len(LEAD_FEATURE_NAMES),
        variant=variant,
        history_hours=config.windows.history_hours,
        forecast_hours=config.windows.forecast_hours,
        hidden_channels=config.model.hidden_channels,
        num_blocks=config.model.num_blocks,
        kernel_size=config.model.kernel_size,
        dilations=tuple(config.model.dilations),
        dropout=config.model.dropout,
        decoder_hidden_dim=config.model.decoder_hidden_dim,
        lead_embedding_dim=config.model.lead_embedding_dim,
        include_missing_masks=config.features.include_missing_masks,
        water_target_mode=config.features.water_target_mode,
    )
    model = ImpactNet(architecture)
    pos_weight = _positive_weight(train_index, config.loss.max_pos_weight)
    resolved_epochs = max_epochs or config.training.max_epochs
    resolved_batch = batch_size or config.training.batch_size
    trainer = ImpactTrainer(
        model,
        learning_rate=config.training.learning_rate,
        weight_decay=config.training.weight_decay,
        event_weight=config.loss.event_weight,
        water_weight=config.loss.water_weight,
        pos_weight=pos_weight,
        grad_clip_norm=config.training.grad_clip_norm,
        max_epochs=resolved_epochs,
        early_stopping_patience=min(
            config.training.early_stopping_patience,
            resolved_epochs,
        ),
        seed=config.training.seed,
        mixed_precision=config.training.mixed_precision,
        device=device,
    )
    result = trainer.fit(
        _loader(
            train_dataset,
            batch_size=resolved_batch,
            shuffle=True,
            seed=config.training.seed,
        ),
        _loader(
            validation_dataset,
            batch_size=resolved_batch,
            shuffle=False,
            seed=config.training.seed,
        ),
        checkpoint_dir=directory / "checkpoints",
    )
    save_file(
        {name: value.detach().cpu().contiguous() for name, value in model.state_dict().items()},
        str(directory / "model.safetensors"),
    )
    preprocessing = {
        "fitted_on": "train",
        "dataset_manifest_hash": dataset_manifest_hash,
        "past": past.to_dict(),
        "future": None if future is None else future.to_dict(),
        "static": static.to_dict(),
    }
    _write_json(directory / "preprocessing.json", preprocessing)
    _write_json(directory / "architecture.json", architecture.to_dict())
    _write_json(directory / "resolved_config.json", config.resolved_dict())
    _write_json(directory / "feature_schema.json", schema)
    _write_json(
        directory / "training_history.json",
        {
            "history": [asdict(record) for record in result.history],
            "best_epoch": result.best_epoch,
            "best_validation_score": result.best_validation_score,
            "stopped_early": result.stopped_early,
            "determinism": dict(result.determinism),
            "positive_weight": pos_weight,
            "sample_weight_distribution": _sample_weight_summary(train_index),
            "train_samples": len(train_dataset),
            "validation_samples": len(validation_dataset),
        },
    )
    validation_frame = _prediction_frame(
        validation_index,
        result.validation_predictions,
        model_kind="impactnet",
    )
    validation_frame.to_parquet(
        directory / "validation_predictions.parquet", index=False, engine="pyarrow"
    )
    files = _inventory(directory, excluded={"run_manifest.json"})
    manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "run_kind": run_kind,
        "model_kind": "impactnet",
        "model_variant": variant,
        "model_name": config.model_name,
        "label_mode": config.scope.label_mode,
        "synthetic_data": bool(dataset_manifest["synthetic_data"]),
        "scientific_result": False,
        "shadow_mode": True,
        "dataset_path": str(dataset_root),
        "dataset_id": dataset_manifest["dataset_id"],
        "dataset_manifest_sha256": dataset_manifest_hash,
        "feature_schema": schema,
        "resolved_config": config.resolved_dict(),
        "positive_weight": pos_weight,
        "sample_weight_distribution": _sample_weight_summary(train_index),
        "held_out_site_id": held_out_site_id,
        "files": files,
    }
    _write_json(directory / "run_manifest.json", manifest)
    return manifest


def train_impactnet_artifact(
    dataset_directory: str | Path,
    config_path: str | Path,
    output_directory: str | Path,
    *,
    variant: Literal["obs_only_tcn", "hybrid_tcn"] | None = None,
    max_epochs: int | None = None,
    batch_size: int | None = None,
    device: str = "cpu",
    dry_run: bool = False,
) -> dict[str, Any]:
    destination = Path(output_directory).resolve()
    dataset_root, dataset_manifest, dataset_config, schema, tables = _load_dataset_context(
        dataset_directory
    )
    config = load_config(config_path)
    _config_compatible(config, dataset_config)
    resolved_variant = variant or config.mode.model_variant
    train_index = _split_index(tables["sample_index"], "train")
    validation_index = _split_index(tables["sample_index"], "validation")
    _positive_weight(train_index, config.loss.max_pos_weight)
    plan = {
        "output": str(destination),
        "dataset_id": dataset_manifest["dataset_id"],
        "variant": resolved_variant,
        "train_samples": len(train_index),
        "validation_samples": len(validation_index),
        "max_epochs": max_epochs or config.training.max_epochs,
        "batch_size": batch_size or config.training.batch_size,
        "device": device,
        "synthetic_data": bool(dataset_manifest["synthetic_data"]),
    }
    if dry_run:
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite run: {destination}")
        return {**plan, "planned": True}
    staging = _new_staging(destination)
    shutil.rmtree(staging)
    try:
        manifest = _train_tcn_at(
            staging,
            run_id=destination.name,
            dataset_root=dataset_root,
            dataset_manifest=dataset_manifest,
            config=config,
            schema=schema,
            tables=tables,
            train_index=train_index,
            validation_index=validation_index,
            variant=resolved_variant,
            max_epochs=max_epochs,
            batch_size=batch_size,
            device=device,
        )
        _commit_staging(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        **plan,
        "planned": False,
        "run_id": manifest["run_id"],
        "manifest": str(destination / "run_manifest.json"),
    }


def _baseline_arrays(
    dataset: CoastWatchWindowDataset,
    *,
    include_future: bool,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.bool_]]:
    features: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    for index in range(len(dataset)):
        sample = dataset[index]
        past = sample["past_values"].numpy().astype(np.float64)
        past[~sample["past_mask"].numpy().astype(bool)] = np.nan
        future = sample["future_values"].numpy().astype(np.float64)
        future[~sample["future_mask"].numpy().astype(bool)] = np.nan
        summary = build_logistic_summary_features(
            past[None, ...],
            future[None, ...] if include_future else None,
        )[0]
        features.append(summary)
        targets.append(sample["hazard_target"].numpy().astype(np.float64))
        masks.append(sample["hazard_mask"].numpy().astype(bool))
    return np.stack(features), np.stack(targets), np.stack(masks)


def _baseline_prediction_frame(
    index: pd.DataFrame,
    logits: np.ndarray,
    targets: np.ndarray,
    masks: np.ndarray,
) -> pd.DataFrame:
    cumulative = cumulative_event_probability(logits)
    frame = index[
        [
            "site_id",
            "coastal_zone_id",
            "prediction_time_utc",
            "split",
            "event_id",
            "storm_group_id",
        ]
    ].copy()
    frame["hazard_logits"] = [value.tolist() for value in logits]
    frame["hazard_target"] = [value.tolist() for value in targets]
    frame["hazard_mask"] = [value.tolist() for value in masks]
    frame["cumulative_event_probability"] = [value.tolist() for value in cumulative]
    frame["event_probability"] = cumulative[:, -1]
    frame["calibrated"] = False
    frame["model_kind"] = "baseline_logistic"
    return frame


def train_logistic_artifact(
    dataset_directory: str | Path,
    config_path: str | Path,
    output_directory: str | Path,
    *,
    include_future: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    destination = Path(output_directory).resolve()
    dataset_root, dataset_manifest, dataset_config, schema, tables = _load_dataset_context(
        dataset_directory
    )
    config = load_config(config_path)
    _config_compatible(config, dataset_config)
    train_index = _split_index(tables["sample_index"], "train")
    validation_index = _split_index(tables["sample_index"], "validation")
    _positive_weight(train_index, config.loss.max_pos_weight)
    plan = {
        "output": str(destination),
        "dataset_id": dataset_manifest["dataset_id"],
        "model_kind": "baseline_logistic",
        "model_variant": "logistic_summary",
        "include_future": include_future,
        "train_samples": len(train_index),
        "validation_samples": len(validation_index),
        "synthetic_data": bool(dataset_manifest["synthetic_data"]),
    }
    if dry_run:
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite run: {destination}")
        return {**plan, "planned": True}
    staging = _new_staging(destination)
    try:
        train_dataset = _window_dataset(
            tables, train_index, config, schema, past=None, future=None, static=None
        )
        validation_dataset = _window_dataset(
            tables, validation_index, config, schema, past=None, future=None, static=None
        )
        train_x, train_y, train_mask = _baseline_arrays(
            train_dataset, include_future=include_future
        )
        valid_x, valid_y, valid_mask = _baseline_arrays(
            validation_dataset, include_future=include_future
        )
        binary_train_mask = train_mask & ((train_y == 0.0) | (train_y == 1.0))
        if not np.any(binary_train_mask & (train_y == 1.0)):
            raise ArtifactContractError("logistic training has no binary positive hazard evidence")
        model = LogisticEventBaseline(random_state=config.training.seed)
        model.fit(train_x, train_y, mask=binary_train_mask)
        model.save_json(staging / "model.json")
        validation_logits = np.asarray(model.predict_logits(valid_x), dtype=np.float64)
        validation = _baseline_prediction_frame(
            validation_index,
            validation_logits,
            valid_y,
            valid_mask,
        )
        validation.to_parquet(
            staging / "validation_predictions.parquet", index=False, engine="pyarrow"
        )
        _write_json(staging / "resolved_config.json", config.resolved_dict())
        _write_json(staging / "feature_schema.json", schema)
        files = _inventory(staging, excluded={"run_manifest.json"})
        manifest = {
            "schema_version": RUN_SCHEMA_VERSION,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "run_id": destination.name,
            "run_kind": "baseline_logistic",
            "model_kind": "baseline_logistic",
            "model_variant": "logistic_summary",
            "model_name": "CoastWatch Logistic Baseline",
            "label_mode": config.scope.label_mode,
            "synthetic_data": bool(dataset_manifest["synthetic_data"]),
            "scientific_result": False,
            "shadow_mode": True,
            "dataset_path": str(dataset_root),
            "dataset_id": dataset_manifest["dataset_id"],
            "dataset_manifest_sha256": sha256_file(dataset_root / "dataset_manifest.json"),
            "feature_schema": schema,
            "resolved_config": config.resolved_dict(),
            "include_future": include_future,
            "files": files,
        }
        _write_json(staging / "run_manifest.json", manifest)
        _commit_staging(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**plan, "planned": False, "run_id": destination.name}


def verify_run_artifact(directory: str | Path) -> dict[str, Any]:
    root = Path(directory).resolve()
    manifest = _read_json(root / "run_manifest.json")
    if manifest.get("schema_version") != RUN_SCHEMA_VERSION:
        raise ArtifactContractError("unsupported run artifact schema")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ArtifactContractError("run manifest has no immutable file inventory")
    for relative, expected in files.items():
        path = root / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ArtifactContractError(f"run file is missing or changed: {relative}")
    dataset_root = Path(str(manifest["dataset_path"]))
    dataset = verify_dataset_artifact(dataset_root)
    if dataset.get("dataset_id") != manifest.get("dataset_id"):
        raise ArtifactContractError("run dataset_id no longer matches its dataset")
    if sha256_file(dataset_root / "dataset_manifest.json") != manifest.get(
        "dataset_manifest_sha256"
    ):
        raise ArtifactContractError("run dataset manifest hash changed")
    return manifest


def _stack_column(frame: pd.DataFrame, name: str, *, dtype: Any) -> np.ndarray:
    if name not in frame:
        raise ArtifactContractError(f"prediction artifact lacks {name}")
    try:
        arrays = [
            np.asarray(value.tolist() if isinstance(value, np.ndarray) else value, dtype=dtype)
            for value in frame[name]
        ]
        return np.stack(arrays).astype(dtype, copy=False)
    except Exception as error:
        raise ArtifactContractError(f"prediction column {name} is not rectangular") from error


def calibrate_temperature_artifact(
    run_directory: str | Path,
    output_path: str | Path,
    *,
    iterations: int = 96,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_root = Path(run_directory).resolve()
    manifest = verify_run_artifact(run_root)
    destination = Path(output_path).resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite calibration: {destination}")
    prediction_path = run_root / "validation_predictions.parquet"
    predictions = pd.read_parquet(prediction_path)
    if predictions.empty or not predictions["split"].astype(str).eq("validation").all():
        raise ArtifactContractError("calibration requires validation-only predictions")
    logits = _stack_column(predictions, "hazard_logits", dtype=np.float64)
    targets = _stack_column(predictions, "hazard_target", dtype=np.float64)
    masks = _stack_column(predictions, "hazard_mask", dtype=bool)
    scaler = fit_global_temperature(
        logits,
        targets,
        masks,
        split="validation",
        iterations=iterations,
    )
    payload = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        **scaler.to_dict(),
        "calibrated": True,
        "run_id": manifest["run_id"],
        "model_kind": manifest["model_kind"],
        "source_predictions": str(prediction_path),
        "source_predictions_sha256": sha256_file(prediction_path),
        "synthetic_data": bool(manifest["synthetic_data"]),
    }
    if not dry_run:
        _write_json(destination, payload)
    return {**payload, "output": str(destination), "planned": dry_run}


def _load_calibration(
    path: Path,
    manifest: dict[str, Any],
    prediction_path: Path,
) -> dict[str, Any]:
    payload = _read_json(path)
    if payload.get("schema_version") != SELECTION_SCHEMA_VERSION:
        raise ArtifactContractError("unsupported calibration artifact schema")
    if payload.get("run_id") != manifest.get("run_id"):
        raise ArtifactContractError("calibration belongs to a different run")
    if payload.get("fitted_split") != "validation" or payload.get("calibrated") is not True:
        raise ArtifactContractError("calibration must be fitted on validation")
    if payload.get("source_predictions_sha256") != sha256_file(prediction_path):
        raise ArtifactContractError("calibration prediction hash no longer matches")
    TemperatureScaler(
        temperature=float(payload["temperature"]),
        fitted_split=str(payload["fitted_split"]),
        method=str(payload["method"]),
    )
    return payload


def _calibrated_probabilities(
    predictions: pd.DataFrame,
    calibration: dict[str, Any] | None,
) -> np.ndarray:
    logits = _stack_column(predictions, "hazard_logits", dtype=np.float64)
    if calibration is None:
        return cumulative_event_probability(logits)
    scaler = TemperatureScaler(
        temperature=float(calibration["temperature"]),
        fitted_split="validation",
        method=str(calibration["method"]),
    )
    return scaler.cumulative_probabilities(logits)


def _events_for_split(
    dataset_root: Path,
    dataset_manifest: dict[str, Any],
    split: Literal["validation", "test"],
    *,
    site_ids: set[str] | None = None,
) -> pd.DataFrame:
    events = pd.read_parquet(dataset_root / "event_catalog.parquet")
    sites = pd.read_parquet(dataset_root / "sites.parquet")
    events = events.merge(
        sites[["site_id", "coastal_zone_id"]],
        on="coastal_zone_id",
        how="inner",
        validate="many_to_many",
    )
    events["onset_time_utc"] = pd.to_datetime(events["onset_time_utc"], utc=True)
    resolved = dataset_manifest["resolved_config"]["split"]
    if split == "validation":
        start = pd.Timestamp(resolved["train_end"])
        end = pd.Timestamp(resolved["validation_end"])
    else:
        start = pd.Timestamp(resolved["validation_end"])
        end = pd.Timestamp(resolved["test_end"])
    events = events.loc[
        events["onset_time_utc"].notna()
        & (events["onset_time_utc"] > start)
        & (events["onset_time_utc"] <= end)
    ].copy()
    if site_ids is not None:
        events = events.loc[events["site_id"].astype(str).isin(site_ids)].copy()
    return events.reset_index(drop=True)


def _threshold_value(payload: dict[str, Any]) -> float:
    try:
        value = float(payload["selected"]["balanced"]["threshold"])
    except (KeyError, TypeError, ValueError) as error:
        raise ArtifactContractError(
            "threshold artifact lacks a balanced operating point"
        ) from error
    if not 0.0 <= value <= 1.0:
        raise ArtifactContractError("balanced threshold must lie in [0, 1]")
    return value


def select_thresholds_artifact(
    run_directory: str | Path,
    calibration_path: str | Path,
    output_path: str | Path,
    *,
    candidate_thresholds: Sequence[float] | None = None,
    max_false_alert_episodes: int | None = None,
    max_false_alerts_per_site_month: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_root = Path(run_directory).resolve()
    manifest = verify_run_artifact(run_root)
    destination = Path(output_path).resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite thresholds: {destination}")
    prediction_path = run_root / "validation_predictions.parquet"
    predictions = pd.read_parquet(prediction_path)
    calibration_file = Path(calibration_path).resolve()
    calibration = _load_calibration(calibration_file, manifest, prediction_path)
    dataset_root = Path(str(manifest["dataset_path"]))
    dataset_manifest = verify_dataset_artifact(dataset_root)
    if int(dataset_manifest.get("stride_hours", 0)) != 1:
        raise ArtifactContractError("episode threshold selection requires an hourly timeline")
    cumulative = _calibrated_probabilities(predictions, calibration)
    timeline = predictions[["site_id", "prediction_time_utc", "split"]].copy()
    timeline["event_probability"] = cumulative[:, -1]
    events = _events_for_split(
        dataset_root,
        dataset_manifest,
        "validation",
        site_ids=set(timeline["site_id"].astype(str)),
    )
    eligible_events = events.loc[
        events["impact_confirmed"].eq(True)  # noqa: E712
        & events["label_confidence"].astype(str).isin(["A", "B"])
        & events["onset_precision"].astype(str).eq("exact_hour")
    ]
    if eligible_events.empty:
        raise ArtifactContractError(
            "validation split has no exact-hour confirmed A/B events for threshold selection"
        )
    config = ImpactConfig.model_validate(manifest["resolved_config"])
    selected = select_operating_thresholds(
        timeline,
        events,
        split="validation",
        candidate_thresholds=candidate_thresholds,
        max_false_alert_episodes=max_false_alert_episodes,
        max_false_alerts_per_site_month=max_false_alerts_per_site_month,
        merge_gap_hours=config.alerts.merge_gap_hours,
        cooldown_hours=config.alerts.cooldown_hours,
        lookahead_hours=config.alerts.match_lookahead_hours,
    )
    balanced = float(selected["selected"]["balanced"]["threshold"])
    selected.update(
        {
            "schema_version": SELECTION_SCHEMA_VERSION,
            "run_id": manifest["run_id"],
            "source_predictions_sha256": sha256_file(prediction_path),
            "calibration_sha256": sha256_file(calibration_file),
            "synthetic_data": bool(manifest["synthetic_data"]),
            "research_bands": {
                "advisory": float(max(0.01, balanced * 0.5)),
                "warning": balanced,
                "critical": float(min(0.99, max(balanced + 0.1, balanced * 1.25))),
            },
        }
    )
    if not dry_run:
        _write_json(destination, selected)
    return {
        "output": str(destination),
        "planned": dry_run,
        "run_id": manifest["run_id"],
        "fitted_split": selected["fitted_split"],
        "selected": selected["selected"],
        "eligible_validation_events": int(len(eligible_events)),
    }


def _load_thresholds(
    path: Path,
    manifest: dict[str, Any],
    prediction_path: Path,
    calibration_path: Path,
) -> dict[str, Any]:
    payload = _read_json(path)
    if payload.get("schema_version") != SELECTION_SCHEMA_VERSION:
        raise ArtifactContractError("unsupported threshold artifact schema")
    if payload.get("run_id") != manifest.get("run_id"):
        raise ArtifactContractError("thresholds belong to a different run")
    if payload.get("fitted_split") != "validation":
        raise ArtifactContractError("thresholds were not fitted on validation")
    if payload.get("source_predictions_sha256") != sha256_file(prediction_path):
        raise ArtifactContractError("threshold prediction hash no longer matches")
    if payload.get("calibration_sha256") != sha256_file(calibration_path):
        raise ArtifactContractError("threshold calibration hash no longer matches")
    _threshold_value(payload)
    return payload


def _binary_evaluation_arrays(predictions: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, int]:
    target = _stack_column(predictions, "hazard_target", dtype=np.float64)
    mask = _stack_column(predictions, "hazard_mask", dtype=bool)
    binary = (target == 0.0) | (target == 1.0)
    excluded = int(np.sum(mask & ~binary))
    return target, mask & binary, excluded


def _metrics_payload(
    predictions: pd.DataFrame,
    cumulative: np.ndarray,
    *,
    manifest: dict[str, Any],
    split: Literal["validation", "test"],
    threshold: float,
    events: pd.DataFrame | None,
) -> dict[str, Any]:
    config = ImpactConfig.model_validate(manifest["resolved_config"])
    target, mask, excluded_soft = _binary_evaluation_arrays(predictions)
    metrics: dict[str, Any] = {
        "split": split,
        "synthetic_data": bool(manifest["synthetic_data"]),
        "scientific_result": False,
        "horizon_metrics": compute_horizon_metrics(
            cumulative,
            target,
            mask,
            horizons=config.windows.output_horizons_hours,
            threshold=threshold,
        ),
        "soft_target_cells_excluded_from_binary_metrics": excluded_soft,
    }
    if {"water_quantiles", "water_target", "water_mask"}.issubset(predictions.columns):
        metrics["water_metrics"] = water_quantile_metrics(
            _stack_column(predictions, "water_target", dtype=np.float64),
            _stack_column(predictions, "water_quantiles", dtype=np.float64),
            _stack_column(predictions, "water_mask", dtype=bool),
        )
    if events is None:
        metrics["event_metrics"] = {
            "insufficient_evidence": True,
            "reason": "no frozen operating threshold was supplied",
        }
    else:
        timeline = predictions[["site_id", "prediction_time_utc", "split"]].copy()
        timeline["event_probability"] = cumulative[:, -1]
        metrics["event_metrics"] = evaluate_alert_events(
            timeline,
            events,
            threshold,
            merge_gap_hours=config.alerts.merge_gap_hours,
            cooldown_hours=config.alerts.cooldown_hours,
            lookahead_hours=config.alerts.match_lookahead_hours,
        ).metrics
    return metrics


def evaluate_validation_artifact(
    run_directory: str | Path,
    output_path: str | Path,
    *,
    calibration_path: str | Path | None = None,
    thresholds_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_root = Path(run_directory).resolve()
    manifest = verify_run_artifact(run_root)
    destination = Path(output_path).resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite evaluation: {destination}")
    prediction_path = run_root / "validation_predictions.parquet"
    predictions = pd.read_parquet(prediction_path)
    calibration: dict[str, Any] | None = None
    calibration_file: Path | None = None
    if calibration_path is not None:
        calibration_file = Path(calibration_path).resolve()
        calibration = _load_calibration(calibration_file, manifest, prediction_path)
    cumulative = _calibrated_probabilities(predictions, calibration)
    threshold = 0.5
    thresholds: dict[str, Any] | None = None
    if thresholds_path is not None:
        if calibration_file is None:
            raise ArtifactContractError("threshold evaluation also requires its calibration")
        thresholds = _load_thresholds(
            Path(thresholds_path).resolve(),
            manifest,
            prediction_path,
            calibration_file,
        )
        threshold = _threshold_value(thresholds)
    dataset_root = Path(str(manifest["dataset_path"]))
    dataset_manifest = verify_dataset_artifact(dataset_root)
    events = (
        _events_for_split(
            dataset_root,
            dataset_manifest,
            "validation",
            site_ids=set(predictions["site_id"].astype(str)),
        )
        if thresholds is not None
        else None
    )
    payload = _metrics_payload(
        predictions,
        cumulative,
        manifest=manifest,
        split="validation",
        threshold=threshold,
        events=events,
    )
    payload.update(
        {
            "schema_version": "impactnet-evaluation-v1",
            "run_id": manifest["run_id"],
            "calibrated": calibration is not None,
            "calibration_sha256": (
                None if calibration_file is None else sha256_file(calibration_file)
            ),
            "thresholds_sha256": (
                None if thresholds_path is None else sha256_file(Path(thresholds_path))
            ),
        }
    )
    if not dry_run:
        _write_json(destination, payload)
    return {
        "output": str(destination),
        "planned": dry_run,
        "run_id": manifest["run_id"],
        "split": "validation",
        "calibrated": calibration is not None,
        "threshold": threshold,
    }


def _load_tcn_preprocessors(
    run_root: Path,
) -> tuple[TrainOnlyPreprocessor, TrainOnlyPreprocessor | None, TrainOnlyPreprocessor]:
    payload = _read_json(run_root / "preprocessing.json")
    past = TrainOnlyPreprocessor.from_dict(payload["past"])
    future_payload = payload.get("future")
    future = None if future_payload is None else TrainOnlyPreprocessor.from_dict(future_payload)
    static = TrainOnlyPreprocessor.from_dict(payload["static"])
    return past, future, static


def _predict_run_split(
    run_root: Path,
    manifest: dict[str, Any],
    split: Literal["validation", "test"],
    *,
    device: str,
) -> pd.DataFrame:
    dataset_root, _, _, schema, tables = _load_dataset_context(manifest["dataset_path"])
    del dataset_root
    config = ImpactConfig.model_validate(manifest["resolved_config"])
    index = _split_index(tables["sample_index"], split)
    if manifest["model_kind"] == "impactnet":
        architecture = ImpactNetConfig.from_dict(_read_json(run_root / "architecture.json"))
        impact_model = ImpactNet(architecture)
        impact_model.load_state_dict(load_file(str(run_root / "model.safetensors"), device=device))
        past, future, static = _load_tcn_preprocessors(run_root)
        dataset = _window_dataset(
            tables, index, config, schema, past=past, future=future, static=static
        )
        trainer = ImpactTrainer(
            impact_model,
            pos_weight=float(manifest["positive_weight"]),
            max_epochs=1,
            early_stopping_patience=1,
            mixed_precision=False,
            seed=config.training.seed,
            device=device,
        )
        prediction, _ = trainer.predict(
            _loader(
                dataset,
                batch_size=config.training.batch_size,
                shuffle=False,
                seed=config.training.seed,
            )
        )
        return _prediction_frame(index, prediction, model_kind="impactnet")
    if manifest["model_kind"] == "baseline_logistic":
        baseline_model = LogisticEventBaseline.load_json(run_root / "model.json")
        dataset = _window_dataset(
            tables, index, config, schema, past=None, future=None, static=None
        )
        features, targets, masks = _baseline_arrays(
            dataset, include_future=bool(manifest["include_future"])
        )
        logits = np.asarray(baseline_model.predict_logits(features), dtype=np.float64)
        return _baseline_prediction_frame(index, logits, targets, masks)
    raise ArtifactContractError(f"unsupported run model_kind: {manifest['model_kind']!r}")


def evaluate_final_test_artifact(
    run_directory: str | Path,
    calibration_path: str | Path,
    thresholds_path: str | Path,
    output_directory: str | Path,
    *,
    device: str = "cpu",
    dry_run: bool = False,
) -> dict[str, Any]:
    run_root = Path(run_directory).resolve()
    manifest = verify_run_artifact(run_root)
    prediction_path = run_root / "validation_predictions.parquet"
    calibration_file = Path(calibration_path).resolve()
    thresholds_file = Path(thresholds_path).resolve()
    calibration = _load_calibration(calibration_file, manifest, prediction_path)
    thresholds = _load_thresholds(
        thresholds_file,
        manifest,
        prediction_path,
        calibration_file,
    )
    destination = Path(output_directory).resolve()
    lock_path = run_root / "FINAL_TEST.lock.json"
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite final test artifact: {destination}")
    if lock_path.exists():
        raise ArtifactContractError("final test has already been evaluated for this run")
    if dry_run:
        return {
            "planned": True,
            "run_id": manifest["run_id"],
            "output": str(destination),
            "calibration_sha256": sha256_file(calibration_file),
            "thresholds_sha256": sha256_file(thresholds_file),
            "frozen_test": True,
        }
    lock_path.write_text(
        json.dumps(
            {
                "status": "in_progress",
                "started_at_utc": datetime.now(UTC).isoformat(),
                "run_id": manifest["run_id"],
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    staging: Path | None = None
    try:
        raw = _predict_run_split(run_root, manifest, "test", device=device)
        cumulative = _calibrated_probabilities(raw, calibration)
        raw["cumulative_event_probability"] = [value.tolist() for value in cumulative]
        raw["event_probability"] = cumulative[:, -1]
        raw["calibrated"] = True
        dataset_root = Path(str(manifest["dataset_path"]))
        dataset_manifest = verify_dataset_artifact(dataset_root)
        if int(dataset_manifest.get("stride_hours", 0)) != 1:
            raise ArtifactContractError("final event evaluation requires an hourly timeline")
        events = _events_for_split(
            dataset_root,
            dataset_manifest,
            "test",
            site_ids=set(raw["site_id"].astype(str)),
        )
        threshold = _threshold_value(thresholds)
        metrics = _metrics_payload(
            raw,
            cumulative,
            manifest=manifest,
            split="test",
            threshold=threshold,
            events=events,
        )
        metrics.update(
            {
                "schema_version": FINAL_TEST_SCHEMA_VERSION,
                "run_id": manifest["run_id"],
                "frozen_test": True,
                "test_used_for_tuning": False,
                "calibration_fitted_split": "validation",
                "thresholds_fitted_split": "validation",
                "calibration_sha256": sha256_file(calibration_file),
                "thresholds_sha256": sha256_file(thresholds_file),
            }
        )
        staging = _new_staging(destination)
        raw.to_parquet(staging / "test_predictions.parquet", index=False, engine="pyarrow")
        _write_json(staging / "test_metrics.json", metrics)
        files = _inventory(staging, excluded={"final_test_manifest.json"})
        final_manifest = {
            "schema_version": FINAL_TEST_SCHEMA_VERSION,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "run_id": manifest["run_id"],
            "frozen_test": True,
            "test_used_for_tuning": False,
            "synthetic_data": bool(manifest["synthetic_data"]),
            "scientific_result": False,
            "files": files,
        }
        _write_json(staging / "final_test_manifest.json", final_manifest)
        _commit_staging(staging, destination)
        staging = None
        _write_json(
            lock_path,
            {
                "status": "complete",
                "completed_at_utc": datetime.now(UTC).isoformat(),
                "run_id": manifest["run_id"],
                "artifact_path": str(destination),
                "artifact_manifest_sha256": sha256_file(destination / "final_test_manifest.json"),
                "calibration_sha256": sha256_file(calibration_file),
                "thresholds_sha256": sha256_file(thresholds_file),
            },
        )
    except Exception:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        lock_path.unlink(missing_ok=True)
        raise
    return {
        "planned": False,
        "run_id": manifest["run_id"],
        "output": str(destination),
        "split": "test",
        "frozen_test": True,
        "test_used_for_tuning": False,
        "threshold": _threshold_value(thresholds),
    }


def run_loso_artifact(
    dataset_directory: str | Path,
    config_path: str | Path,
    output_directory: str | Path,
    *,
    variant: Literal["obs_only_tcn", "hybrid_tcn"] | None = None,
    max_epochs: int | None = None,
    batch_size: int | None = None,
    device: str = "cpu",
    dry_run: bool = False,
) -> dict[str, Any]:
    destination = Path(output_directory).resolve()
    dataset_root, dataset_manifest, dataset_config, schema, tables = _load_dataset_context(
        dataset_directory
    )
    config = load_config(config_path)
    _config_compatible(config, dataset_config)
    resolved_variant = variant or config.mode.model_variant
    folds = build_leave_one_site_out_folds(tables["sample_index"])
    fold_plan = {
        site_id: {
            "train_rows": int(fold["cv_split"].astype(str).eq("train").sum()),
            "validation_rows": int(fold["cv_split"].astype(str).eq("validation").sum()),
            "final_test_frozen": bool(
                fold.loc[fold["split"].astype(str).eq("test"), "cv_split"]
                .astype(str)
                .eq("excluded")
                .all()
            ),
        }
        for site_id, fold in folds.items()
    }
    if not all(item["final_test_frozen"] for item in fold_plan.values()):
        raise ArtifactContractError("LOSO plan does not preserve the final test split")
    if dry_run:
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite LOSO artifact: {destination}")
        return {
            "planned": True,
            "output": str(destination),
            "variant": resolved_variant,
            "fold_count": len(folds),
            "folds": fold_plan,
            "final_test_evaluated": False,
        }
    staging = _new_staging(destination)
    records: list[dict[str, Any]] = []
    try:
        for held_out, fold in folds.items():
            train_index = fold.loc[fold["cv_split"].astype(str).eq("train")].copy()
            validation_index = fold.loc[fold["cv_split"].astype(str).eq("validation")].copy()
            train_index["split"] = "train"
            validation_index["split"] = "validation"
            fold_directory = staging / "folds" / held_out
            fold_manifest = _train_tcn_at(
                fold_directory,
                run_id=f"{destination.name}-{held_out}",
                dataset_root=dataset_root,
                dataset_manifest=dataset_manifest,
                config=config,
                schema=schema,
                tables=tables,
                train_index=train_index.reset_index(drop=True),
                validation_index=validation_index.reset_index(drop=True),
                variant=resolved_variant,
                max_epochs=max_epochs,
                batch_size=batch_size,
                device=device,
                run_kind="impactnet_loso",
                held_out_site_id=held_out,
            )
            predictions = pd.read_parquet(fold_directory / "validation_predictions.parquet")
            cumulative = _calibrated_probabilities(predictions, None)
            dataset_events = _events_for_split(
                dataset_root,
                dataset_manifest,
                "validation",
                site_ids={held_out},
            )
            fold_metrics = _metrics_payload(
                predictions,
                cumulative,
                manifest=fold_manifest,
                split="validation",
                threshold=0.5,
                events=dataset_events,
            )
            _write_json(fold_directory / "fold_metrics.json", fold_metrics)
            event_recall = fold_metrics["event_metrics"].get("event_recall")
            records.append(
                {
                    "held_out_site_id": held_out,
                    "event_recall": event_recall,
                    "insufficient_event_evidence": event_recall is None,
                    "24h_pr_auc": fold_metrics["horizon_metrics"].get("24h", {}).get("pr_auc"),
                    "train_rows": fold_plan[held_out]["train_rows"],
                    "validation_rows": fold_plan[held_out]["validation_rows"],
                }
            )
        metrics_frame = pd.DataFrame(records)
        complete = metrics_frame["event_recall"].notna().all()
        summary: dict[str, Any]
        if complete:
            summary = summarize_leave_one_site_out(metrics_frame)
            summary["scientific_summary_available"] = True
        else:
            summary = {
                "folds": len(records),
                "per_site": records,
                "scientific_summary_available": False,
                "insufficient_evidence": True,
                "reason": "one or more held-out sites have no confirmed validation event",
            }
        summary.update(
            {
                "schema_version": "impactnet-loso-v1",
                "synthetic_data": bool(dataset_manifest["synthetic_data"]),
                "scientific_result": False,
                "final_test_evaluated": False,
                "final_test_frozen": True,
            }
        )
        _write_json(staging / "loso_summary.json", summary)
        files = _inventory(staging, excluded={"loso_manifest.json"})
        _write_json(
            staging / "loso_manifest.json",
            {
                "schema_version": "impactnet-loso-v1",
                "created_at_utc": datetime.now(UTC).isoformat(),
                "dataset_id": dataset_manifest["dataset_id"],
                "variant": resolved_variant,
                "fold_count": len(folds),
                "final_test_evaluated": False,
                "files": files,
            },
        )
        _commit_staging(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "planned": False,
        "output": str(destination),
        "variant": resolved_variant,
        "fold_count": len(folds),
        "scientific_summary_available": complete,
        "final_test_evaluated": False,
    }


def export_bundle_artifact(
    run_directory: str | Path,
    calibration_path: str | Path,
    thresholds_path: str | Path,
    destination: str | Path,
    *,
    model_version: str,
    coverage_scope: str,
    model_card_path: str | Path | None = None,
    device: str = "cpu",
    dry_run: bool = False,
) -> dict[str, Any]:
    run_root = Path(run_directory).resolve()
    manifest = verify_run_artifact(run_root)
    if manifest.get("model_kind") != "impactnet" or manifest.get("run_kind") != "impactnet":
        raise ArtifactContractError("only a primary ImpactNet run can be exported as a bundle")
    lock = _read_json(run_root / "FINAL_TEST.lock.json")
    if lock.get("status") != "complete" or lock.get("run_id") != manifest.get("run_id"):
        raise ArtifactContractError("bundle export requires one completed frozen final test")
    prediction_path = run_root / "validation_predictions.parquet"
    calibration_file = Path(calibration_path).resolve()
    thresholds_file = Path(thresholds_path).resolve()
    calibration = _load_calibration(calibration_file, manifest, prediction_path)
    thresholds = _load_thresholds(
        thresholds_file,
        manifest,
        prediction_path,
        calibration_file,
    )
    if lock.get("calibration_sha256") != sha256_file(calibration_file):
        raise ArtifactContractError("final test used a different calibration")
    if lock.get("thresholds_sha256") != sha256_file(thresholds_file):
        raise ArtifactContractError("final test used different thresholds")
    bundle_path = Path(destination).resolve()
    if bundle_path.exists():
        raise FileExistsError(f"refusing to overwrite bundle: {bundle_path}")
    architecture = ImpactNetConfig.from_dict(_read_json(run_root / "architecture.json"))
    model = ImpactNet(architecture)
    model.load_state_dict(load_file(str(run_root / "model.safetensors"), device=device))
    preprocessing = _read_json(run_root / "preprocessing.json")
    schema = _read_json(run_root / "feature_schema.json")
    schema["dataset_manifest_hash"] = manifest["dataset_manifest_sha256"]
    dataset_root = Path(str(manifest["dataset_path"]))
    sites = _json_safe(pd.read_parquet(dataset_root / "sites.parquet").to_dict(orient="records"))
    config = ImpactConfig.model_validate(manifest["resolved_config"])
    if model_card_path is None:
        model_card = (
            f"# {config.model_name}\n\n"
            "Research-only Shadow Mode model. Official warnings remain authoritative.\n\n"
            f"- Run: `{manifest['run_id']}`\n"
            f"- Dataset: `{manifest['dataset_id']}`\n"
            f"- Label mode: `{config.scope.label_mode}`\n"
            f"- Synthetic data: `{str(manifest['synthetic_data']).lower()}`\n"
            "- Calibration and thresholds: validation only\n"
            "- Final test: frozen before bundle export\n"
        )
    else:
        model_card = Path(model_card_path).read_text(encoding="utf-8")
    plan = {
        "run_id": manifest["run_id"],
        "destination": str(bundle_path),
        "model_version": model_version,
        "model_name": config.model_name,
        "synthetic_data": bool(manifest["synthetic_data"]),
        "shadow_mode": True,
        "frozen_test_verified": True,
    }
    if dry_run:
        return {**plan, "planned": True}
    staging = _new_staging(bundle_path)
    try:
        create_model_bundle(
            staging,
            model,
            model_version=model_version,
            model_name=config.model_name,
            label_mode=config.scope.label_mode,
            coverage_scope=coverage_scope,
            horizons_hours=config.windows.output_horizons_hours,
            preprocessing=preprocessing,
            calibration=calibration,
            thresholds=thresholds,
            feature_schema=schema,
            sites=sites,
            model_card=model_card,
            synthetic_data=bool(manifest["synthetic_data"]),
        )
        _commit_staging(staging, bundle_path)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**plan, "planned": False, "bundle": str(bundle_path)}


def replay_shadow_artifact(
    bundle_directory: str | Path,
    input_path: str | Path,
    output_path: str | Path,
    *,
    obs_only_bundle: str | Path | None = None,
    device: str = "cpu",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Replay validated feature requests offline and write a hashed JSONL artifact."""

    from .serve.model_loader import BundlePredictor
    from .serve.schemas import FeaturePredictionRequest

    bundle_root = Path(bundle_directory).resolve()
    source = Path(input_path).resolve()
    destination = Path(output_path).resolve()
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".jsonl":
        raise ArtifactContractError("shadow replay input must be a .jsonl file")
    if destination.suffix.lower() != ".jsonl":
        raise ArtifactContractError("shadow replay output must be a .jsonl file")
    if destination == source:
        raise ArtifactContractError("shadow replay output must differ from its input")
    if destination.exists() or manifest_path.exists():
        raise FileExistsError("refusing to overwrite replay output or manifest")
    predictor = BundlePredictor(
        bundle_root,
        obs_only=(Path(obs_only_bundle).resolve() if obs_only_bundle is not None else None),
        device=device,
    )
    bundle_manifest = predictor.model_info()
    requests: list[FeaturePredictionRequest] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ArtifactContractError(
                f"invalid replay JSON on line {line_number}: {error.msg}"
            ) from error
        if not isinstance(payload, dict):
            raise ArtifactContractError(
                f"replay request on line {line_number} must be a JSON object"
            )
        try:
            requests.append(FeaturePredictionRequest.model_validate(payload))
        except ValueError as error:
            raise ArtifactContractError(
                f"invalid FeaturePredictionRequest on line {line_number}: {error}"
            ) from error
    if not requests:
        raise ArtifactContractError("shadow replay input contains no requests")
    plan = {
        "bundle": str(bundle_root),
        "input": str(source),
        "output": str(destination),
        "manifest": str(manifest_path),
        "request_count": len(requests),
        "model_version": bundle_manifest["model_version"],
        "synthetic_data": bool(bundle_manifest.get("synthetic_data", False)),
        "shadow_mode": True,
        "scientific_result": False,
    }
    if dry_run:
        return {**plan, "planned": True, "requests_validated": len(requests)}

    responses: list[dict[str, Any]] = []
    for request_index, request in enumerate(requests, 1):
        request_digest = hashlib.sha256(_canonical_bytes(request.model_dump(mode="json")))
        request_id = f"replay-{request_index:08d}-{request_digest.hexdigest()[:16]}"
        response = predictor.predict(request, request_id=request_id).model_dump(mode="json")
        responses.append(response)
    destination.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    output_staging = destination.with_name(f".{destination.name}.{token}")
    manifest_staging = manifest_path.with_name(f".{manifest_path.name}.{token}")
    committed_output = False
    try:
        output_staging.write_text(
            "".join(
                json.dumps(response, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
                for response in responses
            ),
            encoding="utf-8",
            newline="\n",
        )
        manifest = {
            "schema_version": "impactnet-shadow-replay-v1",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "model_version": bundle_manifest["model_version"],
            "model_name": bundle_manifest["model_name"],
            "bundle_manifest_sha256": sha256_file(bundle_root / "manifest.json"),
            "bundle_checksums_sha256": sha256_file(bundle_root / "sha256sums.txt"),
            "input_path": str(source),
            "input_sha256": sha256_file(source),
            "output_filename": destination.name,
            "output_sha256": sha256_file(output_staging),
            "request_count": len(requests),
            "response_count": len(responses),
            "shadow_mode": True,
            "synthetic_data": bool(bundle_manifest.get("synthetic_data", False)),
            "scientific_result": False,
        }
        _write_json(manifest_staging, manifest)
        output_staging.replace(destination)
        committed_output = True
        manifest_staging.replace(manifest_path)
    except Exception:
        output_staging.unlink(missing_ok=True)
        manifest_staging.unlink(missing_ok=True)
        if committed_output:
            destination.unlink(missing_ok=True)
        raise
    return {
        **plan,
        "planned": False,
        "output_sha256": manifest["output_sha256"],
        "response_count": len(responses),
    }


__all__ = [
    "ArtifactContractError",
    "build_dataset_artifact",
    "build_event_catalog_artifact",
    "calibrate_temperature_artifact",
    "evaluate_final_test_artifact",
    "evaluate_validation_artifact",
    "export_bundle_artifact",
    "replay_shadow_artifact",
    "run_loso_artifact",
    "select_thresholds_artifact",
    "sha256_file",
    "train_impactnet_artifact",
    "train_logistic_artifact",
    "validate_event_catalog_artifact",
    "verify_dataset_artifact",
    "verify_run_artifact",
]
