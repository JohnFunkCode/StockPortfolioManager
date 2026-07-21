"""Packet 3b tests: KeyProxyChatClient + session exchange.

The keyproxy app runs under a REAL uvicorn server on a local port (not an
in-process TestClient, which would bypass socket-level buffering), with the
provider stubbed at ``keyproxy.providers.anthropic.stream_turn``. Covers:
client SSE parse, the session exchange (envelope redeemed exactly once per
send, turns 2+ reuse the session, teardown on Done and on error, expired
session mid-chat -> clean re-send error), verbatim budget-message
pass-through, pubkey/validate calls, the thin service — plus the two
risk-pinning tests decided 2026-07-16: ``test_keyproxy_stream_no_buffering``
and ``test_thinking_block_signature_roundtrip``.
"""

import os
import threading
import time
import unittest

from unittest.mock import Mock, patch

import uvicorn

from quantcore.gateways import keyproxy_gateway
from quantcore.services.keyproxy import KeyProxyService
from test_keyproxy_service import (
    API_KEY,
    KID,
    KeyProxyServiceTestBase,
    chat_scope,
    mint_user_token,
)

TEXT_BLOCK = {"type": "text", "text": "hi there"}
THINKING_BLOCK = {
    "type": "thinking",
    "thinking": "let me check the price",
    "signature": "sig-Zm9vYmFyYmF6cXV4/+==",
}
TOOL_USE_BLOCK = {
    "type": "tool_use",
    "id": "toolu_1",
    "name": "get_stock_price",
    "input": {"symbol": "AAPL"},
}


def final_message(*blocks, stop_reason="end_turn", usage=None):
    return {
        "id": "msg_stub",
        "role": "assistant",
        "content": list(blocks),
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5},
    }


class ScriptedProvider:
    """Scripted stand-in for keyproxy.providers.anthropic.stream_turn.

    Each turn dict: ``deltas`` (list of str), ``final`` (message dict),
    ``delay`` (sleep before each emission), ``error`` (raise instead of
    finishing). Records every call's params and each delta's emit time.
    """

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []
        self.emit_times = []

    def __call__(self, api_key, **params):
        turn = self.turns.pop(0)
        self.calls.append({"api_key": api_key, **params})
        for text in turn.get("deltas", []):
            if turn.get("delay"):
                time.sleep(turn["delay"])
            self.emit_times.append(time.monotonic())
            yield ("delta", text)
        if turn.get("error"):
            raise RuntimeError("provider exploded: " + api_key)
        if turn.get("delay"):
            time.sleep(turn["delay"])
        yield ("final", turn["final"])


