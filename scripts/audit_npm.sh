#!/usr/bin/env bash
# Audit the frontend npm locks for known vulnerabilities (issue #354) -- the npm half of
# scripts/audit_deps.sh, run next to it by .github/workflows/dep-audit.yml (on a lock change and
# daily on main; red, never a roll-out gate).
#
#   scripts/audit_npm.sh      needs npm and python3 on PATH; reads the locks only (no npm ci)
#
# The judging is scripts/npm_audit_filter.py (tested in tests/test_audit_npm.py).
#
# Runtime dependencies only (--omit=dev): the dev tree never ships in an image. Exit 1, with one
# ::error line per advisory, if a lock reaches a high or critical advisory that is not excepted
# below.
#
# EXCEPTIONS are for advisories with NO fixed version anywhere -- never for "not upgraded yet".
# Each one names the lock, an expiry date and why it can't be reached. An expired exception fails
# the run, so it gets re-checked rather than outliving its reason; one that no longer matches
# anything prints a warning so it gets deleted.
set -uo pipefail

cd "$(dirname "$0")/.."

LOCKS=(frontend/package-lock.json frontend/server/package-lock.json)

# GHSA id | lock | expires (YYYY-MM-DD) | why it is safe to carry
EXCEPTIONS=(
  "GHSA-vfj7-8cjw-p6xm|frontend/server/package-lock.json|2027-01-09|braces: every version is affected (no fix). Reached only via http-proxy-middleware -> micromatch; server.mjs passes no glob pattern, so no attacker input reaches braces (pinned by frontend/server/proxy-context.test.mjs; removal is #357)."
)

rc=0
for lock in "${LOCKS[@]}"; do
  echo "--- npm audit $lock"
  json=$(cd "$(dirname "$lock")" && npm audit --package-lock-only --omit=dev --json 2>/dev/null)
  if ! printf '%s' "$json" | python3 scripts/npm_audit_filter.py "$lock" "${EXCEPTIONS[@]}"; then
    rc=1
  fi
done
exit $rc
