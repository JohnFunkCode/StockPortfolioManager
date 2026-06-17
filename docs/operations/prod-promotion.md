# Promoting changes into production

This is the runbook for shipping a change to the **production** stack
(`quantcore-prod-20260606`, region `us-central1`). Production runs the exact image
**bytes** validated on test — we never rebuild for prod. Promotion = copy a tested
image set from the test Artifact Registry into the prod AR by digest, then roll Cloud
Run over to it, behind a gated, manually-approved GitHub workflow.

- **Workflow:** [`.github/workflows/prod-rollout.yml`](../../.github/workflows/prod-rollout.yml)
- **One-time infra (already done):** see [`docs/proposals/prod-rollout-plan.md`](../proposals/prod-rollout-plan.md) (steps P1–P8).
- **Minting a token to verify prod afterward:** [`prod-jwt-tokens.md`](prod-jwt-tokens.md).

---

## The model in one paragraph

The test CI ([`deploy.yml`](../../.github/workflows/deploy.yml)) builds the **three**
images — `quantcore-api`, `quantcore-mcp` (all 5 wrappers share it), `quantcore-report`
— and tags each with the **7-char commit SHA**, deploying them to the **test** Cloud Run
stack.

> **Current state (P9):** `deploy.yml`'s automatic build-on-push is **disabled** —
> the test-project WIF and the `GCP_WIF_PROVIDER`/`GCP_DEPLOY_SA` secrets aren't wired
> yet, so the workflow is `workflow_dispatch`-only. Until that follow-up lands, test
> images are built **manually** (`gcloud builds submit` / Cloud Build) and tagged by
> hand, then promoted here exactly as below. The promotion path itself is unaffected —
> it only needs tagged images in the test AR, however they got there.

Promotion takes one such already-built, already-tested
**tag**, copies all three images **by digest** test AR → prod AR (`docker buildx
imagetools create`), and deploys prod **by the resolved original digest** (not the tag).
A manual-approval gate (`prod` GitHub Environment + required reviewers) sits in front of
the prod rollout. Per-service prod config (Cloud SQL binding, secrets, ingress,
resources) was set once during the manual first deploy (P5–P7) and is **preserved** on
every promotion — the workflow only carries the new image.

```
push to main ──► deploy.yml builds+tests, tags <SHA>, deploys TEST
                                   │
                  (validate on test)
                                   ▼
operator dispatches prod-rollout.yml -f image_tag=<SHA>
   gate job: tests + wrapper smoke + OpenAPI surface diff
                                   │  (must pass)
                                   ▼
   manual approval (prod Environment reviewer)
                                   │
                                   ▼
   promote-and-deploy: imagetools copy by digest TEST AR ─► PROD AR
                       gcloud run deploy / jobs update PROD by digest
```

---

## Prerequisites (check once per repo, and after any workflow edit)

1. **The workflow must live on the default branch (`main`).** GitHub only exposes a
   `workflow_dispatch` workflow for dispatch when it exists on the **default branch**.
   If `prod-rollout.yml` is only on a feature branch it is **not dispatchable** — `gh
   workflow list` won't show it and the UI "Run workflow" button won't appear. Confirm:

   ```bash
   gh workflow list --repo JohnFunkCode/StockPortfolioManager        # must list prod-rollout
   gh api "repos/JohnFunkCode/StockPortfolioManager/contents/.github/workflows?ref=main" -q '.[].name'
   ```

   If it's missing, merge the branch carrying `.github/workflows/*` into `main` first
   (or land just the workflow files there). **This is currently the open gate: the CI
   workflows live on `feature/new-architecture-phase1` and have not yet been merged to
   `main`, so the first prod dispatch is blocked until that merge happens.**

2. **Prod WIF + deploy SA + secrets + Environment exist** (one-time, done in P9):
   - Repo secrets `GCP_PROD_WIF_PROVIDER` and `GCP_PROD_DEPLOY_SA`
     (`gh secret list --repo … | grep GCP_PROD`).
   - A `prod` GitHub Environment **with required reviewers**
     (`gh api repos/…/environments -q '.environments[].name'`, then check reviewers in
     Settings → Environments → prod). Without a reviewer the approval gate won't pause.
   - The deploy SA `quantcore-deployer@quantcore-prod-20260606.iam.gserviceaccount.com`
     has, in prod: `run.developer`, `artifactregistry.writer` (prod AR),
     `iam.serviceAccountUser` on the runtime SA `quantcore-run@…`; and
     `artifactregistry.reader` on the **test** AR.

---

## Choosing the tag to promote

The workflow promotes **one tag across all three images**, so that tag must exist on
`quantcore-api`, `quantcore-mcp`, and `quantcore-report` in the test AR.

- **Normal case — a commit SHA.** Use the 7-char SHA that CI built when your change
  merged to `main` (it tags all three together). List what's available:

  ```bash
  for img in quantcore-api quantcore-mcp quantcore-report; do
    echo "== $img =="
    gcloud artifacts docker tags list \
      us-central1-docker.pkg.dev/quantcore-test-20260606/quantcore/$img \
      --format='value(tag.basename())'
  done
  ```

  Pick a SHA present in **all three** lists.

