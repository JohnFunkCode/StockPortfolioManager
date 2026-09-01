#!/usr/bin/env python3
"""The daily QuantCore job: notifications, options capture, fundamentals warming.

Runs as the ``quantcore-report`` Cloud Run Job on Cloud Scheduler, and locally
as ``python main.py``. It no longer builds the HTML report — that moved to
``scripts/generate_portfolio_report.py``, which the Raspberry Pi runs
(issue #147). The service name kept its old spelling; the work it does did not.

The step order is an isolation property, not an accident. The cheap,
high-value work runs first (Discord notifications and the Harvester rung
checks), then the options-chain capture that keeps the open-interest history
accumulating, and only then the fundamentals warmer — the one step with a
wall-clock budget. Each of the last two swallows its own failures, so a slow
or broken tail cannot take down the useful side effects that already
succeeded. That defect — "a failure in the report path can fail the job and
take the *useful* side effects down with it" — is what issue #147 was about,
and the warmer is exactly the kind of long-running step that would inherit it.
"""

import math
import os
import sys
import time

from portfolio import portfolio
from portfolio import watch_list
from notifier import Notifier
from quantcore.db import ensure_schema
from quantcore.services.registry import get_services

# Warming knobs, all env-overridable because the right values differ between a
# laptop and a Cloud Run Job task with a hard timeout.
WARM_BUDGET_SECONDS_ENV = "FUNDAMENTALS_WARM_BUDGET_SECONDS"
REPORT_TASK_TIMEOUT_SECONDS_ENV = "REPORT_TASK_TIMEOUT_SECONDS"
STALE_COVERAGE_FLOOR_ENV = "FUNDAMENTALS_STALE_COVERAGE_FLOOR"
STALE_MAX_AGE_HOURS_ENV = "FUNDAMENTALS_STALE_MAX_AGE_HOURS"

DEFAULT_REPORT_TASK_TIMEOUT_SECONDS = 1800.0  # Cloud Run Job task timeout
WARM_DEADLINE_MARGIN_SECONDS = 60.0
DEFAULT_WARM_BUDGET_SECONDS = 900.0   # 15 minutes, below the 30-minute task limit
DEFAULT_STALE_COVERAGE_FLOOR = 0.80   # alarm below 80% of symbols inside the TTL
DEFAULT_STALE_MAX_AGE_HOURS = 168.0   # ...or when anything is older than a week


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back *loudly* on garbage.

    A typo in a Cloud Run env var should not silently give the warmer a zero
    budget — that would look exactly like a warmer that finished instantly.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
        if value <= 0 or not math.isfinite(value):
            raise ValueError
        return value
    except ValueError:
        print(f"WARNING: {name}={raw!r} is not a number; using {default}.",
              file=sys.stderr)
    return default


def _warm_budget_seconds(budget_seconds: float | None) -> float:
    requested = (_env_float(WARM_BUDGET_SECONDS_ENV, DEFAULT_WARM_BUDGET_SECONDS)
                 if budget_seconds is None else budget_seconds)
    if requested <= 0 or not math.isfinite(requested):
        print(f"WARNING: {WARM_BUDGET_SECONDS_ENV}={requested!r} is not a positive finite number; "
              f"using {DEFAULT_WARM_BUDGET_SECONDS}.", file=sys.stderr)
        requested = DEFAULT_WARM_BUDGET_SECONDS

    task_timeout = _env_float(
        REPORT_TASK_TIMEOUT_SECONDS_ENV, DEFAULT_REPORT_TASK_TIMEOUT_SECONDS
    )
    safe_limit = max(1.0, task_timeout - WARM_DEADLINE_MARGIN_SECONDS)
    if requested > safe_limit:
        print(f"WARNING: fundamentals warm budget {requested:.0f}s exceeds the report task "
              f"deadline; clamping to {safe_limit:.0f}s.", file=sys.stderr)
        return safe_limit
    return requested


def alert_if_watchlist_empty(watchlist, portfolio, notifier) -> bool:
    """Alarm — loudly — when the watchlist table comes back empty (issue #83).

    An empty watchlist is a degraded run, not a normal one: the options-chain
    capture loop silently narrows to portfolio symbols, so the open-interest
    history develops a hole nobody sees until they go looking for it months
    later, and the published report loses its watchlist section.

    There is deliberately no fallback to watchlist.yaml (plan decision 7) —
    that would re-hide the persistence failure this issue is about. Alarm and
    keep going instead: everything that does not depend on the watchlist still
    runs. Returns True when the alarm fired.
    """
    if watchlist.list_stocks():
        return False

    print("ERROR: watchlist is empty; options-chain capture is running on "
          "portfolio symbols only. Re-seed with scripts/import_watchlist.py.",
          file=sys.stderr)
    try:
        notifier.send_empty_watchlist_alert(len(portfolio.list_stocks()))
    except Exception as exc:  # noqa: BLE001 — a dead webhook must not kill the job
        # The stderr line above already recorded the condition, and the options
        # capture below still has real work to do.
        print(f"  (failed to send the empty-watchlist alert: {exc})",
              file=sys.stderr)
    return True


