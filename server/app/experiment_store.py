"""Persistence for official-data training and sensor-only external tests.

This module deliberately stores immutable JSON snapshots next to indexed
columns.  A later rescan, model activation, or profile edit therefore cannot
rewrite the provenance of an already completed experiment.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from .database import _utc_now_text, connect


class OfficialStoreError(RuntimeError):
    pass


class OfficialNotFoundError(OfficialStoreError):
    pass


class OfficialConflictError(OfficialStoreError):
    pass


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _decode_json_columns(row: sqlite3.Row, *names: str) -> dict[str, Any]:
    result = dict(row)
    for name in names:
        value = result.get(name)
        if value is not None:
            result[name.removesuffix("_json")] = json.loads(str(value))
        result.pop(name, None)
    for name in ("activatable",):
        if name in result:
            result[name] = bool(result[name])
    return result


def upsert_official_dataset(record: Mapping[str, Any]) -> dict[str, Any]:
    """Register one immutable version under a globally unique dataset ID.

    The API intentionally keeps ``dataset_id`` as its stable selector. Bundle
    authors must therefore version-qualify that ID (for example,
    ``uk-coasts-2024-v1``). Reusing an ID for another version or changing any
    immutable registration hash is rejected instead of rewriting provenance.
    """

    now = _utc_now_text()
    values = {
        "dataset_id": str(record["dataset_id"]),
        "version": str(record["version"]),
        "display_name": str(record.get("display_name") or record["dataset_id"]),
        "data_origin": str(record["data_origin"]),
        "activatable": 1 if record.get("activatable", True) else 0,
        "manifest_path": str(record["manifest_path"]),
        "registration_path": str(record["registration_path"]),
        "registration_sha256": str(record["registration_sha256"]),
        "manifest_sha256": str(record["manifest_sha256"]),
        "dataset_sha256": str(record["dataset_sha256"]),
        "row_count": int(record["row_count"]),
        "site_ids_json": _json(record["site_ids"]),
        "date_start": str(record["date_start"]),
        "date_end": str(record["date_end"]),
        "splits_json": _json(record["splits"]),
        "label_definition_json": _json(record["label_definition"]),
        "feature_order_json": _json(record["feature_order"]),
        "source_manifest_json": _json(record["source_manifest"]),
        "registered_at": str(record.get("registered_at") or now),
        "last_scanned_at": now,
    }
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM official_dataset_versions WHERE dataset_id = ?",
            (values["dataset_id"],),
        ).fetchone()
        if existing is not None:
            existing_version = str(existing["version"])
            if existing_version != values["version"]:
                raise OfficialConflictError(
                    f"official dataset_id {values['dataset_id']} is already bound "
                    f"to version {existing_version}; dataset_id must be globally "
                    "version-qualified"
                )
            changed_hashes = [
                name
                for name in ("manifest_sha256", "dataset_sha256")
                if str(existing[name]) != values[name]
            ]
            existing_registration_hash = existing["registration_sha256"]
            if (
                existing_registration_hash is not None
                and str(existing_registration_hash)
                != values["registration_sha256"]
            ):
                changed_hashes.append("registration_sha256")
            if changed_hashes:
                raise OfficialConflictError(
                    f"official dataset {values['dataset_id']} version "
                    f"{values['version']} is immutable; changed hashes: "
                    f"{', '.join(changed_hashes)}"
                )
        connection.execute(
            """
            INSERT INTO official_dataset_versions (
                dataset_id, version, display_name, data_origin, activatable,
                manifest_path, registration_path, registration_sha256,
                manifest_sha256, dataset_sha256, row_count, site_ids_json,
                date_start, date_end, splits_json, label_definition_json,
                feature_order_json, source_manifest_json, registered_at,
                last_scanned_at
            ) VALUES (
                :dataset_id, :version, :display_name, :data_origin, :activatable,
                :manifest_path, :registration_path, :registration_sha256,
                :manifest_sha256, :dataset_sha256, :row_count, :site_ids_json,
                :date_start, :date_end, :splits_json, :label_definition_json,
                :feature_order_json, :source_manifest_json, :registered_at,
                :last_scanned_at
            )
            ON CONFLICT(dataset_id) DO UPDATE SET
                display_name = excluded.display_name,
                data_origin = excluded.data_origin,
                activatable = excluded.activatable,
                manifest_path = excluded.manifest_path,
                registration_path = excluded.registration_path,
                registration_sha256 = excluded.registration_sha256,
                manifest_sha256 = excluded.manifest_sha256,
                dataset_sha256 = excluded.dataset_sha256,
                row_count = excluded.row_count,
                site_ids_json = excluded.site_ids_json,
                date_start = excluded.date_start,
                date_end = excluded.date_end,
                splits_json = excluded.splits_json,
                label_definition_json = excluded.label_definition_json,
                feature_order_json = excluded.feature_order_json,
                source_manifest_json = excluded.source_manifest_json,
                last_scanned_at = excluded.last_scanned_at
            """,
            values,
        )
    stored = get_official_dataset(str(values["dataset_id"]))
    if stored is None:  # pragma: no cover - database invariant
        raise RuntimeError("official dataset upsert did not return a row")
    return stored


_DATASET_JSON_COLUMNS = (
    "site_ids_json",
    "splits_json",
    "label_definition_json",
    "feature_order_json",
    "source_manifest_json",
)


def get_official_dataset(dataset_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM official_dataset_versions WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
    return _decode_json_columns(row, *_DATASET_JSON_COLUMNS) if row else None


def list_official_datasets() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM official_dataset_versions
            ORDER BY last_scanned_at DESC, dataset_id
            """
        ).fetchall()
    return [_decode_json_columns(row, *_DATASET_JSON_COLUMNS) for row in rows]