class GatewayTestBase(KeyProxyServiceTestBase):
    """Base harness + a real uvicorn server, KEYPROXY_URL pointed at it."""

    def setUp(self):
        super().setUp()
        config = uvicorn.Config(
            self.app, host="127.0.0.1", port=0, log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        thread = threading.Thread(target=self.server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                self.fail("uvicorn server did not start")
            time.sleep(0.01)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        os.environ["KEYPROXY_URL"] = f"http://127.0.0.1:{port}"

        def stop():
            self.server.should_exit = True
            thread.join(timeout=5)

        self.addCleanup(stop)

    def auth_token(self, sub="alice"):
        return mint_user_token(sub)

    def make_client(self, scope=None, sub="alice", **kwargs):
        scope = scope or chat_scope()
        return keyproxy_gateway.KeyProxyChatClient(
            envelope=self.mint_envelope(scope, sub=sub),
            scope=scope,
            auth_token=self.auth_token(sub),
            model="claude-fable-5",
            effort="medium",
            **kwargs,
        )

    def run_turn(self, client, messages=None):
        return list(
            client.stream_turn(
                system="test system",
                tools=[],
                messages=messages or [{"role": "user", "content": "hi"}],
            )
        )


class TestKeyProxyChatClient(GatewayTestBase):
    def test_parses_deltas_and_final_and_tears_down_on_done(self):
        provider = ScriptedProvider(
            [{"deltas": ["Hel", "lo"], "final": final_message(TEXT_BLOCK)}]
        )
        with patch("keyproxy.providers.anthropic.stream_turn", provider):
            events = self.run_turn(self.make_client())
        self.assertEqual(events[0], ("delta", "Hel"))
        self.assertEqual(events[1], ("delta", "lo"))
        kind, message = events[2]
        self.assertEqual(kind, "final")
        self.assertEqual(message.stop_reason, "end_turn")
        self.assertEqual(message.content[0].type, "text")
        self.assertEqual(message.content[0].text, "hi there")
        self.assertEqual(message.content[0].raw, TEXT_BLOCK)
        # The stub got the plaintext key; the terminal turn tore the session down.
        self.assertEqual(provider.calls[0]["api_key"], API_KEY)
        self.assertEqual(self.state.sessions._sessions, {})

    def test_envelope_redeemed_once_and_session_reused_across_turns(self):
        provider = ScriptedProvider(
            [
                {"final": final_message(THINKING_BLOCK, TOOL_USE_BLOCK)},
                {"final": final_message(TEXT_BLOCK)},
            ]
        )
        client = self.make_client()
        token = self.auth_token()
        with patch("keyproxy.providers.anthropic.stream_turn", provider):
            (_, first) = self.run_turn(client)[-1]
            self.assertEqual(len(self.state.sessions._sessions), 1)
            session_id_after_turn1 = client._session_id
            self.run_turn(
                client,
                messages=[
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": first.content},
                    {"role": "user", "content": [{"type": "tool_result",
                                                  "tool_use_id": "toolu_1",
                                                  "content": "{}"}]},
                ],
            )
        # Turn 2 rode the same session (no second redemption), then tore down.
        self.assertIsNotNone(session_id_after_turn1)
        redemptions = [m for m in self.logged_messages() if "session redeemed" in m]
        self.assertEqual(len(redemptions), 1)
        self.assertEqual(self.state.sessions._sessions, {})
        self.assert_never_logged(API_KEY, token)

    def test_teardown_and_clean_message_on_provider_error(self):
        provider = ScriptedProvider([{"deltas": ["partial"], "error": True}])
        client = self.make_client()
        with patch("keyproxy.providers.anthropic.stream_turn", provider):
            with self.assertRaises(keyproxy_gateway.KeyProxyError) as ctx:
                self.run_turn(client)
        self.assertEqual(str(ctx.exception), keyproxy_gateway.PROVIDER_ERROR_MESSAGE)
        self.assertNotIn(API_KEY, str(ctx.exception))
        self.assertEqual(self.state.sessions._sessions, {})
        self.assert_never_logged(API_KEY, "exploded")

    def test_expired_session_mid_chat_surfaces_clean_resend_error(self):
        provider = ScriptedProvider(
            [{"final": final_message(THINKING_BLOCK, TOOL_USE_BLOCK)}]
        )
        client = self.make_client()
        with patch("keyproxy.providers.anthropic.stream_turn", provider):
            self.run_turn(client)
            # The keyproxy expires the session between turns (TTL backstop).
            self.state.sessions.delete(client._session_id)
            with self.assertRaises(keyproxy_gateway.KeyProxyError) as ctx:
                self.run_turn(client)
        self.assertEqual(str(ctx.exception), keyproxy_gateway.RESEND_MESSAGE)

    def test_budget_exhaustion_detail_passes_through_verbatim(self):
        scope = chat_scope(
            budget={"max_calls": 1, "max_mutations": 0, "max_tokens": 250_000, "ttl": 300}
        )
        provider = ScriptedProvider(
            [{"final": final_message(THINKING_BLOCK, TOOL_USE_BLOCK)}]
        )
        client = self.make_client(scope)
        with patch("keyproxy.providers.anthropic.stream_turn", provider):
            self.run_turn(client)
            with self.assertRaises(keyproxy_gateway.KeyProxyError) as ctx:
                self.run_turn(client)
        # Budget messages name no values and must reach the user as-is.
        self.assertEqual(str(ctx.exception), "session call budget exhausted")

    def test_rejected_redemption_is_a_clean_error(self):
        scope = chat_scope()
        client = keyproxy_gateway.KeyProxyChatClient(
            envelope=self.mint_envelope(scope, sub="bob"),  # sub mismatch
            scope=scope,
            auth_token=self.auth_token("alice"),
            model="claude-fable-5",
            effort="medium",
        )
        with self.assertRaises(keyproxy_gateway.KeyProxyError) as ctx:
            self.run_turn(client)
        self.assertEqual(str(ctx.exception), keyproxy_gateway.RESEND_MESSAGE)

    def test_invalid_bearer_token_is_a_distinct_auth_error_and_logs_status(self):
        # A real 401 from require_caller (bad JWT), not a mocked status code —
        # must map to a distinct message from UNAVAILABLE_MESSAGE and must log
        # the bare status so the next occurrence is diagnosable server-side.
        scope = chat_scope()
        client = keyproxy_gateway.KeyProxyChatClient(
            envelope=self.mint_envelope(scope),
            scope=scope,
            auth_token="not-a-valid-jwt",
            model="claude-fable-5",
            effort="medium",
        )
        with self.assertRaises(keyproxy_gateway.KeyProxyError) as ctx:
            self.run_turn(client)
        self.assertEqual(str(ctx.exception), keyproxy_gateway.AUTH_ERROR_MESSAGE)
        self.assertNotEqual(
            keyproxy_gateway.AUTH_ERROR_MESSAGE, keyproxy_gateway.UNAVAILABLE_MESSAGE
        )
        rejections = [
            m for m in self.logged_messages() if "keyproxy session rejected" in m
        ]
        self.assertEqual(len(rejections), 1)
        self.assertIn("401", rejections[0])
        # Never-log policy: the log line carries the status only — never the
        # token, the response detail string, or any other request material.
        self.assert_never_logged("not-a-valid-jwt", "invalid or missing bearer token")

    def test_keyproxy_stream_no_buffering(self):
        # Risk-pinning test (2026-07-16): the api -> keyproxy hop must pass
        # chunks through as they arrive. 5 deltas spaced ~200 ms apart; the
        # first must arrive before the stub emits the last, and arrivals must
        # be spread across the emission window, not clustered at the end.
        provider = ScriptedProvider(
            [{"deltas": ["d0", "d1", "d2", "d3", "d4"], "delay": 0.2,
              "final": final_message(TEXT_BLOCK)}]
        )
        client = self.make_client()
        arrivals = []
        with patch("keyproxy.providers.anthropic.stream_turn", provider):
            for kind, _payload in client.stream_turn(
                system="s", tools=[], messages=[{"role": "user", "content": "hi"}]
            ):
                if kind == "delta":
                    arrivals.append(time.monotonic())
        self.assertEqual(len(arrivals), 5)
        self.assertLess(
            arrivals[0],
            provider.emit_times[-1],
            "first delta arrived only after the stub finished emitting — "
            "something on the hop is buffering",
        )
        spread = arrivals[-1] - arrivals[0]
        self.assertGreaterEqual(
            spread,
            0.4,
            f"delta arrivals clustered within {spread:.3f}s of each other over a "
            "~0.8s emission window — something on the hop is buffering",
        )

    def test_thinking_block_signature_roundtrip(self):
        # Risk-pinning test (2026-07-16): a thinking block must survive
        # keyproxy JSON -> SSE -> client parse -> ChatService echo -> the next
        # turn's outbound payload BYTE-EXACTLY (signature included).
        from quantcore.services.chat import ChatService, Done

        provider = ScriptedProvider(
            [
                {"final": final_message(THINKING_BLOCK, TOOL_USE_BLOCK)},
                {"final": final_message(TEXT_BLOCK)},
            ]
        )
        prices = Mock()
        prices.get_stock_price.return_value = {"symbol": "AAPL", "price": 123.45}
        service = ChatService(
            prices, Mock(), Mock(), Mock(),
            client_factory=lambda context: self.make_client(),
        )
        with patch("keyproxy.providers.anthropic.stream_turn", provider):
            events = list(
                service.stream_chat([{"role": "user", "content": "price of AAPL?"}])
            )
        self.assertIsInstance(events[-1], Done)
        prices.get_stock_price.assert_called_once_with("AAPL")
        # What the keyproxy parsed off turn 2's request — the full round trip.
        outbound = provider.calls[1]["messages"]
        assistant = next(m for m in outbound if m["role"] == "assistant")
        self.assertEqual(assistant["content"][0], THINKING_BLOCK)
        self.assertEqual(assistant["content"][1], TOOL_USE_BLOCK)


class TestClientHeartbeatTolerance(GatewayTestBase):
    extra_env = {"KEYPROXY_HEARTBEAT_SECS": "0.05"}

    def test_heartbeat_comments_are_invisible_to_the_parser(self):
        provider = ScriptedProvider(
            [{"deltas": ["slow"], "delay": 0.2, "final": final_message(TEXT_BLOCK)}]
        )
        with patch("keyproxy.providers.anthropic.stream_turn", provider):
            events = self.run_turn(self.make_client())
        # Pings were on the wire (~0.2s gaps vs 0.05s interval) yet the
        # parsed event sequence is exactly delta + final.
        self.assertEqual(events[0], ("delta", "slow"))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1][0], "final")


