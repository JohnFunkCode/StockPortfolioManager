"""Packet 2b tests: the Key Proxy FastAPI service (keyproxy/main.py).

TestClient tests over the assembled app with real envelope crypto round
trips: redemption happy path, jti burning (second redemption 400), stale
``iat``/sub-mismatch/scope-hash-mismatch rejections, wrong-sub session use,
unclassifiable operations, the per-sub 429, and the never-log assertion —
no envelope, key, or bearer material may reach any log record on either
success or failure paths.
"""

import functools
import importlib.util
import logging
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.serialization import load_der_public_key
from fastapi.testclient import TestClient

from keyproxy import crypto
from keyproxy.main import DEV_KID, create_app
from keyproxy.scopes import TOKEN_BUDGET_MESSAGE
from keyproxy.sessions import SessionError

# Packet 7a: the keyproxy verifies ES256 only — the harness mints user tokens
# with a module-level EC keypair and patches the public half into the env.
_JWT_SIGNING_KEY = crypto.generate_private_key()
JWT_SIGNING_PEM = crypto.private_key_to_pem(_JWT_SIGNING_KEY)
JWT_PUBLIC_PEM = crypto.public_key_to_pem(_JWT_SIGNING_KEY.public_key())
JWT_AUDIENCE = ["quantcore-api", "quantcore-keyproxy"]
KID = "kp-test-1"
API_KEY = "sk-ant-test-key-abcd"
GENERIC = "invalid request"


def chat_scope(**overrides):
    scope = {
        "v": 1,
        "provider": "anthropic",
        "action": "chat.turn",
        "params": {},
        "budget": {"max_calls": 20, "max_mutations": 0, "max_tokens": 250_000, "ttl": 300},
    }
    scope.update(overrides)
    return scope


def render_bundle(kid, private_key):
    """The KEYPROXY_PRIVATE_KEYS format: kid line + PEMs (public PEM must be ignored)."""
    return "\n".join(
        [
            f"kid: {kid}",
            "",
            crypto.public_key_to_pem(private_key.public_key()).rstrip(),
            "",
            crypto.private_key_to_pem(private_key).rstrip(),
            "",
        ]
    )


@functools.lru_cache(maxsize=None)
def mint_user_token(sub):
    # Memoized: ECDSA signatures are randomized per signing, but the
    # never-log assertions grep logs for the exact token a request carried,
    # so the same sub must yield the same token within a run.
    return jwt.encode({"sub": sub, "aud": JWT_AUDIENCE}, JWT_SIGNING_PEM, algorithm="ES256")


def bearer(sub):
    return {"Authorization": f"Bearer {mint_user_token(sub)}"}


class _RecordingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class KeyProxyServiceTestBase(unittest.TestCase):
    extra_env = {}

    def setUp(self):
        self.private_key = crypto.generate_private_key()
        env = {
            "KEYPROXY_PRIVATE_KEYS": render_bundle(KID, self.private_key),
            "QUANTCORE_JWT_PUBLIC_KEY": JWT_PUBLIC_PEM,
            **self.extra_env,
        }
        env_patcher = patch.dict("os.environ", env, clear=True)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        self.app = create_app()
        self.state = self.app.state.keyproxy
        self.client = TestClient(self.app, raise_server_exceptions=True)

        # Capture every log record the process emits during the test.
        self.log_handler = _RecordingHandler()
        root = logging.getLogger()
        self._old_root_level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(self.log_handler)
        self.addCleanup(root.removeHandler, self.log_handler)
        self.addCleanup(root.setLevel, self._old_root_level)

    def logged_messages(self):
        return [record.getMessage() for record in self.log_handler.records]

    def assert_never_logged(self, *needles):
        for message in self.logged_messages():
            for needle in needles:
                self.assertNotIn(needle, message)

    def fetch_public_key(self, expect_kid=KID):
        response = self.client.get("/v1/publickey")
        self.assertEqual(response.status_code, 200)
        keys = response.json()["keys"]
        entry = next(k for k in keys if k["kid"] == expect_kid)
        self.assertEqual(entry["alg"], crypto.ENVELOPE_ALG)
        return load_der_public_key(crypto.b64url_decode(entry["spki"]))

    def mint_envelope(
        self, scope, *, sub="alice", provider="anthropic", api_key=API_KEY,
        iat=None, kid=KID, public_key=None,
    ):
        aad = {
            "sub": sub,
            "provider": provider,
            "iat": int(time.time()) if iat is None else iat,
            "jti": str(uuid.uuid4()),
            "scope_hash": crypto.compute_scope_hash(scope),
        }
        return crypto.encrypt_envelope(
            api_key, public_key or self.fetch_public_key(), kid=kid, aad=aad
        )

    def redeem(self, envelope, scope, *, sub="alice", provider="anthropic"):
        return self.client.post(
            "/v1/sessions",
            json={"provider": provider, "envelope": envelope, "scope": scope},
            headers=bearer(sub),
        )


