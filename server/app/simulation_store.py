"""SQLite persistence for user-recorded, synthetic water-level sessions."""

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping
from datetime import timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .database import _utc_now_text, connect
from .experiment_store import (
    OfficialConflictError,
    freeze_sensor_profile_for_session,
)
from .model_registry import OFFICIAL_MODEL_ID, get_selected_model_id
from .schemas import DeviceId
from .simulation_artifacts import (
    SIMULATION_ARTIFACT_LOCK,
    SimulationArtifactVerificationError,
    referencing_training_artifact,
)
from .simulation_schemas import (
    SIMULATION_DATA_WARNING,
    PositiveVersion,
    SimulationLabelUpsertIn,
    SimulationSampleData,
    SimulationScenarioUpsertIn,
    SimulationSessionId,
    SimulationSessionStartIn,
)
from .telemetry_quality import (
    ULTRASONIC_HEALTH_BIT,
    ULTRASONIC_MAX_DISTANCE_MM,
    ULTRASONIC_MIN_DISTANCE_MM,
    ultrasonic_sample_is_valid,
)


class SimulationStoreError(RuntimeError):
    """Base error suitable for translation to an HTTP response."""


class SimulationNotFoundError(SimulationStoreError):
    pass


class SimulationConflictError(SimulationStoreError):
    pass


class SimulationValidationError(SimulationStoreError):
    pass


_SESSION_ID_ADAPTER = TypeAdapter(SimulationSessionId)
_DEVICE_ID_ADAPTER = TypeAdapter(DeviceId)
_VERSION_ADAPTER = TypeAdapter(PositiveVersion)
_SAMPLE_FIELDS = tuple(SimulationSampleData.model_fields)
_SCENARIO_SCHEMA = "coastwatch.operator-simulated-coast"
_SCENARIO_SCHEMA_VERSION = 1


def _validation_error(exc: ValidationError) -> SimulationValidationError:
    return SimulationValidationError(str(exc))


def _validated_session_id(value: str) -> str:
    try:
        return _SESSION_ID_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise _validation_error(exc) from exc


def _validated_device_id(value: str) -> str:
    try:
        return _DEVICE_ID_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise _validation_error(exc) from exc


def _validated_version(value: int) -> int:
    try:
        return _VERSION_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise _validation_error(exc) from exc


