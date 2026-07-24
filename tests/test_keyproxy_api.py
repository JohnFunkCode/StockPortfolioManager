"""Integration tests for the /api/keyproxy routes + chat BYOK wiring (3c).

Runs the FastAPI app through TestClient with KEYPROXY_FAKE=1, so the real
routes, schemas, registry wiring, and KeyProxyService relay are exercised
against the canned in-process gateway — which uses a REAL generated P-256
keypair and the REAL envelope decrypt, with zero network. DB-safety preamble
mirrors test_chat_api.py.

Never-log policy: rejection paths assert that no key material, envelope
fields, or tokens reach any logger.
"""

import json
import logging
import os
import time
import unittest
import uuid
from pathlib import Path

# Registry precedence is read when get_services() first builds: the fake
# keyproxy gateway for the relay routes, the fake LLM for any chat turns.
os.environ["KEYPROXY_FAKE"] = "1"
os.environ["CHAT_FAKE"] = "1"
os.environ.pop("KEYPROXY_URL", None)

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from keyproxy import crypto  # noqa: E402
from quantcore.services.chat import ENVELOPE_REQUIRED_MESSAGE  # noqa: E402
from quantcore.services.registry import get_services  # noqa: E402
from api.main import create_app  # noqa: E402

SECRET = "test-secret-key-at-least-32-bytes-long-000"
API_KEY = "sk-ant-test-key-abcd"

_AUTH_ENV_KEYS = (
    "AUTH_DISABLED",
    "QUANTCORE_JWT_SECRET",
    "QUANTCORE_JWT_PUBLIC_KEY",
    "QUANTCORE_JWT_ALGORITHMS",
    "QUANTCORE_JWT_ISSUER",
    "QUANTCORE_JWT_AUDIENCE",
    "QUANTCORE_JWT_LEEWAY",
)


def validate_scope(provider: str = "anthropic") -> dict:
    return {
        "v": 1,
        "provider": provider,
        "action": "key.validate",
        "params": {},
        "budget": {"max_calls": 1, "max_mutations": 0, "max_tokens": 0, "ttl": 60},
    }


def mint_envelope(
    public_key,
    scope: dict,
    *,
    sub: str = "local",
    provider: str = "anthropic",
    api_key: str = API_KEY,
    iat: int | None = None,
    kid: str = "kp-fake-1",
) -> dict:
    aad = {
        "sub": sub,
        "provider": provider,
        "iat": int(time.time()) if iat is None else iat,
        "jti": uuid.uuid4().hex,
        "scope_hash": crypto.compute_scope_hash(scope),
    }
    return crypto.encrypt_envelope(api_key, public_key, kid=kid, aad=aad)


class _RecordingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class KeyProxyApiTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        get_services.cache_clear()  # pick up KEYPROXY_FAKE=1 / CHAT_FAKE=1
        cls.client = TestClient(create_app(), raise_server_exceptions=False)
        cls.fake_gateway = get_services().keyproxy._gateway

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _AUTH_ENV_KEYS}
        for k in _AUTH_ENV_KEYS:
            os.environ.pop(k, None)
        self._log_handler = _RecordingHandler()
        logging.getLogger().addHandler(self._log_handler)

    def tearDown(self):
        logging.getLogger().removeHandler(self._log_handler)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def assert_never_logged(self, *needles: str) -> None:
        for record in self._log_handler.records:
            text = record.getMessage()
            for needle in needles:
                self.assertNotIn(
                    needle, text, f"sensitive material reached a log: {record}"
                )

    def bearer(self, sub: str) -> dict:
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        token = jwt.encode({"sub": sub}, SECRET, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}


