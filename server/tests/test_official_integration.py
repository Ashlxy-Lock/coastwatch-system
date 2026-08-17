import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import main, risk_dispatch, simulation_store
from app.database import connect, init_database
from app.experiment_store import (
    OfficialConflictError,
    activate_official_training_run,
    complete_official_training_run,
    create_official_training_run,
    create_sensor_test_run,
    delete_sensor_test_profile,
    get_sensor_session_snapshot,
    list_official_training_runs,
    list_sensor_test_runs,
    upsert_official_dataset,
    upsert_sensor_test_profile,
)
from app.model_registry import OFFICIAL_MODEL_ID, init_model_registry
from app.simulation_schemas import SimulationSessionStartIn
from app.simulation_store import (
    SimulationConflictError,
    delete_simulation_session,
    start_simulation_session,
    stop_simulation_session,
)

DEVICE_ID = "COAST_01"
HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture
def official_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "official-store.db"))
    monkeypatch.setenv(
        "COAST_CUSTOM_MODEL_PATH", str(tmp_path / "unused-legacy-model.json")
    )
    init_database()
    init_model_registry()
    dataset = upsert_official_dataset(
        {
            "dataset_id": "synthetic-integration-fixture",
            "version": "1",
            "display_name": "Synthetic integration fixture",
            "data_origin": "synthetic_test_fixture",
            "activatable": False,
            "manifest_path": str(tmp_path / "fixture" / "manifest.json"),
            "registration_path": str(tmp_path / "fixture.registration.json"),
            "registration_sha256": "e" * 64,
            "manifest_sha256": HASH_A,
            "dataset_sha256": HASH_B,
            "row_count": 24,
            "site_ids": ["TEST_SITE"],
            "date_start": "2026-01-01T00:00:00Z",
            "date_end": "2026-01-04T00:00:00Z",
            "splits": {
                "train": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T23:00:00Z"},
                "validation": {"start": "2026-01-02T00:00:00Z", "end": "2026-01-02T23:00:00Z"},
                "frozen_test": {"start": "2026-01-03T00:00:00Z", "end": "2026-01-03T23:00:00Z"},
                "leakage_gap_hours": 1,
            },
            "label_definition": {"column": "target_extreme_water"},
            "feature_order": ["relative_water_level_m"],
            "source_manifest": {"fixture_only": True},
        }
    )
    return dataset


def _successful_run(dataset_id: str, artifact_hash: str) -> dict:
    run = create_official_training_run(
        dataset_id, ["TEST_SITE"], {"automatic_training": False}
    )
    return complete_official_training_run(
        str(run["run_id"]),
        artifact_path=f"C:/fixture/{run['run_id']}.json",
        artifact_sha256=artifact_hash,
        artifact_version="test",
        metrics={"frozen_test": {"pr_auc": 0.5}},
        source_manifest={"dataset_id": dataset_id},
        data_contract={
            "sensor_rows_used_for_fit": 0,
            "sensor_rows_used_for_scaler": 0,
            "sensor_rows_used_for_threshold": 0,
        },
    )


