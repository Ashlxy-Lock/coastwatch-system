"""Minimal authenticated API intended to be exposed through a public tunnel."""

import json
import logging
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import (
    LOGIN_HTML,
    AdminAuthConfig,
    AdminLoginIn,
    AdminSessionResponse,
    clear_admin_session_cookie,
    clear_login_failures,
    current_admin_session,
    enforce_login_rate_limit,
    issue_admin_session,
    load_admin_auth_config,
    record_login_failure,
    require_admin_csrf,
    require_admin_session,
    reset_login_failures,
    set_admin_session_cookie,
    verify_admin_credentials,
)
from .dashboard import render_dashboard
from .database import (
    database_is_healthy,
    get_device_location,
    init_database,
    insert_telemetry,
    latest_telemetry,
    telemetry_history,
    upsert_device_location,
)
from .environment import (
    get_device_location_preset,
    get_device_location_presets,
    get_geo_device_location_preset,
    parse_geo_location_id,
    search_device_locations,
)
from .environment import get_environment as load_environment
from .experiment_store import recover_interrupted_training_runs
from .main import app as admin_backend_app
from .model_registry import (
    CUSTOM_MODEL_ID,
    OFFICIAL_MODEL_ID,
    get_model_catalog,
    get_selected_model_id,
    init_model_registry,
    select_device_model,
)
from .risk_dispatch import (
    build_official_sensor_environment,
    build_selected_risk_result,
)
from .risk_model import load_risk_model
from .schemas import (
    DeviceId,
    DeviceLocationPreset,
    DeviceLocationPresetIn,
    DeviceLocationPresetSelection,
    DeviceModelSelection,
    DeviceModelSelectionIn,
    EnvironmentResponse,
    HealthResponse,
    ModelCatalogResponse,
    RiskResponse,
    TelemetryIn,
    TelemetryRecord,
)
from .simulation_artifacts import SIMULATION_ARTIFACT_LOCK
from .simulation_schemas import (
    SimulationSessionRecord,
    SimulationSessionStartIn,
    SimulationSessionStopIn,
)
from .simulation_service import build_simulated_environment
from .simulation_store import (
    SimulationConflictError,
    SimulationNotFoundError,
    SimulationValidationError,
    get_active_simulation_session,
    get_device_simulation_scenario,
    start_simulation_session,
    stop_simulation_session,
)

DEVICE_TOKEN_ENV = "COAST_DEVICE_TOKEN"
RISK_MODEL_PATH_ENV = "COAST_RISK_MODEL_PATH"
MAX_ADMIN_LOGIN_BODY_BYTES = 4096
logger = logging.getLogger(__name__)


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


