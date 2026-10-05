"""scripts/ensure_app_db_role.py -- the least-privilege app role (issue #308).

The pure parts (verifier, DSN swap, statement plan, output hygiene) run
anywhere. The database case creates a uniquely named role in a scratch
database, applies the grants, and logs in as it: it needs CREATEROLE and
CREATEDB, which CI's Postgres superuser has. Locally it skips; in CI it fails
rather than skip, like the schema parity test.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import io
import os
import unittest
import uuid
from unittest import mock

from scripts import ensure_app_db_role as tool

PASSWORD = "test-app-db-password-308-not-real"


class ScramVerifierTests(unittest.TestCase):
    def test_has_postgres_shape(self):
        v = tool.scram_verifier(PASSWORD, salt=b"0123456789abcdef")
        algo, rest = v.split("$", 1)
        iters_salt, keys = rest.split("$")
        iters, salt = iters_salt.split(":")
        stored, server = keys.split(":")
        self.assertEqual(algo, "SCRAM-SHA-256")
        self.assertEqual(int(iters), 4096)
        self.assertEqual(base64.b64decode(salt), b"0123456789abcdef")
        self.assertEqual(len(base64.b64decode(stored)), 32)
        self.assertEqual(len(base64.b64decode(server)), 32)

    def test_matches_rfc_7677_derivation(self):
        salt = b"saltsaltsaltsalt"
        salted = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt, 4096)
        stored = hashlib.sha256(hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
        server = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        expected = (f"SCRAM-SHA-256$4096:{base64.b64encode(salt).decode()}"
                    f"${base64.b64encode(stored).decode()}:{base64.b64encode(server).decode()}")
        self.assertEqual(tool.scram_verifier(PASSWORD, salt=salt), expected)

    def test_never_contains_the_password(self):
        self.assertNotIn(PASSWORD, tool.scram_verifier(PASSWORD))

    def test_salt_is_random(self):
        self.assertNotEqual(tool.scram_verifier(PASSWORD), tool.scram_verifier(PASSWORD))


class SwapDsnTests(unittest.TestCase):
    def test_proxy_form(self):
        out = tool.swap_dsn("postgresql://quantcore:old@127.0.0.1:5434/quantcore", "quantcore_app", "pw")
        self.assertEqual(out, "postgresql://quantcore_app:pw@127.0.0.1:5434/quantcore")

    def test_cloud_run_socket_form_keeps_the_query(self):
        dsn = "postgresql://quantcore:old@/quantcore?host=/cloudsql/p:us-central1:quantcore"
        out = tool.swap_dsn(dsn + "\n", "quantcore_app", "pw")
        self.assertEqual(out, "postgresql://quantcore_app:pw@/quantcore?host=/cloudsql/p:us-central1:quantcore")

    def test_password_is_percent_encoded(self):
        out = tool.swap_dsn("postgresql://u:p@h:5432/d", "quantcore_app", "a@b:c/d%e")
        self.assertIn("quantcore_app:a%40b%3Ac%2Fd%25e@h:5432", out)

    def test_an_at_sign_in_the_old_password_is_not_mistaken_for_the_host(self):
        out = tool.swap_dsn("postgresql://u:p@ss@h:5432/d", "r", "x")
        self.assertEqual(out, "postgresql://r:x@h:5432/d")

    def test_rejects_non_url_forms(self):
        for bad in ("host=h user=u password=p", "postgresql://h:5432/d", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tool.swap_dsn(bad, "r", "x")

    def test_swap_mode_refuses_a_terminal(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True

        out, err = Tty(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(tool.main(["--swap-dsn"]), 2)
        self.assertIn("not a terminal", err.getvalue())
        self.assertEqual(out.getvalue(), "")

    def test_swap_mode_writes_only_the_dsn(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {tool.PASSWORD_ENV: PASSWORD}), \
                mock.patch("sys.stdin", io.StringIO("postgresql://quantcore:old@/db?host=/cloudsql/x\n")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(tool.main(["--swap-dsn"]), 0)
        self.assertEqual(out.getvalue(), f"postgresql://quantcore_app:{PASSWORD}@/db?host=/cloudsql/x")
        self.assertNotIn("old", err.getvalue())

    def test_short_passwords_are_refused(self):
        with mock.patch.dict(os.environ, {tool.PASSWORD_ENV: "short"}), \
                self.assertRaises(SystemExit):
            tool._read_password(confirm=False)


class PlanStatementTests(unittest.TestCase):
    def _render(self, **kw):
        # sql.Composed.as_string needs a connection only for literals and
        # identifiers; a fake that quotes is enough to read the plan.
        from psycopg2 import sql

        def text(obj):
            if isinstance(obj, sql.Composed):
                return "".join(text(p) for p in obj)
            if isinstance(obj, sql.Identifier):
                return ".".join(f'"{s}"' for s in obj.strings)
            if isinstance(obj, sql.Literal):
                return f"'{obj.wrapped}'"
            return obj.string

        return [text(s) for s in tool.plan_statements("quantcore_app", "quantcore", **kw)]

    def test_create_path(self):
        stmts = self._render(role_exists=False, verifier="SCRAM-SHA-256$v", flyway_exists=True)
        self.assertTrue(stmts[0].startswith('CREATE ROLE "quantcore_app" WITH LOGIN NOSUPERUSER'))
        self.assertIn("PASSWORD 'SCRAM-SHA-256$v'", stmts[0])
        joined = "\n".join(stmts)
        self.assertIn('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "quantcore_app"', joined)
        self.assertIn("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES", joined)
        self.assertIn("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES", joined)
        self.assertIn('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON "flyway_schema_history"', stmts[-1])

    def test_no_ddl_or_create_rights_are_ever_granted(self):
        joined = "\n".join(self._render(role_exists=False, verifier="v", flyway_exists=True))
        for forbidden in ("GRANT ALL", "GRANT CREATE", "TRUNCATE ON ALL", "REFERENCES",
                          "TRIGGER", " TO \"quantcore\"", "IN ROLE", "ADMIN"):
            self.assertNotIn(forbidden, joined)

    def test_alter_path_names_no_superuser_only_attribute(self):
        first = self._render(role_exists=True, verifier=None, flyway_exists=False)[0]
        self.assertEqual(first, 'ALTER ROLE "quantcore_app" WITH LOGIN NOCREATEDB NOCREATEROLE')
        for attr in ("SUPERUSER", "REPLICATION", "BYPASSRLS", "PASSWORD"):
            self.assertNotIn(attr, first)

    def test_no_flyway_revoke_without_the_table(self):
        stmts = self._render(role_exists=True, verifier=None, flyway_exists=False)
        self.assertFalse(any("flyway" in s for s in stmts))


class DatabaseTests(unittest.TestCase):
    """Applies the real grants in a scratch database and logs in as the role."""

    def _skip_or_fail(self, reason: str) -> None:
        if os.environ.get("CI"):
            self.fail(f"the app-role test must run in CI, but would have skipped: {reason}")
        self.skipTest(reason)

    def test_role_can_write_rows_but_not_run_ddl(self):
        import psycopg2
        import psycopg2.errors
        from quantcore import db
        from quantcore.db_safety import assert_not_production
        from quantcore.schema_introspect import ScratchDatabaseUnavailable, scratch_database

        assert_not_production()
        role = f"qc_app_test_{uuid.uuid4().hex[:10]}"
        try:
            admin = psycopg2.connect(db.DB_DSN)
        except psycopg2.OperationalError as exc:
            self._skip_or_fail(f"database unreachable: {type(exc).__name__}")
        admin.autocommit = True
        try:
            with scratch_database(db.DB_DSN) as dsn:
                db.init_schema(dsn)
                database = dsn.rsplit("/", 1)[1].split("?")[0]
                conn = psycopg2.connect(dsn)
                try:
                    with conn.cursor() as cur:
                        cur.execute("CREATE TABLE flyway_schema_history (installed_rank int)")
                        try:
                            for stmt in tool.plan_statements(
                                    role, database, role_exists=False,
                                    verifier=tool.scram_verifier(PASSWORD), flyway_exists=True):
                                cur.execute(stmt)
                        except psycopg2.errors.InsufficientPrivilege as exc:
                            conn.rollback()
                            self._skip_or_fail(f"no CREATEROLE: {type(exc).__name__}")
                        self.assertEqual(tool.verify(cur, role, database), [])
                    conn.commit()

                    app_dsn = tool.swap_dsn(dsn, role, PASSWORD)
                    self.assertEqual(tool.login_probe(app_dsn), [])
                    self._check_dml_and_ledger(app_dsn)

                    # A table a later migration creates is covered by default privileges.
                    with conn.cursor() as cur:
                        cur.execute("CREATE TABLE later_table (id serial PRIMARY KEY, v text)")
                    conn.commit()
                    app = psycopg2.connect(app_dsn)
                    try:
                        with app.cursor() as cur:
                            cur.execute("INSERT INTO later_table (v) VALUES ('x')")
                        app.rollback()
                    finally:
                        app.close()
                    with conn.cursor() as cur:
                        self.assertEqual(tool.verify(cur, role, database), [])
                        cur.execute("DROP OWNED BY " + role)
                    conn.commit()
                finally:
                    conn.close()
        except ScratchDatabaseUnavailable as exc:
            self._skip_or_fail(str(exc))
        finally:
            with admin.cursor() as cur:
                cur.execute(f'DROP ROLE IF EXISTS "{role}"')
            admin.close()

    def _check_dml_and_ledger(self, app_dsn: str) -> None:
        import psycopg2
        import psycopg2.errors

        app = psycopg2.connect(app_dsn)
        try:
            with app.cursor() as cur:
                cur.execute("SELECT count(*) FROM watchlist")
                cur.execute("SELECT count(*) FROM flyway_schema_history")
                with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute("INSERT INTO flyway_schema_history VALUES (1)")
            app.rollback()
            with app.cursor() as cur:
                with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute("ALTER TABLE watchlist ADD COLUMN probe int")
            app.rollback()
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
