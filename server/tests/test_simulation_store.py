import multiprocessing
import os
import sqlite3
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import database, simulation_artifacts
from app.simulation_artifacts import SimulationArtifactLockError
from app.simulation_schemas import (
    SimulationLabelUpsertIn,
    SimulationSessionStartIn,
    SimulationTelemetryIn,
)
from app.simulation_service import build_training_dataset
from app.simulation_store import (
    SimulationConflictError,
    SimulationNotFoundError,
    SimulationValidationError,
    add_simulation_sample,
    delete_simulation_session,
    get_active_simulation_session,
    get_device_simulation_scenario,
    get_simulation_scenario,
    get_simulation_session,
    list_labeled_training_rows,
    list_simulation_labels,
    list_simulation_samples,
    list_simulation_sessions,
    start_simulation_session,
    stop_simulation_session,
    upsert_device_simulation_scenario,
    upsert_simulation_label,
)


def _artifact_lock_worker(model_path: str, entered, release) -> None:
    os.environ["COAST_CUSTOM_MODEL_PATH"] = model_path
    from app.simulation_artifacts import SIMULATION_ARTIFACT_LOCK

    with SIMULATION_ARTIFACT_LOCK:
        entered.set()
        if not release.wait(timeout=15):
            raise RuntimeError("artifact lock test release timed out")


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "simulation-test.db"))
    monkeypatch.setenv("COAST_CUSTOM_MODEL_PATH", str(tmp_path / "custom-model.json"))
    database.init_database()


def sample(seq: int, *, device_id: str = "COAST_01", distance_mm: int = 800) -> dict:
    return {
        "device_id": device_id,
        "seq": seq,
        "uptime_ms": seq * 1000,
        "distance_mm": distance_mm,
        "water_rise_mm": 800 - distance_mm,
        "rise_rate_mm_s": 5,
        "person_detected": False,
        "alarm_level": 0,
        "health_flags": 7,
        "wifi_rssi": -55,
    }


def scenario(*, name: str = "Fictitious coast A") -> dict:
    return {
        "device_id": "COAST_01",
        "scenario_name": name,
        "simulated_at": "2026-08-14T09:00:00Z",
        "sim_air_temperature_c": 12.0,
        "sim_humidity_percent": 72.0,
        "sim_wind_speed_kmh": 18.0,
        "sim_wave_height_m": 1.2,
        "sim_wave_period_s": 6.0,
        "sim_water_temperature_c": 14.0,
        "sim_sea_level_height_m": 0.3,
        "sim_ocean_current_velocity_kmh": 1.1,
        "sim_latitude": 50.8,
        "sim_longitude": -1.1,
        "note": "operator-authored course scenario",
    }


def start(session_id: str = "sim_session_001") -> dict:
    if get_device_simulation_scenario("COAST_01") is None:
        upsert_device_simulation_scenario(scenario())
    return start_simulation_session(
        {
            "session_id": session_id,
            "device_id": "COAST_01",
            "name": "Tank wave test",
        }
    )


def test_session_start_requires_explicit_operator_scenario():
    with pytest.raises(SimulationConflictError, match="must be saved before"):
        start_simulation_session(
            {
                "session_id": "sim_missing_scenario",
                "device_id": "COAST_01",
                "name": "Must fail closed",
            }
        )


