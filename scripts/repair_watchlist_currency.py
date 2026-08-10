#!/usr/bin/env python3
"""Re-resolve every watchlist entry's currency against the exchange.

``WatchlistService.add_entry`` looks the currency up rather than trusting the
caller, but that only governs entries added from then on. The list seeded from
watchlist.yaml carries whatever the file said, and the file was wrong: ASSA-B.ST
(Stockholm, SEK), AUTO.OL (Oslo, NOK) and NIB.F (Frankfurt, EUR) were all
declared USD. That mislabels their market caps as dollars on the watchlist
fundamentals page — a wrong number, not a missing one.

Usage:
    python scripts/repair_watchlist_currency.py                 # dry run
    python scripts/repair_watchlist_currency.py --apply
    python scripts/repair_watchlist_currency.py --symbols AUTO.OL,NIB.F --apply

Dry run by default: it prints the diff and writes nothing, because the input is
a live network lookup and a bad afternoon at Yahoo should not be able to rewrite
the whole table unattended. One request per symbol, so a full ~230-entry pass
takes a few minutes.

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the resolved currencies (default: dry run, print only).",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated subset to check (default: the whole watchlist).",
    )
    parser.add_argument(
        "--allow-prod",
        action="store_true",
        help="Permit running against the production DB in .env (default: refuse).",
    )
    args = parser.parse_args()

    if not args.allow_prod:
        from quantcore.db_safety import assert_not_production
        assert_not_production()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None

    from quantcore.services.registry import get_services

    results = get_services().watchlist.resync_currencies(
        symbols=symbols, apply=args.apply
    )
    if not results:
        print("No watchlist entries matched.", file=sys.stderr)
        return 1

    changed = [r for r in results if r["changed"]]
    unresolved = [r for r in results if r["resolved"] is None]

    for row in changed:
        verb = "updated" if row["updated"] else "would update"
        print(f"  {verb} {row['symbol']}: {row['stored']} -> {row['resolved']}")

    # Reported separately and never rewritten: a lookup that failed is not
    # evidence that the stored value is wrong.
    for row in unresolved:
        print(f"  no currency from the exchange for {row['symbol']} "
              f"(left as {row['stored']})", file=sys.stderr)

    print(
        f"\n{len(results)} checked, {len(changed)} "
        f"{'updated' if args.apply else 'to change'}, "
        f"{len(unresolved)} unresolved."
    )
    if changed and not args.apply:
        print("Dry run — re-run with --apply to write these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
