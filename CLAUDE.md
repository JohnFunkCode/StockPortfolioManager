# CLAUDE.md
claude --resume 44dcf10f-5cc7-494e-90b2-1e4d0bc4a672

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the application (generates HTML report + sends Discord notifications)
python main.py

# Run all tests
python -m unittest discover

# Run a single test file
python -m unittest test_money
python -m unittest test_stock_portfolio_manager

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

`main.py` is the entry point. It loads portfolio from CSV + watchlist from YAML, fetches prices/metrics, generates matplotlib charts (embedded as base64 in HTML via Jinja2 template), optionally uploads to S3, and triggers notifications.

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

- **`quantcore/db.py`** — Shared connection factory (`get_connection()`) backed by `psycopg2`, connecting via the `QUANTCORE_DB_DSN` environment variable. Centralized schema DDL for all 16 tables (`init_schema()`), using `SERIAL` primary keys and `ON CONFLICT` upserts. Imported as `from quantcore.db import get_connection`.
- **Schema** includes: symbols, OHLCV (merged from daily + intraday intervals), fetch_log, plan_templates/instances/rungs/alerts (Harvester), options_snapshots/expirations/contracts/gamma_wall_history/options_positions, news_articles, sentiment_snapshots, fundamentals_history.

All repositories under `quantcore/repositories/` and the REST API (`api/app.py`) use the shared factory instead of managing individual database connections.

**Migrating from a legacy SQLite database:** `scripts/migrate_sqlite_to_postgres.py` performs a one-shot copy of an existing `quantcore.sqlite` file into PostgreSQL — it initializes the schema, migrates all 16 tables in FK-safe order via batched `execute_values()` inserts, resets `SERIAL` sequences, and verifies row counts. Run it with `--sqlite <path>` and `--dsn <postgresql-uri>`.

### Services Layer (`quantcore/`)

Per [`docs/proposals/architectural-standard-v2.md`](docs/proposals/architectural-standard-v2.md), all business logic lives in an object-oriented services layer; the MCP tool bodies (`fastMCPTest/*_server.py`, `options_analysis.py`) and Flask routes (`api/app.py`) are thin adapters that are **exactly one service call deep**.

- **`quantcore/gateways/`** — external-IO wrappers: `YFinanceGateway` (yfinance), `PolygonGateway` (Polygon HTTP/pagination). These are the *only* place outside `portfolio/` (the legacy domain layer, retained for `main.py`'s report path) and the standalone `experiments/` monitors that imports `yfinance`.
- **`quantcore/repositories/`** — SQL-only persistence, no analytics: `OhlcvRepository`, `OptionsStore`, `OptionsPositionStore`, `NewsStore`, `SentimentStore`, `FundamentalsRepository`, `HarvesterPlanDB`, `PortfolioRepository`.
- **`quantcore/analytics/`** — pure functions (DataFrame/dict in, value out), no I/O: `indicators.py` (RSI/MACD), `options_math.py` (Black–Scholes delta/gamma, max-pain, expected-move — single home, deduped).
- **`quantcore/services/`** — the business logic: `PricesService`, `OptionsService`, `OptionsScreeningService`, `FundamentalsService`, `SentimentService`, `MicrostructureService`, `HarvesterService`, `PortfolioService`, `RecommendationsService` (composes the other services).
- **`quantcore/services/registry.py`** — the composition root: a lazy `@lru_cache get_services()` returning a frozen `Services` dataclass with all dependencies constructor-injected. Adapters call `get_services().<service>.<method>(...)`; service modules never import each other or the registry (acyclic).

Positions are DB-backed with multi-owner support (`positions` table, `owner` column); `portfolio.csv` is a per-owner import format (`scripts/import_portfolio.py --csv portfolio.csv --owner john`, full-sync replace). The REST `GET/POST/DELETE /api/portfolio*` routes take an `?owner=` param defaulting to `john`; `main.py`'s report/notifications stay on John's portfolio.

**Refactor status:** Phase 1 of architectural-standard-v2 (services-layer extraction) is **complete** — see [`docs/proposals/phase1-migration-plan.md`](docs/proposals/phase1-migration-plan.md) for the checkpoint log. Phase 2 (FastAPI/Pydantic; consolidating `portfolio/yfinance_gateway.py` into the shared gateway) is not yet started.

## Configuration

- **`.env`** — `QUANTCORE_DB_DSN` is the PostgreSQL connection string for the unified database (e.g. `postgresql://<user>:<password>@<host>:<port>/<database>`); `QUANTCORE_TEST_DB_DSN` optionally points the same code at an isolated database for testing; `DISCORD_WEBHOOK_URL` for notifications; `BUCKET_NAME`/`BUCKET_KEY` for optional S3 upload.
- **`portfolio.csv`** — Holdings data: `name,symbol,purchase_price,quantity,purchase_date,currency,sale_price,sale_date,current_price`
- **`watchlist.yaml`** — Watchlist entries with `name`, `symbol`, `currency`, and optional `tags` list.

**Database Initialization:** The unified PostgreSQL database and its 16-table schema are automatically created on startup by any application entry point (`main.py`, REST API, or MCP servers) — `init_schema()` runs before any database operations. The database itself (and its `quantcore` user) must already exist; point `QUANTCORE_DB_DSN` at any reachable PostgreSQL instance — local, or a managed service such as Cloud SQL accessed through the Cloud SQL Auth Proxy (which exposes the remote instance as a local TCP host:port, so no code changes are needed to switch targets).

## Key Dependencies

pandas, yfinance, matplotlib, jinja2, python-dotenv, boto3, PyYAML, requests, psycopg2
