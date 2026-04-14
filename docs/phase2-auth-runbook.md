# Phase 2 — Authentication & Authorization Runbook
### Stock Portfolio Manager — Agentic Market Intelligence System

---

## Overview

This runbook documents the exact steps to implement OAuth 2.0 authentication, JWT session management, and RBAC middleware for the Flask API. It reflects what was actually done, with commands for both **fish** and **bash** shells.

**Prerequisites:**
- Phase 1 complete (GCP project, Cloud SQL, Secret Manager, Auth Proxy)
- Auth Proxy running on port 5433
- All Phase 1 secrets present in Secret Manager

---

## Step 1 — Configure the OAuth Consent Screen (GCP Console)

This step must be done in the browser — it cannot be done via `gcloud`.

1. Go to `console.cloud.google.com` → select `stock-portfolio-tfowler`
2. Navigate to **APIs & Services → OAuth consent screen**
3. Set the following:
   - **User Type:** Internal
   - **App name:** `Stock Portfolio Manager`
   - **User support email:** your Google account email
   - **Developer contact email:** your Google account email
   - **Scopes:** add `openid`, `email`, `profile`
4. Save and continue through all remaining screens

> **Note:** Use **Internal** user type — this is an invite-only team tool (decision 3 from the decisions log). Internal restricts sign-in to users within your Google Workspace. If using personal Gmail accounts, select **External** and add test users manually.

---

## Step 2 — Create OAuth 2.0 Credentials (GCP Console)

1. Still in **APIs & Services** → **Credentials**
2. Click **Create Credentials → OAuth 2.0 Client ID**
3. Set the following:
   - **Application type:** Web application
   - **Name:** `Stock Portfolio Manager Web`
   - **Authorized redirect URIs:** add both:
     - `http://localhost:5001/auth/callback`
     - `http://localhost:5173/auth/callback`
4. Click **Create**
5. Copy the **Client ID** and **Client Secret** from the dialog

---

## Step 3 — Store OAuth Credentials in Secret Manager

**fish**
```fish
echo -n "<YOUR_CLIENT_ID>" | gcloud secrets create google-oauth-client-id --data-file=-
echo -n "<YOUR_CLIENT_SECRET>" | gcloud secrets create google-oauth-client-secret --data-file=-
```

**bash**
```bash
echo -n "<YOUR_CLIENT_ID>" | gcloud secrets create google-oauth-client-id --data-file=-
echo -n "<YOUR_CLIENT_SECRET>" | gcloud secrets create google-oauth-client-secret --data-file=-
```

---

## Step 4 — Generate and Store the JWT Secret

**fish**
```fish
echo -n (python3 -c "import secrets; print(secrets.token_hex(32))") | gcloud secrets create jwt-secret --data-file=-
```

**bash**
```bash
echo -n "$(python3 -c "import secrets; print(secrets.token_hex(32))")" | gcloud secrets create jwt-secret --data-file=-
```

Verify all three new secrets exist alongside the Phase 1 secrets:

**fish**
```fish
gcloud secrets list
```

**bash**
```bash
gcloud secrets list
```

Expected secrets: `db-app-user-password`, `db-connection-string`, `google-oauth-client-id`, `google-oauth-client-secret`, `jwt-secret`

---

## Step 5 — Install Required Python Packages

**fish**
```fish
source .venv/bin/activate.fish; and pip install "google-auth>=2.29.0" "PyJWT>=2.8.0"
```

**bash**
```bash
source .venv/bin/activate && pip install "google-auth>=2.29.0" "PyJWT>=2.8.0"
```

> **Note:** Both packages are likely already present as transitive dependencies. The install will print "Requirement already satisfied" — this is fine.

Add to `requirements.txt`:
```
google-auth>=2.29.0
PyJWT>=2.8.0
```

---

## Step 6 — Create New Source Files

Four new files are created in this phase. They are already present in the repository:

| File | Purpose |
|------|---------|
| `db/config.py` | Secret loading helper — env var first, Secret Manager fallback |
| `db/database.py` | SQLAlchemy engine + `get_db()` context manager with tenant context |
| `api/auth.py` | Flask Blueprint — `/auth/login`, `/auth/callback`, `/auth/refresh`, `/auth/logout`, `/auth/me` |
| `api/middleware.py` | `@require_auth` and `@require_role` decorators |

### `db/config.py` — Secret Resolution

Resolves secrets in priority order:
1. Environment variable (name uppercased, hyphens → underscores, e.g. `jwt-secret` → `JWT_SECRET`)
2. GCP Secret Manager

The `lru_cache` ensures each secret is only fetched once per process lifetime.

### `db/database.py` — Database Connection

- Checks `DATABASE_URL` env var first (local dev via Auth Proxy)
- Falls back to assembling the URL from `db-connection-string` + `db-app-user-password` secrets
- `get_db(tenant_id)` sets `app.tenant_id` on the connection before yielding — this activates PostgreSQL RLS

### `api/auth.py` — OAuth Flow

Full Google OAuth 2.0 authorization code flow:

```
Browser → GET /auth/login
        → 302 to accounts.google.com (with state cookie)
        → Google sign-in
        → GET /auth/callback?code=...&state=...
        → Verify state (CSRF protection)
        → Exchange code for id_token via POST to oauth2.googleapis.com/token
        → Verify id_token signature (google-auth library)
        → SELECT user WHERE google_sub = <sub>
        → Issue JWT session cookie (1h expiry, HttpOnly, SameSite=Lax)
        → 302 to frontend /dashboard
```

