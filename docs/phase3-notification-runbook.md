# Phase 3 — Notification System Extension Runbook
### Stock Portfolio Manager — Agentic Market Intelligence System

---

## Overview

This runbook documents the exact steps to extend the existing Discord notification system for multi-tenant, continuous agent use. It reflects what was actually done, with commands for both **fish** and **bash** shells.

**Prerequisites:**
- Phase 1 complete (GCP project, Cloud SQL, schema migrated, tenant seeded)
- Phase 2 complete (OAuth login working, `DATABASE_URL` env var pattern established)
- Auth Proxy running on port 5433

---

## What Changes in This Phase

| Component | Before | After |
|-----------|--------|-------|
| Webhook URL | Single `DISCORD_WEBHOOK_URL` from `.env` | Per-tenant URL stored in `tenants.discord_webhook_url` |
| Deduplication | File-based `notification.log` (never rotates) | PostgreSQL `alert_dedup` table with time-windowed suppression per `(tenant_id, symbol, alert_type)` |
| Alert methods | `send_notifications(embed)` only | `send_signal_alert`, `send_recommendation`, `send_portfolio_alert`, `send_morning_report`, `send_heartbeat` |
| Suppress windows | N/A | Configurable per alert type in `alert_dedup_config` table — no deployment needed to tune |

The existing `Notifier` class and `main.py` are **unchanged** — they continue to use `notification.log` and the single `.env` webhook for harvest and options alerts (phased retirement plan, decision 10).

---

## Step 1 — Create the `agents/` Package

Create the agents directory and its `__init__.py`:

**fish**
```fish
mkdir -p agents
touch agents/__init__.py
touch db/__init__.py
```

**bash**
```bash
mkdir -p agents
touch agents/__init__.py
touch db/__init__.py
```

> **Note:** `db/__init__.py` is also needed if not already present — Python requires it to treat `db/` as a package importable from the project root.

---

## Step 2 — Create `agents/agent_notifier.py`

The file is present in the repository at `agents/agent_notifier.py`. Key design decisions:

**Why skip `super().__init__()`:**
The base `Notifier.__init__` requires a `Portfolio` object. `AgentNotifier` is not portfolio-aware — it receives a `tenant_id` instead. Skipping `super().__init__()` is intentional and safe because `AgentNotifier` overrides every method it uses from the parent.

**Why override `send_notifications()`:**
The parent implementation checks and writes `notification.log`. For agents running every 15 minutes, the file-based log would permanently suppress any signal that fired once. The override replaces this with a time-windowed PostgreSQL query.

**Dedup query pattern:**
```sql
SELECT fired_at FROM alert_dedup
WHERE tenant_id = :tid
  AND symbol     = :sym
  AND alert_type = :at
  AND fired_at   > NOW() - (:minutes * INTERVAL '1 minute')
ORDER BY fired_at DESC
LIMIT 1
```

The suppress window (`minutes`) is fetched from `alert_dedup_config` per `(tenant_id, alert_type)` — tunable without a deployment.

**Synthetic symbol for reports:**
Morning/closing reports are portfolio-wide — no single symbol. The string `"__portfolio__"` is used as the symbol key in `alert_dedup` for report dedup entries.

**Heartbeat bypasses dedup:**
`send_heartbeat()` calls `_post_to_discord()` directly, skipping `send_notifications()`. Heartbeats should always fire.

---

## Step 3 — Create the Smoke Test Script

The test script is at `scripts/test_agent_notifier.py`. Create the `scripts/` directory if needed:

**fish**
```fish
mkdir -p scripts
```

**bash**
```bash
mkdir -p scripts
```

---

## Step 4 — Run the Smoke Test

With the Auth Proxy running on port 5433:

**fish**
```fish
set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
source .venv/bin/activate.fish; and python scripts/test_agent_notifier.py
```

**bash**
```bash
export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
source .venv/bin/activate && python scripts/test_agent_notifier.py
```

Expected output:
```
Webhook URL     : None
Suppressed before record : False
Suppressed after record  : True
2026-04-13 19:31:47 Suppressed duplicate: signal_buy for NVDA
All assertions passed.
```

- `Webhook URL: None` — correct; no webhook configured for the tenant yet
- `Suppressed before record: False` — first fire is never suppressed
- `Suppressed after record: True` — immediately suppressed within the 2-hour window
- Final `send_signal_alert` call correctly logs "Suppressed duplicate" without crashing

---

## Step 5 — Configure a Discord Webhook for the Tenant

To enable actual Discord posting, update the tenant's webhook URL in the database.

Create a webhook in your Discord server:
1. Open Discord → Server Settings → Integrations → Webhooks
2. Click **New Webhook**
3. Name it (e.g. `Stock Portfolio Alerts`), assign it to a channel
4. Copy the webhook URL

Store it in Secret Manager and update the tenant record:

**fish**
```fish
echo -n "<YOUR_WEBHOOK_URL>" | gcloud secrets create discord-webhook-url --data-file=-
```

