# Prod access grants for Thomas (for John to run)

**Goal:** give Thomas's account IAM on the prod project `quantcore-prod-20260606`
(project # `127961694257`, region `us-central1`) so he can:

1. Reach the prod Cloud SQL DB locally via the Cloud SQL Auth Proxy.
2. Mint his own `QUANTCORE_MCP_TOKEN` (the MCP data tools).

All three commands below are run by **John** (or anyone with admin on the prod project).

---

## 0. Set the grantee account

Thomas has two Google accounts; grant the one he'll actually use:

```bash
# Primary (the account currently active on his machine):
GRANTEE="user:thomas@zoidbergfolio.com"

# ...or his other account, if that's the one to authorize:
# GRANTEE="user:thomasdfowler@gmail.com"

PROJECT="quantcore-prod-20260606"
```

---

## 1. Cloud SQL access (DB / proxy)

Lets the Cloud SQL Auth Proxy authenticate to the prod instance under Thomas's identity.

```bash
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="$GRANTEE" \
  --role="roles/cloudsql.client"
```

## 2. MCP token signing secret

Lets `scripts/mint_prod_jwt.py` read `quantcore-jwt-secret` to mint Thomas's prod JWT.

```bash
gcloud secrets add-iam-policy-binding quantcore-jwt-secret \
  --project="$PROJECT" \
  --member="$GRANTEE" \
  --role="roles/secretmanager.secretAccessor"
```

## 3. (Optional) Prod DB DSN secret

Only needed if Thomas should pull the prod DB password from Secret Manager rather
than using the password John already shared. Skip if not needed.

```bash
gcloud secrets add-iam-policy-binding quantcore-prod-db-dsn \
  --project="$PROJECT" \
  --member="$GRANTEE" \
  --role="roles/secretmanager.secretAccessor"
```

---

## Lighter alternative (no GCP grant for the token)

If granting project access is undesirable, John can instead hand Thomas the **raw**
`quantcore-jwt-secret` value out-of-band. Thomas then mints offline with no GCP access:

```bash
export QUANTCORE_JWT_SECRET=<value-from-john>
python scripts/mint_prod_jwt.py --secret-source env --output token --expires-hours 2160 --sub thomas
```

(This unblocks only the MCP token, not the direct DB path — that still needs grant #1.)

---

## Verification (Thomas runs, after grants land)

```bash
# Confirm the active account is the granted one:
gcloud auth list

# DB / proxy access:
gcloud projects describe quantcore-prod-20260606 --format="value(projectId,lifecycleState)"

# MCP token can be minted (should print a token, no PERMISSION_DENIED):
python scripts/mint_prod_jwt.py --output token --expires-hours 2160 --sub thomas
```

Grants are usually effective within a minute or two.