- **The `latest` tag is the human-pinned, known-good set.** `latest` is maintained to
  point at the currently-blessed digests (as of 2026-06-17: api `ac5cd17f`, mcp
  `1b7da905`, report `65d70659` — the digests validated end-to-end in P8 and running in
  prod). Promoting `image_tag=latest` redeploys exactly those bytes (a safe no-op
  validation if prod already runs them). **Do not assume a raw commit-SHA tag is
  blessed** — e.g. a newer build may sit under its SHA without `latest` having moved to
  it, meaning it hasn't been promoted/validated. When in doubt, verify the digest behind
  the tag:

  ```bash
  gcloud artifacts docker images describe \
    us-central1-docker.pkg.dev/quantcore-test-20260606/quantcore/quantcore-mcp:<tag> \
    --format='value(image_summary.digest)'
  ```

> The promote step copies the manifest with `docker buildx imagetools create`, which
> wraps the prod **tag** in a new OCI index (different top-level digest). This is
> expected — the workflow re-resolves and deploys by the **original** digest, which is
> pushed verbatim and stays addressable in the prod AR. Don't "fix" the prod tag.

---

## Promote (the actual procedure)

1. **Validate on test first.** Confirm the tag you're promoting is healthy on the test
   stack (the team's daily driver) — the prod images are byte-identical, so a problem on
   test is a problem in prod.

2. **Dispatch the workflow** with the chosen tag:

   ```bash
   gh workflow run prod-rollout.yml \
     --repo JohnFunkCode/StockPortfolioManager \
     -f image_tag=<SHA-or-latest>
   ```

   (Or in the UI: Actions → **prod-rollout** → Run workflow → enter the tag.)

3. **Watch the gate job.** It spins up Postgres and runs unit tests + the wrapper smoke
   (`ci_wrapper_smoke.py`) + the OpenAPI surface diff (`check_openapi_snapshot.py`). If
   any fail, the promotion stops before touching prod — fix forward and re-dispatch.

   ```bash
   gh run watch --repo JohnFunkCode/StockPortfolioManager
   ```

4. **Approve the prod gate.** After the gate passes, the `promote-and-deploy` job pauses
   for the `prod` Environment's required reviewer. Approve it in the UI (the run page
   shows "Review deployments") or:

   ```bash
   gh run view --repo JohnFunkCode/StockPortfolioManager <run-id>     # find the pending review
   # then approve via the "Review deployments" button on the run page
   ```

5. **The deploy runs automatically** after approval: it resolves each image's source
   digest, `imagetools`-copies test AR → prod AR, verifies the original digest resolves
   in the prod AR, then `gcloud run deploy` (api + 5 wrappers) and `gcloud run jobs
   update` (report) **by digest**. Per-service env/secrets/Cloud-SQL bindings are
   untouched.

---

## Verify production after a promotion

```bash
# Health (no auth needed)
curl -s https://quantcore-api-127961694257.us-central1.run.app/api/health
# -> {"status":"ok","db_connected":true}

# Authenticated read (mint a token per docs/operations/prod-jwt-tokens.md)
TOKEN="$(python scripts/mint_prod_jwt.py --expires-hours 1)"
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://quantcore-api-127961694257.us-central1.run.app/api/portfolio?owner=john"

# Confirm the services are serving the digest you promoted
for s in quantcore-api quantcore-stock-price; do
  gcloud run services describe "$s" --project quantcore-prod-20260606 \
    --region us-central1 --format='value(spec.template.spec.containers[0].image)'
done
```

The report **Job** has no HTTP surface; verify it by running it once
(`gcloud run jobs execute quantcore-report --project quantcore-prod-20260606 --region
us-central1`) or by waiting for its scheduled run. (Note the open follow-up: the report
path can crash on a flaky Yahoo response — see the P7 row in the rollout plan.)

---

## Rollback

Promotion is just "deploy a digest," so rollback is "deploy the previous digest." Find
the prior good digest (Cloud Run keeps revision history) and redeploy it:

```bash
# List recent revisions + their images
gcloud run revisions list --service quantcore-api \
  --project quantcore-prod-20260606 --region us-central1 \
  --format='table(metadata.name, spec.containers[0].image)'

# Redeploy a known-good digest directly
gcloud run deploy quantcore-api --project quantcore-prod-20260606 --region us-central1 \
  --image us-central1-docker.pkg.dev/quantcore-prod-20260606/quantcore/quantcore-api@<good-digest>
```

The last-validated baseline (P8) is api `ac5cd17f` / mcp `1b7da905` / report `65d70659`
— the `latest` tag's digests. Re-dispatching the workflow with `image_tag=latest`
restores that set across the whole stack.

---

## Quick reference

| Item | Value |
| --- | --- |
| Prod project | `quantcore-prod-20260606` (region `us-central1`) |
| Prod AR | `us-central1-docker.pkg.dev/quantcore-prod-20260606/quantcore` |
| Prod API URL | `https://quantcore-api-127961694257.us-central1.run.app` |
| Deploy SA | `quantcore-deployer@quantcore-prod-20260606.iam.gserviceaccount.com` |
| Runtime SA | `quantcore-run@quantcore-prod-20260606.iam.gserviceaccount.com` |
| WIF provider | `projects/127961694257/locations/global/workloadIdentityPools/github-prod/providers/github` |
| Repo secrets | `GCP_PROD_WIF_PROVIDER`, `GCP_PROD_DEPLOY_SA` |
| Approval gate | `prod` GitHub Environment + required reviewers |
| Blessed tag | `latest` (pinned to the validated digest set) |
