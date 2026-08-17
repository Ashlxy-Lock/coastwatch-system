"""Alert-episode construction and one-to-one pre-onset event evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

EPISODE_COLUMNS = [
    "episode_id",
    "site_id",
    "start_time_utc",
    "end_time_utc",
    "peak_time_utc",
    "peak_probability",
    "above_threshold_hours",
]


def _utc_series(values: pd.Series, name: str) -> pd.Series:
    if values.empty:
        return pd.Series(pd.DatetimeIndex([], tz="UTC"), index=values.index, name=name)
    parsed: list[pd.Timestamp] = []
    for value in values:
        if pd.isna(value):
            raise ValueError(f"{name} contains a missing timestamp")
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"{name} must contain timezone-aware timestamps")
        parsed.append(timestamp.tz_convert("UTC"))
    return pd.Series(pd.DatetimeIndex(parsed), index=values.index, name=name)


def _validate_probability(value: float, name: str = "threshold") -> float:
    result = float(value)
    if not np.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must lie in [0, 1]")
    return result


def merge_alert_episodes(
    predictions: pd.DataFrame,
    threshold: float,
    *,
    site_col: str = "site_id",
    time_col: str = "prediction_time_utc",
    probability_col: str = "event_probability",
    merge_gap_hours: int = 2,
    cooldown_hours: int = 6,
) -> pd.DataFrame:
    """Merge above-threshold hourly predictions into auditable episodes.

    First, runs separated by at most ``merge_gap_hours`` missing hourly points
    are joined.  Then alerts restarting during the configured cooldown are
    folded into the same episode so oscillation around a threshold is not
    counted as many false alerts.
    """

    threshold = _validate_probability(threshold)
    if merge_gap_hours < 0 or cooldown_hours < 0:
        raise ValueError("merge gap and cooldown must be non-negative")
    required = {site_col, time_col, probability_col}
    missing = required.difference(predictions.columns)
    if missing:
        raise ValueError(f"prediction table is missing columns: {sorted(missing)}")
    if predictions.empty:
        return pd.DataFrame(columns=EPISODE_COLUMNS)

    frame = predictions[[site_col, time_col, probability_col]].copy()
    frame[site_col] = frame[site_col].astype(str)
    frame[time_col] = _utc_series(frame[time_col], time_col)
    frame[probability_col] = pd.to_numeric(frame[probability_col], errors="coerce")
    probabilities = frame[probability_col].to_numpy(dtype=np.float64)
    if not np.isfinite(probabilities).all() or np.any(
        (probabilities < 0.0) | (probabilities > 1.0)
    ):
        raise ValueError("event probabilities must be finite and lie in [0, 1]")
    if frame.duplicated([site_col, time_col]).any():
        raise ValueError("predictions must be unique by site and prediction time")
    frame = frame.sort_values([site_col, time_col], kind="stable")
    frame = frame.loc[frame[probability_col] >= threshold]
    if frame.empty:
        return pd.DataFrame(columns=EPISODE_COLUMNS)

    preliminary: list[dict[str, Any]] = []
    allowed_delta = pd.Timedelta(hours=merge_gap_hours + 1)
    for site_id, site_rows in frame.groupby(site_col, sort=True):
        current: dict[str, Any] | None = None
        last_time: pd.Timestamp | None = None
        for row in site_rows.itertuples(index=False, name=None):
            timestamp = row[1]
            probability = float(row[2])
            if current is None or (last_time is not None and timestamp - last_time > allowed_delta):
                if current is not None:
                    preliminary.append(current)
                current = {
                    "site_id": site_id,
                    "start_time_utc": timestamp,
                    "end_time_utc": timestamp,
                    "peak_time_utc": timestamp,
                    "peak_probability": probability,
                    "above_threshold_hours": 1,
                }
            else:
                current["end_time_utc"] = timestamp
                current["above_threshold_hours"] += 1
                if probability > current["peak_probability"]:
                    current["peak_probability"] = probability
                    current["peak_time_utc"] = timestamp
            last_time = timestamp
        if current is not None:
            preliminary.append(current)

    consolidated: list[dict[str, Any]] = []
    cooldown = pd.Timedelta(hours=cooldown_hours)
    for candidate in preliminary:
        previous = consolidated[-1] if consolidated else None
        if (
            previous is not None
            and previous["site_id"] == candidate["site_id"]
            and candidate["start_time_utc"] - previous["end_time_utc"] <= cooldown
        ):
            previous["end_time_utc"] = candidate["end_time_utc"]
            previous["above_threshold_hours"] += candidate["above_threshold_hours"]
            if candidate["peak_probability"] > previous["peak_probability"]:
                previous["peak_probability"] = candidate["peak_probability"]
                previous["peak_time_utc"] = candidate["peak_time_utc"]
        else:
            consolidated.append(candidate.copy())

    for index, episode in enumerate(consolidated, start=1):
        episode["episode_id"] = f"alert-{index:06d}"
    return pd.DataFrame(consolidated)[EPISODE_COLUMNS]


@dataclass(frozen=True)
class EventMatchResult:
    """Complete event and episode tables after one-to-one matching."""

    event_matches: pd.DataFrame
    episode_matches: pd.DataFrame

    @property
    def false_episodes(self) -> pd.DataFrame:
        if self.episode_matches.empty:
            return self.episode_matches.copy()
        return self.episode_matches.loc[self.episode_matches["matched_event_id"].isna()].copy()


def match_alerts_to_events(
    episodes: pd.DataFrame,
    events: pd.DataFrame,
    *,
    lookahead_hours: int = 24,
    allowed_label_confidence: tuple[str, ...] = ("A", "B"),
) -> EventMatchResult:
    """Match one episode to at most one later confirmed event at the same site."""

    if lookahead_hours < 1:
        raise ValueError("lookahead_hours must be positive")
    event_required = {"event_id", "site_id", "onset_time_utc"}
    missing_events = event_required.difference(events.columns)
    if missing_events:
        raise ValueError(f"event table is missing columns: {sorted(missing_events)}")
    episode_required = {
        "episode_id",
        "site_id",
        "start_time_utc",
        "end_time_utc",
    }
    missing_episodes = episode_required.difference(episodes.columns)
    if missing_episodes:
        raise ValueError(f"episode table is missing columns: {sorted(missing_episodes)}")

    confirmed = events.copy()
    if "impact_confirmed" in confirmed:
        confirmed = confirmed.loc[confirmed["impact_confirmed"] == True]  # noqa: E712
    if "label_confidence" in confirmed:
        confirmed = confirmed.loc[confirmed["label_confidence"].isin(allowed_label_confidence)]
    if "onset_precision" in confirmed:
        confirmed = confirmed.loc[confirmed["onset_precision"] == "exact_hour"]
    confirmed = confirmed.copy()
    confirmed["site_id"] = confirmed["site_id"].astype(str)
    confirmed["onset_time_utc"] = _utc_series(confirmed["onset_time_utc"], "onset_time_utc")
    if confirmed["event_id"].astype(str).duplicated().any():
        raise ValueError("event_id must be unique in the evaluation event catalog")
    confirmed = confirmed.sort_values("onset_time_utc", kind="stable").reset_index(drop=True)

    episode_table = episodes.copy()
    episode_table["site_id"] = episode_table["site_id"].astype(str)
    episode_table["start_time_utc"] = _utc_series(episode_table["start_time_utc"], "start_time_utc")
    episode_table["end_time_utc"] = _utc_series(episode_table["end_time_utc"], "end_time_utc")
    if episode_table["episode_id"].astype(str).duplicated().any():
        raise ValueError("episode_id must be unique")
    episode_table = episode_table.sort_values(
        ["site_id", "start_time_utc"], kind="stable"
    ).reset_index(drop=True)
    episode_table["matched_event_id"] = pd.Series([None] * len(episode_table), dtype="object")

    matched_episode_indices: set[int] = set()
    event_rows: list[dict[str, Any]] = []
    lookahead = pd.Timedelta(hours=lookahead_hours)
    for event in confirmed.to_dict(orient="records"):
        onset = event["onset_time_utc"]
        candidates = episode_table.loc[
            (episode_table["site_id"] == event["site_id"])
            & (episode_table["start_time_utc"] < onset)
            & (episode_table["start_time_utc"] >= onset - lookahead)
            & (~episode_table.index.isin(matched_episode_indices))
        ]
        matched_index: int | None = None
        if not candidates.empty:
            # The first alert in the valid pre-onset window defines lead time.
            matched_index = int(candidates["start_time_utc"].idxmin())
            matched_episode_indices.add(matched_index)
            episode_table.at[matched_index, "matched_event_id"] = str(event["event_id"])
        output = dict(event)
        output["detected"] = matched_index is not None
        output["matched_episode_id"] = (
            episode_table.at[matched_index, "episode_id"] if matched_index is not None else None
        )
        output["alert_time_utc"] = (
            episode_table.at[matched_index, "start_time_utc"]
            if matched_index is not None
            else pd.NaT
        )
        output["lead_time_hours"] = (
            float(
                (onset - episode_table.at[matched_index, "start_time_utc"]) / pd.Timedelta(hours=1)
            )
            if matched_index is not None
            else np.nan
        )
        event_rows.append(output)

    event_matches = pd.DataFrame(event_rows)
    if event_matches.empty:
        event_matches = pd.DataFrame(
            columns=[
                *confirmed.columns,
                "detected",
                "matched_episode_id",
                "alert_time_utc",
                "lead_time_hours",
            ]
        )
    return EventMatchResult(event_matches=event_matches, episode_matches=episode_table)


@dataclass(frozen=True)
class EventEvaluation:
    episodes: pd.DataFrame
    event_matches: pd.DataFrame
    episode_matches: pd.DataFrame
    metrics: dict[str, Any]


def evaluate_alert_events(
    predictions: pd.DataFrame,
    events: pd.DataFrame,
    threshold: float,
    *,
    probability_col: str = "event_probability",
    merge_gap_hours: int = 2,
    cooldown_hours: int = 6,
    lookahead_hours: int = 24,
    minimum_events_for_evidence: int = 5,
) -> EventEvaluation:
    """Evaluate alert episodes on a complete continuous prediction timeline."""

    episodes = merge_alert_episodes(
        predictions,
        threshold,
        probability_col=probability_col,
        merge_gap_hours=merge_gap_hours,
        cooldown_hours=cooldown_hours,
    )
    matches = match_alerts_to_events(episodes, events, lookahead_hours=lookahead_hours)
    event_table = matches.event_matches
    episode_table = matches.episode_matches
    detected = int(event_table["detected"].sum()) if not event_table.empty else 0
    event_count = len(event_table)
    episode_count = len(episode_table)
    false_count = int(episode_table["matched_event_id"].isna().sum())
    recall = detected / event_count if event_count else None
    precision = detected / episode_count if episode_count else 0.0
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if recall is not None and precision + recall > 0.0
        else (0.0 if recall is not None else None)
    )

    prediction_times = _utc_series(predictions["prediction_time_utc"], "prediction_time_utc")
    site_month_frame = pd.DataFrame(
        {
            "site_id": predictions["site_id"].astype(str).to_numpy(),
            "month": prediction_times.dt.strftime("%Y-%m").to_numpy(),
        }
    )
    site_months = int(site_month_frame.drop_duplicates().shape[0])

    per_site: list[dict[str, Any]] = []
    if not event_table.empty:
        for site_id, rows in event_table.groupby("site_id", sort=True):
            site_detected = int(rows["detected"].sum())
            per_site.append(
                {
                    "site_id": str(site_id),
                    "events": len(rows),
                    "detected": site_detected,
                    "recall": site_detected / len(rows),
                }
            )
    site_recalls = [row["recall"] for row in per_site]
    zero_sites = sum(row["detected"] == 0 for row in per_site)
    lead_times = (
        event_table.loc[event_table["detected"], "lead_time_hours"].to_numpy(dtype=np.float64)
        if not event_table.empty
        else np.array([], dtype=np.float64)
    )
    metrics: dict[str, Any] = {
        "confirmed_events": event_count,
        "detected_events": detected,
        "missed_events": event_count - detected,
        "alert_episodes": episode_count,
        "false_alert_episodes": false_count,
        "event_recall": None if recall is None else float(recall),
        "event_precision": float(precision),
        "event_f1": None if f1 is None else float(f1),
        "median_lead_time_hours": (float(np.median(lead_times)) if lead_times.size else None),
        "lead_time_hours": lead_times.tolist(),
        "evaluated_site_months": site_months,
        "false_alert_episodes_per_site_month": (false_count / site_months if site_months else None),
        "fraction_of_event_sites_with_zero_detected_events": (
            zero_sites / len(per_site) if per_site else None
        ),
        "worst_site_event_recall": min(site_recalls) if site_recalls else None,
        "per_site": per_site,
        "insufficient_evidence": event_count < minimum_events_for_evidence,
    }
    return EventEvaluation(
        episodes=episodes,
        event_matches=event_table,
        episode_matches=episode_table,
        metrics=metrics,
    )


merge_alert_hours = merge_alert_episodes
match_alert_episodes = match_alerts_to_events


__all__ = [
    "EventEvaluation",
    "EventMatchResult",
    "evaluate_alert_events",
    "match_alert_episodes",
    "match_alerts_to_events",
    "merge_alert_episodes",
    "merge_alert_hours",
]
