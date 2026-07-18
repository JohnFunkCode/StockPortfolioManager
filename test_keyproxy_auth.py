"""Packet 7a tests: keyproxy Bearer-JWT verification (keyproxy/auth.py), ES256-only.

The keyproxy holds no signing material — it verifies against the ES256 public
key in QUANTCORE_JWT_PUBLIC_KEY and requires "quantcore-keyproxy" in ``aud``.
Auth is inert until that key is configured, KEYPROXY_AUTH_DISABLED forces auth
off, and every rejection is a uniform 401 whose detail never contains PyJWT
error text or token material (never-log policy). HS256 service tokens (the MCP
wrappers') must be rejected here, including the classic algorithm-confusion
probe: an HS256 token whose HMAC secret is the ES256 public PEM itself.
"""

import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch

import jwt
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from keyproxy import auth, crypto

_SIGNING_KEY = crypto.generate_private_key()
PRIVATE_PEM = crypto.private_key_to_pem(_SIGNING_KEY)
PUBLIC_PEM = crypto.public_key_to_pem(_SIGNING_KEY.public_key())
AUDIENCE = ["quantcore-api", "quantcore-keyproxy"]

HS_SECRET = "unit-test-service-token-secret-0123456789"


def creds(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def mint(claims, key=PRIVATE_PEM, algorithm="ES256", aud=AUDIENCE):
    if aud is not None:
        claims = {**claims, "aud": aud}
    return jwt.encode(claims, key, algorithm=algorithm)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def forge_hs256(claims, hmac_secret: str) -> str:
    """Hand-rolled HS256 token — PyJWT refuses to HMAC-sign with PEM-looking
    keys precisely because of the confusion attack, so the probe builds the
    token manually."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}"
    sig = hmac.new(
        hmac_secret.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url(sig)}"


class TestAuthInactive(unittest.TestCase):
    def test_no_public_key_configured_returns_local(self):
        with patch.dict("os.environ", {}, clear=True):
            caller = auth.require_caller(None)
        self.assertTrue(caller.is_local)
        self.assertEqual(caller.sub, "local")

    def test_legacy_hs_secret_alone_does_not_activate_auth(self):
        # Packet 7a: the keyproxy keys on the public key only — the HS
        # secret is api-side service-token material it must not honor.
        env = {"QUANTCORE_JWT_SECRET": HS_SECRET}
        with patch.dict("os.environ", env, clear=True):
            caller = auth.require_caller(None)
        self.assertTrue(caller.is_local)

    def test_disabled_flag_overrides_configured_key(self):
        env = {"QUANTCORE_JWT_PUBLIC_KEY": PUBLIC_PEM, "KEYPROXY_AUTH_DISABLED": "1"}
        with patch.dict("os.environ", env, clear=True):
            caller = auth.require_caller(None)
        self.assertTrue(caller.is_local)

    def test_api_auth_disabled_var_does_not_disable_keyproxy(self):
        # The keyproxy honors only its own override, not the api's.
        env = {"QUANTCORE_JWT_PUBLIC_KEY": PUBLIC_PEM, "AUTH_DISABLED": "1"}
        with patch.dict("os.environ", env, clear=True):
            with self.assertRaises(HTTPException):
                auth.require_caller(None)


class TestAuthActive(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(
            "os.environ", {"QUANTCORE_JWT_PUBLIC_KEY": PUBLIC_PEM}, clear=True
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_uniform_401(self, credentials):
        with self.assertRaises(HTTPException) as ctx:
            auth.require_caller(credentials)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "invalid or missing bearer token")
        return ctx.exception

    def test_valid_token_resolves_sub(self):
        caller = auth.require_caller(creds(mint({"sub": "john"})))
        self.assertEqual(caller.sub, "john")
        self.assertFalse(caller.is_local)
        self.assertEqual(caller.claims["sub"], "john")
        self.assertIsNotNone(caller.token)

    def test_email_fallback_matches_api_principal_semantics(self):
        caller = auth.require_caller(creds(mint({"email": "a@b.com"})))
        self.assertEqual(caller.sub, "a@b.com")

    def test_missing_token_rejected(self):
        self.assert_uniform_401(None)

    def test_wrong_audience_rejected(self):
        token = mint({"sub": "john"}, aud=["quantcore-api"])
        self.assert_uniform_401(creds(token))

    def test_missing_audience_rejected(self):
        self.assert_uniform_401(creds(mint({"sub": "john"}, aud=None)))

    def test_wrong_signing_key_rejected(self):
        other = crypto.private_key_to_pem(crypto.generate_private_key())
        self.assert_uniform_401(creds(mint({"sub": "john"}, key=other)))

    def test_hs256_service_token_rejected(self):
        # The MCP wrappers' shared-secret tokens authenticate to
        # quantcore-api only — never to the keyproxy.
        token = forge_hs256({"sub": "svc", "aud": AUDIENCE}, HS_SECRET)
        self.assert_uniform_401(creds(token))

    def test_algorithm_confusion_probe_rejected(self):
        # HS256 signed with the ES256 *public* PEM as the HMAC secret: a
        # verifier that feeds its public key into HMAC would accept this.
        token = forge_hs256({"sub": "mallory", "aud": AUDIENCE}, PUBLIC_PEM)
        self.assert_uniform_401(creds(token))

    def test_expired_token_rejected(self):
        token = mint({"sub": "john", "exp": int(time.time()) - 3600})
        self.assert_uniform_401(creds(token))

    def test_unsigned_alg_none_rejected(self):
        header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps({"sub": "john", "aud": AUDIENCE}).encode())
        self.assert_uniform_401(creds(f"{header}.{payload}."))

    def test_subless_token_rejected(self):
        self.assert_uniform_401(creds(mint({"role": "admin"})))

    def test_rejection_detail_never_leaks_jwt_error_text(self):
        exc = self.assert_uniform_401(creds("not-even-a-jwt"))
        detail = str(exc.detail).lower()
        for fragment in ("signature", "segment", "decode", "expired", "audience"):
            self.assertNotIn(fragment, detail)

    def test_rejections_are_unchained(self):
        # `from None` — the PyJWT exception (which embeds token detail) must
        # not ride along as __cause__/__context__ into logs.
        with self.assertRaises(HTTPException) as ctx:
            auth.require_caller(creds("garbage"))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)


if __name__ == "__main__":
    unittest.main()
