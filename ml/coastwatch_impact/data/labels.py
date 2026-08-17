"""Auditable A/B/C/U/N event catalogue to discrete-time hazard labels."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Literal

import numpy as np
import pandas as pd

from .schemas import LabelConfidence, OnsetPrecision, utc_datetime


class LabelConstructionError(ValueError):
    """Raised when event evidence cannot safely be converted to labels."""


@dataclass(frozen=True)
class HazardLabel:
    hazard_target: np.ndarray
    hazard_mask: np.ndarray
    active_event: bool
    event_id: str | None
    storm_group_id: str | None
    label_confidence: str | None
    audit_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.hazard_target.ndim != 1 or self.hazard_mask.ndim != 1:
            raise ValueError("hazard target and mask must be one-dimensional")
        if self.hazard_target.shape != self.hazard_mask.shape:
            raise ValueError("hazard target and mask must have equal length")
        if np.any((self.hazard_target < 0.0) | (self.hazard_target > 1.0)):
            raise ValueError("hazard targets must lie in [0, 1]")


def _utc_stamp(value: object, *, name: str) -> pd.Timestamp:
    return pd.Timestamp(utc_datetime(value, name=name))


def _optional_stamp(value: object, *, name: str) -> pd.Timestamp | None:
    if value is None or value is pd.NaT or (isinstance(value, float) and pd.isna(value)):
        return None
    return _utc_stamp(value, name=name)


def _confidence(value: object) -> LabelConfidence:
    try:
        return value if isinstance(value, LabelConfidence) else LabelConfidence(str(value))
    except ValueError as exc:
        raise LabelConstructionError(f"invalid label confidence {value!r}") from exc


def _precision(value: object) -> OnsetPrecision:
    try:
        return value if isinstance(value, OnsetPrecision) else OnsetPrecision(str(value))
    except ValueError as exc:
        raise LabelConstructionError(f"invalid onset precision {value!r}") from exc


def _slot_overlaps(
    prediction: pd.Timestamp,
    lead_index: int,
    interval_start: pd.Timestamp,
    interval_end: pd.Timestamp,
) -> bool:
    """Whether lead slot ``(t+j-1h, t+jh]`` intersects an uncertain interval."""

    slot_start = prediction + pd.Timedelta(hours=lead_index)
    slot_end = slot_start + pd.Timedelta(hours=1)
    # Lead slots are right-closed: an onset exactly at t+j belongs to lead j.
    return slot_end >= interval_start and slot_start < interval_end


def _event_uncertainty_interval(
    event: pd.Series,
    *,
    horizon_start: pd.Timestamp,
    horizon_end: pd.Timestamp,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    onset = _optional_stamp(event.get("onset_time_utc"), name="onset_time_utc")
    end = _optional_stamp(event.get("end_time_utc"), name="end_time_utc")
    precision = _precision(event.get("onset_precision", OnsetPrecision.UNKNOWN.value))
    if onset is None:
        return horizon_start, horizon_end
    if precision == OnsetPrecision.DATE_ONLY:
        day_start = onset.floor("D")
        return day_start, day_start + pd.Timedelta(days=1)
    if precision == OnsetPrecision.UNKNOWN:
        return onset, end or horizon_end
    return onset, end or onset + pd.Timedelta(hours=1)


def _as_bool_mask(value: bool | Sequence[bool], horizon_hours: int) -> np.ndarray:
    if isinstance(value, (bool, np.bool_)):
        return np.full(horizon_hours, bool(value), dtype=np.bool_)
    result = np.asarray(value, dtype=np.bool_)
    if result.shape != (horizon_hours,):
        raise ValueError(f"known_negative_mask must have shape ({horizon_hours},)")
    return result.copy()


def build_hazard_label(
    prediction_time_utc: datetime | str | pd.Timestamp,
    coastal_zone_id: str,
    event_catalog: pd.DataFrame,
    *,
    horizon_hours: int = 24,
    positive_confidences: Iterable[str | LabelConfidence] = (
        LabelConfidence.A,
        LabelConfidence.B,
    ),
    known_negative_mask: bool | Sequence[bool] = False,
    maximum_soft_interval_hours: int = 6,
    label_mode: Literal["weak_rule", "official_warning", "confirmed_impact"] = ("confirmed_impact"),
) -> HazardLabel:
    """Construct one masked discrete-onset hazard target.

    The safe default is *unknown*, not negative: ``known_negative_mask=False``.
    Callers must explicitly provide clean-negative coverage or N-labelled
    intervals. A/B exact onsets make the confirmed-impact risk set known through
    the first onset. In ``official_warning`` mode, precise C evidence instead
    trains a WarningNet onset head. U/date-only evidence remains masked. If
    prediction time is inside a positive event, the complete onset head is masked.
    """

    if horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive")
    if maximum_soft_interval_hours <= 0:
        raise ValueError("maximum_soft_interval_hours must be positive")
    prediction = _utc_stamp(prediction_time_utc, name="prediction_time_utc")
    horizon_end = prediction + pd.Timedelta(hours=horizon_hours)
    positives = {_confidence(value) for value in positive_confidences}
    permitted = (
        {LabelConfidence.C}
        if label_mode == "official_warning"
        else {LabelConfidence.A, LabelConfidence.B}
    )
    if not positives or not positives.issubset(permitted):
        expected = "C warning evidence" if label_mode == "official_warning" else "A/B evidence"
        raise ValueError(f"{label_mode} primary head may train only from {expected}")
    target = np.zeros(horizon_hours, dtype=np.float32)
    mask = _as_bool_mask(known_negative_mask, horizon_hours)
    reasons: list[str] = []

    if event_catalog.empty:
        return HazardLabel(target, mask, False, None, None, None, ("empty_catalog",))
    required = {"coastal_zone_id", "label_confidence", "onset_precision"}
    missing = required.difference(event_catalog.columns)
    if missing:
        raise LabelConstructionError(f"event catalog missing columns: {sorted(missing)}")
    zone_events = event_catalog[
        event_catalog["coastal_zone_id"].astype(str) == str(coastal_zone_id)
    ]
    if zone_events.empty:
        return HazardLabel(target, mask, False, None, None, None, ("no_zone_evidence",))

    # Confirmed event-active samples are never allowed to train onset prediction.
    for _, event in zone_events.iterrows():
        confidence = _confidence(event["label_confidence"])
        if confidence not in positives:
            continue
        onset = _optional_stamp(event.get("onset_time_utc"), name="onset_time_utc")
        end = _optional_stamp(event.get("end_time_utc"), name="end_time_utc")
        if onset is not None and onset <= prediction and (end is None or prediction <= end):
            return HazardLabel(
                target,
                np.zeros(horizon_hours, dtype=np.bool_),
                True,
                str(event.get("event_id")) if pd.notna(event.get("event_id")) else None,
                str(event.get("storm_group_id")) if pd.notna(event.get("storm_group_id")) else None,
                confidence.value,
                ("prediction_time_inside_confirmed_event",),
            )

    # N rows are explicit negative-coverage evidence.  Absence of an N row does
    # not change mask=False, preserving the unknown-not-negative boundary.
    for _, event in zone_events.iterrows():
        if _confidence(event["label_confidence"]) != LabelConfidence.N:
            continue
        interval_start, interval_end = _event_uncertainty_interval(
            event,
            horizon_start=prediction,
            horizon_end=horizon_end,
        )
        for lead_index in range(horizon_hours):
            if _slot_overlaps(prediction, lead_index, interval_start, interval_end):
                mask[lead_index] = True
        reasons.append("explicit_N_negative_coverage")

    # U, C, date-only and unknown-precision evidence must never be converted to
    # zero-labelled clean negatives.
    uncertain = np.zeros(horizon_hours, dtype=np.bool_)
    for _, event in zone_events.iterrows():
        confidence = _confidence(event["label_confidence"])
        precision = _precision(event["onset_precision"])
        if confidence not in {LabelConfidence.U, LabelConfidence.C} and precision not in {
            OnsetPrecision.DATE_ONLY,
            OnsetPrecision.UNKNOWN,
        }:
            continue
        interval_start, interval_end = _event_uncertainty_interval(
            event,
            horizon_start=prediction,
            horizon_end=horizon_end,
        )
        for lead_index in range(horizon_hours):
            if _slot_overlaps(prediction, lead_index, interval_start, interval_end):
                uncertain[lead_index] = True
        reasons.append(f"masked_{confidence.value}_{precision.value}")
    mask[uncertain] = False

    candidates: list[tuple[pd.Timestamp, pd.Series, OnsetPrecision]] = []
    for _, event in zone_events.iterrows():
        confidence = _confidence(event["label_confidence"])
        precision = _precision(event["onset_precision"])
        onset = _optional_stamp(event.get("onset_time_utc"), name="onset_time_utc")
        if (
            confidence in positives
            and onset is not None
            and prediction < onset <= horizon_end
            and precision in {OnsetPrecision.EXACT_HOUR, OnsetPrecision.INTERVAL}
        ):
            candidates.append((onset, event, precision))
    if not candidates:
        return HazardLabel(
            target, mask, False, None, None, None, tuple(reasons or ["no_positive_onset"])
        )

    onset, event, precision = min(candidates, key=lambda item: item[0])
    confidence = _confidence(event["label_confidence"])
    event_id = str(event.get("event_id")) if pd.notna(event.get("event_id")) else None
    storm_group_id = (
        str(event.get("storm_group_id")) if pd.notna(event.get("storm_group_id")) else None
    )
    lead = int(ceil((onset - prediction).total_seconds() / 3600.0))
    lead = min(max(lead, 1), horizon_hours)

    if precision == OnsetPrecision.EXACT_HOUR:
        # At-risk, known-zero hours through the confirmed first onset are usable.
        mask[:lead] = True
        target[lead - 1] = 1.0
        mask[lead:] = False
        reasons.append("exact_positive_onset")
    else:
        interval_end = _optional_stamp(event.get("peak_time_utc"), name="peak_time_utc")
        if interval_end is None or interval_end <= onset:
            interval_end = onset + pd.Timedelta(hours=1)
        width = (interval_end - onset).total_seconds() / 3600.0
        if width > maximum_soft_interval_hours:
            for lead_index in range(horizon_hours):
                if _slot_overlaps(prediction, lead_index, onset, interval_end):
                    mask[lead_index] = False
            reasons.append("wide_interval_masked")
        else:
            bins = [
                index
                for index in range(horizon_hours)
                if _slot_overlaps(prediction, index, onset, interval_end)
            ]
            if bins:
                mask[: max(bins) + 1] = True
                mask[max(bins) + 1 :] = False
                target[bins] = 1.0 / len(bins)
                reasons.append("narrow_interval_soft_label")

    # Confirmed evidence wins at its positive bin, while other U/C bins remain
    # masked.  Unknown evidence can never create a negative target.
    positive_bins = target > 0.0
    mask[uncertain & ~positive_bins] = False
    mask[positive_bins] = True
    return HazardLabel(
        target,
        mask,
        False,
        event_id,
        storm_group_id,
        confidence.value,
        tuple(reasons),
    )


def build_hazard_labels(
    sample_index: pd.DataFrame,
    event_catalog: pd.DataFrame,
    *,
    prediction_time_col: str = "prediction_time_utc",
    coastal_zone_col: str = "coastal_zone_id",
    horizon_hours: int = 24,
    positive_confidences: Iterable[str | LabelConfidence] = (
        LabelConfidence.A,
        LabelConfidence.B,
    ),
    known_negative_col: str | None = None,
    label_mode: Literal["weak_rule", "official_warning", "confirmed_impact"] = ("confirmed_impact"),
) -> pd.DataFrame:
    """Attach list-valued hazard targets, masks and audit metadata to samples."""

    required = {prediction_time_col, coastal_zone_col}
    missing = required.difference(sample_index.columns)
    if missing:
        raise KeyError(f"sample index missing columns: {sorted(missing)}")
    rows: list[dict[str, object]] = []
    for sample in sample_index.to_dict(orient="records"):
        known_negative: bool | Sequence[bool] = False
        if known_negative_col is not None:
            if known_negative_col not in sample:
                raise KeyError(f"sample index missing known-negative column {known_negative_col!r}")
            known_negative = sample[known_negative_col]  # type: ignore[assignment]
        label = build_hazard_label(
            sample[prediction_time_col],
            str(sample[coastal_zone_col]),
            event_catalog,
            horizon_hours=horizon_hours,
            positive_confidences=positive_confidences,
            known_negative_mask=known_negative,
            label_mode=label_mode,
        )
        enriched = dict(sample)
        enriched.update(
            hazard_target=label.hazard_target.tolist(),
            hazard_mask=label.hazard_mask.tolist(),
            event_active=label.active_event,
            event_id=label.event_id,
            storm_group_id=label.storm_group_id,
            label_confidence=label.label_confidence,
            label_audit_reasons=list(label.audit_reasons),
        )
        rows.append(enriched)
    return pd.DataFrame(rows)


def assert_unknown_not_negative(label: HazardLabel, unknown_leads: Sequence[bool]) -> None:
    unknown = np.asarray(unknown_leads, dtype=np.bool_)
    if unknown.shape != label.hazard_mask.shape:
        raise ValueError("unknown_leads shape does not match hazard label")
    if np.any(label.hazard_mask[unknown] & (label.hazard_target[unknown] == 0.0)):
        raise LabelConstructionError("unknown evidence was incorrectly treated as a negative")


# Compatibility aliases for the names used in the prose specification.
build_hazard_targets = build_hazard_labels
construct_hazard_label = build_hazard_label


__all__ = [
    "HazardLabel",
    "LabelConstructionError",
    "assert_unknown_not_negative",
    "build_hazard_label",
    "build_hazard_labels",
    "build_hazard_targets",
    "construct_hazard_label",
]
