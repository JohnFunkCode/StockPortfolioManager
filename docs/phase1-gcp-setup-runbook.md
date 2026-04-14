# Phase 1 — GCP Foundation Setup Runbook
### Stock Portfolio Manager — Agentic Market Intelligence System

---

## Overview

This runbook documents the exact steps to provision the Phase 1 GCP infrastructure from scratch. It reflects what was actually done, with commands provided for both **fish** and **bash** shells.

**Time to complete:** ~30 minutes (excluding Cloud SQL provisioning which takes 3–5 minutes)

**Prerequisites:**
- `gcloud` CLI installed (`brew install google-cloud-sdk`)
- Python 3.12 virtualenv at `.venv/`
- Billing account available in GCP

---

## Step 1 — Authenticate with GCP

If you have stale or missing Application Default Credentials, clear them first:

**fish**
```bash
gcloud auth application-default revoke
```

**bash**
```bash
gcloud auth application-default revoke
```

Then do a fresh login using `--no-browser` to avoid browser auth issues, and explicitly request the required scope:

**fish**
```bash
gcloud auth application-default login --no-browser --scopes="https://www.googleapis.com/auth/cloud-platform"
```

**bash**
```bash
gcloud auth application-default login --no-browser --scopes="https://www.googleapis.com/auth/cloud-platform"
```

This prints a URL. Open it in a browser, sign in, grant permissions, and paste the authorization code back into the terminal.

> **Note:** The standard `gcloud auth application-default login` (without `--no-browser`) can fail on macOS with a scope consent error. Always use `--no-browser` for this project.

---

## Step 2 — Create the GCP Project

**fish**
```bash
gcloud projects create stock-portfolio-tfowler --name="Stock Portfolio Manager"
gcloud config set project stock-portfolio-tfowler
```

**bash**
```bash
gcloud projects create stock-portfolio-tfowler --name="Stock Portfolio Manager"
gcloud config set project stock-portfolio-tfowler
```

Fix the quota project warning that appears after setting the project:

**fish**
```bash
gcloud auth application-default set-quota-project stock-portfolio-tfowler
```

**bash**
```bash
gcloud auth application-default set-quota-project stock-portfolio-tfowler
```

Verify:

**fish**
```bash
gcloud config get-value project
# Expected output: stock-portfolio-tfowler
```

**bash**
```bash
gcloud config get-value project
# Expected output: stock-portfolio-tfowler
```

> **Note:** GCP project IDs are globally unique. If `stock-portfolio-tfowler` is taken, append a different suffix.

---

## Step 3 — Enable Required APIs

**fish**
```bash
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  pubsub.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  vpcaccess.googleapis.com
```

**bash**
```bash
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  pubsub.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  vpcaccess.googleapis.com
```

Wait for: `Operation "operations/..." finished successfully.`

---

## Step 4 — Link Billing Account

List available billing accounts:

**fish**
```bash
gcloud billing accounts list
```

**bash**
```bash
gcloud billing accounts list
```

Link the billing account to the project (replace `ACCOUNT_ID` with the value from the list):

**fish**
```bash
gcloud billing projects link stock-portfolio-tfowler --billing-account=ACCOUNT_ID
```

**bash**
```bash
gcloud billing projects link stock-portfolio-tfowler --billing-account=ACCOUNT_ID
```

Expected output includes `billingEnabled: true`.

---

## Step 5 — Provision Cloud SQL

**fish**
```bash
gcloud sql instances create stock-portfolio-db \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region=us-central1 \
  --backup-start-time=03:00 \
  --availability-type=zonal
```

**bash**
```bash
gcloud sql instances create stock-portfolio-db \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region=us-central1 \
  --backup-start-time=03:00 \
  --availability-type=zonal
```

This takes 3–5 minutes and blocks until complete. Expected output:

```
NAME                DATABASE_VERSION  LOCATION       TIER         PRIMARY_ADDRESS  STATUS
stock-portfolio-db  POSTGRES_15       us-central1-f  db-f1-micro  x.x.x.x          RUNNABLE
```

Note the `PRIMARY_ADDRESS` — you will not need it directly (the Auth Proxy handles connectivity) but record it for reference.

> **Note:** Do NOT use `--no-assign-ip`. Cloud SQL requires at least one connectivity method enabled. We use public IP + Auth Proxy rather than private IP to avoid the additional VPC setup steps required for private IP.

---

## Step 6 — Create Database and App User

**fish**
```bash
gcloud sql databases create stock_portfolio --instance=stock-portfolio-db
gcloud sql users create app_user --instance=stock-portfolio-db --password=(openssl rand -base64 24)
```

**bash**
```bash
gcloud sql databases create stock_portfolio --instance=stock-portfolio-db
gcloud sql users create app_user --instance=stock-portfolio-db --password=$(openssl rand -base64 24)
```

> **Note:** The password set here is a throwaway — it gets overwritten in Step 7 with the Secret Manager version.

---

## Step 7 — Store Credentials in Secret Manager

Generate and store the app user password:

