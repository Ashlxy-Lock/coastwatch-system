from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from coastwatch_impact.cli import app
from coastwatch_impact.monitoring import (
    build_delayed_evaluation_report,
    build_drift_report,
    build_monitoring_report,
    read_audit_jsonl,
    write_atomic_hashed_report,
)

RUNNER = CliRunner()


def _prediction(
    index: int,
    when: datetime,
    *,
    probability: float = 0.1,
    status: str = "normal",
    model_version: str = "impact-v1",
    model_run_id: str = "weather-run-a",
    synthetic_data: bool | None = True,
    feature_values: list[float] | None = None,
) -> dict[str, Any]:
    source_issue = when - timedelta(hours=6)
    record: dict[str, Any] = {
        "timestamp_utc": (when + timedelta(minutes=1)).isoformat(),
        "level": "info",
        "logger": "coastwatch_impact.serve",
        "message": "shadow_prediction",
        "request_id": f"request-{index:05d}",
        "model_version": model_version,
        "model_variant": ("obs_only_tcn" if status == "degraded_obs_only" else "hybrid_tcn"),
        "site_id": "site-a",
        "prediction_time_utc": when.isoformat(),
        "feature_manifest_hash": "a" * 64,
        "source_issue_times": {"weather": source_issue.isoformat()},
        "issued_forecast_provenance": [
            {
                "source_model": "weather",
                "model_run_id": model_run_id,
                "issue_time_utc": source_issue.isoformat(),
                "valid_time_utc": (when + timedelta(hours=1)).isoformat(),
            }
        ],
        "data_quality": {
            "status": status,
            "missing_fraction": 0.2 if status != "normal" else 0.0,
            "stale_sources": ["weather"] if status != "normal" else [],
            "out_of_domain": False,
        },
        "raw_logits": [0.0, 0.1],
        "calibrated_probabilities": {"24h": probability},
        "research_band": "safe",
        "calibrated": True,
        "latency_ms": 4.0 + index,
        "shadow_mode": True,
    }
    if synthetic_data is not None:
        record["synthetic_data"] = synthetic_data
    if feature_values is not None:
        record["input_summary"] = {
            "features": {
                "surge_residual_m": {
                    "observed_count": len(feature_values),
                    "missing_count": 0,
                    "minimum": min(feature_values),
                    "maximum": max(feature_values),
                    "distribution_sample": feature_values,
                }
            },
            "ood_score": 0.05,
        }
    return record


