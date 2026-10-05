#!/usr/bin/env bash
# Audit every dependency lock for known vulnerabilities (issue #218).
#
#   scripts/audit_deps.sh      needs pip-audit on PATH (pip install pip-audit)
#
# Exit 1, with one ::error line per lock, if any pinned version has a published advisory.
# Run by deploy.yml's dep-audit job (red, but not a roll-out gate: an advisory published
# today must not block an unrelated merge) and by deps-lock-update.yml every week.
#
# OSV, not the default PyPI service: PyPI's audit skips torch, whose pin is the CPU build
# 2.14.1+cpu from the PyTorch index ("not found on PyPI"). With OSV, --require-hashes only
# checks that hashes are present; the installs are what verify them.
set -uo pipefail

cd "$(dirname "$0")/.."

LOCKS=(requirements-base.lock requirements-ml.lock requirements-dev.lock
       keyproxy/requirements.lock requirements.lock)
rc=0
for f in "${LOCKS[@]}"; do
  echo "--- pip-audit $f"
  if ! pip-audit --disable-pip --require-hashes --vulnerability-service osv \
      --progress-spinner off --desc on -r "$f"; then
    echo "::error title=vulnerable dependency::$f pins a version with a known advisory (see the table above). Raise the floor in its requirements-*.txt, or run scripts/lock_deps.sh --upgrade, and commit the lock."
    rc=1
  fi
done
exit $rc
