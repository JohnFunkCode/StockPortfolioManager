-- V12: per-owner options positions (#460, security gap G1).
--
-- Until now `options_positions` had no owner: every row was implicitly John's,
-- and the daily notification pass expired and alerted on every row, posting to
-- his Discord webhook. Nothing user-facing reads the table yet, but the first
-- feature that did would have been cross-user by default. The owner column
-- makes isolation an SQL predicate in every OptionsPositionStore statement,
-- the same rule Harvester (V7) and positions (V2) follow.
--
-- Existing rows are John's, which is what the DEFAULT backfills.

ALTER TABLE options_positions ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'john';

CREATE INDEX IF NOT EXISTS idx_options_positions_owner_status
    ON options_positions(owner, status);
