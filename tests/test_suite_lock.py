"""The suite's one-run-per-database advisory lock (issue #248).

``tests/__init__.py`` takes the lock when the package is imported, so by the
time these tests run this process already holds it (whenever a database is
reachable). A second acquire from here is therefore a real "another run is
using it" case, which exercises the wait message and the timeout.
"""
import io
import os
import time
import unittest

import tests
from tests import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    _acquire_suite_lock,
    _lock_timeout_seconds,
)


class LockTimeoutParsingTest(unittest.TestCase):
    def test_unset_uses_default(self):
        self.assertEqual(_lock_timeout_seconds(None), DEFAULT_LOCK_TIMEOUT_SECONDS)
        self.assertEqual(_lock_timeout_seconds(""), DEFAULT_LOCK_TIMEOUT_SECONDS)

    def test_valid_value_is_used(self):
        self.assertEqual(_lock_timeout_seconds("90"), 90)

    def test_typo_falls_back_to_default(self):
        self.assertEqual(_lock_timeout_seconds("30m"), DEFAULT_LOCK_TIMEOUT_SECONDS)

    def test_non_positive_falls_back_to_default(self):
        # 0 would mean "no statement timeout" to Postgres: wait forever.
        self.assertEqual(_lock_timeout_seconds("0"), DEFAULT_LOCK_TIMEOUT_SECONDS)
        self.assertEqual(_lock_timeout_seconds("-5"), DEFAULT_LOCK_TIMEOUT_SECONDS)


class UnreachableDatabaseTest(unittest.TestCase):
    def test_unreachable_database_skips_the_lock(self):
        # Port 1 refuses at once; pure modules must still run without a DB.
        out = io.StringIO()
        dsn = "postgresql://u:s3cret@127.0.0.1:1/quantcore_test"
        self.assertIsNone(_acquire_suite_lock(dsn, 5, out=out))
        self.assertEqual(out.getvalue(), "")


@unittest.skipIf(tests._SUITE_LOCK is None, "no reachable test database")
class HeldLockTest(unittest.TestCase):
    def test_second_run_waits_then_fails_loudly_without_the_dsn(self):
        dsn = os.environ["QUANTCORE_DB_DSN"]
        out = io.StringIO()
        start = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx:
            _acquire_suite_lock(dsn, 1, out=out)
        self.assertLess(time.monotonic() - start, 10)

        from quantcore.db import describe_dsn

        where = describe_dsn(dsn)
        self.assertIn(f"Another test run is using {where}", out.getvalue())
        self.assertIn(where, str(ctx.exception))
        self.assertIn("QUANTCORE_UNITTEST_LOCK_TIMEOUT", str(ctx.exception))
        # Never-log policy: the DSN carries the password.
        self.assertNotIn(dsn, out.getvalue())
        self.assertNotIn(dsn, str(ctx.exception))

    def test_holder_session_is_alive_and_idle(self):
        with tests._SUITE_LOCK.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
            )
            self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