def create_official_training_run(
    dataset_id: str,
    selected_site_ids: Sequence[str],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    dataset = get_official_dataset(dataset_id)
    if dataset is None:
        raise OfficialNotFoundError(f"official dataset {dataset_id} not found")
    request_snapshot = dict(request)
    request_snapshot["dataset_id"] = dataset_id
    request_snapshot["dataset_version"] = str(dataset["version"])
    run_id = f"ukrun_{uuid4().hex}"
    now = _utc_now_text()
    try:
        with connect() as connection:
            connection.execute(
                """
                INSERT INTO official_training_runs (
                    run_id, dataset_id, status, selected_site_ids_json,
                    request_json, started_at
                ) VALUES (?, ?, 'running', ?, ?, ?)
                """,
                (
                    run_id,
                    dataset_id,
                    _json(list(selected_site_ids)),
                    _json(request_snapshot),
                    now,
                ),
            )
    except sqlite3.IntegrityError as exc:
        if "idx_official_one_running_train" in str(exc) or "UNIQUE" in str(exc):
            raise OfficialConflictError(
                "another official model training run is already in progress"
            ) from exc
        raise
    result = get_official_training_run(run_id)
    if result is None:  # pragma: no cover
        raise RuntimeError("official training run insert did not return a row")
    return result


def recover_interrupted_training_runs(
    *, max_age_seconds: float = 1_800.0
) -> list[str]:
    """Fail abandoned rows without disturbing a plausible live worker.

    A process crash releases the cross-process artifact lock but cannot update
    SQLite.  Training requests call this before creating a run, so an old row
    cannot block the unique single-flight index forever.  The conservative
    grace period is much longer than the expected small teaching datasets.
    """

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
    now = _utc_now_text()
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT run_id FROM official_training_runs
            WHERE status = 'running' AND started_at < ?
            """,
            (cutoff_text,),
        ).fetchall()
        run_ids = [str(row["run_id"]) for row in rows]
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            connection.execute(
                f"""
                UPDATE official_training_runs
                SET status = 'failed', finished_at = ?,
                    error_message = 'training process was interrupted'
                WHERE status = 'running' AND run_id IN ({placeholders})
                """,
                (now, *run_ids),
            )
    return run_ids


_RUN_JSON_COLUMNS = (
    "selected_site_ids_json",
    "request_json",
    "metrics_json",
    "source_manifest_json",
    "data_contract_json",
)


def complete_official_training_run(
    run_id: str,
    *,
    artifact_path: str,
    artifact_sha256: str,
    artifact_version: str,
    metrics: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
    data_contract: Mapping[str, Any],
) -> dict[str, Any]:
    now = _utc_now_text()
    with connect() as connection:
        updated = connection.execute(
            """
            UPDATE official_training_runs
            SET status = 'succeeded', finished_at = ?, artifact_path = ?,
                artifact_sha256 = ?, artifact_version = ?, metrics_json = ?,
                source_manifest_json = ?, data_contract_json = ?
            WHERE run_id = ? AND status = 'running'
            """,
            (
                now,
                artifact_path,
                artifact_sha256,
                artifact_version,
                _json(metrics),
                _json(source_manifest),
                _json(data_contract),
                run_id,
            ),
        ).rowcount
    if updated != 1:
        raise OfficialConflictError(f"official training run {run_id} is not running")
    result = get_official_training_run(run_id)
    assert result is not None
    return result


def fail_official_training_run(run_id: str, error_message: str) -> dict[str, Any]:
    now = _utc_now_text()
    message = error_message.strip()[:2000] or "official training failed"
    with connect() as connection:
        updated = connection.execute(
            """
            UPDATE official_training_runs
            SET status = 'failed', finished_at = ?, error_message = ?
            WHERE run_id = ? AND status = 'running'
            """,
            (now, message, run_id),
        ).rowcount
    if updated != 1:
        raise OfficialConflictError(f"official training run {run_id} is not running")
    result = get_official_training_run(run_id)
    assert result is not None
    return result


def get_official_training_run(run_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM official_training_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return _decode_json_columns(row, *_RUN_JSON_COLUMNS) if row else None


def list_official_training_runs(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM official_training_runs
            ORDER BY started_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_decode_json_columns(row, *_RUN_JSON_COLUMNS) for row in rows]


def activate_official_training_run(run_id: str) -> dict[str, Any]:
    now = _utc_now_text()
    with connect() as connection:
        active_sensor_session = connection.execute(
            """
            SELECT snapshots.session_id
            FROM sensor_test_session_snapshots AS snapshots
            JOIN simulation_sessions AS sessions
              ON sessions.session_id = snapshots.session_id
             AND sessions.device_id = snapshots.device_id
            WHERE sessions.state = 'active'
            ORDER BY snapshots.frozen_at LIMIT 1
            """
        ).fetchone()
        if active_sensor_session is not None:
            raise OfficialConflictError(
                "official model activation is frozen while sensor external-test "
                f"session {active_sensor_session['session_id']} is active"
            )
        run = connection.execute(
            "SELECT * FROM official_training_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise OfficialNotFoundError(f"official training run {run_id} not found")
        if str(run["status"]) != "succeeded":
            raise OfficialConflictError("only a successful official run can activate")
        connection.execute(
            "UPDATE official_training_runs SET activated_at = NULL WHERE activated_at IS NOT NULL"
        )
        connection.execute(
            "UPDATE official_training_runs SET activated_at = ? WHERE run_id = ?",
            (now, run_id),
        )
        connection.execute(
            """
            INSERT INTO official_model_activation (
                singleton_id, run_id, artifact_sha256, activated_at
            ) VALUES (1, ?, ?, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                run_id = excluded.run_id,
                artifact_sha256 = excluded.artifact_sha256,
                activated_at = excluded.activated_at
            """,
            (run_id, str(run["artifact_sha256"]), now),
        )
    result = get_active_official_model()
    assert result is not None
    return result


def get_active_official_model() -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT runs.*, activation.activated_at AS active_since
            FROM official_model_activation AS activation
            JOIN official_training_runs AS runs ON runs.run_id = activation.run_id
            WHERE activation.singleton_id = 1
            """
        ).fetchone()
    return _decode_json_columns(row, *_RUN_JSON_COLUMNS) if row else None


_PROFILE_JSON_COLUMNS = ("profile_json",)


def upsert_sensor_test_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    device_id = str(profile["device_id"])
    now = _utc_now_text()
    with connect() as connection:
        active = connection.execute(
            """
            SELECT session_id FROM simulation_sessions
            WHERE device_id = ? AND state = 'active'
            """,
            (device_id,),
        ).fetchone()
        if active is not None:
            raise OfficialConflictError(
                f"sensor profile is frozen while session {active['session_id']} is active"
            )
        connection.execute(
            """
            INSERT INTO sensor_test_profiles (
                device_id, profile_id, profile_sha256, official_run_id,
                artifact_sha256, station_id, context_timestamp, datum, mode,
                calibration_session_id, profile_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                profile_id = excluded.profile_id,
                profile_sha256 = excluded.profile_sha256,
                official_run_id = excluded.official_run_id,
                artifact_sha256 = excluded.artifact_sha256,
                station_id = excluded.station_id,
                context_timestamp = excluded.context_timestamp,
                datum = excluded.datum,
                mode = excluded.mode,
                calibration_session_id = excluded.calibration_session_id,
                profile_json = excluded.profile_json,
                updated_at = excluded.updated_at
            """,
            (
                device_id,
                str(profile["profile_id"]),
                str(profile["profile_sha256"]),
                str(profile["official_run_id"]),
                str(profile["artifact_sha256"]),
                str(profile["station_id"]),
                str(profile["context_timestamp"]),
                str(profile["datum"]),
                str(profile["mode"]),
                profile.get("calibration_session_id"),
                _json(profile["profile"]),
                now,
                now,
            ),
        )
    result = get_sensor_test_profile(device_id)
    assert result is not None
    return result


def get_sensor_test_profile(device_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM sensor_test_profiles WHERE device_id = ?", (device_id,)
        ).fetchone()
    return _decode_json_columns(row, *_PROFILE_JSON_COLUMNS) if row else None


def delete_sensor_test_profile(device_id: str) -> None:
    with connect() as connection:
        active = connection.execute(
            """
            SELECT session_id FROM simulation_sessions
            WHERE device_id = ? AND state = 'active'
            """,
            (device_id,),
        ).fetchone()
        if active is not None:
            raise OfficialConflictError(
                f"sensor profile is frozen while session {active['session_id']} is active"
            )
        deleted = connection.execute(
            "DELETE FROM sensor_test_profiles WHERE device_id = ?", (device_id,)
        ).rowcount
    if deleted != 1:
        raise OfficialNotFoundError(f"device {device_id} has no sensor test profile")


def freeze_sensor_profile_for_session(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    device_id: str,
    frozen_at: str,
) -> bool:
    """Freeze the active profile inside the caller's session transaction."""

    # The caller already resolved the ready model through model_registry.
    # Re-reading a raw selection row here would revive retired/stale IDs and
    # split the purpose decision across two different transactions.
    activation = connection.execute(
        "SELECT * FROM official_model_activation WHERE singleton_id = 1"
    ).fetchone()
    if activation is None:
        raise OfficialConflictError("official model is not activated")
    profile = connection.execute(
        "SELECT * FROM sensor_test_profiles WHERE device_id = ?", (device_id,)
    ).fetchone()
    if profile is None:
        raise OfficialConflictError("official model requires a sensor test profile")
    if str(profile["artifact_sha256"]) != str(activation["artifact_sha256"]):
        raise OfficialConflictError(
            "sensor test profile does not match the active official artifact"
        )
    connection.execute(
        """
        INSERT INTO sensor_test_session_snapshots (
            session_id, device_id, profile_id, profile_sha256, official_run_id,
            artifact_sha256, calibration_session_id, profile_json, frozen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            device_id,
            str(profile["profile_id"]),
            str(profile["profile_sha256"]),
            str(profile["official_run_id"]),
            str(profile["artifact_sha256"]),
            profile["calibration_session_id"],
            str(profile["profile_json"]),
            frozen_at,
        ),
    )
    return True


def get_sensor_session_snapshot(session_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM sensor_test_session_snapshots WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return _decode_json_columns(row, "profile_json") if row else None


def create_sensor_test_run(
    *,
    session_id: str,
    device_id: str,
    profile_sha256: str,
    artifact_sha256_before: str,
    artifact_sha256_after: str,
    sample_count: int,
    result: Mapping[str, Any] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    run_id = f"sensortest_{uuid4().hex}"
    now = _utc_now_text()
    status = "succeeded" if result is not None else "failed"
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO sensor_test_runs (
                run_id, session_id, device_id, profile_sha256,
                artifact_sha256_before, artifact_sha256_after, status,
                sample_count, result_json, error_message, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                session_id,
                device_id,
                profile_sha256,
                artifact_sha256_before,
                artifact_sha256_after,
                status,
                sample_count,
                _json(result) if result is not None else None,
                (error_message or "sensor external test failed")[:2000]
                if result is None
                else None,
                now,
                now,
            ),
        )
    stored = get_sensor_test_run(run_id)
    assert stored is not None
    return stored


def get_sensor_test_run(run_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM sensor_test_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return _decode_json_columns(row, "result_json") if row else None


def list_sensor_test_runs(
    *, device_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    columns = """
        run_id, session_id, device_id, profile_sha256,
        artifact_sha256_before, artifact_sha256_after, status, sample_count,
        error_message, created_at, completed_at
    """
    with connect() as connection:
        if device_id is None:
            rows = connection.execute(
                f"""
                SELECT {columns} FROM sensor_test_runs
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                f"""
                SELECT {columns} FROM sensor_test_runs
                WHERE device_id = ? ORDER BY created_at DESC LIMIT ?
                """,
                (device_id, limit),
            ).fetchall()
    return [dict(row) for row in rows]
