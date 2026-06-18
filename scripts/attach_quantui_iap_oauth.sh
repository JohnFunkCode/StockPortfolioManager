#!/usr/bin/env bash
#
# Attach a custom OAuth client (ID + secret) to the quantui Cloud Run service's
# IAP. Required for IAP login in a standalone (non-org) project, where the --iap
# deploy cannot auto-provision an OAuth client (symptom: 502 "Empty Google
# Account OAuth client ID(s)/secret(s)").
#
# Prereq: create the OAuth client first (Console -> APIs & Services -> Clients,
# type "Web application") and add the redirect URI
#   https://iap.googleapis.com/v1/oauth/clientIds/CLIENT_ID:handleRedirect
#
# The client secret is read interactively (hidden) and written only to a
# temp YAML that is deleted on exit -- it never touches shell history.
#
# Usage:  ./scripts/attach_quantui_iap_oauth.sh
#
set -euo pipefail

PROJECT="quantcore-test-20260606"
REGION="us-central1"
SERVICE="quantui"

read -r -p "OAuth Client ID: " CLIENT_ID
read -r -s -p "OAuth Client secret (hidden): " CLIENT_SECRET
echo

if [[ -z "${CLIENT_ID}" || -z "${CLIENT_SECRET}" ]]; then
  echo "ERROR: client id and secret are both required." >&2
  exit 1
fi

TMP_YAML="$(mktemp -t iap_settings.XXXXXX.yaml)"
cleanup() { rm -f "${TMP_YAML}"; }
trap cleanup EXIT

cat > "${TMP_YAML}" <<YAML
access_settings:
  oauth_settings:
    client_id: ${CLIENT_ID}
    client_secret: ${CLIENT_SECRET}
YAML

echo "==> Applying IAP OAuth settings to ${SERVICE} (${PROJECT}/${REGION})"
gcloud iap settings set "${TMP_YAML}" \
  --resource-type=cloud-run \
  --service="${SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT}"

echo "Done. (temp YAML shredded)"
