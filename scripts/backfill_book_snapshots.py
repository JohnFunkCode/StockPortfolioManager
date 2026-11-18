#!/usr/bin/env python3
"""Rebuild a book's daily value snapshots from its history (#431 step 5.2).

The daily job has written one `book_value_snapshots` row a book and session
since step 5.1 rolled out. A real book's history before that, back to the
opening balance its owner entered, has no rows: this script writes them, from
the book's lots and sales, its cash ledger and the closed daily bars stored
in `ohlcv` (it fetches only a symbol whose stored bars are behind). Each flow
is placed on the date it carries. The rebuild is the same one the daily job
runs to restate a book after a late change.

Usage:
    python scripts/backfill_book_snapshots.py                       # dry run, every real book
    python scripts/backfill_book_snapshots.py --owner john --apply
    python scripts/backfill_book_snapshots.py --owner john --book-id 7 --apply
    python scripts/backfill_book_snapshots.py --owner john --from 2026-01-02 --through 2026-06-30

Dry run by default. It runs the same writes and rolls them back, so what it
prints is exactly what --apply would do. A row that comes out the same is left
alone; one that differs is replaced and stamped `restated_at`. Running it
twice is safe.

It prints every session it could not value (a holding with no close in the
week before, or no FX rate) and every session that priced a holding from an
earlier close, so a hole in the history is named rather than found later.
A book with no opening balance is skipped: its owner enters one first.

Like the other write scripts, it refuses to touch the production database in
.env unless given --allow-prod.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _date(text: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}") from exc


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Write the rows (default: dry run, print only).")
    parser.add_argument("--owner", default="",
                        help="One owner (default: every owner with a book).")
    parser.add_argument("--book-id", type=int, default=None,
                        help="One of the owner's books (default: the real book). Needs --owner.")
    parser.add_argument("--from", dest="start", type=_date, default=None,
                        help="First session to rebuild (default: the opening balance's date).")
    parser.add_argument("--through", type=_date, default=None,
                        help="Last session to rebuild (default: the latest completed session).")
    parser.add_argument("--allow-prod", action="store_true",
                        help="Permit running against the production DB in .env (default: refuse).")
    args = parser.parse_args(argv)
    if args.book_id is not None and not args.owner:
        parser.error("--book-id needs --owner")
    return args


def _dates(days) -> str:
    return ", ".join(d.isoformat() for d in days) or "none"


def _report(label: str, result: dict, apply: bool) -> int:
    """Print one book's result. Returns the rows written or changed (or that
    would be)."""
    if result.get("start") is None:
        print(f"{label}: no opening balance, skipped")
        return 0
    verb = "" if apply else "would be "
    print(f"{label}: {result['sessions']} session(s) from {result['start']}: "
          f"{result['inserted']} {verb}added, {result['restated']} {verb}restated, "
          f"{result['unchanged']} unchanged, {result['deleted']} {verb}removed")
    print(f"  no value: {_dates(result['unvalued'])}")
    print(f"  priced from an earlier close: {_dates(result['stale'])}")
    if result["undated_lots"]:
        print(f"  {result['undated_lots']} lot(s) with no trade date, or closed with no "
              "sale date, left out of every session")
    if result["cleared"]:
        print(f"  the book's restatement mark is {'cleared' if apply else 'covered'}")
    return result["inserted"] + result["restated"] + result["deleted"]


def _backfill(performance, targets, args, through: datetime.date) -> int:
    total = 0
    for owner, book_id in targets:
        label = f"{owner} / " + (f"book {book_id}" if book_id is not None else "real book")
        result = performance.backfill_book(owner=owner, book_id=book_id, start=args.start,
                                           through=through, apply=args.apply)
        total += _report(label, result, args.apply)
    return total


def main(argv=None) -> int:
    args = _parse_args(argv)

    if not args.allow_prod:
        from quantcore.db_safety import assert_not_production
        assert_not_production()

    from quantcore.analytics.market_time import latest_completed_session
    from quantcore.services.registry import get_services

    services = get_services()
    owners = [args.owner] if args.owner else services.cash.list_owners()
    targets = [(owner, args.book_id) for owner in owners]
    through = args.through or latest_completed_session()
    total = _backfill(services.performance, targets, args, through)

    print(f"\n{len(targets)} book(s) checked through {through}, {total} row(s) "
          f"{'written' if args.apply else 'to write'}.")
    if total and not args.apply:
        print("Dry run — re-run with --apply to write these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
