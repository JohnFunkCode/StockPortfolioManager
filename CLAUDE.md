# CLAUDE.md
claude --resume 44dcf10f-5cc7-494e-90b2-1e4d0bc4a672

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the application (generates HTML report + sends Discord notifications)
python main.py

# Run all tests (suites live under tests/; the tests/__init__.py package
# initializer swaps in the test DSN before quantcore.db is imported)
python -m unittest discover -s tests -t .

# Backend tests with coverage (CI enforces a ratchet floor — see .coveragerc + deploy.yml gate)
coverage run -m unittest discover -s tests -t . && coverage report

# Frontend tests with coverage (thresholds in frontend/vitest.config.ts)
cd frontend && npx vitest run --coverage

# Run a single test module (dotted path from the repo root)
python -m unittest tests.test_money
python -m unittest tests.test_stock_portfolio_manager

# Activate virtualenv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Architecture

This is a Python stock portfolio tracker that fetches live prices from Yahoo Finance, generates an HTML report (with charts), optionally uploads to S3, and sends Discord notifications when price thresholds are breached.

### Core Domain (`portfolio/`)

- **`money.py`** — `Money` value object using `Decimal` for precision. Supports arithmetic operators and currency conversion via the open.er-api.com exchange rate API.
- **`stock.py`** — `Stock` entity holding purchase info, current price, and a `Metrics` object. Computes gain/loss, gain/loss %, and dollars-per-day.
- **`portfolio.py`** — `Portfolio` aggregates `Stock` objects (keyed by symbol). Reads holdings from `portfolio.csv`. Delegates price updates and metrics to gateway/metrics modules.
- **`watch_list.py`** — `WatchList` is similar to Portfolio but for non-owned stocks. Reads from `watchlist.yaml` (supports per-stock `tags`).
- **`metrics.py`** — `Metrics` dataclass plus `get_historical_metrics()` which bulk-downloads 2 years of daily data via yfinance and computes moving averages (10/30/50/100/200-day), period returns, and percent change today.
- **`yfinance_gateway.py`** — Thin wrapper around `yf.download()` for latest prices and `yf.Tickers()` for descriptive info (earnings dates, income statements).

### Report Generation (`main.py`)

`main.py` is the entry point. It loads portfolio from CSV + watchlist from YAML, fetches prices/metrics, generates matplotlib charts (embedded as base64 in HTML via Jinja2 template), optionally uploads to S3, and triggers notifications. It also captures a full options chain snapshot per portfolio/watchlist symbol (in-process `OptionsService.get_full_options_chain`, capped expirations, per-symbol try/except) so open-interest history accumulates daily for `get_oi_change_analysis`.

### Notifications (`notifier.py`)

Sends Discord webhook alerts for: moving average violations (30/50/100/200-day), price below purchase price, and Harvester plan rung hits. Uses `notification.log` file to deduplicate alerts within a run.

### Harvester System

An experimental "harvest ladder" strategy for systematically selling shares as prices rise:

- **`experiments/HarvesterExperiment.py`** — Core algorithm: computes volatility-based harvest thresholds (H), builds forward price target ladders, and backtests harvest plans. (`experiments/INTC_bear_call_spread_monitor.py` and `WMT_bull_call_spread_monitor.py` are standalone position monitors kept alongside it.)
- **`quantcore/repositories/harvester_repository.py`** — `HarvesterPlanDB` + `PlanBuildParams` persist plans in the unified **QuantCore** PostgreSQL database (plan templates/instances/rungs/alerts). SQL only.
- **`quantcore/services/harvester.py`** — `HarvesterService` wraps the repository and scans prices against active plan rungs, firing alerts (the former `HarvesterController` behaviour).

The Harvester integrates with the notification system: when `main.py` runs, it checks each portfolio stock against active harvest plan rungs (via `HarvesterService`) and sends Discord alerts for any hits.

### Unified Database (`quantcore/`)

All persistence is consolidated into a single **QuantCore** PostgreSQL database, accessed via `psycopg2`:

