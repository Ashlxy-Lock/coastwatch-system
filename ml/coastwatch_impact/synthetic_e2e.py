"""Deterministic synthetic-only engineering end-to-end orchestration.

This module intentionally does not claim scientific performance.  It proves
that the canonical synthetic data, leakage-safe preprocessing/windowing,
observation-only TCN, validation-only calibration/threshold selection, frozen
test evaluation, safe bundle loading, and Shadow API can work together.
"""

from __future__ import annotations

import gc
import json
import math
import os
import random
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
import yaml
from fastapi.testclient import TestClient
from torch.nn.utils import clip_grad_norm_
from torch.utils.data import DataLoader, Dataset, Subset

from coastwatch_impact.data import (
    CoastWatchWindowDataset,
    TrainOnlyPreprocessor,
    assign_global_time_split,
    build_synthetic_sample_index,
    collate_window_batch,
    default_synthetic_split_config,
    generate_synthetic_dataset,
    sample_training_rows,
    sha256_file,
)
from coastwatch_impact.evaluation import (
    bootstrap_event_metrics,
    compute_horizon_metrics,
    cumulative_targets_from_hazards,
    evaluate_alert_events,
    fit_global_temperature,
    select_operating_thresholds,
    water_quantile_metrics,
)
from coastwatch_impact.export.model_bundle import (
    create_model_bundle,
    load_model_bundle,
    verify_model_bundle,
)
from coastwatch_impact.models.impactnet import ImpactNet, ImpactNetConfig
from coastwatch_impact.models.losses import multitask_loss
from coastwatch_impact.provenance import environment_record, git_state
from coastwatch_impact.serve.app import create_app
from coastwatch_impact.serve.model_loader import BundlePredictor

SYNTHETIC_ONLY_NOTICE = (
    "Synthetic engineering run only. It is not evidence of real coastal-flood "
    "prediction performance and must never drive public warnings."
)

PAST_FEATURES = (
    "water_level_m_aod",
    "predicted_tide_m_aod",
    "surge_residual_m",
    "significant_wave_height_m",
    "wave_period_s",
    "wind_speed_m_s",
    "wind_gust_m_s",
    "surface_pressure_hpa",
    "rainfall_mm_h",
    "air_temperature_c",
    "humidity_percent",
)

FUTURE_PLACEHOLDER_FEATURES = ("forecast_total_water_level_m_aod",)

STATIC_FEATURES = (
    "latitude",
    "longitude",
    "ground_elevation_m_aod",
    "defence_crest_height_m_aod",
    "distance_to_coast_m",
    "historic_flood_fraction",
    "low_lying_area_fraction",
    "road_exposure_count",
    "building_exposure_count",
)


@dataclass(frozen=True, slots=True)
class SyntheticE2EConfig:
    """Small CPU configuration; defaults still use the required 180-day fixture."""

    seed: int = 20260813
    duration_days: int = 180
    epochs: int = 2
    batch_size: int = 64
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    grad_clip_norm: float = 1.0
    train_stride_hours: int = 6
    event_buffer_hours: int = 72
    negative_to_positive_target_ratio: float = 4.0
    normalize_positive_weight_per_event: bool = True
    hidden_channels: int = 8
    num_blocks: int = 2
    kernel_size: int = 3
    decoder_hidden_dim: int = 16
    decoder_layers: int = 1
    lead_embedding_dim: int = 4
    dropout: float = 0.0
    event_loss_weight: float = 1.0
    water_loss_weight: float = 0.4
    maximum_pos_weight: float = 20.0
    horizons_hours: tuple[int, ...] = (1, 3, 6, 12, 24)
    temperature_iterations: int = 48
    threshold_candidates: tuple[float, ...] = (
        0.05,
        0.10,
        0.15,
        0.20,
        0.30,
        0.40,
        0.50,
        0.60,
        0.70,
        0.80,
        0.90,
        0.95,
    )
    torch_num_threads: int = 1
    device: str = "cpu"

    def __post_init__(self) -> None:
        if self.duration_days < 12:
            raise ValueError("duration_days must be at least 12 for train/validation/test events")
        if self.epochs < 1 or self.batch_size < 1:
            raise ValueError("epochs and batch_size must be positive")
        if self.learning_rate <= 0 or self.weight_decay < 0 or self.grad_clip_norm <= 0:
            raise ValueError("optimizer values are invalid")
        if self.train_stride_hours < 1:
            raise ValueError("train_stride_hours must be positive")
        if self.event_buffer_hours < 0:
            raise ValueError("event_buffer_hours must be non-negative")
        if self.hidden_channels < 2 or self.num_blocks < 1 or self.kernel_size < 2:
            raise ValueError("TCN dimensions are invalid")
        if self.decoder_hidden_dim < 1 or self.decoder_layers < 1:
            raise ValueError("decoder dimensions are invalid")
        if self.lead_embedding_dim < 1 or not 0.0 <= self.dropout < 1.0:
            raise ValueError("lead embedding or dropout is invalid")
        if self.event_loss_weight < 0 or self.water_loss_weight < 0:
            raise ValueError("loss weights must be non-negative")
        if self.maximum_pos_weight <= 0:
            raise ValueError("maximum_pos_weight must be positive")
        if self.device != "cpu":
            raise ValueError("synthetic engineering E2E is intentionally CPU-only")
        if self.torch_num_threads < 1:
            raise ValueError("torch_num_threads must be positive")
        if tuple(sorted(set(self.horizons_hours))) != self.horizons_hours:
            raise ValueError("horizons_hours must be unique and increasing")
        if self.horizons_hours[0] < 1 or self.horizons_hours[-1] > 24:
            raise ValueError("horizons_hours must lie inside the 24-hour window")
        candidates = np.asarray(self.threshold_candidates, dtype=np.float64)
        if candidates.size == 0 or not np.isfinite(candidates).all():
            raise ValueError("threshold_candidates must be non-empty and finite")
        if np.any((candidates < 0.0) | (candidates > 1.0)):
            raise ValueError("threshold_candidates must lie in [0, 1]")


@dataclass(frozen=True, slots=True)
class SyntheticE2EResult:
    run_directory: Path
    bundle_directory: Path
    run_manifest_path: Path
    test_metrics_path: Path
    validation_predictions_path: Path
    test_predictions_path: Path
    api_smoke_path: Path
    model_sha256: str
    api_status_code: int
    synthetic_only: bool = True
    elapsed_seconds: float = 0.0
    stage_timings_path: Path | None = None


@dataclass(slots=True)
class _PredictionArrays:
    hazard_logits: np.ndarray
    hazard_target: np.ndarray
    hazard_mask: np.ndarray
    water_quantiles: np.ndarray
    water_target: np.ndarray
    water_mask: np.ndarray
    site_ids: list[str]
    prediction_times: list[datetime]
    event_ids: list[str | None]
    storm_group_ids: list[str | None]
    split: str
    mean_loss: float


