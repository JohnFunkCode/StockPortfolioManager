#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup_test_wif.sh — one-time Workload Identity Federation (WIF) setup so that
# .github/workflows/deploy.yml can auto-deploy to the TEST project on a push to
# `main`. Mirrors the prod-project setup done in P9 (see docs/operations/
# prod-promotion.md), but for `quantcore-test-20260606`.
#
# WHAT THIS CREATES (idempotent — safe to re-run; existing resources are reused):
#   1. A WIF pool   `github-test`   + an OIDC provider `github` for token.actions.githubusercontent.com,
#      attribute-restricted to THIS GitHub repository.
#   2. A deploy service account `quantcore-deployer@<test-project>` with the
#      minimum roles CI needs: run.developer, cloudbuild.builds.editor,
#      artifactregistry.writer (project-level), plus iam.serviceAccountUser on
#      the Cloud Run runtime SA (so it can deploy services that *run as* that SA).
#   3. An IAM binding letting the GitHub repo impersonate the deploy SA via WIF.
#
# AFTER IT FINISHES it prints the two values to store as repo secrets:
#       GCP_WIF_PROVIDER   (the full provider resource name)
#       GCP_DEPLOY_SA      (the deploy SA email)
# Set them with:
#       gh secret set GCP_WIF_PROVIDER --repo "$REPO" --body "<provider>"
#       gh secret set GCP_DEPLOY_SA    --repo "$REPO" --body "<sa-email>"
# Once both secrets exist, deploy.yml's `preflight` job emits has_creds=true and
# the `deploy` job rolls out on the next push to main. No workflow edit needed.
#
# DB SAFETY: this script touches ONLY IAM / WIF in the TEST project. It creates
# no Cloud SQL, runs no migrations, and never touches the prod project or any DB.
# All commands are IAM/admin actions — run them yourself (you own these grants).
# ---------------------------------------------------------------------------
set -euo pipefail

# --- Config (override via env if your names differ) ------------------------
PROJECT_ID="${PROJECT_ID:-quantcore-test-20260606}"
REPO="${REPO:-JohnFunkCode/StockPortfolioManager}"   # owner/name as GitHub knows it
POOL_ID="${POOL_ID:-github-test}"
PROVIDER_ID="${PROVIDER_ID:-github}"
DEPLOY_SA_NAME="${DEPLOY_SA_NAME:-quantcore-deployer}"
REGION="${REGION:-us-central1}"
# The runtime SA the TEST Cloud Run services run as. Leave RUNTIME_SA unset and the
# script auto-detects it from the deployed quantcore-api service (falling back to the
# default compute SA). Override only if you know it differs.
RUNTIME_SA="${RUNTIME_SA:-}"

DEPLOY_SA_EMAIL="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo ">> Project:      $PROJECT_ID"
echo ">> Repo:         $REPO"
echo ">> Deploy SA:    $DEPLOY_SA_EMAIL"
echo

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
echo ">> Project number: $PROJECT_NUMBER"

# --- Resolve the runtime SA the test Cloud Run services run as -------------
# Prefer what quantcore-api is actually deployed with; an empty serviceAccountName
# means the service uses the project's default compute SA. Honor an explicit override.
if [ -z "$RUNTIME_SA" ]; then
  RUNTIME_SA="$(gcloud run services describe quantcore-api \
    --project "$PROJECT_ID" --region "$REGION" \
    --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || true)"
  if [ -z "$RUNTIME_SA" ]; then
    RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
    echo ">> Runtime SA:   $RUNTIME_SA  (default compute SA — quantcore-api had no explicit SA)"
  else
    echo ">> Runtime SA:   $RUNTIME_SA  (detected from quantcore-api)"
  fi
else
  echo ">> Runtime SA:   $RUNTIME_SA  (override)"
fi
echo

# --- 1. Enable the IAM Credentials API (needed for WIF impersonation) ------
gcloud services enable iamcredentials.googleapis.com --project "$PROJECT_ID"

# --- 2. WIF pool -----------------------------------------------------------
if ! gcloud iam workload-identity-pools describe "$POOL_ID" \
      --project "$PROJECT_ID" --location global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$POOL_ID" \
    --project "$PROJECT_ID" --location global \
    --display-name "GitHub Actions (test)"
else
  echo "Pool $POOL_ID already exists — reusing."
fi

# --- 3. OIDC provider, restricted to this repo -----------------------------
if ! gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
      --project "$PROJECT_ID" --location global \
      --workload-identity-pool "$POOL_ID" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --project "$PROJECT_ID" --location global \
    --workload-identity-pool "$POOL_ID" \
    --display-name "GitHub OIDC" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition "assertion.repository=='${REPO}'"
else
  echo "Provider $PROVIDER_ID already exists — reusing."
fi

# --- 4. Deploy service account --------------------------------------------
if ! gcloud iam service-accounts describe "$DEPLOY_SA_EMAIL" \
      --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$DEPLOY_SA_NAME" \
    --project "$PROJECT_ID" \
    --display-name "QuantCore CI deployer (test)"
else
  echo "Deploy SA $DEPLOY_SA_EMAIL already exists — reusing."
fi

# --- 4b. Wait for the SA to propagate --------------------------------------
# A freshly created service account is not immediately usable as a member in
# IAM policy bindings (eventual consistency — you'll otherwise get
# "Service account ... does not exist" / INVALID_ARGUMENT). Poll describe until
# it resolves, then settle briefly before the bindings below.
echo "Waiting for $DEPLOY_SA_EMAIL to propagate..."
for _i in $(seq 1 30); do
  if gcloud iam service-accounts describe "$DEPLOY_SA_EMAIL" \
        --project "$PROJECT_ID" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
sleep 10  # extra settle so add-iam-policy-binding sees the new member

# --- 5. Project-level roles the deploy SA needs ----------------------------
for ROLE in roles/run.developer roles/cloudbuild.builds.editor roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:${DEPLOY_SA_EMAIL}" \
    --role "$ROLE" --condition None >/dev/null
  echo "Granted $ROLE to $DEPLOY_SA_EMAIL"
done

# --- 6. actAs the runtime SA (deploy services that run as it) ---------------
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --project "$PROJECT_ID" \
  --member "serviceAccount:${DEPLOY_SA_EMAIL}" \
  --role roles/iam.serviceAccountUser >/dev/null
echo "Granted iam.serviceAccountUser on $RUNTIME_SA to $DEPLOY_SA_EMAIL"

# --- 7. Let the GitHub repo impersonate the deploy SA via WIF ---------------
PRINCIPAL_SET="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${REPO}"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA_EMAIL" \
  --project "$PROJECT_ID" \
  --member "$PRINCIPAL_SET" \
  --role roles/iam.workloadIdentityUser >/dev/null
echo "Bound repo $REPO -> $DEPLOY_SA_EMAIL via WIF"

# --- 8. Print the values to store as repo secrets --------------------------
PROVIDER_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
cat <<EOF

============================================================================
WIF setup complete. Now set the two repo secrets:

  gh secret set GCP_WIF_PROVIDER --repo "$REPO" \\
    --body "$PROVIDER_RESOURCE"

  gh secret set GCP_DEPLOY_SA --repo "$REPO" \\
    --body "$DEPLOY_SA_EMAIL"

Verify:   gh secret list --repo "$REPO" | grep GCP_
Then the next push to main auto-deploys (deploy.yml preflight -> deploy).
============================================================================
EOF
