"""The hash-pinned dependency locks and their wiring (issue #218).

Static checks only: no network, no database, no uv. Whether each lock still matches its
requirements-*.txt input is the gate's `scripts/lock_deps.sh --check` step.
"""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

# Dockerfile -> the lock it must install.
DOCKERFILE_LOCKS = {
    "Dockerfile.api": "requirements-ml.lock",
    "Dockerfile.news": "requirements-ml.lock",
    "Dockerfile.mcp": "requirements-base.lock",
    "Dockerfile.report": "requirements-base.lock",
    "Dockerfile.keyproxy": "keyproxy/requirements.lock",
}
LOCKS = ["requirements-base.lock", "requirements-ml.lock", "requirements-dev.lock",
         "keyproxy/requirements.lock", "requirements.lock"]
PIN_RE = re.compile(r"^([A-Za-z0-9_.\-\[\]]+)==(\S+)")


def pins(lock):
    """{name: version} for every requirement line, plus whether each one carries a hash."""
    out, hashed = {}, {}
    lines = (ROOT / lock).read_text().splitlines()
    for i, line in enumerate(lines):
        m = PIN_RE.match(line)
        if not m:
            continue
        name = m.group(1).lower().split("[")[0]
        out[name] = m.group(2)
        hashed[name] = i + 1 < len(lines) and "--hash=sha256:" in lines[i + 1]
    return out, hashed


def steps(workflow):
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    return {name: [s.get("run", "") for s in job.get("steps", [])]
            for name, job in doc["jobs"].items()}


class LockFilesTest(unittest.TestCase):
    def test_every_pin_is_exact_and_hashed(self):
        for lock in LOCKS:
            with self.subTest(lock=lock):
                versions, hashed = pins(lock)
                self.assertGreater(len(versions), 5)
                self.assertEqual([n for n, h in hashed.items() if not h], [])

    def test_locks_are_generated_by_the_script(self):
        for lock in LOCKS:
            with self.subTest(lock=lock):
                self.assertIn("scripts/lock_deps.sh", (ROOT / lock).read_text()[:400])

    def test_ml_and_dev_agree_with_base(self):
        # Both are compiled with -c requirements-base.lock: a shared package has one pin.
        base, _ = pins("requirements-base.lock")
        for lock in ("requirements-ml.lock", "requirements-dev.lock"):
            other, _ = pins(lock)
            with self.subTest(lock=lock):
                self.assertEqual({n: (v, other[n]) for n, v in base.items()
                                  if n in other and other[n] != v}, {})
                self.assertEqual(sorted(set(base) - set(other)), [])

    def test_base_lock_stays_lean(self):
        # The layering the locks keep: no report or ML packages in the container base.
        base, _ = pins("requirements-base.lock")
        for pkg in ("matplotlib", "jinja2", "boto3", "torch", "transformers"):
            self.assertNotIn(pkg, base)

    def test_torch_is_the_cpu_build(self):
        ml, _ = pins("requirements-ml.lock")
        self.assertTrue(ml["torch"].endswith("+cpu"), ml["torch"])
        self.assertIn("--extra-index-url https://download.pytorch.org/whl/cpu",
                      (ROOT / "requirements-ml.lock").read_text())


class InstallWiringTest(unittest.TestCase):
    def test_dockerfiles_install_their_lock_with_hashes(self):
        for dockerfile, lock in DOCKERFILE_LOCKS.items():
            text = (ROOT / dockerfile).read_text()
            installed = Path(lock).name
            with self.subTest(dockerfile=dockerfile):
                self.assertIn(f"COPY {lock} ./", text)
                self.assertIn(f"pip install --require-hashes -r {installed}", text)
                # An unpinned pip upgrade would be the one thing left floating.
                self.assertIsNone(re.search(r"^RUN .*--upgrade pip", text, re.M))
                self.assertIsNone(re.search(r"pip install .*-r requirements[\w-]*\.txt", text))

    def test_ci_installs_the_locks(self):
        deploy = steps("deploy.yml")
        self.assertIn("pip install --require-hashes -r requirements-dev.lock",
                      "\n".join(deploy["gate"]))
        self.assertIn("scripts/lock_deps.sh --check", "\n".join(deploy["gate"]))
        self.assertIn("pip install --require-hashes -r requirements-base.lock",
                      "\n".join(deploy["lean-import"]))
        self.assertIn("scripts/audit_deps.sh", "\n".join(deploy["dep-audit"]))

    def test_prod_rollout_gate_stays_base_only(self):
        # The lean-import property: prod-rollout tests on the base set alone.
        runs = "\n".join(r for job in steps("prod-rollout.yml").values() for r in job)
        self.assertIn("pip install --require-hashes -r requirements-base.lock", runs)
        self.assertNotIn("requirements-dev", runs)
        self.assertNotIn("requirements-ml", runs)

    def test_dep_audit_is_not_a_rollout_gate(self):
        doc = yaml.safe_load((WORKFLOWS / "deploy.yml").read_text())
        self.assertNotIn("dep-audit", doc["jobs"]["deploy"]["needs"])

    def test_deps_epoch_is_gone(self):
        # The hashed lock keys the pip layer now; the weekly cache bust (#278) is obsolete.
        for rel in ["cloudbuild.yaml", ".github/workflows/deploy.yml",
                    ".github/workflows/prod-rollout.yml", *DOCKERFILE_LOCKS]:
            with self.subTest(file=rel):
                self.assertNotIn("DEPS_EPOCH=", (ROOT / rel).read_text())
                self.assertNotIn("ARG DEPS_EPOCH", (ROOT / rel).read_text())

    def test_update_workflow_relocks_and_audits(self):
        runs = "\n".join(steps("deps-lock-update.yml")["update"])
        self.assertIn("scripts/lock_deps.sh --upgrade", runs)
        self.assertIn("scripts/audit_deps.sh", runs)
        self.assertIn("gh pr create --base main", runs)

    def test_update_workflow_runs_only_from_the_default_branch(self):
        # workflow_dispatch can target any branch, and the job holds DEPS_PR_TOKEN (#315 review).
        doc = yaml.safe_load((WORKFLOWS / "deps-lock-update.yml").read_text())
        self.assertEqual(
            doc["jobs"]["update"].get("if"),
            "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)")


if __name__ == "__main__":
    unittest.main()
