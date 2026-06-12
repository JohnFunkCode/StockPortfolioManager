#!/bin/bash
# Starts the Cloud SQL Auth Proxy in the background.
# Logs are written to cloud-sql-proxy.log in the project root.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load Cloud SQL connection settings from .env (CLOUDSQL_CONNECTION_NAME, _PORT, _QUOTA_PROJECT)
set -a
source "$SCRIPT_DIR/.env"
set +a

# Start the proxy only if one isn't already running
PROXY_PID="$(pgrep -f cloud-sql-proxy | head -1)"
if [ -n "$PROXY_PID" ]; then
    echo "Cloud SQL Auth Proxy already running (PID $PROXY_PID) — not starting a second copy."
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

echo ""
echo "✓ Cloud SQL Proxy running"
echo "Cloud SQL Proxy PID: $PROXY_PID"
echo ""
echo "Listening on: 127.0.0.1:$CLOUDSQL_PORT"
echo ""
echo "To stop:  pkill -f 'cloud-sql-proxy'"
