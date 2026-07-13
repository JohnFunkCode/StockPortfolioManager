"""One-off repair for the 2026-06-30 OHLCV symbol-misalignment corruption.

A bulk ingest on 2026-06-30 ~20:10-20:13 UTC wrote misaligned price data to
prod: groups of unrelated symbols received byte-identical OHLCV bars (e.g.
INTC=MRVL=POWL=SOLS all got one ticker's bar). This re-fetches each affected
symbol individually through the proven-correct single-symbol path
(YFinanceGateway.fetch_history -> store_bars, ON CONFLICT DO UPDATE) and overwrites the bad
rows in place. Re-fetching a healthy symbol is harmless (idempotent upsert).

Usage:
    .venv/bin/python scripts/repair_ohlcv_misalignment.py            # repair
    .venv/bin/python scripts/repair_ohlcv_misalignment.py --dry-run  # report only

Targets whatever QUANTCORE_DB_DSN points at (prod via the :5433 proxy here).
"""
import argparse
import datetime as dt
import sys
import time
from contextlib import closing

from dotenv import load_dotenv

load_dotenv("/Users/thomasfowler/source/com/thomasdfowler/StockPortfolioManager/.env")

from quantcore.db import get_connection
from quantcore.gateways.yfinance_gateway import YFinanceGateway
from quantcore.repositories.ohlcv_repository import store_bars

_gateway = YFinanceGateway()

WARM_DAYS = 730


def affected_symbols() -> list[str]:
    """Symbols with at least one daily bar ingested on a corruption date.

    2026-06-30: original incident (UI Refresh All -> threaded chain refresh).
    2026-07-02: recurrence before the yf.download lock landed.
    """
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT symbol
            FROM ohlcv
            WHERE interval = '1d'
              AND to_timestamp(ingested_at)::date IN (DATE '2026-06-30', DATE '2026-07-02')
            ORDER BY symbol
            """
        ).fetchall()
    return [r[0] for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="List targets, fetch, but do not write")
    ap.add_argument("--sleep", type=float, default=0.4, help="Seconds between symbols (rate-limit guard)")
    args = ap.parse_args()

    symbols = affected_symbols()
    print(f"[{dt.datetime.now():%H:%M:%S}] affected symbols: {len(symbols)}  dry_run={args.dry_run}")

    ok = empty = failed = 0
    for i, sym in enumerate(symbols, 1):
        try:
            df = _gateway.fetch_history(sym, "1d", WARM_DAYS)
            if df.empty:
                empty += 1
                print(f"  [{i}/{len(symbols)}] {sym:8} EMPTY (yfinance returned nothing)")
                continue
            last_close = float(df["Close"].iloc[-1])
            last_date = df.index[-1].date()
            # Sanity guard: a non-positive close is never valid.
            if last_close <= 0:
                failed += 1
                print(f"  [{i}/{len(symbols)}] {sym:8} SKIP (non-positive close {last_close})")
                continue
            if not args.dry_run:
                store_bars(sym, "1d", df)
            ok += 1
            if i % 25 == 0 or i == len(symbols):
                print(f"  [{i}/{len(symbols)}] {sym:8} {len(df)} bars  last={last_date} close={last_close:.2f}  (ok={ok})")
        except Exception as exc:  # noqa: BLE001 - one-off repair, keep going
            failed += 1
            print(f"  [{i}/{len(symbols)}] {sym:8} FAILED: {type(exc).__name__}: {exc}")
        time.sleep(args.sleep)

    print(f"[{dt.datetime.now():%H:%M:%S}] DONE  repaired={ok}  empty={empty}  failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