**bash**
```bash
echo -n "<YOUR_WEBHOOK_URL>" | gcloud secrets create discord-webhook-url --data-file=-
```

Update the tenant record:

**fish**
```fish
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
set WEBHOOK (gcloud secrets versions access latest --secret=discord-webhook-url)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
UPDATE tenants
SET discord_webhook_url = '$WEBHOOK'
WHERE id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b';
"
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
WEBHOOK=$(gcloud secrets versions access latest --secret=discord-webhook-url)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
UPDATE tenants
SET discord_webhook_url = '$WEBHOOK'
WHERE id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b';
"
```

> **Note:** The webhook URL is stored in the database (not `.env`) so each tenant can have their own channel. Future per-channel routing (signals / recommendations / portfolio-health) extends this by adding additional webhook columns to the `tenants` table — the schema supports it without a migration.

---

## Step 6 — Test a Live Heartbeat

Once a webhook URL is set, verify Discord posting works:

**fish**
```fish
set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
set -x DATABASE_URL "postgresql+psycopg2://app_user:$DB_PASS@127.0.0.1:5433/stock_portfolio"
source .venv/bin/activate.fish; and python3 -c "
import sys; sys.path.insert(0, '.')
from agents.agent_notifier import AgentNotifier
n = AgentNotifier('7d3cc53d-a909-4574-bbf7-c3c02ee0940b')
n.send_heartbeat(status='ok', message='Phase 3 smoke test — AgentNotifier live.')
"
```

**bash**
```bash
export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
export DATABASE_URL="postgresql+psycopg2://app_user:$DB_PASS@127.0.0.1:5433/stock_portfolio"
source .venv/bin/activate && python3 -c "
import sys; sys.path.insert(0, '.')
from agents.agent_notifier import AgentNotifier
n = AgentNotifier('7d3cc53d-a909-4574-bbf7-c3c02ee0940b')
n.send_heartbeat(status='ok', message='Phase 3 smoke test — AgentNotifier live.')
"
```

A grey embed should appear in your Discord channel within a few seconds.

---

## Alert Type Reference

### Dedup Alert Type Keys

These are the exact strings used in `alert_dedup_config` and `alert_dedup`. Suppression windows are seeded by `seed_tenant_defaults()` and configurable per tenant without a deployment.

| Alert Type Key | Method | Default Window |
|---------------|--------|---------------|
| `signal_buy` | `send_signal_alert(..., direction="buy")` | 120 min |
| `signal_sell` | `send_signal_alert(..., direction="sell")` | 120 min |
| `recommendation_buy` | `send_recommendation(..., recommendation="BUY")` | 240 min |
| `recommendation_sell` | `send_recommendation(..., recommendation="SELL")` | 240 min |
| `recommendation_hold` | `send_recommendation(..., recommendation="HOLD")` | 1440 min |
| `portfolio_at_risk` | `send_portfolio_alert("portfolio_at_risk", ...)` | 120 min |
| `portfolio_inst_exit` | `send_portfolio_alert("portfolio_inst_exit", ...)` | 120 min |
| `portfolio_report` | `send_morning_report(...)` | 720 min |

### Tuning a Suppression Window

To change a window without redeploying:

**fish**
```fish
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
UPDATE alert_dedup_config
SET suppress_minutes = 60
WHERE tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b'
  AND alert_type = 'signal_buy';
"
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
UPDATE alert_dedup_config
SET suppress_minutes = 60
WHERE tenant_id = '7d3cc53d-a909-4574-bbf7-c3c02ee0940b'
  AND alert_type = 'signal_buy';
"
```

---

## File Reference

| File | Purpose |
|------|---------|
| `agents/__init__.py` | Package marker |
| `agents/agent_notifier.py` | `AgentNotifier` class |
| `db/__init__.py` | Package marker |
| `db/config.py` | Secret resolution (Phase 2) |
| `db/database.py` | SQLAlchemy engine + `get_db()` (Phase 2) |
| `scripts/test_agent_notifier.py` | Smoke test |

---

## Gotchas Reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Webhook URL: None` during test | No webhook set on tenant record | Expected during development; set via `UPDATE tenants SET discord_webhook_url = ...` when ready |
| `AssertionError: Should not be suppressed before first fire` | Stale dedup record from a previous test run | Delete the row: `DELETE FROM alert_dedup WHERE symbol = 'NVDA' AND alert_type = 'signal_buy'` |
| `psycopg2.OperationalError: Connection refused 5433` | Auth Proxy not running | Start `./cloud-sql-proxy ... --port=5433` in a separate terminal |
| `RLS violation` on INSERT to `alert_dedup` | `get_db()` called without `tenant_id` | Use `get_db(tenant_id=self.tenant_id)` in `_record_fired` and `_is_suppressed` |
| `TypeError: 'NoneType' object is not subscriptable` on `_load_webhook` | Tenant UUID not found in database | Verify the tenant UUID matches what was seeded in Phase 1 |

---

*Runbook prepared April 2026. Covers Phase 3 of the Agentic Market Intelligence System — GCP Edition.*
