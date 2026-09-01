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

The test CI ([`deploy.yml`](../../.github/workflows/deploy.yml)) builds the **five**
images — `quantcore-api`, `quantcore-mcp` (all 5 wrappers share it), `quantcore-report`,
`quantcore-ui` (QuantUI), and `quantcore-keyproxy` (BYOK) — and tags each with the
**7-char commit SHA**, deploying them to the **test** Cloud Run stack. Automatic
build-on-push is **live**: the test-project WIF secrets are wired
(`scripts/setup_test_wif.sh`), and a `preflight` job skips the deploy only if those
secrets are ever absent (e.g. on forks).

Promotion takes one such already-built, already-tested
**tag**, copies all five images **by digest** test AR → prod AR (`docker buildx
imagetools create`), and deploys prod **by the resolved original digest** (not the tag).
A manual-approval gate (`prod` GitHub Environment + required reviewers) sits in front of
the prod rollout. Per-service prod config (Cloud SQL binding, secrets, ingress,
resources) was set once during the manual first deploy (P5–P7) and is **preserved** on
every promotion — the workflow only carries the new image.

### MCP service request timeout

All streamable HTTP MCP wrappers use a 900-second Cloud Run request timeout. The value is carried
by `MCP_REQUEST_TIMEOUT` in both deployment workflows and passed explicitly on each image rollout,
so a later promotion cannot restore the former 300-second default. The shared `mcp_gateway.serve`
launcher also installs FastMCP's 30-second `PingMiddleware` keepalive. These settings bound the
transport connection; they do not make slow tool work unbounded. The REST seam remains limited by
`QUANTCORE_REST_TIMEOUT` (60 seconds by default).

For a first/manual service creation or an existing service that predates this policy, apply and
verify the setting without replacing environment variables or secrets:

```bash
gcloud run services update quantcore-stock-price \
  --project quantcore-prod-20260606 --region us-central1 --timeout 900s
gcloud run services describe quantcore-stock-price \
  --project quantcore-prod-20260606 --region us-central1 \
  --format='value(spec.template.timeoutSeconds)'
```

Repeat the update for the other MCP wrapper services before production verification. A successful
check should report `900` and subsequent `/mcp` requests should no longer end at 300 seconds.

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
   (or land just the workflow files there). *(Resolved: the workflows have lived on
   `main` since the phase-1 merge, and prod dispatches have run successfully — most
   recently `image_tag=177e411` for the BYOK rollout on 2026-07-18.)*

2. **Prod WIF + deploy SA + secrets + Environment exist** (one-time, done in P9):
   - Repo secrets `GCP_PROD_WIF_PROVIDER` and `GCP_PROD_DEPLOY_SA`
     (`gh secret list --repo … | grep GCP_PROD`).
   - A `prod` GitHub Environment **with required reviewers**
     (`gh api repos/…/environments -q '.environments[].name'`, then check reviewers in
     Settings → Environments → prod). Without a reviewer the approval gate won't pause.
   - The deploy SA `quantcore-deployer@quantcore-prod-20260606.iam.gserviceaccount.com`
     has, in prod: `run.developer`, `artifactregistry.writer` (prod AR),
     `iam.serviceAccountUser` on **both** runtime SAs — `quantcore-run@…` **and**
     `keyproxy-runtime@…` (the keyproxy runs as its own least-privilege SA; a missing
     grant there fails the rollout's final step with `iam.serviceaccounts.actAs`
     denied, as happened on the 2026-07-18 dispatch); and `artifactregistry.reader`
     on the **test** AR.

---

## Choosing the tag to promote

The workflow promotes **one tag across all five images**, so that tag should exist on
`quantcore-api`, `quantcore-mcp`, `quantcore-report`, `quantcore-ui`, and
`quantcore-keyproxy` in the test AR. (The ui and keyproxy steps tolerate an absent
tag/service — tags built before those images existed skip them cleanly.)

- **Normal case — a commit SHA.** Use the 7-char SHA that CI built when your change
  merged to `main` (it tags all five together). List what's available:

  ```bash
  for img in quantcore-api quantcore-mcp quantcore-report quantcore-ui quantcore-keyproxy; do
    echo "== $img =="
    gcloud artifacts docker tags list \
      us-central1-docker.pkg.dev/quantcore-test-20260606/quantcore/$img \
      --format='value(tag.basename())'
  done
  ```

  Pick a SHA present in **all five** lists (or accept the skip for pre-BYOK tags).

