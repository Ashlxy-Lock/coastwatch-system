"""FastAPI factory for ImpactNet shadow inference."""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from .model_loader import BundlePredictor, InsufficientDataError
from .schemas import (
    FeaturePredictionRequest,
    HealthResponse,
    InsufficientDataResponse,
    ModelInfoResponse,
    PredictionResponse,
)

FeatureProvider = Callable[[str], FeaturePredictionRequest | Awaitable[FeaturePredictionRequest]]
LOGGER = logging.getLogger("coastwatch_impact.serve")
DISCLAIMER = "Research output only; official warnings remain authoritative."


def create_app(
    predictor: BundlePredictor | None = None,
    *,
    feature_provider: FeatureProvider | None = None,
) -> FastAPI:
    """Create a service without loading a bundle as an import side effect."""

    app = FastAPI(
        title="CoastWatch ImpactNet Shadow API",
        version="1.0.0",
        description="Research output only; official warnings remain authoritative.",
    )
    app.state.predictor = predictor
    app.state.feature_provider = feature_provider

    @app.middleware("http")
    async def audit_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-shadow-mode"] = "true"
        LOGGER.info(
            "shadow_request",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "shadow_mode": True,
            },
        )
        return response

    @app.exception_handler(InsufficientDataError)
    async def insufficient_handler(request: Request, error: InsufficientDataError) -> JSONResponse:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        payload = InsufficientDataResponse(request_id=request_id, reason=str(error))
        return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "request_id": request.headers.get("x-request-id", str(uuid.uuid4())),
                "status": "invalid_request",
                "detail": jsonable_encoder(error.errors()),
                "shadow_mode": True,
                "disclaimer": DISCLAIMER,
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "request_id": request.headers.get("x-request-id", str(uuid.uuid4())),
                "status": "service_error",
                "detail": error.detail,
                "shadow_mode": True,
                "disclaimer": DISCLAIMER,
            },
            headers=error.headers,
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, error: Exception) -> JSONResponse:
        LOGGER.exception("shadow_internal_error", exc_info=error)
        return JSONResponse(
            status_code=500,
            content={
                "request_id": request.headers.get("x-request-id", str(uuid.uuid4())),
                "status": "internal_error",
                "detail": "shadow inference failed without producing a probability",
                "shadow_mode": True,
                "disclaimer": DISCLAIMER,
            },
        )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        active: BundlePredictor | None = app.state.predictor
        return HealthResponse(
            status="ok" if active is not None else "not_ready",
            loaded_models=[] if active is None else active.loaded_versions,
            shadow_mode=True,
        )

    @app.get("/v1/model-info", response_model=ModelInfoResponse)
    def model_info() -> ModelInfoResponse:
        active: BundlePredictor | None = app.state.predictor
        if active is None:
            raise HTTPException(status_code=503, detail="no verified model bundle is loaded")
        return ModelInfoResponse(manifest=active.model_info(), shadow_mode=True)

    @app.post("/v1/predict/features", response_model=PredictionResponse)
    def predict_features(request: Request, payload: FeaturePredictionRequest) -> PredictionResponse:
        active: BundlePredictor | None = app.state.predictor
        if active is None:
            raise HTTPException(status_code=503, detail="no verified model bundle is loaded")
        return active.predict(payload, request_id=request.state.request_id)

    @app.post("/v1/predict/site/{site_id}", response_model=PredictionResponse)
    async def predict_site(request: Request, site_id: str) -> PredictionResponse:
        active: BundlePredictor | None = app.state.predictor
        provider: FeatureProvider | None = app.state.feature_provider
        if active is None:
            raise HTTPException(status_code=503, detail="no verified model bundle is loaded")
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail="live feature provider is not configured; no probability was generated",
            )
        value = provider(site_id)
        payload = await value if inspect.isawaitable(value) else value
        if payload.site_id != site_id:
            raise HTTPException(status_code=500, detail="feature provider returned the wrong site")
        return active.predict(payload, request_id=request.state.request_id)

    return app


# Import-safe default for ASGI discovery. It reports not_ready until configured.
app = create_app()


__all__ = ["FeatureProvider", "app", "create_app"]
