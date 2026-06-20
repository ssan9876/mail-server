"""
Application entrypoint — wires settings, database, Redis, middleware, rate
limiting, exception handlers, and the versioned API router.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import RateLimitError, register_exception_handlers
from app.core.ratelimit import limiter
from app.core.redis import create_redis_pool
from app.services import user_service

logger = logging.getLogger("mailserver")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: open Redis, bootstrap the first superadmin. Shutdown: close Redis."""
    app.state.redis = create_redis_pool()

    try:
        async with AsyncSessionLocal() as db:
            created = await user_service.bootstrap_superadmin(db)
            if created:
                logger.info("Bootstrapped initial superadmin %s", created.email)
    except Exception:  # pragma: no cover - non-fatal so /health still works
        logger.warning("Superadmin bootstrap skipped (DB not ready?)", exc_info=True)

    yield

    await app.state.redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="mail-server API",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # --- Rate limiting (slowapi) ---
    app.state.limiter = limiter

    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        # Funnel slowapi's error through our standard JSON envelope.
        from app.core.exceptions import app_error_handler

        return await app_error_handler(request, RateLimitError("Rate limit exceeded."))

    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)

    # --- CORS ---
    if settings.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # --- Request ID + security headers ---
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        # Defense-in-depth headers (the edge proxy adds the SPA-facing CSP/HSTS).
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # --- Errors ---
    register_exception_handlers(app)

    # --- Routes ---
    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()