def test_legacy_labelled_session_without_snapshot_is_excluded_not_deadlocked():
    session = start("sim_legacy_without_scenario")
    for seq in range(1, 6):
        add_simulation_sample(session["session_id"], sample(seq, distance_mm=810 - seq))
    stop_simulation_session(session["session_id"], "COAST_01")
    upsert_simulation_label(
        {
            "session_id": session["session_id"],
            "device_id": "COAST_01",
            "start_seq": 1,
            "end_seq": 5,
            "label": "safe",
            "version": 1,
        }
    )
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM simulation_scenarios WHERE session_id = ?",
            (session["session_id"],),
        )

    dataset = build_training_dataset("COAST_01")
    quality = dataset["readiness"]["data_quality"]
    assert dataset["rows"] == []
    assert dataset["readiness"]["selection"]["selected_session_ids"] == [
        session["session_id"]
    ]
    assert dataset["readiness"]["selection"]["effective_session_ids"] == []
    assert quality["excluded_legacy_session_count"] == 1
    assert quality["excluded_legacy_session_ids"] == [session["session_id"]]
    assert quality["eligible_labelled_sample_count"] == 0
    assert any(
        "permanently excluded" in warning
        for warning in dataset["readiness"]["warnings"]
    )
    assert not any(
        "every labelled session requires" in blocker
        for blocker in dataset["readiness"]["blockers"]
    )
    with pytest.raises(SimulationConflictError, match="immutable operator-supplied"):
        upsert_simulation_label(
            {
                "session_id": session["session_id"],
                "device_id": "COAST_01",
                "start_seq": 1,
                "end_seq": 5,
                "label": "danger",
                "version": 2,
            }
        )


def test_session_lifecycle_is_fail_closed_and_recoverable():
    created = start()
    assert created["state"] == "active"
    assert created["ended_at"] is None
    assert created["baseline_distance_mm"] is None
    assert created["synthetic"] is True
    assert created["sample_count"] == 0
    snapshot = get_simulation_scenario(created["session_id"], "COAST_01")
    assert snapshot is not None
    assert snapshot["scenario_name"] == "Fictitious coast A"
    assert len(snapshot["scenario_hash"]) == 64
    with pytest.raises(SimulationConflictError, match="different device"):
        get_simulation_scenario(created["session_id"], "COAST_99")

    active = get_active_simulation_session("COAST_01")
    assert active is not None
    assert active["session_id"] == created["session_id"]

    with pytest.raises(SimulationConflictError, match="already has active session"):
        start("sim_session_002")

    with pytest.raises(SimulationConflictError, match="cannot change"):
        upsert_device_simulation_scenario(scenario(name="Changed during run"))

    stopped = stop_simulation_session(created["session_id"], "COAST_01")
    assert stopped["state"] == "completed"
    assert stopped["ended_at"] is not None
    assert get_active_simulation_session("COAST_01") is None
    assert get_simulation_session(created["session_id"])["state"] == "completed"
    assert (
        list_simulation_sessions("COAST_01")[0]["session_id"] == created["session_id"]
    )

    with pytest.raises(SimulationConflictError, match="already completed"):
        stop_simulation_session(created["session_id"], "COAST_01")

    replacement = start("sim_session_002")
    assert replacement["state"] == "active"


