# QuantUI on Cloud Run behind Identity-Aware Proxy (IAP) — plan + checkpoint log

> Status: **test project complete (Steps 1–7); prod promotion (Step 8) pending.** This doc is the
> canonical plan and checkpoint log for deploying the QuantUI front end to Cloud Run secured by Google
> IAP — mirroring the cadence of `phase3-gateway-plan.md` / `prod-rollout-plan.md`. One commit per
> step, pushed, logged below.

## Checkpoint log

| Step | What | Status | Commit |
|------|------|--------|--------|
| 1 | Express serve+proxy (`frontend/server/`), `Dockerfile.ui`, `frontend/.dockerignore` | ☑ done | PR #58 |
| 2 | Optional `quantui` service in `docker-compose.yml` (local stack parity) | ☑ done | PR #58 |
| 3 | Wire `build-ui` into `cloudbuild.yaml` + Deploy step in `deploy.yml` | ☑ done | PR #58 (+ `.gcloudignore` fix PR #59) |
| 4 | Secret Manager `quantui-api-token` (test) + accessor grant | ☑ done | (user/GCP) |
| 5 | First Cloud Run deploy with `--iap` (test) | ☑ done | (user/GCP) |
| 6 | OAuth consent (External) + custom OAuth client + IAP IAM grants (test) | ☑ done | (user/GCP) + `scripts/{grant,attach}_quantui_iap_*.sh` (PR #60) |
| 7 | Verify IAP login + proxy end-to-end (test) | ☑ done | token-trim fix PR #60 (`quantui-00004-c5v`) |
| 8 | Promote to prod (`quantcore-prod-20260606`, by digest, via `prod-rollout.yml`) | ☐ (user/GCP) | — |

### Step 4–7 results & findings (test project, 2026-06-17/18)

- **Non-org project needs a custom OAuth client.** The IAP OAuth Admin APIs
  (`gcloud iap oauth-brands`/`oauth-clients`) were **shut down 2026-03-19** and are org-gated anyway,
  and a standalone (non-Organization) project's `--iap` deploy can **not** auto-provision a client —
  symptom **502 "Empty Google Account OAuth client ID(s)/secret(s)"**. Fix: create the OAuth consent
  screen + a **Web-application OAuth client** in the Console, add redirect URI
  `https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect`, and attach it via
  `gcloud iap settings set` (helper `scripts/attach_quantui_iap_oauth.sh`). Test client id
  `493357101423-…apps.googleusercontent.com`.
- **Two lists gate login** while the consent screen is in *Testing*: an account must be **both** a
  consent-screen **test user** **and** hold **`roles/iap.httpsResourceAccessor`** on the service. Five
  dev accounts granted via `scripts/grant_quantui_iap_access.sh`. (`--resource-type=cloud-run` +
  `--region` is the correct shape for direct Cloud Run IAP; the brief's `web-types` was outdated.)
- **Trailing-newline header crash (root-caused).** Piping `mint_prod_jwt.py` output into
  `gcloud secrets versions add --data-file=-` stored a trailing `\n`; the proxy injecting
  `Bearer <token>\n` threw `ERR_INVALID_CHAR` in `onProxyReq` and **crashed the whole process**, so
  every request (even `favicon.ico`) 503'd. Fixed two ways: re-stored the secret without the newline
  (`… | tr -d '\n' | gcloud secrets versions add …` + `services update` for a new revision), **and**
  hardened the code — `server.mjs` now `.trim()`s `QUANTCORE_API_TOKEN` at read time (PR #60). CI then
  rolled the hardened image; live revision **`quantui-00004-c5v`** (`quantcore-ui:4d26cb7`).
- **Verified:** authorized Google account → IAP login → SPA loads → data grids populate end-to-end
  (`browser → IAP → quantui → quantcore-api → service → Cloud SQL`); no JWT in any browser-issued
  request (bearer added only on the server hop). **Test project complete.**

---

## Context

QuantUI (`frontend/`) is the React 19 + Vite 6 SPA front end for the QuantCore platform. The
backend (`quantcore-api` + 5 MCP wrappers + report Job) is already on Cloud Run in both the
**test** (`quantcore-test-20260606`) and **prod** (`quantcore-prod-20260606`) projects, but the
UI still only runs locally via `npm run dev` (`runUI-MAC.sh`). We want the dev team to reach the
**real UI** from anywhere, restricted to their Google accounts, **without writing auth code** —
exactly what Cloud Run's **direct IAP integration** provides (Google blocks unauthorized traffic at
the perimeter of the `*.run.app` URL).

Two facts shape the design:

1. **QuantUI is a static SPA, not a Node server.** `npm run build` (`tsc -b && vite build`) emits a
   static `dist/`. It needs *something* to serve it on Cloud Run.
2. **The API is JWT-enforced.** `api/auth.py` requires an app-level HS256 bearer
   (`QUANTCORE_JWT_SECRET`) on the deployed api in both projects. **IAP secures who can load the
   UI; it does NOT authenticate the UI→API hop** — that still needs the app JWT. Today
   `vite.config.ts` proxies `/api/*` to the API server-side (so the browser stays same-origin, no
   CORS) and the token is supplied out-of-band. We preserve that same-origin + server-side-token
   model in production.

**Decisions locked with the user:**
- **API auth model → server-side proxy.** Serve `dist/` from a tiny container that reverse-proxies
  `/api/*` to `quantcore-api`, injecting the bearer token **server-side** from Secret Manager. The
  token never reaches the browser; same-origin (no CORS); mirrors the existing Vite dev proxy.
- **Target → test project first**, validate IAP + team login + proxy end-to-end, **then promote to
  prod** (same image/pattern in `quantcore-prod-20260606`).
- **Build/rollout → Dockerfile + CI**, matching the existing `Dockerfile.{api,mcp,report}` +
  `cloudbuild.yaml` + `.github/workflows/deploy.yml` pattern (reproducible, git-SHA-tagged), not the
  brief's `--source .` buildpack path.

**Standing constraints (unchanged):** test work targets `quantcore-test-20260606`; the prod DB in
`.env`'s `QUANTCORE_DB_DSN` is never touched by this work (the UI only issues normal API calls). The
user runs all GCP/IAM/console commands and commits/pushes git themselves; secrets (the API token, the
JWT signing secret) are never echoed into the transcript. `frontend/.env.local` is gitignored — never
commit it.

---

## Target runtime architecture

```text
                         ┌─────────── Google IAP (perimeter) ───────────┐
Dev team ──HTTPS──►  *.run.app   │  verifies Google login, injects        │
(browser)                        │  x-goog-authenticated-user-email/-id   │
                                 └───────────────────┬────────────────────┘
                                                     ▼
                                   Cloud Run service  quantui  (Node/Express)
                                     ├─ GET /*       → static dist/  (SPA, index.html fallback)
                                     └─ /api/*        → reverse-proxy to quantcore-api
                                                         + Authorization: Bearer <secret>   (server-side)
                                                                     │
                                                                     ▼
                                                     quantcore-api (JWT-enforced) ──► services ──► Cloud SQL
```

- IAP gates **who can load the UI**. The Express layer holds the **service JWT** (from Secret
  Manager) and attaches it to proxied `/api/*` calls, so the browser bundle never contains a token
  and there is no CORS surface. This is the production equivalent of `vite.config.ts`'s dev proxy.
- Cloud Run ingress stays **public** with `--no-allow-unauthenticated --iap`: IAP (not Cloud Run
  IAM) is the enforcement layer for human access; the container itself runs no auth.

---

## New / changed files

```text
frontend/server/server.mjs        # NEW Express server: express.static(dist) + SPA fallback +
                                  #   http-proxy-middleware for /api/* → QUANTCORE_REST_URL, injecting
                                  #   Authorization: Bearer ${QUANTCORE_API_TOKEN}; listens on $PORT
frontend/server/package.json      # NEW server-only deps (express, http-proxy-middleware) + lockfile,
                                  #   kept separate from the SPA's package.json so the build stays lean
frontend/.dockerignore            # NEW (UI build context is frontend/) — excludes node_modules, dist,
                                  #   .env*, build artifacts so secrets are never baked
Dockerfile.ui                     # NEW multi-stage (context ./frontend): node build → dist/, then a
                                  #   slim node runtime with the server + dist; non-root; $PORT
cloudbuild.yaml                   # ADD a build-ui step + push quantcore-ui:${_TAG}/:latest
.github/workflows/deploy.yml      # ADD a "Deploy quantui" step (image-only, after the api/wrappers)
docker-compose.yml                # ADD an optional `quantui` service for local stack parity
CLAUDE.md / readme.md             # document the UI service + IAP access + local container run
```

The React app under `frontend/src/**` is **unchanged** — `frontend/src/api/client.ts` already calls
`/api/*` with `API_BASE` defaulting to `''`, so a build with **no `VITE_*` env** is same-origin and
token-less; the Express server supplies the token at runtime. `VITE_API_TOKEN` is therefore **no
longer baked** into the bundle.

---

## Step-by-step plan (one commit per step; user commits/pushes + checkpoint-logged)

### Part A — Build the serving container (local, no GCP)

- **Step 1 — Express serve+proxy + Dockerfile.**
  - `frontend/server/server.mjs`: `express.static(dist)`; `/healthz` liveness; SPA `index.html`
    fallback; `createProxyMiddleware('/api', { target: QUANTCORE_REST_URL, changeOrigin: true,
    onProxyReq })` adding the bearer only when `QUANTCORE_API_TOKEN` is set (local AUTH_DISABLED → no
    token). Listens on `process.env.PORT`.
  - `frontend/server/package.json` (+ `package-lock.json`): `express`, `http-proxy-middleware` only.
  - `Dockerfile.ui` (build context `./frontend`, mirrors `Dockerfile.api`'s multi-stage + non-root +
    shell-form `${PORT}`): builder `node:22-slim` → `npm ci && npm run build` → `dist/`; runtime
    `node:22-slim` → `npm ci --omit=dev` server deps, copy `dist/` + `server/`, non-root,
    `CMD node server/server.mjs`.
  - `frontend/.dockerignore` keeps `node_modules`/`dist`/`.env*` out of the image.
  - **Verify locally:** `docker build -f Dockerfile.ui -t quantui:dev ./frontend`, then run with
    `-e QUANTCORE_REST_URL=http://host.docker.internal:5001 -e PORT=8080 -p 8080:8080` against a local
    `uvicorn api.main:app` (AUTH_DISABLED) → UI loads at `:8080`, `/api/health` + a data grid populate
    through the proxy.

- **Step 2 — (optional) compose parity.** Add a `quantui` service to `docker-compose.yml`
  (`build: { context: ./frontend, dockerfile: ../Dockerfile.ui }` or root-relative equivalent,
  `QUANTCORE_REST_URL=http://quantcore-api:5001`, no token since the compose api is `AUTH_DISABLED=1`,
  `ports: ["8080:8080"]`, `depends_on: quantcore-api healthy`). Lets the team run the whole stack
  incl. UI via `./runUI-CONTAINERS.sh up --build`.

### Part B — Wire into CI image build

- **Step 3 — cloudbuild + deploy.yml.** Add a `build-ui` step to `cloudbuild.yaml` (build
  `Dockerfile.ui` with context `frontend`, tag `quantcore-ui:${_TAG}` + `:latest`, add to `images:`).
  Add a **Deploy quantui** step to `deploy.yml` (image-only `gcloud run deploy quantui --image
  …:${GITHUB_SHA::7}`, after the api/wrapper/report steps). Like the api, **CI carries only the image
  tag** — the `--iap`, `--no-allow-unauthenticated`, secret binding, and `QUANTCORE_REST_URL` are set
  once at first deploy (Step 5) and preserved across image-only redeploys.

### Part C — First deploy + IAP (TEST project; user runs gcloud/console)

- **Step 4 — Secret (test).** Create a Secret Manager secret holding the **service JWT** the proxy
  presents to the test api — a long-lived HS256 token signed with the test `QUANTCORE_JWT_SECRET` (a
  valid one already exists locally in `frontend/.env.local`; reuse its value or mint a fresh one).
  Store as `quantui-api-token` (test project); grant the quantui runtime SA
  `roles/secretmanager.secretAccessor` on it.
- **Step 5 — First Cloud Run deploy with IAP (test).**
  ```bash
  gcloud run deploy quantui --project quantcore-test-20260606 --region us-central1 \
    --image us-central1-docker.pkg.dev/quantcore-test-20260606/quantcore/quantcore-ui:<tag> \
    --no-allow-unauthenticated --iap \
    --set-env-vars QUANTCORE_REST_URL=https://<test-quantcore-api-url> \
    --set-secrets QUANTCORE_API_TOKEN=quantui-api-token:latest
  ```
- **Step 6 — OAuth consent + IAP IAM (test, one-time).**
  1. Cloud Run → quantui → **Security** → **Configure in IAP** → set up the **OAuth consent screen**,
     user type **External** (team uses `@gmail.com`).
  2. Grant the IAP service agent invoke rights on the private service:
     ```bash
     gcloud run services add-iam-policy-binding quantui --region us-central1 \
       --project quantcore-test-20260606 \
       --member=serviceAccount:service-493357101423@gcp-sa-iap.iam.gserviceaccount.com \
       --role=roles/run.invoker
     ```
     (test project number = `493357101423`.)
  3. Authorize each developer (verify the exact `--resource-type` for direct Cloud Run IAP via
     `gcloud iap web add-iam-policy-binding --help` — the brief's `web-types` is likely outdated):
     ```bash
     gcloud iap web add-iam-policy-binding --resource-type=<cloud-run|backend-services> \
       --service=quantui --region=us-central1 --project=quantcore-test-20260606 \
       --member='user:<dev>@gmail.com' --role='roles/iap.httpsResourceAccessor'
     ```
- **Step 7 — Verify (test).** Open the quantui `*.run.app` URL in an incognito window → Google login
  prompt (IAP); an unauthorized account is blocked; an authorized dev account loads the SPA; data
  grids populate (proxy → test api → test DB). Confirm via DevTools that **no JWT appears** in any
  client-side bundle or browser-issued request (the bearer is added only on the server hop).

### Part D — Promote to PROD

- **Step 8 — Prod promotion.** Repeat Steps 4–7 in `quantcore-prod-20260606` (project #
  `127961694257`): a `quantui-api-token` secret signed with the **prod** `QUANTCORE_JWT_SECRET`,
  `QUANTCORE_REST_URL=https://quantcore-api-127961694257.us-central1.run.app`, IAP + OAuth consent +
  team `iap.httpsResourceAccessor` grants, image promoted **by digest** test→prod (consistent with the
  existing api/mcp/report promotion). Prod deploy goes through the supervised `prod-rollout.yml`
  (`workflow_dispatch`, `prod` environment, required reviewers) — not the test auto-deploy.

---

## Verification

- **Container (Step 1):** `docker build -f Dockerfile.ui ./frontend` succeeds; the running container
  serves the SPA and round-trips `/api/*` through the proxy against a local `uvicorn api.main:app`.
- **CI (Step 3):** `deploy.yml` `gate` stays green; on push to main the `build-ui` image builds and the
  `Deploy quantui` step rolls out (image-only; IAP/secret settings preserved).
- **IAP (Steps 6–7):** incognito visit forces Google login; unauthorized account → 403/blocked;
  authorized dev → SPA loads and data populates end-to-end (`browser → IAP → quantui → quantcore-api →
  service → Cloud SQL`).
- **No-token-in-browser (Step 7):** DevTools Network/Sources show the bearer only on the server hop,
  never in the delivered bundle or browser-issued requests.
- **Prod (Step 8):** same checks against the prod project; image matches the test digest.

## Security notes & deferred hardening

- **Shared service token (baseline).** The proxy presents one long-lived service JWT for all UI users;
  IAP is what restricts access to the team. Adequate for an internal dev-team tool.
- **Deferred — per-user identity for audit (Rule 5).** IAP injects
  `x-goog-authenticated-user-email`. A natural follow-up: have the Express proxy **mint a short-lived
  per-request JWT** with `sub=<that email>` (signed with `QUANTCORE_JWT_SECRET`) instead of the shared
  token, so `api/auth.py`'s `Principal.owner` attributes work to the real user and the portfolio
  `?owner=` partition follows the logged-in identity. Noted, not in this plan's baseline.
- **Token rotation.** Because the token lives in Secret Manager (`:latest`), rotation is a secret
  update + image-less redeploy — no rebuild.

## Risks & mitigations

- **Direct Cloud Run IAP flag/resource-type drift** (the feature is new; the brief's `web-types` looks
  off) → verify `--iap` and the `gcloud iap web add-iam-policy-binding` resource-type against current
  `gcloud --help` before running; fall back to console wiring (Cloud Run → Security tab) if a CLI flag
  is unavailable.
- **CI redeploy dropping IAP/secret settings** → mirror the api pattern (image-only redeploys preserve
  service config); confirm after the first CI deploy that `--iap` and the secret binding survive.
- **CORS creeping in** → avoided by design: the SPA stays same-origin (`/api/*` via the proxy); never
  point the browser directly at the api domain.
- **Wrong-project token** → test and prod use separate `quantui-api-token` secrets signed with each
  project's own `QUANTCORE_JWT_SECRET`; promotion copies the image by digest, not the secret.
