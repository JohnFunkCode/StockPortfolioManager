-- V5: Watchlist persistence (issue #83)
--
-- POST /api/watchlist appended a YAML block to watchlist.yaml on the Cloud Run
-- container filesystem: in-memory, per-instance, and reset to the image's copy
-- on every deploy. The write usually SUCCEEDED, which is what made it a silent
-- failure rather than a loud one -- the symbol appeared, was invisible to any
-- read served by another instance, and vanished on scale-to-zero.
--
-- The list is deliberately GLOBAL (plan decision 1): one shared watchlist, no
-- `owner` column. The portfolio is per-owner and the watchlist is not; that
-- inconsistency is accepted and recorded rather than designed around. The
-- forward path if per-owner watchlists are ever wanted is to add `owner TEXT`
-- and swap require_principal for require_owner on the write routes -- nothing
-- here blocks it.

CREATE TABLE IF NOT EXISTS watchlist (
    watchlist_id SERIAL PRIMARY KEY,
    -- UNIQUE, so "already in the watchlist" is a database invariant rather
    -- than a check-then-insert race. The service turns the conflict into 409.
    symbol_id    INTEGER NOT NULL UNIQUE REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    name         TEXT,
    currency     TEXT NOT NULL DEFAULT 'USD',
    -- The schema's first array column. psycopg2 adapts a Python list to TEXT[]
    -- and back natively, so no join table and no comma-joined string parsing.
    tags         TEXT[] NOT NULL DEFAULT '{}',
    -- Audit only: the authenticated principal that added the row. The list is
    -- global, so this never gates a read or a delete.
    added_by     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
