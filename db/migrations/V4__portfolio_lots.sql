-- V4: Portfolio lots (issue #126, PR 3)
--
-- The `positions` table becomes lot-granular: multiple open lots per symbol,
-- partial sales via `lot_sales`, and parent/child lineage for splits. V2's
-- (owner, symbol_id, purchase_date) UNIQUE index forbade exactly the split
-- rule this PR introduces (a partial sale creates a second lot with the SAME
-- owner/symbol/trade_date as its parent), so it is dropped here.

-- Drop the index that forbids #126's own split rule. A partial sale creates a
-- second lot with the SAME (owner, symbol, trade_date) as its parent.
DROP INDEX IF EXISTS idx_positions_owner_symbol_date;

ALTER TABLE positions
  ADD COLUMN IF NOT EXISTS status            TEXT NOT NULL DEFAULT 'OPEN',
  ADD COLUMN IF NOT EXISTS parent_lot_id     INTEGER REFERENCES positions(position_id) ON DELETE SET NULL,
  -- 'trade_date', not 'purchase_date': cost basis and holding period run from
  -- the TRADE date, not the settlement date, and "days held" counts from it.
  ADD COLUMN IF NOT EXISTS trade_date        DATE,
  ADD COLUMN IF NOT EXISTS fees              NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS acquisition_type  TEXT NOT NULL DEFAULT 'BUY',
  ADD COLUMN IF NOT EXISTS covered           BOOLEAN,
  ADD COLUMN IF NOT EXISTS fx_rate_at_purchase NUMERIC(18,8),
  -- Nullable now, populated when tax work lands (#126 decision 10).
  ADD COLUMN IF NOT EXISTS basis_adjustment      NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS wash_sale_disallowed  NUMERIC(18,6);

-- Fractional shares: Schwab Stock Slices = 4dp, E*TRADE = 3dp on the ticket.
ALTER TABLE positions ALTER COLUMN quantity TYPE NUMERIC(18,6);

-- Backfill (#126 decision 11).
UPDATE positions SET status = 'OPEN'          WHERE status IS NULL;
UPDATE positions SET acquisition_type = 'BUY' WHERE acquisition_type IS NULL;
UPDATE positions SET trade_date = purchase_date::date
  WHERE trade_date IS NULL AND purchase_date IS NOT NULL AND purchase_date <> '';

CREATE INDEX IF NOT EXISTS idx_positions_owner_status ON positions(owner, status);
CREATE INDEX IF NOT EXISTS idx_positions_parent       ON positions(parent_lot_id);

CREATE TABLE IF NOT EXISTS lot_sales (
    sale_id        SERIAL PRIMARY KEY,
    lot_id         INTEGER NOT NULL REFERENCES positions(position_id) ON DELETE CASCADE,
    shares_sold    NUMERIC(18,6) NOT NULL,
    sale_price     NUMERIC(18,6) NOT NULL,
    sale_trade_date DATE NOT NULL,
    fees           NUMERIC(18,6),
    allocation_method TEXT,      -- FIFO | LIFO | HIFO | MANUAL
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes          TEXT
);
CREATE INDEX IF NOT EXISTS idx_lot_sales_lot ON lot_sales(lot_id);
