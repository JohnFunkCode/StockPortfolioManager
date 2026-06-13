-- V2: Multi-owner positions + CSV-parity columns
--
-- Phase 1 Step 6 (docs/proposals/phase1-migration-plan.md): the `positions`
-- table becomes the source of truth for portfolio holdings, replacing direct
-- reads of portfolio.csv. CSV becomes an import format (one file per owner,
-- full-sync/replace semantics, --owner flag).
--
-- All additions are guarded with IF NOT EXISTS so this migration is safe to
-- run against the existing (empty) deployed `positions` table and against a
-- fresh database created by init_schema(). Additive only — no DROP.

-- Owner: which named individual holds these positions. The deployed table is
-- empty (dead schema with no readers/writers), so adding NOT NULL is safe.
ALTER TABLE positions ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'john';

-- The new CSV-parity columns below become the source of truth. The legacy
-- columns (opened_at, entry_price, shares, cost_basis_total) are populated on
-- insert when data is available (opened_at<-purchase_date,
-- entry_price<-purchase_price, shares<-quantity,
-- cost_basis_total<-purchase_price*quantity), but a position may now be added
-- via the REST API without full purchase info, so their NOT NULL / positivity
-- constraints are relaxed. Non-destructive: the deployed table is empty.
ALTER TABLE positions ALTER COLUMN opened_at DROP NOT NULL;
ALTER TABLE positions ALTER COLUMN entry_price DROP NOT NULL;
ALTER TABLE positions ALTER COLUMN shares DROP NOT NULL;
ALTER TABLE positions ALTER COLUMN cost_basis_total DROP NOT NULL;
ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_entry_price_check;
ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_shares_check;
ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_cost_basis_total_check;

-- CSV-parity columns.
ALTER TABLE positions ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS purchase_price REAL;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS quantity INTEGER;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS purchase_date TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS currency TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS sale_price REAL;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS sale_date TEXT;

-- One row per (owner, symbol, purchase lot). symbol_id is 1:1 with the ticker
-- via the symbols table, so (owner, symbol_id, purchase_date) is equivalent to
-- the plan's (owner, symbol, purchase_date) unique key and supports multiple
-- lots of the same symbol bought on different dates.
CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_owner_symbol_date
    ON positions(owner, symbol_id, purchase_date);
