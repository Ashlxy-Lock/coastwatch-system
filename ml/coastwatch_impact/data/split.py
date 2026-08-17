"""Global time splitting, target purge and event/storm leakage audits."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import utc_datetime

SplitName = Literal["train", "validation", "test"]


class SplitLeakageError(ValueError):
    """Raised when temporal targets or event groups cross data splits."""


class GlobalSplitConfig(BaseModel):
    """One set of boundaries shared by every site."""

    model_config = ConfigDict(frozen=True)

    train_end_utc: datetime
    validation_end_utc: datetime
    test_end_utc: datetime
    forecast_horizon_hours: int = Field(default=24, gt=0)
    event_buffer_hours: int = Field(default=72, ge=0)
    history_hours: int = Field(default=72, gt=0)
    context_mode: Literal["operational_context", "strict_no_overlap"] = "operational_context"

    @field_validator("train_end_utc", "validation_end_utc", "test_end_utc", mode="before")
    @classmethod
    def _utc_boundaries(cls, value: Any, info: Any) -> datetime:
        return utc_datetime(value, name=info.field_name)

    @model_validator(mode="after")
    def _ordered(self) -> GlobalSplitConfig:
        if not self.train_end_utc < self.validation_end_utc < self.test_end_utc:
            raise ValueError("split boundaries must satisfy train_end < validation_end < test_end")
        return self


def _utc_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        raise KeyError(f"missing timestamp column {column!r}")
    converted = [
        pd.Timestamp(utc_datetime(value, name=f"{column}[{i}]"))
        for i, value in enumerate(frame[column])
    ]
    return pd.Series(converted, index=frame.index, dtype="datetime64[ns, UTC]")


def assign_global_time_split(
    samples: pd.DataFrame,
    config: GlobalSplitConfig,
    *,
    prediction_time_col: str = "prediction_time_utc",
    split_col: str = "split",
    drop_purged: bool = False,
) -> pd.DataFrame:
    """Assign all sites with the same UTC boundaries and target-window purge.

    A sample is assigned only when its complete 24-hour (configurable) target
    window ends inside its split.  Boundary-adjacent samples are explicitly
    labelled ``purged`` so audit reports can account for them.
    """

    result = samples.copy()
    times = _utc_series(result, prediction_time_col)
    result[prediction_time_col] = times
    horizon = pd.Timedelta(hours=config.forecast_horizon_hours)
    train_end = pd.Timestamp(config.train_end_utc)
    validation_end = pd.Timestamp(config.validation_end_utc)
    test_end = pd.Timestamp(config.test_end_utc)
    labels = pd.Series("outside", index=result.index, dtype="string")
    reasons = pd.Series("outside_configured_range", index=result.index, dtype="string")

    train = times + horizon <= train_end
    labels.loc[train] = "train"
    reasons.loc[train] = ""

    train_boundary_purge = (times > train_end - horizon) & (times <= train_end)
    labels.loc[train_boundary_purge] = "purged"
    reasons.loc[train_boundary_purge] = "target_crosses_train_boundary"

    validation = (times > train_end) & (times + horizon <= validation_end)
    labels.loc[validation] = "validation"
    reasons.loc[validation] = ""

    validation_boundary_purge = (times > validation_end - horizon) & (times <= validation_end)
    labels.loc[validation_boundary_purge] = "purged"
    reasons.loc[validation_boundary_purge] = "target_crosses_validation_boundary"

    test = (times > validation_end) & (times + horizon <= test_end)
    labels.loc[test] = "test"
    reasons.loc[test] = ""

    # In strict sensitivity mode, the first history window after each boundary is
    # additionally purged.  Operational context intentionally permits it because
    # those observations existed at prediction time.
    if config.context_mode == "strict_no_overlap":
        history = pd.Timedelta(hours=config.history_hours)
        strict_validation = (times > train_end) & (times <= train_end + history)
        strict_test = (times > validation_end) & (times <= validation_end + history)
        labels.loc[strict_validation | strict_test] = "purged"
        reasons.loc[strict_validation] = "strict_history_crosses_train_boundary"
        reasons.loc[strict_test] = "strict_history_crosses_validation_boundary"

    # Event/storm-linked target rows identify the correlated episode window. If
    # its configured buffer reaches into another split, purge those neighbouring
    # samples across every site. This is deliberately global: one multi-site
    # storm must not leak through a nominally unrelated site's normal hours.
    if config.event_buffer_hours:
        event_buffer = pd.Timedelta(hours=config.event_buffer_hours)
        provisional_valid = labels.isin(["train", "validation", "test"])
        for group_column in ("event_id", "storm_group_id"):
            if group_column not in result:
                continue
            linked = result[group_column].notna() & (
                result[group_column].astype(str).str.strip() != ""
            )
            for group_value in result.loc[linked, group_column].astype(str).unique():
                group_rows = linked & result[group_column].astype(str).eq(group_value)
                group_splits = sorted(labels.loc[group_rows & provisional_valid].unique())
                if len(group_splits) != 1:
                    # The dedicated exclusivity audit reports a clearer error.
                    continue
                group_split = str(group_splits[0])
                group_times = times.loc[group_rows]
                buffered = times.between(
                    group_times.min() - event_buffer,
                    group_times.max() + event_buffer,
                    inclusive="both",
                )
                cross_split = buffered & provisional_valid & labels.ne(group_split)
                labels.loc[cross_split] = "purged"
                reasons.loc[cross_split] = f"{group_column}_buffer_crosses_split"
                provisional_valid.loc[cross_split] = False

    result[split_col] = labels
    result["split_purge_reason"] = reasons.replace("", pd.NA)
    result["target_end_time_utc"] = times + horizon
    audit_target_window_purge(
        result,
        config,
        prediction_time_col=prediction_time_col,
        split_col=split_col,
    )
    if drop_purged:
        result = result[result[split_col].isin(["train", "validation", "test"])].copy()
    return result.reset_index(drop=True)


def audit_target_window_purge(
    samples: pd.DataFrame,
    config: GlobalSplitConfig,
    *,
    prediction_time_col: str = "prediction_time_utc",
    split_col: str = "split",
) -> dict[str, Any]:
    if split_col not in samples:
        raise KeyError(f"missing split column {split_col!r}")
    times = _utc_series(samples, prediction_time_col)
    horizon = pd.Timedelta(hours=config.forecast_horizon_hours)
    target_end = times + horizon
    train_end = pd.Timestamp(config.train_end_utc)
    validation_end = pd.Timestamp(config.validation_end_utc)
    test_end = pd.Timestamp(config.test_end_utc)
    split = samples[split_col].astype(str)
    bad_train = (split == "train") & (target_end > train_end)
    bad_validation = (split == "validation") & (
        (times <= train_end) | (target_end > validation_end)
    )
    bad_test = (split == "test") & ((times <= validation_end) | (target_end > test_end))
    bad = bad_train | bad_validation | bad_test
    if bad.any():
        examples = (
            samples.loc[bad, [prediction_time_col, split_col]].head(10).to_dict(orient="records")
        )
        raise SplitLeakageError(
            f"target horizon crosses or precedes its assigned split boundary: {examples}"
        )
    counts = split.value_counts().sort_index().to_dict()
    return {
        "valid": True,
        "rows": int(len(samples)),
        "split_counts": {str(key): int(value) for key, value in counts.items()},
        "forecast_horizon_hours": config.forecast_horizon_hours,
        "boundaries_utc": {
            "train_end": config.train_end_utc.isoformat(),
            "validation_end": config.validation_end_utc.isoformat(),
            "test_end": config.test_end_utc.isoformat(),
        },
    }


def audit_group_exclusivity(
    samples: pd.DataFrame,
    *,
    split_col: str = "split",
    group_columns: tuple[str, ...] = ("event_id", "storm_group_id"),
    raise_on_error: bool = True,
) -> dict[str, Any]:
    """Assert each event and multi-site storm belongs to exactly one split."""

    if split_col not in samples:
        raise KeyError(f"missing split column {split_col!r}")
    valid = samples[samples[split_col].astype(str).isin(["train", "validation", "test"])]
    violations: dict[str, dict[str, list[str]]] = {}
    for column in group_columns:
        if column not in valid:
            continue
        non_null = valid[valid[column].notna() & (valid[column].astype(str).str.strip() != "")]
        for group_value, group in non_null.groupby(column, sort=True):
            splits = sorted(group[split_col].astype(str).unique().tolist())
            if len(splits) > 1:
                violations.setdefault(column, {})[str(group_value)] = splits
    if violations and raise_on_error:
        raise SplitLeakageError(f"event/storm group appears in multiple splits: {violations}")
    return {
        "valid": not violations,
        "violations": violations,
        "groups_checked": {
            column: int(valid[column].dropna().astype(str).replace("", pd.NA).dropna().nunique())
            for column in group_columns
            if column in valid
        },
    }


def audit_global_split(
    samples: pd.DataFrame,
    config: GlobalSplitConfig,
    *,
    prediction_time_col: str = "prediction_time_utc",
    split_col: str = "split",
) -> dict[str, Any]:
    return {
        "target_window": audit_target_window_purge(
            samples,
            config,
            prediction_time_col=prediction_time_col,
            split_col=split_col,
        ),
        "group_exclusivity": audit_group_exclusivity(samples, split_col=split_col),
    }


def assign_event_splits(
    event_catalog: pd.DataFrame,
    config: GlobalSplitConfig,
    *,
    onset_col: str = "onset_time_utc",
    split_col: str = "split",
) -> pd.DataFrame:
    """Assign catalogue events by onset for pre-auditing multi-site groups."""

    if onset_col not in event_catalog:
        raise KeyError(f"missing event onset column {onset_col!r}")
    work = event_catalog.copy()
    known = work[onset_col].notna()
    work[split_col] = "unknown"
    if known.any():
        assigned = assign_global_time_split(
            work.loc[known].rename(columns={onset_col: "prediction_time_utc"}),
            config.model_copy(update={"forecast_horizon_hours": 1}),
            prediction_time_col="prediction_time_utc",
            split_col=split_col,
        )
        work.loc[known, split_col] = assigned[split_col].to_numpy()
    audit_group_exclusivity(work, split_col=split_col)
    return work


def build_leave_one_site_out_folds(
    samples: pd.DataFrame,
    *,
    site_col: str = "site_id",
    global_split_col: str = "split",
) -> dict[str, pd.DataFrame]:
    """Build spatial-generalisation folds without touching the final test split.

    Each fold trains on the global training period from all other sites and
    validates on the held-out site's global validation period. Other rows are
    marked ``excluded`` rather than silently reused. This keeps the final time
    test frozen and avoids training on concurrent validation storms.
    """

    required = {site_col, global_split_col}
    missing = required.difference(samples.columns)
    if missing:
        raise KeyError(f"LOSO sample table is missing columns: {sorted(missing)}")
    if samples.empty:
        raise ValueError("LOSO sample table is empty")
    sites = sorted(samples[site_col].dropna().astype(str).unique())
    if len(sites) < 2:
        raise ValueError("leave-one-site-out requires at least two sites")
    known_splits = set(samples[global_split_col].dropna().astype(str))
    if not known_splits.issubset({"train", "validation", "test"}):
        raise SplitLeakageError(f"LOSO received unknown global splits: {known_splits}")

    folds: dict[str, pd.DataFrame] = {}
    site_values = samples[site_col].astype(str)
    split_values = samples[global_split_col].astype(str)
    for held_out in sites:
        fold = samples.copy()
        fold["held_out_site_id"] = held_out
        fold["cv_split"] = "excluded"
        fold.loc[(site_values != held_out) & (split_values == "train"), "cv_split"] = "train"
        fold.loc[
            (site_values == held_out) & (split_values == "validation"),
            "cv_split",
        ] = "validation"
        if not fold["cv_split"].eq("train").any():
            raise SplitLeakageError(f"LOSO fold {held_out!r} has no training rows")
        if not fold["cv_split"].eq("validation").any():
            raise SplitLeakageError(f"LOSO fold {held_out!r} has no validation rows")
        if fold.loc[fold["cv_split"] == "train", site_col].astype(str).eq(held_out).any():
            raise SplitLeakageError(f"held-out site leaked into training: {held_out}")
        folds[held_out] = fold
    return folds


# Compatibility names.
SplitConfig = GlobalSplitConfig
global_time_split = assign_global_time_split
ensure_group_exclusivity = audit_group_exclusivity


__all__ = [
    "GlobalSplitConfig",
    "SplitConfig",
    "SplitLeakageError",
    "assign_event_splits",
    "assign_global_time_split",
    "build_leave_one_site_out_folds",
    "audit_global_split",
    "audit_group_exclusivity",
    "audit_target_window_purge",
    "ensure_group_exclusivity",
    "global_time_split",
]
