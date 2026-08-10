# Stock Portfolio Manager

A Python-based stock portfolio tracker with real-time price updates, multi-currency support, and HTML report generation capabilities.

## Features

- Track portfolio positions with per-lot purchase information (price, date, quantity), stored in
  the database per owner and editable from the UI (`portfolio.csv` is the import format)
- Tracks a shared watchlist including per-stock 'tags', stored in the database and editable from the UI (watchlist.yaml is the import format)
- Fetch real-time stock prices via Yahoo Finance API
- Calculate gain/loss for individual stocks and total portfolio
- Support for multiple currencies with real-time conversion
- Generate HTML reports with portfolio performance metrics
- Calculate portfolio performance statistics
- Technical, fundamental, options, and sentiment analytics served over a REST API and to AI
  agents through seven **MCP servers**
- An **arbitrage scanner** for securities that have stretched against a structurally linked
  underlying (NAV vehicles, commodity ETFs, producers)
- Web dashboard (**QuantUI**) with an AI chat **Sidekick** — each user brings their own Anthropic
  API key (**BYOK**), stored encrypted in the browser and never visible to the backend (see
  [BYOK Sidekick](#byok-sidekick-bring-your-own-llm-api-key))

## Technologies Used

- Python 3.12 (the version every container image and CI job runs)
- pandas / numpy - Data manipulation and analysis
- yfinance - Yahoo Finance API integration
- PostgreSQL via psycopg2 - the unified **QuantCore** database
- FastAPI + Pydantic - the REST tier (`api/`)
- FastMCP - the MCP servers that expose analysis to AI agents (`fastMCPTest/`)
- React 19 + TypeScript + Vite + MUI - the QuantUI front end (`frontend/`)
- Jinja2 + matplotlib - HTML report rendering
- Docker / Google Cloud Run - how everything is deployed

## Example Stocks

This example uses Apple Inc (AAPL) and Alphabet Inc Class C (GOOG) stocks to demonstrate functionality.

### Example Stock Data

```csv
name,symbol,purchase_price,quantity,purchase_date,currency,sale_price,sale_date,current_price
Apple Inc,AAPL,150.82,10,2023-06-15,USD,,,
Alphabet Inc Class C,GOOG,125.23,5,2023-07-21,USD,,,
```

### Example Output

```
Stock Portfolio Report created on 2025-06-27 at 8:33pm

Portfolio Summary
Total Investment: $49,236.00
Total Current Value: $60,269.00
Total Gain/Loss: $11,033.00
Total Gain/Loss %: 22.41%

Individual Stock Details
Name	             Symbol	Purchase Price	 Current Price	Quantity	Gain/Loss	Gain/Loss %
Apple Inc	         AAPL	$169.82	         $201.10	    100	        $3128.00	18.42%
Alphabet Inc Class C GOOG	$153.57	         $178.38	    100	        $2481.00	16.16%
Amazon.com Inc	     AMZN	$168.97	         $223.21	    100	        $5424.00	32.10%

```

### Watchlist Files
The watchlist lives in the database (the global `watchlist` table — one shared list, no
per-owner copies). Add and remove symbols from the QuantUI Securities page, or over the REST
tier (`GET/POST /api/watchlist`, `DELETE /api/watchlist/{ticker}`).

`GET /api/watchlist/fundamentals` returns the whole list with price returns (5d/30d/60d/YTD/1y)
and its cached fundamental scores in one request — the data the nightly
`generate_watchlist_fundamentals_report.py` HTML used to produce. It reads cache only and never
calls Yahoo, so it answers in well under a second. Fundamentals older than the cache TTL are
returned **labelled** (`fundamentals_stale`, `fundamentals_age_hours`) rather than dropped, and a
symbol the cache has never scored comes back with null fundamentals instead of zeros. Market caps
carry their own currency: `market_cap` is the native figure and only `market_cap_usd` — populated
just for USD-denominated names for now — is comparable across securities.

`watchlist.yaml` is the **import format** — nothing reads it at runtime. Seed or re-seed the
table from it with:

```bash
python scripts/import_watchlist.py --yaml watchlist.yaml
```

That is a full-sync replace: the table ends up matching the file exactly. YAML file entries:
~~~
- name: Example Corp
  symbol: EXMPL
  currency: USD
  tags:
    - ai
    - cloud
~~~

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/JohnFunkCode/stock-portfolio-manager.git
   cd stock-portfolio-manager
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

   That installs everything, as it always has. The file is now layered —
   `requirements-base.txt` (lean: what the containers install) + `requirements-ml.txt`
   (torch/transformers for FinBERT) + `requirements-report.txt` (matplotlib, jinja2, boto3) — so
   that the deployed images stop carrying the HTML report's rendering stack. If you deliberately
   install only the base set, note that `scripts/generate_portfolio_report.py` will not run.

3. Point `QUANTCORE_DB_DSN` at a PostgreSQL database (see [Database](#database)) — the schema is
   created on first start.

4. Seed your holdings. Positions live in the database; `portfolio.csv` (format shown above) is the
   import format:
   ```bash
   python scripts/import_portfolio.py --csv portfolio.csv --owner <you>
   ```
   That is a full-sync replace for that owner. You can also add positions directly in the QuantUI
   Portfolio page or over `POST /api/portfolio`.

## Configuration

### Database

The application uses a unified **PostgreSQL** database (codename **QuantCore**, accessed via `psycopg2`) to store:
- Portfolio holdings and historical OHLCV data (daily, intraday)
- Options chain snapshots, Greeks, and gamma wall history
- News articles and sentiment analysis
- Fundamental metrics and earnings dates
- Harvester plan instances and alert logs

**The schema is automatically created** when any application component starts (main.py, REST API, or MCP servers) on a database nothing else manages — local dev, CI, compose, a brand-new instance. Where a Flyway ledger exists (test and prod), startup instead **checks** the schema and refuses to start on drift; see `QUANTCORE_SCHEMA_MODE` below and [Migrations](#migrations-flyway). The PostgreSQL server, database, and `quantcore` user must already exist; point the app at any reachable PostgreSQL instance via the DSN below — a local server, or a managed service such as Cloud SQL connected through the [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy) (which exposes the remote database as a local `host:port`, so no code changes are needed to switch targets).

**Environment Variables:**
- `QUANTCORE_DB_DSN` — PostgreSQL connection string for the unified database, e.g. `postgresql://<user>:<password>@<host>:<port>/<database>`
- `QUANTCORE_TEST_DB_DSN` — optional DSN for an isolated database, used to run the app or test suite against a separate copy of the data without touching the primary database
- `QUANTCORE_SCHEMA_MODE` — what startup does about the schema: `create` (run the DDL), `warn` (check it and log differences), `verify` (check it and refuse to start on a missing or mismatched object), or `auto` (the default: create where no Flyway ledger exists, otherwise verify). See [Migrations](#migrations-flyway) below
- `DISCORD_WEBHOOK_URL` — Discord webhook for price alerts (optional)
- `BUCKET_NAME` / `BUCKET_KEY` — AWS S3 credentials for report uploads (optional)

**Migrations (Flyway):** versioned SQL lives in `db/migrations/V*.sql`. `db/flyway.conf`
deliberately holds **no credentials**, so use the wrapper — it derives the JDBC URL and login
from the DSNs in `.env`, defaults to the **test** database, echoes the target host before
running, and asks for confirmation before a prod `migrate`:

```bash
./scripts/flyway.sh info               # test (default)
./scripts/flyway.sh --prod info
./scripts/flyway.sh --prod migrate     # prompts before writing
```

Every schema change touches **three** files, and CI fails if you miss one:

| File | What it is |
|---|---|
| `db/migrations/V*.sql` | a **new** version — never an edit to a migration that has already been applied |
| `_SCHEMA` in `quantcore/db.py` | the DDL `init_schema()` runs where nothing else owns the schema |
| `db/schema_snapshot.json` | the committed expectation — regenerate with `python scripts/check_schema_snapshot.py --update` |

The first two are checked against each other by `tests/test_schema_parity.py`: it builds one
throwaway database from `init_schema()` and another from `db/baseline/V1__*.sql` plus every
migration, and fails with the full list of differing tables, columns, indexes, and constraints
unless the two are identical. A change that ships to only one owner cannot merge, and in CI that
check can never quietly skip. The third is checked by `scripts/check_schema_snapshot.py` in the
`gate` job.

Historically `init_schema()` ran on every application startup, so a deployed database had usually
already reached the right *shape* before Flyway saw it — which is why pure-DDL migrations report
"already exists, skipping" and why **`flyway info` is a changelog view, not evidence of what a
deployed database actually contains**. Run `python scripts/schema_check.py --prod` for that (it
connects read-only and diffs the live schema against the committed `db/schema_snapshot.json`).
`QUANTCORE_SCHEMA_MODE` (above) is what ends the race: where a Flyway ledger exists, startup now
checks the schema instead of creating it, leaving Flyway the sole owner of the DDL.
That two-owners-of-the-schema problem was
[issue #165](https://github.com/JohnFunkCode/StockPortfolioManager/issues/165) (plan:
[`docs/proposals/schema-ownership-plan.md`](docs/proposals/schema-ownership-plan.md)).

**Migrations are load-bearing — this changes how you ship a schema change.** Startup no longer
creates anything on test or prod, so:

1. **Migrate first, deploy second — in both projects.** `./scripts/flyway.sh migrate` before the
   merge to `main` (`deploy.yml` auto-rolls test), and `./scripts/flyway.sh --prod migrate` before
   dispatching `prod-rollout.yml`. Nothing else will create the object for you.
2. **The migration must be complete DDL.** A forgotten column used to be invisible, because
   `init_schema()` created it at startup anyway. Now it is a `MISSING`/`MISMATCH` line and it
   fails the deploy.
3. **Failing early is the point.** Startup raises `SchemaDriftError`, so the new Cloud Run
   revision never passes its health check and never takes traffic — the previous revision keeps
   serving. Drift surfaces at deploy time instead of hours later as query errors on live traffic.
4. **If you need the old behaviour back**, it is one command and no code change:
   ```bash
   gcloud run services update quantcore-api --project quantcore-prod-20260606 --region us-central1 --update-env-vars QUANTCORE_SCHEMA_MODE=create
   ```
   Always `--update-env-vars`, never `--set-env-vars` — the latter replaces the whole variable set
   and has broken prod before.

**Migrating from a legacy SQLite database:** if you have an existing `quantcore.sqlite` file, `scripts/migrate_sqlite_to_postgres.py` performs a one-shot copy into PostgreSQL — it initializes the schema, migrates all tables in foreign-key-safe order using batched inserts, resets primary-key sequences, and verifies row counts:
```bash
python scripts/migrate_sqlite_to_postgres.py --sqlite data/quantcore.sqlite --dsn "$QUANTCORE_DB_DSN"
```

### Environments (prod vs test)

There are two GCP environments, and the rule is simple:

| Environment | Project | Role |
|-------------|---------|------|
| **Prod** | `quantcore-prod-20260606` | **System of record.** All users and all analysis run against prod. |
| **Test** | `quantcore-test-20260606` | **Development and CI only** — never the place to do real analysis. |

Now that changes ship through a CI/CD pipeline (`deploy.yml` → test, `prod-rollout.yml` → prod),
prod is the live system everyone reads from. The deployed `quantui` UI and the `.mcp.json` AI-client
remotes both already target prod; connecting an AI client just needs a prod token (see
[Connecting AI clients to prod](#connecting-ai-clients-to-prod-mcp-token)). Reserve test for
developing and validating changes before they're promoted.

## Usage

### The daily job

`main.py` is the daily job (deployed as the `quantcore-report` Cloud Run Job). It prices John's
positions and the shared watchlist, then sends the Discord notifications, captures the day's
options chains, and warms the fundamentals cache:

```bash
python main.py
```

The fundamentals warming pass runs last, oldest-cache-entry first, under a wall-clock budget, and
can never fail the run — three env vars tune it, and an alarm fires to Discord if the cache falls
behind anyway:

| Env var | Default | Meaning |
|---|---|---|
| `FUNDAMENTALS_WARM_BUDGET_SECONDS` | `900` | wall-clock budget for the warming pass |
| `FUNDAMENTALS_STALE_COVERAGE_FLOOR` | `0.80` | alarm below this in-TTL fraction |
| `FUNDAMENTALS_STALE_MAX_AGE_HOURS` | `168` | alarm above this oldest cache age |

### The legacy HTML report

Since issue #147 the HTML report is its own script, not part of `main.py`:

```bash
python scripts/generate_portfolio_report.py                    # writes portfolio_report.html
python scripts/generate_portfolio_report.py --output /tmp/r.html
python scripts/generate_portfolio_report.py --publish          # uploads to S3 instead
```

`--publish` needs `BUCKET_NAME` and `BUCKET_KEY`; without them it exits non-zero rather than
quietly reporting success.

**Running it from the Raspberry Pi** (`runOnPi.sh`) has four one-time prerequisites: a
`QUANTCORE_DB_DSN` in `.env`, a running Cloud SQL Auth Proxy, AWS credentials plus
`BUCKET_NAME`/`BUCKET_KEY`, and `pip install -r requirements.txt` (the base set is not enough —
the rendering stack lives in `requirements-report.txt`). The Pi deliberately runs *this script*
and not `main.py`: the Cloud Run Job already sends the notifications and writes the options
snapshots, so running `main.py` there too would double every alert.

## Project Structure

Layered per [`docs/proposals/architectural-standard-v2.md`](docs/proposals/architectural-standard-v2.md):
business logic lives in the services layer, and the REST routes and MCP tool bodies are thin
adapters exactly one service call deep.

| Path | What's in it |
|------|--------------|
| `quantcore/` | The core. `db.py` (connection factory + `init_schema()`), `gateways/` (yfinance, Polygon, Anthropic, keyproxy), `repositories/` (SQL only), `analytics/` (pure functions — indicators, options math, pairs, NAV, volume profile), `services/` (the business logic), `services/registry.py` (the composition root) |
| `api/` | FastAPI REST tier — app factory `api/main.py`, route groups under `routers/`, Pydantic schemas under `schemas/`, JWT verification in `auth.py` |
| `frontend/` | React 19 + TypeScript + Vite SPA (**QuantUI**), including the Sidekick chat and the browser BYOK vault; `server/` is the Express host used by the deployed container |
| `fastMCPTest/` | The seven FastMCP servers — thin HTTP gateway wrappers over the REST tier |
| `mcp_gateway/` | `rest_client.py`, the single seam every MCP wrapper calls through (read verbs only — no `delete`) |
| `keyproxy/` | Standalone BYOK credential-isolation service; decrypts envelopes in memory, holds no database |
| `portfolio/` | Legacy domain layer retained for the daily job and the report script (`stock.py`, `money.py`, `metrics.py`, `portfolio.py`, `watch_list.py`, `yfinance_gateway.py`) |
| `main.py`, `notifier.py` | The daily job: price, Discord-notify, capture options chains, warm the fundamentals cache |
| `scripts/generate_portfolio_report.py`, `templates/`, `html_summary.py`, `simple_text_summary.py` | The legacy HTML report — chart, render, upload. Run from the Pi, never in a container |
| `experiments/` | `HarvesterExperiment.py` (harvest-ladder algorithm and backtests) plus standalone spread monitors |
| `db/` | `flyway.conf` + versioned migrations under `db/migrations/` |
| `scripts/` | Operational scripts — importers, `flyway.sh`, `mint_prod_jwt.py`, IAP/WIF setup, the SQLite→Postgres migration |
| `tests/` | Backend test suites (`python -m unittest discover -s tests -t .`); front-end tests live beside the code in `frontend/src` |
| `docs/` | Design proposals, plans with their checkpoint logs, and analysis write-ups |
| `Dockerfile.*`, `docker-compose.yml`, `cloudbuild.yaml`, `.github/workflows/` | Images, the local container stack, and CI/CD |
| `portfolio.csv`, `watchlist.yaml`, `arb_universe.yaml` | Import / curation files — none of them are read at runtime except `arb_universe.yaml` |

## REST API (`api/`)

The FastAPI front door for everything: it runs the services in-process and is the *only* thing that
talks to the database. The React front end and all seven MCP wrappers reach the system through it.
Interactive OpenAPI docs are served at `/docs` and the spec at `/openapi.json` — **that spec is the
authoritative endpoint list**; the ~106 routes are too many to mirror here without going stale.

**Entry point:** `api/main.py` (the FastAPI `app`)

### Route groups

| Router | Prefix | What it covers |
|--------|--------|----------------|
| `system.py` | `/api/health` | Health check — confirms the API and PostgreSQL database are reachable |
| `portfolio.py` | `/api/portfolio*`, `/api/watchlist*`, `/api/securities` | Positions and lots (`?owner=`, defaults to `john`), CSV import, and the **global** shared watchlist (no `?owner=`) |
| `prices.py` | `/api/securities/{ticker}/…` | OHLCV, technicals and technical/risk signals, RSI, MACD, stochastic, volume + OBV, ATR bands, volume profile, VWAP (+ anchored, + history), candlesticks, higher lows, gaps, drawdown |
| `options.py` | `/api/securities/{ticker}/options/…`, `/api/options/…` | Chain and contract lookup, exact vertical-spread pricing, IV rank, unusual calls, OI change, GEX profile, gamma-wall history, delta exposure, watchlist screening |
| `fundamentals.py` | `/api/securities/…` | Fundamental score (+ batch, + changes), revenue growth, earnings acceleration, earnings calendar, history, sector breakdown, cache stats |
| `sentiment.py` | `/api/securities/…/news…` | News collection, per-symbol sentiment, sentiment trend |
| `microstructure.py` | `/api/securities/{ticker}/…` | Short interest, dark-pool proxy, bid/ask spread |
| `recommendations.py` | `/api/securities/{ticker}/…` | `get_trade_recommendation`, stop-loss analysis, relative strength (+ history), support confluence |
| `arbitrage.py` | `/api/arbitrage/…` | Curated universe, pair analysis, scan, cointegration discovery, spread/premium history |
| `plans.py`, `rungs.py`, `symbols.py`, `dashboard.py` | `/api/plans`, `/api/rungs`, `/api/symbols`, `/api/dashboard` | The Harvester: plans and their rungs (create, supersede, mark achieved, record executions), plan symbols, dashboard roll-ups |
| `settings.py` | `/api/settings` | Per-owner UI preferences (currently the Sidekick chat model) |
| `chat.py`, `keyproxy.py` | `/api/chat`, `/api/keyproxy` | Sidekick chat turns and the BYOK public key / envelope validation (see [BYOK Sidekick](#byok-sidekick-bring-your-own-llm-api-key)) |

### Starting the API server

```bash
# From the project root, with the virtualenv active:
source .venv/bin/activate
uvicorn api.main:app --host 127.0.0.1 --port 5001
# or, equivalently:
python -m api.main
```

The server starts on `http://127.0.0.1:5001`. CORS is enabled for all origins on `/api/*` routes so the React dev server can connect without a proxy.

---

## React Frontend (`frontend/`)

**QuantUI** — a portfolio and market-analysis dashboard built with React 19, TypeScript, Vite, and
Material UI. It communicates exclusively with the FastAPI service above.

### Pages

| Page | Route | Description |
|------|-------|-------------|
| Portfolio | `/` | Your positions and lots — value, gain/loss, and per-lot detail |
| Harvester | `/harvester` | Summary stats: total active plans, rungs hit, shares harvested, and estimated proceeds |
| Securities | `/securities` | Securities dashboard with per-symbol technical/fundamental views; add/remove watchlist symbols here |
| Security Detail | `/securities/:symbol` | Deep-dive charts and analytics for one symbol, including the Technical Analysis tab's Support Confluence card |
| Arbitrage | `/arbitrage` | The arbitrage scanner — universe, scan results, and per-pair factor breakdowns |
| Plans | `/plans` | Table of all harvest plans with status badges; create or delete plans |
| Plan Detail | `/plans/:id` | Full rung ladder for a plan; mark rungs as achieved or record executions |
| Symbols | `/symbols` | Look up the latest live price for any ticker symbol |
| Settings | `/settings` | Manage your BYOK API key (vault unlock, rotate, remove) and pick your Sidekick chat model |

Every page also carries the **Sidekick** chat rail — an AI assistant powered by the user's own
Anthropic API key (see [BYOK Sidekick](#byok-sidekick-bring-your-own-llm-api-key)).

### Key dependencies

- **React Router v7** — client-side navigation
- **TanStack Query v5** — data fetching, caching, and background refresh
- **MUI v6** (Material UI + MUI X Data Grid) — UI components and data tables

### Starting the frontend dev server

```bash
# From the frontend/ directory:
cd frontend
npm install        # first time only
npm run dev
```

The dev server starts on `http://localhost:5173` and hot-reloads on file changes. It expects the FastAPI service to be running on `http://127.0.0.1:5001` (the Vite dev server proxies `/api/*` to that port).

To build a production bundle:

```bash
cd frontend
npm run build      # output goes to frontend/dist/
```

---

## QuantUI on Cloud Run (behind IAP)

The same React app is deployed as a hosted service (**QuantUI**) so the team can reach the real UI
from anywhere, gated by their Google accounts via **Identity-Aware Proxy (IAP)** — no auth code in
the app. It runs in both projects:

| Environment | URL | Project | Auto-deploy? |
|-------------|-----|---------|--------------|
| **Test** | https://quantui-493357101423.us-central1.run.app | `quantcore-test-20260606` | **Yes** — every push to `main` |
| **Prod** | https://quantui-127961694257.us-central1.run.app | `quantcore-prod-20260606` | **No** — manual promotion only |

### How it's served

The SPA is *not* served by Vite in production. `Dockerfile.ui` builds `frontend/dist/` and runs a
tiny Express server (`frontend/server/server.mjs`) that:

- serves the static `dist/` bundle (with SPA `index.html` fallback), and
- reverse-proxies `/api/*` to `quantcore-api`, attaching a **per-user token** server-side: it
  verifies the Google-signed IAP assertion on each request, then mints a short-lived (15-min)
  **ES256 JWT** with `sub` = the signed-in user's email (`frontend/server/auth.mjs`, signing key
  in the `quantui-signing-key` secret; `quantcore-api` and the keyproxy verify with the public
  half).

So the browser stays same-origin (no CORS) and no bearer token **ever reaches the client** — the
production equivalent of the Vite dev proxy. IAP gates *who can load the UI*; the minted per-user
JWT authenticates the UI→API hop and carries the user's identity all the way to the backend (the
BYOK keyproxy binds key envelopes to it). The serving fallback ladder is keyed on configuration:
per-user mint when `QUANTUI_SIGNING_KEY` is set → legacy static `quantui-api-token` secret →
open (docker-compose, where the api runs `AUTH_DISABLED=1`). Each project has its own signing
key/secret pair and its own custom OAuth client.

### Workflow for a UI change

1. **Edit** under `frontend/` and open a PR against `main`.
2. **Merge to `main`.** This triggers `.github/workflows/deploy.yml` (no path filters, so any push to
   `main` qualifies). The `gate` job runs tests + smoke; then `cloudbuild.yaml`'s `build-ui` step
   builds `quantcore-ui:<sha>` and the **Deploy quantui** step image-only-rolls it onto the **test**
   service (IAP + secret + `QUANTCORE_REST_URL` config is preserved across redeploys).
3. **Verify on test** — open the test URL above in the browser, confirm the data grids populate.
4. **Promote to prod** — run the **`prod-rollout`** GitHub Action (`workflow_dispatch`) with that
   commit's 7-char SHA as `image_tag`. It copies the validated image **by digest** test→prod and
   image-only-deploys the prod `quantui` service. Prod is **never** auto-deployed; it requires this
   manual, reviewer-gated dispatch.

> Note: because `deploy.yml` has no path filters, *any* push to `main` (not just `frontend/` changes)
> rebuilds and redeploys all services, including a fresh `quantui` revision. Harmless, just expect the
> revision counter to climb on every merge.

### Granting a new user access

While the OAuth consent screen is in **Testing** status, an account needs to be on **two** lists for
login to succeed:

1. **OAuth consent test user** — Console → APIs & Services → OAuth consent screen → **Audience** →
   *Add users* (add the account in the relevant project).
2. **IAP accessor role** — `roles/iap.httpsResourceAccessor` on the `quantui` service.

Add the email to the `USERS=( … )` array in `scripts/grant_quantui_iap_access.sh`, then run it per
project (defaults to test; pass the prod project to grant there):

```bash
./scripts/grant_quantui_iap_access.sh                          # test
./scripts/grant_quantui_iap_access.sh quantcore-prod-20260606  # prod
```

Both the Audience entry **and** the IAM grant must be present — having only one results in a blocked
login. (One-time per project: attaching the custom OAuth client is done with
`scripts/attach_quantui_iap_oauth.sh`.)

> **Full onboarding is now two things:** (a) **UI access** via the IAP grant above, **and** (b) an
> AI-client **prod MCP token** so their Claude/agent can call the analysis tools — have them mint
> their own as described in
> [Connecting AI clients to prod](#connecting-ai-clients-to-prod-mcp-token). Remind them the token
> expires after **90 days** and should be rotated quarterly (re-run the mint command).

### Running the serving container locally

```bash
docker build -f Dockerfile.ui -t quantui:dev .
docker run --rm -p 8080:8080 \
  -e QUANTCORE_REST_URL=http://host.docker.internal:5001 \
  -e PORT=8080 \
  quantui:dev
# → UI at http://localhost:8080, proxying /api/* to a local uvicorn api.main:app
```

The `docker-compose.yml` stack also includes a `quantui` service for full local parity (no token,
since the compose api runs `AUTH_DISABLED=1`).

---

## BYOK Sidekick (bring your own LLM API key)

The Sidekick chat in QuantUI is powered by **each user's own Anthropic API key** — the project pays
for no LLM usage, and the backend is architected so it **never holds a usable key at rest**. Live on
test and prod since 2026-07-18; the full design and runbook log is
[docs/proposals/byok-key-proxy-plan.md](docs/proposals/byok-key-proxy-plan.md).

### How it works

```text
Browser vault (IndexedDB, passphrase-encrypted key)
   │  per-turn: single-use envelope (ECDH → AES-GCM to the keyproxy's pinned public key)
   ▼
Express (server.mjs) ── IAP-verified per-user ES256 mint ──► quantcore-api (/api/chat)
                                                                 │  envelope + scope (never decrypted here)
                                                                 ▼
                                                         quantcore-keyproxy  ── the ONLY process that
                                                                 │              ever sees the plaintext key
                                                                 ▼
                                                             Anthropic API (SSE streamed back)
```

- **Browser vault** (`frontend/src/vault/`): the API key is stored in IndexedDB encrypted with a
  passphrase-derived key (WebCrypto PBKDF2 + AES-GCM). Add/rotate/unlock/remove via the
  **Settings** page.
- **Envelope encryption** (`frontend/src/vault/envelope.ts` ↔ `keyproxy/crypto.py`): each chat
  turn seals the key into a **single-use envelope** encrypted to the keyproxy's public key (SPKI
  pin baked into the UI bundle), bound to the user's identity (`sub`), a scope hash, and a `jti`
  that is burned on first use — replays and cross-user swaps are rejected.
- **Key proxy** (`keyproxy/`): a small FastAPI service with **no database** that decrypts the
  envelope in memory, enforces **scopes and token budgets**, calls the provider, and streams the
  response back. A strict **never-log policy** (enforced by tests) means no key, header, envelope,
  or credential-bearing error dump can reach any log.
- **Defense in depth on Cloud Run:** the keyproxy is **IAM-locked** (`--no-allow-unauthenticated`;
  only `quantcore-api`'s service account may invoke it, via a Google ID token) and runs as a
  dedicated least-privilege service account (`keyproxy-runtime@`, zero project roles). App-level
  ES256 user JWTs are enforced on top.
- **Per-user identity:** IAP authenticates the person → Express mints a 15-min ES256 JWT →
  `quantcore-api` (dual-mode: ES256 user tokens + legacy HS256 service tokens) and the keyproxy
  (ES256-only) verify it with the public key. Envelopes are bound to that identity.

### Using it

1. Open QuantUI (test or prod URL above) and go to **Settings → API Keys**.
2. Add your Anthropic API key and choose a vault passphrase (the key never leaves your browser
   unencrypted; losing the passphrase just means re-entering the key).
3. Chat in the Sidekick rail on any page. If the vault is locked, the rail prompts for your
   passphrase first.

Locally, the docker-compose stack runs the keyproxy at `:5002` with a persistent dev keypair
(generated by `runUI-CONTAINERS.sh`), so the compose UI's baked-in SPKI pin matches across
restarts.

---

## Starting Both Servers Together (Mac)

`runUI-MAC.sh` is a convenience script that launches both the API and frontend servers in the background from a single command.

```bash
./runUI-MAC.sh
```

What it does:
- Starts the **Cloud SQL Auth Proxy** if it isn't already listening — output logged to `cloud-sql-proxy.log`
- Activates the Python virtualenv (`.venv/`)
- Starts the FastAPI (uvicorn) server in the background — output logged to `api.log`
- Starts the Vite frontend dev server in the background — output logged to `frontend.log`
- Prints the PID of each process and the URLs for both servers

Both processes run independently; closing the terminal does not stop them. The script prints a `kill` command with both PIDs so you can shut them down when done.

---

## Local Container Stack (`docker-compose.yml`)

The whole backend runs as containers locally — the team's daily driver and the
fallback if the GCP deployment has issues. The topology mirrors the Cloud Run
target: AI agents talk to the seven **MCP wrapper** containers over streamable
HTTP; each wrapper is a thin HTTP gateway that calls the **`quantcore-api`**
FastAPI front door (`QUANTCORE_REST_URL=http://quantcore-api:5001`); the api runs
the services in-process and reaches **Cloud SQL** through a `cloud-sql-proxy`
sidecar — all on one bridge network.

```text
AI agents ──HTTP──► stock-price :6001 │ options-analysis :6002 │ company-fundamentals :6003
                    news-sentiment :6004 │ market-analysis :6005 │ portfolio :6006
                    arbitrage :6007
                              │  QUANTCORE_REST_URL
                              ▼
                       quantcore-api :5001  (FastAPI; services in-process)
                         │                    │  BYOK chat turns (envelope, never decrypted in the api)
                         ▼                    ▼
                  cloud-sql-proxy       keyproxy :5002  (no DB; decrypts envelopes in memory)
                         │
                         ▼
                 Cloud SQL (TEST instance)
```

### Bring it up

Always use the launcher — **not** plain `docker compose up`:

```bash
./runUI-CONTAINERS.sh up --build     # build images + start (first run)
./runUI-CONTAINERS.sh up -d          # start detached
./runUI-CONTAINERS.sh ps             # status
./runUI-CONTAINERS.sh logs -f quantcore-api
./runUI-CONTAINERS.sh down           # stop + remove
```

Any arguments pass straight through to `docker compose`.

**DB safety.** The stack talks **only** to the TEST Cloud SQL instance
(`quantcore-test-20260606:us-central1:quantcore`). The launcher derives a
git-ignored `.env.docker` (TEST connection name + credentials read from
`QUANTCORE_TEST_DB_DSN`) and runs `docker compose --env-file .env.docker`, which
**suppresses** Compose's default `./.env` load — so the prod `QUANTCORE_DB_DSN` /
`CLOUDSQL_CONNECTION_NAME` in `.env` can never leak into the containers. Requires
Google ADC on the host (`gcloud auth application-default login`).

**Auth.** The api runs with `AUTH_DISABLED=1` locally (today's no-auth contract),
so the team can test immediately. JWT validation is enabled only on Cloud Run.

### Images

| Image | Dockerfile | Deps | Role |
|-------|-----------|------|------|
| `quantcore-api` | `Dockerfile.api` | `requirements-ml.txt` (incl. torch/transformers for FinBERT) | FastAPI front door + service execution |
| MCP wrappers (×7) | `Dockerfile.mcp` | `requirements-base.txt` (lean) | one image reused per wrapper via `SERVER_MODULE`/`PORT` |
| `report` | `Dockerfile.report` | `requirements-base.txt` (lean) | `main.py` once-and-exit (Cloud Run Job) — notify, capture, warm; the service name kept the old "report" spelling |
| `quantcore-keyproxy` | `Dockerfile.keyproxy` | `keyproxy/requirements.txt` (slim) | BYOK credential-isolation boundary; no DB (IAM-locked on Cloud Run) |
| `quantui` | `Dockerfile.ui` | Node/Express | serves the built SPA + `/api/*` proxy (see QuantUI section) |

Only the api image carries the heavy ML stack — post-inversion FinBERT scoring
runs in the api, and `main.py` never scores sentiment, so the wrapper and report
images stay lean. Since issue #147 **no** image carries matplotlib/jinja2/boto3
either; those live in `requirements-report.txt` for the report script alone.

### Verify

```bash
curl http://localhost:5001/api/health          # {"status":"ok","db_connected":true}
# point Claude Desktop/Code at a wrapper, e.g. http://localhost:6001/mcp, and run a tool
```

The React frontend (`frontend/`, `npm run dev`) is a separate Vite app; point it
at `http://localhost:5001` (the published api port) and it runs unchanged.

---

## MCP Intelligence Layer (`fastMCPTest/`)

A suite of **FastMCP servers** that expose real-time market analysis as tools consumable by AI agents (Claude Code, custom agents, or any MCP-compatible client). The servers provide the analytical backbone for the `get_trade_recommendation` tool described below.

### Connecting AI clients to prod (MCP token)

The repo's `.mcp.json` already points the seven remote servers at the **prod** wrapper URLs
(`https://quantcore-<svc>-swgixldxzq-uc.a.run.app/mcp`, or the equivalent
`https://quantcore-<svc>-127961694257.us-central1.run.app/mcp` form), each sending
`Authorization: Bearer ${QUANTCORE_MCP_TOKEN}`. The wrappers do **identity passthrough** — they
forward that bearer to `quantcore-api`, whose `api/auth.py` is **dual-mode**: HS256 for these
service/MCP tokens, ES256 for the per-user tokens QuantUI mints. So the only
thing each team member supplies is their own prod token in `QUANTCORE_MCP_TOKEN`; without it every
data tool returns `401: … Not enough segments`.

**Prereq:** `gcloud auth login` with an account that can read the `quantcore-jwt-secret` secret in
`quantcore-prod-20260606` (the mint script fetches the signing secret from Secret Manager and never
prints it).

#### GCP access a team member needs (granted by the GCP admin)

Minting a prod token and reaching the prod DB through the Cloud SQL Auth Proxy require **four IAM
roles** in each project (`quantcore-prod-20260606` and, for dev/CI, `quantcore-test-20260606`).
These are granted by the **GCP administrator (John)** — a team member cannot grant them to
themselves:

| Role | On | Why |
|------|----|-----|
| `roles/secretmanager.secretAccessor` | secret `quantcore-jwt-secret` | mint the prod MCP token (read the JWT signing secret) |
| `roles/secretmanager.secretAccessor` | secret `quantcore-prod-db-dsn` (prod) / `quantcore-test-db-dsn` (test) | pull the DB DSN from Secret Manager (optional if the password was shared out-of-band) |
| `roles/cloudsql.client` | the project | authenticate the Cloud SQL Auth Proxy to the DB instance |
| `roles/browser` | the project | basic project visibility so `gcloud projects describe <project>` and Console browsing work |

> **Why `roles/browser` matters:** the three functional roles above do **not** include
> `resourcemanager.projects.get`, so without `roles/browser` a `gcloud projects describe` (and Console
> browsing) fails with *"caller does not have permission"* even though token minting and the DB proxy
> work fine. It's a visibility role, not an access path — don't mistake that error for a broken grant.

**Use a real Google account.** IAM silently drops `user:` bindings for consumer email addresses that
aren't backed by an actual, active Google account — the grant command reports success but the binding
never persists. If your access isn't working and the admin confirms the grant "went through," verify
the email you're using is a real Google account (and that it's the **active** one — see below).

#### Hitting a permissions issue?

1. **Check the active account first.** Most *"does not have permission"* errors are simply the wrong
   identity selected: `gcloud auth list` shows the active account; switch with
   `gcloud config set account <email>`. The mint script and proxy authenticate as whatever account is
   active.
2. **Confirm you're using a granted, real Google account** (see the caveat above).
3. **Still stuck? Contact the GCP administrator (John).** All four grants are admin-only; John can
   confirm the IAM bindings landed (`gcloud projects get-iam-policy …`) and re-grant if needed. Tell
   him which account (email) and which project you're using so he can verify the exact bindings.

Mint a **3-month** prod JWT for yourself and load it into the shell environment Claude Code launches
from (the token is a live bearer — it's redirected straight to a file in `$HOME`, never printed to
the terminal):

```bash
# --sub is your owner partition (use your name; defaults to john). 2160h = 90 days.
python scripts/mint_prod_jwt.py --output export --expires-hours 2160 --sub <you> > ~/.quantcore_mcp.env
chmod 600 ~/.quantcore_mcp.env
echo 'source ~/.quantcore_mcp.env' >> ~/.zshrc   # so every new shell inherits it
```

Then **restart Claude Code from a fresh shell** — `.mcp.json` reads `${QUANTCORE_MCP_TOKEN}` from
the process environment at startup, so a var exported in a child shell won't reach an
already-running client. Verify by running any prod data tool (e.g. `get_short_interest AAPL`); you
should get data, not a 401.

> **Token lifetime:** this token expires after **90 days** — re-run the mint command to rotate it.
> `~/.quantcore_mcp.env` is outside the repo and must **never** be committed. Each team member mints
> their own with their own `--sub`; don't share tokens.

### Servers

| Server | File | Purpose |
|--------|------|---------|
| `stock-price-server` | `fastMCPTest/stock_price_server.py` | Technical analysis, price/volume history, support levels, trade recommendations |
| `market-analysis-server` | `fastMCPTest/market_analysis_server.py` | Dark pool proxy, short interest, bid/ask spread |
| `options-analysis-server` | `fastMCPTest/options_analysis.py` | The whole options surface: chain + contracts, exact spread pricing, watchlist scoring, flow (unusual calls, OI change) and dealer positioning (GEX, gamma wall) |
| `company-fundamentals-server` | `fastMCPTest/company_fundamentals_server.py` | Fundamental score, revenue growth, earnings acceleration, + cross-symbol analytics |
| `news-sentiment-server` | `fastMCPTest/news_sentiment_server.py` | News collection, per-symbol sentiment, sentiment trend |
| `portfolio-server` | `fastMCPTest/portfolio_server.py` | Caller's own portfolio (read-only: roll-up, lots, totals) + the shared watchlist (`list_watchlist`, `add_to_watchlist` — no removal tool; removal is a UI action) |
| `arbitrage-server` | `fastMCPTest/arbitrage_server.py` | Arbitrage universe, pair analysis, scan, and cointegration discovery |

### Exact Options Contract & Spread Pricing

The stock-price and options-analysis MCP servers both expose exact contract lookup and vertical spread pricing tools for tactical options setups. These tools close the gap between broad directional analysis and executable spread evaluation.

**Tools:**
- `get_option_contracts(symbol, expirations, strikes, kind="call")` — returns specific call or put contracts by expiration and strike, including bid, ask, mid, IV, volume, open interest, moneyness, and bid/ask spread percentage.
- `price_vertical_spread(symbol, expiration, long_strike, short_strike, kind="call")` — prices a two-leg vertical spread using exact contracts. Returns conservative debit (`long ask - short bid`), mid-debit estimate, max profit, max loss, breakeven, risk/reward, leg details, liquidity label, cache source, and warnings.
- `get_full_options_chain(symbol, max_expirations=None)` — still fetches and persists all strikes/all expirations (optionally capped via `max_expirations`), and now reports `snapshot_id`, `persisted`, and `storage_warning` so callers know whether the database cache was updated. The daily job (`main.py`) calls this per portfolio/watchlist symbol so open-interest history accumulates for `get_oi_change_analysis`.

**Data flow:**
- Exact-contract tools use the latest full-chain database snapshot first.
- If the cache is missing, stale, or incomplete, they can fetch the live chain from Yahoo Finance, persist it, and retry the lookup.
- Full-chain writes are explicitly committed, so snapshots fetched by MCP are immediately queryable by later MCP calls and REST/WebUI readers.

### Fundamentals Cache & Cross-Symbol Analytics

The `company-fundamentals-server` now features a persistent database cache layer that enables portfolio-wide fundamental analysis. The cache stores earnings calendar, composite scores, revenue growth, and EPS acceleration data — building a time series for trend detection.

**Wrapped tools (cache-transparent):**
- `get_earnings_calendar(symbol)` — earnings dates and options risk profile
- `get_fundamental_score(symbol)` — composite score (-14 to +14) + metric breakdown
- `get_revenue_growth(symbol)` — quarterly trajectory and 3Y CAGR
- `get_earnings_acceleration(symbol)` — CAN SLIM 'A' criterion EPS acceleration

**New cross-symbol analytics tools:**
- `get_fundamental_scores_batch(symbols)` — batch score multiple stocks; returns cache hit/miss counts
- `get_full_fundamental_profile(symbol)` — all 4 metrics + synthesized summary in one call
- `get_top_fundamental_stocks(n=10, min_coverage=0.5)` — rank by composite score (cache-only, zero network calls)
- `get_upcoming_earnings(days=14, include_stale=False)` — stocks with earnings within N days, recomputed days-to-earnings
- `get_cache_stats()` — cache inventory and database health
- `get_sector_fundamental_breakdown(sector=None, top_n=5)` — group cached scores by sector
- `get_fundamental_score_changes(min_delta=2, since_days=90, direction="both")` — surfaces deteriorating or improving fundamentals
- `get_fundamental_history(symbol, data_type, since_days=365)` — historical snapshots with trend detection

**Configuration:**
- Cache TTL controlled via `FUNDAMENTALS_CACHE_TTL_HOURS` env var (default 24 hours)
- Storage: unified QuantCore PostgreSQL database (schema auto-created on startup)
- Setting TTL to 0 disables caching (useful for testing)
- TTL checked on every call, so changes take effect without server restart

For full implementation details, see [docs/FUNDAMENTALS_CACHE_IMPLEMENTATION.md](docs/FUNDAMENTALS_CACHE_IMPLEMENTATION.md).

### Historical Trend Tools

Three MCP tools expose historical views of key technical metrics to support multi-week position monitoring. They differ in how their history is sourced:

| Tool                                                   | Source | Backfill |
|--------------------------------------------------------|--------|----------|
| `get_vwap_history(symbol, since_days=90)`              | Computed from OHLCV data in the unified QuantCore database | Up to 2 years |
| `get_relative_strength_history(symbol, since_days=90)` | Computed from OHLCV data in the unified QuantCore database | Up to 2 years |
| `get_gamma_wall_history(symbol, since_days=90)`        | Stored snapshots in the unified QuantCore database | Forward-only from first call |

#### VWAP History

Rolling 20-day VWAP computed at each historical date. Use this to answer:
- Is price sustaining above VWAP (healthy uptrend) or repeatedly failing at it?
- When did the most recent VWAP reclaim or breakdown occur across the hold period?

Returns each row with: date, close, vwap, distance_pct, position (above/below VWAP).

#### Relative Strength History

Rolling 21-day returns for the symbol vs SPY, QQQ, and the symbol's sector ETF. This is the **most actionable metric for multi-week equity holds**:
- Improving RS = rotation into this stock — favorable tailwind
- Deteriorating RS while price rises = weak rally, easy to reverse
- Transition from laggard → outperformer often precedes sustained moves

Returns each row with: date, close, return_pct, rs_vs_spy, rs_vs_qqq, rs_vs_sector, rs_label (leader/outperforming/neutral/laggard/weak).

#### Gamma Wall History

The strike with the highest `|delta × OI|` concentration, **auto-persisted on every call to `get_delta_adjusted_oi()`**. History builds passively — no cron job needed. A post-close call (4:15pm+ ET) overwrites any earlier intraday call so settled EOD open interest is stored.

Gamma wall is an **intraday and weekly tool** used by professional services (SpotGamma, Tier1Alpha) to identify price pinning zones and MM hedging flows around expirations. It is **not** designed for multi-week equity decisions. Primary use of historical data: post-hoc analysis ("did price pin at the gamma wall on OpEx Fridays?").

Our implementation uses `max(|delta × OI|)` as a GEX proxy — directional intuition, not institutional-grade precision.

### Support-Level Analysis Tools

Tools for locating durable support/resistance levels and volatility-calibrated stops (built per issue [#93](https://github.com/JohnFunkCode/StockPortfolioManager/issues/93); each is also a REST endpoint under `/api/securities/<ticker>/…`):

- `get_atr_bands(symbol, period=14, band_mult=2.0, stop_mult=3.0, interval="1d", lookback=250)` — Wilder ATR volatility bands (close ± mult·ATR) plus a **chandelier trailing stop** (22-bar highest high − mult·ATR) with distance-to-stop and an expanding/contracting ATR trend read. Prefer this over `get_stop_loss_analysis`'s drawdown-based trailing % when earnings gaps pollute the drawdown history — ATR re-adapts within ~one period after a gap. REST: `GET /api/securities/<ticker>/atr-bands`.
- `get_anchored_vwap(symbol, anchor_date=None, lookback_days=365)` — volume-weighted average price anchored at significant events, approximating the **cost basis of participants since that event**. Anchors resolve automatically — recent earnings dates, the 52-week high/low, the largest unfilled gaps, and the most recent confirmed swing high/low — deduped within 3 trading days (a user-supplied `anchor_date` outranks all). Each anchor reports its AVWAP, distance from spot, and whether it acts as support or resistance, plus the nearest AVWAP support/resistance overall. An AVWAP reclaim is an institutional re-accumulation signal. REST: `GET /api/securities/<ticker>/anchored-vwap`.
- `get_volume_profile(symbol, days=365, interval="1d", bins=50, value_area_pct=0.70)` — histogram of **traded volume at each price level** (each bar's volume distributed uniformly across its high–low range). Returns the **POC** (Point of Control — the heaviest-traded price), the **value area** (the band holding 70% of volume, grown outward from the POC), and **HVN/LVN** nodes: High Volume Nodes are acceptance zones that act as support/resistance; Low Volume Nodes are air pockets price slices through quickly. Also reports the nearest HVN/LVN on each side of the current price. Use daily/1y for structural levels or `interval="1h"` for a ~60-day micro-profile. REST: `GET /api/securities/<ticker>/volume-profile`.
- `get_oi_change_analysis(symbol, days=30, top_n=10, min_oi=100, expiration=None)` — compares open interest across stored full-chain snapshots and classifies each big mover with the classic **2×2 OI/price read**: OI↑+price↑ = `new_longs`, OI↑+price↓ = `new_shorts`, OI↓+price↑ = `short_covering`, OI↓+price↓ = `long_liquidation`. Also surfaces **put-writing support strikes** (large put-OI builds below spot) and **call-wall resistance strikes** (call-OI builds above), plus a latest-vs-previous-day OI pulse. History accumulates from the daily report job's chain captures; with fewer than two snapshot days the tool returns an explanatory note rather than an error. REST: `GET /api/securities/<ticker>/options/oi-change`.
- `get_gex_profile(symbol, max_expirations=6, risk_free_rate=0.045)` — **signed Gamma Exposure (GEX) profile** using the standard dealer convention (long calls +, short puts −): `GEX = gamma × OI × 100 × spot² × 1%` per contract, aggregated per strike. Reports the **zero-gamma level** (the volatility-regime boundary — dealers dampen moves above it, amplify below), the **call wall** (top positive-GEX strike, resistance/pin) and **put support** (top negative-GEX strike, the vol trigger), the current `positive_gamma`/`negative_gamma` regime, and aggregate **vanna/charm exposures** with hedge-flow interpretations. A daily summary (net GEX, zero-gamma, regime) auto-persists to `gex_history` on every call, building a queryable regime series. REST: `GET /api/securities/<ticker>/options/gex-profile`.
- `get_support_confluence(symbol, tolerance_pct=1.0, max_expirations=4, max_zones=5)` — **the composite support/resistance tool**: fans out to every level-finding technique in the system (gamma wall, signed GEX walls + zero-gamma, volume profile POC/value-area/HVNs, anchored VWAPs, put/call OI builds, expected move, rolling VWAP, SMA 50/100/200, prior day/week/month high-low, Bollinger, ATR bands + chandelier stop, Fibonacci retracements), clusters levels within `tolerance_pct` of each other into zones, and scores each zone by the summed weight of the **independent methods** agreeing on it (dealer-positioning and volume-acceptance sources weigh most, geometric levels least). Returns ranked `support_zones`/`resistance_zones` with per-level contributors, `strongest_support` for stop placement, and `methods_available`/`methods_failed` so sparse coverage is visible — options-source failures degrade gracefully instead of failing the call. Prefer this over the individual tools when the question is "where is support?". REST: `GET /api/securities/<ticker>/support-confluence`. In QuantUI, the same data renders as the **Support Confluence card** on the security detail page's Technical Analysis tab (`frontend/src/components/securities/SupportConfluenceCard.tsx`).

### Trade Recommendations (`get_trade_recommendation`)

The flagship tool of the MCP layer. Given a stock symbol and available capital, it runs **13 independent signals** in parallel, scores each one as bullish or bearish, and produces a single actionable recommendation with entry price, target, stop loss, position size, and risk/reward ratio.

**Signals scored:**

| Category | Signals |
|----------|---------|
| Price structure | Bollinger Band position, VWAP, 20-day SMA |
| Momentum | RSI, MACD crossover, Stochastic %K |
| Volume & flow | Volume climax/capitulation, OBV divergence, dark pool accumulation/distribution |
| Market microstructure | Bid/ask spread, short interest / squeeze potential |
| Options intelligence | Unusual call sweeps, delta-adjusted OI (MM hedge flows), net options positioning |

**Trade types returned:**

| Net Score | Trade Type |
|-----------|-----------|
| ≥ 5 | LONG_CALL or BULL_CALL_SPREAD (high IV) |
| 3 – 4 | LONG_STOCK |
| 1 – 2 | WEAK_LONG |
| -2 – 0 | SKIP |
| -3 – -4 | LONG_PUT |
| ≤ -5 | LONG_PUT or BEAR_PUT_SPREAD (high IV) |

Position sizing uses a 2% risk budget: `risk_budget = capital × 0.02`, divided by the distance to the technical stop for stock trades, or by the ATM option ask for options trades.

The tool also includes automated contradiction detection — it flags when options flow (institutional call sweeps, market maker hedging direction) conflicts with technical signals, helping avoid false-directional trades.

For full design details, signal scoring tables, the AMZN case study, and implementation notes, see [docs/Get Trade Recommendations.md](docs/Get%20Trade%20Recommendations.md).

### Starting the MCP servers

The servers are configured in `.mcp.json` and start automatically when Claude Code is launched in this project. To start them manually:

```bash
source .venv/bin/activate
fastmcp run fastMCPTest/stock_price_server.py
```

---

## Harvester System

A "harvest ladder" strategy for systematically selling shares into strength. It computes a
volatility-based harvest threshold (H) per symbol, builds a forward ladder of price targets
("rungs"), and tracks each rung from *planned* → *achieved* → *executed*.

- `experiments/HarvesterExperiment.py` — the algorithm and its backtests
- `quantcore/repositories/harvester_repository.py` — `HarvesterPlanDB` + `PlanBuildParams`; SQL only
- `quantcore/services/harvester.py` — `HarvesterService`, which scans prices against active rungs
- REST: `/api/plans`, `/api/rungs`, `/api/symbols`, `/api/dashboard`; UI: `/harvester`, `/plans`

It also hooks into notifications: each `main.py` run checks every portfolio stock against the
active plan rungs and fires a Discord alert for any hit.

## Arbitrage Scanner

Finds securities whose price has stretched against a structurally linked underlying, across three
families: **nav_vehicle** (treasury companies and trusts — the only family with a computable fair
value), **commodity_etf** (a fund vs its reference future), and **producer** (a miner or E&P vs the
commodity it sells). Curated links live in `arb_universe.yaml`; `discover_pairs` additionally
sweeps for undeclared cointegrated links against a reference panel, gated by a sector/industry
economic-link filter.

**The scoring is deliberately inverted: spread width only qualifies a candidate, the convergence
mechanism ranks it.** The score is a product of named factors — `opportunity × evidence ×
convergence × hedge × carry × trend × freshness` — all returned in the `factors` block alongside
`reasons` and `breaks_on`, so any score is attributable. Because the account is equity/ETF-only,
any pair whose only clean hedge is a futures contract is flagged `hedge_available: false` and
halved. **Expect most scans to return nothing above `watch` — that is the intended behaviour, not
a bug.**

REST: `GET /api/arbitrage/{universe,scan,discover,pairs/{security}}`; MCP: `arbitrage-server`;
UI: `/arbitrage`. For example prompts, how to read the `factors` breakdown, the MSTR worked
example (gross vs net discount), and how to add a pair, see
[docs/arbitrage-scanner-usage.md](docs/arbitrage-scanner-usage.md).

---

## Testing

Backend suites live under `tests/`. The `tests/__init__.py` package initializer swaps in the test
DSN before `quantcore.db` is imported, so the suite never touches the primary database:

```bash
python -m unittest discover -s tests -t .
```

Run a single module by dotted path from the repo root:

```bash
python -m unittest tests.test_money
```

CI enforces a coverage ratchet on both sides — a floor for the backend (`.coveragerc` + the gate in
`deploy.yml`) and thresholds for the front end (`frontend/vitest.config.ts`) that only move upward:

```bash
coverage run -m unittest discover -s tests -t . && coverage report
cd frontend && npx vitest run --coverage
```

## Report Features

The HTML reports include:
- Portfolio summary with total values
- Individual stock performance metrics
- Gain/loss visualization
- Generated timestamp in 12-hour format (e.g., "2023-05-15 2:30:45 pm")
- Currency conversion options
