"""scripts/ensure_news_job.sh -- the quantcore-news Job + Scheduler setup (#275, #372).

The invoker grant for the Scheduler's service account must run on every
invocation, not only when the Scheduler entry is created: an existing entry
without it fails every night with code 7 (#372).

Runs the script against a stub `gcloud` on PATH that records every call and
answers the describes from environment flags. Nothing reaches Google Cloud.
"""
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ensure_news_job.sh"

REPORT_JOB = {
    "spec": {"template": {"spec": {"template": {"spec": {"containers": [{"env": [
        {"name": "QUANTCORE_DB_DSN",
         "valueFrom": {"secretKeyRef": {"name": "quantcore-db-dsn", "key": "latest"}}},
        {"name": "PLAIN", "value": "x"},
    ]}]}}}}},
}

# Answers `describe` from STUB_* flags; logs every call, one per line, to $STUB_LOG.
STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
case "$*" in
  "run jobs describe quantcore-news"*) [[ -n "${STUB_JOB_EXISTS:-}" ]] ;;
  "run jobs describe quantcore-report"*"--format=json"*) cat "$STUB_REPORT" ;;
  "run jobs describe quantcore-report"*) echo "value-from-report" ;;
  "scheduler jobs describe quantcore-news-daily"*"serviceAccountEmail"*)
    [[ -n "${STUB_NEWS_SA:-}" ]] && echo "$STUB_NEWS_SA"; [[ -n "${STUB_NEWS_SA:-}" ]] ;;
  "scheduler jobs describe quantcore-news-daily"*) [[ -n "${STUB_NEWS_SA:-}" ]] ;;
  "scheduler jobs describe quantcore-report-daily"*)
    [[ -n "${STUB_REPORT_SA:-}" ]] && echo "$STUB_REPORT_SA"; [[ -n "${STUB_REPORT_SA:-}" ]] ;;
  "projects describe"*) echo 123456 ;;
  *) true ;;
esac
"""

NEWS_SA = "news-sched@p.iam.gserviceaccount.com"
REPORT_SA = "report-sched@p.iam.gserviceaccount.com"


class EnsureNewsJobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        gcloud = d / "gcloud"
        gcloud.write_text(STUB)
        gcloud.chmod(gcloud.stat().st_mode | stat.S_IEXEC)
        self.log = d / "calls.log"
        report = d / "report.json"
        report.write_text(json.dumps(REPORT_JOB))
        self.env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}",
                    "STUB_LOG": str(self.log), "STUB_REPORT": str(report)}

    def run_script(self, *args, stdin="", **flags):
        r = subprocess.run(["bash", str(SCRIPT), *args], input=stdin, capture_output=True,
                           text=True, timeout=30, env={**self.env, **flags})
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return r, calls

    @staticmethod
    def find(calls, prefix):
        return [c for c in calls if c.startswith(prefix)]

    def test_grants_invoker_even_when_job_and_scheduler_exist(self):
        r, calls = self.run_script(STUB_JOB_EXISTS="1", STUB_NEWS_SA=NEWS_SA,
                                   STUB_REPORT_SA=REPORT_SA)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.find(calls, "run jobs create"), [])
        self.assertEqual(self.find(calls, "scheduler jobs create"), [])
        grants = self.find(calls, "run jobs add-iam-policy-binding quantcore-news")
        self.assertEqual(len(grants), 1)
        self.assertIn(f"--member serviceAccount:{NEWS_SA}", grants[0])
        self.assertIn("--role roles/run.invoker", grants[0])
        self.assertIn("--project quantcore-test-20260606", grants[0])

    def test_falls_back_to_the_report_schedulers_sa_and_creates_both(self):
        r, calls = self.run_script(STUB_REPORT_SA=REPORT_SA)
        self.assertEqual(r.returncode, 0, r.stderr)
        create = self.find(calls, "run jobs create quantcore-news")
        self.assertEqual(len(create), 1)
        self.assertIn("--set-secrets QUANTCORE_DB_DSN=quantcore-db-dsn:latest", create[0])
        sched = self.find(calls, "scheduler jobs create http quantcore-news-daily")
        self.assertEqual(len(sched), 1)
        self.assertIn(f"--oauth-service-account-email {REPORT_SA}", sched[0])
        self.assertIn("/namespaces/123456/jobs/quantcore-news:run", sched[0])
        grants = self.find(calls, "run jobs add-iam-policy-binding quantcore-news")
        self.assertEqual(len(grants), 1)
        self.assertIn(f"serviceAccount:{REPORT_SA}", grants[0])

    def test_no_scheduler_sa_is_an_error_and_grants_nothing(self):
        r, calls = self.run_script(STUB_JOB_EXISTS="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no Scheduler service account", r.stderr)
        self.assertEqual(self.find(calls, "run jobs add-iam-policy-binding"), [])
        self.assertEqual(self.find(calls, "scheduler jobs create"), [])

    def test_schedule_is_left_alone_on_an_existing_entry_by_default(self):
        r, calls = self.run_script(STUB_JOB_EXISTS="1", STUB_NEWS_SA=NEWS_SA)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.find(calls, "scheduler jobs update"), [])

    def test_schedule_flag_updates_an_existing_entry(self):
        # #373: test's slot is reproducible from the script, not a one-off gcloud.
        r, calls = self.run_script("--schedule", "15 18 * * 1-5", STUB_JOB_EXISTS="1",
                                   STUB_NEWS_SA=NEWS_SA)
        self.assertEqual(r.returncode, 0, r.stderr)
        upd = self.find(calls, "scheduler jobs update http quantcore-news-daily")
        self.assertEqual(len(upd), 1)
        self.assertIn("--schedule 15 18 * * 1-5", upd[0])
        self.assertIn("--time-zone America/New_York", upd[0])
        self.assertIn("--project quantcore-test-20260606", upd[0])
        self.assertEqual(len(self.find(calls, "run jobs add-iam-policy-binding")), 1)

    def test_schedule_flag_is_used_on_create(self):
        r, calls = self.run_script("--schedule", "15 18 * * 1-5", STUB_REPORT_SA=REPORT_SA)
        self.assertEqual(r.returncode, 0, r.stderr)
        sched = self.find(calls, "scheduler jobs create http quantcore-news-daily")
        self.assertIn("--schedule 15 18 * * 1-5", sched[0])
        self.assertEqual(self.find(calls, "scheduler jobs update"), [])

    def test_default_schedule_on_create(self):
        r, calls = self.run_script(STUB_REPORT_SA=REPORT_SA)
        sched = self.find(calls, "scheduler jobs create http quantcore-news-daily")
        self.assertIn("--schedule 30 17 * * 1-5", sched[0])

    def test_bad_arguments_fail_before_any_gcloud_call(self):
        for args in (["--schedule"], ["--bogus"]):
            with self.subTest(args):
                r, calls = self.run_script(*args)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(calls, [])

    def test_prod_prompts_and_aborts_without_yes(self):
        r, calls = self.run_script("--prod", stdin="n\n", STUB_JOB_EXISTS="1",
                                   STUB_NEWS_SA=NEWS_SA)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(calls, [])

    def test_prod_targets_the_prod_project_after_yes(self):
        r, calls = self.run_script("--prod", stdin="y\n", STUB_JOB_EXISTS="1",
                                   STUB_NEWS_SA=NEWS_SA)
        self.assertEqual(r.returncode, 0, r.stderr)
        grants = self.find(calls, "run jobs add-iam-policy-binding quantcore-news")
        self.assertIn("--project quantcore-prod-20260606", grants[0])


if __name__ == "__main__":
    unittest.main()
