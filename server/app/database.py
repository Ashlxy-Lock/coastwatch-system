import os
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "coastal_warning.db"
)


def database_path() -> Path:
    configured = os.getenv("COASTAL_DB_PATH", "").strip()
    return (
        Path(configured).expanduser().resolve() if configured else DEFAULT_DATABASE_PATH
    )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _has_calibration_session_foreign_key(
    connection: sqlite3.Connection, table: str
) -> bool:
    groups: dict[int, list[sqlite3.Row]] = {}
    for row in connection.execute(f"PRAGMA foreign_key_list({table})"):
        groups.setdefault(int(row["id"]), []).append(row)
    expected = {
        ("calibration_session_id", "session_id"),
        ("device_id", "device_id"),
    }
    return any(
        str(rows[0]["table"]) == "simulation_sessions"
        and str(rows[0]["on_delete"]).upper() == "RESTRICT"
        and {(str(row["from"]), str(row["to"])) for row in rows} == expected
        for rows in groups.values()
    )


def _audit_calibration_session_provenance(
    connection: sqlite3.Connection, table: str
) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if "calibration_session_id" not in columns:
        row_count = int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        )
        if row_count:
            raise RuntimeError(
                f"database integrity error: legacy {table} rows have no "
                "calibration_session_id provenance; manual recovery is required"
            )
        return
    dangling = connection.execute(
        f"""
        SELECT child.device_id, child.calibration_session_id
        FROM {table} AS child
        LEFT JOIN simulation_sessions AS calibration
          ON calibration.session_id = child.calibration_session_id
         AND calibration.device_id = child.device_id
        WHERE child.calibration_session_id IS NOT NULL
          AND calibration.session_id IS NULL
        LIMIT 1
        """
    ).fetchone()
    if dangling is not None:
        raise RuntimeError(
            "database integrity error: dangling calibration provenance in "
            f"{table} for device {dangling['device_id']} and session "
            f"{dangling['calibration_session_id']}; startup is blocked"
        )


