#!/bin/bash
# One-shot recovery for the local dev stack after the daily ADC (RAPT) expiry.
#
# Usage:  ./scripts/restart_local_stack.sh
#
# Prereq: valid Application Default Credentials. If ADC has expired, this
# script tells you the exact command and exits — that step is interactive
# (browser) and can't be automated:
#     gcloud auth application-default login
#
# What it does: restarts the Cloud SQL proxy (it caches credentials, so a
# fresh ADC needs a fresh proxy), restarts the FastAPI tier with
# ANTHROPIC_API_KEY loaded from .env, and makes sure vite is serving.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# --- 1. ADC must be valid --------------------------------------------------
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    echo "✗ ADC is expired or missing. Run this first (browser reauth), then re-run me:"
    echo ""
    echo "    gcloud auth application-default login"
    echo ""
    echo "  (Note: 'gcloud auth login' is a DIFFERENT credential and won't fix the proxy.)"
    exit 1
fi
echo "✓ ADC valid"

# --- 2. Fresh proxy ----------------------------------------------------------
pkill -f cloud-sql-proxy 2>/dev/null
sleep 2
./runProxy-MAC.sh >/dev/null 2>&1
sleep 4
DSN="$(grep '^QUANTCORE_DB_DSN=' .env | cut -d= -f2-)"
if PGCONNECT_TIMEOUT=10 psql "$DSN" -c "select 1" >/dev/null 2>&1; then
    echo "✓ prod DB reachable through proxy (:5433)"
else
    echo "✗ proxy is up but prod DB unreachable — check cloud-sql-proxy.log"
    exit 1
fi

# --- 3. Fresh API ------------------------------------------------------------
pkill -f "uvicorn api.main:app" 2>/dev/null
sleep 2
source .venv/bin/activate
KEY="$(grep '^ANTHROPIC_API_KEY=' .env | cut -d= -f2- || true)"
# .env may quote the value (ANTHROPIC_API_KEY="sk-ant-..."); strip surrounding
# quotes so they don't end up INSIDE the key and cause a 401 invalid x-api-key.
KEY="${KEY%\"}"; KEY="${KEY#\"}"; KEY="${KEY%\'}"; KEY="${KEY#\'}"
ANTHROPIC_API_KEY="$KEY" nohup uvicorn api.main:app --host 127.0.0.1 --port 5001 \
    > "$REPO/api.log" 2>&1 &
echo "  API starting (slow first boot is normal)..."
for i in $(seq 1 30); do
    out=$(curl -s http://127.0.0.1:5001/api/health 2>/dev/null)
    if echo "$out" | grep -q '"db_connected": *true\|"db_connected":true'; then
        echo "✓ API healthy on :5001 — $out"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "✗ API failed to become healthy — tail of api.log:"
        tail -5 "$REPO/api.log"
        exit 1
    fi
    sleep 4
done

# --- 4. Vite -----------------------------------------------------------------
if curl -s -o /dev/null http://localhost:5173; then
    echo "✓ vite already serving on :5173"
else
    # node/npm live under nvm and aren't on non-interactive PATHs.
    if ! command -v npm >/dev/null 2>&1; then
        NVM_NODE="$(ls -td "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | head -1)"
        [ -n "$NVM_NODE" ] && export PATH="$NVM_NODE:$PATH"
    fi
    (cd frontend && nohup npm run dev > "$REPO/frontend.log" 2>&1 &)
    sleep 3
    if curl -s -o /dev/null http://localhost:5173; then
        echo "✓ vite started on :5173"
    else
        echo "✗ vite failed to start — tail of frontend.log:"
        tail -5 "$REPO/frontend.log"
        exit 1
    fi
fi

echo ""
echo "All good — reload http://localhost:5173"