def _request(index: int, when: datetime, status_code: int) -> dict[str, Any]:
    return {
        "timestamp_utc": (when + timedelta(minutes=2)).isoformat(),
        "level": "info",
        "logger": "coastwatch_impact.serve",
        "message": "shadow_request",
        "request_id": f"request-{index:05d}",
        "path": "/v1/predict/features",
        "status_code": status_code,
        "latency_ms": 7.0 + index,
        "shadow_mode": True,
    }


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _event_catalog(path: Path, onset: datetime) -> Path:
    rows = [
        {
            "event_id": "event-a",
            "storm_group_id": "storm-a",
            "coastal_zone_id": "zone-a",
            "site_id": "site-a",
            "onset_time_utc": onset.isoformat(),
            "peak_time_utc": (onset + timedelta(hours=1)).isoformat(),
            "end_time_utc": (onset + timedelta(hours=3)).isoformat(),
            "onset_precision": "exact_hour",
            "impact_confirmed": True,
            "impact_severity": 2,
            "label_confidence": "A",
            "warning_max_severity": 2,
            "spatial_evidence": True,
            "observational_evidence": True,
            "human_reviewed": True,
            "primary_source": "reviewed-test-catalog",
            "source_references_json": "[]",
            "review_notes": "synthetic engineering fixture",
            "created_at_utc": (onset + timedelta(hours=4)).isoformat(),
            "updated_at_utc": (onset + timedelta(hours=5)).isoformat(),
        }
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_monitoring_aggregates_only_logged_evidence(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    logs = _write_jsonl(
        tmp_path / "service.jsonl",
        [
            _prediction(0, start, probability=0.1),
            _request(0, start, 200),
            _prediction(1, start + timedelta(hours=1), probability=0.8, status="degraded_obs_only"),
            _request(1, start + timedelta(hours=1), 503),
            {
                "timestamp_utc": start.isoformat(),
                "level": "info",
                "logger": "uvicorn.error",
                "message": "Started server process",
            },
        ],
    )

    report = build_monitoring_report(logs)

    assert report["source_freshness_hours"]["weather"]["mean"] == 6.0
    assert report["prediction_distribution"]["24h"]["p50"] == pytest.approx(0.45)
    assert report["api"]["failure_rate"] == 0.5
    assert report["fallback"]["rate"] == 0.5
    assert report["synthetic_data"] is True
    assert report["record_counts"]["ignored_messages"] == 1
    warning_codes = {warning["code"] for warning in report["evidence_warnings"]}
    assert "input_ranges_unavailable" in warning_codes
    assert "continuous_ood_score_unavailable" in warning_codes
    assert report["data_ranges"] == {}
    assert report["retraining_triggered"] is False


def test_known_audit_schema_fails_closed(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    invalid = _prediction(0, start)
    invalid["invented_field"] = 123
    logs = _write_jsonl(tmp_path / "invalid.jsonl", [invalid])
    with pytest.raises(ValueError, match="invalid 'shadow_prediction' audit schema"):
        read_audit_jsonl(logs)

    naive = _prediction(1, start)
    naive["prediction_time_utc"] = "2026-01-01T00:00:00"
    logs = _write_jsonl(tmp_path / "naive.jsonl", [naive])
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        read_audit_jsonl(logs)


def test_delayed_evaluation_preserves_classification_and_hashes_output(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    onset = start + timedelta(hours=30)
    records = [
        _prediction(
            hour,
            start + timedelta(hours=hour),
            probability=0.9 if 24 <= hour <= 27 else 0.05,
        )
        for hour in range(48)
    ]
    logs = _write_jsonl(tmp_path / "predictions.jsonl", records)
    events = _event_catalog(tmp_path / "events.json", onset)

    report = build_delayed_evaluation_report(
        logs,
        events,
        threshold=0.5,
        window_days=1,
        step_days=1,
        minimum_events_for_evidence=1,
    )
    output = tmp_path / "delayed.json"
    artifact = write_atomic_hashed_report(output, report)

    assert report["synthetic_data"] is True
    assert report["overall_event_metrics"]["confirmed_events"] == 1
    assert report["overall_event_metrics"]["detected_events"] == 1
    assert len(report["rolling_event_metrics"]) == 2
    assert report["prediction_timeline_evidence"]["continuous_hourly_timeline"] is True
    assert report["retraining_triggered"] is False
    assert artifact["written"] is True
    assert hashlib.sha256(output.read_bytes()).hexdigest() == artifact["sha256"]
    assert output.with_name("delayed.json.sha256").is_file()


def test_delayed_evaluation_never_guesses_legacy_log_classification(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    logs = _write_jsonl(
        tmp_path / "legacy.jsonl",
        [_prediction(0, start, synthetic_data=None)],
    )
    events = _event_catalog(tmp_path / "events.json", start + timedelta(hours=1))
    with pytest.raises(ValueError, match="explicit synthetic/real declaration"):
        build_delayed_evaluation_report(logs, events, threshold=0.5)


def test_drift_reports_psi_wasserstein_site_and_source_changes(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    reference = _write_jsonl(
        tmp_path / "reference.jsonl",
        [
            _prediction(
                index,
                start + timedelta(hours=index),
                probability=0.1 + index * 0.01,
                model_run_id="weather-run-a",
                feature_values=[0.0, 0.1, 0.2, 0.3],
            )
            for index in range(4)
        ],
    )
    live = _write_jsonl(
        tmp_path / "live.jsonl",
        [
            _prediction(
                100 + index,
                start + timedelta(days=1, hours=index),
                probability=0.7 + index * 0.01,
                model_version="impact-v2",
                model_run_id="weather-run-b",
                feature_values=[1.0, 1.1, 1.2, 1.3],
            )
            for index in range(4)
        ],
    )

    report = build_drift_report(reference, live, psi_review_threshold=0.1)

    feature = report["feature_drift"]["surge_residual_m"]
    assert feature["psi"] > 0.0
    assert feature["wasserstein_distance"] == pytest.approx(1.0)
    assert feature["review_flag"] is True
    site = report["site_specific_drift"][0]
    assert site["prediction_distribution_drift"]["24h"]["wasserstein_distance"] > 0.5
    changes = report["source_and_model_change_detection"]
    assert changes["forecast_model_runs_changed"] is True
    assert changes["impact_model_versions_changed"] is True
    assert report["manual_review_required"] is True
    assert report["retraining_triggered"] is False
    assert report["automatic_retraining_allowed"] is False


def test_monitor_cli_dry_run_is_json_and_has_no_side_effect(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    logs = _write_jsonl(tmp_path / "service.jsonl", [_prediction(0, start)])
    output = tmp_path / "monitor.json"
    result = RUNNER.invoke(
        app,
        [
            "monitor",
            "aggregate",
            "--logs",
            str(logs),
            "--output",
            str(output),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not output.exists()
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "planned"
    assert payload["written"] is False
    assert payload["retraining_triggered"] is False
