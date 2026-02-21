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

### Harvester System (`experiments/`)

An experimental "harvest ladder" strategy for systematically selling shares as prices rise:

- **`HarvesterExperiment.py`** — Core algorithm: computes volatility-based harvest thresholds (H), builds forward price target ladders, and backtests harvest plans.
- **`HarvesterPlanStore.py`** — `HarvesterPlanDB` persists plans in SQLite (`harvester.sqlite`). Schema includes symbols, price bars, plan templates, plan instances, plan rungs, and alerts. `HarvesterController` scans prices against active plan rungs and fires alerts.

The Harvester integrates with the notification system: when `main.py` runs, it checks each portfolio stock against active harvest plan rungs and sends Discord alerts for any hits.

## Configuration

- **`.env`** — `DISCORD_WEBHOOK_URL` for notifications; `HARVESTER_DB_PATH` for the SQLite harvester database path; `BUCKET_NAME`/`BUCKET_KEY` for optional S3 upload.
- **`portfolio.csv`** — Holdings data: `name,symbol,purchase_price,quantity,purchase_date,currency,sale_price,sale_date,current_price`
- **`watchlist.yaml`** — Watchlist entries with `name`, `symbol`, `currency`, and optional `tags` list.

## Key Dependencies

pandas, yfinance, matplotlib, jinja2, python-dotenv, boto3, PyYAML, requests
