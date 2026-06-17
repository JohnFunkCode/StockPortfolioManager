"""Tests for the REST tier's JWT auth dependency (Phase 3 Step 6).

Exercises ``api.auth.require_principal`` against a minimal FastAPI app so the suite
needs no database (the full ``api.main.create_app`` runs ``init_schema()``). Covers the
AUTH_DISABLED bypass, valid/invalid/missing/expired tokens, and issuer/audience checks.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.auth import Principal, require_principal

SECRET = "test-secret-key-at-least-32-bytes-long-000"

_AUTH_ENV_KEYS = (
    "AUTH_DISABLED",
    "QUANTCORE_JWT_SECRET",
    "QUANTCORE_JWT_PUBLIC_KEY",
    "QUANTCORE_JWT_ALGORITHMS",
    "QUANTCORE_JWT_ISSUER",
    "QUANTCORE_JWT_AUDIENCE",
    "QUANTCORE_JWT_LEEWAY",
)


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    def protected(p: Principal = Depends(require_principal)) -> dict:
        return {
            "subject": p.subject,
            "owner": p.owner,
            "email": p.email,
            "roles": list(p.roles),
            "is_local": p.is_local,
        }

    return app


def _token(claims: dict, *, secret: str = SECRET, alg: str = "HS256") -> str:
    return jwt.encode(claims, secret, algorithm=alg)


class AuthDependencyTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _AUTH_ENV_KEYS}
        for k in _AUTH_ENV_KEYS:
            os.environ.pop(k, None)
        self.client = TestClient(_make_app(), raise_server_exceptions=False)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # --- AUTH_DISABLED bypass (local / compose parity) --------------------- #
    def test_disabled_injects_local_principal_without_token(self):
        os.environ["AUTH_DISABLED"] = "1"
        r = self.client.get("/protected")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["is_local"])
        self.assertEqual(body["owner"], "local")

    def test_disabled_ignores_supplied_token(self):
        os.environ["AUTH_DISABLED"] = "true"
        r = self.client.get("/protected", headers={"Authorization": "Bearer garbage"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["is_local"])

    # --- Enforced mode ----------------------------------------------------- #
    def test_valid_token_resolves_principal(self):
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        tok = _token({"sub": "john", "email": "john@example.com", "roles": ["trader"]})
        r = self.client.get("/protected", headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["is_local"])
        self.assertEqual(body["subject"], "john")
        self.assertEqual(body["owner"], "john")
        self.assertEqual(body["roles"], ["trader"])

    def test_missing_token_is_401(self):
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        r = self.client.get("/protected")
        self.assertEqual(r.status_code, 401)

    def test_bad_signature_is_401(self):
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        tok = _token({"sub": "john"}, secret="a-different-secret-also-32-bytes-long-0000")
        r = self.client.get("/protected", headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual(r.status_code, 401)

    def test_expired_token_is_401(self):
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
        tok = _token({"sub": "john", "exp": exp})
        r = self.client.get("/protected", headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual(r.status_code, 401)

    def test_issuer_mismatch_is_401(self):
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        os.environ["QUANTCORE_JWT_ISSUER"] = "https://expected-issuer"
        tok = _token({"sub": "john", "iss": "https://wrong-issuer"})
        r = self.client.get("/protected", headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual(r.status_code, 401)

    def test_audience_enforced_when_configured(self):
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        os.environ["QUANTCORE_JWT_AUDIENCE"] = "quantcore-api"
        good = _token({"sub": "john", "aud": "quantcore-api"})
        bad = _token({"sub": "john", "aud": "someone-else"})
        self.assertEqual(
            self.client.get(
                "/protected", headers={"Authorization": f"Bearer {good}"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/protected", headers={"Authorization": f"Bearer {bad}"}
            ).status_code,
            401,
        )

    def test_no_key_configured_means_auth_inactive(self):
        # No JWT key + AUTH_DISABLED unset → auth is inert (preserves the open local
        # contract); any/no token yields a local principal, not a rejection.
        r = self.client.get("/protected")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["is_local"])

    def test_explicit_disable_overrides_configured_key(self):
        # A key is present but AUTH_DISABLED forces auth off (compose belt-and-suspenders).
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        os.environ["AUTH_DISABLED"] = "1"
        r = self.client.get("/protected")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["is_local"])


if __name__ == "__main__":
    unittest.main()