class TestHealthAndKeys(KeyProxyServiceTestBase):
    def test_healthz_open(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_publickey_open_and_importable(self):
        public_key = self.fetch_public_key()
        self.assertEqual(
            crypto.spki_fingerprint(public_key),
            crypto.spki_fingerprint(self.private_key.public_key()),
        )

    def test_keypair_script_output_is_a_valid_bundle(self):
        # The runbook pipes the generate script's stdout straight into the
        # secret — pin that the parser accepts the script's exact format.
        script = Path(__file__).parent.parent / "scripts" / "generate_keyproxy_keypair.py"
        spec = importlib.util.spec_from_file_location("gen_keypair", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.dict(
            "os.environ",
            {"KEYPROXY_PRIVATE_KEYS": module.render(kid="kp-script-1")},
        ):
            app = create_app()
        keys = app.state.keyproxy.public_keys_newest_first()
        self.assertEqual([k["kid"] for k in keys], ["kp-script-1"])


class TestSessionRedemption(KeyProxyServiceTestBase):
    def test_happy_path_real_crypto_round_trip(self):
        scope = chat_scope()
        response = self.redeem(self.mint_envelope(scope), scope)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["session_id"]), 32)
        self.assertGreater(body["expires_at"], time.time())
        session = self.state.sessions.get(body["session_id"], sub="alice")
        self.assertEqual(session.api_key, API_KEY)
        self.assertEqual(session.provider, "anthropic")

    def test_second_redemption_is_a_replay(self):
        scope = chat_scope()
        envelope = self.mint_envelope(scope)
        self.assertEqual(self.redeem(envelope, scope).status_code, 200)
        replay = self.redeem(envelope, scope)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["detail"], GENERIC)

    def test_missing_bearer_is_401(self):
        scope = chat_scope()
        response = self.client.post(
            "/v1/sessions",
            json={"provider": "anthropic", "envelope": self.mint_envelope(scope), "scope": scope},
        )
        self.assertEqual(response.status_code, 401)

    def test_stale_iat_rejected(self):
        scope = chat_scope()
        envelope = self.mint_envelope(scope, iat=int(time.time()) - 3_600)
        response = self.redeem(envelope, scope)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], GENERIC)

    def test_sub_mismatch_rejected(self):
        scope = chat_scope()
        envelope = self.mint_envelope(scope, sub="bob")  # redeemed by alice
        self.assertEqual(self.redeem(envelope, scope).status_code, 400)

    def test_scope_hash_mismatch_rejected(self):
        minted_scope = chat_scope()
        envelope = self.mint_envelope(minted_scope)
        widened = chat_scope(
            budget={"max_calls": 999, "max_mutations": 0, "max_tokens": 250_000, "ttl": 300}
        )
        response = self.redeem(envelope, widened)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], GENERIC)

    def test_unknown_provider_rejected(self):
        scope = chat_scope(provider="openai")
        envelope = {"anything": "at all"}
        response = self.redeem(envelope, scope, provider="openai")
        self.assertEqual(response.status_code, 400)

    def test_unclassifiable_action_rejected(self):
        scope = chat_scope(action="orders.place")
        response = self.redeem(self.mint_envelope(scope), scope)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], GENERIC)

    def test_mutation_budget_rejected_for_anthropic(self):
        scope = chat_scope(
            budget={"max_calls": 20, "max_mutations": 1, "max_tokens": 250_000, "ttl": 300}
        )
        response = self.redeem(self.mint_envelope(scope), scope)
        self.assertEqual(response.status_code, 400)

    def test_token_budget_over_ceiling_names_system_threshold(self):
        scope = chat_scope(
            budget={"max_calls": 20, "max_mutations": 0, "max_tokens": 250_001, "ttl": 300}
        )
        response = self.redeem({}, scope)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], TOKEN_BUDGET_MESSAGE)

    def test_malformed_body_is_generic_400_not_echoing_422(self):
        response = self.client.post(
            "/v1/sessions", json={"provider": "anthropic"}, headers=bearer("alice")
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": GENERIC})


class TestSessionTeardown(KeyProxyServiceTestBase):
    def create_session(self):
        scope = chat_scope()
        response = self.redeem(self.mint_envelope(scope), scope)
        self.assertEqual(response.status_code, 200)
        return response.json()["session_id"]

    def test_wrong_sub_delete_is_204_but_tears_nothing_down(self):
        session_id = self.create_session()
        response = self.client.delete(
            f"/v1/sessions/{session_id}", headers=bearer("bob")
        )
        self.assertEqual(response.status_code, 204)
        # Alice's session survives an interloper's delete.
        self.state.sessions.get(session_id, sub="alice")

    def test_owner_delete_tears_down(self):
        session_id = self.create_session()
        response = self.client.delete(
            f"/v1/sessions/{session_id}", headers=bearer("alice")
        )
        self.assertEqual(response.status_code, 204)
        with self.assertRaises(SessionError):
            self.state.sessions.get(session_id, sub="alice")

    def test_unknown_session_delete_is_still_204(self):
        response = self.client.delete(
            "/v1/sessions/deadbeefdeadbeefdeadbeefdeadbeef", headers=bearer("alice")
        )
        self.assertEqual(response.status_code, 204)


class TestRateLimit(KeyProxyServiceTestBase):
    extra_env = {"KEYPROXY_RATE_LIMIT_PER_MIN": "2"}

    def test_third_redemption_in_a_minute_is_429(self):
        public_key = self.fetch_public_key()
        for _ in range(2):
            scope = chat_scope()
            envelope = self.mint_envelope(scope, public_key=public_key)
            self.assertEqual(self.redeem(envelope, scope).status_code, 200)
        scope = chat_scope()
        envelope = self.mint_envelope(scope, public_key=public_key)
        response = self.redeem(envelope, scope)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["detail"], "rate limit exceeded")

    def test_other_subs_have_their_own_bucket(self):
        public_key = self.fetch_public_key()
        for _ in range(2):
            scope = chat_scope()
            envelope = self.mint_envelope(scope, public_key=public_key)
            self.redeem(envelope, scope)
        scope = chat_scope()
        envelope = self.mint_envelope(scope, sub="bob", public_key=public_key)
        self.assertEqual(self.redeem(envelope, scope, sub="bob").status_code, 200)


