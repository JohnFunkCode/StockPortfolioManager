#!/usr/bin/env python3
"""Write the cash ledger rows that existing lots, sales and paper books lack (#431).

A book's cash ledger (`cash_entries`) has a buy row for each lot and a sell
row for each sale, written in the same transaction as the lot or sale. Lots
and sales recorded before the ledger existed have none, and a paper book made
before it has no opening balance. This script writes the missing rows.

Usage:
    python scripts/backfill_cash_entries.py                  # dry run, every owner
    python scripts/backfill_cash_entries.py --apply
    python scripts/backfill_cash_entries.py --owner john --apply

Dry run by default. It runs the same statements and rolls them back, so the
counts it prints are exactly what --apply would write. It only adds a row that
is missing and never changes one that is there, so running it twice is safe.
It makes no network call: a non-USD lot with no stored FX rate gets a row with
no amount, which the ledger flags. Run scripts/backfill_fx_rates.py first to
keep those to a minimum; a rate filled later gives the row its amount.

It does not set a real book's opening balance. The owner enters that on the
Portfolio page, and until they do the real book shows no balance.

Like the other write scripts, it refuses to touch the production database in
.env unless given --allow-prod.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_COUNTS = ("buys", "sells", "openings")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the rows (default: dry run, print only).",
    )
    parser.add_argument(
        "--owner",
        default="",
        help="Backfill one owner (default: every owner with a book).",
    )
    parser.add_argument(
        "--allow-prod",
        action="store_true",
        help="Permit running against the production DB in .env (default: refuse).",
    )
    return parser.parse_args()


def _backfill(cash, owners: list, apply: bool) -> int:
    """Backfill each owner's books and print a line per book that had rows
    missing; return the number of rows written (or that would be)."""
    total = 0
    verb = "wrote" if apply else "would write"
    for owner in owners:
        for book in cash.backfill(owner=owner, apply=apply):
            rows = sum(book[key] for key in _COUNTS)
            if not rows:
                continue
            total += rows
            print(
                f"{owner} / {book['name']} ({book['kind']} book {book['book_id']}): {verb} "
                f"{book['buys']} buy, {book['sells']} sell, {book['openings']} opening balance"
            )
    return total


def main() -> int:
    args = _parse_args()

    if not args.allow_prod:
        from quantcore.db_safety import assert_not_production
        assert_not_production()

    from quantcore.services.registry import get_services

    cash = get_services().cash
    owners = [args.owner] if args.owner else cash.list_owners()
    total = _backfill(cash, owners, args.apply)

    print(
        f"\n{len(owners)} owner(s) checked, {total} cash row(s) "
        f"{'written' if args.apply else 'to write'}."
    )
    if total and not args.apply:
        print("Dry run — re-run with --apply to write these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