**fish**
```bash
set DB_PASS (openssl rand -base64 24)
echo $DB_PASS | gcloud secrets create db-app-user-password --data-file=-
echo "Stored: $DB_PASS"
```

**bash**
```bash
DB_PASS=$(openssl rand -base64 24)
echo $DB_PASS | gcloud secrets create db-app-user-password --data-file=-
echo "Stored: $DB_PASS"
```

Store the connection string:

**fish**
```bash
echo -n "postgresql+psycopg2://app_user@127.0.0.1:5433/stock_portfolio" | gcloud secrets create db-connection-string --data-file=-
```

**bash**
```bash
echo -n "postgresql+psycopg2://app_user@127.0.0.1:5433/stock_portfolio" | gcloud secrets create db-connection-string --data-file=-
```

> **Note:** The connection string uses `127.0.0.1:5433` because all connections go through the Cloud SQL Auth Proxy (see Step 9). The public IP of the Cloud SQL instance is not used directly.

Sync the Cloud SQL user password to match what is stored in Secret Manager:

**fish**
```bash
gcloud sql users set-password app_user \
  --instance=stock-portfolio-db \
  --password=(gcloud secrets versions access latest --secret=db-app-user-password)
```

**bash**
```bash
gcloud sql users set-password app_user \
  --instance=stock-portfolio-db \
  --password=$(gcloud secrets versions access latest --secret=db-app-user-password)
```

Verify secrets are stored:

**fish**
```bash
gcloud secrets list
# Expected: db-app-user-password, db-connection-string
```

**bash**
```bash
gcloud secrets list
# Expected: db-app-user-password, db-connection-string
```

---

## Step 8 — Install Python Dependencies

Add to `requirements.txt`:

```
alembic>=1.13.0
psycopg2-binary>=2.9.0
sqlalchemy>=2.0.0
google-cloud-secret-manager>=2.20.0
cloud-sql-python-connector[psycopg2]>=1.10.0
```

Install:

**fish**
```bash
source .venv/bin/activate.fish; and pip install alembic psycopg2-binary sqlalchemy google-cloud-secret-manager "cloud-sql-python-connector[psycopg2]"
```

**bash**
```bash
source .venv/bin/activate && pip install alembic psycopg2-binary sqlalchemy google-cloud-secret-manager "cloud-sql-python-connector[psycopg2]"
```

> **Note:** The package is `cloud-sql-python-connector`, NOT `google-cloud-sql-connector`. The latter does not exist on PyPI.

---

## Step 9 — Set Up Cloud SQL Auth Proxy

Download the proxy binary (Apple Silicon Mac):

**fish**
```bash
curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.15.2/cloud-sql-proxy.darwin.arm64
chmod +x cloud-sql-proxy
```

**bash**
```bash
curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.15.2/cloud-sql-proxy.darwin.arm64
chmod +x cloud-sql-proxy
```

Start the proxy in a **separate terminal** and leave it running for all subsequent database operations:

**fish**
```bash
./cloud-sql-proxy stock-portfolio-tfowler:us-central1:stock-portfolio-db --port=5433
```

**bash**
```bash
./cloud-sql-proxy stock-portfolio-tfowler:us-central1:stock-portfolio-db --port=5433
```

> **Note:** Port 5433 is used to avoid conflicts with any local PostgreSQL instance on the default 5432.

> **Note:** The proxy must be running any time you run Alembic migrations or connect to the database locally.

---

## Step 10 — Initialise Alembic

**fish**
```bash
source .venv/bin/activate.fish; and alembic init db/migrations
```

**bash**
```bash
source .venv/bin/activate && alembic init db/migrations
```

This creates `alembic.ini` at the project root and `db/migrations/` with `env.py`, `script.py.mako`, and `versions/`.

Replace `db/migrations/env.py` with the version in this repo — it reads the database URL from:
1. `ALEMBIC_DB_URL` environment variable (used for local migrations via the proxy)
2. Secret Manager (used in GCP environments)
3. `alembic.ini` `sqlalchemy.url` (fallback only)

---

## Step 11 — Run Migrations

With the Auth Proxy running on port 5433, fetch the password and run the migration:

**fish**
```bash
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password); and source .venv/bin/activate.fish; and set -x ALEMBIC_DB_URL "postgresql+psycopg2://app_user:$DB_PASS@127.0.0.1:5433/stock_portfolio"; and alembic upgrade head
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password) && \
source .venv/bin/activate && \
export ALEMBIC_DB_URL="postgresql+psycopg2://app_user:$DB_PASS@127.0.0.1:5433/stock_portfolio" && \
alembic upgrade head
```

Expected output:

```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, Initial schema — tenants, users, portfolio, watchlist, agents, harvester, dedup config
```

> **Note (fish):** Command substitution uses `(command)` not `$(command)`. Environment variables are set with `set -x VAR value`. Fetch the password into a variable first — embedding the `gcloud` command directly inside a quoted string will not execute it, causing a password authentication failure.

> **Note (bash):** Use `export` to make `ALEMBIC_DB_URL` available to the `alembic` subprocess. A plain assignment without `export` will not be visible to child processes.

Verify all 12 tables were created:

**fish**
```bash
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password); and PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "\dt"
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password) && \
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "\dt"
```

> **Note:** Use `PGPASSWORD` + separate `-h`, `-p`, `-U`, `-d` flags for psql. Embedding the password in the connection URL string fails when the password contains special characters (base64 passwords often contain `+`, `/`, or `=`).

---

## Step 12 — Seed First Tenant

Get your Google OAuth subject ID:

**fish**
```bash
gcloud auth print-identity-token | python3 -c "import sys,json,base64; parts=sys.stdin.read().strip().split('.'); print(json.loads(base64.b64decode(parts[1]+'=='))['sub'])"
```

**bash**
```bash
gcloud auth print-identity-token | python3 -c "import sys,json,base64; parts=sys.stdin.read().strip().split('.'); print(json.loads(base64.b64decode(parts[1]+'=='))['sub'])"
```

Insert the tenant record and note the returned UUID:

**fish**
```bash
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password); and PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
INSERT INTO tenants (name) VALUES ('Your Name') RETURNING id;
"
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password) && \
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
INSERT INTO tenants (name) VALUES ('Your Name') RETURNING id;
"
```

Copy the returned UUID, then seed the config, dedup defaults, and admin user (replace `<TENANT_UUID>`, `<GOOGLE_SUB>`, and email):

**fish**
```bash
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password); and PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
INSERT INTO tenant_config (tenant_id, conviction_threshold, puts_budget, scanner_scope)
VALUES ('<TENANT_UUID>', 4, 1000.00, 'portfolio');

SELECT seed_tenant_defaults('<TENANT_UUID>');

INSERT INTO users (tenant_id, email, google_sub, role)
VALUES ('<TENANT_UUID>', 'your@email.com', '<GOOGLE_SUB>', 'admin');
"
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password) && \
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
INSERT INTO tenant_config (tenant_id, conviction_threshold, puts_budget, scanner_scope)
VALUES ('<TENANT_UUID>', 4, 1000.00, 'portfolio');

SELECT seed_tenant_defaults('<TENANT_UUID>');

INSERT INTO users (tenant_id, email, google_sub, role)
VALUES ('<TENANT_UUID>', 'your@email.com', '<GOOGLE_SUB>', 'admin');
"
```

Verify:

**fish**
```bash
set DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password); and PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
SELECT u.email, u.role, tc.conviction_threshold, tc.puts_budget, tc.scanner_scope
FROM users u
JOIN tenant_config tc ON tc.tenant_id = u.tenant_id;

SELECT alert_type, suppress_minutes FROM alert_dedup_config
WHERE tenant_id = '<TENANT_UUID>'
ORDER BY alert_type;
"
```

**bash**
```bash
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password) && \
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio -c "
SELECT u.email, u.role, tc.conviction_threshold, tc.puts_budget, tc.scanner_scope
FROM users u
JOIN tenant_config tc ON tc.tenant_id = u.tenant_id;

SELECT alert_type, suppress_minutes FROM alert_dedup_config
WHERE tenant_id = '<TENANT_UUID>'
ORDER BY alert_type;
"
```

Expected dedup windows:

| alert_type | suppress_minutes |
|-----------|-----------------|
| portfolio_at_risk | 120 |
| portfolio_inst_exit | 120 |
| portfolio_report | 720 |
| recommendation_buy | 240 |
| recommendation_hold | 1440 |
| recommendation_sell | 240 |
| signal_buy | 120 |
| signal_sell | 120 |

---

## Gotchas Reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `invalid_grant: Bad Request` on ADC login | Stale or corrupt `~/.config/gcloud/application_default_credentials.json` with empty `account` field | `rm ~/.config/gcloud/application_default_credentials.json` then re-run login with `--no-browser` |
| `scope is required but not consented` | Browser auth flow missing `cloud-platform` scope | Use `--no-browser --scopes="https://www.googleapis.com/auth/cloud-platform"` |
| `At least one of Public IP or Private IP must be enabled` | `--no-assign-ip` used without VPC private service access configured | Remove `--no-assign-ip`; use public IP + Auth Proxy instead |
| `Operation timed out` connecting to Cloud SQL public IP | No authorized networks configured on the instance | Use Cloud SQL Auth Proxy on localhost:5433 instead of direct IP |
| `password authentication failed` (fish) | `gcloud` command substitution inside a quoted string not executed — passed as literal | Fetch password into a variable first: `set DB_PASS (gcloud secrets ...)` |
| `password authentication failed` (bash) | `ALEMBIC_DB_URL` assigned without `export` — not visible to alembic subprocess | Use `export ALEMBIC_DB_URL=...` |
| `invalid integer value "xdr8..." for connection option "port"` | Password with special characters embedded in psql connection URL | Use `PGPASSWORD=$DB_PASS psql -h ... -p ... -U ...` instead |
| `No matching distribution found for google-cloud-sql-connector` | Wrong package name | Use `cloud-sql-python-connector[psycopg2]` |

---

*Runbook prepared April 2026. Covers Phase 1 of the Agentic Market Intelligence System — GCP Edition.*