- **`quantcore/db.py`** — Shared connection factory (`get_connection()`) backed by `psycopg2`, connecting via the `QUANTCORE_DB_DSN` environment variable. Centralized schema DDL for all 17 tables (`init_schema()`), using `SERIAL` primary keys and `ON CONFLICT` upserts. Imported as `from quantcore.db import get_connection`.
- **Schema** includes: symbols, OHLCV (merged from daily + intraday intervals), fetch_log, plan_templates/instances/rungs/alerts (Harvester), options_snapshots/expirations/contracts/gamma_wall_history/gex_history/options_positions, news_articles, sentiment_snapshots, fundamentals_history.

All repositories under `quantcore/repositories/` and the REST API (`api/main.py`) use the shared factory instead of managing individual database connections.

**Migrating from a legacy SQLite database:** `scripts/migrate_sqlite_to_postgres.py` performs a one-shot copy of an existing `quantcore.sqlite` file into PostgreSQL — it initializes the schema, migrates all 16 tables in FK-safe order via batched `execute_values()` inserts, resets `SERIAL` sequences, and verifies row counts. Run it with `--sqlite <path>` and `--dsn <postgresql-uri>`.

### Services Layer (`quantcore/`)

Per [`docs/proposals/architectural-standard-v2.md`](docs/proposals/architectural-standard-v2.md), all business logic lives in an object-oriented services layer; the MCP tool bodies (`fastMCPTest/*_server.py`, `options_analysis.py`) and FastAPI routes (`api/routers/*`, app assembled in `api/main.py`) are thin adapters that are **exactly one service call deep**.

