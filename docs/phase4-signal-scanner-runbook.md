# Phase 4 — Orchestrator & Signal Scanner Runbook
### Stock Portfolio Manager — Agentic Market Intelligence System

---

## Overview

This runbook documents Phase 4: the Signal Scanner and Orchestrator. The Signal Scanner scores each portfolio position across 6 indicators (RSI, MACD, VWAP, Bollinger Bands, Unusual Calls, DAOI) and fires a Discord alert when the conviction score reaches ±4. The Orchestrator is a Flask endpoint that Cloud Scheduler hits to fan out scans across all active tenants.

**Prerequisites:**
- Phase 1 complete (GCP project, Cloud SQL, schema migrated, tenant seeded)
- Phase 2 complete (OAuth login working, `DATABASE_URL` env var pattern established)
- Phase 3 complete (`AgentNotifier` working, dedup smoke test passed)
- Auth Proxy running on port 5433
- MCP servers running (or at least `fastMCPTest/stock_price_server.py` importable from project root)

---

## What Was Built in This Phase

| File | Purpose |
|------|---------|
| `agents/signal_scanner/__init__.py` | Package marker |
| `agents/signal_scanner/scanner.py` | 6-indicator scorer, `scan_symbol()`, `scan_tenant()` |
| `agents/orchestrator/__init__.py` | Package marker |
| `agents/orchestrator/orchestrator.py` | Flask Blueprint — `POST /run-signal-scanner` |
| `scripts/test_signal_scanner.py` | Smoke test for `scan_symbol()` |

The orchestrator blueprint was registered in `api/app.py`.

---

## Scoring Rules

| Indicator | Tool | +Score | −Score |
|-----------|------|--------|--------|
| RSI | `get_rsi` | +2 oversold | −2 overbought |
| MACD | `get_macd` | +2 bullish_crossover | −2 bearish_crossover |
| VWAP | `get_vwap` | +1 reclaim | −1 below_vwap (no reclaim) |
| Bollinger | `get_stock_price` | +1 near lower band | −1 near upper band |
| Unusual Calls | `get_unusual_calls` | +2 strong/moderate sweep | — |
| DAOI | `get_delta_adjusted_oi` | +1 buy_on_rally | −1 sell_on_rally |

- **Maximum score:** +9 (all bullish) / −9 (all bearish)
- **Default threshold:** ±4 (configurable per tenant in `tenant_config.conviction_threshold`)
- When `|score| ≥ threshold`, a signal is written to `agent_signals` and a Discord alert fires via `AgentNotifier.send_signal_alert()`

---

## Step 1 — Verify the Smoke Test

With the Auth Proxy running on port 5433:

**fish**
```fish
set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
source .venv/bin/activate.fish; and python scripts/test_signal_scanner.py
```

**bash**
```bash
export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
source .venv/bin/activate && python scripts/test_signal_scanner.py
```

Expected output (values will vary):
```
Scanning AAPL for tenant 7d3cc53d...
Symbol:    AAPL
Score:     +1 / 9
Direction: buy
Threshold: 4
Triggered: False
Indicator breakdown:
  rsi              score=+0  {'rsi': 52.3, 'signal': 'neutral'}
  macd             score=+0  {'crossover': 'bullish', 'histogram': 0.12}
  vwap             score=+1  {'position': 'above_vwap', 'reclaim_signal': True, ...}
  bollinger        score=+0  {'price': 189.0, 'bb_lower': 178.0, 'bb_upper': 201.0, ...}
  unusual_calls    score=+0  {'sweep_signal': 'none', 'unusual_call_count': 0}
  daoi             score=+0  {'mm_hedge_bias': 'sell_on_rally', 'net_daoi_shares': -4200.0}
All assertions passed.
```

---

## Step 2 — Seed a Position (If None Exist)

`scan_tenant()` queries `positions WHERE sale_date IS NULL`. If your tenant has no positions, insert one:

**fish**
```fish
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
SET app.tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b';
INSERT INTO positions (tenant_id, symbol, purchase_price, quantity, purchase_date)
VALUES
  ('7d3cc53d-a909-4574-bbf7-c3c02ee0940b', 'AAPL', 185.00, 10, '2024-01-15'),
  ('7d3cc53d-a909-4574-bbf7-c3c02ee0940b', 'NVDA', 500.00, 5,  '2024-03-01');
"
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
SET app.tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b';
INSERT INTO positions (tenant_id, symbol, purchase_price, quantity, purchase_date)
VALUES
  ('7d3cc53d-a909-4574-bbf7-c3c02ee0940b', 'AAPL', 185.00, 10, '2024-01-15'),
  ('7d3cc53d-a909-4574-bbf7-c3c02ee0940b', 'NVDA', 500.00, 5,  '2024-03-01');
"
```

---

## Step 3 — Test the Orchestrator Endpoint Locally

Start the Flask app (with Auth Proxy running):

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

In a second terminal, trigger a scan:

**fish**
```fish
curl -s -X POST http://localhost:5001/run-signal-scanner \
  -H "Content-Type: application/json" | python3 -m json.tool
```

**bash**
```bash
curl -s -X POST http://localhost:5001/run-signal-scanner \
  -H "Content-Type: application/json" | python3 -m json.tool
```

To target a single tenant:

**fish / bash**
```bash
curl -s -X POST http://localhost:5001/run-signal-scanner \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "7d3cc53d-a909-4574-bbf7-c3c02ee0940b"}' | python3 -m json.tool
```

Expected response shape:
```json
{
  "elapsed_seconds": 4.21,
  "run_at": "2026-04-12T09:35:01.123456",
  "tenants_scanned": 1,
  "tenants": [
    {
      "tenant_id": "7d3cc53d-a909-4574-bbf7-c3c02ee0940b",
      "symbols_scanned": 2,
      "signals_fired": 0,
      "triggered": []
    }
  ]
}
```

---

## Step 4 — Tune the Conviction Threshold

The threshold defaults to 4 (seeded into `tenant_config` by `seed_tenant_defaults()`). To lower it for testing:

**fish**
```fish
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
UPDATE tenant_config
SET conviction_threshold = 2
WHERE tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b';
"
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
UPDATE tenant_config
SET conviction_threshold = 2
WHERE tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b';
"
```

Reset to production default when done:
```sql
UPDATE tenant_config SET conviction_threshold = 4 WHERE tenant_id = '...';
```

---

## Architecture Notes

**Monorepo pattern:** Signal Scanner and Orchestrator live in `agents/` alongside `agent_notifier.py`. In Cloud Run, the Flask app (`api/app.py`) is the only deployed process — both `agents/` and `fastMCPTest/` are bundled in the same container image.

**Direct tool imports:** `scanner.py` imports `get_rsi`, `get_macd`, etc. directly from `fastMCPTest/stock_price_server.py` by inserting the `fastMCPTest/` directory into `sys.path`. This works in a monorepo without an additional network hop. The MCP servers' FastMCP definitions are not invoked — only their Python functions.

**No Pub/Sub in Phase 4:** The Pub/Sub escalation queue (for Deep Analysis) is Phase 5. Signals that cross the threshold write to `agent_signals` and send a Discord alert. The `escalated` column on `agent_signals` is set to `false` by default; Phase 5 will flip it to `true` when the deep-analysis agent processes the signal.

---

## Gotchas Reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: stock_price_server` | `fastMCPTest/` not on sys.path | Ensure `scanner.py` sets `sys.path.insert(0, ...)` before the import |
| `No open positions to scan` | No rows in `positions` with `sale_date IS NULL` | Insert positions via Step 2 above |
| `RLS violation on agent_signals INSERT` | `get_db()` called without `tenant_id` | Ensure `_write_signal()` uses `get_db(tenant_id=tenant_id)` |
| `psycopg2.OperationalError: Connection refused 5433` | Auth Proxy not running | Start `./cloud-sql-proxy ... --port=5433` in a separate terminal |
| `KeyError: conviction_threshold` | `tenant_config` row not seeded | Run `SELECT seed_tenant_defaults('<tenant_uuid>'::UUID)` in psql |
| Discord alert not firing | Score below threshold | Lower `conviction_threshold` in `tenant_config` to test (Step 4) |

---

*Runbook prepared April 2026. Covers Phase 4 of the Agentic Market Intelligence System — GCP Edition.*
