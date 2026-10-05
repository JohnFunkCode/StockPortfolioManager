"""scripts/setup_test_wif.sh — the test WIF provider's mapping and trust condition (#313).

The script runs against a stub `gcloud` (and a no-op `sleep`) on PATH. The stub records each
call's argv exactly, one call per line with arguments separated by \\x1f, so a test can assert
the precise `--attribute-condition` string rather than a whitespace-joined approximation.
Nothing reaches Google Cloud.
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "setup_test_wif.sh"

REPO = "JohnFunkCode/StockPortfolioManager"
EXPECTED_MAPPING = (
    "google.subject=assertion.sub,"
    "attribute.repository=assertion.repository,"
    "attribute.ref=assertion.ref,"
    "attribute.workflow_ref=assertion.job_workflow_ref"
)
EXPECTED_CONDITION = f"assertion.repository=='{REPO}' && assertion.ref=='refs/heads/main'"

# Every call is logged. The provider `describe` succeeds only when STUB_PROVIDER_EXISTS is set,
# which selects the script's update-oidc path over create-oidc.
GCLOUD_STUB = r"""#!/usr/bin/env bash
for a in "$@"; do printf '%s\x1f' "$a"; done >> "$STUB_LOG"
printf '\n' >> "$STUB_LOG"
case "$*" in
  "projects describe "*) echo 111222333 ;;
  "iam workload-identity-pools providers describe "*) [[ -n "${STUB_PROVIDER_EXISTS:-}" ]] ;;
esac
exit $?
"""


class SetupTestWifTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        bindir = tmp / "bin"
        bindir.mkdir()
        for name, body in (("gcloud", GCLOUD_STUB), ("sleep", "#!/bin/sh\nexit 0\n")):
            path = bindir / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.log = tmp / "gcloud.log"
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp),
            "STUB_LOG": str(self.log),
            # Skips the script's own `run services describe` lookup of the runtime SA.
            "RUNTIME_SA": "run@example.iam.gserviceaccount.com",
        }

    def _run(self, provider_exists):
        env = dict(self.env)
        if provider_exists:
            env["STUB_PROVIDER_EXISTS"] = "1"
        result = subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [line.split("\x1f")[:-1] for line in self.log.read_text().splitlines()]
        return calls

    @staticmethod
    def _find(calls, verb):
        return [c for c in calls if c[:4] == ["iam", "workload-identity-pools", "providers", verb]]

    @staticmethod
    def _flag(call, name):
        i = call.index(name)
        return call[i + 1]

    def _assert_trust(self, call):
        self.assertEqual(self._flag(call, "--attribute-mapping"), EXPECTED_MAPPING)
        condition = self._flag(call, "--attribute-condition")
        self.assertEqual(condition, EXPECTED_CONDITION)
        # Defence in depth on the exact-match above: the ref clause can't silently disappear.
        self.assertIn("assertion.ref=='refs/heads/main'", condition)
        self.assertNotIn("*", condition)
        self.assertEqual(self._flag(call, "--workload-identity-pool"), "github-test")
        self.assertEqual(self._flag(call, "--project"), "quantcore-test-20260606")

    def test_missing_provider_is_created_with_main_only_condition(self):
        calls = self._run(provider_exists=False)
        creates = self._find(calls, "create-oidc")
        self.assertEqual(len(creates), 1, calls)
        self.assertEqual(self._find(calls, "update-oidc"), [])
        self._assert_trust(creates[0])
        self.assertEqual(
            self._flag(creates[0], "--issuer-uri"), "https://token.actions.githubusercontent.com"
        )

    def test_existing_provider_is_updated_to_main_only_condition(self):
        calls = self._run(provider_exists=True)
        updates = self._find(calls, "update-oidc")
        self.assertEqual(len(updates), 1, calls)
        self.assertEqual(self._find(calls, "create-oidc"), [])
        self._assert_trust(updates[0])


if __name__ == "__main__":
    unittest.main()
