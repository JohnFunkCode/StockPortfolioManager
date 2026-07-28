"""Guards on how the schema DDL is bootstrapped.

The DDL takes ~3s against a managed instance and touches ~20 tables. Two
properties keep it from colliding with concurrent writers, and both have been
regressed before:

1. It runs in **autocommit**, so each statement's lock is released as soon as
   that statement finishes. Held to a single final commit, the DDL accumulates
   locks across every table it has touched, and a writer taking the same tables
   in the opposite order deadlocks (observed as ``DeadlockDetected`` between
   ``symbols`` and ``plan_instances``/``plan_rungs`` in full-suite runs).
2. It runs **at most once per process per DSN**. ``create_app()`` used to call
   ``init_schema()`` unconditionally, so every ``TestClient(create_app())``
   re-ran all 65 statements mid-suite.

These tests use a fake connection, so they need no database -- except
``test_create_app_does_not_re_run_the_ddl``, which imports ``api.main`` and so
inherits that module's real bootstrap on the first import in the process.
"""
import unittest
from unittest.mock import patch

import quantcore.db as db


class FakeCursor:
    def __init__(self, log):
        self._log = log

    def execute(self, stmt):
        self._log.append(stmt)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    """Records what init_schema() does to a connection."""

    def __init__(self, log):
        self.log = log
        self.autocommit = False
        self.commits = 0
        self.closed = False

    def set_session(self, autocommit=False):
        self.autocommit = autocommit

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class SchemaBootstrapTest(unittest.TestCase):
    def setUp(self):
        # Every test drives the bootstrap from a clean slate.
        self._saved = set(db._schema_ready_dsns)
        db._schema_ready_dsns.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        db._schema_ready_dsns.clear()
        db._schema_ready_dsns.update(self._saved)

    def _run_init(self, dsn=None):
        log = []
        conn = FakeConnection(log)
        with patch.object(db.psycopg2, "connect", return_value=conn) as connect:
            db.init_schema(dsn)
        return conn, log, connect

    def test_ddl_runs_in_autocommit_with_a_lock_timeout(self):
        conn, log, _ = self._run_init()

        self.assertTrue(conn.autocommit,
                        "schema DDL must not hold every table's lock until a "
                        "single final commit")
        self.assertEqual(conn.commits, 0)
        self.assertTrue(conn.closed)
        self.assertTrue(
            any(s.lower().startswith("set lock_timeout") for s in log),
            "a blocked DDL statement must fail loudly, not hang",
        )

    def test_ddl_executes_every_statement(self):
        _, log, _ = self._run_init()

        statements = [s for s in log if not s.lower().startswith("set ")]
        self.assertEqual(statements, db._split_schema(db._SCHEMA))

    def test_ensure_schema_runs_the_ddl_once_per_process(self):
        with patch.object(db, "init_schema") as init:
            db.ensure_schema()
            db.ensure_schema()
            db.ensure_schema()

        self.assertEqual(init.call_count, 1)

    def test_ensure_schema_initializes_each_distinct_dsn_once(self):
        with patch.object(db, "init_schema") as init:
            db.ensure_schema("postgresql://other/one")
            db.ensure_schema("postgresql://other/one")
            db.ensure_schema("postgresql://other/two")

        self.assertEqual(init.call_count, 2)

    def test_init_schema_records_the_dsn_so_ensure_schema_skips_it(self):
        self._run_init()

        with patch.object(db, "init_schema") as init:
            db.ensure_schema()

        init.assert_not_called()

    def test_create_app_does_not_re_run_the_ddl(self):
        """Each TestClient(create_app()) used to re-run all 65 statements.

        Exactly one, not "at most one": create_app() is an entry point and must
        still guarantee the tables exist, so dropping the bootstrap altogether
        has to fail here too.
        """
        # api/main.py builds a module-level ``app = create_app()`` for uvicorn,
        # so the first import of it in a process bootstraps the schema before
        # the patch below is in place. Clear the record afterwards, or this
        # test measures which module imported api.main first rather than what
        # create_app() does.
        from api.main import create_app
        db._schema_ready_dsns.clear()

        with patch.object(db, "init_schema") as init:
            create_app()
            create_app()

        self.assertEqual(init.call_count, 1)


if __name__ == "__main__":
    unittest.main()
