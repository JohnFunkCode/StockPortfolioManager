#!/usr/bin/env bash
# Idempotently create the quantcore-news Cloud Run Job and its Cloud Scheduler entry.
#
# deploy.yml / prod-rollout.yml only UPDATE the Job's image and now warn loudly when
# it is missing (#275); this is the one-time operator step that creates it. Service
# account, Cloud SQL instance, and secrets are copied from the existing quantcore-report
# Job in the same project, so the two cannot drift. Safe to re-run: an existing Job or
# Scheduler entry is left alone.
#
# The one thing it always (re-)asserts is roles/run.invoker on the Job for the
# Scheduler's service account (#372). Without it the Scheduler's :run call fails with
# code 7 (PERMISSION_DENIED) every night and no execution starts; the binding is
# idempotent, so re-running this repairs a project where the entry already existed.
#
#   ./scripts/ensure_news_job.sh            # test project
#   ./scripts/ensure_news_job.sh --prod     # prod project (prompts)
set -euo pipefail

PROJECT="quantcore-test-20260606"
if [[ "${1:-}" == "--prod" ]]; then
  PROJECT="quantcore-prod-20260606"
  read -r -p "Create/verify quantcore-news in PROD (${PROJECT})? [y/N] " ok
  [[ "$ok" == "y" ]] || exit 1
fi
REGION="us-central1"
AR_REPO="${AR_REPO:-quantcore}"
TAG="${NEWS_IMAGE_TAG:-latest}"
SCHEDULE="30 17 * * 1-5"

echo "Target project: ${PROJECT}"

if gcloud run jobs describe quantcore-news --project "$PROJECT" --region "$REGION" >/dev/null 2>&1; then
  echo "Job quantcore-news already exists — leaving it alone."
else
  fmt() { gcloud run jobs describe quantcore-report --project "$PROJECT" --region "$REGION" --format="$1"; }
  SA="$(fmt 'value(spec.template.spec.template.spec.serviceAccountName)')"
  SQL="$(fmt 'value(spec.template.metadata.annotations."run.googleapis.com/cloudsql-instances")')"
  # --format=json on the whole Job, then walk it: a projection like
  # json(spec...env) would emit the bare array, not the nested document.
  SECRETS="$(gcloud run jobs describe quantcore-report --project "$PROJECT" --region "$REGION" \
    --format=json | python3 -c '
import json, sys
job = json.load(sys.stdin)
env = job["spec"]["template"]["spec"]["template"]["spec"]["containers"][0].get("env", [])
pairs = []
for e in env:
    ref = e.get("valueFrom", {}).get("secretKeyRef")
    if ref:
        pairs.append(e["name"] + "=" + ref["name"] + ":" + ref["key"])
print(",".join(pairs))')"
  gcloud run jobs create quantcore-news --project "$PROJECT" --region "$REGION" \
    --image "${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/quantcore-news:${TAG}" \
    --service-account "$SA" --set-cloudsql-instances "$SQL" --set-secrets "$SECRETS" \
    --set-env-vars NEWS_TASK_TIMEOUT_SECONDS=1800,NEWS_COLLECT_BUDGET_SECONDS=900 \
    --task-timeout 1800s --max-retries 0 --cpu 2 --memory 4Gi
fi

sched_sa() {
  gcloud scheduler jobs describe "$1" --project "$PROJECT" --location "$REGION" \
    --format='value(httpTarget.oauthToken.serviceAccountEmail)' 2>/dev/null || true
}
SCHED_SA="$(sched_sa quantcore-news-daily)"
[[ -n "$SCHED_SA" ]] || SCHED_SA="$(sched_sa quantcore-report-daily)"
if [[ -z "$SCHED_SA" ]]; then
  echo "ERROR: no Scheduler service account found on quantcore-news-daily or quantcore-report-daily." >&2
  exit 1
fi

if gcloud scheduler jobs describe quantcore-news-daily --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
  echo "Scheduler quantcore-news-daily already exists — leaving it alone."
else
  NUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
  gcloud scheduler jobs create http quantcore-news-daily --project "$PROJECT" --location "$REGION" \
    --schedule "$SCHEDULE" --time-zone America/New_York --http-method POST \
    --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${NUM}/jobs/quantcore-news:run" \
    --oauth-service-account-email "$SCHED_SA"
fi

# Always, not only on create (#372): idempotent, and the repair for an existing entry.
echo "Granting roles/run.invoker on quantcore-news to ${SCHED_SA}."
gcloud run jobs add-iam-policy-binding quantcore-news --project "$PROJECT" --region "$REGION" \
  --member "serviceAccount:${SCHED_SA}" --role roles/run.invoker >/dev/null
echo "Done. Verify: gcloud run jobs execute quantcore-news --project $PROJECT --region $REGION --wait"
