"""Exception handlers for the FastAPI REST tier.

Ports the Flask app's error contract (api/app.py) so the front end sees the
same ``{"error", "message", "status"}`` JSON body and status codes:

  * ``ValueError``            -> 400 ValidationError
  * ``DuplicateSymbolError``  -> 409 DuplicateSymbol
  * ``RuntimeError``          -> 422 (plan-build failures in the Flask source)
  * ``HTTPException``         -> its own status / detail
  * anything else             -> 500 InternalServerError
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from quantcore.services.portfolio import DuplicateSymbolError

from .json_response import QuantCoreJSONResponse


def _body(error: str, message: str, status: int) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        status_code=status,
        content={"error": error, "message": message, "status": status},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the legacy-compatible exception handlers to ``app``."""

    @app.exception_handler(ValueError)
    async def _handle_value_error(request: Request, exc: ValueError):  # noqa: ANN202
        # DuplicateSymbolError subclasses ValueError in some call paths; keep it 409.
        if isinstance(exc, DuplicateSymbolError):
            return _body("DuplicateSymbol", str(exc), 409)
        return _body("ValidationError", str(exc), 400)

    @app.exception_handler(DuplicateSymbolError)
    async def _handle_duplicate(request: Request, exc: DuplicateSymbolError):  # noqa: ANN202
        return _body("DuplicateSymbol", str(exc), 409)

    @app.exception_handler(RuntimeError)
    async def _handle_runtime(request: Request, exc: RuntimeError):  # noqa: ANN202
        return _body("UnprocessableEntity", str(exc), 422)

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(request: Request, exc: RequestValidationError):  # noqa: ANN202
        return _body("ValidationError", str(exc), 422)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException):  # noqa: ANN202
        return _body(
            getattr(exc, "detail", "Error") if isinstance(exc.detail, str) else "Error",
            str(exc.detail),
            exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception):  # noqa: ANN202
        return _body("InternalServerError", str(exc), 500)
