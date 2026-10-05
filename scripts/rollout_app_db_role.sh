#!/usr/bin/env bash
# Roll out the least-privilege app database role on one project (issue #308).
#
# Runs runbook steps 0-5 from docs/proposals/db-roles-308-plan.md in order, so
# nothing has to be copied out of the doc:
#   0. generate the app role's password (in this process only, never shown)
#   1. create the role and verify its grants  (scripts/ensure_app_db_role.py)
#   2. copy the owner's DSN into quantcore-<env>-migrator-dsn
#   3. point the migrate Job at it, drop its access to the app secret, run it once
#      (scripts/ensure_migrate_job.sh --execute); 4. that run must succeed
#   5. add an app-secret version holding the app role's DSN
# Steps 6 (roll) and 7 (verify) are printed at the end.
#
#   ./scripts/rollout_app_db_role.sh                    # test
#   ./scripts/rollout_app_db_role.sh --prod             # prod (prompts)
#   ./scripts/rollout_app_db_role.sh [--prod] --rollback  # owner's DSN back as the app secret
#
# Safe to re-run. The secrets' DSNs are never printed: the script reads only the
# user name out of them, to decide what is already done.
#   - App secret already holds quantcore_app: steps 0-5 are done; it only verifies.
#   - Migrator secret already exists: it is kept, after checking it holds the owner.
#   - Failed part-way: run it again. Step 1 sets a fresh password, which is fine,
#     because nothing logs in as the role until step 5 writes the app secret.
#
# Needs: gcloud logged in as an owner of the project, and the Cloud SQL Auth Proxy
# for that project running (./runProxy-MAC.sh --test, or no flag for prod).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

ENV=test
ROLLBACK=0
while (( $# )); do
  case "$1" in
    --prod) ENV=prod ;;
    --test) ENV=test ;;
    --rollback) ROLLBACK=1 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "rollout_app_db_role: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

PROJECT="quantcore-${ENV}-20260606"
APP_SECRET="quantcore-${ENV}-db-dsn"
MIGRATOR_SECRET="quantcore-${ENV}-migrator-dsn"
APP_ROLE="quantcore_app"
OWNER_ROLE="${OWNER_ROLE:-quantcore}"
ENV_FLAG="--${ENV}"
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x .venv/bin/python ]]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
fi
MIGRATE_JOB_SCRIPT="${MIGRATE_JOB_SCRIPT:-./scripts/ensure_migrate_job.sh}"

step() { printf '\n== %s\n' "$*"; }
die() { echo "rollout_app_db_role: $*" >&2; exit 1; }

# The user name in a DSN on stdin, and nothing else from it.
dsn_user() { sed -nE '1s#^[A-Za-z0-9+.-]+://([^:@/]+).*#\1#p'; }

secret_user() {
  gcloud secrets versions access latest --secret "$1" --project "$PROJECT" | dsn_user
}

secret_exists() {
  gcloud secrets describe "$1" --project "$PROJECT" >/dev/null 2>&1
}

echo "Project:  $PROJECT ($ENV)"
echo "Secrets:  app=$APP_SECRET  migrator=$MIGRATOR_SECRET"

if [[ "$ENV" == prod ]]; then
  read -r -p "This changes PROD. Type 'yes' to continue: " answer
  [[ "$answer" == yes ]] || die "aborted"
fi

if (( ROLLBACK )); then
  step "Rollback: the owner's DSN back as the newest app-secret version"
  secret_exists "$MIGRATOR_SECRET" || die "$MIGRATOR_SECRET does not exist; nothing to roll back to"
  [[ "$(secret_user "$MIGRATOR_SECRET")" == "$OWNER_ROLE" ]] \
    || die "$MIGRATOR_SECRET does not hold the $OWNER_ROLE DSN; not copying it"
  gcloud secrets versions access latest --secret "$MIGRATOR_SECRET" --project "$PROJECT" \
    | gcloud secrets versions add "$APP_SECRET" --project "$PROJECT" --data-file=-
  [[ "$(secret_user "$APP_SECRET")" == "$OWNER_ROLE" ]] || die "the app secret did not change"
  echo "Done. New instances and Job executions now connect as $OWNER_ROLE."
  exit 0
fi

step "Checking what is already done"
app_user="$(secret_user "$APP_SECRET")"
[[ -n "$app_user" ]] || die "could not read a user name from $APP_SECRET"
if [[ "$app_user" == "$APP_ROLE" ]]; then
  echo "$APP_SECRET already connects as $APP_ROLE: steps 0-5 are done. Verifying only."
  "$PYTHON" scripts/ensure_app_db_role.py "$ENV_FLAG" --dry-run
  exit $?
fi
[[ "$app_user" == "$OWNER_ROLE" ]] \
  || die "$APP_SECRET connects as '$app_user', neither $OWNER_ROLE nor $APP_ROLE; stopping"
echo "$APP_SECRET connects as $OWNER_ROLE: starting the rollout."

step "0. Generating the app role's password (not shown)"
QUANTCORE_APP_DB_PASSWORD="$(openssl rand -base64 33)"
export QUANTCORE_APP_DB_PASSWORD
trap 'unset QUANTCORE_APP_DB_PASSWORD' EXIT

step "1. Creating the role and verifying its grants"
"$PYTHON" scripts/ensure_app_db_role.py "$ENV_FLAG" \
  || die "step 1 failed (error above; if it could not connect, start the $ENV proxy). It runs in one transaction, so the database is unchanged, and no secret has been touched."

step "2. The owner's DSN in $MIGRATOR_SECRET"
if secret_exists "$MIGRATOR_SECRET"; then
  [[ "$(secret_user "$MIGRATOR_SECRET")" == "$OWNER_ROLE" ]] \
    || die "$MIGRATOR_SECRET exists but does not hold the $OWNER_ROLE DSN; fix it by hand"
  echo "Already exists and holds $OWNER_ROLE; kept."
else
  gcloud secrets versions access latest --secret "$APP_SECRET" --project "$PROJECT" \
    | gcloud secrets create "$MIGRATOR_SECRET" --project "$PROJECT" --data-file=-
  [[ "$(secret_user "$MIGRATOR_SECRET")" == "$OWNER_ROLE" ]] \
    || die "$MIGRATOR_SECRET was created but does not hold the $OWNER_ROLE DSN"
fi

step "3-4. Pointing the migrate Job at $MIGRATOR_SECRET and running it once"
migrate_args=(--execute)
[[ "$ENV" == prod ]] && migrate_args=(--prod --execute)
"$MIGRATE_JOB_SCRIPT" "${migrate_args[@]}" \
  || die "the migrate Job step failed; $APP_SECRET is unchanged. Fix it and re-run this script."

step "5. The app secret's new version, connecting as $APP_ROLE"
gcloud secrets versions access latest --secret "$APP_SECRET" --project "$PROJECT" \
  | "$PYTHON" scripts/ensure_app_db_role.py --swap-dsn \
  | gcloud secrets versions add "$APP_SECRET" --project "$PROJECT" --data-file=-
[[ "$(secret_user "$APP_SECRET")" == "$APP_ROLE" ]] \
  || die "$APP_SECRET does not connect as $APP_ROLE after step 5"

cat <<EOF

Done: $APP_SECRET now connects as $APP_ROLE.

6. Roll: the report and news Jobs pick it up on their next run; the api on its next
   revision (the next deploy.yml on test, the next prod-rollout.yml on prod).
7. Verify after that: re-run this script (it only verifies now), check the api log for
   "schema check: ... missing=0 mismatch=0", and that the next migrate run succeeds.
Rollback: $0 ${ENV_FLAG} --rollback
EOF
