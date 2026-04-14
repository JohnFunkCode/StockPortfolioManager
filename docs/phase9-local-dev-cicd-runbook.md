# Phase 9 — Local Development & CI/CD Runbook
### Stock Portfolio Manager — Agentic Market Intelligence System

---

## Overview

This runbook covers two things:

1. **Local development** — running the full stack on your Mac with a single command
2. **CI/CD** — push-button deployment to GCP Cloud Run via GitHub Actions

**Prerequisites:** Phases 1–8 complete. All agent code, Flask API, and React frontend are in place.

---

## What Was Built in This Phase

| File | Purpose |
|------|---------|
| `Makefile` | Developer workflow commands (`make dev`, `make stop`, `make logs`, etc.) |
| `runUI-MAC.sh` | Updated startup script — starts proxy + API + frontend, fixes port, auto-detects Node |
| `.env.example` | Documents every required environment variable |
| `Dockerfile` | Multi-stage build: React → gunicorn/Flask single container |
| `.dockerignore` | Excludes local artifacts from the Docker build context |
| `requirements.txt` | Added `gunicorn>=21.2.0` |
| `api/app.py` | Added SPA catch-all route — Flask serves `frontend/dist/` in production |
| `.github/workflows/deploy.yml` | GitHub Actions: build image → migrate DB → deploy to Cloud Run |
| `scripts/setup-gcp-cicd.sh` | One-time GCP + GitHub setup (Artifact Registry, WIF, IAM, secrets) |

---

## Part 1 — Local Development

### Prerequisites (one-time)

**fish / bash**
```bash
# 1. Python virtualenv
python3 -m venv .venv
source .venv/bin/activate   # bash
# or: source .venv/bin/activate.fish (fish)
pip install -r requirements.txt

# 2. Node dependencies
cd frontend && npm install && cd ..

# 3. GCP Application Default Credentials (needed by cloud-sql-proxy)
gcloud auth application-default login

# 4. Environment file
cp .env.example .env
# Open .env and fill in: DATABASE_URL, JWT_SECRET, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
```

### `.env` quick reference

| Variable | Where to get it |
|----------|----------------|
| `NODE_BIN` | `dirname $(which node)` |
| `HARVESTER_DB_PATH` | Absolute path to `harvester.sqlite` |
| `DATABASE_URL` | `postgresql+psycopg2://app_user:PASS@127.0.0.1:5433/stock_portfolio` — use the password from Secret Manager: `gcloud secrets versions access latest --secret=db-app-user-password` |
| `JWT_SECRET` | Any random string locally: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GOOGLE_CLIENT_ID` | GCP Console → APIs & Services → Credentials → OAuth 2.0 Client |
| `GOOGLE_CLIENT_SECRET` | Same OAuth client |
| `GOOGLE_REDIRECT_URI` | `http://localhost:5001/auth/callback` |
| `GCP_PROJECT` | `stock-portfolio-tfowler` |
| `PUBSUB_ENABLED` | `false` (skip Pub/Sub in local dev) |

### First-time database setup

**fish / bash**
```bash
# Start the proxy first (requires ADC credentials above)
./cloud-sql-proxy stock-portfolio-tfowler:us-central1:stock-portfolio-db \
  --port=5433 --auto-iam-authn &

# Run migrations
make migrate

# Verify (optional)
DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
PGPASSWORD=$DB_PASS psql -h 127.0.0.1 -p 5433 -U app_user -d stock_portfolio \
  -c "\dt"
```

---

### Daily workflow

#### Start the full stack

**fish / bash**
```bash
make dev
```

This runs three processes in parallel:
1. Cloud SQL Proxy — `localhost:5433` → Cloud SQL
2. Flask API — `http://127.0.0.1:5001`
3. React dev server — `http://localhost:5173` (proxies `/api` to Flask)

Open your browser at **http://localhost:5173**.

#### Watch logs

**fish / bash**
```bash
make logs
# tails api.log and frontend.log together
```

Or tail individually:

**fish / bash**
```bash
tail -f api.log        # Flask
tail -f frontend.log   # Vite
```

#### Stop everything

**fish / bash**
```bash
make stop
# kills cloud-sql-proxy, flask, and vite
```

---

### Individual service commands

Start services in separate terminals when you need independent control:

**Terminal 1 — proxy**
```bash
make proxy
```

**Terminal 2 — API**
```bash
make api
```

**Terminal 3 — frontend**
```bash
make ui
```

---

### Other useful targets

| Command | What it does |
|---------|-------------|
| `make migrate` | Run `alembic upgrade head` (needed after schema changes) |
| `make test` | Run Python unit tests (`python -m unittest discover`) |
| `make shell` | Open a Python REPL inside the venv |
| `make build` | Build the production Docker image locally (tags as `:local`) |

---

### Makefile variables you can override

```bash
# Use a different GCP project:
make proxy GCP_PROJECT=my-other-project

# Use a different proxy port:
make proxy PROXY_PORT=5434
```

---

