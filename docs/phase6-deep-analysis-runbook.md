# Phase 6 — Deep Analysis Agent Runbook
### Stock Portfolio Manager — Agentic Market Intelligence System

---

## Overview

This runbook documents Phase 6: the Deep Analysis Agent. It runs a 6-phase, 20-tool conviction pipeline on a single symbol, synthesises all signals into a BUY / SELL / HOLD / AVOID recommendation, writes it to `agent_recommendations`, and sends a Discord embed.

**Triggers:**
- Pub/Sub push from Signal Scanner (score ≥ ±4, priority P3)
- Pub/Sub push from Portfolio Monitor (AT RISK → P1, INSTITUTIONAL EXIT → P2)
- Manual API call to `POST /run-deep-analysis`

**Prerequisites:**
- Phase 1–5 complete
- Auth Proxy running on port 5433
- `google-cloud-pubsub>=2.21.0` installed (`pip install -r requirements.txt`)

---

## What Was Built in This Phase

| File | Purpose |
|------|---------|
| `agents/deep_analysis/__init__.py` | Package marker |
| `agents/deep_analysis/analyzer.py` | 6-phase pipeline, synthesis, `analyze()` |
| `agents/pubsub.py` | `publish_escalation()` — wraps google-cloud-pubsub |
| `agents/orchestrator/orchestrator.py` | Added `POST /run-deep-analysis` |
| `agents/signal_scanner/scanner.py` | Added `publish_escalation()` call on trigger |
| `agents/portfolio_monitor/monitor.py` | Added `publish_escalation()` calls for AT RISK / INST EXIT |
| `scripts/test_deep_analysis.py` | Smoke test for the full pipeline |

---

## Pipeline Overview

| Phase | Tools | Max Score |
|-------|-------|-----------|
| 1 — Price Structure | `get_stock_price`, `get_candlestick_patterns`, `get_higher_lows`, `get_gap_analysis` | ±4 |
| 2 — Momentum | `get_rsi`, `get_macd`, `get_stochastic` | ±6 |
| 3 — Volume & Institutional | `get_vwap`, `get_volume_analysis`, `get_obv` | ±6 |
| 4 — Options Intelligence | `get_delta_adjusted_oi`, `get_full_options_chain`, `analyze_options_symbol`, `get_unusual_calls` | ±5 |
| 5 — Market Structure | `get_dark_pool`, `get_short_interest`, `get_bid_ask_spread` | ±4 |
| 6 — Risk & Sentiment | `get_historical_drawdown`, `get_stop_loss_analysis`, `get_news` | ±2 |

**Score → Recommendation mapping:**

| Score | Recommendation | Conviction |
|-------|---------------|------------|
| ≥ +12 | BUY | HIGH |
| +7 to +11 | BUY | MEDIUM |
| +3 to +6 | BUY | LOW |
| −2 to +2 | HOLD | MEDIUM |
| −3 to −6 | SELL | LOW |
| −7 to −11 | SELL | MEDIUM |
| ≤ −12 | SELL | HIGH |

**Dedup:** BUY/SELL suppressed per `(tenant_id, symbol, alert_type)` for 4 hours. HOLD for 24 hours. Configured in `alert_dedup_config`.

---

## Step 1 — Install Dependencies

**fish**
```fish
source .venv/bin/activate.fish; and pip install -r requirements.txt
```

**bash**
```bash
source .venv/bin/activate && pip install -r requirements.txt
```

---

## Step 2 — Create the Pub/Sub Topic and Subscription

Run these once during GCP setup:

**fish / bash**
```bash
gcloud pubsub topics create deep-analysis-escalation \
  --project=stock-portfolio-tfowler

gcloud pubsub subscriptions create deep-analysis-push \
  --topic=deep-analysis-escalation \
  --push-endpoint=https://<CLOUD-RUN-URL>/run-deep-analysis \
  --push-auth-service-account=<SERVICE-ACCOUNT>@stock-portfolio-tfowler.iam.gserviceaccount.com \
  --project=stock-portfolio-tfowler
```

> Replace `<CLOUD-RUN-URL>` with your deployed Cloud Run service URL (available after Phase 7 deployment). For local development, leave this step until deployment.

---

## Step 3 — Run the Smoke Test

Set `PUBSUB_ENABLED=false` for local runs (avoids ADC requirements):

**fish**
```fish
set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
set -x PUBSUB_ENABLED false
source .venv/bin/activate.fish; and python scripts/test_deep_analysis.py
```

**bash**
```bash
export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
export PUBSUB_ENABLED=false
source .venv/bin/activate && python scripts/test_deep_analysis.py
```

