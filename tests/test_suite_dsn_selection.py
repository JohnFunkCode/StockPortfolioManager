"""Which ``.env`` key the test suite connects with (issue #289).

``tests/__init__.py`` prefers a local Postgres (QUANTCORE_UNITTEST_DB_DSN) over
Cloud SQL test (QUANTCORE_TEST_DB_DSN). The fallback has to keep working
exactly as before, or every ``.env`` without the new key breaks on pull.
"""
import unittest

from tests import _select_test_dsn

LOCAL = "postgresql://u:p@localhost:5432/quantcore_test"
CLOUD = "postgresql://u:p@127.0.0.1:5434/quantcore"


class SelectTestDsnTest(unittest.TestCase):
    def test_local_key_wins_when_present(self):
        env = f"QUANTCORE_TEST_DB_DSN={CLOUD}\nQUANTCORE_UNITTEST_DB_DSN={LOCAL}\n"
        self.assertEqual(_select_test_dsn(env, None), LOCAL)

    def test_falls_back_to_cloud_sql_test_without_the_local_key(self):
        env = f"QUANTCORE_DB_DSN=postgresql://prod\nQUANTCORE_TEST_DB_DSN={CLOUD}\n"
        self.assertEqual(_select_test_dsn(env, None), CLOUD)

    def test_cloudsql_opt_in_forces_the_fallback(self):
        env = f"QUANTCORE_UNITTEST_DB_DSN={LOCAL}\nQUANTCORE_TEST_DB_DSN={CLOUD}\n"
        self.assertEqual(_select_test_dsn(env, "cloudsql"), CLOUD)

    def test_commented_and_empty_local_key_are_ignored(self):
        env = (
            f"#QUANTCORE_UNITTEST_DB_DSN={LOCAL}\n"
            "QUANTCORE_UNITTEST_DB_DSN=\n"
            f"QUANTCORE_TEST_DB_DSN={CLOUD}\n"
        )
        self.assertEqual(_select_test_dsn(env, None), CLOUD)

    def test_neither_key_leaves_the_environment_alone(self):
        self.assertIsNone(_select_test_dsn("QUANTCORE_DB_DSN=postgresql://prod\n", None))


if __name__ == "__main__":
    unittest.main()
