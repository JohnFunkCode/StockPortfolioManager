import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv(
    "QUANTCORE_DB_PATH",
    Path(__file__).parent.parent / "data" / "quantcore.sqlite"
))

_SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS symbols (
    symbol_id INTEGER PRIMARY KEY,
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
    template_id INTEGER PRIMARY KEY,
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
    position_id INTEGER PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS plan_instances (
    instance_id INTEGER PRIMARY KEY,
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
    rung_id INTEGER PRIMARY KEY,
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
    alert_id INTEGER PRIMARY KEY,
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
    snapshot_id INTEGER PRIMARY KEY,
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
    expiration_id INTEGER PRIMARY KEY,
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
    contract_id INTEGER PRIMARY KEY,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    article_id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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


def get_connection() -> sqlite3.Connection:
    """Get a connection to the QuantCore database with standard settings."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_schema() -> None:
    """Initialize the database schema if it doesn't exist."""
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
