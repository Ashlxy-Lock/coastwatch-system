import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_service
from app import simulation_service, simulation_store
from app.auth import encode_admin_password_hash
from app.database import connect
from app.gateway import app as gateway_app
from app.main import app as internal_app
from app.risk_dispatch import (
    CUSTOM_WINDOW_MAX_SAMPLE_AGE_SECONDS,
    ultrasonic_sample_is_valid,
)
from app.simulation_artifacts import (
    SIMULATION_ARTIFACT_LOCK,
    SimulationArtifactLockError,
)
from app.simulation_store import (
    list_labeled_training_rows,
    upsert_device_simulation_scenario,
)

DEVICE_ID = "COAST_01"
CUSTOM_MODEL_ID = "custom-water-logreg-v1"
DEFAULT_MODEL_ID = "coastal-risk-logreg-v1"
OFFICIAL_MODEL_ID = "uk-official-coast-logreg-v2"
DEVICE_TOKEN = "simulation-api-test-token"
AUTH_HEADERS = {"X-Device-Token": DEVICE_TOKEN}
ADMIN_PASSWORD_HASH = encode_admin_password_hash(
    "simulation-admin-password", salt=b"simulation-tests"
)


@pytest.mark.parametrize(
    ("distance_mm", "expected"),
    ((19, False), (20, True), (4_000, True), (4_001, False)),
)
def test_ultrasonic_validity_uses_shared_physical_bounds_and_not_alarm(
    distance_mm: int, expected: bool
):
    assert (
        ultrasonic_sample_is_valid(
            {
                "distance_mm": distance_mm,
                "health_flags": 1,
                "alarm_level": 4,
            }
        )
        is expected
    )


def test_product_training_readiness_thresholds_are_explicit():
    assert simulation_service.MINIMUM_ELIGIBLE_SESSIONS == 12
    assert simulation_service.RECOMMENDED_ELIGIBLE_SESSIONS == 30
    assert simulation_service.MINIMUM_LABELLED_SAMPLES == 240
    assert simulation_service.MINIMUM_SAMPLES_PER_CLASS == 80
    assert simulation_service.MINIMUM_SESSIONS_PER_CLASS == 6
    assert simulation_service.MINIMUM_MIXED_LABEL_SESSIONS == 4
    assert simulation_service.MINIMUM_DISTINCT_SCENARIOS == 3


def test_simulated_environment_strings_fit_esp32_utf8_buffers():
    payload = scenario_payload(1)
    payload.update(
        {
            "scenario_name": "模拟海岸" * 20,
            "updated_at": datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc),
        }
    )

    environment = simulation_service.build_simulated_environment(payload)

    assert len(environment.location.encode("utf-8")) <= 63
    assert len(environment.display_location.encode("ascii")) <= 35
    assert environment.display_location == "SIMULATED COAST"
    assert environment.source == "manual"
    assert environment.weather == "OPERATOR SIMULATION"


