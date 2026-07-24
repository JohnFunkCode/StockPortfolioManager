"""Integration tests for POST /api/chat (SSE route).

Runs the FastAPI app through TestClient with CHAT_FAKE=1, so the real route,
schema validation, registry wiring, ChatService loop, and SSE encoding are all
exercised against the deterministic FakeChatClient — keyless and offline at the
LLM layer. DB-safety preamble mirrors test_api_smoke.py.
"""

import json
import os
import unittest
from pathlib import Path

# The fake LLM must be selected before get_services() is first called.
os.environ["CHAT_FAKE"] = "1"

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from quantcore.services.registry import get_services  # noqa: E402
from api.main import create_app  # noqa: E402

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

VALID_BODY = {"messages": [{"role": "user", "content": "How's INTC looking?"}]}


def parse_frames(body: str) -> list[tuple[str, str]]:
    """Split an SSE body into (event_type, data_json_string) tuples."""
    frames = []
    for chunk in body.split("\n\n"):
        if not chunk.strip():
            continue
        lines = chunk.split("\n")
        event = next(l[len("event: "):] for l in lines if l.startswith("event: "))
        data = next(l[len("data: "):] for l in lines if l.startswith("data: "))
        frames.append((event, data))
    return frames


class ChatApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        get_services.cache_clear()  # pick up CHAT_FAKE=1 even if another test built first
        cls.client = TestClient(create_app(), raise_server_exceptions=False)

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _AUTH_ENV_KEYS}
        for k in _AUTH_ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # -- happy path -------------------------------------------------------

    def test_stream_contract(self):
        resp = self.client.post("/api/chat", json=VALID_BODY)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            resp.headers["content-type"].startswith("text/event-stream"),
            resp.headers["content-type"],
        )
        frames = parse_frames(resp.text)
        kinds = [k for k, _ in frames]
        self.assertGreaterEqual(kinds.count("text"), 1)
        self.assertEqual(kinds.count("directive"), 1)
        self.assertEqual(kinds[-1], "done")
        directive_data = next(d for k, d in frames if k == "directive")
        self.assertIn('"signals"', directive_data)
        self.assertIn('"INTC"', directive_data)

    def test_directive_frame_carries_component_id(self):
        resp = self.client.post("/api/chat", json=VALID_BODY)
        directive_data = next(
            d for k, d in parse_frames(resp.text) if k == "directive"
        )
        self.assertIn('"component_id"', directive_data)
        payload = json.loads(directive_data)
        self.assertTrue(payload["component_id"])

    # -- interactions (the UI->model backchannel) ---------------------------

    def test_valid_interaction_round_trips_to_ack(self):
        body = {
            "messages": [{"role": "user", "content": "What about this strike?"}],
            "interactions": [
                {
                    "component_id": "abc123",
                    "component": "spread_payoff",
                    "action": "select_strike",
                    "payload": {"strike": 120.0},
                }
            ],
        }
        resp = self.client.post("/api/chat", json=body)
        self.assertEqual(resp.status_code, 200)
        frames = parse_frames(resp.text)
        kinds = [k for k, _ in frames]
        self.assertEqual(kinds[-1], "done")
        text = "".join(json.loads(d)["delta"] for k, d in frames if k == "text")
        self.assertIn("120", text)
        self.assertIn("selection", text.lower())

    def test_unknown_action_streams_error_frame(self):
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "interactions": [
                {
                    "component_id": "abc123",
                    "component": "spread_payoff",
                    "action": "explode",
                    "payload": {},
                }
            ],
        }
        resp = self.client.post("/api/chat", json=body)
        self.assertEqual(resp.status_code, 200)  # SSE stream carries the error
        frames = parse_frames(resp.text)
        self.assertEqual([k for k, _ in frames], ["error"])
        self.assertIn("explode", frames[0][1])

    def test_interaction_missing_component_id_422(self):
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "interactions": [
                {"component": "spread_payoff", "action": "select_strike", "payload": {}}
            ],
        }
        resp = self.client.post("/api/chat", json=body)
        self.assertEqual(resp.status_code, 422)

    def test_interactions_default_to_empty(self):
        # Old clients that never send the field keep working unchanged.
        resp = self.client.post("/api/chat", json=VALID_BODY)
        self.assertEqual(resp.status_code, 200)

    # -- validation --------------------------------------------------------

    def test_missing_messages_422(self):
        resp = self.client.post("/api/chat", json={})
        self.assertEqual(resp.status_code, 422)

    def test_bad_role_422(self):
        resp = self.client.post(
            "/api/chat", json={"messages": [{"role": "wizard", "content": "hi"}]}
        )
        self.assertEqual(resp.status_code, 422)

    def test_empty_content_422(self):
        resp = self.client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": ""}]}
        )
        self.assertEqual(resp.status_code, 422)

    def test_get_405(self):
        resp = self.client.get("/api/chat")
        self.assertEqual(resp.status_code, 405)

    # -- auth ---------------------------------------------------------------

    def test_jwt_enforced_when_secret_configured(self):
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        resp = self.client.post("/api/chat", json=VALID_BODY)
        self.assertEqual(resp.status_code, 401)

        token = jwt.encode({"sub": "thomas"}, SECRET, algorithm="HS256")
        resp = self.client.post(
            "/api/chat",
            json=VALID_BODY,
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(parse_frames(resp.text)[-1][0], "done")


if __name__ == "__main__":
    unittest.main()
