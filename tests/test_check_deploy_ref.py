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
        for job in ("gate", "lean-import", "frontend-gate", "secret-scan"):
            with self.subTest(job=job):
                checkout = self.jobs[job]["steps"][0]
                self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
                self.assertEqual(checkout["with"]["ref"], "${{ inputs.ref }}")
        self.assertEqual(self.jobs["gate"]["outputs"]["sha"], "${{ steps.sha.outputs.sha }}")

    def test_preflight_admits_dispatch_only_from_main(self):
        self.assertIn("github.event_name == 'workflow_dispatch'", self.jobs["preflight"]["if"])
        body = self.step("preflight", "has_creds")
        self.assertIn('"$GITHUB_REF" != refs/heads/main', body)
        self.assertIn("exit 1", body)

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


if __name__ == "__main__":
    unittest.main()