class _StageTracker:
    """Durable progress so a detached long run is never mistaken for an exit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.started = time.perf_counter()
        self.started_at_utc = datetime.now(UTC)
        self.current_stage: str | None = None
        self.current_started = self.started
        self.completed: list[dict[str, Any]] = []
        self.details: dict[str, Any] = {}

    @property
    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self.started

    def advance(self, stage: str, **details: Any) -> None:
        now = time.perf_counter()
        if self.current_stage is not None:
            self.completed.append(
                {
                    "stage": self.current_stage,
                    "elapsed_seconds": now - self.current_started,
                    **self.details,
                }
            )
        self.current_stage = stage
        self.current_started = now
        self.details = details
        self._persist("running")

    def heartbeat(self, **details: Any) -> None:
        self.details.update(details)
        self._persist("running")

    def complete(self) -> None:
        now = time.perf_counter()
        if self.current_stage is not None:
            self.completed.append(
                {
                    "stage": self.current_stage,
                    "elapsed_seconds": now - self.current_started,
                    **self.details,
                }
            )
        self.current_stage = None
        self.details = {}
        self._persist("complete")

    def fail(self, error: BaseException) -> None:
        self.details.update(error_type=type(error).__name__, error=str(error))
        self._persist("failed")

    def _persist(self, status: str) -> None:
        _write_json(
            self.path,
            {
                "status": status,
                "synthetic_only": True,
                "started_at_utc": self.started_at_utc.isoformat(),
                "updated_at_utc": datetime.now(UTC).isoformat(),
                "elapsed_seconds": self.elapsed_seconds,
                "current_stage": self.current_stage,
                "current_stage_elapsed_seconds": (
                    time.perf_counter() - self.current_started
                    if self.current_stage is not None
                    else None
                ),
                "current_stage_details": self.details,
                "completed_stages": self.completed,
                "memory": _memory_snapshot(),
            },
        )


def _memory_snapshot() -> dict[str, float | None]:
    """Best-effort current process memory without adding a dependency."""

    if os.name != "nt":
        return {"working_set_mb": None, "private_mb": None}
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        current_process = ctypes.windll.kernel32.GetCurrentProcess()
        success = ctypes.windll.psapi.GetProcessMemoryInfo(
            current_process,
            ctypes.byref(counters),
            counters.cb,
        )
        if not success:
            raise OSError("GetProcessMemoryInfo failed")
        return {
            "working_set_mb": counters.WorkingSetSize / (1024 * 1024),
            "private_mb": counters.PrivateUsage / (1024 * 1024),
        }
    except (AttributeError, OSError, TypeError, ValueError):
        return {"working_set_mb": None, "private_mb": None}


class _ObsOnlySyntheticWindowDataset(CoastWatchWindowDataset):
    """Skip 24 pointless as-of lookups when the experiment is observation-only."""

    def _future_window(self, site_id: str, prediction: pd.Timestamp) -> pd.DataFrame:
        del site_id, prediction
        return pd.DataFrame(
            np.nan,
            index=range(self.horizon_hours),
            columns=self.future_feature_names,
        )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _prepare_output_directory(path: str | Path) -> Path:
    destination = Path(path)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty E2E run: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _seed_everything(config: SyntheticE2EConfig) -> None:
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    torch.set_num_threads(config.torch_num_threads)
    torch.use_deterministic_algorithms(True)


def _mask_unusable_training_values(frame: pd.DataFrame, features: tuple[str, ...]) -> pd.DataFrame:
    clean = frame.copy()
    if "quality_flag" in clean:
        rejected = (
            clean["quality_flag"]
            .fillna("")
            .astype(str)
            .str.lower()
            .isin({"stale", "invalid", "rejected", "missing"})
        )
        clean.loc[rejected, list(features)] = np.nan
    for feature in features:
        missing_column = f"{feature}__missing"
        if missing_column in clean:
            missing = clean[missing_column].fillna(True).astype(bool)
            clean.loc[missing, feature] = np.nan
    return clean


def _model_observations(frame: pd.DataFrame) -> pd.DataFrame:
    """Retain only columns consumed by obs-only windows and their masks."""

    columns = ["site_id", "coastal_zone_id", "timestamp_utc", *PAST_FEATURES]
    columns.extend(
        f"{feature}__missing" for feature in PAST_FEATURES if f"{feature}__missing" in frame
    )
    if "quality_flag" in frame:
        columns.append("quality_flag")
    return frame.loc[:, list(dict.fromkeys(columns))].copy()


def _model_static_features(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["coastal_zone_id", *STATIC_FEATURES]
    columns.extend(
        f"{feature}__missing" for feature in STATIC_FEATURES if f"{feature}__missing" in frame
    )
    return frame.loc[:, list(dict.fromkeys(columns))].copy()


def _fit_preprocessors(
    observations: pd.DataFrame,
    static_features: pd.DataFrame,
    *,
    train_end_utc: datetime,
    dataset_manifest_hash: str,
) -> tuple[TrainOnlyPreprocessor, TrainOnlyPreprocessor]:
    observation_times = pd.to_datetime(observations["timestamp_utc"], utc=True)
    train_observations = observations.loc[observation_times <= pd.Timestamp(train_end_utc)].copy()
    train_observations["split"] = "train"
    train_observations = _mask_unusable_training_values(train_observations, PAST_FEATURES)
    past = TrainOnlyPreprocessor(
        PAST_FEATURES,
        dataset_manifest_hash=dataset_manifest_hash,
    ).fit(train_observations, timestamp_col="timestamp_utc")

    train_static = static_features.copy()
    train_static["split"] = "train"
    static = TrainOnlyPreprocessor(
        STATIC_FEATURES,
        dataset_manifest_hash=dataset_manifest_hash,
    ).fit(train_static, timestamp_col=None)
    return past, static


def _subsample_train(
    index: pd.DataFrame,
    stride_hours: int,
    *,
    negative_to_positive_target_ratio: float,
    normalize_positive_weight_per_event: bool,
) -> pd.DataFrame:
    train = index[index["split"].astype(str) == "train"].copy()
    if train.empty:
        raise RuntimeError("synthetic split produced no training samples")
    times = pd.to_datetime(train["prediction_time_utc"], utc=True)
    origin = times.min()
    offsets = ((times - origin).dt.total_seconds() // 3600).astype(int)
    selected = train.loc[offsets % stride_hours == 0].copy()
    if selected.empty:
        raise RuntimeError("train_stride_hours removed every training sample")
    return sample_training_rows(
        selected,
        negative_min_spacing_hours=stride_hours,
        negative_to_positive_target_ratio=negative_to_positive_target_ratio,
        normalize_positive_weight_per_event=normalize_positive_weight_per_event,
    )


def _make_dataset(
    observations: pd.DataFrame,
    empty_forecasts: pd.DataFrame,
    static_features: pd.DataFrame,
    sample_index: pd.DataFrame,
    *,
    past_preprocessor: TrainOnlyPreprocessor | None,
    static_preprocessor: TrainOnlyPreprocessor | None,
) -> CoastWatchWindowDataset:
    return _ObsOnlySyntheticWindowDataset(
        observations,
        empty_forecasts,
        static_features,
        sample_index,
        past_feature_names=PAST_FEATURES,
        future_feature_names=FUTURE_PLACEHOLDER_FEATURES,
        static_feature_names=STATIC_FEATURES,
        past_hours=72,
        horizon_hours=24,
        source_model="synthetic_issued_model",
        past_preprocessor=past_preprocessor,
        static_preprocessor=static_preprocessor,
        reject_future_only_forecasts=False,
    )


def _loader(
    dataset: Dataset[dict[str, Any]],
    *,
    batch_size: int,
    shuffle: bool,
    seed: int,
) -> DataLoader[dict[str, Any]]:
    generator = torch.Generator().manual_seed(seed)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=0,
        collate_fn=collate_window_batch,
        generator=generator,
    )


def _model_kwargs(batch: dict[str, Any], device: str) -> dict[str, torch.Tensor]:
    return {
        "past_observations": batch["past_values"].to(device),
        "static_features": batch["static_values"].to(device),
        "future_time_features": batch["lead_features"].to(device),
        "past_mask": batch["past_mask"].to(device),
        "static_mask": batch["static_mask"].to(device),
    }


def _loss_for_batch(
    outputs: dict[str, torch.Tensor],
    batch: dict[str, Any],
    *,
    device: str,
    pos_weight: float,
    config: SyntheticE2EConfig,
) -> dict[str, torch.Tensor]:
    sample_weight = batch["sample_weight"].to(device)
    return multitask_loss(
        hazard_logits=outputs["hazard_logits"],
        hazard_target=batch["hazard_target"].to(device),
        hazard_mask=batch["hazard_mask"].to(device),
        water_quantiles=outputs["water_quantiles"],
        water_target=batch["water_target"].to(device),
        water_mask=batch["water_mask"].to(device),
        event_weight=config.event_loss_weight,
        water_weight=config.water_loss_weight,
        pos_weight=pos_weight,
        event_sample_weight=sample_weight,
        water_sample_weight=None,
    )


def _positive_weight(index: pd.DataFrame, maximum: float) -> float:
    targets = np.asarray(index["hazard_target"].tolist(), dtype=np.float64)
    mask = np.asarray(index["hazard_mask"].tolist(), dtype=np.bool_)
    positives = float(targets[mask].sum())
    negatives = float(mask.sum() - positives)
    if positives <= 0.0:
        return 1.0
    return float(min(maximum, max(1.0, negatives / positives)))


def _sample_weight_distribution(index: pd.DataFrame) -> dict[str, Any]:
    weights = pd.to_numeric(index["sample_weight"], errors="raise")
    positive = index["hazard_target"].map(
        lambda value: bool(np.nanmax(np.asarray(value, dtype=np.float64)) > 0.0)
    )
    return {
        "all_count": int(len(weights)),
        "all_minimum": float(weights.min()),
        "all_median": float(weights.median()),
        "all_maximum": float(weights.max()),
        "all_sum": float(weights.sum()),
        "positive_count": int(positive.sum()),
        "positive_sum": float(weights[positive].sum()),
        "negative_count": int((~positive).sum()),
        "negative_sum": float(weights[~positive].sum()),
    }


def _mean_validation_loss(
    model: ImpactNet,
    loader: DataLoader[dict[str, Any]],
    *,
    config: SyntheticE2EConfig,
    pos_weight: float,
) -> float:
    model.eval()
    losses: list[float] = []
    with torch.inference_mode():
        for batch in loader:
            output = model(**_model_kwargs(batch, config.device))
            loss = _loss_for_batch(
                output,
                batch,
                device=config.device,
                pos_weight=pos_weight,
                config=config,
            )["loss"]
            losses.append(float(loss.detach().cpu()))
    return float(np.mean(losses)) if losses else float("nan")


def _train(
    model: ImpactNet,
    train_loader: DataLoader[dict[str, Any]],
    validation_loader: DataLoader[dict[str, Any]],
    *,
    config: SyntheticE2EConfig,
    pos_weight: float,
    progress: _StageTracker | None = None,
) -> list[dict[str, float | int]]:
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    history: list[dict[str, float | int]] = []
    for epoch in range(1, config.epochs + 1):
        model.train()
        batch_losses: list[float] = []
        for batch in train_loader:
            optimizer.zero_grad(set_to_none=True)
            output = model(**_model_kwargs(batch, config.device))
            losses = _loss_for_batch(
                output,
                batch,
                device=config.device,
                pos_weight=pos_weight,
                config=config,
            )
            losses["loss"].backward()
            clip_grad_norm_(model.parameters(), config.grad_clip_norm)
            optimizer.step()
            batch_losses.append(float(losses["loss"].detach().cpu()))
        record: dict[str, float | int] = {
            "epoch": epoch,
            "train_loss": float(np.mean(batch_losses)),
            "validation_loss": _mean_validation_loss(
                model,
                validation_loader,
                config=config,
                pos_weight=pos_weight,
            ),
        }
        history.append(record)
        if progress is not None:
            progress.heartbeat(
                completed_epoch=epoch,
                total_epochs=config.epochs,
                train_loss=record["train_loss"],
                validation_loss=record["validation_loss"],
            )
    return history


def _predict(
    model: ImpactNet,
    loader: DataLoader[dict[str, Any]],
    *,
    config: SyntheticE2EConfig,
    pos_weight: float,
    split: str,
) -> _PredictionArrays:
    model.eval()
    logits: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    water: list[np.ndarray] = []
    water_target: list[np.ndarray] = []
    water_mask: list[np.ndarray] = []
    site_ids: list[str] = []
    prediction_times: list[datetime] = []
    event_ids: list[str | None] = []
    storm_group_ids: list[str | None] = []
    losses: list[float] = []
    with torch.inference_mode():
        for batch in loader:
            output = model(**_model_kwargs(batch, config.device))
            loss = _loss_for_batch(
                output,
                batch,
                device=config.device,
                pos_weight=pos_weight,
                config=config,
            )["loss"]
            losses.append(float(loss.detach().cpu()))
            logits.append(output["hazard_logits"].detach().cpu().numpy())
            targets.append(batch["hazard_target"].numpy())
            masks.append(batch["hazard_mask"].numpy())
            water.append(output["water_quantiles"].detach().cpu().numpy())
            water_target.append(batch["water_target"].numpy())
            water_mask.append(batch["water_mask"].numpy())
            site_ids.extend(str(value) for value in batch["site_id"])
            prediction_times.extend(batch["prediction_time_utc"])
            event_ids.extend(batch["event_id"])
            storm_group_ids.extend(batch["storm_group_id"])
            if any(value != split for value in batch["split"]):
                raise RuntimeError(f"{split} loader contained another split")
    return _PredictionArrays(
        hazard_logits=np.concatenate(logits),
        hazard_target=np.concatenate(targets),
        hazard_mask=np.concatenate(masks),
        water_quantiles=np.concatenate(water),
        water_target=np.concatenate(water_target),
        water_mask=np.concatenate(water_mask),
        site_ids=site_ids,
        prediction_times=prediction_times,
        event_ids=event_ids,
        storm_group_ids=storm_group_ids,
        split=split,
        mean_loss=float(np.mean(losses)),
    )


def _prediction_frame(
    arrays: _PredictionArrays,
    cumulative_probability: np.ndarray,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "site_id": arrays.site_ids,
            "prediction_time_utc": pd.to_datetime(arrays.prediction_times, utc=True),
            "split": arrays.split,
            "event_id": arrays.event_ids,
            "storm_group_id": arrays.storm_group_ids,
            "event_probability": cumulative_probability[:, -1],
            "hazard_logits": arrays.hazard_logits.tolist(),
            "cumulative_event_probability": cumulative_probability.tolist(),
            "hazard_target": arrays.hazard_target.tolist(),
            "hazard_mask": arrays.hazard_mask.tolist(),
            "water_p10": arrays.water_quantiles[..., 0].tolist(),
            "water_p50": arrays.water_quantiles[..., 1].tolist(),
            "water_p90": arrays.water_quantiles[..., 2].tolist(),
            "water_target": arrays.water_target.tolist(),
            "water_mask": arrays.water_mask.tolist(),
            "synthetic_only": True,
        }
    )


def _events_for_split(
    event_catalog: pd.DataFrame,
    sites: pd.DataFrame,
    *,
    start_exclusive: datetime,
    end_inclusive: datetime,
) -> pd.DataFrame:
    mapping = dict(zip(sites["coastal_zone_id"], sites["site_id"], strict=True))
    events = event_catalog.copy()
    events["site_id"] = events["coastal_zone_id"].map(mapping)
    onset = pd.to_datetime(events["onset_time_utc"], utc=True, errors="coerce")
    confidence = events["label_confidence"].astype(str)
    selected = (
        onset.notna()
        & (onset > pd.Timestamp(start_exclusive))
        & (onset <= pd.Timestamp(end_inclusive))
        & confidence.isin(["A", "B"])
        & events["impact_confirmed"].eq(True)
        & events["onset_precision"].astype(str).eq("exact_hour")
    )
    return events.loc[selected].reset_index(drop=True)


def _select_thresholds(
    validation_predictions: pd.DataFrame,
    validation_events: pd.DataFrame,
    config: SyntheticE2EConfig,
) -> dict[str, Any]:
    try:
        return select_operating_thresholds(
            validation_predictions[
                ["site_id", "prediction_time_utc", "event_probability", "split"]
            ],
            validation_events,
            split="validation",
            candidate_thresholds=config.threshold_candidates,
            conservative_minimum_recall=0.0,
        )
    except ValueError as error:
        return {
            "fitted_split": "validation",
            "selection_basis": "engineering_fallback",
            "fallback_reason": str(error),
            "selected": {
                name: {
                    "threshold": 0.5,
                    "constraint_met": False,
                }
                for name in ("sensitive", "balanced", "conservative")
            },
            "candidate_metrics": [],
        }


def _event_metrics(
    predictions: pd.DataFrame,
    events: pd.DataFrame,
    threshold: float,
) -> dict[str, Any]:
    try:
        result = evaluate_alert_events(
            predictions[["site_id", "prediction_time_utc", "event_probability"]],
            events,
            threshold,
        )
        return result.metrics
    except ValueError as error:
        return {"insufficient_evidence": True, "reason": str(error)}


def _research_bands(threshold: float) -> dict[str, float]:
    warning = float(np.clip(threshold, 0.05, 0.9))
    advisory = float(max(0.01, warning * 0.5))
    critical = float(min(0.99, max(warning + 0.1, warning * 1.25)))
    return {"advisory": advisory, "warning": warning, "critical": critical}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def _synthetic_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if "synthetic_only" not in frame or frame.empty:
        raise ValueError(f"synthetic plot source is empty or unmarked: {path}")
    if not frame["synthetic_only"].fillna(False).astype(bool).all():
        raise ValueError(f"refusing to plot a mixed/non-synthetic table: {path}")
    return frame


def _array_matrix(frame: pd.DataFrame, column: str, *, width: int = 24) -> np.ndarray:
    if column not in frame:
        raise ValueError(f"prediction table has no {column!r} column")
    rows = [np.asarray(value, dtype=np.float64) for value in frame[column]]
    if not rows or any(row.shape != (width,) for row in rows):
        shapes = sorted({str(row.shape) for row in rows})
        raise ValueError(f"{column} must contain {width}-element vectors; found {shapes}")
    return np.stack(rows)


def _horizon_vectors(
    predictions: pd.DataFrame,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray]:
    probabilities = _array_matrix(predictions, "cumulative_event_probability")[:, horizon - 1]
    targets, masks = cumulative_targets_from_hazards(
        _array_matrix(predictions, "hazard_target"),
        _array_matrix(predictions, "hazard_mask").astype(bool),
    )
    targets = targets[:, horizon - 1]
    masks = masks[:, horizon - 1]
    valid = masks & np.isfinite(probabilities) & np.isfinite(targets)
    return probabilities[valid], targets[valid].astype(np.int64)


def _precision_recall_points(
    probabilities: np.ndarray,
    targets: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float | None]:
    if probabilities.size == 0:
        return np.array([0.0]), np.array([1.0]), None
    order = np.argsort(-probabilities, kind="stable")
    sorted_targets = targets[order] == 1
    positives = int(sorted_targets.sum())
    if positives == 0:
        return np.array([0.0]), np.array([1.0]), None
    true_positive = np.cumsum(sorted_targets)
    false_positive = np.cumsum(~sorted_targets)
    precision = true_positive / np.maximum(true_positive + false_positive, 1)
    recall = true_positive / positives
    precision = np.concatenate(([1.0], precision))
    recall = np.concatenate(([0.0], recall))
    area = float(np.trapezoid(precision, recall))
    return recall, precision, area


def _balanced_threshold(validation_metrics: dict[str, Any]) -> float:
    selection = validation_metrics.get("threshold_selection", {})
    selected = selection.get("selected", {}) if isinstance(selection, dict) else {}
    balanced = selected.get("balanced", {}) if isinstance(selected, dict) else {}
    threshold = balanced.get("threshold") if isinstance(balanced, dict) else None
    if not isinstance(threshold, (int, float)):
        raise ValueError("validation balanced threshold is absent or invalid")
    value = float(threshold)
    if not np.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("validation balanced threshold is absent or invalid")
    return value


def _confirmed_events_for_predictions(
    event_catalog: pd.DataFrame,
    sites: pd.DataFrame,
    predictions: pd.DataFrame,
) -> pd.DataFrame:
    mapping = sites[["site_id", "coastal_zone_id"]].drop_duplicates("coastal_zone_id")
    events = event_catalog.merge(mapping, on="coastal_zone_id", how="left", validate="many_to_one")
    events["onset_time_utc"] = pd.to_datetime(events["onset_time_utc"], utc=True)
    start = pd.to_datetime(predictions["prediction_time_utc"], utc=True).min()
    end = pd.to_datetime(predictions["prediction_time_utc"], utc=True).max() + pd.Timedelta(
        hours=24
    )
    selected = (
        events["site_id"].notna()
        & events["impact_confirmed"].eq(True)
        & events["label_confidence"].astype(str).isin(["A", "B"])
        & events["onset_precision"].astype(str).eq("exact_hour")
        & events["onset_time_utc"].between(start, end)
    )
    return events.loc[selected].reset_index(drop=True)


def _issued_physics_forecast(
    forecasts: pd.DataFrame,
    *,
    site_id: str,
    origin: pd.Timestamp,
    horizon: int = 24,
) -> pd.DataFrame:
    valid_times = pd.date_range(origin + pd.Timedelta(hours=1), periods=horizon, freq="h")
    eligible = forecasts.loc[
        forecasts["site_id"].astype(str).eq(site_id)
        & (pd.to_datetime(forecasts["issue_time_utc"], utc=True) <= origin)
        & pd.to_datetime(forecasts["valid_time_utc"], utc=True).isin(valid_times)
    ].copy()
    if not eligible.empty:
        eligible["issue_time_utc"] = pd.to_datetime(eligible["issue_time_utc"], utc=True)
        eligible["valid_time_utc"] = pd.to_datetime(eligible["valid_time_utc"], utc=True)
        eligible = eligible.sort_values(["valid_time_utc", "issue_time_utc"], kind="stable")
        eligible = eligible.drop_duplicates("valid_time_utc", keep="last")
        eligible = eligible.set_index("valid_time_utc")
    output = eligible.reindex(valid_times).rename_axis("valid_time_utc").reset_index()
    output["quality_flag"] = output.get("quality_flag", pd.Series(index=output.index)).fillna(
        "unavailable"
    )
    return output


def _timeline_origin(
    site_predictions: pd.DataFrame,
    forecasts: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    site_id: str,
    onset: pd.Timestamp,
    preferred: pd.Timestamp,
) -> pd.Series:
    times = pd.to_datetime(site_predictions["prediction_time_utc"], utc=True)
    candidates = site_predictions.loc[(times < onset) & (times >= onset - pd.Timedelta(hours=24))]
    if candidates.empty:
        nearest = (times - preferred).abs().idxmin()
        return site_predictions.loc[nearest]
    scored: list[tuple[int, float, int]] = []
    site_observations = observations.loc[observations["site_id"].astype(str).eq(site_id)].copy()
    site_observations["timestamp_utc"] = pd.to_datetime(
        site_observations["timestamp_utc"], utc=True
    )
    for index, row in candidates.iterrows():
        origin = pd.Timestamp(row["prediction_time_utc"])
        physics = _issued_physics_forecast(forecasts, site_id=site_id, origin=origin)
        degraded_physics = int(
            (~physics["quality_flag"].astype(str).eq("synthetic_good").to_numpy()).sum()
        )
        valid_times = pd.DatetimeIndex(physics["valid_time_utc"])
        observed = site_observations.set_index("timestamp_utc").reindex(valid_times)
        degraded_observations = int(
            (
                ~observed.get("quality_flag", pd.Series(index=observed.index, dtype="object"))
                .fillna("unavailable")
                .astype(str)
                .eq("good")
                .to_numpy()
            ).sum()
        )
        distance = abs((origin - preferred) / pd.Timedelta(hours=1))
        scored.append((degraded_physics + degraded_observations, -float(distance), int(index)))
    return candidates.loc[max(scored)[2]]


def _plot_reliability(
    axis: Any,
    bins: list[dict[str, Any]],
    *,
    split: str,
    horizon: int,
) -> None:
    observed = [row for row in bins if int(row.get("count", 0)) > 0]
    axis.plot([0, 1], [0, 1], linestyle="--", color="grey", label="ideal")
    if observed:
        axis.plot(
            [float(row["mean_probability"]) for row in observed],
            [float(row["observed_frequency"]) for row in observed],
            marker="o",
            label=f"synthetic {split}",
        )
    else:
        axis.text(0.5, 0.5, "No populated reliability bins", ha="center", va="center")
    axis.set(
        xlim=(0, 1),
        ylim=(0, 1),
        xlabel="Predicted probability",
        ylabel="Observed synthetic frequency",
        title=f"{split.title()} reliability at {horizon} h\nSYNTHETIC ONLY — not science",
    )
    axis.legend(loc="upper left")
    axis.grid(alpha=0.25)


def _plot_prediction_timelines(
    plot_directory: Path,
    test_predictions: pd.DataFrame,
    test_events: pd.DataFrame,
    event_evaluation: Any,
    observations: pd.DataFrame,
    forecasts: pd.DataFrame,
    threshold: float,
    plt: Any,
) -> list[Path]:
    timeline_directory = plot_directory / "prediction_timeline_examples"
    timeline_directory.mkdir(parents=True, exist_ok=True)
    matches = event_evaluation.event_matches
    matched_by_event = (
        matches.set_index(matches["event_id"].astype(str)) if not matches.empty else pd.DataFrame()
    )
    outputs: list[Path] = []
    for event in test_events.sort_values(["site_id", "onset_time_utc"]).itertuples(index=False):
        site_id = str(event.site_id)
        event_id = str(event.event_id)
        onset = pd.Timestamp(event.onset_time_utc)
        site_predictions = test_predictions.loc[
            test_predictions["site_id"].astype(str).eq(site_id)
        ].copy()
        match = (
            None
            if matches.empty or event_id not in matched_by_event.index
            else matched_by_event.loc[event_id]
        )
        alert_time = None if match is None else match.get("alert_time_utc")
        preferred = (
            pd.Timestamp(alert_time)
            if alert_time is not None and not pd.isna(alert_time)
            else onset - pd.Timedelta(hours=12)
        )
        row = _timeline_origin(
            site_predictions,
            forecasts,
            observations,
            site_id=site_id,
            onset=onset,
            preferred=preferred,
        )
        origin = pd.Timestamp(row["prediction_time_utc"])
        valid_times = pd.date_range(origin + pd.Timedelta(hours=1), periods=24, freq="h")
        p10 = np.asarray(row["water_p10"], dtype=np.float64)
        p50 = np.asarray(row["water_p50"], dtype=np.float64)
        p90 = np.asarray(row["water_p90"], dtype=np.float64)
        measured = np.asarray(row["water_target"], dtype=np.float64)
        measured_mask = np.asarray(row["water_mask"], dtype=bool)
        probability = np.asarray(row["cumulative_event_probability"], dtype=np.float64)
        physics = _issued_physics_forecast(forecasts, site_id=site_id, origin=origin)
        physics_values = pd.to_numeric(
            physics.get("forecast_total_water_level_m_aod"), errors="coerce"
        ).to_numpy(dtype=np.float64)
        physics_good = physics["quality_flag"].astype(str).eq("synthetic_good").to_numpy()
        quality_bad = ~physics_good | ~measured_mask

        figure, (water_axis, probability_axis) = plt.subplots(
            2,
            1,
            figsize=(10.5, 6.8),
            sharex=True,
            gridspec_kw={"height_ratios": [2.0, 1.0]},
        )
        water_axis.fill_between(
            valid_times,
            p10,
            p90,
            color="tab:blue",
            alpha=0.18,
            label="ImpactNet P10–P90",
        )
        water_axis.plot(valid_times, p50, color="tab:blue", label="ImpactNet P50")
        water_axis.plot(
            valid_times,
            np.where(measured_mask, measured, np.nan),
            color="black",
            marker=".",
            label="Synthetic observed water",
        )
        if np.isfinite(physics_values).any():
            water_axis.plot(
                valid_times,
                np.where(physics_good, physics_values, np.nan),
                color="tab:orange",
                linestyle="--",
                label="Synthetic issued physics forecast (as-of origin)",
            )
        if quality_bad.any():
            for timestamp in valid_times[quality_bad]:
                water_axis.axvspan(
                    timestamp - pd.Timedelta(minutes=30),
                    timestamp + pd.Timedelta(minutes=30),
                    color="tab:red",
                    alpha=0.12,
                )
            water_axis.scatter(
                valid_times[quality_bad],
                np.full(
                    int(quality_bad.sum()),
                    np.nanmin(p10) if np.isfinite(p10).any() else 0.0,
                ),
                marker="x",
                color="tab:red",
                label="Missing/degraded source hour",
                zorder=5,
            )
        else:
            water_axis.text(
                0.01,
                0.03,
                "Missing/degraded status: none in this 24 h window",
                transform=water_axis.transAxes,
                fontsize=8,
                color="tab:green",
            )
        water_axis.axvline(
            onset,
            color="tab:red",
            linewidth=1.5,
            label="Confirmed synthetic onset",
        )
        water_axis.set(ylabel="Water level (mAOD)")
        water_axis.grid(alpha=0.2)
        water_axis.legend(loc="upper left", fontsize=7, ncol=2)

        probability_axis.plot(
            valid_times,
            probability,
            color="tab:purple",
            label="Event probability",
        )
        probability_axis.axhline(
            threshold,
            color="tab:red",
            linestyle="--",
            label=f"Validation threshold ({threshold:.2f})",
        )
        probability_axis.axvline(onset, color="tab:red", linewidth=1.5)
        probability_axis.set(
            ylim=(0, 1.03),
            xlabel="Valid time (UTC)",
            ylabel="Probability",
        )
        probability_axis.grid(alpha=0.2)
        probability_axis.legend(loc="upper left", fontsize=8)
        probability_axis.text(
            0.99,
            0.03,
            "Official warning intervals: UNAVAILABLE\n"
            "(synthetic fixture contains no official-warning archive)",
            transform=probability_axis.transAxes,
            ha="right",
            va="bottom",
            fontsize=8,
            color="tab:red",
        )
        figure.suptitle(
            f"SYNTHETIC ONLY — {site_id} / {event_id}\n"
            f"Origin {origin.isoformat()} | obs-only model | not operational evidence",
            fontsize=11,
        )
        figure.tight_layout()
        destination = timeline_directory / f"test_{site_id}_{event_id}.png"
        figure.savefig(destination, dpi=140)
        plt.close(figure)
        outputs.append(destination)
    return outputs


def _render_synthetic_run_plots(output_directory: Path, plot_directory: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    plot_directory.mkdir(parents=True, exist_ok=True)
    history_payload = _read_json(output_directory / "training_history.json")
    validation_metrics = _read_json(output_directory / "validation_metrics.json")
    test_metrics = _read_json(output_directory / "test_metrics.json")
    resolved_config = _read_json(output_directory / "resolved_config.json")
    for name, payload in (
        ("training_history", history_payload),
        ("validation_metrics", validation_metrics),
        ("test_metrics", test_metrics),
        ("resolved_config", resolved_config),
    ):
        if payload.get("synthetic_only") is not True:
            raise ValueError(f"refusing to render unmarked {name} as a synthetic run")
    validation_predictions = _synthetic_frame(output_directory / "validation_predictions.parquet")
    test_predictions = _synthetic_frame(output_directory / "test_predictions.parquet")
    observations = _synthetic_frame(output_directory / "data" / "observations_hourly.parquet")
    forecasts = _synthetic_frame(output_directory / "data" / "forecasts_hourly.parquet")
    event_catalog = _synthetic_frame(output_directory / "data" / "event_catalog.parquet")
    sites = _synthetic_frame(output_directory / "data" / "sites.parquet")
    horizons = tuple(int(value) for value in resolved_config["horizons_hours"])
    threshold = _balanced_threshold(validation_metrics)
    history = history_payload.get("epochs", [])
    if not isinstance(history, list) or not history:
        raise ValueError("training_history.json contains no epoch records")

    outputs: list[Path] = []
    training_path = plot_directory / "training_loss.png"
    figure, axis = plt.subplots(figsize=(6.4, 3.6))
    epochs = [int(row["epoch"]) for row in history]
    axis.plot(epochs, [float(row["train_loss"]) for row in history], marker="o", label="train")
    axis.plot(
        epochs,
        [float(row["validation_loss"]) for row in history],
        marker="o",
        label="validation",
    )
    axis.set(xlabel="Epoch", ylabel="Masked multitask loss", title="Synthetic engineering loss")
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(training_path, dpi=140)
    plt.close(figure)
    outputs.append(training_path)

    for horizon in horizons:
        probabilities, targets = _horizon_vectors(validation_predictions, horizon)
        recall, precision, area = _precision_recall_points(probabilities, targets)
        figure, axis = plt.subplots(figsize=(5.0, 4.0))
        axis.step(recall, precision, where="post", color="tab:blue", label="synthetic validation")
        prevalence = float(targets.mean()) if targets.size else 0.0
        axis.axhline(prevalence, linestyle=":", color="grey", label=f"prevalence {prevalence:.3f}")
        if area is None:
            axis.text(0.5, 0.5, "PR area unavailable: no positive labels", ha="center")
        axis.set(
            xlim=(0, 1),
            ylim=(0, 1.03),
            xlabel="Recall",
            ylabel="Precision",
            title=f"Validation PR at {horizon} h — SYNTHETIC ONLY\n"
            + ("PR area unavailable" if area is None else f"step area={area:.3f}; not science"),
        )
        axis.grid(alpha=0.25)
        axis.legend(loc="lower left", fontsize=8)
        figure.tight_layout()
        destination = plot_directory / f"validation_pr_curve_{horizon}h.png"
        figure.savefig(destination, dpi=140)
        plt.close(figure)
        outputs.append(destination)

        for split, metrics in (("validation", validation_metrics), ("test", test_metrics)):
            bins = (
                metrics.get("horizon_metrics", {})
                .get(f"{horizon}h", {})
                .get("reliability_bins", [])
            )
            figure, axis = plt.subplots(figsize=(4.6, 4.2))
            _plot_reliability(axis, bins, split=split, horizon=horizon)
            figure.tight_layout()
            destination = plot_directory / f"{split}_reliability_{horizon}h.png"
            figure.savefig(destination, dpi=140)
            plt.close(figure)
            outputs.append(destination)

    test_events = _confirmed_events_for_predictions(event_catalog, sites, test_predictions)
    event_evaluation = evaluate_alert_events(
        test_predictions[["site_id", "prediction_time_utc", "event_probability"]],
        test_events,
        threshold,
    )

    figure, axis = plt.subplots(figsize=(6.0, 3.8))
    detected_rows = event_evaluation.event_matches["detected"].astype(bool)
    lead_times = pd.to_numeric(
        event_evaluation.event_matches.loc[detected_rows, "lead_time_hours"],
        errors="coerce",
    ).dropna()
    if lead_times.empty:
        axis.text(0.5, 0.5, "No detected synthetic events", ha="center", va="center")
    else:
        bins = np.arange(-0.5, 25.5, 2.0).tolist()
        axis.hist(lead_times, bins=bins, color="tab:blue", edgecolor="white")
    axis.set(
        xlabel="Lead time (hours)",
        ylabel="Detected event count",
        title="Synthetic test lead-time distribution — insufficient for science",
    )
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    destination = plot_directory / "lead_time_distribution.png"
    figure.savefig(destination, dpi=140)
    plt.close(figure)
    outputs.append(destination)

    site_ids = sorted(sites["site_id"].astype(str).unique())
    false_episodes = event_evaluation.episode_matches.loc[
        event_evaluation.episode_matches["matched_event_id"].isna()
    ]
    false_counts = false_episodes.groupby("site_id").size().reindex(site_ids, fill_value=0)
    figure, axis = plt.subplots(figsize=(6.4, 3.8))
    axis.bar(site_ids, false_counts.to_numpy(), color="tab:orange")
    for index, count in enumerate(false_counts.to_numpy()):
        axis.text(index, float(count) + 0.03, str(int(count)), ha="center", fontsize=8)
    axis.set(
        ylabel="Merged false-alert episodes",
        title="False alerts by site — synthetic frozen test; not science",
    )
    axis.tick_params(axis="x", rotation=15)
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    destination = plot_directory / "false_alerts_by_site.png"
    figure.savefig(destination, dpi=140)
    plt.close(figure)
    outputs.append(destination)

    event_rows = event_evaluation.event_matches
    detected = event_rows.groupby("site_id")["detected"].sum().reindex(site_ids, fill_value=0)
    totals = event_rows.groupby("site_id").size().reindex(site_ids, fill_value=0)
    recall_by_site = detected.div(totals.replace(0, np.nan))
    figure, axis = plt.subplots(figsize=(6.4, 3.8))
    values = recall_by_site.fillna(0.0).to_numpy(dtype=np.float64)
    axis.bar(site_ids, values, color="tab:green")
    for index, (value, count, total) in enumerate(
        zip(values, detected.to_numpy(), totals.to_numpy(), strict=True)
    ):
        axis.text(index, float(value) + 0.02, f"{int(count)}/{int(total)}", ha="center", fontsize=8)
    axis.set(
        ylim=(0, 1.12),
        ylabel="Event recall",
        title="Event recall by site — synthetic counts are insufficient evidence",
    )
    axis.tick_params(axis="x", rotation=15)
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    destination = plot_directory / "event_recall_by_site.png"
    figure.savefig(destination, dpi=140)
    plt.close(figure)
    outputs.append(destination)

    p10 = _array_matrix(test_predictions, "water_p10")
    p50 = _array_matrix(test_predictions, "water_p50")
    p90 = _array_matrix(test_predictions, "water_p90")
    target = _array_matrix(test_predictions, "water_target")
    water_mask = _array_matrix(test_predictions, "water_mask").astype(bool)
    lead_hours = np.arange(1, 25)
    median_absolute_error = np.array(
        [
            np.median(np.abs(p50[water_mask[:, lead], lead] - target[water_mask[:, lead], lead]))
            if water_mask[:, lead].any()
            else np.nan
            for lead in range(24)
        ]
    )
    coverage = np.array(
        [
            np.mean(
                (target[water_mask[:, lead], lead] >= p10[water_mask[:, lead], lead])
                & (target[water_mask[:, lead], lead] <= p90[water_mask[:, lead], lead])
            )
            if water_mask[:, lead].any()
            else np.nan
            for lead in range(24)
        ]
    )

    figure, axis = plt.subplots(figsize=(7.0, 3.8))
    axis.plot(lead_hours, median_absolute_error, marker="o", markersize=3)
    axis.set(
        xlabel="Lead hour",
        ylabel="Median |P50 − target| (m)",
        title="Water error by lead — synthetic frozen test; not science",
    )
    axis.grid(alpha=0.25)
    figure.tight_layout()
    destination = plot_directory / "water_error_by_lead.png"
    figure.savefig(destination, dpi=140)
    plt.close(figure)
    outputs.append(destination)

    figure, axis = plt.subplots(figsize=(7.0, 3.8))
    axis.plot(lead_hours, coverage, marker="o", markersize=3, label="Empirical P10–P90 coverage")
    axis.axhline(0.8, linestyle="--", color="grey", label="Nominal 80% reference")
    axis.set(
        ylim=(0, 1.03),
        xlabel="Lead hour",
        ylabel="Coverage",
        title="Water interval coverage — synthetic frozen test; not science",
    )
    axis.legend(fontsize=8)
    axis.grid(alpha=0.25)
    figure.tight_layout()
    destination = plot_directory / "water_interval_coverage.png"
    figure.savefig(destination, dpi=140)
    plt.close(figure)
    outputs.append(destination)

    outputs.extend(
        _plot_prediction_timelines(
            plot_directory,
            test_predictions,
            test_events,
            event_evaluation,
            observations,
            forecasts,
            threshold,
            plt,
        )
    )
    provenance = {
        "schema_version": "coastwatch-synthetic-plot-provenance-v1",
        "synthetic_only": True,
        "scientific_result": False,
        "public_warning_use_allowed": False,
        "source_artifacts": [
            "training_history.json",
            "validation_predictions.parquet",
            "validation_metrics.json",
            "test_predictions.parquet",
            "test_metrics.json",
            "data/observations_hourly.parquet",
            "data/forecasts_hourly.parquet",
            "data/event_catalog.parquet",
            "data/sites.parquet",
        ],
        "official_warning_intervals": {
            "available": False,
            "reason": (
                "Synthetic fixture contains no archived official-warning table; "
                "none were fabricated."
            ),
        },
        "artifacts": [path.relative_to(plot_directory).as_posix() for path in outputs],
        "notice": SYNTHETIC_ONLY_NOTICE,
    }
    provenance_path = _write_json(plot_directory / "plot_provenance.json", provenance)
    outputs.append(provenance_path)
    return outputs


def verify_synthetic_run_manifest(
    run_directory: str | Path,
    *,
    minimum_files: int = 0,
) -> dict[str, Any]:
    """Verify the complete synthetic run inventory and its detached hash."""

    root = Path(run_directory).resolve()
    manifest_path = root / "run_manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("synthetic_only") is not True:
        raise ValueError("refusing to verify a run not marked synthetic_only=true")
    recorded_manifest_hash = (root / "run_manifest.sha256").read_text(encoding="ascii").split()[0]
    actual_manifest_hash = sha256_file(manifest_path)
    if recorded_manifest_hash != actual_manifest_hash:
        raise ValueError("run_manifest.sha256 does not match run_manifest.json")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("run manifest has no file inventory")
    if len(files) < minimum_files:
        raise ValueError(f"run manifest has {len(files)} files; expected at least {minimum_files}")
    excluded = {"run_manifest.json", "run_manifest.sha256", "run_progress.json"}
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in excluded
    }
    if actual_paths != set(files):
        missing = sorted(set(files).difference(actual_paths))
        untracked = sorted(actual_paths.difference(files))
        raise ValueError(f"run inventory mismatch; missing={missing}, untracked={untracked}")
    mismatches = [
        relative
        for relative, expected in files.items()
        if sha256_file(root / relative) != str(expected)
    ]
    if mismatches:
        raise ValueError(f"run artifact hashes do not match: {mismatches}")
    return {
        "run_directory": str(root),
        "file_count": len(files),
        "manifest_sha256": actual_manifest_hash,
        "synthetic_only": True,
        "verified": True,
    }


def replot_synthetic_run_artifacts(
    run_directory: str | Path,
    *,
    update_manifest: bool = True,
) -> list[Path]:
    """Safely redraw plots from persisted predictions without touching model/metrics."""

    root = Path(run_directory).resolve()
    manifest_exists = (root / "run_manifest.json").exists()
    if manifest_exists:
        verify_synthetic_run_manifest(root)
    with tempfile.TemporaryDirectory(prefix=".replot-", dir=root) as temporary:
        temporary_root = Path(temporary)
        staged_plot_directory = temporary_root / "plots"
        staged = _render_synthetic_run_plots(root, staged_plot_directory)
        final_plot_directory = root / "plots"
        final_plot_directory.mkdir(parents=True, exist_ok=True)
        final_paths: list[Path] = []
        for source in staged:
            relative = source.relative_to(staged_plot_directory)
            destination = final_plot_directory / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            final_paths.append(destination)
    if update_manifest:
        if not manifest_exists:
            raise ValueError("cannot update a manifest that does not yet exist")
        payload = _read_json(root / "resolved_config.json")
        names = {field.name for field in fields(SyntheticE2EConfig)}
        config_values = {key: value for key, value in payload.items() if key in names}
        for tuple_field in ("horizons_hours", "threshold_candidates"):
            if tuple_field in config_values:
                config_values[tuple_field] = tuple(config_values[tuple_field])
        resolved = SyntheticE2EConfig(
            **config_values,
        )
        _write_run_manifest(root, resolved)
        verify_synthetic_run_manifest(root)
    return final_paths


def _raw_api_request(
    dataset: CoastWatchWindowDataset,
    *,
    dataset_manifest_hash: str,
) -> dict[str, Any]:
    sample = dataset[0]
    past_values = sample["past_values"].numpy()
    past_mask = sample["past_mask"].numpy()
    static_values = sample["static_values"].numpy()
    static_mask = sample["static_mask"].numpy()
    return {
        "site_id": sample["site_id"],
        "prediction_time_utc": sample["prediction_time_utc"].isoformat(),
        "past_values": past_values.tolist(),
        "past_mask": past_mask.tolist(),
        "static_values": [
            float(value) if bool(observed) else None
            for value, observed in zip(static_values, static_mask, strict=True)
        ],
        "future_time_features": sample["lead_features"].numpy().tolist(),
        "source_issue_times": {},
        "feature_manifest_hash": dataset_manifest_hash,
    }


def _write_run_manifest(output_directory: Path, config: SyntheticE2EConfig) -> Path:
    # Progress is durably updated to ``complete`` after this immutable inventory
    # is written, so it is intentionally excluded alongside the manifest itself.
    excluded = {"run_manifest.json", "run_manifest.sha256", "run_progress.json"}
    inventory = {
        path.relative_to(output_directory).as_posix(): sha256_file(path)
        for path in sorted(output_directory.rglob("*"))
        if path.is_file() and path.name not in excluded
    }
    manifest_path = _write_json(
        output_directory / "run_manifest.json",
        {
            "schema_version": "synthetic-e2e-run-v1",
            "synthetic_only": True,
            "scientific_use_allowed": False,
            "public_warning_use_allowed": False,
            "shadow_mode": True,
            "notice": SYNTHETIC_ONLY_NOTICE,
            "seed": config.seed,
            "files": inventory,
        },
    )
    (output_directory / "run_manifest.sha256").write_text(
        f"{sha256_file(manifest_path)}  run_manifest.json\n",
        encoding="ascii",
        newline="\n",
    )
    return manifest_path


def _run_synthetic_e2e_impl(
    output: Path,
    resolved: SyntheticE2EConfig,
    progress: _StageTracker,
) -> SyntheticE2EResult:
    """Internal implementation; the public wrapper records failure state."""

    resolved_payload = {
        **asdict(resolved),
        "synthetic_only": True,
        "notice": SYNTHETIC_ONLY_NOTICE,
    }
    _write_json(output / "resolved_config.json", resolved_payload)
    (output / "resolved_config.yaml").write_text(
        yaml.safe_dump(_json_safe(resolved_payload), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    _write_json(output / "environment.json", environment_record(Path(__file__).parents[1]))
    _write_json(output / "git_state.json", git_state(Path(__file__).parents[1]))

    progress.advance("generate_and_write_synthetic_data")
    synthetic = generate_synthetic_dataset(
        duration_days=resolved.duration_days,
        seed=resolved.seed,
    )
    data_directory = synthetic.write(output / "data")
    dataset_marker = json.loads(
        (data_directory / "SYNTHETIC_ONLY.json").read_text(encoding="utf-8")
    )
    dataset_manifest_path = _write_json(
        output / "dataset_manifest.json",
        {
            "schema_version": "coastwatch-synthetic-dataset-v1",
            **dataset_marker,
        },
    )
    dataset_manifest_hash = sha256_file(dataset_manifest_path)
    (output / "dataset_manifest.sha256").write_text(
        f"{dataset_manifest_hash}  dataset_manifest.json\n",
        encoding="ascii",
        newline="\n",
    )
    split_config = default_synthetic_split_config(synthetic).model_copy(
        update={"event_buffer_hours": resolved.event_buffer_hours}
    )
    observations = _model_observations(synthetic.observations_hourly)
    static_features = _model_static_features(synthetic.static_features)
    event_catalog = synthetic.event_catalog.copy()
    sites = synthetic.sites.copy()

    progress.advance("build_hourly_sample_index")
    full_index = build_synthetic_sample_index(
        synthetic,
        split_config=split_config,
        stride_hours=1,
        past_hours=72,
        horizon_hours=24,
    )
    full_index.to_parquet(output / "sample_index.parquet", index=False)
    strict_index = assign_global_time_split(
        full_index.drop(columns=["split", "split_purge_reason", "target_end_time_utc"]),
        split_config.model_copy(update={"context_mode": "strict_no_overlap"}),
        drop_purged=False,
    )
    _write_json(
        output / "split_sensitivity.json",
        {
            "primary_mode": "operational_context",
            "strict_mode": "strict_no_overlap",
            "event_buffer_hours": split_config.event_buffer_hours,
            "target_purge_hours": split_config.forecast_horizon_hours,
            "primary_counts": {
                str(key): int(value)
                for key, value in full_index["split"]
                .astype(str)
                .value_counts()
                .sort_index()
                .items()
            },
            "strict_counts_on_primary_rows": {
                str(key): int(value)
                for key, value in strict_index["split"]
                .astype(str)
                .value_counts()
                .sort_index()
                .items()
            },
            "strict_additional_purged_rows": int(
                strict_index["split"].astype(str).eq("purged").sum()
            ),
            "synthetic_only": True,
        },
    )
    del strict_index
    train_index = _subsample_train(
        full_index,
        resolved.train_stride_hours,
        negative_to_positive_target_ratio=resolved.negative_to_positive_target_ratio,
        normalize_positive_weight_per_event=(resolved.normalize_positive_weight_per_event),
    )
    validation_index = full_index[full_index["split"].astype(str) == "validation"].reset_index(
        drop=True
    )
    test_index = full_index[full_index["split"].astype(str) == "test"].reset_index(drop=True)
    if validation_index.empty or test_index.empty:
        counts = full_index["split"].astype(str).value_counts().sort_index().to_dict()
        raise RuntimeError(
            "synthetic split produced an empty validation or test set after "
            f"target purge and {split_config.event_buffer_hours}h event buffer; "
            f"counts={counts}. Use a longer fixture or an explicit smaller non-negative "
            "event_buffer_hours for engineering smoke tests"
        )

    progress.advance("fit_train_only_preprocessing")
    past_preprocessor, static_preprocessor = _fit_preprocessors(
        observations,
        static_features,
        train_end_utc=split_config.train_end_utc,
        dataset_manifest_hash=dataset_manifest_hash,
    )
    preprocessing_payload = {
        "fitted_on": "train",
        "dataset_manifest_hash": dataset_manifest_hash,
        "past": past_preprocessor.to_dict(),
        "static": static_preprocessor.to_dict(),
        "synthetic_only": True,
    }
    _write_json(output / "preprocessing.json", preprocessing_payload)

    # obs_only_tcn intentionally receives no future forecast tensor.  The issued
    # forecasts remain in the auditable synthetic data and are exercised by the
    # data-core tests; hybrid training is a separate experiment.
    empty_forecasts = synthetic.forecasts_hourly.head(0).copy()
    del synthetic
    gc.collect()

    progress.advance("build_shared_lazy_dataset")
    combined_index = pd.concat(
        [train_index, validation_index, test_index],
        ignore_index=True,
    )
    train_count = len(train_index)
    validation_count = len(validation_index)
    test_count = len(test_index)
    raw_api_index = test_index.iloc[[0]].reset_index(drop=True).copy()
    shared_dataset = _make_dataset(
        observations,
        empty_forecasts,
        static_features,
        combined_index,
        past_preprocessor=past_preprocessor,
        static_preprocessor=static_preprocessor,
    )
    train_dataset = Subset(shared_dataset, range(0, train_count))
    validation_dataset = Subset(
        shared_dataset,
        range(train_count, train_count + validation_count),
    )
    test_dataset = Subset(
        shared_dataset,
        range(train_count + validation_count, len(combined_index)),
    )
    del full_index, combined_index, train_index, validation_index, test_index
    gc.collect()
    train_loader = _loader(
        train_dataset,
        batch_size=resolved.batch_size,
        shuffle=True,
        seed=resolved.seed,
    )
    validation_loader = _loader(
        validation_dataset,
        batch_size=resolved.batch_size,
        shuffle=False,
        seed=resolved.seed,
    )
    test_loader = _loader(
        test_dataset,
        batch_size=resolved.batch_size,
        shuffle=False,
        seed=resolved.seed,
    )

    architecture = ImpactNetConfig(
        past_feature_dim=len(PAST_FEATURES),
        forecast_feature_dim=0,
        static_feature_dim=len(STATIC_FEATURES),
        time_feature_dim=5,
        variant="obs_only_tcn",
        history_hours=72,
        forecast_hours=24,
        hidden_channels=resolved.hidden_channels,
        num_blocks=resolved.num_blocks,
        kernel_size=resolved.kernel_size,
        dilations=tuple(2**index for index in range(resolved.num_blocks)),
        dropout=resolved.dropout,
        static_hidden_dim=max(4, resolved.hidden_channels),
        static_context_dim=max(4, resolved.hidden_channels // 2),
        decoder_hidden_dim=resolved.decoder_hidden_dim,
        decoder_layers=resolved.decoder_layers,
        lead_embedding_dim=resolved.lead_embedding_dim,
        include_missing_masks=True,
        water_target_mode="absolute",
    )
    model = ImpactNet(architecture).to(resolved.device)
    pos_weight = _positive_weight(
        shared_dataset.sample_index.iloc[:train_count],
        resolved.maximum_pos_weight,
    )
    progress.advance(
        "train_obs_only_tcn",
        total_epochs=resolved.epochs,
        train_samples=train_count,
        validation_samples=validation_count,
    )
    history = _train(
        model,
        train_loader,
        validation_loader,
        config=resolved,
        pos_weight=pos_weight,
        progress=progress,
    )
    _write_json(
        output / "training_history.json",
        {
            "synthetic_only": True,
            "epochs": history,
            "positive_weight": pos_weight,
            "sample_weight_distribution": _sample_weight_distribution(
                shared_dataset.sample_index.iloc[:train_count]
            ),
            "train_samples": train_count,
            "validation_samples": validation_count,
            "test_samples": test_count,
        },
    )
    del train_loader, train_dataset
    gc.collect()

    progress.advance("validation_calibration_and_thresholds")
    validation_raw = _predict(
        model,
        validation_loader,
        config=resolved,
        pos_weight=pos_weight,
        split="validation",
    )
    temperature = fit_global_temperature(
        validation_raw.hazard_logits,
        validation_raw.hazard_target,
        validation_raw.hazard_mask,
        split="validation",
        iterations=resolved.temperature_iterations,
    )
    validation_cumulative = temperature.cumulative_probabilities(validation_raw.hazard_logits)
    validation_predictions = _prediction_frame(validation_raw, validation_cumulative)
    validation_predictions_path = output / "validation_predictions.parquet"
    validation_predictions.to_parquet(validation_predictions_path, index=False)
    validation_events = _events_for_split(
        event_catalog,
        sites,
        start_exclusive=split_config.train_end_utc,
        end_inclusive=split_config.validation_end_utc,
    )
    threshold_selection = _select_thresholds(
        validation_predictions,
        validation_events,
        resolved,
    )
    selected_threshold = float(threshold_selection["selected"]["balanced"]["threshold"])
    validation_metrics = {
        "synthetic_only": True,
        "scientific_result": False,
        "mean_loss": validation_raw.mean_loss,
        "horizon_metrics": compute_horizon_metrics(
            validation_cumulative,
            validation_raw.hazard_target,
            validation_raw.hazard_mask,
            horizons=resolved.horizons_hours,
            threshold=selected_threshold,
        ),
        "water_metrics": water_quantile_metrics(
            validation_raw.water_target,
            validation_raw.water_quantiles,
            validation_raw.water_mask,
        ),
        "event_metrics": _event_metrics(
            validation_predictions,
            validation_events,
            selected_threshold,
        ),
        "calibration": {**temperature.to_dict(), "calibrated": True},
        "threshold_selection": threshold_selection,
    }
    _write_json(output / "validation_metrics.json", validation_metrics)
    del validation_raw, validation_cumulative, validation_predictions, validation_loader
    del validation_dataset, validation_events
    gc.collect()

    # Test starts only after calibration and operating points are frozen.
    progress.advance("frozen_test_evaluation")
    test_raw = _predict(
        model,
        test_loader,
        config=resolved,
        pos_weight=pos_weight,
        split="test",
    )
    test_cumulative = temperature.cumulative_probabilities(test_raw.hazard_logits)
    test_predictions = _prediction_frame(test_raw, test_cumulative)
    test_predictions_path = output / "test_predictions.parquet"
    test_predictions.to_parquet(test_predictions_path, index=False)
    test_events = _events_for_split(
        event_catalog,
        sites,
        start_exclusive=split_config.validation_end_utc,
        end_inclusive=split_config.test_end_utc,
    )
    try:
        test_event_evaluation = evaluate_alert_events(
            test_predictions[["site_id", "prediction_time_utc", "event_probability"]],
            test_events,
            selected_threshold,
        )
        test_event_metrics = test_event_evaluation.metrics
        event_matches = test_event_evaluation.event_matches
    except ValueError as error:
        test_event_metrics = {"insufficient_evidence": True, "reason": str(error)}
        event_matches = pd.DataFrame()
    if not event_matches.empty and "storm_group_id" in event_matches:
        bootstrap_intervals = bootstrap_event_metrics(
            event_matches,
            n_resamples=1000,
            seed=resolved.seed,
        )
        missed_event_ids = (
            event_matches.loc[~event_matches["detected"].astype(bool), "event_id"]
            .astype(str)
            .tolist()
        )
    else:
        bootstrap_intervals = {
            "bootstrap_unit": "storm_group_id",
            "n_storm_groups": 0,
            "n_resamples": 1000,
            "seed": resolved.seed,
            "insufficient_evidence": True,
            "metrics": {},
        }
        missed_event_ids = []
    test_horizon_metrics = compute_horizon_metrics(
        test_cumulative,
        test_raw.hazard_target,
        test_raw.hazard_mask,
        horizons=resolved.horizons_hours,
        threshold=selected_threshold,
    )
    test_water_metrics = water_quantile_metrics(
        test_raw.water_target,
        test_raw.water_quantiles,
        test_raw.water_mask,
    )
    test_metrics = {
        "synthetic_only": True,
        "scientific_result": False,
        "frozen_test": True,
        "test_used_for_tuning": False,
        "notice": SYNTHETIC_ONLY_NOTICE,
        "mean_loss": test_raw.mean_loss,
        "selected_validation_threshold": selected_threshold,
        "calibration_fitted_split": "validation",
        "thresholds_fitted_split": "validation",
        "horizon_metrics": test_horizon_metrics,
        "water_metrics": test_water_metrics,
        "event_metrics": test_event_metrics,
        "bootstrap_intervals": bootstrap_intervals,
    }
    test_metrics_path = _write_json(output / "test_metrics.json", test_metrics)
    _write_json(output / "hourly_metrics.json", test_horizon_metrics)
    _write_json(output / "event_metrics.json", test_event_metrics)
    _write_json(output / "water_metrics.json", test_water_metrics)
    _write_json(output / "bootstrap_intervals.json", bootstrap_intervals)
    replot_synthetic_run_artifacts(output, update_manifest=False)
    del test_raw, test_cumulative, test_predictions, test_loader, test_dataset, test_events
    del shared_dataset
    gc.collect()

    progress.advance("export_and_verify_safe_bundle")
    calibration_payload = {**temperature.to_dict(), "calibrated": True}
    threshold_payload = {
        "fitted_split": "validation",
        "operating_points": threshold_selection,
        "research_bands": _research_bands(selected_threshold),
        "synthetic_only": True,
    }
    feature_schema_payload = {
        "past_feature_names": list(PAST_FEATURES),
        "future_feature_names": [],
        "static_feature_names": list(STATIC_FEATURES),
        "lead_feature_names": [
            "hour_sin",
            "hour_cos",
            "day_of_year_sin",
            "day_of_year_cos",
            "lead_hour_normalized",
        ],
        "max_missing_fraction_past": 0.25,
        "max_missing_fraction_future": 1.0,
        "dataset_manifest_hash": dataset_manifest_hash,
        "synthetic_only": True,
    }
    label_schema_payload = {
        "schema_version": "coastwatch-onset-label-v1",
        "label_mode": "confirmed_impact",
        "synthetic_only": True,
        "primary_positive_confidences": ["A", "B"],
        "warning_only_confidence": "C",
        "unknown_confidence": "U",
        "explicit_negative_confidence": "N",
        "unknown_is_negative": False,
        "active_event_onset_masked": True,
        "horizon_hours": 24,
    }
    validation_summary = json.dumps(
        _json_safe(validation_metrics["horizon_metrics"]), sort_keys=True
    )
    test_summary = json.dumps(_json_safe(test_metrics["horizon_metrics"]), sort_keys=True)
    per_site_summary = json.dumps(
        _json_safe(test_event_metrics.get("per_site", [])), sort_keys=True
    )
    missed_summary = json.dumps(missed_event_ids, sort_keys=True)
    model_card = f"""# CoastWatch Synthetic-Test TCN

