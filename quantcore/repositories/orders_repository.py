"""OrdersRepository — paper orders, the `orders` table (#431 step 3.3).

An order is a request to buy or sell in one of an owner's books. It is
`pending` only while it is queued for a session; `filled`, `cancelled` and
`rejected` are final, and no statement here moves an order out of one: every
change of status is `UPDATE ... WHERE status = 'pending'`.

A fill is several rows that must land together: the order's status, the lot
(a buy) or the sale rows (a sell), and their cash rows. `transaction()` hands
the service one connection for all of them, with the book's row locked, so
two fills in one book run one after the other and a crash leaves either all
of a fill or none of it. The lot and sale writes are `PortfolioRepository`'s
own, on that connection.

Rules this module keeps (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument, bound in every statement.
- `book_id` is keyword-only and defaults to None, which means the owner's real
  book. Every statement restricts the book with the same literal subquery
  `PortfolioRepository` uses, so an order in another book matches no rows.
  (`OrdersService` refuses an order for a real book before it gets here.)

SQL only — no analytics. What an order should do is `analytics.orders`.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from quantcore.db import get_connection
from quantcore.repositories.books_repository import resolve_book_id
from quantcore.repositories.cash_repository import list_entries_on
from quantcore.repositories.portfolio_repository import PortfolioRepository
from quantcore.repositories.stop_sql import resolve_stop_on, trigger_stop_on

SQL_LIST_ORDERS = """
SELECT
    o.order_id         AS order_id,
    o.book_id          AS book_id,
    s.ticker           AS symbol,
    o.side             AS side,
    o.requested_shares AS requested_shares,
    o.requested_amount AS requested_amount,
    o.status           AS status,
    o.source           AS source,
    o.source_ref       AS source_ref,
    o.idempotency_key  AS idempotency_key,
    o.session_date     AS session_date,
    o.placed_at        AS placed_at,
    o.resolved_at      AS resolved_at,
    o.filled_shares    AS filled_shares,
    o.fill_price       AS fill_price,
    o.price_source     AS price_source,
    o.quote_time       AS quote_time,
    o.reject_reason    AS reject_reason,
    o.kind             AS kind
FROM orders o
JOIN symbols s ON s.symbol_id = o.symbol_id
WHERE o.owner = :owner
  AND o.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
ORDER BY o.placed_at DESC, o.order_id DESC;
"""

SQL_GET_ORDER = """
SELECT
    o.order_id         AS order_id,
    o.book_id          AS book_id,
    s.ticker           AS symbol,
    o.side             AS side,
    o.requested_shares AS requested_shares,
    o.requested_amount AS requested_amount,
    o.status           AS status,
    o.source           AS source,
    o.source_ref       AS source_ref,
    o.idempotency_key  AS idempotency_key,
    o.session_date     AS session_date,
    o.placed_at        AS placed_at,
    o.resolved_at      AS resolved_at,
    o.filled_shares    AS filled_shares,
    o.fill_price       AS fill_price,
    o.price_source     AS price_source,
    o.quote_time       AS quote_time,
    o.reject_reason    AS reject_reason,
    o.kind             AS kind
FROM orders o
JOIN symbols s ON s.symbol_id = o.symbol_id
WHERE o.owner = :owner
  AND o.order_id = :order_id
  AND o.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

SQL_GET_ORDER_BY_KEY = """
SELECT
    o.order_id         AS order_id,
    o.book_id          AS book_id,
    s.ticker           AS symbol,
    o.side             AS side,
    o.requested_shares AS requested_shares,
    o.requested_amount AS requested_amount,
    o.status           AS status,
    o.source           AS source,
    o.source_ref       AS source_ref,
    o.idempotency_key  AS idempotency_key,
    o.session_date     AS session_date,
    o.placed_at        AS placed_at,
    o.resolved_at      AS resolved_at,
    o.filled_shares    AS filled_shares,
    o.fill_price       AS fill_price,
    o.price_source     AS price_source,
    o.quote_time       AS quote_time,
    o.reject_reason    AS reject_reason,
    o.kind             AS kind
FROM orders o
JOIN symbols s ON s.symbol_id = o.symbol_id
WHERE o.owner = :owner
  AND o.idempotency_key = :idempotency_key
  AND o.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

# One book's queued orders whose session has come, in the order the daily job
# fills them: the oldest session first, then the order they were placed in.
SQL_LIST_DUE_ORDERS = """
SELECT
    o.order_id         AS order_id,
    o.book_id          AS book_id,
    s.ticker           AS symbol,
    o.side             AS side,
    o.requested_shares AS requested_shares,
    o.requested_amount AS requested_amount,
    o.status           AS status,
    o.source           AS source,
    o.source_ref       AS source_ref,
    o.idempotency_key  AS idempotency_key,
    o.session_date     AS session_date,
    o.placed_at        AS placed_at,
    o.resolved_at      AS resolved_at,
    o.filled_shares    AS filled_shares,
    o.fill_price       AS fill_price,
    o.price_source     AS price_source,
    o.quote_time       AS quote_time,
    o.reject_reason    AS reject_reason,
    o.kind             AS kind
