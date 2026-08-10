"""The DSN never leaves the process (issue #179).

``cache_stats()`` used to return ``{"db_path": DB_DSN, ...}`` — and that dict
travels verbatim through FundamentalsService → ``GET
/api/securities/fundamentals/cache-stats`` → the ``get_cache_stats`` MCP tool,
so any client holding a token could read the production database password.
These tests pin the redaction (``quantcore.db.describe_dsn``) and both return
paths of ``cache_stats()``, so the field cannot come back.

Fully offline: the DSN is patched to a synthetic one whose password is a
distinctive string, and the connection is faked. CI's real test DSN has
``quantcore`` as user, password *and* database name, which makes a substring
assertion against it meaningless.
"""
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import psycopg2

from quantcore import db
from quantcore.repositories import fundamentals_repository as fr

SECRET = "sup3r-s3cret-pw"
FAKE_DSN = f"postgresql://dbadmin:{SECRET}@10.1.2.3:5432/quantcore"


class TestDescribeDsn(unittest.TestCase):
    def test_strips_user_and_password(self):
        described = db.describe_dsn(FAKE_DSN)
        self.assertEqual(described, "10.1.2.3:5432/quantcore")
        self.assertNotIn(SECRET, described)
        self.assertNotIn("dbadmin", described)

    def test_defaults_to_the_module_dsn(self):
        with patch.object(db, "DB_DSN", FAKE_DSN):
            self.assertEqual(db.describe_dsn(), "10.1.2.3:5432/quantcore")

    def test_omits_the_port_when_the_dsn_does(self):
        self.assertEqual(
            db.describe_dsn("postgresql://u:p@cloudsql-proxy/quantcore"),
            "cloudsql-proxy/quantcore",
        )

    def test_postgres_scheme_alias(self):
        self.assertEqual(
            db.describe_dsn("postgres://u:p@localhost:5433/prod"),
            "localhost:5433/prod",
        )

    def test_keyword_form_dsn_is_not_echoed(self):
        # libpq's keyword form has no URL structure to strip, so echoing any
        # part of it back would leak the password sitting in the middle.
        described = db.describe_dsn(f"host=10.1.2.3 dbname=quantcore password={SECRET}")
        self.assertEqual(described, "unknown")

    def test_unparseable_dsn_is_not_echoed(self):
        # An empty string is not in this list: it takes the DB_DSN default,
        # which is itself redacted.
        for junk in ("   ", "not a dsn at all", "mysql://u:p@host/db",
                     "postgresql://u:p@host:notaport/db"):
            self.assertEqual(db.describe_dsn(junk), "unknown", junk)


@contextmanager
def fake_connection(rows):
    """Stand in for get_connection(): one cursor returning `rows`."""

    class Cursor:
        def fetchall(self):
            return rows

    class Conn:
        def execute(self, *_args, **_kwargs):
            return Cursor()

        def close(self):
            pass

    with patch.object(fr, "get_connection", return_value=Conn()):
        yield


class TestCacheStatsLeaksNothing(unittest.TestCase):
    def assert_no_credentials(self, stats):
        blob = json.dumps(stats)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("dbadmin", blob)
        self.assertNotIn(FAKE_DSN, blob)
        self.assertNotIn("://", blob)
        self.assertNotIn("db_path", stats)
        self.assertEqual(stats["database"], "10.1.2.3:5432/quantcore")

    def test_success_path(self):
        with patch.object(db, "DB_DSN", FAKE_DSN):
            with fake_connection([("fundamental_score", 3, 1750000000, 1755000000)]):
                stats = fr.cache_stats()
        self.assertEqual(len(stats["data_types"]), 1)
        self.assert_no_credentials(stats)

    def test_error_path(self):
        # The path that reports a database failure is exactly where an
        # "informative" DSN is most tempting.
        with patch.object(db, "DB_DSN", FAKE_DSN):
            with patch.object(fr, "get_connection", side_effect=psycopg2.Error("boom")):
                stats = fr.cache_stats()
        self.assertEqual(stats["data_types"], [])
        self.assertIn("error", stats)
        self.assert_no_credentials(stats)


if __name__ == "__main__":
    unittest.main()
