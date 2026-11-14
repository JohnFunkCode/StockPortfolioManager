#!/usr/bin/env python3
"""Fill the FX rates missing from non-USD lots and their sales (#235).

Realized P/L is reported in USD: cost basis at the rate on the lot's trade
date, proceeds at the rate on the sale date. Both rates are stored on the rows
when a trade is recorded (`positions.fx_rate_at_purchase`,
`lot_sales.fx_rate_at_sale`). Rows written before that existed have none, and
so does a trade recorded while Yahoo was unreachable. A non-USD sale without
both rates shows on the Realized view with a "no FX rate" flag and is in no
total. This script looks the missing rates up and fills them in.

Usage:
    python scripts/backfill_fx_rates.py                  # dry run, every owner
    python scripts/backfill_fx_rates.py --apply
    python scripts/backfill_fx_rates.py --owner john --apply

Dry run by default: it prints what it would write and writes nothing, because
the input is a live network lookup. It only fills a rate that is NULL, so it
never replaces one captured with the trade, and running it twice is safe. One
request per currency per owner. A date with no close within the week before it
is reported as unresolved and left NULL; a missing rate is never written as 1.0.

Real books only. Paper orders are USD-only for now.

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


def _report(kind: str, key: str, date_key: str, rows: list, apply: bool) -> tuple[int, int]:
    """Print one line per row; return (resolved, unresolved)."""
    resolved = unresolved = 0
    for row in rows:
        label = f"{kind} {row[key]} {row['symbol']} {row['currency']} {row[date_key]}"
        if row["rate"] is None:
            unresolved += 1
            print(f"  no rate for {label} (left empty)", file=sys.stderr)
            continue
        resolved += 1
        if not apply:
            verb = "would fill"
        elif row["filled"]:
            verb = "filled"
        else:
            verb = "already had a rate, left"
        print(f"  {verb} {label}: {row['rate']}")
    return resolved, unresolved


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the rates found (default: dry run, print only).",
    )
    parser.add_argument(
        "--owner",
        default="",
        help="Backfill one owner (default: every owner with a position).",
    )
    parser.add_argument(
        "--allow-prod",
        action="store_true",
        help="Permit running against the production DB in .env (default: refuse).",
    )
    return parser.parse_args()


def _backfill(portfolio, owners: list, apply: bool) -> tuple[int, int]:
    """Backfill each owner and print their rows; return (resolved, unresolved)
    over all of them. An owner with nothing missing prints nothing."""
    resolved = unresolved = 0
    for owner in owners:
        result = portfolio.backfill_fx_rates(owner, apply=apply)
        if not result["lots"] and not result["sales"]:
            continue
        print(f"{owner}:")
        for counts in (
            _report("lot", "lot_id", "trade_date", result["lots"], apply),
            _report("sale", "sale_id", "sale_trade_date", result["sales"], apply),
        ):
            resolved += counts[0]
            unresolved += counts[1]
    return resolved, unresolved


def main() -> int:
    args = _parse_args()

    if not args.allow_prod:
        from quantcore.db_safety import assert_not_production
        assert_not_production()

    from quantcore.services.registry import get_services

    portfolio = get_services().portfolio
    owners = [args.owner] if args.owner else portfolio.list_owners()
    resolved, unresolved = _backfill(portfolio, owners, args.apply)

    print(
        f"\n{len(owners)} owner(s) checked, {resolved} rate(s) "
        f"{'filled' if args.apply else 'to fill'}, {unresolved} unresolved."
    )
    if resolved and not args.apply:
        print("Dry run — re-run with --apply to write these.")
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
