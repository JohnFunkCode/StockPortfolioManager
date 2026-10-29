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
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.auth import (
    AuthConfigurationError,
    Principal,
    assert_auth_enforced_on_cloud_run,
    require_owner,
    require_principal,
    resolve_owner_or_none,
)
from quantcore.services.identity import UnknownIdentityError

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


class ClaimShapeTests(unittest.TestCase):
    """``Principal.from_claims`` accepts a string or a list of strings, nothing else (#472)."""

    def test_accepted_shapes(self):
        cases = [
            ({"roles": "admin"}, ("admin",), ()),
            ({"roles": ["a", "b"]}, ("a", "b"), ()),
            ({"role": "trader"}, ("trader",), ()),
            ({"roles": [], "role": "trader"}, ("trader",), ()),
            ({"scope": "read write"}, (), ("read", "write")),
            ({"scopes": ["read", "write"]}, (), ("read", "write")),
            ({"scope": "", "scopes": ["read"]}, (), ("read",)),
            ({}, (), ()),
        ]
        for claims, roles, scopes in cases:
            with self.subTest(claims):
                p = Principal.from_claims({"sub": "x", **claims}, None)
                self.assertEqual((p.roles, p.scopes), (roles, scopes))

    def test_malformed_shapes_raise_invalid_token(self):
        for claims in ({"roles": 42}, {"roles": 0}, {"role": True}, {"scope": 7},
                       {"scopes": {}}, {"roles": [1, 2]}, {"scope": ["a", None]}):
            with self.subTest(claims):
                with self.assertRaises(jwt.InvalidTokenError):
                    Principal.from_claims({"sub": "x", **claims}, None)


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


class CloudRunFailClosedTests(unittest.TestCase):
    """Issue #459: a Cloud Run service must never start with auth inert.

    ``K_SERVICE`` is what Cloud Run sets on a service; without it the open local
    contract above is unchanged.
    """

    def setUp(self):
        keys = _AUTH_ENV_KEYS + ("K_SERVICE",)
        self._saved = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_local_without_key_is_allowed(self):
        assert_auth_enforced_on_cloud_run()  # no K_SERVICE: compose/dev stays open

    def test_local_with_auth_disabled_is_allowed(self):
        os.environ["AUTH_DISABLED"] = "1"
        assert_auth_enforced_on_cloud_run()

    def test_cloud_run_without_any_key_refuses(self):
        os.environ["K_SERVICE"] = "quantcore-api"
        with self.assertLogs("quantcore.security", level="ERROR") as logs, \
                self.assertRaises(AuthConfigurationError) as ctx:
            assert_auth_enforced_on_cloud_run()
        self.assertIn("QUANTCORE_JWT_PUBLIC_KEY", str(ctx.exception))
        self.assertIn("quantcore-api", logs.output[0])

    def test_cloud_run_with_auth_disabled_refuses_even_with_a_key(self):
        os.environ["K_SERVICE"] = "quantcore-api"
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        os.environ["AUTH_DISABLED"] = "true"
        with self.assertLogs("quantcore.security", level="ERROR"), \
                self.assertRaises(AuthConfigurationError) as ctx:
            assert_auth_enforced_on_cloud_run()
        self.assertIn("AUTH_DISABLED", str(ctx.exception))

    def test_cloud_run_with_either_key_starts(self):
        os.environ["K_SERVICE"] = "quantcore-api"
        for name, value in (("QUANTCORE_JWT_SECRET", SECRET),
                            ("QUANTCORE_JWT_PUBLIC_KEY", EC_PUBLIC_PEM)):
            with self.subTest(name):
                os.environ[name] = value
                assert_auth_enforced_on_cloud_run()
                os.environ.pop(name)

    def test_refusal_never_carries_the_key(self):
        os.environ["K_SERVICE"] = "quantcore-api"
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        os.environ["AUTH_DISABLED"] = "1"
        with self.assertLogs("quantcore.security", level="ERROR") as logs, \
                self.assertRaises(AuthConfigurationError) as ctx:
            assert_auth_enforced_on_cloud_run()
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(SECRET, "\n".join(logs.output))

    def test_create_app_refuses_before_touching_the_database(self):
        with patch("quantcore.db.ensure_schema") as ensure_schema:
            # Imported under the patch: api.main builds its app at module level.
            from api.main import create_app

            ensure_schema.reset_mock()
            os.environ["K_SERVICE"] = "quantcore-api"
            with self.assertLogs("quantcore.security", level="ERROR"), \
                    self.assertRaises(AuthConfigurationError):
                create_app()
            ensure_schema.assert_not_called()


