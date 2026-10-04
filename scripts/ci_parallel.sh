#!/usr/bin/env bash
# Run roll-out commands concurrently, with every failure still failing the step (#296).
#
# Sourced by the roll-out step of deploy.yml and prod-rollout.yml:
#
#   . scripts/ci_parallel.sh
#   deploy_wrapper() { gcloud run deploy "quantcore-$1" ...; }
#   run_parallel "deploy_api" "deploy_wrapper stock-price" "deploy_wrapper options-analysis"
#
# Each argument is one command: a shell function name plus optional arguments, split on
# spaces (no eval; quoting inside an entry is not honoured). All entries start at once.
# Each runs under `set -e`, so a multi-command function stops at its first failure.
#
# Why not a bare `&` + `wait`: `wait` with no PIDs always returns 0 and swallows every
# exit code, and interleaved output from ten gcloud calls is unreadable. Here each PID is
# waited on by itself and each command's output goes to its own log. The logs are printed
# afterwards, one ::group:: per command, in argument order, with the exit code and
# duration in the group title. Workflow commands such as ::warning:: inside a log still
# take effect when it is printed.
#
# Returns 1 if any entry failed, after every entry has finished and every log has been
# printed. Each failure gets its own ::error:: annotation naming the entry. Entries that
# are still running are not cancelled when another one fails: killing a `gcloud run
# deploy` mid-flight leaves its revision half-rolled, which is worse than letting it finish.

run_parallel() {
  local dir entry i rc secs failed=""
  local -a pids labels
  # Explicit template: BSD mktemp -d (macOS) ignores $TMPDIR without one.
  dir="$(mktemp -d "${TMPDIR:-/tmp}/ci_parallel.XXXXXX")"

  i=0
  for entry in "$@"; do
    (
      set +e
      start=$SECONDS
      # Word-split on purpose: "fn arg1 arg2" -> fn arg1 arg2.
      # shellcheck disable=SC2086
      ( set -e; $entry ) >"$dir/$i.log" 2>&1
      rc=$?
      echo "$((SECONDS - start))" >"$dir/$i.secs"
      exit "$rc"
    ) &
    pids[i]=$!
    labels[i]="$entry"
    i=$((i + 1))
  done

  for i in "${!pids[@]}"; do
    if wait "${pids[i]}"; then rc=0; else rc=$?; fi
    secs="$(cat "$dir/$i.secs" 2>/dev/null || echo '?')"
    echo "::group::${labels[i]} (exit $rc, ${secs}s)"
    cat "$dir/$i.log"
    echo "::endgroup::"
    if [ "$rc" -ne 0 ]; then
      echo "::error title=${labels[i]} failed::${labels[i]} exited $rc after ${secs}s; its log is in the group above."
      failed="$failed ${labels[i]};"
    fi
  done

  rm -rf "$dir"
  if [ -n "$failed" ]; then
    echo "run_parallel: failed:$failed" >&2
    return 1
  fi
}
