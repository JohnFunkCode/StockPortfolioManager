"""
options_store.py — PostgreSQL persistence for options chain snapshots.

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
    store = OptionsStore()                                          # uses default DSN
    store = OptionsStore("postgresql://quantcore:pw@host/testdb")  # custom DSN

    store.save_snapshot(symbol, price, bands_dict, options_dict)
    history = store.get_pc_history("NVDA", days=30)
    snap    = store.get_latest_snapshot("NVDA")
    snaps   = store.get_snapshots("NVDA", since="2026-01-01")
"""

import psycopg2
import psycopg2.errors
from contextlib import closing
from datetime import datetime, timezone
from typing import Optional

from quantcore.db import get_connection, init_schema


# ---------------------------------------------------------------------------
# OptionsStore
# ---------------------------------------------------------------------------

class OptionsStore:
    """
    Thin persistence wrapper around the options PostgreSQL tables.

    Thread-safety: Each method opens its own connection.

    Constructor supports optional dsn for testing; uses quantcore.db.get_connection() by default.
    """

    def __init__(self, dsn: Optional[str] = None) -> None:
        self._dsn = dsn
        if dsn:
            init_schema(dsn)

    def _get_connection(self):
        if self._dsn:
            from quantcore.db import _PGConn
            return _PGConn(psycopg2.connect(self._dsn))
        return get_connection()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

        with closing(self._get_connection()) as conn:
            # --- options_snapshots ---
            try:
                cur = conn.execute(
                    """
                    INSERT INTO options_snapshots
                        (symbol, captured_at, price, bb_upper, bb_middle, bb_lower, bb_period)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING snapshot_id
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
                snapshot_id = cur.fetchone()[0]
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                return None

            if options is None:
                conn.commit()
                return snapshot_id

            # --- options_expirations ---
            expiration = options.get("expiration")
            if not expiration:
                conn.commit()
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (snapshot_id, expiration) DO UPDATE SET
                    put_call_ratio = COALESCE(EXCLUDED.put_call_ratio, options_expirations.put_call_ratio)
                RETURNING expiration_id
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
            expiration_id = cur.fetchone()[0]

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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (expiration_id, kind, strike) DO NOTHING
                """,
                rows,
            )

            conn.commit()

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

        with closing(self._get_connection()) as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO options_snapshots
                        (symbol, captured_at, price, bb_upper, bb_middle, bb_lower, bb_period, chain_type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'full')
                    RETURNING snapshot_id
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
                snapshot_id = cur.fetchone()[0]
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (snapshot_id, expiration) DO UPDATE SET
                        put_call_ratio = COALESCE(EXCLUDED.put_call_ratio, options_expirations.put_call_ratio)
                    RETURNING expiration_id
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
                expiration_id = cur.fetchone()[0]

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
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (expiration_id, kind, strike) DO NOTHING
                        """,
                        rows,
                    )

            conn.commit()

        return snapshot_id

    def save_gamma_wall(self, symbol: str, result: dict) -> None:
        """
        Persist one daily gamma wall snapshot. Last write of the day wins
        so a post-close capture (4:15pm+ ET) overwrites any earlier intraday call.

        Parameters
        ----------
        symbol : ticker, e.g. "MSFT"
        result : dict from get_delta_adjusted_oi(), containing:
                 price, gamma_wall_strike, delta_flip_strike, dist_to_flip_pct,
                 net_daoi_shares, call_daoi_shares, put_daoi_shares,
                 mm_hedge_bias, signal, expirations_scanned, and full payload
        """
        import json
        symbol = symbol.upper()
        captured_at = self._now_utc()
        date_only = captured_at[:10]  # Extract YYYY-MM-DD

        with closing(self._get_connection()) as conn:
            conn.execute(
                """
                INSERT INTO gamma_wall_history
                (symbol, date_only, captured_at, price, gamma_wall_strike, delta_flip_strike,
                 dist_to_flip_pct, net_daoi_shares, call_daoi_shares, put_daoi_shares,
                 mm_hedge_bias, signal, expirations_scanned, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, date_only) DO UPDATE SET
                    captured_at          = EXCLUDED.captured_at,
                    price                = EXCLUDED.price,
                    gamma_wall_strike    = EXCLUDED.gamma_wall_strike,
                    delta_flip_strike    = EXCLUDED.delta_flip_strike,
                    dist_to_flip_pct     = EXCLUDED.dist_to_flip_pct,
                    net_daoi_shares      = EXCLUDED.net_daoi_shares,
                    call_daoi_shares     = EXCLUDED.call_daoi_shares,
                    put_daoi_shares      = EXCLUDED.put_daoi_shares,
                    mm_hedge_bias        = EXCLUDED.mm_hedge_bias,
                    signal               = EXCLUDED.signal,
                    expirations_scanned  = EXCLUDED.expirations_scanned,
                    payload              = EXCLUDED.payload
                """,
                (
                    symbol,
                    date_only,
                    captured_at,
                    result.get("price"),
                    result.get("gamma_wall_strike"),
                    result.get("delta_flip_strike"),
                    result.get("dist_to_flip_pct"),
                    result.get("net_daoi_shares"),
                    result.get("call_daoi_shares"),
                    result.get("put_daoi_shares"),
                    result.get("mm_hedge_bias"),
                    result.get("signal"),
                    json.dumps(result.get("expirations_scanned", [])),
                    json.dumps(result),
                ),
            )
            conn.commit()

    def get_gamma_wall_history(self, symbol: str, since_days: int = 90) -> list[dict]:
        """
        Return daily gamma wall snapshots for `symbol` over the past `since_days` days.

        Returns a list of dicts ordered by date_only ASC:
            [{
                date, captured_at, price, gamma_wall_strike, delta_flip_strike,
                dist_to_flip_pct, net_daoi_shares, call_daoi_shares, put_daoi_shares,
                mm_hedge_bias, signal, expirations_scanned
            }, ...]
        """
        import json
        from datetime import timedelta
        symbol = symbol.upper()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%d")

        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT date_only, captured_at, price, gamma_wall_strike, delta_flip_strike,
                       dist_to_flip_pct, net_daoi_shares, call_daoi_shares, put_daoi_shares,
                       mm_hedge_bias, signal, expirations_scanned
                FROM gamma_wall_history
                WHERE symbol = %s AND date_only >= %s
                ORDER BY date_only ASC
                """,
                (symbol, cutoff),
            ).fetchall()

        return [
            {
                "date":               r["date_only"],
                "captured_at":        r["captured_at"],
                "price":              r["price"],
                "gamma_wall_strike":  r["gamma_wall_strike"],
                "delta_flip_strike":  r["delta_flip_strike"],
                "dist_to_flip_pct":   r["dist_to_flip_pct"],
                "net_daoi_shares":    r["net_daoi_shares"],
                "call_daoi_shares":   r["call_daoi_shares"],
                "put_daoi_shares":    r["put_daoi_shares"],
                "mm_hedge_bias":      r["mm_hedge_bias"],
                "signal":             r["signal"],
                "expirations_scanned": json.loads(r["expirations_scanned"]) if r["expirations_scanned"] else [],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Read / backtesting queries
    # ------------------------------------------------------------------

    def get_full_chain(self, symbol: str) -> Optional[dict]:
        """
        Return the most recent full-chain snapshot for a symbol,
        including all expirations and all strikes.
        """
        symbol = symbol.upper()

        with closing(self._get_connection()) as conn:
            snap = conn.execute(
                """
                SELECT * FROM options_snapshots
                WHERE  symbol = %s AND chain_type = 'full'
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
                WHERE  snapshot_id = %s
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
                    WHERE  expiration_id = %s
                    ORDER  BY kind, strike
                    """,
                    (exp_dict["expiration_id"],),
                ).fetchall()
                exp_dict["contracts"] = [dict(c) for c in contracts]
                result["expirations"].append(exp_dict)

        return result

    def get_snapshot_dates(self, symbol: str, days: int = 365) -> set[str]:
        """
        Return the set of calendar dates (YYYY-MM-DD) for which a snapshot
        already exists, so backfill callers can skip them.
        """
        from datetime import timedelta
        symbol = symbol.upper()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT captured_at
                FROM   options_snapshots
                WHERE  symbol      = %s
                  AND  captured_at >= %s
                """,
                (symbol, cutoff_str),
            ).fetchall()
        return {r[0][:10] for r in rows}

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

        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT s.captured_at, s.price, e.put_call_ratio,
                       s.bb_upper, s.bb_middle, s.bb_lower
                FROM   options_snapshots   s
                JOIN   options_expirations e ON e.snapshot_id = s.snapshot_id
                WHERE  s.symbol      = %s
                  AND  s.captured_at >= %s
                ORDER  BY s.captured_at ASC
                """,
                (symbol, cutoff_str),
            ).fetchall()

        return [dict(r) for r in rows]

    def get_latest_snapshot(self, symbol: str) -> Optional[dict]:
        """
        Return the most recent full snapshot for a symbol, including
        its expiration data and ATM contracts.

        Prefers the most recent 'full' chain snapshot (all strikes/expirations)
        over ATM snapshots, since ATM snapshots have no contract/expiration data
        and would leave the Options Chain tab empty.  Falls back to any snapshot
        if no full chain exists yet.
        """
        symbol = symbol.upper()

        with closing(self._get_connection()) as conn:
            # Prefer the most recent full-chain snapshot
            snap = conn.execute(
                """
                SELECT * FROM options_snapshots
                WHERE  symbol = %s
                  AND  chain_type = 'full'
                ORDER  BY captured_at DESC
                LIMIT  1
                """,
                (symbol,),
            ).fetchone()

            # Fall back to any snapshot (e.g. ATM-only) if no full chain exists
            if snap is None:
                snap = conn.execute(
                    """
                    SELECT * FROM options_snapshots
                    WHERE  symbol = %s
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
                WHERE  snapshot_id = %s
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
                    WHERE  expiration_id = %s
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
            WHERE  s.symbol = %s
        """
        params: list = [symbol]

        if since:
            query += " AND s.captured_at >= %s"
            params.append(since)

        query += " ORDER BY s.captured_at DESC LIMIT %s"
        params.append(limit)

        with closing(self._get_connection()) as conn:
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

        with closing(self._get_connection()) as conn:
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
                WHERE  s.symbol      = %s
                  AND  s.captured_at >= %s
                GROUP  BY s.snapshot_id, s.captured_at
                HAVING AVG(
                    CASE
                      WHEN e.avg_call_iv IS NOT NULL AND e.avg_put_iv IS NOT NULL
                        THEN (e.avg_call_iv + e.avg_put_iv) / 2.0
                      WHEN e.avg_call_iv IS NOT NULL THEN e.avg_call_iv
                      WHEN e.avg_put_iv  IS NOT NULL THEN e.avg_put_iv
                      ELSE NULL
                    END
                ) IS NOT NULL
                ORDER  BY s.captured_at ASC
                """,
                (symbol, cutoff_str),
            ).fetchall()

        return [{"captured_at": r["captured_at"], "composite_iv": r["composite_iv"]} for r in rows]

    def get_symbols(self) -> list[str]:
        """Return all symbols that have at least one snapshot."""
        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM options_snapshots ORDER BY symbol"
            ).fetchall()
        return [r["symbol"] for r in rows]

    def snapshot_count(self, symbol: Optional[str] = None) -> int:
        """Return total number of snapshots, optionally for one symbol."""
        with closing(self._get_connection()) as conn:
            if symbol:
                row = conn.execute(
                    "SELECT COUNT(*) FROM options_snapshots WHERE symbol = %s",
                    (symbol.upper(),),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM options_snapshots"
                ).fetchone()
        return row[0]
