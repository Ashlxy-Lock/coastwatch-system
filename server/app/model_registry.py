"""Fail-closed catalog and per-device model selection.

The ESP32 chooses a server-side inference policy, never downloads a model and
never trains locally.  A selection is accepted only while that model is ready.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .database import connect

DEFAULT_MODEL_ID = "coastal-risk-logreg-v1"
IMPACTNET_MODEL_ID = "impactnet-v2"
CUSTOM_MODEL_ID = "custom-water-logreg-v1"
OFFICIAL_MODEL_ID = "uk-official-coast-logreg-v2"
OFFICIAL_PROVENANCE_DESCRIPTION = (
    "provenance_assurance=operator_attested_raw_hash_verified; "
    "deterministic_importer_replay_verified=false"
)
CUSTOM_MODEL_PATH_ENV = "COAST_CUSTOM_MODEL_PATH"
DEFAULT_CUSTOM_MODEL_PATH = (
    Path(__file__).resolve().parents[1] / "models" / "custom_water_v1.json"
)

ModelStatus = Literal["ready", "unavailable", "not_trained"]


def custom_model_path() -> Path:
    configured = os.getenv(CUSTOM_MODEL_PATH_ENV, "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else DEFAULT_CUSTOM_MODEL_PATH
    )


def init_model_registry() -> None:
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS device_model_selections (
                device_id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                selected_at TEXT NOT NULL
            )
            """
        )


def _artifact_looks_ready(path: Path) -> bool:
    """Catalog status is ready only after full schema and hash validation."""

    try:
        from .simulation_model import load_simulation_model

        model = load_simulation_model(path)
    except (OSError, UnicodeError, ValueError):
        return False
    return model.model_id == CUSTOM_MODEL_ID


def _active_official_artifact() -> dict[str, Any] | None:
    """Return a fully verified active official run, or fail closed."""

    from .experiment_store import get_active_official_model

    record = get_active_official_model()
    if record is None or not record.get("artifact_path"):
        return None
    try:
        from .official_model import load_official_model

        loaded = load_official_model(
            Path(str(record["artifact_path"])), require_activatable=True
        )
    except (OSError, UnicodeError, ValueError):
        return None
    if loaded.model_id != OFFICIAL_MODEL_ID:
        return None
    if loaded.artifact_sha256 != str(record.get("artifact_sha256", "")):
        return None
    return record


def list_models() -> list[dict[str, Any]]:
    official_ready = _active_official_artifact() is not None
    if os.getenv("COAST_ENABLE_LEGACY_SIMULATION_TRAINING", "").strip() == "1":
        custom_ready = _artifact_looks_ready(custom_model_path())
        third_model = {
            "model_id": CUSTOM_MODEL_ID,
            "display_name": "Simulated Coast Fusion (Legacy)",
            "status": "ready" if custom_ready else "not_trained",
            "mode": "simulation-shadow",
            "description": (
                "Archived operator-labelled teaching model"
                if custom_ready
                else "Legacy compatibility mode; not part of the current strategy"
            ),
        }
    else:
        third_model = {
            "model_id": OFFICIAL_MODEL_ID,
            "display_name": "UK Official Coast v2",
            "status": "ready" if official_ready else "not_trained",
            "mode": "sensor-external-test",
            "description": OFFICIAL_PROVENANCE_DESCRIPTION,
        }
    return [
        {
            "model_id": DEFAULT_MODEL_ID,
            "display_name": "Coastal Risk v1",
            "status": "ready",
            "mode": "shadow",
            "description": "6-hour weak-label coastal risk baseline",
        },
        {
            "model_id": IMPACTNET_MODEL_ID,
            "display_name": "ImpactNet v2",
            "status": "unavailable",
            "mode": "synthetic-only",
            "description": "Research bundle is synthetic; live inputs are unavailable",
        },
        third_model,
    ]


def _ready_model_ids() -> set[str]:
    return {
        str(model["model_id"]) for model in list_models() if model["status"] == "ready"
    }


def get_selected_model_id(device_id: str) -> str:
    init_model_registry()
    with connect() as connection:
        row = connection.execute(
            "SELECT model_id FROM device_model_selections WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    if row is None:
        return DEFAULT_MODEL_ID
    selected = str(row["model_id"])
    if selected == OFFICIAL_MODEL_ID:
        # Once explicitly selected, missing/stale profile data must make the
        # risk route fail closed rather than silently changing the experiment
        # back to the legacy baseline.
        return selected
    if selected not in _ready_model_ids():
        return DEFAULT_MODEL_ID
    return selected


def get_model_catalog(device_id: str) -> dict[str, Any]:
    return {
        "selected_model_id": get_selected_model_id(device_id),
        "models": list_models(),
    }


def select_device_model(device_id: str, model_id: str) -> dict[str, Any]:
    models = {str(model["model_id"]): model for model in list_models()}
    model = models.get(model_id)
    if model is None:
        raise KeyError("unknown model")
    if model["status"] != "ready":
        raise ValueError("model is not ready")
    if model_id == OFFICIAL_MODEL_ID:
        from .experiment_store import get_sensor_test_profile

        active = _active_official_artifact()
        profile = get_sensor_test_profile(device_id)
        if active is None or profile is None:
            raise ValueError("official model requires a sensor test profile")
        if str(profile["artifact_sha256"]) != str(active["artifact_sha256"]):
            raise ValueError("sensor test profile does not match active model")

    init_model_registry()
    selected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO device_model_selections (device_id, model_id, selected_at)
            VALUES (?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                model_id = excluded.model_id,
                selected_at = excluded.selected_at
            """,
            (device_id, model_id, selected_at),
        )
    return {
        "device_id": device_id,
        "selected_model_id": model_id,
        "selected_at": selected_at,
    }
