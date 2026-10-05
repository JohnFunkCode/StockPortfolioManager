"""scripts/ensure_migrate_job.sh — one-time setup of the quantcore-migrate Job (#200, Step 3).

Runs the script against a stub `gcloud` on PATH that records every call and answers
the describes from environment flags. Nothing reaches Google Cloud.
"""
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ensure_migrate_job.sh"

REPORT_JOB = {
    "spec": {"template": {
        "metadata": {"annotations": {
            "run.googleapis.com/cloudsql-instances": "proj:us-central1:inst"}},
        "spec": {"template": {"spec": {"containers": [{"env": [
            {"name": "DISCORD_WEBHOOK_URL",
             "valueFrom": {"secretKeyRef": {"name": "discord-webhook", "key": "latest"}}},
            {"name": "QUANTCORE_DB_DSN",
             "valueFrom": {"secretKeyRef": {"name": "quantcore-db-dsn", "key": "3"}}},
            {"name": "PLAIN", "value": "x"},
        ]}]}}},
    }},
}

# Answers `describe` from STUB_* flags; logs every call, one per line, to $STUB_LOG.
STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
case "$*" in
  "run jobs describe quantcore-report"*) cat "$STUB_REPORT" ;;
  "run jobs describe quantcore-migrate"*) [[ -n "${STUB_JOB_EXISTS:-}" ]] ;;
  "iam service-accounts describe"*) [[ -n "${STUB_SA_EXISTS:-}" ]] ;;
  *) [[ -z "${STUB_FAIL_MUTATIONS:-}" ]] ;;
