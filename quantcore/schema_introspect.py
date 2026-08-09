"""Schema introspection and comparison — the evidence layer for issue #165.

Two systems create the schema of a deployed database (``quantcore.db.init_schema()``
and Flyway's ``db/migrations/V*.sql``) and neither can see the other. This module is
the comparison neither of them has: it turns a live ``public`` schema into a stable,
JSON-serializable description (:func:`describe_schema`), diffs two such descriptions
into human-readable lines (:func:`diff_schemas`), and provides a disposable database
to introspect ``init_schema()``'s own output against (:func:`scratch_database`,
:func:`snapshot_from_dsn`).

This module is infrastructure beside ``db.py``, not a service, and it is
comparison-only: it decides nothing about what to *do* with a difference. That policy
(warn vs. verify vs. hard-fail) lives in ``quantcore/db.py`` (PR 3 of
``docs/proposals/schema-ownership-plan.md``).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras

# flyway_schema_history marks a database as Flyway-managed; it is not part of
# the application schema and every deployed database that has ever run
# `flyway migrate` carries it. Ignoring it here means a database with no
# migration history at all (fresh CI, local dev) diffs identically to one
# that has been fully migrated -- decision D4 in the schema-ownership plan.
IGNORED_TABLES = frozenset({"flyway_schema_history"})


def _column_type(col: dict) -> str:
    """Compose data_type + length/precision/scale into one readable string.

    ``numeric(18,6)`` or ``character varying(8)`` is one diff line; splitting
    type/precision/scale into separate dict keys would make an intentional
    precision bump look like three unrelated differences.
    """
    data_type = col["data_type"]
    if col["character_maximum_length"] is not None:
        return f"{data_type}({col['character_maximum_length']})"
    if col["numeric_precision"] is not None and data_type in ("numeric", "decimal"):
        scale = col["numeric_scale"] if col["numeric_scale"] is not None else 0
        return f"{data_type}({col['numeric_precision']},{scale})"
    return data_type


def describe_schema(conn, *, ignore_tables: frozenset = IGNORED_TABLES) -> dict:
    """Introspect the connection's ``public`` schema into a comparable dict.

    Shape::

        {"tables": {
            "<table>": {
                "columns":     {"<col>": {"type": str, "nullable": bool, "default": str|None}},
                "indexes":     {"<index_name>": "<indexdef>"},
                "constraints": {"<constraint_name>": "<constraintdef>"},
            }
        }}

    Every level is built from a dict comprehension over a sorted query result,
    so two descriptions of the same schema are always equal regardless of the
    order Postgres happened to return rows in.
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    )
    table_names = [r["table_name"] for r in cur.fetchall() if r["table_name"] not in ignore_tables]

    tables: dict = {}
    for table in table_names:
        cur.execute(
            """
            SELECT column_name, data_type, character_maximum_length,
                   numeric_precision, numeric_scale, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY column_name
            """,
            (table,),
        )
        columns = {
            row["column_name"]: {
                "type": _column_type(row),
                "nullable": row["is_nullable"] == "YES",
                "default": row["column_default"],
            }
            for row in cur.fetchall()
        }

        cur.execute(
            "SELECT indexname, indexdef FROM pg_indexes"
            " WHERE schemaname = 'public' AND tablename = %s ORDER BY indexname",
            (table,),
        )
        indexes = {row["indexname"]: row["indexdef"] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT con.conname AS constraint_name, pg_get_constraintdef(con.oid) AS def
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            WHERE nsp.nspname = 'public' AND rel.relname = %s
            ORDER BY con.conname
            """,
            (table,),
        )
        constraints = {row["constraint_name"]: row["def"] for row in cur.fetchall()}

        tables[table] = {"columns": columns, "indexes": indexes, "constraints": constraints}

    cur.close()
    return {"tables": tables}


def diff_schemas(expected: dict, actual: dict) -> list[str]:
    """Compare two :func:`describe_schema` outputs.

    Returns sorted, human-readable lines, each prefixed ``MISSING``, ``EXTRA``,
    or ``MISMATCH`` -- empty when the schemas are identical. Table-level
    differences are reported as a single ``table`` line rather than exploding
    into per-column noise for a table that doesn't exist on one side.
    """
    lines: list[str] = []
    exp_tables = expected.get("tables", {})
    act_tables = actual.get("tables", {})

    for table in sorted(set(exp_tables) - set(act_tables)):
        lines.append(f"MISSING  table {table}")
    for table in sorted(set(act_tables) - set(exp_tables)):
        lines.append(f"EXTRA    table {table}")

    for table in sorted(set(exp_tables) & set(act_tables)):
        exp = exp_tables[table]
        act = act_tables[table]
        # Column diffs use "table.column" (matches diff_schemas' own doctest-
        # style examples); index/constraint diffs are reported "table" with
        # the object name folded into a "  " section prefix so all three read
        # consistently as "MISSING  <section> <table>.<name>".
        for key in sorted(set(exp["columns"]) - set(act["columns"])):
            lines.append(f"MISSING  column {table}.{key}")
        for key in sorted(set(act["columns"]) - set(exp["columns"])):
            lines.append(f"EXTRA    column {table}.{key}")
        for key in sorted(set(exp["columns"]) & set(act["columns"])):
            if exp["columns"][key] != act["columns"][key]:
                e, a = exp["columns"][key], act["columns"][key]
                if e["type"] != a["type"]:
                    lines.append(
                        f"MISMATCH column {table}.{key}  expected {e['type']}  actual {a['type']}"
                    )
                elif e["nullable"] != a["nullable"]:
                    lines.append(
                        f"MISMATCH column {table}.{key}  expected nullable={e['nullable']}"
                        f"  actual nullable={a['nullable']}"
                    )
                else:
                    lines.append(
                        f"MISMATCH column {table}.{key}  expected default={e['default']!r}"
                        f"  actual default={a['default']!r}"
                    )

        for key in sorted(set(exp["indexes"]) - set(act["indexes"])):
            lines.append(f"MISSING  index {table}.{key}")
        for key in sorted(set(act["indexes"]) - set(exp["indexes"])):
            lines.append(f"EXTRA    index {table}.{key}")
        for key in sorted(set(exp["indexes"]) & set(act["indexes"])):
            if exp["indexes"][key] != act["indexes"][key]:
                lines.append(
                    f"MISMATCH index {table}.{key}  expected {exp['indexes'][key]!r}"
                    f"  actual {act['indexes'][key]!r}"
                )

        for key in sorted(set(exp["constraints"]) - set(act["constraints"])):
            lines.append(f"MISSING  constraint {table}.{key}")
        for key in sorted(set(act["constraints"]) - set(exp["constraints"])):
            lines.append(f"EXTRA    constraint {table}.{key}")
        for key in sorted(set(exp["constraints"]) & set(act["constraints"])):
            if exp["constraints"][key] != act["constraints"][key]:
                lines.append(
                    f"MISMATCH constraint {table}.{key}  expected {exp['constraints'][key]!r}"
                    f"  actual {act['constraints'][key]!r}"
                )

    return sorted(lines)


def _with_database(dsn: str, database: str) -> str:
    """Return ``dsn`` with its path component swapped to ``database``."""
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


class ScratchDatabaseUnavailable(RuntimeError):
    """Raised when the connecting role cannot CREATE DATABASE.

    CI's ``quantcore`` role is the container's Postgres superuser and always
    has this. A managed-service application role (Cloud SQL) may not --
    callers should catch this and skip rather than fail.
    """


@contextmanager
def scratch_database(dsn: str, name: str | None = None) -> Iterator[str]:
    """Create a throwaway database alongside ``dsn``'s target, yield its DSN, drop it after.

    Connects to the ``postgres`` maintenance database on the same host as
    ``dsn`` (never the target database itself -- CREATE/DROP DATABASE cannot
    run inside a transaction against the database being created/dropped).
    ``name`` defaults to a random ``schema_scratch_<uuid>`` so concurrent
    callers (parallel CI jobs) never collide.
    """
    name = name or f"schema_scratch_{uuid.uuid4().hex[:12]}"
    admin_dsn = _with_database(dsn, "postgres")
    conn = psycopg2.connect(admin_dsn)
    try:
        conn.set_session(autocommit=True)
        with conn.cursor() as cur:
            try:
                cur.execute(f'CREATE DATABASE "{name}"')
            except psycopg2.errors.InsufficientPrivilege as exc:
                raise ScratchDatabaseUnavailable(
                    f"role lacks CREATEDB against {admin_dsn.split('@')[-1]}: {exc}"
                ) from exc
        try:
            yield _with_database(dsn, name)
        finally:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        conn.close()


def snapshot_from_dsn(dsn: str) -> dict:
    """What ``init_schema()`` actually produces, on a fresh scratch database.

    This is the one definition of "what the code expects" -- both
    ``scripts/check_schema_snapshot.py`` (does the committed snapshot match?)
    and ``scripts/schema_check.py`` (does a live deployment match?) compare
    against this, never against each other.
    """
    # Imported lazily: quantcore.db imports this module's siblings in some
    # call paths, and a top-level import here would risk a cycle as the
    # modules grow. init_schema() is the only thing needed from it.
    from quantcore.db import init_schema

    with scratch_database(dsn) as scratch_dsn:
        init_schema(scratch_dsn)
        conn = psycopg2.connect(scratch_dsn)
        try:
            return describe_schema(conn)
        finally:
            conn.close()
