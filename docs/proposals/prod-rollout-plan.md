# Prod Rollout Plan — Phase 3 stack into `quantcore-prod-20260606`

## Checkpoint Log

Update this table as part of every step-commit. Any dev machine can resume by reading the last DONE row, running `git pull`, and continuing with the next step. Mirrors [`phase3-gateway-plan.md`](phase3-gateway-plan.md).

### Status: NOT STARTED — plan captured (2026-06-17). No GCP commands run yet against the prod project.

Phase 3 of [`architectural-standard-v2.md`](architectural-standard-v2.md) §11 is **complete on the test project** (`quantcore-test-20260606`): `quantcore-api` + 5 MCP wrapper services + a daily report Cloud Run Job all run on Cloud Run against the **test** Cloud SQL instance, JWT-enforced, with CI/CD in `.github/workflows/deploy.yml`. This plan covers the final operational milestone — standing up the **same stack in a dedicated production project** pointed at the production database.

**Why a separate prod project (not a test-service DSN flip):** the original Phase 3 exit framed the cutover as repointing the test-project services' `QUANTCORE_DB_DSN` from the test instance to prod. The user chose the stronger isolation boundary instead: **the test stack stays on test, untouched**, and production gets its *own* Cloud Run stack in its *own* project reaching its *own* Cloud SQL. This gives prod independent IAM, secrets, quota, and blast radius.

**The prod project + DB already exist.** `quantcore-prod-20260606:us-central1:quantcore` is a live Cloud SQL instance holding the production data (reached locally today via the `:5433` cloud-sql-proxy; the `:5434` proxy is test). What is missing is only the Cloud Run **compute** layer — this plan replicates Steps 7–10 of the Phase 3 plan into the prod project.

---

## Locked decisions (with the user)

1. **Image source → Option B2 (digest copy into a dedicated prod AR).** Prod owns a `quantcore` Artifact Registry repo; images are **copied by digest** from the test AR (`gcloud artifacts docker images copy …@sha256:…`), *not* rebuilt. This keeps prod self-contained (no standing cross-project pull dependency) **and** runs the exact bytes validated on test (no rebuild-divergence). Rejected: Option A (cross-project pull — reintroduces a permanent prod→test dependency, against the isolation goal) and B1 (rebuild in prod — second pipeline *and* gives up the same-artifact guarantee).
2. **Separate prod GCP project.** Full deployment into `quantcore-prod-20260606`; the test stack is never repointed at the prod DB.
3. **New prod JWT secret.** A brand-new HS256 key in the prod project — the test `quantcore-jwt-secret` is **never** reused across the trust boundary.
4. **No reseed.** The prod `positions` table already holds the live data; `scripts/import_portfolio.py` is **never** run against the prod DB.
5. **Prod deploys stay gated/manual.** No auto-deploy to prod on every push to `main`. Prod promotion is a deliberate `workflow_dispatch` (or tag) job with its own WIF provider/SA and a `prod` GitHub Environment — consistent with "prod-flip deliberately not automated."

## Standing constraints

