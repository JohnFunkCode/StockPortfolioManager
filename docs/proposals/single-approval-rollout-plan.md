# One approval per prod roll-out (#346)

**Status:** implemented on branch `issue-346-single-approval`; end-to-end dispatch pending (John,
after merge). Runbook: [`prod-promotion.md`](../operations/prod-promotion.md).

## Problem

`prod-rollout.yml` ran a `gate` job (unit tests, wrapper smoke, OpenAPI diff; about 2 min) and
only then asked for the `prod` environment approval on `promote-and-deploy`. The approver had to
come back mid-run, and a dispatch left alone sat waiting with nothing to show for it.

## Design

Two GitHub facts set the shape:

1. **An environment approval is requested only after the job's `needs` have finished.** Anything
   in front of the prod job delays the prompt, so it must be fast.
2. **Every job on `environment: prod` asks for approval separately**, and WIF (#313) forces any
   job that authenticates to prod onto `environment: prod`. So exactly one job holds all the prod
   work.

The result:

- **`preflight`** has no environment and no prod access. It authenticates with the *test* WIF
  provider and runs [`scripts/prod_preflight.py`](../../scripts/prod_preflight.py) (stdlib only),
  which:
  - resolves every promoted image's digest at `<test AR>/<img>:<tag>`. keyproxy and news may be
    absent; any other missing image fails the run, so a tag without `quantcore-migrate` fails
    here, before the approval;
  - resolves the tag to a commit by finding the 7-hex tag that shares quantcore-api's digest;
  - scans the migrations added or changed since the last promoted commit with the migrate image's
    own scanner (`db/migrate-entrypoint.sh` `check_pending`). A refusal fails the run unless
    `allow_refused_migrations: true` (the case after a hand-applied prod migration);
  - writes the digests and SHA as job outputs and a job summary for the approver.
- **`promote-and-deploy`** (`environment: prod`, `needs: preflight`) is the only prod job. After
  the one approval it runs the gate steps (unchanged, still on `requirements-base.lock` alone)
  **before** the prod auth step, so a test failure touches nothing in prod. Then it runs a
  read-only `cloudrun_services.py check --env prod --missing-manual`, promotes **the preflight's
  digests** (never the tag again), migrates, rolls out, and uploads the `promoted-sha` artifact
  (90 days) that the next preflight diffs against.
- `deploy.yml`'s display name is now `Deploy Version To Test`. Only the `name:` changed: the
  filename stays, and `prod-rollout.yml` must never be renamed because the prod WIF condition pins
  it.

Binding the digests replaces any idea of an approval expiry. Whatever the tag points to by the
time someone approves, what ships is what the summary showed. If the tag has moved, a `::notice`
says so.

## Decisions (John, 2026-10-11)

| Question | Choice |
|---|---|
| Where the gate runs | inline in the prod job, after approval, before prod auth |
| Refused-migration check | diff against the last promoted SHA (artifact) |
| deploy.yml display name | rename to `Deploy Version To Test` |
| Missing-manual-service check | after approval, before any prod change (not in the preflight; gotcha 9) |

## Gotchas

1. **Approval waits for `needs`.** Leaving the 2-minute gate as a separate job in front would
   have kept the prompt late. That is why the gate moved inside the prod job.
2. **A second `environment: prod` job means a second approval.** `tests/test_prod_rollout.py`
   pins exactly one.
3. **quantcore-migrate's digest is shared across many commits.** The image holds only `db/`, so
   every commit that doesn't touch migrations reuses one digest, and its SHA tags name dozens of
   commits. The SHA lookup uses quantcore-api's digest instead. Several SHA tags can still share
   that one (a rebuild with no image change), and the newest commit git knows wins. Any of them
   carries identical migrations.
4. **`gcloud artifacts docker tags list --format=json` gives the full resource path** in `tag`
   (`projects/…/tags/abc1234`), not the bare tag. Take the last path segment.
5. **deploy.yml's `name:` was lowercase `deploy`**, so a sed looking for `Deploy` matched nothing.
6. **Commits made in the same second tie on `%ct`.** The SHA-selection test gave each test commit
   an explicit, increasing `GIT_COMMITTER_DATE`, or "newest wins" picked arbitrarily.
7. **`tests/test_ci_migrate.py` looks for a promote-loop line containing `quantcore-keyproxy` and
   `if`.** The digest-based loop at first only handled an empty digest generically, which failed
   that test with `StopIteration`. The loop now keeps an explicit keyproxy/news skip and fails on
   any other empty digest. That is belt and braces, because the preflight already failed on it.
8. **Postgres.app refuses connections from Claude locally**, sandboxed or not ("You did not allow
   Claude to connect without a password"). Every DB-backed module errors, so the full suite has to
   be run by John or by CI.
9. **The preflight can't check prod, so the missing-manual-service check runs after the
   approval.** Issue #346 lists that check among the pre-approval candidates, and the PR review
   asked for it there. But under #313 every prod token needs `environment: prod`, so the
   preflight would need a new prod identity: a provider or reviewer-less environment without
   that claim, plus a `run.viewer` SA. That would be an exception to #313's rule that prod tokens
   exist only in the prod environment, made for a case that only happens while onboarding a
   manual service, which already has a runbook. Deferred (John, 2026-10-11). The check still runs
   before any prod mutation, so the cost of a miss is one wasted approval, never a partial roll-out.
   Revisit if a read-only prod identity is ever added for another reason.

## Checkpoint log

- **2026-10-11, implementation.** `prod_preflight.py`, the reworked workflow, `--missing-manual`,
  and the tests `tests/test_prod_preflight.py` (temp git repo, gcloud/gh stubbed) and
  `tests/test_prod_rollout.py` (wiring). The affected modules pass: 131 tests (`test_prod_rollout`,
  `test_prod_preflight`, `test_cloudrun_services`, `test_ci_migrate`, `test_ci_parallel`,
  `test_dependency_locks`, `test_check_deploy_ref`). In the full local suite every error was a
  Postgres.app connection refusal (gotcha 8); CI runs it.
- **2026-10-11, local preflight against test AR** (`--tag latest`, no artifact): every digest
  resolved, the commit came out as `9043003`, and it warned "migrations not pre-checked". With
  `--previous-sha` set to a commit before the old DROP migrations, those migrations were refused
  as expected.
- **Pending (John, after merge):** dispatch prod-rollout and check:
  - the approval prompt appears within about a minute;
  - the summary shows the digests and SHA;
  - one approval runs the job to the end;
  - a `promoted-sha` artifact exists.

  A second dispatch should report "0 since <sha>". Record both runs here in a docs-only PR.