def _profile(
    run: dict,
    *,
    suffix: str = "one",
    calibration_session_id: str = "sim_calibration_001",
) -> dict:
    with connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO simulation_sessions (
                session_id, device_id, name, state, started_at, ended_at, synthetic
            ) VALUES (?, ?, ?, 'completed', ?, ?, 1)
            """,
            (
                calibration_session_id,
                DEVICE_ID,
                "Integration calibration fixture",
                "2026-01-02T00:00:00Z",
                "2026-01-02T00:05:00Z",
            ),
        )
    profile_hash = ("c" if suffix == "one" else "d") * 64
    return upsert_sensor_test_profile(
        {
            "device_id": DEVICE_ID,
            "profile_id": f"sensorprof_{suffix}",
            "profile_sha256": profile_hash,
            "official_run_id": run["run_id"],
            "artifact_sha256": run["artifact_sha256"],
            "station_id": "TEST_SITE",
            "context_timestamp": "2026-01-03T00:00:00Z",
            "datum": "TEST DATUM",
            "mode": "formal",
            "calibration_session_id": calibration_session_id,
            "profile": {
                "schema": "fixture-only-profile",
                "profile_id": f"sensorprof_{suffix}",
            },
        }
    )


def test_training_store_is_single_flight(official_store: dict) -> None:
    first = create_official_training_run(
        official_store["dataset_id"], ["TEST_SITE"], {"automatic_training": False}
    )
    assert first["status"] == "running"
    with pytest.raises(
        OfficialConflictError,
        match="another official model training run is already in progress",
    ):
        create_official_training_run(
            official_store["dataset_id"],
            ["TEST_SITE"],
            {"automatic_training": False},
        )
    assert first["request"]["dataset_version"] == official_store["version"]


def test_training_creates_running_row_only_after_cross_process_lock(
    official_store: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_state = {"held": False}

    class ObservedLock:
        def __enter__(self):
            assert list_official_training_runs() == []
            lock_state["held"] = True
            return self

        def __exit__(self, *_args):
            lock_state["held"] = False

    readiness = {
        "ready": True,
        "selected_site_ids": ["TEST_SITE"],
        "blockers": [],
    }
    monkeypatch.setattr(main, "SIMULATION_ARTIFACT_LOCK", ObservedLock())
    monkeypatch.setattr(main, "_load_dataset_record", lambda _dataset_id: object())
    monkeypatch.setattr(
        main,
        "assess_official_training_data",
        lambda _dataset, selected_site_ids=None: readiness,
    )
    monkeypatch.setattr(main, "_official_artifact_directory", lambda: tmp_path)

    def fake_train(*_args, **_kwargs):
        assert lock_state["held"] is True
        running = list_official_training_runs()
        assert len(running) == 1
        assert running[0]["status"] == "running"
        return {
            "artifact_sha256": HASH_A,
            "version": "test",
            "metrics": {"frozen_test": {"pr_auc": 0.5}},
            "source_manifest": {"dataset_id": official_store["dataset_id"]},
            "data_contract": {
                "sensor_rows_used_for_fit": 0,
                "sensor_rows_used_for_scaler": 0,
                "sensor_rows_used_for_threshold": 0,
            },
        }

    monkeypatch.setattr(main, "train_official_model", fake_train)
    monkeypatch.setattr(main, "load_official_model", lambda *_args, **_kwargs: object())

    result = main.create_official_model_training_run(
        main.OfficialTrainingRunIn(dataset_id=official_store["dataset_id"])
    )

    assert result["status"] == "succeeded"
    assert lock_state["held"] is False


def test_external_test_holds_lock_from_session_read_through_result_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_state = {"held": False}

    class ObservedLock:
        def __enter__(self):
            lock_state["held"] = True
            return self

        def __exit__(self, *_args):
            lock_state["held"] = False

    def assert_locked(value):
        assert lock_state["held"] is True
        return value

    monkeypatch.setattr(main, "SIMULATION_ARTIFACT_LOCK", ObservedLock())
    monkeypatch.setattr(
        main,
        "get_simulation_session",
        lambda *_args: assert_locked(
            {
                "state": "completed",
                "sample_count": 6_002,
                "valid_ultrasonic_samples": 6_000,
                "invalid_ultrasonic_samples": 2,
            }
        ),
    )
    monkeypatch.setattr(
        main,
        "get_sensor_session_snapshot",
        lambda *_args: assert_locked(
            {
                "device_id": DEVICE_ID,
                "official_run_id": "ukrun_test",
                "artifact_sha256": HASH_A,
                "profile_sha256": "c" * 64,
                "profile": {"profile_id": "sensorprof_test"},
                "calibration_session_id": "sim_calibration_independent",
            }
        ),
    )
    monkeypatch.setattr(
        main,
        "get_official_training_run",
        lambda *_args: assert_locked(
            {"status": "succeeded", "artifact_path": "unused.json"}
        ),
    )
    monkeypatch.setattr(
        main,
        "list_valid_simulation_samples",
        lambda *_args, **kwargs: assert_locked(
            [{"seq": seq, "water_rise_mm": 12.0} for seq in range(kwargs["limit"])]
        ),
    )
    monkeypatch.setattr(
        main,
        "load_official_model",
        lambda *_args, **_kwargs: assert_locked(
            SimpleNamespace(artifact_sha256=HASH_A)
        ),
    )
    monkeypatch.setattr(
        main,
        "load_sensor_proxy_profile",
        lambda *_args, **_kwargs: assert_locked(object()),
    )
    monkeypatch.setattr(
        main,
        "run_sensor_proxy_external_test",
        lambda _model, _profile, samples, **_kwargs: assert_locked(
            {
                "rows": [
                    {
                        "index": index,
                        "proxy_relative_water_level_m": float(index),
                        "extreme_water_probability": index / len(samples),
                        "out_of_distribution": False,
                    }
                    for index, _sample in enumerate(samples)
                ],
                "sample_count": len(samples),
                "out_of_distribution_count": 0,
                "mean_extreme_water_probability": 0.5,
                "max_extreme_water_probability": 1.0,
            }
        ),
    )

    def fake_store(**kwargs):
        assert lock_state["held"] is True
        return {
            "run_id": "sensorrun_test",
            "status": "succeeded",
            "result": kwargs["result"],
        }

    monkeypatch.setattr(main, "create_sensor_test_run", fake_store)
    monkeypatch.setattr(main, "_sensor_test_run_api_view", lambda record: record)

    result = main.create_sensor_external_test_run(
        main.SensorExternalTestRunIn(
            session_id="sim_external_lock_test", device_id=DEVICE_ID
        )
    )

    assert result["run_id"] == "sensorrun_test"
    assert result["result"]["input_sample_count"] == 6_002
    assert result["result"]["valid_input_sample_count"] == 6_000
    assert result["result"]["excluded_invalid_ultrasonic_samples"] == 2
    assert result["result"]["evaluated_sample_count"] == 5_000
    assert result["result"]["truncated_valid_sample_count"] == 1_000
    assert result["result"]["evaluation_truncated"] is True
    assert result["result"]["result_row_count"] == 5_000
    assert result["result"]["preview_row_count"] == 200
    assert len(result["result"]["rows"]) == 200
    assert result["result"]["rows"][0]["index"] == 0
    assert result["result"]["rows"][-1]["index"] == 4_999
    assert result["result"]["mapped_min_m"] == pytest.approx(0.0)
    assert result["result"]["mapped_max_m"] == pytest.approx(4_999.0)
    assert len(result["result"]["evaluated_samples_sha256"]) == 64
    assert lock_state["held"] is False


def test_official_dataset_identity_is_immutable_and_version_qualified(
    official_store: dict,
) -> None:
    api_view = main._dataset_api_view(official_store)
    assert api_view["provenance_assurance"] == "operator_attested_raw_hash_verified"
    assert api_view["deterministic_importer_replay_verified"] is False

    changed_version = dict(official_store)
    changed_version["version"] = "2"
    with pytest.raises(
        OfficialConflictError, match="dataset_id must be globally version-qualified"
    ):
        upsert_official_dataset(changed_version)

    changed_registration = dict(official_store)
    changed_registration["registration_sha256"] = "f" * 64
    with pytest.raises(
        OfficialConflictError,
        match="immutable; changed hashes: registration_sha256",
    ):
        upsert_official_dataset(changed_registration)

    stored = upsert_official_dataset(official_store)
    assert stored["version"] == "1"
    assert stored["registration_sha256"] == "e" * 64


def test_rescan_reports_version_identity_conflict_without_overwriting(
    official_store: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = dict(official_store["source_manifest"])
    manifest["date_range"] = {
        "start": official_store["date_start"],
        "end": official_store["date_end"],
    }
    manifest["table"] = {"row_count": official_store["row_count"]}
    manifest["site_ids"] = official_store["site_ids"]
    manifest["splits"] = official_store["splits"]
    manifest["label_definition"] = official_store["label_definition"]
    conflicting = SimpleNamespace(
        dataset_id=official_store["dataset_id"],
        version="2",
        data_origin=official_store["data_origin"],
        activatable=official_store["activatable"],
        manifest=manifest,
        manifest_path=tmp_path / "v2" / "manifest.json",
        registration_path=tmp_path / "v2.registration.json",
        registration_sha256="f" * 64,
        manifest_sha256=HASH_A,
        table_sha256=HASH_B,
    )
    dataset_root = tmp_path / "official-datasets"
    registry = tmp_path / "registry"
    monkeypatch.setattr(main, "_official_dataset_root", lambda: dataset_root)
    monkeypatch.setattr(main, "_official_registry_directory", lambda: registry)
    monkeypatch.setattr(
        main,
        "rescan_official_dataset_root",
        lambda _root, _registry: [conflicting],
    )

    result = main.rescan_official_datasets()

    assert result["registered_count"] == 0
    assert result["error_count"] == 1
    assert result["errors"][0]["bundle"].endswith("/2")
    assert "globally version-qualified" in result["errors"][0]["detail"]
    assert upsert_official_dataset(official_store)["version"] == "1"


def test_official_environment_uses_only_frozen_profile_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = {
        "site_id": "NEW_HAVEN",
        "official_context": {
            "timestamp": "2026-01-03T00:00:00+00:00",
            "features": {
                "wind_speed_m_s": 10.0,
                "significant_wave_height_m": 1.8,
                "wave_period_s": 7.0,
                "air_temperature_c": 11.5,
                "relative_humidity_percent": 82.0,
                "water_temperature_c": 9.25,
                "ocean_current_velocity_m_s": 0.6,
            },
        },
        "mapping": {"reference_level_m": 0.75},
    }
    monkeypatch.setattr(
        risk_dispatch,
        "get_sensor_test_profile",
        lambda _device_id: {
            "profile": profile,
            "artifact_sha256": HASH_A,
        },
    )
    monkeypatch.setattr(
        risk_dispatch,
        "get_active_official_model",
        lambda: {"artifact_path": "unused.json", "artifact_sha256": HASH_A},
    )
    monkeypatch.setattr(
        risk_dispatch,
        "load_official_model",
        lambda *_args, **_kwargs: SimpleNamespace(artifact_sha256=HASH_A),
    )
    monkeypatch.setattr(
        risk_dispatch, "load_sensor_proxy_profile", lambda *_args, **_kwargs: object()
    )

    environment = risk_dispatch.build_official_sensor_environment(DEVICE_ID)

    assert environment.location == "NEW_HAVEN"
    assert environment.source == "manual"
    assert environment.provider == "UK OFFICIAL FROZEN CONTEXT"
    assert environment.air_temperature_c == pytest.approx(11.5)
    assert environment.humidity_percent == pytest.approx(82.0)
    assert environment.wind_speed_kmh == pytest.approx(36.0)
    assert environment.water_temperature_c == pytest.approx(9.25)
    assert environment.wave_height_m == pytest.approx(1.8)
    assert environment.sea_level_height_m is None
    assert environment.ocean_current_velocity_kmh == pytest.approx(2.16)


def test_profile_is_frozen_atomically_and_lifecycle_writes_are_blocked(
    official_store: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_run = _successful_run(official_store["dataset_id"], HASH_A)
    activate_official_training_run(first_run["run_id"])
    profile = _profile(first_run)
    monkeypatch.setattr(
        simulation_store, "get_selected_model_id", lambda _device_id: OFFICIAL_MODEL_ID
    )

    session = start_simulation_session(
        SimulationSessionStartIn(
            device_id=DEVICE_ID,
            name="External test collection",
            session_id="sim_external_001",
        )
    )
    snapshot = get_sensor_session_snapshot(session["session_id"])
    assert snapshot is not None
    assert snapshot["profile_sha256"] == profile["profile_sha256"]
    assert snapshot["artifact_sha256"] == HASH_A
    assert snapshot["calibration_session_id"] == "sim_calibration_001"

    second_run = _successful_run(official_store["dataset_id"], HASH_B)
    with pytest.raises(OfficialConflictError, match="activation is frozen"):
        activate_official_training_run(second_run["run_id"])
    with pytest.raises(OfficialConflictError, match="profile is frozen"):
        _profile(first_run, suffix="two")
    with pytest.raises(OfficialConflictError, match="profile is frozen"):
        delete_sensor_test_profile(DEVICE_ID)


def test_external_test_provenance_blocks_session_delete_but_unused_snapshot_cascades(
    official_store: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _successful_run(official_store["dataset_id"], HASH_A)
    activate_official_training_run(run["run_id"])
    profile = _profile(run)
    monkeypatch.setattr(
        simulation_store, "get_selected_model_id", lambda _device_id: OFFICIAL_MODEL_ID
    )

    used = start_simulation_session(
        SimulationSessionStartIn(
            device_id=DEVICE_ID,
            name="Used external test",
            session_id="sim_external_used",
        )
    )
    stop_simulation_session(used["session_id"], DEVICE_ID)
    test_run = create_sensor_test_run(
        session_id=used["session_id"],
        device_id=DEVICE_ID,
        profile_sha256=profile["profile_sha256"],
        artifact_sha256_before=HASH_A,
        artifact_sha256_after=HASH_A,
        sample_count=0,
        result={"sample_count": 0, "rows": []},
    )
    summary = list_sensor_test_runs(device_id=DEVICE_ID, limit=1)[0]
    assert summary["run_id"] == test_run["run_id"]
    assert "result" not in summary
    with pytest.raises(
        SimulationConflictError,
        match=f"sensor external-test run {test_run['run_id']}",
    ):
        delete_simulation_session(used["session_id"], DEVICE_ID)

    unused = start_simulation_session(
        SimulationSessionStartIn(
            device_id=DEVICE_ID,
            name="Unused snapshot",
            session_id="sim_external_unused",
        )
    )
    stop_simulation_session(unused["session_id"], DEVICE_ID)
    deleted = delete_simulation_session(unused["session_id"], DEVICE_ID)
    assert deleted["status"] == "deleted"
    assert get_sensor_session_snapshot(unused["session_id"]) is None


def test_calibration_provenance_blocks_session_delete_after_profile_is_removed(
    official_store: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    calibration_session_id = "sim_calibration_provenance"
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO simulation_sessions (
                session_id, device_id, name, state, started_at, ended_at, synthetic
            ) VALUES (?, ?, ?, 'completed', ?, ?, 1)
            """,
            (
                calibration_session_id,
                DEVICE_ID,
                "Formal mapping calibration",
                "2026-01-04T00:00:00Z",
                "2026-01-04T00:05:00Z",
            ),
        )

    run = _successful_run(official_store["dataset_id"], HASH_A)
    activate_official_training_run(run["run_id"])
    profile = _profile(run, calibration_session_id=calibration_session_id)

    with pytest.raises(
        SimulationConflictError,
        match=f"calibration provenance by sensor profile {profile['profile_id']}",
    ):
        delete_simulation_session(calibration_session_id, DEVICE_ID)
    with connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "DELETE FROM simulation_sessions WHERE session_id = ?",
            (calibration_session_id,),
        )

    monkeypatch.setattr(
        simulation_store, "get_selected_model_id", lambda _device_id: OFFICIAL_MODEL_ID
    )
    external = start_simulation_session(
        SimulationSessionStartIn(
            device_id=DEVICE_ID,
            name="Frozen calibration provenance",
            session_id="sim_external_calibration_provenance",
        )
    )
    stop_simulation_session(external["session_id"], DEVICE_ID)
    delete_sensor_test_profile(DEVICE_ID)

    with connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "DELETE FROM simulation_sessions WHERE session_id = ?",
            (calibration_session_id,),
        )

    with pytest.raises(
        SimulationConflictError,
        match=(
            "calibration provenance by frozen sensor profile snapshot for session "
            f"{external['session_id']}"
        ),
    ):
        delete_simulation_session(calibration_session_id, DEVICE_ID)
