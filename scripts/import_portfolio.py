#!/usr/bin/env python3
"""Import a portfolio CSV into the DB-backed positions table for one owner.

Phase 1 Step 6 (docs/proposals/phase1-migration-plan.md). Full-sync/replace
semantics: every existing position for the given owner is deleted and replaced
with the rows in the CSV, in a single transaction.

Usage:
    python scripts/import_portfolio.py --csv portfolio.csv --owner john

By default the script refuses to run against the production database recorded
in .env (the prod-DSN guard) — develop and validate against the test DB
(QUANTCORE_TEST_DB_DSN exported as QUANTCORE_DB_DSN). The one-time production
import is a deliberate, user-initiated step: pass --allow-prod to permit it.
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
    parser.add_argument("--csv", required=True, help="Path to the portfolio CSV to import.")
    parser.add_argument("--owner", required=True, help="Owner these positions belong to (e.g. john).")
    parser.add_argument(
        "--allow-prod",
        action="store_true",
        help="Permit running against the production DB in .env (default: refuse).",
    )
    args = parser.parse_args()

    if not args.allow_prod:
        from quantcore.db_safety import assert_not_production
        assert_not_production()

    from quantcore.services.registry import get_services

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 1

    count = get_services().portfolio.import_csv(str(csv_path), args.owner)
    print(f"Imported {count} position(s) for owner '{args.owner}' from {csv_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