- **`quantcore/gateways/`** — external-IO wrappers: `YFinanceGateway` (yfinance), `PolygonGateway` (Polygon HTTP/pagination). These are the *only* place outside `portfolio/` (the legacy domain layer, retained for `main.py`'s report path) and the standalone `experiments/` monitors that imports `yfinance`.
- **`quantcore/repositories/`** — SQL-only persistence, no analytics: `OhlcvRepository`, `OptionsStore`, `OptionsPositionStore`, `NewsStore`, `SentimentStore`, `FundamentalsRepository`, `HarvesterPlanDB`, `PortfolioRepository`.
- **`quantcore/analytics/`** — pure functions (DataFrame/dict in, value out), no I/O: `indicators.py` (RSI/MACD, Wilder ATR, anchored VWAP, swing detection), `volume_profile.py` (volume-at-price histogram: POC, value area, HVN/LVN nodes), `options_math.py` (Black–Scholes delta/gamma/vega/vanna/charm, max-pain, expected-move — single home, deduped).
- **`quantcore/services/`** — the business logic: `PricesService`, `OptionsService`, `OptionsScreeningService`, `FundamentalsService`, `SentimentService`, `MicrostructureService`, `HarvesterService`, `PortfolioService`, `RecommendationsService` (composes the other services).
- **`quantcore/services/registry.py`** — the composition root: a lazy `@lru_cache get_services()` returning a frozen `Services` dataclass with all dependencies constructor-injected. Adapters call `get_services().<service>.<method>(...)`; service modules never import each other or the registry (acyclic).

**UI component rules (arch-v2 Rules 8–9):** any front-end component that displays analytical data must be **GenUI-compliant / sidekick-renderable** — scalar self-contained props, registered with matching strict prop specs in BOTH `quantcore/services/chat_tools.py` (`BACKEND_COMPONENT_REGISTRY` + the `show_component` tool description) and `frontend/src/chat/componentRegistry.tsx`, rendered via `DirectiveRenderer`, displayed math in `quantcore/analytics` (never in the front end), gestures only via the dual interaction registries + `useDirectiveInteractions` (honoring locked/consumed history). Every new or materially changed UI component ships vitest tests (loading/error/success + key values) and registry parity cases in the same PR; the vitest coverage thresholds only ratchet upward.

Positions are DB-backed with multi-owner support (`positions` table, `owner` column); `portfolio.csv` is a per-owner import format (`scripts/import_portfolio.py --csv portfolio.csv --owner john`, full-sync replace). The REST `GET/POST/DELETE /api/portfolio*` routes take an `?owner=` param defaulting to `john`; `main.py`'s report/notifications stay on John's portfolio.

**Refactor status:** Phase 1 of architectural-standard-v2 (services-layer extraction) is **complete** — see [`docs/proposals/phase1-migration-plan.md`](docs/proposals/phase1-migration-plan.md) for the checkpoint log. Phase 2 (FastAPI/Pydantic REST tier) is **complete** — the Flask app (`api/app.py`) has been retired and rebuilt on FastAPI (app factory `api/main.py`, route groups under `api/routers/*`, Pydantic request/response schemas under `api/schemas/*`), preserving every route path and JSON shape so the React front end runs unmodified; OpenAPI docs are served at `/docs` and the spec at `/openapi.json`. See [`docs/proposals/phase2-fastapi-plan.md`](docs/proposals/phase2-fastapi-plan.md) for the checkpoint log. Run it with `uvicorn api.main:app --host 127.0.0.1 --port 5001` (or `python -m api.main`). Phase 3 (AI gateway + GCP deployment) is **complete on the test project** — see [`docs/proposals/phase3-gateway-plan.md`](docs/proposals/phase3-gateway-plan.md): the five MCP servers (`fastMCPTest/*_server.py`, `options_analysis.py`) were inverted into thin **HTTP gateway wrappers** that call the REST tier through the single seam `mcp_gateway/rest_client.py` (Rule 6 — `AI Agent → MCP wrapper → REST tier → Service`); `api/auth.py` adds JWT verification (inert until a key is configured, so local/compose stay open); everything is containerized (`Dockerfile.{api,mcp,report}`, `docker-compose.yml` local stack) and deployed to **GCP Cloud Run** — `quantcore-api` (JWT-enforced) + 5 wrapper services + `main.py` as a daily Cloud Run **Job** on Cloud Scheduler (in-process services, never HTTP). CI/CD is `.github/workflows/deploy.yml` (tests + wrapper smoke + OpenAPI surface diff, then build/roll-out; push/PR triggers are gated by a `preflight` job that skips the deploy when the test-WIF secrets are absent — wire them with `scripts/setup_test_wif.sh`). **Production rollout is COMPLETE** — see [`docs/proposals/prod-rollout-plan.md`](docs/proposals/prod-rollout-plan.md): rather than a test-service DSN flip, the same stack was stood up in a **dedicated prod project** `quantcore-prod-20260606` (project # `127961694257`, `us-central1`) reaching its own prod Cloud SQL — `quantcore-api` (JWT-enforced) + 5 wrapper services + the report Cloud Run Job on Cloud Scheduler, on images **copied by digest** test→prod (api `ac5cd17f…`, mcp `1b7da905…`, report `65d70659…`). Gated prod CI/CD is `.github/workflows/prod-rollout.yml` (`workflow_dispatch`/`release`, `prod` GitHub Environment with required reviewers, separate prod WIF). `.mcp.json` points AI clients at the prod wrapper `/mcp` URLs (`https://quantcore-<svc>-127961694257.us-central1.run.app`, bearer `${QUANTCORE_MCP_TOKEN}`); the 5 `*-local` entries remain for the docker-compose stack. The deferred `portfolio/yfinance_gateway.py` `get_latest_prices` fragility is now **hardened** (retry/back-off + graceful all-None degrade so a flaky Yahoo response no longer crashes the daily report); rebuilding/redeploying the prod report image with this fix is a pending user/CI step.

### QuantUI front end on Cloud Run (behind IAP)

The React SPA (`frontend/`) is deployed as the **QuantUI** Cloud Run service in both projects,
gated by **Identity-Aware Proxy (IAP)** so the team reaches the real UI from anywhere with no auth
code in the app — see [`docs/proposals/quantui-iap-plan.md`](docs/proposals/quantui-iap-plan.md)
(status: **COMPLETE, Steps 1–8**). Live URLs:

- **Test:** `https://quantui-uikpdb55ea-uc.a.run.app` (`quantcore-test-20260606`)
- **Prod:** `https://quantui-swgixldxzq-uc.a.run.app` (`quantcore-prod-20260606`)

The security detail page's Technical Analysis tab includes the **Support Confluence card**
(`frontend/src/components/securities/SupportConfluenceCard.tsx`, issue #93 Phase 7), rendering the
`GET /api/securities/{ticker}/support-confluence` composite support/resistance zones.

**Serving model:** `Dockerfile.ui` builds `frontend/dist/` and runs a tiny Express server
(`frontend/server/server.mjs`) that serves the static bundle (SPA fallback, plus CSP + Trusted
Types headers) and **reverse-proxies `/api/*` to `quantcore-api`, attaching a per-user token
server-side**: it verifies the Google-signed IAP assertion (`x-goog-iap-jwt-assertion`) and mints
a 15-min **ES256 JWT** (`sub` = the IAP email, `aud: ['quantcore-api','quantcore-keyproxy']`) in
`frontend/server/auth.mjs`, signed with the `quantui-signing-key` secret (public half in
`quantui-signing-pub`, given to the verifiers). Fallback ladder keyed on configuration:
`QUANTUI_SIGNING_KEY` set → per-user mint (missing/invalid IAP assertion = hard 401); else
`QUANTCORE_API_TOKEN` (legacy static `quantui-api-token` secret) → else no header (compose,
`AUTH_DISABLED=1`). The browser stays same-origin (no CORS) and never sees any bearer — the
production equivalent of the Vite dev proxy. IAP gates *who can load the UI*; the minted JWT
authenticates the UI→API hop and carries user identity to the BYOK keyproxy. Each project has its
own signing keypair + OAuth client (standalone projects can't auto-provision one; attach via
`scripts/attach_quantui_iap_oauth.sh`).

**Deploy workflow for a UI change:** edit `frontend/` → PR → merge to `main`. `deploy.yml` (no path
filters) builds `quantcore-ui` (`build-ui` step in `cloudbuild.yaml`) and image-only-rolls it onto
the **test** `quantui` service automatically (IAP/secret/env config preserved). Verify on the test
URL, then promote to **prod** by manually dispatching `prod-rollout.yml` (`workflow_dispatch`) with
the commit's 7-char SHA — it copies the image **by digest** test→prod and image-only-deploys prod
`quantui`. Prod is never auto-deployed.

**Granting a new user:** while the OAuth consent screen is in "Testing", an account must be on BOTH
(1) the consent screen **Audience** test-user list and (2) hold `roles/iap.httpsResourceAccessor`
on `quantui`. Add the email to the `USERS=( … )` array in `scripts/grant_quantui_iap_access.sh` and
run it per project (`./scripts/grant_quantui_iap_access.sh` for test;
`./scripts/grant_quantui_iap_access.sh quantcore-prod-20260606` for prod), plus add them to the
Audience tab in Console. Both are required — only one results in a blocked login.

### BYOK key proxy (Sidekick chat — users bring their own Anthropic key)

**Status: COMPLETE — live on test and prod since 2026-07-18** (GitHub issue #100; plan +
checkpoint/runbook log in [`docs/proposals/byok-key-proxy-plan.md`](docs/proposals/byok-key-proxy-plan.md),
merged via PRs #105/#106 at `177e411`). The QuantUI Sidekick chat runs on each user's own
Anthropic API key; the backend never holds a usable key at rest.

- **Flow:** browser vault (`frontend/src/vault/` — IndexedDB, passphrase PBKDF2 + AES-GCM;
  managed on the `/settings` page) seals the key per turn into a **single-use envelope**
  (`frontend/src/vault/envelope.ts` ↔ `keyproxy/crypto.py`, SPKI pin baked into the UI bundle,
  AAD binds `sub`/`jti`/scope-hash) → `/api/chat` carries envelope + scope through
  `quantcore-api` (never decrypted there) → **`keyproxy/`** (own FastAPI service, no DB) decrypts
  in memory, enforces scopes/budgets/replay (`scopes.py`, `sessions.py`, `replay.py`), streams
  SSE from Anthropic back through the chain.
- **Never-log policy (enforced by tests):** no API keys, `Authorization` headers, envelopes,
  decrypted payloads, request bodies, or exception dumps containing credentials may reach any log
  or print. Any new failure path must add the corresponding log assertion.
- **Auth layers:** keyproxy is **IAM-locked on Cloud Run** (`--no-allow-unauthenticated`;
  `run.invoker` only for `quantcore-run@`; the api attaches a Google ID token in
  `X-Serverless-Authorization`) and runs as dedicated SA `keyproxy-runtime@` (zero project roles,
  per-secret grants only). App level: keyproxy verifies **ES256-only** user JWTs (audience
  `quantcore-keyproxy`); `api/auth.py` is **dual-mode** (ES256 per-user UI tokens via
  `QUANTCORE_JWT_PUBLIC_KEY` + legacy HS256 service/MCP tokens via `QUANTCORE_JWT_SECRET`).
- **Deploy wiring:** `Dockerfile.keyproxy`; compose service `keyproxy:5002` (ephemeral or
  persistent dev keypair via `runUI-CONTAINERS.sh`); `cloudbuild.yaml` `build-keyproxy`;
  `deploy.yml` image-only-deploys test `quantcore-keyproxy` (skips if the service doesn't exist);
  `prod-rollout.yml` promotes/deploys it by digest the same way. First deploy in each project is
  the manual packet-8b runbook (secrets `keyproxy-private-key`, `quantui-signing-key`/`-pub`;
  private keys are piped straight into Secret Manager, never printed). Gitleaks secret-scanning
  job runs in CI (`.gitleaks.toml`).
- **Gotchas learned on the prod rollout (details in the plan doc):** on existing Cloud Run
  services always `--update-secrets`/`--update-env-vars` (`--set-*` replaces the whole set);
  "inert" env-var claims must be checked against the image actually running (the pre-BYOK
  `api/auth.py` used `QUANTCORE_JWT_PUBLIC_KEY` as an HMAC secret and broke all HS256 tokens);
  the CI deployer needs `roles/iam.serviceAccountUser` on `keyproxy-runtime@` (granted in both
  projects).

### Environments (prod is the system of record)

**Prod (`quantcore-prod-20260606`) is the system of record for all analysis for all users; test
(`quantcore-test-20260606`) is for development and CI only.** This supersedes the earlier "do
analysis on test, treat prod as read-only" operating rule — now that changes ship through CI/CD
(`deploy.yml` → test, `prod-rollout.yml` → prod), prod is the live system everyone reads from. The
deployed `quantui` UI and the `.mcp.json` AI-client remotes both already target prod.

The 5 remote MCP servers in `.mcp.json` send `Authorization: Bearer ${QUANTCORE_MCP_TOKEN}`, which
the wrappers forward unchanged to `quantcore-api` (identity passthrough → the legacy HS256
service-token path in the now dual-mode `api/auth.py`). So real analysis requires `QUANTCORE_MCP_TOKEN` to be a valid prod JWT in the
environment Claude Code launches from; if it's unset, every data tool returns `401: … Not enough
segments` (the wrapper-local `mcp_health_check` still passes, which is misleading). Each user mints
their own 3-month token with `scripts/mint_prod_jwt.py --output export --expires-hours 2160 --sub
<you>` (see readme "Connecting AI clients to prod"). **When onboarding a user, remind them the token
expires after 90 days and recommend quarterly rotation** (and a per-user `--sub`).

## Configuration

- **`.env`** — `QUANTCORE_DB_DSN` is the PostgreSQL connection string for the unified database (e.g. `postgresql://<user>:<password>@<host>:<port>/<database>`); `QUANTCORE_TEST_DB_DSN` optionally points the same code at an isolated database for testing; `DISCORD_WEBHOOK_URL` for notifications; `BUCKET_NAME`/`BUCKET_KEY` for optional S3 upload.
- **`portfolio.csv`** — Holdings data: `name,symbol,purchase_price,quantity,purchase_date,currency,sale_price,sale_date,current_price`
- **`watchlist.yaml`** — Watchlist entries with `name`, `symbol`, `currency`, and optional `tags` list.

**Database Initialization:** The unified PostgreSQL database and its 17-table schema are automatically created on startup by any application entry point (`main.py`, REST API, or MCP servers) — `init_schema()` runs before any database operations. The database itself (and its `quantcore` user) must already exist; point `QUANTCORE_DB_DSN` at any reachable PostgreSQL instance — local, or a managed service such as Cloud SQL accessed through the Cloud SQL Auth Proxy (which exposes the remote instance as a local TCP host:port, so no code changes are needed to switch targets).

## Key Dependencies

pandas, yfinance, matplotlib, jinja2, python-dotenv, boto3, PyYAML, requests, psycopg2