{SYNTHETIC_ONLY_NOTICE}

1. Model/version/variant: `synthetic-e2e-seed-{resolved.seed}-v1`, `obs_only_tcn`.
2. Label mode: `confirmed_impact` schema exercised with generated labels only.
3. Geographic coverage: three invented zones; no real location coverage.
4. Data range: `{dataset_marker.get("coverage_start_utc")}` to
   `{dataset_marker.get("coverage_end_utc")}`.
5. Sources: deterministic generator v1; source tables are hashed in
   `dataset_manifest.json`.
6. Confirmed events in frozen test: `{test_event_metrics.get("confirmed_events", 0)}`
   generated events, not observed incidents.
7. Inputs/outputs: 72 h observations and static context to 24 hourly onset hazards
   plus water-level P10/P50/P90.
8. Split/purge: one global chronological split, 24 h target purge, storm-group
   isolation.
9. Calibration: global temperature fitted on validation only; temperature
   `{temperature.temperature:.8g}`.
10. Validation hourly metrics (synthetic, non-scientific): `{validation_summary}`.
11. Frozen test hourly metrics (synthetic, non-scientific): `{test_summary}`.
12. Per-site event results: `{per_site_summary}`.
13. Missed generated event IDs: `{missed_summary}`; false alert episodes:
   `{test_event_metrics.get("false_alert_episodes")}`.
