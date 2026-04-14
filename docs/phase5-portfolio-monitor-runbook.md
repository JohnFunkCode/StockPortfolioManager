# Phase 5 — Portfolio Monitor Runbook
### Stock Portfolio Manager — Agentic Market Intelligence System

---

## Overview

This runbook documents Phase 5: the Portfolio Monitor. It runs a daily health check on all open positions for each tenant, classifies seven types of alerts, sends per-position Discord alerts for critical conditions (AT RISK, INSTITUTIONAL EXIT), and sends a consolidated morning/closing report.

**Prerequisites:**
- Phase 1–4 complete
- Auth Proxy running on port 5433
- At least one open position in the `positions` table (`sale_date IS NULL`)

---

## What Was Built in This Phase

| File | Purpose |
|------|---------|
| `agents/portfolio_monitor/__init__.py` | Package marker |
| `agents/portfolio_monitor/monitor.py` | `_check_position()`, `monitor_tenant()` |
| `agents/orchestrator/orchestrator.py` | Added `POST /run-portfolio-monitor` |
| `scripts/test_portfolio_monitor.py` | Smoke test for `_check_position()` |

---

## Alert Classification

| Alert | Tool Used | Trigger Condition | Action |
|-------|-----------|-------------------|--------|
| **AT RISK** | `get_stop_loss_analysis` | Price within 3% of technical stop | Discord alert (red) + `agent_signals` row |
| **DRAWDOWN WARNING** | `get_stop_loss_analysis` | Position loss > 75% of trailing stop % | Report only |
| **TREND DEGRADING** | `get_stop_loss_analysis` | 3+ consecutive sessions below VWAP | Report only |
| **INSTITUTIONAL EXIT** | `get_dark_pool` | `net_signal == "distribution"` | Discord alert (orange) + `agent_signals` row |
| **SQUEEZE WATCH** | `get_stop_loss_analysis` | Short float > 15% + MEDIUM/HIGH squeeze potential | Report only |
| **CAPITULATION** | `get_bid_ask_spread` | `spread_vs_norm == "narrowing"` | Report only |
| **MM BIAS REVERSAL** | `get_delta_adjusted_oi` | `mm_hedge_bias == "sell_on_rally"` | Report only |

AT RISK and INSTITUTIONAL EXIT write to `agent_signals.escalated = false`. Phase 6 (Deep Analysis) will subscribe to these and flip `escalated = true`.

---

## Step 1 — Run the Position Health Check Smoke Test

**fish**
```fish
set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
source .venv/bin/activate.fish; and python scripts/test_portfolio_monitor.py
```

**bash**
```bash
export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
source .venv/bin/activate && python scripts/test_portfolio_monitor.py
```

Expected output (alerts vary by market conditions):
```
Checking AAPL...
Symbol:    AAPL
Price:     $189.50
Cost basis: $185.00
Alerts (1):
  [trend_degrading] 4 consecutive sessions below VWAP $192.30
All assertions passed.
```

---

## Step 2 — Test the Orchestrator Endpoint Locally

Start Flask (with Auth Proxy running):

**fish**
```fish
set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
set -x DATABASE_URL "postgresql+psycopg2://app_user:$DB_PASS@127.0.0.1:5433/stock_portfolio"
set -x GOOGLE_OAUTH_CLIENT_ID (gcloud secrets versions access latest --secret=google-oauth-client-id)
set -x GOOGLE_OAUTH_CLIENT_SECRET (gcloud secrets versions access latest --secret=google-oauth-client-secret)
set -x JWT_SECRET (gcloud secrets versions access latest --secret=jwt-secret)
source .venv/bin/activate.fish; and python -m api.app
```

**bash**
```bash
export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
export DATABASE_URL="postgresql+psycopg2://app_user:$DB_PASS@127.0.0.1:5433/stock_portfolio"
export GOOGLE_OAUTH_CLIENT_ID=$(gcloud secrets versions access latest --secret=google-oauth-client-id)
export GOOGLE_OAUTH_CLIENT_SECRET=$(gcloud secrets versions access latest --secret=google-oauth-client-secret)
export JWT_SECRET=$(gcloud secrets versions access latest --secret=jwt-secret)
source .venv/bin/activate && python -m api.app
```

