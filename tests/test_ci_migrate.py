"""scripts/ci_migrate.sh and its deploy.yml step — migrate before the roll-out (#200, Step 4).

The script runs against a stub `gcloud` on PATH that records every call; nothing reaches
Google Cloud. The wiring tests read the workflow itself.
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci_migrate.sh"
DEPLOY = ROOT / ".github/workflows/deploy.yml"
ARGS = ["--project", "quantcore-test-20260606", "--region", "us-central1",
        "--image", "reg/quantcore-migrate:abc1234"]

# Logs every call to $STUB_LOG and answers from STUB_* flags. `logging read` prints
# newest first, as the real one does with --order desc.
STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
case "$*" in
  "run jobs describe"*) [[ -n "${STUB_JOB_EXISTS:-}" ]] ;;
  "run jobs execute"*) [[ -z "${STUB_EXEC_FAILS:-}" ]] ;;
  "run jobs executions list"*) echo "quantcore-migrate-x7k2p" ;;
  "logging read"*)
    [[ -n "${STUB_LOGGING_DENIED:-}" ]] && { echo "PERMISSION_DENIED" >&2; exit 1; }
    printf '%s\n' "${STUB_LOG_LINES:-}" ;;
  *) [[ -z "${STUB_FAIL_UPDATE:-}" ]] ;;
esac
"""


class CiMigrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        gcloud = d / "gcloud"
        gcloud.write_text(STUB)
        gcloud.chmod(gcloud.stat().st_mode | stat.S_IEXEC)
        self.log = d / "calls.log"
        self.env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}", "STUB_LOG": str(self.log)}

    def run_script(self, *args, **flags):
        r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
                           timeout=30, env={**self.env, **flags})
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return r, calls

    @staticmethod
    def find(calls, prefix):
        return [c for c in calls if c.startswith(prefix)]

    def test_success_updates_the_image_then_executes_and_waits(self):
        r, calls = self.run_script(*ARGS, STUB_JOB_EXISTS="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        verbs = [c.split(" quantcore-migrate")[0] for c in calls]
        self.assertEqual(verbs, ["run jobs describe", "run jobs update", "run jobs execute"])
        (update,) = self.find(calls, "run jobs update quantcore-migrate")
        self.assertIn("--image reg/quantcore-migrate:abc1234", update)
        self.assertIn("--project quantcore-test-20260606 --region us-central1", update)
        self.assertNotIn("--set-", update)
        self.assertNotIn("--clear-", update)
        (ex,) = self.find(calls, "run jobs execute quantcore-migrate")
        self.assertIn("--wait", ex)
        self.assertNotIn("::error", r.stdout)

    def test_a_missing_job_fails_without_any_change(self):
        # Not a ::warning:: skip: rolling out without migrating is the #200 failure.
        r, calls = self.run_script(*ARGS)
        self.assertEqual(r.returncode, 1)
        self.assertIn("::error title=quantcore-migrate missing::", r.stdout)
        self.assertIn("ensure_migrate_job.sh", r.stdout)
        self.assertEqual([c for c in calls if not c.startswith("run jobs describe")], [])

    def test_a_failed_image_update_does_not_execute(self):
        r, calls = self.run_script(*ARGS, STUB_JOB_EXISTS="1", STUB_FAIL_UPDATE="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.find(calls, "run jobs execute"), [])

    def test_a_failed_migration_prints_its_log_in_order_and_fails(self):
        r, calls = self.run_script(*ARGS, STUB_JOB_EXISTS="1", STUB_EXEC_FAILS="1",
                                   STUB_LOG_LINES="line 3\nline 2\nline 1")
        self.assertEqual(r.returncode, 1)
        (read,) = self.find(calls, "logging read")
        self.assertIn('resource.labels.job_name="quantcore-migrate"', read)
        self.assertIn('"run.googleapis.com/execution_name"="quantcore-migrate-x7k2p"', read)
        self.assertIn("--project quantcore-test-20260606", read)
        self.assertIn("--order desc", read)
        self.assertIn("::group::quantcore-migrate execution quantcore-migrate-x7k2p", r.stdout)
        self.assertIn("line 1\nline 2\nline 3\n", r.stdout)  # reversed into reading order
        self.assertIn("::error title=migration failed::", r.stdout)
        self.assertIn("Fix forward", r.stdout)
        self.assertEqual(r.stdout.count("::error"), 1)

    def test_a_refused_contract_migration_points_at_the_manual_path(self):
        r, _ = self.run_script(*ARGS, STUB_JOB_EXISTS="1", STUB_EXEC_FAILS="1",
                               STUB_LOG_LINES="migrate: REFUSED V11__drop.sql: DROP TABLE x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("::error title=migration refused::", r.stdout)
        self.assertIn("./scripts/flyway.sh migrate", r.stdout)
        self.assertNotIn("title=migration failed", r.stdout)

    def test_unreadable_logs_still_fail_for_the_migration(self):
        # Without roles/logging.viewer the step must still fail, and say where to look.
        r, _ = self.run_script(*ARGS, STUB_JOB_EXISTS="1", STUB_EXEC_FAILS="1",
                               STUB_LOGGING_DENIED="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no log lines read", r.stdout)
        self.assertIn("console.cloud.google.com/run/jobs/details/us-central1/quantcore-migrate",
                      r.stdout)
        self.assertIn("::error title=migration failed::", r.stdout)

    def test_every_argument_is_required(self):
        for drop in ("--project", "--region", "--image"):
            with self.subTest(missing=drop):
                i = ARGS.index(drop)
                r, calls = self.run_script(*(ARGS[:i] + ARGS[i + 2:]))
                self.assertEqual(r.returncode, 2)
                self.assertEqual(calls, [])


class DeployWiringTest(unittest.TestCase):
    """deploy.yml migrates after the images are built and before anything rolls out.

    prod-rollout.yml joins in Step 5 of the plan.
    """

    def test_migrate_step_sits_between_build_and_rollout(self):
        doc = yaml.safe_load(DEPLOY.read_text())
        steps = doc["jobs"]["deploy"]["steps"]
        runs = [s.get("run", "") for s in steps]
        build = next(i for i, r in enumerate(runs) if "gcloud builds submit" in r)
        migrate = [i for i, r in enumerate(runs) if "scripts/ci_migrate.sh" in r]
        rollout = next(i for i, r in enumerate(runs) if "run_parallel" in r)
        self.assertEqual(len(migrate), 1)
        self.assertEqual(migrate[0], build + 1)
        self.assertEqual(rollout, migrate[0] + 1)
        step = steps[migrate[0]]
        # A failure must stop the job: no continue-on-error, no `if: always()`.
        self.assertNotIn("continue-on-error", step)
        self.assertNotIn("if", step)
        body = step["run"]
        self.assertIn('--project "$PROJECT_ID"', body)
        self.assertIn("quantcore-migrate:${GITHUB_SHA::7}", body)  # this commit's image
        self.assertNotIn(":latest", body)
        self.assertNotIn("--set-", body)

    def test_the_script_executes_and_waits(self):
        code = "\n".join(l for l in SCRIPT.read_text().splitlines()
                         if not l.lstrip().startswith("#"))  # the header says "never --set-*"
        self.assertIn("gcloud run jobs execute", code)
        self.assertIn("--wait", code)
        self.assertNotIn("--set-", code)
        self.assertTrue(os.access(SCRIPT, os.X_OK), "the workflow runs it directly")


if __name__ == "__main__":
    unittest.main()