esac
"""


class EnsureMigrateJobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        gcloud = d / "gcloud"
        gcloud.write_text(STUB)
        gcloud.chmod(gcloud.stat().st_mode | stat.S_IEXEC)
        self.log = d / "calls.log"
        self.report = d / "report.json"
        self.report.write_text(json.dumps(REPORT_JOB))
        self.env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}",
                    "STUB_LOG": str(self.log), "STUB_REPORT": str(self.report)}
        self.env.pop("DEPLOYER_SA", None)

    def run_script(self, *args, stdin="", **flags):
        r = subprocess.run(["bash", str(SCRIPT), *args], input=stdin, capture_output=True,
                           text=True, timeout=30, env={**self.env, **flags})
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return r, calls

    @staticmethod
    def find(calls, prefix):
        return [c for c in calls if c.startswith(prefix)]

    def test_creates_sa_grants_and_job_on_test_by_default(self):
        r, calls = self.run_script("--tag", "trial-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Target project: quantcore-test-20260606", r.stdout)
        self.assertTrue(all("quantcore-prod" not in c for c in calls), calls)
        sa = "quantcore-migrate@quantcore-test-20260606.iam.gserviceaccount.com"
        self.assertEqual(len(self.find(calls, "iam service-accounts create quantcore-migrate ")), 1)
        (proj,) = self.find(calls, "projects add-iam-policy-binding")
        self.assertIn(f"serviceAccount:{sa} --role roles/cloudsql.client", proj)
        # secretAccessor on the DSN secret only, not the report Job's other secrets.
        (secret,) = self.find(calls, "secrets add-iam-policy-binding")
        self.assertTrue(secret.startswith("secrets add-iam-policy-binding quantcore-db-dsn "))
        self.assertIn("roles/secretmanager.secretAccessor", secret)
        (actas,) = self.find(calls, "iam service-accounts add-iam-policy-binding")
        self.assertIn("serviceAccount:quantcore-deployer@quantcore-test-20260606"
                      ".iam.gserviceaccount.com --role roles/iam.serviceAccountUser", actas)
        (create,) = self.find(calls, "run jobs create quantcore-migrate")
        self.assertIn("--image us-central1-docker.pkg.dev/quantcore-test-20260606/quantcore/"
                      "quantcore-migrate:trial-1", create)
        self.assertIn(f"--service-account {sa}", create)
        self.assertIn("--set-cloudsql-instances proj:us-central1:inst", create)
        self.assertIn("--set-secrets QUANTCORE_DB_DSN=quantcore-db-dsn:3", create)
        self.assertNotIn("discord", create)
        self.assertIn("--task-timeout 600s", create)
        self.assertIn("--max-retries 0", create)
        self.assertEqual(self.find(calls, "run jobs execute"), [])

    def test_existing_job_is_updated_with_update_flags_only(self):
        r, calls = self.run_script(STUB_JOB_EXISTS="1", STUB_SA_EXISTS="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.find(calls, "iam service-accounts create"), [])
        self.assertEqual(self.find(calls, "run jobs create"), [])
        (update,) = self.find(calls, "run jobs update quantcore-migrate")
        self.assertNotIn("--set-", update)
        self.assertNotIn("--clear-", update)
        self.assertNotIn("--image", update)  # CI owns the image once the Job exists
        self.assertIn("--update-secrets QUANTCORE_DB_DSN=quantcore-db-dsn:3", update)
        self.assertIn("--add-cloudsql-instances proj:us-central1:inst", update)
        self.assertIn("--max-retries 0", update)
        # Grants are re-asserted on every run; add-iam-policy-binding is idempotent.
        self.assertEqual(len(self.find(calls, "projects add-iam-policy-binding")), 1)

    def test_existing_job_takes_a_given_image(self):
        r, calls = self.run_script("--image", "reg/quantcore-migrate@sha256:abc",
                                   STUB_JOB_EXISTS="1", STUB_SA_EXISTS="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        (update,) = self.find(calls, "run jobs update")
        self.assertIn("--image reg/quantcore-migrate@sha256:abc", update)

    def test_creating_without_an_image_fails_before_any_change(self):
        r, calls = self.run_script()
        self.assertEqual(r.returncode, 2)
        self.assertIn("pass --tag or --image", r.stderr)
        # No SA, no grants, no Job: an invocation that cannot finish changes nothing.
        self.assertEqual([c for c in calls if " describe " not in f" {c} "], [])

    def test_tag_and_image_together_are_refused(self):
        r, calls = self.run_script("--tag", "a", "--image", "b")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(calls, [])

    def test_prod_prompts_and_aborts_without_y(self):
        r, calls = self.run_script("--prod", "--tag", "x", stdin="n\n")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(calls, [])

    def test_prod_targets_the_prod_project_and_its_deployer(self):
        r, calls = self.run_script("--prod", "--image", "reg/img@sha256:abc", stdin="y\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(all("quantcore-test" not in c for c in calls), calls)
        (actas,) = self.find(calls, "iam service-accounts add-iam-policy-binding")
        self.assertIn("quantcore-deployer@quantcore-prod-20260606", actas)

    def test_deployer_sa_can_be_overridden(self):
        r, calls = self.run_script("--tag", "t", DEPLOYER_SA="ci@other.iam.gserviceaccount.com")
        self.assertEqual(r.returncode, 0, r.stderr)
        (actas,) = self.find(calls, "iam service-accounts add-iam-policy-binding")
        self.assertIn("serviceAccount:ci@other.iam.gserviceaccount.com", actas)

    def test_dry_run_makes_only_reads(self):
        r, calls = self.run_script("--dry-run", "--prod", "--tag", "t", "--execute")
        self.assertEqual(r.returncode, 0, r.stderr)  # no prompt in a dry run
        self.assertTrue(all(" describe " in f" {c} " for c in calls), calls)
        self.assertIn("+ gcloud run jobs create quantcore-migrate", r.stdout)
        self.assertIn("+ gcloud run jobs execute quantcore-migrate", r.stdout)

    def test_execute_runs_the_job_and_waits(self):
        r, calls = self.run_script("--tag", "t", "--execute")
        self.assertEqual(r.returncode, 0, r.stderr)
        (ex,) = self.find(calls, "run jobs execute quantcore-migrate")
        self.assertIn("--wait", ex)

    def test_report_without_dsn_secret_stops_before_any_change(self):
        job = json.loads(json.dumps(REPORT_JOB))
        env = job["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["env"]
        env[:] = [e for e in env if e["name"] != "QUANTCORE_DB_DSN"]
        self.report.write_text(json.dumps(job))
        r, calls = self.run_script("--tag", "t")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no QUANTCORE_DB_DSN secret", r.stderr)
        self.assertEqual([c for c in calls if " describe " not in f" {c} "], [])

    def test_failed_report_describe_stops_the_script(self):
        self.report.unlink()  # the stub's `cat` fails, so the describe pipeline does
        r, calls = self.run_script("--tag", "t")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual([c for c in calls if " describe " not in f" {c} "], [])

    def test_a_failed_grant_stops_before_the_job(self):
        r, calls = self.run_script("--tag", "t", STUB_FAIL_MUTATIONS="1", STUB_SA_EXISTS="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.find(calls, "run jobs create"), [])


if __name__ == "__main__":
    unittest.main()
