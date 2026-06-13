"""PolygonGateway — all Polygon.io network access for the services layer.

Architectural standard v2 §5.1: services never make HTTP calls directly; they
receive this gateway via constructor injection. It owns the Polygon options
snapshot endpoint, including pagination (following ``next_url`` cursors) and the
plan/auth error contract used by the historical-options backfill.

Requires ``POLYGON_API_KEY`` in the environment (.env or shell). The Starter
plan ($29/mo) or higher is required for historical options snapshots — the free
tier returns 403.
"""

from __future__ import annotations

import os

import requests


class PolygonPlanError(Exception):
    """Raised when Polygon returns 403 — invalid key or plan lacks history access."""

    def __init__(self, status_code: int, message: str | None = None):
        self.status_code = status_code
        super().__init__(message or "Polygon API key is invalid or the account plan does "
                                    "not include historical options snapshots. "
                                    "A Starter plan ($29/mo) or higher is required.")


class PolygonGateway:
    BASE_URL = "https://api.polygon.io"

    def __init__(self, api_key: str | None = None):
        self._api_key = (api_key if api_key is not None
                         else os.environ.get("POLYGON_API_KEY", "")).strip()

    @property
    def has_key(self) -> bool:
        return bool(self._api_key)

    def option_snapshots(self, ticker: str, date_str: str, timeout: float = 30.0):
        """Fetch every option-contract snapshot for one date, following pagination.

        Returns a list of raw Polygon contract dicts. Returns ``None`` when
        Polygon has no data for that date (HTTP 404 — holiday, pre-listing).
        Raises :class:`PolygonPlanError` on 403 (invalid key / insufficient
        plan), and propagates :class:`requests.RequestException` on transport
        errors so callers can record per-date failures.
        """
        ticker = ticker.upper()
        contracts_all: list[dict] = []
        url: str | None = (
            f"{self.BASE_URL}/v3/snapshot/options/{ticker}"
            f"?date={date_str}&limit=250&apiKey={self._api_key}"
        )
        while url:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 403:
                raise PolygonPlanError(resp.status_code)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
            contracts_all.extend(body.get("results") or [])
            next_url = body.get("next_url")
            url = f"{next_url}&apiKey={self._api_key}" if next_url else None
        return contracts_all