### `api/middleware.py` — RBAC

```python
@require_auth          # validates JWT cookie → populates g.user
@require_role("admin") # checks g.user["role"] ∈ allowed set
```

---

## Step 7 — Update `api/app.py`

Two changes made to the existing app:

1. Import and register the auth blueprint:
```python
from api.auth import auth_bp
# inside create_app():
app.register_blueprint(auth_bp)
```

2. Tighten CORS to restrict origins and enable credentials (required for cookies):
```python
CORS(app, resources={r"/*": {"origins": [
    "http://localhost:5173",
    "http://localhost:5001",
]}}, supports_credentials=True)
```

> **Note:** `supports_credentials=True` is required for the browser to send the `session` cookie with cross-origin requests from the React frontend on port 5173.

---

## Step 8 — Start the API with Auth Enabled

Set all secrets as environment variables (avoids Secret Manager calls during local dev — Secret Manager requires valid ADC which can expire):

**fish**
```fish
set -x GOOGLE_OAUTH_CLIENT_ID (gcloud secrets versions access latest --secret=google-oauth-client-id)
set -x GOOGLE_OAUTH_CLIENT_SECRET (gcloud secrets versions access latest --secret=google-oauth-client-secret)
set -x JWT_SECRET (gcloud secrets versions access latest --secret=jwt-secret)
set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
set -x DATABASE_URL "postgresql+psycopg2://app_user:$DB_PASS@127.0.0.1:5433/stock_portfolio"
source .venv/bin/activate.fish; and python -m api.app
```

**bash**
```bash
export GOOGLE_OAUTH_CLIENT_ID=$(gcloud secrets versions access latest --secret=google-oauth-client-id)
export GOOGLE_OAUTH_CLIENT_SECRET=$(gcloud secrets versions access latest --secret=google-oauth-client-secret)
export JWT_SECRET=$(gcloud secrets versions access latest --secret=jwt-secret)
export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
export DATABASE_URL="postgresql+psycopg2://app_user:$DB_PASS@127.0.0.1:5433/stock_portfolio"
source .venv/bin/activate && python -m api.app
```

> **Note:** The Auth Proxy must be running in a separate terminal on port 5433 before starting Flask. See Phase 1 runbook Step 9.

> **Note:** Set env vars in the same terminal session as the Flask process — fish/bash variables are not inherited across terminals.

---

## Step 9 — Verify the Auth Flow

**Check unauthenticated access is rejected:**

**fish**
```fish
curl -s http://localhost:5001/auth/me
# Expected: {"error": "Not authenticated"}  HTTP 401
```

**bash**
```bash
curl -s http://localhost:5001/auth/me
# Expected: {"error": "Not authenticated"}  HTTP 401
```

**Check login redirect:**

**fish**
```fish
curl -sv http://localhost:5001/auth/login 2>&1 | head -30
# Expected: HTTP/1.1 302 FOUND
#           Location: https://accounts.google.com/o/oauth2/v2/auth?...
#           Set-Cookie: oauth_state=...; HttpOnly; SameSite=Lax
```

**bash**
```bash
curl -sv http://localhost:5001/auth/login 2>&1 | head -30
```

**Complete the full browser flow:**

1. Visit `http://localhost:5001/auth/login`
2. Sign in with your Google account
3. After callback, visit `http://localhost:5001/auth/me`

Expected response:
```json
{
  "email": "your@email.com",
  "role": "admin",
  "sub": "<user-uuid>",
  "tenant_id": "<tenant-uuid>"
}
```

**Verify all auth routes are registered:**

**fish**
```fish
source .venv/bin/activate.fish; and python -c "from api.app import create_app; app = create_app(); print([str(r) for r in app.url_map.iter_rules() if 'auth' in str(r)])"
# Expected: ['/auth/login', '/auth/callback', '/auth/refresh', '/auth/logout', '/auth/me']
```

**bash**
```bash
source .venv/bin/activate && python -c "from api.app import create_app; app = create_app(); print([str(r) for r in app.url_map.iter_rules() if 'auth' in str(r)])"
```

---

## Gotchas Reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `/auth/login` hangs with no response | Flask blocking on Secret Manager call (ADC expired) | Set all secrets as env vars before starting Flask (Step 8) |
| `ModuleNotFoundError: google.auth.transport.urllib_request` | Module does not exist in google-auth | Use `google.auth.transport.requests.Request()` instead |
| `sqlalchemy.exc.OperationalError: Connection refused port 5433` | Auth Proxy not running | Start `./cloud-sql-proxy ... --port=5433` in a separate terminal |
| `RetryError: Reauthentication is needed` on callback | `database.py` calling Secret Manager (ADC expired) despite `DATABASE_URL` being set | `database.py` must check `DATABASE_URL` env var before calling `get_secret()` |
| `/auth/me` returns 404 after successful callback | Flask running stale process without blueprint registered | Kill Flask, re-export env vars, restart `python -m api.app` |
| `/auth/me` returns 401 when tested via curl after browser login | curl does not carry the browser's session cookie | Test `/auth/me` directly in the browser address bar, not via curl |
| `{"error": "Account not found"}` on callback | Google sub not in `users` table | Run the Phase 1 tenant seeding step with the correct `google_sub` value |

---

*Runbook prepared April 2026. Covers Phase 2 of the Agentic Market Intelligence System — GCP Edition.*
