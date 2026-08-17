import hashlib
import json
import logging
import os
import shutil
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .dashboard import DASHBOARD_HTML
from .database import (
    database_is_healthy,
    get_device_location,
    init_database,
    insert_telemetry,
    latest_telemetry,
    telemetry_history,
    upsert_device_location,
)
from .environment import get_environment as load_environment
from .environment import get_location_presets
from .environment import search_locations as search_environment_locations
from .experiment_store import (
    OfficialConflictError,
    OfficialNotFoundError,
    activate_official_training_run,
    complete_official_training_run,
    create_official_training_run,
    create_sensor_test_run,
    delete_sensor_test_profile,
    fail_official_training_run,
    get_active_official_model,
    get_official_dataset,
    get_official_training_run,
    get_sensor_session_snapshot,
    get_sensor_test_profile,
    get_sensor_test_run,
    list_official_datasets,
    list_official_training_runs,
    list_sensor_test_runs,
    recover_interrupted_training_runs,
    upsert_official_dataset,
    upsert_sensor_test_profile,
)
from .model_registry import (
    CUSTOM_MODEL_ID,
    OFFICIAL_MODEL_ID,
    custom_model_path,
    get_model_catalog,
    get_selected_model_id,
    init_model_registry,
    select_device_model,
)
from .official_dataset import (
    OFFICIAL_FEATURE_ORDER,
    OfficialDatasetError,
    load_registered_official_dataset,
    rescan_official_dataset_root,
)
from .official_model import (
    OfficialModelError,
    assess_official_training_data,
    load_official_model,
    train_official_model,
)
from .risk_dispatch import (
    build_official_sensor_environment,
    build_selected_risk_result,
)
from .risk_model import load_risk_model
from .schemas import (
    DeviceId,
    DeviceLocationIn,
    DeviceLocationRecord,
    DeviceModelSelection,
    DeviceModelSelectionIn,
    EnvironmentResponse,
    HealthResponse,
    LocationSearchResult,
    ModelCatalogResponse,
    RiskResponse,
    SimulationSessionId,
    TelemetryIn,
    TelemetryRecord,
)
from .sensor_proxy_model import (
    SensorProxyError,
    build_sensor_proxy_profile,
    load_sensor_proxy_profile,
    run_sensor_proxy_external_test,
)
from .simulation_artifacts import (
    SIMULATION_ARTIFACT_LOCK,
    SimulationArtifactLockError,
)
from .simulation_model import (
    SimulationModelError,
    load_simulation_model,
    train_simulation_model,
)
from .simulation_schemas import (
    SimulationDeviceScenarioRecord,
    SimulationLabelRecord,
    SimulationLabelUpsertIn,
    SimulationModelResponse,
    SimulationOverviewResponse,
    SimulationSampleRecord,
    SimulationScenarioRecord,
    SimulationScenarioUpsertIn,
    SimulationSessionDeleteResponse,
    SimulationSessionRecord,
    SimulationSessionStartIn,
    SimulationSessionStopIn,
    SimulationTimelineResponse,
    SimulationTrainIn,
    SimulationTrainingReadinessResponse,
    SimulationTrainingResponse,
)
from .simulation_service import (
    build_simulated_environment,
    build_simulation_overview,
    build_training_dataset,
)
from .simulation_store import (
    SimulationConflictError,
    SimulationNotFoundError,
    SimulationValidationError,
    delete_device_simulation_scenario,
    delete_simulation_session,
    get_active_simulation_session,
    get_device_simulation_scenario,
    get_simulation_scenario,
    get_simulation_session,
    get_simulation_timeline,
    list_simulation_labels,
    list_simulation_samples,
    list_simulation_sessions,
    list_valid_simulation_samples,
    start_simulation_session,
    stop_simulation_session,
    upsert_device_simulation_scenario,
    upsert_simulation_label,
)
from .telemetry_quality import ultrasonic_sample_is_valid

logger = logging.getLogger(__name__)

OFFICIAL_DATASET_ROOT_ENV = "COAST_OFFICIAL_DATASET_ROOT"
OFFICIAL_REGISTRY_DIR_ENV = "COAST_OFFICIAL_REGISTRY_DIR"
OFFICIAL_ARTIFACT_DIR_ENV = "COAST_OFFICIAL_ARTIFACT_DIR"
SENSOR_EXTERNAL_TEST_MAX_SAMPLES = 5_000
SENSOR_EXTERNAL_TEST_PREVIEW_ROWS = 200
OFFICIAL_PROVENANCE_ASSURANCE = "operator_attested_raw_hash_verified"
OFFICIAL_IMPORTER_REPLAY_VERIFIED = False
_SERVER_ROOT = Path(__file__).resolve().parents[1]


class OfficialTrainingRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dataset_id: Annotated[str, Field(min_length=1, max_length=128)]
    selected_site_ids: Annotated[list[str] | None, Field(min_length=1)] = None


class SensorProfileUpsertIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: DeviceId
    context_id: Annotated[str, Field(min_length=1, max_length=256)]
    mode: Literal["formal", "exploratory"] = "formal"
    calibration_session_id: SimulationSessionId | None = None
    manual_gain: Annotated[float | None, Field(gt=0.0)] = None
    manual_reference_level_m: float | None = None


class SensorExternalTestRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: DeviceId
    session_id: SimulationSessionId


def _configured_directory(environment_name: str, default: Path) -> Path:
    value = os.getenv(environment_name, "").strip()
    return Path(value).expanduser().resolve() if value else default.resolve()


def _official_dataset_root() -> Path:
    return _configured_directory(
        OFFICIAL_DATASET_ROOT_ENV, _SERVER_ROOT / "data" / "official_datasets"
    )


