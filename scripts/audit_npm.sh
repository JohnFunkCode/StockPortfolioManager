#!/usr/bin/env bash
# Audit the frontend npm locks for known vulnerabilities (issue #354) -- the npm half of
# scripts/audit_deps.sh, run next to it by .github/workflows/dep-audit.yml (on a lock change and
# daily on main; red, never a roll-out gate).
#
#   scripts/audit_npm.sh      needs npm and python3 on PATH; reads the locks only (no npm ci)
#
# The judging is scripts/npm_audit_filter.py (tested in tests/test_audit_npm.py).
#
# The whole tree is audited, dev included, and the filter classes each finding by the lock's own
# "dev" flags. Exit 1, with one ::error line per advisory, if a runtime dependency (what ships in
# an image) has a moderate, high or critical advisory that is not excepted below. A dev-only
# dependency at high or critical gets a ::warning and never fails the run: it doesn't ship, but it
# runs in CI and locally. Until 2026-10-28 this audited --omit=dev at high+ only, which hid a
# runtime qs moderate and eight undici advisories in the dev tree (pin-deps-plan.md gotcha 17).
#
# EXCEPTIONS are for advisories with NO fixed version anywhere -- never for "not upgraded yet".
# Each one names the lock, an expiry date and why it can't be reached. An expired exception fails
# the run, so it gets re-checked rather than outliving its reason; one that no longer matches
# anything prints a warning so it gets deleted.
set -uo pipefail

cd "$(dirname "$0")/.."

LOCKS=(frontend/package-lock.json frontend/server/package-lock.json)

# GHSA id | lock | expires (YYYY-MM-DD) | why it is safe to carry
# None today: the braces entry went with http-proxy-middleware (#357).
EXCEPTIONS=(
)

rc=0
for lock in "${LOCKS[@]}"; do
  echo "--- npm audit $lock"
  json=$(cd "$(dirname "$lock")" && npm audit --package-lock-only --json 2>/dev/null)
  if ! printf '%s' "$json" | python3 scripts/npm_audit_filter.py "$lock" ${EXCEPTIONS[@]+"${EXCEPTIONS[@]}"}; then
    rc=1
  fi
done
exit $rc
