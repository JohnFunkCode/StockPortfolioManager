#!/usr/bin/env python3
"""The daily QuantCore job: notifications, options capture, GEX, news check, fundamentals warming.

Runs as the ``quantcore-report`` Cloud Run Job on Cloud Scheduler, and locally
as ``python main.py``. It no longer builds the HTML report — that moved to
``scripts/generate_portfolio_report.py``, which the Raspberry Pi runs
(issue #147). The service name kept its old spelling; the work it does did not.

The step order is an isolation property, not an accident. The cheap,
high-value work runs first (Discord notifications and the Harvester rung
checks), then the options-chain capture that keeps the open-interest history
accumulating, then gamma/GEX computed from the chain the capture just
persisted (no second Yahoo fetch, #373), the capture health check, the news
staleness check, and only then the fundamentals warmer. Each tail step
swallows its own failures, so a slow or broken tail cannot take down the
useful side effects that already succeeded. That defect — "a failure in the
report path can fail the job and take the *useful* side effects down with it"
— is what issue #147 was about.

Warming stays last on purpose (#373): the end-of-day options chain can only be
captured at the close, while a fundamentals profile skipped tonight can be
warmed any later night. When the budget is short, the step that can be
redone is the one that gives way.

The job does nothing on a day the NYSE is closed (weekend or holiday): no
prices move, so a notification would be noise and a capture would be a
duplicate of the prior session. Cloud Scheduler fires Mon-Fri, so the guard
exists for the weekday holidays.

Every tail step is budgeted against one job deadline, so a slow step can only
squeeze the ones after it, never push the task past its timeout. After the
tail, ``check_capture_health`` compares what actually landed in the database
against the universe that was attempted and raises a Discord alarm on a gap --
the steps swallow their own failures by design, so this is what makes a night
that quietly captured nothing visible.
"""

import math
import os
import sys
import time
from datetime import date

from portfolio import portfolio
from portfolio import watch_list
from notifier import Notifier
from quantcore.analytics.market_time import is_trading_day, market_date
from quantcore.db import ensure_schema
from quantcore.services.registry import get_services

# Warming knobs, all env-overridable because the right values differ between a
# laptop and a Cloud Run Job task with a hard timeout.
WARM_BUDGET_SECONDS_ENV = "FUNDAMENTALS_WARM_BUDGET_SECONDS"
REPORT_TASK_TIMEOUT_SECONDS_ENV = "REPORT_TASK_TIMEOUT_SECONDS"
STALE_COVERAGE_FLOOR_ENV = "FUNDAMENTALS_STALE_COVERAGE_FLOOR"
STALE_MAX_AGE_HOURS_ENV = "FUNDAMENTALS_STALE_MAX_AGE_HOURS"

OPTIONS_CAPTURE_BUDGET_SECONDS_ENV = "OPTIONS_CAPTURE_BUDGET_SECONDS"
GEX_BUDGET_SECONDS_ENV = "GEX_RECORD_BUDGET_SECONDS"
CAPTURE_COVERAGE_FLOOR_ENV = "OPTIONS_CAPTURE_COVERAGE_FLOOR"
CAPTURE_FAILURE_CEILING_ENV = "OPTIONS_CAPTURE_FAILURE_CEILING"
CAPTURE_NO_OPTIONS_CEILING_ENV = "OPTIONS_CAPTURE_NO_OPTIONS_CEILING"

DEFAULT_OPTIONS_CAPTURE_BUDGET_SECONDS = 600.0
DEFAULT_GEX_BUDGET_SECONDS = 300.0
DEFAULT_CAPTURE_COVERAGE_FLOOR = 0.90  # alarm when fewer of the universe's chains landed
DEFAULT_CAPTURE_FAILURE_CEILING = 0.50  # alarm when more of a step's attempts failed
DEFAULT_CAPTURE_NO_OPTIONS_CEILING = 0.25  # alarm when more of the universe listed no options

