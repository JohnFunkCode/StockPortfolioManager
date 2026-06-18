#!/usr/bin/env bash
#
# Grant the dev team roles/iap.httpsResourceAccessor on the quantui Cloud Run
# service (direct IAP integration) so they can log in through IAP.
#
# Each account ALSO needs to be a test user on the OAuth consent screen
# (Console -> APIs & Services -> OAuth consent screen -> Audience -> Add users)
# while the app is in "Testing" status. Both must match for login to succeed.
#
# Usage:  ./scripts/grant_quantui_iap_access.sh [PROJECT [REGION [SERVICE]]]
#   Defaults to the TEST project; pass quantcore-prod-20260606 to grant in prod.
#   e.g.  ./scripts/grant_quantui_iap_access.sh quantcore-prod-20260606
#
set -euo pipefail

PROJECT="${1:-quantcore-test-20260606}"
REGION="${2:-us-central1}"
SERVICE="${3:-quantui}"
ROLE="roles/iap.httpsResourceAccessor"

USERS=(
  "funkjohn@gmail.com"
  "jlsager@csuchico.edu"
  "john@johnfunk.com"
  "musicalmacdonald@gmail.com"
  "superdavidabrown@gmail.com"
  "thomasdfowler@gmail.com"
  "thomas@zoidbergfolio.com"
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
