# Phase 7 — Calibration & Hardening Runbook
### Stock Portfolio Manager — Agentic Market Intelligence System

---

## Overview

This runbook documents Phase 7: production hardening. Goal: the system runs unattended for a full trading week without intervention.

**Prerequisites:** Phases 1–6 complete.

---

## What Was Built in This Phase

| File | Purpose |
|------|---------|
| `agents/circuit_breaker.py` | Market hours gate + per-tool error rate breaker |
| `agents/retry.py` | Exponential backoff decorator/function for tool calls |
| `agents/structured_logging.py` | JSON logging for Cloud Logging, human-readable locally |
| `agents/deep_analysis/analyzer.py` | Added `threading.Semaphore` — max 3 concurrent runs |
| `agents/orchestrator/orchestrator.py` | Circuit breaker checks in `/run-signal-scanner` and `/run-deep-analysis` |
| `api/maintenance.py` | `POST /maintenance/prune` + `GET /maintenance/health` |
| `api/app.py` | Registered `maintenance_bp` |

---

## 1 — Circuit Breakers

### Market Hours Gate

`/run-signal-scanner` now checks `require_market_open()` before running. Outside trading hours it returns immediately:

```json
{"skipped": true, "reason": "Market is closed at 2026-04-12 08:15 ET. Agents only run Mon–Fri 09:25–16:00 ET."}
```

To force a run outside market hours (manual testing):

**fish / bash**
```bash
curl -s -X POST http://localhost:5001/run-signal-scanner \
  -H "Content-Type: application/json" \
  -d '{"force": true}' | python3 -m json.tool
```

Environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CIRCUIT_BREAKER_ENABLED` | `true` | Set `false` to disable all breakers (tests) |
| `MARKET_PRE_RUN_MINUTES` | `5` | Minutes before 9:30 AM open that jobs are allowed |

### Tool Error Rate Breaker

If a tool (e.g. `get_rsi`) errors more than `TOOL_ERROR_THRESHOLD` times in a 5-minute window, its breaker opens and blocks further calls for `TOOL_COOLDOWN_SECONDS`. The `GET /maintenance/health` endpoint shows breaker state:

```bash
curl -s http://localhost:5001/maintenance/health | python3 -m json.tool
```

```json
{
  "circuit_breakers": {
    "get_rsi": {"state": "open", "resets_in_seconds": 87}
  },
  "market_open": false,
  "status": "ok",
  "timestamp": "2026-04-12T10:00:01.123456"
}
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `TOOL_ERROR_THRESHOLD` | `5` | Max errors per 5-min window before tripping |
| `TOOL_COOLDOWN_SECONDS` | `120` | Seconds the breaker stays open |

---

## 2 — Retry with Exponential Backoff

Use `with_retry()` or `@retry` from `agents/retry.py` when calling tools in new code:

```python
from agents.retry import with_retry, retry

# Functional form
data = with_retry(get_rsi, "AAPL", max_attempts=3, base_delay=1.0)

# Decorator form
@retry(max_attempts=3, base_delay=1.0)
def fetch_price(symbol: str) -> dict:
    return get_stock_price(symbol)
```

Each retry doubles the delay with ±20% jitter: 1 s → ~2 s → ~4 s. Errors are automatically recorded in the circuit breaker.

| Variable | Default | Purpose |
|----------|---------|---------|
| `RETRY_MAX_ATTEMPTS` | `3` | Default attempt count |
| `RETRY_BASE_DELAY` | `1.0` | Seconds before second attempt |

---

## 3 — Deep Analysis Concurrency Limit

The Deep Analysis pipeline caps at `DEEP_ANALYSIS_MAX_CONCURRENT` (default 3) simultaneous runs per Cloud Run instance. Overflow returns HTTP 429, which Pub/Sub treats as a NACK and retries via the dead-letter policy.

| Variable | Default | Purpose |
|----------|---------|---------|
| `DEEP_ANALYSIS_MAX_CONCURRENT` | `3` | Max concurrent pipeline runs |

### Pub/Sub Dead-Letter Setup

Configure a dead-letter topic on the escalation subscription so overflowed messages are retried automatically:

**fish / bash**
```bash
# Create dead-letter topic
gcloud pubsub topics create deep-analysis-dead-letter \
  --project=stock-portfolio-tfowler

# Attach dead-letter policy to the subscription (max 5 delivery attempts)
gcloud pubsub subscriptions modify-push-config deep-analysis-push \
  --project=stock-portfolio-tfowler

gcloud pubsub subscriptions update deep-analysis-push \
  --dead-letter-topic=deep-analysis-dead-letter \
  --max-delivery-attempts=5 \
  --project=stock-portfolio-tfowler

# Grant Pub/Sub permission to publish to the dead-letter topic
PROJECT_NUMBER=$(gcloud projects describe stock-portfolio-tfowler --format="value(projectNumber)")
gcloud pubsub topics add-iam-policy-binding deep-analysis-dead-letter \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role="roles/pubsub.publisher" \
  --project=stock-portfolio-tfowler
```

To inspect dead-lettered messages:

**fish / bash**
```bash
gcloud pubsub subscriptions pull deep-analysis-dead-letter-sub \
  --limit=10 --auto-ack \
  --project=stock-portfolio-tfowler
```

---

## 4 — Structured Logging

All new agent code uses `get_logger(__name__)` from `agents/structured_logging.py`.

- **Cloud Run** (`K_SERVICE` env var present): JSON output on stderr — Cloud Logging ingests automatically with severity, timestamp, and structured fields.
- **Local dev**: Human-readable `2026-04-12 10:00:01 [I] agents.orchestrator: Signal scan complete  tenants=1  elapsed=4.2`

