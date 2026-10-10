# Stock Portfolio Manager (QuantCore)

QuantCore is a portfolio tracker and market-analysis platform. A FastAPI REST tier runs every piece
of business logic over a single PostgreSQL database. Seven MCP servers expose that analysis to AI
clients such as Claude Code. The **QuantUI** web app pairs a portfolio and research dashboard with
the **Sidekick**, an AI chat that runs on each user's own Anthropic API key. Daily Cloud Run Jobs
price positions, send alerts, capture options chains and score the news.

## Features

- **Per-user portfolios.** Positions are stored per owner as purchase lots. Each lot and sale can
  carry an optional note on why it was bought or sold. The UI edits them, and `portfolio.csv` is
  the import format.
- **A shared watchlist** with per-symbol tags, stored in the database and edited from the UI. The
  currency is resolved from the exchange. `watchlist.yaml` is an import format only.
- **A fundamentals cache and screeners.** A composite score, revenue growth, EPS acceleration and
  the earnings calendar are cached as a time series. The tracked universe is ranked by score,
  broken down by sector, and checked for score changes.
- **Technical and support-level analytics:**
  - RSI, MACD and stochastic
  - VWAP: rolling, anchored and history
  - volume profile and ATR bands with a chandelier stop
  - relative strength
  - a support-confluence tool that clusters every level-finding method into scored zones
- **Options analytics:**
  - full-chain capture and exact contract lookup
  - vertical-spread pricing
  - unusual calls and open-interest (OI) change
  - signed GEX profile and gamma-wall history
  - a **cache-first watchlist screen** that answers in seconds without calling the data provider
- **Trade recommendations.** Thirteen signals are scored into one recommendation with entry,
  target, stop, size and risk/reward. It also flags when the signals contradict each other.
- **An arbitrage scanner** for securities stretched against a structurally linked underlying: NAV
  vehicles, commodity ETFs and producers. Candidates are ranked by how they would converge, not by
  how wide the spread is.
- **The Harvester.** A volatility-based "harvest ladder" of price targets for selling into
  strength, with alerts when a rung is hit.
- **News sentiment.** Headlines are collected daily and scored with FinBERT.
- **Discord alerts** for moving-average breaks, price below cost, and Harvester rung hits.
- **Sidekick chat.** Bring your own Anthropic key, which is never usable by the backend at rest.
  Each user picks their chat model.

## Architecture at a glance

```text
AI client ──► MCP wrapper (×7) ──┐
                                 ├──► REST tier (FastAPI) ──► services ──► PostgreSQL
QuantUI (React) ──► Express ─────┘                              │
                                                                └──► external data (Yahoo, Polygon, Anthropic)
```

All business logic lives in an object-oriented services layer. REST routes and MCP tool bodies
are thin adapters, each exactly one service call deep. The design is
[`docs/proposals/architectural-standard-v2.md`](docs/proposals/architectural-standard-v2.md), and
how it got here is in [`docs/architecture/history.md`](docs/architecture/history.md).

| Layer | Lives in | Rule |
|-------|----------|------|
| Gateways | `quantcore/gateways/` | The only code that talks to external services: yfinance, Polygon, Anthropic and the key proxy |
| Repositories | `quantcore/repositories/` | SQL only, no analytics |
| Analytics | `quantcore/analytics/` | Pure functions with no I/O: indicators, options math, pairs, NAV, volume profile, returns |
| Services | `quantcore/services/` | The business logic. Services never import each other |
| Composition root | `quantcore/services/registry.py` | `get_services()` wires everything by constructor injection |

The MCP wrappers never touch the database. They call the REST tier through a single seam,
`mcp_gateway/rest_client.py`, forwarding the caller's bearer token unchanged.

## Engineering practices

- **CI gates on every PR:**
  - a coverage ratchet on both backend and frontend
  - a "lean import" job that proves every entry point and test imports under the slim dependency
    set
  - a gitleaks secret scan
  - schema-snapshot and Cloud Build config checks
  - a dependency-vulnerability audit on any lock change, plus a daily audit of main's locks
- **Hash-pinned dependency locks.** Every install uses `pip install --require-hashes`, and a
  weekly PR moves the pins.
- **Flyway migrations run by CI before each rollout.** Contract and non-transactional migrations
  are refused, a parity test proves the migrations and the app's own DDL build the same schema,
  and a startup drift check stops a mismatched revision from ever taking traffic.
