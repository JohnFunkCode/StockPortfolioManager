"""db/migrate-entrypoint.sh, the quantcore-migrate Job's entrypoint (#200).

Each test sources the script in a bash subprocess and calls one function, so
nothing here runs Flyway or touches a database: main() runs against a stub
FLYWAY_BIN that prints canned `info` JSON and records what it was asked to do.
"""

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "db" / "migrate-entrypoint.sh"

PASSWORD = "s3cr%40t@pw"  # decodes to s3cr@t@pw


def run(snippet, env=None, cwd=None):
    """Source the entrypoint, then run `snippet` in the same bash."""
    full_env = {k: v for k, v in os.environ.items() if k != "QUANTCORE_DB_DSN"}
    full_env.update(env or {})
    return subprocess.run(
        ["bash", "-c", f'. "{SCRIPT}"; {snippet}'],
        capture_output=True, text=True, env=full_env, cwd=cwd or REPO, timeout=30,
    )


def parse(dsn):
    r = run('parse_dsn "$DSN"; printf "%s\\n" "$FLYWAY_URL" "$FLYWAY_USER" '
            '"$FLYWAY_PASSWORD" "$MIGRATE_TARGET"', env={"DSN": dsn})
    if r.returncode != 0:
        return r.returncode, r.stderr
    return 0, r.stdout.split("\n")[:4]


class ParseDsnTest(unittest.TestCase):

    def test_cloudsql_socket_form(self):
        rc, (url, user, pw, target) = parse(
            f"postgresql://quantcore:{PASSWORD}@/quantcore?host=/cloudsql/p:us-central1:i")
        self.assertEqual(rc, 0)
        self.assertEqual(url, "jdbc:postgresql://localhost/quantcore?socketFactory="
                         "org.newsclub.net.unix.AFUNIXSocketFactory$FactoryArg"
                         "&socketFactoryArg=/cloudsql/p:us-central1:i/.s.PGSQL.5432")
        self.assertEqual(user, "quantcore")
        self.assertEqual(pw, "s3cr@t@pw")
        self.assertEqual(target, "cloudsql:p:us-central1:i/quantcore")

    def test_plain_socket_dir_with_port(self):
        rc, (url, _, _, target) = parse("postgresql://u:p@/db?port=5499&host=%2Ftmp%2Fpgs")
        self.assertEqual(rc, 0)
        self.assertTrue(url.endswith("&socketFactoryArg=/tmp/pgs/.s.PGSQL.5499"), url)
        self.assertEqual(target, "socket:/tmp/pgs/db")

    def test_tcp_form(self):
        rc, (url, user, pw, target) = parse("postgres://u:p@127.0.0.1:5434/quantcore")
        self.assertEqual(rc, 0)
        self.assertEqual(url, "jdbc:postgresql://127.0.0.1:5434/quantcore")
        self.assertEqual((user, pw, target), ("u", "p", "127.0.0.1:5434/quantcore"))

    def test_tcp_default_port_and_query_port(self):
        self.assertEqual(parse("postgresql://u:p@db.local/q")[1][3], "db.local:5432/q")
        self.assertEqual(parse("postgresql://u:p@db.local/q?port=6000")[1][3], "db.local:6000/q")

    def test_password_decoding_is_libpq_style(self):
        # %25 -> %, '+' stays '+', a backslash survives printf %b.
        pw = parse("postgresql://u:a%25b+c\\d@h:1/db")[1][2]
        self.assertEqual(pw, "a%b+c\\d")

    def test_no_password(self):
        _, (_, user, pw, _) = parse("postgresql://u@h:1/db")
        self.assertEqual((user, pw), ("u", ""))

    def test_rejects(self):
        for dsn in ("mysql://u:p@h/db", "postgresql://h:1/db", "postgresql://u:p@h:1/",
                    "postgresql://u:p@/db", "postgresql://u:p@/db?host=relative"):
            with self.subTest(dsn=dsn):
                rc, err = parse(dsn)
                self.assertEqual(rc, 2)
                self.assertNotIn("p@", err)


