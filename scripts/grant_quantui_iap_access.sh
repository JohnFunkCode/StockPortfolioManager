#!/usr/bin/env bash
#
# Grant the dev team roles/iap.httpsResourceAccessor on the quantui Cloud Run
# service (direct IAP integration) so they can log in through IAP, AND write
# the matching owner_identities row in the same run (issue #126 Step 2.7).
# These two must never drift apart: a user with IAP access but no DB row (or
# vice versa) is exactly the state that lands a legitimate user on the harsh
# RestrictedAccess screen. See db/migrations/V3__owner_identities.sql.
#
# Each account ALSO needs to be a test user on the OAuth consent screen
# (Console -> APIs & Services -> OAuth consent screen -> Audience -> Add users)
# while the app is in "Testing" status. Both must match for login to succeed.
#
# Usage:  ./scripts/grant_quantui_iap_access.sh [PROJECT [REGION [SERVICE]]]
#   Defaults to the TEST project; pass quantcore-prod-20260606 to grant in prod.
#   e.g.  ./scripts/grant_quantui_iap_access.sh quantcore-prod-20260606
#
# The owner_identities write targets QUANTCORE_TEST_DB_DSN for the test
# project, or QUANTCORE_DB_DSN (the default DSN) for prod — both read from
# .env, same convention as scripts/with-test-db.sh. The corresponding Cloud
# SQL Auth Proxy tunnel must already be running locally (5433=prod, 5434=test).
#
set -euo pipefail

cd "$(dirname "$0")/.."

PROJECT="${1:-quantcore-test-20260606}"
REGION="${2:-us-central1}"
SERVICE="${3:-quantui}"
ROLE="roles/iap.httpsResourceAccessor"

# email:owner pairs. The owner handle is the canonical short value already
# used as positions.owner (e.g. 'john') — see db/migrations/V3__owner_identities.sql
# for the seed this list must stay in sync with.
#
# jlsager@csuchico.edu and thomasdfowler@gmail.com are deliberately absent:
# thomasdfowler@gmail.com is not a valid/active Google account, so IAM
# silently drops the `user:` binding even though gcloud reports success —
# no admin action fixes this. jlsager@csuchico.edu is the wrong identity for
# J.L. Sager (fails to authenticate for secret access); dr.sagerjl@gmail.com
# is the one that actually works. Both are known, don't re-add without a
# fresh working account.
USERS=(
  "funkjohn@gmail.com:john"
  "john@johnfunk.com:john"
  "musicalmacdonald@gmail.com:macdonald"
  "superdavidabrown@gmail.com:dabrown"
  "thomas@zoidbergfolio.com:thomas"
  "dr.sagerjl@gmail.com:sager"
)

# --- DB DSN selection --------------------------------------------------------
if [[ "${PROJECT}" == *prod* ]]; then
  DSN_VAR="QUANTCORE_DB_DSN"
else
  DSN_VAR="QUANTCORE_TEST_DB_DSN"
fi
DB_DSN=$(grep -E "^${DSN_VAR}=" .env | head -1 | cut -d= -f2-)
if [ -z "${DB_DSN}" ]; then
  echo "${DSN_VAR} not found in .env — cannot write owner_identities rows." >&2
  exit 1
fi
export QUANTCORE_DB_DSN="${DB_DSN}"
export PYTHONPATH=.

FAILED_DB=()

for pair in "${USERS[@]}"; do
  u="${pair%%:*}"
  owner="${pair##*:}"

  echo "==> Granting ${ROLE} to ${u} on ${SERVICE} (${PROJECT}/${REGION})"
  gcloud iap web add-iam-policy-binding \
    --resource-type=cloud-run \
    --service="${SERVICE}" \
    --region="${REGION}" \
    --project="${PROJECT}" \
    --member="user:${u}" \
    --role="${ROLE}"

  echo "==> Recording ${u} -> ${owner} in owner_identities (${DSN_VAR})"
  if ! OWNER_IDENTITY="${u}" OWNER_HANDLE="${owner}" python -c "
import os
import sys
from quantcore.db import get_connection

identity = os.environ['OWNER_IDENTITY']
owner = os.environ['OWNER_HANDLE']

conn = get_connection()
conn.execute(
    'INSERT INTO owner_identities (identity, owner, notes) '
    'VALUES (:identity, :owner, :notes) '
    'ON CONFLICT (identity) DO NOTHING',
    {
        'identity': identity,
        'owner': owner,
        'notes': 'scripts/grant_quantui_iap_access.sh',
    },
)
conn.commit()

# ON CONFLICT DO NOTHING means a pre-existing row for this identity is left
# untouched. Verify what actually landed rather than trusting the insert:
# a rerun after fixing a typo in USERS, or granting an identity that was
# already (mis)mapped to someone else, must not report success while the
# identity stays mapped to the wrong owner.
row = conn.execute(
    'SELECT owner FROM owner_identities WHERE identity = :identity',
    {'identity': identity},
).fetchone()
conn.close()

if row is None:
    print(f'{identity}: no owner_identities row found after insert', file=sys.stderr)
    sys.exit(1)

stored_owner = row['owner']
if stored_owner != owner:
    print(
        f'{identity}: already mapped to owner={stored_owner!r}, not the '
        f'requested {owner!r} — ON CONFLICT DO NOTHING left it untouched. '
        'Fix the USERS array (if this was a typo) or update the row by hand '
        '(if the existing mapping is the mistake); this script will not '
        'overwrite an existing mapping automatically.',
        file=sys.stderr,
    )
    sys.exit(1)
"; then
    echo "!! IAP grant for ${u} succeeded but the owner_identities write/verify FAILED." >&2
    FAILED_DB+=("${u}")
  fi
done

if [ "${#FAILED_DB[@]}" -gt 0 ]; then
  echo
  echo "FAILED: owner_identities write did not complete for: ${FAILED_DB[*]}" >&2
  echo "IAP access was granted for these accounts, but until the DB row is" >&2
  echo "fixed by hand they will still hit the restricted-access screen." >&2
  exit 1
fi

echo
echo "Done. Current IAP IAM policy for ${SERVICE}:"
gcloud iap web get-iam-policy \
  --resource-type=cloud-run \
  --service="${SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT}"
