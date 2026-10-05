# CLAUDE.md
claude --resume 44dcf10f-5cc7-494e-90b2-1e4d0bc4a672

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Documentation is part of the change (read this first)

**Any change that alters what a reader of the docs would be told must update the docs in the same
PR.** Docs are not a follow-up task and not a separate ticket — a PR that leaves them stale is
incomplete, and reviewers should say so.

Update the docs when you:

- add, remove, or rename a **service, repository, gateway, or analytics module**
- add or change a **REST route group**, an **MCP server or tool**, or a **UI page or route**
- change the **schema** (which means **three** files — a Flyway migration, `_SCHEMA` in
  `quantcore/db.py`, and `db/schema_snapshot.json`; enforced by CI, see Migrations)
- change **deployment, CI/CD, environment, or auth** wiring
- add an **operational script**, or change how an existing one is invoked
- change a **default, environment variable, or configuration file's role**
- discover that something documented is **already wrong** — fix it while you're there

Where it goes:

| Doc | Audience | Holds |
|-----|----------|-------|
| `CLAUDE.md` | agents (auto-loaded every session) | architecture, constraints, and the rules an agent must not violate |
| `AGENTS.md` | non-Claude agents | a pointer to `CLAUDE.md` plus the non-negotiables — **never a second copy of the architecture** |
| `readme.md` | humans | the tour: install, configure, run, endpoints, UI, containers, MCP setup |
| `docs/architecture/*.md` | both | the detail and the *why* behind CLAUDE.md's rules — CLAUDE.md keeps the rule and links here |
| `docs/proposals/*.md` | both | plans and their checkpoint logs — append the checkpoint as each step lands, not at the end |

Two rules that keep this from rotting:

1. **One fact, one home.** If two documents would both state something, one states it and the
   other links. `AGENTS.md` drifted for months precisely because it was a copy.
2. **Record the gotcha, not just the outcome.** When something cost real time to figure out — a
   command that half-succeeded, a flag that behaved differently than documented — write down what
   misled you, in the plan doc for that work. That is the part nobody can reconstruct later.

Prefer a smaller true statement to a larger stale one: if you can't verify a claim, cut it or
mark it, rather than leaving a confident sentence that no longer holds.

## Commands