FROM orders o
JOIN symbols s ON s.symbol_id = o.symbol_id
WHERE o.owner = :owner
  AND o.status = 'pending'
  AND o.session_date <= :through
  AND o.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
ORDER BY o.session_date, o.placed_at, o.order_id;
"""

# One book's orders filled in `[:start, :end)`, in the order they filled: what
# the daily job reports to Discord.
SQL_LIST_FILLED_ORDERS = """
SELECT
    o.order_id         AS order_id,
    o.book_id          AS book_id,
    s.ticker           AS symbol,
    o.side             AS side,
    o.requested_shares AS requested_shares,
    o.requested_amount AS requested_amount,
    o.status           AS status,
    o.source           AS source,
    o.source_ref       AS source_ref,
    o.idempotency_key  AS idempotency_key,
    o.session_date     AS session_date,
    o.placed_at        AS placed_at,
    o.resolved_at      AS resolved_at,
    o.filled_shares    AS filled_shares,
    o.fill_price       AS fill_price,
    o.price_source     AS price_source,
    o.quote_time       AS quote_time,
    o.reject_reason    AS reject_reason,
    o.kind             AS kind
FROM orders o
JOIN symbols s ON s.symbol_id = o.symbol_id
WHERE o.owner = :owner
  AND o.status = 'filled'
  AND o.resolved_at >= :start
  AND o.resolved_at < :end
  AND o.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
ORDER BY o.resolved_at, o.order_id;
"""

# How many orders a book has ever had, of any status. A paper lot is written
# only by an order's fill, so a book with none is untouched.
SQL_COUNT_ORDERS = """
SELECT COUNT(*) AS orders
FROM orders o
WHERE o.owner = :owner
  AND o.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

# The lock a fill holds: one book's row, so two fills in one book can't both
# read the cash balance before either has written its buy.
SQL_LOCK_BOOK = """
SELECT book_id FROM books
WHERE owner = :owner AND book_id = :book_id
FOR UPDATE;
"""

SQL_INSERT_SYMBOL = """
INSERT INTO symbols (ticker, created_at)
VALUES (:ticker, :created_at)
ON CONFLICT(ticker) DO NOTHING;
"""

# `:book_id` is the book `transaction()` has resolved as this owner's and
# locked. A key this owner has already used inserts nothing.
SQL_INSERT_ORDER = """
INSERT INTO orders (
    owner, book_id, symbol_id, side, requested_shares, requested_amount,
    source, source_ref, idempotency_key, session_date, placed_at, kind
) VALUES (
    :owner, :book_id, (SELECT symbol_id FROM symbols WHERE ticker = :ticker), :side,
    :requested_shares, :requested_amount,
    :source, :source_ref, :idempotency_key, :session_date, :placed_at, :kind
)
ON CONFLICT (owner, idempotency_key) DO NOTHING
RETURNING order_id;
"""

# The one statement that takes an order out of `pending`. It matches no row
# for an order that has already been filled, cancelled or rejected, which is
# what makes a fill happen exactly once.
SQL_RESOLVE_ORDER = """
UPDATE orders
SET status = :status,
    resolved_at = :resolved_at,
    filled_shares = :filled_shares,
    fill_price = :fill_price,
    price_source = :price_source,
    quote_time = :quote_time,
    reject_reason = :reject_reason
WHERE owner = :owner
  AND order_id = :order_id
  AND status = 'pending'
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

# Shares promised to sells that are still queued, other than `:order_id`'s own.
SQL_QUEUED_SELL_SHARES = """
SELECT COALESCE(SUM(o.requested_shares), 0) AS shares
FROM orders o
WHERE o.owner = :owner
  AND o.side = 'sell'
  AND o.status = 'pending'
  AND o.order_id <> :order_id
  AND o.symbol_id = (SELECT symbol_id FROM symbols WHERE ticker = :ticker)
  AND o.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

# Plain literals: no value is ever formatted into SQL here.
SQL_SAVEPOINT = "SAVEPOINT order_trade"
SQL_ROLLBACK_TO_SAVEPOINT = "ROLLBACK TO SAVEPOINT order_trade"