DEFAULT_REPORT_TASK_TIMEOUT_SECONDS = 1800.0  # Cloud Run Job task timeout
WARM_DEADLINE_MARGIN_SECONDS = 60.0
DEFAULT_WARM_BUDGET_SECONDS = 900.0   # 15 minutes, below the 30-minute task limit
DEFAULT_STALE_COVERAGE_FLOOR = 0.80   # alarm below 80% of symbols inside the TTL
NEWS_STALE_MAX_AGE_HOURS_ENV = "NEWS_STALE_MAX_AGE_HOURS"
DEFAULT_NEWS_STALE_MAX_AGE_HOURS = 120.0   # Fri run -> Tue after a Monday holiday is ~95h
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


def _remaining_budget(requested: float, job_started: float,
                      clock=time.monotonic) -> float:
    """Clamp a step budget to what is left of the job's task deadline.

    Each tail step has its own budget, but the budgets together can exceed the
    task timeout; without this a slow capture would leave the warmer a budget it
    can never spend. Never below one second, so a late step still tries once
    rather than being skipped by a rounding accident.
    """
    task_timeout = _env_float(
        REPORT_TASK_TIMEOUT_SECONDS_ENV, DEFAULT_REPORT_TASK_TIMEOUT_SECONDS
    )
    left = task_timeout - WARM_DEADLINE_MARGIN_SECONDS - (clock() - job_started)
    return max(1.0, min(requested, left))


def capture_options_chains(symbols, options, budget_seconds=None,
                           clock=time.monotonic, rate_limit_count=None) -> dict:
    """Capture a full options chain per symbol under a wall-clock budget.

    Same shape as ``warm_fundamentals_cache``: a per-symbol guard so one bad
    ticker degrades one row, and a summary the caller can alarm on. Previously
    this loop was unbudgeted and only printed failures, so a Yahoo outage (every
    symbol failing) and a healthy run both exited 0 with nothing to tell them
    apart. Never raises.

    A symbol the service marks ``no_listed_options`` is counted as
    ``no_options``, not ``duplicate``: there was never a chain to persist, so it
    must not count against coverage (#374). Only that explicit flag qualifies --
    ``expiration_count == 0`` alone is not proof, and a symbol whose every chain
    fetch failed raises in the service and lands in ``failed``. A real
    duplicate is a non-empty chain the store declined to persist again.
    Expirations that failed inside an otherwise-captured chain are summed into
    ``partial_expirations_failed`` so a creeping outage shows in the log.

    ``rate_limit_count`` reads the gateway's running 429 counter (#373). The
    service folds a 429 into a generic error, so the counter's movement across
    a symbol is what marks that symbol's failure as rate-limited:
    ``rate_limited_failures`` lists those symbols and ``rate_limited_calls`` is
    the night's total 429s, retried ones included.
    """
    budget = (_env_float(OPTIONS_CAPTURE_BUDGET_SECONDS_ENV,
                         DEFAULT_OPTIONS_CAPTURE_BUDGET_SECONDS)
              if budget_seconds is None else budget_seconds)
    print(f"Capturing options chains for {len(symbols)} symbols, budget {budget:.0f}s...")

    count_429 = rate_limit_count or (lambda: 0)
    calls_429_before = count_429()
    started = clock()
    captured = duplicate = no_options = failed = attempted = 0
    partial_expirations_failed = 0
    failures = []
    rate_limited_failures = []
    no_options_symbols = []
    partial_symbols = []
    for sym in symbols:
        if clock() - started >= budget:
            break
        attempted += 1
        symbol_429_before = count_429()
        try:
            chain = options.get_full_options_chain(sym, max_expirations=6)
            missed = chain.get("failed_expirations") or 0
            if missed:
                partial_expirations_failed += missed
                partial_symbols.append(sym)
            if chain.get("persisted"):
                captured += 1
                note = f" ({missed} expiration fetch(es) failed)" if missed else ""
            elif chain.get("no_listed_options"):
                no_options += 1
                no_options_symbols.append(sym)
                note = " (no listed options)"
            else:
                duplicate += 1
                note = " (not persisted — duplicate)"
            print(f"  {sym}: {chain.get('expiration_count', 0)} expirations, "
                  f"{chain.get('total_contracts', 0)} contracts{note}")
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not kill the job
            failed += 1
            failures.append(sym)
            if count_429() > symbol_429_before:
                rate_limited_failures.append(sym)
            print(f"  {sym}: options chain capture failed: {exc}")

    summary = {
        "requested": len(symbols), "attempted": attempted, "captured": captured,
        "duplicate": duplicate, "no_options": no_options,
        "no_options_symbols": no_options_symbols,
        "partial_expirations_failed": partial_expirations_failed,
        "partial_symbols": partial_symbols,
        "failed": failed, "failures": failures,
        "rate_limited_failures": rate_limited_failures,
        "rate_limited_calls": count_429() - calls_429_before,
        "skipped": len(symbols) - attempted,
        "elapsed_seconds": round(clock() - started, 1), "budget_seconds": budget,
        "budget_exhausted": attempted < len(symbols),
    }
    print(f"  captured {captured}, duplicate {duplicate}, no_options {no_options}, "
          f"failed {failed}, partial {len(partial_symbols)} "
          f"({partial_expirations_failed} expirations), "
          f"rate-limited calls {summary['rate_limited_calls']}, "
          f"skipped {summary['skipped']} in {summary['elapsed_seconds']}s"
          + (" (budget exhausted)" if summary["budget_exhausted"] else ""))
    return summary


