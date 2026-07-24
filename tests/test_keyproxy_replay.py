"""Packet 2a tests: replay protection + rate limiting (keyproxy/replay.py).

Includes test_replay_race (external security review, 2026-07-16): concurrent
redemptions of the same jti racing the replay set — exactly one must win.
"""

import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from keyproxy.replay import JtiReplaySet, ReplayCapacityError, SubRateLimiter


class FakeClock:
    def __init__(self, start=1_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestJtiReplaySet(unittest.TestCase):
    def test_first_burn_wins_second_is_replay(self):
        replay = JtiReplaySet(ttl_seconds=120)
        self.assertTrue(replay.burn("jti-1"))
        self.assertFalse(replay.burn("jti-1"))
        self.assertTrue(replay.burn("jti-2"))

    def test_replay_race(self):
        # Two-plus concurrent redemptions of the same jti: exactly one wins.
        replay = JtiReplaySet(ttl_seconds=120)
        threads = 8
        for _ in range(50):
            jti = str(uuid.uuid4())
            barrier = threading.Barrier(threads)

            def attempt():
                barrier.wait()
                return replay.burn(jti)

            with ThreadPoolExecutor(max_workers=threads) as pool:
                results = list(pool.map(lambda _: attempt(), range(threads)))
            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), threads - 1)

    def test_entries_expire_after_ttl(self):
        # Forgetting a jti after the TTL is safe ONLY because the envelope's
        # iat skew check (60 s) rejects it long before the 120 s retention
        # ends — the set just needs to cover the redeemable window.
        clock = FakeClock()
        replay = JtiReplaySet(ttl_seconds=120, clock=clock)
        self.assertTrue(replay.burn("jti-1"))
        clock.advance(121)
        self.assertTrue(replay.burn("jti-1"))

    def test_capacity_fails_closed(self):
        clock = FakeClock()
        replay = JtiReplaySet(ttl_seconds=120, max_entries=2, clock=clock)
        self.assertTrue(replay.burn("a"))
        self.assertTrue(replay.burn("b"))
        with self.assertRaises(ReplayCapacityError):
            replay.burn("c")
        # Already-burned jtis still answer False at capacity (replay beats
        # capacity), and expiry frees space for new redemptions.
        self.assertFalse(replay.burn("a"))
        clock.advance(121)
        self.assertTrue(replay.burn("c"))

    def test_capacity_error_message_has_no_jti(self):
        replay = JtiReplaySet(ttl_seconds=120, max_entries=1)
        replay.burn("first-jti")
        with self.assertRaises(ReplayCapacityError) as ctx:
            replay.burn("second-secret-jti")
        self.assertNotIn("jti", str(ctx.exception).replace("replay", ""))
        self.assertNotIn("second-secret-jti", str(ctx.exception))

    def test_default_ttl_tracks_max_skew_env(self):
        clock = FakeClock()
        with patch.dict("os.environ", {"KEYPROXY_MAX_SKEW": "5"}):
            replay = JtiReplaySet(clock=clock)
        self.assertTrue(replay.burn("jti-1"))
        clock.advance(9)
        self.assertFalse(replay.burn("jti-1"))
        clock.advance(2)  # past 2 * skew
        self.assertTrue(replay.burn("jti-1"))


class TestSubRateLimiter(unittest.TestCase):
    def test_allows_up_to_capacity_then_denies(self):
        clock = FakeClock()
        limiter = SubRateLimiter(per_minute=3, clock=clock)
        self.assertTrue(limiter.allow("john"))
        self.assertTrue(limiter.allow("john"))
        self.assertTrue(limiter.allow("john"))
        self.assertFalse(limiter.allow("john"))

    def test_refills_over_time(self):
        clock = FakeClock()
        limiter = SubRateLimiter(per_minute=3, clock=clock)
        for _ in range(3):
            limiter.allow("john")
        self.assertFalse(limiter.allow("john"))
        clock.advance(20)  # 3/min -> one token per 20 s
        self.assertTrue(limiter.allow("john"))
        self.assertFalse(limiter.allow("john"))

    def test_subs_are_isolated(self):
        clock = FakeClock()
        limiter = SubRateLimiter(per_minute=1, clock=clock)
        self.assertTrue(limiter.allow("john"))
        self.assertFalse(limiter.allow("john"))
        self.assertTrue(limiter.allow("sager"))

    def test_full_map_fails_closed_for_new_subs(self):
        clock = FakeClock()
        limiter = SubRateLimiter(per_minute=2, max_subs=1, clock=clock)
        self.assertTrue(limiter.allow("john"))  # john's bucket is now drained by 1
        self.assertFalse(limiter.allow("sager"))  # no room; john not prunable
        self.assertTrue(limiter.allow("john"))  # existing subs keep working

    def test_idle_buckets_are_pruned_to_admit_new_subs(self):
        clock = FakeClock()
        limiter = SubRateLimiter(per_minute=2, max_subs=1, clock=clock)
        self.assertTrue(limiter.allow("john"))
        clock.advance(60)  # john fully refilled -> idle, prunable
        self.assertTrue(limiter.allow("sager"))

    def test_default_capacity_from_env(self):
        clock = FakeClock()
        with patch.dict("os.environ", {"KEYPROXY_RATE_LIMIT_PER_MIN": "2"}):
            limiter = SubRateLimiter(clock=clock)
        self.assertTrue(limiter.allow("john"))
        self.assertTrue(limiter.allow("john"))
        self.assertFalse(limiter.allow("john"))


if __name__ == "__main__":
    unittest.main()