class SecurityEventLogTests(unittest.TestCase):
    """Every rejection logs one ``SECURITY`` line on ``quantcore.security`` (#464, G6).

    The log-based metrics in the G6 alert match these exact words, so the format is
    part of the contract. No line may carry the token: it is a live credential (or,
    expired, a replayable one), and the never-log policy covers it either way.
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

    def _rejected(self, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with self.assertLogs("quantcore.security", level="WARNING") as logs:
            r = self.client.get("/protected", headers=headers)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(len(logs.records), 1)
        out = logs.records[0].getMessage()
        if token:
            self.assertNotIn(token, out)
        return out

    def test_missing_token(self):
        self.assertEqual(self._rejected(), "SECURITY auth_rejected reason=missing")

    def test_bad_signature_is_invalid_and_names_no_one(self):
        tok = _token({"sub": "mallory"}, secret="a-different-secret-also-32-bytes-long-0000")
        out = self._rejected(tok)
        self.assertEqual(out, "SECURITY auth_rejected reason=invalid")
        self.assertNotIn("mallory", out)

    def test_garbage_token_is_invalid(self):
        self.assertEqual(
            self._rejected("not-a-jwt"), "SECURITY auth_rejected reason=invalid"
        )

    def test_expired_service_token_names_its_holder(self):
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
        out = self._rejected(_token({"sub": "sager", "exp": exp}))
        self.assertEqual(out, "SECURITY auth_rejected reason=expired identity=sager")

    def test_expired_user_token_names_its_holder(self):
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
        out = self._rejected(_user_token({"sub": "thomas@example.com", "exp": exp}))
        self.assertEqual(
            out, "SECURITY auth_rejected reason=expired identity=thomas@example.com"
        )

    def test_expired_token_with_bad_signature_is_invalid(self):
        # PyJWT checks the signature before exp, so a forged expired token is
        # "invalid", and its sub never reaches the log.
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
        tok = _token(
            {"sub": "mallory", "exp": exp},
            secret="a-different-secret-also-32-bytes-long-0000",
        )
        self.assertEqual(self._rejected(tok), "SECURITY auth_rejected reason=invalid")

    def test_expired_token_for_another_audience_names_no_one(self):
        # PyJWT raises ExpiredSignatureError before checking aud, so expiry alone
        # doesn't make the sub ours; the exp-skipping re-verification rejects it.
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
        tok = _user_token({"sub": "thomas@example.com", "exp": exp}, aud="other-service")
        out = self._rejected(tok)
        self.assertEqual(out, "SECURITY auth_rejected reason=expired identity=unknown")

    def test_expired_token_from_another_issuer_names_no_one(self):
        os.environ["QUANTCORE_JWT_ISSUER"] = "https://expected-issuer"
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
        tok = _token({"sub": "sager", "exp": exp, "iss": "https://other-issuer"})
        out = self._rejected(tok)
        self.assertEqual(out, "SECURITY auth_rejected reason=expired identity=unknown")

    def test_expired_token_with_non_string_subject_names_no_one(self):
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
        out = self._rejected(_token({"sub": ["a", "b"], "exp": exp}))
        self.assertEqual(out, "SECURITY auth_rejected reason=expired identity=unknown")

    def test_expired_token_with_malformed_optional_claims_is_still_401(self):
        # roles/scope aren't PyJWT-validated; reading them must not turn the 401
        # into a 500 (review of #468).
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
        for claim in ({"roles": 42}, {"scope": 42}):
            with self.subTest(claim):
                out = self._rejected(_token({"sub": "holder", "exp": exp, **claim}))
                self.assertEqual(
                    out, "SECURITY auth_rejected reason=expired identity=holder"
                )

    def test_valid_token_with_malformed_roles_or_scope_is_invalid(self):
        # A token we signed whose roles/scope isn't a string or a list of strings
        # was a 500 that no alert counted (#472); now it is a logged 401.
        for claim in ({"roles": 42}, {"role": True}, {"scope": 7},
                      {"scopes": {"a": 1}}, {"roles": {"a": 1}}, {"roles": [1, 2]},
                      {"scope": ["read", None]}):
            with self.subTest(claim):
                tok = _token({"sub": "holder", **claim})
                out = self._rejected(tok)
                self.assertEqual(out, "SECURITY auth_rejected reason=invalid")
                r = self.client.get("/protected", headers={"Authorization": f"Bearer {tok}"})
                self.assertEqual(r.headers.get("WWW-Authenticate"), "Bearer")
                self.assertEqual(r.json()["detail"], "invalid or expired token")

    def test_accepted_token_logs_nothing(self):
        with self.assertNoLogs("quantcore.security", level="WARNING"):
            r = self.client.get(
                "/protected", headers={"Authorization": f"Bearer {_token({'sub': 'john'})}"}
            )
        self.assertEqual(r.status_code, 200)


class UnmappedIdentityLogTests(unittest.TestCase):
    """An identity with no ``owner_identities`` row logs a security event (#464).

    Hard (``require_owner``, 403) and soft (``resolve_owner_or_none``, Sidekick)
    paths log under distinct names so monitoring can tell them apart.
    """

    def setUp(self):
        self.token = _token({"sub": "stranger@example.com"})
        self.principal = Principal.from_claims(
            {"sub": "stranger@example.com"}, self.token
        )
        services = MagicMock()
        services.identity.resolve_owner.side_effect = UnknownIdentityError("unmapped")
        patcher = patch("api.auth.get_services", return_value=services)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_require_owner_logs_and_403s(self):
        with self.assertLogs("quantcore.security", level="WARNING") as logs, \
                self.assertRaises(HTTPException) as ctx:
            require_owner(self.principal)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "not_provisioned")
        out = logs.records[0].getMessage()
        self.assertTrue(
            out.startswith("SECURITY unmapped_principal identity=stranger@example.com at="),
            out,
        )
        self.assertNotIn(self.token, "\n".join(logs.output))

    def test_soft_path_logs_distinct_event_and_returns_none(self):
        with self.assertLogs("quantcore.security", level="WARNING") as logs:
            self.assertIsNone(resolve_owner_or_none(self.principal))
        out = logs.records[0].getMessage()
        self.assertTrue(
            out.startswith(
                "SECURITY unmapped_principal_soft identity=stranger@example.com at="
            ),
            out,
        )
        self.assertNotIn(self.token, "\n".join(logs.output))

    def test_local_principal_never_logs(self):
        with self.assertNoLogs("quantcore.security", level="WARNING"):
            self.assertEqual(require_owner(Principal.local()), "john")
            self.assertEqual(resolve_owner_or_none(Principal.local()), "john")


if __name__ == "__main__":
    unittest.main()
