"""Tests for the REST tier's JWT auth dependency (Phase 3 Step 6).

Exercises ``api.auth.require_principal`` against a minimal FastAPI app so the suite
needs no database (the full ``api.main.create_app`` runs ``init_schema()``). Covers the
AUTH_DISABLED bypass, valid/invalid/missing/expired tokens, and issuer/audience checks,
plus the packet-7a dual mode: ES256 user tokens (public key, own name in ``aud``)
alongside the HS256 service-token path, with the algorithm-confusion probe rejected.
"""

import base64
import hashlib
import hmac
import json
import os
import unittest
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.auth import Principal, require_principal

SECRET = "test-secret-key-at-least-32-bytes-long-000"

_EC_KEY = ec.generate_private_key(ec.SECP256R1())
EC_PRIVATE_PEM = _EC_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
EC_PUBLIC_PEM = _EC_KEY.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()
USER_AUDIENCE = ["quantcore-api", "quantcore-keyproxy"]

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


def _user_token(claims: dict, *, key: str = EC_PRIVATE_PEM, aud=USER_AUDIENCE) -> str:
    if aud is not None:
        claims = {**claims, "aud": aud}
    return jwt.encode(claims, key, algorithm="ES256")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _forge_hs256(claims: dict, hmac_secret: str) -> str:
    """Hand-rolled HS256 token — PyJWT refuses to HMAC-sign with PEM-looking
    keys precisely because of the confusion attack, so build it manually."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}"
    sig = hmac.new(
        hmac_secret.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url(sig)}"


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


class DualModeES256Tests(unittest.TestCase):
    """Packet 7a: ES256 user tokens verified alongside HS256 service tokens.

    Cloud config after Phase 7: BOTH keys are set — the public key for the
    quantui-minted user tokens, the shared secret for the MCP wrappers'
    long-lived service tokens. Routing is by the token's declared ``alg``,
    each branch pinned to its own key and algorithm family.
    """

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _AUTH_ENV_KEYS}
        for k in _AUTH_ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        os.environ["QUANTCORE_JWT_PUBLIC_KEY"] = EC_PUBLIC_PEM
        self.client = TestClient(_make_app(), raise_server_exceptions=False)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _get(self, token: str):
        return self.client.get(
            "/protected", headers={"Authorization": f"Bearer {token}"}
        )

    def test_valid_es256_user_token_resolves_sub(self):
        r = self._get(_user_token({"sub": "john@funkyinnovations.com"}))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["subject"], "john@funkyinnovations.com")
        self.assertFalse(body["is_local"])

    def test_es256_wrong_audience_rejected(self):
        # A token minted for the keyproxy only must not authenticate here.
        r = self._get(_user_token({"sub": "john"}, aud=["quantcore-keyproxy"]))
        self.assertEqual(r.status_code, 401)

    def test_es256_missing_audience_rejected(self):
        r = self._get(_user_token({"sub": "john"}, aud=None))
        self.assertEqual(r.status_code, 401)

    def test_es256_wrong_key_rejected(self):
        other = ec.generate_private_key(ec.SECP256R1())
        other_pem = other.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        r = self._get(_user_token({"sub": "john"}, key=other_pem))
        self.assertEqual(r.status_code, 401)

    def test_es256_expired_rejected(self):
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
        r = self._get(_user_token({"sub": "john", "exp": exp}))
        self.assertEqual(r.status_code, 401)

    def test_hs256_service_token_still_accepted(self):
        # The MCP wrappers' long-lived tokens keep working unchanged.
        r = self._get(_token({"sub": "svc-wrapper"}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["subject"], "svc-wrapper")

    def test_algorithm_confusion_probe_rejected(self):
        # HS256 signed with the ES256 *public* PEM as the HMAC secret — a
        # verifier that routes the public key into HMAC would accept this.
        probe = _forge_hs256({"sub": "mallory", "aud": "quantcore-api"}, EC_PUBLIC_PEM)
        self.assertEqual(self._get(probe).status_code, 401)

    def test_es256_token_rejected_when_no_public_key_configured(self):
        os.environ.pop("QUANTCORE_JWT_PUBLIC_KEY")
        r = self._get(_user_token({"sub": "john"}))
        self.assertEqual(r.status_code, 401)

    def test_hs256_rejected_when_only_public_key_configured(self):
        # Public-key-only deployment: no secret means no HS path at all —
        # the public key must never be used for HMAC verification.
        os.environ.pop("QUANTCORE_JWT_SECRET")
        r = self._get(_token({"sub": "svc-wrapper"}))
        self.assertEqual(r.status_code, 401)

    def test_configured_asymmetric_algorithms_do_not_reach_hs_path(self):
        # Even if QUANTCORE_JWT_ALGORITHMS lists non-HS algorithms, the
        # service-token branch stays HS-only (confusion-attack hygiene).
        os.environ["QUANTCORE_JWT_ALGORITHMS"] = "HS256,ES256,RS256"
        self.assertEqual(self._get(_token({"sub": "svc"})).status_code, 200)
        probe = _forge_hs256({"sub": "mallory", "aud": "quantcore-api"}, EC_PUBLIC_PEM)
        self.assertEqual(self._get(probe).status_code, 401)


if __name__ == "__main__":
    unittest.main()
