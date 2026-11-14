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
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from quantcore.analytics import portfolio_math
from quantcore.analytics.market_time import market_date
from quantcore.repositories.books_repository import PAPER, BookNotFoundError
from quantcore.repositories.portfolio_repository import PortfolioRepository

logger = logging.getLogger(__name__)

# Trailing windows read for the Portfolio table, in bars (#518): the same
# 1d / 5d / 30d / 60d the Watchlist shows, plus YTD and 1y from the same read.
RETURN_PERIODS = (1, 5, 30, 60)
# How long before a symbol that is still without history is fetched again.
COLD_WARM_RETRY_SECONDS = 3600.0
RETURN_COLUMNS = tuple(f"return_{n}d" for n in RETURN_PERIODS) + ("return_ytd", "return_1y")
QUOTE_COLUMNS = {
    "regular_change_pct": None, "pre_change_pct": None, "post_change_pct": None,
    "market_state": None, "quote_as_of": None, "quote_stale": True,
    "quote_earlier_session": False,
    "return_1d_display": None, "return_1d_stale": True,
}


class DuplicateSymbolError(Exception):
    """Kept for import-compatibility. No longer raised by add_position — a
    symbol may have any number of open lots (issue #126 Step 3.3)."""


def _money(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v not in (None, "") else None


def _quantity(v) -> Optional[Decimal]:
    """A share count at the precision the database stores (`NUMERIC(18,6)`).

    Every write rounds here, once, before any arithmetic. Left to the column,
    a partial sale of 0.3333335 from one share would store 0.333334 sold and
    0.666667 remaining: more shares than the lot held.
    """
    return Decimal(str(v)).quantize(Decimal("0.000001")) if v not in (None, "") else None


def _clean_note(note: Any) -> Optional[str]:
    """Blank / whitespace-only notes are stored as NULL (issue #266)."""
    return (str(note).strip() if note is not None else "") or None


def _validate_lot_fields(row: Dict[str, Any], *, require_all: bool) -> None:
    """Shared invariant for lot writes: price and quantity must be positive
    and trade_date must be a valid ISO date. ``require_all`` additionally
    demands presence — set for creation (a new lot needs all three to be
    usable by summary_totals/gain_loss_pct); updates only validate whichever
    of these fields the caller is actually setting.

    Coerces purchase_price/quantity to Decimal itself rather than trusting
    the caller to have normalized them: add_position() passes an already
    Decimal-normalized row, but update_lot() passes fields straight through
    (Pydantic coerces to Decimal via the router, but a direct service call
    may still hand in a raw string) — comparing an un-coerced str with ``<=``
    raises TypeError instead of the ValueError callers expect from here.
    """
    purchase_price = row.get("purchase_price")
    quantity = row.get("quantity")
    trade_date = row.get("trade_date")

    if require_all and (purchase_price is None or quantity is None or trade_date is None):
        raise ValueError("purchase_price, quantity, and trade_date are required")

    if purchase_price is not None:
        try:
            purchase_price = Decimal(str(purchase_price))
        except InvalidOperation:
            raise ValueError(f"purchase_price is not a valid number: {purchase_price!r}")
        if purchase_price <= 0:
            raise ValueError("purchase_price must be positive")
    if quantity is not None:
        try:
            quantity = Decimal(str(quantity))
        except InvalidOperation:
            raise ValueError(f"quantity is not a valid number: {quantity!r}")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
    if trade_date is not None:
        try:
            date.fromisoformat(str(trade_date))
        except ValueError:
            raise ValueError(f"trade_date is not a valid date: {trade_date!r}")


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
    def __init__(
        self,
        portfolio_repository: PortfolioRepository,
        prices,
        options=None,
        harvester_repository=None,
        books_repository=None,
    ) -> None:
        self._repo = portfolio_repository
        # BooksRepository — checks a named book is the owner's before a read
        # (#431 step 1.2). Optional like `options`; without it a named book is
        # refused, never read unchecked.
        self._books_repo = books_repository
        # PricesService — one batched, TTL-cached get_quotes() call per
        # list_lots() instead of one get_fast_price() per lot (issue #126
        # Step 4.4).
        self._prices = prices
        # OptionsService — latest MM hedge bias per symbol (decision #18) for
        # symbol_rows(). Optional so light-weight/unit-test construction
        # doesn't have to wire the whole options stack.
        self._options = options
        # HarvesterPlanDB — the exit half of the holding invariant (issue #147
        # Part H5) and the portfolio page's plan chip (Part H6). The
        # *repository*, not HarvesterService, because HarvesterService already
        # composes this service's repository for the entry half and services in
        # both directions would be a construction cycle. Optional for the same
        # reason as `options`.
        self._harvester_repo = harvester_repository
        # symbol -> monotonic time of its last cold-start warm attempt (#518).
        self._warm_attempts: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_positions(self, owner: str) -> List[Dict[str, Any]]:
        return self._repo.list_positions(owner)

    def list_owners(self) -> List[str]:
        return self._repo.list_owners()

    def all_symbols(self) -> List[str]:
        """Every symbol held by *any* owner, de-duplicated, first-seen order.

        Not just John's: other owners have no daily job of their own (issue #126
        decision #5), so anything the nightly capture and the fundamentals
        warmer walk on their behalf belongs in this roster too. Half of the
        "tracked" universe the fundamentals views scope to (issue #147 Part B4).
        """
        symbols: List[str] = []
        for owner in self._repo.list_owners():
            for row in self._repo.list_positions(owner):
                sym = row.get("symbol")
                if sym and sym not in symbols:
                    symbols.append(sym)
        return symbols

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
        _validate_lot_fields(row, require_all=True)
        self._repo.add_position(owner, row)
        return {"symbol": row["symbol"]}

    def remove_position(self, owner: str, symbol: str) -> int:
        """Remove every lot of `symbol` for `owner`. Returns rows removed."""
        removed = self._repo.remove_position(owner, symbol)
        if removed:
            self._close_plan_if_flat(owner, symbol, "position removed")
        return removed

    # ------------------------------------------------------------------
    # Holding invariant, exit half (issue #147 Part H5)
    # ------------------------------------------------------------------
    def _close_plan_if_flat(self, owner: str, symbol: str, reason: str) -> None:
        """Close `owner`'s ACTIVE plan on `symbol` once no OPEN lot remains.

        Deliberately *after* the mutation and on its own connection, so this
        cannot roll back a recorded sale — the sale is the fact, the plan is
        bookkeeping about it. That makes the seam eventually consistent by
        design: a crash between the two leaves an ACTIVE plan with nothing
        under it, which ``display_all_plans``' ``in_portfolio`` flag (Part H7)
        surfaces on the Plans page rather than letting it fire alerts unseen.

        A partial close leaves lots open and therefore leaves the plan ACTIVE —
        that off-by-one is the difference between finishing a harvest and
        silently killing a live ladder.
        """
        if self._harvester_repo is None:
            return
        try:
            if self._repo.list_lots_for_symbol(owner, symbol, status="OPEN"):
                return
            closed = self._harvester_repo.close_active_plan_for_symbol(
                symbol, owner=owner, reason=reason
            )
            if closed:
                logger.info(
                    "closed harvest plan for %s/%s: %s (no open lots remain)",
                    owner, symbol, reason,
                )
        except Exception:
            # Never fail the write that already committed. The orphan flag is
            # the backstop.
            logger.warning(
                "failed to close harvest plan for %s/%s after %s",
                owner, symbol, reason, exc_info=True,
            )

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

    def _require_book(self, owner: str, book_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """The named book, or None when no book is named (the real book).

        Raises `BookNotFoundError` for a book that isn't `owner`'s. The
        repository's SQL already returns no rows for such an id; this turns
        the empty table into a 404, so a stale link doesn't read as a book
        that holds nothing. A read with no book named looks nothing up, so it
        still never creates a book.
        """
        if book_id is None:
            return None
        book = (
            self._books_repo.get_book(owner=owner, book_id=book_id)
            if self._books_repo is not None else None
        )
        if book is None:
            raise BookNotFoundError(f"book {book_id} not found")
        return book

    def list_lots(
        self,
        owner: str,
        status: str = "OPEN",
        force: bool = False,
        *,
        book_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """`owner`'s lots, enriched with live price, gain/loss, days held, $/day.

        Live prices come from one batched, TTL-cached PricesService.get_quotes()
        call covering every distinct symbol rather than one quote per lot
        (issue #126 Step 4.4). `force` bypasses the cache's TTL (still subject
        to its own re-fetch floor). `book_id` names a book; None is the real
        book, and a book that isn't `owner`'s raises `BookNotFoundError`.
        """
        self._require_book(owner, book_id)
        return self._priced_lots(owner, status, force, book_id)

    def _priced_lots(
        self, owner: str, status: str, force: bool, book_id: Optional[int]
    ) -> List[Dict[str, Any]]:
        lots = self._repo.list_positions(owner, status=status, book_id=book_id)
        quotes = self._prices.get_quotes([lot["symbol"] for lot in lots], force=force)
        as_of = market_date()
        return [
            self._enrich_lot(lot, quotes.get(lot["symbol"]), as_of)
            for lot in lots
        ]

    def _period_returns(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Return columns for every symbol from **one** cached read (#518).

        ``bulk_period_returns`` computes them all from a single SQL query and
        no network call; before, this was one ``get_history`` per symbol,
        each of which could reach Yahoo. A failure degrades every return
        column to None and leaves the rest of the table standing.

        A symbol with no cached history at all is warmed first
        (``_warm_cold_history``): nothing else on this page fetches history
        any more, and the nightly close-out skips such a symbol on purpose.
        """
        try:
            rows = self._prices.bulk_period_returns(symbols, periods=RETURN_PERIODS)
            cold = [s for s in symbols if not rows[s]["bars"]]
            if cold and self._warm_cold_history(cold):
                rows.update(self._prices.bulk_period_returns(cold, periods=RETURN_PERIODS))
            return rows
        except Exception as exc:  # noqa: BLE001 — the table outlives its return columns
            logger.warning("symbol_rows: period returns failed (%s)", type(exc).__name__)
            return {}

    def _warm_cold_history(self, symbols: List[str]) -> bool:
        """Cold-start the history of symbols with no cached bars; True if any worked.

        ``get_history`` on an empty cache fetches the full warm window. This
        is the one network path left in ``symbol_rows``, taken once per newly
        held symbol. A symbol whose history can't be fetched (delisted, or a
        bad ticker) stays cold, so each is tried at most once per
        ``COLD_WARM_RETRY_SECONDS`` per process, not on every page load.
        """
        warmed = False
        now = time.monotonic()
        for symbol in symbols:
            last = self._warm_attempts.get(symbol)
            if last is not None and now - last < COLD_WARM_RETRY_SECONDS:
                continue
            self._warm_attempts[symbol] = now
            try:
                hist = self._prices.get_history(symbol, "1d", 365)
                warmed = warmed or (hist is not None and not hist.empty)
            except Exception as exc:  # noqa: BLE001 — one symbol's columns, not the page
                logger.warning("symbol_rows(%s): history warm failed (%s)",
                               symbol, type(exc).__name__)
        return warmed

    def _quote_changes(self, symbols: List[str], returns: Dict[str, Dict[str, Any]]
                       ) -> Dict[str, Dict[str, Any]]:
        """The live 1d columns per symbol, or ``{}`` when they can't be had.

        ``list_lots`` has just fetched these symbols' quotes, so this reads the
        quote cache and makes no upstream call of its own.
        """
        try:
            return self._prices.quote_changes(
                symbols, return_1d={s: row.get("return_1d") for s, row in returns.items()})
        except Exception as exc:  # noqa: BLE001 — the table outlives its 1d column
            logger.warning("symbol_rows: quote changes failed (%s)", type(exc).__name__)
            return {}

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

    def _active_plan_ids(self, owner: str) -> Dict[str, int]:
        """`{symbol: instance_id}` for the Plan chip, or `{}` if unavailable.

        A harvester outage must degrade the chip, not the portfolio table.
        """
        if self._harvester_repo is None:
            return {}
        try:
            return self._harvester_repo.active_plan_ids(owner=owner)
        except Exception:
            logger.warning("symbol_rows(%s): active plan lookup failed", owner, exc_info=True)
            return {}

    def symbol_rows(
        self, owner: str, force: bool = False, *, book_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Per-symbol roll-up: aggregate totals, period-return columns (1d, 5d,
        30d, 60d, YTD, 1y), the live 1d quote columns
        (#518), the MM
        hedge bias (decision #18), and the stacked allocation bar's three
        segments (issue #147 Part A), each carrying its own open child lots
        (decision #14). `force` bypasses the quote cache's TTL (Step 4.4).

        `book_id` names a book; None is the real book. A paper book gets no
        Plan chip: harvest plans belong to real holdings (#431).
        """
        book = self._require_book(owner, book_id)
        lots = self._priced_lots(owner, "OPEN", force, book_id)
        by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        for lot in lots:
            by_symbol.setdefault(lot["symbol"], []).append(lot)

        is_paper = book is not None and book["kind"] == PAPER
        plan_ids = {} if is_paper else self._active_plan_ids(owner)
        as_of = market_date()
        returns = self._period_returns(sorted(by_symbol))
        changes = self._quote_changes(sorted(by_symbol), returns)
        rows = []
        for symbol, symbol_lots in sorted(by_symbol.items()):
            priced_lots = [lot for lot in symbol_lots if lot["current_price"] is not None]
            totals = portfolio_math.summary_totals(priced_lots, as_of=as_of)
            row = {
                "symbol": symbol,
                "lots": symbol_lots,
                **totals,
                # The allocation chart's segments are a function of numbers
                # already in `totals`, so they cost nothing extra here and save
                # the front end from doing the split itself (Rule 8.4). A
                # symbol with no priced lot gets None rather than the zeroes
                # summary_totals reports — the chart must drop it, not draw a
                # flat bar that reads as a worthless position.
                **portfolio_math.allocation_segments(
                    totals["total_investment"] if priced_lots else None,
                    totals["total_gain_loss"] if priced_lots else None,
                ),
                **{col: returns.get(symbol, {}).get(col) for col in RETURN_COLUMNS},
                **{**QUOTE_COLUMNS, **changes.get(symbol, {})},
                **self._latest_mm_hedge_bias(symbol),
                # The Plan chip (issue #147 Part H6). One extra query for the
                # whole page, not one per symbol; None means "no ladder yet",
                # which the page turns into a create affordance.
                "active_plan_id": plan_ids.get(symbol),
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
        return portfolio_math.summary_totals(all_lots, as_of=market_date())

    def update_lot(self, owner: str, lot_id: int, **fields: Any) -> bool:
        """Correct a single lot's fields. Returns True if a lot was updated.

        Only the fields the caller supplies are validated/written (PATCH
        semantics) — but any of purchase_price/quantity/trade_date that IS
        supplied must be positive/valid, so a correction can't null out an
        existing lot into the same unusable state add_position rejects.
        """
        if fields.get("quantity") is not None:
            try:
                fields = {**fields, "quantity": _quantity(fields["quantity"])}
            except InvalidOperation:
                raise ValueError(f"quantity is not a valid number: {fields['quantity']!r}")
        _validate_lot_fields(fields, require_all=False)
        if "notes" in fields:
            fields = {**fields, "notes": _clean_note(fields["notes"])}
        return self._repo.update_lot(owner, lot_id, fields)

    def delete_lot(self, owner: str, lot_id: int) -> bool:
        """Remove a mistaken lot entry. Returns True if a lot was deleted."""
        # Read the symbol first — after the delete there is no row to read it
        # from, and the plan close needs to know which ladder to check.
        lot = self._repo.get_lot(owner, lot_id)
        deleted = self._repo.delete_lot(owner, lot_id)
        if deleted and lot is not None:
            self._close_plan_if_flat(owner, lot["symbol"], "last lot deleted")
        return deleted

    def _resolve_close_allocation(
        self,
        owner: str,
        lot_id: int,
        shares: Any,
        method: str,
        lots: Optional[Sequence[Tuple[int, Any]]],
    ) -> Tuple[Dict[str, Any], Decimal, List[Dict[str, Any]], List[Tuple[Any, Decimal]]]:
        """Anchor lookup + allocation shared by `close_lot` and `preview_close`."""
        anchor = self._repo.get_lot(owner, lot_id)
        if anchor is None:
            raise ValueError(f"lot {lot_id} not found for owner {owner!r}")
        try:
            shares = _quantity(shares)
            if lots is not None:
                lots = [(alloc_lot_id, _quantity(alloc)) for alloc_lot_id, alloc in lots]
        except InvalidOperation:
            raise ValueError("shares is not a valid number")
        symbol_lots = self._repo.list_lots_for_symbol(owner, anchor["symbol"], status="OPEN")
        allocation = portfolio_math.allocate(symbol_lots, shares, method, lots)
        return anchor, shares, symbol_lots, allocation

    def preview_close(
        self,
        owner: str,
        lot_id: int,
        shares: Any,
        method: str = "FIFO",
        lots: Optional[Sequence[Tuple[int, Any]]] = None,
    ) -> Dict[str, Any]:
        """Which lots a sale of `shares` would touch — writes nothing.

        Lets the Close dialog offer one note field per lot (issue #266).
        """
        anchor, _, symbol_lots, allocation = self._resolve_close_allocation(
            owner, lot_id, shares, method, lots
        )
        by_id = {lot["lot_id"]: lot for lot in symbol_lots}
        return {
            "symbol": anchor["symbol"],
            "allocations": [
                {
                    "lot_id": allocated_id,
                    "shares": allocated_shares,
                    "trade_date": by_id[allocated_id].get("trade_date"),
                    "purchase_price": by_id[allocated_id].get("purchase_price"),
                }
                for allocated_id, allocated_shares in allocation
            ],
        }

    def list_sales(
        self, owner: str, symbol: Optional[str] = None, *, book_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """`owner`'s recorded sales (with their notes), optionally for one
        symbol. `book_id` names a book; None is the real book."""
        self._require_book(owner, book_id)
        return self._repo.list_sales(
            owner, symbol.strip().upper() if symbol else None, book_id=book_id
        )

    def update_sale_notes(self, owner: str, sale_id: int, notes: Optional[str]) -> bool:
        """Set or clear a sale's note. Returns False if no such sale for `owner`."""
        return self._repo.update_sale_notes(owner, sale_id, _clean_note(notes))

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
        notes: Optional[str] = None,
        lot_notes: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        """Close `shares` of the symbol anchored by `lot_id`.

        `lot_id` identifies the symbol to sell from — the actual sale may span
        multiple lots of that symbol. Auto methods (FIFO/LIFO/HIFO) resolve the
        allocation across every OPEN lot of that symbol; MANUAL uses the
        caller-supplied `lots` pairs. Writes the sale (and any partial-close
        split) atomically via `PortfolioRepository.close_lots`.

        `notes` is the sale rationale recorded on every lot the sale touches;
        `lot_notes` (`{lot_id: note}`) overrides it per lot (issue #266). Blank
        notes are stored as NULL; a `lot_notes` key outside the resolved
        allocation is rejected.
        """
        anchor, shares, _, allocation = self._resolve_close_allocation(
            owner, lot_id, shares, method, lots
        )
        sale_price = Decimal(str(sale_price))
        fees = Decimal(str(fees)) if fees not in (None, "") else None

        notes = _clean_note(notes)
        cleaned_lot_notes: Dict[int, str] = {}
        for note_lot_id, note in (lot_notes or {}).items():
            cleaned = _clean_note(note)
            if cleaned is not None:
                cleaned_lot_notes[int(note_lot_id)] = cleaned
        extra = set(cleaned_lot_notes) - {allocated_id for allocated_id, _ in allocation}
        if extra:
            raise ValueError(f"lot_notes references lot(s) not in this sale: {sorted(extra)}")

        allocations = self._repo.close_lots(
            owner, allocation, sale_price, sale_trade_date,
            fees=fees, allocation_method=method,
            notes=notes, lot_notes=cleaned_lot_notes or None,
        )
        self._close_plan_if_flat(owner, anchor["symbol"], "last open lot closed")
        return {
            "symbol": anchor["symbol"],
            "shares_sold": shares,
            "sale_price": sale_price,
            "sale_trade_date": sale_trade_date,
            "allocations": allocations,
        }
