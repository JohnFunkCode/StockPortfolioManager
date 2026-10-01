#!/usr/bin/env python3
"""The nightly news collector: fetch headlines and FinBERT-score them (issue #68).

Runs as the ``quantcore-news`` Cloud Run Job (``Dockerfile.news``) and locally
as ``python news_job.py``. It is a job of its own, not a step of ``main.py``,
because scoring needs torch/transformers (``requirements-ml.txt``) and the
report image is deliberately lean. Keeping it separate also means a model
download or a slow scoring pass can never delay a notification.

Universe: the shared watchlist plus every owner's positions -- the same
"tracked" roster the fundamentals warmer uses. Closed-market days are skipped,
matching ``main.py``. Each symbol is collected in its own try/except and the
whole walk shares one wall-clock budget; scoring runs once at the end so a
budget-truncated collection still gets its new articles scored.

Failures are swallowed per symbol and then made loud: ``check_news_health``
raises one Discord alarm when too many symbols failed, the budget ran out, or
FinBERT scored nothing despite unscored articles waiting.
"""
from __future__ import annotations

import math
import os
import sys
import time

from quantcore.analytics.market_time import is_trading_day, market_date
from quantcore.db import ensure_schema
from quantcore.services.registry import get_services

TASK_TIMEOUT_ENV = "NEWS_TASK_TIMEOUT_SECONDS"
BUDGET_ENV = "NEWS_COLLECT_BUDGET_SECONDS"
FAILURE_CEILING_ENV = "NEWS_FAILURE_CEILING"
DEFAULT_TASK_TIMEOUT_SECONDS = 1800.0
DEFAULT_BUDGET_SECONDS = 900.0
DEADLINE_MARGIN_SECONDS = 60.0
DEFAULT_FAILURE_CEILING = 0.50
SCORE_LIMIT = 500


def _env_float(name: str, default: float) -> float:
    """Float from the environment; a bad value warns on stderr and uses the default."""
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


def _budget_seconds(budget_seconds=None, job_started=None, clock=time.monotonic) -> float:
    """The collection budget, clamped to what is left of the task deadline."""
    requested = (_env_float(BUDGET_ENV, DEFAULT_BUDGET_SECONDS)
                 if budget_seconds is None else budget_seconds)
    task_timeout = _env_float(TASK_TIMEOUT_ENV, DEFAULT_TASK_TIMEOUT_SECONDS)
    elapsed = 0.0 if job_started is None else clock() - job_started
    return max(1.0, min(requested, task_timeout - DEADLINE_MARGIN_SECONDS - elapsed))


def collect_news(symbols, sentiment, budget_seconds=None, clock=time.monotonic) -> dict:
    """Collect (unscored) news per symbol within a budget, then score once."""
    budget = _budget_seconds(budget_seconds)
    started = clock()
    summary = {"requested": len(symbols), "attempted": 0, "collected": 0,
               "new_articles": 0, "failed": 0, "failures": [], "scored": 0,
               "budget_exhausted": False, "budget_seconds": budget}
    for sym in symbols:
        if clock() - started >= budget:
            summary["budget_exhausted"] = True
            break
        summary["attempted"] += 1
        try:
            result = sentiment.collect_news(sym, score=False)
            summary["collected"] += 1
            summary["new_articles"] += int(result.get("new_articles") or 0)
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not stop the walk
            summary["failed"] += 1
            summary["failures"].append(f"{sym}: {type(exc).__name__}")
            print(f"ERROR: news collection failed for {sym}: {exc}", file=sys.stderr)
    summary["elapsed_seconds"] = round(clock() - started, 1)
    try:
        summary["scored"] = sentiment.score_unscored(limit=SCORE_LIMIT)
    except Exception as exc:  # noqa: BLE001 — scoring is best-effort; health check reports it
        summary["score_error"] = type(exc).__name__
        print(f"ERROR: news scoring failed: {exc}", file=sys.stderr)
    return summary


def check_news_health(summary, notifier, failure_ceiling=None) -> list[str]:
    """Alarm on a high failure rate, an exhausted budget, or a scoring failure."""
    ceiling = (_env_float(FAILURE_CEILING_ENV, DEFAULT_FAILURE_CEILING)
               if failure_ceiling is None else failure_ceiling)
    problems: list[str] = []
    if summary["attempted"] and summary["failed"] / summary["attempted"] > ceiling:
        problems.append(
            f"News collection: {summary['failed']} of {summary['attempted']} "
            f"symbols failed (ceiling {ceiling:.0%}).")
    if summary["budget_exhausted"]:
        problems.append(
            f"News collection ran out of its {summary['budget_seconds']:.0f}s budget "
            f"after {summary['attempted']} of {summary['requested']} symbols.")
    if summary.get("score_error"):
        problems.append(f"FinBERT scoring raised {summary['score_error']}.")
    print(f"News health: {summary['collected']}/{summary['requested']} collected, "
          f"{summary['new_articles']} new, {summary['scored']} scored, "
          f"{len(problems)} problem(s).")
    if problems:
        for line in problems:
            print(f"ERROR: {line}", file=sys.stderr)
        try:
            notifier.send_news_gap_alert(problems)
        except Exception as exc:  # noqa: BLE001 — a dead webhook must not kill the job
            print(f"  (failed to send the news-gap alert: {exc})", file=sys.stderr)
    return problems


def tracked_universe(services) -> list[str]:
    """Watchlist plus every owner's positions, de-duplicated, order preserved."""
    seen: list[str] = []
    for sym in list(services.watchlist.symbols()) + list(services.portfolio.all_symbols()):
        if sym and sym not in seen:
            seen.append(sym)
    return seen


def main() -> int:
    ensure_schema()
    job_started = time.monotonic()
    if not is_trading_day():
        print(f"{market_date():%Y-%m-%d} is not an NYSE trading day; nothing to do.")
        return 0
    from notifier import Notifier  # deferred: pulls the notification stack
    services = get_services()
    symbols = tracked_universe(services)
    if not symbols:
        print("ERROR: tracked universe is empty; nothing to collect.", file=sys.stderr)
        return 0
    summary = collect_news(
        symbols, services.sentiment,
        budget_seconds=_budget_seconds(job_started=job_started))
    check_news_health(summary, Notifier(None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
