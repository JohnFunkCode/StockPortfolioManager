"""scripts/ci_parallel.sh — the concurrent roll-out runner both workflows source (#296)."""
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "scripts" / "ci_parallel.sh"
WORKFLOWS = [ROOT / ".github/workflows/deploy.yml", ROOT / ".github/workflows/prod-rollout.yml"]


def run(body):
    """Run `body` in bash -e (GitHub's default `run:` shell) with the library sourced."""
    with tempfile.TemporaryDirectory() as tmp:
        return subprocess.run(
            ["bash", "-e", "-c", f". '{LIB}'\n{body}"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "TMPDIR": tmp},  # run_parallel's mktemp -d lands here
        )


class RunParallelTest(unittest.TestCase):
    def test_all_succeed(self):
        r = run('ok() { echo "hello $1"; }\nrun_parallel "ok a" "ok b"\necho after')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("::group::ok a (exit 0,", r.stdout)
        self.assertIn("hello a", r.stdout)
        self.assertIn("hello b", r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith("after"))

    def test_entries_run_concurrently(self):
        t0 = time.monotonic()
        r = run("nap() { sleep 1; }\nrun_parallel nap nap nap nap")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.monotonic() - t0, 2.5)

    def test_one_failure_fails_the_step_and_names_the_entry(self):
        r = run(
            'ok() { echo "fine $1"; }\n'
            'bad() { echo "deploying $1"; return 3; }\n'
            'run_parallel "ok x" "bad quantcore-api" "ok y"\n'
            "echo not-reached"
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("not-reached", r.stdout)
        self.assertIn("::error title=bad quantcore-api failed::bad quantcore-api exited 3", r.stdout)
        self.assertIn("bad quantcore-api", r.stderr)
        # The siblings still ran to completion and their logs were printed.
        self.assertIn("fine x", r.stdout)
        self.assertIn("fine y", r.stdout)
        self.assertIn("deploying quantcore-api", r.stdout)
        self.assertEqual(r.stdout.count("::error"), 1)

    def test_every_failure_is_reported(self):
        r = run("bad() { return 1; }\nrun_parallel 'bad a' 'bad b'")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("::error title=bad a failed::", r.stdout)
        self.assertIn("::error title=bad b failed::", r.stdout)

    def test_a_function_stops_at_its_first_failing_command(self):
        # Each entry runs under set -e, like a step's own body would.
        r = run("two() { false; echo SHOULD-NOT-PRINT; }\nrun_parallel two")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("SHOULD-NOT-PRINT", r.stdout)

    def test_annotations_inside_a_log_survive(self):
        r = run("skip() { echo '::warning title=x missing::x not found'; }\nrun_parallel skip")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("\n::warning title=x missing::x not found\n", r.stdout)


class WorkflowWiringTest(unittest.TestCase):
    """Both workflows roll out through run_parallel, and keep their deploy invariants."""

    def rollout_step(self, path):
        doc = yaml.safe_load(path.read_text())
        steps = [s for job in doc["jobs"].values() for s in job.get("steps", [])]
        matches = [s for s in steps if "run_parallel" in s.get("run", "")]
        self.assertEqual(len(matches), 1, f"{path.name}: expected one roll-out step")
        uses = [s.get("uses", "") for s in steps]
        self.assertTrue(any(u.startswith("actions/checkout@") for u in uses),
                        f"{path.name}: the roll-out sources a repo script, so it needs checkout")
        return matches[0]["run"]

    def test_both_workflows(self):
        for path, env in ((WORKFLOWS[0], "test"), (WORKFLOWS[1], "prod")):
            with self.subTest(workflow=path.name):
                body = self.rollout_step(path)
                self.assertIn(". scripts/ci_parallel.sh", body)
                # api first, its consumers after: two phases.
                self.assertEqual(body.count("run_parallel "), 2)
                self.assertNotIn("--set-", body)
                # The services come from the inventory (#161), the Jobs stay in the step.
                for phase in (1, 2):
                    self.assertIn(f"cloudrun_services.py names --env {env} --phase {phase}",
                                  body)
                self.assertIn(f"cloudrun_services.py deploy \"$1\" --env {env}", body)
                self.assertEqual("--by-digest" in body, env == "prod")
                for job in ("update_report_job", "update_news_job"):
                    self.assertIn(f"{job}()", body)
                self.assertIn('run_parallel "${p1[@]}" update_report_job update_news_job',
                              body)
                self.assertIn('run_parallel "${p2[@]}"', body)
                # Sizing lives only in deploy/cloudrun-services.toml now.
                self.assertNotIn("API_MEMORY", path.read_text())


if __name__ == "__main__":
    unittest.main()