class TestKeyValidation(KeyProxyServiceTestBase):
    def validate(self, scope, envelope):
        return self.client.post(
            "/v1/keys/validate",
            json={"provider": "anthropic", "envelope": envelope, "scope": scope},
            headers=bearer("alice"),
        )

    def test_validate_probes_and_tears_down_immediately(self):
        scope = chat_scope(action="key.validate")
        envelope = self.mint_envelope(scope)
        with patch(
            "keyproxy.providers.anthropic.validate_key", return_value=True
        ) as probe:
            response = self.validate(scope, envelope)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"valid": True, "provider": "anthropic", "key_hint": "…abcd"},
        )
        probe.assert_called_once_with(API_KEY)
        # Immediate teardown: no session persists, and the jti is burned.
        self.assertEqual(self.state.sessions._sessions, {})
        self.assertEqual(self.validate(scope, envelope).status_code, 400)

    def test_invalid_key_reports_valid_false(self):
        scope = chat_scope(action="key.validate")
        envelope = self.mint_envelope(scope)
        with patch("keyproxy.providers.anthropic.validate_key", return_value=False):
            response = self.validate(scope, envelope)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["valid"])

    def test_validate_requires_the_validate_action(self):
        scope = chat_scope()  # chat.turn
        response = self.validate(scope, self.mint_envelope(scope))
        self.assertEqual(response.status_code, 400)


