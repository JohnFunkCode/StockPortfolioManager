-- V6: Back-fill the three tables that only ever shipped through init_schema()
--     (issue #165, PR 2 of docs/proposals/schema-ownership-plan.md)
--
-- This is the first parity run's finding, written down as SQL. `gex_history`,
-- `user_settings`, and `arb_nav_snapshots` were added to `_SCHEMA` in
-- `quantcore/db.py` and never given a migration, so a database built purely
-- from db/baseline + db/migrations was missing all three. Nothing broke,
-- because `init_schema()` runs on every application startup and created them
-- anyway -- which is exactly the failure mode #165 is about: the migrations
-- stopped describing the schema and nobody could tell.
--
-- Per decision D6, prod reality wins. All three tables are confirmed present
-- in prod (`quantcore-prod-20260606`, verified read-only on 2026-08-09), so
-- the migrations are the side that was wrong and this file makes them agree.
--
-- Every statement is idempotent. Deployed databases already have these
-- objects, so when Flyway finally applies V6 there it will find nothing to do
-- and record the version -- that no-op is the point. The DDL below is copied
-- verbatim from `_SCHEMA` so the two owners stay byte-identical under
-- `tests/test_schema_parity.py`; change one and you must change the other.

CREATE TABLE IF NOT EXISTS gex_history (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    date_only TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    price REAL,
    net_gex REAL,
    zero_gamma_level REAL,
    regime TEXT,
    payload TEXT,
    UNIQUE(symbol, date_only)
);

CREATE INDEX IF NOT EXISTS idx_gex_history_symbol_date ON gex_history(symbol, date_only DESC);

-- Per-owner UI preferences (issue #124). Currently just the Sidekick chat
-- model; `SettingsService` validates the value against the allow-list in
-- `quantcore/chat_models.py` rather than a CHECK constraint, so a retired
-- model id degrades to the default instead of making the row unreadable.
CREATE TABLE IF NOT EXISTS user_settings (
    owner       TEXT PRIMARY KEY,
    chat_model  TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Arbitrage scanner: point-in-time holdings/capital-structure snapshots for
-- NAV vehicles (MSTR-style treasuries, closed-end funds, trusts). Curated
-- input rather than fetched -- no free API publishes a coin count or a
-- preferred stack -- so each row carries its own as-of date and source, and a
-- premium history becomes computable as snapshots accumulate.
-- DOUBLE PRECISION rather than the REAL used for prices elsewhere: these are
-- whole-balance-sheet figures (a $16.5B preferred stack), and float4's ~7
-- significant digits would quietly round them.
CREATE TABLE IF NOT EXISTS arb_nav_snapshots (
    security            TEXT NOT NULL,
    as_of               TEXT NOT NULL,
    underlying          TEXT NOT NULL,
    units               DOUBLE PRECISION NOT NULL CHECK(units >= 0),
    senior_claims       DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(senior_claims >= 0),
    annual_senior_cost  DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(annual_senior_cost >= 0),
    other_assets        DOUBLE PRECISION NOT NULL DEFAULT 0,
    diluted_shares      DOUBLE PRECISION CHECK(diluted_shares IS NULL OR diluted_shares > 0),
    source              TEXT,
    ingested_at         INTEGER NOT NULL,
    PRIMARY KEY (security, as_of)
);

CREATE INDEX IF NOT EXISTS idx_arb_nav_latest
    ON arb_nav_snapshots(security, as_of DESC);