- **The current validated baseline is `177e411`** (the BYOK rollout, promoted and
  E2E-verified on prod 2026-07-18 — api digest `4e50638c…`, keyproxy `9b3b0ecb…`).
  The `latest` tag is the human-pinned, known-good marker; keep it pointed at the
  blessed set when you promote. **Do not assume a raw commit-SHA tag is blessed** —
  a newer build may sit under its SHA without having been promoted/validated. When in
  doubt, verify the digest behind the tag:

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

2. **If the change carries a schema change, migrate prod first.** Startup no longer creates
   tables on a Flyway-managed database — it checks them and raises `SchemaDriftError`, which
   fails the revision's health check (issue #165). So the migration has to land *before* the
   image that expects it:

   ```bash
   ./scripts/flyway.sh --prod migrate
   ```

   Then confirm the live schema actually matches what the new image expects — `flyway info` is
   a changelog view, not evidence:

   ```bash
   python scripts/schema_check.py --prod
   ```

   Skip this step only when nothing under `db/migrations/` changed between the deployed tag and
   the one you're promoting. Getting the order wrong is not an outage: the new revision fails
   its check and never takes traffic, so the previous revision keeps serving while you migrate.

3. **Dispatch the workflow** with the chosen tag:

   ```bash
   gh workflow run prod-rollout.yml \
     --repo JohnFunkCode/StockPortfolioManager \
     -f image_tag=<SHA-or-latest>
   ```

   (Or in the UI: Actions → **prod-rollout** → Run workflow → enter the tag.)

4. **Watch the gate job.** It spins up Postgres and runs unit tests + the wrapper smoke
   (`ci_wrapper_smoke.py`) + the OpenAPI surface diff (`check_openapi_snapshot.py`). If
   any fail, the promotion stops before touching prod — fix forward and re-dispatch.

   ```bash
   gh run watch --repo JohnFunkCode/StockPortfolioManager
   ```

5. **Approve the prod gate.** After the gate passes, the `promote-and-deploy` job pauses
   for the `prod` Environment's required reviewer. Approve it in the UI (the run page
   shows "Review deployments") or:

   ```bash
   gh run view --repo JohnFunkCode/StockPortfolioManager <run-id>     # find the pending review
   # then approve via the "Review deployments" button on the run page
   ```

6. **The deploy runs automatically** after approval: it resolves each image's source
   digest, `imagetools`-copies test AR → prod AR, verifies the original digest resolves
   in the prod AR, then `gcloud run deploy` (api + 5 wrappers + `quantui` +
   `quantcore-keyproxy`) and `gcloud run jobs update` (report) **by digest**. The
   quantui/keyproxy steps are image-only and skip when the tag predates those images or
   the service doesn't exist yet. Per-service env/secrets/Cloud-SQL bindings are
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
us-central1`) or by waiting for its scheduled run. (The former flaky-Yahoo crash is
fixed — `portfolio/yfinance_gateway.py` now retries with back-off and degrades
gracefully to all-None prices.)

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

The last-validated baseline is tag **`177e411`** (BYOK rollout, verified end-to-end on
prod 2026-07-18). Re-dispatching the workflow with `image_tag=177e411` restores that
set across the whole stack.

---

## Quick reference

| Item | Value |
| --- | --- |
| Prod project | `quantcore-prod-20260606` (region `us-central1`) |
| Prod AR | `us-central1-docker.pkg.dev/quantcore-prod-20260606/quantcore` |
| Prod API URL | `https://quantcore-api-127961694257.us-central1.run.app` |
| Deploy SA | `quantcore-deployer@quantcore-prod-20260606.iam.gserviceaccount.com` |
| Runtime SAs | `quantcore-run@…` (api/wrappers/report/ui) and `keyproxy-runtime@…` (keyproxy; deployer needs actAs on both) |
| WIF provider | `projects/127961694257/locations/global/workloadIdentityPools/github-prod/providers/github` |
| Repo secrets | `GCP_PROD_WIF_PROVIDER`, `GCP_PROD_DEPLOY_SA` |
| Approval gate | `prod` GitHub Environment + required reviewers |
| Blessed tag | `177e411` (BYOK rollout, validated 2026-07-18) |