class TestNeverLogs(KeyProxyServiceTestBase):
    """The plan's logging policy, enforced: no envelope/key/bearer material
    in any log record, on success or on any failure path."""

    def sensitive(self, envelope):
        token = bearer("alice")["Authorization"]
        return [API_KEY, envelope["ct"], envelope["epk"], token.split(" ", 1)[1]]

    def test_success_and_failure_paths_log_no_material(self):
        scope = chat_scope()
        envelope = self.mint_envelope(scope)

        # Success path (logs the allowlist line only).
        self.assertEqual(self.redeem(envelope, scope).status_code, 200)
        # Replay failure.
        self.assertEqual(self.redeem(envelope, scope).status_code, 400)
        # GCM tamper failure.
        tampered = dict(envelope)
        tampered["ct"] = crypto.b64url_encode(
            bytes(reversed(crypto.b64url_decode(envelope["ct"])))
        )
        self.assertEqual(self.redeem(tampered, scope).status_code, 400)
        # Sub mismatch failure.
        wrong_sub = self.mint_envelope(scope, sub="bob")
        self.assertEqual(self.redeem(wrong_sub, scope).status_code, 400)
        # Malformed body failure.
        self.client.post(
            "/v1/sessions", json={"provider": "anthropic"}, headers=bearer("alice")
        )

        self.assert_never_logged(*self.sensitive(envelope))
        self.assert_never_logged(*self.sensitive(wrong_sub))

    def test_allowlist_line_carries_correlation_id_only(self):
        scope = chat_scope()
        response = self.redeem(self.mint_envelope(scope), scope)
        session = self.state.sessions.get(response.json()["session_id"], sub="alice")
        allowlist = [
            m for m in self.logged_messages() if "session redeemed" in m
        ]
        self.assertEqual(len(allowlist), 1)
        self.assertIn(f"correlation_id={session.correlation_id}", allowlist[0])
        self.assertIn("sub=alice", allowlist[0])
        self.assertIn("provider=anthropic", allowlist[0])
        self.assertNotIn(API_KEY, allowlist[0])

    def test_decrypt_failure_logs_nothing_at_all(self):
        scope = chat_scope()
        envelope = self.mint_envelope(scope, iat=int(time.time()) - 3_600)
        before = len(self.log_handler.records)
        self.assertEqual(self.redeem(envelope, scope).status_code, 400)
        new = self.log_handler.records[before:]
        for record in new:
            for needle in self.sensitive(envelope):
                self.assertNotIn(needle, record.getMessage())
        keyproxy_lines = [
            r.getMessage() for r in new if r.name.startswith("keyproxy")
        ]
        self.assertEqual(
            keyproxy_lines, [],
            "decrypt failure emitted keyproxy log lines",
        )


class TestRotationAndDevMode(unittest.TestCase):
    def test_publickey_advertises_newest_first_and_old_kid_still_decrypts(self):
        old_key = crypto.generate_private_key()
        new_key = crypto.generate_private_key()
        bundle = render_bundle("kp-old", old_key) + "\n" + render_bundle("kp-new", new_key)
        env = {
            "KEYPROXY_PRIVATE_KEYS": bundle,
            "QUANTCORE_JWT_PUBLIC_KEY": JWT_PUBLIC_PEM,
        }
        with patch.dict("os.environ", env, clear=True):
            app = create_app()
            client = TestClient(app)
            keys = client.get("/v1/publickey").json()["keys"]
            self.assertEqual([k["kid"] for k in keys], ["kp-new", "kp-old"])

            scope = chat_scope()
            aad = {
                "sub": "alice",
                "provider": "anthropic",
                "iat": int(time.time()),
                "jti": str(uuid.uuid4()),
                "scope_hash": crypto.compute_scope_hash(scope),
            }
            envelope = crypto.encrypt_envelope(
                API_KEY, old_key.public_key(), kid="kp-old", aad=aad
            )
            response = client.post(
                "/v1/sessions",
                json={"provider": "anthropic", "envelope": envelope, "scope": scope},
                headers=bearer("alice"),
            )
            self.assertEqual(response.status_code, 200)

    def test_dev_mode_ephemeral_key_and_disabled_auth(self):
        env = {"KEYPROXY_AUTH_DISABLED": "1"}
        with patch.dict("os.environ", env, clear=True):
            app = create_app()
            client = TestClient(app)
            keys = client.get("/v1/publickey").json()["keys"]
            self.assertEqual([k["kid"] for k in keys], [DEV_KID])
            public_key = load_der_public_key(crypto.b64url_decode(keys[0]["spki"]))

            scope = chat_scope()
            aad = {
                "sub": "local",  # the synthetic caller when auth is off
                "provider": "anthropic",
                "iat": int(time.time()),
                "jti": str(uuid.uuid4()),
                "scope_hash": crypto.compute_scope_hash(scope),
            }
            envelope = crypto.encrypt_envelope(
                API_KEY, public_key, kid=DEV_KID, aad=aad
            )
            response = client.post(
                "/v1/sessions",
                json={"provider": "anthropic", "envelope": envelope, "scope": scope},
            )
            self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
