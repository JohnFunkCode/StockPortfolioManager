#!/usr/bin/env bash
# Apply the pending Flyway migrations as a CI step, before the roll-out (issue #200, Step 4).
#
# Run by deploy.yml (after the build) and prod-rollout.yml (after the promotion), before the
# roll-out:
#
#   scripts/ci_migrate.sh --project <id> --region <region> --image <ref:tag | ref@digest>
#
#   1. Fail if the quantcore-migrate Job does not exist (or the deployer cannot see it).
#      This is deliberately NOT a ::warning:: skip like the roll-out's first-deploy guards
#      (#163): skipping would roll out an image ahead of its schema, the failure #200
#      removes. Create it once with scripts/ensure_migrate_job.sh.
#   2. Point the Job at this commit's quantcore-migrate image. --image only, never --set-*
#      (those replace the whole set; the Job's secret and Cloud SQL attachment stay as
#      ensure_migrate_job.sh left them).
#   3. Execute it and wait. A non-zero exit fails the step, so the roll-out never starts
#      and the previous revisions keep serving on an unchanged schema.
#   4. On failure, print the execution's last log lines (Cloud Logging) and one ::error::
#      saying which recovery applies: a refused contract migration (entrypoint exit 3)
#      goes the manual flyway.sh path; anything else is fixed forward.
#      Reading the logs is best effort: without roles/logging.viewer the step still fails
#      for the migration, and says where the logs are.
#
# Nothing here reads the DSN: the Job holds a reference to the secret, and the logs it
# prints are the entrypoint's, which name the database as host/db, never the DSN.
#
# Plan: docs/proposals/flyway-automation-plan.md
set -euo pipefail

PROJECT=""
REGION=""
IMAGE=""
while (( $# )); do
  case "$1" in
    --project) PROJECT="${2:?--project needs a value}"; shift ;;
    --region) REGION="${2:?--region needs a value}"; shift ;;
    --image) IMAGE="${2:?--image needs a value}"; shift ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "ci_migrate: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
if [[ -z "$PROJECT" || -z "$REGION" || -z "$IMAGE" ]]; then
  echo "ci_migrate: --project, --region and --image are all required" >&2
  exit 2
fi

JOB="quantcore-migrate"
LOG_LINES="${CI_MIGRATE_LOG_LINES:-80}"
at=(--project "$PROJECT" --region "$REGION")

if ! gcloud run jobs describe "$JOB" "${at[@]}" >/dev/null 2>&1; then
  echo "::error title=${JOB} missing::${JOB} Job not found in ${PROJECT}, or the deployer cannot read it. Nothing was migrated and nothing will roll out. Create it with scripts/ensure_migrate_job.sh (docs/proposals/flyway-automation-plan.md)."
  exit 1
fi

echo "Migrating ${PROJECT} with ${IMAGE}"
gcloud run jobs update "$JOB" "${at[@]}" --image "$IMAGE"

if gcloud run jobs execute "$JOB" "${at[@]}" --wait; then
  echo "Migrations applied (or none pending); the roll-out may proceed."
  exit 0
fi

# ---- failure: show why, then fail the step ----
# The concurrency: group serializes deploys, so the newest execution is this one.
exec_name="$(gcloud run jobs executions list --job "$JOB" "${at[@]}" \
  --sort-by=~metadata.creationTimestamp --limit 1 --format='value(metadata.name)' 2>/dev/null || true)"

logs=""
if [[ -n "$exec_name" ]]; then
  filter="resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${JOB}\""
  filter+=" AND labels.\"run.googleapis.com/execution_name\"=\"${exec_name}\""
  # Newest first, then reversed into reading order (sed, not tac: macOS has no tac).
  logs="$(gcloud logging read "$filter" --project "$PROJECT" --limit "$LOG_LINES" \
    --order desc --format='value(textPayload)' 2>/dev/null | sed -n '1!G;h;$p' || true)"
fi

echo "::group::${JOB} execution ${exec_name:-(unknown)}: last ${LOG_LINES} log lines"
if [[ -n "$logs" ]]; then
  printf '%s\n' "$logs"
else
  echo "(no log lines read: they can lag the execution by a few seconds, or the deployer lacks roles/logging.viewer)"
  echo "Console: https://console.cloud.google.com/run/jobs/details/${REGION}/${JOB}/executions?project=${PROJECT}"
fi
echo "::endgroup::"

if grep -q "migrate: REFUSED" <<<"$logs"; then
  echo "::error title=migration refused::A pending migration is a contract or non-transactional change; nothing was applied and nothing rolled out. Apply it by hand (./scripts/flyway.sh migrate, with --prod for prod), then re-run this workflow."
else
  echo "::error title=migration failed::${JOB} failed in ${PROJECT}; nothing rolled out. The failing version was rolled back. Fix forward with a new migration: never edit an applied one. The log is in the group above."
fi
exit 1
