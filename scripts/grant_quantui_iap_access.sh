#!/usr/bin/env bash
#
# Grant the dev team roles/iap.httpsResourceAccessor on the quantui Cloud Run
# service (direct IAP integration) so they can log in through IAP.
#
# Each account ALSO needs to be a test user on the OAuth consent screen
# (Console -> APIs & Services -> OAuth consent screen -> Audience -> Add users)
# while the app is in "Testing" status. Both must match for login to succeed.
#
# Usage:  ./scripts/grant_quantui_iap_access.sh
#
set -euo pipefail

PROJECT="quantcore-test-20260606"
REGION="us-central1"
SERVICE="quantui"
ROLE="roles/iap.httpsResourceAccessor"

USERS=(
  "funkjohn@gmail.com"
  "jlsager@csuchico.edu"
  "john@johnfunk.com"
  "musicalmacdonald@gmail.com"
  "superdavidabrown@gmail.com"
)

for u in "${USERS[@]}"; do
  echo "==> Granting ${ROLE} to ${u} on ${SERVICE} (${PROJECT}/${REGION})"
  gcloud iap web add-iam-policy-binding \
    --resource-type=cloud-run \
    --service="${SERVICE}" \
    --region="${REGION}" \
    --project="${PROJECT}" \
    --member="user:${u}" \
    --role="${ROLE}"
done

echo
echo "Done. Current IAP IAM policy for ${SERVICE}:"
gcloud iap web get-iam-policy \
  --resource-type=cloud-run \
  --service="${SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT}"
