#!/usr/bin/env bash
# Compile the hash-pinned dependency locks from the requirements-*.txt layers (issue #218).
#
#   scripts/lock_deps.sh             re-lock, keeping every existing pin that still satisfies
#                                    the .txt inputs (uv reads the current lock as preferences)
#   scripts/lock_deps.sh --upgrade   re-resolve everything to the newest allowed versions
#   scripts/lock_deps.sh --check     re-lock into a temp dir and fail if any lock differs
#
# The .txt files stay the human-edited inputs (floors and comments); the .lock files are
# generated, carry a sha256 for every distribution, and are what every install uses:
#
#   requirements-base.lock     Dockerfile.mcp, Dockerfile.report, lean-import, prod-rollout gate
#   requirements-ml.lock       Dockerfile.api, Dockerfile.news           (base pins + torch stack)
#   requirements-dev.lock      deploy.yml's gate job                     (base pins + report + dev)
#   keyproxy/requirements.lock Dockerfile.keyproxy
#   requirements.lock          local dev and the Pi (universal: any OS/arch, python >= 3.11)
#
# The container/CI locks target CPython 3.12 on x86_64 manylinux, which is what Cloud Build
# and ubuntu-latest run. ml and dev are compiled with the base lock as a constraint, so a
# package shared with base has the same pin in all three.
#
# Needs uv (pip install uv). Plan and gotchas: docs/proposals/pin-deps-plan.md.
set -euo pipefail

cd "$(dirname "$0")/.."

MODE=lock
case "${1:-}" in
  "") ;;
  --upgrade) MODE=upgrade ;;
  --check) MODE=check ;;
  *) echo "usage: $0 [--upgrade|--check]" >&2; exit 2 ;;
esac

# Absolute, because --check compiles from a scratch directory.
UV="$(command -v "${UV:-uv}" || true)"
if [[ -z "$UV" ]]; then
  echo "lock_deps: uv not found (pip install uv, or set UV=/path/to/uv)" >&2
  exit 2
fi
[[ "$UV" == /* ]] || UV="$PWD/$UV"

LOCKS=(requirements-base.lock requirements-ml.lock requirements-dev.lock
       keyproxy/requirements.lock requirements.lock)

REPO="$PWD"
if [[ "$MODE" == check ]]; then
  # Compile in a scratch copy. It holds the committed locks too, so --check resolves
  # exactly like a plain re-lock: a new release upstream must not fail the check, only an
  # input the lock no longer matches. Same relative paths, so the `# via` lines match.
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT
  mkdir -p "$WORK/keyproxy"
  for f in requirements*.txt keyproxy/requirements.txt "${LOCKS[@]}"; do cp "$f" "$WORK/$f"; done
  cd "$WORK"
fi

COMMON=(--generate-hashes --quiet --custom-compile-command "scripts/lock_deps.sh")
[[ "$MODE" == upgrade ]] && COMMON+=(--upgrade)
TARGET=(--python-version 3.12 --python-platform x86_64-manylinux_2_28)
# The PyTorch CPU index mirrors numpy and friends. uv's default (first index wins) would
# take numpy only from there and miss PyPI's newer releases; best-match is what pip does.
TORCH=(--index-strategy unsafe-best-match --emit-index-url)

"$UV" pip compile requirements-base.txt "${TARGET[@]}" "${COMMON[@]}" \
  -o requirements-base.lock
"$UV" pip compile requirements-ml.txt -c requirements-base.lock \
  "${TORCH[@]}" "${TARGET[@]}" "${COMMON[@]}" -o requirements-ml.lock
"$UV" pip compile requirements-dev.txt -c requirements-base.lock \
  "${TARGET[@]}" "${COMMON[@]}" -o requirements-dev.lock
"$UV" pip compile keyproxy/requirements.txt "${TARGET[@]}" "${COMMON[@]}" \
  -o keyproxy/requirements.lock
# Universal: one lock with environment markers for macOS (dev), linux aarch64 (a 64-bit Pi)
# and x86_64, from python 3.11 up.
"$UV" pip compile requirements.txt --universal --python-version 3.11 \
  "${TORCH[@]}" "${COMMON[@]}" -o requirements.lock

if [[ "$MODE" == check ]]; then
  rc=0
  for f in "${LOCKS[@]}"; do
    if ! diff -u "$REPO/$f" "$f" >/dev/null; then
      echo "::error title=stale dependency lock::$f does not match its requirements input. Run scripts/lock_deps.sh and commit the result."
      diff -u "$REPO/$f" "$f" | head -40 || true
      rc=1
    fi
  done
  [[ $rc == 0 ]] && echo "locks ok: ${#LOCKS[@]} locks match their inputs."
  exit $rc
fi
echo "wrote ${LOCKS[*]}"
