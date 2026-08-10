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
# initializer swaps in the test DSN before quantcore.db is imported)
python -m unittest discover -s tests -t .

# Backend tests with coverage (CI enforces a ratchet floor — see .coveragerc + deploy.yml gate)
coverage run -m unittest discover -s tests -t . && coverage report

# Frontend tests with coverage (thresholds in frontend/vitest.config.ts)
cd frontend && npx vitest run --coverage

# Run a single test module (dotted path from the repo root)
python -m unittest tests.test_money
python -m unittest tests.test_stock_portfolio_manager

# Start the REST API
uvicorn api.main:app --host 127.0.0.1 --port 5001

# Database migrations (defaults to the TEST database; prompts before a prod migrate)
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

`main.py` is the daily Cloud Run Job (`quantcore-report` — the service name kept the old
spelling; the work did not). It loads John's positions and the shared watchlist from the database
(via `get_services().portfolio` / `.watchlist` — never from `portfolio.csv` or `watchlist.yaml`),
fetches prices/metrics, and then does three things **in this order, which is a deliberate
isolation property** — the cheap, high-value side effects land before anything that can run long:

1. **Notifications** — `Notifier(portfolio).calculate_and_send_notifications()`, plus
   `alert_if_watchlist_empty()`.
2. **Options capture** — a full options chain snapshot per symbol (in-process
   `OptionsService.get_full_options_chain`, capped expirations, per-symbol try/except) so
   open-interest history accumulates daily for `get_oi_change_analysis`. The universe
   (`capture_symbols`) is John's positions + the global watchlist + **every other owner's**
   positions (issue #126 decision #5).
3. **Fundamentals warming** — `run_fundamentals_warming(capture_symbols, …)` refreshes the
   fundamentals cache over that same universe, **oldest-`fetched_at` first**, under a wall-clock
   budget. A cold pass is ~10 serial yfinance calls per symbol and is *expected* not to finish;
   the universe converges over a few nights. Ordering comes from
   `FundamentalsService.cache_freshness()`; the pass is wrapped in an outer `try/except` that
   never raises, so a warmer failure cannot retroactively fail a run whose notifications already
   went out. That silence is then made loud by `alert_if_fundamentals_stale()`, which re-reads
   freshness **after** warming and fires a Discord alarm on either trigger — coverage below a
   floor, or an oldest-age above a ceiling. Tunable per-deployment without a code change:

   | Env var | Default | Meaning |
   |---|---|---|
   | `FUNDAMENTALS_WARM_BUDGET_SECONDS` | `900` | wall-clock budget for the warming pass |
   | `FUNDAMENTALS_STALE_COVERAGE_FLOOR` | `0.80` | alarm below this in-TTL fraction |
   | `FUNDAMENTALS_STALE_MAX_AGE_HOURS` | `168` | alarm above this oldest age |

   An unparseable value logs a warning and falls back to the default — a typo in a Cloud Run env
   var must not silently disarm the alarm.

Since issue #147 `main.py` **does not render the HTML report**. That moved verbatim to
**`scripts/generate_portfolio_report.py`** (`--output PATH`, or `--publish` to upload to S3),
which the Raspberry Pi runs via `runOnPi.sh`. Two consequences worth keeping straight:

- The Pi runs the script and **not** `main.py`, because the Cloud Run Job already sends the
  notifications and captures the snapshots — running both would double every alert.
- `--publish` now **fails loudly** when `BUCKET_NAME`/`BUCKET_KEY` are missing, checked up front
  before minutes of price fetching. The old code returned `None` and let the caller report
  success, which is how the public page could stop updating unnoticed (the original #147 defect).

### Notifications (`notifier.py`)

Sends Discord webhook alerts for: moving average violations (30/50/100/200-day), price below purchase price, and Harvester plan rung hits. Uses `notification.log` file to deduplicate alerts within a run.

### Arbitrage Scanner

Finds securities whose price has stretched against a structurally linked underlying, across
three families: **nav_vehicle** (treasury companies/trusts — the only family with a computable
fair value), **commodity_etf** (fund vs its reference future), and **producer** (miner/E&P vs
the commodity it sells). Curated links live in **`arb_universe.yaml`** at the repo root
(alongside `watchlist.yaml`); `discover_pairs` additionally sweeps for undeclared cointegrated
links against a reference panel, gated by a sector/industry economic-link filter.

**The scoring is deliberately inverted: spread width only qualifies a candidate, the
convergence mechanism ranks it.** `ArbitrageService._score` multiplies named factors —
`opportunity × evidence × convergence × hedge × carry × trend × freshness` — all returned in
the `factors` block alongside `reasons` and `breaks_on`, so any score is attributable. The
design and every penalty trace to
[`docs/analysis results/MSTR_BTC_arbitrage_assessment_2026-07-14.md`](docs/analysis%20results/MSTR_BTC_arbitrage_assessment_2026-07-14.md);
`tests/test_arbitrage_nav.py` is a regression guard that the MSTR inputs still produce a ~10%
**net** discount rather than the ~37% headline against gross assets. Because the account is
equity/ETF-only, any pair whose sole clean hedge is a futures contract is flagged
`hedge_available: false` and halved.

Surfaced as `GET /api/arbitrage/{universe,scan,discover,pairs/{security}}` and its own MCP
wrapper `fastMCPTest/arbitrage_server.py` (`arbitrage-server`, port 6007 locally,
`quantcore-arbitrage` on Cloud Run) carrying `list_arbitrage_universe`,
`analyze_arbitrage_pair`, `scan_arbitrage`, `discover_arbitrage_pairs` — one domain per
server, like the others. Expect most scans to return nothing above `watch` — that is the
intended behaviour, not a bug.

**Driving it:** [`docs/arbitrage-scanner-usage.md`](docs/arbitrage-scanner-usage.md) — example
prompts per tool, how to read the `factors` breakdown, the MSTR worked example (gross vs net
discount), and how to add a pair to `arb_universe.yaml`.

### Harvester System

An experimental "harvest ladder" strategy for systematically selling shares as prices rise:

- **`experiments/HarvesterExperiment.py`** — Core algorithm: computes volatility-based harvest thresholds (H), builds forward price target ladders, and backtests harvest plans. (`experiments/INTC_bear_call_spread_monitor.py` and `WMT_bull_call_spread_monitor.py` are standalone position monitors kept alongside it.)
- **`quantcore/repositories/harvester_repository.py`** — `HarvesterPlanDB` + `PlanBuildParams` persist plans in the unified **QuantCore** PostgreSQL database (plan templates/instances/rungs/alerts). SQL only.
- **`quantcore/services/harvester.py`** — `HarvesterService` wraps the repository and scans prices against active plan rungs, firing alerts (the former `HarvesterController` behaviour).

The Harvester integrates with the notification system: when `main.py` runs, it checks each portfolio stock against active harvest plan rungs (via `HarvesterService`) and sends Discord alerts for any hits.

### Unified Database (`quantcore/`)

All persistence is consolidated into a single **QuantCore** PostgreSQL database, accessed via `psycopg2`:

- **`quantcore/db.py`** — Shared connection factory (`get_connection()`) backed by `psycopg2`, connecting via the `QUANTCORE_DB_DSN` environment variable. Centralized schema DDL for all 22 tables (`init_schema()`), using `SERIAL` primary keys and `ON CONFLICT` upserts. Imported as `from quantcore.db import get_connection`.
- **Schema** includes: symbols, OHLCV (merged from daily + intraday intervals), fetch_log, positions/lot_sales/owner_identities, watchlist (the global shared list, #83), plan_templates/instances/rungs/alerts (Harvester), options_snapshots/expirations/contracts/gamma_wall_history/gex_history/options_positions, news_articles, sentiment_snapshots, fundamentals_history, user_settings (per-owner UI preferences, e.g. the Sidekick chat model), arb_nav_snapshots (curated holdings/capital-structure history for the arbitrage scanner's NAV vehicles).

All repositories under `quantcore/repositories/` and the REST API (`api/main.py`) use the shared factory instead of managing individual database connections.

**Migrating from a legacy SQLite database:** `scripts/migrate_sqlite_to_postgres.py` performs a one-shot copy of an existing `quantcore.sqlite` file into PostgreSQL — it initializes the schema, migrates all 16 tables in FK-safe order via batched `execute_values()` inserts, resets `SERIAL` sequences, and verifies row counts. Run it with `--sqlite <path>` and `--dsn <postgresql-uri>`.

### Services Layer (`quantcore/`)

Per [`docs/proposals/architectural-standard-v2.md`](docs/proposals/architectural-standard-v2.md), all business logic lives in an object-oriented services layer; the MCP tool bodies (`fastMCPTest/*_server.py`, `options_analysis.py`) and FastAPI routes (`api/routers/*`, app assembled in `api/main.py`) are thin adapters that are **exactly one service call deep**.

- **`quantcore/gateways/`** — external-IO wrappers: `YFinanceGateway` (yfinance), `PolygonGateway` (Polygon HTTP/pagination), `AnthropicGateway` + `KeyproxyGateway` (the Sidekick/BYOK hops, with `keyproxy_fake.py` for tests). These are the *only* place outside `portfolio/` (the legacy domain layer, retained for `main.py`'s report path) and the standalone `experiments/` monitors that imports `yfinance`.
- **`quantcore/repositories/`** — SQL-only persistence, no analytics: `OhlcvRepository`, `OptionsStore`, `OptionsPositionStore`, `NewsStore`, `SentimentStore`, `FundamentalsRepository`, `HarvesterPlanDB`, `PortfolioRepository`, `WatchlistRepository`, `OwnerIdentityRepository`, `UserSettingsRepository`, `ArbitrageRepository` (also loads the curated `arb_universe.yaml`).
- **`quantcore/analytics/`** — pure functions (DataFrame/dict in, value out), no I/O: `indicators.py` (RSI/MACD, Wilder ATR, anchored VWAP, swing detection), `volume_profile.py` (volume-at-price histogram: POC, value area, HVN/LVN nodes), `options_math.py` (Black–Scholes delta/gamma/vega/vanna/charm, max-pain, expected-move — single home, deduped), `pairs.py` (hedge ratio + stability, ADF with AIC lag selection, Engle–Granger cointegration, OU half-life, spread z-score/trend — implemented on numpy so `statsmodels`/`scipy` stay out of the lean image), `nav.py` (net-of-senior-claims NAV per share, premium/discount, carry drag, exposure ratio), `portfolio_math.py` (lot/position roll-ups, allocation-bar segments), `returns.py` (close-to-close trailing/YTD/1-year returns and market-cap currency normalization — the single correct copy; the five columns in `portfolio/metrics.py` are the legacy report script's and disagree, deliberately), `market_time.py` (session and trading-day arithmetic).
- **`quantcore/services/`** — the business logic: `PricesService`, `OptionsService`, `OptionsContractsService`, `OptionsScreeningService`, `FundamentalsService`, `SentimentService`, `MicrostructureService`, `HarvesterService`, `PortfolioService`, `WatchlistService`, `SettingsService`, `IdentityService`, `ChatService` (+ `chat_tools.py`, `chat_fake.py`), `ArbitrageService`, `RecommendationsService` (composes the other services).
- **`quantcore/chat_models.py`** — pure-data catalog of the three user-selectable Sidekick chat models (issue #124: `claude-sonnet-5` default, `claude-opus-4-8`, `claude-fable-5`), injected by the registry into both `SettingsService` and `ChatService` so the two never import each other for it. `SettingsService` (backed by `UserSettingsRepository`'s `user_settings` table) resolves a per-owner chat model — falling back to the default if the stored value has since been retired from the allow-list — and validates writes against the same allow-list; exposed via `GET/PUT /api/settings` (`api/routers/settings.py`). The frontend Settings page and the Sidekick chat-header quick-switch both read/write this endpoint, converging on one server-side source of truth rather than sharing a JS module.
- **`quantcore/services/registry.py`** — the composition root: a lazy `@lru_cache get_services()` returning a frozen `Services` dataclass with all dependencies constructor-injected. Adapters call `get_services().<service>.<method>(...)`; service modules never import each other or the registry (acyclic).

**UI component rules (arch-v2 Rules 8–9):** any front-end component that displays analytical data must be **GenUI-compliant / sidekick-renderable** — scalar self-contained props, registered with matching strict prop specs in BOTH `quantcore/services/chat_tools.py` (`BACKEND_COMPONENT_REGISTRY` + the `show_component` tool description) and `frontend/src/chat/componentRegistry.tsx`, rendered via `DirectiveRenderer`, displayed math in `quantcore/analytics` (never in the front end), gestures only via the dual interaction registries + `useDirectiveInteractions` (honoring locked/consumed history). Every new or materially changed UI component ships vitest tests (loading/error/success + key values) and registry parity cases in the same PR; the vitest coverage thresholds only ratchet upward.

Positions are DB-backed with multi-owner support (`positions` table, `owner` column); `portfolio.csv` is a per-owner import format (`scripts/import_portfolio.py --csv portfolio.csv --owner john`, full-sync replace). The REST `GET/POST/DELETE /api/portfolio*` routes take an `?owner=` param defaulting to `john`; `main.py`'s report/notifications stay on John's portfolio.

The watchlist is DB-backed too (`watchlist` table, `WatchlistRepository` → `WatchlistService`, issue #83) but — unlike positions — it is **global**: one shared list, no `owner` column, with the writing principal recorded in `added_by` for audit only. `watchlist.yaml` is now purely an import format (`scripts/import_watchlist.py`, full-sync replace); every consumer (the daily report, the options screener, the fundamentals report, the REST tier) reads the table, and there is deliberately **no fallback to the YAML file** — an empty table is a loud Discord alarm (`alert_if_watchlist_empty` in `main.py`), not a quiet degrade. Surfaced as `GET/POST /api/watchlist`, `DELETE /api/watchlist/{ticker}`, and `GET /api/watchlist/fundamentals` (returns + cached fundamentals for the whole list — `WatchlistService.returns_and_fundamentals` composes `PricesService` and `FundamentalsService` to serve it in **six queries and zero network calls**, which is what let the nightly HTML report become a page; the query count is constant in list size and `tests/test_watchlist_service.py` guards it, so do not "simplify" it into a per-symbol loop). That route must stay **declared before** `/watchlist/{ticker}` in `api/routers/portfolio.py` — FastAPI matches in declaration order. Plus `list_watchlist` / `add_to_watchlist` on the portfolio MCP wrapper. Removal is a UI-only action: the seam `mcp_gateway/rest_client.py` has no `delete` verb, so no agent can drop symbols off a list the whole team shares.

**The currency on a watchlist entry is resolved server-side, not supplied.**
`WatchlistService.add_entry` reads it off the exchange via `YFinanceGateway.ticker_info`
(`info["currency"]` — the *trading* currency, which is the unit `marketCap` is quoted in;
`financialCurrency` is a different thing and using it mislabels the cap). The `currency`
argument survives in the signature but has demoted to a **fallback**, used only when the
lookup comes back empty, and a disagreement is logged. Three entries seeded from
`watchlist.yaml` were declared USD and are not — ASSA-B.ST is Stockholm, AUTO.OL Oslo,
NIB.F Frankfurt — which renders a foreign market cap as dollars: a wrong number, not a
missing one. Consequences to keep straight:

- The lookup **fails soft, never closed** — Yahoo being down must not block adding a symbol.
  A miss falls back to the supplied value and logs a warning; it never raises.
- Soft is not enough on its own: it must also be **fast**, because this runs inside the user's
  `POST /api/watchlist`. `add_entry` passes `ADD_LOOKUP_TIMEOUT_SECONDS` (6s, under the
  gateway's 15s default) and `YFinanceGateway.ticker_info` enforces it with a bare **daemon
  thread + `join(timeout)`** — deliberately *not* a `ThreadPoolExecutor`. `Executor.__exit__`
  calls `shutdown(wait=True)`, so raising `TimeoutError` inside `with ThreadPoolExecutor(...)`
  blocks on the way out until the hung worker returns anyway: the timeout picks when the
  exception is *built*, not when the caller regains control (measured — a 1s timeout against a
  6s hang took 6.01s, and the add path held a caller 30s behind a 0.25s deadline). Don't
  "tidy" it back into an executor; `tests/test_yfinance_gateway.py` and
  `tests/test_watchlist_service.py` assert on elapsed wall clock for exactly that reason.
- `add_to_watchlist` on the portfolio MCP wrapper has **no `currency` parameter** at all, and
  the Add Security dialog shows its currency picker on the Portfolio tab only. Don't add
  either back; `tests/test_mcp_seam.py` and `AddSecurityDialog.test.tsx` guard both.
- `POST /api/watchlist` returns `{symbol, destination, currency}` — the currency that was
  *stored*, which is not necessarily what was posted.
- Rows already in the table are repaired by **`scripts/repair_watchlist_currency.py`**
  (dry run by default, `--apply` to write, `--symbols A,B` to scope, refuses prod without
  `--allow-prod`), which drives `WatchlistService.resync_currencies`.

**Refactor status:** Phase 1 of architectural-standard-v2 (services-layer extraction) is **complete** — see [`docs/proposals/phase1-migration-plan.md`](docs/proposals/phase1-migration-plan.md) for the checkpoint log. Phase 2 (FastAPI/Pydantic REST tier) is **complete** — the Flask app (`api/app.py`) has been retired and rebuilt on FastAPI (app factory `api/main.py`, route groups under `api/routers/*`, Pydantic request/response schemas under `api/schemas/*`), preserving every route path and JSON shape so the React front end runs unmodified; OpenAPI docs are served at `/docs` and the spec at `/openapi.json`. See [`docs/proposals/phase2-fastapi-plan.md`](docs/proposals/phase2-fastapi-plan.md) for the checkpoint log. Run it with `uvicorn api.main:app --host 127.0.0.1 --port 5001` (or `python -m api.main`). Phase 3 (AI gateway + GCP deployment) is **complete on the test project** — see [`docs/proposals/phase3-gateway-plan.md`](docs/proposals/phase3-gateway-plan.md): the MCP servers (`fastMCPTest/*_server.py`, `options_analysis.py`) were inverted into thin **HTTP gateway wrappers** that call the REST tier through the single seam `mcp_gateway/rest_client.py` (Rule 6 — `AI Agent → MCP wrapper → REST tier → Service`); `api/auth.py` adds JWT verification (inert until a key is configured, so local/compose stay open); everything is containerized (`Dockerfile.{api,mcp,report}`, `docker-compose.yml` local stack) and deployed to **GCP Cloud Run** — `quantcore-api` (JWT-enforced) + 7 wrapper services + `main.py` as a daily Cloud Run **Job** on Cloud Scheduler (in-process services, never HTTP). CI/CD is `.github/workflows/deploy.yml` (tests + wrapper smoke + OpenAPI surface diff, then build/roll-out; push/PR triggers are gated by a `preflight` job that skips the deploy when the test-WIF secrets are absent — wire them with `scripts/setup_test_wif.sh`). **Production rollout is COMPLETE** — see [`docs/proposals/prod-rollout-plan.md`](docs/proposals/prod-rollout-plan.md): rather than a test-service DSN flip, the same stack was stood up in a **dedicated prod project** `quantcore-prod-20260606` (project # `127961694257`, `us-central1`) reaching its own prod Cloud SQL — `quantcore-api` (JWT-enforced) + 7 wrapper services + the report Cloud Run Job on Cloud Scheduler, on images **copied by digest** test→prod (api `ac5cd17f…`, mcp `1b7da905…`, report `65d70659…`). Gated prod CI/CD is `.github/workflows/prod-rollout.yml` (`workflow_dispatch`/`release`, `prod` GitHub Environment with required reviewers, separate prod WIF). `.mcp.json` points AI clients at the prod wrapper `/mcp` URLs (`https://quantcore-<svc>-127961694257.us-central1.run.app`, bearer `${QUANTCORE_MCP_TOKEN}`); the 7 `*-local` entries remain for the docker-compose stack. The deferred `portfolio/yfinance_gateway.py` `get_latest_prices` fragility is now **hardened** (retry/back-off + graceful all-None degrade so a flaky Yahoo response no longer crashes the daily report); rebuilding/redeploying the prod report image with this fix is a pending user/CI step.

### QuantUI front end on Cloud Run (behind IAP)

The React SPA (`frontend/`) is deployed as the **QuantUI** Cloud Run service in both projects,
gated by **Identity-Aware Proxy (IAP)** so the team reaches the real UI from anywhere with no auth
code in the app — see [`docs/proposals/quantui-iap-plan.md`](docs/proposals/quantui-iap-plan.md)
(status: **COMPLETE, Steps 1–8**). Live URLs:

- **Test:** `https://quantui-493357101423.us-central1.run.app` (`quantcore-test-20260606`)
- **Prod:** `https://quantui-127961694257.us-central1.run.app` (`quantcore-prod-20260606`)

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
  or print. Any new failure path must add the corresponding log assertion. The **database DSN**
  counts as a credential — it carries the password — and the policy covers API/MCP **responses**,
  not just logs: name a database with `quantcore.db.describe_dsn()` (`host:port/name`), never with
  the DSN. `tests/test_dsn_redaction.py` guards the case that got through
  (`cache_stats()` returned the DSN as `db_path` all the way out to the `get_cache_stats` MCP
  tool).
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

- Migrate **before** the image carrying the schema change deploys, in **both** projects:
  `./scripts/flyway.sh migrate` before the merge to `main` (`deploy.yml` auto-rolls test), and
  `./scripts/flyway.sh --prod migrate` before dispatching `prod-rollout.yml`. `init_schema()` is
  no longer the safety net on a deployed database; nothing else will create the object for you.
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