def test_custom_environment_and_risk_fail_closed_without_operator_scenario(
    internal_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, _artifact_path = internal_client
    monkeypatch.setattr(
        main_service, "get_selected_model_id", lambda _device_id: CUSTOM_MODEL_ID
    )
    monkeypatch.setattr(
        main_service, "get_device_simulation_scenario", lambda _device_id: None
    )

    environment = client.get("/api/v1/environment", params={"device_id": DEVICE_ID})
    assert environment.status_code == 503
    assert environment.json()["detail"] == (
        "Custom simulation model requires an active operator scenario"
    )

    assert (
        client.post(
            "/api/v1/telemetry",
            json=live_telemetry_payload(1, distance_mm=800),
        ).status_code
        == 201
    )
    risk = client.get("/api/v1/risk", params={"device_id": DEVICE_ID})
    assert risk.status_code == 503
    assert risk.json()["detail"] == (
        "Custom simulation model requires an active operator scenario"
    )


def test_official_environment_and_risk_fail_closed_without_frozen_profile(
    internal_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, _artifact_path = internal_client
    monkeypatch.setattr(
        main_service,
        "get_selected_model_id",
        lambda _device_id: "uk-official-coast-logreg-v2",
    )
    assert (
        client.post(
            "/api/v1/telemetry",
            json=live_telemetry_payload(91, distance_mm=800),
        ).status_code
        == 201
    )

    environment = client.get(
        "/api/v1/environment", params={"device_id": DEVICE_ID}
    )
    assert environment.status_code == 503
    assert environment.json()["detail"] == (
        "Official model requires a valid frozen sensor profile"
    )
    risk = client.get("/api/v1/risk", params={"device_id": DEVICE_ID})
    assert risk.status_code == 503
    assert risk.json()["detail"] == (
        "Official model requires a valid frozen sensor profile"
    )


def test_legacy_custom_artifact_fails_closed_and_selection_falls_back(
    internal_client,
):
    client, artifact_path = internal_client
    legacy_artifact = {
        "schema": "coastwatch.simulation-water-logreg",
        "schema_version": 1,
        "model_id": CUSTOM_MODEL_ID,
        "model_type": "binary_logistic_regression",
    }
    canonical = json.dumps(
        legacy_artifact,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    legacy_artifact["hash"] = hashlib.sha256(canonical).hexdigest()
    artifact_path.write_text(
        json.dumps(legacy_artifact),
        encoding="utf-8",
    )
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO device_model_selections (device_id, model_id, selected_at)
            VALUES (?, ?, ?)
            """,
            (DEVICE_ID, CUSTOM_MODEL_ID, "2026-08-14T09:00:00Z"),
        )

    catalog = client.get("/api/v1/models", params={"device_id": DEVICE_ID})
    assert catalog.status_code == 200
    assert catalog.json()["selected_model_id"] == DEFAULT_MODEL_ID
    custom = next(
        model
        for model in catalog.json()["models"]
        if model["model_id"] == CUSTOM_MODEL_ID
    )
    assert custom["status"] == "not_trained"

    model = client.get("/api/v1/simulations/model")
    assert model.status_code == 503
    assert model.json()["detail"] == "Custom simulation model artifact is invalid"


def test_training_resets_warmup_after_received_gap_and_uptime_reboot():
    processed_sessions: list[dict] = []
    for session_index in range(2):
        environment = {
            "sim_air_temperature_c": 10.0 + session_index,
            "sim_humidity_percent": 60.0 + session_index,
            "sim_wind_speed_kmh": 15.0 + session_index,
            "sim_wave_height_m": 0.8 + session_index * 0.2,
            "sim_wave_period_s": 5.0 + session_index,
            "sim_water_temperature_c": 12.0 + session_index,
            "sim_sea_level_height_m": 0.1 + session_index * 0.1,
            "sim_ocean_current_velocity_kmh": 0.6 + session_index * 0.1,
            "sim_hour_sin": 0.2 + session_index * 0.1,
            "sim_hour_cos": 0.9 - session_index * 0.1,
            "sim_day_of_year_sin": -0.3 + session_index * 0.1,
            "sim_day_of_year_cos": -0.8 + session_index * 0.1,
            "sim_latitude": 50.8 + session_index * 0.01,
            "sim_longitude": -1.1 - session_index * 0.01,
        }
        rows: list[dict] = []
        # A >5 s receive gap starts epoch 1; a falling uptime starts epoch 2.
        epochs = (
            (range(1, 6), (0.0, 0.5, 1.0, 1.5, 2.0), 500, "safe"),
            (range(6, 11), (10.0, 10.5, 11.0, 11.5, 12.0), 3_000, "danger"),
            (range(11, 16), (13.0, 13.5, 14.0, 14.5, 15.0), 500, "safe"),
        )
        base = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
        for seqs, offsets, first_uptime, label in epochs:
            for step, (seq, offset) in enumerate(zip(seqs, offsets, strict=True)):
                rows.append(
                    {
                        "session_id": f"continuity-{session_index}",
                        "device_id": DEVICE_ID,
                        "seq": seq,
                        "uptime_ms": first_uptime + step * 500,
                        "received_at": (base + timedelta(seconds=offset)).isoformat(),
                        "distance_mm": 900 - seq * 2,
                        "baseline_distance_mm": 900,
                        "water_rise_mm": seq * 2,
                        "rise_rate_mm_s": 4,
                        "health_flags": 1,
                        "label": label,
                        **environment,
                    }
                )
        valid_rows, epoch_count = simulation_service._valid_training_rows_with_epochs(
            rows
        )
        assert epoch_count == 3
        assert [row["_window_epoch"] for row in valid_rows] == [
            *([0] * 5),
            *([1] * 5),
            *([2] * 5),
        ]
        processed_sessions.append(
            {
                "session_id": f"continuity-{session_index}",
                "samples": valid_rows,
            }
        )

    assessment = simulation_service.assess_simulation_training_data(
        processed_sessions,
        test_fraction=0.5,
    )
    assert assessment["ready"] is True
    assert assessment["excluded_warmup_samples"] == 24
    assert assessment["labelled_sample_count"] == 6


@pytest.fixture
def internal_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    artifact_path = tmp_path / "custom-water-logreg-v1.json"
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "internal.db"))
    monkeypatch.setenv("COAST_CUSTOM_MODEL_PATH", str(artifact_path))
    # Legacy model tests exercise the archived implementation explicitly.
    # Production/default runtime leaves this unset and returns 410 for fit.
    monkeypatch.setenv("COAST_ENABLE_LEGACY_SIMULATION_TRAINING", "1")
    monkeypatch.setattr(simulation_service, "MINIMUM_ELIGIBLE_SESSIONS", 2)
    monkeypatch.setattr(simulation_service, "RECOMMENDED_ELIGIBLE_SESSIONS", 3)
    monkeypatch.setattr(simulation_service, "MINIMUM_LABELLED_SAMPLES", 4)
    monkeypatch.setattr(simulation_service, "MINIMUM_SAMPLES_PER_CLASS", 1)
    monkeypatch.setattr(simulation_service, "MINIMUM_SESSIONS_PER_CLASS", 1)
    monkeypatch.setattr(simulation_service, "MINIMUM_MIXED_LABEL_SESSIONS", 1)
    monkeypatch.setattr(simulation_service, "MINIMUM_DISTINCT_SCENARIOS", 2)
    with TestClient(internal_app) as client:
        yield client, artifact_path


def test_legacy_training_is_retired_by_default_and_official_model_owns_third_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "retired-default.db"))
    monkeypatch.setenv(
        "COAST_CUSTOM_MODEL_PATH", str(tmp_path / "legacy-custom-model.json")
    )
    monkeypatch.delenv("COAST_ENABLE_LEGACY_SIMULATION_TRAINING", raising=False)
    with TestClient(internal_app) as client:
        with connect() as connection:
            connection.execute(
                """
                INSERT INTO device_model_selections (device_id, model_id, selected_at)
                VALUES (?, ?, ?)
                """,
                (DEVICE_ID, CUSTOM_MODEL_ID, "2026-08-17T12:00:00Z"),
            )
        response = client.post(
            "/api/v1/simulations/train", json={"device_id": DEVICE_ID}
        )
        assert response.status_code == 410
        catalog = client.get("/api/v1/models", params={"device_id": DEVICE_ID})
        with connect() as connection:
            connection.execute(
                """
                UPDATE device_model_selections
                SET model_id = ?, selected_at = ?
                WHERE device_id = ?
                """,
                (OFFICIAL_MODEL_ID, "2026-08-17T12:01:00Z", DEVICE_ID),
            )
        stale_official_catalog = client.get(
            "/api/v1/models", params={"device_id": DEVICE_ID}
        )
        stale_official_environment = client.get(
            "/api/v1/environment", params={"device_id": DEVICE_ID}
        )
    assert catalog.status_code == 200
    ids = [item["model_id"] for item in catalog.json()["models"]]
    assert catalog.json()["selected_model_id"] == DEFAULT_MODEL_ID
    assert CUSTOM_MODEL_ID not in ids
    assert OFFICIAL_MODEL_ID in ids
    official_descriptor = next(
        item for item in catalog.json()["models"] if item["model_id"] == OFFICIAL_MODEL_ID
    )
    assert "operator_attested_raw_hash_verified" in official_descriptor["description"]
    assert "deterministic_importer_replay_verified=false" in official_descriptor[
        "description"
    ]
    assert stale_official_catalog.json()["selected_model_id"] == OFFICIAL_MODEL_ID
    assert stale_official_environment.status_code == 503


@pytest.fixture
def gateway_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    artifact_path = tmp_path / "gateway-custom-water.json"
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "gateway.db"))
    monkeypatch.setenv("COAST_CUSTOM_MODEL_PATH", str(artifact_path))
    monkeypatch.setenv("COAST_DEVICE_TOKEN", DEVICE_TOKEN)
    monkeypatch.setenv("COAST_ADMIN_PASSWORD_HASH", ADMIN_PASSWORD_HASH)
    monkeypatch.setenv(
        "COAST_ADMIN_SESSION_SECRET", "simulation-admin-session-secret-0001"
    )
    with TestClient(gateway_app) as client:
        yield client


def telemetry_payload(
    session_id: str,
    seq: int,
    *,
    session_index: int = 0,
    vision_fault: bool = False,
) -> dict:
    distances = (900, 895, 890, 820, 700, 650, 600, 550)
    rises = (0, 5, 10, 80, 200, 250, 300, 350)
    rates = (0, 5, 5, 70, 120, 50, 50, 50)
    offset = session_index * 3
    distance = (
        distances[seq - 1]
        if seq <= len(distances)
        else max(200, distances[-1] - (seq - len(distances)) * 20)
    )
    rise = rises[seq - 1] if seq <= len(rises) else rises[-1] + (seq - len(rises)) * 20
    rate = rates[seq - 1] if seq <= len(rates) else 40
    return {
        "device_id": DEVICE_ID,
        "seq": seq,
        "uptime_ms": session_index * 100_000 + seq * 500,
        "distance_mm": distance + offset,
        "water_rise_mm": rise,
        "rise_rate_mm_s": rate,
        "person_detected": False,
        "alarm_level": 4 if vision_fault else (0 if seq <= 4 else 2),
        "health_flags": 1 if vision_fault else 7,
        "wifi_rssi": -55,
        "simulation_session_id": session_id,
    }


def scenario_payload(session_index: int) -> dict:
    return {
        "device_id": DEVICE_ID,
        "scenario_name": f"Fictitious coast {session_index}",
        "simulated_at": (
            f"2026-08-{(session_index % 20) + 1:02d}T"
            f"{8 + (session_index % 12):02d}:00:00Z"
        ),
        "sim_air_temperature_c": 8.0 + session_index,
        "sim_humidity_percent": 55.0 + session_index,
        "sim_wind_speed_kmh": 10.0 + session_index * 2.0,
        "sim_wave_height_m": 0.4 + session_index * 0.1,
        "sim_wave_period_s": 4.0 + session_index * 0.2,
        "sim_water_temperature_c": 11.0 + session_index * 0.2,
        "sim_sea_level_height_m": -0.4 + session_index * 0.05,
        "sim_ocean_current_velocity_kmh": 0.5 + session_index * 0.05,
        "sim_latitude": 50.8 + session_index * 0.001,
        "sim_longitude": -1.1 - session_index * 0.001,
        "note": "operator-authored course simulation",
    }


def live_telemetry_payload(
    seq: int,
    *,
    distance_mm: int,
    health_flags: int = 1,
    alarm_level: int = 0,
) -> dict:
    return {
        "device_id": DEVICE_ID,
        "seq": seq,
        "uptime_ms": 1_000_000 + seq * 500,
        "distance_mm": distance_mm,
        "water_rise_mm": 900 - distance_mm,
        "rise_rate_mm_s": 25,
        "person_detected": False,
        "alarm_level": alarm_level,
        "health_flags": health_flags,
        "wifi_rssi": -52,
    }


def collect_completed_session(
    client: TestClient,
    session_index: int,
    *,
    verify_backend_only_label_guard: bool = False,
    vision_fault: bool = False,
) -> str:
    session_id = f"sim_api_{session_index:03d}"
    saved_scenario = client.put(
        "/api/v1/simulations/device-scenario",
        json=scenario_payload(session_index),
    )
    assert saved_scenario.status_code == 200
    assert saved_scenario.json()["data_kind"] == "operator_supplied_simulation"
    started = client.post(
        "/api/v1/simulations/sessions",
        json={
            "device_id": DEVICE_ID,
            "name": f"Tank wave run {session_index}",
            "session_id": session_id,
        },
    )
    assert started.status_code == 201
    assert started.json()["state"] == "active"
    assert started.json()["synthetic"] is True
    assert started.json()["sample_count"] == 0
    snapshot = client.get(
        f"/api/v1/simulations/sessions/{session_id}/scenario",
        params={"device_id": DEVICE_ID},
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["scenario_hash"] == saved_scenario.json()["scenario_hash"]

    active = client.get(
        "/api/v1/simulations/sessions/active",
        params={"device_id": DEVICE_ID},
    )
    assert active.status_code == 200
    assert active.json()["session_id"] == session_id

    for seq in range(1, 9):
        telemetry = client.post(
            "/api/v1/telemetry",
            json=telemetry_payload(
                session_id,
                seq,
                session_index=session_index,
                vision_fault=vision_fault,
            ),
        )
        assert telemetry.status_code == 201
        assert telemetry.json()["simulation_session_id"] == session_id

    if verify_backend_only_label_guard:
        active_label = client.put(
            "/api/v1/simulations/labels",
            json={
                "session_id": session_id,
                "device_id": DEVICE_ID,
                "start_seq": 1,
                "end_seq": 3,
                "label": "safe",
                "note": "must not be accepted during collection",
                "version": 1,
            },
        )
        assert active_label.status_code == 409
        assert "after" in active_label.json()["detail"]

    active = client.get(
        "/api/v1/simulations/sessions/active",
        params={"device_id": DEVICE_ID},
    )
    assert active.status_code == 200
    assert active.json()["sample_count"] == 8

    stopped = client.post(
        f"/api/v1/simulations/sessions/{session_id}/stop",
        json={"device_id": DEVICE_ID},
    )
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "completed"
    assert stopped.json()["sample_count"] == 8

    samples = client.get(
        f"/api/v1/simulations/sessions/{session_id}/samples",
        params={"device_id": DEVICE_ID},
    )
    assert samples.status_code == 200
    assert [sample["seq"] for sample in samples.json()] == list(range(1, 9))
    assert all(sample["session_id"] == session_id for sample in samples.json())

    # No sensor sample is silently called safe before a backend user labels it.
    labels = client.get(
        f"/api/v1/simulations/sessions/{session_id}/labels",
        params={"device_id": DEVICE_ID, "version": 1},
    )
    assert labels.status_code == 200
    assert labels.json() == []
    assert {
        row["label"]
        for row in list_labeled_training_rows(session_id, DEVICE_ID, version=1)
    } == {"unknown"}

    for start_seq, end_seq, label, note in (
        (1, 5, "safe", "calm water"),
        (7, 8, "danger", "simulated rapid rise"),
    ):
        labelled = client.put(
            "/api/v1/simulations/labels",
            json={
                "session_id": session_id,
                "device_id": DEVICE_ID,
                "start_seq": start_seq,
                "end_seq": end_seq,
                "label": label,
                "note": note,
                "version": 1,
            },
        )
        assert labelled.status_code == 200
        assert labelled.json()["label"] == label

    training_rows = list_labeled_training_rows(session_id, DEVICE_ID, version=1)
    assert [row["label"] for row in training_rows] == [
        "safe",
        "safe",
        "safe",
        "safe",
        "safe",
        "unknown",
        "danger",
        "danger",
    ]
    return session_id


def test_unused_completed_session_can_be_deleted_through_strict_internal_api(
    internal_client,
):
    client, _artifact_path = internal_client
    session_id = collect_completed_session(client, 40)

    assert client.delete(f"/api/v1/simulations/sessions/{session_id}").status_code == 422
    hidden = client.delete(
        f"/api/v1/simulations/sessions/{session_id}",
        params={"device_id": "COAST_99"},
    )
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == f"simulation session {session_id} not found"

    deleted = client.delete(
        f"/api/v1/simulations/sessions/{session_id}",
        params={"device_id": DEVICE_ID},
    )
    assert deleted.status_code == 200
    assert deleted.json() == {
        "status": "deleted",
        "session_id": session_id,
        "device_id": DEVICE_ID,
        "deleted_counts": {
            "sessions": 1,
            "samples": 8,
            "labels": 2,
            "scenario_snapshots": 1,
        },
        "detached_telemetry_count": 8,
    }
    assert client.get(
        f"/api/v1/simulations/sessions/{session_id}",
        params={"device_id": DEVICE_ID},
    ).status_code == 404
    with connect() as connection:
        audit_rows = connection.execute(
            """
            SELECT simulation_session_id FROM telemetry
            WHERE device_id = ? ORDER BY id
            """,
            (DEVICE_ID,),
        ).fetchall()
    assert len(audit_rows) == 8
    assert all(row["simulation_session_id"] is None for row in audit_rows)


def test_active_session_delete_is_rejected_by_internal_api(internal_client):
    client, _artifact_path = internal_client
    assert client.put(
        "/api/v1/simulations/device-scenario",
        json=scenario_payload(41),
    ).status_code == 200
    session_id = "sim_api_delete_active"
    assert client.post(
        "/api/v1/simulations/sessions",
        json={
            "device_id": DEVICE_ID,
            "name": "Active delete guard",
            "session_id": session_id,
        },
    ).status_code == 201

    rejected = client.delete(
        f"/api/v1/simulations/sessions/{session_id}",
        params={"device_id": DEVICE_ID},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == (
        f"active simulation session {session_id} cannot be deleted"
    )


def test_training_and_delete_use_the_same_process_lock():
    assert main_service.SIMULATION_ARTIFACT_LOCK is SIMULATION_ARTIFACT_LOCK
    assert simulation_store.SIMULATION_ARTIFACT_LOCK is SIMULATION_ARTIFACT_LOCK


def test_artifact_lock_failure_returns_503_without_deleting_or_training(
    internal_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, artifact_path = internal_client
    session_id = collect_completed_session(client, 42)

    class UnavailableLock:
        def __enter__(self):
            raise SimulationArtifactLockError("test lock failure")

        def __exit__(self, *_args):  # pragma: no cover - enter always raises
            return None

    monkeypatch.setattr(
        simulation_store, "SIMULATION_ARTIFACT_LOCK", UnavailableLock()
    )
    delete_response = client.delete(
        f"/api/v1/simulations/sessions/{session_id}",
        params={"device_id": DEVICE_ID},
    )
    assert delete_response.status_code == 503
    assert delete_response.json()["detail"] == (
        "Simulation artifact maintenance is temporarily unavailable"
    )
    assert client.get(
        f"/api/v1/simulations/sessions/{session_id}",
        params={"device_id": DEVICE_ID},
    ).status_code == 200

    monkeypatch.setattr(main_service, "SIMULATION_ARTIFACT_LOCK", UnavailableLock())
    train_response = client.post(
        "/api/v1/simulations/train",
        json={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert train_response.status_code == 503
    assert not artifact_path.exists()


def test_internal_simulation_lifecycle_training_catalog_and_selection(
    internal_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, artifact_path = internal_client

    initial_catalog = client.get("/api/v1/models", params={"device_id": DEVICE_ID})
    assert initial_catalog.status_code == 200
    assert initial_catalog.json()["selected_model_id"] == DEFAULT_MODEL_ID
    initial_models = {
        model["model_id"]: model for model in initial_catalog.json()["models"]
    }
    assert initial_models[CUSTOM_MODEL_ID]["status"] == "not_trained"
    assert client.get("/api/v1/simulations/model").status_code == 404
    assert (
        client.put(
            "/api/v1/device-model",
            json={"device_id": DEVICE_ID, "model_id": CUSTOM_MODEL_ID},
        ).status_code
        == 409
    )

    for session_index in range(1, 4):
        collect_completed_session(
            client,
            session_index,
            verify_backend_only_label_guard=session_index == 1,
            vision_fault=True,
        )

    overview = client.get(
        "/api/v1/simulations/overview",
        params={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert overview.status_code == 200
    assert overview.json()["totals"] == {
        "session_count": 3,
        "active_session_count": 0,
        "completed_session_count": 3,
        "sample_count": 24,
        "valid_ultrasonic_samples": 24,
        "invalid_ultrasonic_samples": 0,
        "label_counts": {"safe": 15, "danger": 6, "unknown": 3},
        "labelled_sample_count": 21,
        "label_coverage": 0.875,
    }
    timeline = client.get(
        "/api/v1/simulations/sessions/sim_api_001/timeline",
        params={
            "device_id": DEVICE_ID,
            "label_version": 1,
            "after_seq": 3,
        },
    )
    assert timeline.status_code == 200
    assert timeline.json()["session"]["label_counts"] == {
        "safe": 5,
        "danger": 2,
        "unknown": 1,
    }
    assert [point["seq"] for point in timeline.json()["points"]] == list(range(4, 9))
    assert [point["label"] for point in timeline.json()["points"]] == [
        "safe",
        "safe",
        "unknown",
        "danger",
        "danger",
    ]
    assert all(point["valid_ultrasonic"] for point in timeline.json()["points"])

    readiness = client.get(
        "/api/v1/simulations/training-readiness",
        params={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert readiness.json()["blockers"] == []
    assert readiness.json()["data_quality"]["eligible_session_count"] == 3
    planned = readiness.json()["planned_split"]
    assert set(planned["train_sessions"]).isdisjoint(planned["test_sessions"])

    trained = client.post(
        "/api/v1/simulations/train",
        json={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert trained.status_code == 200
    assert trained.json()["model_id"] == CUSTOM_MODEL_ID
    assert trained.json()["data_kind"] == "simulation"
    assert trained.json()["deployment_mode"] == "shadow"
    assert trained.json()["session_count"] == 3
    assert trained.json()["sample_count"] == 24
    assert trained.json()["labelled_sample_count"] == 9
    assert trained.json()["excluded_invalid_ultrasonic_samples"] == 0
    assert len(trained.json()["artifact_hash"]) == 64
    assert len(trained.json()["dataset_hash"]) == 64
    assert trained.json()["training_config"]["random_state"] == 42
    assert trained.json()["source_manifest"]["label_version"] == 1
    archived_path = artifact_path.parent / trained.json()["archived_artifact_file"]
    assert archived_path.is_file()

    assert artifact_path.is_file()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["model_id"] == CUSTOM_MODEL_ID
    assert artifact["metrics"]["split_strategy"] == (
        "whole_session_and_simulated_environment_group_holdout"
    )
    assert artifact["metrics"]["session_overlap"] == []
    assert artifact["metrics"]["scenario_group_overlap"] == []
    assert artifact["metrics"]["excluded_unknown_samples"] == 3
    assert artifact["source_manifest"]["dataset_hash"] == trained.json()["dataset_hash"]
    baseline = artifact["metrics"]["baselines"]["water_rise_threshold"]
    assert baseline["selection"]["fit_on"] == "train_sessions_only"
    assert baseline["selection"]["sample_weighting"] == "equal_session"
    assert baseline["test"]["brier_score"] is None
    assert "balanced_accuracy" in baseline["test"]
    assert "balanced_accuracy" in artifact["metrics"]["delta_vs_baseline"]
    assert "ultrasonic_only_logistic_regression" in artifact["metrics"]["baselines"]
    assert "environment_only_logistic_regression" in artifact["metrics"]["baselines"]

    model_metadata = client.get("/api/v1/simulations/model")
    assert model_metadata.status_code == 200
    assert model_metadata.json()["hash"] == trained.json()["artifact_hash"]
    assert model_metadata.json()["deployment_mode"] == "shadow"
    assert model_metadata.json()["source_manifest"]["device_id"] == DEVICE_ID

    trained_catalog = client.get("/api/v1/models", params={"device_id": DEVICE_ID})
    assert trained_catalog.status_code == 200
    trained_models = {
        model["model_id"]: model for model in trained_catalog.json()["models"]
    }
    assert trained_models[CUSTOM_MODEL_ID]["status"] == "ready"
    assert trained_models[CUSTOM_MODEL_ID]["mode"] == "simulation-shadow"

    selection = client.put(
        "/api/v1/device-model",
        json={"device_id": DEVICE_ID, "model_id": CUSTOM_MODEL_ID},
    )
    assert selection.status_code == 200
    assert selection.json()["selected_model_id"] == CUSTOM_MODEL_ID
    assert (
        client.get("/api/v1/models", params={"device_id": DEVICE_ID}).json()[
            "selected_model_id"
        ]
        == CUSTOM_MODEL_ID
    )

    simulated_environment = client.get(
        "/api/v1/environment", params={"device_id": DEVICE_ID}
    )
    assert simulated_environment.status_code == 200
    assert simulated_environment.json()["source"] == "manual"
    assert simulated_environment.json()["weather"] == "OPERATOR SIMULATION"
    assert simulated_environment.json()["provider"] == "CoastWatch manual scenario"
    assert simulated_environment.json()["wave_height_m"] == pytest.approx(
        scenario_payload(3)["sim_wave_height_m"]
    )

    monkeypatch.setattr(
        main_service,
        "load_environment",
        lambda _device_id=None: {
            "location": "Brighton",
            "display_location": "BRIGHTON",
            "kind": "coast",
            "weather": "CLEAR",
            "air_temperature_c": 15.0,
            "humidity_percent": 65.0,
            "wind_speed_kmh": 12.0,
            "water_temperature_c": 14.0,
            "wave_height_m": 0.8,
            "wave_period_s": 5.0,
            "sea_level_height_m": 0.1,
            "tide_status": "RISING",
            "ocean_current_velocity_kmh": 0.7,
            "source": "demo",
            "provider": "test provider",
            "stale": False,
            "updated_at": "2026-08-14T09:00:00Z",
        },
    )
    selected_default = client.put(
        "/api/v1/device-model",
        json={"device_id": DEVICE_ID, "model_id": DEFAULT_MODEL_ID},
    )
    assert selected_default.status_code == 200
    real_environment = client.get(
        "/api/v1/environment", params={"device_id": DEVICE_ID}
    )
    assert real_environment.status_code == 200
    assert real_environment.json()["source"] == "demo"
    assert real_environment.json()["weather"] == "CLEAR"
    reselected_custom = client.put(
        "/api/v1/device-model",
        json={"device_id": DEVICE_ID, "model_id": CUSTOM_MODEL_ID},
    )
    assert reselected_custom.status_code == 200

    # A missing OpenMV makes the STM32 report FAULT while its ultrasonic bit and
    # water measurements remain valid. Training and this custom water model
    # must use the ultrasonic-specific health signal without weakening the
    # independent local-alarm fault state.
    for seq, distance_mm in enumerate((890, 865, 830, 780, 720), start=100):
        telemetry = client.post(
            "/api/v1/telemetry",
            json=live_telemetry_payload(
                seq,
                distance_mm=distance_mm,
                alarm_level=4,
            ),
        )
        assert telemetry.status_code == 201
        assert telemetry.json()["simulation_session_id"] is None

    risk = client.get("/api/v1/risk", params={"device_id": DEVICE_ID})
    assert risk.status_code == 200
    assert risk.json()["model_version"].startswith(f"{CUSTOM_MODEL_ID}-")
    assert risk.json()["model_source"] == "model"
    assert risk.json()["deployment_mode"] == "shadow"
    assert risk.json()["local_alarm_level"] == 4
    assert risk.json()["data_quality"] == "fault"
    assert risk.json()["degraded"] is True
    assert "SENSOR_FAULT" in risk.json()["reason_codes"]
    assert "SIMULATION_DATA_ONLY" in risk.json()["reason_codes"]

    bad_ultrasonic = client.post(
        "/api/v1/telemetry",
        json=live_telemetry_payload(
            105,
            distance_mm=0,
            health_flags=0,
            alarm_level=4,
        ),
    )
    assert bad_ultrasonic.status_code == 201
    unavailable = client.get("/api/v1/risk", params={"device_id": DEVICE_ID})
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == (
        "Selected model cannot produce a valid result"
    )


def test_training_selection_subset_defaults_and_provenance(internal_client):
    client, artifact_path = internal_client
    session_ids = [collect_completed_session(client, index) for index in range(10, 13)]

    default_readiness = client.get(
        "/api/v1/simulations/training-readiness",
        params={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert default_readiness.status_code == 200
    default_selection = default_readiness.json()["selection"]
    assert default_selection["mode"] == "all_completed"
    assert default_selection["selected_session_ids"] == sorted(session_ids)
    assert default_selection["effective_session_ids"] == sorted(session_ids)

    one_session = client.get(
        "/api/v1/simulations/training-readiness",
        params=[
            ("device_id", DEVICE_ID),
            ("label_version", "1"),
            ("session_id", session_ids[0]),
        ],
    )
    assert one_session.status_code == 200
    assert one_session.json()["ready"] is False
    assert one_session.json()["selection"]["effective_session_ids"] == [session_ids[0]]
    assert any(
        "two labelled sessions" in item for item in one_session.json()["blockers"]
    )

    selected = session_ids[:2]
    explicit_readiness = client.get(
        "/api/v1/simulations/training-readiness",
        params=[
            ("device_id", DEVICE_ID),
            ("label_version", "1"),
            *(("session_id", session_id) for session_id in selected),
        ],
    )
    assert explicit_readiness.status_code == 200
    explicit_selection = explicit_readiness.json()["selection"]
    assert explicit_selection["mode"] == "explicit"
    assert explicit_selection["requested_session_ids"] == sorted(selected)
    assert explicit_selection["effective_session_ids"] == sorted(selected)
    assert len(explicit_selection["selection_hash"]) == 64
    assert explicit_readiness.json()["evidence_quality"]["tier"] == "exploratory"
    assert (
        explicit_readiness.json()["evidence_quality"]["environment_effects_learnable"]
        is False
    )

    trained = client.post(
        "/api/v1/simulations/train",
        json={
            "device_id": DEVICE_ID,
            "label_version": 1,
            "session_ids": selected,
        },
    )
    assert trained.status_code == 200
    body = trained.json()
    assert body["selection"] == explicit_selection
    assert body["source_manifest"]["selection"] == explicit_selection
    assert body["source_manifest"]["session_ids"] == sorted(selected)
    assert {
        item["session_id"] for item in body["source_manifest"]["collection_sessions"]
    } == set(selected)
    assert set(body["metrics"]["train_sessions"]) | set(
        body["metrics"]["test_sessions"]
    ) == set(selected)
    subset_hash = body["dataset_hash"]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert (
        artifact["source_manifest"]["selection"]["selection_hash"]
        == body["selection"]["selection_hash"]
    )

    # Replace the current model with a run that excludes session_ids[0].  The
    # first archived artifact still proves that session was used and must keep
    # deletion fail-closed.
    alternative = client.post(
        "/api/v1/simulations/train",
        json={
            "device_id": DEVICE_ID,
            "label_version": 1,
            "session_ids": session_ids[1:],
        },
    )
    assert alternative.status_code == 200
    assert session_ids[0] not in alternative.json()["source_manifest"]["session_ids"]
    referenced = client.delete(
        f"/api/v1/simulations/sessions/{session_ids[0]}",
        params={"device_id": DEVICE_ID},
    )
    assert referenced.status_code == 409
    assert referenced.json()["detail"].startswith(
        f"simulation session {session_ids[0]} is referenced by training artifact "
        f"{CUSTOM_MODEL_ID}-"
    )

    trained_all = client.post(
        "/api/v1/simulations/train",
        json={"device_id": DEVICE_ID, "label_version": 1, "session_ids": None},
    )
    assert trained_all.status_code == 200
    assert trained_all.json()["selection"]["mode"] == "all_completed"
    assert trained_all.json()["source_manifest"]["session_ids"] == sorted(session_ids)
    assert trained_all.json()["dataset_hash"] != subset_hash

    assert (
        client.post(
            "/api/v1/simulations/train",
            json={"device_id": DEVICE_ID, "session_ids": []},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/simulations/train",
            json={"device_id": DEVICE_ID, "session_ids": [selected[0], selected[0]]},
        ).status_code
        == 422
    )
    duplicate_query = client.get(
        "/api/v1/simulations/training-readiness",
        params=[
            ("device_id", DEVICE_ID),
            ("session_id", selected[0]),
            ("session_id", selected[0]),
        ],
    )
    assert duplicate_query.status_code == 422


def test_explicit_training_selection_rejects_invalid_sessions(internal_client):
    client, _artifact_path = internal_client

    unknown = client.post(
        "/api/v1/simulations/train",
        json={"device_id": DEVICE_ID, "session_ids": ["sim_unknown_999"]},
    )
    assert unknown.status_code == 422
    assert "does not exist" in unknown.json()["detail"]

    other_scenario = scenario_payload(1)
    other_scenario["device_id"] = "OTHER_01"
    assert (
        client.put(
            "/api/v1/simulations/device-scenario", json=other_scenario
        ).status_code
        == 200
    )
    other_id = "sim_other_001"
    assert (
        client.post(
            "/api/v1/simulations/sessions",
            json={
                "device_id": "OTHER_01",
                "name": "Other device run",
                "session_id": other_id,
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/simulations/sessions/{other_id}/stop",
            json={"device_id": "OTHER_01"},
        ).status_code
        == 200
    )
    cross_device = client.post(
        "/api/v1/simulations/train",
        json={"device_id": DEVICE_ID, "session_ids": [other_id]},
    )
    assert cross_device.status_code == 409
    assert "different device" in cross_device.json()["detail"]

    assert (
        client.put(
            "/api/v1/simulations/device-scenario", json=scenario_payload(2)
        ).status_code
        == 200
    )
    active_id = "sim_active_001"
    assert (
        client.post(
            "/api/v1/simulations/sessions",
            json={
                "device_id": DEVICE_ID,
                "name": "Still active",
                "session_id": active_id,
            },
        ).status_code
        == 201
    )
    active = client.post(
        "/api/v1/simulations/train",
        json={"device_id": DEVICE_ID, "session_ids": [active_id]},
    )
    assert active.status_code == 409
    assert "not completed" in active.json()["detail"]
    assert (
        client.post(
            f"/api/v1/simulations/sessions/{active_id}/stop",
            json={"device_id": DEVICE_ID},
        ).status_code
        == 200
    )

    no_window_id = "sim_no_window_001"
    assert (
        client.post(
            "/api/v1/simulations/sessions",
            json={
                "device_id": DEVICE_ID,
                "name": "Too short",
                "session_id": no_window_id,
            },
        ).status_code
        == 201
    )
    for seq in range(1, 5):
        assert (
            client.post(
                "/api/v1/telemetry", json=telemetry_payload(no_window_id, seq)
            ).status_code
            == 201
        )
    assert (
        client.post(
            f"/api/v1/simulations/sessions/{no_window_id}/stop",
            json={"device_id": DEVICE_ID},
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/v1/simulations/labels",
            json={
                "session_id": no_window_id,
                "device_id": DEVICE_ID,
                "start_seq": 1,
                "end_seq": 4,
                "label": "safe",
                "version": 1,
            },
        ).status_code
        == 200
    )
    no_window = client.post(
        "/api/v1/simulations/train",
        json={"device_id": DEVICE_ID, "session_ids": [no_window_id]},
    )
    assert no_window.status_code == 422
    assert "five-sample-window-eligible" in no_window.json()["detail"]

    legacy_id = collect_completed_session(client, 3)
    with connect() as connection:
        connection.execute(
            "DELETE FROM simulation_scenarios WHERE session_id = ?", (legacy_id,)
        )
    no_scenario = client.post(
        "/api/v1/simulations/train",
        json={"device_id": DEVICE_ID, "session_ids": [legacy_id]},
    )
    assert no_scenario.status_code == 422
    assert "no scenario snapshot" in no_scenario.json()["detail"]


def test_custom_model_live_window_is_fresh_contiguous_and_epoch_scoped(
    internal_client,
):
    client, _artifact_path = internal_client
    for session_index in range(11, 14):
        collect_completed_session(client, session_index, vision_fault=True)
    trained = client.post(
        "/api/v1/simulations/train",
        json={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert trained.status_code == 200
    selected = client.put(
        "/api/v1/device-model",
        json={"device_id": DEVICE_ID, "model_id": CUSTOM_MODEL_ID},
    )
    assert selected.status_code == 200

    for seq, distance_mm in enumerate((890, 875, 850, 825, 800), start=100):
        assert (
            client.post(
                "/api/v1/telemetry",
                json=live_telemetry_payload(seq, distance_mm=distance_mm),
            ).status_code
            == 201
        )
    assert (
        client.get("/api/v1/risk", params={"device_id": DEVICE_ID}).status_code == 200
    )

    # Reboot: seq and uptime fall even though the physical sample remains valid.
    assert (
        client.post(
            "/api/v1/telemetry",
            json=live_telemetry_payload(1, distance_mm=790),
        ).status_code
        == 201
    )
    assert (
        client.get("/api/v1/risk", params={"device_id": DEVICE_ID}).status_code == 503
    )
    for seq in range(2, 6):
        assert (
            client.post(
                "/api/v1/telemetry",
                json=live_telemetry_payload(seq, distance_mm=790 - seq * 5),
            ).status_code
            == 201
        )
    assert (
        client.get("/api/v1/risk", params={"device_id": DEVICE_ID}).status_code == 200
    )

    # A collection session is a distinct live epoch from normal telemetry.
    live_session_id = "sim_live_epoch_001"
    assert (
        client.post(
            "/api/v1/simulations/sessions",
            json={
                "device_id": DEVICE_ID,
                "name": "Live epoch boundary",
                "session_id": live_session_id,
            },
        ).status_code
        == 201
    )
    for seq in range(1, 6):
        payload = telemetry_payload(
            live_session_id,
            seq,
            session_index=20,
            vision_fault=True,
        )
        assert client.post("/api/v1/telemetry", json=payload).status_code == 201
    assert (
        client.get("/api/v1/risk", params={"device_id": DEVICE_ID}).status_code == 200
    )
    assert (
        client.post(
            f"/api/v1/simulations/sessions/{live_session_id}/stop",
            json={"device_id": DEVICE_ID},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/telemetry",
            json=live_telemetry_payload(6, distance_mm=755),
        ).status_code
        == 201
    )
    assert (
        client.get("/api/v1/risk", params={"device_id": DEVICE_ID}).status_code == 503
    )
    for seq in range(7, 11):
        assert (
            client.post(
                "/api/v1/telemetry",
                json=live_telemetry_payload(seq, distance_mm=785 - seq * 5),
            ).status_code
            == 201
        )
    assert (
        client.get("/api/v1/risk", params={"device_id": DEVICE_ID}).status_code == 200
    )

    # One new row must not be combined with otherwise valid but stale history.
    old_timestamp = (
        datetime.now(timezone.utc)
        - timedelta(seconds=CUSTOM_WINDOW_MAX_SAMPLE_AGE_SECONDS + 5)
    ).isoformat()
    with connect() as connection:
        connection.execute(
            """
            UPDATE telemetry
            SET received_at = ?
            WHERE device_id = ? AND simulation_session_id IS NULL
            """,
            (old_timestamp, DEVICE_ID),
        )
    assert (
        client.post(
            "/api/v1/telemetry",
            json=live_telemetry_payload(11, distance_mm=730),
        ).status_code
        == 201
    )
    assert (
        client.get("/api/v1/risk", params={"device_id": DEVICE_ID}).status_code == 503
    )
    for seq in range(12, 16):
        assert (
            client.post(
                "/api/v1/telemetry",
                json=live_telemetry_payload(seq, distance_mm=785 - seq * 5),
            ).status_code
            == 201
        )
    assert (
        client.get("/api/v1/risk", params={"device_id": DEVICE_ID}).status_code == 200
    )


def test_training_readiness_and_training_exclude_invalid_ultrasonic_samples(
    internal_client,
):
    client, _artifact_path = internal_client
    for session_index in range(1, 3):
        session_id = f"sim_quality_{session_index:03d}"
        assert (
            client.put(
                "/api/v1/simulations/device-scenario",
                json=scenario_payload(session_index),
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/simulations/sessions",
                json={
                    "device_id": DEVICE_ID,
                    "name": f"Quality run {session_index}",
                    "session_id": session_id,
                },
            ).status_code
            == 201
        )
        for seq in range(1, 17):
            sample = telemetry_payload(session_id, seq, session_index=session_index)
            if seq == 7:
                sample.update({"distance_mm": 0, "health_flags": 0})
            assert client.post("/api/v1/telemetry", json=sample).status_code == 201
        assert (
            client.post(
                f"/api/v1/simulations/sessions/{session_id}/stop",
                json={"device_id": DEVICE_ID},
            ).status_code
            == 200
        )
        for start_seq, end_seq, label in (
            (1, 6, "safe"),
            (8, 16, "danger"),
        ):
            assert (
                client.put(
                    "/api/v1/simulations/labels",
                    json={
                        "session_id": session_id,
                        "device_id": DEVICE_ID,
                        "start_seq": start_seq,
                        "end_seq": end_seq,
                        "label": label,
                        "note": "manual operator label",
                        "version": 1,
                    },
                ).status_code
                == 200
            )

    readiness = client.get(
        "/api/v1/simulations/training-readiness",
        params={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert readiness.status_code == 200
    quality = readiness.json()["data_quality"]
    assert readiness.json()["ready"] is True
    assert quality["collected_sample_count"] == 32
    assert quality["valid_ultrasonic_samples"] == 30
    assert quality["excluded_invalid_ultrasonic_samples"] == 2
    assert quality["label_counts"] == {"safe": 12, "danger": 18, "unknown": 0}
    assert quality["eligible_class_counts"] == {
        "safe": 4,
        "danger": 10,
        "unknown": 0,
    }
    assert quality["label_coverage"] == pytest.approx(1.0)

    trained = client.post(
        "/api/v1/simulations/train",
        json={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert trained.status_code == 200
    assert trained.json()["sample_count"] == 30
    assert trained.json()["labelled_sample_count"] == 14
    assert trained.json()["excluded_unknown_samples"] == 0
    assert trained.json()["excluded_warmup_samples"] == 16
    assert trained.json()["excluded_invalid_ultrasonic_samples"] == 2
    collection_sessions = trained.json()["source_manifest"]["collection_sessions"]
    assert [session["valid_run_count"] for session in collection_sessions] == [2, 2]


def test_overview_timeline_and_readiness_share_ultrasonic_distance_bounds(
    internal_client,
):
    client, _artifact_path = internal_client
    session_id = "sim_bounds_001"
    assert (
        client.put(
            "/api/v1/simulations/device-scenario",
            json=scenario_payload(30),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/simulations/sessions",
            json={
                "device_id": DEVICE_ID,
                "name": "Sensor boundary run",
                "session_id": session_id,
            },
        ).status_code
        == 201
    )
    for seq, distance_mm in enumerate((19, 20, 4_000, 4_001), start=1):
        sample = telemetry_payload(session_id, seq, vision_fault=True)
        sample["distance_mm"] = distance_mm
        sample["health_flags"] = 1
        sample["alarm_level"] = 4
        assert client.post("/api/v1/telemetry", json=sample).status_code == 201
    assert (
        client.post(
            f"/api/v1/simulations/sessions/{session_id}/stop",
            json={"device_id": DEVICE_ID},
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/v1/simulations/labels",
            json={
                "session_id": session_id,
                "device_id": DEVICE_ID,
                "start_seq": 1,
                "end_seq": 4,
                "label": "safe",
                "note": "boundary audit",
                "version": 1,
            },
        ).status_code
        == 200
    )

    overview = client.get(
        "/api/v1/simulations/overview",
        params={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert overview.status_code == 200
    assert overview.json()["totals"]["valid_ultrasonic_samples"] == 2
    assert overview.json()["totals"]["invalid_ultrasonic_samples"] == 2

    timeline = client.get(
        f"/api/v1/simulations/sessions/{session_id}/timeline",
        params={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert timeline.status_code == 200
    assert [point["valid_ultrasonic"] for point in timeline.json()["points"]] == [
        False,
        True,
        True,
        False,
    ]

    readiness = client.get(
        "/api/v1/simulations/training-readiness",
        params={"device_id": DEVICE_ID, "label_version": 1},
    )
    assert readiness.status_code == 200
    quality = readiness.json()["data_quality"]
    assert quality["valid_ultrasonic_samples"] == 2
    assert quality["excluded_invalid_ultrasonic_samples"] == 2
    assert quality["label_counts"] == {"safe": 2, "danger": 0, "unknown": 0}


def test_gateway_exposes_only_authenticated_device_simulation_controls(
    gateway_client: TestClient,
):
    client = gateway_client
    session_id = "sim_gateway_001"

    assert (
        client.get("/api/v1/models", params={"device_id": DEVICE_ID}).status_code == 401
    )
    assert (
        client.post(
            "/api/v1/simulations/sessions",
            json={"device_id": DEVICE_ID, "name": "Gateway run"},
        ).status_code
        == 401
    )

    catalog = client.get(
        "/api/v1/models",
        params={"device_id": DEVICE_ID},
        headers=AUTH_HEADERS,
    )
    assert catalog.status_code == 200
    assert catalog.json()["selected_model_id"] == DEFAULT_MODEL_ID

    selection = client.put(
        "/api/v1/device-model",
        json={"device_id": DEVICE_ID, "model_id": DEFAULT_MODEL_ID},
        headers=AUTH_HEADERS,
    )
    assert selection.status_code == 200
    assert selection.json()["selected_model_id"] == DEFAULT_MODEL_ID

    upsert_device_simulation_scenario(scenario_payload(40))

    started = client.post(
        "/api/v1/simulations/sessions",
        json={
            "device_id": DEVICE_ID,
            "name": "Gateway run",
            "session_id": session_id,
        },
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 201
    assert started.json()["state"] == "active"

    active = client.get(
        "/api/v1/simulations/sessions/active",
        params={"device_id": DEVICE_ID},
        headers=AUTH_HEADERS,
    )
    assert active.status_code == 200
    assert active.json()["session_id"] == session_id

    telemetry = client.post(
        "/api/v1/telemetry",
        json=telemetry_payload(session_id, 1),
        headers=AUTH_HEADERS,
    )
    assert telemetry.status_code == 201
    assert telemetry.json()["simulation_session_id"] == session_id

    stopped = client.post(
        f"/api/v1/simulations/sessions/{session_id}/stop",
        json={"device_id": DEVICE_ID},
        headers=AUTH_HEADERS,
    )
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "completed"

    hidden_requests = (
        ("GET", "/"),
        ("GET", "/api/v1/simulations/sessions"),
        ("GET", "/api/v1/simulations/overview"),
        ("GET", "/api/v1/simulations/training-readiness"),
        ("GET", "/api/v1/simulations/model"),
        ("GET", "/api/v1/simulations/device-scenario"),
        ("PUT", "/api/v1/simulations/device-scenario"),
        ("DELETE", "/api/v1/simulations/device-scenario"),
        ("GET", f"/api/v1/simulations/sessions/{session_id}/samples"),
        ("GET", f"/api/v1/simulations/sessions/{session_id}/scenario"),
        ("GET", f"/api/v1/simulations/sessions/{session_id}/timeline"),
        ("GET", f"/api/v1/simulations/sessions/{session_id}/labels"),
        ("PUT", "/api/v1/simulations/labels"),
        ("POST", "/api/v1/simulations/train"),
    )
    for method, path in hidden_requests:
        response = client.request(
            method,
            path,
            headers=AUTH_HEADERS,
            json={} if method in {"PUT", "POST"} else None,
        )
        assert response.status_code == 404, (method, path, response.text)
