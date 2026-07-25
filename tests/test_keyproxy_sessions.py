"""Packet 2a tests: in-memory scoped session store (keyproxy/sessions.py).

Covers the plan's session lifecycle: sliding TTL, the ~900 s hard lifetime
cap, teardown discarding the plaintext key, and the generic "invalid
session" rejection that makes missing / expired / wrong-sub lookups
indistinguishable.
"""

import unittest
from unittest.mock import patch

from keyproxy.scopes import validate_scope
from keyproxy.sessions import (
    DEFAULT_SESSION_TTL_SECONDS,
    HARD_LIFETIME_CAP_SECONDS,
    SessionError,
    SessionStore,
)


class FakeClock:
    def __init__(self, start=1_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_scope(ttl=300, max_calls=20):
    return validate_scope(
        {
            "v": 1,
            "provider": "anthropic",
            "action": "chat.turn",
            "params": {},
            "budget": {"max_calls": max_calls, "max_mutations": 0, "ttl": ttl},
        },
        max_session_tokens=250_000,
    )


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = SessionStore(ttl_seconds=300, clock=self.clock)

    def create(self, sub="john", ttl=300):
        return self.store.create(
            sub=sub,
            provider="anthropic",
            api_key="sk-ant-test-key",
            scope=make_scope(ttl=ttl),
        )

    def test_create_issues_random_ids_and_holds_key(self):
        a = self.create()
        b = self.create()
        self.assertRegex(a.session_id, r"^[0-9a-f]{32}$")  # 128-bit random
        self.assertNotEqual(a.session_id, b.session_id)
        self.assertNotEqual(a.correlation_id, b.correlation_id)
        self.assertEqual(a.api_key, "sk-ant-test-key")
        self.assertEqual(a.sub, "john")

    def test_get_returns_live_session(self):
        session = self.create()
        self.assertIs(self.store.get(session.session_id, sub="john"), session)

    def test_unknown_id_rejected_generically(self):
        with self.assertRaises(SessionError) as ctx:
            self.store.get("f" * 32, sub="john")
        self.assertEqual(str(ctx.exception), "invalid session")

    def test_sub_mismatch_is_indistinguishable_from_missing(self):
        session = self.create(sub="john")
        with self.assertRaises(SessionError) as wrong_sub:
            self.store.get(session.session_id, sub="sager")
        with self.assertRaises(SessionError) as missing:
            self.store.get("f" * 32, sub="john")
        self.assertEqual(str(wrong_sub.exception), str(missing.exception))
        # The session itself is unharmed — the right sub still gets it.
        self.store.get(session.session_id, sub="john")

    def test_ttl_slides_on_activity(self):
        session = self.create()
        for _ in range(3):
            self.clock.advance(250)  # inside the 300 s window each time
            self.store.get(session.session_id, sub="john")
        # 750 s elapsed — far past the original expiry, alive via sliding.

    def test_expires_without_activity(self):
        session = self.create()
        self.clock.advance(301)
        with self.assertRaises(SessionError):
            self.store.get(session.session_id, sub="john")

    def test_hard_lifetime_cap_beats_sliding_ttl(self):
        session = self.create()
        for _ in range(4):
            self.clock.advance(200)
            self.store.get(session.session_id, sub="john")  # alive through 800 s
        self.clock.advance(200)  # t = 1000 s > 900 s cap despite recent touch
        with self.assertRaises(SessionError):
            self.store.get(session.session_id, sub="john")
        self.assertEqual(HARD_LIFETIME_CAP_SECONDS, 900.0)

    def test_scope_ttl_narrows_server_ttl(self):
        session = self.create(ttl=60)
        self.clock.advance(61)
        with self.assertRaises(SessionError):
            self.store.get(session.session_id, sub="john")

    def test_scope_ttl_cannot_widen_server_ttl(self):
        session = self.create(ttl=86_400)
        self.clock.advance(301)  # server default 300 s still governs
        with self.assertRaises(SessionError):
            self.store.get(session.session_id, sub="john")

    def test_expiry_tears_down_key(self):
        session = self.create()
        self.clock.advance(301)
        with self.assertRaises(SessionError):
            self.store.get(session.session_id, sub="john")
        with self.assertRaises(SessionError):
            session.api_key

    def test_delete_is_idempotent_and_discards_key(self):
        session = self.create()
        self.store.delete(session.session_id)
        self.store.delete(session.session_id)  # no raise on repeat
        self.store.delete("f" * 32)  # nor on unknown ids
        with self.assertRaises(SessionError):
            session.api_key
        with self.assertRaises(SessionError):
            self.store.get(session.session_id, sub="john")

    def test_capacity_fails_closed_and_sweep_frees_space(self):
        store = SessionStore(ttl_seconds=300, max_sessions=2, clock=self.clock)
        scope = make_scope()
        for _ in range(2):
            store.create(
                sub="john", provider="anthropic", api_key="k", scope=scope
            )
        with self.assertRaises(SessionError):
            store.create(sub="john", provider="anthropic", api_key="k", scope=scope)
        self.clock.advance(301)  # both expire; create sweeps them out
        store.create(sub="john", provider="anthropic", api_key="k", scope=scope)

    def test_default_ttl_from_env(self):
        with patch.dict("os.environ", {"KEYPROXY_SESSION_TTL": "10"}):
            store = SessionStore(clock=self.clock)
        session = store.create(
            sub="john", provider="anthropic", api_key="k", scope=make_scope()
        )
        self.clock.advance(11)
        with self.assertRaises(SessionError):
            store.get(session.session_id, sub="john")
        self.assertEqual(DEFAULT_SESSION_TTL_SECONDS, 300.0)


if __name__ == "__main__":
    unittest.main()