def record_gamma_and_gex(symbols, options, budget_seconds=None,
                         clock=time.monotonic, today=None, skip=()) -> dict:
    """Persist the daily gamma-wall and GEX rows for the tracked universe.

    Both rows are computed from the full chain the capture step persisted a
    moment ago (``OptionsService.record_gex_from_snapshot``), so this step makes
    no Yahoo calls at all. It used to call ``get_delta_adjusted_oi`` and
    ``get_gex_profile``, each fetching the chain live again -- roughly doubling
    the night's Yahoo traffic, which is what tipped it into 429s (#373). A
    symbol with no full snapshot from today returns ``None`` and is counted as
    ``no_snapshot``: there is nothing to compute from, and fetching it live
    would bring the 429s back.

    The walk is still budgeted and its start still rotates with the date, so a
    budget that cannot cover the universe visits every symbol over a few
    nights. ``skip`` names symbols the capture found no listed options for.
    Never raises.
    """
    budget = (_env_float(GEX_BUDGET_SECONDS_ENV, DEFAULT_GEX_BUDGET_SECONDS)
              if budget_seconds is None else budget_seconds)
    skip = set(skip)
    ordered = [sym for sym in symbols if sym not in skip]
    if ordered:
        shift = (today or date.today()).toordinal() % len(ordered)
        ordered = ordered[shift:] + ordered[:shift]
    print(f"Recording gamma wall / GEX for {len(ordered)} symbols from today's "
          f"snapshots, budget {budget:.0f}s...")

    started = clock()
    recorded = failed = attempted = no_snapshot = 0
    for sym in ordered:
        if clock() - started >= budget:
            break
        attempted += 1
        try:
            if options.record_gex_from_snapshot(sym) is None:
                no_snapshot += 1
                print(f"  {sym}: no full snapshot from today; gamma/GEX not recorded")
            else:
                recorded += 1
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not kill the step
            failed += 1
            print(f"  {sym}: gamma/GEX record failed: {exc}")

    summary = {
        "requested": len(ordered), "attempted": attempted, "recorded": recorded,
        "no_snapshot": no_snapshot,
        "failed": failed, "skipped": len(ordered) - attempted,
        "elapsed_seconds": round(clock() - started, 1), "budget_seconds": budget,
        "budget_exhausted": attempted < len(ordered),
    }
    print(f"  recorded {recorded}, no_snapshot {no_snapshot}, failed {failed}, "
          f"skipped {summary['skipped']} "
          f"in {summary['elapsed_seconds']}s"
          + (" (budget exhausted)" if summary["budget_exhausted"] else ""))
    return summary


