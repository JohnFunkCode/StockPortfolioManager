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

## Checkpoint log

| Step | Result |
|---|---|
| Trial commit: `deploy.yml` on `ubuntu-26.04` + probe workflow | Pushed; CI pending. |