def _read_device_token() -> str:
    token = os.getenv(DEVICE_TOKEN_ENV)
    if token is None or not token.strip():
        raise RuntimeError(
            f"{DEVICE_TOKEN_ENV} must be set before starting the public gateway"
        )
    return token


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.device_token = _read_device_token()
    app.state.admin_auth = load_admin_auth_config()
    reset_login_failures()
    init_database()
    init_model_registry()
    with SIMULATION_ARTIFACT_LOCK:
        recover_interrupted_training_runs(max_age_seconds=0.0)
    configured_model_path = os.getenv(RISK_MODEL_PATH_ENV, "").strip()
    try:
        app.state.risk_model = load_risk_model(
            Path(configured_model_path) if configured_model_path else None
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Risk model unavailable; using rule fallback: %s", exc)
        app.state.risk_model = None
    admin_backend_app.state.risk_model = app.state.risk_model
    try:
        yield
    finally:
        if hasattr(admin_backend_app.state, "risk_model"):
            del admin_backend_app.state.risk_model
        del app.state.risk_model
        del app.state.admin_auth
        del app.state.device_token


def require_device_token(
    request: Request,
    presented_token: Annotated[str | None, Header(alias="X-Device-Token")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
        return
    expected_token = getattr(request.app.state, "device_token", None)
    if not isinstance(expected_token, str) or not expected_token.strip():
        # Lifespan normally prevents this state. Keep the request path fail-closed
        # for ASGI hosts or tests that bypass lifespan handling.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway is not configured",
        )

    bearer_token = ""
    if isinstance(authorization, str):
        scheme, separator, credentials = authorization.partition(" ")
        if separator and scheme.lower() == "bearer":
            bearer_token = credentials.strip()
    candidate = presented_token or bearer_token
    if not secrets.compare_digest(
        candidate.encode("utf-8"), expected_token.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


class _AdminLoginBodyLimitMiddleware:
    """Reject oversized login bodies before FastAPI/Pydantic parses them."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method", "").upper() != "POST"
            or scope.get("path") != "/admin/api/auth/login"
        ):
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        content_lengths = [
            value for name, value in headers if name.lower() == b"content-length"
        ]
        if len(content_lengths) > 1:
            await self._reject(
                scope,
                receive,
                send,
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Multiple Content-Length values are not allowed",
            )
            return
        declared_length: int | None = None
        if content_lengths:
            try:
                length_text = content_lengths[0].decode("ascii")
                if not length_text.isdigit():
                    raise ValueError
                declared_length = int(length_text)
            except (UnicodeDecodeError, ValueError):
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid Content-Length",
                )
                return
            if declared_length > self.max_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Administrator login request is too large",
                )
                return

        chunks: list[bytes] = []
        actual_length = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Incomplete administrator login request",
                )
                return
            chunk = message.get("body", b"")
            actual_length += len(chunk)
            if actual_length > self.max_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Administrator login request is too large",
                )
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        if declared_length is not None and actual_length != declared_length:
            await self._reject(
                scope,
                receive,
                send,
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content-Length does not match the request body",
            )
            return

        body = b"".join(chunks)
        delivered = False

        async def replay_body() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_body, send)


app = FastAPI(
    title="Coastal Warning Device Gateway",
    version="0.2.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    dependencies=[Depends(require_device_token)],
)
app.add_middleware(_AdminLoginBodyLimitMiddleware, max_bytes=MAX_ADMIN_LOGIN_BODY_BYTES)


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


@app.middleware("http")
async def conceal_unsupported_methods(request: Request, call_next):
    response = await call_next(request)
    if response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Not Found"},
        )
    return response


def _admin_auth_config(request: Request) -> AdminAuthConfig:
    config = getattr(request.app.state, "admin_auth", None)
    if not isinstance(config, AdminAuthConfig):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Administrator authentication is not configured",
        )
    return config


def _admin_error_response(exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


def _secure_admin_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    return response


@app.middleware("http")
async def protect_admin_namespace(request: Request, call_next):
    path = request.url.path
    if not (path == "/admin" or path.startswith("/admin/")):
        return await call_next(request)

    public_or_explicit_routes = {
        "/admin",
        "/admin/login",
        "/admin/console",
        "/admin/api/auth/login",
        "/admin/api/auth/session",
        "/admin/api/auth/logout",
    }
    if path in public_or_explicit_routes:
        return _secure_admin_response(await call_next(request))
    if not path.startswith("/admin/api/v1/"):
        return _secure_admin_response(
            JSONResponse(status_code=404, content={"detail": "Not Found"})
        )

    try:
        config = _admin_auth_config(request)
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            require_admin_csrf(request, config)
        else:
            require_admin_session(request, config)
    except HTTPException as exc:
        return _secure_admin_response(_admin_error_response(exc))
    return _secure_admin_response(await call_next(request))


@app.get("/admin", include_in_schema=False)
def admin_entry(request: Request):
    config = _admin_auth_config(request)
    destination = (
        "/admin/console"
        if current_admin_session(request, config) is not None
        else "/admin/login"
    )
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
def admin_login_page(request: Request):
    config = _admin_auth_config(request)
    if current_admin_session(request, config) is not None:
        return RedirectResponse("/admin/console", status_code=status.HTTP_303_SEE_OTHER)
    return HTMLResponse(LOGIN_HTML, headers={"Cache-Control": "no-store"})


@app.post(
    "/admin/api/auth/login",
    response_model=AdminSessionResponse,
    include_in_schema=False,
)
def admin_login(request: Request, payload: AdminLoginIn):
    config = _admin_auth_config(request)
    client_key = enforce_login_rate_limit(request)
    if not verify_admin_credentials(config, payload.username, payload.password):
        record_login_failure(client_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid administrator credentials",
        )
    clear_login_failures(client_key)
    token, session = issue_admin_session(config)
    response = JSONResponse(
        {
            "authenticated": True,
            "username": session.username,
            "csrf_token": session.csrf_token,
        }
    )
    set_admin_session_cookie(response, token)
    return response


@app.get(
    "/admin/api/auth/session",
    response_model=AdminSessionResponse,
    include_in_schema=False,
)
def read_admin_session(request: Request) -> AdminSessionResponse:
    session = require_admin_session(request, _admin_auth_config(request))
    return AdminSessionResponse(
        authenticated=True,
        username=session.username,
        csrf_token=session.csrf_token,
    )


@app.post("/admin/api/auth/logout", include_in_schema=False)
def admin_logout(request: Request) -> JSONResponse:
    require_admin_csrf(request, _admin_auth_config(request))
    response = JSONResponse({"authenticated": False})
    clear_admin_session_cookie(response)
    return response


@app.get("/admin/console", response_class=HTMLResponse, include_in_schema=False)
def admin_console(request: Request):
    config = _admin_auth_config(request)
    if current_admin_session(request, config) is None:
        return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    return HTMLResponse(
        render_dashboard(api_prefix="/admin", admin_mode=True),
        headers={"Cache-Control": "no-store"},
    )


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


@app.post(
    "/api/v1/simulations/sessions/{session_id}/stop",
    response_model=SimulationSessionRecord,
)
def complete_simulation_session(
    session_id: str, payload: SimulationSessionStopIn
) -> dict:
    return stop_simulation_session(session_id, payload.device_id)


@app.get("/api/v1/environment", response_model=EnvironmentResponse)
def get_environment(
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> EnvironmentResponse:
    return _environment_for_device(device_id)


@app.get("/api/v1/risk", response_model=RiskResponse)
def get_risk(
    request: Request,
    device_id: Annotated[DeviceId, Query()] = "COAST_01",
) -> dict:
    telemetry = latest_telemetry(device_id)
    if telemetry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No telemetry for this device",
        )
    environment = _environment_for_device(device_id)
    location = get_device_location(device_id)
    selected_model_id = get_selected_model_id(device_id)
    try:
        return build_selected_risk_result(
            selected_model_id,
            getattr(request.app.state, "risk_model", None),
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


@app.get(
    "/api/v1/locations/presets",
    response_model=list[DeviceLocationPreset],
)
def location_presets() -> list[DeviceLocationPreset]:
    return get_device_location_presets()


@app.get(
    "/api/v1/locations/search",
    response_model=list[DeviceLocationPreset],
)
def search_locations(
    q: Annotated[str, Query(min_length=2, max_length=80)],
    count: Annotated[int, Query(ge=1, le=8)] = 8,
) -> list[DeviceLocationPreset]:
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Location query must contain at least two characters",
        )
    try:
        return search_device_locations(query, count)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Location search provider unavailable",
        ) from exc


@app.put(
    "/api/v1/device-location",
    response_model=DeviceLocationPresetSelection,
)
def select_device_location(
    payload: DeviceLocationPresetIn,
) -> DeviceLocationPresetSelection:
    preset = get_device_location_preset(payload.location_id)
    if preset is None:
        provider_id = parse_geo_location_id(payload.location_id)
        if provider_id is not None:
            try:
                preset = get_geo_device_location_preset(provider_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Location provider unavailable",
                ) from exc
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown location ID",
        )
    upsert_device_location(
        {
            "device_id": payload.device_id,
            "kind": preset.kind,
            "location": preset.name,
            "display_location": preset.display_location,
            "latitude": preset.lat,
            "longitude": preset.lon,
        }
    )
    return DeviceLocationPresetSelection(
        device_id=payload.device_id,
        **preset.model_dump(),
    )


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


# Registered last so the explicit login/session/console routes above win before
# the protected internal FastAPI application handles /admin/api/v1/*.
app.mount("/admin", admin_backend_app, name="admin-backend")
