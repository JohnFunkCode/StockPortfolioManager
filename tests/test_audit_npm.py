"""scripts/npm_audit_filter.py, the judge behind scripts/audit_npm.sh (issue #354).

Fed hand-built `npm audit --json` reports; no npm, no network.
"""
import re
import unittest
from pathlib import Path

from scripts.npm_audit_filter import judge

ROOT = Path(__file__).resolve().parent.parent
LOCK = "frontend/server/package-lock.json"
TODAY = "2026-10-09"


def advisory(name, severity, ghsa, transitive_parents=()):
    vulns = {name: {"severity": severity, "via": [{
        "source": 1, "name": name, "severity": severity, "range": "<9",
        "url": f"https://github.com/advisories/{ghsa}", "title": f"{name} is bad"}]}}
    child = name
    for parent in transitive_parents:
        vulns[parent] = {"severity": severity, "via": [child]}
        child = parent
    return {"vulnerabilities": vulns}


def exception(ghsa, expires="2027-01-01", lock=LOCK):
    return f"{ghsa}|{lock}|{expires}|reason"


class JudgeTest(unittest.TestCase):
    def test_clean_report_passes(self):
        self.assertEqual(judge({"vulnerabilities": {}}, LOCK, [], TODAY), ([], 0))

    def test_high_and_critical_fail(self):
        for sev in ("high", "critical"):
            with self.subTest(sev=sev):
                lines, rc = judge(advisory("qs", sev, "GHSA-aaaa-bbbb-cccc"), LOCK, [], TODAY)
                self.assertEqual(rc, 1)
                self.assertTrue(any(l.startswith("::error") and "GHSA-aaaa-bbbb-cccc" in l
                                    for l in lines))

    def test_runtime_moderate_fails(self):
        # qs 6.16.0 (GHSA-6pq8-jhm2-2vcw) shipped in quantui unflagged while the bar was high.
        lines, rc = judge(advisory("qs", "moderate", "GHSA-aaaa-bbbb-cccc"), LOCK, [], TODAY)
        self.assertEqual(rc, 1)
        self.assertTrue(any(l.startswith("::error") and "GHSA-aaaa-bbbb-cccc" in l for l in lines))

    def test_runtime_low_is_listed_but_passes(self):
        lines, rc = judge(advisory("qs", "low", "GHSA-aaaa-bbbb-cccc"), LOCK, [], TODAY)
        self.assertEqual(rc, 0)
        self.assertTrue(any("GHSA-aaaa-bbbb-cccc" in l for l in lines))
        self.assertFalse(any(l.startswith("::") for l in lines))

    def test_transitive_entries_are_one_finding(self):
        report = advisory("braces", "high", "GHSA-x", ("micromatch", "http-proxy-middleware"))
        lines, rc = judge(report, LOCK, [], TODAY)
        self.assertEqual(rc, 1)
        self.assertEqual(sum(l.startswith("::error") for l in lines), 1)

    def test_exception_excuses_only_its_own_advisory_and_lock(self):
        report = advisory("braces", "high", "GHSA-x")
        self.assertEqual(judge(report, LOCK, [exception("GHSA-x")], TODAY)[1], 0)
        self.assertEqual(judge(report, LOCK, [exception("GHSA-y")], TODAY)[1], 1)
        other = exception("GHSA-x", lock="frontend/package-lock.json")
        self.assertEqual(judge(report, LOCK, [other], TODAY)[1], 1)

    def test_expired_exception_fails(self):
        lines, rc = judge(advisory("braces", "high", "GHSA-x"), LOCK,
                          [exception("GHSA-x", expires="2026-10-08")], TODAY)
        self.assertEqual(rc, 1)
        self.assertTrue(any("expired npm audit exception" in l for l in lines))

    def test_stale_exception_warns_without_failing(self):
        lines, rc = judge({"vulnerabilities": {}}, LOCK, [exception("GHSA-x")], TODAY)
        self.assertEqual(rc, 0)
        self.assertTrue(any(l.startswith("::warning") and "GHSA-x" in l for l in lines))

    def test_npm_error_or_garbage_fails(self):
        # A failed audit (no network) must not read as a clean one.
        self.assertEqual(judge({"error": {"summary": "ENOTFOUND"}}, LOCK, [], TODAY)[1], 1)
        self.assertEqual(judge(None, LOCK, [], TODAY)[1], 1)


DEV = {"node_modules/undici": {"dev": True}}


