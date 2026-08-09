"""Schema evidence command — what a deployed database actually contains (issue #165).

``flyway info`` only reports Flyway's own changelog, and because ``init_schema()``
(``quantcore/db.py``) runs its full idempotent DDL on every application startup, a
deployed database usually reaches the right *shape* long before Flyway ever touches it
— "``flyway info`` is not evidence of what a deployed database actually contains" (see
``CLAUDE.md`` Migrations). This script is that evidence: it connects read-only, compares
the live ``public`` schema against the committed ``db/schema_snapshot.json`` (the same
comparison ``scripts/check_schema_snapshot.py`` runs in CI, just against a real
database instead of a scratch one), and separately prints the Flyway changelog labelled
for what it is — a changelog, not a schema description.

Opens no write transaction, takes no DDL lock, creates nothing. Safe to run against
prod at any time.

Usage:
    python scripts/schema_check.py            # test (default — the safe target)
    python scripts/schema_check.py --test
    python scripts/schema_check.py --prod

Exit codes: 0 clean or extras-only, 1 if the SCHEMA block reports any MISSING or
MISMATCH line.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg2

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO_ROOT / "db" / "schema_snapshot.json"

# Run from the repo root without PYTHONPATH=. -- this is an operator command
# typed by hand, and `ModuleNotFoundError: No module named 'quantcore'` is a
# poor first impression. Same convention as scripts/import_watchlist.py.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _dsn_for(target: str) -> str:
    # Mirrors scripts/flyway.sh's mapping exactly, so the two agree on what
    # --test/--prod mean: test -> QUANTCORE_TEST_DB_DSN, prod -> QUANTCORE_DB_DSN.
    import quantcore.db  # noqa: F401  (import side effect: load_dotenv from .env)
    import os

    var = "QUANTCORE_DB_DSN" if target == "prod" else "QUANTCORE_TEST_DB_DSN"
    dsn = os.environ.get(var)
    if not dsn:
        print(f"ERROR: {var} not found in .env", file=sys.stderr)
        sys.exit(1)
    return dsn


def _echo_target(target: str, dsn: str) -> None:
    parts = urlsplit(dsn)
    hostport = parts.hostname or ""
    if parts.port:
        hostport += f":{parts.port}"
    database = parts.path.lstrip("/")
    print(f"target: {target}  {hostport}/{database}  (user: {parts.username})")


def _schema_block(dsn: str) -> tuple[list[str], int]:
    """SCHEMA evidence: live objects vs the committed snapshot. Returns (lines, table_count)."""
    from quantcore.schema_introspect import describe_schema, diff_schemas

    if not SNAPSHOT.exists():
        return ([f"ERROR: {SNAPSHOT} missing — run scripts/check_schema_snapshot.py --update"], 0)

    expected = json.loads(SNAPSHOT.read_text())
    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(readonly=True)
        actual = describe_schema(conn)
    finally:
        conn.close()

    diff = diff_schemas(expected, actual)
    missing = sum(1 for line in diff if line.startswith("MISSING"))
    extra = sum(1 for line in diff if line.startswith("EXTRA") and line.split()[1] == "table")
    table_count = len(actual.get("tables", {}))

    lines = [f"{table_count} tables, {missing} missing, {extra} extra"]
    lines.extend(diff)
    return lines, table_count


def _flyway_block(dsn: str) -> list[str]:
    """FLYWAY LEDGER: the changelog only. Not evidence of schema state — see module docstring."""
    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.flyway_schema_history')")
        if cur.fetchone()[0] is None:
            return ["no flyway_schema_history table -- Flyway has never run against this database"]
        cur.execute(
            """
            SELECT version, description, installed_on
            FROM flyway_schema_history
            WHERE success = true
            ORDER BY installed_rank DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row is None:
            return ["flyway_schema_history exists but has no successful entries"]
        version, description, installed_on = row
        return [f"applied through V{version}__{description}  ({installed_on:%Y-%m-%d})"]
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    target = "test"
    for arg in argv:
        if arg == "--test":
            target = "test"
        elif arg == "--prod":
            target = "prod"
        else:
            print(f"usage: {sys.argv[0]} [--test|--prod]", file=sys.stderr)
            return 2

    dsn = _dsn_for(target)
    _echo_target(target, dsn)
    print()

    schema_lines, _ = _schema_block(dsn)
    print("SCHEMA (evidence — live objects vs db/schema_snapshot.json)")
    for line in schema_lines:
        print(f"  {line}")
    has_failure = any(
        line.strip().startswith("MISSING") or line.strip().startswith("MISMATCH")
        for line in schema_lines
    )
    print()

    print("FLYWAY LEDGER (changelog only — NOT evidence of schema state, issue #165)")
    for line in _flyway_block(dsn):
        print(f"  {line}")

    return 1 if has_failure else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
