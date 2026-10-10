"""scripts/ci_changes.sh, the classifier behind deploy.yml's job-level skips (issue #351).

Checked against throwaway git repos: a base commit, then one change on top. No network.
"""
import re
import subprocess
import tempfile
import unittest
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci_changes.sh"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


class CiChangesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "commit.gpgsign", "false")
        self.commit("readme.md")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self):
        self._tmp.cleanup()

    def commit(self, *paths):
        for rel in paths:
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"{rel} {len(list(self.repo.rglob('*')))}\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "change")

    def classify(self, base=None):
        out = subprocess.run(["bash", str(SCRIPT), self.base if base is None else base],
                             cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)   # never fails the job
        return dict(line.split("=", 1) for line in out.stdout.split())

    def assert_classified(self, deps, frontend, base=None):
        got = self.classify(base)
        self.assertEqual(got, {"deps_changed": deps, "frontend_changed": frontend})

    def test_lock_change_is_deps(self):
        self.commit("requirements-base.lock")
        self.assert_classified("true", "false")

    def test_keyproxy_lock_change_is_deps(self):
        self.commit("keyproxy/requirements.lock")
        self.assert_classified("true", "false")

    def test_npm_lock_change_is_deps_and_frontend(self):
        # #354: dep-audit.yml audits both npm locks; frontend-gate tests against them.
        for rel in ("frontend/package-lock.json", "frontend/server/package-lock.json"):
            with self.subTest(rel=rel):
                self.commit(rel)
                self.assert_classified("true", "true")
                self.base = git(self.repo, "rev-parse", "HEAD")

    def test_frontend_source_alone_is_not_deps(self):
        self.commit("frontend/package.json", "frontend/src/App.tsx")
        self.assert_classified("false", "true")

    def test_audit_wiring_is_deps(self):
        for rel in ("scripts/audit_deps.sh", ".github/workflows/dep-audit.yml",
                    "scripts/audit_npm.sh", "scripts/npm_audit_filter.py"):
            with self.subTest(rel=rel):
                self.commit(rel)
                self.assertEqual(self.classify()["deps_changed"], "true")

    def test_python_only_change_skips_both(self):
        self.commit("quantcore/services/foo.py", "tests/test_foo.py")
        self.assert_classified("false", "false")

    def test_requirements_txt_alone_is_not_deps(self):
        # A floor edit without a re-lock already fails the gate's lock_deps.sh --check.
        self.commit("requirements-base.txt")
        self.assert_classified("false", "false")

    def test_deploy_yml_is_frontend_not_deps(self):
        self.commit(".github/workflows/deploy.yml")
        self.assert_classified("false", "true")

    def test_frontend_changes(self):
        for rel in ("frontend/src/App.tsx", "frontend/server/index.js",
                    "tests/vectors/keyproxy_envelope_v1.json", "scripts/ci_changes.sh"):
            with self.subTest(rel=rel):
                self.commit(rel)
                self.assertEqual(self.classify()["frontend_changed"], "true")
                self.base = git(self.repo, "rev-parse", "HEAD")

    def test_diff_is_from_the_merge_base(self):
        # A PR is classified by what it changed, not by what main gained since it branched.
        git(self.repo, "checkout", "-q", "-b", "feature")
        self.commit("quantcore/x.py")
        git(self.repo, "checkout", "-q", "main")
        self.commit("frontend/src/App.tsx", "requirements-base.lock")
        main = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "feature")
        self.assert_classified("false", "false", base=main)

    def test_fails_open(self):
        for base in ("", "0" * 40, "deadbeef" * 5, "no-such-branch"):
            with self.subTest(base=base):
                self.commit("quantcore/x.py")
                self.assert_classified("true", "true", base=base)

    def test_every_audited_lock_is_a_deps_pattern(self):
        locks = []
        for audit in ("audit_deps.sh", "audit_npm.sh"):     # pip (#218) and npm (#354)
            text = (ROOT / "scripts" / audit).read_text()
            found = re.search(r"LOCKS=\(([^)]*)\)", text).group(1).split()
            self.assertTrue(found, audit)
            locks += found
        # The deps case arm may span backslash-continued lines.
        arm = re.search(r"(requirements\*\.lock\|.*?)\)\n", SCRIPT.read_text(), re.S).group(1)
        patterns = [p.strip() for p in arm.replace("\\\n", "").split("|")]
        for lock in locks:
            with self.subTest(lock=lock):
                self.assertTrue(any(fnmatch(lock, p) for p in patterns), lock)


if __name__ == "__main__":
    unittest.main()
