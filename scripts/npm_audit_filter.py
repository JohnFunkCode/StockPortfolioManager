"""Judge one `npm audit --json` report against scripts/audit_npm.sh's exceptions (issue #354).

    npm audit --package-lock-only --json | python3 scripts/npm_audit_filter.py LOCK [EXC...]

Each EXC is "GHSA id|lock|expires YYYY-MM-DD|why". The report covers the whole tree, dev included;
LOCK's own "dev" flags say which findings ship. Prints every advisory, then exits 1 if a runtime
one at moderate or above is not excepted, an exception for this lock has expired, or the report is
an error. A dev-only advisory at high or above is a ::warning and never fails the run: the dev tree
never ships in an image, but it does run in CI and on laptops. Both thresholds were raised after
qs (moderate, runtime) and undici (high, dev only) went unflagged on 2026-10-28.
Stdlib only: dep-audit.yml runs it on a bare runner.
"""
import datetime
import json
import re
import sys
from pathlib import Path

FAILING = ("moderate", "high", "critical")   # runtime advisories that fail the run
DEV_WARNING = ("high", "critical")           # dev-only advisories that warn


def judge(report, lock, exceptions, today, lock_packages=None):
    """(lines to print, exit code) for one lock's report.

    lock_packages is the lock's "packages" map; a package is dev-only when every node npm reports
    for it carries "dev": true there. Without it, every finding counts as runtime."""
    error = _report_error(report, lock)
    if error:
        return [error], 1
    out, excepted = _active_exceptions(exceptions, lock, today)
    rc = 1 if out else 0
    seen = set()
    for name, via, dev in _advisories(report, lock_packages or {}):
        ghsa, lines, failed = _judge_advisory(name, via, lock, excepted, dev)
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


def _advisories(report, lock_packages):
    """(package, advisory, dev-only?) triples. A string `via` is a transitive entry: the advisory
    is reported under the package that carries it, so it is skipped here to count each finding
    once."""
    for name, vuln in sorted(report.get("vulnerabilities", {}).items()):
        dev = _dev_only(vuln.get("nodes") or [f"node_modules/{name}"], lock_packages)
        for via in vuln.get("via", []):
            if isinstance(via, dict):
                yield name, via, dev


def _dev_only(nodes, lock_packages):
    """True when every install of the package is dev-only in the lock; an unknown node is runtime,
    so a lock the filter can't read fails closed.

    Only "dev": true proves a node never ships. "devOptional" means a dev dependency that is also
    an optional dependency of a production one, and `npm ci --omit=dev` still installs it, so it
    counts as runtime (PR #454 review)."""
    return all(lock_packages.get(n, {}).get("dev") is True for n in nodes)


def _judge_advisory(name, via, lock, excepted, dev):
    """(ghsa, lines, 1 if it fails the audit else 0) for one advisory."""
    m = re.search(r"GHSA-[\w-]+", via.get("url", ""))
    ghsa = m.group(0) if m else str(via.get("source"))
    sev = via.get("severity", "")
    scope = "dev" if dev else "runtime"
    lines = [f"  {sev:9} {scope:7} {name} {via.get('range', '')}  {ghsa}  {via.get('title', '')}"]
    if sev not in (DEV_WARNING if dev else FAILING):
        return ghsa, lines, 0
    if ghsa in excepted:
        return ghsa, lines + [f"    excepted: {excepted[ghsa]}"], 0
    if dev:
        lines.append(f"::warning title=vulnerable npm dev dependency::{lock}: {name} ({sev}, dev "
                     f"only) {ghsa}. Run npm audit fix in {lock.rsplit('/', 1)[0]} and commit "
                     "the lock.")
        return ghsa, lines, 0
    lines.append(f"::error title=vulnerable npm dependency::{lock}: {name} ({sev}) {ghsa}. "
                 f"Run npm audit fix in {lock.rsplit('/', 1)[0]} and commit the lock.")
    return ghsa, lines, 1


def main(argv):
    lock, exceptions = argv[1], argv[2:]
    try:
        report = json.load(sys.stdin)
    except ValueError:
        report = None
    try:
        lock_packages = json.loads(Path(lock).read_text()).get("packages", {})
    except (OSError, ValueError):
        lock_packages = {}   # every finding then counts as runtime: fail closed
    lines, rc = judge(report, lock, exceptions, datetime.date.today().isoformat(), lock_packages)
    if lines:
        print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
