# CI minutes — cut billed Actions minutes (issue #351)

**Status:** implemented on branch `issue-351-ci-minutes` (2026-10-09). The "after" numbers are
still pending; they go in the checkpoint log once ~5 PR runs and ~5 main pushes have run.

## Context

The repo may go private. That moves GitHub Actions onto the plan's included minutes
(2,000–3,000/month), and at a $0 spending limit CI simply **stops** once the quota runs out.

Measured before this change:

- **~2,338 billed minutes in the last 30 days.**
- **~24% of that is per-job round-up.** Every job bills at least one whole minute, so a
  15-second job costs 60.
- A PR run bills ~10 min and a main push ~19 min.

The levers follow from the billing model:

- **A step-level skip saves nothing.** The job still starts, and it still bills its minimum
  minute.
- **A job-level `if:` saves the whole job.** The skip has to be decided in another job and
  passed in as an output.
- **A trigger-level path filter saves the whole run.** Nothing starts, so nothing bills.

Every change below keeps every gate that blocks `deploy` today.

## What changed

### 1a — `preflight` removed; missing credentials now fail loudly

`preflight` probed for the WIF secrets and *skipped* `deploy` if they were absent. It was
scaffolding from the time before WIF existed. Both secrets have been set since 2026-06-17
(checked with `gh secret list`, 2026-10-09), so the skip now has only one possible effect: hiding
a broken deploy behind a skipped check.

- `deploy` gates itself with
  `!cancelled() && !contains(needs.*.result, 'failure') && (main push || dispatch)`.