- **Least-privilege database roles.** The services connect with a DML-only role, and only the
  migration Job holds the owner role.
- **Prod is never auto-deployed.** Merges roll out to a test project. Prod is promoted by manual
  dispatch, copying the tested image by digest.
- **A declarative Cloud Run inventory** ([`deploy/cloudrun-services.toml`](deploy/cloudrun-services.toml))
  holds every service's configuration. The rollout runs in two parallel phases, so a failing API
  revision stops before its consumers roll.
- **Workload Identity Federation** for CI, with trust scoped to the workflow and branch. There are
  no long-lived deploy keys.
- **A contract test for every MCP tool,** so a tool without a case fails the build.
- **A never-log policy for credentials,** enforced by tests. It covers API keys, auth headers,
  envelopes and database DSNs, in logs and in responses.

## Technologies

Python 3.12, with:

- FastAPI and Pydantic
- FastMCP
- pandas and numpy
- yfinance
- PostgreSQL via psycopg2, with Flyway
- PyTorch and transformers (FinBERT)

On the front end:

- React 19 and TypeScript
- MUI 6 and MUI X Data Grid 7
- React Router 7 and TanStack Query 5
- Vite 6 and Vitest
- Node 22 and Express, which serve it

Everything is deployed with Docker on Google Cloud Run, Cloud SQL and Identity-Aware Proxy, through
GitHub Actions and Cloud Build.

---

## Getting started (local)

### Install

```bash
git clone https://github.com/JohnFunkCode/StockPortfolioManager.git
cd StockPortfolioManager
python3.12 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
```

The requirements are layered, and the split is what keeps the container images lean:

| File | Holds |
|------|-------|
| `requirements-base.txt` | the lean core: API, MCP wrappers, daily job |
| `requirements-ml.txt` | base + torch/transformers for FinBERT |
| `requirements-report.txt` | matplotlib, jinja2 and boto3, for the legacy report script only |
| `requirements-dev.txt` | base + report + coverage tooling |
| `requirements.txt` | everything, for local development |

