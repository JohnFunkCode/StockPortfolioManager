"""Packet 3a tests: the keyproxy streaming turn endpoint.

``POST /v1/providers/anthropic/messages/stream`` against a stub provider:
delta/final/error SSE framing, per-call scope check + budget count before the
key is attached, token-ceiling session kill, expired/unknown/wrong-sub
rejections, the ``: ping`` heartbeat during provider silence, the
no-compression guarantee, and the never-log assertions for the new paths.
"""

import json
import time
import unittest

from unittest.mock import patch

from test_keyproxy_service import (
    API_KEY,
    GENERIC,
    KeyProxyServiceTestBase,
    bearer,
    chat_scope,
)

STREAM_URL = "/v1/providers/anthropic/messages/stream"

FINAL_MESSAGE = {
    "id": "msg_stub",
    "role": "assistant",
    "content": [{"type": "text", "text": "hello world"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 100, "output_tokens": 50},
}


def stub_stream(*deltas, final=None, delay=0.0, error=False):
    """A stand-in for keyproxy.providers.anthropic.stream_turn."""

    def stream_turn(api_key, **params):
        stream_turn.calls.append((api_key, params))
        for text in deltas:
            if delay:
                time.sleep(delay)
            yield ("delta", text)
        if error:
            raise RuntimeError("provider blew up: " + api_key)
        if delay:
            time.sleep(delay)
        yield ("final", final if final is not None else dict(FINAL_MESSAGE))

    stream_turn.calls = []
    return stream_turn


def parse_sse(raw_text):
    """Split an SSE body into (comment_lines, [(event, data_dict), ...])."""
    comments, events = [], []
    for block in raw_text.split("\n\n"):
        event = data = None
        for line in block.split("\n"):
            if line.startswith(":"):
                comments.append(line)
            elif line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1])
        if event is not None:
            events.append((event, data))
    return comments, events


class StreamingTestBase(KeyProxyServiceTestBase):
    def open_session(self, scope=None, sub="alice"):
        scope = scope or chat_scope()
        response = self.redeem(self.mint_envelope(scope, sub=sub), scope, sub=sub)
        self.assertEqual(response.status_code, 200)
        return response.json()["session_id"]

    def stream(self, session_id, sub="alice", headers=None, **overrides):
        body = {
            "session_id": session_id,
            "model": "claude-fable-5",
            "effort": "high",
            "system": "you are a test",
            "tools": [],
            "messages": [{"role": "user", "content": "hi"}],
        }
        body.update(overrides)
        return self.client.post(
            STREAM_URL, json=body, headers={**bearer(sub), **(headers or {})}
        )


