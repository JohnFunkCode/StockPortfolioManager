"""The two owners of the deployed schema must agree (issue #165, PR 2).

``init_schema()`` in ``quantcore/db.py`` and the Flyway files under
``db/`` both claim to produce the QuantCore schema, and nothing has ever
compared them. This test builds a scratch database from each and asserts the
descriptions are identical, so a change that ships to only one owner cannot
merge.

The diff *is* the bug report: on failure the assertion message carries every
MISSING/EXTRA/MISMATCH line, which names the object and which side has it.
"""
import os
import re
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path

import psycopg2

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import quantcore.db as db  # noqa: E402
from quantcore.schema_introspect import (  # noqa: E402
    ScratchDatabaseUnavailable,
    describe_schema,
    diff_schemas,
    scratch_database,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIR = REPO_ROOT / "db" / "baseline"
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

_VERSION_RE = re.compile(r"^V(\d+)__")


def _version(path: Path) -> int:
    """Numeric version from a Flyway filename, for ordering.

    Sorting the paths as strings would run V10 before V9; Flyway orders by
    parsed version, so this does too.
    """
    match = _VERSION_RE.match(path.name)
    if match is None:
        raise ValueError(f"not a Flyway migration filename: {path.name}")
    return int(match.group(1))


def sql_files_in_order() -> list[Path]:
    """The baseline, then every migration, in the order Flyway would apply them.

    The baseline lives outside ``flyway.locations`` on purpose (decision D3 in
    docs/proposals/schema-ownership-plan.md) -- deployed databases were
    baselined at V1 and must never replay it. This test is the one caller that
    does replay it, because it is building an empty database from scratch.
    """
    baseline = sorted(BASELINE_DIR.glob("V*.sql"), key=_version)
    migrations = sorted(MIGRATIONS_DIR.glob("V*.sql"), key=_version)
    return baseline + migrations


def _apply_sql_files(dsn: str, paths: list[Path]) -> None:
    """Run each file as one multi-statement execute on an autocommit connection.

    psycopg2 executes a multi-statement string in one round trip, and
    autocommit matches how ``init_schema()`` runs (see its docstring: per
    statement commit is what keeps the DDL from holding locks on every table
    it has touched).
    """
    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(autocommit=True)
        with conn.cursor() as cur:
            for path in paths:
                try:
                    cur.execute(path.read_text())
                except psycopg2.Error as exc:
                    raise AssertionError(f"{path.name} failed to apply: {exc}") from exc
    finally:
        conn.close()


def _describe(dsn: str) -> dict:
    with closing(psycopg2.connect(dsn)) as conn:
        return describe_schema(conn)


class MigrationOrderTests(unittest.TestCase):
    """Pure ordering logic -- no database."""

    def test_versions_are_parsed_as_integers(self):
        self.assertEqual(_version(Path("V10__later.sql")), 10)
        self.assertEqual(_version(Path("V2__positions_multi_owner.sql")), 2)

    def test_v10_sorts_after_v9_not_before(self):
        paths = [Path("V10__ten.sql"), Path("V9__nine.sql"), Path("V2__two.sql")]
        self.assertEqual(
            [p.name for p in sorted(paths, key=_version)],
            ["V2__two.sql", "V9__nine.sql", "V10__ten.sql"],
        )

    def test_non_migration_filename_is_rejected(self):
        with self.assertRaises(ValueError):
            _version(Path("afterMigrate__seed.sql"))

    def test_migration_versions_are_unique(self):
        """Two branches numbering their migration V7 is the one collision git
        allows silently: both files apply cleanly on the branch that wrote them,
        and the merge produces two V7 files rather than a conflict. Flyway then
        refuses to migrate ("Found more than one migration with version 7"), on
        a deployed database, at rollout time. Catch it in CI instead.

        The contiguity check below would also fail on a duplicate, but on the
        wrong grounds -- it would report a gap at the far end of the sequence.
        """
        counts = Counter(_version(p) for p in MIGRATIONS_DIR.glob("V*.sql"))
        duplicates = {
            version: sorted(p.name for p in MIGRATIONS_DIR.glob(f"V{version}__*.sql"))
            for version, count in counts.items()
            if count > 1
        }
        self.assertEqual(
            duplicates,
            {},
            f"duplicate Flyway migration versions: {duplicates}. Renumber the "
            f"newer file -- Flyway keys its ledger on the version, not the name.",
        )

    def test_baseline_comes_first_and_files_are_contiguous(self):
        paths = sql_files_in_order()
        self.assertTrue(paths, "no SQL files found -- wrong repo layout?")
        self.assertEqual(paths[0].name, "V1__quantcore_baseline.sql")
        versions = [_version(p) for p in paths]
        self.assertEqual(
            versions,
            list(range(1, len(versions) + 1)),
            f"Flyway versions are not contiguous from V1: {versions}",
        )


class SkipGuardTests(unittest.TestCase):
    """The skip path must not be reachable in CI.

    A guard that quietly skips is the failure mode this whole plan exists to
    remove: the build goes green and nobody learns the two owners diverged.
    These cases pin the behaviour so a later edit can't soften it back into a
    silent skip.
    """

    def setUp(self):
        self.case = SchemaParityTests("test_init_schema_and_flyway_files_agree")
        self._original_ci = os.environ.get("CI")

    def tearDown(self):
        if self._original_ci is None:
            os.environ.pop("CI", None)
        else:
            os.environ["CI"] = self._original_ci

    def test_skips_when_ci_is_not_set(self):
        os.environ.pop("CI", None)
        with self.assertRaises(unittest.SkipTest):
            self.case._skip_or_fail("database unreachable")

    def test_fails_instead_of_skipping_when_ci_is_set(self):
        os.environ["CI"] = "true"
        with self.assertRaises(self.failureException) as ctx:
            self.case._skip_or_fail("database unreachable")
        self.assertIn("must run in CI", str(ctx.exception))
        self.assertIn("database unreachable", str(ctx.exception))


class SchemaParityTests(unittest.TestCase):
    """init_schema() and the Flyway files must produce the same schema."""

    def _skip_or_fail(self, reason: str) -> None:
        """Skip locally, but never in CI.

        A guard that quietly skips is worse than no guard: the build goes
        green and nobody learns the two owners diverged. CI is the
        authoritative run, so there the same condition is a failure.
        """
        if os.environ.get("CI"):
            self.fail(f"schema parity must run in CI, but would have skipped: {reason}")
        self.skipTest(reason)

    def test_init_schema_and_flyway_files_agree(self):
        paths = sql_files_in_order()
        try:
            with scratch_database(db.DB_DSN) as code_dsn:
                db.init_schema(code_dsn)
                code_schema = _describe(code_dsn)

                with scratch_database(db.DB_DSN) as sql_dsn:
                    _apply_sql_files(sql_dsn, paths)
                    sql_schema = _describe(sql_dsn)
        except ScratchDatabaseUnavailable as exc:
            self._skip_or_fail(f"cannot build scratch databases: {exc}")
            return
        except psycopg2.OperationalError as exc:
            self._skip_or_fail(f"database unreachable: {exc}")
            return

        # expected = the code, actual = the SQL files. So MISSING means the
        # migrations never got a change init_schema() has, and EXTRA means the
        # reverse.
        differences = diff_schemas(code_schema, sql_schema)
        self.assertEqual(
            differences,
            [],
            "init_schema() and db/baseline + db/migrations disagree.\n"
            "expected = init_schema(), actual = "
            + " -> ".join(p.name for p in paths)
            + "\n\n"
            + "\n".join(differences),
        )


if __name__ == "__main__":
    unittest.main()
