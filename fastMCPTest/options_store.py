"""
options_store.py — SQLite persistence for options chain snapshots.

Addresses GitHub issue #10: "Persist options chain info so we can use it
in future back testing."

Each time get_stock_price() is called (via MCP or options_analysis.py),
a full snapshot is saved:
  - Symbol price + Bollinger Bands at capture time
  - Per-expiration: put/call ratio, aggregate OI/volume/IV
  - Per-contract: all ATM contract details (call and put sides)

This gives a time-series of options sentiment (P/C ratio, IV surface)
that can be replayed for backtesting the scoring rules in options_analysis.py.

Usage:
    from options_store import OptionsStore
    store = OptionsStore()                        # uses default path
    store = OptionsStore("/path/to/options.db")   # custom path

    store.save_snapshot(symbol, price, bands_dict, options_dict)
    history = store.get_pc_history("NVDA", days=30)
    snap    = store.get_latest_snapshot("NVDA")
    snaps   = store.get_snapshots("NVDA", since="2026-01-01")
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Default DB lives next to this file
DEFAULT_DB_PATH = Path(__file__).parent / "options_chain.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- One row per (symbol, point-in-time fetch).
-- Stores price and Bollinger Band context so we can reconstruct what the
-- scoring model saw at the time of the snapshot.
CREATE TABLE IF NOT EXISTS options_snapshots (
    snapshot_id  INTEGER PRIMARY KEY,
    symbol       TEXT    NOT NULL,
    captured_at  TEXT    NOT NULL,   -- ISO-8601 UTC, e.g. "2026-04-08T14:32:00Z"
    price        REAL    NOT NULL,
    bb_upper     REAL,
    bb_middle    REAL,
    bb_lower     REAL,
    bb_period    INTEGER DEFAULT 20,
    chain_type   TEXT    NOT NULL DEFAULT 'atm',  -- 'atm' (nearest exp, ATM only) or 'full' (all exps, all strikes)
    UNIQUE (symbol, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_time
    ON options_snapshots (symbol, captured_at DESC);

-- One row per expiration date within a snapshot.
-- put_call_ratio here is computed from aggregate OI across all strikes
-- (same calculation used by get_stock_price and options_analysis.py).
CREATE TABLE IF NOT EXISTS options_expirations (
    expiration_id  INTEGER PRIMARY KEY,
    snapshot_id    INTEGER NOT NULL
                       REFERENCES options_snapshots (snapshot_id)
                       ON DELETE CASCADE,
    expiration     TEXT    NOT NULL,  -- "YYYY-MM-DD"
    put_call_ratio REAL,
    total_call_oi  INTEGER,
    total_put_oi   INTEGER,
    total_call_vol INTEGER,
    total_put_vol  INTEGER,
    avg_call_iv    REAL,
    avg_put_iv     REAL,
    UNIQUE (snapshot_id, expiration)
);

CREATE INDEX IF NOT EXISTS idx_expirations_snapshot
    ON options_expirations (snapshot_id);

-- Individual option contracts within an expiration.
-- Stores both call and put sides.  Only ATM contracts (nearest 5 strikes)
-- are persisted — full-chain storage is deferred until needed.
CREATE TABLE IF NOT EXISTS options_contracts (
    contract_id   INTEGER PRIMARY KEY,
    expiration_id INTEGER NOT NULL
                      REFERENCES options_expirations (expiration_id)
                      ON DELETE CASCADE,
    kind          TEXT    NOT NULL CHECK (kind IN ('call', 'put')),
    strike        REAL    NOT NULL,
    last_price    REAL,
    bid           REAL,
    ask           REAL,
    implied_vol   REAL,   -- as a percentage (e.g. 61.5 means 61.5%)
    volume        INTEGER,
    open_interest INTEGER,
    in_the_money  INTEGER CHECK (in_the_money IN (0, 1)),
    UNIQUE (expiration_id, kind, strike)
);

CREATE INDEX IF NOT EXISTS idx_contracts_expiration
    ON options_contracts (expiration_id, kind);
"""


# ---------------------------------------------------------------------------
# OptionsStore
# ---------------------------------------------------------------------------

