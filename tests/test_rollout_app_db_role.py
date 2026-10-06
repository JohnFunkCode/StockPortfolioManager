"""scripts/rollout_app_db_role.sh — the #308 operator runbook as one script.

Runs the script against a stub `gcloud` whose Secret Manager is a directory of
files (one file per secret, holding its latest version), a stub Python that logs
its arguments and does `--swap-dsn`'s user swap, and a stub migrate-Job script.
Nothing reaches Google Cloud or a database.
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "rollout_app_db_role.sh"

PASSWORD = "test-app-db-password-308-not-real"
OWNER_DSN = f"postgresql://quantcore:{PASSWORD}@127.0.0.1:5432/quantcore"
APP_DSN = f"postgresql://quantcore_app:{PASSWORD}@127.0.0.1:5432/quantcore"

GCLOUD = r"""#!/usr/bin/env bash
printf 'gcloud %s\n' "$*" >> "$STUB_LOG"
name=""; prev=""
for a in "$@"; do
  [[ "$prev" == --secret ]] && name="$a"
  prev="$a"
done
case "$*" in
  "secrets versions access"*) cat "$STUB_SECRETS/$name" ;;
  "secrets describe "*) [[ -f "$STUB_SECRETS/$3" ]] ;;
  "secrets create "*) [[ ! -f "$STUB_SECRETS/$3" ]] && cat > "$STUB_SECRETS/$3" ;;
  # Write then rename: in `access | ... | add` on one secret, `add` must not
  # truncate the file before `access` has read it (real versions are immutable).
  "secrets versions add "*) cat > "$STUB_SECRETS/.new" && mv "$STUB_SECRETS/.new" "$STUB_SECRETS/$4" ;;
  *) exit 9 ;;