- **DB safety is paramount.** All commands here target `quantcore-prod-20260606`. The production DSN (`QUANTCORE_DB_DSN`, the `:5433` proxy locally) is the live database — read-only smokes only, **no schema reseed, no destructive ops**. The test stack and test DB are untouched by this plan.
- **The user runs all IAM / high-severity GCP commands and commits/pushes themselves.** I provide the exact command + the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` footer; the user executes.
- **Rule 6 is sacred.** Wrappers + front end go through REST; the report Job calls services **in-process** (anti-pattern 5), never the HTTP tier — identical to test.
- One commit per code/doc step (the `prod-rollout.yml` workflow, doc updates); the GCP buildout steps are gcloud/IAM actions logged in this table.

---

## Open decisions to resolve before / during rollout

These are **not yet locked** — flagged so they're not silently assumed:

- **Runtime service account.** Reuse the prod Compute Engine default SA (`<prod-proj-num>-compute@developer.gserviceaccount.com`, mirrors test) **or** create a dedicated least-privilege runtime SA per service. Test used the compute default; prod is the right place to tighten if desired.
- **Discord channel.** Same webhook as test (alerts interleave) **or** a separate prod Discord channel/webhook. Affects the `quantcore-discord-webhook` secret payload.
- **JWT issuance for real AI clients.** Who mints the HS256 tokens external AI clients present (`QUANTCORE_MCP_TOKEN`)? Test used hand-minted tokens for smokes. Prod needs a real issuance story (a small signing endpoint/CLI, rotation policy) before the wrappers are meaningfully usable by humans. May warrant its own design pass.
- **Initial scope.** All 5 wrappers + report Job from day one, **or** a narrower first cut (e.g. `quantcore-api` + report Job, wrappers later)? Narrower reduces the prod surface to validate first.
- **Prod region / HA.** Test is single-region `us-central1`. Confirm prod stays there; revisit Cloud SQL HA / Cloud Run min-instances for prod SLOs separately.

---

## Step-by-step plan (one commit per code/doc step; GCP steps logged in the table)

| Step | Description | Status | Commit | Date | Notes |
|---|---|---|---|---|---|
| P0 | Plan + decisions captured | DONE | — | 2026-06-17 | This document. Image source = B2 (digest copy → dedicated prod AR); separate prod project; new prod JWT secret; no reseed; gated prod deploys. Prod project + Cloud SQL (`quantcore-prod-20260606:us-central1:quantcore`) already exist with live data. Open decisions (runtime SA, Discord channel, JWT issuance, initial scope, HA) listed above — resolve before the matching step. |
| P1 | Enable prod APIs | TODO | | | On `quantcore-prod-20260606`: `run`, `secretmanager`, `artifactregistry`, `cloudbuild` (for the copy/permissions, not builds), `cloudscheduler`, `iam`. `gcloud services enable …`. |
| P2 | Create prod AR repo | TODO | | | Docker AR repo **`quantcore`** in `us-central1` on the prod project → `us-central1-docker.pkg.dev/quantcore-prod-20260606/quantcore`. |
| P3 | Promote images (B2 digest copy) | TODO | | | Copy the **three** tested images **by digest** test→prod (no rebuild): `quantcore-api`, `quantcore-mcp`, `quantcore-report`. Per image: `gcloud artifacts docker images copy us-central1-docker.pkg.dev/quantcore-test-20260606/quantcore/<img>@sha256:<digest> us-central1-docker.pkg.dev/quantcore-prod-20260606/quantcore/<img>:<sha>`. **Reference the source by `@sha256:` to guarantee identical bytes.** The copier identity needs `reader` on the test AR + `writer` on the prod AR. Record the promoted digests here. |
| P4 | Prod secrets + IAM | TODO | | | Secret Manager (prod project): `quantcore-prod-db-dsn` = unix-socket DSN `postgresql://quantcore:***@/quantcore?host=/cloudsql/quantcore-prod-20260606:us-central1:quantcore`; **new** `quantcore-jwt-secret` (freshly generated 48-byte HS256 key, NOT the test key); `quantcore-discord-webhook` (**quote-stripped** — `.env` double-quote bug from Phase 3 Step 9). **IAM (user runs):** grant the prod runtime SA `roles/secretmanager.secretAccessor` on each secret (basic `roles/editor` excludes secret-payload access). |
| P5 | Deploy `quantcore-api` (prod) | TODO | | | From the prod-AR `quantcore-api` digest: `--add-cloudsql-instances quantcore-prod-20260606:us-central1:quantcore --set-secrets QUANTCORE_DB_DSN=quantcore-prod-db-dsn:latest,QUANTCORE_JWT_SECRET=quantcore-jwt-secret:latest --allow-unauthenticated --ingress all --memory 4Gi --cpu 2 --cpu-boost`. App-level JWT is the enforcement layer (Cloud Run IAM open, same rationale as test Step 8). Smoke: `/api/health` 200 `db_connected:true`; protected route no-token → 401; minted **prod** token → 200. |
| P6 | Deploy 5 wrappers (prod) | TODO | | | All from the single prod-AR `quantcore-mcp` digest, per-wrapper `SERVER_MODULE` + `QUANTCORE_REST_URL`→prod api, `--allow-unauthenticated --ingress all` (public + app-JWT passthrough; internal-ingress lockdown still deferred until a client VPC/IAP path exists). Services `quantcore-{stock-price,options-analysis,company-fundamentals,news-sentiment,market-analysis}`. Smoke each wrapper's `listTools()` + one authenticated call end-to-end (use the slash-less `/mcp` URL — Step 8 gotcha). |
| P7 | Report Job + Scheduler (prod) | TODO | | | `quantcore-report` Cloud Run **Job** from the prod-AR `quantcore-report` digest; `--set-secrets QUANTCORE_DB_DSN=quantcore-prod-db-dsn:latest` (+ `DISCORD_WEBHOOK_URL=quantcore-discord-webhook:latest` if Discord opted in) `--set-cloudsql-instances …prod…:quantcore --task-timeout 600 --max-retries 1 --memory 1Gi`. **No JWT** (no HTTP surface; in-process services). Cloud Scheduler `quantcore-report-daily` weekdays `0 17 * * 1-5 America/New_York`. **IAM (user runs):** Cloud Scheduler service agent needs `roles/iam.serviceAccountTokenCreator` on the OAuth SA (the trigger silently no-ops otherwise — Phase 3 Step 9). **No reseed** — prod DB already has positions. Verify one manual `jobs execute`. |
| P8 | End-to-end prod verification | TODO | | | All services healthy; api JWT-enforced publicly; wrappers forward the caller JWT; report Job runs once green against the prod DB (read + report render + Discord if enabled). **Read-only** — no writes beyond what the normal report path does. |
| P9 | Gated prod CI/CD + repoint clients | TODO | | | **`.github/workflows/prod-rollout.yml` NEW** — `workflow_dispatch` (or `release`/tag) only, a `prod` GitHub Environment (manual approval), separate WIF provider/SA (`GCP_PROD_WIF_PROVIDER`/`GCP_PROD_DEPLOY_SA`). Job: re-run the Phase 3 `gate`, then the **B2 digest-copy promotion** (test AR → prod AR by digest) + `gcloud run deploy`/`jobs update` against prod (image-only; env/secrets preserved). Never triggered by a plain push to `main`. **Clients:** add a prod entry set to the prod wrapper `/mcp` URLs with prod-`QUANTCORE_MCP_TOKEN`; test URLs become staging. |
| P10 | Prod exit + docs | TODO | | | Update this status header → COMPLETE; note in `CLAUDE.md` / `architectural-standard-v2.md` §11 that prod is live; record promoted digests + prod service URLs. Phase 3 fully closed. |