class DevTreeTest(unittest.TestCase):
    """A dev-only finding warns at high+ and never fails; the lock's flags decide what is dev."""

    def test_dev_high_and_critical_warn_without_failing(self):
        for sev in ("high", "critical"):
            with self.subTest(sev=sev):
                lines, rc = judge(advisory("undici", sev, "GHSA-x"), LOCK, [], TODAY, DEV)
                self.assertEqual(rc, 0)
                self.assertTrue(any(l.startswith("::warning") and "GHSA-x" in l for l in lines))
                self.assertFalse(any(l.startswith("::error") for l in lines))

    def test_dev_moderate_is_listed_only(self):
        lines, rc = judge(advisory("undici", "moderate", "GHSA-x"), LOCK, [], TODAY, DEV)
        self.assertEqual(rc, 0)
        self.assertTrue(any("GHSA-x" in l and "dev" in l for l in lines))
        self.assertFalse(any(l.startswith("::") for l in lines))

    def test_dev_optional_counts_as_runtime(self):
        # npm marks a package devOptional when a dev dependency needs it and a production
        # dependency optionally does too; `npm ci --omit=dev` still installs it. Reproduced with
        # optional express 4.22.3 + dev qs 6.16.2: qs gets {"devOptional": true} and ships.
        packages = {"node_modules/qs": {"devOptional": True}}
        for sev in ("moderate", "high", "critical"):
            with self.subTest(sev=sev):
                lines, rc = judge(advisory("qs", sev, "GHSA-x"), LOCK, [], TODAY, packages)
                self.assertEqual(rc, 1)
                self.assertTrue(any(l.startswith("::error") and "GHSA-x" in l for l in lines))
                self.assertFalse(any(l.startswith("::warning") for l in lines))
                self.assertTrue(any("runtime" in l and "qs" in l for l in lines))

    def test_only_dev_true_demotes(self):
        # Truthy-but-not-True values (a corrupted or hand-edited lock) must not demote a finding.
        for flag in ({"dev": "true"}, {"dev": 1}, {"optional": True}, {"peer": True}):
            with self.subTest(flag=flag):
                packages = {"node_modules/qs": flag}
                self.assertEqual(judge(advisory("qs", "moderate", "GHSA-x"), LOCK, [], TODAY,
                                       packages)[1], 1)

    def test_one_runtime_install_makes_it_runtime(self):
        report = advisory("undici", "moderate", "GHSA-x")
        report["vulnerabilities"]["undici"]["nodes"] = [
            "node_modules/undici", "node_modules/jsdom/node_modules/undici"]
        packages = {"node_modules/undici": {"dev": True},
                    "node_modules/jsdom/node_modules/undici": {}}
        self.assertEqual(judge(report, LOCK, [], TODAY, packages)[1], 1)

    def test_package_missing_from_lock_is_runtime(self):
        # A lock the filter couldn't read must fail closed, not demote findings to warnings.
        self.assertEqual(judge(advisory("qs", "moderate", "GHSA-x"), LOCK, [], TODAY, {})[1], 1)

    def test_excepted_dev_advisory_does_not_warn(self):
        lines, rc = judge(advisory("undici", "high", "GHSA-x"), LOCK, [exception("GHSA-x")],
                          TODAY, DEV)
        self.assertEqual(rc, 0)
        self.assertFalse(any(l.startswith("::warning") for l in lines))


class AuditScriptTest(unittest.TestCase):
    def test_audits_the_whole_tree(self):
        # --omit=dev would hide the dev tree from the filter, so dev warnings could never fire.
        script = (ROOT / "scripts" / "audit_npm.sh").read_text()
        audit = re.search(r"npm audit [^|)]*", script).group(0)
        self.assertNotIn("--omit", audit)


class ExceptionListTest(unittest.TestCase):
    def test_every_exception_is_well_formed_and_bounded(self):
        script = (ROOT / "scripts" / "audit_npm.sh").read_text()
        locks = re.search(r"LOCKS=\(([^)]*)\)", script).group(1).split()
        match = re.search(r"EXCEPTIONS=\((.*?)\n\)", script, re.S)
        self.assertIsNotNone(match, "keep EXCEPTIONS=( ... ) multi-line, closing ')' on its own line")
        body = match.group(1)
        entries = re.findall(r'"([^"]+)"', body)
        for entry in entries:
            ghsa, lock, expires, why = entry.split("|", 3)
            with self.subTest(ghsa=ghsa):
                self.assertRegex(ghsa, r"^GHSA-[\w]{4}-[\w]{4}-[\w]{4}$")
                self.assertIn(lock, locks)
                self.assertRegex(expires, r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(why.strip())


if __name__ == "__main__":
    unittest.main()