def check_capture_health(universe_size, capture, gex, counts, notifier,
                         coverage_floor=None, failure_ceiling=None,
                         no_options_ceiling=None) -> list:
    """Alarm when the datasets that landed fall short of what was attempted.

    ``counts`` is what the *database* holds for today (``capture_counts``), not
    what the loop believes it did -- a loop can report success against a
    transaction that never committed. Three independent triggers: chain
    coverage of the universe, the failure rate of a step's attempts, and an
    exhausted capture budget (unlike the GEX step, the capture is meant to
    finish every night). When more than half the failed captures were 429s the
    failure line names Yahoo rate-limiting instead of a generic gap (#373). Returns the list of problems; sends one alert if any.

    Coverage is measured against the symbols that *have* listed options:
    ``universe_size`` minus the capture's ``no_options``. A no-options symbol
    can never land a chain, so counting it would hold coverage under the floor
    every night (#374). A fourth trigger keeps that exclusion honest -- if more
    than ``no_options_ceiling`` of the universe reports no options, that is far
    more likely Yahoo returning empty expirations than ~everything delisting.
    """
    floor = _env_float(CAPTURE_COVERAGE_FLOOR_ENV, DEFAULT_CAPTURE_COVERAGE_FLOOR) \
        if coverage_floor is None else coverage_floor
    ceiling = _env_float(CAPTURE_FAILURE_CEILING_ENV, DEFAULT_CAPTURE_FAILURE_CEILING) \
        if failure_ceiling is None else failure_ceiling
    no_options_cap = _env_float(CAPTURE_NO_OPTIONS_CEILING_ENV,
                                DEFAULT_CAPTURE_NO_OPTIONS_CEILING) \
        if no_options_ceiling is None else no_options_ceiling

    no_options = (capture or {}).get("no_options", 0)
    optionable = universe_size - no_options
    problems = []
    if optionable > 0:
        coverage = counts["chains"] / optionable
        if coverage < floor:
            problems.append(
                f"Options chains: {counts['chains']} of {optionable} optionable symbols "
                f"landed today ({coverage:.0%}, floor {floor:.0%}; "
                f"{no_options} with no listed options excluded)."
            )
    if universe_size and no_options / universe_size > no_options_cap:
        problems.append(
            f"Options capture: {no_options} of {universe_size} symbols returned no "
            f"options chain (ceiling {no_options_cap:.0%}), e.g. "
            + ", ".join(capture.get("no_options_symbols", [])[:5]) + "."
        )
    if capture and capture["attempted"] and capture["failed"] / capture["attempted"] > ceiling:
        limited = capture.get("rate_limited_failures") or []
        if len(limited) * 2 > capture["failed"]:
            # Mostly 429s: say so, because the fix is spacing the calls out
            # (schedule, budget), not chasing the symbols (#373).
            problems.append(
                f"Options capture: Yahoo rate-limited {len(limited)} symbols "
                f"({capture['failed']} of {capture['attempted']} attempts failed, "
                f"ceiling {ceiling:.0%}; {capture.get('rate_limited_calls', 0)} "
                f"429 responses), e.g. " + ", ".join(limited[:5]) + "."
            )
        else:
            problems.append(
                f"Options capture: {capture['failed']} of {capture['attempted']} "
                f"attempts failed (ceiling {ceiling:.0%}), e.g. "
                + ", ".join(capture["failures"][:5]) + "."
            )
    if capture and capture["budget_exhausted"]:
        problems.append(
            f"Options capture hit its {capture['budget_seconds']:.0f}s budget with "
            f"{capture['skipped']} symbol(s) not attempted -- raise "
            f"`{OPTIONS_CAPTURE_BUDGET_SECONDS_ENV}`."
        )
    if gex and gex["attempted"] and gex["failed"] / gex["attempted"] > ceiling:
        problems.append(
            f"Gamma/GEX record: {gex['failed']} of {gex['attempted']} attempts "
            f"failed (ceiling {ceiling:.0%})."
        )

    print(f"Capture health: chains {counts['chains']}/{optionable} "
          f"({no_options} no-options), "
          f"gamma_wall {counts['gamma_wall']}, gex {counts['gex']}, "
          f"{len(problems)} problem(s).")
    if problems:
        for line in problems:
            print(f"ERROR: {line}", file=sys.stderr)
        try:
            notifier.send_capture_gap_alert(problems)
        except Exception as exc:  # noqa: BLE001 — a dead webhook must not kill the job
            print(f"  (failed to send the capture-gap alert: {exc})", file=sys.stderr)
    return problems


