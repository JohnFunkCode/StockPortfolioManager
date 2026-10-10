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

    def test_moderate_and_low_are_listed_but_pass(self):
        for sev in ("moderate", "low"):
            with self.subTest(sev=sev):
                lines, rc = judge(advisory("qs", sev, "GHSA-aaaa-bbbb-cccc"), LOCK, [], TODAY)
                self.assertEqual(rc, 0)
                self.assertTrue(any("GHSA-aaaa-bbbb-cccc" in l for l in lines))

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


class ExceptionListTest(unittest.TestCase):
    def test_every_exception_is_well_formed_and_bounded(self):
        script = (ROOT / "scripts" / "audit_npm.sh").read_text()
        locks = re.search(r"LOCKS=\(([^)]*)\)", script).group(1).split()
        body = re.search(r"EXCEPTIONS=\((.*?)\n\)", script, re.S).group(1)
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