class TestPublickey(KeyProxyApiTestBase):
    def test_relays_keys_and_local_sub_when_auth_off(self):
        resp = self.client.get("/api/keyproxy/publickey")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["sub"], "local")
        self.assertEqual(len(body["keys"]), 1)
        key = body["keys"][0]
        self.assertEqual(key["kid"], "kp-fake-1")
        self.assertEqual(key["alg"], crypto.ENVELOPE_ALG)
        # spki round-trips to the fake's actual public key.
        spki = crypto.b64url_decode(key["spki"])
        self.assertEqual(
            spki, crypto.public_key_spki_der(self.fake_gateway.public_key)
        )

    def test_sub_is_the_jwt_subject_when_auth_on(self):
        headers = self.bearer("alice")
        resp = self.client.get("/api/keyproxy/publickey", headers=headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sub"], "alice")

    def test_401_without_token_when_auth_on(self):
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        resp = self.client.get("/api/keyproxy/publickey")
        self.assertEqual(resp.status_code, 401)


class TestValidate(KeyProxyApiTestBase):
    def post_validate(self, envelope: dict, scope: dict, headers: dict | None = None):
        return self.client.post(
            "/api/keyproxy/validate",
            json={"envelope": envelope, "scope": scope},
            headers=headers or {},
        )

    def test_happy_path_returns_key_hint(self):
        scope = validate_scope()
        envelope = mint_envelope(self.fake_gateway.public_key, scope)
        resp = self.post_validate(envelope, scope)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"valid": True, "provider": "anthropic", "key_hint": "…abcd"},
        )

    def test_happy_path_with_jwt_sub(self):
        scope = validate_scope()
        envelope = mint_envelope(self.fake_gateway.public_key, scope, sub="alice")
        resp = self.post_validate(envelope, scope, headers=self.bearer("alice"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["valid"])

    def test_sub_mismatch_is_safe_400_and_never_logged(self):
        scope = validate_scope()
        envelope = mint_envelope(self.fake_gateway.public_key, scope, sub="mallory")
        resp = self.post_validate(envelope, scope, headers=self.bearer("alice"))
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["message"]
        # Safe user-facing copy — names no values.
        self.assertNotIn("mallory", detail)
        self.assertNotIn("sub", detail)
        self.assert_never_logged(API_KEY, envelope["ct"], envelope["epk"], "mallory")

    def test_tampered_ciphertext_is_safe_400_and_never_logged(self):
        scope = validate_scope()
        envelope = mint_envelope(self.fake_gateway.public_key, scope)
        envelope["ct"] = crypto.b64url_encode(
            bytes(64)
        )  # garbage of plausible length
        resp = self.post_validate(envelope, scope)
        self.assertEqual(resp.status_code, 400)
        self.assert_never_logged(API_KEY, envelope["ct"], envelope["epk"])

    def test_malformed_body_is_422_and_never_logged(self):
        resp = self.client.post(
            "/api/keyproxy/validate", json={"envelope": {"v": 1}, "scope": {}}
        )
        self.assertEqual(resp.status_code, 422)
        self.assert_never_logged(API_KEY)


class TestChatEnvelopeRequired(unittest.TestCase):
    """With a keyproxy configured (KEYPROXY_URL) and no envelope on the send,
    the chat stream must open normally and carry exactly one error frame with
    the Settings prompt — the factory raises before any network I/O (the URL
    here is a black hole; a connection attempt would hang/fail loudly)."""

    @classmethod
    def setUpClass(cls):
        cls._saved_env = {
            k: os.environ.get(k) for k in ("CHAT_FAKE", "KEYPROXY_URL")
        }
        os.environ.pop("CHAT_FAKE", None)
        os.environ["KEYPROXY_URL"] = "http://keyproxy.invalid:5002"
        get_services.cache_clear()
        cls.client = TestClient(create_app(), raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_services.cache_clear()

    def test_missing_envelope_yields_settings_prompt_error_frame(self):
        resp = self.client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            resp.headers["content-type"].startswith("text/event-stream")
        )
        frames = [
            chunk
            for chunk in resp.text.split("\n\n")
            if chunk.strip() and not chunk.startswith(":")
        ]
        self.assertEqual(len(frames), 1)
        self.assertIn("event: error", frames[0])
        data = json.loads(frames[0].split("data: ", 1)[1])
        self.assertEqual(data["message"], ENVELOPE_REQUIRED_MESSAGE)


if __name__ == "__main__":
    unittest.main()
