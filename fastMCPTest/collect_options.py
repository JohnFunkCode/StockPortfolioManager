#!/usr/bin/env python3
"""
collect_options.py — EOD options snapshot collector.

Intended to be run as a daily cron job at ~4:10 PM ET on trading days. For each
watchlist symbol it fetches the full options chain from yfinance and persists a
snapshot to the unified QuantCore PostgreSQL database (via ``OptionsService``).
Running it once per trading day builds the put/call-ratio and IV trend history
that the options-analysis tooling reads back.

Usage
-----
    python collect_options.py                        # snapshot today, all watchlist symbols
    python collect_options.py --date 2026-04-01      # label the snapshot with a specific date
    python collect_options.py --symbols MU,WDC,GEV   # specific symbols only
    python collect_options.py --dry-run              # validate config/connectivity, skip DB writes
    python collect_options.py --log-level DEBUG      # verbose logging
    python collect_options.py --force                # run even on a non-trading day

Persistence is the unified QuantCore PostgreSQL database addressed by the
``QUANTCORE_DB_DSN`` environment variable — there is no local SQLite file.

Cron entry (4:10 PM ET, Mon–Fri):
    10 16 * * 1-5  cd /path/to/repo && /path/to/venv/python fastMCPTest/collect_options.py

Exit codes
----------
    0 — all symbols succeeded (or non-trading day, nothing attempted)
    1 — one or more symbols failed
    2 — configuration error (no snapshot attempted)

To AutoRun it on MacOS, you can create a Launch Agent plist file like this:
File Name: /Users/your_user/Library/LaunchAgents/com.stockportfolio.collect_options.plist

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
    <string>/Users/you_user/Documents/code/StockPortfolioManager</string>

    <!--
        Run at 4:10 PM every day.  The script exits cleanly (code 0) on weekends
        (and NYSE holidays when pandas_market_calendars is installed), so no data
        is written on non-trading days.
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
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the project root is importable when run directly (for quantcore.*)
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent.resolve()
_PROJECT_ROOT = _HERE.parent
for _p in (_PROJECT_ROOT, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from quantcore.services.registry import get_services

# Default paths
_DEFAULT_WATCHLIST = _PROJECT_ROOT / "watchlist.yaml"
_DEFAULT_LOG_DIR   = _HERE / "logs"

logger = logging.getLogger("collect_options")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _configure_logging(log_dir: Path | None, level: int) -> None:
    """Console logging always; a rotating-free file handler when log_dir is set."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.date.today().isoformat()
        handlers.append(logging.FileHandler(log_dir / f"collect_options_{stamp}.log"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        handlers=handlers,
        force=True,
    )


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
        help="Snapshot date label (default: today)",
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
        default=None,
        metavar="PATH",
        help="Deprecated/ignored. Persistence is the QuantCore PostgreSQL database "
             "addressed by QUANTCORE_DB_DSN; there is no local SQLite file.",
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
        help="Deprecated/advisory. The service now captures every available "
             "expiration in the chain (default kept for CLI compatibility).",
    )
    p.add_argument(
        "--tree-steps",
        type=int,
        default=100,
        metavar="N",
        help="Deprecated/ignored. Greeks come from yfinance; the QuantLib pricer "
             "is no longer used (kept for CLI compatibility).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and connectivity without writing to the DB",
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
# Trading-day check
# ---------------------------------------------------------------------------

def _is_trading_day(day: datetime.date) -> bool:
    """
    True if `day` is a NYSE trading day. Uses pandas_market_calendars for exact
    holiday handling when it is installed; otherwise falls back to a weekday
    check (Mon–Fri), which over-counts holidays but never under-counts.
    """
    try:
        import pandas_market_calendars as mcal
        sched = mcal.get_calendar("NYSE").schedule(start_date=day, end_date=day)
        return not sched.empty
    except Exception:
        return day.weekday() < 5  # Mon=0 .. Fri=4


# ---------------------------------------------------------------------------
# Per-symbol result + report
# ---------------------------------------------------------------------------

@dataclass
class SymbolResult:
    symbol: str
    success: bool = False
    expirations: int = 0
    contracts: int = 0
    duration_ms: int = 0
    persisted: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _print_report(
    results: list[SymbolResult],
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
        f"  {'Symbol':<8}  {'Status':<8}  {'Exp':>4}  {'Contracts':>10}  {'ms':>7}\n"
        f"  {'─' * 60}"
    )
    print(header)

    any_failed = False
    for r in results:
        status = "OK" if r.success else "FAILED"
        print(
            f"  {r.symbol:<8}  {status:<8}  {r.expirations:>4}  "
            f"{r.contracts:>10}  {r.duration_ms:>7}"
        )
        for e in r.errors:
            print(f"           ERROR: {e}")
        for w in r.warnings:
            print(f"           WARN:  {w}")
        if not r.success:
            any_failed = True

    total_contracts = sum(r.contracts for r in results)
    print(f"  {'─' * 60}")
    print(
        f"  {'TOTAL':<8}  {len(results):>8}  {'':>4}  "
        f"{total_contracts:>10}  {elapsed_s:>6.1f}s"
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
    _configure_logging(
        log_dir=None if args.dry_run else Path(args.log_dir),
        level=log_level,
    )

    logger.info(
        "collect_options  date=%s  dry_run=%s", args.date or "today", args.dry_run
    )
    if args.db:
        logger.warning("--db is ignored; persistence is PostgreSQL (QUANTCORE_DB_DSN).")

    # ── Snapshot date label ──────────────────────────────────────────────────
    if args.date:
        try:
            snapshot_date = datetime.date.fromisoformat(args.date)
        except ValueError:
            logger.error("Invalid --date format. Use YYYY-MM-DD.")
            return 2
    else:
        snapshot_date = datetime.date.today()

    # ── Trading-day gate ─────────────────────────────────────────────────────
    if not args.force and not _is_trading_day(snapshot_date):
        logger.info("%s is not a trading day — exiting. Use --force to override.", snapshot_date)
        return 0

    # ── Symbol list ──────────────────────────────────────────────────────────
    symbols = _load_symbols(args)
    if not symbols:
        logger.error("No symbols to snapshot.")
        return 2

    # ── Wire up services (lazy registry; ensures schema exists) ──────────────
    from quantcore.db import init_schema
    init_schema()
    services = get_services()

    # ── Run snapshot ─────────────────────────────────────────────────────────
    t0 = time.monotonic()
    results: list[SymbolResult] = []

    for sym in symbols:
        r = SymbolResult(symbol=sym)
        s0 = time.monotonic()
        try:
            if args.dry_run:
                # Connectivity check only — no chain fetch, no DB write.
                info = services.yfinance_gateway.fast_info(sym)
                price = getattr(info, "last_price", None)
                if price is None:
                    raise ValueError("no price available")
                r.success = True
                r.warnings.append("dry-run: chain not fetched, nothing written")
            else:
                # get_full_options_chain fetches every expiration and persists a
                # snapshot to PostgreSQL via OptionsService -> OptionsStore.
                chain = services.options.get_full_options_chain(sym)
                r.expirations = chain.get("expiration_count", 0)
                r.contracts   = chain.get("total_contracts", 0)
                r.persisted   = bool(chain.get("persisted"))
                r.success     = True
                if not r.persisted and chain.get("storage_warning"):
                    r.warnings.append(chain["storage_warning"])
        except Exception as exc:
            r.errors.append(str(exc))
            logger.warning("%s failed: %s", sym, exc)
        finally:
            r.duration_ms = int((time.monotonic() - s0) * 1000)
            results.append(r)

    elapsed = time.monotonic() - t0
    return _print_report(results, snapshot_date, elapsed, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