def _official_registry_directory() -> Path:
    return _configured_directory(
        OFFICIAL_REGISTRY_DIR_ENV, _SERVER_ROOT / "data" / "official_registry"
    )


def _official_artifact_directory() -> Path:
    return _configured_directory(
        OFFICIAL_ARTIFACT_DIR_ENV, _SERVER_ROOT / "models" / "official_runs"
    )


def _json_copy(value: object) -> object:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    )


def _evenly_spaced_preview(rows: list[object], limit: int) -> list[object]:
    if len(rows) <= limit:
        return list(rows)
    if limit == 1:
        return [rows[0]]
    last = len(rows) - 1
    return [rows[index * last // (limit - 1)] for index in range(limit)]


def _registered_dataset_record(dataset: object) -> dict[str, object]:
    manifest = _json_copy(dict(dataset.manifest))  # type: ignore[attr-defined]
    assert isinstance(manifest, dict)  # core registration invariant
    date_range = manifest["date_range"]
    table = manifest["table"]
    return {
        "dataset_id": dataset.dataset_id,  # type: ignore[attr-defined]
        "version": dataset.version,  # type: ignore[attr-defined]
        "display_name": manifest.get("display_name", dataset.dataset_id),  # type: ignore[attr-defined]
        "data_origin": dataset.data_origin,  # type: ignore[attr-defined]
        "activatable": dataset.activatable,  # type: ignore[attr-defined]
        "manifest_path": str(dataset.manifest_path),  # type: ignore[attr-defined]
        "registration_path": str(dataset.registration_path),  # type: ignore[attr-defined]
        "registration_sha256": dataset.registration_sha256,  # type: ignore[attr-defined]
        "manifest_sha256": dataset.manifest_sha256,  # type: ignore[attr-defined]
        "dataset_sha256": dataset.table_sha256,  # type: ignore[attr-defined]
        "row_count": int(table["row_count"]),
        "site_ids": manifest["site_ids"],
        "date_start": date_range["start"],
        "date_end": date_range["end"],
        "splits": manifest["splits"],
        "label_definition": manifest["label_definition"],
        "feature_order": list(OFFICIAL_FEATURE_ORDER),
        "source_manifest": manifest,
    }


def _load_dataset_record(dataset_id: str):
    record = get_official_dataset(dataset_id)
    if record is None:
        raise OfficialNotFoundError(f"official dataset {dataset_id} not found")
    return load_registered_official_dataset(
        str(record["registration_path"]), dataset_root=_official_dataset_root()
    )


def _dataset_api_view(record: dict[str, object]) -> dict[str, object]:
    result = dict(record)
    result.update(
        {
            "provenance_assurance": OFFICIAL_PROVENANCE_ASSURANCE,
            "deterministic_importer_replay_verified": (
                OFFICIAL_IMPORTER_REPLAY_VERIFIED
            ),
        }
    )
    splits = result.get("splits")
    if isinstance(splits, dict) and "leakage_gap_hours" in splits:
        result["splits"] = {
            **splits,
            "leakage_gap": f"{splits['leakage_gap_hours']} h",
        }
    manifest = result.get("source_manifest")
    if isinstance(manifest, dict):
        metadata = manifest.get("site_metadata")
        site_ids = result.get("site_ids")
        if isinstance(site_ids, list):
            result["sites"] = [
                {
                    "site_id": site_id,
                    **(
                        dict(metadata.get(site_id, {}))
                        if isinstance(metadata, dict)
                        and isinstance(metadata.get(site_id), dict)
                        else {}
                    ),
                }
                for site_id in site_ids
            ]
        result["sources"] = manifest.get("sources", [])
        result["date_range"] = manifest.get(
            "date_range",
            {"start": result.get("date_start"), "end": result.get("date_end")},
        )
    result["sha256"] = result.get("dataset_sha256")
    return result


def _training_run_api_view(record: dict[str, object]) -> dict[str, object]:
    result = dict(record)
    result.update(
        {
            "model_id": OFFICIAL_MODEL_ID,
            "artifact_hash": result.get("artifact_sha256"),
            "created_at": result.get("started_at"),
            "activated": bool(result.get("activated_at")),
            "provenance_assurance": OFFICIAL_PROVENANCE_ASSURANCE,
            "deterministic_importer_replay_verified": (
                OFFICIAL_IMPORTER_REPLAY_VERIFIED
            ),
        }
    )
    source = result.get("source_manifest")
    if isinstance(source, dict):
        result["dataset_hash"] = source.get("harmonised_table_sha256")
    contract = result.get("data_contract")
    if isinstance(contract, dict):
        result.update(
            {
                "sensor_rows_used_for_fit": contract.get(
                    "sensor_rows_used_for_fit", 0
                ),
                "sensor_rows_used_for_scaler": contract.get(
                    "sensor_rows_used_for_scaler", 0
                ),
                "sensor_rows_used_for_threshold": contract.get(
                    "sensor_rows_used_for_threshold", 0
                ),
            }
        )
    metrics = result.get("metrics")
    if isinstance(metrics, dict):
        result["baselines"] = metrics.get("baselines", {})
    artifact_path = result.get("artifact_path")
    if result.get("status") == "succeeded" and isinstance(artifact_path, str):
        try:
            artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            artifact = None
        if isinstance(artifact, dict):
            result["decision_threshold"] = artifact.get("decision_threshold")
            result["deployment_mode"] = artifact.get("deployment_mode")
            result["activatable"] = artifact.get("activatable", False)
            readiness = artifact.get("readiness_snapshot")
            if isinstance(readiness, dict):
                result["activation_blockers"] = readiness.get(
                    "activation_blockers", []
                )
    return result


def _sensor_profile_api_view(record: dict[str, object]) -> dict[str, object]:
    payload = record.get("profile")
    result = dict(payload) if isinstance(payload, dict) else {}
    context = result.get("official_context")
    mapping = result.get("mapping")
    result.update(
        {
            "device_id": record.get("device_id"),
            "official_run_id": record.get("official_run_id"),
            "artifact_sha256": record.get("artifact_sha256"),
            "profile_sha256": record.get("profile_sha256"),
            "calibration_session_id": record.get("calibration_session_id"),
            "mode": record.get("mode"),
            "station_id": record.get("station_id"),
            "context_timestamp": record.get("context_timestamp"),
        }
    )
    if isinstance(context, dict):
        result["context_id"] = context.get("context_id")
        result["source_row_sha256"] = context.get("source_row_sha256")
    if isinstance(mapping, dict):
        result["gain"] = mapping.get("gain_m_per_m")
        result["reference_level_m"] = mapping.get("reference_level_m")
        result["official_train_range"] = {
            "min": mapping.get("official_train_q05_m"),
            "max": mapping.get("official_train_q95_m"),
        }
    return result


def _sensor_test_run_api_view(record: dict[str, object]) -> dict[str, object]:
    payload = record.get("result")
    result = dict(payload) if isinstance(payload, dict) else {}
    rows = result.get("rows")
    proxy_levels = (
        [
            float(row["proxy_relative_water_level_m"])
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("proxy_relative_water_level_m"), (int, float))
        ]
        if isinstance(rows, list)
        else []
    )
    sample_count = int(result.get("sample_count", record.get("sample_count", 0)))
    ood_count = int(result.get("out_of_distribution_count", 0))
    result.update(
        {
            "run_id": record.get("run_id"),
            "status": record.get("status"),
            "device_id": record.get("device_id"),
            "session_id": record.get("session_id"),
            "profile_sha256": record.get("profile_sha256"),
            "artifact_sha256": record.get("artifact_sha256_before"),
            "created_at": record.get("created_at"),
            "error_message": record.get("error_message"),
            "metrics": {
                "mapped_sample_count": sample_count,
                "ood_rate": ood_count / sample_count if sample_count else 0.0,
                "mapped_min_m": result.get(
                    "mapped_min_m", min(proxy_levels) if proxy_levels else None
                ),
                "mapped_max_m": result.get(
                    "mapped_max_m", max(proxy_levels) if proxy_levels else None
                ),
                "mean_risk_score": result.get("mean_extreme_water_probability"),
            },
        }
    )
    return result


def _legacy_simulation_training_enabled() -> bool:
    return os.getenv("COAST_ENABLE_LEGACY_SIMULATION_TRAINING", "").strip() == "1"


def _environment_for_device(device_id: str) -> EnvironmentResponse:
    selected_model_id = get_selected_model_id(device_id)
    if selected_model_id == OFFICIAL_MODEL_ID:
        try:
            return build_official_sensor_environment(device_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Official model requires a valid frozen sensor profile",
            ) from exc
    if selected_model_id != CUSTOM_MODEL_ID:
        return load_environment(device_id)
    scenario = get_device_simulation_scenario(device_id)
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Custom simulation model requires an active operator scenario",
        )
    return build_simulated_environment(scenario)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    init_model_registry()
    # The OS lock proves that no live trainer owns the artifact transaction.
    # Only then may a process-start recovery fail rows left by a crashed worker.
    with SIMULATION_ARTIFACT_LOCK:
        recover_interrupted_training_runs(max_age_seconds=0.0)
    configured_model_path = os.getenv("COAST_RISK_MODEL_PATH", "").strip()
    try:
        app.state.risk_model = load_risk_model(
            Path(configured_model_path) if configured_model_path else None
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Risk model unavailable; using rule fallback: %s", exc)
        app.state.risk_model = None
    try:
        yield
    finally:
        del app.state.risk_model


app = FastAPI(
    title="海岸智能预警本地服务器",
    version="0.1.0",
    description="接收 ESP32 遥测，使用 SQLite 保存并提供本地监控页面。",
    lifespan=lifespan,
)


@app.exception_handler(SimulationNotFoundError)
async def simulation_not_found_handler(
    _request: Request, exc: SimulationNotFoundError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(SimulationConflictError)
async def simulation_conflict_handler(
    _request: Request, exc: SimulationConflictError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(SimulationValidationError)
async def simulation_validation_handler(
    _request: Request, exc: SimulationValidationError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(SimulationArtifactLockError)
async def simulation_artifact_lock_handler(
    _request: Request, _exc: SimulationArtifactLockError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "Simulation artifact maintenance is temporarily unavailable"
        },
    )


@app.exception_handler(OfficialNotFoundError)
async def official_not_found_handler(
    _request: Request, exc: OfficialNotFoundError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(OfficialConflictError)
async def official_conflict_handler(
    _request: Request, exc: OfficialConflictError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    return DASHBOARD_HTML


@app.post(
    "/api/v1/telemetry",
    response_model=TelemetryRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_telemetry(payload: TelemetryIn) -> dict:
    return insert_telemetry(payload.model_dump())


@app.get("/api/v1/models", response_model=ModelCatalogResponse)
def models(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    return get_model_catalog(device_id)


@app.put("/api/v1/device-model", response_model=DeviceModelSelection)
def save_device_model(payload: DeviceModelSelectionIn) -> dict:
    try:
        return select_device_model(payload.device_id, payload.model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown model ID") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Model is not ready") from exc


@app.get("/api/v1/official-datasets")
def read_official_datasets() -> list[dict]:
    return [_dataset_api_view(record) for record in list_official_datasets()]


@app.get("/api/v1/official-datasets/{dataset_id}")
def read_official_dataset(dataset_id: str) -> dict:
    record = get_official_dataset(dataset_id)
    if record is None:
        raise OfficialNotFoundError(f"official dataset {dataset_id} not found")
    return _dataset_api_view(record)


@app.post("/api/v1/official-datasets/rescan")
def rescan_official_datasets() -> dict:
    """Discover only fixed-name bundles below the protected server root."""

    root = _official_dataset_root()
    registry = _official_registry_directory()
    root.mkdir(parents=True, exist_ok=True)
    registry.mkdir(parents=True, exist_ok=True)
    registered: list[dict] = []
    errors: list[dict[str, str]] = []
    try:
        discovered = rescan_official_dataset_root(root, registry)
    except (OSError, UnicodeError, OfficialDatasetError) as exc:
        discovered = []
        errors.append({"bundle": "protected-root", "detail": str(exc)})
    for dataset in discovered:
        try:
            registered.append(
                _dataset_api_view(
                    upsert_official_dataset(_registered_dataset_record(dataset))
                )
            )
        except OfficialConflictError as exc:
            errors.append(
                {
                    "bundle": f"{dataset.dataset_id}/{dataset.version}",
                    "detail": str(exc),
                }
            )
    return {
        "data_origin_required": "uk_official_archive",
        "provenance_assurance": OFFICIAL_PROVENANCE_ASSURANCE,
        "deterministic_importer_replay_verified": (
            OFFICIAL_IMPORTER_REPLAY_VERIFIED
        ),
        "dataset_id_policy": (
            "globally version-qualified and immutable, e.g. uk-coasts-2024-v1"
        ),
        "registered_count": len(registered),
        "error_count": len(errors),
        "registered": registered,
        "errors": errors,
    }


@app.get("/api/v1/official-training/readiness")
def read_official_training_readiness(
    dataset_id: Annotated[str, Query(min_length=1, max_length=128)],
    site_id: Annotated[list[str] | None, Query()] = None,
) -> dict:
    try:
        dataset = _load_dataset_record(dataset_id)
        result = assess_official_training_data(
            dataset, selected_site_ids=site_id
        )
        result.update(
            {
                "warnings": result.get("evidence_warnings", []),
                "sensor_rows_used_for_fit": 0,
                "sensor_rows_used_for_scaler": 0,
                "sensor_rows_used_for_threshold": 0,
                "provenance_assurance": OFFICIAL_PROVENANCE_ASSURANCE,
                "deterministic_importer_replay_verified": (
                    OFFICIAL_IMPORTER_REPLAY_VERIFIED
                ),
            }
        )
        return result
    except OfficialDatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/official-training/runs")
def read_official_training_runs(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    return [
        _training_run_api_view(record)
        for record in list_official_training_runs(limit)
    ]


@app.get("/api/v1/official-training/runs/{run_id}")
def read_official_training_run(run_id: str) -> dict:
    record = get_official_training_run(run_id)
    if record is None:
        raise OfficialNotFoundError(f"official training run {run_id} not found")
    return _training_run_api_view(record)


@app.post(
    "/api/v1/official-training/runs",
    status_code=status.HTTP_201_CREATED,
)
def create_official_model_training_run(payload: OfficialTrainingRunIn) -> dict:
    """Run one explicit, single-threaded fit in FastAPI's worker thread."""

    try:
        dataset = _load_dataset_record(payload.dataset_id)
        readiness = assess_official_training_data(
            dataset, selected_site_ids=payload.selected_site_ids
        )
    except OfficialDatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not readiness["ready"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "official dataset is not training-ready",
                "blockers": readiness["blockers"],
            },
        )
    artifact_directory = _official_artifact_directory()
    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    run_id: str | None = None
    try:
        with SIMULATION_ARTIFACT_LOCK:
            # The running row is created only after acquiring the process-wide
            # lock. A concurrently starting gateway therefore cannot recover
            # this live run as an interrupted row before training owns the lock.
            dataset = _load_dataset_record(payload.dataset_id)
            locked_readiness = assess_official_training_data(
                dataset, selected_site_ids=payload.selected_site_ids
            )
            if not locked_readiness["ready"]:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "official dataset is not training-ready",
                        "blockers": locked_readiness["blockers"],
                    },
                )
            selected_sites = list(locked_readiness["selected_site_ids"])
            request_snapshot = {
                "dataset_id": payload.dataset_id,
                "selected_site_ids": selected_sites,
                "automatic_training": False,
            }
            run = create_official_training_run(
                payload.dataset_id, selected_sites, request_snapshot
            )
            run_id = str(run["run_id"])
            destination = artifact_directory / f"{run_id}.json"
            artifact_directory.mkdir(parents=True, exist_ok=True)
            artifact = train_official_model(
                dataset,
                output_path=destination,
                selected_site_ids=selected_sites,
                version=version,
            )
            load_official_model(destination, require_activatable=False)
            return _training_run_api_view(
                complete_official_training_run(
                    run_id,
                    artifact_path=str(destination.resolve()),
                    artifact_sha256=str(artifact["artifact_sha256"]),
                    artifact_version=str(artifact["version"]),
                    metrics=artifact["metrics"],
                    source_manifest=artifact["source_manifest"],
                    data_contract=artifact["data_contract"],
                )
            )
    except SimulationArtifactLockError:
        raise
    except (OfficialDatasetError, OfficialModelError, OSError, RuntimeError) as exc:
        if run_id is None:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        failed = fail_official_training_run(run_id, str(exc))
        raise HTTPException(
            status_code=422,
            detail={
                "message": "official model training failed",
                "run_id": run_id,
                "error": failed["error_message"],
            },
        ) from exc


@app.post("/api/v1/official-training/runs/{run_id}/activate")
def activate_official_model_run(run_id: str) -> dict:
    run = get_official_training_run(run_id)
    if run is None:
        raise OfficialNotFoundError(f"official training run {run_id} not found")
    if run["status"] != "succeeded":
        raise OfficialConflictError("only a successful official run can activate")
    with SIMULATION_ARTIFACT_LOCK:
        try:
            loaded = load_official_model(
                str(run["artifact_path"]), require_activatable=True
            )
        except (OSError, UnicodeError, OfficialModelError) as exc:
            raise HTTPException(
                status_code=422,
                detail="official artifact is not activation-ready",
            ) from exc
        if loaded.artifact_sha256 != run["artifact_sha256"]:
            raise HTTPException(status_code=422, detail="official artifact hash mismatch")
        return _training_run_api_view(activate_official_training_run(run_id))


@app.get("/api/v1/official-model")
def read_active_official_model() -> dict:
    run = get_active_official_model()
    if run is None:
        raise HTTPException(status_code=404, detail="No official model is activated")
    try:
        artifact = json.loads(Path(str(run["artifact_path"])).read_text(encoding="utf-8"))
        loaded = load_official_model(artifact, require_activatable=True)
    except (OSError, UnicodeError, json.JSONDecodeError, OfficialModelError) as exc:
        raise HTTPException(
            status_code=503, detail="Active official model artifact is invalid"
        ) from exc
    if loaded.artifact_sha256 != run["artifact_sha256"]:
        raise HTTPException(status_code=503, detail="Active official model hash mismatch")
    return {
        **artifact,
        "artifact_hash": artifact.get("artifact_sha256"),
        "mode": artifact.get("deployment_mode"),
        "sensor_contexts": artifact.get("sensor_test_contexts", []),
        "provenance_assurance": OFFICIAL_PROVENANCE_ASSURANCE,
        "deterministic_importer_replay_verified": (
            OFFICIAL_IMPORTER_REPLAY_VERIFIED
        ),
        "active_run": _training_run_api_view(run),
    }


@app.get("/api/v1/sensor-test/device-profile")
def read_sensor_test_device_profile(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    record = get_sensor_test_profile(device_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No sensor test profile")
    return _sensor_profile_api_view(record)


@app.put("/api/v1/sensor-test/device-profile")
def save_sensor_test_device_profile(payload: SensorProfileUpsertIn) -> dict:
    with SIMULATION_ARTIFACT_LOCK:
        active = get_active_official_model()
        if active is None:
            raise HTTPException(
                status_code=409,
                detail="Activate a UK official model before freezing a sensor profile",
            )
        try:
            official_model = load_official_model(
                str(active["artifact_path"]), require_activatable=True
            )
        except (OSError, UnicodeError, OfficialModelError) as exc:
            raise HTTPException(
                status_code=503, detail="Active official model artifact is invalid"
            ) from exc
        if official_model.artifact_sha256 != active["artifact_sha256"]:
            raise HTTPException(status_code=503, detail="Active artifact hash mismatch")
        context = next(
            (
                item
                for item in official_model.sensor_test_contexts
                if item["context_id"] == payload.context_id
            ),
            None,
        )
        if context is None:
            raise HTTPException(
                status_code=422,
                detail="context_id is not pinned in the active official artifact",
            )

        calibration_values: list[float] | None = None
        calibration_source: dict[str, str] | None = None
        if payload.mode == "formal":
            if payload.calibration_session_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="formal mapping requires a completed calibration session",
                )
            calibration_session = get_simulation_session(
                payload.calibration_session_id, payload.device_id
            )
            if calibration_session is None:
                raise HTTPException(status_code=404, detail="Calibration session not found")
            if calibration_session["state"] != "completed":
                raise HTTPException(
                    status_code=409, detail="Calibration session must be completed"
                )
            calibration_samples = list_simulation_samples(
                payload.calibration_session_id,
                payload.device_id,
                limit=100_000,
            )
            calibration_values = [
                float(sample["water_rise_mm"])
                for sample in calibration_samples
                if ultrasonic_sample_is_valid(sample)
            ]
            calibration_source = {
                "session_id": payload.calibration_session_id,
                "device_id": payload.device_id,
                "started_at": str(calibration_session["started_at"]),
                "ended_at": str(calibration_session["ended_at"]),
            }
        try:
            profile = build_sensor_proxy_profile(
                profile_id=f"sensorprof_{uuid4().hex}",
                official_model=official_model,
                official_context=context,
                calibration_water_rise_mm=calibration_values,
                calibration_source=calibration_source,
                manual_gain=payload.manual_gain,
                manual_reference_level_m=payload.manual_reference_level_m,
                exploratory=payload.mode == "exploratory",
            )
        except SensorProxyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        stored = upsert_sensor_test_profile(
            {
                "device_id": payload.device_id,
                "profile_id": profile["profile_id"],
                "profile_sha256": profile["profile_sha256"],
                "official_run_id": active["run_id"],
                "artifact_sha256": profile["official_model_artifact_sha256"],
                "station_id": profile["site_id"],
                "context_timestamp": profile["official_context"]["timestamp"],
                "datum": profile["datum"],
                "mode": payload.mode,
                "calibration_session_id": payload.calibration_session_id,
                "profile": profile,
            }
        )
        return _sensor_profile_api_view(stored)


@app.delete("/api/v1/sensor-test/device-profile")
def clear_sensor_test_device_profile(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    delete_sensor_test_profile(device_id)
    return {"status": "deleted", "device_id": device_id}


@app.get("/api/v1/sensor-test/runs")
def read_sensor_external_test_runs(
    device_id: Annotated[DeviceId | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    return [
        _sensor_test_run_api_view(record)
        for record in list_sensor_test_runs(device_id=device_id, limit=limit)
    ]


@app.get("/api/v1/sensor-test/runs/{run_id}")
def read_sensor_external_test_run(run_id: str) -> dict:
    record = get_sensor_test_run(run_id)
    if record is None:
        raise OfficialNotFoundError(f"sensor external-test run {run_id} not found")
    return _sensor_test_run_api_view(record)


@app.post(
    "/api/v1/sensor-test/runs", status_code=status.HTTP_201_CREATED
)
def create_sensor_external_test_run(payload: SensorExternalTestRunIn) -> dict:
    with SIMULATION_ARTIFACT_LOCK:
        # Deletion uses the same lock. Keep the completed session, its frozen
        # snapshot, samples, artifact verification and persisted test result in
        # one critical section so provenance cannot disappear mid-analysis.
        session = get_simulation_session(payload.session_id, payload.device_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Sensor test session not found")
        if session["state"] != "completed":
            raise HTTPException(
                status_code=409, detail="Sensor test session must be completed"
            )
        snapshot = get_sensor_session_snapshot(payload.session_id)
        if snapshot is None or snapshot["device_id"] != payload.device_id:
            raise HTTPException(
                status_code=409,
                detail="Session has no pre-collection frozen sensor profile snapshot",
            )
        if snapshot.get("calibration_session_id") == payload.session_id:
            raise HTTPException(
                status_code=409,
                detail="External test session must be independent of calibration",
            )
        source_run = get_official_training_run(str(snapshot["official_run_id"]))
        if source_run is None or source_run["status"] != "succeeded":
            raise HTTPException(status_code=503, detail="Snapshot model run is unavailable")
        valid_samples = list_valid_simulation_samples(
            payload.session_id,
            payload.device_id,
            limit=SENSOR_EXTERNAL_TEST_MAX_SAMPLES,
        )
        if not valid_samples:
            raise HTTPException(
                status_code=422, detail="External test has no valid ultrasonic samples"
            )
        try:
            official_model = load_official_model(
                str(source_run["artifact_path"]), require_activatable=True
            )
            if official_model.artifact_sha256 != snapshot["artifact_sha256"]:
                raise SensorProxyError("snapshot artifact hash mismatch")
            profile = load_sensor_proxy_profile(
                snapshot["profile"], official_model=official_model
            )
            result = run_sensor_proxy_external_test(
                official_model,
                profile,
                valid_samples,
                session_id=payload.session_id,
            )
            after = load_official_model(
                str(source_run["artifact_path"]), require_activatable=True
            ).artifact_sha256
            before = official_model.artifact_sha256
            if before != after:
                raise SensorProxyError("official artifact changed during external test")
        except (OSError, UnicodeError, OfficialModelError, SensorProxyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        input_sample_count = int(session["sample_count"])
        valid_input_sample_count = int(session["valid_ultrasonic_samples"])
        invalid_input_sample_count = int(session["invalid_ultrasonic_samples"])
        evaluated_sample_count = len(valid_samples)
        result_rows = result.get("rows")
        if not isinstance(result_rows, list) or len(result_rows) != evaluated_sample_count:
            raise HTTPException(
                status_code=422,
                detail="external-test model returned an invalid result row count",
            )
        proxy_levels = [
            float(row["proxy_relative_water_level_m"])
            for row in result_rows
            if isinstance(row, dict)
            and isinstance(row.get("proxy_relative_water_level_m"), (int, float))
        ]
        if len(proxy_levels) != evaluated_sample_count:
            raise HTTPException(
                status_code=422,
                detail="external-test model returned incomplete mapped levels",
            )
        result_preview = _evenly_spaced_preview(
            result_rows, SENSOR_EXTERNAL_TEST_PREVIEW_ROWS
        )
        evaluated_samples_sha256 = hashlib.sha256(
            json.dumps(
                valid_samples,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        result.update(
            {
                "input_sample_count": input_sample_count,
                "valid_input_sample_count": valid_input_sample_count,
                "evaluated_sample_count": evaluated_sample_count,
                "excluded_invalid_ultrasonic_samples": invalid_input_sample_count,
                "truncated_valid_sample_count": max(
                    valid_input_sample_count - evaluated_sample_count, 0
                ),
                "evaluation_sample_limit": SENSOR_EXTERNAL_TEST_MAX_SAMPLES,
                "evaluation_truncated": (
                    valid_input_sample_count > evaluated_sample_count
                ),
                "sampling_policy": "earliest-valid-sequence-v1",
                "evaluated_samples_sha256": evaluated_samples_sha256,
                "mapped_min_m": min(proxy_levels),
                "mapped_max_m": max(proxy_levels),
                "result_row_count": len(result_rows),
                "preview_row_count": len(result_preview),
                "preview_row_limit": SENSOR_EXTERNAL_TEST_PREVIEW_ROWS,
                "preview_sampling_policy": (
                    "evenly-spaced-over-evaluated-sequence-v1"
                ),
                "rows": result_preview,
            }
        )
        stored = create_sensor_test_run(
            session_id=payload.session_id,
            device_id=payload.device_id,
            profile_sha256=str(snapshot["profile_sha256"]),
            artifact_sha256_before=before,
            artifact_sha256_after=after,
            sample_count=len(valid_samples),
            result=result,
        )
    return _sensor_test_run_api_view(stored)


@app.put(
    "/api/v1/simulations/device-scenario",
    response_model=SimulationDeviceScenarioRecord,
)
def save_device_simulation_scenario(
    payload: SimulationScenarioUpsertIn,
) -> dict:
    return upsert_device_simulation_scenario(payload)


@app.get(
    "/api/v1/simulations/device-scenario",
    response_model=SimulationDeviceScenarioRecord,
)
def read_device_simulation_scenario(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    record = get_device_simulation_scenario(device_id)
    if record is None:
        raise SimulationNotFoundError(
            f"device {device_id} has no active simulation scenario"
        )
    return record


@app.delete("/api/v1/simulations/device-scenario")
def clear_device_simulation_scenario(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    delete_device_simulation_scenario(device_id)
    return {"status": "cleared", "device_id": device_id}


@app.post(
    "/api/v1/simulations/sessions",
    response_model=SimulationSessionRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_simulation_session(payload: SimulationSessionStartIn) -> dict:
    return start_simulation_session(payload)


@app.get(
    "/api/v1/simulations/sessions/active",
    response_model=SimulationSessionRecord,
)
def read_active_simulation_session(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    record = get_active_simulation_session(device_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No active simulation session")
    return record


@app.get(
    "/api/v1/simulations/sessions",
    response_model=list[SimulationSessionRecord],
)
def read_simulation_sessions(
    device_id: Annotated[DeviceId | None, Query()] = None,
    state: Annotated[str | None, Query(pattern="^(active|completed)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    return list_simulation_sessions(device_id, state, limit=limit)  # type: ignore[arg-type]


@app.get(
    "/api/v1/simulations/overview",
    response_model=SimulationOverviewResponse,
)
def read_simulation_overview(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
    label_version: Annotated[int, Query(ge=1)] = 1,
) -> dict:
    return build_simulation_overview(device_id, label_version=label_version, limit=500)


@app.get(
    "/api/v1/simulations/training-readiness",
    response_model=SimulationTrainingReadinessResponse,
)
def read_simulation_training_readiness(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
    label_version: Annotated[int, Query(ge=1)] = 1,
    session_id: Annotated[list[SimulationSessionId] | None, Query()] = None,
) -> dict:
    return build_training_dataset(
        device_id,
        label_version=label_version,
        selected_session_ids=session_id,
    )["readiness"]


@app.get(
    "/api/v1/simulations/model",
    response_model=SimulationModelResponse,
)
def read_custom_simulation_model() -> dict:
    path = custom_model_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        load_simulation_model(payload)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom simulation model has not been trained",
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError, SimulationModelError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Custom simulation model artifact is invalid",
        ) from exc
    return payload


@app.get(
    "/api/v1/simulations/sessions/{session_id}/scenario",
    response_model=SimulationScenarioRecord,
)
def read_simulation_scenario(
    session_id: str,
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    record = get_simulation_scenario(session_id, device_id)
    if record is None:
        raise SimulationNotFoundError(
            f"simulation session {session_id} has no scenario snapshot"
        )
    return record


@app.get(
    "/api/v1/simulations/sessions/{session_id}",
    response_model=SimulationSessionRecord,
)
def read_simulation_session(
    session_id: str,
    device_id: Annotated[DeviceId | None, Query()] = None,
) -> dict:
    record = get_simulation_session(session_id, device_id)
    if record is None:
        raise SimulationNotFoundError(f"simulation session {session_id} not found")
    return record


@app.delete(
    "/api/v1/simulations/sessions/{session_id}",
    response_model=SimulationSessionDeleteResponse,
)
def remove_simulation_session(
    session_id: str,
    device_id: Annotated[DeviceId, Query()],
) -> dict:
    return delete_simulation_session(session_id, device_id)


@app.post(
    "/api/v1/simulations/sessions/{session_id}/stop",
    response_model=SimulationSessionRecord,
)
def complete_simulation_session(
    session_id: str, payload: SimulationSessionStopIn
) -> dict:
    return stop_simulation_session(session_id, payload.device_id)


@app.get(
    "/api/v1/simulations/sessions/{session_id}/samples",
    response_model=list[SimulationSampleRecord],
)
def read_simulation_samples(
    session_id: str,
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
    after_seq: Annotated[int | None, Query(ge=0, le=4_294_967_295)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> list[dict]:
    return list_simulation_samples(
        session_id, device_id, after_seq=after_seq, limit=limit
    )


@app.get(
    "/api/v1/simulations/sessions/{session_id}/timeline",
    response_model=SimulationTimelineResponse,
)
def read_simulation_timeline(
    session_id: str,
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
    label_version: Annotated[int, Query(ge=1)] = 1,
    after_seq: Annotated[int | None, Query(ge=0, le=4_294_967_295)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> dict:
    return get_simulation_timeline(
        session_id,
        device_id,
        version=label_version,
        after_seq=after_seq,
        limit=limit,
    )


@app.get(
    "/api/v1/simulations/sessions/{session_id}/labels",
    response_model=list[SimulationLabelRecord],
)
def read_simulation_labels(
    session_id: str,
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
    version: Annotated[int, Query(ge=1)] = 1,
) -> list[dict]:
    return list_simulation_labels(session_id, device_id, version=version)


@app.put(
    "/api/v1/simulations/labels",
    response_model=SimulationLabelRecord,
)
def save_simulation_label(payload: SimulationLabelUpsertIn) -> dict:
    return upsert_simulation_label(payload)


@app.post(
    "/api/v1/simulations/train",
    response_model=SimulationTrainingResponse,
)
def train_custom_simulation_model(payload: SimulationTrainIn) -> dict:
    if not _legacy_simulation_training_enabled():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "Legacy operator-labelled simulation training is archived; "
                "use the UK official training console"
            ),
        )
    with SIMULATION_ARTIFACT_LOCK:
        return _train_custom_simulation_model(payload)


def _train_custom_simulation_model(payload: SimulationTrainIn) -> dict:
    dataset = build_training_dataset(
        payload.device_id,
        label_version=payload.label_version,
        selected_session_ids=payload.session_ids,
    )
    readiness = dataset["readiness"]
    if not readiness["ready"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(readiness["blockers"]),
        )
    rows = dataset["rows"]

    destination = custom_model_path()
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    archive_temporary: Path | None = None
    archived: Path | None = None
    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    try:
        artifact = train_simulation_model(
            rows,
            output_path=temporary,
            model_id=CUSTOM_MODEL_ID,
            version=version,
            source_context=dataset["source_context"],
        )
        load_simulation_model(temporary)
        destination.parent.mkdir(parents=True, exist_ok=True)
        archive_directory = destination.parent / "runs"
        archive_directory.mkdir(parents=True, exist_ok=True)
        archived = archive_directory / (
            f"{CUSTOM_MODEL_ID}-{version}-{artifact['hash']}.json"
        )
        if not archived.exists():
            archive_temporary = archive_directory / (
                f".{archived.name}.{uuid4().hex}.tmp"
            )
            shutil.copyfile(temporary, archive_temporary)
            os.replace(archive_temporary, archived)
        os.replace(temporary, destination)
    except SimulationModelError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
        if archive_temporary is not None:
            archive_temporary.unlink(missing_ok=True)

    metrics = artifact["metrics"]
    source_manifest = artifact["source_manifest"]
    if archived is None:  # pragma: no cover - assigned before successful replace
        raise RuntimeError("training artifact archive path is unavailable")
    return {
        "model_id": artifact["model_id"],
        "version": artifact["version"],
        "status": "ready",
        "data_kind": artifact["data_kind"],
        "data_origin": artifact["data_origin"],
        "deployment_mode": artifact["deployment_mode"],
        "session_count": source_manifest["session_count"],
        "sample_count": len(rows),
        "labelled_sample_count": metrics["labelled_samples"],
        "excluded_unknown_samples": metrics["excluded_unknown_samples"],
        "excluded_warmup_samples": metrics["excluded_warmup_samples"],
        "excluded_invalid_ultrasonic_samples": dataset["source_context"][
            "training_input_excluded_invalid_ultrasonic_samples"
        ],
        "artifact_hash": artifact["hash"],
        "dataset_hash": source_manifest["dataset_hash"],
        "artifact_file": destination.name,
        "archived_artifact_file": str(Path("runs") / archived.name).replace("\\", "/"),
        "selection": readiness["selection"],
        "evidence_quality": readiness["evidence_quality"],
        "training_config": artifact["training_config"],
        "source_manifest": source_manifest,
        "metrics": metrics,
    }


@app.get("/api/v1/telemetry/latest", response_model=TelemetryRecord)
def get_latest_telemetry(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    record = latest_telemetry(device_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"设备 {device_id} 暂无遥测数据",
        )
    return record


@app.get("/api/v1/telemetry", response_model=list[TelemetryRecord])
def get_telemetry_history(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict]:
    return telemetry_history(device_id, limit)


@app.get("/api/v1/environment", response_model=EnvironmentResponse)
def get_environment(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> EnvironmentResponse:
    return _environment_for_device(device_id)


@app.get("/api/v1/risk", response_model=RiskResponse)
def get_risk(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    telemetry = latest_telemetry(device_id)
    if telemetry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No telemetry for device {device_id}",
        )
    environment = _environment_for_device(device_id)
    location = get_device_location(device_id)
    selected_model_id = get_selected_model_id(device_id)
    try:
        return build_selected_risk_result(
            selected_model_id,
            getattr(app.state, "risk_model", None),
            telemetry,
            telemetry_history(device_id, 64),
            environment,
            location,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Selected model cannot produce a valid result",
        ) from exc


@app.get("/api/v1/locations/search", response_model=list[LocationSearchResult])
def search_locations(
    q: Annotated[str, Query(min_length=2, max_length=80)],
    count: Annotated[int, Query(ge=1, le=20)] = 8,
) -> list[LocationSearchResult]:
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="地区名称至少需要 2 个字符",
        )
    try:
        return search_environment_locations(query, count)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="地区搜索服务暂不可用",
        ) from exc


@app.get("/api/v1/locations/presets", response_model=list[LocationSearchResult])
def location_presets() -> list[LocationSearchResult]:
    return get_location_presets()


@app.get("/api/v1/device-location", response_model=DeviceLocationRecord)
def read_device_location(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    record = get_device_location(device_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"设备 {device_id} 尚未选择地区",
        )
    return record


@app.put("/api/v1/device-location", response_model=DeviceLocationRecord)
def save_device_location(payload: DeviceLocationIn) -> dict:
    values = payload.model_dump()
    values["display_location"] = payload.display_location.upper()
    return upsert_device_location(values)


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        healthy = database_is_healthy()
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SQLite unavailable",
        ) from exc
    if not healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SQLite health check failed",
        )
    return HealthResponse(
        status="ok",
        database="ok",
        server_time=datetime.now(timezone.utc),
    )
