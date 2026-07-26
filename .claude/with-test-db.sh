#!/bin/bash
# Runs a command with QUANTCORE_DB_DSN pointed at QUANTCORE_TEST_DB_DSN (127.0.0.1:5434, test).
# Usage: ./.claude/with-test-db.sh env FOO=1 .venv/bin/python -m unittest some_test
set -euo pipefail
cd "$(dirname "$0")/.."
TEST_DSN=$(grep -E '^QUANTCORE_TEST_DB_DSN=' .env | head -1 | cut -d= -f2-)
if [ -z "$TEST_DSN" ]; then
  echo "QUANTCORE_TEST_DB_DSN not found in .env" >&2
  exit 1
fi
export QUANTCORE_DB_DSN="$TEST_DSN"
exec "$@"
