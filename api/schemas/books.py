"""Pydantic models for /api/books (#431 step 1.2).

A book is a named container of positions: the owner's one real book, or a
paper book. It is not an account — a lot's brokerage account is a field on
the lot.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel


class BookResponse(BaseModel):
    book_id: int
    name: str
    # "real" or "paper".
    kind: str
    starting_cash: Decimal
    created_at: datetime
    archived_at: Optional[datetime] = None


class BooksResponse(BaseModel):
    books: List[BookResponse]


class CreateBookRequest(BaseModel):
    """A new paper book. The real book is never created through the API."""

    name: str
    starting_cash: Decimal = Decimal("0")


# --------------------------------------------------------------------------
# Performance (#431 step 5.3)
# --------------------------------------------------------------------------


class PerformancePoint(BaseModel):
    """One valued session. `index` is the growth of 1 in the book since the
    first point (time-weighted); `benchmark_index` the same for SPY, None
    when SPY has no close for that session."""

    session_date: date
    book_value: Optional[Decimal] = None
    index: Decimal
    benchmark_index: Optional[Decimal] = None


class PerformanceLatest(BaseModel):
    """The newest snapshot in the window."""

    session_date: date
    book_value: Optional[Decimal] = None
    cash: Optional[Decimal] = None
    holdings_value: Optional[Decimal] = None
    unpriced_count: int
    stale_count: int
    fx_missing_count: int


class SourceRealized(BaseModel):
    """Realized P/L on the sales one source prompted (`manual`, `stop`, …).
    USD, gross of fees; the money fields are None when no sale could be
    summed."""

    source: Optional[str] = None
    sale_count: int
    fx_missing_count: int
    basis_missing_count: int
    cost_basis: Optional[Decimal] = None
    proceeds: Optional[Decimal] = None
    realized_pnl: Optional[Decimal] = None
    realized_pnl_pct: Optional[Decimal] = None


class Attribution(BaseModel):
    sources: List[SourceRealized]
    totals: SourceRealized


class BookPerformanceResponse(BaseModel):
    """GET /api/books/performance. Returns are fractions (0.05 is 5%).
    `vs_benchmark` is the book's return less SPY's over the same sessions.
    `cash_negative_count` counts sessions whose cash was below zero, which
    makes the return unreliable (plan decision 8). `restated_count` and
    `last_restated_at` disclose rows rebuilt after a late change."""

    book_id: Optional[int] = None
    name: str
    kind: str
    period: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    first_session: Optional[date] = None
    last_session: Optional[date] = None
    twr: Optional[Decimal] = None
    benchmark_symbol: str
    benchmark_available: bool
    benchmark_return: Optional[Decimal] = None
    vs_benchmark: Optional[Decimal] = None
    max_drawdown: Optional[Decimal] = None
    drawdown_peak: Optional[date] = None
    drawdown_trough: Optional[date] = None
    series: List[PerformancePoint]
    gaps: List[date]
    restated_count: int
    last_restated_at: Optional[datetime] = None
    cash_negative_count: int
    latest: Optional[PerformanceLatest] = None
    attribution: Attribution


class CompareBooksResponse(BaseModel):
    """GET /api/books/performance/compare: every open book over one period."""

    period: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    books: List[BookPerformanceResponse]


# --------------------------------------------------------------------------
# The read-only book tool (#431 step 5.4)
# --------------------------------------------------------------------------


class BookHolding(BaseModel):
    """One symbol a book holds open lots of. `avg_price` is the
    quantity-weighted purchase price, in `currency`."""

    symbol: str
    currency: Optional[str] = None
    quantity: Optional[Decimal] = None
    avg_price: Optional[Decimal] = None
    lot_count: int


class BookOverview(BaseModel):
    """One book as the overview gives it: the performance figures, the series
    sampled to the last session of each day, week or month by the period
    (`series_interval`), the window's gaps counted, and the book's open
    holdings."""

    book_id: Optional[int] = None
    name: str
    kind: str
    period: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    first_session: Optional[date] = None
    last_session: Optional[date] = None
    twr: Optional[Decimal] = None
    benchmark_symbol: str
    benchmark_available: bool
    benchmark_return: Optional[Decimal] = None
    vs_benchmark: Optional[Decimal] = None
    max_drawdown: Optional[Decimal] = None
    drawdown_peak: Optional[date] = None
    drawdown_trough: Optional[date] = None
    # "day", "week" or "month".
    series_interval: str
    series: List[PerformancePoint]
    gap_count: int
    restated_count: int
    last_restated_at: Optional[datetime] = None
    cash_negative_count: int
    latest: Optional[PerformanceLatest] = None
    attribution: Attribution
    holdings: List[BookHolding]


class BooksOverviewResponse(BaseModel):
    """GET /api/books/overview: every open book, the real book first."""

    period: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    books: List[BookOverview]


# --------------------------------------------------------------------------
# The read-only stops tool (#579)
# --------------------------------------------------------------------------


class ToolStop(BaseModel):
    """The stop one holding shows, as the stops tool lists it."""

    stop_id: int
    symbol: str
    # active, or triggered when a bar reached it while the holding was open.
    status: str
    # fixed (a price) or trailing (a percentage below a high-water mark).
    kind: str
    stop_level: Decimal
    trail_pct: Optional[Decimal] = None
    high_water_mark: Optional[Decimal] = None
    method: str
    # The stop mirrors an order placed at an outside broker (real book only).
    at_broker: bool = False
    broker_quantity: Optional[Decimal] = None
    broker_order_type: Optional[str] = None
    brokerage: Optional[str] = None
    created_at: datetime
    last_bar_checked: Optional[date] = None
    triggered_session: Optional[date] = None
    trigger_price: Optional[Decimal] = None
    # The symbol's newest stored closed daily bar, and how far its close is
    # above the level: in price and as a fraction of the close (0.05 is 5%).
    # Negative under the level; None with no close stored.
    last_close: Optional[Decimal] = None
    last_close_date: Optional[date] = None
    distance: Optional[Decimal] = None
    distance_fraction: Optional[Decimal] = None
    # What the daily job last recorded: a live stop whose newest tested bar
    # closed within its approach threshold, that session, and how many ATRs
    # above the level the close was.
    near: bool = False
    approached_session: Optional[date] = None
    atr_distance: Optional[Decimal] = None


class BookStops(BaseModel):
    """One open book's stops. `book_id` is None for an owner's real book
    before its first write."""

    book_id: Optional[int] = None
    name: str
    kind: str
    # One a held symbol at most, in symbol order.
    stops: List[ToolStop]
    # Held symbols with no live stop, in order.
    unprotected: List[str]


class BookStopsOverviewResponse(BaseModel):
    """GET /api/books/stops: every open book, the real book first."""

    books: List[BookStops]
