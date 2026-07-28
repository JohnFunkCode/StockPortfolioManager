#!/usr/bin/env python3
"""Import watchlist.yaml into the DB-backed watchlist table.

Issue #83 (docs/proposals/watchlist-db-plan.md). Companion to
scripts/import_portfolio.py, with the same full-sync/replace semantics: every
existing watchlist row is deleted and replaced with the entries in the YAML, in
a single transaction.

Usage:
    python scripts/import_watchlist.py --yaml watchlist.yaml

The watchlist is global — there is no --owner, because there is no owner to
scope it to. After this seeding run, entries are added and removed through the
UI/API; the YAML stays as the import format and the seed of record, not as
something the running system reads.

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


def _warn_if_shrunk(before: int, after: int) -> None:
    """Shout if this import gutted the list.

    A full-sync replace against a truncated or wrong-path YAML silently empties
    the capture universe: the daily options-chain snapshot then runs on
    portfolio symbols only, and nobody notices until a history gap shows up
    weeks later. This script is the only thing between that YAML and prod, so
    the shrink is reported loudly rather than left in an exit code.
    """
    if after and (before == 0 or after * 2 >= before):
        return

    reason = (
        "the watchlist is now EMPTY"
        if after == 0
        else f"the watchlist shrank by more than half ({before} -> {after})"
    )
    banner = "!" * 72
    print(
        f"\n{banner}\n"
        f"WARNING: {reason}.\n"
        "Import is a full-sync replace, so whatever was in the table is gone.\n"
        "If that was not intended, re-run this script against the complete\n"
        "watchlist YAML now — the daily options-chain capture loop reads this\n"
        "table, and an empty list means it captures portfolio symbols only.\n"
        f"{banner}\n",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yaml",
        default=str(ROOT / "watchlist.yaml"),
        help="Path to the watchlist YAML to import (default: ./watchlist.yaml).",
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

    yaml_path = Path(args.yaml)
    if not yaml_path.exists():
        # Checked before the service call: a typo'd path would otherwise
        # truncate the table and import nothing.
        print(f"Watchlist YAML not found: {yaml_path}", file=sys.stderr)
        return 1

    from quantcore.services.registry import get_services

    watchlist = get_services().watchlist
    before = watchlist.count()
    count = watchlist.import_yaml(str(yaml_path))
    print(f"Imported {count} watchlist entr{'y' if count == 1 else 'ies'} "
          f"from {yaml_path} (was {before}).")

    _warn_if_shrunk(before, count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