def warm_fundamentals_cache(symbols, fundamentals, budget_seconds=None,
                            clock=time.monotonic) -> dict:
    """Refresh the fundamentals cache oldest-first until the budget runs out.

    Nothing else refreshes this cache, so without a warming pass the
    fundamentals views serve whatever a human last happened to ask for.

    The pass is bounded because it cannot afford not to be: a cold sweep is
    roughly ten serial yfinance calls per symbol across a couple of hundred
    symbols, which will not finish inside a Cloud Run Job's task timeout.
    Oldest-first ordering plus a wall-clock budget converges the whole universe
    over a few nights while keeping any single night's cost predictable.

    Only symbols outside the TTL are candidates. ``get_full_fundamental_profile``
    writes all four data types in one pass, so they age together and
    ``fundamental_score`` freshness stands in for the set.

    One bad symbol degrades one row: the per-symbol ``try/except`` here is the
    inner guard, and ``run_fundamentals_warming`` is the outer one.
    """
    # Resolved here rather than at the call site so every caller — including
    # run_fundamentals_warming, which passes nothing — honours the env var.
    budget = _warm_budget_seconds(budget_seconds)

    before = fundamentals.cache_freshness(symbols)
    candidates = [row["symbol"] for row in before["symbols"] if row["stale"]]

    print(f"Warming fundamentals for {len(candidates)} stale of "
          f"{before['requested']} symbol(s), budget {budget:.0f}s "
          f"(coverage {before['coverage']:.0%})...")

    started = clock()
    warmed = 0
    failed = 0
    attempted = 0

    for sym in candidates:
        if clock() - started >= budget:
            break
        attempted += 1
        try:
            fundamentals.get_full_fundamental_profile(sym)
            warmed += 1
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not kill the pass
            failed += 1
            print(f"  {sym}: fundamentals warm failed: {exc}")

    elapsed = clock() - started
    summary = {
        "candidates":       len(candidates),
        "attempted":        attempted,
        "warmed":           warmed,
        "failed":           failed,
        "skipped":          len(candidates) - attempted,
        "elapsed_seconds":  round(elapsed, 1),
        "budget_seconds":   budget,
        "budget_exhausted": attempted < len(candidates),
    }
    print(f"  warmed {warmed}, failed {failed}, skipped {summary['skipped']} "
          f"in {summary['elapsed_seconds']}s"
          + (" (budget exhausted)" if summary["budget_exhausted"] else ""))
    return summary


def alert_if_fundamentals_stale(freshness, notifier, coverage_floor=None,
                                max_age_hours=None) -> bool:
    """Alarm when the cache is still stale *after* a warming pass.

    Same bilge-pump shape as ``alert_if_watchlist_empty``: the warmer swallows
    its failures so the job survives, and this makes the resulting degradation
    loud. Without it, a warmer that quietly does nothing is indistinguishable
    from one that finished, and the fundamentals views keep serving month-old
    scores. Returns True when the alarm fired.

    Two independent triggers, because they catch different failures: coverage
    catches a warmer that is not keeping up across the board, and the age
    ceiling catches a handful of symbols that fail every night while the
    average stays healthy.
    """
    floor = _env_float(STALE_COVERAGE_FLOOR_ENV, DEFAULT_STALE_COVERAGE_FLOOR) \
        if coverage_floor is None else coverage_floor
    ceiling = _env_float(STALE_MAX_AGE_HOURS_ENV, DEFAULT_STALE_MAX_AGE_HOURS) \
        if max_age_hours is None else max_age_hours

    coverage = freshness["coverage"]
    oldest_seconds = freshness.get("oldest_age_seconds")
    oldest_hours = None if oldest_seconds is None else oldest_seconds / 3600.0

    too_thin = coverage < floor
    too_old = oldest_hours is not None and oldest_hours > ceiling
    if not (too_thin or too_old):
        return False

    print(f"ERROR: fundamentals cache is stale — {freshness['stale_count']} of "
          f"{freshness['requested']} symbol(s) outside the TTL "
          f"({coverage:.0%} coverage, floor {floor:.0%}"
          + (f", oldest {oldest_hours:.0f}h, ceiling {ceiling:.0f}h" if oldest_hours is not None else "")
          + ").", file=sys.stderr)
    try:
        notifier.send_stale_fundamentals_alert(
            coverage=coverage,
            stale_count=freshness["stale_count"],
            requested=freshness["requested"],
            oldest_age_hours=oldest_hours,
        )
    except Exception as exc:  # noqa: BLE001 — a dead webhook must not kill the job
        print(f"  (failed to send the stale-fundamentals alert: {exc})",
              file=sys.stderr)
    return True


