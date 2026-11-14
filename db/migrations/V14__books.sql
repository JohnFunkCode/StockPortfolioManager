-- V14: books (#431, plan step 1.1 in docs/proposals/paper-books-realized-pnl-plan.md).
--
-- A book is a named, owned container of positions (and later cash and orders).
-- Each owner has exactly one REAL book, which holds every position that exists
-- today, and any number of PAPER books. Until now a position belonged to an
-- owner and nothing else, so there was nowhere to put a simulated position
-- without it mixing into real views and alerts.
--
-- positions.book_id says which book a lot is in. lot_sales reaches its book
-- through its lot, the same way it reaches its owner. Every repository
-- statement restricts positions to one book, and with no book named that book
-- is the owner's real one.
--
-- A book is not an account: positions.account stays what it was, the brokerage
-- account within a book.

CREATE TABLE IF NOT EXISTS books (
    book_id       SERIAL PRIMARY KEY,
    owner         TEXT          NOT NULL,
    name          TEXT          NOT NULL,
    kind          TEXT          NOT NULL CHECK (kind IN ('real', 'paper')),
    starting_cash NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (starting_cash >= 0),
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    archived_at   TIMESTAMPTZ
);

-- One real book per owner is a database rule, not a convention: it is what
-- makes "the owner's real book" a single row everywhere it is looked up.
CREATE UNIQUE INDEX IF NOT EXISTS ux_books_one_real_per_owner
    ON books(owner) WHERE kind = 'real';

CREATE UNIQUE INDEX IF NOT EXISTS ux_books_owner_name ON books(owner, name);

ALTER TABLE positions
    ADD COLUMN IF NOT EXISTS book_id INTEGER REFERENCES books(book_id);

-- Backfill: every owner who holds a position gets a real book, and every
-- existing position goes into it. An owner with no positions gets a real book
-- the first time one is written (BooksRepository.ensure_real_book).
INSERT INTO books (owner, name, kind)
SELECT DISTINCT p.owner, 'Real', 'real'
FROM positions p
ON CONFLICT (owner) WHERE kind = 'real' DO NOTHING;

UPDATE positions p
SET book_id = b.book_id
FROM books b
WHERE b.owner = p.owner
  AND b.kind = 'real'
  AND p.book_id IS NULL;

-- After the backfill, so it cannot fail on an existing row. A revision from
-- before this migration writes no book_id, so its Add Lot fails until the new
-- revision is serving; CI migrates immediately before it rolls out.
ALTER TABLE positions ALTER COLUMN book_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_positions_book_status ON positions(book_id, status);
