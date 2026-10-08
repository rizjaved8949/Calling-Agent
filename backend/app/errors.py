"""
One failure shape for the whole API: {"error": {"message", "code", "details"}}.

The frontend reads `error.message` and shows it. Anything that reaches the
client without passing through here would break that contract, which is why
even an unhandled exception is funnelled into the same envelope.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import settings

log = logging.getLogger("calling-agent")


class AppError(Exception):
    """An error a route raises on purpose, carrying the status to send."""

    def __init__(self, status: int, message: str, *, code: str = "error", details: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.details = details


class NotFound(AppError):
    def __init__(self, what: str):
        super().__init__(404, f"{what} not found", code="not_found")


class Conflict(AppError):
    def __init__(self, message: str):
        super().__init__(409, message, code="conflict")


class Unauthorized(AppError):
    def __init__(self, message: str = "Not authorised"):
        super().__init__(401, message, code="unauthorized")


class Forbidden(AppError):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(403, message, code="forbidden")


class UpstreamError(AppError):
    """A dependency we do not control refused or failed.

    502 rather than 500: the caller's request was fine, and a retry may work.
    """

    def __init__(self, message: str, *, details: Any = None):
        super().__init__(502, message, code="upstream_error", details=details)


def _envelope(status: int, message: str, code: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"message": message, "code": code}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
        if exc.status >= 500:
            log.warning("%s: %s", exc.code, exc.message)
        return _envelope(exc.status, exc.message, exc.code, exc.details)

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        return _envelope(exc.status_code, str(exc.detail), "http_error")

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # 422 with the offending fields. Pydantic's own errors carry exception
        # objects that JSON cannot encode, so only the readable parts are sent.
        details = [
            {"field": ".".join(str(p) for p in err.get("loc", ())), "message": err.get("msg", "")}
            for err in exc.errors()
        ]
        return _envelope(422, "The request is not valid.", "validation_error", details)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Logged with a stack trace; the client gets a flat message in
        # production, because an exception string can carry a token or a query.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        message = "Something went wrong." if settings.is_production else f"{type(exc).__name__}: {exc}"
        return _envelope(500, message, "internal_error")
