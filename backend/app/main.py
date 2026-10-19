"""FastAPI application factory."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

import redis.exceptions
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from app.api.routers import health, sessions, streams
from app.container import Container
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import log_event, setup_logging, trace_id_var

log = logging.getLogger("app")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or (container.settings if container else get_settings())
    setup_logging(settings.log_level, settings.log_format or "json")
    warnings = settings.validate_startup()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = container or Container.build(settings)
        app.state.container = c
        for w in warnings:
            log.warning(w)
        if settings.run_migrations_on_startup:
            from app.db.migrate import upgrade_head

            try:
                await upgrade_head(settings)
            except Exception as e:  # noqa: BLE001 - keep serving; /api/ready reports the DB problem
                log_event(log, "migrations.failed", logging.ERROR, error=type(e).__name__)
        log_event(log, "app.startup", instance=settings.instance_id, **settings.describe())
        c.start_background()
        try:
            yield
        finally:
            log_event(log, "app.shutdown", instance=settings.instance_id)
            await c.shutdown()

    app = FastAPI(title="AI Chat API", version="1.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )

    @app.middleware("http")
    async def trace(request: Request, call_next):
        tid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        trace_id_var.set(tid)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = tid
        return response

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return JSONResponse(exc.to_body(), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        msg = f"{loc}: {first.get('msg', '参数非法')}" if loc else "请求参数非法"
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": msg}}, status_code=400)

    async def dependency_error(_: Request, exc: Exception):
        log_event(log, "dependency.unavailable", logging.ERROR, error=type(exc).__name__)
        return JSONResponse(
            {"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": "依赖服务不可用，暂时无法处理请求。"}},
            status_code=503,
        )

    for exc_type in (
        OperationalError,
        DBAPIError,
        SATimeoutError,
        redis.exceptions.ConnectionError,
        redis.exceptions.TimeoutError,
        ConnectionError,
        OSError,
    ):
        app.add_exception_handler(exc_type, dependency_error)

    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(streams.router)
    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app()
