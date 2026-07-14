import os
import re
import threading

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Load .env from the project root so every entry point (main.py, REST API,
# MCP servers) resolves QUANTCORE_DB_DSN consistently. Existing environment
# variables are not overridden.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DB_DSN = os.getenv(
    "QUANTCORE_DB_DSN",
    "postgresql://quantcore:changeme@localhost:5432/quantcore",
)

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

CREATE TABLE IF NOT EXISTS plan_instances (
    instance_id SERIAL PRIMARY KEY,
    template_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL,
    position_id INTEGER,
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

CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_plan_per_symbol
    ON plan_instances(symbol_id) WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_instances_symbol_status
    ON plan_instances(symbol_id, status);

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
# Entry points may still call init_schema() explicitly; it sets the flag too,
# so the DDL is not re-run on the first connection afterwards. The lock keeps
# concurrent first connections (threaded Flask / MCP servers) from running the
# DDL in parallel — PostgreSQL's CREATE ... IF NOT EXISTS can fail under
# concurrent execution.
_schema_ready = False
_schema_lock = threading.Lock()


def get_connection() -> _PGConn:
    """Get a connection to the QuantCore PostgreSQL database."""
    if not _schema_ready:
        with _schema_lock:
            if not _schema_ready:
                init_schema()
    return _PGConn(psycopg2.connect(DB_DSN))


def _split_schema(schema: str) -> list[str]:
    stmts = [s.strip() for s in schema.split(';')]
    return [s + ';' for s in stmts if s]


def init_schema(dsn: str = None) -> None:
    """Initialize the database schema if tables don't exist."""
    global _schema_ready
    target_dsn = dsn or DB_DSN
    conn = psycopg2.connect(target_dsn)
    try:
        with conn.cursor() as cur:
            for stmt in _split_schema(_SCHEMA):
                cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    if target_dsn == DB_DSN:
        _schema_ready = True