Expected output (values vary):
```
Running deep analysis for AAPL (this may take 30–60s)...
Completed in 38.2s
Symbol:         AAPL
Score:          +5
Recommendation: BUY
Conviction:     LOW
Entry:          $183.50 – $189.50
Target:         $201.20
Stop:           $178.40

Phase scores:   {'price_structure': 1, 'momentum': 2, 'volume_inst': 1, 'options_intel': 1, 'market_structure': 0, 'risk_sentiment': 0}

Bull case (3 points):
  + RSI 42.1 — oversold, mean reversion likely
  + MACD bullish crossover
  + VWAP reclaim (moderate)

Bear case (1 points):
  - MM net long delta — sell pressure on rallies (resistance)

All assertions passed.
```

---

## Step 4 — Test the Endpoint Manually

Start Flask (with Auth Proxy running):

**fish**
```fish
set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
set -x DATABASE_URL "postgresql+psycopg2://app_user:$DB_PASS@127.0.0.1:5433/stock_portfolio"
set -x GOOGLE_OAUTH_CLIENT_ID (gcloud secrets versions access latest --secret=google-oauth-client-id)
set -x GOOGLE_OAUTH_CLIENT_SECRET (gcloud secrets versions access latest --secret=google-oauth-client-secret)
set -x JWT_SECRET (gcloud secrets versions access latest --secret=jwt-secret)
set -x PUBSUB_ENABLED false
source .venv/bin/activate.fish; and python -m api.app
```

**bash**
```bash
export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
export DATABASE_URL="postgresql+psycopg2://app_user:$DB_PASS@127.0.0.1:5433/stock_portfolio"
export GOOGLE_OAUTH_CLIENT_ID=$(gcloud secrets versions access latest --secret=google-oauth-client-id)
export GOOGLE_OAUTH_CLIENT_SECRET=$(gcloud secrets versions access latest --secret=google-oauth-client-secret)
export JWT_SECRET=$(gcloud secrets versions access latest --secret=jwt-secret)
export PUBSUB_ENABLED=false
source .venv/bin/activate && python -m api.app
```

Trigger a manual deep analysis:

**fish / bash**
```bash
curl -s -X POST http://localhost:5001/run-deep-analysis \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "7d3cc53d-a909-4574-bbf7-c3c02ee0940b", "symbol": "AAPL", "source": "manual"}' \
  | python3 -m json.tool
```

Expected response:
```json
{
  "conviction": "LOW",
  "elapsed_seconds": 41.3,
  "recommendation": "BUY",
  "run_at": "2026-04-12T10:00:01.123456",
  "score": 5,
  "symbol": "AAPL"
}
```

A Discord embed should appear in your channel.

---

## Step 5 — Simulate a Pub/Sub Push

To test the Pub/Sub path locally, send the push envelope format:

**fish / bash**
```bash
PAYLOAD=$(echo -n '{"tenant_id":"7d3cc53d-a909-4574-bbf7-c3c02ee0940b","symbol":"NVDA","source":"signal_scanner","priority":"P3"}' | base64)
curl -s -X POST http://localhost:5001/run-deep-analysis \
  -H "Content-Type: application/json" \
  -d "{\"message\":{\"data\":\"$PAYLOAD\",\"messageId\":\"123\"}}" \
  | python3 -m json.tool
```

---

## Step 6 — Verify the Recommendation in the Database

**fish**
```fish
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
SET app.tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b';
SELECT symbol, recommendation, conviction, entry_low, entry_high, price_target, stop_loss, fired_at
FROM agent_recommendations
WHERE tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b'
ORDER BY fired_at DESC
LIMIT 5;
"
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
SET app.tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b';
SELECT symbol, recommendation, conviction, entry_low, entry_high, price_target, stop_loss, fired_at
FROM agent_recommendations
WHERE tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b'
ORDER BY fired_at DESC
LIMIT 5;
"
```

---

## Pub/Sub Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PUBSUB_ENABLED` | `true` | Set to `false` for local dev (skips publishing silently) |
| `GCP_PROJECT` | `stock-portfolio-tfowler` | GCP project for topic path |
| `PUBSUB_ESCALATION_TOPIC` | `deep-analysis-escalation` | Pub/Sub topic name |

---

## Gotchas Reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: options_analysis` | `fastMCPTest/` not on sys.path | Ensure `analyzer.py` inserts `fastMCPTest/` into `sys.path` |
| Test takes > 120s | `get_full_options_chain` is slow for some symbols | Acceptable; reduce `max_expirations` if needed |
| `google.api_core.exceptions.NotFound` on publish | Pub/Sub topic not created | Run Step 2, or set `PUBSUB_ENABLED=false` for local dev |
| `Suppressed duplicate: recommendation_buy` | BUY already sent within 4h window | Delete the dedup row or wait for the window to expire |
| Discord embed not sent | No webhook on tenant | `UPDATE tenants SET discord_webhook_url = '...' WHERE id = '...'` |
| `psycopg2.OperationalError: Connection refused 5433` | Auth Proxy not running | Start `./cloud-sql-proxy ... --port=5433` |
| Score always 0 | All 20 tools silently erroring | Check stderr for per-phase `_error` keys in the result |

---

*Runbook prepared April 2026. Covers Phase 6 of the Agentic Market Intelligence System — GCP Edition.*
