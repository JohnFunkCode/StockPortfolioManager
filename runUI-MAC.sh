#!/bin/bash
# Starts the Cloud SQL Auth Proxy, Flask API, and React frontend servers in the background.
# Logs are written to cloud-sql-proxy.log, api.log, and frontend.log in the project root.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load Cloud SQL connection settings from .env (CLOUDSQL_CONNECTION_NAME, _PORT, _QUOTA_PROJECT)
set -a
source "$SCRIPT_DIR/.env"
set +a

# Clean up any old processes (the proxy is reused if already running)
echo "Cleaning up old processes..."
pkill -f "python -m api.app" 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
sleep 1

# Reuse the proxy only if something is actually listening on the configured
# port — a process-name match alone can't tell a healthy proxy from a stale
# one bound to a different port, or an unrelated process (e.g. tail on its log).
if nc -z 127.0.0.1 "$CLOUDSQL_PORT" 2>/dev/null; then
    PROXY_PID="$(pgrep -f cloud-sql-proxy | head -1)"
    echo "Cloud SQL Auth Proxy already listening on port $CLOUDSQL_PORT${PROXY_PID:+ (PID $PROXY_PID)} — not starting a second copy."
else
    echo "Starting Cloud SQL Auth Proxy... (logs: cloud-sql-proxy.log)"
    cloud-sql-proxy "$CLOUDSQL_CONNECTION_NAME" \
        --port="$CLOUDSQL_PORT" \
        --quota-project="$CLOUDSQL_QUOTA_PROJECT" \
        > "$SCRIPT_DIR/cloud-sql-proxy.log" 2>&1 &
    PROXY_PID=$!

    echo "Waiting for proxy to start listening on port $CLOUDSQL_PORT..."
    for i in $(seq 1 30); do
        if nc -z 127.0.0.1 "$CLOUDSQL_PORT" 2>/dev/null; then
            echo "Proxy is ready."
            break
        fi
        sleep 1
    done
fi

echo "Starting API server... (logs: api.log)"
source "$SCRIPT_DIR/.venv/bin/activate"
python -m api.app > "$SCRIPT_DIR/api.log" 2>&1 &
API_PID=$!

echo "Starting frontend server... (logs: frontend.log)"
cd "$SCRIPT_DIR/frontend"
npm run dev > "$SCRIPT_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

sleep 3

echo ""
echo "✓ Servers started"
[ -n "$PROXY_PID" ] && echo "Cloud SQL Proxy PID: $PROXY_PID"
echo "API PID:      $API_PID"
echo "Frontend PID: $FRONTEND_PID"
echo ""
echo "API:      http://127.0.0.1:5001"
echo "Frontend: http://localhost:5173"
echo ""
echo "To stop:  pkill -f 'python -m api.app' && pkill -f 'vite' && pkill -f 'cloud-sql-proxy'"
