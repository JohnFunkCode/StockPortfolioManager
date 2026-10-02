#!/usr/bin/env bash
# Apply the Artifact Registry cleanup policy (scripts/ar_cleanup_policy.json) to the
# quantcore repo. Without it every build's images are kept forever: by 2026-10-02 the
# test repo held 80 GB (~110 versions per image) and prod 18 GB, and baking FinBERT
# into the api/news images (#280) adds ~440 MB per build to each.
#
# The policy deliberately ignores tag state. Prod deploys the *inner* manifest digest
# that prod-rollout.yml's `imagetools create` copies, and that manifest is untagged in
# the prod repo (the tag lands on the wrapping index) — so a "delete untagged" rule
# would delete the image prod is running. Instead: keep each image's 15 newest
# versions no matter what, delete the rest once they are older than 30 days. Keep
# rules always win over Delete rules.
#
# Applies in DRY-RUN mode by default: deletions are only logged (Cloud Audit Logs,
# "artifactregistry" DeleteVersion entries with dry-run). Review those, then re-run
# with --enforce.
#
#   ./scripts/apply_ar_cleanup_policy.sh                     # test, dry run
#   ./scripts/apply_ar_cleanup_policy.sh --prod              # prod, dry run (prompts)
#   ./scripts/apply_ar_cleanup_policy.sh --prod --enforce    # prod, deletes (prompts)
set -euo pipefail

PROJECT="quantcore-test-20260606"
MODE="--dry-run"
for arg in "$@"; do
  case "$arg" in
    --prod) PROJECT="quantcore-prod-20260606" ;;
    --enforce) MODE="--no-dry-run" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
REGION="us-central1"
AR_REPO="${AR_REPO:-quantcore}"
POLICY="$(dirname "$0")/ar_cleanup_policy.json"

echo "Target: ${PROJECT}/${REGION}/${AR_REPO}  mode: ${MODE#--}"
if [[ "$PROJECT" == *prod* ]]; then
  read -r -p "Apply the cleanup policy to PROD? [y/N] " ok
  [[ "$ok" == "y" ]] || exit 1
fi

gcloud artifacts repositories set-cleanup-policies "$AR_REPO" \
  --project "$PROJECT" --location "$REGION" --policy "$POLICY" "$MODE"
gcloud artifacts repositories describe "$AR_REPO" --project "$PROJECT" --location "$REGION" \
  --format='yaml(cleanupPolicies,cleanupPolicyDryRun)'
