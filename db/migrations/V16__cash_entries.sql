-- V16: the cash ledger (#431, plan step 3.1 in
-- docs/proposals/paper-books-realized-pnl-plan.md).
--
-- A book holds cash as well as positions. cash_entries is the book's ledger:
-- one signed USD amount per row, positive when cash comes in.
--
--   opening_balance  the cash the book held at the start of entry_date. One
--                    per book. Entries dated before it are not counted.
--   deposit          cash paid in      (amount > 0), entered by the owner
--   withdrawal       cash taken out    (amount < 0), entered by the owner
--   buy              a lot was opened  (amount <= 0), one per lot
--   sell             a sale was made   (amount >= 0), one per lot_sales row
--
-- A buy or sell row is derived from its lot or sale and rewritten whenever
-- that row changes, in the same transaction. Its amount is NULL when the lot
-- is not in USD and has no FX rate for the trade date: an unknown amount is
-- flagged and left out of the balance, never converted at 1.0.
--
-- The table carries its owner as well as its book, like positions, so every
-- statement can bind both. A buy or sell row goes when its lot or sale goes.
-- A book is never deleted, so book_id has no cascade.
--
-- Existing lots and sales get their rows from scripts/backfill_cash_entries.py,
-- not from this migration: an operator runs it once per environment.

CREATE TABLE IF NOT EXISTS cash_entries (
    entry_id   SERIAL PRIMARY KEY,
    owner      TEXT          NOT NULL,
    book_id    INTEGER       NOT NULL REFERENCES books(book_id),
    kind       TEXT          NOT NULL
               CHECK (kind IN ('opening_balance', 'deposit', 'withdrawal', 'buy', 'sell')),
    entry_date DATE          NOT NULL,
    amount     NUMERIC(18,2),
    lot_id     INTEGER       REFERENCES positions(position_id) ON DELETE CASCADE,
    sale_id    INTEGER       REFERENCES lot_sales(sale_id) ON DELETE CASCADE,
    notes      TEXT,
    created_at TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT cash_entries_buy_has_lot CHECK ((kind = 'buy') = (lot_id IS NOT NULL)),
    CONSTRAINT cash_entries_sell_has_sale CHECK ((kind = 'sell') = (sale_id IS NOT NULL)),
    CONSTRAINT cash_entries_manual_has_amount
        CHECK (amount IS NOT NULL OR kind IN ('buy', 'sell'))
);

-- One buy row per lot, one sell row per sale, one opening balance per book.
-- The first two are what let the backfill skip a row that is already there.
CREATE UNIQUE INDEX IF NOT EXISTS ux_cash_entries_buy_lot
    ON cash_entries(lot_id) WHERE kind = 'buy';
CREATE UNIQUE INDEX IF NOT EXISTS ux_cash_entries_sell_sale
    ON cash_entries(sale_id) WHERE kind = 'sell';
CREATE UNIQUE INDEX IF NOT EXISTS ux_cash_entries_one_opening
    ON cash_entries(book_id) WHERE kind = 'opening_balance';

CREATE INDEX IF NOT EXISTS idx_cash_entries_book_date ON cash_entries(book_id, entry_date);
