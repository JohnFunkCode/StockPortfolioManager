"""PortfolioService — DB-backed, multi-owner portfolio holdings.

Phase 1 Step 6 (docs/proposals/phase1-migration-plan.md). Positions live in the
`positions` table (owner-scoped); portfolio.csv becomes an import format with
full-sync/replace semantics. Adapters (REST routes in api/app.py, the report
build in main.py, scripts/import_portfolio.py) call
``get_services().portfolio.<method>``.

list_positions() returns the same dict shape api/app.py previously produced from
the CSV, so the WebUI and downstream callers are unaffected.
"""

from __future__ import annotations

import csv
import logging
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from quantcore.analytics import portfolio_math
from quantcore.repositories.portfolio_repository import PortfolioRepository

logger = logging.getLogger(__name__)

PERIOD_RETURN_DAYS = (7, 30, 90)


class DuplicateSymbolError(Exception):
    """Kept for import-compatibility. No longer raised by add_position — a
    symbol may have any number of open lots (issue #126 Step 3.3)."""


def _money(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v not in (None, "") else None


def _quantity(v) -> Optional[Decimal]:
    return Decimal(str(v)).quantize(Decimal("0.000001")) if v not in (None, "") else None


def _normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a raw CSV/body dict into the canonical position shape.

    `trade_date` is the lot's real identity; `purchase_date` is a deprecated
    alias accepted for backward compatibility (issue #126 Step 3.3).
    """
    return {
        "name": (raw.get("name") or "").strip(),
        "symbol": (raw.get("symbol") or "").strip().upper(),
        "purchase_price": _money(raw.get("purchase_price")),
        "quantity": _quantity(raw.get("quantity")),
        "purchase_date": (raw.get("purchase_date") or None),
        "trade_date": (raw.get("trade_date") or raw.get("purchase_date") or None),
        "currency": (raw.get("currency") or "USD").strip().upper(),
        "sale_price": _money(raw.get("sale_price")),
        "sale_date": (raw.get("sale_date") or None),
        "fees": _money(raw.get("fees")),
        "acquisition_type": (raw.get("acquisition_type") or None),
        "account": (raw.get("account") or None),
        "notes": (raw.get("notes") or None),
    }


class PortfolioService:
    def __init__(self, portfolio_repository: PortfolioRepository, prices, options=None) -> None:
        self._repo = portfolio_repository
        # PricesService — one batched, TTL-cached get_quotes() call per
        # list_lots() instead of one get_fast_price() per lot (issue #126
        # Step 4.4).
        self._prices = prices
        # OptionsService — latest MM hedge bias per symbol (decision #18) for
        # symbol_rows(). Optional so light-weight/unit-test construction
        # doesn't have to wire the whole options stack.
        self._options = options

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_positions(self, owner: str) -> List[Dict[str, Any]]:
        return self._repo.list_positions(owner)

    def list_owners(self) -> List[str]:
        return self._repo.list_owners()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def import_csv(self, path: str, owner: str) -> int:
        """Full-replace `owner`'s positions with the rows in the CSV at `path`.

        Returns the number of positions imported.
        """
        rows: List[Dict[str, Any]] = []
        warned_deprecated_alias = False
        with open(path, newline="") as fh:
            for raw in csv.DictReader(fh):
                row = _normalize_row(raw)
                if not row["symbol"]:
                    continue
                if not raw.get("trade_date") and raw.get("purchase_date") and not warned_deprecated_alias:
                    logger.warning(
                        "import_csv(owner=%s, path=%s): 'purchase_date' is a deprecated "
                        "alias for 'trade_date' — update the CSV to include 'trade_date'",
                        owner, path,
                    )
                    warned_deprecated_alias = True
                rows.append(row)
        return self._repo.replace_owner_positions(owner, rows)

    def add_position(self, owner: str = "john", **fields: Any) -> Dict[str, Any]:
        """Add a single lot for `owner`. Multiple lots per symbol are allowed —
        each call creates a new lot rather than upserting (issue #126 Step 3.3).
        """
        row = _normalize_row(fields)
        if not row["symbol"]:
            raise ValueError("symbol is required")
        self._repo.add_position(owner, row)
        return {"symbol": row["symbol"]}

    def remove_position(self, owner: str, symbol: str) -> int:
        """Remove every lot of `symbol` for `owner`. Returns rows removed."""
        return self._repo.remove_position(owner, symbol)

    # ------------------------------------------------------------------
    # Lot lifecycle (issue #126 PR 4 Step 4.3)
    # ------------------------------------------------------------------
    def _enrich_lot(self, lot: Dict[str, Any], quote: Optional[Dict[str, Any]], as_of: date) -> Dict[str, Any]:
        row = dict(lot)
        price = quote.get("price") if quote else None
        current_price = Decimal(str(price)) if price is not None else None
        row["current_price"] = current_price
        row["price_as_of"] = quote.get("as_of") if quote else None
        row["price_stale"] = quote.get("stale", True) if quote else True
        purchase_price = lot.get("purchase_price")
        quantity = lot.get("quantity")
        trade_date = lot.get("trade_date")

        if current_price is not None and purchase_price is not None and quantity is not None:
            row["gain_loss"] = portfolio_math.gain_loss(current_price, purchase_price, quantity)
            row["gain_loss_pct"] = portfolio_math.gain_loss_pct(current_price, purchase_price, quantity)
        else:
            row["gain_loss"] = None
            row["gain_loss_pct"] = None

        if trade_date is not None:
            days = portfolio_math.days_held(trade_date, as_of)
            row["days_held"] = days
            row["dollars_per_day"] = (
                portfolio_math.dollars_per_day(row["gain_loss"], days)
                if row["gain_loss"] is not None else None
            )
        else:
            row["days_held"] = None
            row["dollars_per_day"] = None
        return row

    def list_lots(self, owner: str, status: str = "OPEN", force: bool = False) -> List[Dict[str, Any]]:
        """`owner`'s lots, enriched with live price, gain/loss, days held, $/day.

        Live prices come from one batched, TTL-cached PricesService.get_quotes()
        call covering every distinct symbol rather than one quote per lot
        (issue #126 Step 4.4). `force` bypasses the cache's TTL (still subject
        to its own re-fetch floor).
        """
        lots = self._repo.list_positions(owner, status=status)
        quotes = self._prices.get_quotes([lot["symbol"] for lot in lots], force=force)
        as_of = date.today()
        return [
            self._enrich_lot(lot, quotes.get(lot["symbol"]), as_of)
            for lot in lots
        ]

    def _period_returns(self, symbol: str) -> Dict[str, Optional[Decimal]]:
        keys = {f"return_{d}d": None for d in PERIOD_RETURN_DAYS}
        try:
            hist = self._prices.get_history(symbol, "1d", max(PERIOD_RETURN_DAYS) + 5)
        except Exception:
            logger.warning("symbol_rows(%s): period-return history fetch failed", symbol, exc_info=True)
            return keys
        if hist is None or hist.empty:
            return keys

        closes = hist["Close"]
        current_price = Decimal(str(closes.iloc[-1]))
        for d in PERIOD_RETURN_DAYS:
            if len(closes) > d:
                price_then = Decimal(str(closes.iloc[-1 - d]))
                keys[f"return_{d}d"] = portfolio_math.period_return(current_price, price_then)
        return keys

    def _latest_mm_hedge_bias(self, symbol: str) -> Dict[str, Any]:
        result = {"mm_hedge_bias": None, "mm_hedge_bias_captured_at": None}
        if self._options is None:
            return result
        try:
            history = self._options.get_gamma_wall_history(symbol, since_days=90).get("history") or []
        except Exception:
            logger.warning("symbol_rows(%s): gamma wall history fetch failed", symbol, exc_info=True)
            return result
        if not history:
            return result
        latest = history[-1]
        result["mm_hedge_bias"] = latest.get("mm_hedge_bias")
        result["mm_hedge_bias_captured_at"] = latest.get("captured_at")
        return result

    def symbol_rows(self, owner: str, force: bool = False) -> List[Dict[str, Any]]:
        """Per-symbol roll-up: aggregate totals, period-return columns, and the
        MM hedge bias (decision #18), each carrying its own open child lots
        (decision #14). `force` bypasses the quote cache's TTL (Step 4.4).
        """
        lots = self.list_lots(owner, status="OPEN", force=force)
        by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        for lot in lots:
            by_symbol.setdefault(lot["symbol"], []).append(lot)

        as_of = date.today()
        rows = []
        for symbol, symbol_lots in sorted(by_symbol.items()):
            priced_lots = [lot for lot in symbol_lots if lot["current_price"] is not None]
            totals = portfolio_math.summary_totals(priced_lots, as_of=as_of)
            row = {
                "symbol": symbol,
                "lots": symbol_lots,
                **totals,
                **self._period_returns(symbol),
                **self._latest_mm_hedge_bias(symbol),
            }
            rows.append(row)
        return rows

    def portfolio_totals(self, rows: List[Dict[str, Any]]) -> Dict[str, Optional[Decimal]]:
        """Portfolio-wide Summary-section totals (issue #126 Step 4.6) across
        every symbol row's lots, flattened. Takes `symbol_rows()`'s output
        rather than re-fetching quotes, since each row's lots are already
        priced."""
        all_lots = [
            lot for row in rows for lot in row["lots"] if lot["current_price"] is not None
        ]
        return portfolio_math.summary_totals(all_lots, as_of=date.today())

    def update_lot(self, owner: str, lot_id: int, **fields: Any) -> bool:
        """Correct a single lot's fields. Returns True if a lot was updated."""
        return self._repo.update_lot(owner, lot_id, fields)

    def delete_lot(self, owner: str, lot_id: int) -> bool:
        """Remove a mistaken lot entry. Returns True if a lot was deleted."""
        return self._repo.delete_lot(owner, lot_id)

    def close_lot(
        self,
        owner: str,
        lot_id: int,
        shares: Any,
        sale_price: Any,
        sale_trade_date: str,
        method: str = "FIFO",
        lots: Optional[Sequence[Tuple[int, Any]]] = None,
        fees: Any = None,
    ) -> Dict[str, Any]:
        """Close `shares` of the symbol anchored by `lot_id`.

        `lot_id` identifies the symbol to sell from — the actual sale may span
        multiple lots of that symbol. Auto methods (FIFO/LIFO/HIFO) resolve the
        allocation across every OPEN lot of that symbol; MANUAL uses the
        caller-supplied `lots` pairs. Writes the sale (and any partial-close
        split) atomically via `PortfolioRepository.close_lots`.
        """
        anchor = self._repo.get_lot(owner, lot_id)
        if anchor is None:
            raise ValueError(f"lot {lot_id} not found for owner {owner!r}")

        shares = Decimal(str(shares))
        sale_price = Decimal(str(sale_price))
        fees = Decimal(str(fees)) if fees not in (None, "") else None

        symbol_lots = self._repo.list_lots_for_symbol(owner, anchor["symbol"], status="OPEN")
        allocation = portfolio_math.allocate(symbol_lots, shares, method, lots)

        allocations = self._repo.close_lots(
            owner, allocation, sale_price, sale_trade_date,
            fees=fees, allocation_method=method,
        )
        return {
            "symbol": anchor["symbol"],
            "shares_sold": shares,
            "sale_price": sale_price,
            "sale_trade_date": sale_trade_date,
            "allocations": allocations,
        }
