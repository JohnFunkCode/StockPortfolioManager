-- V27: daily book-value snapshots (#431 phase 3, plan step 5.1 in
-- docs/proposals/paper-books-realized-pnl-plan.md).
--
-- One row a book and session: what the book was worth at that session's
-- close. The daily job writes today's row for every book that isn't archived
-- and has an opening balance by then (quantcore/analytics/performance.py
-- holds the rules). Performance (step 5.3) reads the rows back as a
-- time-weighted return and a drawdown.
--
--   cash            the ledger's balance at the close
--   holdings_value  the open lots at the newest closed bar on or before the
--                   session, in USD. NULL when a holding had no close or no
--                   FX rate (unpriced_count, fx_missing_count say how many)
--   book_value      cash plus holdings_value. NULL when either is incomplete
--   contributed     the opening balance plus deposits less withdrawals that
--                   the ledger counted at the close
--   flow            value that came in or went out without being earned since
--                   the book's previous row: the change in contributed (so a
--                   deposit typed in late, dated before that row, still
--                   counts), plus lots that arrived without cash. NULL when
--                   one of those lots could not be valued
--   stale_count     holdings priced from an earlier session's close
--
-- Re-running the job on the same session replaces that session's row. The
-- table carries its owner as well as its book, like every per-owner table,
-- so every statement can bind both. A book is never deleted, so book_id has
-- no cascade.
CREATE TABLE IF NOT EXISTS book_value_snapshots (
    snapshot_id      SERIAL PRIMARY KEY,
    owner            TEXT          NOT NULL,
    book_id          INTEGER       NOT NULL REFERENCES books(book_id),
    session_date     DATE          NOT NULL,
    cash             NUMERIC(18,2) NOT NULL,
    holdings_value   NUMERIC(18,2),
    book_value       NUMERIC(18,2),
    flow             NUMERIC(18,2),
    contributed      NUMERIC(18,2) NOT NULL,
    holding_count    INTEGER       NOT NULL CHECK (holding_count >= 0),
    unpriced_count   INTEGER       NOT NULL DEFAULT 0 CHECK (unpriced_count >= 0),
    stale_count      INTEGER       NOT NULL DEFAULT 0 CHECK (stale_count >= 0),
    fx_missing_count INTEGER       NOT NULL DEFAULT 0 CHECK (fx_missing_count >= 0),
    taken_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT book_value_snapshots_one_a_session UNIQUE (book_id, session_date),
    CONSTRAINT book_value_snapshots_value_rule
        CHECK (book_value IS NULL
               OR (holdings_value IS NOT NULL AND book_value = cash + holdings_value))
);