Trigger a morning report:

**fish / bash**
```bash
curl -s -X POST http://localhost:5001/run-portfolio-monitor \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "7d3cc53d-a909-4574-bbf7-c3c02ee0940b", "report_type": "Morning"}' \
  | python3 -m json.tool
```

Expected response shape:
```json
{
  "elapsed_seconds": 12.43,
  "report_type": "Morning",
  "run_at": "2026-04-12T09:35:02.123456",
  "tenants_scanned": 1,
  "tenants": [
    {
      "tenant_id": "7d3cc53d-a909-4574-bbf7-c3c02ee0940b",
      "report_type": "Morning",
      "positions_checked": 2,
      "at_risk": 0,
      "degrading": 1,
      "squeeze_watch": 0,
      "capitulation": 0,
      "escalated": []
    }
  ]
}
```

A purple Discord embed should arrive in your channel within a few seconds.

---

## Step 3 — Trigger a Closing Report

**fish / bash**
```bash
curl -s -X POST http://localhost:5001/run-portfolio-monitor \
  -H "Content-Type: application/json" \
  -d '{"report_type": "Closing"}' \
  | python3 -m json.tool
```

---

## Step 4 — Verify Signals in the Database

After a run that produces AT RISK or INSTITUTIONAL EXIT alerts:

**fish**
```fish
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
SET app.tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b';
SELECT symbol, direction, triggers, escalated, fired_at
FROM agent_signals
WHERE tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b'
ORDER BY fired_at DESC
LIMIT 10;
"
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
SET app.tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b';
SELECT symbol, direction, triggers, escalated, fired_at
FROM agent_signals
WHERE tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b'
ORDER BY fired_at DESC
LIMIT 10;
"
```

---

## Cloud Scheduler Configuration (Production)

Two jobs per tenant — configure after Cloud Run deployment in Phase 7:

| Job Name | Schedule | Body |
|----------|----------|------|
| `portfolio-monitor-morning` | `35 9 * * 1-5` (ET) | `{"report_type": "Morning"}` |
| `portfolio-monitor-closing` | `55 15 * * 1-5` (ET) | `{"report_type": "Closing"}` |

Cloud Scheduler uses UTC — subtract 4h (EDT) or 5h (EST):
- Morning: `35 13 * * 1-5` UTC (EDT) / `35 14 * * 1-5` UTC (EST)
- Closing: `55 19 * * 1-5` UTC (EDT) / `55 20 * * 1-5` UTC (EST)

---

## Phase 6 Preview — Deep Analysis Escalation

AT RISK and INSTITUTIONAL EXIT signals are now persisted to `agent_signals` with `escalated = false`. Phase 6 (Deep Analysis Agent) will:
1. Subscribe to a Pub/Sub topic populated from `agent_signals WHERE escalated = false`
2. Run the full 20-tool deep analysis pipeline
3. Write a recommendation to `agent_recommendations`
4. Flip `agent_signals.escalated = true`
5. Send a Discord recommendation embed

---

## Gotchas Reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: market_analysis_server` | `fastMCPTest/` not on sys.path | Ensure `monitor.py` sets `sys.path.insert(0, ...)` before the import |
| No report sent to Discord | `discord_webhook_url` not set on tenant | Run `UPDATE tenants SET discord_webhook_url = '...' WHERE id = '...'` |
| `Suppressed duplicate: portfolio_report` | Report already sent within 12-hour window | Delete the dedup row or reduce the `suppress_minutes` in `alert_dedup_config` |
| `RLS violation on agent_signals INSERT` | `get_db()` called without `tenant_id` | Ensure `_write_signal()` uses `get_db(tenant_id=tenant_id)` |
| `TimeoutError: Yahoo Finance .info request` | `get_short_interest` or `get_dark_pool` hit the slow Yahoo endpoint | Transient — retry the run; Yahoo rate-limits infrequently |
| `psycopg2.OperationalError: Connection refused 5433` | Auth Proxy not running | Start `./cloud-sql-proxy ... --port=5433` in a separate terminal |

---

*Runbook prepared April 2026. Covers Phase 5 of the Agentic Market Intelligence System — GCP Edition.*
