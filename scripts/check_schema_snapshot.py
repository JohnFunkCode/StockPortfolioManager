"""CI schema snapshot diff — guard what init_schema() actually produces (issue #165).

``quantcore.db.init_schema()`` is one of two systems that can create a deployed
database's schema (Flyway's ``db/migrations/V*.sql`` is the other), and until now
nothing checked that a change to ``init_schema()``'s DDL was intentional. This script
runs ``init_schema()`` against a disposable scratch database
(:func:`quantcore.schema_introspect.snapshot_from_dsn`), and diffs the result against
the committed snapshot at ``db/schema_snapshot.json``
(:func:`quantcore.schema_introspect.diff_schemas`).

Adding, dropping, or changing a table/column/index/constraint in ``init_schema()``
fails CI until the snapshot is regenerated
(``python scripts/check_schema_snapshot.py --update``) and committed in the same PR —
making every change to what the code expects the schema to look like a reviewable
diff, the same way ``scripts/check_openapi_snapshot.py`` does for the REST surface.

This is evidence, not enforcement: a match only proves ``init_schema()``'s own DDL is
internally stable PR to PR. It says nothing about whether Flyway's migrations produce
the same shape (that's PR 2's parity test) or whether a live deployment matches either
(that's ``scripts/schema_check.py``).

Needs a reachable Postgres server with CREATEDB on the connecting role — CI's `gate`
job Postgres service qualifies; so does local Postgres via
``.claude/with-test-db.sh``. Creates and drops a scratch database each run; never
touches the target database itself.

Run (check):  ``QUANTCORE_DB_DSN=... python scripts/check_schema_snapshot.py``
Run (update): ``QUANTCORE_DB_DSN=... python scripts/check_schema_snapshot.py --update``
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent / "db" / "schema_snapshot.json"


def current_schema() -> dict:
    # Importing quantcore.db triggers its load_dotenv(), so QUANTCORE_DB_DSN
    # from .env is available even when the caller didn't export it manually.
    import quantcore.db  # noqa: F401  (import side effect: load_dotenv)
    from quantcore.schema_introspect import snapshot_from_dsn

    dsn = os.environ.get("QUANTCORE_DB_DSN")
    if not dsn:
        print("ERROR: QUANTCORE_DB_DSN not set (directly or via .env).", file=sys.stderr)
        sys.exit(2)
    return snapshot_from_dsn(dsn)


def _dump(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    schema = current_schema()
    rendered = _dump(schema)
    table_count = len(schema.get("tables", {}))

    if "--update" in argv:
        SNAPSHOT.write_text(rendered)
        print(f"Wrote {SNAPSHOT.relative_to(SNAPSHOT.parents[1])} ({table_count} tables).")
        return 0

    if not SNAPSHOT.exists():
        print(f"ERROR: snapshot {SNAPSHOT} missing — run with --update and commit it.")
        return 2

    from quantcore.schema_introspect import diff_schemas

    expected = json.loads(SNAPSHOT.read_text())
    diff = diff_schemas(expected, schema)
    if not diff:
        print(f"schema snapshot up to date ({table_count} tables).")
        return 0

    print("Schema snapshot DRIFT vs db/schema_snapshot.json:")
    for line in diff:
        print(f"  {line}")
    print("\nIf intentional: python scripts/check_schema_snapshot.py --update  (and commit)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