### Circuit breakers in local dev

The signal scanner checks market hours and blocks runs by default. To override:

**fish / bash**
```bash
curl -s -X POST http://localhost:5001/run-signal-scanner \
  -H "Content-Type: application/json" \
  -d '{"force": true}' | python3 -m json.tool
```

Or set `CIRCUIT_BREAKER_ENABLED=false` in `.env` to disable all breakers.

---

### Gotchas — local dev

| Symptom | Cause | Fix |
|---------|-------|-----|
| `cloud-sql-proxy` exits immediately | ADC credentials missing or expired | Run `gcloud auth application-default login` |
| Flask starts but DB queries fail | Proxy not running or wrong port | Check `make proxy` is running; verify `DATABASE_URL` port matches |
| `npm run dev` fails with "node not found" | `NODE_BIN` in `.env` is wrong or blank | Set `NODE_BIN=$(dirname $(which node))` in `.env` |
| `alembic upgrade head` fails | `DATABASE_URL` not set in shell env | `.env` is loaded by the Makefile automatically — run `make migrate` not `alembic` directly |
| `PUBSUB_ENABLED` not set → Pub/Sub errors | Pub/Sub tries to publish without ADC | Set `PUBSUB_ENABLED=false` in `.env` |

---

## Part 2 — CI/CD and GCP Deployment

### Architecture

```
git push origin main
       │
       ▼
GitHub Actions (.github/workflows/deploy.yml)
       │
       ├─ [build]   docker build --tag api:SHA .
       │            docker push → Artifact Registry
       │
       ├─ [migrate] cloud-sql-proxy + alembic upgrade head
       │
       └─ [deploy]  gcloud run deploy stock-portfolio-api \
                      --image=api:SHA
```

Authentication uses **Workload Identity Federation** — no service account JSON keys are stored anywhere.

---

### One-time GCP + GitHub setup

Run this **once** per repository. It takes about 2 minutes.

**Prerequisites:**

**fish / bash**
```bash
# GCP CLI authenticated
gcloud auth login
gcloud config set project stock-portfolio-tfowler

# GitHub CLI authenticated
brew install gh
gh auth login
```

**Run the setup script:**

**fish / bash**
```bash
bash scripts/setup-gcp-cicd.sh
```

The script does the following (safe to re-run — each step is idempotent):

| Step | What it creates |
|------|----------------|
| 1 | Enables 8 GCP APIs (Artifact Registry, Cloud Run, IAM, Secret Manager, etc.) |
| 2 | Creates Artifact Registry Docker repo `stock-portfolio` in `us-central1` |
| 3 | Creates service account `github-actions-deploy` with least-privilege roles |
| 4 | Creates Workload Identity Federation pool + OIDC provider for this repo |
| 5 | Writes `WIF_PROVIDER` and `WIF_SERVICE_ACCOUNT` into GitHub repo secrets |
| 6 | Prompts for `DATABASE_URL` and writes it as a GitHub secret |
| 7 | Creates a placeholder Cloud Run service so the first real deploy succeeds |

---

### GCP Secrets Manager — required secrets

The deployed container reads these from Secret Manager at startup. Verify they exist:

**fish / bash**
```bash
gcloud secrets list --project=stock-portfolio-tfowler
```

| Secret name | Contents |
|-------------|---------|
| `jwt-secret` | 32-byte hex token (JWT signing key) |
| `db-connection-string` | `postgresql+psycopg2://app_user:PASS@/stock_portfolio?host=/cloudsql/stock-portfolio-tfowler:us-central1:stock-portfolio-db` |
| `google-client-id` | OAuth 2.0 Client ID |
| `google-client-secret` | OAuth 2.0 Client Secret |
| `db-app-user-password` | Raw password for `app_user` (used by proxy in CI migrations) |

To create a missing secret:

**fish / bash**
```bash
echo -n "SECRET_VALUE" | gcloud secrets create SECRET_NAME \
  --data-file=- \
  --project=stock-portfolio-tfowler

# Or update an existing secret's value:
echo -n "NEW_VALUE" | gcloud secrets versions add SECRET_NAME \
  --data-file=- \
  --project=stock-portfolio-tfowler
```

---

### GitHub repository secrets

The workflow needs these secrets set in the GitHub repo (set automatically by the setup script, but shown here for reference):

| Secret | Value |
|--------|-------|
| `WIF_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-actions-pool/providers/github-actions-provider` |
| `WIF_SERVICE_ACCOUNT` | `github-actions-deploy@stock-portfolio-tfowler.iam.gserviceaccount.com` |
| `DATABASE_URL` | Cloud SQL connection string (for migrations in CI) |

To verify they exist:

**fish / bash**
```bash
gh secret list --repo thomasdfowler/StockPortfolioManager
```

---

### Triggering a deployment

#### Automatic (normal workflow)

```bash
git push origin main
```

Any push to `main` triggers the full build → migrate → deploy pipeline.

#### Manual trigger (without a code change)

