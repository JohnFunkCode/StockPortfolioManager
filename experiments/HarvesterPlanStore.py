import sqlite3
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf

# Import your plan builder from HarvesterExperiment.py
# Assumes HarvesterExperiment.py is in the same folder or on PYTHONPATH.
from experiments.HarvesterExperiment import design_forward_ladder_from_history


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -----------------------------
# SQLite schema (OHLCV + adj_close + plans + rungs + alerts)
# -----------------------------

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS symbols (
  symbol_id     INTEGER PRIMARY KEY,
  ticker        TEXT NOT NULL UNIQUE,
  name          TEXT,
  exchange      TEXT,
  currency      TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_bars_daily (
  symbol_id     INTEGER NOT NULL,
  bar_date      TEXT NOT NULL,
  open          REAL,
  high          REAL,
  low           REAL,
  close         REAL NOT NULL,
  adj_close     REAL NOT NULL,
  volume        REAL,
  data_vendor   TEXT NOT NULL DEFAULT 'yfinance',
  ingested_at   TEXT NOT NULL,
  PRIMARY KEY (symbol_id, bar_date),
  FOREIGN KEY (symbol_id) REFERENCES symbols(symbol_id) ON DELETE CASCADE,
  CHECK (close > 0),
  CHECK (adj_close > 0),
  CHECK (volume IS NULL OR volume >= 0)
);

CREATE INDEX IF NOT EXISTS idx_price_bars_symbol_date ON price_bars_daily(symbol_id, bar_date);

CREATE TABLE IF NOT EXISTS plan_templates (
  template_id         INTEGER PRIMARY KEY,
  name                TEXT NOT NULL,
  is_dynamic_h         INTEGER NOT NULL,
  history_window_days  INTEGER NOT NULL,
  n_iterations         INTEGER NOT NULL,
  alpha               REAL,
  min_h               REAL,
  max_h               REAL,
  fixed_h             REAL,
  drift_method        TEXT NOT NULL DEFAULT 'CAGR',
  vol_method          TEXT NOT NULL DEFAULT 'LOGRET_STD',
  stats_price_series  TEXT NOT NULL DEFAULT 'adj_close',
  created_at          TEXT NOT NULL,
  notes               TEXT,
  metadata_json       TEXT
);

CREATE TABLE IF NOT EXISTS positions (
  position_id      INTEGER PRIMARY KEY,
  symbol_id        INTEGER NOT NULL,
  opened_at        TEXT NOT NULL,
  entry_price      REAL NOT NULL,
  shares           INTEGER NOT NULL,
  cost_basis_total REAL NOT NULL,
  account          TEXT,
  notes            TEXT,
  FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
  CHECK (shares > 0),
  CHECK (entry_price > 0),
  CHECK (cost_basis_total >= 0)
);

CREATE TABLE IF NOT EXISTS plan_instances (
  instance_id         INTEGER PRIMARY KEY,
  template_id         INTEGER NOT NULL,
  symbol_id           INTEGER NOT NULL,
  position_id         INTEGER,
  status              TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at          TEXT NOT NULL,
  asof_date           TEXT NOT NULL,
  price_asof          REAL NOT NULL,
  shares_initial      INTEGER NOT NULL,
  v0_floor            REAL NOT NULL,
  capital_at_risk     REAL NOT NULL,
  history_end_date    TEXT NOT NULL,
  history_window_days INTEGER NOT NULL,
  r_daily             REAL NOT NULL,
  annual_vol          REAL NOT NULL,
  h_threshold         REAL NOT NULL,
  n_iterations        INTEGER NOT NULL,
  stats_price_series  TEXT NOT NULL DEFAULT 'adj_close',
  supersedes_instance_id INTEGER,
  notes               TEXT,
  metadata_json       TEXT,
  FOREIGN KEY(template_id) REFERENCES plan_templates(template_id),
  FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
  FOREIGN KEY(position_id) REFERENCES positions(position_id),
  FOREIGN KEY(supersedes_instance_id) REFERENCES plan_instances(instance_id),
  CHECK (shares_initial > 0),
  CHECK (price_asof > 0),
  CHECK (v0_floor > 0),
  CHECK (capital_at_risk > 0),
  CHECK (h_threshold > 0),
  CHECK (n_iterations > 0),
  CHECK (annual_vol >= 0)
);

CREATE INDEX IF NOT EXISTS idx_instances_symbol_status ON plan_instances(symbol_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_plan_per_symbol
ON plan_instances(symbol_id)
WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS plan_rungs (
  rung_id                 INTEGER PRIMARY KEY,
  instance_id             INTEGER NOT NULL,
  rung_index              INTEGER NOT NULL,
  target_price            REAL NOT NULL,
  shares_before           INTEGER NOT NULL,
  shares_sold_planned     INTEGER NOT NULL,
  shares_after_planned    INTEGER NOT NULL,
  expected_days_from_now  REAL,
  expected_date           TEXT,
  gross_harvest_planned       REAL NOT NULL,
  cumulative_harvest_planned  REAL NOT NULL,
  remaining_value_planned     REAL NOT NULL,
  total_wealth_planned        REAL NOT NULL,
  total_return_planned        REAL NOT NULL,
  status                 TEXT NOT NULL DEFAULT 'PENDING',
  triggered_at           TEXT,
  trigger_price          REAL,
  executed_at            TEXT,
  executed_price         REAL,
  shares_sold_actual     INTEGER,
  gross_harvest_actual   REAL,
  tax_paid_actual        REAL,
  net_harvest_actual     REAL,
  notes                  TEXT,
  UNIQUE(instance_id, rung_index),
  FOREIGN KEY(instance_id) REFERENCES plan_instances(instance_id) ON DELETE CASCADE,
  CHECK (target_price > 0),
  CHECK (shares_before > 0),
  CHECK (shares_sold_planned > 0),
  CHECK (shares_after_planned >= 0)
);

CREATE INDEX IF NOT EXISTS idx_rungs_instance_status ON plan_rungs(instance_id, status);

CREATE TABLE IF NOT EXISTS alerts (
  alert_id          INTEGER PRIMARY KEY,
  rung_id           INTEGER NOT NULL,
  symbol_id         INTEGER NOT NULL,
  instance_id       INTEGER NOT NULL,
  alert_type        TEXT NOT NULL DEFAULT 'PRICE_GE',
  threshold_price   REAL NOT NULL,
  status            TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at        TEXT NOT NULL,
  last_checked_at   TEXT,
  fired_at          TEXT,
  fired_price       REAL,
  cooldown_seconds  INTEGER,
  channel           TEXT,
  destination       TEXT,
  message_template  TEXT,
  FOREIGN KEY(rung_id) REFERENCES plan_rungs(rung_id) ON DELETE CASCADE,
  FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
  FOREIGN KEY(instance_id) REFERENCES plan_instances(instance_id) ON DELETE CASCADE,
  CHECK (threshold_price > 0)
);

CREATE INDEX IF NOT EXISTS idx_alerts_symbol_status ON alerts(symbol_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS ux_alerts_one_per_rung
ON alerts(rung_id);
"""


# -----------------------------
# SQL snippets (queries)
# -----------------------------

SQL_INSERT_SYMBOL = """
INSERT INTO symbols (ticker, created_at)
VALUES (:ticker, :created_at)
ON CONFLICT(ticker) DO NOTHING;
"""

SQL_GET_SYMBOL_ID = """
SELECT symbol_id FROM symbols WHERE ticker = :ticker;
"""

SQL_UPSERT_DAILY_BAR = """
INSERT INTO price_bars_daily (
  symbol_id, bar_date,
  open, high, low, close, adj_close, volume,
  data_vendor, ingested_at
) VALUES (
  :symbol_id, :bar_date,
  :open, :high, :low, :close, :adj_close, :volume,
  :data_vendor, :ingested_at
)
ON CONFLICT(symbol_id, bar_date) DO UPDATE SET
  open        = excluded.open,
  high        = excluded.high,
  low         = excluded.low,
  close       = excluded.close,
  adj_close   = excluded.adj_close,
  volume      = excluded.volume,
  data_vendor = excluded.data_vendor,
  ingested_at = excluded.ingested_at;
"""

SQL_GET_ACTIVE_INSTANCE_FOR_TICKER = """
SELECT pi.*
FROM plan_instances pi
JOIN symbols s ON s.symbol_id = pi.symbol_id
WHERE s.ticker = :ticker AND pi.status = 'ACTIVE'
ORDER BY pi.created_at DESC
LIMIT 1;
"""

SQL_GET_NEXT_PENDING_RUNG = """
SELECT pr.rung_id, pr.instance_id, pr.rung_index, pr.target_price,
       pr.shares_sold_planned, pi.symbol_id
FROM plan_rungs pr
JOIN plan_instances pi ON pi.instance_id = pr.instance_id
WHERE pi.instance_id = :instance_id
  AND pi.status = 'ACTIVE'
  AND pr.status = 'PENDING'
ORDER BY pr.rung_index ASC
LIMIT 1;
"""

SQL_UPSERT_ALERT_FOR_RUNG = """
INSERT INTO alerts (
  rung_id, symbol_id, instance_id,
  alert_type, threshold_price,
  status, created_at
) VALUES (
  :rung_id, :symbol_id, :instance_id,
  'PRICE_GE', :threshold_price,
  'ACTIVE', :created_at
)
ON CONFLICT(rung_id) DO UPDATE SET
  threshold_price = excluded.threshold_price,
  status = 'ACTIVE',
  alert_type = excluded.alert_type;
"""

SQL_DISABLE_OTHER_ALERTS_FOR_INSTANCE = """
UPDATE alerts
SET status = 'DISABLED'
WHERE instance_id = :instance_id
  AND status = 'ACTIVE'
  AND rung_id <> :rung_id;
"""

SQL_MARK_ALERT_FIRED = """
UPDATE alerts
SET status = 'FIRED',
    fired_at = :ts,
    fired_price = :price,
    last_checked_at = :ts
WHERE alert_id = :alert_id
  AND status = 'ACTIVE';
"""

SQL_MARK_RUNG_TRIGGERED = """
UPDATE plan_rungs
SET status = 'TRIGGERED',
    triggered_at = :ts,
    trigger_price = :price
WHERE rung_id = :rung_id
  AND status = 'PENDING';
"""

SQL_GET_ACTIVE_ALERT_FOR_RUNG = """
SELECT alert_id
FROM alerts
WHERE rung_id = :rung_id
  AND status = 'ACTIVE'
LIMIT 1;
"""

SQL_MARK_RUNG_EXECUTED = """
UPDATE plan_rungs
SET status = 'EXECUTED',
    executed_at = :ts,
    executed_price = :price,
    shares_sold_actual = :shares_sold,
    tax_paid_actual = :tax_paid,
    net_harvest_actual = :net_harvest
WHERE rung_id = :rung_id
  AND status IN ('TRIGGERED', 'PENDING');
"""

SQL_GET_RUNG_INSTANCE = """
SELECT instance_id
FROM plan_rungs
WHERE rung_id = :rung_id
LIMIT 1;
"""

SQL_LIST_PLANS = """
SELECT
  pi.instance_id,
  s.ticker AS symbol,
  pi.status,
  pi.created_at,
  pi.asof_date,
  pi.price_asof,
  pi.shares_initial,
  pi.v0_floor,
  pi.h_threshold,
  pi.n_iterations,
  pi.annual_vol,
  pi.r_daily
FROM plan_instances pi
JOIN symbols s ON s.symbol_id = pi.symbol_id
ORDER BY pi.created_at DESC;
"""

SQL_LIST_ACTIVE_PLANS = """
SELECT
  pi.instance_id,
  s.symbol_id,
  s.ticker,
  pi.price_asof,
  pi.h_threshold,
  pi.n_iterations
FROM plan_instances pi
JOIN symbols s ON s.symbol_id = pi.symbol_id
WHERE pi.status = 'ACTIVE';
"""

SQL_PURGE_SUPERSEDED_PLANS = """
DELETE FROM plan_instances
WHERE status = 'SUPERSEDED';
"""

# -----------------------------
# Data ingestion (OHLCV + adj_close)
# -----------------------------

def fetch_daily_history_ohlcv(symbol: str, days: int = 400) -> pd.DataFrame:
    """
    Fetch daily bars with yfinance: Open/High/Low/Close/Adj Close/Volume.

    Notes:
    - yfinance changed the default for auto_adjust; we set auto_adjust=False to reliably
      receive an 'Adj Close' column.
    - If 'Adj Close' is still unavailable for a symbol, we fall back to using 'Close'.
    """
    df = yf.download(
        symbol,
        period=f"{days}d",
        interval="1d",
        progress=False,
        auto_adjust=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    # Normalize MultiIndex (can happen for some yfinance outputs)
    if isinstance(df.columns, pd.MultiIndex):
        # Try common layouts. Prefer selecting the requested symbol if present.
        lvl0 = df.columns.get_level_values(0)
        lvl1 = df.columns.get_level_values(1)

        if symbol in lvl0:
            # symbol-first layout: (SYMBOL, Field)
            df = df.xs(symbol, axis=1, level=0, drop_level=True)
        elif symbol in lvl1:
            # field-first layout: (Field, SYMBOL)
            df = df.xs(symbol, axis=1, level=1, drop_level=True)
        else:
            # Fallback: take the first column block
            df = df.droplevel(0, axis=1)

    # Ensure we have the needed columns.
    # With auto_adjust=False, yfinance typically provides: Open, High, Low, Close, Adj Close, Volume
    # But some tickers/markets may omit Adj Close; if so, use Close.
    if "Adj Close" not in df.columns and "Close" in df.columns:
        df["Adj Close"] = df["Close"]

    required = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return pd.DataFrame()

    df = df[required].dropna(subset=["Close", "Adj Close"])  # keep bars with prices

    # Index is DatetimeIndex; convert to YYYY-MM-DD
    df = df.reset_index()
    # yfinance uses 'Date' for daily
    if "Date" in df.columns:
        df.rename(columns={"Date": "bar_date"}, inplace=True)
    elif "Datetime" in df.columns:
        df.rename(columns={"Datetime": "bar_date"}, inplace=True)
    else:
        # Unknown index name; assume first column is the date
        df.rename(columns={df.columns[0]: "bar_date"}, inplace=True)

    df["bar_date"] = pd.to_datetime(df["bar_date"]).dt.strftime("%Y-%m-%d")
    return df


# -----------------------------
# Store + query manager
# -----------------------------

@dataclass
class PlanBuildParams:
    history_window_days: int = 360
    n_iterations: int = 4
    alpha: float = 0.5
    min_H: float = 0.05
    max_H: float = 0.30
    max_s0: int = 1000


class HarvesterPlanDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    # -------------------------
    # Public API methods
    # -------------------------

    def build_plan(self, symbol: str, template_name: str, params: PlanBuildParams) -> Dict[str, Any]:
        """
        Build a forward plan for `symbol` using the existing planner from HarvesterExperiment.py,
        persist it to SQLite, and return a summary.

        This will:
        - upsert symbol
        - fetch and upsert OHLCV+adj_close history
        - compute the plan from the most recent 360 adj_close prices (or close if you change it)
        - insert a plan_template (if missing)
        - insert a plan_instance
        - insert plan_rungs
        - create/refresh an alert for the next pending rung
        """
        symbol = symbol.upper().strip()
        now = _utc_now_iso()

        # 1) Ensure symbol row and get symbol_id
        with self._connect() as conn:
            conn.execute(SQL_INSERT_SYMBOL, {"ticker": symbol, "created_at": now})
            row = conn.execute(SQL_GET_SYMBOL_ID, {"ticker": symbol}).fetchone()
            if not row:
                raise RuntimeError(f"Failed to resolve symbol_id for {symbol}")
            symbol_id = int(row["symbol_id"])

        # 2) Fetch bars and upsert into price_bars_daily
        bars = fetch_daily_history_ohlcv(symbol, days=max(params.history_window_days + 60, 420))
        if bars.empty:
            raise RuntimeError(f"No price history returned for {symbol}")

        with self._connect() as conn:
            conn.execute("BEGIN;")
            try:
                for _, r in bars.iterrows():
                    conn.execute(SQL_UPSERT_DAILY_BAR, {
                        "symbol_id": symbol_id,
                        "bar_date": r["bar_date"],
                        "open": float(r["Open"]) if pd.notna(r["Open"]) else None,
                        "high": float(r["High"]) if pd.notna(r["High"]) else None,
                        "low": float(r["Low"]) if pd.notna(r["Low"]) else None,
                        "close": float(r["Close"]),
                        "adj_close": float(r["Adj Close"]),
                        "volume": float(r["Volume"]) if pd.notna(r["Volume"]) else None,
                        "data_vendor": "yfinance",
                        "ingested_at": now,
                    })
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        # 3) Pull last N adj_close prices from the local DB (deterministic)
        with self._connect() as conn:
            prices_rows = conn.execute(
                """
                SELECT bar_date, adj_close, close
                FROM price_bars_daily
                WHERE symbol_id = ?
                ORDER BY bar_date DESC
                LIMIT ?;
                """,
                (symbol_id, params.history_window_days),
            ).fetchall()

        if len(prices_rows) < 2:
            raise RuntimeError(f"Not enough stored history to build a plan for {symbol}")

        # Reverse to chronological order
        prices_rows = list(reversed(prices_rows))
        prices_adj = [float(r["adj_close"]) for r in prices_rows]
        prices_close = [float(r["close"]) for r in prices_rows]

        history_end_date = prices_rows[-1]["bar_date"]
        # Use last close as "price_asof" for simplicity. Replace with live quote if you prefer.
        price_asof = prices_close[-1]

        # 4) Build forward ladder (dynamic H)
        forward_plan = design_forward_ladder_from_history(
            prices_adj,
            H=None,
            n_iterations=params.n_iterations,
            max_s0=params.max_s0,
            alpha=params.alpha,
            min_H=params.min_H,
            max_H=params.max_H,
        )
        if not forward_plan:
            raise RuntimeError(f"No feasible forward ladder found for {symbol}")

        # 5) Create/find template
        with self._connect() as conn:
            tmpl = conn.execute(
                """
                SELECT template_id FROM plan_templates
                WHERE name = :name
                LIMIT 1;
                """,
                {"name": template_name},
            ).fetchone()

            if tmpl:
                template_id = int(tmpl["template_id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO plan_templates (
                      name, is_dynamic_h, history_window_days, n_iterations,
                      alpha, min_h, max_h, fixed_h,
                      drift_method, vol_method, stats_price_series,
                      created_at
                    ) VALUES (
                      :name, 1, :history_window_days, :n_iterations,
                      :alpha, :min_h, :max_h, NULL,
                      'CAGR', 'LOGRET_STD', 'adj_close',
                      :created_at
                    );
                    """,
                    {
                        "name": template_name,
                        "history_window_days": params.history_window_days,
                        "n_iterations": params.n_iterations,
                        "alpha": params.alpha,
                        "min_h": params.min_H,
                        "max_h": params.max_H,
                        "created_at": now,
                    },
                )
                template_id = cur.lastrowid

        # 6) Insert plan_instance (supersede any active plan for that symbol)
        with self._connect() as conn:
            conn.execute("BEGIN;")
            try:
                prev = conn.execute(
                    "SELECT instance_id FROM plan_instances WHERE symbol_id=? AND status='ACTIVE' ORDER BY created_at DESC LIMIT 1;",
                    (symbol_id,),
                ).fetchone()
                supersedes = int(prev["instance_id"]) if prev else None
                if prev:
                    conn.execute(
                        "UPDATE plan_instances SET status='SUPERSEDED' WHERE instance_id=?;",
                        (supersedes,),
                    )

                shares_initial = int(forward_plan["s0"])
                v0_floor = float(forward_plan["V0"])

                cur = conn.execute(
                    """
                    INSERT INTO plan_instances (
                      template_id, symbol_id, position_id, status,
                      created_at, asof_date,
                      price_asof,
                      shares_initial, v0_floor, capital_at_risk,
                      history_end_date, history_window_days,
                      r_daily, annual_vol, h_threshold, n_iterations,
                      stats_price_series,
                      supersedes_instance_id
                    ) VALUES (
                      :template_id, :symbol_id, NULL, 'ACTIVE',
                      :created_at, :asof_date,
                      :price_asof,
                      :shares_initial, :v0_floor, :capital_at_risk,
                      :history_end_date, :history_window_days,
                      :r_daily, :annual_vol, :h_threshold, :n_iterations,
                      'adj_close',
                      :supersedes_instance_id
                    );
                    """,
                    {
                        "template_id": template_id,
                        "symbol_id": symbol_id,
                        "created_at": now,
                        "asof_date": now,
                        "price_asof": price_asof,
                        "shares_initial": shares_initial,
                        "v0_floor": v0_floor,
                        "capital_at_risk": v0_floor,
                        "history_end_date": history_end_date,
                        "history_window_days": params.history_window_days,
                        "r_daily": float(forward_plan["r_daily"]),
                        "annual_vol": float(forward_plan["annual_vol"]),
                        "h_threshold": float(forward_plan["H"]),
                        "n_iterations": int(forward_plan["n_iterations"]),
                        "supersedes_instance_id": supersedes,
                    },
                )
                instance_id = int(cur.lastrowid)

                # Insert rungs
                for rung in forward_plan["ladder"]:
                    expected_days = rung["expected_days_from_now"]
                    expected_date = None
                    if expected_days is not None:
                        expected_dt = datetime.fromisoformat(now)
                        if expected_dt.tzinfo is None:
                            expected_dt = expected_dt.replace(tzinfo=timezone.utc)
                        expected_date = (expected_dt + timedelta(days=float(expected_days))).date().isoformat()
                    conn.execute(
                        """
                        INSERT INTO plan_rungs (
                          instance_id, rung_index,
                          target_price,
                          shares_before, shares_sold_planned, shares_after_planned,
                          expected_days_from_now, expected_date,
                          gross_harvest_planned, cumulative_harvest_planned,
                          remaining_value_planned, total_wealth_planned, total_return_planned,
                          status
                        ) VALUES (
                          :instance_id, :rung_index,
                          :target_price,
                          :shares_before, :shares_sold_planned, :shares_after_planned,
                          :expected_days_from_now, NULL,
                          :gross_harvest_planned, :cumulative_harvest_planned,
                          :remaining_value_planned, :total_wealth_planned, :total_return_planned,
                          'PENDING'
                        );
                        """,
                        {
                            "instance_id": instance_id,
                            "rung_index": int(rung["harvest"]),
                            "target_price": float(rung["price_target"]),
                            "shares_before": int(rung["shares_before"]),
                            "shares_sold_planned": int(rung["shares_sold"]),
                            "shares_after_planned": int(rung["shares_after"]),
                            "expected_days_from_now": float(expected_days) if expected_days is not None else None,
                            "expected_date": expected_date,
                            "gross_harvest_planned": float(rung["gross_harvest"]),
                            "cumulative_harvest_planned": float(rung["cumulative_harvest"]),
                            "remaining_value_planned": float(rung["remaining_value"]),
                            "total_wealth_planned": float(rung["total_wealth"]),
                            "total_return_planned": float(rung["total_return_vs_initial"]),
                        }
                    )

                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        # 7) Create/refresh alert for the next pending rung only
        self._ensure_next_rung_alert(instance_id)

        return {
            "symbol": symbol,
            "instance_id": instance_id,
            "template_id": template_id,
            "history_end_date": history_end_date,
            "price_asof": price_asof,
            "shares_initial": shares_initial,
            "H": float(forward_plan["H"]),
            "n_iterations": int(forward_plan["n_iterations"]),
        }

    def display_all_plans(self, status: str = "ACTIVE") -> List[Dict[str, Any]]:
        """
        Return plan instances by status (most recent first).
        status: 'ACTIVE' (default), 'SUPERSEDED', or 'ALL'
        """
        status = status.upper()
        if status not in {"ACTIVE", "SUPERSEDED", "ALL"}:
            raise ValueError("status must be 'ACTIVE', 'SUPERSEDED', or 'ALL'")

        sql = SQL_LIST_PLANS
        params: tuple = ()
        if status != "ALL":
            sql = SQL_LIST_PLANS.replace(
                "ORDER BY pi.created_at DESC;",
                "WHERE pi.status = ? ORDER BY pi.created_at DESC;",
            )
            params = (status,)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def symbols_at_harvest_points(self) -> List[Dict[str, Any]]:
        """
        Poll current prices and return a list of:
          { "symbol": <ticker>, "shares_to_sell": <int> }
        for ACTIVE plans whose NEXT pending rung has been reached/exceeded.

        Notes:
        - Uses yfinance to fetch the most recent close (period=2d, interval=1d).
        - If you want true real-time, swap this for a live quote source.
        """
        results: List[Dict[str, Any]] = []
        now = _utc_now_iso()

        with self._connect() as conn:
            active = conn.execute(SQL_LIST_ACTIVE_PLANS).fetchall()

        for row in active:
            instance_id = int(row["instance_id"])
            symbol_id = int(row["symbol_id"])
            ticker = row["ticker"]

            next_rung = None
            with self._connect() as conn:
                next_rung = conn.execute(SQL_GET_NEXT_PENDING_RUNG, {"instance_id": instance_id}).fetchone()

            if not next_rung:
                continue

            target_price = float(next_rung["target_price"])
            shares_to_sell = int(next_rung["shares_sold_planned"])
            rung_id = int(next_rung["rung_id"])

            # Poll current price (use last close)
            current_price = self._poll_latest_close(ticker)
            if current_price is None:
                continue

            if current_price >= target_price:
                results.append({
                    "symbol": ticker,
                    "shares_to_sell": shares_to_sell,
                    # optional extras (handy for logging/debug)
                    "current_price": current_price,
                    "target_price": target_price,
                    "instance_id": instance_id,
                    "rung_id": rung_id,
                })

                # Optional: mark alert/rung as triggered automatically here
                # or leave that to a separate "fire alerts" path.
        return results

    def purge_superseded_plans(
        self,
        *,
        symbol: Optional[str] = None,
        older_than_days: Optional[int] = None,
        dry_run: bool = False,
        return_ids: bool = False,
    ) -> Any:
        """
        Delete SUPERSEDED plan instances and all related artifacts via cascades.
        Options:
          - symbol: only purge plans for this ticker
          - older_than_days: only purge plans created at or before now - N days
          - dry_run: if True, return the count without deleting
          - return_ids: if True, return (count, [instance_id, ...]) instead of count
        Returns the number of plan instances matched (or removed if not dry_run),
        or a dict with count and instance_ids if return_ids is True.
        """
        conditions = ["status = 'SUPERSEDED'"]
        params: List[Any] = []

        if symbol:
            conditions.append(
                "symbol_id = (SELECT symbol_id FROM symbols WHERE ticker = ?)"
            )
            params.append(symbol.upper().strip())

        if older_than_days is not None:
            if older_than_days < 0:
                raise ValueError("older_than_days must be >= 0")
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            conditions.append("created_at <= ?")
            params.append(cutoff.isoformat())

        where_clause = " AND ".join(conditions)
        count_sql = f"SELECT COUNT(*) AS cnt FROM plan_instances WHERE {where_clause};"
        ids_sql = f"SELECT instance_id FROM plan_instances WHERE {where_clause} ORDER BY created_at DESC;"
        delete_sql = f"DELETE FROM plan_instances WHERE {where_clause};"

        with self._connect() as conn:
            row = conn.execute(count_sql, params).fetchone()
            matched = int(row["cnt"]) if row else 0
            ids: List[int] = []
            if return_ids and matched > 0:
                rows = conn.execute(ids_sql, params).fetchall()
                ids = [int(r["instance_id"]) for r in rows]
            if dry_run or matched == 0:
                return {"count": matched, "instance_ids": ids} if return_ids else matched

            conn.execute("BEGIN;")
            try:
                conn.execute(delete_sql, params)
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
        return {"count": matched, "instance_ids": ids} if return_ids else matched

    # -------------------------
    # Internal helpers
    # -------------------------

    def _poll_latest_close(self, ticker: str) -> Optional[float]:
        try:
            df = yf.download(ticker, period="5d", interval="1d", progress=False)
            if df is None or df.empty:
                return None
            # Handle MultiIndex similarly
            if isinstance(df.columns, pd.MultiIndex):
                if "Close" in df.columns.get_level_values(0):
                    if ticker in df.columns.get_level_values(1):
                        df = df.xs(ticker, axis=1, level=1, drop_level=True)
                else:
                    df = df.xs(ticker, axis=1, level=0, drop_level=True)

            if "Close" not in df.columns:
                return None
            close = df["Close"].dropna().iloc[-1]
            return float(close)
        except Exception:
            return None

    def _ensure_next_rung_alert(self, instance_id: int) -> None:
        """
        Create/refresh an alert for the next pending rung and disable others for the instance.
        """
        now = _utc_now_iso()
        with self._connect() as conn:
            rung = conn.execute(SQL_GET_NEXT_PENDING_RUNG, {"instance_id": instance_id}).fetchone()
            if not rung:
                return

            rung_id = int(rung["rung_id"])
            symbol_id = int(rung["symbol_id"])
            threshold_price = float(rung["target_price"])

            conn.execute("BEGIN;")
            try:
                conn.execute(SQL_UPSERT_ALERT_FOR_RUNG, {
                    "rung_id": rung_id,
                    "symbol_id": symbol_id,
                    "instance_id": instance_id,
                    "threshold_price": threshold_price,
                    "created_at": now,
                })
                conn.execute(SQL_DISABLE_OTHER_ALERTS_FOR_INSTANCE, {
                    "instance_id": instance_id,
                    "rung_id": rung_id,
                })
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise


class HarvesterController:
    def __init__(self, db: HarvesterPlanDB):
        self.db = db

    def get_next_actions(self) -> List[Dict[str, Any]]:
        """
        Return the next pending rung for each ACTIVE plan.
        """
        actions: List[Dict[str, Any]] = []
        with self.db._connect() as conn:
            active = conn.execute(SQL_LIST_ACTIVE_PLANS).fetchall()

        for row in active:
            instance_id = int(row["instance_id"])
            with self.db._connect() as conn:
                rung = conn.execute(SQL_GET_NEXT_PENDING_RUNG, {"instance_id": instance_id}).fetchone()
            if not rung:
                continue
            actions.append({
                "instance_id": instance_id,
                "rung_id": int(rung["rung_id"]),
                "rung_index": int(rung["rung_index"]),
                "target_price": float(rung["target_price"]),
                "shares_to_sell": int(rung["shares_sold_planned"]),
            })
        return actions

    def scan_and_fire_alerts(self) -> List[Dict[str, Any]]:
        """
        Poll prices for active plans, and mark alert/rung when target is reached.
        Returns a list of triggered rungs with context for execution.
        """
        fired: List[Dict[str, Any]] = []
        now = _utc_now_iso()

        with self.db._connect() as conn:
            active = conn.execute(SQL_LIST_ACTIVE_PLANS).fetchall()

        for row in active:
            instance_id = int(row["instance_id"])
            ticker = row["ticker"]

            with self.db._connect() as conn:
                rung = conn.execute(SQL_GET_NEXT_PENDING_RUNG, {"instance_id": instance_id}).fetchone()
            if not rung:
                continue

            target_price = float(rung["target_price"])
            rung_id = int(rung["rung_id"])
            shares_to_sell = int(rung["shares_sold_planned"])

            current_price = self.db._poll_latest_close(ticker)
            if current_price is None or current_price < target_price:
                continue

            with self.db._connect() as conn:
                conn.execute("BEGIN;")
                try:
                    alert = conn.execute(
                        SQL_GET_ACTIVE_ALERT_FOR_RUNG,
                        {"rung_id": rung_id},
                    ).fetchone()
                    if alert:
                        conn.execute(SQL_MARK_ALERT_FIRED, {
                            "alert_id": int(alert["alert_id"]),
                            "ts": now,
                            "price": current_price,
                        })
                    conn.execute(SQL_MARK_RUNG_TRIGGERED, {
                        "rung_id": rung_id,
                        "ts": now,
                        "price": current_price,
                    })
                    conn.execute("COMMIT;")
                except Exception:
                    conn.execute("ROLLBACK;")
                    raise

            fired.append({
                "symbol": ticker,
                "instance_id": instance_id,
                "rung_id": rung_id,
                "shares_to_sell": shares_to_sell,
                "current_price": current_price,
                "target_price": target_price,
            })

        return fired

    def record_execution(
        self,
        rung_id: int,
        executed_price: float,
        shares_sold: int,
        tax_paid: float = 0.0,
        executed_at: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """
        Record the actual execution of a triggered rung and refresh the next-rung alert.
        """
        ts = executed_at or _utc_now_iso()
        gross = executed_price * shares_sold
        net = gross - tax_paid

        with self.db._connect() as conn:
            conn.execute("BEGIN;")
            try:
                conn.execute(SQL_MARK_RUNG_EXECUTED, {
                    "rung_id": rung_id,
                    "ts": ts,
                    "price": executed_price,
                    "shares_sold": shares_sold,
                    "tax_paid": tax_paid,
                    "net_harvest": net,
                })
                if notes:
                    conn.execute(
                        "UPDATE plan_rungs SET notes = :notes WHERE rung_id = :rung_id;",
                        {"notes": notes, "rung_id": rung_id},
                    )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        with self.db._connect() as conn:
            row = conn.execute(SQL_GET_RUNG_INSTANCE, {"rung_id": rung_id}).fetchone()
        if row:
            self.db._ensure_next_rung_alert(int(row["instance_id"]))

if __name__ == "__main__":
    db_path = os.environ.get("HARVESTER_DB_PATH", "harvester.sqlite")
    db = HarvesterPlanDB(db_path)

    # 1) Build + store a plan
    params = PlanBuildParams(history_window_days=360, n_iterations=4, alpha=0.5, min_H=0.05, max_H=0.30)
    summary = db.build_plan(symbol="TER", template_name="Vol-adjust ladder v1", params=params)
    print(summary)

    # 2) Display all plans
    plans = db.display_all_plans()
    for p in plans:
        print(p)

    # 3) Find symbols currently at a harvest point
    hits = db.symbols_at_harvest_points()
    print(hits)

    # 4) Controller demo: scan and fire alerts for next pending rungs
    controller = HarvesterController(db)
    fired = controller.scan_and_fire_alerts()
    print(fired)
    print(hits)  # list of dicts: {symbol, shares_to_sell, ...}