14. Degradation: an independently bundled observation-only model may replace a
   hybrid model; configured physics fallback is otherwise used; insufficient data
   returns no probability.
15. Known limits: synthetic-only, CPU-only validation, rare-event uncertainty,
   no approved spatial mapping, no real issued forecast archive.
16. Not for public alerts, emergency action, official-warning replacement, or a
   claim about UK flood probability.
17. Shadow Mode: always true. Official warnings remain authoritative.
18. Rollback: stop this research service and load the preceding hash-verified
   bundle; v1 and the deterministic device path are separate and unchanged.

This artifact demonstrates engineering continuity only. Official warnings remain authoritative.
"""
    bundle_directory = create_model_bundle(
        output / "model_bundle",
        model,
        model_version=f"synthetic-e2e-seed-{resolved.seed}-v1",
        model_name="CoastWatch Synthetic-Test TCN",
        label_mode="confirmed_impact",
        coverage_scope="Deterministic synthetic engineering fixture; no real location coverage",
        horizons_hours=resolved.horizons_hours,
        preprocessing=preprocessing_payload,
        calibration=calibration_payload,
        thresholds=threshold_payload,
        feature_schema=feature_schema_payload,
        sites=sites.to_dict(orient="records"),
        model_card=model_card,
        synthetic_data=True,
    )
    verify_model_bundle(bundle_directory)
    loaded = load_model_bundle(bundle_directory, device=resolved.device)
    _write_json(output / "calibration.json", calibration_payload)
    _write_json(output / "thresholds.json", threshold_payload)
    _write_json(output / "feature_schema.json", feature_schema_payload)
    _write_json(output / "label_schema.json", label_schema_payload)
    shutil.copyfile(bundle_directory / "model.safetensors", output / "model.safetensors")
    shutil.copyfile(
        bundle_directory / "preprocessing_arrays.npz",
        output / "preprocessing_arrays.npz",
    )
    (output / "MODEL_CARD.md").write_text(model_card, encoding="utf-8", newline="\n")
    (output / "DATA_CARD.md").write_text(
        "# Synthetic E2E Data Card\n\n"
        f"{SYNTHETIC_ONLY_NOTICE}\n\n"
        "The run contains three invented coastal zones and deterministic generated "
        "observations, issued forecasts, static context, missing/stale values, and "
        "events. The canonical table hashes and row counts are recorded in "
        "`dataset_manifest.json`. No official or observed data are present.\n",
        encoding="utf-8",
        newline="\n",
    )
    (output / "LABEL_CARD.md").write_text(
        "# Synthetic E2E Label Card\n\n"
        f"{SYNTHETIC_ONLY_NOTICE}\n\n"
        "The generated catalogue exercises A/B positives, C warning-only evidence, "
        "U unknown masking, and reviewed-style N negative intervals. Absence is not "
        "treated as a negative. These generated labels do not establish confirmed "
        "real-world impacts or event prevalence.\n",
        encoding="utf-8",
        newline="\n",
    )

    progress.advance("shadow_api_smoke")
    raw_test_dataset = _make_dataset(
        observations,
        empty_forecasts,
        static_features,
        raw_api_index,
        past_preprocessor=None,
        static_preprocessor=None,
    )
    request_payload = _raw_api_request(
        raw_test_dataset,
        dataset_manifest_hash=dataset_manifest_hash,
    )
    with TestClient(create_app(BundlePredictor(loaded, device=resolved.device))) as client:
        health = client.get("/health")
        response = client.post("/v1/predict/features", json=request_payload)
    if health.status_code != 200 or response.status_code != 200:
        raise RuntimeError(
            f"Shadow API smoke failed: health={health.status_code}, "
            f"prediction={response.status_code}, body={response.text}"
        )
    response_payload = response.json()
    if response_payload.get("shadow_mode") is not True:
        raise RuntimeError("Shadow API response lost shadow_mode=true")
    if response_payload.get("synthetic_data") is not True:
        raise RuntimeError("Shadow API response lost synthetic_data=true")
    response_payload["request_id"] = "<generated-per-request>"
    api_smoke_path = _write_json(
        output / "api_smoke.json",
        {
            "synthetic_only": True,
            "request": request_payload,
            "health_status_code": health.status_code,
            "health": health.json(),
            "prediction_status_code": response.status_code,
            "prediction": response_payload,
        },
    )
    (output / "RUN_CARD.md").write_text(
        f"# Synthetic engineering E2E run\n\n{SYNTHETIC_ONLY_NOTICE}\n\n"
        f"Seed: `{resolved.seed}`  \n"
        f"Model SHA-256: `{sha256_file(bundle_directory / 'model.safetensors')}`  \n"
        "API smoke: passed with `shadow_mode=true` and `synthetic_data=true`.\n",
        encoding="utf-8",
        newline="\n",
    )
    log_rows = [
        {
            "level": "INFO",
            "event": "stage_complete",
            "synthetic_only": True,
            **row,
        }
        for row in progress.completed
    ]
    log_rows.append(
        {
            "level": "INFO",
            "event": "shadow_api_smoke_complete",
            "synthetic_only": True,
            "status_code": response.status_code,
            "shadow_mode": True,
        }
    )
    (output / "run.log").write_text(
        "".join(json.dumps(_json_safe(row), sort_keys=True) + "\n" for row in log_rows),
        encoding="utf-8",
        newline="\n",
    )
    progress.advance("hash_run_artifacts")
    run_manifest_path = _write_run_manifest(output, resolved)
    return SyntheticE2EResult(
        run_directory=output,
        bundle_directory=bundle_directory,
        run_manifest_path=run_manifest_path,
        test_metrics_path=test_metrics_path,
        validation_predictions_path=validation_predictions_path,
        test_predictions_path=test_predictions_path,
        api_smoke_path=api_smoke_path,
        model_sha256=sha256_file(bundle_directory / "model.safetensors"),
        api_status_code=response.status_code,
    )


def run_synthetic_e2e(
    output_directory: str | Path,
    config: SyntheticE2EConfig | None = None,
) -> SyntheticE2EResult:
    """Execute the complete deterministic synthetic engineering proof path."""

    resolved = config or SyntheticE2EConfig()
    output = _prepare_output_directory(output_directory)
    _seed_everything(resolved)
    progress = _StageTracker(output / "run_progress.json")
    progress.advance("initialise_run")
    try:
        result = _run_synthetic_e2e_impl(output, resolved, progress)
    except BaseException as error:
        progress.fail(error)
        raise
    progress.complete()
    return replace(
        result,
        elapsed_seconds=progress.elapsed_seconds,
        stage_timings_path=progress.path,
    )


__all__ = [
    "FUTURE_PLACEHOLDER_FEATURES",
    "PAST_FEATURES",
    "STATIC_FEATURES",
    "SYNTHETIC_ONLY_NOTICE",
    "SyntheticE2EConfig",
    "SyntheticE2EResult",
    "run_synthetic_e2e",
]
