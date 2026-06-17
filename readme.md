# Stock Portfolio Manager

A Python-based stock portfolio tracker with real-time price updates, multi-currency support, and HTML report generation capabilities.

## Features

- Track stocks portfolio positions including purchase information (price, date, quantity) read from posrtolio.csv
- Tracks Optional watchlist including per-stock 'tags' loaded from watchlist.yaml
- Fetch real-time stock prices via Yahoo Finance API
- Calculate gain/loss for individual stocks and total portfolio
- Support for multiple currencies with real-time conversion
- Generate HTML reports with portfolio performance metrics
- Calculate portfolio performance statistics

## Technologies Used

- Python 3.9+
- pandas - Data manipulation and analysis
- yfinance - Yahoo Finance API integration
- requests - HTTP library
- Jinja2 - HTML template rendering

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
YAML file entries:
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

3. Create a `stocks.csv` file with your portfolio data following the format in the example above.

## Configuration

### Database

The application uses a unified **PostgreSQL** database (codename **QuantCore**, accessed via `psycopg2`) to store:
- Portfolio holdings and historical OHLCV data (daily, intraday)
- Options chain snapshots, Greeks, and gamma wall history
- News articles and sentiment analysis
- Fundamental metrics and earnings dates
- Harvester plan instances and alert logs

**The schema is automatically created** when any application component starts (main.py, REST API, or MCP servers) — `init_schema()` runs before any database operations. The PostgreSQL server, database, and `quantcore` user must already exist; point the app at any reachable PostgreSQL instance via the DSN below — a local server, or a managed service such as Cloud SQL connected through the [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy) (which exposes the remote database as a local `host:port`, so no code changes are needed to switch targets).

**Environment Variables:**
- `QUANTCORE_DB_DSN` — PostgreSQL connection string for the unified database, e.g. `postgresql://<user>:<password>@<host>:<port>/<database>`
- `QUANTCORE_TEST_DB_DSN` — optional DSN for an isolated database, used to run the app or test suite against a separate copy of the data without touching the primary database
- `DISCORD_WEBHOOK_URL` — Discord webhook for price alerts (optional)
- `BUCKET_NAME` / `BUCKET_KEY` — AWS S3 credentials for report uploads (optional)

**Migrating from a legacy SQLite database:** if you have an existing `quantcore.sqlite` file, `scripts/migrate_sqlite_to_postgres.py` performs a one-shot copy into PostgreSQL — it initializes the schema, migrates all tables in foreign-key-safe order using batched inserts, resets primary-key sequences, and verifies row counts:
```bash
python scripts/migrate_sqlite_to_postgres.py --sqlite data/quantcore.sqlite --dsn "$QUANTCORE_DB_DSN"
```

## Usage

### Basic Portfolio Analysis

Run the main application:
The application can generate detailed HTML reports with portfolio performance metrics and timestamps in 12-hour format:

```
python main.py

```

## Project Structure

- `main.py`: Application entry point, reads CSV data and displays portfolio information
- portfolio/ – domain modules (stock.py, money.py, metrics.py, portfolio.py, watch_list.py, yfinance_gateway.py).
- html_summary.py, simple_text_summary.py – reporting utilities.
- notifier.py – notification hook.
- templates/ – Jinja2 HTML template.
- Tests: test_money.py, test_stock_portfolio_manager.py.
- Data samples: portfolio.csv, watchlist.csv, watchlist.yaml.

## REST API (`api/`)

A FastAPI REST API that exposes the Harvester Plan Store and Securities Dashboard over HTTP for use by the React frontend or other clients. Interactive OpenAPI docs are served at `/docs` and the spec at `/openapi.json`.

**Entry point:** `api/main.py` (the FastAPI `app`)

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check — confirms the API and PostgreSQL database are reachable |
| GET | `/api/plans` | List harvest plans; filter by `?status=ACTIVE\|SUPERSEDED\|ALL` |
| POST | `/api/plans` | Create a new harvest plan for a symbol |
| GET | `/api/plans/<id>` | Get a single plan with its rungs |
| PATCH | `/api/plans/<id>` | Update plan notes or metadata |
| DELETE | `/api/plans/<id>` | Delete (supersede) a plan |
| GET | `/api/plans/<id>/rungs` | List all rungs for a plan |
| GET | `/api/rungs/<id>` | Get a single rung |
| POST | `/api/rungs/<id>/achieve` | Mark a rung as achieved at a given trigger price |
| POST | `/api/rungs/<id>/execute` | Record that shares were sold at a rung (price, quantity, tax) |
| GET | `/api/symbols` | List all ticker symbols that have plans |
| GET | `/api/symbols/<ticker>/price` | Fetch the latest close price for a ticker |
| GET | `/api/dashboard/stats` | Aggregate stats for the dashboard |

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

A **Harvest Ladder** dashboard built with React 19, TypeScript, Vite, and Material UI. It communicates exclusively with the FastAPI service above.