The `.txt` files are the human-edited inputs, and every install uses the hash-pinned `.lock`
compiled from them. After editing a `.txt`, run `scripts/lock_deps.sh` (it needs
[uv](https://docs.astral.sh/uv/)) and commit both; CI fails a stale lock. Background:
[`docs/proposals/pin-deps-plan.md`](docs/proposals/pin-deps-plan.md).

### Configure

Create a `.env` in the repo root. It is git-ignored; never commit credentials.

| Variable | Purpose |
|----------|---------|
| `QUANTCORE_DB_DSN` | PostgreSQL connection string, e.g. `postgresql://<user>:<password>@<host>:<port>/<database>` |
| `QUANTCORE_TEST_DB_DSN` | optional: an isolated database for testing and one-off scripts |
| `QUANTCORE_UNITTEST_DB_DSN` | optional: a local Postgres the unit suite prefers (see [Testing](#testing)) |
| `QUANTCORE_SCHEMA_MODE` | optional: how startup treats the schema (below) |
| `DISCORD_WEBHOOK_URL` | where alerts are posted |
| `BUCKET_NAME`, `BUCKET_KEY` | optional: S3 upload target for the legacy HTML report |
| `CLOUDSQL_CONNECTION_NAME`, `CLOUDSQL_PORT`, `CLOUDSQL_QUOTA_PROJECT` | optional: Cloud SQL Auth Proxy target (and a `CLOUDSQL_TEST_*` trio for the test instance) |

### Database

Point `QUANTCORE_DB_DSN` at any reachable PostgreSQL database (CI uses 16); the database and its user must
already exist. A managed instance works the same way through the Cloud SQL Auth Proxy, which
exposes it as a local `host:port`. `./runProxy-MAC.sh` starts the proxy for the prod target, and
`--test` starts it for the test target, both read from `.env`. To run a one-off against the test
database, use `./scripts/with-test-db.sh <command>`.

Every entry point calls `ensure_schema()` on startup. `QUANTCORE_SCHEMA_MODE` decides what that
does:

| Mode | Behaviour |
|------|-----------|
| `create` | Run the full DDL (`_SCHEMA` in `quantcore/db.py`). |
| `warn` | Compare the live schema with `db/schema_snapshot.json` and log differences. No DDL. |
| `verify` | Like `warn`, but refuse to start on a missing or mismatched object. |
| `auto` *(default)* | `create` on a database Flyway doesn't manage yet (local, CI), otherwise `verify`. |

Seed your positions and the watchlist from their import files:

```bash
python scripts/import_portfolio.py --csv portfolio.csv --owner <you>
python scripts/import_watchlist.py --yaml watchlist.yaml   # full-sync replace
```

Moving from the old SQLite store? Use `scripts/migrate_sqlite_to_postgres.py`.

### Migrations (Flyway)

Versioned SQL lives in `db/migrations/V*.sql`. Every schema change touches exactly **three**
files, and CI fails if one is missing:

| File | What it is |
|------|------------|
| `db/migrations/V*.sql` | a **new** version. Never edit an applied one |
| `_SCHEMA` in `quantcore/db.py` | what `init_schema()` creates on a fresh database |
| `db/schema_snapshot.json` | the committed expectation (`python scripts/check_schema_snapshot.py --update`) |

`tests/test_schema_parity.py` builds one database from the migrations and another from `_SCHEMA`,
and fails unless they are identical.

`./scripts/flyway.sh info` shows the migration history. It defaults to test and asks for
confirmation before touching prod. `flyway info` only lists the migrations that have run; to see
what a database actually contains, run `python scripts/schema_check.py`. Deployed databases are
migrated by CI before each rollout. Changes are forward-fix only. Design and runbooks:
[`flyway-automation-plan.md`](docs/proposals/flyway-automation-plan.md),
[`db-roles-308-plan.md`](docs/proposals/db-roles-308-plan.md),
[`schema-ownership-plan.md`](docs/proposals/schema-ownership-plan.md).

---

## Running

### REST API

```bash
uvicorn api.main:app --host 127.0.0.1 --port 5001     # or: python -m api.main
curl http://127.0.0.1:5001/api/health
```

Interactive docs are served at `/docs`, and the spec at `/openapi.json`. **That spec is the
authoritative endpoint list.** There are 111 routes, so the table below only groups them.

| Router | Prefix | Covers |
|--------|--------|--------|
| `system.py` | `/api/health` | health check |
| `portfolio.py` | `/api/portfolio*`, `/api/watchlist*`, `/api/securities*` | positions and lots for the caller's identity: lot and sale notes, close preview, sales history, CSV import, delta exposure. Also the global shared watchlist (`GET /api/watchlist/fundamentals` reads only cached fundamentals) and security lookup |
| `prices.py` | `/api/securities/{ticker}/…` | OHLCV, price summary, technicals, VWAP (rolling, anchored, history), ATR bands, volume profile, candlesticks, gaps, drawdown |
| `options.py` | `/api/securities/{ticker}/options/…`, `/api/options/…` | chains and contracts, spread pricing, IV rank, flow, OI change, GEX, gamma-wall history, the watchlist screen |
| `fundamentals.py` | `/api/securities/…` | scores (single and batch, at most 25 symbols), growth, acceleration, earnings calendar, history, sector breakdown, cache stats. Collection reads take `?scope=all\|tracked` |
| `sentiment.py` | `/api/securities/…/news…`, `/api/sentiment/…` | news collection, per-symbol sentiment and trend, summaries |
| `microstructure.py` | `/api/securities/{ticker}/…` | short interest, dark-pool proxy, bid/ask spread |
| `recommendations.py` | `/api/securities/{ticker}/…` | trade recommendation, stop-loss analysis, relative strength, support confluence |
| `arbitrage.py` | `/api/arbitrage/…` | universe, pair analysis, scan, discovery, spread history |
| `plans.py`, `rungs.py`, `symbols.py`, `dashboard.py` | `/api/plans`, `/api/rungs`, `/api/symbols`, `/api/dashboard` | the Harvester |
| `settings.py` | `/api/settings` | per-user preferences, such as the Sidekick model |
| `chat.py`, `keyproxy.py` | `/api/chat`, `/api/keyproxy` | Sidekick chat turns and the BYOK public key |

Owner-scoped data is isolated in SQL. Another owner's plan or lot reads as not found.

### Front end (QuantUI)

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api to :5001
npm run build    # production bundle in frontend/dist/
```

The nav bar groups the pages into **My Positions** and **Research**, with **Settings** at the top
level. Pages are declared once, in `frontend/src/navigation.tsx`.

| Page | Route | Menu | What it shows |
|------|-------|------|---------------|
| Portfolio | `/` | My Positions | positions and lots, gain/loss, lot and sale notes, and a chip linking each holding to its harvest plan |
| Plans | `/plans` | My Positions | harvest plans with status. A plan can only exist for a symbol you hold |
| Harvester | `/harvester` | My Positions | ladder roll-ups: active plans, rungs hit, shares harvested |
| Securities | `/securities` | Research | per-symbol technical and fundamental views |
| Watchlist | `/watchlist` | Research | the shared watchlist ranked by fundamental score, with returns, USD market caps, staleness and tags |
| Fundamentals | `/fundamentals` | Research | the tracked universe ranked by score, cut by sector, with score movement and the earnings calendar |
| Arbitrage | `/arbitrage` | Research | scan results and per-pair factor breakdowns |
| Settings | `/settings` | top level | your BYOK key vault and Sidekick model |

The drill-down pages, `/plans/:id` and `/securities/:symbol`, are reached from those pages and
never from the nav bar. Every page carries the Sidekick chat rail.

`./runUI-MAC.sh` starts the database proxy, the API and the Vite dev server together.

### Local container stack

`docker-compose.yml` runs the backend as containers, mirroring the Cloud Run topology:

```text
AI clients ──HTTP──► stock-price :6001 │ options-analysis :6002 │ company-fundamentals :6003
                     news-sentiment :6004 │ market-analysis :6005 │ portfolio :6006
                     arbitrage :6007
                               │
                               ▼
                        quantcore-api :5001 ──► keyproxy :5002
                               │
                               ▼
                        cloud-sql-proxy ──► the TEST database instance
```

```bash
./runUI-CONTAINERS.sh up --build     # first run
./runUI-CONTAINERS.sh up -d          # detached
./runUI-CONTAINERS.sh logs -f quantcore-api
./runUI-CONTAINERS.sh down
```

Always use the launcher rather than plain `docker compose up`. It derives a git-ignored
`.env.docker` that points only at the test database, and it suppresses the default `.env`, so prod
settings can never reach the containers. It needs Google Application Default Credentials on the
host. Locally the API runs with authentication disabled, so every port is bound to `127.0.0.1`
only. The stack runs services only; the daily Jobs are not part of it.

| Image | Dockerfile | Role |
|-------|-----------|------|
| `quantcore-api` | `Dockerfile.api` | the REST tier. Carries the ML stack and baked FinBERT weights |
| `quantcore-news` | `Dockerfile.news` | the news Job. Same ML stack |
| `quantcore-mcp` | `Dockerfile.mcp` | one lean image reused by all seven MCP wrappers (`SERVER_MODULE`/`PORT`) |
| `quantcore-report` | `Dockerfile.report` | the daily Job (`main.py`). Lean |
| `quantcore-keyproxy` | `Dockerfile.keyproxy` | the BYOK credential-isolation service. No database |
| `quantcore-migrate` | `Dockerfile.migrate` | Flyway, run as a Job before each rollout |
| `quantcore-ui` | `Dockerfile.ui` | Express serving the SPA and proxying `/api` |

FinBERT weights are pinned and baked in at build time by `scripts/bake_finbert.py`, so no
container downloads a model at runtime.

---

## The daily Jobs

`main.py` runs as a Cloud Run Job after the close. It reads the configured owner's positions and
the shared watchlist from the database, and exits immediately on days the NYSE is closed. Otherwise
it runs these steps, in order:

1. price the positions and the watchlist
2. send Discord notifications
3. raise an alarm if the watchlist table is empty
4. the capture tail: capture full options chains, record GEX, then check capture health
5. raise an alarm if the news is stale
6. warm the fundamentals cache, then raise an alarm if coverage is stale

The cheap, high-value side effects land first. Every later stage is wrapped so it cannot fail a run
whose alerts already went out, and each one is backed by an alarm that re-reads what actually
landed in the database. Step budgets are clamped to the time left before the task deadline. An
unparseable tuning value falls back to its default, so a typo cannot silently disarm an alarm.

| Variable | Default |
|----------|---------|
| `REPORT_TASK_TIMEOUT_SECONDS` | 1800 |
| `OPTIONS_CAPTURE_BUDGET_SECONDS` | 600 |
| `GEX_RECORD_BUDGET_SECONDS` | 300 |
| `OPTIONS_CAPTURE_COVERAGE_FLOOR` | 0.90 |
| `OPTIONS_CAPTURE_FAILURE_CEILING` | 0.50 |
| `FUNDAMENTALS_WARM_BUDGET_SECONDS` | 900 (and it always stops 60 s before the deadline) |
| `FUNDAMENTALS_STALE_COVERAGE_FLOOR` | 0.80 |
| `FUNDAMENTALS_STALE_MAX_AGE_HOURS` | 168 |
| `NEWS_STALE_MAX_AGE_HOURS` | 120 |

**The news Job** (`news_job.py`) collects headlines for every tracked symbol and scores them with
FinBERT. It is a separate image because FinBERT needs torch, which the lean daily-job image must
not carry. It is tuned by `NEWS_TASK_TIMEOUT_SECONDS` (1800), `NEWS_COLLECT_BUDGET_SECONDS` (900),
`NEWS_FAILURE_CEILING` (0.50), `NEWS_EMPTY_CEILING` (0.90) and `NEWS_EMPTY_MIN_ATTEMPTS` (10).

Every alarm, the rationale for the order, and the FinBERT load path are in
[`docs/architecture/daily-jobs.md`](docs/architecture/daily-jobs.md).

### Legacy HTML report

`scripts/generate_portfolio_report.py [--output path] [--publish]` renders a standalone HTML report
with matplotlib and Jinja2. With `--publish` it uploads the report to S3, which needs
`BUCKET_NAME`/`BUCKET_KEY`; without them it exits non-zero. The report covers:

- a portfolio summary and per-stock performance
- moving averages and returns
- embedded charts
- a watchlist section with earnings dates
- a generation timestamp such as "2026-10-08 at 5:15pm"

It runs on any always-on host (for example a Raspberry Pi with a 64-bit OS, via `runOnPi.sh`),
never in a container, and **never alongside `main.py`**, which would double every alert.
`html_summary.py` and `simple_text_summary.py` are older standalone scripts and are not part of it.

---

## Deployment

The REST tier, the seven MCP wrappers, the key proxy and QuantUI run as Cloud Run services; the
daily, news and migration tasks run as Cloud Run Jobs. There are two GCP projects. **Prod is the
system of record for all analysis**, and **test** is for development and CI.

- Merging to `main` builds every image, migrates the test database, and rolls out to test.
- Prod is promoted only by manually dispatching the prod-rollout workflow with a commit SHA. It
  copies the tested images **by digest**, migrates prod, then rolls out.
- An unmerged branch can be tried on test by dispatching the deploy workflow from `main` with a
  `ref` input. A ref that changes migrations is refused. Details:
  [`deploy-ref-to-test-plan.md`](docs/proposals/deploy-ref-to-test-plan.md).

QuantUI sits behind Identity-Aware Proxy. Its Express server verifies the IAP assertion and mints a
short-lived, per-user ES256 JWT server-side, so the browser never holds a bearer token. Serving
model and auth: [`docs/architecture/quantui.md`](docs/architecture/quantui.md). Service
configuration and onboarding a new service:
[`docs/architecture/cloudrun-services.md`](docs/architecture/cloudrun-services.md). Promotion
runbook: [`docs/operations/prod-promotion.md`](docs/operations/prod-promotion.md).

**Access** to QuantUI, to the prod MCP servers, and to the database is granted by a project owner.
See [`docs/operations/team-access.md`](docs/operations/team-access.md).

## Sidekick and BYOK

The Sidekick chat runs on **each user's own Anthropic API key**. The backend is built so that it
never holds a usable key at rest.

```text
Browser vault (IndexedDB, passphrase-encrypted key)
   │  per turn: single-use envelope (ECDH → AES-GCM to the key proxy's pinned public key)
   ▼
Express ── per-user ES256 JWT ──► REST tier (/api/chat)
                                      │  envelope forwarded, never decrypted here
                                      ▼
                                  key proxy ── the only process that ever sees the plaintext key
                                      │
                                      ▼
                                  Anthropic API (streamed back)
```

- The key is stored in the browser, encrypted with a key derived from the user's passphrase
  (PBKDF2 + AES-GCM). It is managed from **Settings → API Keys**.
- Each turn seals the key into an envelope bound to the user's identity and the request scope. The
  envelope is single-use, so replays and cross-user swaps are rejected.
- The key proxy has no database and is not publicly invocable. It runs as a dedicated
  service account with no project roles, and it enforces scopes and token budgets.
- Users choose a chat model in Settings or from the chat header. The choices are
  `claude-sonnet-5` (the default), `claude-opus-4-8` and `claude-fable-5`, from
  `quantcore/chat_models.py`.

Design: [`docs/architecture/byok.md`](docs/architecture/byok.md).

## MCP servers

Seven FastMCP servers in `fastMCPTest/` carry **63 tools**. Count them with the anchored
`grep -c "^@mcp.tool" fastMCPTest/*.py`; the unanchored form also counts a mention inside a
docstring.

| Server | Tools | Covers |
|--------|------:|--------|
| `stock-price-server` | 21 | price, news, technicals (RSI, MACD, stochastic, OBV, VWAP, candlesticks, gaps, higher lows), ATR bands, anchored VWAP, volume profile, support confluence, drawdown and stop-loss, VWAP and relative-strength history, `get_trade_recommendation` |
| `company-fundamentals-server` | 12 | earnings calendar, fundamental score (single and batch), revenue growth, earnings acceleration, full profile, top stocks, upcoming earnings, sector breakdown, score changes, history, cache stats |
| `options-analysis-server` | 11 | the watchlist screen, a per-symbol analysis, contract lookup, spread pricing, full chain, unusual calls, delta-adjusted OI, gamma-wall history, OI change, GEX profile, health check |
| `portfolio-server` | 7 | the caller's portfolio, lots and sales (read-only), a summary, the shared watchlist (list and add; removal is UI-only), health check |
| `arbitrage-server` | 5 | universe, pair analysis, scan, cointegration discovery, health check |
| `news-sentiment-server` | 4 | news collection, sentiment, sentiment trend, covered symbols |
| `market-analysis-server` | 3 | short interest, dark-pool proxy, bid/ask spread |

A few behaviours worth knowing:

- **`analyze_options_watchlist` is cache-first.** It scores the watchlist from the daily options
  capture and other cached inputs, and never calls the data provider. Symbols without a capture
  from the last trading day are listed as `stale`. Run `analyze_options_symbol` (which is live) on
  those, and re-price any trade live before acting on it.
- **`get_trade_recommendation`** scores 13 signals across price structure, momentum, volume and
  flow, microstructure and options positioning. The net score maps to a trade type, sized from a
  2% risk budget. See [`docs/Get Trade Recommendations.md`](docs/Get%20Trade%20Recommendations.md).
- **The fundamentals tools** read and write a TTL cache (`FUNDAMENTALS_CACHE_TTL_HOURS`, default
  24). The cross-symbol tools read the cache only, so they make no network calls. See
  [`docs/FUNDAMENTALS_CACHE_IMPLEMENTATION.md`](docs/FUNDAMENTALS_CACHE_IMPLEMENTATION.md).
- **Batch requests are bounded.** A fundamentals batch takes at most 25 unique symbols; split
  larger universes across calls.

### Connecting an AI client

`.mcp.json` configures two sets of entries:

- **remote HTTP entries** for the deployed wrappers, each sending
  `Authorization: Bearer ${QUANTCORE_MCP_TOKEN}`
- **`-local` entries** for wrappers you run yourself on ports 6001–6007

The wrappers forward the bearer to the REST tier, so using the remote set needs a personal JWT in
`QUANTCORE_MCP_TOKEN`. Without one, every data tool returns a 401. A project owner grants the
access needed to mint one; the recipe is in
[`docs/operations/team-access.md`](docs/operations/team-access.md).

To run a wrapper locally, use `fastmcp run fastMCPTest/stock_price_server.py`, or run the file
directly. It binds `127.0.0.1` by default; set `MCP_HOST` to bind elsewhere. In a container,
`mcp_gateway/serve.py` binds `0.0.0.0`.

### Timeouts and smoke tests

- Deployed wrappers use a 900 s request timeout, so long MCP sessions survive.
- Each upstream REST call is bounded by `QUANTCORE_REST_TIMEOUT` (60 s by default).
- The launcher sends protocol pings every 30 s, so a proxy doesn't mistake an active session for
  an idle one.

There are two smoke tests for a deployed wrapper:

- `scripts/mcp_http_smoke.py` checks one endpoint and one tool.
- `scripts/mcp_live_smoke.py` checks every read-only tool on every **test** wrapper. It refuses
  prod, reads a test JWT from `QUANTCORE_TEST_MCP_TOKEN`, and can be narrowed with `--wrapper` or
  `--tool`.

Both print only metadata, never response payloads.

### Adding a wrapper

Add four things:

- the module under `fastMCPTest/`
- an entry in `WRAPPERS` in `scripts/ci_wrapper_smoke.py`
- a contract case per tool in `scripts/mcp_tool_cases.py`
- a `[[services]]` block in `deploy/cloudrun-services.toml`

CI deliberately cannot make a service public, so a project owner creates a new wrapper by hand
once. See [`docs/architecture/cloudrun-services.md`](docs/architecture/cloudrun-services.md).

---

## Harvester

A "harvest ladder" for selling shares into strength. It computes a volatility-based harvest
threshold per symbol and builds a forward ladder of price targets ("rungs"). Each rung moves from
*planned* to *achieved* to *executed*.

The algorithm and its backtests are in `experiments/HarvesterExperiment.py`, and the service is
`HarvesterService`. Plans belong to an owner, and isolation is enforced in SQL. A plan can only
exist while the owner holds the shares, so it closes automatically when the position goes flat.
The daily Job alerts on any rung hit. Details:
[`docs/architecture/harvester.md`](docs/architecture/harvester.md).

## Arbitrage scanner

The scanner looks for securities stretched against a structurally linked underlying. There are
three families:

- **nav_vehicle** — the only family with a computable fair value
- **commodity_etf** — a fund against its reference future
- **producer** — a miner or E&P against its commodity

The curated links are in `arb_universe.yaml`, and a discovery sweep looks for undeclared
cointegrated pairs.

**The scoring is deliberately inverted: spread width only qualifies a candidate, and the
convergence mechanism ranks it.** The score is the product of seven named factors (opportunity,
evidence, convergence, hedge, carry, trend and freshness). Each one is returned together with its
reasons and its "breaks on" conditions. Expect most scans to return nothing above `watch`. That is
intended.

Design: [`docs/architecture/arbitrage.md`](docs/architecture/arbitrage.md). Usage and worked
examples: [`docs/arbitrage-scanner-usage.md`](docs/arbitrage-scanner-usage.md).

---

## Testing

```bash
python -m unittest discover -s tests -t . --durations 25          # backend
python -m unittest tests.test_money                               # one module
coverage run -m unittest discover -s tests -t . --durations 25 && coverage report
cd frontend && npx vitest run --coverage                          # front end
```

`tests/__init__.py` swaps in a test database before anything connects, and makes any real data
provider request fail fast. It picks the database from `.env`:

1. `QUANTCORE_UNITTEST_DB_DSN`, a local Postgres (recommended)
2. otherwise, `QUANTCORE_TEST_DB_DSN`

A local database runs the suite in about a minute; a remote database through the proxy takes far
longer. Setup takes about five minutes and is in
[`docs/local-unit-test-db.md`](docs/local-unit-test-db.md).

The suite holds an advisory lock on its database for the whole run. A second run against the same
database waits instead of deleting the first run's rows
([details](docs/local-unit-test-db.md#running-two-suites-at-once)). Coverage floors on both sides
only move upward.

## Documentation map

| Where | What |
|-------|------|
| [`docs/architecture/`](docs/architecture/) | the detail and the *why*: [data](docs/architecture/data.md), [daily jobs](docs/architecture/daily-jobs.md), [QuantUI](docs/architecture/quantui.md), [BYOK](docs/architecture/byok.md), [Cloud Run services](docs/architecture/cloudrun-services.md), [Harvester](docs/architecture/harvester.md), [arbitrage](docs/architecture/arbitrage.md), [history](docs/architecture/history.md) |
| [`docs/operations/`](docs/operations/) | runbooks: [team access](docs/operations/team-access.md), [prod promotion](docs/operations/prod-promotion.md), [service tokens](docs/operations/prod-jwt-tokens.md) |
| [`docs/capabilities-matrix.md`](docs/capabilities-matrix.md) | which capability is reachable from REST, MCP and the UI |
| [`docs/proposals/`](docs/proposals/) | design plans, each with a checkpoint log recorded as the work landed, including the gotchas |
| [`CLAUDE.md`](CLAUDE.md), [`AGENTS.md`](AGENTS.md) | guidance and non-negotiable rules for AI coding agents working in this repo |

## Roadmap

These are plans, not commitments:

- connecting a brokerage paper account (Alpaca) for tracking and simulated execution
- Cloud Run observability: dashboards and alerting on the services and Jobs
- event-risk MCP tools covering earnings, macro calendars and corporate actions
- agentic market intelligence: scheduled, multi-tool research runs that report back

## License

[MIT](LICENSE).
