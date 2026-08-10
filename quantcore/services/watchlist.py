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

    def __init__(self, repository: WatchlistRepository, yfinance: Any = None):
        self._repo = repository
        # The gateway is what makes `currency` a looked-up fact rather than a
        # field the caller guesses. Optional so the CRUD half of this service
        # stays constructible from a bare repository (import scripts, tests);
        # without it add_entry falls back to the supplied value.
        self._yf = yfinance

    # ------------------------------------------------------------------
    # Currency resolution
    # ------------------------------------------------------------------
    def resolve_currency(self, symbol: str) -> Optional[str]:
        """The currency `symbol` actually trades in, or ``None`` if unknown.

        ``info["currency"]`` is the trading currency, which is the one that
        matches ``marketCap`` — yfinance derives the cap from price × shares,
        so labelling it with anything else (``financialCurrency``, or whatever
        the user typed) is how ASSA-B.ST's SEK ends up rendered as dollars.

        Returns ``None`` rather than raising on any failure: Yahoo being slow
        or wrong is not a reason to refuse to watch a symbol. The caller
        decides what to fall back to.
        """
        if self._yf is None:
            return None
        try:
            info = self._yf.ticker_info(symbol) or {}
        except Exception as exc:  # noqa: BLE001 — any gateway failure is a miss
            logger.warning("currency lookup failed for %s: %s", symbol, exc)
            return None
        currency = str(info.get("currency") or "").strip().upper()
        return currency or None

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
        currency: Optional[str] = None,
        tags: Optional[List[str]] = None,
        added_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add one symbol to the watchlist.

        ``currency`` is a **fallback, not an instruction**: the currency is
        looked up from the exchange and the supplied value is used only when
        that lookup comes back empty. Three of the entries seeded from
        watchlist.yaml were declared USD and are not (ASSA-B.ST is Stockholm,
        AUTO.OL Oslo, NIB.F Frankfurt), which is enough to conclude that a
        human typing a currency next to a ticker gets it wrong often enough
        to stop asking. See ``scripts/repair_watchlist_currency.py`` for the
        rows already in the table.

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

        looked_up = self.resolve_currency(row["symbol"])
        if looked_up:
            if currency and looked_up != str(currency).strip().upper():
                logger.info(
                    "%s: storing %s from the exchange, not the %s that was passed",
                    row["symbol"], looked_up, str(currency).strip().upper(),
                )
            row["currency"] = looked_up
        elif self._yf is not None:
            # Only worth a warning if a lookup was actually attempted and came
            # back empty. With no gateway wired (import scripts, unit tests)
            # the supplied value is the design, not a degraded path.
            logger.warning(
                "%s: no currency from the exchange, falling back to %s",
                row["symbol"], row["currency"],
            )

        entry_id = self._repo.add_entry(
            symbol=row["symbol"],
            name=row["name"],
            currency=row["currency"],
            tags=row["tags"],
            added_by=row["added_by"],
        )
        if entry_id is None:
            raise DuplicateSymbolError(f"{row['symbol']} is already in the watchlist")
        return {
            "symbol": row["symbol"],
            "name": row["name"],
            "currency": row["currency"],
        }

    def resync_currencies(
        self, symbols: Optional[List[str]] = None, apply: bool = False
    ) -> List[Dict[str, Any]]:
        """Re-resolve stored currencies against the exchange.

        ``add_entry`` only fixes entries added from now on; the list seeded
        from watchlist.yaml carries whatever the file said. Returns one row per
        entry — ``symbol``, ``stored``, ``resolved``, ``changed``, ``updated``
        — so the caller can show the diff before writing anything. Nothing is
        written unless ``apply`` is true.

        One network call per symbol, so this is a script's job (see
        ``scripts/repair_watchlist_currency.py``), never a request handler's.
        A symbol whose lookup fails is reported with ``resolved: None`` and
        left alone rather than reset to a guess.
        """
        wanted = {str(s).strip().upper() for s in symbols} if symbols else None

        results: List[Dict[str, Any]] = []
        for entry in self.list_entries():
            symbol = str(entry.get("symbol") or "").strip().upper()
            if not symbol or (wanted is not None and symbol not in wanted):
                continue

            stored = str(entry.get("currency") or "").strip().upper() or None
            resolved = self.resolve_currency(symbol)
            changed = bool(resolved) and resolved != stored

            updated = False
            if changed and apply:
                updated = self._repo.set_currency(symbol, resolved) > 0

            results.append({
                "symbol": symbol,
                "stored": stored,
                "resolved": resolved,
                "changed": changed,
                "updated": updated,
            })
        return results

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