class TestPubkeyAndValidate(GatewayTestBase):
    def test_get_public_keys(self):
        keys = keyproxy_gateway.get_public_keys()
        self.assertEqual(keys[0]["kid"], KID)

    def test_validate_key_round_trip(self):
        scope = chat_scope(action="key.validate")
        with patch("keyproxy.providers.anthropic.validate_key", return_value=True):
            result = keyproxy_gateway.validate_key(
                envelope=self.mint_envelope(scope),
                scope=scope,
                auth_token=self.auth_token(),
            )
        self.assertEqual(
            result, {"valid": True, "provider": "anthropic", "key_hint": "…abcd"}
        )
        # Immediate teardown: the validation session never outlives the call.
        self.assertEqual(self.state.sessions._sessions, {})

    def test_validate_key_rejection_is_clean(self):
        scope = chat_scope(action="key.validate")
        with self.assertRaises(keyproxy_gateway.KeyProxyError) as ctx:
            keyproxy_gateway.validate_key(
                envelope=self.mint_envelope(scope, sub="bob"),
                scope=scope,
                auth_token=self.auth_token("alice"),
            )
        self.assertEqual(str(ctx.exception), keyproxy_gateway.RESEND_MESSAGE)


class TestAuthHeaders(unittest.TestCase):
    def setUp(self):
        os.environ.pop("KEYPROXY_ID_TOKEN_AUDIENCE", None)
        keyproxy_gateway._id_token_cache = None
        self.addCleanup(os.environ.pop, "KEYPROXY_ID_TOKEN_AUDIENCE", None)
        self.addCleanup(setattr, keyproxy_gateway, "_id_token_cache", None)

    def test_empty_token_omits_the_header_entirely(self):
        # AUTH_DISABLED dev stacks pass auth_token="" — "Bearer " with no
        # token is an illegal header value that h11 rejects client-side, so
        # the header must be absent, not empty.
        self.assertEqual(keyproxy_gateway._headers(""), {})

    def test_token_is_sent_as_bearer(self):
        self.assertEqual(
            keyproxy_gateway._headers("tok-123"),
            {"Authorization": "Bearer tok-123"},
        )


