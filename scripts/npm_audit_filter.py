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
    if not isinstance(report, dict):
        return [f"::error title=npm audit failed::{lock}: npm audit printed no JSON report"], 1
    if "error" in report:
        summary = report["error"].get("summary", "unknown error")
        return [f"::error title=npm audit failed::{lock}: {summary}"], 1

    out, rc, excepted = [], 0, {}
    for entry in exceptions:
        ghsa, for_lock, expires, why = entry.split("|", 3)
        if for_lock != lock:
            continue
        if expires < today:
            out.append(f"::error title=expired npm audit exception::{ghsa} in {lock} expired "
                       f"{expires}. Re-check whether a fix exists, then upgrade or extend the date "
                       "in scripts/audit_npm.sh.")
            rc = 1
        else:
            excepted[ghsa] = why

    seen = set()
    for name, vuln in sorted(report.get("vulnerabilities", {}).items()):
        for via in vuln.get("via", []):
            if not isinstance(via, dict):
                continue    # transitive: reported under the package that carries the advisory
            m = re.search(r"GHSA-[\w-]+", via.get("url", ""))
            ghsa = m.group(0) if m else str(via.get("source"))
            sev = via.get("severity", "")
            seen.add(ghsa)
            out.append(f"  {sev:9} {name} {via.get('range', '')}  {ghsa}  {via.get('title', '')}")
            if sev not in FAILING:
                continue
            if ghsa in excepted:
                out.append(f"    excepted: {excepted[ghsa]}")
                continue
            out.append(f"::error title=vulnerable npm dependency::{lock}: {name} ({sev}) {ghsa}. "
                       f"Run npm audit fix in {lock.rsplit('/', 1)[0]} and commit the lock.")
            rc = 1

    for ghsa in sorted(set(excepted) - seen):
        out.append(f"::warning title=stale npm audit exception::{ghsa} no longer appears in "
                   f"{lock}; delete it from scripts/audit_npm.sh.")
    return out, rc


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