def _dec(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "order_id": int(row["order_id"]),
        "book_id": int(row["book_id"]),
        "symbol": (row["symbol"] or "").strip().upper(),
        "side": row["side"],
        "requested_shares": _dec(row["requested_shares"]),
        "requested_amount": _dec(row["requested_amount"]),
        "status": row["status"],
        "source": row["source"],
        "source_ref": row["source_ref"] or None,
        "idempotency_key": row["idempotency_key"],
        "session_date": str(row["session_date"]),
        "placed_at": row["placed_at"],
        "resolved_at": row["resolved_at"],
        "filled_shares": _dec(row["filled_shares"]),
        "fill_price": _dec(row["fill_price"]),
        "price_source": row["price_source"],
        "quote_time": row["quote_time"],
        "reject_reason": row["reject_reason"],
        "kind": row["kind"],
    }


class OrderTransaction:
    """One open transaction on one of an owner's books, with the book's row
    locked. Made by `OrdersRepository.transaction`, which commits when its
    block ends and rolls everything back if the block raises.

    The owner and the book are fixed when it is made, so no method here can
    be pointed at another owner's rows.
    """

    def __init__(self, conn, owner: str, book: int, lots: PortfolioRepository) -> None:
        self._conn = conn
        self._owner = owner
        self._book = book
        self._lots = lots

    def _scope(self, **params: Any) -> Dict[str, Any]:
        return {"owner": self._owner, "book_id": self._book, **params}

    def insert(self, order: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """Store a new pending order. Returns `(order, created)`.

        `created` is False when this owner has already used the order's
        `idempotency_key` in this book: nothing is written and the first
        order comes back. Raises `ValueError` when the key was used in
        another of the owner's books.
        """
        ticker = order["symbol"]
        self._conn.execute(
            SQL_INSERT_SYMBOL, {"ticker": ticker, "created_at": order["placed_at"].isoformat()}
        )
        row = self._conn.execute(SQL_INSERT_ORDER, self._scope(
            ticker=ticker,
            side=order["side"],
            requested_shares=order.get("requested_shares"),
            requested_amount=order.get("requested_amount"),
            source=order["source"],
            source_ref=order.get("source_ref"),
            idempotency_key=order["idempotency_key"],
            session_date=order["session_date"],
            placed_at=order["placed_at"],
            kind=order.get("kind", "market"),
        )).fetchone()
        if row is not None:
            return self.get(int(row["order_id"])), True
        existing = self._conn.execute(
            SQL_GET_ORDER_BY_KEY, self._scope(idempotency_key=order["idempotency_key"])
        ).fetchone()
        if existing is None:
            raise ValueError("this idempotency key has already been used for another book")
        return _row_to_dict(existing), False

    def get(self, order_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(SQL_GET_ORDER, self._scope(order_id=order_id)).fetchone()
        return _row_to_dict(row) if row is not None else None

    def resolve(
        self,
        order_id: int,
        status: str,
        *,
        resolved_at: Any,
        filled_shares: Any = None,
        fill_price: Any = None,
        price_source: Optional[str] = None,
        quote_time: Any = None,
        reject_reason: Optional[str] = None,
    ) -> bool:
        """Move a pending order to `filled`, `cancelled` or `rejected`.
        Returns False, and changes nothing, when the order is not pending."""
        cur = self._conn.execute(SQL_RESOLVE_ORDER, self._scope(
            order_id=order_id,
            status=status,
            resolved_at=resolved_at,
            filled_shares=filled_shares,
            fill_price=fill_price,
            price_source=price_source,
            quote_time=quote_time,
            reject_reason=reject_reason,
        ))
        return cur.rowcount == 1

    def savepoint(self) -> None:
        """Mark the point a trade can be undone to without losing the order."""
        self._conn.execute(SQL_SAVEPOINT)

    def undo_trade(self) -> None:
        """Take back every row written since `savepoint()`."""
        self._conn.execute(SQL_ROLLBACK_TO_SAVEPOINT)

    def buy(self, row: Dict[str, Any]) -> int:
        """Write the lot a buy fills, and its cash row. Returns the `lot_id`."""
        return self._lots.add_lot_on(self._conn, self._owner, row, book_id=self._book)

    def sell(
        self,
        allocation: Sequence[Tuple[int, Any]],
        sale_price: Any,
        sale_trade_date: str,
        *,
        allocation_method: str,
        source: str,
        source_ref: Optional[str],
        order_id: int,
        notes: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Write the sale rows a sell fills, close or split their lots, and
        write the cash rows. `notes` goes on every sale row. Raises
        `ValueError` when a lot can't cover it."""
        return self._lots.close_lots_on(
            self._conn, self._owner, list(allocation), sale_price, sale_trade_date,
            allocation_method=allocation_method, notes=notes, book_id=self._book,
            source=source, source_ref=source_ref, order_id=order_id,
        )

    def open_lots(self, symbol: str) -> List[Dict[str, Any]]:
        """The book's OPEN lots of `symbol`, as this transaction sees them."""
        return self._lots.open_lots_on(self._conn, self._owner, symbol, book_id=self._book)

    def trigger_stop(
        self,
        stop_id: int,
        *,
        stop_level: Any,
        high_water_mark: Any,
        triggered_session: Any,
        trigger_price: Any,
        resolved_at: Any,
    ) -> bool:
        """Mark one of the book's active stops `triggered`, in this
        transaction, so the stop and the sale it causes land together (plan
        step 4.2). Returns False, and changes nothing, when it is not active."""
        return trigger_stop_on(
            self._conn, owner=self._owner, book_id=self._book, stop_id=stop_id,
            stop_level=stop_level, high_water_mark=high_water_mark,
            triggered_session=triggered_session, trigger_price=trigger_price,
            resolved_at=resolved_at,
        )

    def close_stop(self, stop_id: int, *, resolved_at: Any) -> bool:
        """Mark one of the book's active stops `closed`: the holding it was
        set on is gone. Returns False when it is not active."""
        return resolve_stop_on(
            self._conn, owner=self._owner, book_id=self._book, stop_id=stop_id,
            status="closed", resolved_at=resolved_at,
        )

    def order_count(self) -> int:
        """How many orders the book has ever had, of any status."""
        row = self._conn.execute(SQL_COUNT_ORDERS, self._scope()).fetchone()
        return int(row["orders"])

    def cash_entries(self) -> List[Dict[str, Any]]:
        """The book's cash rows, as this transaction sees them."""
        return list_entries_on(self._conn, owner=self._owner, book_id=self._book)

    def queued_sell_shares(self, symbol: str, *, excluding_order_id: int) -> Decimal:
        """Shares of `symbol` promised to the book's other queued sells."""
        row = self._conn.execute(SQL_QUEUED_SELL_SHARES, self._scope(
            ticker=symbol, order_id=excluding_order_id,
        )).fetchone()
        return Decimal(str(row["shares"]))


class OrdersRepository:
    """SQL persistence for paper orders, scoped by owner and by book.

    `book_id=None` on any method means the owner's real book.
    """

    def __init__(self, portfolio_repository: Optional[PortfolioRepository] = None) -> None:
        self._lots = portfolio_repository or PortfolioRepository()

    def list_orders(self, *, owner: str, book_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Every order of one book, newest first."""
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_ORDERS, {"owner": owner, "book_id": book_id}).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_due_orders(
        self, *, owner: str, through: Any, book_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """One book's pending orders queued for a session on or before
        `through`, the oldest session first, then in the order placed."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                SQL_LIST_DUE_ORDERS, {"owner": owner, "through": through, "book_id": book_id}
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_filled_orders(
        self, *, owner: str, start: Any, end: Any, book_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """One book's orders filled in `[start, end)`, in the order they filled."""
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_FILLED_ORDERS, {
                "owner": owner, "start": start, "end": end, "book_id": book_id,
            }).fetchall()
        return [_row_to_dict(r) for r in rows]

    def open_lots(self, *, owner: str, book_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Every OPEN lot of one book: what a seed copies from the real book."""
        return self._lots.list_positions(owner, book_id=book_id)

    def get_order(
        self, *, owner: str, order_id: int, book_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """One order, or None when it isn't in this owner's book."""
        with closing(get_connection()) as conn:
            row = conn.execute(
                SQL_GET_ORDER, {"owner": owner, "order_id": order_id, "book_id": book_id}
            ).fetchone()
        return _row_to_dict(row) if row is not None else None

    def get_order_by_key(
        self, *, owner: str, idempotency_key: str, book_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """The order this owner placed in this book with `idempotency_key`."""
        with closing(get_connection()) as conn:
            row = conn.execute(SQL_GET_ORDER_BY_KEY, {
                "owner": owner, "idempotency_key": idempotency_key, "book_id": book_id,
            }).fetchone()
        return _row_to_dict(row) if row is not None else None

    @contextmanager
    def transaction(
        self, *, owner: str, book_id: Optional[int] = None
    ) -> Iterator[OrderTransaction]:
        """One transaction on one book, with the book's row locked until it
        ends. Commits when the block ends; rolls back if it raises.

        Raises `BookNotFoundError` for a book that isn't this owner's.
        """
        with closing(get_connection()) as conn:
            try:
                book = resolve_book_id(conn, owner=owner, book_id=book_id)
                conn.execute(SQL_LOCK_BOOK, {"owner": owner, "book_id": book})
                yield OrderTransaction(conn, owner, book, self._lots)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
