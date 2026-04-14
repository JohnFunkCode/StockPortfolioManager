#!/usr/bin/env bash
# =============================================================================
# One-time setup: GCP infrastructure for CI/CD
#
# Run this ONCE to wire up GitHub Actions → Artifact Registry → Cloud Run.
# After this script, every push to main deploys automatically.
#
# Prerequisites:
#   gcloud auth login
#   gcloud config set project stock-portfolio-tfowler
#   gh auth login         (GitHub CLI — brew install gh)
#
# Usage:
#   bash scripts/setup-gcp-cicd.sh
# =============================================================================

set -euo pipefail

PROJECT="stock-portfolio-tfowler"
REGION="us-central1"
REPO="stock-portfolio"
SERVICE_ACCOUNT="github-actions-deploy"
WIF_POOL="github-actions-pool"
WIF_PROVIDER="github-actions-provider"
GITHUB_REPO="thomasdfowler/StockPortfolioManager"   # change if your repo path differs

echo "=== Setting up CI/CD for ${PROJECT} ==="
echo ""

# ── 1. Enable required APIs ───────────────────────────────────────────────────
echo "[1/7] Enabling GCP APIs..."
gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  secretmanager.googleapis.com \
  sqladmin.googleapis.com \
  cloudscheduler.googleapis.com \
  pubsub.googleapis.com \
  --project="${PROJECT}"

# ── 2. Create Artifact Registry repository ───────────────────────────────────
echo "[2/7] Creating Artifact Registry repository..."
gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="Stock Portfolio Manager API images" \
  --project="${PROJECT}" 2>/dev/null || echo "  (already exists)"

# ── 3. Create deploy service account ─────────────────────────────────────────
echo "[3/7] Creating deploy service account..."
gcloud iam service-accounts create "${SERVICE_ACCOUNT}" \
  --display-name="GitHub Actions deploy SA" \
  --project="${PROJECT}" 2>/dev/null || echo "  (already exists)"

SA_EMAIL="${SERVICE_ACCOUNT}@${PROJECT}.iam.gserviceaccount.com"

# Grant minimum required roles
for ROLE in \
  roles/artifactregistry.writer \
  roles/run.developer \
  roles/iam.serviceAccountUser \
  roles/secretmanager.secretAccessor \
  roles/cloudsql.client; do
  gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet
done
echo "  Roles granted to ${SA_EMAIL}"

# ── 4. Create Workload Identity Federation pool + provider ───────────────────
echo "[4/7] Configuring Workload Identity Federation..."
gcloud iam workload-identity-pools create "${WIF_POOL}" \
  --location=global \
  --display-name="GitHub Actions pool" \
  --project="${PROJECT}" 2>/dev/null || echo "  (pool already exists)"

gcloud iam workload-identity-pools providers create-oidc "${WIF_PROVIDER}" \
  --location=global \
  --workload-identity-pool="${WIF_POOL}" \
  --display-name="GitHub OIDC provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository == '${GITHUB_REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --project="${PROJECT}" 2>/dev/null || echo "  (provider already exists)"

PROJECT_NUMBER=$(gcloud projects describe "${PROJECT}" --format="value(projectNumber)")
WIF_PROVIDER_FULL="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"

# Allow the GitHub repo to impersonate the deploy SA
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_REPO}" \
  --project="${PROJECT}" \
  --quiet

echo "  WIF provider: ${WIF_PROVIDER_FULL}"

# ── 5. Store GitHub Actions secrets ──────────────────────────────────────────
echo "[5/7] Storing secrets in GitHub repository..."
gh secret set WIF_PROVIDER \
  --body="${WIF_PROVIDER_FULL}" \
  --repo="${GITHUB_REPO}"

gh secret set WIF_SERVICE_ACCOUNT \
  --body="${SA_EMAIL}" \
  --repo="${GITHUB_REPO}"

# DATABASE_URL secret — prompt for value
echo ""
echo "  Enter the Cloud SQL connection string for GitHub Actions migrations."
echo "  Format: postgresql+psycopg2://app_user:PASS@127.0.0.1:5433/stock_portfolio"
read -r -s -p "  DATABASE_URL: " DB_URL
echo ""
gh secret set DATABASE_URL \
  --body="${DB_URL}" \
  --repo="${GITHUB_REPO}"

# ── 6. Create Cloud Run service (first deploy) ───────────────────────────────
echo "[6/7] Creating Cloud Run service placeholder..."
# This is a dummy deploy so the service exists before GitHub Actions first runs.
# GitHub Actions will overwrite it on first push.
gcloud run deploy "stock-portfolio-api" \
  --image="us-docker.pkg.dev/cloudrun/container/hello" \
  --region="${REGION}" \
  --allow-unauthenticated \
  --project="${PROJECT}" \
  --quiet 2>/dev/null || echo "  (service already exists)"

# ── 7. Print next steps ───────────────────────────────────────────────────────
echo ""
echo "[7/7] Setup complete!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "NEXT STEPS"
echo ""
echo "1. Verify these GCP secrets exist in Secret Manager:"
echo "     jwt-secret, db-connection-string, google-client-id, google-client-secret"
echo "   Add any that are missing:"
echo "     echo -n 'VALUE' | gcloud secrets create SECRET_NAME --data-file=- --project=${PROJECT}"
echo ""
echo "2. Push to main to trigger first real deploy:"
echo "     git push origin main"
echo ""
echo "3. Watch the deployment:"
echo "     gh run watch"
echo ""
echo "4. After first successful deploy, update CORS in api/app.py:"
echo "     Replace 'http://localhost:5173' with your Cloud Run URL."
echo ""
echo "Artifact Registry: us-central1-docker.pkg.dev/${PROJECT}/${REPO}/api"
echo "Cloud Run:         https://console.cloud.google.com/run?project=${PROJECT}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
