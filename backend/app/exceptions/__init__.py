"""Application-level exceptions mapped to consistent HTTP error responses.

Every error the API intentionally returns is one of these; anything else is a
500 with the details logged server-side only.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all intentional HTTP errors."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, detail: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(detail)
        self.retry_after_seconds = retry_after_seconds


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
