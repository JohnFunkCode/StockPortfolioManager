"""scripts/check_deploy_ref.py and deploy.yml's dispatch-a-ref wiring (issue #120).

The script is checked against throwaway git repos: a "main" with one migration and a
cloudbuild.yaml, and a ref branched from it. No network, no database.
"""
import contextlib
import io
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import check_deploy_ref

ROOT = Path(__file__).resolve().parent.parent
DEPLOY_YML = ROOT / ".github" / "workflows" / "deploy.yml"


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   text=True)


def cloudbuild_for(images):
    return "".join(f"  - us-docker.pkg.dev/p/r/{img}:${{_TAG}}\n" for img in sorted(images))


class RepoCase(unittest.TestCase):
    """A repo whose `main` builds every required image and has V2__base.sql."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "db" / "migrations").mkdir(parents=True)
        (self.repo / "db" / "baseline").mkdir(parents=True)
        (self.repo / "db" / "baseline" / "V1__baseline.sql").write_text("-- v1\n")
        (self.repo / "db" / "migrations" / "V2__base.sql").write_text("-- v2\n")
        (self.repo / "cloudbuild.yaml").write_text(
            cloudbuild_for(check_deploy_ref.required_images()))
        self.commit("main")
        git(self.repo, "checkout", "-q", "-b", "feature")

    def tearDown(self):
        self._tmp.cleanup()

    def commit(self, msg):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", msg)

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_main(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check_deploy_ref.main(["--ref-dir", str(self.repo), "--base", "main"])
        return code, out.getvalue()


class CheckDeployRefTest(RepoCase):
    def test_identical_to_main_is_ok(self):
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("ref ok", out)

    def test_code_only_change_is_ok(self):
        self.write("app.py", "x = 1\n")
        self.commit("code")
        self.assertEqual(self.run_main()[0], 0)

    def test_added_migration_is_refused(self):
        self.write("db/migrations/V3__new.sql", "-- v3\n")
        self.commit("migration")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("::error title=ref cannot deploy to test::", out)
        self.assertIn("A db/migrations/V3__new.sql", out)

    def test_modified_migration_is_refused(self):
        self.write("db/migrations/V2__base.sql", "-- v2 edited\n")
        self.commit("edit")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("M db/migrations/V2__base.sql", out)

    def test_modified_baseline_is_refused(self):
        self.write("db/baseline/V1__baseline.sql", "-- edited\n")
        self.commit("edit baseline")
        self.assertIn("M db/baseline/V1__baseline.sql", self.run_main()[1])

    def test_ref_behind_main_is_ok(self):
        # main gains a migration after the ref was cut: Flyway ignores *:future.
        git(self.repo, "checkout", "-q", "main")
        self.write("db/migrations/V3__later.sql", "-- v3\n")
        self.commit("main moves on")
        git(self.repo, "checkout", "-q", "feature")
        self.assertEqual(self.run_main()[0], 0)

    def test_deleted_migration_is_refused(self):
        # PR #310 review: an applied migration missing locally fails Flyway's validate.
        (self.repo / "db" / "migrations" / "V2__base.sql").unlink()
        self.commit("delete")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("D db/migrations/V2__base.sql", out)

    def test_deleted_middle_migration_is_refused(self):
        git(self.repo, "checkout", "-q", "main")
        self.write("db/migrations/V3__mid.sql", "-- v3\n")
        self.write("db/migrations/V4__top.sql", "-- v4\n")
        self.commit("main gains V3, V4")
        git(self.repo, "checkout", "-q", "-B", "feature")
        (self.repo / "db" / "migrations" / "V3__mid.sql").unlink()
        self.commit("drop the middle one")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("D db/migrations/V3__mid.sql", out)
        self.assertNotIn("V4__top", out)

    def test_ref_behind_main_with_its_own_code_is_ok(self):
        # Files main gained later look deleted in a plain two-dot diff; they aren't the ref's.
        self.write("app.py", "x = 1\n")
        self.commit("code")
        git(self.repo, "checkout", "-q", "main")
        self.write("db/migrations/V3__later.sql", "-- v3\n")
        self.commit("main moves on")
        git(self.repo, "checkout", "-q", "feature")
        code, out = self.run_main()
        self.assertEqual(code, 0, out)

    def test_migration_main_has_since_taken_is_ok(self):
        # The ref added V3 and main merged the same file: nothing is ahead of main.
        self.write("db/migrations/V3__new.sql", "-- v3\n")
        self.commit("migration")
        git(self.repo, "checkout", "-q", "main")
        self.write("db/migrations/V3__new.sql", "-- v3\n")
        self.commit("same migration lands on main")
        git(self.repo, "checkout", "-q", "feature")
        self.assertEqual(self.run_main()[0], 0)

    def test_ref_missing_an_image_is_refused(self):
        images = check_deploy_ref.required_images() - {"quantcore-migrate"}
        self.write("cloudbuild.yaml", cloudbuild_for(images))
        self.commit("old cloudbuild")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("does not build quantcore-migrate", out)

    def test_ref_without_cloudbuild_is_refused(self):
        (self.repo / "cloudbuild.yaml").unlink()
        self.commit("no cloudbuild")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("does not build quantcore-api", out)

    def test_unknown_base_fails_loudly(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check_deploy_ref.main(["--ref-dir", str(self.repo), "--base", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("::error title=deploy ref check failed::", out.getvalue())


class RealInventoryTest(unittest.TestCase):
    def test_required_images_cover_inventory_and_jobs(self):
        required = check_deploy_ref.required_images()
        for img in ("quantcore-api", "quantcore-mcp", "quantcore-ui", "quantcore-keyproxy",
                    "quantcore-migrate", "quantcore-report", "quantcore-news"):
            self.assertIn(img, required)

    def test_this_checkout_builds_every_required_image(self):
        self.assertEqual(
            check_deploy_ref.required_images() - check_deploy_ref.built_images(ROOT), set())


class DispatchWiringTest(unittest.TestCase):
    """deploy.yml can roll any ref to test, and never moves :latest off main."""

    @classmethod
    def setUpClass(cls):
        cls.text = DEPLOY_YML.read_text()
        cls.doc = yaml.safe_load(cls.text)
        # PyYAML reads the bare `on:` key as True.
        cls.on = cls.doc.get("on", cls.doc.get(True))
        cls.jobs = cls.doc["jobs"]

    def step(self, job, needle):
        steps = [s for s in self.jobs[job]["steps"] if needle in s.get("run", "")]
        self.assertEqual(len(steps), 1, f"{job}: expected one step running {needle!r}")
        return steps[0]["run"]

    def test_ref_input(self):
        ref = self.on["workflow_dispatch"]["inputs"]["ref"]
        self.assertTrue(ref["required"])
        self.assertEqual(ref["default"], "main")

    def test_gates_check_out_the_ref(self):
        for job in ("gate", "lean-import", "frontend-gate"):
            with self.subTest(job=job):
                checkout = self.jobs[job]["steps"][0]
                self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
                self.assertEqual(checkout["with"]["ref"], "${{ inputs.ref }}")
        self.assertEqual(self.jobs["gate"]["outputs"]["sha"], "${{ steps.sha.outputs.sha }}")

    def test_deploy_admits_dispatch_only_from_main(self):
        # #351: the preflight credentials probe is gone. A main push or a dispatch always
        # deploys, and missing WIF secrets fail at the auth step instead of skipping quietly.
        cond = self.jobs["deploy"]["if"]
        self.assertIn("github.event_name == 'workflow_dispatch'", cond)
        # No repository guard: a rename or transfer must not silently stop every deploy.
        self.assertNotIn("github.repository", cond)
        first = self.jobs["deploy"]["steps"][0]
        self.assertNotIn("uses", first, "the refusal must run before either checkout")
        self.assertIn('"$GITHUB_REF" != refs/heads/main', first["run"])
        self.assertIn("exit 1", first["run"])
        self.assertNotIn("preflight", self.jobs)
        self.assertNotIn("has_creds", self.text)

    def test_deploy_builds_the_gated_sha(self):
        deploy = self.jobs["deploy"]
        self.assertEqual(deploy["env"]["DEPLOY_SHA"], "${{ needs.gate.outputs.sha }}")
        ref_checkout = [s for s in deploy["steps"]
                        if s.get("with", {}).get("path") == "ref"]
        self.assertEqual(len(ref_checkout), 1)
        self.assertEqual(ref_checkout[0]["with"]["ref"], "${{ needs.gate.outputs.sha }}")
        self.assertNotIn("${GITHUB_SHA", self.text)
        build = self.step("deploy", "gcloud builds submit")
        self.assertIn("gcloud builds submit ref ", build)
        self.assertIn("--config ref/cloudbuild.yaml", build)
        self.assertIn('_TAG="${DEPLOY_SHA::7}"', build)
        self.assertIn("check_deploy_ref.py --ref-dir ref --base origin/main",
                      self.step("deploy", "check_deploy_ref.py"))

    def test_dispatch_never_moves_latest(self):
        build = self.step("deploy", "gcloud builds submit")
        self.assertIn("LATEST_TAG=dispatch-latest CACHE_TAG=dispatch", build)
        self.assertIn("LATEST_TAG=latest CACHE_TAG=main", build)
        self.assertIn('_LATEST_TAG="$LATEST_TAG",_CACHE_TAG="$CACHE_TAG"', build)

    def test_summary_names_ref_and_sha(self):
        body = self.step("deploy", "GITHUB_STEP_SUMMARY")
        self.assertIn("Deploying to TEST", body)
        self.assertIn("${DEPLOY_REF}", body)
        self.assertIn("${DEPLOY_SHA}", body)

    def test_ref_input_never_interpolated_into_a_shell(self):
        # Free text from the dispatch form: only `with:`/`env:` may carry it.
        for name, job in self.jobs.items():
            for s in job.get("steps", []):
                with self.subTest(job=name, step=s.get("name") or s.get("id")):
                    self.assertIsNone(re.search(r"\$\{\{\s*inputs\.", s.get("run", "")))


WORKFLOWS = ROOT / ".github" / "workflows"


def load_workflow(name):
    doc = yaml.safe_load((WORKFLOWS / name).read_text())
    doc["on"] = doc.get("on", doc.get(True))   # PyYAML reads a bare `on:` as True
    return doc


class CiCostWiringTest(unittest.TestCase):
    """Each assertion pins one #351 decision (docs/proposals/ci-minutes-plan.md)."""

    @classmethod
    def setUpClass(cls):
        cls.doc = load_workflow("deploy.yml")
        cls.jobs = cls.doc["jobs"]

    def step_named(self, job, name):
        found = [s for s in self.jobs[job]["steps"] if s.get("name") == name]
        self.assertEqual(len(found), 1, f"{job}: expected one step named {name!r}")
        return found[0]

    def test_secret_scan_lives_in_lean_import(self):
        steps = self.jobs["lean-import"]["steps"]
        self.assertEqual(steps[0]["with"]["fetch-depth"], 0)   # full range for gitleaks
        ids = [s.get("id") or s.get("name") for s in steps]
        gitleaks = self.step_named("lean-import", "Secret scan (gitleaks)")
        self.assertTrue(gitleaks["uses"].startswith("gitleaks/gitleaks-action@"))
        self.assertEqual(gitleaks["env"]["GITLEAKS_CONFIG"], ".gitleaks.toml")
        # The classifier runs first, so its outputs exist even when a later step fails.
        self.assertLess(ids.index("changes"), ids.index("Secret scan (gitleaks)"))
        # ...and a secret hit doesn't hide the import smoke (or the reverse).
        after = steps[ids.index("Secret scan (gitleaks)") + 1:]
        self.assertTrue(after)
        for s in after:
            self.assertEqual(s.get("if"), "${{ !cancelled() }}", s.get("name"))

    def test_folded_jobs_are_gone(self):
        self.assertIn("lean-import", self.jobs["deploy"]["needs"])
        self.assertNotIn("secret-scan", self.jobs)
        self.assertNotIn("preflight", self.jobs)

    def test_lean_import_exports_the_classification(self):
        out = self.jobs["lean-import"]["outputs"]
        self.assertEqual(out["deps_changed"], "${{ steps.changes.outputs.deps_changed }}")
        self.assertEqual(out["frontend_changed"],
                         "${{ steps.changes.outputs.frontend_changed }}")
        run = self.jobs["lean-import"]["steps"][1]["run"]
        self.assertIn("scripts/ci_changes.sh", run)
        self.assertIn("$GITHUB_OUTPUT", run)

    def test_docs_only_changes_run_nothing(self):
        # Exactly **.md: docs/openapi-surface.txt is read by the gates, so never docs/**.
        for event in ("push", "pull_request"):
            with self.subTest(event=event):
                self.assertEqual(self.doc["on"][event]["paths-ignore"], ["**.md"])
        self.assertNotIn("paths-ignore", self.doc["on"]["workflow_dispatch"] or {})

    def test_skips_fail_open(self):
        # `!= 'false'`: an empty output (classifier never ran) still runs the gate.
        self.assertIn("needs.lean-import.outputs.frontend_changed != 'false'",
                      self.jobs["frontend-gate"]["if"])
        self.assertIn("needs.lean-import.outputs.deps_changed != 'false'",
                      self.jobs["dep-audit"]["if"])

    def test_deploy_tolerates_a_skipped_frontend_gate(self):
        cond = self.jobs["deploy"]["if"]
        self.assertIn("!cancelled()", cond)
        self.assertIn("!contains(needs.*.result, 'failure')", cond)

    def test_every_job_has_a_timeout(self):
        for name in ("deploy.yml", "dep-audit.yml", "prod-rollout.yml", "deps-lock-update.yml"):
            for job_name, job in load_workflow(name)["jobs"].items():
                with self.subTest(workflow=name, job=job_name):
                    if "uses" in job:
                        # A reusable-workflow call can't carry one; its called job does.
                        called = load_workflow(Path(job["uses"]).name)
                        for inner in called["jobs"].values():
                            self.assertIsInstance(inner.get("timeout-minutes"), int)
                    else:
                        self.assertIsInstance(job.get("timeout-minutes"), int)

    def test_only_pr_runs_cancel_in_progress(self):
        # Never a bare `true`: that would cut a main roll-out off mid-deploy.
        self.assertEqual(self.doc["concurrency"]["cancel-in-progress"],
                         "${{ github.event_name == 'pull_request' }}")

    def test_coverage_html_uploads_only_on_failure(self):
        uploads = [(job, s) for job in ("gate", "frontend-gate")
                   for s in self.jobs[job]["steps"]
                   if s.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 2)
        for job, s in uploads:
            with self.subTest(job=job):
                self.assertEqual(s.get("if"), "failure()")


if __name__ == "__main__":
    unittest.main()
