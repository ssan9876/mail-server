"""
Domain exceptions + FastAPI handlers.

Service/business code raises these; the registered handlers translate them into
consistent JSON error envelopes so the API never leaks tracebacks.
"""
from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base application error mapped to an HTTP response."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(self, message: str | None = None, *, code: str | None = None):
        self.message = message or self.__class__.__name__
        if code:
            self.code = code
        super().__init__(self.message)


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_error"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class ExternalServiceError(AppError):
    """An upstream dependency (e.g. Cloudflare) failed."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "external_service_error"


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthenticationError) else None
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.code, exc.message),
        headers=headers,
    )


def register_exception_handlers(app) -> None:
    app.add_exception_handler(AppError, app_error_handler)