**fish / bash**
```bash
gh workflow run deploy.yml
```

Or from the GitHub UI: **Actions → Build & Deploy to Cloud Run → Run workflow**.

#### Watch a running deployment

**fish / bash**
```bash
gh run watch
```

---

### Deployment pipeline breakdown

#### Job 1: `build`
- Checks out code
- Authenticates to GCP via WIF (no keys)
- Runs `docker build` — multi-stage (React → Flask/gunicorn)
- Pushes image as both `:SHORT_SHA` and `:latest` to Artifact Registry
- Uses `--cache-from :latest` to reuse layers and speed up builds

#### Job 2: `migrate`
- Downloads and starts `cloud-sql-proxy` inside the runner
- Installs `alembic` + `psycopg2`
- Runs `alembic upgrade head` against the production database
- Uses `DATABASE_URL` GitHub secret for connection
- Only runs after `build` succeeds

#### Job 3: `deploy`
- Runs `gcloud run deploy` with the exact image SHA from job 1
- Sets Cloud Run configuration:
  - `--concurrency=10`, `--max-instances=3`, `--min-instances=0`
  - `--cpu=1`, `--memory=2Gi`, `--timeout=300`
  - Secrets mounted from Secret Manager: `DATABASE_URL`, `JWT_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- Only runs after `build` AND `migrate` succeed

---

### Post-deploy: update CORS

After the first successful deploy, your Cloud Run service will have a URL like:
`https://stock-portfolio-api-xxxxxxxx-uc.a.run.app`

Update `api/app.py` to allow that origin:

**fish / bash**
```bash
# Get your Cloud Run URL
gcloud run services describe stock-portfolio-api \
  --region=us-central1 \
  --format="value(status.url)" \
  --project=stock-portfolio-tfowler
```

Then edit `api/app.py`:

```python
CORS(app, resources={r"/*": {"origins": [
    "http://localhost:5173",
    "http://localhost:5001",
    "https://stock-portfolio-api-xxxxxxxx-uc.a.run.app",   # add this
]}}, supports_credentials=True)
```

Also update the Google OAuth redirect URI in the GCP Console to include the production URL:
`https://your-cloud-run-url/auth/callback`

---

### Rollback

To roll back to a previous image:

**fish / bash**
```bash
# List recent deployments
gcloud run revisions list \
  --service=stock-portfolio-api \
  --region=us-central1 \
  --project=stock-portfolio-tfowler

# Roll back to a specific revision
gcloud run services update-traffic stock-portfolio-api \
  --to-revisions=stock-portfolio-api-00005-abc=100 \
  --region=us-central1 \
  --project=stock-portfolio-tfowler
```

---

### Viewing production logs

**fish / bash**
```bash
# Stream live logs
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="stock-portfolio-api"' \
  --project=stock-portfolio-tfowler \
  --limit=50 \
  --format=json \
  | python3 -m json.tool

# Filter by severity
gcloud logging read \
  'resource.type="cloud_run_revision" AND severity>=ERROR' \
  --project=stock-portfolio-tfowler \
  --limit=20
```

---

### Docker image management

**fish / bash**
```bash
# List all images in Artifact Registry
gcloud artifacts docker images list \
  us-central1-docker.pkg.dev/stock-portfolio-tfowler/stock-portfolio/api \
  --include-tags \
  --project=stock-portfolio-tfowler

# Delete old images (keep last 10)
gcloud artifacts docker images list \
  us-central1-docker.pkg.dev/stock-portfolio-tfowler/stock-portfolio/api \
  --format="value(version)" \
  --project=stock-portfolio-tfowler \
  | tail -n +11 \
  | xargs -I{} gcloud artifacts docker images delete \
      "us-central1-docker.pkg.dev/stock-portfolio-tfowler/stock-portfolio/api@{}" \
      --quiet --project=stock-portfolio-tfowler
```

---

### Gotchas — CI/CD

| Symptom | Cause | Fix |
|---------|-------|-----|
| WIF auth fails with `invalid_grant` | WIF pool/provider attribute condition doesn't match repo name | Re-run setup script; verify `GITHUB_REPO` in script matches your actual `owner/repo` |
| `docker build` fails on `torch` install | pip timeout (torch is ~2 GB) | Increase runner timeout, or remove `torch`/`transformers` from `requirements.txt` if not used in the API path |
| `alembic upgrade head` fails in CI | `DATABASE_URL` secret missing or stale | Re-run `gh secret set DATABASE_URL` with the correct value |
| Cloud Run deploy fails: "permission denied on secret" | Deploy SA missing `secretmanager.secretAccessor` | Re-run setup script to re-grant roles |
| First deploy shows placeholder hello-world page | Expected — setup script deploys a placeholder | Push to `main` to trigger first real build |
| CORS errors after deploy | Production URL not in CORS allowlist | Add Cloud Run URL to `app.py` CORS config and push |

---

*Runbook prepared April 2026. Covers Phase 9 of the Agentic Market Intelligence System — GCP Edition.*