def scan(sql):
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
        f.write(textwrap.dedent(sql))
    try:
        r = run('scan_sql "$F"', env={"F": f.name})
        assert r.returncode == 0, r.stderr
        return r.stdout.splitlines()
    finally:
        os.unlink(f.name)


class ScanSqlTest(unittest.TestCase):

    def test_each_rule(self):
        cases = {
            "DROP TABLE t;": "contract: DROP",
            "alter table t drop column c;": "contract: DROP",
            "ALTER TABLE t RENAME COLUMN a TO b;": "contract: RENAME",
            "ALTER TABLE t\n  ALTER COLUMN c\n  TYPE bigint;": "contract: ALTER ... TYPE",
            "CREATE INDEX CONCURRENTLY ix ON t (c);": "non-transactional: CONCURRENTLY",
            "VACUUM t;": "non-transactional: VACUUM",
            "ALTER SYSTEM SET work_mem = '64MB';": "non-transactional: ALTER SYSTEM",
            "CREATE DATABASE x;": "non-transactional: CREATE/DROP DATABASE",
        }
        for sql, rule in cases.items():
            with self.subTest(sql=sql):
                self.assertIn(rule, scan(sql))

    def test_expand_migration_is_clean(self):
        self.assertEqual(scan("""
            CREATE TABLE IF NOT EXISTS t (id serial PRIMARY KEY, event_type text, drop_count int);
            ALTER TABLE t ADD COLUMN IF NOT EXISTS renamed_at timestamptz;
            CREATE INDEX IF NOT EXISTS ix_t ON t (event_type);
            INSERT INTO t (event_type) VALUES ('x');
        """), [])

    def test_keywords_in_comments_and_strings_do_not_refuse(self):
        self.assertEqual(scan("""
            -- DROP TABLE t; VACUUM
            /* RENAME /* nested CONCURRENTLY */ still a comment: DROP */
            COMMENT ON TABLE t IS 'do not DROP this; it''s VACUUM-safe';
            CREATE TABLE u (id int);
        """), [])

    def test_alter_and_type_in_different_statements_do_not_combine(self):
        self.assertEqual(scan("ALTER TABLE t ADD COLUMN c int; CREATE TYPE mood AS ENUM ('a');"), [])

    def test_dollar_quoted_body_is_scanned(self):
        # A DO block executes, so a DROP inside one must refuse.
        self.assertIn("contract: DROP", scan("DO $$ BEGIN EXECUTE 'x'; DROP TABLE t; END $$;"))


INFO_JSON = """{
  "migrations" : [ {
    "category" : "Versioned",
    "filepath" : "/q/db/migrations/V9__a.sql",
    "state" : "Success",
    "version" : "9"
  }, {
    "category" : "Versioned",
    "filepath" : "%s",
    "state" : "Pending",
    "version" : "10"
  } ],
  "schemaVersion" : "9"
}"""


class PendingFilesTest(unittest.TestCase):

    def test_prints_only_pending(self):
        r = run("pending_files <<<\"$J\"", env={"J": INFO_JSON % "/q/db/migrations/V10__b.sql"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["/q/db/migrations/V10__b.sql"])

    def test_several_pending_in_info_order(self):
        j = INFO_JSON.replace("%s", "/q/db/migrations/V10__b.sql").replace(
            '  } ],', '  }, {\n    "filepath" : "/q/db/migrations/V11__c.sql",\n'
                      '    "state" : "Pending",\n    "version" : "11"\n  } ],')
        r = run("pending_files <<<\"$J\"", env={"J": j})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(),
                         ["/q/db/migrations/V10__b.sql", "/q/db/migrations/V11__c.sql"])

    def test_key_order_does_not_matter(self):
        # state before filepath, as a future Flyway might write it.
        j = ('{ "migrations" : [ {\n    "state" : "Success",\n'
             '    "filepath" : "/q/V9__a.sql"\n  }, {\n    "state" : "Pending",\n'
             '    "filepath" : "/q/V10__b.sql"\n  } ] }')
        r = run("pending_files <<<\"$J\"", env={"J": j})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["/q/V10__b.sql"])

    def test_pending_without_filepath_fails(self):
        r = run("pending_files <<<\"$J\"", env={"J": INFO_JSON % ""})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no filepath", r.stderr)


