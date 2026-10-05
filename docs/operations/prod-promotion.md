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

The test CI ([`deploy.yml`](../../.github/workflows/deploy.yml)) builds the **seven**
images — `quantcore-api`, `quantcore-mcp` (all 7 wrappers share it), `quantcore-report`,
`quantcore-ui` (QuantUI), `quantcore-keyproxy` (BYOK), `quantcore-news`, and
`quantcore-migrate` (Flyway, issue #200) — and tags each with the **7-char commit SHA**,
migrating the test database and then deploying them to the **test** Cloud Run stack. Automatic
build-on-push is **live**: the test-project WIF secrets are wired
(`scripts/setup_test_wif.sh`), and a `preflight` job skips the deploy only if those
secrets are ever absent (e.g. on forks).

Promotion takes one such already-built, already-tested
**tag**, copies all seven images **by digest** test AR → prod AR (`docker buildx
imagetools create`), **migrates the prod database** with the `quantcore-migrate` Job, and
deploys prod **by the resolved original digest** (not the tag).
A manual-approval gate (`prod` GitHub Environment + required reviewers) sits in front of
the prod rollout. Per-service prod config (port, resources, timeout, ingress, runtime SA, env, secrets,
Cloud SQL) comes from the service inventory, `deploy/cloudrun-services.toml` (#161). Each
promotion re-asserts it, applying only what differs and keeping any key the inventory does not
name. A wrapper missing from prod is **created** from the inventory behind this gate. A missing
api, keyproxy or quantui fails the promotion, naming its runbook. Before dispatching, preview
what a promotion would change with
`python scripts/cloudrun_services.py check --env prod` (read-only). See
[`cloudrun-services.md`](../architecture/cloudrun-services.md).

### MCP service request timeout

All streamable HTTP MCP wrappers use a 900-second Cloud Run request timeout. The value is each
wrapper's `timeout` in `deploy/cloudrun-services.toml`, passed explicitly on every roll-out, so a
later promotion cannot restore the former 300-second default. The shared `mcp_gateway.serve`
launcher also installs FastMCP's 30-second `PingMiddleware` keepalive. These settings bound the
transport connection; the pings help clients and intermediaries that enforce idle limits, but they
do not extend Cloud Run's absolute request deadline. The REST seam remains limited by
`QUANTCORE_REST_TIMEOUT` (60 seconds by default).

Every roll-out and every CI-created wrapper applies the timeout from the inventory. To verify one
service by hand (or apply it out of band, without replacing environment variables or secrets):

```bash
gcloud run services update quantcore-stock-price \
  --project quantcore-prod-20260606 --region us-central1 --timeout 900s
gcloud run services describe quantcore-stock-price \
  --project quantcore-prod-20260606 --region us-central1 \
  --format='value(spec.template.timeoutSeconds)'
```

A successful
check should report `900` and subsequent `/mcp` requests should no longer end at 300 seconds.

For the end-to-end check, run `scripts/mcp_http_smoke.py` with one `--endpoint name=url` and
matching `--tool name=tool` per wrapper. Use `--hold-seconds 301` for the stock-price endpoint
when explicitly checking the former boundary. The tool prints status metadata only; do not add
tokens or response bodies to logs. A request that ends at roughly 901 seconds is the expected new
Cloud Run hard boundary and is distinct from the old 301-second failure.

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
                       quantcore-migrate Job: flyway migrate PROD (stops here on failure)
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
     on the **test** AR. For the migrate step (#200) it also needs
     `iam.serviceAccountUser` on `quantcore-migrate@…` and `roles/logging.viewer` (to
     print a failed migration's log). `scripts/ensure_migrate_job.sh --prod` grants both. To **create** a new
     MCP wrapper (#161) it would also need `run.services.setIamPolicy` to make the service public.
     That grant is **declined** (2026-10-05), so the step refuses to create the service and
     prints the bind command. Create it by hand as in
     [`cloudrun-services.md`](../architecture/cloudrun-services.md#onboarding-a-wrapper-by-hand).
   - The **`quantcore-migrate` Job** exists in prod. Without it the migrate step fails and
     nothing rolls out — deliberately, not a skip. One-time setup:
     [`flyway-automation-plan.md`](../proposals/flyway-automation-plan.md) Step 5.

---

## Choosing the tag to promote

The workflow promotes **one tag across all seven images**, so that tag must exist on
`quantcore-api`, `quantcore-mcp`, `quantcore-report`, `quantcore-ui` and
`quantcore-migrate` in the test AR. `quantcore-keyproxy` and `quantcore-news` tolerate an
absent tag (tags built before those images existed skip them cleanly). **`quantcore-migrate`
does not**: a tag with no `quantcore-migrate` image (anything built before it, #200 Step 2)
cannot be promoted, because rolling out without migrating is the failure the migrate step
removes. The `quantcore-migrate` tag list below is the test of whether a tag qualifies.

- **Normal case — a commit SHA.** Use the 7-char SHA that CI built when your change
  merged to `main` (it tags all seven together). List what's available:

  ```bash
  for img in quantcore-api quantcore-mcp quantcore-report quantcore-ui quantcore-keyproxy quantcore-news quantcore-migrate; do
    echo "== $img =="
    gcloud artifacts docker tags list \
      us-central1-docker.pkg.dev/quantcore-test-20260606/quantcore/$img \
      --format='value(tag.basename())'
  done
  ```

  Pick a SHA present in **all seven** lists.

- **The last recorded validated baseline was `177e411`** (the BYOK rollout, promoted and
  E2E-verified on prod 2026-07-18 — api digest `4e50638c…`, keyproxy `9b3b0ecb…`). It
  predates `quantcore-migrate`, so it can no longer be promoted; it is history, not a target.
  The `latest` tag is the human-pinned, known-good marker; keep it pointed at the
  blessed set when you promote. Only a push to main moves it: a `deploy.yml` dispatch of
  another ref to test (#120) tags its build `:dispatch-latest` instead. A SHA tag from such a
  dispatch is an **unmerged** build — never promote one. **Do not assume a raw commit-SHA tag is blessed** —
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

2. **Schema changes migrate themselves** (issue #200). The workflow runs the
   `quantcore-migrate` Job against prod after the promotion and **before** anything rolls
   out, on every release (a no-op when nothing is pending). If it fails, nothing rolls out and
   the previous revisions keep serving on an unchanged schema; the step's log says which case
   applies:

   - **`migration failed`** — the failing version was rolled back. Fix forward with a new
     migration (never edit an applied one) and re-dispatch.
   - **`migration refused`** — a pending migration is a contract change (drop/rename) or
     non-transactional, which CI deliberately won't apply. Apply it by hand, then re-dispatch:

     ```bash
     ./scripts/flyway.sh --prod migrate
     python scripts/schema_check.py --prod   # flyway info is a changelog, not evidence
     ```

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
   in the prod AR, executes the `quantcore-migrate` Job **by digest** and waits for it,
   then `gcloud run deploy` (api + 7 wrappers + `quantui` + `quantcore-keyproxy`) and
   `gcloud run jobs update` (report, news) **by digest**. The keyproxy/news steps are
   image-only and skip when the tag predates those images or the service doesn't exist
   yet. Per-service env/secrets/Cloud-SQL bindings are untouched.

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

**Roll back by revision or digest, not by re-dispatching an old tag.** A tag that predates
`quantcore-migrate` (such as the old `177e411` baseline) can no longer be promoted, and
schema changes are forward-fix only (#200): an older image runs against the newer schema,
which expand-only migrations keep compatible. Re-dispatching a tag that *does* carry the
migrate image is fine — its migrate step finds nothing pending.

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
| Blessed tag | none recorded since `177e411` (2026-07-18), which predates `quantcore-migrate` and can't be promoted; pick a SHA present in all seven repos |
