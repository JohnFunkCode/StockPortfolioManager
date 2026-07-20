"""Packet 3c Verify: SSE compression + heartbeat pinning on BOTH hops.

Sends stream requests **with ``Accept-Encoding: gzip``** to the keyproxy's
stream endpoint and to ``POST /api/chat`` (the full browser path: api route →
registry KeyProxyChatClient → real uvicorn keyproxy → stubbed provider) and
asserts that no compression middleware touches ``text/event-stream`` (no
``Content-Encoding``, plain chunks) and that ``: ping`` heartbeat comments
appear on the wire during an artificially long provider pause — on each hop.

The keyproxy runs under real uvicorn (GatewayTestBase); the api app runs under
TestClient in the same process, so the provider stub patch reaches it.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

# Swap in the test DSN BEFORE quantcore.db is imported (it freezes DB_DSN at
# import time), then let the guard abort if this process would reach prod.
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
            os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
            break

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

from quantcore.services.registry import get_services  # noqa: E402
from api.main import create_app  # noqa: E402
from test_keyproxy_gateway import (  # noqa: E402
    GatewayTestBase,
    ScriptedProvider,
    TEXT_BLOCK,
    final_message,
)
from test_keyproxy_service import bearer, chat_scope  # noqa: E402

# The base patches os.environ with clear=True; the api app (and any DB touch
# inside a request) must keep seeing the test DSN inside that patched world.
_TEST_DSN = os.environ.get("QUANTCORE_DB_DSN", "")


class TestStreamNotCompressed(GatewayTestBase):
    extra_env = {
        "KEYPROXY_HEARTBEAT_SECS": "0.05",
        "QUANTCORE_DB_DSN": _TEST_DSN,
    }

    def setUp(self):
        super().setUp()  # uvicorn keyproxy up; KEYPROXY_URL now points at it
        get_services.cache_clear()  # rebuild with the KeyProxyChatClient factory
        self.addCleanup(get_services.cache_clear)
        self.api = TestClient(create_app(), raise_server_exceptions=False)

    def slow_provider(self):
        # One ~0.25s pause before the delta and another before the final —
        # multiples of the 0.05s heartbeat, so pings MUST appear on the wire.
        return ScriptedProvider(
            [{"deltas": ["slow"], "delay": 0.25, "final": final_message(TEXT_BLOCK)}]
        )

    def test_keyproxy_stream_not_compressed(self):
        scope = chat_scope()
        envelope = self.mint_envelope(scope, sub="alice")
        redeemed = self.redeem(envelope, scope)
        self.assertEqual(redeemed.status_code, 200)
        session_id = redeemed.json()["session_id"]
        provider = self.slow_provider()
        with patch("keyproxy.providers.anthropic.stream_turn", provider):
            response = self.client.post(
                "/v1/providers/anthropic/messages/stream",
                json={
                    "session_id": session_id,
                    "model": "claude-fable-5",
                    "effort": "medium",
                    "system": "s",
                    "tools": [],
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers={**bearer("alice"), "Accept-Encoding": "gzip"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        self.assertNotIn("content-encoding", response.headers)
        # Plain chunks: the raw body parses as text and carries heartbeats
        # emitted during the provider pauses, plus the real frames.
        self.assertIn(": ping", response.text)
        self.assertIn("event: delta", response.text)
        self.assertIn("event: final", response.text)

    def test_api_chat_stream_not_compressed(self):
        scope = chat_scope()
        envelope = self.mint_envelope(scope, sub="alice")
        provider = self.slow_provider()
        with patch("keyproxy.providers.anthropic.stream_turn", provider):
            response = self.api.post(
                "/api/chat",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "key_envelope": envelope,
                    "scope": scope,
                },
                headers={**bearer("alice"), "Accept-Encoding": "gzip"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        self.assertNotIn("content-encoding", response.headers)
        self.assertIn(": ping", response.text)
        self.assertIn("event: text", response.text)
        self.assertIn("event: done", response.text)
        # The provider really was reached through the keyproxy (full path).
        self.assertEqual(len(provider.calls), 1)


if __name__ == "__main__":
    unittest.main()
