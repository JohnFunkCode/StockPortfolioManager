# Minting prod JWT tokens

The production API (`quantcore-api` on Cloud Run, project `quantcore-prod-20260606`)
enforces an **app-level JWT** on every request (see [`api/auth.py`](../../api/auth.py)).
Since the BYOK rollout (2026-07-18) auth is **dual-mode**:

- **ES256 per-user tokens** — minted automatically by the deployed QuantUI Express server
  from the IAP identity (`frontend/server/auth.mjs`, `quantui-signing-key` secret). Nothing
  to do by hand; if you're using the hosted UI you already have one.
- **HS256 service tokens** — the manually minted tokens **this doc covers**, signed with
  `quantcore-jwt-secret`. Still the path for MCP clients, `curl`/CLI, and the dev UI.

Any caller — the CLI smoke, an MCP client, a `curl`, the local dev UI — must send:

```
Authorization: Bearer <token>
```

A request with no token (or a bad/expired one) gets **HTTP 401**. Tokens are
**short-lived and self-contained**: nothing is stored or revoked server-side, so when
one expires you just mint another. The long-lived secrets are the signing keys — the
HMAC key `quantcore-jwt-secret` (this doc's tokens) and the ES256 keypair
`quantui-signing-key`/`-pub` (the UI's per-user tokens) — all held in Secret Manager
on the prod project.

> Local development and the docker-compose stack run with `AUTH_DISABLED=1`, so they
> need **no token**. Tokens are only for talking to the **prod** deployment.

---

## One-time prerequisites

You need `gcloud` authenticated as someone who can read the secret:

```bash
gcloud auth login
gcloud config set project quantcore-prod-20260606
# Confirm you can read the signing secret (prints the key — do this once, in private):
gcloud secrets versions access latest --secret=quantcore-jwt-secret >/dev/null && echo OK
```

Access to `quantcore-jwt-secret` is granted via `roles/secretmanager.secretAccessor`
on that secret. If the check above 403s, ask an owner to grant it.

The minting tool is [`scripts/mint_prod_jwt.py`](../../scripts/mint_prod_jwt.py). It
fetches the signing key from Secret Manager itself (default) and **never prints the
secret** — only the resulting token.

---

## Mint a token from scratch

### A) For the React UI (`npm run dev` against prod)

```bash
python scripts/mint_prod_jwt.py --output frontend-env --expires-hours 8
cd frontend && npm run dev      # open http://localhost:5173
```

This writes `frontend/.env.local` (gitignored) with the prod API proxy target and the
bearer token. The vite dev server proxies `/api` to prod **server-side**, so the
browser stays same-origin — no CORS setup needed. The token is written to that file
only; it is never echoed to the terminal.

### B) For a `curl` / CLI call

```bash
TOKEN="$(python scripts/mint_prod_jwt.py --expires-hours 1)"
curl -H "Authorization: Bearer $TOKEN" \
  "https://quantcore-api-127961694257.us-central1.run.app/api/portfolio?owner=john"
```

### C) For an MCP client (wrapper `/mcp` endpoints)

```bash
eval "$(python scripts/mint_prod_jwt.py --output export --expires-hours 8)"
# -> sets QUANTCORE_MCP_TOKEN in the current shell; reference it in your client config.
```

The wrappers forward the caller's `Authorization` header to the API, which is where the
JWT is actually checked.

---

## When the old token expires

**Symptom:** a previously-working call starts returning **401**, or the UI shows auth
errors / empty data. There is nothing to clean up — just mint a fresh token the same
way you did above:

| You're using…        | Refresh command                                                        |
| -------------------- | ---------------------------------------------------------------------- |
| React UI             | `python scripts/mint_prod_jwt.py --output frontend-env` then restart `npm run dev` |
| curl / CLI           | re-run the `TOKEN="$(python scripts/mint_prod_jwt.py …)"` line          |
| MCP client           | re-run the `eval "$(python scripts/mint_prod_jwt.py --output export …)"` line |

Default lifetime is **24 hours** (covers after-hours work without re-minting); pass
`--expires-hours N` to shorten it for a one-off call. Keep lifetimes only as long as you
need — a leaked token can't be revoked short of rotating the signing key.

### Useful flags

- `--sub <name>` — token subject/owner (default `john`); the API uses it for ownership.
- `--expires-hours <N>` — lifetime in hours (default `1`).
- `--secret-source env` — sign with `QUANTCORE_JWT_SECRET` from the environment instead
  of calling gcloud (for CI, or when you've injected the key yourself).
- `--secret-name` / `--project` — override the Secret Manager source if it ever moves.

Run `python scripts/mint_prod_jwt.py --help` for the full list.

---

## Rotating the signing key (advanced, breaks all tokens)

Rotating `quantcore-jwt-secret` invalidates **every** outstanding token at once, so do
it deliberately (e.g. on suspected leak):

```bash
# 1. Add a new key version (48 random bytes, base64).
openssl rand -base64 48 | gcloud secrets versions add quantcore-jwt-secret \
  --project quantcore-prod-20260606 --data-file=-

# 2. Roll the API so it picks up the new version (it reads :latest at startup).
gcloud run services update quantcore-api --project quantcore-prod-20260606 \
  --region us-central1 --update-secrets QUANTCORE_JWT_SECRET=quantcore-jwt-secret:latest

# 3. Everyone re-mints (the steps above). Old tokens now 401.
```

---

## Security rules

- **Never commit a token.** `frontend/.env.local` is gitignored; keep it that way.
- **Never echo the signing secret** into the terminal, logs, or shell history. The
  script and the prereq check are written to avoid this — don't paste the raw secret.
- **Prefer short lifetimes.** Tokens can't be revoked individually; expiry is the only
  guardrail until the key is rotated.
- The crown-jewel secrets — the prod **database** password, the HS256 JWT signing key,
  the ES256 UI signing key (`quantui-signing-key`), and the BYOK keyproxy private key
  (`keyproxy-private-key`) — all live in Secret Manager, never in the repo or a shared
  `.env`. Private keys are piped straight into Secret Manager at creation and never
  printed.
