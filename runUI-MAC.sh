#!/usr/bin/env bash
# Starts the full local dev stack: Cloud SQL Proxy, Flask API, React frontend.
# Prefer `make dev` for a cleaner experience; this script is the underlying implementation.
#
# Logs:
#   api.log       Flask output
#   frontend.log  Vite output
#
# Usage: ./runUI-MAC.sh [--no-proxy]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NODE_BIN="$(dirname "$(which node)" 2>/dev/null || echo "")"

# Allow overriding Node path via .env or environment
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  NODE_BIN_ENV=$(grep '^NODE_BIN=' "$SCRIPT_DIR/.env" | cut -d= -f2-)
  [[ -n "$NODE_BIN_ENV" ]] && NODE_BIN="$NODE_BIN_ENV"
fi

START_PROXY=true
[[ "${1:-}" == "--no-proxy" ]] && START_PROXY=false

# ── Cloud SQL Proxy ───────────────────────────────────────────────────────────
if $START_PROXY; then
  if pgrep -f "cloud-sql-proxy" > /dev/null; then
    echo "[proxy] Already running — skipping start"
  else
    echo "[proxy] Starting Cloud SQL Proxy on port 5433... (logs: /dev/null)"
    "$SCRIPT_DIR/cloud-sql-proxy" \
      "stock-portfolio-tfowler:us-central1:stock-portfolio-db" \
      --port=5433 \
      --auto-iam-authn \
      > /dev/null 2>&1 &
    PROXY_PID=$!
    sleep 2
    if ! kill -0 "$PROXY_PID" 2>/dev/null; then
      echo "[proxy] WARNING: proxy exited immediately — check gcloud auth (run: gcloud auth application-default login)"
    fi
  fi
fi

# ── Flask API ─────────────────────────────────────────────────────────────────
echo "[api]   Starting Flask API on port 5001... (logs: api.log)"
source "$SCRIPT_DIR/.venv/bin/activate"
python -m api.app > "$SCRIPT_DIR/api.log" 2>&1 &
API_PID=$!

# ── React frontend ────────────────────────────────────────────────────────────
echo "[ui]    Starting React dev server on port 5173... (logs: frontend.log)"
cd "$SCRIPT_DIR/frontend"
PATH="$NODE_BIN:$PATH" npm run dev > "$SCRIPT_DIR/frontend.log" 2>&1 &
UI_PID=$!

echo ""
echo "  API:      http://127.0.0.1:5001"
echo "  Frontend: http://localhost:5173"
echo ""
echo "  API PID:      $API_PID"
echo "  Frontend PID: $UI_PID"
echo ""
echo "  To stop:  kill $API_PID $UI_PID  (or: make stop)"
echo "  To tail:  tail -f $SCRIPT_DIR/api.log $SCRIPT_DIR/frontend.log"