def run_capture_tail(symbols, services, notifier, job_started,
                     clock=time.monotonic) -> None:
    """Options capture, gamma/GEX from the captured snapshot, then the health check.

    Never raises.

    The same outer-guard role ``run_fundamentals_warming`` plays for the last
    step: notifications already went out before this, so nothing here may turn
    the run into a failure.
    """
    capture = gex = None
    try:
        capture = capture_options_chains(
            symbols, services.options,
            budget_seconds=_remaining_budget(
                _env_float(OPTIONS_CAPTURE_BUDGET_SECONDS_ENV,
                           DEFAULT_OPTIONS_CAPTURE_BUDGET_SECONDS),
                job_started, clock),
            rate_limit_count=lambda: services.yfinance_gateway.rate_limited,
        )
        gex = record_gamma_and_gex(
            symbols, services.options,
            budget_seconds=_remaining_budget(
                _env_float(GEX_BUDGET_SECONDS_ENV, DEFAULT_GEX_BUDGET_SECONDS),
                job_started, clock),
            skip=capture["no_options_symbols"],
        )
        counts = services.options.capture_counts(market_date().isoformat())
        check_capture_health(len(symbols), capture, gex, counts, notifier)
    except Exception as exc:  # noqa: BLE001 — the tail must not fail the job
        print(f"ERROR: options capture tail failed: {exc}", file=sys.stderr)


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


def alert_if_news_stale(sentiment, notifier, max_age_hours=None) -> bool:
    """Alarm when no news has been collected lately. Never raises.

    The news collector is a separate Job; if it was never created, or its
    scheduler is gone, nothing inside it can say so (#275). This runs in the
    report Job, which does run, and reads only what landed in the database.
    """
    ceiling = _env_float(NEWS_STALE_MAX_AGE_HOURS_ENV, DEFAULT_NEWS_STALE_MAX_AGE_HOURS) \
        if max_age_hours is None else max_age_hours
    try:
        age = sentiment.news_freshness()["age_hours"]
        if age is not None and age <= ceiling:
            return False
        print(f"ERROR: news collection is stale (age {age}h, ceiling {ceiling:.0f}h).",
              file=sys.stderr)
        notifier.send_news_stale_alert(age, ceiling)
        return True
    except Exception as exc:  # noqa: BLE001 — a check on the checker must not fail the job
        print(f"ERROR: news staleness check failed: {exc}", file=sys.stderr)
        return False


def run_fundamentals_warming(symbols, fundamentals, notifier,
                             budget_seconds=None) -> None:
    """Warm the cache, then alarm if it is still stale. Never raises.

    The per-symbol guard inside ``warm_fundamentals_cache`` is not sufficient
    on its own — a failure in the freshness query, the service registry, or the
    database connection happens outside any per-symbol block. This runs last in
    the job, so letting anything here propagate would mark a run that already
    sent its notifications and captured its options chains as failed.
    """
    try:
        warm_fundamentals_cache(symbols, fundamentals, budget_seconds=budget_seconds)
        # Re-read after warming: the alarm should describe the state the run
        # actually left behind, not the one it started from.
        alert_if_fundamentals_stale(fundamentals.cache_freshness(symbols), notifier)
    except Exception as exc:  # noqa: BLE001 — the last step must not fail the job
        print(f"ERROR: fundamentals warming pass failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    # Make sure the schema is right (creates tables, or verifies them
    # where Flyway owns the DDL -- see QUANTCORE_SCHEMA_MODE).
    ensure_schema()

    job_started = time.monotonic()

    # Closed-market days are skipped wholesale, notifications included: no price
    # moved, so an alert would repeat yesterday's and a capture would duplicate it.
    if not is_trading_day():
        print(f"{market_date():%Y-%m-%d} is not an NYSE trading day; nothing to do.")
        sys.exit(0)

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

    run_capture_tail(capture_symbols, get_services(), notifier, job_started)

    alert_if_news_stale(get_services().sentiment, notifier)

    # Last, and budgeted: the same universe the options capture just walked is
    # the one the fundamentals views read, so warm exactly that.
    run_fundamentals_warming(
        capture_symbols, get_services().fundamentals, notifier,
        budget_seconds=_remaining_budget(
            _warm_budget_seconds(None), job_started),
    )
