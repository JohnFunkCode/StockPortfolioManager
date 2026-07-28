"""WatchlistService — business logic for the global watchlist (issue #83).

Adapters (the REST routes in api/routers/portfolio.py, the MCP wrapper, the
daily report, scripts/import_watchlist.py) call
``get_services().watchlist.<method>``. Normalization and the "already watching"
policy live here; the repository below only reports what SQL did.

The list is global by decision — one shared watchlist, no owner scoping. The
`added_by` value is recorded for audit and never gates a read or a delete.

``DuplicateSymbolError`` is imported from the portfolio service rather than
redefined: ``api/errors.py`` already maps that one class to a 409, and a second
identically-named class would be a different type that silently falls through
to a 500. This is an exception type, not a service call — the routers stay one
service call deep, and portfolio.py does not import this module, so there is no
cycle.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import yaml

from quantcore.repositories.watchlist_repository import WatchlistRepository
from quantcore.services.portfolio import DuplicateSymbolError

logger = logging.getLogger(__name__)


def _clean_tags(tags: Optional[List[str]]) -> List[str]:
    """Drop blanks and surrounding whitespace, preserving order."""
    if not tags:
        return []
    return [str(t).strip() for t in tags if str(t).strip()]


def _normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a YAML/body dict into the canonical watchlist shape.

    Name defaults to the symbol so the front end never renders a blank label
    for an entry added by ticker alone.
    """
    symbol = str(raw.get("symbol") or "").strip().upper()
    name = str(raw.get("name") or "").strip() or symbol
    currency = str(raw.get("currency") or "USD").strip().upper() or "USD"
    return {
        "symbol": symbol,
        "name": name,
        "currency": currency,
        "tags": _clean_tags(raw.get("tags")),
        "added_by": raw.get("added_by"),
    }


class WatchlistService:
    """Watchlist reads and writes, backed by ``WatchlistRepository``."""

    def __init__(self, repository: WatchlistRepository):
        self._repo = repository

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_entries(self) -> List[Dict[str, Any]]:
        """Every watchlist entry, in the legacy ``load_watchlist()`` shape."""
        return self._repo.list_entries()

    def count(self) -> int:
        return self._repo.count()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def add_entry(
        self,
        symbol: str,
        name: Optional[str] = None,
        currency: str = "USD",
        tags: Optional[List[str]] = None,
        added_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add one symbol to the watchlist.

        Raises ``ValueError`` for an empty symbol and ``DuplicateSymbolError``
        when it is already watched — the repository returns None there, having
        let the UNIQUE constraint settle the race rather than checking first.
        """
        row = _normalize({
            "symbol": symbol, "name": name, "currency": currency,
            "tags": tags, "added_by": added_by,
        })
        if not row["symbol"]:
            raise ValueError("symbol is required")

        entry_id = self._repo.add_entry(
            symbol=row["symbol"],
            name=row["name"],
            currency=row["currency"],
            tags=row["tags"],
            added_by=row["added_by"],
        )
        if entry_id is None:
            raise DuplicateSymbolError(f"{row['symbol']} is already in the watchlist")
        return {"symbol": row["symbol"], "name": row["name"]}

    def remove_entry(self, symbol: str) -> int:
        """Remove `symbol`. Returns rows removed — 0 means it wasn't watched."""
        if not str(symbol or "").strip():
            raise ValueError("symbol is required")
        return self._repo.remove_entry(symbol)

    def import_yaml(self, path: str) -> int:
        """Full-replace the watchlist with the entries in the YAML at `path`.

        Full-sync/replace, matching ``PortfolioService.import_csv``: re-running
        it converges rather than accumulating. Returns the number imported.
        """
        with open(path) as fh:
            entries = yaml.safe_load(fh) or []

        rows: List[Dict[str, Any]] = []
        for raw in entries:
            row = _normalize(raw or {})
            if not row["symbol"]:
                continue
            rows.append(row)

        skipped = len(entries) - len(rows)
        if skipped:
            logger.warning(
                "import_yaml(%s): skipped %d entr%s with no symbol",
                path, skipped, "y" if skipped == 1 else "ies",
            )
        return self._repo.replace_all(rows)