def test_unused_completed_session_delete_is_transactional_and_keeps_telemetry():
    session = start("sim_delete_unused")
    for seq in range(1, 4):
        payload = sample(seq, distance_mm=810 - seq)
        payload["simulation_session_id"] = session["session_id"]
        database.insert_telemetry(payload)
    stop_simulation_session(session["session_id"], "COAST_01")
    upsert_simulation_label(
        {
            "session_id": session["session_id"],
            "device_id": "COAST_01",
            "start_seq": 1,
            "end_seq": 3,
            "label": "safe",
            "version": 1,
        }
    )

    deleted = delete_simulation_session(session["session_id"], "COAST_01")

    assert deleted == {
        "status": "deleted",
        "session_id": session["session_id"],
        "device_id": "COAST_01",
        "deleted_counts": {
            "sessions": 1,
            "samples": 3,
            "labels": 1,
            "scenario_snapshots": 1,
        },
        "detached_telemetry_count": 3,
    }
    assert get_simulation_session(session["session_id"]) is None
    with database.connect() as connection:
        telemetry = connection.execute(
            """
            SELECT simulation_session_id FROM telemetry
            WHERE device_id = ? ORDER BY id
            """,
            ("COAST_01",),
        ).fetchall()
        assert len(telemetry) == 3
        assert all(row["simulation_session_id"] is None for row in telemetry)
        assert connection.execute(
            "SELECT COUNT(*) FROM simulation_samples WHERE session_id = ?",
            (session["session_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM simulation_labels WHERE session_id = ?",
            (session["session_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM simulation_scenarios WHERE session_id = ?",
            (session["session_id"],),
        ).fetchone()[0] == 0


def test_session_delete_hides_cross_device_and_rejects_active_session():
    session = start("sim_delete_guard")

    with pytest.raises(SimulationNotFoundError, match="not found"):
        delete_simulation_session(session["session_id"], "COAST_99")
    with pytest.raises(SimulationNotFoundError, match="not found"):
        delete_simulation_session("sim_missing_delete", "COAST_01")
    with pytest.raises(SimulationConflictError, match="active simulation session"):
        delete_simulation_session(session["session_id"], "COAST_01")
    assert get_simulation_session(session["session_id"])["state"] == "active"


def test_session_delete_fails_closed_for_unverifiable_archive_entry(
    tmp_path: Path,
):
    session = start("sim_delete_bad_artifact")
    stop_simulation_session(session["session_id"], "COAST_01")
    archive_directory = tmp_path / "runs"
    archive_directory.mkdir()
    (archive_directory / ".training-artifact.json.staged.tmp").write_text(
        "not valid JSON",
        encoding="utf-8",
    )

    with pytest.raises(
        SimulationConflictError,
        match="cannot be verified; session deletion is blocked",
    ):
        delete_simulation_session(session["session_id"], "COAST_01")
    assert get_simulation_session(session["session_id"])["state"] == "completed"


def test_artifact_maintenance_lock_serializes_separate_processes(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    model_path = str(tmp_path / "cross-process" / "custom-model.json")
    first_entered = context.Event()
    first_release = context.Event()
    second_entered = context.Event()
    second_release = context.Event()
    first = context.Process(
        target=_artifact_lock_worker,
        args=(model_path, first_entered, first_release),
    )
    second = context.Process(
        target=_artifact_lock_worker,
        args=(model_path, second_entered, second_release),
    )
    try:
        first.start()
        assert first_entered.wait(timeout=10)
        second.start()
        assert not second_entered.wait(timeout=0.5)
        first_release.set()
        assert second_entered.wait(timeout=10)
        second_release.set()
        first.join(timeout=10)
        second.join(timeout=10)
    finally:
        first_release.set()
        second_release.set()
        for process in (first, second):
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
    assert first.exitcode == 0
    assert second.exitcode == 0
    assert (Path(model_path).parent / ".simulation-artifacts.lock").is_file()


def test_artifact_maintenance_lock_times_out_for_competing_thread(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        simulation_artifacts, "_LOCK_ACQUIRE_TIMEOUT_SECONDS", 0.2
    )
    entered = threading.Event()
    release = threading.Event()
    errors: list[Exception] = []

    def hold_lock() -> None:
        try:
            with simulation_artifacts.SIMULATION_ARTIFACT_LOCK:
                entered.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("thread lock test release timed out")
        except (OSError, RuntimeError) as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(timeout=5)
    try:
        with (
            pytest.raises(SimulationArtifactLockError, match="timed out"),
            simulation_artifacts.SIMULATION_ARTIFACT_LOCK,
        ):
            pass
    finally:
        release.set()
        holder.join(timeout=5)
    assert not holder.is_alive()
    assert errors == []


def test_session_delete_rolls_back_every_table_when_mid_delete_fails():
    session = start("sim_delete_rollback")
    for seq in range(1, 3):
        payload = sample(seq, distance_mm=810 - seq)
        payload["simulation_session_id"] = session["session_id"]
        database.insert_telemetry(payload)
    stop_simulation_session(session["session_id"], "COAST_01")
    upsert_simulation_label(
        {
            "session_id": session["session_id"],
            "device_id": "COAST_01",
            "start_seq": 1,
            "end_seq": 2,
            "label": "safe",
            "version": 1,
        }
    )
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_injected_sample_delete
            BEFORE DELETE ON simulation_samples
            BEGIN
                SELECT RAISE(ABORT, 'injected delete failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected delete failure"):
        delete_simulation_session(session["session_id"], "COAST_01")

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM simulation_sessions WHERE session_id = ?",
            (session["session_id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM simulation_scenarios WHERE session_id = ?",
            (session["session_id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM simulation_samples WHERE session_id = ?",
            (session["session_id"],),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM simulation_labels WHERE session_id = ?",
            (session["session_id"],),
        ).fetchone()[0] == 1
        audit_links = connection.execute(
            """
            SELECT simulation_session_id FROM telemetry
            WHERE device_id = ? ORDER BY id
            """,
            ("COAST_01",),
        ).fetchall()
    assert [row["simulation_session_id"] for row in audit_links] == [
        session["session_id"],
        session["session_id"],
    ]


def test_sample_association_sets_first_valid_baseline_and_blocks_bad_writes():
    session = start()
    first = add_simulation_sample(session["session_id"], sample(1, distance_mm=0))
    assert first["distance_mm"] == 0
    assert get_simulation_session(session["session_id"])["baseline_distance_mm"] is None

    add_simulation_sample(session["session_id"], sample(2, distance_mm=19))
    add_simulation_sample(session["session_id"], sample(3, distance_mm=4_001))
    unhealthy = sample(4, distance_mm=800)
    unhealthy["health_flags"] = 6
    add_simulation_sample(session["session_id"], unhealthy)
    assert get_simulation_session(session["session_id"])["baseline_distance_mm"] is None

    valid = add_simulation_sample(session["session_id"], sample(5, distance_mm=812))
    assert valid["session_id"] == session["session_id"]
    assert get_simulation_session(session["session_id"])["baseline_distance_mm"] == 812
    assert [
        row["seq"] for row in list_simulation_samples(session["session_id"], "COAST_01")
    ] == [1, 2, 3, 4, 5]

    with pytest.raises(SimulationConflictError, match="different device"):
        add_simulation_sample(session["session_id"], sample(3, device_id="COAST_02"))
    with pytest.raises(SimulationConflictError, match="already exists"):
        add_simulation_sample(session["session_id"], sample(2))

    stop_simulation_session(session["session_id"], "COAST_01")
    with pytest.raises(SimulationConflictError, match="completed"):
        add_simulation_sample(session["session_id"], sample(3))


def test_telemetry_and_simulation_sample_are_persisted_atomically():
    session = start()
    payload = sample(7, distance_mm=790)
    payload["simulation_session_id"] = session["session_id"]
    record = database.insert_telemetry(payload)
    assert record["simulation_session_id"] == session["session_id"]

    samples = list_simulation_samples(session["session_id"], "COAST_01")
    assert len(samples) == 1
    assert samples[0]["telemetry_id"] == record["id"]
    assert samples[0]["received_at"] == record["received_at"]

    bad = sample(8)
    bad["simulation_session_id"] = "sim_missing_001"
    with pytest.raises(SimulationNotFoundError):
        database.insert_telemetry(bad)
    assert [row["seq"] for row in database.telemetry_history("COAST_01", 10)] == [7]


def test_label_intervals_reject_conflicts_and_keep_versions_independent():
    session = start()
    for seq in range(10, 15):
        add_simulation_sample(session["session_id"], sample(seq, distance_mm=810 - seq))

    label_request = {
        "session_id": session["session_id"],
        "device_id": "COAST_01",
        "start_seq": 10,
        "end_seq": 12,
        "label": "safe",
        "note": "calm period",
        "version": 1,
    }
    with pytest.raises(SimulationConflictError, match="only be edited after"):
        upsert_simulation_label(label_request)
    with pytest.raises(SimulationConflictError, match="unavailable until"):
        list_labeled_training_rows(session["session_id"], "COAST_01")
    stop_simulation_session(session["session_id"], "COAST_01")

    safe = upsert_simulation_label(label_request)
    assert safe["label"] == "safe"

    # Same-label overlap is unambiguous and therefore allowed.
    upsert_simulation_label(
        {
            "session_id": session["session_id"],
            "device_id": "COAST_01",
            "start_seq": 12,
            "end_seq": 13,
            "label": "safe",
            "version": 1,
        }
    )
    with pytest.raises(SimulationConflictError, match="conflicts"):
        upsert_simulation_label(
            {
                "session_id": session["session_id"],
                "device_id": "COAST_01",
                "start_seq": 11,
                "end_seq": 14,
                "label": "danger",
                "version": 1,
            }
        )

    # A separate annotation version may intentionally express a new opinion.
    v2 = upsert_simulation_label(
        {
            "session_id": session["session_id"],
            "device_id": "COAST_01",
            "start_seq": 11,
            "end_seq": 14,
            "label": "danger",
            "version": 2,
        }
    )
    assert v2["version"] == 2

    with pytest.raises(SimulationConflictError, match="different device"):
        upsert_simulation_label(
            {
                "session_id": session["session_id"],
                "device_id": "COAST_99",
                "start_seq": 10,
                "end_seq": 10,
                "label": "safe",
            }
        )
    with pytest.raises(SimulationValidationError, match="at least one"):
        upsert_simulation_label(
            {
                "session_id": session["session_id"],
                "device_id": "COAST_01",
                "start_seq": 100,
                "end_seq": 101,
                "label": "safe",
            }
        )
    assert len(list_simulation_labels(session["session_id"], "COAST_01")) == 2


def test_training_rows_default_gaps_to_unknown_and_can_filter_them():
    session = start()
    for seq, distance in ((1, 800), (2, 790), (3, 760), (4, 730)):
        add_simulation_sample(session["session_id"], sample(seq, distance_mm=distance))
    stop_simulation_session(session["session_id"], "COAST_01")
    upsert_simulation_label(
        {
            "session_id": session["session_id"],
            "device_id": "COAST_01",
            "start_seq": 1,
            "end_seq": 2,
            "label": "safe",
            "note": "before wave",
        }
    )
    upsert_simulation_label(
        {
            "session_id": session["session_id"],
            "device_id": "COAST_01",
            "start_seq": 4,
            "end_seq": 4,
            "label": "danger",
            "note": "peak",
        }
    )

    rows = list_labeled_training_rows(session["session_id"], "COAST_01")
    assert [row["label"] for row in rows] == ["safe", "safe", "unknown", "danger"]
    assert all(row["baseline_distance_mm"] == 800 for row in rows)
    assert rows[3]["label_note"] == "peak"
    assert rows[0]["label_version"] == 1

    labeled = list_labeled_training_rows(
        session["session_id"], "COAST_01", include_unknown=False
    )
    assert [row["seq"] for row in labeled] == [1, 2, 4]


def test_schemas_are_strict_and_require_sim_prefix():
    with pytest.raises(ValidationError):
        SimulationSessionStartIn.model_validate(
            {"session_id": "plain", "device_id": "COAST_01", "name": "test"}
        )
    with pytest.raises(ValidationError):
        SimulationTelemetryIn.model_validate(
            {
                **sample(1),
                "simulation_session_id": "sim_valid_001",
                "unexpected": "rejected",
            }
        )
    with pytest.raises(ValidationError):
        SimulationLabelUpsertIn.model_validate(
            {
                "session_id": "sim_valid_001",
                "device_id": "COAST_01",
                "start_seq": 1,
                "end_seq": 2,
                "label": "warning",
            }
        )


def test_existing_telemetry_database_gets_additive_simulation_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "legacy-telemetry.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                uptime_ms INTEGER NOT NULL,
                distance_mm INTEGER NOT NULL,
                water_rise_mm INTEGER NOT NULL,
                rise_rate_mm_s INTEGER NOT NULL,
                person_detected INTEGER NOT NULL,
                alarm_level INTEGER NOT NULL,
                health_flags INTEGER NOT NULL,
                wifi_rssi INTEGER NOT NULL,
                received_at TEXT NOT NULL
            )
            """
        )
    monkeypatch.setenv("COASTAL_DB_PATH", str(path))
    database.init_database()
    with sqlite3.connect(path) as connection:
        telemetry_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(telemetry)")
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "simulation_session_id" in telemetry_columns
    assert {
        "simulation_sessions",
        "simulation_samples",
        "simulation_labels",
        "simulation_scenarios",
        "simulation_device_scenarios",
    } <= tables
