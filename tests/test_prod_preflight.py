"""scripts/prod_preflight.py, the no-prod-access job before prod-rollout's approval (#346).

gcloud and gh are stubbed through the script's `_run` seam; git and the migrate image's
own scanner (db/migrate-entrypoint.sh) run for real against a throwaway repo.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import prod_preflight as pf

ROOT = Path(__file__).resolve().parent.parent
REG = "us-docker.pkg.dev/p/r"
REAL_RUN = pf._run


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def done(stdout="", rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout, stderr)


class PreflightCase(unittest.TestCase):
    """A repo with one promoted commit (`self.prev`) and a fake test AR."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "commit.gpgsign", "false")
        self.write("db/migrations/V2__base.sql", "CREATE TABLE t (id int);\n")
        self.prev = self.commit("promoted")
        self.digests = {img: f"sha256:{i:064x}" for i, img in enumerate(pf.IMAGES, 1)}
        self.tags = {}            # 7-hex tags on the api digest
        self.artifact_sha = None  # what the last successful run recorded

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, msg):
        # One commit per second of history, so "newest" never ties on %ct.
        self._n = getattr(self, "_n", 0) + 1
        when = f"2026-10-11T00:00:{self._n:02d}"
        git(self.repo, "add", "-A")
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "--allow-empty",
                        "-m", msg], check=True, capture_output=True,
                       env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        return git(self.repo, "rev-parse", "--short=7", "HEAD")

    def fake_run(self, cmd, cwd=None):
        if cmd[:4] == ["gcloud", "artifacts", "docker", "images"]:
            img = cmd[5].split("/")[-1].split(":")[0]
            digest = self.digests.get(img)
            return done(digest + "\n") if digest else done(rc=1, stderr="NOT_FOUND")
        if cmd[:4] == ["gcloud", "artifacts", "docker", "tags"]:
            return done(json.dumps([{"tag": f"projects/p/locations/l/repositories/r/"
                                            f"packages/quantcore-api/tags/{t}"}
                                    for t in [*self.tags, "latest"]]))
        if cmd[:3] == ["gh", "run", "list"]:
            return done(json.dumps([{"databaseId": 7}]))
        if cmd[:3] == ["gh", "run", "download"]:
            if self.artifact_sha is None:
                return done(rc=1, stderr="no artifact")
            Path(cmd[cmd.index("-D") + 1], pf.ARTIFACT_FILE).write_text(self.artifact_sha + "\n")
            return done()
        return REAL_RUN(cmd, cwd)  # git, and the entrypoint's check_pending

    def run_main(self, *args, tag="latest"):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            outputs = Path(d) / "out"
            summary = Path(d) / "summary"
            env = {"GITHUB_OUTPUT": str(outputs), "GITHUB_STEP_SUMMARY": str(summary)}
            with mock.patch.object(pf, "_run", self.fake_run), \
                    mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
                rc = pf.main(["--tag", tag, "--registry", REG, "--repo", str(self.repo), *args])
            self.outputs = dict(l.split("=", 1) for l in outputs.read_text().splitlines()) \
                if outputs.exists() else {}
            self.summary = summary.read_text() if summary.exists() else ""
        return rc, out.getvalue()