esac
"""

PYTHON = r"""#!/usr/bin/env bash
printf 'python %s pw=%s\n' "$*" "${QUANTCORE_APP_DB_PASSWORD:+set}" >> "$STUB_LOG"
if [[ "$*" == *--swap-dsn* ]]; then sed 's#://quantcore:#://quantcore_app:#'; exit 0; fi
[[ "$*" == *--dry-run* ]] && exit "${STUB_VERIFY_RC:-0}"
exit "${STUB_ROLE_RC:-0}"
"""

MIGRATE = r"""#!/usr/bin/env bash
printf 'migrate %s\n' "$*" >> "$STUB_LOG"
exit "${STUB_MIGRATE_RC:-0}"
"""


class RolloutAppDbRoleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        for name, body in (("gcloud", GCLOUD), ("python", PYTHON), ("migrate", MIGRATE)):
            p = d / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        self.secrets = d / "secrets"
        self.secrets.mkdir()
        self.log = d / "calls.log"
        self.env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}",
                    "STUB_LOG": str(self.log), "STUB_SECRETS": str(self.secrets),
                    "PYTHON": str(d / "python"), "MIGRATE_JOB_SCRIPT": str(d / "migrate")}
        self.env.pop("QUANTCORE_APP_DB_PASSWORD", None)

    def secret(self, name, value=None):
        path = self.secrets / name
        if value is not None:
            path.write_text(value + "\n")
        return path.read_text().strip() if path.exists() else None

    def run_script(self, *args, stdin="", **flags):
        r = subprocess.run(["bash", str(SCRIPT), *args], input=stdin, capture_output=True,
                           text=True, timeout=30, env={**self.env, **flags})
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return r, calls

    def assert_no_credential_in_output(self, r):
        self.assertNotIn(PASSWORD, r.stdout + r.stderr)

    def test_full_rollout_on_test_runs_the_steps_in_order(self):
        self.secret("quantcore-test-db-dsn", OWNER_DSN)
        r, calls = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_no_credential_in_output(r)
        self.assertEqual(self.secret("quantcore-test-migrator-dsn"), OWNER_DSN)
        self.assertEqual(self.secret("quantcore-test-db-dsn"), APP_DSN)
        steps = [c for c in calls if not c.startswith(("gcloud secrets versions access",
                                                       "gcloud secrets describe"))]
        self.assertEqual(steps[:3], [
            "python scripts/ensure_app_db_role.py --test pw=set",
            "gcloud secrets create quantcore-test-migrator-dsn"
            " --project quantcore-test-20260606 --data-file=-",
            "migrate --execute",
        ])
        # The swap is one pipeline (access | --swap-dsn | versions add); its
        # stages start together, so the order they log in is a race.
        self.assertCountEqual(steps[3:], [
            "python scripts/ensure_app_db_role.py --swap-dsn pw=set",
            "gcloud secrets versions add quantcore-test-db-dsn"
            " --project quantcore-test-20260606 --data-file=-",
        ])
        self.assertIn("now connects as quantcore_app", r.stdout)

    def test_prod_needs_yes_and_passes_prod_everywhere(self):
        self.secret("quantcore-prod-db-dsn", OWNER_DSN)
        r, calls = self.run_script("--prod", stdin="no\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(calls, [])
        r, calls = self.run_script("--prod", stdin="yes\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("python scripts/ensure_app_db_role.py --prod pw=set", calls)
        self.assertIn("migrate --prod --execute", calls)
        self.assertTrue(all("quantcore-test" not in c for c in calls))
        self.assertEqual(self.secret("quantcore-prod-db-dsn"), APP_DSN)

    def test_already_rolled_out_only_verifies(self):
        self.secret("quantcore-test-db-dsn", APP_DSN)
        self.secret("quantcore-test-migrator-dsn", OWNER_DSN)
        r, calls = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Verifying only", r.stdout)
        self.assertEqual([c for c in calls if not c.startswith("gcloud secrets versions access")],
                         ["python scripts/ensure_app_db_role.py --test --dry-run pw="])
        self.assertEqual(self.secret("quantcore-test-db-dsn"), APP_DSN)

    def test_verify_failure_is_the_exit_code(self):
        self.secret("quantcore-test-db-dsn", APP_DSN)
        r, _ = self.run_script(STUB_VERIFY_RC="1")
        self.assertEqual(r.returncode, 1)

    def test_unknown_app_secret_user_stops_before_any_change(self):
        self.secret("quantcore-test-db-dsn", OWNER_DSN.replace("quantcore:", "someone:", 1))
        r, calls = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertIn("'someone'", r.stderr)
        self.assertFalse([c for c in calls if c.startswith(("python", "migrate"))])
        self.assert_no_credential_in_output(r)

    def test_existing_migrator_secret_is_kept(self):
        self.secret("quantcore-test-db-dsn", OWNER_DSN)
        self.secret("quantcore-test-migrator-dsn", OWNER_DSN)
        r, calls = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse([c for c in calls if c.startswith("gcloud secrets create")])
        self.assertEqual(self.secret("quantcore-test-db-dsn"), APP_DSN)

    def test_migrator_secret_holding_another_user_stops(self):
        self.secret("quantcore-test-db-dsn", OWNER_DSN)
        self.secret("quantcore-test-migrator-dsn", APP_DSN)
        r, calls = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertFalse([c for c in calls if c.startswith("migrate")])
        self.assertEqual(self.secret("quantcore-test-db-dsn"), OWNER_DSN)

    def test_role_step_failure_changes_no_secret(self):
        self.secret("quantcore-test-db-dsn", OWNER_DSN)
        r, calls = self.run_script(STUB_ROLE_RC="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("proxy", r.stderr)
        self.assertIsNone(self.secret("quantcore-test-migrator-dsn"))
        self.assertEqual(self.secret("quantcore-test-db-dsn"), OWNER_DSN)

    def test_migrate_failure_leaves_the_app_secret_alone_and_reruns_cleanly(self):
        self.secret("quantcore-test-db-dsn", OWNER_DSN)
        r, _ = self.run_script(STUB_MIGRATE_RC="1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.secret("quantcore-test-db-dsn"), OWNER_DSN)
        r, _ = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.secret("quantcore-test-db-dsn"), APP_DSN)

    def test_rollback_restores_the_owner_dsn(self):
        self.secret("quantcore-test-db-dsn", APP_DSN)
        self.secret("quantcore-test-migrator-dsn", OWNER_DSN)
        r, calls = self.run_script("--rollback")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_no_credential_in_output(r)
        self.assertEqual(self.secret("quantcore-test-db-dsn"), OWNER_DSN)
        self.assertFalse([c for c in calls if c.startswith(("python", "migrate"))])

    def test_rollback_without_migrator_secret_refuses(self):
        self.secret("quantcore-test-db-dsn", APP_DSN)
        r, _ = self.run_script("--rollback")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.secret("quantcore-test-db-dsn"), APP_DSN)

    def test_unknown_argument_is_a_usage_error(self):
        r, calls = self.run_script("--bogus")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
