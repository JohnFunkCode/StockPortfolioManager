##
## Stock Portfolio Manager — developer workflow
##
## Usage:
##   make dev        start proxy + api + frontend (full local stack)
##   make proxy      start Cloud SQL Proxy only
##   make api        start Flask API only
##   make ui         start React dev server only
##   make migrate    run database migrations (alembic upgrade head)
##   make stop       kill proxy, api, and frontend processes
##   make logs       tail api.log and frontend.log together
##   make test       run Python unit tests
##   make build      build the production Docker image locally
##   make shell      open a Python REPL inside the venv
##

SHELL        := /bin/bash
PROJECT_ROOT := $(shell pwd)
VENV         := $(PROJECT_ROOT)/.venv
PYTHON       := $(VENV)/bin/python
PIP          := $(VENV)/bin/pip
ALEMBIC      := $(VENV)/bin/alembic
GUNICORN     := $(VENV)/bin/gunicorn

# GCP config — override on the command line if needed
GCP_PROJECT  := stock-portfolio-tfowler
CLOUD_SQL_INSTANCE := $(GCP_PROJECT):us-central1:stock-portfolio-db
PROXY_PORT   := 5433
IMAGE        := us-central1-docker.pkg.dev/$(GCP_PROJECT)/stock-portfolio/api

.PHONY: help dev proxy api ui stop migrate logs test build shell

help:
	@grep -E '^##' Makefile | sed 's/^## //'

# ── Full local stack ───────────────────────────────────────────────────────────

dev: _check_env
	@$(MAKE) -j3 _proxy_bg _api_bg _ui_bg
	@echo ""
	@echo "  API:      http://127.0.0.1:5001"
	@echo "  Frontend: http://localhost:5173"
	@echo ""
	@echo "  Run 'make logs' to tail logs, 'make stop' to shut down."

_proxy_bg:
	@$(MAKE) proxy 2>&1 | sed 's/^/[proxy] /' &

_api_bg:
	@sleep 2 && $(MAKE) api 2>&1 | sed 's/^/[api]   /' &

_ui_bg:
	@sleep 2 && $(MAKE) ui 2>&1 | sed 's/^/[ui]    /' &

# ── Individual services ────────────────────────────────────────────────────────

proxy:
	@echo "[proxy] Starting Cloud SQL Proxy on port $(PROXY_PORT)..."
	@$(PROJECT_ROOT)/cloud-sql-proxy \
	  "$(GCP_PROJECT):us-central1:stock-portfolio-db" \
	  --port=$(PROXY_PORT) \
	  --auto-iam-authn

api: _check_venv
	@echo "[api] Starting Flask API on port 5001..."
	@source $(VENV)/bin/activate && \
	  DATABASE_URL=$$(grep ^DATABASE_URL .env 2>/dev/null | cut -d= -f2-) \
	  python -m api.app 2>&1 | tee api.log

ui:
	@echo "[ui] Starting Vite dev server on port 5173..."
	@cd frontend && npm run dev 2>&1 | tee $(PROJECT_ROOT)/frontend.log

# ── Database ───────────────────────────────────────────────────────────────────

migrate: _check_venv
	@echo "[db] Running migrations..."
	@source $(VENV)/bin/activate && \
	  DATABASE_URL=$$(grep ^DATABASE_URL .env 2>/dev/null | cut -d= -f2-) \
	  $(ALEMBIC) upgrade head

# ── Lifecycle ─────────────────────────────────────────────────────────────────

stop:
	@echo "Stopping all dev processes..."
	@pkill -f "cloud-sql-proxy" 2>/dev/null && echo "  proxy stopped"   || echo "  proxy not running"
	@pkill -f "python -m api.app" 2>/dev/null && echo "  api stopped"   || echo "  api not running"
	@pkill -f "vite" 2>/dev/null && echo "  frontend stopped" || echo "  frontend not running"

logs:
	@tail -f api.log frontend.log 2>/dev/null || echo "No log files found — run 'make dev' first"

# ── Quality ───────────────────────────────────────────────────────────────────

test: _check_venv
	@source $(VENV)/bin/activate && python -m unittest discover -v

shell: _check_venv
	@source $(VENV)/bin/activate && python

# ── Docker / Cloud Build ──────────────────────────────────────────────────────

build:
	@echo "[docker] Building production image..."
	docker build -t $(IMAGE):local .

# Deploy is handled by GitHub Actions on push to main.
# To deploy manually without CI:
deploy-manual: build
	@echo "[deploy] Pushing image and deploying to Cloud Run..."
	docker tag $(IMAGE):local $(IMAGE):manual
	docker push $(IMAGE):manual
	gcloud run deploy stock-portfolio-api \
	  --image=$(IMAGE):manual \
	  --region=us-central1 \
	  --project=$(GCP_PROJECT)

# ── Internal checks ───────────────────────────────────────────────────────────

_check_env:
	@test -f .env || (echo "ERROR: .env not found. Copy .env.example and fill in values." && exit 1)

_check_venv:
	@test -d $(VENV) || (echo "ERROR: .venv not found. Run: python -m venv .venv && .venv/bin/pip install -r requirements.txt" && exit 1)
