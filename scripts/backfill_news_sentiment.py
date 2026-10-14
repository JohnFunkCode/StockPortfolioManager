#!/usr/bin/env python3
"""FinBERT-score the news articles left unscored by an outage (#375).

The nightly news Job scores the newest unscored articles first, up to its
limit, so a backlog left by days without FinBERT is never reached once fresh
articles keep arriving. This drains it oldest first, in batches, from a date.

Usage:
    python scripts/backfill_news_sentiment.py --since 2026-10-02 --dry-run
    python scripts/backfill_news_sentiment.py --since 2026-10-02 [--batch 500]

It needs FinBERT, so on a deployed project it runs on the news image, as a
one-off execution of the quantcore-news Job (see readme.md). It stops with exit
code 1 when a batch scores nothing while articles remain: FinBERT failed to
load, or the rows have no text. A broken model can't loop forever.

Prints counts only -- never article text or the DSN. Like the other write
scripts, it refuses the production database in .env unless given --allow-prod
(a Cloud Run Job has no .env, so the check passes there).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", required=True, type=date.fromisoformat,
                        help="First fetched_at day to re-score (YYYY-MM-DD).")
    parser.add_argument("--batch", type=int, default=500,
                        help="Articles per scoring batch (default 500).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the unscored counts per day and write nothing.")
    parser.add_argument("--allow-prod", action="store_true",
                        help="Permit running against the production DB in .env.")
    args = parser.parse_args(argv)
    if args.batch < 1:
        parser.error("--batch must be at least 1")
    return args


def backfill(sentiment, since: str, batch: int) -> tuple[int, int]:
    """Score oldest first until nothing is left. Returns (scored, remaining)."""
    scored = 0
    while True:
        remaining = sum(sentiment.count_unscored_by_day(since=since).values())
        if remaining == 0:
            return scored, 0
        n = sentiment.score_unscored(limit=batch, since=since, oldest_first=True)
        print(f"batch: scored {n}, {remaining - n} unscored remain", flush=True)
        if n == 0:
            return scored, remaining
        scored += n


def main(argv=None) -> int:
    args = _parse_args(argv)
    if not args.allow_prod:
        from quantcore.db_safety import assert_not_production
        assert_not_production()

    from quantcore.db import ensure_schema
    from quantcore.services.registry import get_services

    ensure_schema()
    sentiment = get_services().sentiment
    since = args.since.isoformat()

    if args.dry_run:
        by_day = sentiment.count_unscored_by_day(since=since)
        for day, n in by_day.items():
            print(f"  {day}: {n}")
        print(f"{sum(by_day.values())} unscored article(s) fetched since {since} (dry run)")
        return 0

    scored, remaining = backfill(sentiment, since, args.batch)
    print(f"scored {scored} article(s) fetched since {since}; {remaining} remain unscored")
    if remaining:
        print("a batch scored nothing while articles remain: FinBERT failed to load "
              "(see the warning above) or those rows have no text to score", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
