#!/usr/bin/env bash
# Idempotently create or update the quantcore-migrate Cloud Run Job and its service
# account (issue #200, plan Step 3). An operator runs this once per project; the
# workflows then only point the Job at a new image and execute it.
#
# What it sets up, all safe to re-run:
#   1. SA quantcore-migrate@<project> (D3: a dedicated identity, so the Job's grants
#      are its own and not the report Job's).
#   2. Its grants: roles/cloudsql.client on the project, secretAccessor on the DSN
#      secret only, and roles/iam.serviceAccountUser on it for the CI deployer
#      (quantcore-deployer@, or $DEPLOYER_SA), which must act as it to execute the Job.
#   3. The Job, with the Cloud SQL instance and the QUANTCORE_DB_DSN secret copied from
#      quantcore-report in the same project so the two cannot drift, a 600s task
#      timeout and no retries (a migration failure is deterministic).
#   An existing Job is brought to that shape with --update-secrets and
#   --add-cloudsql-instances, never --set-*: those replace the whole set.
#
# Nothing here reads or prints the DSN: the Job gets a reference to the secret.
#
#   ./scripts/ensure_migrate_job.sh --tag <trial-tag>            # test project
#   ./scripts/ensure_migrate_job.sh --tag <trial-tag> --execute  # ...then run it once
#   ./scripts/ensure_migrate_job.sh --prod --image <ref@digest>  # prod (prompts)
#   ./scripts/ensure_migrate_job.sh --dry-run ...                # print, change nothing
#
# --tag names an image in this project's AR repo; --image takes a full reference. One
# of them is required to create the Job; on an existing Job the image is left alone
# unless one is given (CI sets it on every run). Never warm or tag a trial build as
# :latest in test AR (CLAUDE.md, cloud-build-speed-plan.md).
#
# Plan: docs/proposals/flyway-automation-plan.md
set -euo pipefail

PROJECT="quantcore-test-20260606"
PROD=0
TAG=""
IMAGE=""
EXECUTE=0
DRY_RUN=0
while (( $# )); do
  case "$1" in
    --prod) PROD=1; PROJECT="quantcore-prod-20260606" ;;
    --tag) TAG="${2:?--tag needs a value}"; shift ;;
    --image) IMAGE="${2:?--image needs a value}"; shift ;;
    --execute) EXECUTE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "ensure_migrate_job: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
if [[ -n "$TAG" && -n "$IMAGE" ]]; then
  echo "ensure_migrate_job: pass --tag or --image, not both" >&2
  exit 2
fi

REGION="us-central1"
AR_REPO="${AR_REPO:-quantcore}"
JOB="quantcore-migrate"
SA="quantcore-migrate@${PROJECT}.iam.gserviceaccount.com"
DEPLOYER_SA="${DEPLOYER_SA:-quantcore-deployer@${PROJECT}.iam.gserviceaccount.com}"
[[ -n "$TAG" ]] && IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${JOB}:${TAG}"

if (( PROD && ! DRY_RUN )); then
  read -r -p "Create/update ${JOB} in PROD (${PROJECT})? [y/N] " ok
  [[ "$ok" == "y" ]] || exit 1
fi

# Mutating calls go through `run`, so --dry-run prints them instead. Reads always run.
run() {
  if (( DRY_RUN )); then
    echo "+ $*"
  else
    "$@"
  fi
}

echo "Target project: ${PROJECT}$( (( DRY_RUN )) && echo ' (dry run)')"

# ---- what to copy from quantcore-report ----
# --format=json on the whole Job, then walk it (a json(...) projection emits the bare
# value, not the nested document; see ensure_news_job.sh).
# A command substitution, not <(...), so a failed describe stops the script.
report="$(
  gcloud run jobs describe quantcore-report --project "$PROJECT" --region "$REGION" \
    --format=json | python3 -c '
import json, sys
job = json.load(sys.stdin)
tmpl = job["spec"]["template"]
sql = tmpl["metadata"].get("annotations", {}).get("run.googleapis.com/cloudsql-instances", "")
ref = ""
for e in tmpl["spec"]["template"]["spec"]["containers"][0].get("env", []):
    k = e.get("valueFrom", {}).get("secretKeyRef")
    if e.get("name") == "QUANTCORE_DB_DSN" and k:
        ref = k["name"] + ":" + k["key"]
print(sql or "-", ref or "-")'
)"
read -r SQL DSN_REF <<<"$report"
if [[ "$SQL" == "-" || "$DSN_REF" == "-" ]]; then
  echo "ensure_migrate_job: quantcore-report has no Cloud SQL instance or no QUANTCORE_DB_DSN secret;" \
       "nothing to copy" >&2
  exit 1
fi
DSN_SECRET="${DSN_REF%%:*}"
echo "Cloud SQL: ${SQL}"
echo "DSN secret: ${DSN_REF} (reference only)"

# Validate before any change: creating the Job needs an image, and an invocation that
# cannot finish must not leave a half-made SA and grants behind.
JOB_EXISTS=0
if gcloud run jobs describe "$JOB" --project "$PROJECT" --region "$REGION" >/dev/null 2>&1; then
  JOB_EXISTS=1
elif [[ -z "$IMAGE" ]]; then
  echo "ensure_migrate_job: ${JOB} does not exist yet; pass --tag or --image to create it" >&2
  exit 2
fi

# ---- the service account and its grants ----
if gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
  echo "Service account ${SA} already exists."
else
  run gcloud iam service-accounts create "${JOB}" --project "$PROJECT" \
    --display-name "Flyway migrations (quantcore-migrate Job, #200)"
fi
# add-iam-policy-binding is idempotent. --format=none: the printed policy is noise.
run gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:${SA}" --role roles/cloudsql.client --condition=None --format=none
run gcloud secrets add-iam-policy-binding "$DSN_SECRET" --project "$PROJECT" \
  --member "serviceAccount:${SA}" --role roles/secretmanager.secretAccessor --format=none
run gcloud iam service-accounts add-iam-policy-binding "$SA" --project "$PROJECT" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/iam.serviceAccountUser --format=none

# ---- the Job ----
shape=(--service-account "$SA" --task-timeout 600s --max-retries 0 --cpu 1 --memory 1Gi)
if (( JOB_EXISTS )); then
  echo "Job ${JOB} exists; updating it in place."
  image_arg=()
  [[ -n "$IMAGE" ]] && image_arg=(--image "$IMAGE")
  run gcloud run jobs update "$JOB" --project "$PROJECT" --region "$REGION" \
    ${image_arg[@]+"${image_arg[@]}"} "${shape[@]}" \
    --add-cloudsql-instances "$SQL" --update-secrets "QUANTCORE_DB_DSN=${DSN_REF}"
else
  run gcloud run jobs create "$JOB" --project "$PROJECT" --region "$REGION" \
    --image "$IMAGE" "${shape[@]}" \
    --set-cloudsql-instances "$SQL" --set-secrets "QUANTCORE_DB_DSN=${DSN_REF}"
fi

if (( EXECUTE )); then
  run gcloud run jobs execute "$JOB" --project "$PROJECT" --region "$REGION" --wait
else
  echo "Done. Run it once: gcloud run jobs execute ${JOB} --project ${PROJECT} --region ${REGION} --wait"
fi
