#!/usr/bin/env python3
"""
collect_options.py — EOD options snapshot collector.

Intended to be run as a daily cron job at ~4:10 PM ET on trading days.

Usage
-----
    python collect_options.py                        # snapshot today, all watchlist symbols
    python collect_options.py --date 2026-04-01      # snapshot a specific date
    python collect_options.py --symbols MU,WDC,GEV   # specific symbols only
    python collect_options.py --dry-run              # validate config, skip DB writes
    python collect_options.py --log-level DEBUG      # verbose per-contract logging
    python collect_options.py --max-expirations 6    # capture 6 expirations instead of 4

Cron entry (4:10 PM ET, Mon–Fri):
    10 16 * * 1-5  cd /path/to/fastMCPTest && /path/to/venv/python collect_options.py

Exit codes
----------
    0 — all symbols succeeded
    1 — one or more symbols failed
    2 — market closed / configuration error (no snapshot attempted)

To AutoRun it on MacOS, you can create a Launch Agent plist file like this:
File Name: /Users/your_user/Library/LaunchAgents//com.stockportfolio.collect_options.plist

<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.stockportfolio.collect_options</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/you_user/Documents/code/StockPortfolioManager/.venv/bin/python</string>
        <string>/Users/you_user/Documents/code/StockPortfolioManager/fastMCPTest/collect_options.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/you_user/Documents/code/StockPortfolioManager/fastMCPTest</string>

    <!--
        Run at 4:10 PM every day.  The script itself exits cleanly (code 0) when
        pandas_market_calendars detects a non-trading day (weekend or NYSE holiday),
        so no data is written on those days.
    -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>16</integer>
        <key>Minute</key>
        <integer>10</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/you_user/Documents/code/StockPortfolioManager/fastMCPTest/logs/collect_options_launchd.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/you_user/Documents/code/StockPortfolioManager/fastMCPTest/logs/collect_options_launchd_err.log</string>

    <!-- Force Eastern Time so the 16:10 trigger fires at the correct wall-clock time -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>TZ</key>
        <string>America/New_York</string>
    </dict>
</dict>
</plist>

"""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the package directory is importable when run directly
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent.resolve()
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from options_store import (
    MarketDataFetcher,
    MarketClosedError,
    OptionsRepository,
    SnapshotService,
    configure_logging,
    create_pricer,
    get_logger,
)

# Default paths
_DEFAULT_DB        = _HERE / "options_store.db"
_DEFAULT_WATCHLIST = _HERE.parent / "watchlist.yaml"
_DEFAULT_LOG_DIR   = _HERE / "logs"