class MainTest(unittest.TestCase):
    """main() against a stub flyway that answers `info -outputType=json` from a file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.calls = d / "calls"
        self.json = d / "info.json"
        self.stub = d / "flyway"
        self.stub.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            echo "$*" >> "{self.calls}"
            if [[ "$*" == *outputType=json* ]]; then cat "{self.json}"; else echo "stub $2"; fi
            """))
        self.stub.chmod(0o755)
        self.dir = d

    def tearDown(self):
        self.tmp.cleanup()

    def main(self, pending_sql=None):
        if pending_sql is None:
            self.json.write_text('{ "migrations" : [ ] }')
        else:
            f = self.dir / "V99__x.sql"
            f.write_text(pending_sql)
            self.json.write_text(INFO_JSON % f)
        r = run("main", env={
            "FLYWAY_BIN": str(self.stub),
            "QUANTCORE_DB_DSN": f"postgresql://quantcore:{PASSWORD}@/quantcore?host=/cloudsql/p:r:i",
        })
        calls = self.calls.read_text().splitlines() if self.calls.exists() else []
        return r, [c.split()[1] for c in calls]

    def assert_no_secret(self, r):
        for secret in (PASSWORD, "s3cr@t@pw", "s3cr"):
            self.assertNotIn(secret, r.stdout + r.stderr)

    def test_nothing_pending(self):
        r, calls = self.main()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("migrate: target cloudsql:p:r:i/quantcore (user: quantcore)", r.stdout)
        self.assertIn("nothing pending", r.stdout)
        self.assertEqual(calls, ["info", "info"])
        self.assert_no_secret(r)

    def test_expand_migration_is_applied(self):
        r, calls = self.main("ALTER TABLE t ADD COLUMN c int;")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 pending: V99__x.sql", r.stdout)
        self.assertEqual(calls, ["info", "migrate", "info"])
        self.assert_no_secret(r)

    def test_contract_migration_is_refused_before_migrate(self):
        r, calls = self.main("ALTER TABLE t DROP COLUMN c;")
        self.assertEqual(r.returncode, 3)
        self.assertIn("migrate: REFUSED V99__x.sql: contract: DROP", r.stdout)
        self.assertIn("./scripts/flyway.sh --prod migrate", r.stdout)
        self.assertEqual(calls, ["info"])
        self.assert_no_secret(r)

    def test_missing_dsn(self):
        r = run("main", env={"FLYWAY_BIN": str(self.stub)})
        self.assertEqual(r.returncode, 2)
        self.assertIn("QUANTCORE_DB_DSN is not set", r.stderr)
        self.assertFalse(self.calls.exists())


class ConfigTest(unittest.TestCase):

    def test_flyway_mixed_stays_unset(self):
        # D4a: a non-transactional statement must fail rather than run unwrapped.
        conf = (REPO / "db" / "flyway.conf").read_text()
        active = [l for l in conf.splitlines() if l.strip() and not l.lstrip().startswith("#")]
        self.assertFalse([l for l in active if "mixed" in l.lower()])

    def test_image_ships_the_entrypoint_and_migrations(self):
        df = (REPO / "Dockerfile.migrate").read_text()
        for needle in ("COPY db/flyway.conf", "COPY db/migrations/", "migrate-entrypoint.sh",
                       "/flyway/drivers/", "sha256sum -c"):
            self.assertIn(needle, df)
        self.assertTrue(os.access(SCRIPT, os.X_OK))


if __name__ == "__main__":
    unittest.main()
