#!/bin/bash
# Start the Cloud SQL Auth Proxy in the background, against prod or test.
#
# Usage:
#   ./runProxy-MAC.sh              # prod (default) — 127.0.0.1:5433
#   ./runProxy-MAC.sh --prod       # same, said out loud
#   ./runProxy-MAC.sh --test       # test — 127.0.0.1:5434
#
# Prod stays the default because scripts/restart_local_stack.sh calls this bare
# and expects :5433. The --test/--prod flag convention matches ./scripts/flyway.sh.
#
# Connection targets come from .env, one key per setting:
#   prod: CLOUDSQL_CONNECTION_NAME, CLOUDSQL_PORT, CLOUDSQL_QUOTA_PROJECT
#   test: CLOUDSQL_TEST_CONNECTION_NAME, CLOUDSQL_TEST_PORT, CLOUDSQL_TEST_QUOTA_PROJECT
#
# Those keys are read individually rather than `set -a; source .env`, which used
# to export the whole file — prod DSN, Discord webhook, bucket key, Anthropic key —
# into the proxy process and everything it spawned. The proxy needs three values;
# it gets three values.
#
# Logs go to cloud-sql-proxy.log (prod) or cloud-sql-proxy-test.log (test) in the
# project root; both are gitignored by the *.log rule. Instance connection names
# are not secrets, but no DSN or password is read or printed here at all.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

TARGET=prod
case "${1:-}" in
  --test) TARGET=test; shift ;;
  --prod) TARGET=prod; shift ;;
  "") ;;
  *) echo "usage: $0 [--test|--prod]" >&2; exit 2 ;;
esac

if [ "$TARGET" = test ]; then
  PREFIX=CLOUDSQL_TEST
  LOG="$SCRIPT_DIR/cloud-sql-proxy-test.log"
else
  PREFIX=CLOUDSQL
  LOG="$SCRIPT_DIR/cloud-sql-proxy.log"
fi

# Read one key out of .env without sourcing the file.
env_value() {
  grep -E "^$1=" "$SCRIPT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2-
}

CONNECTION_NAME="$(env_value "${PREFIX}_CONNECTION_NAME")"
PORT="$(env_value "${PREFIX}_PORT")"
QUOTA_PROJECT="$(env_value "${PREFIX}_QUOTA_PROJECT")"

if [ -z "$CONNECTION_NAME" ] || [ -z "$PORT" ]; then
  echo "Missing ${PREFIX}_CONNECTION_NAME / ${PREFIX}_PORT in .env" >&2
  if [ "$TARGET" = test ]; then
    echo "" >&2
    echo "Add the test target to .env (values from the test project's Cloud SQL instance):" >&2
    echo "  CLOUDSQL_TEST_CONNECTION_NAME=<project>:<region>:<instance>" >&2
    echo "  CLOUDSQL_TEST_PORT=5434" >&2
    echo "  CLOUDSQL_TEST_QUOTA_PROJECT=<project>" >&2
  fi
  exit 1
fi

# Match on the port, not just the binary. The old guard was a bare
# `pgrep -f cloud-sql-proxy`, so a test proxy already up on 5434 made this
# print "already running" and decline to start the prod proxy on 5433 —
# leaving you connected to whichever database you did not ask for.
PROXY_PID="$(pgrep -f "cloud-sql-proxy.*port=${PORT}" | head -1)"
if [ -n "$PROXY_PID" ]; then
    echo "Cloud SQL Auth Proxy already running on port ${PORT} (PID $PROXY_PID) — not starting a second copy."
else
    echo "Starting Cloud SQL Auth Proxy [${TARGET}]... (logs: $(basename "$LOG"))"
    QUOTA_ARGS=()
    [ -n "$QUOTA_PROJECT" ] && QUOTA_ARGS=(--quota-project="$QUOTA_PROJECT")
    cloud-sql-proxy "$CONNECTION_NAME" \
        --port="$PORT" \
        "${QUOTA_ARGS[@]}" \
        > "$LOG" 2>&1 &
    PROXY_PID=$!

    # A live process is not a listening one: the proxy can survive startup and
    # still never bind (bad instance name, port already taken, ADC expired).
    # Report success only when the port actually answers, so the caller does
    # not go on to open a connection that cannot exist.
    if ! command -v nc >/dev/null 2>&1; then
        echo "! nc not found — cannot confirm the port is listening; check $(basename "$LOG")" >&2
    else
        echo "Waiting for proxy to start listening on port ${PORT}..."
        READY=""
        for i in $(seq 1 30); do
            if nc -z 127.0.0.1 "$PORT" 2>/dev/null; then
                READY=1
                echo "Proxy is ready."
                break
            fi
            if ! kill -0 "$PROXY_PID" 2>/dev/null; then
                echo "✗ Proxy exited during startup — see $(basename "$LOG")" >&2
                exit 1
            fi
            sleep 1
        done
        if [ -z "$READY" ]; then
            echo "✗ Proxy did not listen on port ${PORT} within 30s (PID $PROXY_PID still alive) — see $(basename "$LOG")" >&2
            exit 1
        fi
    fi
fi

echo ""
echo "✓ Cloud SQL Proxy running [${TARGET}]"
echo "Cloud SQL Proxy PID: $PROXY_PID"
echo ""
echo "Listening on: 127.0.0.1:${PORT}  (${CONNECTION_NAME})"
echo ""
echo "To stop:  pkill -f 'cloud-sql-proxy.*port=${PORT}'"
