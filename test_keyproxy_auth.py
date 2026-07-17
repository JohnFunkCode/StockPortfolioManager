"""Packet 2a tests: keyproxy Bearer-JWT verification (keyproxy/auth.py).

Mirrors the api/auth.py semantics contract: inert until QUANTCORE_JWT_SECRET
is configured, KEYPROXY_AUTH_DISABLED forces auth off, and every rejection
is a uniform 401 whose detail never contains PyJWT error text or token
material (never-log policy).
"""

import time
import unittest
from unittest.mock import patch

import jwt
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from keyproxy import auth

SECRET = "unit-test-secret"


def creds(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def mint(claims, secret=SECRET, algorithm="HS256"):
    return jwt.encode(claims, secret, algorithm=algorithm)


class TestAuthInactive(unittest.TestCase):
    def test_no_secret_configured_returns_local(self):
        with patch.dict("os.environ", {}, clear=True):
            caller = auth.require_caller(None)
        self.assertTrue(caller.is_local)
        self.assertEqual(caller.sub, "local")

    def test_disabled_flag_overrides_configured_secret(self):
        env = {"QUANTCORE_JWT_SECRET": SECRET, "KEYPROXY_AUTH_DISABLED": "1"}
        with patch.dict("os.environ", env, clear=True):
            caller = auth.require_caller(None)
        self.assertTrue(caller.is_local)

    def test_api_auth_disabled_var_does_not_disable_keyproxy(self):
        # The keyproxy honors only its own override, not the api's.
        env = {"QUANTCORE_JWT_SECRET": SECRET, "AUTH_DISABLED": "1"}
        with patch.dict("os.environ", env, clear=True):
            with self.assertRaises(HTTPException):
                auth.require_caller(None)


class TestAuthActive(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(
            "os.environ", {"QUANTCORE_JWT_SECRET": SECRET}, clear=True
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

    def test_wrong_signature_rejected(self):
        wrong = "wrong-secret-of-recommended-hmac-length!"
        self.assert_uniform_401(creds(mint({"sub": "john"}, secret=wrong)))

    def test_expired_token_rejected(self):
        token = mint({"sub": "john", "exp": int(time.time()) - 3600})
        self.assert_uniform_401(creds(token))

    def test_unsigned_alg_none_rejected(self):
        header_payload = jwt.encode({"sub": "john"}, SECRET, algorithm="HS256")
        forged = header_payload.rsplit(".", 1)[0] + "."
        self.assert_uniform_401(creds(forged))

    def test_subless_token_rejected(self):
        self.assert_uniform_401(creds(mint({"role": "admin"})))

    def test_rejection_detail_never_leaks_jwt_error_text(self):
        exc = self.assert_uniform_401(creds("not-even-a-jwt"))
        detail = str(exc.detail).lower()
        for fragment in ("signature", "segment", "decode", "expired"):
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
