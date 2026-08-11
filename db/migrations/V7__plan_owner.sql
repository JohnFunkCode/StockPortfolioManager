-- V7: multi-owner Harvester plans (#147 Part H1).
--
-- Until now a harvest plan had no owner: `plan_instances` was implicitly John's,
-- and `ux_one_active_plan_per_symbol` made "one ACTIVE plan per symbol" a
-- database invariant for the whole installation. With more than one owner that
-- invariant is wrong -- two people may each hold NVDA and each want a ladder on
-- it -- so the uniqueness moves to (owner, symbol_id).
--
-- Existing rows are John's, which is what the DEFAULT backfills.

ALTER TABLE plan_instances ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'john';

-- The DROP and the CREATE below must stay adjacent and in this order. Between
-- them there is no uniqueness constraint on active plans at all; keeping them
-- together keeps that window to a single statement.
DROP INDEX IF EXISTS ux_one_active_plan_per_symbol;

CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_plan_per_owner_symbol
    ON plan_instances(owner, symbol_id) WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_instances_owner_status
    ON plan_instances(owner, status);