logger = get_logger("collect_options")


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="collect_options.py",
        description="EOD options chain snapshot collector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Snapshot date (default: today)",
    )
    p.add_argument(
        "--symbols",
        metavar="SYM1,SYM2,...",
        help="Comma-separated symbols to snapshot (default: full watchlist)",
    )
    p.add_argument(
        "--watchlist",
        default=str(_DEFAULT_WATCHLIST),
        metavar="PATH",
        help=f"Path to watchlist.yaml (default: {_DEFAULT_WATCHLIST})",
    )
    p.add_argument(
        "--db",
        default=str(_DEFAULT_DB),
        metavar="PATH",
        help=f"Path to options_store.db (default: {_DEFAULT_DB})",
    )
    p.add_argument(
        "--log-dir",
        default=str(_DEFAULT_LOG_DIR),
        metavar="PATH",
        help=f"Log output directory (default: {_DEFAULT_LOG_DIR})",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity (default: INFO)",
    )
    p.add_argument(
        "--max-expirations",
        type=int,
        default=4,
        metavar="N",
        help="Number of nearest expirations to capture (default: 4)",
    )
    p.add_argument(
        "--tree-steps",
        type=int,
        default=100,
        metavar="N",
        help="QuantLib binomial tree steps (default: 100, use 200 for higher accuracy)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and connectivity without writing to DB",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Run even if today is not a trading day (useful for testing)",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Watchlist loading
# ---------------------------------------------------------------------------

def _load_symbols(args: argparse.Namespace) -> list[str]:
    """
    Resolve the symbol list from CLI --symbols or from watchlist.yaml.
    Non-USD symbols (e.g. SU.PA, 000660.KS) are filtered out with a
    warning — yfinance rarely provides options chains for them.
    """
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        logger.info("Using CLI symbols: %s", syms)
        return syms

    watchlist_path = Path(args.watchlist)
    if not watchlist_path.exists():
        logger.error("Watchlist not found: %s", watchlist_path)
        sys.exit(2)

    try:
        import yaml
        with open(watchlist_path) as f:
            entries = yaml.safe_load(f) or []
    except Exception as exc:
        logger.error("Failed to load watchlist: %s", exc)
        sys.exit(2)

    syms = []
    skipped = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sym      = entry.get("symbol", "").strip().upper()
        currency = entry.get("currency", "USD")
        tags     = [t for t in (entry.get("tags") or []) if t is not None]

        if not sym:
            continue

        # Skip non-US symbols — options chains are unlikely to be available
        if "." in sym or currency not in ("USD",):
            skipped.append(sym)
            continue

        # Skip ETFs (they have options but not equity-style options analysis)
        if "ETF" in tags:
            skipped.append(sym)
            continue

        syms.append(sym)

    if skipped:
        logger.info(
            "Skipped %d non-USD / ETF symbols: %s", len(skipped), skipped
        )
    logger.info("Watchlist: %d US equity symbols to snapshot", len(syms))
    return syms


# ---------------------------------------------------------------------------
# Dry-run wrapper
# ---------------------------------------------------------------------------

class _DryRunRepository:
    """
    Drop-in replacement for OptionsRepository that logs every write call
    instead of executing it.  Used with --dry-run.
    """
    def __init__(self, db_path):
        logger.info("[DRY-RUN] Would write to: %s", db_path)

    def upsert_market_rate(self, *a, **kw):
        logger.info("[DRY-RUN] upsert_market_rate: %s", a)

    def upsert_contracts(self, contracts, **kw):
        logger.info("[DRY-RUN] upsert_contracts: %d rows", len(contracts))
        return len(contracts)

    def upsert_snapshot(self, snap, **kw):
        logger.info("[DRY-RUN] upsert_snapshot: %s  exp=%s", snap.symbol, snap.expiration)

    def upsert_iv_snapshot(self, iv, **kw):
        logger.info("[DRY-RUN] upsert_iv_snapshot: %s  iv_rank=%.1f",
                    iv.symbol, iv.iv_rank or 0)

    def upsert_sweeps(self, sweeps, **kw):
        logger.info("[DRY-RUN] upsert_sweeps: %d rows", len(sweeps))
        return len(sweeps)

    def upsert_short_interest(self, si, **kw):
        logger.info("[DRY-RUN] upsert_short_interest: %s", si.symbol)

    def get_latest_short_interest_date(self, symbol):
        return None

    def get_iv_history(self, symbol, days=252):
        return []

    def count_snapshots(self, symbol):
        return 0

    @staticmethod
    def transaction():
        """Context manager stub — yields self."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            yield _DryRunRepository.__new__(_DryRunRepository)

        return _ctx()


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _print_report(
    results: list,
    snapshot_date: datetime.date,
    elapsed_s: float,
    dry_run: bool,
) -> int:
    """Print a formatted summary table. Returns exit code (0=all OK, 1=any failed)."""
    header = (
        f"\n{'=' * 72}\n"
        f"  Options Snapshot Report  —  {snapshot_date}"
        + ("  [DRY RUN]" if dry_run else "")
        + f"\n{'=' * 72}\n"
        f"  {'Symbol':<8}  {'Status':<8}  {'Exp':>4}  {'Contracts':>10}  "
        f"{'Sweeps':>7}  {'ms':>6}\n"
        f"  {'─' * 66}"
    )
    print(header)

    any_failed = False
    for r in results:
        status = "OK" if r.success else "FAILED"
        print(
            f"  {r.symbol:<8}  {status:<8}  {r.expirations_processed:>4}  "
            f"{r.contracts_stored:>10}  {r.sweeps_detected:>7}  {r.duration_ms:>6}"
        )
        if r.errors:
            for e in r.errors:
                print(f"           ERROR: {e}")
        if r.warnings:
            for w in r.warnings:
                print(f"           WARN:  {w}")
        if not r.success:
            any_failed = True

    total_contracts = sum(r.contracts_stored for r in results)
    total_sweeps    = sum(r.sweeps_detected for r in results)
    print(f"  {'─' * 66}")
    print(
        f"  {'TOTAL':<8}  {'':>8}  {len(results):>4}d  "
        f"{total_contracts:>10}  {total_sweeps:>7}  {elapsed_s:.1f}s"
    )
    print(f"{'=' * 72}\n")

    return 1 if any_failed else 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    args = _parse_args(argv)

    # ── Logging ─────────────────────────────────────────────────────────────
    log_level = getattr(logging, args.log_level)
    configure_logging(
        log_dir=None if args.dry_run else Path(args.log_dir),
        level=log_level,
        console=True,
    )

    logger.info(
        "collect_options  date=%s  dry_run=%s  max_exp=%d  tree_steps=%d",
        args.date or "today", args.dry_run, args.max_expirations, args.tree_steps,
    )

    # ── Snapshot date ────────────────────────────────────────────────────────
    if args.date:
        try:
            snapshot_date = datetime.date.fromisoformat(args.date)
        except ValueError:
            logger.error("Invalid --date format. Use YYYY-MM-DD.")
            return 2
    else:
        snapshot_date = datetime.date.today()

    # ── Symbol list ──────────────────────────────────────────────────────────
    symbols = _load_symbols(args)
    if not symbols:
        logger.error("No symbols to snapshot.")
        return 2

    # ── Wire up dependencies ─────────────────────────────────────────────────
    fetcher = MarketDataFetcher(
        max_expirations=args.max_expirations,
    )

    # Trading day check (can be overridden with --force for testing)
    if not args.force and not fetcher.is_trading_day(snapshot_date):
        logger.info("%s is not a trading day — exiting. Use --force to override.", snapshot_date)
        return 0

    pricer = create_pricer(tree_steps=args.tree_steps)

    if args.dry_run:
        repository = _DryRunRepository(args.db)
        logger.warning("DRY RUN mode — no data will be written to disk.")
    else:
        repository = OptionsRepository(db_path=Path(args.db))

    service = SnapshotService(
        repository=repository,
        fetcher=fetcher,
        pricer=pricer,
    )

    # ── Run snapshot ─────────────────────────────────────────────────────────
    import time
    t0 = time.monotonic()

    try:
        results = service.run(symbols=symbols, snapshot_date=snapshot_date)
    except MarketClosedError as exc:
        logger.info("%s", exc)
        return 0
    except Exception as exc:
        logger.exception("Fatal error during snapshot run: %s", exc)
        return 2

    elapsed = time.monotonic() - t0
    return _print_report(results, snapshot_date, elapsed, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
