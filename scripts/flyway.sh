#!/bin/bash
# Run Flyway against the test or prod QuantCore database.
#
# `db/flyway.conf` deliberately holds no credentials, so the JDBC URL and login
# have to be derived from the DSNs in `.env` every time. Before this script that
# was reconstructed by hand on each rollout, which is both tedious and a good way
# to point a migration at the wrong database.
#
# Usage:
#   ./scripts/flyway.sh info                 # test (default — the safe target)
#   ./scripts/flyway.sh migrate
#   ./scripts/flyway.sh --prod info
#   ./scripts/flyway.sh --prod migrate       # prompts for confirmation
#
# The target database is echoed (host:port/name only) before Flyway runs. The
# password is exported to Flyway's environment and never printed.
#
# NOTE: `init_schema()` in `quantcore/db.py` runs 22 tables of idempotent DDL on
# every application startup, so a deployed database usually already has the right
# *shape* before Flyway ever runs — pure-DDL migrations are expected to report
# "already exists, skipping". `flyway info` is therefore NOT evidence of what a
# deployed database contains; run `scripts/schema_check.py --test|--prod` for that
# (read-only, diffs live objects against db/schema_snapshot.json). See issue #165.
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET=test
case "${1:-}" in
  --test) TARGET=test; shift ;;
  --prod) TARGET=prod; shift ;;
esac

if [ "$TARGET" = prod ]; then
  VAR=QUANTCORE_DB_DSN
else
  VAR=QUANTCORE_TEST_DB_DSN
fi

DSN=$(grep -E "^${VAR}=" .env | head -1 | cut -d= -f2-)
if [ -z "$DSN" ]; then
  echo "${VAR} not found in .env" >&2
  exit 1
fi

# postgresql://user:password@host:port/database  ->  Flyway's three settings
rest=${DSN#*://}
creds=${rest%%@*}
hostpart=${rest#*@}
FLYWAY_USER=${creds%%:*}
FLYWAY_PASSWORD=${creds#*:}
FLYWAY_URL="jdbc:postgresql://${hostpart}"
export FLYWAY_USER FLYWAY_PASSWORD FLYWAY_URL

echo "target: ${TARGET}  ${hostpart}  (user: ${FLYWAY_USER})"

# Guard the one command that writes. `info`, `validate`, etc. are read-only.
if [ "$TARGET" = prod ] && [ "${1:-}" = migrate ]; then
  read -r -p "Run 'migrate' against PROD (${hostpart})? [yes/N] " reply
  [ "$reply" = yes ] || { echo "aborted"; exit 1; }
fi

exec flyway -configFiles=db/flyway.conf "$@"
