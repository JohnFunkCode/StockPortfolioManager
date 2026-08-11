import json
import logging
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from quantcore.schema_introspect import describe_schema, diff_schemas

# Load .env from the project root so every entry point (main.py, REST API,
# MCP servers) resolves QUANTCORE_DB_DSN consistently. Existing environment
# variables are not overridden.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

logger = logging.getLogger(__name__)

DB_DSN = os.getenv(
    "QUANTCORE_DB_DSN",
    "postgresql://quantcore:changeme@localhost:5432/quantcore",
)

# The committed expectation, written by scripts/check_schema_snapshot.py
# --update. Ships in every image (.dockerignore excludes *.db files, not the
# db/ directory), so warn/verify work in a container as they do locally.
SCHEMA_SNAPSHOT = Path(__file__).resolve().parent.parent / "db" / "schema_snapshot.json"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    symbol_id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    name TEXT,
    exchange TEXT,
    currency TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ohlcv (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL DEFAULT '1d',
    ts INTEGER NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL NOT NULL CHECK(close > 0),
    adj_close REAL,
    volume REAL CHECK(volume >= 0),
    status TEXT NOT NULL DEFAULT 'CLOSED'
        CHECK(status IN ('OPEN','CLOSED','GAP','CORRECTED')),
    data_vendor TEXT NOT NULL DEFAULT 'yfinance',
    ingested_at INTEGER NOT NULL,
    PRIMARY KEY (symbol, interval, ts)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup ON ohlcv (symbol, interval, ts DESC);

CREATE INDEX IF NOT EXISTS idx_ohlcv_needs_action ON ohlcv (symbol, interval)
    WHERE status IN ('OPEN', 'CORRECTED');

CREATE TABLE IF NOT EXISTS fetch_log (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY(symbol, interval)
);

CREATE TABLE IF NOT EXISTS plan_templates (
    template_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    is_dynamic_h INTEGER NOT NULL,
    history_window_days INTEGER NOT NULL,
    n_iterations INTEGER NOT NULL,
    alpha REAL,
    min_h REAL,
    max_h REAL,
    fixed_h REAL,
    drift_method TEXT NOT NULL DEFAULT 'CAGR',
    vol_method TEXT NOT NULL DEFAULT 'LOGRET_STD',
    stats_price_series TEXT NOT NULL DEFAULT 'adj_close',
    created_at TEXT NOT NULL,
    notes TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    position_id SERIAL PRIMARY KEY,
    symbol_id INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    entry_price REAL NOT NULL,
    shares INTEGER NOT NULL,
    cost_basis_total REAL NOT NULL,
    account TEXT,
    notes TEXT,
    FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    CHECK(shares > 0),
    CHECK(entry_price > 0),
    CHECK(cost_basis_total >= 0)
);

-- V2 (Phase 1 Step 6): multi-owner positions + CSV-parity columns. Mirrors
-- db/migrations/V2__positions_multi_owner.sql so fresh/test databases created
-- by init_schema() converge to the same shape Flyway applies to existing ones.
-- Additive only -- the IF NOT EXISTS guards make this idempotent.
ALTER TABLE positions ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'john';
ALTER TABLE positions ALTER COLUMN opened_at DROP NOT NULL;
ALTER TABLE positions ALTER COLUMN entry_price DROP NOT NULL;
ALTER TABLE positions ALTER COLUMN shares DROP NOT NULL;
ALTER TABLE positions ALTER COLUMN cost_basis_total DROP NOT NULL;
ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_entry_price_check;
ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_shares_check;
ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_cost_basis_total_check;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS purchase_price REAL;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS quantity INTEGER;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS purchase_date TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS currency TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS sale_price REAL;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS sale_date TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_owner_symbol_date
    ON positions(owner, symbol_id, purchase_date);

-- V4 (issue #126, PR 3): lot-granular positions. Mirrors
-- db/migrations/V4__portfolio_lots.sql. Drops the V2 unique index (it forbids
-- the split rule: a partial sale creates a second lot with the SAME
-- owner/symbol/trade_date as its parent) and adds lot status/lineage columns
-- plus the lot_sales table.
DROP INDEX IF EXISTS idx_positions_owner_symbol_date;

ALTER TABLE positions
  ADD COLUMN IF NOT EXISTS status            TEXT NOT NULL DEFAULT 'OPEN',
  ADD COLUMN IF NOT EXISTS parent_lot_id     INTEGER REFERENCES positions(position_id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS trade_date        DATE,
  ADD COLUMN IF NOT EXISTS fees              NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS acquisition_type  TEXT NOT NULL DEFAULT 'BUY',
  ADD COLUMN IF NOT EXISTS covered           BOOLEAN,
  ADD COLUMN IF NOT EXISTS fx_rate_at_purchase NUMERIC(18,8),
  ADD COLUMN IF NOT EXISTS basis_adjustment      NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS wash_sale_disallowed  NUMERIC(18,6);

ALTER TABLE positions ALTER COLUMN quantity TYPE NUMERIC(18,6);

UPDATE positions SET status = 'OPEN'          WHERE status IS NULL;
UPDATE positions SET acquisition_type = 'BUY' WHERE acquisition_type IS NULL;
UPDATE positions SET trade_date = purchase_date::date
  WHERE trade_date IS NULL AND purchase_date IS NOT NULL AND purchase_date <> '';

CREATE INDEX IF NOT EXISTS idx_positions_owner_status ON positions(owner, status);
CREATE INDEX IF NOT EXISTS idx_positions_parent       ON positions(parent_lot_id);

CREATE TABLE IF NOT EXISTS lot_sales (
    sale_id        SERIAL PRIMARY KEY,
    lot_id         INTEGER NOT NULL REFERENCES positions(position_id) ON DELETE CASCADE,
    shares_sold    NUMERIC(18,6) NOT NULL,
    sale_price     NUMERIC(18,6) NOT NULL,
    sale_trade_date DATE NOT NULL,
    fees           NUMERIC(18,6),
    allocation_method TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes          TEXT
);
CREATE INDEX IF NOT EXISTS idx_lot_sales_lot ON lot_sales(lot_id);

-- Global watchlist (#83, V5). No `owner` column by design -- one shared list.
-- symbol_id is UNIQUE so duplicate-add is a database invariant, not a race.
-- `tags` is the schema's first array column. psycopg2 adapts a Python list to
-- TEXT[] natively. NB: _split_schema() splits this string on a bare
-- semicolon, so one must never appear here -- not even inside a comment.
CREATE TABLE IF NOT EXISTS watchlist (
    watchlist_id SERIAL PRIMARY KEY,
    symbol_id    INTEGER NOT NULL UNIQUE REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    name         TEXT,
    currency     TEXT NOT NULL DEFAULT 'USD',
    tags         TEXT[] NOT NULL DEFAULT '{}',
    added_by     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plan_instances (
    instance_id SERIAL PRIMARY KEY,
    template_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL,
    position_id INTEGER,
    owner TEXT NOT NULL DEFAULT 'john',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    asof_date TEXT NOT NULL,
    price_asof REAL NOT NULL,
    shares_initial INTEGER NOT NULL,
    v0_floor REAL NOT NULL,
    capital_at_risk REAL NOT NULL,
    history_end_date TEXT NOT NULL,
    history_window_days INTEGER NOT NULL,
    r_daily REAL NOT NULL,
    annual_vol REAL NOT NULL,
    h_threshold REAL NOT NULL,
    n_iterations INTEGER NOT NULL,
    stats_price_series TEXT NOT NULL DEFAULT 'adj_close',
    supersedes_instance_id INTEGER,
    notes TEXT,
    metadata_json TEXT,
    FOREIGN KEY(template_id) REFERENCES plan_templates(template_id) ON DELETE CASCADE,
    FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    FOREIGN KEY(position_id) REFERENCES positions(position_id) ON DELETE SET NULL,
    FOREIGN KEY(supersedes_instance_id) REFERENCES plan_instances(instance_id) ON DELETE SET NULL
);

-- CREATE TABLE IF NOT EXISTS above is a no-op on a database that predates the
-- column, so `owner` has to be added explicitly the same way positions' did --
-- and it has to happen before the index below, which is built on it.
ALTER TABLE plan_instances ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'john';

-- One ACTIVE plan per (owner, symbol), not per symbol -- two owners may each
-- run a ladder on the same ticker (#147 Part H1, V7).
--
-- This DROP is the only non-additive statement in _SCHEMA, and the pair must
-- stay adjacent and in this order: under QUANTCORE_SCHEMA_MODE=create against a
-- live database there is a window between them with no uniqueness constraint on
-- active plans at all. Anything inserted between the two -- another statement,
-- a reordering -- widens that window.
DROP INDEX IF EXISTS ux_one_active_plan_per_symbol;

CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_plan_per_owner_symbol
    ON plan_instances(owner, symbol_id) WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_instances_symbol_status
    ON plan_instances(symbol_id, status);

CREATE INDEX IF NOT EXISTS idx_instances_owner_status
    ON plan_instances(owner, status);

CREATE TABLE IF NOT EXISTS plan_rungs (
    rung_id SERIAL PRIMARY KEY,
    instance_id INTEGER NOT NULL,
    rung_index INTEGER NOT NULL,
    target_price REAL NOT NULL,
    shares_before INTEGER NOT NULL,
    shares_sold_planned INTEGER NOT NULL,
    shares_after_planned INTEGER NOT NULL,
    expected_days_from_now REAL,
    expected_date TEXT,
    gross_harvest_planned REAL NOT NULL,
    cumulative_harvest_planned REAL NOT NULL,
    remaining_value_planned REAL NOT NULL,
    total_wealth_planned REAL NOT NULL,
    total_return_planned REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    triggered_at TEXT,
    trigger_price REAL,
    executed_at TEXT,
    executed_price REAL,
    shares_sold_actual INTEGER,
    gross_harvest_actual REAL,
    tax_paid_actual REAL,
    net_harvest_actual REAL,
    notes TEXT,
    UNIQUE(instance_id, rung_index),
    FOREIGN KEY(instance_id) REFERENCES plan_instances(instance_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_rungs_instance_status ON plan_rungs(instance_id, status);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id SERIAL PRIMARY KEY,
    rung_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL,
    instance_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL DEFAULT 'PRICE_GE',
    threshold_price REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    last_checked_at TEXT,
    fired_at TEXT,
    fired_price REAL,
    cooldown_seconds INTEGER,
    channel TEXT,
    destination TEXT,
    message_template TEXT,
    UNIQUE(rung_id),
    FOREIGN KEY(rung_id) REFERENCES plan_rungs(rung_id) ON DELETE CASCADE,
    FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    FOREIGN KEY(instance_id) REFERENCES plan_instances(instance_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_alerts_symbol_status ON alerts(symbol_id, status);

CREATE TABLE IF NOT EXISTS options_snapshots (
    snapshot_id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    price REAL NOT NULL,
    bb_upper REAL,
    bb_middle REAL,
    bb_lower REAL,
    bb_period INTEGER DEFAULT 20,
    chain_type TEXT NOT NULL DEFAULT 'atm',
    UNIQUE(symbol, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_time
    ON options_snapshots(symbol, captured_at DESC);

CREATE TABLE IF NOT EXISTS options_expirations (
    expiration_id SERIAL PRIMARY KEY,
    snapshot_id INTEGER NOT NULL,
    expiration TEXT NOT NULL,
    put_call_ratio REAL,
    total_call_oi INTEGER,
    total_put_oi INTEGER,
    total_call_vol INTEGER,
    total_put_vol INTEGER,
    avg_call_iv REAL,
    avg_put_iv REAL,
    UNIQUE(snapshot_id, expiration),
    FOREIGN KEY(snapshot_id) REFERENCES options_snapshots(snapshot_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_expirations_snapshot ON options_expirations(snapshot_id);

CREATE TABLE IF NOT EXISTS options_contracts (
    contract_id SERIAL PRIMARY KEY,
    expiration_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('call', 'put')),
    strike REAL NOT NULL,
    last_price REAL,
    bid REAL,
    ask REAL,
    implied_vol REAL,
    volume INTEGER,
    open_interest INTEGER,
    in_the_money INTEGER CHECK (in_the_money IN (0, 1)),
    UNIQUE(expiration_id, kind, strike),
    FOREIGN KEY(expiration_id) REFERENCES options_expirations(expiration_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_contracts_expiration ON options_contracts(expiration_id);

CREATE TABLE IF NOT EXISTS gamma_wall_history (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    date_only TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    price REAL NOT NULL,
    gamma_wall_strike REAL,
    delta_flip_strike REAL,
    dist_to_flip_pct REAL,
    net_daoi_shares REAL,
    call_daoi_shares REAL,
    put_daoi_shares REAL,
    mm_hedge_bias TEXT,
    signal TEXT,
    expirations_scanned TEXT,
    payload TEXT,
    UNIQUE(symbol, date_only)
);

CREATE INDEX IF NOT EXISTS idx_gamma_wall_symbol_date ON gamma_wall_history(symbol, date_only DESC);

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

CREATE TABLE IF NOT EXISTS options_positions (
    position_id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('call', 'put')),
    strike REAL NOT NULL,
    expiration TEXT NOT NULL,
    contracts INTEGER NOT NULL DEFAULT 1,
    purchase_price REAL NOT NULL,
    purchase_date TEXT NOT NULL,
    target_price REAL,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK(status IN ('ACTIVE', 'CLOSED', 'EXPIRED')),
    closed_at TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol ON options_positions(symbol);

CREATE INDEX IF NOT EXISTS idx_positions_expiration ON options_positions(expiration);

CREATE TABLE IF NOT EXISTS news_articles (
    article_id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    publisher TEXT,
    url TEXT NOT NULL,
    published_at TEXT,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    sentiment TEXT CHECK(sentiment IN ('positive','negative','neutral')),
    sentiment_score REAL,
    positive_score REAL,
    negative_score REAL,
    neutral_score REAL,
    UNIQUE(symbol, url),
    CHECK(source IN ('rss', 'yfinance'))
);

CREATE INDEX IF NOT EXISTS idx_news_symbol_time ON news_articles(symbol, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_unscored ON news_articles(symbol) WHERE sentiment IS NULL;

CREATE TABLE IF NOT EXISTS sentiment_snapshots (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    article_count INTEGER NOT NULL DEFAULT 0,
    positive_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    neutral_count INTEGER NOT NULL DEFAULT 0,
    scored_count INTEGER NOT NULL DEFAULT 0,
    overall_sentiment TEXT,
    articles_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_ts ON sentiment_snapshots(symbol, captured_at DESC);

CREATE TABLE IF NOT EXISTS fundamentals_history (
    symbol TEXT NOT NULL,
    data_type TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(symbol, data_type, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_latest
    ON fundamentals_history(symbol, data_type, fetched_at DESC);

CREATE TABLE IF NOT EXISTS user_settings (
    owner       TEXT PRIMARY KEY,
    chat_model  TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Arbitrage scanner: point-in-time holdings/capital-structure snapshots for
-- NAV vehicles (MSTR-style treasuries, closed-end funds, trusts). Curated
-- input rather than fetched — no free API publishes a coin count or a
-- preferred stack — so each row carries its own as-of date and source, and a
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

-- Maps an authenticated identity (IAP email or MCP token sub, lowercased) to
-- the canonical short-handle owner value used elsewhere (e.g. positions.owner).
-- Unknown identities are never auto-provisioned - rows are admin-managed (see
-- db/migrations/V3__owner_identities.sql and scripts/grant_quantui_iap_access.sh).
CREATE TABLE IF NOT EXISTS owner_identities (
    identity   TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes      TEXT
);

CREATE INDEX IF NOT EXISTS idx_owner_identities_owner ON owner_identities(owner);
"""


def _adapt_sql(sql: str, params):
    """Convert SQLite ? or :name params to psycopg2 %s / %(name)s."""
    if params is None:
        return sql, params
    if isinstance(params, dict):
        sql = re.sub(r':(\w+)', r'%(\1)s', sql)
    else:
        sql = sql.replace('?', '%s')
    return sql, params


class _PGConn:
    """
    Thin sqlite3-compatible wrapper around a psycopg2 connection.

    Provides conn.execute(sql, params).fetchone()/fetchall() so call sites
    written for sqlite3 work without modification, while automatically
    converting ? and :name parameter styles to psycopg2's %s / %(name)s.
    """

    def __init__(self, pg_conn):
        self._c = pg_conn

    def execute(self, sql: str, params=None):
        sql, params = _adapt_sql(sql, params)
        cur = self._c.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, seq):
        if seq and isinstance(seq[0], dict):
            sql = re.sub(r':(\w+)', r'%(\1)s', sql)
        else:
            sql = sql.replace('?', '%s')
        cur = self._c.cursor()
        # psycopg2's cursor.executemany() is one server round trip PER ROW.
        # Against Cloud SQL through the auth proxy (~90ms RTT) that made a
        # 3.2K-contract options snapshot take ~5 minutes; execute_batch sends
        # pages of statements per round trip (500 rows ≈ 7 trips, <1s).
        psycopg2.extras.execute_batch(cur, sql, seq, page_size=500)
        return cur

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._c.__exit__(exc_type, exc_val, exc_tb)


# Schema is ensured once per process on first connection, so callers never
# see a missing table regardless of which entry point started the process.
# Entry points should call ensure_schema() rather than init_schema(): it
# records the DSN, so the DDL is not re-run on the first connection afterwards.
# The lock keeps concurrent first connections (threaded Flask / MCP servers)
# from running the DDL in parallel — PostgreSQL's CREATE ... IF NOT EXISTS can
# fail under concurrent execution.
#
# Keyed by DSN rather than a single bool so that pointing a repository at a
# second database (OptionsStore(dsn=...)) initializes it once too, instead of
# on every construction.
_schema_ready_dsns: set[str] = set()
# Reentrant: ensure_schema() holds it across init_schema(), which records the
# DSN under the same lock.
_schema_lock = threading.RLock()


class SchemaDriftError(RuntimeError):
    """The live schema is missing something the code expects.

    Raised only in ``verify`` mode. ``EXTRA`` objects never raise: a deployed
    database is allowed to carry more than the snapshot describes (decision D4
    in ``docs/proposals/schema-ownership-plan.md``).
    """


SCHEMA_MODES = ("create", "warn", "verify", "auto")


def _flyway_managed(dsn: str) -> bool:
    """Has ``flyway migrate`` ever run against this database?

    ``to_regclass`` returns NULL rather than raising for an absent relation,
    so this is one cheap read-only query with no error handling around it.
    """
    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.flyway_schema_history')")
            return cur.fetchone()[0] is not None
    finally:
        conn.close()


def _resolve_schema_mode(dsn: str) -> tuple[str, str]:
    """Return ``(requested, resolved)`` for ``QUANTCORE_SCHEMA_MODE``.

    Read at call time, not frozen at import like ``DB_DSN``: the escape hatch
    is a ``gcloud run services update --update-env-vars`` away, and tests set
    it per case.

    An unrecognized value resolves to ``create`` — the historic behaviour — and
    logs an error. That direction is deliberate: a typo is most likely made by
    an operator reaching for the escape hatch mid-incident, and failing closed
    there would deny them the very thing they were reaching for.

    **The escape hatch.** ``auto`` enforces on a Flyway-managed database, so a
    missing migration now fails a deploy. To get the old create-on-startup
    behaviour back without a code change::

        gcloud run services update quantcore-api --project <project> \\
          --region us-central1 --update-env-vars QUANTCORE_SCHEMA_MODE=create

    ``--update-env-vars``, never ``--set-env-vars``: the latter replaces the
    whole set and has taken prod down before.
    """
    requested = (os.getenv("QUANTCORE_SCHEMA_MODE") or "auto").strip().lower()
    if requested not in SCHEMA_MODES:
        logger.error(
            "schema check: QUANTCORE_SCHEMA_MODE=%r is not one of %s "
            "— falling back to create",
            requested, ", ".join(SCHEMA_MODES),
        )
        return requested, "create"
    if requested != "auto":
        return requested, requested
    # A database with a Flyway ledger has an owner for its DDL already; one
    # without (local dev, CI, compose, a brand-new instance) does not. This
    # resolved to `warn` for one deploy cycle on both projects (decision D5);
    # both reported missing=0 mismatch=0, so it now enforces.
    return "auto", "verify" if _flyway_managed(dsn) else "create"


def _check_schema(dsn: str, *, requested: str, resolved: str) -> None:
    """Diff the live schema against ``db/schema_snapshot.json``, run no DDL.

    Emits one greppable summary line plus one line per difference. Nothing
    logged here contains the DSN — the never-log policy in ``keyproxy/``
    applies to every log line in this repo, and a DSN carries a password.
    """
    if not SCHEMA_SNAPSHOT.exists():
        message = f"schema check: snapshot {SCHEMA_SNAPSHOT.name} is missing; cannot verify"
        if resolved == "verify":
            raise SchemaDriftError(message)
        logger.error(message)
        return

    expected = json.loads(SCHEMA_SNAPSHOT.read_text())
    conn = psycopg2.connect(dsn)
    try:
        actual = describe_schema(conn)
    finally:
        conn.close()

    differences = diff_schemas(expected, actual)
    missing = [d for d in differences if d.startswith("MISSING")]
    mismatch = [d for d in differences if d.startswith("MISMATCH")]
    extra = [d for d in differences if d.startswith("EXTRA")]

    logger.info(
        "schema check: mode=%s resolved=%s tables=%d missing=%d mismatch=%d extra=%d",
        requested, resolved, len(actual.get("tables", {})),
        len(missing), len(mismatch), len(extra),
    )
    for line in differences:
        logger.warning("schema check: %s", line)

    if resolved == "verify" and (missing or mismatch):
        raise SchemaDriftError(
            "live schema does not match db/schema_snapshot.json:\n  "
            + "\n  ".join(missing + mismatch)
            + "\n\nEXTRA objects are not counted. Resolve under decision D6 "
              "(prod reality wins) in docs/proposals/schema-ownership-plan.md: "
              "add a NEW db/migrations/V*.sql, never edit an applied one."
        )


def ensure_schema(dsn: str = None) -> None:
    """Make sure the schema is right, at most once per process, per DSN.

    "Right" depends on ``QUANTCORE_SCHEMA_MODE`` (see
    :func:`_resolve_schema_mode`): on a database Flyway already manages this
    *checks* and raises :class:`SchemaDriftError` on drift rather than papering
    over it with DDL; on one nobody manages it still creates. Raising here
    aborts startup, which is the point — a Cloud Run revision that fails its
    startup check never takes traffic, so the previous revision keeps serving
    instead of the mismatch surfacing as query errors later.

    Callers that just need the tables to exist should use this rather than
    ``init_schema()``: the DDL takes ~3s against a managed instance and locks
    ~20 tables while it runs, so re-running it on every application factory
    call or repository construction is both slow and a source of lock
    contention with concurrent writers. On a deployed database the check path
    replaces that entirely with read-only catalog queries (three per table,
    so ~70 today) that take no locks at all — which is also why
    the AB-BA deadlock documented on :func:`init_schema` stops being reachable
    in prod once ``auto`` resolves to a non-creating mode there.
    """
    target_dsn = dsn or DB_DSN
    if target_dsn in _schema_ready_dsns:
        return
    with _schema_lock:
        if target_dsn in _schema_ready_dsns:
            return
        requested, resolved = _resolve_schema_mode(target_dsn)
        if resolved == "create":
            init_schema(target_dsn)
        else:
            # Raises in verify mode, and the DSN stays unrecorded below, so a
            # caller that catches and retries gets a real second check.
            _check_schema(target_dsn, requested=requested, resolved=resolved)
        # init_schema() records it too; recorded here as well so the
        # once-per-process gate holds however init_schema is reached.
        _schema_ready_dsns.add(target_dsn)


def describe_dsn(dsn: str = None) -> str:
    """Return a non-secret identifier for a DSN: ``host:port/database``.

    A DSN carries the password, so it must never reach a log line or an API
    response (the never-log policy in CLAUDE.md). This is what to name a
    database with instead — enough to tell test from prod, nothing more.

    Anything that isn't a recognizable ``postgresql://`` URL returns
    ``"unknown"`` rather than being echoed back: the libpq keyword form
    (``host=... password=...``) would otherwise leak the very thing this
    exists to strip.
    """
    try:
        parsed = urlparse(dsn or DB_DSN)
        if parsed.scheme not in ("postgresql", "postgres"):
            return "unknown"
        host, port = parsed.hostname, parsed.port
    except ValueError:
        # A malformed netloc (e.g. a non-numeric port) — say nothing rather
        # than fall back to any part of the raw string.
        return "unknown"
    database = (parsed.path or "").lstrip("/")
    if not host and not database:
        return "unknown"
    where = host or "unknown-host"
    if port:
        where = f"{where}:{port}"
    return f"{where}/{database}" if database else where


def get_connection() -> _PGConn:
    """Get a connection to the QuantCore PostgreSQL database."""
    ensure_schema()
    return _PGConn(psycopg2.connect(DB_DSN))


def _split_schema(schema: str) -> list[str]:
    stmts = [s.strip() for s in schema.split(';')]
    return [s + ';' for s in stmts if s]


def init_schema(dsn: str = None) -> None:
    """Initialize the database schema if tables don't exist.

    Each statement commits on its own. The DDL is idempotent
    (``CREATE ... IF NOT EXISTS`` / ``ADD COLUMN IF NOT EXISTS``), so
    per-statement commit converges to the same schema, and it keeps the DDL
    from holding locks on every table it has touched until one final commit.
    That accumulation is what let a concurrent writer form an AB-BA deadlock:
    the DDL holds ``symbols`` (statement 1) while reaching for
    ``plan_instances`` (statement 34), while a writer holds ``plan_instances``
    and reaches for ``symbols`` through its foreign key.

    ``lock_timeout`` turns a wait behind a live writer into a loud, diagnosable
    error instead of an indefinite hang.
    """
    target_dsn = dsn or DB_DSN
    conn = psycopg2.connect(target_dsn)
    try:
        conn.set_session(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '10s'")
            for stmt in _split_schema(_SCHEMA):
                cur.execute(stmt)
    finally:
        conn.close()
    with _schema_lock:
        _schema_ready_dsns.add(target_dsn)