class ResolveTest(PreflightCase):
    def test_outputs_bind_every_digest_and_the_commit(self):
        sha = self.commit("next")
        self.tags = {sha: 1}
        self.artifact_sha = self.prev
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.outputs["sha"], sha)
        for img in pf.IMAGES:
            self.assertEqual(self.outputs[pf.output_key(img)], self.digests[img])
        self.assertIn(self.digests["quantcore-migrate"], self.summary)
        self.assertIn(f"0 since {self.prev}", self.summary)

    def test_newest_known_sha_tag_wins(self):
        old = self.prev
        new = self.commit("docs-only rebuild, same api image")
        self.tags = {old: 1, new: 1, "abcdef0": 1}  # abcdef0: not in this clone
        self.artifact_sha = self.prev
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.outputs["sha"], new)

    def test_a_sha_tag_is_used_as_the_commit(self):
        sha = self.commit("next")
        self.artifact_sha = self.prev
        self.assertEqual(self.run_main(tag=sha)[0], 0)
        self.assertEqual(self.outputs["sha"], sha)

    def test_no_sha_tag_fails(self):
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("no 7-hex commit tag", out)

    def test_missing_required_image_fails(self):
        for img in ("quantcore-migrate", "quantcore-api"):
            with self.subTest(img=img):
                saved = self.digests.pop(img)
                rc, out = self.run_main(tag=self.prev)
                self.digests[img] = saved
                self.assertEqual(rc, 1)
                self.assertIn(f"{img} not found", out)

    def test_absent_optional_images_are_skipped(self):
        for img in pf.OPTIONAL:
            del self.digests[img]
        self.artifact_sha = self.prev
        rc, out = self.run_main(tag=self.prev)
        self.assertEqual(rc, 0, out)
        for img in pf.OPTIONAL:
            self.assertEqual(self.outputs[pf.output_key(img)], "")


class MigrationTest(PreflightCase):
    def test_refused_migration_fails_before_the_approval(self):
        self.write("db/migrations/V3__drop.sql", "DROP TABLE t;\n")
        sha = self.commit("contract")
        self.artifact_sha = self.prev
        rc, out = self.run_main(tag=sha)
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"::error title=refused migration::.*V3__drop\.sql")
        self.assertIn("allow_refused_migrations", out)
        self.assertIn("(REFUSED)", self.summary)
        self.assertEqual(self.outputs["sha"], sha)  # still recorded for the summary

    def test_allow_flag_lets_a_hand_applied_refusal_through(self):
        self.write("db/migrations/V3__drop.sql", "DROP TABLE t;\n")
        sha = self.commit("contract")
        self.artifact_sha = self.prev
        rc, out = self.run_main("--allow-refused-migrations", tag=sha)
        self.assertEqual(rc, 0, out)
        self.assertIn("::warning title=refused migration::", out)
        self.assertNotIn("::error", out)

    def test_expand_migration_passes(self):
        self.write("db/migrations/V3__add.sql", "ALTER TABLE t ADD COLUMN x int;\n")
        sha = self.commit("expand")
        self.artifact_sha = self.prev
        rc, out = self.run_main(tag=sha)
        self.assertEqual(rc, 0, out)
        self.assertIn("V3__add.sql (none refused)", out)

    def test_already_promoted_refusal_is_not_rescanned(self):
        self.write("db/migrations/V3__drop.sql", "DROP TABLE t;\n")
        promoted = self.commit("contract, promoted last time")
        sha = self.commit("next")
        self.artifact_sha = promoted
        self.assertEqual(self.run_main(tag=sha)[0], 0)

    def test_no_artifact_warns_and_passes(self):
        self.write("db/migrations/V3__drop.sql", "DROP TABLE t;\n")
        sha = self.commit("contract")
        rc, out = self.run_main(tag=sha)
        self.assertEqual(rc, 0)
        self.assertIn("::warning title=migrations not pre-checked::", out)
        self.assertIn("not checked", self.summary)

    def test_previous_sha_flag_skips_the_artifact_lookup(self):
        self.write("db/migrations/V3__drop.sql", "DROP TABLE t;\n")
        sha = self.commit("contract")
        self.assertEqual(self.run_main("--previous-sha", self.prev, tag=sha)[0], 1)


class WorkflowAgreementTest(unittest.TestCase):
    def test_images_match_the_promote_loop(self):
        wf = (ROOT / ".github/workflows/prod-rollout.yml").read_text()
        loop = re.search(r"for img in ([^;]+); do", wf).group(1).split()
        self.assertEqual(tuple(loop), pf.IMAGES)

    def test_every_image_has_a_preflight_output(self):
        wf = (ROOT / ".github/workflows/prod-rollout.yml").read_text()
        for img in pf.IMAGES:
            self.assertIn(f"needs.preflight.outputs.{pf.output_key(img)}", wf)


if __name__ == "__main__":
    unittest.main()
