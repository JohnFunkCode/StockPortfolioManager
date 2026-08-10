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
   re-ran the whole DDL mid-suite.

3. It runs **at all** only where nothing else owns the DDL. Issue #165: on a
   database Flyway already manages, the app creating tables makes two systems
   owners of one schema, and ``QUANTCORE_SCHEMA_MODE`` is how that is settled
   (``SchemaModeTest`` below).

These tests use a fake connection, so they need no database -- except
``test_create_app_does_not_re_run_the_ddl``, which imports ``api.main`` and so
inherits that module's real bootstrap on the first import in the process.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import quantcore.db as db


class FakeCursor:
    def __init__(self, log, flyway=False):
        self._log = log
        self._flyway = flyway

    def execute(self, stmt):
        self._log.append(stmt)

    def fetchone(self):
        # The only query the bootstrap reads a row back from is
        # _flyway_managed()'s to_regclass probe.
        return ("public.flyway_schema_history" if self._flyway else None,)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    """Records what init_schema() does to a connection.

    ``flyway`` decides what the ``to_regclass('public.flyway_schema_history')``
    probe sees, which is what ``auto`` mode resolves on.
    """

    def __init__(self, log, flyway=False):
        self.log = log
        self.flyway = flyway
        self.autocommit = False
        self.commits = 0
        self.closed = False

    def set_session(self, autocommit=False):
        self.autocommit = autocommit

    def cursor(self):
        return FakeCursor(self.log, self.flyway)

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
        """Each TestClient(create_app()) used to re-run the whole DDL.

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


def _schema(*tables) -> dict:
    """A describe_schema()-shaped dict with the named tables and nothing in them.

    Table-level presence is all these tests need: diff_schemas reports a table
    that exists on only one side as a single MISSING/EXTRA line rather than
    exploding into per-column noise, and that line is what the mode has to act
    on.
    """
    return {"tables": {t: {"columns": {}, "indexes": {}, "constraints": {}} for t in tables}}


class SchemaModeTest(unittest.TestCase):
    """QUANTCORE_SCHEMA_MODE decides whether the bootstrap creates or checks.

    Issue #165: on a database Flyway already manages, the app running its own
    CREATE TABLE DDL makes two systems owners of one schema. These stay
    database-free -- the connection is faked and describe_schema is stubbed,
    but diff_schemas is the real one, so the MISSING/EXTRA/MISMATCH
    classification under test is the shipped classification.
    """

    def setUp(self):
        self._saved = set(db._schema_ready_dsns)
        db._schema_ready_dsns.clear()
        self.addCleanup(self._restore)

        snapshot = Path(self.enterContext(tempfile.TemporaryDirectory())) / "snap.json"
        snapshot.write_text(json.dumps(_schema("symbols", "positions")))
        self.enterContext(patch.object(db, "SCHEMA_SNAPSHOT", snapshot))
        self.snapshot = snapshot

    def _restore(self):
        db._schema_ready_dsns.clear()
        db._schema_ready_dsns.update(self._saved)

    def _ensure(self, mode, *, flyway, live=("symbols", "positions")):
        """Run ensure_schema() under ``mode``; report whether the DDL ran.

        ``live`` is what the database is pretended to contain.
        """
        conn = FakeConnection([], flyway=flyway)
        with patch.dict(os.environ, {"QUANTCORE_SCHEMA_MODE": mode}), \
             patch.object(db.psycopg2, "connect", return_value=conn), \
             patch.object(db, "describe_schema", return_value=_schema(*live)), \
             patch.object(db, "init_schema") as init:
            db.ensure_schema("postgresql://fake/one")
        return init

    # -- auto ------------------------------------------------------------

    def test_auto_creates_when_no_flyway_ledger(self):
        """Local dev, CI and compose have no ledger and must still get tables."""
        init = self._ensure("auto", flyway=False)

        init.assert_called_once()

    def test_auto_only_checks_when_flyway_manages_the_database(self):
        init = self._ensure("auto", flyway=True)

        init.assert_not_called()

    def test_auto_enforces_on_a_flyway_managed_database(self):
        """The PR 4 flip: auto resolves to verify, not warn, where Flyway owns the DDL.

        Soaked warn-only on both projects first (decision D5). A forgotten
        migration now aborts startup rather than being silently papered over by
        the app's own CREATE TABLE.
        """
        with self.assertRaises(db.SchemaDriftError):
            self._ensure("auto", flyway=True, live=("symbols",))

    # -- create ----------------------------------------------------------

    def test_create_runs_the_ddl_even_on_a_flyway_managed_database(self):
        """The escape hatch: one --update-env-vars back to today's behaviour."""
        init = self._ensure("create", flyway=True)

        init.assert_called_once()

    def test_an_unrecognized_mode_falls_back_to_create_and_says_so(self):
        with self.assertLogs("quantcore.db", level="ERROR") as logs:
            init = self._ensure("verfiy", flyway=True)

        init.assert_called_once()
        self.assertIn("verfiy", "\n".join(logs.output))

    # -- warn vs verify --------------------------------------------------

    def test_warn_logs_a_missing_table_without_raising(self):
        with self.assertLogs("quantcore.db", level="INFO") as logs:
            init = self._ensure("warn", flyway=True, live=("symbols",))

        init.assert_not_called()
        output = "\n".join(logs.output)
        self.assertIn("MISSING  table positions", output)
        self.assertIn("missing=1", output)

    def test_verify_raises_on_a_missing_table_and_names_it(self):
        with self.assertRaises(db.SchemaDriftError) as raised:
            self._ensure("verify", flyway=True, live=("symbols",))

        self.assertIn("positions", str(raised.exception))

    def test_verify_leaves_the_dsn_unrecorded_so_a_retry_re_checks(self):
        with self.assertRaises(db.SchemaDriftError):
            self._ensure("verify", flyway=True, live=("symbols",))

        self.assertNotIn("postgresql://fake/one", db._schema_ready_dsns)

    def test_verify_passes_when_the_live_schema_matches(self):
        init = self._ensure("verify", flyway=True)

        init.assert_not_called()
        self.assertIn("postgresql://fake/one", db._schema_ready_dsns)

    def test_extras_never_raise_in_any_mode(self):
        """Decision D4: a deployed database may carry more than the snapshot."""
        for mode in ("warn", "verify", "auto"):
            with self.subTest(mode=mode):
                self._restore()
                db._schema_ready_dsns.clear()
                with self.assertLogs("quantcore.db", level="INFO") as logs:
                    self._ensure(mode, flyway=True,
                                 live=("symbols", "positions", "leftover_table"))

                output = "\n".join(logs.output)
                self.assertIn("EXTRA    table leftover_table", output)
                self.assertIn("extra=1", output)
                self.assertIn("missing=0", output)

    # -- operational guards ----------------------------------------------

    def test_the_check_runs_once_per_process_like_the_ddl_does(self):
        conn = FakeConnection([], flyway=True)
        with patch.dict(os.environ, {"QUANTCORE_SCHEMA_MODE": "warn"}), \
             patch.object(db.psycopg2, "connect", return_value=conn), \
             patch.object(db, "describe_schema",
                          return_value=_schema("symbols", "positions")) as describe:
            db.ensure_schema("postgresql://fake/one")
            db.ensure_schema("postgresql://fake/one")
            db.ensure_schema("postgresql://fake/one")

        self.assertEqual(describe.call_count, 1)

    def test_no_log_line_carries_the_dsn(self):
        """The never-log policy covers every log line in this repo, not just keyproxy's."""
        dsn = "postgresql://quantcore:hunter2@db.example:5432/quantcore"
        conn = FakeConnection([], flyway=True)
        with self.assertLogs("quantcore.db", level="INFO") as logs, \
             patch.dict(os.environ, {"QUANTCORE_SCHEMA_MODE": "warn"}), \
             patch.object(db.psycopg2, "connect", return_value=conn), \
             patch.object(db, "describe_schema", return_value=_schema("symbols")):
            db.ensure_schema(dsn)

        output = "\n".join(logs.output)
        self.assertNotIn("hunter2", output)
        self.assertNotIn("db.example", output)

    def test_a_missing_snapshot_file_warns_but_does_not_block_startup(self):
        self.snapshot.unlink()
        with self.assertLogs("quantcore.db", level="ERROR") as logs:
            init = self._ensure("warn", flyway=True)

        init.assert_not_called()
        self.assertIn("snap.json", "\n".join(logs.output))

    def test_a_missing_snapshot_file_is_fatal_in_verify(self):
        """Told to enforce and unable to: fail loudly rather than pass silently."""
        self.snapshot.unlink()

        with self.assertRaises(db.SchemaDriftError):
            self._ensure("verify", flyway=True)


if __name__ == "__main__":
    unittest.main()