```bash
# Run the application (generates HTML report + sends Discord notifications)
python main.py

# Run all tests (suites live under tests/; the tests/__init__.py package
# initializer swaps in the test DSN before quantcore.db is imported, and makes
# any real yfinance request fail fast -- tests never reach Yahoo; stub
# YFinanceGateway instead). The DSN comes from .env: QUANTCORE_UNITTEST_DB_DSN
# (local Postgres, ~1 min) if set, else QUANTCORE_TEST_DB_DSN (Cloud SQL test
# via the proxy, ~17 min). QUANTCORE_UNITTEST_DB=cloudsql forces the latter.
# It also takes an advisory lock on that database for the whole run: a second
# run on the same database waits (and says so) rather than purging this one's
# synthetic rows (#248) -- don't remove it to "speed up" parallel runs.
# --durations lists the slowest tests (CI does the same).
python -m unittest discover -s tests -t . --durations 25

# Backend tests with coverage (CI enforces a ratchet floor — see .coveragerc + deploy.yml gate)
coverage run -m unittest discover -s tests -t . --durations 25 && coverage report

# Frontend tests with coverage (thresholds in frontend/vitest.config.ts)
cd frontend && npx vitest run --coverage

# Run a single test module (dotted path from the repo root)
python -m unittest tests.test_money
python -m unittest tests.test_stock_portfolio_manager

# Start the REST API
uvicorn api.main:app --host 127.0.0.1 --port 5001

# Cloud SQL Auth Proxy (prod = :5433 by default, test = :5434); targets come from .env
./runProxy-MAC.sh
./runProxy-MAC.sh --test

# Run a one-off against the TEST database (swaps QUANTCORE_TEST_DB_DSN in for the child only).
# The test suite needs no wrapper — tests/__init__.py already does the same swap.
./scripts/with-test-db.sh python scripts/check_schema_snapshot.py

# Database migrations. CI applies them before each roll-out (#200); by hand only for
# refused contract/non-transactional ones. Defaults to TEST; prompts before a prod migrate.
./scripts/flyway.sh info
./scripts/flyway.sh --prod info

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
- **`portfolio.py`** — `Portfolio` aggregates `Stock` objects (keyed by symbol). `read_stocks_from_records()` loads holdings from the DB-backed `positions` table; `read_stocks_from_csv()` remains for the `portfolio.csv` import path. Delegates price updates and metrics to gateway/metrics modules.
- **`watch_list.py`** — `WatchList` is similar to Portfolio but for non-owned stocks. `read_stocks_from_records()` loads it from the DB-backed `watchlist` table (issue #83); `read_stocks_from_yaml()` remains for the `watchlist.yaml` import path. Supports per-stock `tags`.
- **`metrics.py`** — `Metrics` dataclass plus `get_historical_metrics()` which bulk-downloads 2 years of daily data via yfinance and computes moving averages (10/30/50/100/200-day), period returns, and percent change today.
- **`yfinance_gateway.py`** — Thin wrapper around `yf.download()` for latest prices and `yf.Tickers()` for descriptive info (earnings dates, income statements).

### The daily job (`main.py`) and the legacy report script

`main.py` is the daily Cloud Run Job (`quantcore-report`). It reads John's positions and the
shared watchlist from the database (never from `portfolio.csv` or `watchlist.yaml`) and runs, **in
this order** — notifications → options capture → fundamentals warming → gamma/GEX recording →
capture health check. The order is a deliberate isolation property: the cheap, high-value side
effects land before anything that can run long. Rules to keep:

- The warming pass and the capture tail are each wrapped in an outer `try/except` that **never
  raises**, so a late failure cannot fail a run whose notifications already went out — and each is
  made loud by a Discord alarm that re-reads what actually **landed in the database**. Keep both
  halves.
- Every step budget is clamped to what is left of the task deadline (`_remaining_budget`), so
  budgets can't add up past the timeout.
- Tuning env vars (`REPORT_TASK_TIMEOUT_SECONDS`, `FUNDAMENTALS_*`, `OPTIONS_CAPTURE_*`,
  `GEX_RECORD_BUDGET_SECONDS`, `NEWS_*`) fall back to the default on an unparseable value — a typo
  must not silently disarm an alarm.
- The job exits 0 on NYSE-closed days (`is_trading_day`), notifications included.
- The fundamentals batch endpoint takes at most 25 unique symbols (422 otherwise); larger universes
  must be split across requests.
- News collection is a **separate Job** (`news_job.py`, `Dockerfile.news`) because FinBERT needs
  torch, which the lean report image must not carry.
- **FinBERT weights are baked into the images, never downloaded at runtime.** Bumping the model is
  a reviewed one-line change to `DEFAULT_REVISION` in `scripts/bake_finbert.py`, never a follow of
  Hub `main`. Don't "simplify" away the bake's `use_safetensors=False`.
- `main.py` **does not render the HTML report** — `scripts/generate_portfolio_report.py` does, and
  the Pi runs that script and **not** `main.py` (running both would double every alert).

Defaults table, every alarm, the news Job, the FinBERT load path, and the report script:
[`docs/architecture/daily-jobs.md`](docs/architecture/daily-jobs.md).

### Notifications (`notifier.py`)

Sends Discord webhook alerts for: moving average violations (30/50/100/200-day), price below purchase price, and Harvester plan rung hits. Uses `notification.log` file to deduplicate alerts within a run.

### Arbitrage Scanner

Finds securities stretched against a structurally linked underlying (`nav_vehicle`,
`commodity_etf`, `producer`; curated links in `arb_universe.yaml`), served by `ArbitrageService`,
`GET /api/arbitrage/*`, and the `arbitrage-server` MCP wrapper. **The scoring is deliberately
inverted: spread width only qualifies a candidate, the convergence mechanism ranks it** — every
factor and penalty emits its own `reasons`/`breaks_on` entry, so keep the scorer self-documenting.
Futures-only hedges are flagged `hedge_available: false` and halved (the account is equity/ETF-only).
Expect most scans to return nothing above `watch` — that is intended, not a bug.

Design: [`docs/architecture/arbitrage.md`](docs/architecture/arbitrage.md); usage:
[`docs/arbitrage-scanner-usage.md`](docs/arbitrage-scanner-usage.md).

### Harvester System

An experimental "harvest ladder" strategy for selling shares as prices rise: the algorithm in
`experiments/HarvesterExperiment.py`, persistence in `HarvesterPlanDB`
(`quantcore/repositories/harvester_repository.py`), and `HarvesterService` scanning prices against
active rungs and firing alerts from `main.py`'s notification pass. Rules to keep:

- **Plans are owned.** `owner` is a **required keyword-only argument** on every public repository
  and service method that touches a plan, a rung, or an alert — never a defaulted one.
- **Isolation is enforced in SQL, never in a route.** Every statement carries an `owner`
  predicate, **including private helpers** and the scan path's SQL constants; another owner's plan
  reads as `None` and the route answers 404. Routes resolve the owner via
  `Depends(require_owner)` — there is no `?owner=` on the plans/rungs/dashboard routes.
- **A plan may only exist while the owner holds the shares.** `build_plan` refuses a symbol with no
  `OPEN` lot (422); `PortfolioService._close_plan_if_flat` closes the plan to `CLOSED` after the
  write commits, in a `try/except` that only logs. A partial close leaves the plan ACTIVE.
- The two ends are wired with **repositories, not services** — services in both directions would
  close a construction cycle in `registry.py`.
- `V8__close_orphan_plans.sql` is a data migration and is deliberately **not** mirrored into
  `_SCHEMA` or the snapshot.

Full narrative (status vocabulary, the backstop flag, the Plan chip):
[`docs/architecture/harvester.md`](docs/architecture/harvester.md).

### Unified Database (`quantcore/`)

All persistence is a single **QuantCore** PostgreSQL database (22 tables, DDL in `_SCHEMA` in
`quantcore/db.py`), reached through `quantcore.db.get_connection()` (`QUANTCORE_DB_DSN`) by every
repository and the REST API. `ohlcv` has two writers that disagree about `adj_close`; two rules
follow:

- **A price refresh may fill `adj_close`, never blank it** — `OhlcvRepository`'s upsert uses
  `COALESCE(EXCLUDED.adj_close, ohlcv.adj_close)`.
- **A window measured in bars must be fetched in trading days** (`HarvesterService.build_plan`
  scales by `365/252`); when a gap survives anyway, raise — never substitute `close`.

Table list, why the writers disagree, and the SQLite migration script:
[`docs/architecture/data.md`](docs/architecture/data.md).

### Services Layer (`quantcore/`)

Per [`docs/proposals/architectural-standard-v2.md`](docs/proposals/architectural-standard-v2.md), all business logic lives in an object-oriented services layer; the MCP tool bodies (`fastMCPTest/*_server.py`, `options_analysis.py`) and FastAPI routes (`api/routers/*`, app assembled in `api/main.py`) are thin adapters that are **exactly one service call deep**.

- **`quantcore/gateways/`** — external-IO wrappers: `YFinanceGateway` (yfinance), `PolygonGateway` (Polygon HTTP/pagination), `AnthropicGateway` + `KeyproxyGateway` (the Sidekick/BYOK hops, with `keyproxy_fake.py` for tests). These are the *only* place outside `portfolio/` (the legacy domain layer, retained for `main.py`'s report path) and the standalone `experiments/` monitors that imports `yfinance`.
- **`quantcore/repositories/`** — SQL-only persistence, no analytics: `OhlcvRepository`, `OptionsStore`, `OptionsPositionStore`, `NewsStore`, `SentimentStore`, `FundamentalsRepository`, `HarvesterPlanDB`, `PortfolioRepository`, `WatchlistRepository`, `OwnerIdentityRepository`, `UserSettingsRepository`, `ArbitrageRepository` (also loads the curated `arb_universe.yaml`).
- **`quantcore/analytics/`** — pure functions (DataFrame/dict in, value out), no I/O: `indicators.py` (RSI/MACD, Wilder ATR, anchored VWAP, swing detection), `volume_profile.py` (volume-at-price histogram: POC, value area, HVN/LVN nodes), `options_math.py` (Black–Scholes delta/gamma/vega/vanna/charm, max-pain, expected-move — single home, deduped), `pairs.py` (hedge ratio + stability, ADF with AIC lag selection, Engle–Granger cointegration, OU half-life, spread z-score/trend — implemented on numpy so `statsmodels`/`scipy` stay out of the lean image), `nav.py` (net-of-senior-claims NAV per share, premium/discount, carry drag, exposure ratio), `portfolio_math.py` (lot/position roll-ups, allocation-bar segments), `returns.py` (close-to-close trailing/YTD/1-year returns and market-cap currency normalization — the single correct copy; the five columns in `portfolio/metrics.py` are the legacy report script's and disagree, deliberately), `market_time.py` (session and trading-day arithmetic).
- **`quantcore/services/`** — the business logic: `PricesService`, `OptionsService`, `OptionsContractsService`, `OptionsScreeningService`, `FundamentalsService`, `SentimentService`, `MicrostructureService`, `HarvesterService`, `PortfolioService`, `WatchlistService`, `SettingsService`, `IdentityService`, `ChatService` (+ `chat_tools.py`, `chat_fake.py`), `ArbitrageService`, `RecommendationsService` (composes the other services).
- **`quantcore/chat_models.py`** — pure-data catalog of the three user-selectable Sidekick chat models (issue #124: `claude-sonnet-5` default, `claude-opus-4-8`, `claude-fable-5`), injected by the registry into both `SettingsService` and `ChatService` so the two never import each other for it. `SettingsService` (backed by `UserSettingsRepository`'s `user_settings` table) resolves a per-owner chat model — falling back to the default if the stored value has since been retired from the allow-list — and validates writes against the same allow-list; exposed via `GET/PUT /api/settings` (`api/routers/settings.py`). The frontend Settings page and the Sidekick chat-header quick-switch both read/write this endpoint, converging on one server-side source of truth rather than sharing a JS module.
- **`quantcore/services/registry.py`** — the composition root: a lazy `@lru_cache get_services()` returning a frozen `Services` dataclass with all dependencies constructor-injected. Adapters call `get_services().<service>.<method>(...)`; service modules never import each other or the registry (acyclic).

**UI component rules (arch-v2 Rules 8–9):** any front-end component that displays analytical data must be **GenUI-compliant / sidekick-renderable** — scalar self-contained props, registered with matching strict prop specs in BOTH `quantcore/services/chat_tools.py` (`BACKEND_COMPONENT_REGISTRY` + the `show_component` tool description) and `frontend/src/chat/componentRegistry.tsx`, rendered via `DirectiveRenderer`, displayed math in `quantcore/analytics` (never in the front end), gestures only via the dual interaction registries + `useDirectiveInteractions` (honoring locked/consumed history). Every new or materially changed UI component ships vitest tests (loading/error/success + key values) and registry parity cases in the same PR; the vitest coverage thresholds only ratchet upward.

Positions are DB-backed and per-owner (`positions.owner`; `portfolio.csv` is a per-owner import
format). The watchlist is DB-backed and **global** — one shared list, `added_by` for audit only;
`watchlist.yaml` is import-only and there is deliberately **no fallback to the YAML file** (an
empty table is a loud Discord alarm). Rules to keep:

- `GET /api/watchlist/fundamentals` must stay **declared before** `/watchlist/{ticker}` in
  `api/routers/portfolio.py`, and its six-query, zero-network shape must not be "simplified" into a
  per-symbol loop (`tests/test_watchlist_service.py` guards it).
- Watchlist removal is UI-only: `mcp_gateway/rest_client.py` has no `delete` verb.
- **A watchlist entry's currency is resolved server-side** from `info["currency"]` (not
  `financialCurrency`), failing soft *and* fast: `YFinanceGateway.ticker_info` uses a daemon
  thread + `join(timeout)`, deliberately **not** a `ThreadPoolExecutor` — don't "tidy" it back.
  Don't add a `currency` parameter back to `add_to_watchlist`, or a currency picker to the
  Watchlist tab of the Add Security dialog.

Routes, the currency consequences in full, and `scripts/repair_watchlist_currency.py`:
[`docs/architecture/data.md`](docs/architecture/data.md).

**Refactor status:** architectural-standard-v2 Phases 1–3 (services layer, FastAPI REST tier,
MCP gateway + GCP Cloud Run) and the dedicated prod project rollout are **complete**. MCP wrappers
are thin HTTP gateways through the single seam `mcp_gateway/rest_client.py` (Rule 6 —
`AI Agent → MCP wrapper → REST tier → Service`); the report runs as a Cloud Run **Job** with
in-process services, never HTTP. Phase-by-phase record: [`docs/architecture/history.md`](docs/architecture/history.md).

### QuantUI front end on Cloud Run (behind IAP)

The React SPA (`frontend/`) runs as the **QuantUI** Cloud Run service in both projects, gated by
IAP — test `https://quantui-493357101423.us-central1.run.app`, prod
`https://quantui-127961694257.us-central1.run.app`. Rules to keep:

- **Pages are declared once, in `frontend/src/navigation.tsx`** — `<Routes>` and the nav bar both
  map over it; a drill-down (`nav: false`, e.g. `/plans/:id`) must never get a button. The active page is marked with `aria-current`; don't replace it with a CSS-only
  highlight.
- The Watchlist page leaves the native `market_cap` column **unsortable** (sort on
  `market_cap_usd`). The Fundamentals page has **no page-level loading gate**, its `scope` filter
  runs **before** the ranking, and the roster reaches `FundamentalsService` as a late-bound
  callable (injecting the services would close a construction cycle).
- The browser never sees a bearer: `frontend/server/` verifies the IAP assertion and mints a
  per-user ES256 JWT server-side.
- Deploy: merge to `main` → `deploy.yml` rolls **test**; prod only by manually dispatching
  `prod-rollout.yml`. Prod is never auto-deployed.
- Granting a user needs **both** the consent-screen Audience entry and
  `roles/iap.httpsResourceAccessor` (`scripts/grant_quantui_iap_access.sh`) — either alone is a
  blocked login.

Serving model, auth fallback ladder, the pages, and the grant procedure:
[`docs/architecture/quantui.md`](docs/architecture/quantui.md).

### BYOK key proxy (Sidekick chat — users bring their own Anthropic key)

**Status: COMPLETE — live on test and prod since 2026-07-18** (GitHub issue #100; plan +
checkpoint/runbook log in [`docs/proposals/byok-key-proxy-plan.md`](docs/proposals/byok-key-proxy-plan.md),
merged via PRs #105/#106 at `177e411`). The QuantUI Sidekick chat runs on each user's own
Anthropic API key; the backend never holds a usable key at rest.

- **Flow, auth layers, and deploy wiring** (browser vault → single-use envelope → `quantcore-api`,
  never decrypted there → IAM-locked `keyproxy/`, ES256-only user JWTs, dual-mode `api/auth.py`):
  [`docs/architecture/byok.md`](docs/architecture/byok.md).
- **Never-log policy (enforced by tests):** no API keys, `Authorization` headers, envelopes,
  decrypted payloads, request bodies, or exception dumps containing credentials may reach any log
  or print. Any new failure path must add the corresponding log assertion. The **database DSN**
  counts as a credential — it carries the password — and the policy covers API/MCP **responses**,
  not just logs: name a database with `quantcore.db.describe_dsn()` (`host:port/name`), never with
  the DSN. `tests/test_dsn_redaction.py` guards the case that got through
  (`cache_stats()` returned the DSN as `db_path` all the way out to the `get_cache_stats` MCP
  tool).
- **Gotchas learned on the prod rollout (details in the plan doc):** on existing Cloud Run
  services always `--update-secrets`/`--update-env-vars` (`--set-*` replaces the whole set);
  "inert" env-var claims must be checked against the image actually running (the pre-BYOK
  `api/auth.py` used `QUANTCORE_JWT_PUBLIC_KEY` as an HMAC secret and broke all HS256 tokens);
  the CI deployer needs `roles/iam.serviceAccountUser` on `keyproxy-runtime@` (granted in both
  projects).

### Cloud Run sizing (pinned in CI, single home)

**CPU and memory for every Cloud Run *service* live in the sizing env block at the top of
`.github/workflows/deploy.yml` and `.github/workflows/prod-rollout.yml`** (`API_CPU`/`API_MEMORY`,
`MCP_CPU`/`MCP_MEMORY`/`MCP_MEMORY_LITE`, `UI_*`, `KEYPROXY_*`), and every `gcloud run deploy` in
those workflows passes them — so each roll-out **re-asserts** the shape rather than inheriting
whatever the service happens to hold. Change the values there, not with a one-off
`gcloud run services update`, or the next deploy reverts you. The two files must agree. Deploys
remain image-only for env/secrets/Cloud-SQL/IAP bindings; sizing is the one shape carried in the
repo. The `quantcore-report` **Job** is not covered (it is `jobs update`, not `run deploy`).

Two constraints are load-bearing — both were learned the expensive way (2026-09-09, full record in
[`prod-rollout-plan.md`](docs/proposals/prod-rollout-plan.md) row P11):

- **CPU stays at 1 everywhere and must not be lowered.** Cloud Run rejects `Total cpu < 1 is not
  supported with concurrency > 1`, and every service here runs at concurrency 80+. Dropping
  concurrency to buy 0.5 vCPU would cost *more*: the wrappers hold 900s MCP sessions, so one
  session per instance means more instances.
- **`quantcore-api` stays 2 CPU / 4Gi deliberately.** Its *mean* memory utilization is ~6% — the
  number the console shows — but its p99 peak is **58% of 4Gi** on full options chains at
  concurrency 160, recurring daily. Halving it is an OOM, not a tight fit; the mean is the wrong
  metric for a memory ceiling.
- **`quantcore-api` deploys with `--cpu-boost`** (issue #280) — a flag on its deploy step, not a
  sizing variable. It adds CPU only during startup, so a cold start's torch import and baked-FinBERT
  load finish sooner; it is billed only for the boosted seconds. Like the sizing, both workflows
  pass it on every roll-out, so remove it there rather than with a one-off update.

**The roll-out is one step per workflow, in two parallel phases** (#296): phase 1 runs api, the
report and news Jobs, and keyproxy; phase 2 runs api's consumers (the 7 wrappers and quantui), and
only if phase 1 succeeded, so a failing api revision stops the run before its consumers roll. Each
deploy is a shell function run through `run_parallel` from `scripts/ci_parallel.sh`, which waits on
every PID and annotates each failure by name. Never replace it with a bare `&` + `wait`, which
swallows exit codes. Add a new service as a function and an entry in the right phase, in **both**
workflows; `tests/test_ci_parallel.py` checks the wiring. Design and timings:
[`parallel-rollout-plan.md`](docs/proposals/parallel-rollout-plan.md).

### Artifact Registry cleanup policy

The `quantcore` AR repo in each project carries a cleanup policy kept in the repo:
`scripts/ar_cleanup_policy.json`, applied with `scripts/apply_ar_cleanup_policy.sh [--prod]
[--enforce]` (dry run by default). It keeps each image's **15 newest versions unconditionally**
and deletes the rest once they are **older than 30 days**. Until it existed nothing was ever
deleted — 80 GB in test and 18 GB in prod by 2026-10-02 — and the baked FinBERT (#280) adds
~440 MB per api/news build.

**Never add a "delete untagged" rule.** Prod deploys the *inner* manifest digest that
`prod-rollout.yml`'s `docker buildx imagetools create` copies, and in the prod repo that manifest
is **untagged** — the tag lands on the wrapping index. The image prod is running therefore shows as
untagged, and a tag-state rule would delete it once it aged out, breaking new instances and
rollbacks. The policy is version-count based for exactly that reason. Change it by editing the JSON
and re-running the script, not with a one-off `gcloud` call, so the file stays the single home.


### Environments (prod is the system of record)

**Prod (`quantcore-prod-20260606`) is the system of record for all analysis for all users; test
(`quantcore-test-20260606`) is for development and CI only.** This supersedes the earlier "do
analysis on test, treat prod as read-only" operating rule — now that changes ship through CI/CD
(`deploy.yml` → test, `prod-rollout.yml` → prod), prod is the live system everyone reads from. The
deployed `quantui` UI and the `.mcp.json` AI-client remotes both already target prod.

The 7 remote MCP servers in `.mcp.json` (stock-price, company-fundamentals, options-analysis,
portfolio, arbitrage, news-sentiment, market-analysis — carrying **62 tools**; count them with the
**anchored** `grep -c "^@mcp.tool" fastMCPTest/*.py`, since the unanchored form counts a mention
inside `options_analysis.py`'s module docstring) send `Authorization: Bearer ${QUANTCORE_MCP_TOKEN}`, which
the wrappers forward unchanged to `quantcore-api` (identity passthrough → the legacy HS256
service-token path in the now dual-mode `api/auth.py`). So real analysis requires `QUANTCORE_MCP_TOKEN` to be a valid prod JWT in the
environment Claude Code launches from; if it's unset, every data tool returns `401: … Not enough
segments` (the wrapper-local `mcp_health_check` still passes, which is misleading). Each user mints
their own 3-month token with `scripts/mint_prod_jwt.py --output export --expires-hours 2160 --sub
<you>` (see readme "Connecting AI clients to prod"). **When onboarding a user, remind them the token
expires after 90 days and recommend quarterly rotation** (and a per-user `--sub`).

## Configuration

- **`.env`** — `QUANTCORE_DB_DSN` is the PostgreSQL connection string for the unified database (e.g. `postgresql://<user>:<password>@<host>:<port>/<database>`); `QUANTCORE_TEST_DB_DSN` optionally points the same code at an isolated database for testing (Cloud SQL test; what `with-test-db.sh`, `flyway.sh` and the import scripts mean by "test"); `QUANTCORE_UNITTEST_DB_DSN` is the local Postgres the unit suite prefers over it (issue #289, setup in `docs/local-unit-test-db.md`); `DISCORD_WEBHOOK_URL` for notifications; `BUCKET_NAME`/`BUCKET_KEY` for optional S3 upload; `CLOUDSQL_CONNECTION_NAME`/`_PORT`/`_QUOTA_PROJECT` and the parallel `CLOUDSQL_TEST_*` trio are the Cloud SQL Auth Proxy targets for prod (`:5433`) and test (`:5434`).
- **`portfolio.csv`** — Holdings data: `name,symbol,purchase_price,quantity,purchase_date,currency,sale_price,sale_date,current_price`
- **`watchlist.yaml`** — *Import format only* (issue #83). Entries with `name`, `symbol`, `currency`, and optional `tags` list; load them into the global `watchlist` table with `python scripts/import_watchlist.py --yaml watchlist.yaml` (full-sync replace). Nothing reads the file at runtime — the table is the source of truth, and the UI's add/remove actions write straight to it. The `currency:` field is a fallback: single adds resolve it from the exchange, and `scripts/repair_watchlist_currency.py` fixes imported rows.

**Database Initialization:** Every application entry point (`main.py`, REST API, MCP servers) calls
`ensure_schema()` — never `init_schema()` directly — before any database operations. The database
itself (and its `quantcore` user) must already exist; point `QUANTCORE_DB_DSN` at any reachable
PostgreSQL instance — local, or a managed service such as Cloud SQL accessed through the Cloud SQL
Auth Proxy (which exposes the remote instance as a local TCP host:port, so no code changes are
needed to switch targets).

What `ensure_schema()` does is set by **`QUANTCORE_SCHEMA_MODE`**, read at call time so the escape
hatch is one `gcloud run services update --update-env-vars` away:

| Mode | Behaviour |
|---|---|
| `create` | Run the 22-table DDL. Historic behaviour, and the escape hatch. |
| `warn` | Introspect, diff against `db/schema_snapshot.json`, log differences, run **no DDL**. |
| `verify` | As `warn`, but raise `SchemaDriftError` on any `MISSING`/`MISMATCH` (`EXTRA` never raises). |
| `auto` *(default)* | `create` where there is no `flyway_schema_history` (local, CI, compose, a new instance), otherwise `verify`. |

That is the fix for the two-owners problem: on a database Flyway already manages, the app stops
creating schema and only checks it. An unrecognized value falls back to `create` and logs an error
— a typo is most likely made by an operator reaching for the escape hatch mid-incident, and failing
closed there would deny them exactly what they were reaching for. The check emits one greppable
line (`schema check: mode=verify resolved=verify tables=22 missing=0 mismatch=0 extra=0`) plus one
line per difference, and never logs the DSN. The test suite pins `create` in `tests/__init__.py`, so
a developer's Flyway-managed test database and CI's bare Postgres behave identically.

**Migrations are now load-bearing** (`auto` soaked warn-only on both projects for a full deploy
cycle — missing=0, mismatch=0 — and now enforces):

- **CI migrates before it rolls out, in both projects** (issue #200). `deploy.yml` runs
  `scripts/ci_migrate.sh` after the build. `prod-rollout.yml` runs it after the promotion, with the
  promoted digest. The script points the `quantcore-migrate` Cloud Run Job at that commit's image
  and runs it with `--wait`. If it fails, nothing rolls out, and the step's `::error::` says which
  recovery applies. `init_schema()` is no longer the safety net on a deployed database; nothing
  else will create the object for you.
- **Contract and non-transactional migrations are refused** (`DROP`, `RENAME`,
  `ALTER … TYPE`, `CONCURRENTLY`, `VACUUM`, `ALTER SYSTEM`, `CREATE/DROP DATABASE`; the entrypoint
  `db/migrate-entrypoint.sh` exits 3 and logs `migrate: REFUSED`). Apply those by
  hand: `./scripts/flyway.sh migrate`, or `--prod migrate` for prod. Then re-run the workflow.
  Schema changes are **forward-fix only**: never edit an applied migration, and never roll an
  image back past a migration it doesn't understand.
- The Job and its service account `quantcore-migrate@` are created **once per project** by an
  operator, with `scripts/ensure_migrate_job.sh`. A missing Job fails the step; it is never
  skipped, because skipping would roll out an image ahead of its schema. Prod promotion is
  **strict** about the `quantcore-migrate` image, so a tag older than `1e7e7f9` can't be promoted.
  Roll back by revision or digest. Design, decisions and the proof runs:
  [`docs/proposals/flyway-automation-plan.md`](docs/proposals/flyway-automation-plan.md). Runbook:
  [`docs/operations/prod-promotion.md`](docs/operations/prod-promotion.md).
- A migration must now be **complete DDL**. A forgotten column used to be invisible because
  `_SCHEMA` created it at startup anyway; now it is a `MISSING`/`MISMATCH` line and the deploy
  fails.
- **Failing early is the feature.** `ensure_schema()` raises `SchemaDriftError` during startup, so
  the Cloud Run revision never passes its health check and never takes traffic — the previous
  revision keeps serving. The alternative is the drift surfacing hours later as query errors on
  live traffic.
- Escape hatch, one command, no code change:
  `gcloud run services update quantcore-api --project <project> --region us-central1 --update-env-vars QUANTCORE_SCHEMA_MODE=create`
  (`--update-env-vars`, never `--set-env-vars` — the latter replaces the whole set and has taken
  prod down before).

**Migrations (Flyway):** versioned SQL lives in `db/migrations/V*.sql`, configured by `db/flyway.conf` (which deliberately holds **no credentials** — `baselineOnMigrate=true`, `baselineVersion=1`). Run it with the wrapper, which derives the JDBC URL and login from the DSNs in `.env`, defaults to **test**, echoes the target host before running, and confirms before a prod `migrate`:

```bash
./scripts/flyway.sh info            # test (default)
./scripts/flyway.sh --prod info
./scripts/flyway.sh --prod migrate  # prompts
```

Every schema change touches exactly **three** files, and CI fails if you miss one:

| File | What it is | Guarded by |
|---|---|---|
| `db/migrations/V*.sql` | a **new** version, never an edit to an applied one | `tests/test_schema_parity.py` |
| `_SCHEMA` in `quantcore/db.py` | what `init_schema()` creates on startup | `tests/test_schema_parity.py` |
| `db/schema_snapshot.json` | the committed expectation (`python scripts/check_schema_snapshot.py --update`) | `scripts/check_schema_snapshot.py` in the `gate` job |

`tests/test_schema_parity.py` builds one scratch database from `init_schema()` and another from
`db/baseline/V1__*.sql` + every `db/migrations/V*.sql`, and fails with the full object-level diff
unless they are identical — so a change that ships to only one owner cannot merge. It is a hard
failure in CI, never a skip. Because `init_schema()` runs on every application startup, a deployed database has usually already reached the right *shape* before Flyway sees it — so pure-DDL migrations are expected to report "already exists, skipping", and **`flyway info` is a changelog view, not evidence of what a deployed database actually contains** — run `python scripts/schema_check.py --prod` for that (read-only; diffs live objects against `db/schema_snapshot.json` and prints the Flyway changelog separately, labelled for what it is). That two-owners-of-the-schema problem is tracked as [issue #165](https://github.com/JohnFunkCode/StockPortfolioManager/issues/165); plan and checkpoint log in [`docs/proposals/schema-ownership-plan.md`](docs/proposals/schema-ownership-plan.md).

## Key Dependencies

pandas, numpy, yfinance, python-dotenv, PyYAML, requests, psycopg2, fastapi, uvicorn, pydantic,
fastmcp, httpx.

Requirements are **layered**, and the split is load-bearing — it is what keeps matplotlib out of
the API and MCP images:

| File | Holds | Installed by |
|---|---|---|
| `requirements-base.txt` | the lean set above | every `Dockerfile.*` |
| `requirements-ml.txt` | torch / transformers (FinBERT) | the sentiment path only |
| `requirements-report.txt` | **matplotlib, jinja2, boto3** | nothing in any container |
| `requirements-dev.txt` | base + report + coverage/diff-cover | CI's `gate` job |
| `requirements.txt` | base + ml + report | local dev, and the Pi |

**matplotlib, jinja2, and boto3 are report-script-only** (issue #147). They serve
`scripts/generate_portfolio_report.py`, the legacy root scripts `html_summary.py` /
`simple_text_summary.py`, and nothing else. Importing any of them from code that runs in a
container is the mistake this split exists to make visible — add the dependency to
`requirements-base.txt` deliberately, or don't add the import.

**The test suite has to stay importable under the lean set, and the two CI jobs disagree about
it.** `deploy.yml` installs `requirements-dev.txt` (base + report), but `prod-rollout.yml` runs
`unittest discover` on `requirements-base.txt` alone — so a test module that imports a
report-only package passes every PR and then fails the **prod promotion**, which is the worst
possible place to learn it. A test that genuinely needs matplotlib/jinja2/boto3 wraps its import
and raises `unittest.SkipTest`, tolerating **only** those three packages;
`tests/test_generate_portfolio_report.py` is the pattern. Anything else failing to import is a
real defect and must still error the run.

That disagreement is now checked at PR time rather than discovered at a promotion.
`deploy.yml`'s **`lean-import` job** installs `requirements-base.txt` into its own runner and runs
**`scripts/ci_lean_import_smoke.py`**, which imports — and only imports — `main.py`, `api/main.py`,
every MCP wrapper (the list is read from `scripts/ci_wrapper_smoke.py`, so a new server is covered
for free), and every `tests/test_*.py`. A module that raises `SkipTest` at import is reported as
skipped; anything else that fails to import is a red build, and `deploy` won't roll out to test
until it's green. It needs a Postgres service despite being import-only, because `api/main.py`
builds the app at module level and that calls `ensure_schema()`. It deliberately does not run the
tests — the `gate` job already does, and a second full execution would cost minutes to answer a
question about imports.

The `gate` job also runs **`scripts/check_cloudbuild.py`**, which parses `cloudbuild.yaml` and
checks every `quantcore-*` image a step builds is tagged by `tag-latest` (and vice versa), that
`tag-latest` waits for every build step, and that there is no `images:` block. Without it
the first reader of that file is `gcloud builds submit` *after* merge — a mis-indented `images:`
line reached `main` once and blocked the test roll-out.

It also enforces each build step's **layer-cache wiring** (#278, reworked #296 follow-up):
`buildx build --builder quantcore` (the docker-container BuildKit the `builder` step creates),
`--cache-from` and `--cache-to` its own image's `:buildcache-${_CACHE_TAG}` with `mode=max`,
`--provenance=false` and `--push`, plus a `tag-latest` step that waits for every build and moves
`:${_LATEST_TAG}` to the same digest. Don't go back to `docker build` with
`BUILDKIT_INLINE_CACHE=1`: the stock builder's BuildKit re-exports only the layers it ran, so
main alternated warm and cold. All seven steps run in parallel; a warm build with a source change
takes ~1.3 min. Without the wiring the image is still correct, only cold again, which is why the
checker guards it. The pip layers are cached, so `deploy.yml` passes `_DEPS_EPOCH` (the ISO week)
to re-resolve the `>=` floors weekly; keep that arg in front of each Python Dockerfile's install.
**Never warm or tag a trial build as `:latest`** in test AR, because prod-rollout's default tag is
`latest`: pass scratch `_TAG`, `_CACHE_TAG` **and** `_LATEST_TAG`. Numbers and gotchas:
[`docs/proposals/cloud-build-speed-plan.md`](docs/proposals/cloud-build-speed-plan.md).