def _rebuild_sensor_calibration_foreign_keys(
    connection: sqlite3.Connection,
) -> None:
    """Upgrade pre-FK sensor tables without deleting provenance records."""

    tables = ("sensor_test_profiles", "sensor_test_session_snapshots")
    for table in tables:
        _audit_calibration_session_provenance(connection, table)
    rebuild_profiles = not _has_calibration_session_foreign_key(
        connection, "sensor_test_profiles"
    )
    rebuild_snapshots = not _has_calibration_session_foreign_key(
        connection, "sensor_test_session_snapshots"
    )
    if rebuild_profiles or rebuild_snapshots:
        profile_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(sensor_test_profiles)")
        }
        snapshot_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(sensor_test_session_snapshots)"
            )
        }
        profile_calibration = (
            "calibration_session_id"
            if "calibration_session_id" in profile_columns
            else "NULL"
        )
        snapshot_calibration = (
            "calibration_session_id"
            if "calibration_session_id" in snapshot_columns
            else "NULL"
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if rebuild_profiles:
                connection.execute(
                    """
                    CREATE TABLE sensor_test_profiles__fk_migration (
                        device_id TEXT PRIMARY KEY,
                        profile_id TEXT NOT NULL UNIQUE,
                        profile_sha256 TEXT NOT NULL UNIQUE
                            CHECK (length(profile_sha256) = 64),
                        official_run_id TEXT NOT NULL,
                        artifact_sha256 TEXT NOT NULL
                            CHECK (length(artifact_sha256) = 64),
                        station_id TEXT NOT NULL,
                        context_timestamp TEXT NOT NULL,
                        datum TEXT NOT NULL,
                        mode TEXT NOT NULL
                            CHECK (mode IN ('formal', 'exploratory')),
                        calibration_session_id TEXT,
                        profile_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (official_run_id)
                            REFERENCES official_training_runs(run_id),
                        FOREIGN KEY (calibration_session_id, device_id)
                            REFERENCES simulation_sessions(session_id, device_id)
                            ON DELETE RESTRICT
                    )
                    """
                )
                connection.execute(
                    f"""
                    INSERT INTO sensor_test_profiles__fk_migration (
                        device_id, profile_id, profile_sha256, official_run_id,
                        artifact_sha256, station_id, context_timestamp, datum,
                        mode, calibration_session_id, profile_json, created_at,
                        updated_at
                    )
                    SELECT
                        device_id, profile_id, profile_sha256, official_run_id,
                        artifact_sha256, station_id, context_timestamp, datum,
                        mode, {profile_calibration}, profile_json, created_at,
                        updated_at
                    FROM sensor_test_profiles
                    """
                )
            if rebuild_snapshots:
                connection.execute(
                    """
                    CREATE TABLE sensor_test_session_snapshots__fk_migration (
                        session_id TEXT PRIMARY KEY,
                        device_id TEXT NOT NULL,
                        profile_id TEXT NOT NULL,
                        profile_sha256 TEXT NOT NULL
                            CHECK (length(profile_sha256) = 64),
                        official_run_id TEXT NOT NULL,
                        artifact_sha256 TEXT NOT NULL
                            CHECK (length(artifact_sha256) = 64),
                        calibration_session_id TEXT,
                        profile_json TEXT NOT NULL,
                        frozen_at TEXT NOT NULL,
                        UNIQUE (session_id, device_id),
                        FOREIGN KEY (session_id, device_id)
                            REFERENCES simulation_sessions(session_id, device_id)
                            ON DELETE CASCADE,
                        FOREIGN KEY (official_run_id)
                            REFERENCES official_training_runs(run_id),
                        FOREIGN KEY (calibration_session_id, device_id)
                            REFERENCES simulation_sessions(session_id, device_id)
                            ON DELETE RESTRICT
                    )
                    """
                )
                connection.execute(
                    f"""
                    INSERT INTO sensor_test_session_snapshots__fk_migration (
                        session_id, device_id, profile_id, profile_sha256,
                        official_run_id, artifact_sha256,
                        calibration_session_id, profile_json, frozen_at
                    )
                    SELECT
                        session_id, device_id, profile_id, profile_sha256,
                        official_run_id, artifact_sha256,
                        {snapshot_calibration}, profile_json, frozen_at
                    FROM sensor_test_session_snapshots
                    """
                )
            if rebuild_profiles:
                connection.execute("DROP TABLE sensor_test_profiles")
                connection.execute(
                    "ALTER TABLE sensor_test_profiles__fk_migration "
                    "RENAME TO sensor_test_profiles"
                )
            if rebuild_snapshots:
                connection.execute("DROP TABLE sensor_test_session_snapshots")
                connection.execute(
                    "ALTER TABLE sensor_test_session_snapshots__fk_migration "
                    "RENAME TO sensor_test_session_snapshots"
                )
                connection.execute(
                    """
                    CREATE INDEX idx_sensor_snapshots_device_frozen
                    ON sensor_test_session_snapshots(device_id, frozen_at DESC)
                    """
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    violations: list[sqlite3.Row] = []
    for table in (
        "sensor_test_profiles",
        "sensor_test_session_snapshots",
        "sensor_test_runs",
    ):
        violations.extend(connection.execute(f"PRAGMA foreign_key_check({table})"))
    if violations:
        first = violations[0]
        raise RuntimeError(
            "database integrity error: foreign_key_check failed for "
            f"{first['table']} rowid {first['rowid']} referencing "
            f"{first['parent']}; startup is blocked"
        )


def init_database() -> None:
    with connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                uptime_ms INTEGER NOT NULL,
                distance_mm INTEGER NOT NULL,
                water_rise_mm INTEGER NOT NULL,
                rise_rate_mm_s INTEGER NOT NULL,
                person_detected INTEGER NOT NULL CHECK (person_detected IN (0, 1)),
                alarm_level INTEGER NOT NULL CHECK (alarm_level BETWEEN 0 AND 4),
                health_flags INTEGER NOT NULL,
                wifi_rssi INTEGER NOT NULL CHECK (wifi_rssi BETWEEN -127 AND 0),
                simulation_session_id TEXT,
                received_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_telemetry_device_id_id
            ON telemetry(device_id, id DESC);

            CREATE TABLE IF NOT EXISTS device_locations (
                device_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'place'
                    CHECK (kind IN ('coast', 'place')),
                location TEXT NOT NULL,
                display_location TEXT NOT NULL,
                latitude REAL NOT NULL CHECK (latitude BETWEEN -90.0 AND 90.0),
                longitude REAL NOT NULL CHECK (longitude BETWEEN -180.0 AND 180.0),
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS simulation_sessions (
                session_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                name TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'active'
                    CHECK (state IN ('active', 'completed')),
                started_at TEXT NOT NULL,
                ended_at TEXT,
                baseline_distance_mm INTEGER
                    CHECK (baseline_distance_mm IS NULL OR baseline_distance_mm > 0),
                synthetic INTEGER NOT NULL DEFAULT 1 CHECK (synthetic = 1),
                UNIQUE (session_id, device_id),
                CHECK (
                    (state = 'active' AND ended_at IS NULL) OR
                    (state = 'completed' AND ended_at IS NOT NULL)
                )
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_simulation_one_active_device
            ON simulation_sessions(device_id)
            WHERE state = 'active';

            CREATE INDEX IF NOT EXISTS idx_simulation_sessions_device_started
            ON simulation_sessions(device_id, started_at DESC);

            CREATE TABLE IF NOT EXISTS simulation_scenarios (
                session_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                scenario_name TEXT NOT NULL CHECK (length(scenario_name) BETWEEN 1 AND 80),
                simulated_at TEXT NOT NULL,
                sim_air_temperature_c REAL NOT NULL
                    CHECK (sim_air_temperature_c BETWEEN -80.0 AND 60.0),
                sim_humidity_percent REAL NOT NULL
                    CHECK (sim_humidity_percent BETWEEN 0.0 AND 100.0),
                sim_wind_speed_kmh REAL NOT NULL
                    CHECK (sim_wind_speed_kmh BETWEEN 0.0 AND 400.0),
                sim_wave_height_m REAL NOT NULL
                    CHECK (sim_wave_height_m BETWEEN 0.0 AND 40.0),
                sim_wave_period_s REAL NOT NULL
                    CHECK (sim_wave_period_s >= 0.1 AND sim_wave_period_s <= 60.0),
                sim_water_temperature_c REAL NOT NULL
                    CHECK (sim_water_temperature_c BETWEEN -5.0 AND 45.0),
                sim_sea_level_height_m REAL NOT NULL
                    CHECK (sim_sea_level_height_m BETWEEN -20.0 AND 20.0),
                sim_ocean_current_velocity_kmh REAL NOT NULL
                    CHECK (sim_ocean_current_velocity_kmh BETWEEN 0.0 AND 50.0),
                sim_hour_sin REAL NOT NULL CHECK (sim_hour_sin BETWEEN -1.0 AND 1.0),
                sim_hour_cos REAL NOT NULL CHECK (sim_hour_cos BETWEEN -1.0 AND 1.0),
                sim_day_of_year_sin REAL NOT NULL
                    CHECK (sim_day_of_year_sin BETWEEN -1.0 AND 1.0),
                sim_day_of_year_cos REAL NOT NULL
                    CHECK (sim_day_of_year_cos BETWEEN -1.0 AND 1.0),
                sim_latitude REAL NOT NULL CHECK (sim_latitude BETWEEN -90.0 AND 90.0),
                sim_longitude REAL NOT NULL
                    CHECK (sim_longitude BETWEEN -180.0 AND 180.0),
                note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 500),
                scenario_schema TEXT NOT NULL
                    CHECK (scenario_schema = 'coastwatch.operator-simulated-coast'),
                scenario_schema_version INTEGER NOT NULL
                    CHECK (scenario_schema_version = 1),
                scenario_hash TEXT NOT NULL
                    CHECK (length(scenario_hash) = 64),
                updated_at TEXT NOT NULL,
                UNIQUE (session_id, device_id),
                FOREIGN KEY (session_id, device_id)
                    REFERENCES simulation_sessions(session_id, device_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_simulation_scenarios_device_updated
            ON simulation_scenarios(device_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS simulation_device_scenarios (
                device_id TEXT PRIMARY KEY,
                scenario_name TEXT NOT NULL CHECK (length(scenario_name) BETWEEN 1 AND 80),
                simulated_at TEXT NOT NULL,
                sim_air_temperature_c REAL NOT NULL
                    CHECK (sim_air_temperature_c BETWEEN -80.0 AND 60.0),
                sim_humidity_percent REAL NOT NULL
                    CHECK (sim_humidity_percent BETWEEN 0.0 AND 100.0),
                sim_wind_speed_kmh REAL NOT NULL
                    CHECK (sim_wind_speed_kmh BETWEEN 0.0 AND 400.0),
                sim_wave_height_m REAL NOT NULL
                    CHECK (sim_wave_height_m BETWEEN 0.0 AND 40.0),
                sim_wave_period_s REAL NOT NULL
                    CHECK (sim_wave_period_s >= 0.1 AND sim_wave_period_s <= 60.0),
                sim_water_temperature_c REAL NOT NULL
                    CHECK (sim_water_temperature_c BETWEEN -5.0 AND 45.0),
                sim_sea_level_height_m REAL NOT NULL
                    CHECK (sim_sea_level_height_m BETWEEN -20.0 AND 20.0),
                sim_ocean_current_velocity_kmh REAL NOT NULL
                    CHECK (sim_ocean_current_velocity_kmh BETWEEN 0.0 AND 50.0),
                sim_hour_sin REAL NOT NULL CHECK (sim_hour_sin BETWEEN -1.0 AND 1.0),
                sim_hour_cos REAL NOT NULL CHECK (sim_hour_cos BETWEEN -1.0 AND 1.0),
                sim_day_of_year_sin REAL NOT NULL
                    CHECK (sim_day_of_year_sin BETWEEN -1.0 AND 1.0),
                sim_day_of_year_cos REAL NOT NULL
                    CHECK (sim_day_of_year_cos BETWEEN -1.0 AND 1.0),
                sim_latitude REAL NOT NULL CHECK (sim_latitude BETWEEN -90.0 AND 90.0),
                sim_longitude REAL NOT NULL
                    CHECK (sim_longitude BETWEEN -180.0 AND 180.0),
                note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 500),
                scenario_schema TEXT NOT NULL
                    CHECK (scenario_schema = 'coastwatch.operator-simulated-coast'),
                scenario_schema_version INTEGER NOT NULL
                    CHECK (scenario_schema_version = 1),
                scenario_hash TEXT NOT NULL CHECK (length(scenario_hash) = 64),
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS simulation_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                telemetry_id INTEGER UNIQUE,
                seq INTEGER NOT NULL CHECK (seq BETWEEN 0 AND 4294967295),
                uptime_ms INTEGER NOT NULL
                    CHECK (uptime_ms BETWEEN 0 AND 4294967295),
                distance_mm INTEGER NOT NULL
                    CHECK (distance_mm BETWEEN 0 AND 4294967295),
                water_rise_mm INTEGER NOT NULL
                    CHECK (water_rise_mm BETWEEN -2147483648 AND 2147483647),
                rise_rate_mm_s INTEGER NOT NULL
                    CHECK (rise_rate_mm_s BETWEEN -2147483648 AND 2147483647),
                person_detected INTEGER NOT NULL
                    CHECK (person_detected IN (0, 1)),
                alarm_level INTEGER NOT NULL CHECK (alarm_level BETWEEN 0 AND 4),
                health_flags INTEGER NOT NULL
                    CHECK (health_flags BETWEEN 0 AND 4294967295),
                wifi_rssi INTEGER NOT NULL CHECK (wifi_rssi BETWEEN -127 AND 0),
                received_at TEXT NOT NULL,
                UNIQUE (session_id, seq),
                FOREIGN KEY (session_id, device_id)
                    REFERENCES simulation_sessions(session_id, device_id),
                FOREIGN KEY (telemetry_id) REFERENCES telemetry(id)
            );

            CREATE INDEX IF NOT EXISTS idx_simulation_samples_session_seq
            ON simulation_samples(session_id, seq);

            CREATE TABLE IF NOT EXISTS simulation_labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                start_seq INTEGER NOT NULL
                    CHECK (start_seq BETWEEN 0 AND 4294967295),
                end_seq INTEGER NOT NULL
                    CHECK (end_seq BETWEEN 0 AND 4294967295),
                label TEXT NOT NULL CHECK (label IN ('safe', 'danger', 'unknown')),
                note TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (session_id, version, start_seq, end_seq),
                FOREIGN KEY (session_id, device_id)
                    REFERENCES simulation_sessions(session_id, device_id),
                CHECK (start_seq <= end_seq)
            );

            CREATE INDEX IF NOT EXISTS idx_simulation_labels_lookup
            ON simulation_labels(session_id, version, start_seq, end_seq);

            CREATE TABLE IF NOT EXISTS official_dataset_versions (
                dataset_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                display_name TEXT NOT NULL,
                data_origin TEXT NOT NULL
                    CHECK (data_origin IN ('uk_official_archive',
                                           'synthetic_test_fixture')),
                activatable INTEGER NOT NULL CHECK (activatable IN (0, 1)),
                manifest_path TEXT NOT NULL,
                registration_path TEXT NOT NULL,
                registration_sha256 TEXT NOT NULL
                    CHECK (length(registration_sha256) = 64),
                manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
                dataset_sha256 TEXT NOT NULL CHECK (length(dataset_sha256) = 64),
                row_count INTEGER NOT NULL CHECK (row_count > 0),
                site_ids_json TEXT NOT NULL,
                date_start TEXT NOT NULL,
                date_end TEXT NOT NULL,
                splits_json TEXT NOT NULL,
                label_definition_json TEXT NOT NULL,
                feature_order_json TEXT NOT NULL,
                source_manifest_json TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                last_scanned_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_official_datasets_scanned
            ON official_dataset_versions(last_scanned_at DESC);

            CREATE TABLE IF NOT EXISTS official_training_runs (
                run_id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('running', 'succeeded', 'failed')),
                selected_site_ids_json TEXT NOT NULL,
                request_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                artifact_path TEXT,
                artifact_sha256 TEXT
                    CHECK (artifact_sha256 IS NULL OR length(artifact_sha256) = 64),
                artifact_version TEXT,
                metrics_json TEXT,
                source_manifest_json TEXT,
                data_contract_json TEXT,
                error_message TEXT,
                activated_at TEXT,
                FOREIGN KEY (dataset_id) REFERENCES official_dataset_versions(dataset_id),
                CHECK (
                    (status = 'running' AND finished_at IS NULL
                        AND error_message IS NULL) OR
                    (status = 'succeeded' AND finished_at IS NOT NULL
                        AND artifact_path IS NOT NULL
                        AND artifact_sha256 IS NOT NULL
                        AND metrics_json IS NOT NULL
                        AND source_manifest_json IS NOT NULL
                        AND data_contract_json IS NOT NULL
                        AND error_message IS NULL) OR
                    (status = 'failed' AND finished_at IS NOT NULL
                        AND error_message IS NOT NULL)
                )
            );

            CREATE INDEX IF NOT EXISTS idx_official_training_runs_started
            ON official_training_runs(started_at DESC);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_official_one_running_train
            ON official_training_runs((1))
            WHERE status = 'running';

            CREATE TABLE IF NOT EXISTS official_model_activation (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                run_id TEXT NOT NULL UNIQUE,
                artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
                activated_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES official_training_runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS sensor_test_profiles (
                device_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL UNIQUE,
                profile_sha256 TEXT NOT NULL UNIQUE
                    CHECK (length(profile_sha256) = 64),
                official_run_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
                station_id TEXT NOT NULL,
                context_timestamp TEXT NOT NULL,
                datum TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('formal', 'exploratory')),
                calibration_session_id TEXT,
                profile_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (official_run_id) REFERENCES official_training_runs(run_id),
                FOREIGN KEY (calibration_session_id, device_id)
                    REFERENCES simulation_sessions(session_id, device_id)
                    ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS sensor_test_session_snapshots (
                session_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                profile_sha256 TEXT NOT NULL CHECK (length(profile_sha256) = 64),
                official_run_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
                calibration_session_id TEXT,
                profile_json TEXT NOT NULL,
                frozen_at TEXT NOT NULL,
                UNIQUE (session_id, device_id),
                FOREIGN KEY (session_id, device_id)
                    REFERENCES simulation_sessions(session_id, device_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (official_run_id) REFERENCES official_training_runs(run_id),
                FOREIGN KEY (calibration_session_id, device_id)
                    REFERENCES simulation_sessions(session_id, device_id)
                    ON DELETE RESTRICT
            );

            CREATE INDEX IF NOT EXISTS idx_sensor_snapshots_device_frozen
            ON sensor_test_session_snapshots(device_id, frozen_at DESC);

            CREATE TABLE IF NOT EXISTS sensor_test_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                profile_sha256 TEXT NOT NULL CHECK (length(profile_sha256) = 64),
                artifact_sha256_before TEXT NOT NULL
                    CHECK (length(artifact_sha256_before) = 64),
                artifact_sha256_after TEXT NOT NULL
                    CHECK (length(artifact_sha256_after) = 64),
                status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
                sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
                result_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                FOREIGN KEY (session_id, device_id)
                    REFERENCES sensor_test_session_snapshots(session_id, device_id),
                CHECK (
                    (status = 'succeeded' AND result_json IS NOT NULL
                        AND error_message IS NULL
                        AND artifact_sha256_before = artifact_sha256_after) OR
                    (status = 'failed' AND error_message IS NOT NULL)
                )
            );

            CREATE INDEX IF NOT EXISTS idx_sensor_test_runs_device_created
            ON sensor_test_runs(device_id, created_at DESC);
            """
        )
        _rebuild_sensor_calibration_foreign_keys(connection)
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(device_locations)")
        }
        if "kind" not in columns:
            # Existing installations predate coast/place semantics. Marking
            # them as ordinary places is the safe migration: a user may have
            # selected an inland city through global search.
            connection.execute(
                """
                ALTER TABLE device_locations
                ADD COLUMN kind TEXT NOT NULL DEFAULT 'place'
                    CHECK (kind IN ('coast', 'place'))
                """
            )

        official_dataset_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(official_dataset_versions)"
            )
        }
        if "registration_sha256" not in official_dataset_columns:
            # This table was introduced during development and may already
            # exist in a local operator database. NULL honestly means that the
            # old schema never recorded this hash. A protected rescan may adopt
            # the real value only after the manifest and table hashes match.
            connection.execute(
                "ALTER TABLE official_dataset_versions "
                "ADD COLUMN registration_sha256 TEXT"
            )

        telemetry_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(telemetry)")
        }
        if "simulation_session_id" not in telemetry_columns:
            connection.execute(
                "ALTER TABLE telemetry ADD COLUMN simulation_session_id TEXT"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_telemetry_simulation_session
            ON telemetry(simulation_session_id, id)
            """
        )


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["person_detected"] = bool(result["person_detected"])
    return result


def insert_telemetry(payload: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(payload)
    sample_values = dict(values)
    values["person_detected"] = 1 if values["person_detected"] else 0
    values["received_at"] = _utc_now_text()
    values.setdefault("simulation_session_id", None)
    sample_values["received_at"] = values["received_at"]

    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO telemetry (
                device_id, seq, uptime_ms, distance_mm, water_rise_mm,
                rise_rate_mm_s, person_detected, alarm_level, health_flags,
                wifi_rssi, simulation_session_id, received_at
            ) VALUES (
                :device_id, :seq, :uptime_ms, :distance_mm, :water_rise_mm,
                :rise_rate_mm_s, :person_detected, :alarm_level, :health_flags,
                :wifi_rssi, :simulation_session_id, :received_at
            )
            """,
            values,
        )
        telemetry_id = cursor.lastrowid
        if telemetry_id is None:  # pragma: no cover - sqlite invariant
            raise RuntimeError("telemetry insert did not return an id")
        simulation_session_id = values["simulation_session_id"]
        if simulation_session_id is not None:
            # Import locally to avoid a module cycle: simulation_store uses the
            # shared connection helper above.  Keeping both inserts on this
            # connection guarantees all-or-nothing persistence.
            from .simulation_store import _add_sample_with_connection

            _add_sample_with_connection(
                connection,
                str(simulation_session_id),
                sample_values,
                telemetry_id=telemetry_id,
                received_at=str(values["received_at"]),
            )
        row = connection.execute(
            "SELECT * FROM telemetry WHERE id = ?", (telemetry_id,)
        ).fetchone()

    if row is None:
        raise RuntimeError("telemetry insert did not return a row")
    return _row_to_dict(row)


def latest_telemetry(device_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM telemetry
            WHERE device_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (device_id,),
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def telemetry_history(device_id: str, limit: int) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM telemetry
            WHERE device_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (device_id, limit),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_device_location(device_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM device_locations WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def upsert_device_location(payload: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(payload)
    values["display_location"] = str(values["display_location"]).upper()
    values["updated_at"] = _utc_now_text()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO device_locations (
                device_id, kind, location, display_location, latitude,
                longitude, updated_at
            ) VALUES (
                :device_id, :kind, :location, :display_location, :latitude,
                :longitude, :updated_at
            )
            ON CONFLICT(device_id) DO UPDATE SET
                kind = excluded.kind,
                location = excluded.location,
                display_location = excluded.display_location,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                updated_at = excluded.updated_at
            """,
            values,
        )
        row = connection.execute(
            "SELECT * FROM device_locations WHERE device_id = ?",
            (values["device_id"],),
        ).fetchone()
    if row is None:
        raise RuntimeError("device location upsert did not return a row")
    return dict(row)


def database_is_healthy() -> bool:
    with connect() as connection:
        result = connection.execute("SELECT 1").fetchone()
    return result is not None and result[0] == 1