- Its **first step** is the "dispatch must run from main" refusal (#120), moved verbatim from
  `preflight` and placed before either checkout.
- If the secrets are missing, `google-github-actions/auth` fails the run. A comment at that step
  names the two secrets and `scripts/setup_test_wif.sh`.
- **There is deliberately no `github.repository` guard.** After a rename or a transfer it would
  stop matching and silently skip every deploy, which is exactly the failure being removed. A
  fork without the secrets goes red, and that is the fork owner's concern.
- **This deliberately departs from the issue's "keep the skip-not-fail behaviour" line.** A skip
  was the right default while the credentials were optional. Now that they're mandatory, a skip
  is a silent failure.
- Side effect: a dispatch from a non-main ref now fails after all three gates rather than after
  `gate` alone. Only a few minutes, on a run that was going to fail anyway.

### 1b — gitleaks folded into `lean-import`

`secret-scan` was a one-step job that billed a full minute to run ~15 s of work. That step is now
the `Secret scan (gitleaks)` step of `lean-import`. Details:

- `lean-import` checks out with `fetch-depth: 0` so gitleaks still sees the whole push/PR range.
- The later lean steps carry `if: ${{ !cancelled() }}`, so a secret hit doesn't hide the
  import-smoke result, and the import smoke doesn't hide a secret hit.
- Each step keeps its own name, so the failure annotation still says which check failed.

Why `lean-import` and not `gate`:

- it runs in parallel with `gate` and is short, so the extra ~15 s isn't on the critical path;
- it stays isolated from `gate`'s dev lock.

### 2 — `dep-audit` only when a lock changed, plus a daily audit of main

- **The classifier.** `scripts/ci_changes.sh <base>` runs in `lean-import` as step `changes`,
  right after checkout and before gitleaks, so its outputs are set even if a later step fails.
  - It diffs `base...HEAD`, i.e. from the merge base: a PR is classified by what *it* changed,
    not by what main gained since it branched.
  - The base is `origin/<base_ref>` on a PR and `github.event.before` on a push. A dispatch
    skips the script and sets both outputs to `true`: a #120 dispatch deploys a ref nobody gated
    on main, so it runs everything.
- **What counts as a dependency change.** `deps_changed` matches exactly what the audit reads,
  plus its own wiring:

  | Matches | Deliberately not |
  |---|---|
  | `requirements*.lock` | `requirements-*.txt` |
  | `keyproxy/requirements.lock` | `deploy.yml` |
  | `scripts/audit_deps.sh` | |
  | `.github/workflows/dep-audit.yml` | |

  - `requirements-*.txt` is left out because a floor edit without a re-lock already fails the
    gate's `lock_deps.sh --check`.
  - `deploy.yml` is left out because its wiring is unit-tested and it changes often.
  - `tests/test_ci_changes.py` checks that every lock in `audit_deps.sh`'s `LOCKS` matches a
    deps pattern, so the two lists can't drift apart.
- **The audit job.** It moved to the reusable **`.github/workflows/dep-audit.yml`**, which has
  three triggers: `workflow_call`, a daily `schedule`, and `workflow_dispatch`.
  - `deploy.yml` calls it with
    `if: ${{ !cancelled() && needs.lean-import.outputs.deps_changed != 'false' }}`.
  - Inside a called workflow the `github` context is the caller's, so the plain checkout audits
    the PR or push commit, exactly as the inline job did.
  - It is still **not** in `deploy.needs`. An advisory published today must not block an
    unrelated merge (pin-deps gotcha 6).
- **Why a daily run, and not the weekly lock-update run, as the backstop.**
  - The advisory database moves every day, so a lock nobody touched can turn vulnerable on a
    Saturday.
  - `deps-lock-update.yml` audits the *upgraded* locks. When the upgrade happens to fix an
    advisory, that run goes green, and nothing says main is vulnerable.
  - The daily run audits what main actually pins. Detection latency becomes at most a day,
    rather than "the next PR or push".
  - It runs at 10:41 UTC (06:41 ET), weekends included, for ~60 billed min/month.
  - Weekdays-only was rejected: it would save ~16 min/month and delay weekend findings to Monday.

### 3a — docs-only changes run nothing

Both the `push` and `pull_request` triggers carry `paths-ignore: ['**.md']`. 12–13 of the last 40
PRs changed only `.md` files.

- The rule is **every changed path ends in `.md`**, deliberately not "under `docs/`".
  `docs/openapi-surface.txt` is input to `scripts/check_openapi_snapshot.py` and
  `tests/test_mcp_tool_contracts.py`. A test pins the filter at exactly `**.md`.
- Skipping the deploy on a docs-only merge is safe: `.dockerignore` excludes `*.md` and `docs/`,
  so the images can't differ.
- `workflow_dispatch` takes no path filter, so a dispatch always runs.

### 3b — `frontend-gate` only on a frontend change

Only 1 of the last 40 PRs touched `frontend/`. `frontend_changed` matches four kinds of path:

| Path | Why it counts |
|---|---|
| `frontend/**` | the frontend itself |
| `tests/vectors/**` | `frontend/src/vault/envelope.test.ts` imports `tests/vectors/keyproxy_envelope_v1.json` |
| `.github/workflows/deploy.yml` | defines the job |
| `scripts/ci_changes.sh` | the classifier itself |

- The job has `needs: lean-import` and the same fail-open `!= 'false'` test as `dep-audit`. This
  serializes it behind `lean-import` (~1 min), which lengthens the path to `deploy` only on the
  rare frontend run.
- By default a skipped need skips its dependents. That is why `deploy.if` begins with
  `!cancelled() && !contains(needs.*.result, 'failure')`.
  - `gate` and `lean-import` never skip on a run that exists, since 3a skips whole runs.
  - So a skipped `frontend-gate` is the only skip `deploy` ever sees.

### 3c — `timeout-minutes` on every job

No job had one, so the 360-minute default applied, and a single hung job could bill 6 hours. The
limits are about 2–3× the observed maximum:

| Workflow | Job | Limit (min) | Max observed |
|---|---|---|---|
| deploy.yml | `gate` | 15 | |
| deploy.yml | `lean-import` | 10 | |
| deploy.yml | `frontend-gate` | 10 | |
| deploy.yml | `deploy` | 30 | ~13 min |
| dep-audit.yml | `audit` | 10 | |
| prod-rollout.yml | `gate` | 15 | ~2 min |
| prod-rollout.yml | `promote-and-deploy` | 30 | ~7 min |
| deps-lock-update.yml | `update` | 15 | |

A `uses:` job can't take `timeout-minutes`, so deploy.yml's `dep-audit` relies on the called
job's limit.

### 3d — a new push cancels the PR's in-flight run

`cancel-in-progress: ${{ github.event_name == 'pull_request' }}`, with the group unchanged
(`deploy-${{ github.ref }}`). Main pushes and dispatches still queue and finish, so a roll-out is
never cut off mid-deploy.

### 3e — coverage HTML uploads only on failure

Both coverage HTML uploads, backend and frontend, moved from `if: always()` to `if: failure()`.

- **Why:** nobody downloaded them, and the gates and Summary tables don't depend on them.
- **Storage:** they held ~240 MB of artifact storage, which would count against a private repo's
  500 MB–2 GB quota.
- **What's kept:** `retention-days: 14` stays. A failed run's report is the one worth reading.

### Considered and dropped

- **Caching `frontend/server`'s `npm ci`.** It takes seconds and would rarely change a billed
  minute.
- **Skipping the main-push re-run of the gates.** The merge commit can differ from the PR head.
- **Moving the roll-out's billed wait on Cloud Build into a Cloud Build trigger.** That's an
  architecture change; it gets its own issue if wanted.

Expected saving: roughly **450–700 billed min/month**, against ~2,338 today.

## Gotchas (what would mislead)

1. **A scheduled run's failure is emailed to whoever last changed the cron line.** It is not sent
   to the repo owner or the committer. Editing `dep-audit.yml`'s `cron:` hands that person the
   alarm.
2. **On a public repo, GitHub disables schedules after 60 days without repository activity.** The
   daily audit stops silently on a quiet repo. Re-enable it from the Actions tab.
3. **No required status checks, on purpose.** A docs-only PR shows *no* checks, which is fine
   only because the `main` ruleset requires none. Adding a required check later makes docs-only
   PRs unmergeable unless this filter is revisited.
4. **A docs-only merge doesn't reclaim test after a #120 dispatch, and has no image tag.**
   - Test keeps the dispatched ref until the next code merge, or until someone dispatches from
     main.
   - Prod promotion by that merge's SHA finds no tag. Promote the last code commit's SHA (or
     `latest`) instead.
5. **Never widen `paths-ignore` to `docs/**`.** `docs/openapi-surface.txt` is test input.
6. **prod-rollout's long runs were approval wait, not job time.** The longest recent run was ~70
   min wall time. Nearly all of it was the `prod` environment's approval wait. A job's timeout
   clock starts *after* approval, and `promote-and-deploy` itself peaked at ~7 min. So the
   30-minute limit is safe. Reading wall time from `gh run list` would have suggested a limit
   north of 70.
7. **Failing open needs two halves.**
   - The script prints `true` for both outputs on an empty, all-zeros (a new branch's `before`)
     or unresolvable base, and it always exits 0.
   - The gates test `!= 'false'`, not `== 'true'`, so an empty output still runs the gate. An
     empty output happens when `lean-import` dies before the `changes` step.
   - A gate that runs needlessly costs a minute. A gate skipped wrongly costs a missed failure.
8. **Linting the workflows locally.** `pip install actionlint-py` into a scratch venv gives an
   `actionlint` binary with no Go or Homebrew needed. It caught nothing here, but it is the
   cheapest check before a push.

## Out of scope

- **The frontend npm locks are not audited by CI.** `npm audit` on 2026-10-09 found:
  - `frontend/` runtime dependencies: 2 high and 11 moderate;
  - `frontend/server`: **2 critical**, 3 high and 2 moderate.

  `frontend/server` is the package that mints the user JWTs. Tracked separately: [#354](https://github.com/JohnFunkCode/StockPortfolioManager/issues/354).
- **An Actions budget ($10–20/month)** if the repo goes private. That is John's decision.

## Checkpoint log

| Step | Result |
|---|---|
| Workflows, `ci_changes.sh`, `dep-audit.yml`, tests (2026-10-09) | Done. `test_check_deploy_ref`, `test_dependency_locks`, `test_ci_parallel` and `test_ci_changes` pass (63 tests). actionlint is clean on all four workflows. Classifier checked locally: an all-zeros or bogus base gives true/true; `HEAD~3` gives false/true. |
| Docs sweep | Done. Updated `CLAUDE.md`, `readme.md`, `byok.md`, `quantui.md`, `prod-promotion.md`, and the `pin-deps`, `deploy-ref-to-test` and `wif-trust` plans. `ubuntu-26-runner-plan.md` is left as the historical record of that run. |
| PR run | *pending* |
| False paths: a `.md`-only commit starts no run; a Python-only commit skips both conditional gates; a second push cancels the first | *pending* |
| After merge: `gh workflow run dep-audit.yml`, first scheduled run, main-push deploy | *pending* |
| Billed minutes after, ~5 PR runs and ~5 main pushes | *pending* |
