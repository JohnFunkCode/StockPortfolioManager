#!/usr/bin/env bash
# Classify a change for deploy.yml's job-level skips (#351).
#
#   scripts/ci_changes.sh <base-rev>
#
# Diffs <base-rev>...HEAD and prints two lines for $GITHUB_OUTPUT:
#
#   deps_changed=true|false      a dependency lock, or the audit's own wiring, changed
#                                -> dep-audit runs (otherwise dep-audit.yml's daily run covers main)
#   frontend_changed=true|false  frontend/, the vectors its tests import, or this wiring changed
#                                -> frontend-gate runs
#
# Fails OPEN: an empty base, the all-zeros `before` of a new branch, or a commit that isn't in
# this clone prints true for both. A gate that runs needlessly costs a minute; a gate skipped
# wrongly costs a missed failure. Always exits 0 -- a classifier crash must not fail the job,
# and deploy.yml's `!= 'false'` tests run every gate if these lines never appear.
#
# Deliberately not deps: requirements-*.txt (a floor edit without a re-lock already fails the
# gate's lock_deps.sh --check) and deploy.yml (its wiring is unit-tested, and it changes often).
# tests/test_ci_changes.py checks every lock audit_deps.sh audits matches a deps pattern.
set -uo pipefail

base="${1:-}"

all_true() {
  printf 'deps_changed=true\nfrontend_changed=true\n'
  exit 0
}

case "$base" in
  ''|*[!0]*) ;;
  *) all_true ;;   # all zeros: a new branch has no `before`
esac
[ -n "$base" ] || all_true
git rev-parse --verify --quiet "${base}^{commit}" >/dev/null || all_true
files="$(git diff --name-only "${base}...HEAD" 2>/dev/null)" || all_true

deps=false
frontend=false
while IFS= read -r f; do
  case "$f" in
    requirements*.lock|keyproxy/requirements.lock|scripts/audit_deps.sh|.github/workflows/dep-audit.yml)
      deps=true ;;
  esac
  case "$f" in
    frontend/*|tests/vectors/*|.github/workflows/deploy.yml|scripts/ci_changes.sh)
      frontend=true ;;
  esac
done <<< "$files"

printf 'deps_changed=%s\nfrontend_changed=%s\n' "$deps" "$frontend"
