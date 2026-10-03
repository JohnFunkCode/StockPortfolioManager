# BYOK key proxy

> Moved verbatim from `CLAUDE.md`, which keeps the rules an agent must not break as a short
> summary and links here for the explanation. Edit the detail here, the rule there.

## Flow, auth layers, deploy wiring

- **Flow:** browser vault (`frontend/src/vault/` — IndexedDB, passphrase PBKDF2 + AES-GCM;
  managed on the `/settings` page) seals the key per turn into a **single-use envelope**
  (`frontend/src/vault/envelope.ts` ↔ `keyproxy/crypto.py`, SPKI pin baked into the UI bundle,
  AAD binds `sub`/`jti`/scope-hash) → `/api/chat` carries envelope + scope through
  `quantcore-api` (never decrypted there) → **`keyproxy/`** (own FastAPI service, no DB) decrypts
  in memory, enforces scopes/budgets/replay (`scopes.py`, `sessions.py`, `replay.py`), streams
  SSE from Anthropic back through the chain.
- **Auth layers:** keyproxy is **IAM-locked on Cloud Run** (`--no-allow-unauthenticated`;
  `run.invoker` only for `quantcore-run@`; the api attaches a Google ID token in
  `X-Serverless-Authorization`) and runs as dedicated SA `keyproxy-runtime@` (zero project roles,
  per-secret grants only). App level: keyproxy verifies **ES256-only** user JWTs (audience
  `quantcore-keyproxy`); `api/auth.py` is **dual-mode** (ES256 per-user UI tokens via
  `QUANTCORE_JWT_PUBLIC_KEY` + legacy HS256 service/MCP tokens via `QUANTCORE_JWT_SECRET`).
- **Deploy wiring:** `Dockerfile.keyproxy`; compose service `keyproxy:5002` (ephemeral or
  persistent dev keypair via `runUI-CONTAINERS.sh`); `cloudbuild.yaml` `build-keyproxy`;
  `deploy.yml` deploys test `quantcore-keyproxy` (image + pinned sizing; skips if the service
  doesn't exist);
  `prod-rollout.yml` promotes/deploys it by digest the same way. First deploy in each project is
  the manual packet-8b runbook (secrets `keyproxy-private-key`, `quantui-signing-key`/`-pub`;
  private keys are piped straight into Secret Manager, never printed). Gitleaks secret-scanning
  job runs in CI (`.gitleaks.toml`).