class TestStreamingTurn(StreamingTestBase):
    def test_delta_final_framing_and_key_attachment(self):
        stub = stub_stream("Hello", " world")
        with patch("keyproxy.providers.anthropic.stream_turn", stub):
            response = self.stream(self.open_session())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        _, events = parse_sse(response.text)
        self.assertEqual(
            events,
            [
                ("delta", {"text": "Hello"}),
                ("delta", {"text": " world"}),
                ("final", FINAL_MESSAGE),
            ],
        )
        # The plaintext key and the mirrored turn params reached the provider.
        (key, params), = stub.calls
        self.assertEqual(key, API_KEY)
        self.assertEqual(params["model"], "claude-fable-5")
        self.assertEqual(params["effort"], "high")
        self.assertEqual(params["max_tokens"], 8192)
        self.assertEqual(params["system"], "you are a test")
        self.assertEqual(params["messages"], [{"role": "user", "content": "hi"}])

    def test_provider_error_is_a_generic_error_frame(self):
        stub = stub_stream("partial", error=True)
        with patch("keyproxy.providers.anthropic.stream_turn", stub):
            response = self.stream(self.open_session())
        self.assertEqual(response.status_code, 200)
        _, events = parse_sse(response.text)
        # A bare RuntimeError classifies to the opaque provider_error code —
        # only a closed-set reason code crosses the wire, never provider text.
        self.assertEqual(events[-1], ("error", {"code": "provider_error"}))
        # The exception message carried the key — it must never surface on
        # the wire (the SSE response), regardless of what reaches the log.
        self.assertNotIn(API_KEY, response.text)
        self.assertNotIn("blew up", response.text)
        # The exception message (which carried the key) must never be logged.
        self.assert_never_logged(API_KEY)

    def test_classifiable_provider_error_emits_specific_reason_code(self):
        # The billing failure (a 400 whose message is the static "credit
        # balance is too low" string) must reach the SSE `error` frame as its
        # specific reason code — but the provider's raw message must NOT.
        class FakeAPIStatusError(Exception):
            status_code = 400
            body = {
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the "
                    "Anthropic API. Please go to Plans & Billing. " + API_KEY,
                },
            }

        def stream_turn(api_key, **params):
            yield ("delta", "partial")
            raise FakeAPIStatusError("unused")

        with patch("keyproxy.providers.anthropic.stream_turn", stream_turn):
            response = self.stream(self.open_session())
        self.assertEqual(response.status_code, 200)
        _, events = parse_sse(response.text)
        self.assertEqual(events[-1], ("error", {"code": "insufficient_credits"}))
        # Only the code crosses the wire — never the provider's message bytes.
        self.assertNotIn("credit balance", response.text)
        self.assertNotIn(API_KEY, response.text)
        self.assert_never_logged(API_KEY)

    def test_call_budget_counted_then_exhausted_then_killed(self):
        scope = chat_scope(
            budget={"max_calls": 2, "max_mutations": 0, "max_tokens": 250_000, "ttl": 300}
        )
        session_id = self.open_session(scope)
        with patch("keyproxy.providers.anthropic.stream_turn", stub_stream("x")):
            self.assertEqual(self.stream(session_id).status_code, 200)
            session = self.state.sessions.get(session_id, sub="alice")
            self.assertEqual(session.budget.calls_used, 1)
            self.assertEqual(session.budget.tokens_used, 150)
            self.assertEqual(self.stream(session_id).status_code, 200)
            third = self.stream(session_id)
        self.assertEqual(third.status_code, 400)
        self.assertEqual(third.json()["detail"], "session call budget exhausted")
        # Exhaustion kills the session: later calls can't even name it.
        self.assertEqual(self.state.sessions._sessions, {})
        fourth = self.stream(session_id)
        self.assertEqual(fourth.status_code, 400)
        self.assertEqual(fourth.json()["detail"], GENERIC)

    def test_token_ceiling_kills_session_after_final(self):
        scope = chat_scope(
            budget={"max_calls": 20, "max_mutations": 0, "max_tokens": 120, "ttl": 300}
        )
        session_id = self.open_session(scope)
        with patch("keyproxy.providers.anthropic.stream_turn", stub_stream("x")):
            response = self.stream(session_id)
        # The response that crossed the line still streams to completion...
        self.assertEqual(response.status_code, 200)
        self.assertEqual(parse_sse(response.text)[1][-1][0], "final")
        # ...but the cumulative usage (150 > 120) killed the session.
        self.assertEqual(self.state.sessions._sessions, {})
        replayed = self.stream(session_id)
        self.assertEqual(replayed.status_code, 400)
        self.assertEqual(replayed.json()["detail"], GENERIC)

    def test_unknown_session_rejected(self):
        response = self.stream("deadbeef" * 4)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], GENERIC)

    def test_wrong_sub_rejected(self):
        session_id = self.open_session(sub="alice")
        response = self.stream(session_id, sub="bob")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], GENERIC)
        # And the session survives for its real owner.
        self.state.sessions.get(session_id, sub="alice")

    def test_expired_session_rejected(self):
        session_id = self.open_session()
        session = self.state.sessions.get(session_id, sub="alice")
        session._last_activity -= session.ttl + 1
        response = self.stream(session_id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], GENERIC)
        self.assertEqual(self.state.sessions._sessions, {})

    def test_missing_bearer_is_401(self):
        response = self.client.post(
            STREAM_URL,
            json={
                "session_id": "deadbeef" * 4,
                "model": "m",
                "effort": "high",
                "messages": [],
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_malformed_body_is_generic_400(self):
        response = self.client.post(
            STREAM_URL, json={"session_id": "x"}, headers=bearer("alice")
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": GENERIC})

    def test_no_compression_of_event_stream(self):
        # The keyproxy half of test_keyproxy_stream_not_compressed: even when
        # the client advertises gzip, the SSE body must come back identity —
        # a compressor buffers until its window fills, which looks exactly
        # like broken streaming.
        with patch("keyproxy.providers.anthropic.stream_turn", stub_stream("x")):
            response = self.stream(
                self.open_session(), headers={"Accept-Encoding": "gzip"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("content-encoding", response.headers)

    def test_streaming_paths_never_log_key_material(self):
        token = bearer("alice")["Authorization"].split()[1]
        session_id = self.open_session()
        with patch("keyproxy.providers.anthropic.stream_turn", stub_stream("x")):
            self.assertEqual(self.stream(session_id).status_code, 200)
        self.stream("deadbeef" * 4)  # rejection path
        self.assert_never_logged(API_KEY, token)
        allowlist = [m for m in self.logged_messages() if "turn streamed" in m]
        self.assertEqual(len(allowlist), 1)
        self.assertIn("correlation_id=", allowlist[0])
        self.assertIn("sub=alice", allowlist[0])
        self.assertIn("provider=anthropic", allowlist[0])
        self.assertIn("tokens=150", allowlist[0])
        # Beyond the allowlisted summary lines ("session redeemed" from the
        # 2b redemption, "turn streamed" from this packet), the streaming
        # paths — including the rejection — emit no keyproxy records at all.
        unexpected = [
            r.getMessage()
            for r in self.log_handler.records
            if r.name.startswith("keyproxy")
            and "turn streamed" not in r.getMessage()
            and "session redeemed" not in r.getMessage()
        ]
        self.assertEqual(unexpected, [])

    def test_provider_error_logs_only_safe_metadata(self):
        # A bare exception (no .status_code) — e.g. the RuntimeError above,
        # which embeds the key in its message — logs only content-free
        # metadata: class name, status, and the classified reason code. The
        # provider's message text (and the key inside it) never reaches a log.
        stub = stub_stream("partial", error=True)
        with patch("keyproxy.providers.anthropic.stream_turn", stub):
            self.stream(self.open_session())
        self.assert_never_logged(API_KEY)
        diag = [m for m in self.logged_messages() if "provider stream failed" in m]
        self.assertEqual(len(diag), 1)
        self.assertIn("exception_type=RuntimeError", diag[0])
        self.assertIn("status_code=None", diag[0])
        self.assertIn("reason=provider_error", diag[0])
        # The free-text provider message is never logged — not even redacted.
        self.assertNotIn("error_detail", diag[0])
        self.assertNotIn("blew up", diag[0])

    def test_provider_status_error_logs_code_and_type_not_message(self):
        # Duck-typed anthropic.APIStatusError shape: status_code + a body
        # whose error.message carries request-derived text. Only the safe
        # metadata — status code, fixed error-type enum, and classified reason
        # — is logged; the message text (and the key inside it) never is.
        class FakeAPIStatusError(Exception):
            status_code = 400
            body = {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "tool_result for " + API_KEY + " is malformed",
                },
            }

        def stream_turn(api_key, **params):
            yield ("delta", "partial")
            raise FakeAPIStatusError("unused")

        with patch("keyproxy.providers.anthropic.stream_turn", stream_turn):
            self.stream(self.open_session())
        self.assert_never_logged(API_KEY)
        diag = [m for m in self.logged_messages() if "provider stream failed" in m]
        self.assertEqual(len(diag), 1)
        self.assertIn("exception_type=FakeAPIStatusError", diag[0])
        self.assertIn("status_code=400", diag[0])
        self.assertIn("error_type=invalid_request_error", diag[0])
        self.assertIn("reason=provider_error", diag[0])
        # The provider's free-text message is never logged, redacted or not.
        self.assertNotIn("error_detail", diag[0])
        self.assertNotIn("tool_result for", diag[0])


class TestStreamingHeartbeat(StreamingTestBase):
    extra_env = {"KEYPROXY_HEARTBEAT_SECS": "0.05"}

    def test_heartbeat_comments_during_provider_silence(self):
        stub = stub_stream("late delta", delay=0.25)
        with patch("keyproxy.providers.anthropic.stream_turn", stub):
            response = self.stream(self.open_session())
        self.assertEqual(response.status_code, 200)
        comments, events = parse_sse(response.text)
        self.assertTrue(
            any(c.startswith(": ping") for c in comments),
            f"expected ': ping' heartbeats on the wire, got {comments!r}",
        )
        # Heartbeats are comments — the event framing is untouched by them.
        self.assertEqual(events[0], ("delta", {"text": "late delta"}))
        self.assertEqual(events[-1][0], "final")


if __name__ == "__main__":
    unittest.main()
