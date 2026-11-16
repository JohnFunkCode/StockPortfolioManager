-- V17: paper orders (#431, plan step 3.3 in
-- docs/proposals/paper-books-realized-pnl-plan.md).
--
-- An order is a request to buy or sell in a paper book. The system sets every
-- fill: at the current quote while the market is open, at the session's
-- opening price for an order placed outside it.
--
--   pending    queued for session_date, and nothing else. An order placed
--              while the market is open never has this status at rest.
--   filled     fill_price, filled_shares, price_source and resolved_at are set
--   cancelled  the owner withdrew a pending order
--   rejected   reject_reason says why, from a fixed list
--
-- The last three are final. A fill is all or nothing.
--
-- A buy asks for shares or for a dollar amount, never both. A sell asks for
-- shares. idempotency_key is the caller's own key for the request: placing it
-- twice returns the first order.
--
-- source says what prompted the trade and source_ref points at it (a stop, a
-- rung, a recommendation). Both are also on lot_sales, where every sale from
-- before this migration is 'manual'. The lot a buy fills and the sale rows a
-- sell fills carry order_id, so a fill is never inferred from the holdings.
--
-- The table carries its owner as well as its book, like positions and
-- cash_entries, so every statement can bind both. An order is never removed.

CREATE TABLE IF NOT EXISTS orders (
    order_id         SERIAL PRIMARY KEY,
    owner            TEXT          NOT NULL,
    book_id          INTEGER       NOT NULL REFERENCES books(book_id),
    symbol_id        INTEGER       NOT NULL REFERENCES symbols(symbol_id),
    side             TEXT          NOT NULL CHECK (side IN ('buy', 'sell')),
    requested_shares NUMERIC(18,6),
    requested_amount NUMERIC(18,2),
    status           TEXT          NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'filled', 'cancelled', 'rejected')),
    source           TEXT          NOT NULL DEFAULT 'manual'
                     CHECK (source IN ('manual', 'stop', 'harvester', 'evaluator', 'watch', 'ai')),
    source_ref       TEXT,
    idempotency_key  TEXT          NOT NULL,
    session_date     DATE          NOT NULL,
    placed_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ,
    filled_shares    NUMERIC(18,6),
    fill_price       NUMERIC(18,6),
    price_source     TEXT          CHECK (price_source IN ('quote', 'session_open')),
    quote_time       TIMESTAMPTZ,
    reject_reason    TEXT          CHECK (reject_reason IN (
                         'stale_quote', 'no_quote', 'non_usd', 'insufficient_cash',
                         'insufficient_shares', 'no_open_price')),
    CONSTRAINT orders_one_request
        CHECK ((requested_shares IS NULL) <> (requested_amount IS NULL)),
    CONSTRAINT orders_sell_in_shares
        CHECK (side = 'buy' OR requested_shares IS NOT NULL),
    CONSTRAINT orders_positive_request
        CHECK (COALESCE(requested_shares, requested_amount) > 0),
    CONSTRAINT orders_filled_has_fill
        CHECK ((status = 'filled') = (fill_price IS NOT NULL AND filled_shares IS NOT NULL)),
    CONSTRAINT orders_rejected_has_reason
        CHECK ((status = 'rejected') = (reject_reason IS NOT NULL)),
    CONSTRAINT orders_pending_is_unresolved
        CHECK ((status = 'pending') = (resolved_at IS NULL))
);

-- One order per key per owner: what makes a second click return the first order.
CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_owner_key ON orders(owner, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_orders_book_status ON orders(book_id, status);

ALTER TABLE positions
  ADD COLUMN IF NOT EXISTS order_id INTEGER REFERENCES orders(order_id);

ALTER TABLE lot_sales
  ADD COLUMN IF NOT EXISTS order_id   INTEGER REFERENCES orders(order_id),
  ADD COLUMN IF NOT EXISTS source     TEXT NOT NULL DEFAULT 'manual'
      CHECK (source IN ('manual', 'stop', 'harvester', 'evaluator', 'watch', 'ai')),
  ADD COLUMN IF NOT EXISTS source_ref TEXT;
