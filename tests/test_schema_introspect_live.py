"""Live-database tests for quantcore.schema_introspect (issue #165, PR 1).

describe_schema()/scratch_database()/snapshot_from_dsn() are I/O -- they only
mean anything against a real Postgres. tests/__init__.py has already swapped
QUANTCORE_DB_DSN to the test database (or, in CI, left it pointed at the
gate job's disposable postgres service), so this suite runs there, never
against production.
"""
import unittest
from contextlib import closing

import psycopg2

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import quantcore.db as db  # noqa: E402
from quantcore.schema_introspect import (  # noqa: E402
    ScratchDatabaseUnavailable,
    describe_schema,
    scratch_database,
    snapshot_from_dsn,
)


def _raw_connection():
    # describe_schema() takes a plain psycopg2 connection (it calls
    # .cursor(cursor_factory=...) directly), not quantcore.db's sqlite3-
    # compatible _PGConn wrapper -- matches how scripts/schema_check.py and
    # scripts/check_schema_snapshot.py call it.
    db.ensure_schema()
    return psycopg2.connect(db.DB_DSN)


class DescribeSchemaLiveTests(unittest.TestCase):
    def test_describes_a_known_table_with_columns_and_constraints(self):
        with closing(_raw_connection()) as conn:
            schema = describe_schema(conn)

        self.assertIn("positions", schema["tables"])
        positions = schema["tables"]["positions"]
        self.assertIn("owner", positions["columns"])
        self.assertIn("symbol_id", positions["columns"])
        # Every table init_schema() creates gets a primary key index.
        self.assertTrue(positions["indexes"])

    def test_ignores_flyway_schema_history_table(self):
        with closing(_raw_connection()) as conn:
            schema = describe_schema(conn)

        self.assertNotIn("flyway_schema_history", schema["tables"])

    def test_two_descriptions_of_the_same_connection_are_identical(self):
        # describe_schema() sorts every level internally, so calling it twice
        # against an unchanged schema must be byte-for-byte equal -- this is
        # what makes it usable as a diffable snapshot rather than a log.
        with closing(_raw_connection()) as conn:
            first = describe_schema(conn)
            second = describe_schema(conn)
        self.assertEqual(first, second)


class ScratchDatabaseLiveTests(unittest.TestCase):
    def test_scratch_database_is_created_then_dropped(self):
        try:
            with scratch_database(db.DB_DSN) as scratch_dsn:
                self.assertNotEqual(scratch_dsn, db.DB_DSN)
                # Usable while the context is open.
                conn = psycopg2.connect(scratch_dsn)
                conn.close()
        except ScratchDatabaseUnavailable:
            self.skipTest("connecting role lacks CREATEDB against this database")
            return

        # Dropped on exit: connecting again must fail.
        with self.assertRaises(psycopg2.OperationalError):
            psycopg2.connect(scratch_dsn)


class SnapshotFromDsnLiveTests(unittest.TestCase):
    def test_snapshot_matches_a_direct_describe_schema_call(self):
        # snapshot_from_dsn() runs init_schema() on a throwaway database and
        # describes it; describe_schema() against the already-initialized
        # QUANTCORE_DB_DSN database should see the identical set of tables
        # (init_schema() is idempotent DDL, so both are "what the code
        # produces today").
        try:
            snapshot = snapshot_from_dsn(db.DB_DSN)
        except ScratchDatabaseUnavailable:
            self.skipTest("connecting role lacks CREATEDB against this database")
            return

        with closing(_raw_connection()) as conn:
            live = describe_schema(conn)

        self.assertEqual(set(snapshot["tables"]), set(live["tables"]))


if __name__ == "__main__":
    unittest.main()