To view logs in Cloud Logging after deployment:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND jsonPayload.symbol="AAPL"' \
  --project=stock-portfolio-tfowler \
  --limit=20 \
  --format=json
```

To create a Cloud Logging alert for error rate:

```bash
# Create a log-based metric for ERROR severity in agent logs
gcloud logging metrics create agent-error-rate \
  --description="Agent ERROR log entries" \
  --log-filter='resource.type="cloud_run_revision" AND severity=ERROR' \
  --project=stock-portfolio-tfowler
```

Then create an alerting policy in Cloud Monitoring to notify when `agent-error-rate > 10` per minute.

---

## 5 — Data Retention

### Manual prune (admin only)

Requires a valid admin JWT cookie. From a logged-in browser session or with curl + cookie:

**fish / bash**
```bash
curl -s -X POST http://localhost:5001/maintenance/prune \
  -H "Content-Type: application/json" \
  --cookie "session=<JWT_TOKEN>" | python3 -m json.tool
```

Expected response:
```json
{
  "deleted": {"agent_signals": 142, "agent_recommendations": 3},
  "elapsed_seconds": 0.08,
  "pruned_at": "2026-04-12T02:00:01.123456",
  "retention": {
    "agent_signals_days": 90,
    "agent_recommendations_days": 365
  }
}
```

### Cloud Scheduler nightly job

```bash
gcloud scheduler jobs create http prune-agent-data \
  --schedule="0 2 * * *" \
  --uri="https://<CLOUD-RUN-URL>/maintenance/prune" \
  --http-method=POST \
  --oidc-service-account-email=<SERVICE-ACCOUNT>@stock-portfolio-tfowler.iam.gserviceaccount.com \
  --location=us-central1 \
  --project=stock-portfolio-tfowler
```

> The Cloud Scheduler service account must have the `admin` role in the `users` table, or the prune endpoint must be updated to accept a Scheduler OIDC token directly. Simplest approach: add a `PRUNE_SECRET` env var and check it as a bearer token instead of the JWT cookie.

---

## 6 — Security Review

No code changes required — the security controls implemented in Phases 2–4 are already correct. This section documents what to verify before going live.

### Checklist

| Control | Implementation | Verify |
|---------|---------------|--------|
| CORS | Origins restricted to `localhost:5173` / `localhost:5001` | Update to production frontend URL before deploy |
| JWT expiry | 1-hour tokens; `require_auth` validates `exp` claim | Confirm clock skew tolerance is ≤ 30s |
| JWT secret | Stored in Secret Manager as `jwt-secret` | Rotate before first production tenant |
| RLS | All tenant tables have `CREATE POLICY tenant_isolation USING (tenant_id = current_setting('app.tenant_id', true)::UUID)` | Run `SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public'` to confirm all tables are covered |
| Secret Manager | App user has `roles/secretmanager.secretAccessor` only | Verify with `gcloud projects get-iam-policy stock-portfolio-tfowler` |
| HttpOnly cookies | `session_cookie()` sets `HttpOnly=True` | Confirm with browser DevTools → Application → Cookies |
| Pub/Sub auth | Push subscription uses OIDC service account | Verify with `gcloud pubsub subscriptions describe deep-analysis-push` |

### Verify RLS is enforced

**fish / bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
-- Should return 0 rows — RLS blocks access without tenant context
SELECT COUNT(*) FROM agent_signals;
"
```

Expected: `0` (RLS blocks the query when `app.tenant_id` is not set).

---

## 7 — Cloud Run Deployment Configuration

Recommended Cloud Run settings for the main service:

```bash
gcloud run deploy stock-portfolio-api \
  --image=gcr.io/stock-portfolio-tfowler/stock-portfolio-api:latest \
  --region=us-central1 \
  --concurrency=10 \
  --max-instances=3 \
  --min-instances=0 \
  --set-env-vars="DEEP_ANALYSIS_MAX_CONCURRENT=3,GCP_PROJECT=stock-portfolio-tfowler" \
  --set-secrets="DATABASE_URL=db-connection-string:latest,JWT_SECRET=jwt-secret:latest,..." \
  --service-account=<SERVICE-ACCOUNT>@stock-portfolio-tfowler.iam.gserviceaccount.com \
  --project=stock-portfolio-tfowler
```

With `--concurrency=10` and `--max-instances=3`, the system handles up to 30 concurrent HTTP requests while the semaphore caps Deep Analysis to 3 per instance (9 total across the fleet).

---

## Gotchas Reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| Signal scanner always returns `skipped: true` | Running outside 09:25–16:00 ET | Add `"force": true` to request body, or set `CIRCUIT_BREAKER_ENABLED=false` for tests |
| `ToolErrorRateExceeded: Tool 'get_rsi' error breaker is open` | yfinance rate-limited; 5+ errors in 5 min | Wait 2 minutes for auto-reset, or call `reset_tool_breaker("get_rsi")` in a Python console |
| Deep Analysis returns HTTP 429 | Semaphore at capacity | Pub/Sub will retry automatically; or reduce concurrent load |
| `GET /maintenance/health` returns `"status": "degraded"` | DB unreachable (Auth Proxy not running) | Start `./cloud-sql-proxy ... --port=5433` |
| `POST /maintenance/prune` returns 401 | Missing or expired JWT | Re-authenticate at `/auth/login` |

---

*Runbook prepared April 2026. Covers Phase 7 of the Agentic Market Intelligence System — GCP Edition.*
