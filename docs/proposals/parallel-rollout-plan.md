# Plan: parallel Cloud Run roll-out (issue #296)

## Context

After #278 cut Cloud Build to about 2 minutes, the `gcloud run deploy` steps became the largest part
of the `deploy` job. They ran one after another. Baseline from run 37209801132 (test, 2026-10-03):

| Step | Time |
|---|---|
| quantcore-api | 1:57 |
| 5 wrappers (loop) | 1:39 |
| quantcore-portfolio | 0:35 |
| quantcore-keyproxy | 0:23 |
| quantcore-arbitrage | 0:14 |
| quantui | 0:10 |
| report + news Jobs | 0:07 |
| **Total** | **~5:05** |

Each `gcloud run deploy` mostly waits for Cloud Run to roll a revision. Deploys of **different**
services don't contend; the `concurrency:` optimistic-lock conflict only arises between two deploys of
the *same* service.

## Design

One roll-out step per workflow (`deploy.yml`, `prod-rollout.yml`). It defines one shell function per
deploy and runs them through `run_parallel` from `scripts/ci_parallel.sh`, in **two phases**:

1. **Phase 1:** `quantcore-api`, the report and news Jobs, and `quantcore-keyproxy`. None of these
   calls the new api.
2. **Phase 2:** api's REST consumers: the 7 MCP wrappers and `quantui`. This phase runs only if
   phase 1 succeeded.

**Why two phases and not one.** If an api revision fails its health check (for example
`SchemaDriftError` from `ensure_schema()`), the run fails before any consumer rolls. That is the
ordering the sequential steps gave, and it costs about 35 s against fully parallel. Expected
duration is api (~2:00) plus the slowest wrapper (~0:35), about **2:35**.

**Why `run_parallel` and not a bare `&` + `wait`.**
- A bare `wait` returns 0 and swallows every exit code.
- The output of ten interleaved gcloud calls is unreadable.

`run_parallel` waits on each PID on its own and writes each command's output to its own log. Once
all have finished, it prints each log in its own `::group::` (titled with the exit code and
seconds). Each failure gets an `::error title=<entry> failed::` annotation, and the step fails
listing every failed entry.

Each workflow keeps its own behaviour:
- **Guards and annotations (#163):** keyproxy, portfolio, arbitrage and quantui (test only) are
  skipped when their service doesn't exist, with a `::warning::`. The news Job gets a warning when
  it is missing.
- **Prod only:** deploys go by the digests the promotion step exported. keyproxy and news skip when
  this tag had no digest to promote. Prod `quantui` stays unguarded.
- **Every roll-out:** the sizing env vars and `--cpu-boost` are passed as before.
- **Prod now checks out the repo:** `prod-rollout.yml`'s promote job gained an
  `actions/checkout`, solely to source the script.

## Gotchas

- **No cancellation.** When one entry fails, its siblings keep running to completion. That is
  deliberate: killing a `gcloud run deploy` mid-flight leaves a revision half-rolled, which is worse.
  Phase 2 still does not start.
- **Logs appear after the phase, not live.** A hung deploy shows nothing until the job times out.
  Live, interleaved output was the alternative, and it is unreadable.
- **macOS `mktemp -d` ignores `$TMPDIR`.** It uses the per-user `/var/folders` dir instead, which
  the local sandbox blocks. The script passes an explicit `"${TMPDIR:-/tmp}/ci_parallel.XXXXXX"`
  template, which works the same on GNU and BSD. The tests point `TMPDIR` at their own temp dir.
- **Entries are word-split, not `eval`'d.** `"deploy_wrapper stock-price"` runs
  `deploy_wrapper stock-price`. Quoting inside an entry is not honoured, which is fine for
  service names.
- **API quota.** Phase 2 runs 8 concurrent gcloud calls, well under the Cloud Run admin API's
  per-minute write quota. No cap was added.

## Verification

- `tests/test_ci_parallel.py`:
  - **Behaviour:** concurrency (4 × `sleep 1` finishes in under 2.5 s); a failing entry fails the
    run, gets an `::error::` naming it, and still prints its siblings' logs; every failure is
    reported; `set -e` holds inside an entry; `::warning::` lines survive.
  - **Wiring:** each workflow has exactly one roll-out step and two `run_parallel` calls, sources
    the script, has a checkout, keeps `--cpu-boost`, uses no `--set-*`, and deploys every service.
- **Failing-deploy proof:** `test_one_failure_fails_the_step_and_names_the_entry` reproduces the
  issue's "deliberately failing deploy" case without breaking a real service.
- **Timing:** the first `main` run after merge supplies the "after" number below.

## Checkpoint log

| Step | Commit | Result | Gotcha |
|---|---|---|---|
| Baseline (sequential) | `ba7dca2` | roll-out ~5:05 (run 37209801132) | — |
| Two-phase parallel roll-out, both workflows | _this PR_ | 14 unit tests pass | BSD `mktemp -d` ignores `$TMPDIR` |
| First `main` run after merge | — | _to fill: roll-out time_ | — |
