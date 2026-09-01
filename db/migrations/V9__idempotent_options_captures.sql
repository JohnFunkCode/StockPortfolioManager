-- V9: reserve one full-chain capture per symbol and market day (#234).
--
-- This is deliberately a separate claim table rather than a unique index on
-- options_snapshots: production already contains historical duplicate rows,
-- and this migration must not delete or rewrite them. New retries claim their
-- symbol/chain/day atomically before inserting the snapshot and child rows.

CREATE TABLE IF NOT EXISTS options_capture_claims (
    capture_id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    chain_type TEXT NOT NULL,
    trading_day DATE NOT NULL,
    claimed_at TEXT NOT NULL,
    snapshot_id INTEGER UNIQUE,
    UNIQUE(symbol, chain_type, trading_day),
    FOREIGN KEY(snapshot_id) REFERENCES options_snapshots(snapshot_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_options_capture_claims_day
    ON options_capture_claims(symbol, trading_day DESC);
