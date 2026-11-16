-- V19: stored stops (#431 phase 2 and #221, plan step 4.1 in
-- docs/proposals/paper-books-realized-pnl-plan.md).
--
-- A stop is the owner's decision to get out of a holding at a level. It is
-- set on a symbol in one book, not on a lot: lots bought later are covered by
-- the same stop.
--
--   fixed     stop_level is the price the owner chose
--   trailing  trail_pct percent below high_water_mark. stop_level is that
--             level, stored. The checker raises the mark and never lowers it.
--
--   active     live. At most one per book and symbol.
--   triggered  a closed bar reached the level
--   cancelled  the owner took it off
--   replaced   the owner set another stop on the symbol
--   closed     the position went flat
--
-- The last four are final. A stop is never removed: what was set, how, and
-- what became of it is the record that says which stop method works.
--
-- method says how the level was chosen and proposed_level what the analysis
-- suggested, beside the level saved. reference_price is the last close the
-- stop was set against and as_of that close's date. method has no CHECK on
-- purpose: a new method to compare needs no hand-applied migration.
--
-- source says what set the stop and source_ref points at it.
--
-- The table carries its owner as well as its book, like positions and orders,
-- so every statement can bind both. A book is never deleted, so book_id has
-- no cascade.

CREATE TABLE IF NOT EXISTS stops (
    stop_id         SERIAL PRIMARY KEY,
    owner           TEXT          NOT NULL,
    book_id         INTEGER       NOT NULL REFERENCES books(book_id),
    symbol_id       INTEGER       NOT NULL REFERENCES symbols(symbol_id),
    kind            TEXT          NOT NULL CHECK (kind IN ('fixed', 'trailing')),
    stop_level      NUMERIC(18,6) NOT NULL CHECK (stop_level > 0),
    trail_pct       NUMERIC(7,4)  CHECK (trail_pct > 0 AND trail_pct < 100),
    high_water_mark NUMERIC(18,6) CHECK (high_water_mark > 0),
    method          TEXT          NOT NULL DEFAULT 'manual',
    proposed_level  NUMERIC(18,6),
    reference_price NUMERIC(18,6),
    as_of           DATE          NOT NULL,
    source          TEXT          NOT NULL DEFAULT 'manual'
                    CHECK (source IN ('manual', 'harvester', 'evaluator', 'watch', 'ai')),
    source_ref      TEXT,
    status          TEXT          NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'triggered', 'cancelled', 'replaced', 'closed')),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    CONSTRAINT stops_trailing_has_trail
        CHECK ((kind = 'trailing') = (trail_pct IS NOT NULL AND high_water_mark IS NOT NULL)),
    CONSTRAINT stops_fixed_has_no_trail
        CHECK (kind = 'trailing' OR (trail_pct IS NULL AND high_water_mark IS NULL)),
    CONSTRAINT stops_active_is_unresolved
        CHECK ((status = 'active') = (resolved_at IS NULL))
);

-- One live stop per symbol per book: what makes setting a second one a
-- replacement and not a second stop.
CREATE UNIQUE INDEX IF NOT EXISTS ux_stops_one_active
    ON stops(book_id, symbol_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_stops_book_status ON stops(book_id, status);
