#!/bin/bash
# Run a command with QUANTCORE_DB_DSN pointed at the TEST database
# (QUANTCORE_TEST_DB_DSN in .env — 127.0.0.1:5434 behind the test Cloud SQL proxy).
#
# Usage:
#   ./scripts/with-test-db.sh .venv/bin/python -m unittest discover -s tests -t .
#   ./scripts/with-test-db.sh env FOO=1 .venv/bin/python -m unittest some_test
#   ./scripts/with-test-db.sh psql "$QUANTCORE_DB_DSN" -c '\d positions'
#
# Only QUANTCORE_TEST_DB_DSN is read out of .env — the rest of the file
# (prod DSN, Discord webhook, bucket key, API key) is deliberately NOT sourced,
# so nothing this command spawns inherits a credential it has no use for.
#
# The target database is echoed as host:port/name only. The DSN carries the
# password and is never printed; see the never-log policy in CLAUDE.md and
# tests/test_dsn_redaction.py.
#
# NOTE: psql does not read QUANTCORE_DB_DSN. Invoked as `with-test-db.sh psql -c …`
# it falls through to your local socket and silently hits the wrong database —
# pass the DSN explicitly, as in the third example above.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <command> [args...]" >&2
  exit 2
fi

TEST_DSN=$(grep -E '^QUANTCORE_TEST_DB_DSN=' .env | head -1 | cut -d= -f2-)
if [ -z "$TEST_DSN" ]; then
  echo "QUANTCORE_TEST_DB_DSN not found in .env" >&2
  exit 1
fi

# postgresql://user:password@host:port/database -> host:port/database
rest=${TEST_DSN#*://}
echo "target: test  ${rest#*@}" >&2

export QUANTCORE_DB_DSN="$TEST_DSN"
exec "$@"
