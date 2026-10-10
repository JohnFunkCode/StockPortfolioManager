"""Judge one `npm audit --json` report against scripts/audit_npm.sh's exceptions (issue #354).

    npm audit --package-lock-only --omit=dev --json | python3 scripts/npm_audit_filter.py LOCK [EXC...]

Each EXC is "GHSA id|lock|expires YYYY-MM-DD|why". Prints every advisory, then exits 1 if a high or
critical one is not excepted, an exception for this lock has expired, or the report is an error.
Stdlib only: dep-audit.yml runs it on a bare runner.
"""
import datetime
import json
import re
import sys

FAILING = ("high", "critical")


def judge(report, lock, exceptions, today):
    """(lines to print, exit code) for one lock's report."""
    error = _report_error(report, lock)
    if error:
        return [error], 1
    out, excepted = _active_exceptions(exceptions, lock, today)
    rc = 1 if out else 0
    seen = set()
    for name, via in _advisories(report):
        ghsa, lines, failed = _judge_advisory(name, via, lock, excepted)
        seen.add(ghsa)
        out += lines
        rc = rc or failed
    for ghsa in sorted(set(excepted) - seen):
        out.append(f"::warning title=stale npm audit exception::{ghsa} no longer appears in "
                   f"{lock}; delete it from scripts/audit_npm.sh.")
    return out, rc


def _report_error(report, lock):
    """An ::error line when npm audit failed rather than reported; a failure must not read clean."""
    if not isinstance(report, dict):
        return f"::error title=npm audit failed::{lock}: npm audit printed no JSON report"
    if "error" in report:
        summary = report["error"].get("summary", "unknown error")
        return f"::error title=npm audit failed::{lock}: {summary}"
    return None


def _active_exceptions(exceptions, lock, today):
    """(::error lines for this lock's expired exceptions, {ghsa: why} for its live ones)."""
    errors, excepted = [], {}
    for entry in exceptions:
        ghsa, for_lock, expires, why = entry.split("|", 3)
        if for_lock != lock:
            continue
        if expires < today:
            errors.append(f"::error title=expired npm audit exception::{ghsa} in {lock} expired "
                          f"{expires}. Re-check whether a fix exists, then upgrade or extend the "
                          "date in scripts/audit_npm.sh.")
        else:
            excepted[ghsa] = why
    return errors, excepted


def _advisories(report):
    """(package, advisory) pairs. A string `via` is a transitive entry: the advisory is reported
    under the package that carries it, so it is skipped here to count each finding once."""
    for name, vuln in sorted(report.get("vulnerabilities", {}).items()):
        for via in vuln.get("via", []):
            if isinstance(via, dict):
                yield name, via


def _judge_advisory(name, via, lock, excepted):
    """(ghsa, lines, 1 if it fails the audit else 0) for one advisory."""
    m = re.search(r"GHSA-[\w-]+", via.get("url", ""))
    ghsa = m.group(0) if m else str(via.get("source"))
    sev = via.get("severity", "")
    lines = [f"  {sev:9} {name} {via.get('range', '')}  {ghsa}  {via.get('title', '')}"]
    if sev not in FAILING:
        return ghsa, lines, 0
    if ghsa in excepted:
        return ghsa, lines + [f"    excepted: {excepted[ghsa]}"], 0
    lines.append(f"::error title=vulnerable npm dependency::{lock}: {name} ({sev}) {ghsa}. "
                 f"Run npm audit fix in {lock.rsplit('/', 1)[0]} and commit the lock.")
    return ghsa, lines, 1


def main(argv):
    lock, exceptions = argv[1], argv[2:]
    try:
        report = json.load(sys.stdin)
    except ValueError:
        report = None
    lines, rc = judge(report, lock, exceptions, datetime.date.today().isoformat())
    if lines:
        print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
