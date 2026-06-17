#!/usr/bin/env python3
"""Mint a short-lived prod JWT for the JWT-enforced Cloud Run API.

The prod ``quantcore-api`` enforces an app-level HS256 JWT (see ``api/auth.py``):
any caller — the React UI, the CLI smoke, or an MCP client — must present a
``Authorization: Bearer <token>`` signed with the prod secret ``quantcore-jwt-secret``
(stored in Secret Manager on ``quantcore-prod-20260606``). Tokens are short-lived;
when one expires you simply mint another with this script. Nothing is revoked or
stored server-side — the secret is the only long-lived credential.

The signing secret is NEVER printed. By default it is fetched straight from Secret
Manager (you must be ``gcloud auth login``'d with access to the secret); pass
``--secret-source env`` to sign with ``QUANTCORE_JWT_SECRET`` from the environment
instead (e.g. in CI, or when you've injected it yourself).

Output modes (``--output``):
  * ``token``         print just the token to stdout (default; capture it into a var).
  * ``export``        print ``export QUANTCORE_MCP_TOKEN=<token>`` to eval/source.
  * ``frontend-env``  write ``frontend/.env.local`` so ``npm run dev`` talks to prod
                      (proxy target + bearer token); the token is written to that
                      gitignored file only, never to stdout.

Tokens default to a 24h lifetime (after-hours work without re-minting); pass
``--expires-hours N`` to shorten for a one-off call.

Examples:
  # Mint a token and use it in a curl
  TOKEN="$(python scripts/mint_prod_jwt.py)"
  curl -H "Authorization: Bearer $TOKEN" \\
    https://quantcore-api-127961694257.us-central1.run.app/api/portfolio?owner=john

  # Point the React UI at prod for a working session (8h token)
  python scripts/mint_prod_jwt.py --output frontend-env --expires-hours 8
  cd frontend && npm run dev

  # Sign with a secret you already exported (no gcloud call)
  export QUANTCORE_JWT_SECRET=...   # not echoed by this script
  python scripts/mint_prod_jwt.py --secret-source env
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import jwt

ROOT = Path(__file__).resolve().parents[1]

# Prod Cloud Run API (the vite dev proxy / clients target this).
PROD_API_URL = "https://quantcore-api-127961694257.us-central1.run.app"
DEFAULT_PROJECT = "quantcore-prod-20260606"
DEFAULT_SECRET_NAME = "quantcore-jwt-secret"
FRONTEND_ENV = ROOT / "frontend" / ".env.local"


def _secret_from_secret_manager(secret_name: str, project: str) -> str:
    """Fetch the signing secret via gcloud. Captured, never printed."""
    try:
        out = subprocess.run(
            [
                "gcloud", "secrets", "versions", "access", "latest",
                f"--secret={secret_name}", f"--project={project}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        sys.exit("ERROR: `gcloud` not found on PATH. Install the Cloud SDK or use --secret-source env.")
    except subprocess.CalledProcessError as e:
        # stderr may carry an auth/permission hint; it does NOT contain the secret.
        sys.exit(f"ERROR: could not read secret '{secret_name}' from {project}:\n{e.stderr.strip()}")
    secret = out.stdout.strip()
    if not secret:
        sys.exit(f"ERROR: secret '{secret_name}' is empty.")
    return secret


def _resolve_secret(args: argparse.Namespace) -> str:
    if args.secret_source == "env":
        secret = os.environ.get("QUANTCORE_JWT_SECRET", "").strip()
        if not secret:
            sys.exit("ERROR: --secret-source env but QUANTCORE_JWT_SECRET is not set.")
        return secret
    return _secret_from_secret_manager(args.secret_name, args.project)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--sub", default="john", help="Token subject / owner (default: john).")
    p.add_argument("--expires-hours", type=float, default=24.0,
                   help="Token lifetime in hours (default: 24).")
    p.add_argument("--secret-source", choices=("secret-manager", "env"),
                   default="secret-manager",
                   help="Where to read the signing secret (default: secret-manager).")
    p.add_argument("--secret-name", default=DEFAULT_SECRET_NAME,
                   help=f"Secret Manager secret name (default: {DEFAULT_SECRET_NAME}).")
    p.add_argument("--project", default=DEFAULT_PROJECT,
                   help=f"GCP project holding the secret (default: {DEFAULT_PROJECT}).")
    p.add_argument("--output", choices=("token", "export", "frontend-env"),
                   default="token", help="What to emit (default: token).")
    args = p.parse_args()

    secret = _resolve_secret(args)
    now = dt.datetime.now(tz=dt.timezone.utc)
    exp = now + dt.timedelta(hours=args.expires_hours)
    token = jwt.encode({"sub": args.sub, "iat": now, "exp": exp}, secret, algorithm="HS256")

    if args.output == "token":
        print(token)
    elif args.output == "export":
        print(f"export QUANTCORE_MCP_TOKEN={token}")
    else:  # frontend-env
        FRONTEND_ENV.write_text(
            "# Generated by scripts/mint_prod_jwt.py for prod UI testing.\n"
            "# Gitignored; contains a short-lived bearer token.\n"
            f"# Subject={args.sub}, expires {exp.isoformat()}.\n"
            f"VITE_API_PROXY_TARGET={PROD_API_URL}\n"
            f"VITE_API_TOKEN={token}\n"
        )
        print(f"Wrote {FRONTEND_ENV.relative_to(ROOT)} (proxy -> prod api).")
        print(f"Subject={args.sub}, expires {exp.isoformat()}. Token NOT printed.")
        print("Now run: cd frontend && npm run dev")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
