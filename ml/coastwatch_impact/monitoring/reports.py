"""Auditable monitoring, delayed evaluation, and drift reports.

These jobs are intentionally report-only. They never import the training
orchestration and cannot update, promote, or retrain a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import ValidationError
from scipy.stats import wasserstein_distance  # type: ignore[import-untyped]

from ..data.schemas import validate_event_catalog_frame, validate_sites_frame
from ..evaluation.events import evaluate_alert_events
from ..provenance import sha256_file
from .schemas import (
    FeatureDistributionSummary,
    InternalErrorAuditRecord,
    PredictionAuditRecord,
    RequestAuditRecord,
)

REPORT_SCHEMA_VERSION = "coastwatch-monitoring/v1"


@dataclass(frozen=True)
class ParsedAuditLog:
    path: Path
    sha256: str
    predictions: tuple[PredictionAuditRecord, ...]
    requests: tuple[RequestAuditRecord, ...]
    internal_errors: tuple[InternalErrorAuditRecord, ...]
    ignored_messages: dict[str, int]


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            _json_ready(payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def write_atomic_hashed_report(
    output: str | Path,
    payload: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Atomically write a report and SHA-256 sidecar, or only validate on dry-run."""

    destination = Path(output).expanduser().resolve()
    content = _canonical_json(payload)
    digest = hashlib.sha256(content).hexdigest()
    checksum_path = destination.with_name(f"{destination.name}.sha256")
    if dry_run:
        return {
            "output": str(destination),
            "checksum_path": str(checksum_path),
            "sha256": digest,
            "written": False,
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    report_staging = destination.parent / f".{destination.name}.{token}.tmp"
    hash_staging = destination.parent / f".{checksum_path.name}.{token}.tmp"
    try:
        report_staging.write_bytes(content)
        hash_staging.write_text(f"{digest}  {destination.name}\n", encoding="ascii")
        os.replace(report_staging, destination)
        os.replace(hash_staging, checksum_path)
    finally:
        report_staging.unlink(missing_ok=True)
        hash_staging.unlink(missing_ok=True)
    if sha256_file(destination) != digest:
        raise RuntimeError("monitoring report checksum verification failed after atomic write")
    return {
        "output": str(destination),
        "checksum_path": str(checksum_path),
        "sha256": digest,
        "written": True,
    }


def read_audit_jsonl(path: str | Path) -> ParsedAuditLog:
    """Parse known audit records strictly and inventory unrelated JSON log messages."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"audit JSONL does not exist: {source}")
    predictions: list[PredictionAuditRecord] = []
    requests: list[RequestAuditRecord] = []
    internal_errors: list[InternalErrorAuditRecord] = []
    ignored: Counter[str] = Counter()
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{source}:{line_number}: malformed JSON: {error}") from error
            if not isinstance(payload, dict):
                raise ValueError(f"{source}:{line_number}: every JSONL record must be an object")
            message = payload.get("message")
            try:
                if message == "shadow_prediction":
                    predictions.append(PredictionAuditRecord.model_validate(payload))
                elif message == "shadow_request":
                    requests.append(RequestAuditRecord.model_validate(payload))
                elif message == "shadow_internal_error":
                    internal_errors.append(InternalErrorAuditRecord.model_validate(payload))
                else:
                    ignored[str(message) if message is not None else "<missing-message>"] += 1
            except ValidationError as error:
                raise ValueError(
                    f"{source}:{line_number}: invalid {message!r} audit schema: {error}"
                ) from error

    prediction_ids = [record.request_id for record in predictions]
    if len(prediction_ids) != len(set(prediction_ids)):
        duplicates = sorted(key for key, count in Counter(prediction_ids).items() if count > 1)
        raise ValueError(f"duplicate prediction request_id values: {duplicates[:5]}")
    return ParsedAuditLog(
        path=source,
        sha256=sha256_file(source),
        predictions=tuple(predictions),
        requests=tuple(requests),
        internal_errors=tuple(internal_errors),
        ignored_messages=dict(sorted(ignored.items())),
    )


def _numeric_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "p05": None,
            "p50": None,
            "p95": None,
        }
    array = np.asarray(values, dtype=np.float64)
    if not np.isfinite(array).all():
        raise ValueError("non-finite value reached monitoring aggregation")
    return {
        "count": int(array.size),
        "min": float(array.min()),
        "max": float(array.max()),
        "mean": float(array.mean()),
        "p05": float(np.quantile(array, 0.05)),
        "p50": float(np.quantile(array, 0.50)),
        "p95": float(np.quantile(array, 0.95)),
    }


def _log_time_range(records: ParsedAuditLog) -> dict[str, str | None]:
    timestamps = [record.timestamp_utc for record in records.predictions]
    timestamps.extend(record.timestamp_utc for record in records.requests)
    timestamps.extend(record.timestamp_utc for record in records.internal_errors)
    return {
        "first_utc": min(timestamps).isoformat() if timestamps else None,
        "last_utc": max(timestamps).isoformat() if timestamps else None,
    }


def _data_classification(
    records: tuple[PredictionAuditRecord, ...],
    declared: bool | None,
    *,
    required: bool,
) -> tuple[bool | None, str]:
    logged = {record.synthetic_data for record in records if record.synthetic_data is not None}
    if len(logged) > 1:
        raise ValueError("prediction audit log mixes synthetic_data=true and false")
    logged_value = next(iter(logged)) if logged else None
    if declared is not None and logged_value is not None and declared != logged_value:
        raise ValueError("declared synthetic/real classification conflicts with prediction log")
    if logged_value is not None:
        return logged_value, "prediction_audit_log"
    if declared is not None:
        return declared, "explicit_cli_declaration"
    if required:
        raise ValueError(
            "prediction logs do not contain synthetic_data; pass an explicit synthetic/real "
            "declaration rather than guessing"
        )
    return None, "unavailable"


def _feature_summaries(
    records: tuple[PredictionAuditRecord, ...],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[float]]]:
    grouped: dict[str, list[FeatureDistributionSummary]] = defaultdict(list)
    samples: dict[str, list[float]] = defaultdict(list)
    for record in records:
        if record.input_summary is None:
            continue
        for name, summary in record.input_summary.features.items():
            grouped[name].append(summary)
            if summary.distribution_sample:
                samples[name].extend(summary.distribution_sample)
    output: dict[str, dict[str, Any]] = {}
    for name, summaries in sorted(grouped.items()):
        observed = sum(summary.observed_count for summary in summaries)
        missing = sum(summary.missing_count for summary in summaries)
        minima = [summary.minimum for summary in summaries if summary.minimum is not None]
        maxima = [summary.maximum for summary in summaries if summary.maximum is not None]
        output[name] = {
            "summary_records": len(summaries),
            "observed_count": observed,
            "missing_count": missing,
            "missing_fraction": missing / (observed + missing) if observed + missing else None,
            "minimum": min(minima) if minima else None,
            "maximum": max(maxima) if maxima else None,
            "distribution_sample_count": len(samples[name]),
        }
    return output, dict(samples)


def build_monitoring_report(
    audit_log: str | Path,
    *,
    declared_synthetic_data: bool | None = None,
) -> dict[str, Any]:
    """Aggregate only evidence present in historical JSONL audit records."""

    parsed = read_audit_jsonl(audit_log)
    if not parsed.predictions:
        raise ValueError("audit log contains no valid shadow_prediction records")
    warnings: list[dict[str, str]] = []
    if parsed.ignored_messages:
        warnings.append(
            {
                "code": "ignored_non_audit_messages",
                "detail": "Unrelated JSON messages were inventoried but did not affect metrics.",
            }
        )
    synthetic_data, classification_source = _data_classification(
        parsed.predictions, declared_synthetic_data, required=False
    )
    if synthetic_data is None:
        warnings.append(
            {
                "code": "data_classification_unavailable",
                "detail": (
                    "The log has no synthetic_data field; the report does not infer real data."
                ),
            }
        )

    freshness: dict[str, list[float]] = defaultdict(list)
    stale_counts: Counter[str] = Counter()
    source_model_runs: dict[str, set[str]] = defaultdict(set)
    for record in parsed.predictions:
        for source, issue_time in record.source_issue_times.items():
            freshness[source].append(
                (record.prediction_time_utc - issue_time).total_seconds() / 3600.0
            )
        stale_counts.update(record.data_quality.stale_sources)
        for provenance in record.issued_forecast_provenance:
            source_model_runs[provenance.source_model].add(provenance.model_run_id)
    source_freshness = {
        source: {
            **_numeric_summary(freshness.get(source, [])),
            "stale_count": stale_counts[source],
            "stale_rate": (
                stale_counts[source] / len(freshness[source]) if freshness.get(source) else None
            ),
        }
        for source in sorted(set(freshness) | set(stale_counts))
    }
    if not source_freshness:
        warnings.append(
            {
                "code": "source_freshness_unavailable",
                "detail": "No source_issue_times were logged; freshness was not estimated.",
            }
        )

    horizons = sorted(
        {horizon for record in parsed.predictions for horizon in record.calibrated_probabilities}
    )
    prediction_distribution = {
        horizon: _numeric_summary(
            [
                record.calibrated_probabilities[horizon]
                for record in parsed.predictions
                if horizon in record.calibrated_probabilities
            ]
        )
        for horizon in horizons
    }
    if any(
        summary["count"] != len(parsed.predictions) for summary in prediction_distribution.values()
    ):
        warnings.append(
            {
                "code": "inconsistent_prediction_horizons",
                "detail": "Not every prediction record contains the same horizon set.",
            }
        )

    feature_ranges, _ = _feature_summaries(parsed.predictions)
    if not feature_ranges:
        warnings.append(
            {
                "code": "input_ranges_unavailable",
                "detail": (
                    "Current audit records contain no input_summary; feature ranges were not "
                    "invented."
                ),
            }
        )
    site_rows: list[dict[str, Any]] = []
    continuous_ood_available = False
    for site_id in sorted({record.site_id for record in parsed.predictions}):
        site = [record for record in parsed.predictions if record.site_id == site_id]
        ood_scores = [
            record.input_summary.ood_score
            for record in site
            if record.input_summary is not None and record.input_summary.ood_score is not None
        ]
        continuous_ood_available = continuous_ood_available or bool(ood_scores)
        site_rows.append(
            {
                "site_id": site_id,
                "prediction_count": len(site),
                "missing_fraction": _numeric_summary(
                    [record.data_quality.missing_fraction for record in site]
                ),
                "out_of_domain_rate_binary_proxy": sum(
                    record.data_quality.out_of_domain for record in site
                )
                / len(site),
                "ood_score": _numeric_summary([float(value) for value in ood_scores]),
            }
        )
    if not continuous_ood_available:
        warnings.append(
            {
                "code": "continuous_ood_score_unavailable",
                "detail": (
                    "Only the logged binary out_of_domain flag is available; its per-site rate "
                    "is a proxy, not a continuous OOD score."
                ),
            }
        )

    request_latency = [record.latency_ms for record in parsed.requests]
    prediction_latency = [record.latency_ms for record in parsed.predictions]
    failures = [record for record in parsed.requests if record.status_code >= 400]
    if not parsed.requests:
        warnings.append(
            {
                "code": "api_request_records_unavailable",
                "detail": (
                    "No shadow_request records were present; API failure rate cannot be computed."
                ),
            }
        )
    fallback_count = sum(
        record.data_quality.status in {"degraded_obs_only", "degraded_physics_only"}
        for record in parsed.predictions
    )
    status_counts = Counter(record.data_quality.status for record in parsed.predictions)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": "realtime_monitoring_aggregate",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": {"path": str(parsed.path), "sha256": parsed.sha256},
        "evidence_window": _log_time_range(parsed),
        "synthetic_data": synthetic_data,
        "data_classification_source": classification_source,
        "record_counts": {
            "predictions": len(parsed.predictions),
            "requests": len(parsed.requests),
            "internal_errors": len(parsed.internal_errors),
            "ignored_messages": sum(parsed.ignored_messages.values()),
        },
        "ignored_message_inventory": parsed.ignored_messages,
        "source_freshness_hours": source_freshness,
        "source_model_run_inventory": {
            source: sorted(run_ids) for source, run_ids in sorted(source_model_runs.items())
        },
        "missingness": {
            "overall": _numeric_summary(
                [record.data_quality.missing_fraction for record in parsed.predictions]
            ),
            "per_feature": feature_ranges,
        },
        "data_ranges": feature_ranges,
        "prediction_distribution": prediction_distribution,
        "per_site_out_of_domain": site_rows,
        "api": {
            "request_count": len(parsed.requests),
            "failure_count": len(failures),
            "failure_rate": len(failures) / len(parsed.requests) if parsed.requests else None,
            "status_counts": dict(sorted(Counter(r.status_code for r in parsed.requests).items())),
            "internal_error_log_count": len(parsed.internal_errors),
        },
        "latency_ms": {
            "prediction": _numeric_summary(prediction_latency),
            "api_request": _numeric_summary(request_latency),
        },
        "fallback": {
            "count": fallback_count,
            "rate": fallback_count / len(parsed.predictions),
            "data_quality_status_counts": dict(sorted(status_counts.items())),
        },
        "evidence_warnings": warnings,
        "retraining_triggered": False,
    }


def _read_table(path: str | Path) -> pd.DataFrame:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"table does not exist: {source}")
    suffix = source.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(source)
    if suffix == ".csv":
        return pd.read_csv(source)
    if suffix == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise ValueError("JSON event/site table must be an array of objects")
        return pd.DataFrame(payload)
    raise ValueError(f"unsupported table suffix {suffix!r}; use .parquet, .csv, or .json")


def _evaluation_events(event_catalog: str | Path, sites: str | Path | None) -> pd.DataFrame:
    events = validate_event_catalog_frame(_read_table(event_catalog))
    if "site_id" not in events.columns or events["site_id"].isna().any():
        if sites is None:
            raise ValueError(
                "event catalog has no complete site_id column; provide --sites for reviewed "
                "coastal_zone_id to site_id mapping"
            )
        site_table = validate_sites_frame(_read_table(sites))
        mapping = site_table.loc[site_table["active"], ["site_id", "coastal_zone_id"]]
        if mapping["coastal_zone_id"].duplicated().any():
            raise ValueError("active site mapping is not one-to-one by coastal_zone_id")
        events = events.drop(columns=["site_id"], errors="ignore").merge(
            mapping, on="coastal_zone_id", how="left", validate="many_to_one"
        )
        if events["site_id"].isna().any():
            zones = sorted(events.loc[events["site_id"].isna(), "coastal_zone_id"].unique())
            raise ValueError(f"event catalog contains unmapped coastal zones: {zones[:5]}")
    return events


def _cadence_evidence(predictions: pd.DataFrame) -> dict[str, Any]:
    missing_hours = 0
    expected_hours = 0
    duplicate_rows = int(predictions.duplicated(["site_id", "prediction_time_utc"]).sum())
    if duplicate_rows:
        raise ValueError("prediction timeline has duplicate site/time rows")
    for _, rows in predictions.groupby("site_id"):
        times = pd.DatetimeIndex(rows["prediction_time_utc"].sort_values())
        if times.empty:
            continue
        expected = int((times[-1] - times[0]) / pd.Timedelta(hours=1)) + 1
        expected_hours += expected
        missing_hours += max(0, expected - len(times))
    return {
        "expected_hourly_rows": expected_hours,
        "observed_rows": len(predictions),
        "missing_hourly_rows": missing_hours,
        "coverage_fraction": (
            (expected_hours - missing_hours) / expected_hours if expected_hours else None
        ),
        "continuous_hourly_timeline": missing_hours == 0,
    }


def build_delayed_evaluation_report(
    audit_log: str | Path,
    event_catalog: str | Path,
    *,
    sites: str | Path | None = None,
    horizon: str = "24h",
    threshold: float,
    window_days: int = 90,
    step_days: int = 30,
    merge_gap_hours: int = 2,
    cooldown_hours: int = 6,
    lookahead_hours: int = 24,
    minimum_events_for_evidence: int = 5,
    declared_synthetic_data: bool | None = None,
) -> dict[str, Any]:
    """Re-match immutable prediction logs to a newer reviewed event catalog."""

    if window_days < 1 or step_days < 1:
        raise ValueError("window_days and step_days must be positive")
    parsed = read_audit_jsonl(audit_log)
    if not parsed.predictions:
        raise ValueError("audit log contains no valid shadow_prediction records")
    synthetic_data, classification_source = _data_classification(
        parsed.predictions, declared_synthetic_data, required=True
    )
    missing_horizon = [
        record.request_id
        for record in parsed.predictions
        if horizon not in record.calibrated_probabilities
    ]
    if missing_horizon:
        raise ValueError(
            f"horizon {horizon!r} is absent from prediction records: {missing_horizon[:5]}"
        )
    predictions = pd.DataFrame(
        {
            "site_id": [record.site_id for record in parsed.predictions],
            "prediction_time_utc": pd.DatetimeIndex(
                [record.prediction_time_utc for record in parsed.predictions]
            ),
            "event_probability": [
                record.calibrated_probabilities[horizon] for record in parsed.predictions
            ],
        }
    ).sort_values(["site_id", "prediction_time_utc"], kind="stable")
    events = _evaluation_events(event_catalog, sites)
    prediction_sites = set(predictions["site_id"].astype(str))
    unknown_sites = sorted(set(events["site_id"].astype(str)) - prediction_sites)
    warnings: list[dict[str, str]] = []
    if unknown_sites:
        warnings.append(
            {
                "code": "events_outside_prediction_sites",
                "detail": f"Events for {len(unknown_sites)} sites have no prediction log coverage.",
            }
        )
    cadence = _cadence_evidence(predictions)
    if not cadence["continuous_hourly_timeline"]:
        warnings.append(
            {
                "code": "incomplete_prediction_timeline",
                "detail": (
                    "Event and false-alert metrics are reported, but missing hourly predictions "
                    "weaken evidence."
                ),
            }
        )

    earliest = pd.Timestamp(predictions["prediction_time_utc"].min())
    latest = pd.Timestamp(predictions["prediction_time_utc"].max())
    event_onsets = pd.to_datetime(events["onset_time_utc"], utc=True, errors="coerce")
    eligible_event_mask = (
        event_onsets.notna()
        & (event_onsets >= earliest)
        & (event_onsets <= latest)
        & events["site_id"].astype(str).isin(prediction_sites)
    )
    eligible_events = events.loc[eligible_event_mask].copy()
    excluded_events = int((~eligible_event_mask).sum())
    if excluded_events:
        warnings.append(
            {
                "code": "catalog_events_outside_evaluation_coverage",
                "detail": (
                    f"{excluded_events} catalog rows outside the logged time/site coverage were "
                    "excluded from metrics."
                ),
            }
        )
    first_end = earliest + pd.Timedelta(days=window_days)
    if first_end >= latest:
        ends = [latest]
    else:
        ends = list(pd.date_range(first_end, latest, freq=f"{step_days}D"))
        if not ends or ends[-1] != latest:
            ends.append(latest)
    rolling: list[dict[str, Any]] = []
    for end in ends:
        start = max(earliest, end - pd.Timedelta(days=window_days))
        prediction_window = predictions.loc[
            (predictions["prediction_time_utc"] >= start)
            & (predictions["prediction_time_utc"] <= end)
        ]
        onset = pd.to_datetime(eligible_events["onset_time_utc"], utc=True, errors="raise")
        event_window = eligible_events.loc[(onset >= start) & (onset <= end)]
        evaluation = evaluate_alert_events(
            prediction_window,
            event_window,
            threshold,
            merge_gap_hours=merge_gap_hours,
            cooldown_hours=cooldown_hours,
            lookahead_hours=lookahead_hours,
            minimum_events_for_evidence=minimum_events_for_evidence,
        )
        rolling.append(
            {
                "window_start_utc": start.isoformat(),
                "window_end_utc": end.isoformat(),
                "prediction_rows": len(prediction_window),
                "catalog_rows_in_window": len(event_window),
                "metrics": evaluation.metrics,
            }
        )

    overall = evaluate_alert_events(
        predictions,
        eligible_events,
        threshold,
        merge_gap_hours=merge_gap_hours,
        cooldown_hours=cooldown_hours,
        lookahead_hours=lookahead_hours,
        minimum_events_for_evidence=minimum_events_for_evidence,
    )
    catalog_path = Path(event_catalog).expanduser().resolve()
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": "delayed_event_evaluation",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "prediction_log": {"path": str(parsed.path), "sha256": parsed.sha256},
        "event_catalog": {"path": str(catalog_path), "sha256": sha256_file(catalog_path)},
        "sites_mapping": (
            None
            if sites is None
            else {
                "path": str(Path(sites).expanduser().resolve()),
                "sha256": sha256_file(Path(sites).expanduser().resolve()),
            }
        ),
        "synthetic_data": synthetic_data,
        "data_classification_source": classification_source,
        "horizon": horizon,
        "threshold": threshold,
        "window_days": window_days,
        "step_days": step_days,
        "prediction_timeline_evidence": cadence,
        "event_catalog_scope": {
            "catalog_rows": len(events),
            "eligible_rows": len(eligible_events),
            "excluded_rows": excluded_events,
        },
        "overall_event_metrics": overall.metrics,
        "rolling_event_metrics": rolling,
        "evidence_warnings": warnings,
        "label_update_applied": True,
        "retraining_triggered": False,
        "next_action": "Human review only; any candidate must use the fixed retraining pipeline.",
    }


def _psi(reference: list[float], live: list[float], epsilon: float = 1e-6) -> float:
    ref = np.asarray(reference, dtype=np.float64)
    current = np.asarray(live, dtype=np.float64)
    if ref.size < 2 or current.size < 2:
        raise ValueError("PSI requires at least two reference and two live samples")
    quantiles = np.unique(np.quantile(ref, np.linspace(0.0, 1.0, 11)))
    if quantiles.size == 1:
        value = float(quantiles[0])
        scale = max(abs(value), 1.0) * 1e-9
        edges = np.asarray([-np.inf, value - scale, value + scale, np.inf])
    else:
        edges = np.concatenate(([-np.inf], quantiles[1:-1], [np.inf]))
    ref_counts, _ = np.histogram(ref, bins=edges)
    live_counts, _ = np.histogram(current, bins=edges)
    ref_rate = np.clip(ref_counts / ref_counts.sum(), epsilon, None)
    live_rate = np.clip(live_counts / live_counts.sum(), epsilon, None)
    return float(np.sum((live_rate - ref_rate) * np.log(live_rate / ref_rate)))


def _distribution_drift(reference: list[float], live: list[float]) -> dict[str, Any]:
    if len(reference) < 2 or len(live) < 2:
        return {
            "reference_sample_count": len(reference),
            "live_sample_count": len(live),
            "psi": None,
            "wasserstein_distance": None,
            "evidence_available": False,
        }
    return {
        "reference_sample_count": len(reference),
        "live_sample_count": len(live),
        "psi": _psi(reference, live),
        "wasserstein_distance": float(wasserstein_distance(reference, live)),
        "evidence_available": True,
    }


def _provenance_inventory(records: tuple[PredictionAuditRecord, ...]) -> dict[str, Any]:
    sources: dict[str, set[str]] = defaultdict(set)
    source_names: set[str] = set()
    for record in records:
        source_names.update(record.source_issue_times)
        for row in record.issued_forecast_provenance:
            source_names.add(row.source_model)
            sources[row.source_model].add(row.model_run_id)
    return {
        "forecast_sources": sorted(source_names),
        "model_run_ids": {source: sorted(ids) for source, ids in sorted(sources.items())},
        "model_versions": sorted({record.model_version for record in records}),
        "feature_manifest_hashes": sorted(
            {
                record.feature_manifest_hash
                for record in records
                if record.feature_manifest_hash is not None
            }
        ),
    }


def build_drift_report(
    reference_audit_log: str | Path,
    live_audit_log: str | Path,
    *,
    psi_review_threshold: float = 0.2,
    missingness_review_delta: float = 0.1,
) -> dict[str, Any]:
    """Compare a frozen reference replay with live logs; never trigger retraining."""

    if psi_review_threshold <= 0.0 or missingness_review_delta < 0.0:
        raise ValueError("drift review thresholds must be positive/non-negative")
    reference = read_audit_jsonl(reference_audit_log)
    live = read_audit_jsonl(live_audit_log)
    if not reference.predictions or not live.predictions:
        raise ValueError("reference and live logs must each contain shadow_prediction records")
    reference_class, reference_class_source = _data_classification(
        reference.predictions, None, required=False
    )
    live_class, live_class_source = _data_classification(live.predictions, None, required=False)
    reference_features, reference_samples = _feature_summaries(reference.predictions)
    live_features, live_samples = _feature_summaries(live.predictions)
    warnings: list[dict[str, str]] = []
    common_features = sorted(set(reference_features) & set(live_features))
    if not common_features:
        warnings.append(
            {
                "code": "input_feature_drift_unavailable",
                "detail": (
                    "Reference/live logs have no common input_summary features; feature PSI and "
                    "Wasserstein were not invented."
                ),
            }
        )
    if reference_class is None or live_class is None:
        warnings.append(
            {
                "code": "drift_data_classification_unavailable",
                "detail": (
                    "At least one log lacks synthetic_data; classification was left unavailable "
                    "rather than inferred."
                ),
            }
        )
    feature_drift: dict[str, dict[str, Any]] = {}
    for feature in common_features:
        distances = _distribution_drift(
            reference_samples.get(feature, []), live_samples.get(feature, [])
        )
        ref_missing = reference_features[feature]["missing_fraction"]
        live_missing = live_features[feature]["missing_fraction"]
        missing_delta = (
            None
            if ref_missing is None or live_missing is None
            else float(live_missing - ref_missing)
        )
        feature_drift[feature] = {
            **distances,
            "reference_missing_fraction": ref_missing,
            "live_missing_fraction": live_missing,
            "missingness_delta": missing_delta,
            "review_flag": (
                (distances["psi"] is not None and distances["psi"] >= psi_review_threshold)
                or (missing_delta is not None and abs(missing_delta) >= missingness_review_delta)
            ),
        }
        if not distances["evidence_available"]:
            warnings.append(
                {
                    "code": "feature_distribution_sample_unavailable",
                    "detail": (
                        f"Feature {feature!r} lacks enough explicit distribution samples for "
                        "PSI/Wasserstein."
                    ),
                }
            )

    ref_by_site: dict[str, list[PredictionAuditRecord]] = defaultdict(list)
    live_by_site: dict[str, list[PredictionAuditRecord]] = defaultdict(list)
    for record in reference.predictions:
        ref_by_site[record.site_id].append(record)
    for record in live.predictions:
        live_by_site[record.site_id].append(record)
    common_sites = sorted(set(ref_by_site) & set(live_by_site))
    per_site: list[dict[str, Any]] = []
    for site_id in common_sites:
        ref_rows = ref_by_site[site_id]
        live_rows = live_by_site[site_id]
        common_horizons = sorted(
            set.intersection(
                *(set(row.calibrated_probabilities) for row in [*ref_rows, *live_rows])
            )
        )
        probability_drift = {
            horizon: _distribution_drift(
                [row.calibrated_probabilities[horizon] for row in ref_rows],
                [row.calibrated_probabilities[horizon] for row in live_rows],
            )
            for horizon in common_horizons
        }
        ref_missing = float(np.mean([row.data_quality.missing_fraction for row in ref_rows]))
        live_missing = float(np.mean([row.data_quality.missing_fraction for row in live_rows]))
        ref_ood = float(np.mean([row.data_quality.out_of_domain for row in ref_rows]))
        live_ood = float(np.mean([row.data_quality.out_of_domain for row in live_rows]))
        site_flag = abs(live_missing - ref_missing) >= missingness_review_delta or any(
            value["psi"] is not None and value["psi"] >= psi_review_threshold
            for value in probability_drift.values()
        )
        per_site.append(
            {
                "site_id": site_id,
                "reference_count": len(ref_rows),
                "live_count": len(live_rows),
                "reference_missing_fraction": ref_missing,
                "live_missing_fraction": live_missing,
                "missingness_delta": live_missing - ref_missing,
                "reference_out_of_domain_rate": ref_ood,
                "live_out_of_domain_rate": live_ood,
                "out_of_domain_rate_delta": live_ood - ref_ood,
                "prediction_distribution_drift": probability_drift,
                "review_flag": site_flag,
            }
        )

    reference_provenance = _provenance_inventory(reference.predictions)
    live_provenance = _provenance_inventory(live.predictions)
    source_change = {
        "reference": reference_provenance,
        "live": live_provenance,
        "forecast_sources_changed": (
            reference_provenance["forecast_sources"] != live_provenance["forecast_sources"]
        ),
        "forecast_model_runs_changed": (
            reference_provenance["model_run_ids"] != live_provenance["model_run_ids"]
        ),
        "impact_model_versions_changed": (
            reference_provenance["model_versions"] != live_provenance["model_versions"]
        ),
        "feature_manifest_changed": (
            reference_provenance["feature_manifest_hashes"]
            != live_provenance["feature_manifest_hashes"]
        ),
    }
    review_reasons: list[str] = []
    if any(value["review_flag"] for value in feature_drift.values()):
        review_reasons.append("feature_drift_threshold_exceeded")
    if any(value["review_flag"] for value in per_site):
        review_reasons.append("site_specific_drift_threshold_exceeded")
    if any(
        source_change[key]
        for key in (
            "forecast_sources_changed",
            "forecast_model_runs_changed",
            "impact_model_versions_changed",
            "feature_manifest_changed",
        )
    ):
        review_reasons.append("source_or_model_provenance_changed")
    if reference_class is not None and live_class is not None and reference_class != live_class:
        review_reasons.append("synthetic_real_classification_changed")
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": "reference_vs_live_drift",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "reference": {"path": str(reference.path), "sha256": reference.sha256},
        "live": {"path": str(live.path), "sha256": live.sha256},
        "data_classification": {
            "reference_synthetic_data": reference_class,
            "reference_source": reference_class_source,
            "live_synthetic_data": live_class,
            "live_source": live_class_source,
        },
        "thresholds": {
            "psi_manual_review": psi_review_threshold,
            "absolute_missingness_delta_manual_review": missingness_review_delta,
            "wasserstein": "reported without a universal scale-free alarm threshold",
        },
        "feature_drift": feature_drift,
        "features_only_in_reference": sorted(set(reference_features) - set(live_features)),
        "features_only_in_live": sorted(set(live_features) - set(reference_features)),
        "site_specific_drift": per_site,
        "sites_only_in_reference": sorted(set(ref_by_site) - set(live_by_site)),
        "sites_only_in_live": sorted(set(live_by_site) - set(ref_by_site)),
        "source_and_model_change_detection": source_change,
        "evidence_warnings": warnings,
        "manual_review_required": bool(review_reasons),
        "manual_review_reasons": review_reasons,
        "retraining_triggered": False,
        "automatic_retraining_allowed": False,
    }


__all__ = [
    "ParsedAuditLog",
    "build_delayed_evaluation_report",
    "build_drift_report",
    "build_monitoring_report",
    "read_audit_jsonl",
    "write_atomic_hashed_report",
]