### Pages

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Summary stats: total active plans, rungs hit, shares harvested, and estimated proceeds |
| Plans | `/plans` | Table of all harvest plans with status badges; create or delete plans |
| Plan Detail | `/plans/:id` | Full rung ladder for a plan; mark rungs as achieved or record executions |
| Symbols | `/symbols` | Look up the latest live price for any ticker symbol |

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

## Starting Both Servers Together (Mac)

`runUI-MAC.sh` is a convenience script that launches both the API and frontend servers in the background from a single command.

```bash
./runUI-MAC.sh
```

What it does:
- Activates the Python virtualenv (`.venv/`)
- Starts the FastAPI (uvicorn) server in the background — output logged to `api.log`
- Starts the Vite frontend dev server in the background — output logged to `frontent.log`
- Prints the PID of each process and the URLs for both servers

Both processes run independently; closing the terminal does not stop them. The script prints a `kill` command with both PIDs so you can shut them down when done.

---

## Local Container Stack (`docker-compose.yml`)

The whole backend runs as containers locally — the team's daily driver and the
fallback if the GCP deployment has issues. The topology mirrors the Cloud Run
target: AI agents talk to the five **MCP wrapper** containers over streamable
HTTP; each wrapper is a thin HTTP gateway that calls the **`quantcore-api`**
FastAPI front door (`QUANTCORE_REST_URL=http://quantcore-api:5001`); the api runs
the services in-process and reaches **Cloud SQL** through a `cloud-sql-proxy`
sidecar — all on one bridge network.

```text
AI agents ──HTTP──► stock-price :6001 │ options-analysis :6002 │ company-fundamentals :6003
                    news-sentiment :6004 │ market-analysis :6005
                              │  QUANTCORE_REST_URL
                              ▼
                       quantcore-api :5001  (FastAPI; services in-process)
                              │
                              ▼
                       cloud-sql-proxy ──► Cloud SQL (TEST instance)
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
| MCP wrappers (×5) | `Dockerfile.mcp` | `requirements-base.txt` (lean) | one image reused per wrapper via `SERVER_MODULE`/`PORT` |
| `report` | `Dockerfile.report` | `requirements-base.txt` (lean) | `main.py` once-and-exit (Cloud Run Job) |

Only the api image carries the heavy ML stack — post-inversion FinBERT scoring
runs in the api, and `main.py` never scores sentiment, so the wrapper and report
images stay lean.

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

### Servers

| Server | File | Purpose |
|--------|------|---------|
| `stock-price-server` | `fastMCPTest/stock_price_server.py` | Technical analysis, options chain, exact spread pricing, trade recommendations |
| `market-analysis-server` | `fastMCPTest/market_analysis_server.py` | Dark pool proxy, short interest, bid/ask spread |
| `options-analysis-server` | `fastMCPTest/options_analysis.py` | Watchlist-level options scoring, exact spread pricing, and trade building |
| `company-fundamentals-server` | `fastMCPTest/company_fundamentals_server.py` | Fundamental score, revenue growth, earnings acceleration, + cross-symbol analytics |

### Exact Options Contract & Spread Pricing

The stock-price and options-analysis MCP servers both expose exact contract lookup and vertical spread pricing tools for tactical options setups. These tools close the gap between broad directional analysis and executable spread evaluation.

**Tools:**
- `get_option_contracts(symbol, expirations, strikes, kind="call")` — returns specific call or put contracts by expiration and strike, including bid, ask, mid, IV, volume, open interest, moneyness, and bid/ask spread percentage.
- `price_vertical_spread(symbol, expiration, long_strike, short_strike, kind="call")` — prices a two-leg vertical spread using exact contracts. Returns conservative debit (`long ask - short bid`), mid-debit estimate, max profit, max loss, breakeven, risk/reward, leg details, liquidity label, cache source, and warnings.
- `get_full_options_chain(symbol)` — still fetches and persists all strikes/all expirations, and now reports `snapshot_id`, `persisted`, and `storage_warning` so callers know whether the database cache was updated.

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

| Tool | Source | Backfill |
|------|--------|----------|
| `get_vwap_history(symbol, since_days=90)` | Computed from OHLCV data in the unified QuantCore database | Up to 2 years |
| `get_relative_strength_history(symbol, since_days=90)` | Computed from OHLCV data in the unified QuantCore database | Up to 2 years |
| `get_gamma_wall_history(symbol, since_days=90)` | Stored snapshots in the unified QuantCore database | Forward-only from first call |

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

## Testing

Run the unit tests:
```
python -m unittest discover
```

## Report Features

The HTML reports include:
- Portfolio summary with total values
- Individual stock performance metrics
- Gain/loss visualization
- Generated timestamp in 12-hour format (e.g., "2023-05-15 2:30:45 pm")
- Currency conversion options