class OptionsStore:
    """
    Thin persistence wrapper around the options SQLite database.

    Thread-safety: Each method opens its own connection, so the store is
    safe to use from multiple threads (WAL mode handles concurrent readers).
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)
            # Migration: add chain_type column to existing databases
            try:
                conn.execute(
                    "ALTER TABLE options_snapshots ADD COLUMN chain_type TEXT NOT NULL DEFAULT 'atm'"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists

    @staticmethod
    def _now_utc() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save_snapshot(
        self,
        symbol: str,
        price: float,
        bollinger_bands: Optional[dict],
        options: Optional[dict],
        captured_at: Optional[str] = None,
    ) -> Optional[int]:
        """
        Persist one options chain snapshot.

        Parameters
        ----------
        symbol          : ticker, e.g. "NVDA"
        price           : last trade price at capture time
        bollinger_bands : dict with keys upper/middle/lower/period
                          (matches get_stock_price() output)
        options         : dict with keys expiration/put_call_ratio/calls/puts
                          (matches get_stock_price() output)
                          Pass None if the ticker has no options.
        captured_at     : ISO-8601 UTC string; defaults to now

        Returns
        -------
        snapshot_id  or  None if the snapshot already exists (duplicate).
        """
        ts = captured_at or self._now_utc()
        symbol = symbol.upper()

        bb = bollinger_bands or {}

        with self._connect() as conn:
            # --- options_snapshots ---
            try:
                cur = conn.execute(
                    """
                    INSERT INTO options_snapshots
                        (symbol, captured_at, price, bb_upper, bb_middle, bb_lower, bb_period)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        ts,
                        round(price, 4),
                        bb.get("upper"),
                        bb.get("middle"),
                        bb.get("lower"),
                        bb.get("period", 20),
                    ),
                )
                snapshot_id = cur.lastrowid
            except sqlite3.IntegrityError:
                # Duplicate (symbol, captured_at) — skip silently
                return None

            if options is None:
                return snapshot_id

            # --- options_expirations ---
            expiration = options.get("expiration")
            if not expiration:
                return snapshot_id

            calls = options.get("calls") or {}
            puts  = options.get("puts")  or {}

            cur = conn.execute(
                """
                INSERT INTO options_expirations
                    (snapshot_id, expiration, put_call_ratio,
                     total_call_oi, total_put_oi,
                     total_call_vol, total_put_vol,
                     avg_call_iv, avg_put_iv)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (snapshot_id, expiration) DO NOTHING
                """,
                (
                    snapshot_id,
                    expiration,
                    options.get("put_call_ratio"),
                    calls.get("total_open_interest"),
                    puts.get("total_open_interest"),
                    calls.get("total_volume"),
                    puts.get("total_volume"),
                    calls.get("avg_iv_pct"),
                    puts.get("avg_iv_pct"),
                ),
            )
            expiration_id = cur.lastrowid

            # --- options_contracts (ATM calls + puts) ---
            rows = []
            for kind, side in (("call", calls), ("put", puts)):
                for c in side.get("atm_contracts", []):
                    rows.append((
                        expiration_id,
                        kind,
                        c.get("strike"),
                        c.get("last"),
                        c.get("bid"),
                        c.get("ask"),
                        c.get("iv"),
                        c.get("volume"),
                        c.get("open_interest"),
                        int(bool(c.get("in_the_money", False))),
                    ))

            conn.executemany(
                """
                INSERT INTO options_contracts
                    (expiration_id, kind, strike, last_price, bid, ask,
                     implied_vol, volume, open_interest, in_the_money)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (expiration_id, kind, strike) DO NOTHING
                """,
                rows,
            )

        return snapshot_id

    def save_full_chain(
        self,
        symbol: str,
        price: float,
        bollinger_bands: Optional[dict],
        expirations_data: list[dict],
        captured_at: Optional[str] = None,
    ) -> Optional[int]:
        """
        Persist a full options chain snapshot (all strikes, all expirations).

        Parameters
        ----------
        symbol           : ticker, e.g. "AAPL"
        price            : last trade price at capture time
        bollinger_bands  : dict with keys upper/middle/lower/period
        expirations_data : list of dicts, one per expiration:
                           {
                             expiration: str,
                             put_call_ratio: float|None,
                             calls: { contracts: [...], total_open_interest, total_volume, avg_iv_pct },
                             puts:  { contracts: [...], total_open_interest, total_volume, avg_iv_pct },
                           }
                           Each contract: { strike, last, bid, ask, iv, volume, open_interest, in_the_money }
        captured_at      : ISO-8601 UTC string; defaults to now

        Returns
        -------
        snapshot_id  or  None if duplicate.
        """
        ts = captured_at or self._now_utc()
        symbol = symbol.upper()
        bb = bollinger_bands or {}

        with self._connect() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO options_snapshots
                        (symbol, captured_at, price, bb_upper, bb_middle, bb_lower, bb_period, chain_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'full')
                    """,
                    (
                        symbol,
                        ts,
                        round(price, 4),
                        bb.get("upper"),
                        bb.get("middle"),
                        bb.get("lower"),
                        bb.get("period", 20),
                    ),
                )
                snapshot_id = cur.lastrowid
            except sqlite3.IntegrityError:
                return None

            for exp_entry in expirations_data:
                expiration = exp_entry.get("expiration")
                if not expiration:
                    continue

                calls = exp_entry.get("calls") or {}
                puts = exp_entry.get("puts") or {}

                cur = conn.execute(
                    """
                    INSERT INTO options_expirations
                        (snapshot_id, expiration, put_call_ratio,
                         total_call_oi, total_put_oi,
                         total_call_vol, total_put_vol,
                         avg_call_iv, avg_put_iv)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (snapshot_id, expiration) DO NOTHING
                    """,
                    (
                        snapshot_id,
                        expiration,
                        exp_entry.get("put_call_ratio"),
                        calls.get("total_open_interest"),
                        puts.get("total_open_interest"),
                        calls.get("total_volume"),
                        puts.get("total_volume"),
                        calls.get("avg_iv_pct"),
                        puts.get("avg_iv_pct"),
                    ),
                )
                expiration_id = cur.lastrowid

                rows = []
                for kind, side in (("call", calls), ("put", puts)):
                    for c in side.get("contracts", []):
                        rows.append((
                            expiration_id,
                            kind,
                            c.get("strike"),
                            c.get("last"),
                            c.get("bid"),
                            c.get("ask"),
                            c.get("iv"),
                            c.get("volume"),
                            c.get("open_interest"),
                            int(bool(c.get("in_the_money", False))),
                        ))

                if rows:
                    conn.executemany(
                        """
                        INSERT INTO options_contracts
                            (expiration_id, kind, strike, last_price, bid, ask,
                             implied_vol, volume, open_interest, in_the_money)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (expiration_id, kind, strike) DO NOTHING
                        """,
                        rows,
                    )

        return snapshot_id

    # ------------------------------------------------------------------
    # Read / backtesting queries
    # ------------------------------------------------------------------

    def get_full_chain(self, symbol: str) -> Optional[dict]:
        """
        Return the most recent full-chain snapshot for a symbol,
        including all expirations and all strikes.
        """
        symbol = symbol.upper()

        with self._connect() as conn:
            snap = conn.execute(
                """
                SELECT * FROM options_snapshots
                WHERE  symbol = ? AND chain_type = 'full'
                ORDER  BY captured_at DESC
                LIMIT  1
                """,
                (symbol,),
            ).fetchone()

            if snap is None:
                return None

            snap_dict = dict(snap)

            exps = conn.execute(
                """
                SELECT * FROM options_expirations
                WHERE  snapshot_id = ?
                ORDER  BY expiration ASC
                """,
                (snap_dict["snapshot_id"],),
            ).fetchall()

            result = dict(snap_dict)
            result["expirations"] = []

            for exp in exps:
                exp_dict = dict(exp)
                contracts = conn.execute(
                    """
                    SELECT * FROM options_contracts
                    WHERE  expiration_id = ?
                    ORDER  BY kind, strike
                    """,
                    (exp_dict["expiration_id"],),
                ).fetchall()
                exp_dict["contracts"] = [dict(c) for c in contracts]
                result["expirations"].append(exp_dict)

        return result

    def get_pc_history(self, symbol: str, days: int = 30) -> list[dict]:
        """
        Return put/call ratio over time for a symbol.

        Useful for backtesting: plot P/C trend leading up to a trade
        to see whether the signal was building or reversing.

        Returns a list of dicts ordered by captured_at ASC:
            [{"captured_at": str, "price": float, "put_call_ratio": float|None}, ...]
        """
        symbol = symbol.upper()
        cutoff = (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
        )
        from datetime import timedelta
        cutoff -= timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.captured_at, s.price, e.put_call_ratio,
                       s.bb_upper, s.bb_middle, s.bb_lower
                FROM   options_snapshots   s
                JOIN   options_expirations e ON e.snapshot_id = s.snapshot_id
                WHERE  s.symbol      = ?
                  AND  s.captured_at >= ?
                ORDER  BY s.captured_at ASC
                """,
                (symbol, cutoff_str),
            ).fetchall()

        return [dict(r) for r in rows]

    def get_latest_snapshot(self, symbol: str) -> Optional[dict]:
        """
        Return the most recent full snapshot for a symbol, including
        its expiration data and ATM contracts.
        """
        symbol = symbol.upper()

        with self._connect() as conn:
            snap = conn.execute(
                """
                SELECT * FROM options_snapshots
                WHERE  symbol = ?
                ORDER  BY captured_at DESC
                LIMIT  1
                """,
                (symbol,),
            ).fetchone()

            if snap is None:
                return None

            snap = dict(snap)

            exps = conn.execute(
                """
                SELECT * FROM options_expirations
                WHERE  snapshot_id = ?
                ORDER  BY expiration ASC
                """,
                (snap["snapshot_id"],),
            ).fetchall()

            result = dict(snap)
            result["expirations"] = []

            for exp in exps:
                exp_dict = dict(exp)
                contracts = conn.execute(
                    """
                    SELECT * FROM options_contracts
                    WHERE  expiration_id = ?
                    ORDER  BY kind, strike
                    """,
                    (exp_dict["expiration_id"],),
                ).fetchall()
                exp_dict["contracts"] = [dict(c) for c in contracts]
                result["expirations"].append(exp_dict)

        return result

    def get_snapshots(
        self,
        symbol: str,
        since: Optional[str] = None,
        limit: int = 500,
    ) -> list[dict]:
        """
        Return all snapshots for a symbol, optionally filtered by start date.

        Parameters
        ----------
        symbol : ticker
        since  : ISO-8601 date string, e.g. "2026-01-01" or "2026-01-01T00:00:00Z"
        limit  : max rows returned (default 500)

        Returns a list of lightweight snapshot dicts (no contract details).
        Use get_latest_snapshot() or join manually for full detail.
        """
        symbol = symbol.upper()

        query = """
            SELECT s.snapshot_id, s.captured_at, s.price,
                   s.bb_upper, s.bb_middle, s.bb_lower,
                   e.expiration, e.put_call_ratio,
                   e.total_call_oi, e.total_put_oi,
                   e.avg_call_iv, e.avg_put_iv
            FROM   options_snapshots   s
            LEFT JOIN options_expirations e ON e.snapshot_id = s.snapshot_id
            WHERE  s.symbol = ?
        """
        params: list = [symbol]

        if since:
            query += " AND s.captured_at >= ?"
            params.append(since)

        query += " ORDER BY s.captured_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [dict(r) for r in rows]

    def get_iv_history(self, symbol: str, days: int = 365) -> list[dict]:
        """
        Return composite IV (average of avg_call_iv and avg_put_iv across all
        expirations) per snapshot for the past `days` days.

        Used to compute IV Rank and IV Percentile:
          IV Rank       = (current - 52w_low) / (52w_high - 52w_low) × 100
          IV Percentile = % of past days where IV was below today's IV

        Returns list of dicts ordered by captured_at ASC:
            [{"captured_at": str, "composite_iv": float}, ...]
        """
        symbol = symbol.upper()
        from datetime import timedelta
        cutoff = (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
        ) - timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.snapshot_id,
                    s.captured_at,
                    AVG(
                        CASE
                          WHEN e.avg_call_iv IS NOT NULL AND e.avg_put_iv IS NOT NULL
                            THEN (e.avg_call_iv + e.avg_put_iv) / 2.0
                          WHEN e.avg_call_iv IS NOT NULL THEN e.avg_call_iv
                          WHEN e.avg_put_iv  IS NOT NULL THEN e.avg_put_iv
                          ELSE NULL
                        END
                    ) AS composite_iv
                FROM   options_snapshots   s
                JOIN   options_expirations e ON e.snapshot_id = s.snapshot_id
                WHERE  s.symbol      = ?
                  AND  s.captured_at >= ?
                GROUP  BY s.snapshot_id
                HAVING composite_iv IS NOT NULL
                ORDER  BY s.captured_at ASC
                """,
                (symbol, cutoff_str),
            ).fetchall()

        return [{"captured_at": r["captured_at"], "composite_iv": r["composite_iv"]} for r in rows]

    def get_symbols(self) -> list[str]:
        """Return all symbols that have at least one snapshot."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM options_snapshots ORDER BY symbol"
            ).fetchall()
        return [r["symbol"] for r in rows]

    def snapshot_count(self, symbol: Optional[str] = None) -> int:
        """Return total number of snapshots, optionally for one symbol."""
        with self._connect() as conn:
            if symbol:
                row = conn.execute(
                    "SELECT COUNT(*) FROM options_snapshots WHERE symbol = ?",
                    (symbol.upper(),),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM options_snapshots"
                ).fetchone()
        return row[0]