def _session_row(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM simulation_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise SimulationNotFoundError(f"simulation session {session_id} not found")
    return row


def _owned_session_row(
    connection: sqlite3.Connection, session_id: str, device_id: str
) -> sqlite3.Row:
    row = _session_row(connection, session_id)
    if str(row["device_id"]) != device_id:
        raise SimulationConflictError(
            f"simulation session {session_id} belongs to a different device"
        )
    return row


def _session_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["synthetic"] = bool(result["synthetic"])
    result["sample_count"] = int(result.get("sample_count", 0))
    return result


def _sample_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["person_detected"] = bool(result["person_detected"])
    return result


def _label_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _scenario_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result.update(
        {
            "data_kind": "operator_supplied_simulation",
            "warning": SIMULATION_DATA_WARNING,
        }
    )
    return result


def _scenario_values(scenario: SimulationScenarioUpsertIn) -> dict[str, Any]:
    values = scenario.model_dump(mode="json")
    simulated_at = scenario.simulated_at.astimezone(timezone.utc)
    values["simulated_at"] = simulated_at.isoformat().replace("+00:00", "Z")
    values.update(_scenario_time_features(simulated_at))
    values.update(
        {
            "scenario_schema": _SCENARIO_SCHEMA,
            "scenario_schema_version": _SCENARIO_SCHEMA_VERSION,
        }
    )
    canonical = json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    values["scenario_hash"] = hashlib.sha256(canonical).hexdigest()
    values["updated_at"] = _utc_now_text()
    return values


def _scenario_time_features(simulated_at: Any) -> dict[str, float]:
    timestamp = simulated_at.astimezone(timezone.utc)
    hour_angle = 2.0 * math.pi * (timestamp.hour + timestamp.minute / 60.0) / 24.0
    days_in_year = (
        366.0
        if (
            timestamp.year % 4 == 0
            and (timestamp.year % 100 != 0 or timestamp.year % 400 == 0)
        )
        else 365.0
    )
    day_angle = 2.0 * math.pi * (timestamp.timetuple().tm_yday - 1) / days_in_year
    return {
        "sim_hour_sin": math.sin(hour_angle),
        "sim_hour_cos": math.cos(hour_angle),
        "sim_day_of_year_sin": math.sin(day_angle),
        "sim_day_of_year_cos": math.cos(day_angle),
    }


def _session_summary_with_connection(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    *,
    version: int,
) -> dict[str, Any]:
    """Resolve labels at sample level and return one visualization summary."""

    aggregate = connection.execute(
        """
        WITH resolved AS (
            SELECT
                samples.*,
                COALESCE((
                    SELECT labels.label
                    FROM simulation_labels AS labels
                    WHERE labels.session_id = samples.session_id
                      AND labels.version = ?
                      AND samples.seq BETWEEN labels.start_seq AND labels.end_seq
                    ORDER BY labels.updated_at DESC, labels.id DESC
                    LIMIT 1
                ), 'unknown') AS resolved_label
            FROM simulation_samples AS samples
            WHERE samples.session_id = ?
        )
        SELECT
            COUNT(*) AS sample_count,
            COALESCE(SUM(
                CASE WHEN distance_mm BETWEEN ? AND ?
                      AND (health_flags & ?) <> 0 THEN 1 ELSE 0 END
            ), 0) AS valid_ultrasonic_samples,
            COALESCE(SUM(
                CASE WHEN distance_mm < ? OR distance_mm > ?
                      OR (health_flags & ?) = 0 THEN 1 ELSE 0 END
            ), 0) AS invalid_ultrasonic_samples,
            COALESCE(SUM(CASE WHEN resolved_label = 'safe' THEN 1 ELSE 0 END), 0)
                AS safe_samples,
            COALESCE(SUM(CASE WHEN resolved_label = 'danger' THEN 1 ELSE 0 END), 0)
                AS danger_samples,
            COALESCE(SUM(CASE WHEN resolved_label = 'unknown' THEN 1 ELSE 0 END), 0)
                AS unknown_samples,
            MIN(seq) AS first_seq,
            MAX(seq) AS last_seq,
            MIN(received_at) AS first_received_at,
            MAX(received_at) AS last_received_at,
            MIN(distance_mm) AS distance_min_mm,
            MAX(distance_mm) AS distance_max_mm,
            MIN(water_rise_mm) AS water_rise_min_mm,
            MAX(water_rise_mm) AS water_rise_max_mm
        FROM resolved
        """,
        (
            version,
            str(session["session_id"]),
            ULTRASONIC_MIN_DISTANCE_MM,
            ULTRASONIC_MAX_DISTANCE_MM,
            ULTRASONIC_HEALTH_BIT,
            ULTRASONIC_MIN_DISTANCE_MM,
            ULTRASONIC_MAX_DISTANCE_MM,
            ULTRASONIC_HEALTH_BIT,
        ),
    ).fetchone()
    if aggregate is None:  # pragma: no cover - aggregate queries always return a row
        raise RuntimeError("simulation summary query did not return a row")

    result = _session_to_dict(session)
    sample_count = int(aggregate["sample_count"])
    safe_count = int(aggregate["safe_samples"])
    danger_count = int(aggregate["danger_samples"])
    labelled_count = safe_count + danger_count
    result.update(
        {
            "sample_count": sample_count,
            "valid_ultrasonic_samples": int(aggregate["valid_ultrasonic_samples"]),
            "invalid_ultrasonic_samples": int(aggregate["invalid_ultrasonic_samples"]),
            "label_counts": {
                "safe": safe_count,
                "danger": danger_count,
                "unknown": int(aggregate["unknown_samples"]),
            },
            "labelled_sample_count": labelled_count,
            "label_coverage": labelled_count / sample_count if sample_count else 0.0,
            "first_seq": aggregate["first_seq"],
            "last_seq": aggregate["last_seq"],
            "first_received_at": aggregate["first_received_at"],
            "last_received_at": aggregate["last_received_at"],
            "distance_min_mm": aggregate["distance_min_mm"],
            "distance_max_mm": aggregate["distance_max_mm"],
            "water_rise_min_mm": aggregate["water_rise_min_mm"],
            "water_rise_max_mm": aggregate["water_rise_max_mm"],
        }
    )
    return result


def upsert_device_simulation_scenario(
    payload: Mapping[str, Any] | SimulationScenarioUpsertIn,
) -> dict[str, Any]:
    """Save the explicit scenario used for the device's next/current run."""

    try:
        scenario = (
            payload
            if isinstance(payload, SimulationScenarioUpsertIn)
            else SimulationScenarioUpsertIn.model_validate(payload)
        )
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    values = _scenario_values(scenario)
    with connect() as connection:
        active = connection.execute(
            """
            SELECT session_id FROM simulation_sessions
            WHERE device_id = ? AND state = 'active'
            """,
            (scenario.device_id,),
        ).fetchone()
        if active is not None:
            raise SimulationConflictError(
                "the device scenario cannot change while collection session "
                f"{active['session_id']} is active"
            )
        connection.execute(
            """
            INSERT INTO simulation_device_scenarios (
                device_id, scenario_name, simulated_at,
                sim_air_temperature_c, sim_humidity_percent,
                sim_wind_speed_kmh, sim_wave_height_m, sim_wave_period_s,
                sim_water_temperature_c, sim_sea_level_height_m,
                sim_ocean_current_velocity_kmh, sim_hour_sin, sim_hour_cos,
                sim_day_of_year_sin, sim_day_of_year_cos, sim_latitude,
                sim_longitude, note, scenario_schema,
                scenario_schema_version, scenario_hash, updated_at
            ) VALUES (
                :device_id, :scenario_name, :simulated_at,
                :sim_air_temperature_c, :sim_humidity_percent,
                :sim_wind_speed_kmh, :sim_wave_height_m, :sim_wave_period_s,
                :sim_water_temperature_c, :sim_sea_level_height_m,
                :sim_ocean_current_velocity_kmh, :sim_hour_sin, :sim_hour_cos,
                :sim_day_of_year_sin, :sim_day_of_year_cos, :sim_latitude,
                :sim_longitude, :note, :scenario_schema,
                :scenario_schema_version, :scenario_hash, :updated_at
            )
            ON CONFLICT(device_id) DO UPDATE SET
                scenario_name = excluded.scenario_name,
                simulated_at = excluded.simulated_at,
                sim_air_temperature_c = excluded.sim_air_temperature_c,
                sim_humidity_percent = excluded.sim_humidity_percent,
                sim_wind_speed_kmh = excluded.sim_wind_speed_kmh,
                sim_wave_height_m = excluded.sim_wave_height_m,
                sim_wave_period_s = excluded.sim_wave_period_s,
                sim_water_temperature_c = excluded.sim_water_temperature_c,
                sim_sea_level_height_m = excluded.sim_sea_level_height_m,
                sim_ocean_current_velocity_kmh =
                    excluded.sim_ocean_current_velocity_kmh,
                sim_hour_sin = excluded.sim_hour_sin,
                sim_hour_cos = excluded.sim_hour_cos,
                sim_day_of_year_sin = excluded.sim_day_of_year_sin,
                sim_day_of_year_cos = excluded.sim_day_of_year_cos,
                sim_latitude = excluded.sim_latitude,
                sim_longitude = excluded.sim_longitude,
                note = excluded.note,
                scenario_schema = excluded.scenario_schema,
                scenario_schema_version = excluded.scenario_schema_version,
                scenario_hash = excluded.scenario_hash,
                updated_at = excluded.updated_at
            """,
            values,
        )
        row = connection.execute(
            "SELECT * FROM simulation_device_scenarios WHERE device_id = ?",
            (scenario.device_id,),
        ).fetchone()
    if row is None:  # pragma: no cover - defensive database invariant
        raise RuntimeError("device simulation scenario upsert did not return a row")
    return _scenario_to_dict(row)


def get_device_simulation_scenario(device_id: str) -> dict[str, Any] | None:
    device_id = _validated_device_id(device_id)
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM simulation_device_scenarios WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    return _scenario_to_dict(row) if row is not None else None


def delete_device_simulation_scenario(device_id: str) -> None:
    device_id = _validated_device_id(device_id)
    with connect() as connection:
        active = connection.execute(
            """
            SELECT session_id FROM simulation_sessions
            WHERE device_id = ? AND state = 'active'
            """,
            (device_id,),
        ).fetchone()
        if active is not None:
            raise SimulationConflictError(
                "the device scenario cannot be cleared while collection session "
                f"{active['session_id']} is active"
            )
        deleted = connection.execute(
            "DELETE FROM simulation_device_scenarios WHERE device_id = ?",
            (device_id,),
        ).rowcount
        if deleted == 0:
            raise SimulationNotFoundError(
                f"device {device_id} has no active simulation scenario"
            )


def get_simulation_scenario(session_id: str, device_id: str) -> dict[str, Any] | None:
    session_id = _validated_session_id(session_id)
    device_id = _validated_device_id(device_id)
    with connect() as connection:
        _owned_session_row(connection, session_id, device_id)
        row = connection.execute(
            "SELECT * FROM simulation_scenarios WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return _scenario_to_dict(row) if row is not None else None


def start_simulation_session(
    payload: Mapping[str, Any] | SimulationSessionStartIn,
) -> dict[str, Any]:
    """Start the sole active simulation session for a device."""

    try:
        request = (
            payload
            if isinstance(payload, SimulationSessionStartIn)
            else SimulationSessionStartIn.model_validate(payload)
        )
    except ValidationError as exc:
        raise _validation_error(exc) from exc

    session_id = request.session_id or f"sim_{uuid4().hex}"
    now = _utc_now_text()
    resolved_model_id = get_selected_model_id(request.device_id)
    with connect() as connection:
        active = connection.execute(
            """
            SELECT session_id FROM simulation_sessions
            WHERE device_id = ? AND state = 'active'
            """,
            (request.device_id,),
        ).fetchone()
        if active is not None:
            raise SimulationConflictError(
                f"device {request.device_id} already has active session "
                f"{active['session_id']}"
            )
        if connection.execute(
            "SELECT 1 FROM simulation_sessions WHERE session_id = ?", (session_id,)
        ).fetchone():
            raise SimulationConflictError(
                f"simulation session {session_id} already exists"
            )
        official_external_test = resolved_model_id == OFFICIAL_MODEL_ID
        scenario = connection.execute(
            "SELECT * FROM simulation_device_scenarios WHERE device_id = ?",
            (request.device_id,),
        ).fetchone()
        if scenario is None and not official_external_test:
            raise SimulationConflictError(
                "an operator-supplied device scenario must be saved before collection starts"
            )
        try:
            connection.execute(
                """
                INSERT INTO simulation_sessions (
                    session_id, device_id, name, state, started_at, ended_at,
                    baseline_distance_mm, synthetic
                ) VALUES (?, ?, ?, 'active', ?, NULL, NULL, 1)
                """,
                (session_id, request.device_id, request.name, now),
            )
            if official_external_test:
                freeze_sensor_profile_for_session(
                    connection,
                    session_id=session_id,
                    device_id=request.device_id,
                    frozen_at=now,
                )
            else:
                connection.execute(
                    """
                    INSERT INTO simulation_scenarios (
                        session_id, device_id, scenario_name, simulated_at,
                        sim_air_temperature_c, sim_humidity_percent,
                        sim_wind_speed_kmh, sim_wave_height_m, sim_wave_period_s,
                        sim_water_temperature_c, sim_sea_level_height_m,
                        sim_ocean_current_velocity_kmh, sim_hour_sin, sim_hour_cos,
                        sim_day_of_year_sin, sim_day_of_year_cos, sim_latitude,
                        sim_longitude, note, scenario_schema,
                        scenario_schema_version, scenario_hash, updated_at
                    )
                    SELECT
                        ?, device_id, scenario_name, simulated_at,
                        sim_air_temperature_c, sim_humidity_percent,
                        sim_wind_speed_kmh, sim_wave_height_m, sim_wave_period_s,
                        sim_water_temperature_c, sim_sea_level_height_m,
                        sim_ocean_current_velocity_kmh, sim_hour_sin, sim_hour_cos,
                        sim_day_of_year_sin, sim_day_of_year_cos, sim_latitude,
                        sim_longitude, note, scenario_schema,
                        scenario_schema_version, scenario_hash, updated_at
                    FROM simulation_device_scenarios
                    WHERE device_id = ?
                    """,
                    (session_id, request.device_id),
                )
        except OfficialConflictError as exc:
            raise SimulationConflictError(str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise SimulationConflictError(str(exc)) from exc
        row = connection.execute(
            """
            SELECT sessions.*, 0 AS sample_count
            FROM simulation_sessions AS sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    if row is None:  # pragma: no cover - defensive database invariant
        raise RuntimeError("simulation session insert did not return a row")
    return _session_to_dict(row)


def stop_simulation_session(session_id: str, device_id: str) -> dict[str, Any]:
    """Complete an active session; completed sessions are immutable to samples."""

    session_id = _validated_session_id(session_id)
    device_id = _validated_device_id(device_id)
    now = _utc_now_text()
    with connect() as connection:
        row = _owned_session_row(connection, session_id, device_id)
        if str(row["state"]) != "active":
            raise SimulationConflictError(
                f"simulation session {session_id} is already completed"
            )
        connection.execute(
            """
            UPDATE simulation_sessions
            SET state = 'completed', ended_at = ?
            WHERE session_id = ? AND device_id = ? AND state = 'active'
            """,
            (now, session_id, device_id),
        )
        result = connection.execute(
            """
            SELECT sessions.*, COUNT(samples.id) AS sample_count
            FROM simulation_sessions AS sessions
            LEFT JOIN simulation_samples AS samples
                ON samples.session_id = sessions.session_id
            WHERE sessions.session_id = ?
            GROUP BY sessions.session_id
            """,
            (session_id,),
        ).fetchone()
    if result is None:  # pragma: no cover - defensive database invariant
        raise RuntimeError("simulation session update did not return a row")
    return _session_to_dict(result)


def delete_simulation_session(session_id: str, device_id: str) -> dict[str, Any]:
    """Delete one unused completed dataset while retaining telemetry audit rows.

    Training and deletion share a cross-process artifact lock.  The database
    write lock then keeps labels/samples stable from the artifact provenance
    scan through the final delete commit.
    """

    session_id = _validated_session_id(session_id)
    device_id = _validated_device_id(device_id)
    with SIMULATION_ARTIFACT_LOCK, connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = connection.execute(
            """
                SELECT * FROM simulation_sessions
                WHERE session_id = ? AND device_id = ?
                """,
            (session_id, device_id),
        ).fetchone()
        # Do not disclose that a valid session ID belongs to another device.
        if session is None:
            raise SimulationNotFoundError(f"simulation session {session_id} not found")
        if str(session["state"]) != "completed":
            raise SimulationConflictError(
                f"active simulation session {session_id} cannot be deleted"
            )

        try:
            artifact_name = referencing_training_artifact(session_id)
        except SimulationArtifactVerificationError as exc:
            raise SimulationConflictError(str(exc)) from exc
        if artifact_name is not None:
            raise SimulationConflictError(
                f"simulation session {session_id} is referenced by training "
                f"artifact {artifact_name}"
            )

        external_test = connection.execute(
            """
            SELECT run_id FROM sensor_test_runs
            WHERE session_id = ? AND device_id = ?
            ORDER BY created_at LIMIT 1
            """,
            (session_id, device_id),
        ).fetchone()
        if external_test is not None:
            raise SimulationConflictError(
                f"simulation session {session_id} is referenced by sensor "
                f"external-test run {external_test['run_id']}"
            )

        calibration_profile = connection.execute(
            """
            SELECT profile_id FROM sensor_test_profiles
            WHERE calibration_session_id = ? AND device_id = ?
            ORDER BY created_at LIMIT 1
            """,
            (session_id, device_id),
        ).fetchone()
        if calibration_profile is not None:
            raise SimulationConflictError(
                f"simulation session {session_id} is referenced as calibration "
                f"provenance by sensor profile {calibration_profile['profile_id']}"
            )

        calibration_snapshot = connection.execute(
            """
            SELECT session_id FROM sensor_test_session_snapshots
            WHERE calibration_session_id = ? AND device_id = ?
            ORDER BY frozen_at LIMIT 1
            """,
            (session_id, device_id),
        ).fetchone()
        if calibration_snapshot is not None:
            raise SimulationConflictError(
                f"simulation session {session_id} is referenced as calibration "
                "provenance by frozen sensor profile snapshot for session "
                f"{calibration_snapshot['session_id']}"
            )

        detached_telemetry_count = connection.execute(
            """
                UPDATE telemetry
                SET simulation_session_id = NULL
                WHERE simulation_session_id = ?
                """,
            (session_id,),
        ).rowcount
        deleted_labels = connection.execute(
            "DELETE FROM simulation_labels WHERE session_id = ?",
            (session_id,),
        ).rowcount
        deleted_samples = connection.execute(
            "DELETE FROM simulation_samples WHERE session_id = ?",
            (session_id,),
        ).rowcount
        deleted_scenarios = connection.execute(
            "DELETE FROM simulation_scenarios WHERE session_id = ?",
            (session_id,),
        ).rowcount
        deleted_sessions = connection.execute(
            """
                DELETE FROM simulation_sessions
                WHERE session_id = ? AND device_id = ? AND state = 'completed'
                """,
            (session_id, device_id),
        ).rowcount
        if deleted_sessions != 1:  # pragma: no cover - write-lock invariant
            raise RuntimeError("simulation session delete lost its target row")

    return {
        "status": "deleted",
        "session_id": session_id,
        "device_id": device_id,
        "deleted_counts": {
            "sessions": deleted_sessions,
            "samples": deleted_samples,
            "labels": deleted_labels,
            "scenario_snapshots": deleted_scenarios,
        },
        "detached_telemetry_count": detached_telemetry_count,
    }


def get_simulation_session(
    session_id: str, device_id: str | None = None
) -> dict[str, Any] | None:
    session_id = _validated_session_id(session_id)
    if device_id is not None:
        device_id = _validated_device_id(device_id)
    with connect() as connection:
        row = connection.execute(
            """
            SELECT sessions.*, COUNT(samples.id) AS sample_count
            FROM simulation_sessions AS sessions
            LEFT JOIN simulation_samples AS samples
                ON samples.session_id = sessions.session_id
            WHERE sessions.session_id = ?
            GROUP BY sessions.session_id
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    if device_id is not None and str(row["device_id"]) != device_id:
        raise SimulationConflictError(
            f"simulation session {session_id} belongs to a different device"
        )
    return _session_to_dict(row)


def get_active_simulation_session(device_id: str) -> dict[str, Any] | None:
    """Return the active session so a rebooted ESP can resume safely."""

    device_id = _validated_device_id(device_id)
    with connect() as connection:
        row = connection.execute(
            """
            SELECT sessions.*, COUNT(samples.id) AS sample_count
            FROM simulation_sessions AS sessions
            LEFT JOIN simulation_samples AS samples
                ON samples.session_id = sessions.session_id
            WHERE sessions.device_id = ? AND sessions.state = 'active'
            GROUP BY sessions.session_id
            """,
            (device_id,),
        ).fetchone()
    return _session_to_dict(row) if row is not None else None


def list_simulation_sessions(
    device_id: str | None = None,
    state: Literal["active", "completed"] | None = None,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if device_id is not None:
        device_id = _validated_device_id(device_id)
    if state not in (None, "active", "completed"):
        raise SimulationValidationError("state must be active or completed")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise SimulationValidationError("limit must be an integer from 1 to 500")

    clauses: list[str] = []
    parameters: list[Any] = []
    if device_id is not None:
        clauses.append("sessions.device_id = ?")
        parameters.append(device_id)
    if state is not None:
        clauses.append("sessions.state = ?")
        parameters.append(state)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    parameters.append(limit)
    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT sessions.*, COUNT(samples.id) AS sample_count
            FROM simulation_sessions AS sessions
            LEFT JOIN simulation_samples AS samples
                ON samples.session_id = sessions.session_id
            {where}
            GROUP BY sessions.session_id
            ORDER BY sessions.started_at DESC, sessions.session_id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    return [_session_to_dict(row) for row in rows]


def list_simulation_session_summaries(
    device_id: str,
    *,
    version: int = 1,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return label-aware summaries used by the internal visualization UI."""

    device_id = _validated_device_id(device_id)
    version = _validated_version(version)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise SimulationValidationError("limit must be an integer from 1 to 500")
    with connect() as connection:
        sessions = connection.execute(
            """
            SELECT * FROM simulation_sessions
            WHERE device_id = ?
            ORDER BY started_at DESC, session_id DESC
            LIMIT ?
            """,
            (device_id, limit),
        ).fetchall()
        return [
            _session_summary_with_connection(connection, session, version=version)
            for session in sessions
        ]


def get_simulation_session_summary(
    session_id: str,
    device_id: str,
    *,
    version: int = 1,
) -> dict[str, Any]:
    session_id = _validated_session_id(session_id)
    device_id = _validated_device_id(device_id)
    version = _validated_version(version)
    with connect() as connection:
        session = _owned_session_row(connection, session_id, device_id)
        return _session_summary_with_connection(connection, session, version=version)


def _add_sample_with_connection(
    connection: sqlite3.Connection,
    session_id: str,
    payload: Mapping[str, Any],
    *,
    telemetry_id: int | None = None,
    received_at: str | None = None,
) -> dict[str, Any]:
    """Internal transaction-aware primitive shared with insert_telemetry."""

    session_id = _validated_session_id(session_id)
    sample_payload = {
        field: payload[field] for field in _SAMPLE_FIELDS if field in payload
    }
    try:
        sample = SimulationSampleData.model_validate(sample_payload)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    session = _owned_session_row(connection, session_id, sample.device_id)
    if str(session["state"]) != "active":
        raise SimulationConflictError(
            f"cannot append to completed simulation session {session_id}"
        )
    if connection.execute(
        "SELECT 1 FROM simulation_samples WHERE session_id = ? AND seq = ?",
        (session_id, sample.seq),
    ).fetchone():
        raise SimulationConflictError(
            f"sample seq {sample.seq} already exists in session {session_id}"
        )

    if session["baseline_distance_mm"] is None and ultrasonic_sample_is_valid(
        sample.model_dump()
    ):
        connection.execute(
            """
            UPDATE simulation_sessions
            SET baseline_distance_mm = ?
            WHERE session_id = ? AND baseline_distance_mm IS NULL
            """,
            (sample.distance_mm, session_id),
        )

    values = sample.model_dump()
    values.update(
        {
            "session_id": session_id,
            "telemetry_id": telemetry_id,
            "person_detected": 1 if sample.person_detected else 0,
            "received_at": received_at or _utc_now_text(),
        }
    )
    try:
        cursor = connection.execute(
            """
            INSERT INTO simulation_samples (
                session_id, device_id, telemetry_id, seq, uptime_ms,
                distance_mm, water_rise_mm, rise_rate_mm_s, person_detected,
                alarm_level, health_flags, wifi_rssi, received_at
            ) VALUES (
                :session_id, :device_id, :telemetry_id, :seq, :uptime_ms,
                :distance_mm, :water_rise_mm, :rise_rate_mm_s,
                :person_detected, :alarm_level, :health_flags, :wifi_rssi,
                :received_at
            )
            """,
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise SimulationConflictError(str(exc)) from exc
    row = connection.execute(
        "SELECT * FROM simulation_samples WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    if row is None:  # pragma: no cover - defensive database invariant
        raise RuntimeError("simulation sample insert did not return a row")
    return _sample_to_dict(row)


def add_simulation_sample(
    session_id: str,
    payload: Mapping[str, Any] | SimulationSampleData,
    *,
    telemetry_id: int | None = None,
) -> dict[str, Any]:
    values = (
        payload.model_dump() if isinstance(payload, SimulationSampleData) else payload
    )
    with connect() as connection:
        return _add_sample_with_connection(
            connection, session_id, values, telemetry_id=telemetry_id
        )


def list_simulation_samples(
    session_id: str,
    device_id: str,
    *,
    after_seq: int | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    session_id = _validated_session_id(session_id)
    device_id = _validated_device_id(device_id)
    if after_seq is not None and (
        isinstance(after_seq, bool)
        or not isinstance(after_seq, int)
        or not 0 <= after_seq <= 4_294_967_295
    ):
        raise SimulationValidationError("after_seq must be a uint32")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise SimulationValidationError("limit must be an integer from 1 to 5000")
    with connect() as connection:
        _owned_session_row(connection, session_id, device_id)
        if after_seq is None:
            rows = connection.execute(
                """
                SELECT * FROM simulation_samples
                WHERE session_id = ?
                ORDER BY seq ASC, id ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM simulation_samples
                WHERE session_id = ? AND seq > ?
                ORDER BY seq ASC, id ASC
                LIMIT ?
                """,
                (session_id, after_seq, limit),
            ).fetchall()
    return [_sample_to_dict(row) for row in rows]


def list_valid_simulation_samples(
    session_id: str,
    device_id: str,
    *,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Return a deterministic, bounded prefix of healthy ultrasonic rows."""

    session_id = _validated_session_id(session_id)
    device_id = _validated_device_id(device_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise SimulationValidationError("limit must be an integer from 1 to 5000")
    with connect() as connection:
        _owned_session_row(connection, session_id, device_id)
        rows = connection.execute(
            """
            SELECT * FROM simulation_samples
            WHERE session_id = ?
              AND distance_mm BETWEEN ? AND ?
              AND (health_flags & ?) <> 0
            ORDER BY seq ASC, id ASC
            LIMIT ?
            """,
            (
                session_id,
                ULTRASONIC_MIN_DISTANCE_MM,
                ULTRASONIC_MAX_DISTANCE_MM,
                ULTRASONIC_HEALTH_BIT,
                limit,
            ),
        ).fetchall()
    return [_sample_to_dict(row) for row in rows]


def get_simulation_timeline(
    session_id: str,
    device_id: str,
    *,
    version: int = 1,
    after_seq: int | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Return chart points with the operator label resolved for each sample."""

    session_id = _validated_session_id(session_id)
    device_id = _validated_device_id(device_id)
    version = _validated_version(version)
    if after_seq is not None and (
        isinstance(after_seq, bool)
        or not isinstance(after_seq, int)
        or not 0 <= after_seq <= 4_294_967_295
    ):
        raise SimulationValidationError("after_seq must be a uint32")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise SimulationValidationError("limit must be an integer from 1 to 5000")

    with connect() as connection:
        session = _owned_session_row(connection, session_id, device_id)
        after_clause = "AND samples.seq > ?" if after_seq is not None else ""
        points = connection.execute(
            f"""
            SELECT
                samples.*,
                COALESCE((
                    SELECT labels.label
                    FROM simulation_labels AS labels
                    WHERE labels.session_id = samples.session_id
                      AND labels.version = ?
                      AND samples.seq BETWEEN labels.start_seq AND labels.end_seq
                    ORDER BY labels.updated_at DESC, labels.id DESC
                    LIMIT 1
                ), 'unknown') AS label,
                COALESCE((
                    SELECT labels.note
                    FROM simulation_labels AS labels
                    WHERE labels.session_id = samples.session_id
                      AND labels.version = ?
                      AND samples.seq BETWEEN labels.start_seq AND labels.end_seq
                    ORDER BY labels.updated_at DESC, labels.id DESC
                    LIMIT 1
                ), '') AS label_note,
                ? AS label_version,
                CASE WHEN samples.distance_mm BETWEEN ? AND ?
                      AND (samples.health_flags & ?) <> 0
                     THEN 1 ELSE 0 END AS valid_ultrasonic
            FROM simulation_samples AS samples
            WHERE samples.session_id = ? {after_clause}
            ORDER BY samples.seq ASC, samples.id ASC
            LIMIT ?
            """,
            # label_version is selected as a value as well as used twice in
            # correlated subqueries, hence the explicit ordered parameters.
            (
                version,
                version,
                version,
                ULTRASONIC_MIN_DISTANCE_MM,
                ULTRASONIC_MAX_DISTANCE_MM,
                ULTRASONIC_HEALTH_BIT,
                session_id,
                *((after_seq,) if after_seq is not None else ()),
                limit,
            ),
        ).fetchall()
        labels = connection.execute(
            """
            SELECT * FROM simulation_labels
            WHERE session_id = ? AND version = ?
            ORDER BY start_seq ASC, end_seq ASC, id ASC
            """,
            (session_id, version),
        ).fetchall()
        summary = _session_summary_with_connection(connection, session, version=version)

    timeline_points: list[dict[str, Any]] = []
    for row in points:
        point = _sample_to_dict(row)
        point["valid_ultrasonic"] = bool(point["valid_ultrasonic"])
        timeline_points.append(point)
    return {
        "session": summary,
        "label_version": version,
        "points": timeline_points,
        "labels": [_label_to_dict(row) for row in labels],
    }


def upsert_simulation_label(
    payload: Mapping[str, Any] | SimulationLabelUpsertIn,
) -> dict[str, Any]:
    try:
        label = (
            payload
            if isinstance(payload, SimulationLabelUpsertIn)
            else SimulationLabelUpsertIn.model_validate(payload)
        )
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    if label.start_seq > label.end_seq:
        raise SimulationValidationError(
            "start_seq must be less than or equal to end_seq"
        )

    now = _utc_now_text()
    with connect() as connection:
        session = _owned_session_row(connection, label.session_id, label.device_id)
        if str(session["state"]) != "completed":
            raise SimulationConflictError(
                "labels can only be edited after the simulation session is completed"
            )
        scenario_exists = connection.execute(
            "SELECT 1 FROM simulation_scenarios WHERE session_id = ?",
            (label.session_id,),
        ).fetchone()
        if scenario_exists is None:
            raise SimulationConflictError(
                "labels require an immutable operator-supplied session scenario"
            )
        sample_exists = connection.execute(
            """
            SELECT 1 FROM simulation_samples
            WHERE session_id = ? AND seq BETWEEN ? AND ?
            LIMIT 1
            """,
            (label.session_id, label.start_seq, label.end_seq),
        ).fetchone()
        if sample_exists is None:
            raise SimulationValidationError(
                "label interval must contain at least one collected sample"
            )

        conflicts = connection.execute(
            """
            SELECT id, start_seq, end_seq, label
            FROM simulation_labels
            WHERE session_id = ? AND version = ?
              AND NOT (end_seq < ? OR start_seq > ?)
              AND NOT (start_seq = ? AND end_seq = ?)
              AND label <> ?
            ORDER BY start_seq ASC
            """,
            (
                label.session_id,
                label.version,
                label.start_seq,
                label.end_seq,
                label.start_seq,
                label.end_seq,
                label.label,
            ),
        ).fetchall()
        if conflicts:
            conflict = conflicts[0]
            raise SimulationConflictError(
                "label interval conflicts with "
                f"{conflict['label']} interval "
                f"{conflict['start_seq']}..{conflict['end_seq']} in version "
                f"{label.version}"
            )

        values = label.model_dump()
        values.update({"created_at": now, "updated_at": now})
        connection.execute(
            """
            INSERT INTO simulation_labels (
                session_id, device_id, start_seq, end_seq, label, note,
                version, created_at, updated_at
            ) VALUES (
                :session_id, :device_id, :start_seq, :end_seq, :label, :note,
                :version, :created_at, :updated_at
            )
            ON CONFLICT(session_id, version, start_seq, end_seq) DO UPDATE SET
                label = excluded.label,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            values,
        )
        row = connection.execute(
            """
            SELECT * FROM simulation_labels
            WHERE session_id = ? AND version = ?
              AND start_seq = ? AND end_seq = ?
            """,
            (label.session_id, label.version, label.start_seq, label.end_seq),
        ).fetchone()
    if row is None:  # pragma: no cover - defensive database invariant
        raise RuntimeError("simulation label upsert did not return a row")
    return _label_to_dict(row)


def list_simulation_labels(
    session_id: str, device_id: str, *, version: int = 1
) -> list[dict[str, Any]]:
    session_id = _validated_session_id(session_id)
    device_id = _validated_device_id(device_id)
    version = _validated_version(version)
    with connect() as connection:
        _owned_session_row(connection, session_id, device_id)
        rows = connection.execute(
            """
            SELECT * FROM simulation_labels
            WHERE session_id = ? AND version = ?
            ORDER BY start_seq ASC, end_seq ASC, id ASC
            """,
            (session_id, version),
        ).fetchall()
    return [_label_to_dict(row) for row in rows]


def list_labeled_training_rows(
    session_id: str,
    device_id: str,
    *,
    version: int = 1,
    include_unknown: bool = True,
) -> list[dict[str, Any]]:
    """Return one deterministic label per sample; gaps are ``unknown``."""

    session_id = _validated_session_id(session_id)
    device_id = _validated_device_id(device_id)
    version = _validated_version(version)
    if not isinstance(include_unknown, bool):
        raise SimulationValidationError("include_unknown must be a boolean")
    with connect() as connection:
        session = _owned_session_row(connection, session_id, device_id)
        if str(session["state"]) != "completed":
            raise SimulationConflictError(
                "training rows are unavailable until the simulation session is completed"
            )
        rows = connection.execute(
            """
            SELECT
                samples.session_id,
                samples.device_id,
                samples.seq,
                samples.uptime_ms,
                samples.distance_mm,
                samples.water_rise_mm,
                samples.rise_rate_mm_s,
                samples.person_detected,
                samples.alarm_level,
                samples.health_flags,
                samples.wifi_rssi,
                samples.received_at,
                COALESCE((
                    SELECT labels.label
                    FROM simulation_labels AS labels
                    WHERE labels.session_id = samples.session_id
                      AND labels.version = ?
                      AND samples.seq BETWEEN labels.start_seq AND labels.end_seq
                    ORDER BY labels.updated_at DESC, labels.id DESC
                    LIMIT 1
                ), 'unknown') AS label,
                COALESCE((
                    SELECT labels.note
                    FROM simulation_labels AS labels
                    WHERE labels.session_id = samples.session_id
                      AND labels.version = ?
                      AND samples.seq BETWEEN labels.start_seq AND labels.end_seq
                    ORDER BY labels.updated_at DESC, labels.id DESC
                    LIMIT 1
                ), '') AS label_note
            FROM simulation_samples AS samples
            WHERE samples.session_id = ?
            ORDER BY samples.seq ASC, samples.id ASC
            """,
            (version, version, session_id),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = _sample_to_dict(row)
        item["baseline_distance_mm"] = session["baseline_distance_mm"]
        item["label_version"] = version
        if include_unknown or item["label"] != "unknown":
            result.append(item)
    return result
