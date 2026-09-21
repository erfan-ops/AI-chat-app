"""Application entry point: FastAPI app assembly, lifespan, middleware, error handling.

Run with: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger, structured
from app.exceptions import AppError, RateLimitError

logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("starting %s (%s env)", settings.app_name, settings.app_env)
    yield
    logger.info("shutting down %s", settings.app_name)


class AccessLogMiddleware:
    """Pure-ASGI access logging (safe for streaming responses)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            state = scope.get("state")
            user_id = state.get("user_id") if isinstance(state, dict) else None
            structured(
                logger,
                logging.INFO,
                "request",
                method=scope["method"],
                path=scope["path"],
                status=status_code,
                duration_ms=round(duration_ms, 1),
                user_id=user_id,
            )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Backend API for the AI chat application: authentication (including SMS "
            "two-step verification), AI characters, conversations, messages, memories, "
            "and streamed AI replies (SSE). "
            "All endpoints except `/auth/register`, `/auth/login` and `/auth/login/otp` "
            "require a Bearer token."
        ),
        lifespan=lifespan,
    )

    # CORS is configured explicitly from settings; credentials allowed with exact origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AccessLogMiddleware)

    app.include_router(api_router)

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        headers = (
            {"Retry-After": str(exc.retry_after_seconds or 60)}
            if isinstance(exc, RateLimitError)
            else None
        )
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}, headers=headers
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        # Full details go to the server log only; clients get a generic message.
        logger.exception("unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
