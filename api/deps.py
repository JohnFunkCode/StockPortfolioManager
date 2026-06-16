"""Shared dependencies for the FastAPI REST tier.

The composition root stays in ``quantcore.services.registry.get_services``;
this module only re-exports it as a FastAPI dependency seam and provides the
small shared helpers the Flask app defined inline (portfolio / watchlist
loaders) so routers stay exactly one service call deep.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from quantcore.services.registry import Services, get_services

from .json_response import QuantCoreJSONResponse

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def load_portfolio(owner: str = "john") -> list[dict]:
    """Load an owner's positions from the DB-backed positions table."""
    return get_services().portfolio.list_positions(owner)


def load_watchlist() -> list[dict]:
    """Load watchlist entries from ./watchlist.yaml (matches the Flask loader)."""
    wl_path = PROJECT_ROOT / "watchlist.yaml"
    if not wl_path.exists():
        return []
    with open(wl_path) as fh:
        entries = yaml.safe_load(fh) or []
    rows = []
    for e in entries:
        rows.append(
            {
                "name": e.get("name", ""),
                "symbol": str(e.get("symbol", "")).upper(),
                "currency": str(e.get("currency", "USD")).upper(),
                "purchase_price": None,
                "quantity": None,
                "purchase_date": None,
                "sale_price": None,
                "sale_date": None,
                "source": "watchlist",
                "tags": e.get("tags") or [],
            }
        )
    return rows
