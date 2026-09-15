from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.deps import RequestContext
from app.errors import AppError, error_body
from app.logging import new_request_id, setup_logging
from app.routers import router as api_router
from app.routers.workspace import router as workspace_router
from app.routers.intelligence import router as intelligence_router
from app.routers.drive import router as drive_router
from app.routers.workspace_enterprise import router as workspace_enterprise_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level, settings.app_name)
    logger.info("api.startup", extra={"operation": "startup"})
    yield
    engine.dispose()
    logger.info("api.shutdown", extra={"operation": "shutdown"})


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Vridhi API",
        version="0.1.0",
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    cors_origins = {
        settings.frontend_url.rstrip("/"),
        "http://localhost:5173",
        "http://localhost:8443",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8443",
    }
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or new_request_id()
        request.state.ctx = RequestContext(request_id=request_id)
        started = time.perf_counter()
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-Id"] = request_id
        ctx: RequestContext = request.state.ctx
        logger.info(
            "request.completed",
            extra={
                "request_id": request_id,
                "operation": f"{request.method} {request.url.path}",
                "status": response.status_code,
                "latency_ms": latency_ms,
                "tenant_id": str(ctx.membership.tenant_id) if ctx.membership else None,
                "user_id": str(ctx.user.id) if ctx.user else None,
            },
        )
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        request_id = getattr(request.state, "ctx", RequestContext(request_id=new_request_id())).request_id
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, request_id),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, _exc: RequestValidationError):
        request_id = getattr(request.state, "ctx", RequestContext(request_id=new_request_id())).request_id
        return JSONResponse(
            status_code=400,
            content=error_body("VALIDATION_ERROR", "Request validation failed.", request_id),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "ctx", RequestContext(request_id=new_request_id())).request_id
        logger.exception("Unhandled error", extra={"request_id": request_id})
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "An unexpected error occurred.", request_id),
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
            return {"status": "ready"}
        except Exception:
            return JSONResponse(
                status_code=503,
                content=error_body("NOT_READY", "Database is unavailable.", None),
            )

    app.include_router(api_router)
    app.include_router(workspace_router)
    app.include_router(intelligence_router)
    app.include_router(drive_router)
    app.include_router(workspace_enterprise_router)
    return app


app = create_app()