---

## Verification

- **Per GCP step:** the specific smoke listed in its row (health/JWT/listTools/job-execute), against `quantcore-prod-20260606` only.
- **DB safety:** confirm at every step that no command names the test project for *writes* and no command reseeds/migrates the prod DB; the report path is the only thing that touches prod data and it does so exactly as it does today locally.
- **Artifact integrity (B2):** the prod-AR image digest equals the test-AR digest it was copied from (copy-by-digest guarantees this — spot-check with `gcloud artifacts docker images describe`).
- **Exit:** prod `quantcore-api` + 5 wrappers + report Job all healthy on Cloud Run in the prod project, JWT-enforced, pulling images from the **prod** AR (zero cross-project pull at runtime), pointed at the prod Cloud SQL.

## Risks & mitigations

- **Accidental prod-DB write/reseed** → no `import_portfolio.py`/migration against prod; read-only smokes; P0 locks "no reseed."
- **Rebuild divergence** → eliminated by B2 (copy by digest, never rebuild for prod).
- **Cross-project coupling creeping back** → prod pulls only from the prod AR at runtime; the test AR is touched only by the one-time/promotion `copy`, never by a running prod service.
- **JWT key bleed across environments** → prod gets a freshly generated key in its own Secret Manager; the test key is never copied.
- **Auto-deploying prod on a routine merge** → prod workflow is `workflow_dispatch`/tag + a `prod` Environment gate; the Phase 3 `deploy.yml` continues to target only the test project.
- **Token issuance unresolved** → flagged as an open decision; wrappers are deployable but not human-usable until a real prod token-issuance story exists.

## Critical files / targets

- `docs/proposals/prod-rollout-plan.md` (this file) — the checkpoint log.
- `.github/workflows/prod-rollout.yml` (new, Step P9) — gated prod promotion.
- `cloudbuild.yaml` — reused only as the build source for the test AR; prod consumes the **output** via digest copy, not a prod rebuild.
- GCP targets: prod project `quantcore-prod-20260606`, region `us-central1`, AR repo `quantcore` (new), Cloud SQL `quantcore-prod-20260606:us-central1:quantcore` (existing, live data).
- Reference: [`phase3-gateway-plan.md`](phase3-gateway-plan.md) Steps 7–10 — the test-project equivalents this plan mirrors.
