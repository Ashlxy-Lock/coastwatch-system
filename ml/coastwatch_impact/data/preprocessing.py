"""Train-only numerical preprocessing with explicit missingness and provenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .manifests import manifest_hash


class TrainingDataLeakageError(ValueError):
    """Raised when preprocessing statistics are fitted outside train."""


class PreprocessingProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    fit_split: str = "train"
    feature_names: tuple[str, ...]
    train_row_count: int = Field(gt=0)
    train_frame_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_hash: str | None = None
    fitted_at_utc: datetime
    clip_quantiles: tuple[float, float]
    timestamp_min_utc: str | None = None
    timestamp_max_utc: str | None = None
    all_missing_features: tuple[str, ...] = ()


class PreprocessedBatch:
    """Dense values plus masks; mask=True means the raw value was observed."""

    def __init__(
        self,
        values: np.ndarray,
        observed_mask: np.ndarray,
        feature_names: Sequence[str],
        provenance_hash: str,
    ) -> None:
        self.values = np.asarray(values, dtype=np.float32)
        self.observed_mask = np.asarray(observed_mask, dtype=np.bool_)
        self.feature_names = tuple(feature_names)
        self.provenance_hash = provenance_hash
        if self.values.shape != self.observed_mask.shape:
            raise ValueError("values and observed_mask must have identical shapes")

    @property
    def missing_mask(self) -> np.ndarray:
        return ~self.observed_mask

    def __iter__(self) -> Iterator[np.ndarray]:
        # Convenient and backwards-compatible: ``values, mask = transform(...)``.
        yield self.values
        yield self.observed_mask


def _frame_hash(frame: pd.DataFrame, features: Sequence[str]) -> str:
    selected = frame.loc[:, list(features)].copy()
    for column in selected:
        selected[column] = pd.to_numeric(selected[column], errors="coerce")
    row_hashes = pd.util.hash_pandas_object(selected, index=True).to_numpy(dtype=np.uint64)
    digest = hashlib.sha256()
    digest.update("|".join(features).encode("utf-8"))
    digest.update(row_hashes.tobytes())
    return digest.hexdigest()


class TrainOnlyPreprocessor:
    """Median/clip/standardise pipeline whose fit is restricted to train rows."""

    VERSION = "impactnet-train-preprocessor-v1"

    def __init__(
        self,
        feature_names: Sequence[str],
        *,
        clip_quantiles: tuple[float, float] = (0.005, 0.995),
        dataset_manifest_hash: str | None = None,
    ) -> None:
        names = tuple(str(name) for name in feature_names)
        if not names or len(set(names)) != len(names):
            raise ValueError("feature_names must be a non-empty unique sequence")
        lower, upper = clip_quantiles
        if not 0.0 <= lower < upper <= 1.0:
            raise ValueError("clip_quantiles must satisfy 0 <= lower < upper <= 1")
        self.feature_names = names
        self.clip_quantiles = (float(lower), float(upper))
        self.dataset_manifest_hash = dataset_manifest_hash
        self.medians_: dict[str, float] = {}
        self.clip_lower_: dict[str, float] = {}
        self.clip_upper_: dict[str, float] = {}
        self.means_: dict[str, float] = {}
        self.scales_: dict[str, float] = {}
        self.provenance_: PreprocessingProvenance | None = None

    @property
    def fitted(self) -> bool:
        return self.provenance_ is not None

    @property
    def provenance_hash(self) -> str:
        if self.provenance_ is None:
            raise RuntimeError("preprocessor is not fitted")
        return manifest_hash(self.to_dict())

    def fit(
        self,
        frame: pd.DataFrame,
        *,
        split_col: str | None = "split",
        split_name: str = "train",
        timestamp_col: str | None = "prediction_time_utc",
    ) -> TrainOnlyPreprocessor:
        if split_name != "train":
            raise TrainingDataLeakageError("preprocessing statistics may only be fitted on train")
        if frame.empty:
            raise ValueError("cannot fit preprocessing on an empty frame")
        absent = set(self.feature_names).difference(frame.columns)
        if absent:
            raise KeyError(f"training frame missing features: {sorted(absent)}")
        if split_col is not None and split_col in frame:
            splits = set(frame[split_col].dropna().astype(str).unique())
            if splits != {"train"}:
                raise TrainingDataLeakageError(
                    f"fit frame must contain only split='train', received {sorted(splits)}"
                )
        elif split_col is not None:
            # Absence is allowed only because some callers pass a physically
            # separate train table.  The explicit split_name above is recorded.
            pass

        numeric = frame.loc[:, list(self.feature_names)].apply(pd.to_numeric, errors="coerce")
        numeric = numeric.replace([np.inf, -np.inf], np.nan)
        all_missing: list[str] = []
        lower_q, upper_q = self.clip_quantiles
        for feature in self.feature_names:
            series = numeric[feature]
            observed = series.dropna()
            if observed.empty:
                all_missing.append(feature)
                median = lower = upper = mean = 0.0
                scale = 1.0
            else:
                median = float(observed.median())
                lower = float(observed.quantile(lower_q))
                upper = float(observed.quantile(upper_q))
                if lower > upper:
                    lower, upper = upper, lower
                filled = series.fillna(median).clip(lower=lower, upper=upper)
                mean = float(filled.mean())
                scale = float(filled.std(ddof=0))
                if not np.isfinite(scale) or scale < 1e-12:
                    scale = 1.0
            self.medians_[feature] = median
            self.clip_lower_[feature] = lower
            self.clip_upper_[feature] = upper
            self.means_[feature] = mean
            self.scales_[feature] = scale

        timestamp_min: str | None = None
        timestamp_max: str | None = None
        if timestamp_col is not None and timestamp_col in frame:
            timestamps = pd.to_datetime(frame[timestamp_col], utc=True, errors="raise")
            timestamp_min = timestamps.min().isoformat()
            timestamp_max = timestamps.max().isoformat()
        self.provenance_ = PreprocessingProvenance(
            fit_split="train",
            feature_names=self.feature_names,
            train_row_count=len(frame),
            train_frame_sha256=_frame_hash(frame, self.feature_names),
            dataset_manifest_hash=self.dataset_manifest_hash,
            fitted_at_utc=datetime.now(UTC),
            clip_quantiles=self.clip_quantiles,
            timestamp_min_utc=timestamp_min,
            timestamp_max_utc=timestamp_max,
            all_missing_features=tuple(all_missing),
        )
        return self

    def transform(self, frame: pd.DataFrame) -> PreprocessedBatch:
        if self.provenance_ is None:
            raise RuntimeError("fit must be called before transform")
        absent = set(self.feature_names).difference(frame.columns)
        if absent:
            raise KeyError(f"frame missing features: {sorted(absent)}")
        numeric = frame.loc[:, list(self.feature_names)].apply(pd.to_numeric, errors="coerce")
        numeric = numeric.replace([np.inf, -np.inf], np.nan)
        observed_mask = numeric.notna().to_numpy(dtype=np.bool_)
        values = np.empty((len(frame), len(self.feature_names)), dtype=np.float32)
        for index, feature in enumerate(self.feature_names):
            series = numeric[feature].fillna(self.medians_[feature])
            series = series.clip(
                lower=self.clip_lower_[feature],
                upper=self.clip_upper_[feature],
            )
            values[:, index] = (
                (series.to_numpy(dtype=np.float64) - self.means_[feature]) / self.scales_[feature]
            ).astype(np.float32)
        if not np.isfinite(values).all():
            raise RuntimeError("preprocessing produced non-finite values")
        return PreprocessedBatch(values, observed_mask, self.feature_names, self.provenance_hash)

    def transform_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        batch = self.transform(frame)
        result = frame.copy()
        for index, feature in enumerate(self.feature_names):
            result[feature] = batch.values[:, index]
            result[f"{feature}__missing"] = batch.missing_mask[:, index]
        result.attrs["preprocessing_provenance_hash"] = batch.provenance_hash
        result.attrs["preprocessing_fit_split"] = "train"
        return result

    def fit_transform(
        self,
        frame: pd.DataFrame,
        *,
        split_col: str | None = "split",
        split_name: str = "train",
        timestamp_col: str | None = "prediction_time_utc",
    ) -> PreprocessedBatch:
        return self.fit(
            frame,
            split_col=split_col,
            split_name=split_name,
            timestamp_col=timestamp_col,
        ).transform(frame)

    def to_dict(self) -> dict[str, Any]:
        if self.provenance_ is None:
            raise RuntimeError("preprocessor is not fitted")
        return {
            "version": self.VERSION,
            "feature_names": list(self.feature_names),
            "clip_quantiles": list(self.clip_quantiles),
            "medians": self.medians_,
            "clip_lower": self.clip_lower_,
            "clip_upper": self.clip_upper_,
            "means": self.means_,
            "scales": self.scales_,
            "provenance": self.provenance_.model_dump(mode="json"),
        }

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        destination.write_text(payload, encoding="utf-8", newline="\n")
        return destination

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TrainOnlyPreprocessor:
        if payload.get("version") != cls.VERSION:
            raise ValueError(f"unsupported preprocessing version {payload.get('version')!r}")
        provenance = PreprocessingProvenance.model_validate(payload["provenance"])
        if provenance.fit_split != "train":
            raise TrainingDataLeakageError(
                "loaded preprocessing provenance was not fitted on train"
            )
        instance = cls(
            payload["feature_names"],
            clip_quantiles=tuple(payload["clip_quantiles"]),
            dataset_manifest_hash=provenance.dataset_manifest_hash,
        )
        instance.medians_ = {str(k): float(v) for k, v in payload["medians"].items()}
        instance.clip_lower_ = {str(k): float(v) for k, v in payload["clip_lower"].items()}
        instance.clip_upper_ = {str(k): float(v) for k, v in payload["clip_upper"].items()}
        instance.means_ = {str(k): float(v) for k, v in payload["means"].items()}
        instance.scales_ = {str(k): float(v) for k, v in payload["scales"].items()}
        instance.provenance_ = provenance
        return instance

    @classmethod
    def load(cls, path: str | Path) -> TrainOnlyPreprocessor:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def assert_train_only_provenance(
    preprocessor: TrainOnlyPreprocessor,
    *,
    expected_dataset_manifest_hash: str | None = None,
) -> None:
    provenance = preprocessor.provenance_
    if provenance is None or provenance.fit_split != "train":
        raise TrainingDataLeakageError("preprocessor lacks train-only provenance")
    if (
        expected_dataset_manifest_hash is not None
        and provenance.dataset_manifest_hash != expected_dataset_manifest_hash
    ):
        raise TrainingDataLeakageError(
            "preprocessing dataset manifest differs from the expected training dataset"
        )


# Compatibility terminology.
TrainOnlyScaler = TrainOnlyPreprocessor


__all__ = [
    "PreprocessedBatch",
    "PreprocessingProvenance",
    "TrainOnlyPreprocessor",
    "TrainOnlyScaler",
    "TrainingDataLeakageError",
    "assert_train_only_provenance",
]