def run_fundamentals_warming(symbols, fundamentals, notifier) -> None:
    """Warm the cache, then alarm if it is still stale. Never raises.

    The per-symbol guard inside ``warm_fundamentals_cache`` is not sufficient
    on its own — a failure in the freshness query, the service registry, or the
    database connection happens outside any per-symbol block. This runs last in
    the job, so letting anything here propagate would mark a run that already
    sent its notifications and captured its options chains as failed.
    """
    try:
        warm_fundamentals_cache(symbols, fundamentals)
        # Re-read after warming: the alarm should describe the state the run
        # actually left behind, not the one it started from.
        alert_if_fundamentals_stale(fundamentals.cache_freshness(symbols), notifier)
    except Exception as exc:  # noqa: BLE001 — the last step must not fail the job
        print(f"ERROR: fundamentals warming pass failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    # Make sure the schema is right (creates tables, or verifies them
    # where Flyway owns the DDL -- see QUANTCORE_SCHEMA_MODE).
    ensure_schema()

    # Create a portfolio
    portfolio = portfolio.Portfolio()

    # Load John's positions from the DB-backed source of truth (positions table).
    # portfolio.csv remains John's import file; refresh it via
    # scripts/import_portfolio.py --csv portfolio.csv --owner john.
    portfolio.read_stocks_from_records(get_services().portfolio.list_positions("john"))

    # Update current prices
    portfolio.update_all_prices()
    portfolio.add_descriptive_info_to_stocks()
    portfolio.update_metrics()

    # Create a watchlist of stocks to track
    watchlist = watch_list.WatchList()

    # Load the watchlist from the DB-backed source of truth (watchlist table,
    # issue #83). watchlist.yaml is now only an import file; refresh it via
    # scripts/import_watchlist.py. There is deliberately no fallback to the
    # YAML when the table is empty — a silent fallback would re-hide exactly
    # the failure this issue is about.
    watchlist.read_stocks_from_records(get_services().watchlist.list_entries())
    watchlist.update_all_prices()
    watchlist.update_metrics()

    # Notifications first: they are the cheapest step and the one people
    # actually read, so nothing slower gets to stand in front of them.
    notifier = Notifier(portfolio)
    notifier.calculate_and_send_notifications()

    alert_if_watchlist_empty(watchlist, portfolio, notifier)

    # Capture full options chains for portfolio + watchlist symbols so the
    # options_contracts OI time series (and daily GEX regime history) keeps
    # accumulating (issue #93 Phases 4/5). In-process services, capped at 6
    # expirations per symbol; a failed fetch must never fail the job.
    capture_symbols = []
    for stock in portfolio.list_stocks() + watchlist.list_stocks():
        if stock.symbol not in capture_symbols:
            capture_symbols.append(stock.symbol)

    # Capture walks every owner's symbols, not just John's (issue #126 decision
    # #5) — other owners' positions have no separate daily job of their own.
    for other_owner in get_services().portfolio.list_owners():
        if other_owner == "john":
            continue
        for row in get_services().portfolio.list_positions(other_owner):
            sym = row.get("symbol")
            if sym and sym not in capture_symbols:
                capture_symbols.append(sym)

    print(f"Capturing options chains for {len(capture_symbols)} symbols...")
    for sym in capture_symbols:
        try:
            chain = get_services().options.get_full_options_chain(sym, max_expirations=6)
            print(f"  {sym}: {chain.get('expiration_count', 0)} expirations, "
                  f"{chain.get('total_contracts', 0)} contracts"
                  + ("" if chain.get('persisted') else " (not persisted — duplicate)"))
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not kill the job
            print(f"  {sym}: options chain capture failed: {exc}")

    # Last, and budgeted: the same universe the options capture just walked is
    # the one the fundamentals views read, so warm exactly that.
    run_fundamentals_warming(capture_symbols, get_services().fundamentals, notifier)
