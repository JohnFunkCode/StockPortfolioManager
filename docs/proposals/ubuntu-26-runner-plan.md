# Plan: CI on Ubuntu 26.04 before `ubuntu-latest` moves (issue #293)

## Context

GitHub moves the `ubuntu-latest` label to Ubuntu 26.04 from 2026-10-19 to 2026-11-19
([actions/runner-images#14748](https://github.com/actions/runner-images/issues/14748)). Every
job in `deploy.yml`, `prod-rollout.yml` and `deps-lock-update.yml` runs on `ubuntu-latest`.
`prod-rollout.yml` runs rarely, so a break there would first show up mid-promotion (#261).

Decision: option 1 from the issue. Prove the jobs on the explicit `ubuntu-26.04` label in a PR,
then go back to `ubuntu-latest`. Pinning `ubuntu-24.04` would only defer the same check.

## Design

- **PR-runnable jobs** (`gate`, `lean-import`, `frontend-gate`, `secret-scan`, `dep-audit` in
  `deploy.yml`): `runs-on: ubuntu-26.04` for one trial commit.
- **Jobs that never run on a PR** (`deploy`, prod-rollout's `gate` and `promote-and-deploy`,
  `deps-lock-update`'s `update`): a temporary `ubuntu26-probe.yml`.
  - It runs prod-rollout's `gate` verbatim on 26.04.
  - It also runs a credential-free `host-tools` job that exercises what the deploy jobs take
    from the host: the **system `python3`**, `setup-gcloud`, `docker buildx imagetools` and bash.
    `deploy` runs `check_deploy_ref.py` and `cloudrun_services.py` with the runner's own
    `python3`, not setup-python's, so on 26.04 those scripts run on a different interpreter.
  - `deps-lock-update` runs the same setup-python, `lock_deps.sh` and `audit_deps.sh` that `gate`
    and `dep-audit` already prove.
- Once everything is green: revert `runs-on`, delete the probe, and record the results here.

## Gotchas

1. **`deploy` has no setup-python.** It is the one job whose Python version changes with the
   image. The probe runs both of its scripts under the image's own `python3`.

- **Merging a stack top-down strands the upper PRs.** #310–#312 were approved and merged *after*
  #309 had merged to `main`. Each one merged into its base branch, not `main`, so #120, #218 and
  this trial landed on stale branches, and the trial landed still pinned to 26.04 with the probe
  in place. A recovery PR from the top branch was needed. Before merging a stacked PR, retarget it
  at `main` once the PR below it merges, or merge the stack bottom-up and retarget each time.
- **A red stacked PR may be red because of the PR below it.** #312's first gate failure looked like
  a runner regression, but it was a #120 test that #120's own change had made stale. Before
  blaming the image, read the failing assertion, and fix it on the lowest branch that carries it.

## Checkpoint log

| Step | Result |
|---|---|
| Trial commit: `deploy.yml` on `ubuntu-26.04` + probe workflow | Pushed; PR #312's CI ran every job on 26.04. |
| `host-tools` probe (run 37265081449) | Pass. Image `Ubuntu 26.04.1 LTS`: system `python3` **3.14.4** (24.04 had 3.12), gcloud 568.0.0, buildx v0.37.1, bash 5.3.9, git 2.55.0. `check_deploy_ref.py` printed "ref ok"; `cloudrun_services.py names` listed the same phase 1/2 sets for test and prod as on 24.04; `imagetools inspect python:3.12-slim` resolved the index; `run_parallel` grouped and reported both commands. |
| `lean-import` on 26.04 | Pass (57s). |
| `secret-scan` on 26.04 | Pass. |
| `dep-audit` on 26.04 | Pass (1m18s). |
| `frontend-gate` on 26.04 | Pass (2m24s). |
| `gate` and the probe's prod-rollout gate, first run | Both failed on one test only, `test_migrate_step_sits_between_build_and_rollout`. It still asserted `GITHUB_SHA::7`, which #120 had replaced with the gated `DEPLOY_SHA` (the fix is e4d24a4 on #120's branch, merged up). Nothing about 26.04 was involved: the gate ran 1717 tests in 55s, and the probe ran 1709 in 36s. |
| Re-run after the #120 fix (runs 37337040042, 37337040081) | All green on 26.04: `gate` (2m18s), `lean-import`, `frontend-gate`, `secret-scan`, `dep-audit`, the probe's prod-rollout gate (1m47s) and `host-tools`. `deps-lock-update` is covered indirectly: its setup-python, `lock_deps.sh` and `audit_deps.sh` are the steps `gate` and `dep-audit` just ran. |
| Revert (2026-10-05) | `deploy.yml` is back on `ubuntu-latest` and the probe is deleted. This landed with the recovery PR that brought #120/#218/#293 to `main`. When the label moves (2026-10-19 → 11-19), the first runs on 26.04 should be the ones above. |
