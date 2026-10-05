# Plan: scope the WIF trust conditions (issue #313)

## Context

Both Workload Identity Federation providers admitted **any workflow run in the repo**: the only
condition was `assertion.repository=='JohnFunkCode/StockPortfolioManager'`. A workflow on any
branch could therefore mint the deployer (`quantcore-deployer@`) in test or in prod, and the
prod secrets were repo-level, readable by every job. The `prod` environment's required reviewers
gated only the jobs that *declared* it.

Goal: prod tokens only for `prod-rollout.yml` on `main` inside the `prod` environment; test
tokens only for runs on `main`.

## What was checked before changing anything (2026-10-05, read-only)

- **Which jobs authenticate.** Only two workflows call `google-github-actions/auth`:
  - `deploy.yml`'s `deploy` job, which has no environment. It runs only after `preflight`,
    and `preflight` runs only on a push to main or a dispatch. **Pull-request runs never
    authenticate**, and dispatches always run from main (#120). So `ref=='refs/heads/main'` costs
    nothing on test.
  - `prod-rollout.yml`'s `promote-and-deploy` job, which declares `environment: prod`. The
    `gate` job doesn't authenticate.
- **The release trigger.** `prod-rollout.yml` also fired on `release: published`. A release run
  carries `ref=refs/tags/<tag>`, so a main-only condition would have refused it at token
  exchange. No release had ever been published, and every prod run on record (15 of them, since
  2026-08-10) was a dispatch from main. **The trigger was dropped** instead of widening the
  condition to tags.
- **Live config before the change:**
  - Prod `github-prod/github`: the mapping already had `attribute.environment`. Condition
    repo-only.
  - Test `github-test/github`: mapping `sub` + `repository`. Condition repo-only.
  - Both deployers' `workloadIdentityUser` binding is the `attribute.repository/...`
    principalSet. That binding stays as it is; the condition is what narrows it.
  - The `prod` environment: required reviewers, **no branch policy**, no environment secrets.

## Runbook (John applies: IAM and repo settings)

Order matters. The GitHub side goes first, so the provider never demands an environment claim
from a job that can't satisfy it. Merge this PR first, so the `release` trigger is gone.

**1. Give the `prod` environment its secrets, then delete the repo-level copies.** The values
are resource names, not credentials:

```bash
gh secret set GCP_PROD_WIF_PROVIDER --env prod --body "projects/127961694257/locations/global/workloadIdentityPools/github-prod/providers/github"
gh secret set GCP_PROD_DEPLOY_SA --env prod --body "quantcore-deployer@quantcore-prod-20260606.iam.gserviceaccount.com"
gh secret delete GCP_PROD_WIF_PROVIDER
gh secret delete GCP_PROD_DEPLOY_SA
```

**2. Make `prod` deployable from main only.** Do this in the UI, which leaves the reviewers
alone: Settings → Environments → `prod` → Deployment branches and tags → **Selected branches and
tags** → add the branch rule `main`. Keep "Allow administrators to bypass" off, as on `deps-lock`.

**3. Narrow the prod provider:**

```bash
gcloud iam workload-identity-pools providers update-oidc github \
  --workload-identity-pool=github-prod --location=global --project=quantcore-prod-20260606 \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.environment=assertion.environment,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.job_workflow_ref" \
  --attribute-condition="assertion.repository=='JohnFunkCode/StockPortfolioManager' && assertion.environment=='prod' && assertion.ref=='refs/heads/main' && assertion.job_workflow_ref=='JohnFunkCode/StockPortfolioManager/.github/workflows/prod-rollout.yml@refs/heads/main'"
```

**4. Narrow the test provider** by re-running the setup script, which now updates an existing
provider:

```bash
./scripts/setup_test_wif.sh
```

The script also re-applies its role grants. If you'd rather change only the provider, run it
directly:

```bash
gcloud iam workload-identity-pools providers update-oidc github \
  --workload-identity-pool=github-test --location=global --project=quantcore-test-20260606 \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.job_workflow_ref" \
  --attribute-condition="assertion.repository=='JohnFunkCode/StockPortfolioManager' && assertion.ref=='refs/heads/main'"
```

**5. Verify:**

- Read the result back:
  `gcloud iam workload-identity-pools providers describe github --workload-identity-pool=github-prod --location=global --project=quantcore-prod-20260606 --format='value(attributeCondition)'`
  (the same for `github-test`).
- Test positive: the next merge to main rolls out to test as usual.
- Test negative: dispatch `deploy.yml` from a scratch branch
  (`gh workflow run deploy.yml --ref <scratch-branch>`). Its `deploy` job must fail at the
  auth step, with an error naming the attribute condition. Nothing reaches test.
- Prod positive: the next normal prod dispatch from main (approve as usual) authenticates.
- Prod negative: `gh workflow run prod-rollout.yml --ref <scratch-branch>` is refused at the
  environment ("not allowed to deploy to prod") before any job runs.

**Rollback** (if a legitimate run is refused): re-run step 3 or 4 with
`--attribute-condition="assertion.repository=='JohnFunkCode/StockPortfolioManager'"`. This takes
effect within a minute or two, and nothing else needs undoing.

**6. Follow-ups from the issue (optional, separate):**

- An audit-log alert on `SetIamPolicy` / `UpdateWorkloadIdentityPoolProvider` in both projects.
- A review of repo collaborators with write access, since write access is what can push a
  branch.

## Gotchas

1. **`release` events run on the tag ref.** A main-only condition silently breaks
   release-triggered roll-outs. The issue's proposal didn't mention the trigger; it was found by
   reading `on:`. The trigger was dropped, since nobody had ever used it.
2. **The environment claim only exists for jobs that declare an environment.** Requiring
   `assertion.environment=='prod'` is safe only because the one prod-authenticating job declares
   it. A new job that authenticates to prod must declare `environment: prod` too, or its token
   exchange fails.
3. **`job_workflow_ref` pins the file, not just the repo.** Renaming `prod-rollout.yml` will
   break prod auth until the condition is updated to match.

## Checkpoint log

| Step | Result |
|---|---|
| Read-only checks (2026-10-05) | Done; recorded above. |
| PR: drop `release`, `setup_test_wif.sh` converges, docs | This PR. |
| 1. Environment secrets, repo copies deleted | Pending (John). |
| 2. `prod` branch policy main-only | Pending (John). |
| 3. Prod provider condition | Pending (John). |
| 4. Test provider condition | Pending (John). |
| 5. Verification | Pending. |
