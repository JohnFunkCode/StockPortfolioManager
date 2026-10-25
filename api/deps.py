"""Shared dependencies for the FastAPI REST tier.

The composition root stays in ``quantcore.services.registry.get_services``;
this module only re-exports it as a FastAPI dependency seam and provides the
small shared helpers the Flask app defined inline (portfolio / watchlist
loaders) so routers stay exactly one service call deep.
"""

from __future__ import annotations

import logging

from quantcore.services.registry import Services, get_services

from .json_response import QuantCoreJSONResponse


def services() -> Services:
    """FastAPI dependency returning the shared, lazily-built ``Services``."""
    return get_services()


def route_error(message: str, status: int) -> QuantCoreJSONResponse:
    """Build the legacy route-level error body ``{"error", "status"}``.

    The Flask routes returned this shape for in-handler validation/not-found
    cases (distinct from the framework error handler's ``{"error","message",
    "status"}``). Preserved verbatim so the front end's error parsing is
    unchanged.
    """
    return QuantCoreJSONResponse({"error": message, "status": status}, status_code=status)


def route_error_plain(message: str, status: int) -> QuantCoreJSONResponse:
    """Build the bare ``{"error": message}`` body (no ``status`` key).

    The portfolio / watchlist / securities Flask routes returned this simpler
    shape for in-handler validation/not-found cases (the harvester routes used
    ``route_error`` with the extra ``status`` key). Preserved verbatim.
    """
    return QuantCoreJSONResponse({"error": message}, status_code=status)


# What a caller sees for an unexpected failure. The exception's own text stays
# server-side: it can carry a DSN, an internal URL or a driver message, and
# quantcore-api is public (JWT-gated, allUsers).
INTERNAL_ERROR = "internal error"


def public_message(exc: BaseException) -> str:
    """What a caller may read of ``exc``: a ValueError's text, else INTERNAL_ERROR.

    A ValueError is how the services say "bad input" ("bad interval", "out of
    range"), and api/errors.py already answers it with its text. Anything else
    may be a driver error carrying a DSN.
    """
    return str(exc) if isinstance(exc, ValueError) else INTERNAL_ERROR


def exception_kind(exc: BaseException) -> str:
    """``module.QualName`` of an exception — loggable, unlike its text."""
    return f"{type(exc).__module__}.{type(exc).__qualname__}"


def log_route_failure(log: logging.Logger, what: str, exc: BaseException, **context: str) -> None:
    """One WARNING naming the route, its context and the exception class only."""
    ctx = "".join(f" {k}={v}" for k, v in context.items())
    log.warning("%s failed%s (%s)", what, ctx, exception_kind(exc))


def route_internal_error(
    log: logging.Logger, what: str, exc: BaseException, **context: str
) -> QuantCoreJSONResponse:
    """The plain ``{"error"}`` 500 for a caught exception (see public_message)."""
    log_route_failure(log, what, exc, **context)
    return route_error_plain(public_message(exc), 500)


def load_portfolio(owner: str) -> list[dict]:
    """Load an owner's positions from the DB-backed positions table."""
    return get_services().portfolio.list_positions(owner)


def load_watchlist() -> list[dict]:
    """Load watchlist entries from the DB-backed watchlist table.

    Issue #83: this read the repo's ``watchlist.yaml``, which is baked into the
    container image and read-only in Cloud Run, so anything the UI added
    vanished. Repointing this one function also fixes the sentiment summary
    and the screener, which both go through it.
    """
    return get_services().watchlist.list_entries()