class TestGoogleIdTokenLayer(unittest.TestCase):
    """Packet 8b: the Cloud Run IAM hop (X-Serverless-Authorization).

    Only active when KEYPROXY_ID_TOKEN_AUDIENCE is set — every test above and
    below this class runs with it unset, pinning that local/compose behavior
    is completely unchanged.
    """

    AUDIENCE = "https://keyproxy.example.run.app"

    def setUp(self):
        os.environ["KEYPROXY_ID_TOKEN_AUDIENCE"] = self.AUDIENCE
        keyproxy_gateway._id_token_cache = None
        self.addCleanup(os.environ.pop, "KEYPROXY_ID_TOKEN_AUDIENCE", None)
        self.addCleanup(setattr, keyproxy_gateway, "_id_token_cache", None)

    def test_id_token_rides_serverless_header_user_jwt_keeps_authorization(self):
        with patch("google.oauth2.id_token.fetch_id_token", return_value="idtok") as fetch:
            self.assertEqual(
                keyproxy_gateway._headers("user-jwt"),
                {
                    "X-Serverless-Authorization": "Bearer idtok",
                    "Authorization": "Bearer user-jwt",
                },
            )
        # Audience is the keyproxy service URL (what Cloud Run IAM checks).
        self.assertEqual(fetch.call_args.args[1], self.AUDIENCE)

    def test_publickey_style_calls_carry_only_the_id_token(self):
        with patch("google.oauth2.id_token.fetch_id_token", return_value="idtok"):
            self.assertEqual(
                keyproxy_gateway._headers(""),
                {"X-Serverless-Authorization": "Bearer idtok"},
            )

    def test_token_is_cached_across_requests(self):
        with patch("google.oauth2.id_token.fetch_id_token", return_value="idtok") as fetch:
            keyproxy_gateway._headers("a")
            keyproxy_gateway._headers("b")
        self.assertEqual(fetch.call_count, 1)

    def test_token_is_refetched_after_ttl(self):
        with patch("google.oauth2.id_token.fetch_id_token", return_value="idtok") as fetch:
            keyproxy_gateway._headers("a")
            token, fetched_at = keyproxy_gateway._id_token_cache
            keyproxy_gateway._id_token_cache = (
                token,
                fetched_at - keyproxy_gateway._ID_TOKEN_TTL_SECONDS - 1,
            )
            keyproxy_gateway._headers("a")
        self.assertEqual(fetch.call_count, 2)

    def test_fetch_failure_is_a_clean_silent_error(self):
        # Never-log policy: google-auth exceptions can embed request/response
        # material — nothing may be logged, and the raised error must carry
        # only the constant user-facing message, never the cause's text.
        boom = RuntimeError("metadata response body with sensitive-material")
        with patch("google.oauth2.id_token.fetch_id_token", side_effect=boom):
            with self.assertNoLogs(level="DEBUG"):
                with self.assertRaises(keyproxy_gateway.KeyProxyError) as ctx:
                    keyproxy_gateway._headers("user-jwt")
        self.assertEqual(str(ctx.exception), keyproxy_gateway.UNAVAILABLE_MESSAGE)
        self.assertIsNone(ctx.exception.__cause__)
        self.assertNotIn("sensitive-material", repr(ctx.exception))

    def test_close_stays_best_effort_when_the_fetch_fails(self):
        client = keyproxy_gateway.KeyProxyChatClient(
            envelope={}, scope={}, auth_token="t", model="m", effort="low",
        )
        client._session_id = "sess-1"
        with patch("google.oauth2.id_token.fetch_id_token", side_effect=RuntimeError):
            client.close()  # must not raise
        self.assertIsNone(client._session_id)


class TestKeyProxyServiceThin(unittest.TestCase):
    def test_passthrough(self):
        gateway = Mock()
        gateway.is_configured.return_value = True
        gateway.get_public_keys.return_value = [{"kid": "k"}]
        gateway.validate_key.return_value = {"valid": True}
        service = KeyProxyService(gateway)
        self.assertTrue(service.is_configured())
        self.assertEqual(service.get_public_keys(), [{"kid": "k"}])
        self.assertEqual(
            service.validate_key(envelope={"e": 1}, scope={"s": 1}, auth_token="t"),
            {"valid": True},
        )
        gateway.validate_key.assert_called_once_with(
            envelope={"e": 1}, scope={"s": 1}, auth_token="t"
        )


if __name__ == "__main__":
    unittest.main()
