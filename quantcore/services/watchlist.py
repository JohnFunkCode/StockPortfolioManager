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
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

from quantcore.analytics.returns import normalize_market_cap
from quantcore.repositories.watchlist_repository import WatchlistRepository
from quantcore.services.portfolio import DuplicateSymbolError

logger = logging.getLogger(__name__)

# How long an add is willing to wait on Yahoo before storing the fallback
# currency. Deliberately well under the gateway's 15s default: this runs
# inside the user's POST /api/watchlist, and a wrong-but-repairable currency
# beats a dialog that appears to have frozen.
ADD_LOOKUP_TIMEOUT_SECONDS = 6.0


def _metric(score: Dict[str, Any], name: str) -> Dict[str, Any]:
    """One scored metric out of a cached fundamental_score payload, or ``{}``.

    Same accessor the report script used; a symbol scored before a metric
    existed simply has no entry for it, which must read as "unknown", not 0.
    """
    return (score.get("metric_scores") or {}).get(name) or {}


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

    def __init__(
        self,
        repository: WatchlistRepository,
        yfinance: Any = None,
        prices: Any = None,
        fundamentals: Any = None,
    ):
        self._repo = repository
        # The gateway is what makes `currency` a looked-up fact rather than a
        # field the caller guesses. Optional so the CRUD half of this service
        # stays constructible from a bare repository (import scripts, tests);
        # without it add_entry falls back to the supplied value.
        self._yf = yfinance
        # Composed *services*, not their repositories — the RecommendationsService
        # precedent (arch-v2 §5.2). Reaching into FundamentalsRepository directly
        # would mean re-deriving the fundamentals cache's staleness rule here,
        # where it does not belong. Optional for the same reason as above; only
        # returns_and_fundamentals needs them.
        self._prices = prices
        self._fundamentals = fundamentals

    # ------------------------------------------------------------------
    # Currency resolution
    # ------------------------------------------------------------------
    def resolve_currency(
        self, symbol: str, timeout: float = ADD_LOOKUP_TIMEOUT_SECONDS
    ) -> Optional[str]:
        """The currency `symbol` actually trades in, or ``None`` if unknown.

        ``info["currency"]`` is the trading currency, which is the one that
        matches ``marketCap`` — yfinance derives the cap from price × shares,
        so labelling it with anything else (``financialCurrency``, or whatever
        the user typed) is how ASSA-B.ST's SEK ends up rendered as dollars.

        Returns ``None`` rather than raising on any failure: Yahoo being slow
        or wrong is not a reason to refuse to watch a symbol. The caller
        decides what to fall back to.

        The timeout is shorter than the gateway's 15s default because this sits
        on the synchronous add path, where the deadline is a person watching a
        dialog. Missing the currency costs one repair-script run; a dialog that
        hangs for fifteen seconds costs the user's trust in the button.
        """
        if self._yf is None:
            return None
        try:
            info = self._yf.ticker_info(symbol, timeout=timeout) or {}
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

    def returns_and_fundamentals(self, as_of: Optional[date] = None) -> Dict[str, Any]:
        """The whole watchlist fundamentals table, in **six queries and zero
        network calls** (issue #147 Part B3).

        This replaces ``scripts/generate_watchlist_fundamentals_report.py``,
        which walked the list one symbol at a time — ``get_history`` plus
        ``get_full_fundamental_profile`` per row, each of which can hit
        yfinance. On a 40-symbol list that is minutes of serial network, which
        is why the report was a nightly batch job and could never be a page.

        The six: one ``list_entries``, one ``bulk_period_returns`` (itself a
        single bulk OHLCV read), and four ``get_all_latest`` — one per cached
        fundamentals data_type. Every one of them reads cache only. The count is
        constant in the size of the watchlist, and
        ``tests/test_watchlist_service.py`` asserts both halves of that
        (six queries, zero yfinance) as a regression guard, because the easy
        wrong version of this method is a per-symbol loop that still works.

        Cached fundamentals are shown **stale rather than dropped**, with
        ``fundamentals_stale`` and ``fundamentals_age_hours`` on the row: the
        alternative is a table that silently loses rows overnight, which reads
        as "we stopped watching that" instead of "this number is a week old".
        A symbol never scored at all simply has ``None`` fundamentals.
        """
        if self._prices is None or self._fundamentals is None:
            raise RuntimeError(
                "returns_and_fundamentals requires the prices and fundamentals "
                "services; construct WatchlistService via the registry"
            )

        entries = self.list_entries()
        symbols = [str(e.get("symbol") or "").strip().upper() for e in entries]
        symbols = [s for s in symbols if s]

        price_rows = self._prices.bulk_period_returns(symbols, as_of=as_of)

        # Four cache sweeps, keyed by symbol. Each returns every symbol the cache
        # has ever scored, not just ours — indexing is cheaper than filtering in
        # SQL four times over.
        by_type = {
            dt: {
                str(row.get("symbol") or "").upper(): row
                for row in self._fundamentals.get_all_latest(dt)
            }
            for dt in ("fundamental_score", "revenue_growth",
                       "earnings_calendar", "earnings_acceleration")
        }

        ttl_seconds = self._fundamentals.cache_ttl_seconds()
        now_ts = int(time.time())

        rows: List[Dict[str, Any]] = []
        for entry in entries:
            symbol = str(entry.get("symbol") or "").strip().upper()
            if not symbol:
                continue

            score = by_type["fundamental_score"].get(symbol) or {}
            revenue = by_type["revenue_growth"].get(symbol) or {}
            earnings = by_type["earnings_calendar"].get(symbol) or {}
            eps = by_type["earnings_acceleration"].get(symbol) or {}

            # Age the row off the score entry — it is the one that carries the
            # columns a reader actually judges the symbol by.
            fetched_ts = score.get("_fetched_at_ts")
            if not fetched_ts:
                stale, age_hours = None, None
            else:
                age_hours = round((now_ts - fetched_ts) / 3600.0, 1)
                stale = ttl_seconds > 0 and (now_ts - fetched_ts) > ttl_seconds

            prices = price_rows.get(symbol, {})
            # The cap's unit comes from the score payload when it has one:
            # that value was captured in the same fetch as the cap itself,
            # whereas the watchlist row's currency is whatever was stored at
            # add time — right for entries added since the lookup landed, but
            # still USD for anything seeded from watchlist.yaml that
            # scripts/repair_watchlist_currency.py has not been run over.
            cap = normalize_market_cap(
                score.get("market_cap"),
                score.get("market_cap_currency") or entry.get("currency"),
            )

            rows.append({
                "symbol": symbol,
                "name": entry.get("name") or symbol,
                "currency": entry.get("currency") or "USD",
                "tags": list(entry.get("tags") or []),

                "price": prices.get("price"),
                "price_as_of": prices.get("as_of"),
                "return_5d": prices.get("return_5d"),
                "return_30d": prices.get("return_30d"),
                "return_60d": prices.get("return_60d"),
                "return_ytd": prices.get("return_ytd"),
                "return_1y": prices.get("return_1y"),

                "composite_score": score.get("composite_score"),
                "fundamental_label": score.get("fundamental_label"),
                "coverage": score.get("coverage"),
                "sector": score.get("sector"),
                **cap,

                "rev_cagr_3y": _metric(score, "RevCAGR3Y").get("value"),
                "rev_cagr_score": _metric(score, "RevCAGR3Y").get("score"),
                "rev_accel_score": _metric(score, "RevAccel").get("score"),
                "op_margin_score": _metric(score, "OpMargin3Y").get("score"),
                "fcf_margin_score": _metric(score, "FCFMargin3Y").get("score"),
                "valuation_score": _metric(score, "ValMetric").get("score"),
                "momentum_score": _metric(score, "Mom12_1").get("score"),

                "revenue_trajectory": revenue.get("trajectory"),
                "earnings_date": (earnings.get("earnings_date") or None),
                "eps_acceleration": eps.get("acceleration_label"),

                "fundamentals_cached_at": score.get("fetched_at"),
                "fundamentals_stale": stale,
                "fundamentals_age_hours": age_hours,
            })

        # Same ordering the report script used: best composite first, unscored
        # symbols last, ties broken by 30-day momentum. Sorting server-side means
        # the first screenful is the interesting one before any column is clicked.
        rows.sort(
            key=lambda r: (
                r["composite_score"] is not None,
                r["composite_score"] if r["composite_score"] is not None else -999,
                r["return_30d"] if r["return_30d"] is not None else -999,
            ),
            reverse=True,
        )

        return {
            "generated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "as_of": as_of.isoformat() if as_of else None,
            "count": len(rows),
            "stale_count": sum(1 for r in rows if r["fundamentals_stale"]),
            "unscored_count": sum(1 for r in rows if r["composite_score"] is None),
            "rows": rows,
        }

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

        # Passed explicitly rather than left to the signature default: this is
        # the call that sits inside a user's POST, and the deadline belongs at
        # the site that has one. It also resolves at call time, so the ceiling
        # is a module constant a test can pin.
        looked_up = self.resolve_currency(
            row["symbol"], timeout=ADD_LOOKUP_TIMEOUT_SECONDS
        )
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

    def set_tags(self, symbol: str, tags: Optional[List[str]]) -> Optional[List[str]]:
        """Replace `symbol`'s tags. Returns the stored list, or ``None`` if the
        symbol isn't watched (the router's 404, same convention as
        ``remove_entry`` returning 0).

        The list is normalized here rather than trusted from the body: blanks
        dropped, whitespace stripped, and duplicates collapsed **case-sensitively
        on first occurrence**, so a UI that lets someone type "AI" twice stores
        it once. An empty list is a legitimate edit — it means "no tags" — which
        is why this returns the stored value rather than a count the caller
        would have to interpret.
        """
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            raise ValueError("symbol is required")

        cleaned: List[str] = []
        for tag in _clean_tags(tags):
            if tag not in cleaned:
                cleaned.append(tag)

        if self._repo.set_tags(symbol, cleaned) == 0:
            return None
        return cleaned

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
