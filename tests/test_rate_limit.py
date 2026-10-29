"""Per-principal inbound rate limiting (Security G2, #461).

The bucket is tested on a fake clock. The modes run through a minimal FastAPI app
wired exactly like ``api.main`` (``require_principal`` then ``enforce_rate_limit``
on the router), so they need no database; a last case builds the real app with
``ensure_schema`` patched out and checks every protected route carries the
limiter and ``/api/health`` doesn't. Plan: docs/proposals/rate-limiting-g2-plan.md.
"""

import os
import unittest
from unittest.mock import patch

import jwt
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from api import rate_limit
from api.auth import require_principal
from api.errors import register_exception_handlers
from api.rate_limit import (
    BURST_ENV,
    MODE_ENV,
    PER_MIN_ENV,
    PrincipalRateLimiter,
    enforce_rate_limit,
    rate_limit_mode,
)

SECRET = "test-secret-key-at-least-32-bytes-long-000"

_ENV_KEYS = (
    "AUTH_DISABLED",
    "QUANTCORE_JWT_SECRET",
    "QUANTCORE_JWT_PUBLIC_KEY",
    MODE_ENV,
    PER_MIN_ENV,
    BURST_ENV,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _token(sub: str) -> str:
    return jwt.encode({"sub": sub}, SECRET, algorithm="HS256")


def _auth(sub: str = "alice") -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


def _make_app() -> FastAPI:
    router = APIRouter()

    @router.get("/api/things/{ticker}")
    def things(ticker: str) -> dict:
        return {"ticker": ticker}

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        router, dependencies=[Depends(require_principal), Depends(enforce_rate_limit)]
    )
    return app


class EnvTestCase(unittest.TestCase):
    """Isolates the auth and rate-limit env, and the process-wide limiter."""

    env: dict = {}

    def setUp(self) -> None:
        self._saved = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
        os.environ.update(self.env)
        rate_limit.reset_limiter()
        self.addCleanup(rate_limit.reset_limiter)

    def tearDown(self) -> None:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
            if self._saved[k] is not None:
                os.environ[k] = self._saved[k]


class BucketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.limiter = PrincipalRateLimiter(per_minute=60, burst=3, clock=self.clock)

    def _wait(self, identity: str, limiter=None) -> float:
        return (limiter or self.limiter).check(identity).retry_after

    def test_burst_then_refusal(self):
        self.assertEqual([self._wait("a") for _ in range(3)], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(self._wait("a"), 1.0)  # 60/min = 1 token/s

    def test_refills_at_the_configured_rate(self):
        for _ in range(3):
            self._wait("a")
        self.clock.now += 0.5
        self.assertAlmostEqual(self._wait("a"), 0.5)
        self.clock.now += 0.5
        self.assertEqual(self._wait("a"), 0.0)

    def test_refill_never_exceeds_the_burst(self):
        self._wait("a")
        self.clock.now += 3600
        self.assertEqual([self._wait("a") for _ in range(3)], [0.0, 0.0, 0.0])
        self.assertGreater(self._wait("a"), 0.0)

    def test_identities_have_separate_buckets(self):
        for _ in range(3):
            self._wait("a")
        self.assertGreater(self._wait("a"), 0.0)
        self.assertEqual(self._wait("b"), 0.0)

    def test_full_map_prunes_idle_buckets(self):
        limiter = PrincipalRateLimiter(per_minute=60, burst=3, max_principals=2, clock=self.clock)
        self._wait("a", limiter)
        self._wait("b", limiter)
        self.clock.now += 10  # both refill to full, so both are idle
        self.assertEqual(self._wait("c", limiter), 0.0)

    def test_full_map_refuses_a_new_identity_but_keeps_existing_ones(self):
        limiter = PrincipalRateLimiter(per_minute=60, burst=3, max_principals=2, clock=self.clock)
        self._wait("a", limiter)
        self._wait("b", limiter)
        self.assertGreater(self._wait("c", limiter), 0.0)  # nothing idle to prune: fail closed
        self.assertEqual(self._wait("a", limiter), 0.0)

    def test_admitted_requests_never_ask_to_log(self):
        self.assertEqual([self.limiter.check("a").log for _ in range(3)], [False] * 3)

    def test_log_dedup_is_once_per_identity_per_minute(self):
        for identity in ("a", "b"):
            for _ in range(3):
                self.limiter.check(identity)
        self.assertTrue(self.limiter.check("a").log)
        self.assertFalse(self.limiter.check("a").log)
        self.assertTrue(self.limiter.check("b").log)
        self.clock.now += 60  # refills too, so drain again before the next refusal
        for _ in range(3):
            self.limiter.check("a")
        self.assertTrue(self.limiter.check("a").log)


class CapacityBoundTests(unittest.TestCase):
    """Every piece of state stays within ``max_principals`` (#489 review)."""

    def setUp(self) -> None:
        self.clock = FakeClock()  # fixed: nothing ages, nothing refills
        self.limiter = PrincipalRateLimiter(
            per_minute=60, burst=1, max_principals=2, clock=self.clock
        )

    def _flood(self, count: int, start: int = 0) -> list:
        """Two requests per new identity, as the review's probe did."""
        decisions = []
        for i in range(start, start + count):
            decisions += [self.limiter.check(f"id{i}"), self.limiter.check(f"id{i}")]
        return decisions

    def test_overflowing_identities_leave_no_state_behind(self):
        self._flood(100)
        self.assertEqual(len(self.limiter._buckets), 2)
        self.assertEqual(
            {name for name in vars(self.limiter) if isinstance(vars(self.limiter)[name], dict)},
            {"_buckets"},
        )

    def test_overflow_refusals_log_once_per_interval_in_total(self):
        decisions = self._flood(100)
        # id0 and id1 each log their own refusal; the 98 overflow identities share one.
        self.assertEqual(sum(d.log for d in decisions), 3)

    def test_the_overflow_log_reopens_after_the_interval(self):
        # Slow refill and a burst of 2, so the two held buckets never go idle and
        # the map stays full while the clock moves a whole interval.
        self.limiter = PrincipalRateLimiter(
            per_minute=1, burst=2, max_principals=2, clock=self.clock
        )
        self.assertEqual(sum(d.log for d in self._flood(100)), 1)
        self.clock.now += rate_limit.LOG_INTERVAL_SECONDS - 0.01
        self.assertEqual(sum(d.log for d in self._flood(50, start=100)), 0)
        self.clock.now += 0.01
        self.assertEqual(sum(d.log for d in self._flood(50, start=150)), 1)
        self.assertEqual(len(self.limiter._buckets), 2)

    def test_overflow_refusals_are_still_refused(self):
        decisions = self._flood(100)
        self.assertTrue(all(d.retry_after > 0 for d in decisions[4:]))

    def test_a_prune_that_frees_nothing_backs_off(self):
        self._flood(2)
        with patch.object(self.limiter, "_prune", wraps=self.limiter._prune) as prune:
            self._flood(50, start=2)
            self.assertEqual(prune.call_count, 1)
            self.clock.now += PrincipalRateLimiter.PRUNE_BACKOFF_SECONDS
            self._flood(1, start=52)
            self.assertEqual(prune.call_count, 2)

    def test_at_production_scale_the_map_stays_at_the_cap(self):
        limiter = PrincipalRateLimiter(per_minute=60, burst=1, clock=self.clock)
        for i in range(10_020):
            limiter.check(f"id{i}")
            limiter.check(f"id{i}")
        self.assertEqual(len(limiter._buckets), 10_000)


class SettingsTests(EnvTestCase):
    def test_default_mode_is_shadow(self):
        self.assertEqual(rate_limit_mode(), "shadow")

    def test_each_mode_is_read_case_insensitively(self):
        for raw, mode in (("off", "off"), ("ENFORCE", "enforce"), (" shadow ", "shadow")):
            with self.subTest(raw):
                os.environ[MODE_ENV] = raw
                self.assertEqual(rate_limit_mode(), mode)

    def test_unknown_mode_falls_back_to_shadow_and_names_only_the_variable(self):
        os.environ[MODE_ENV] = "enforec-typo"
        with self.assertLogs("api.rate_limit", level="ERROR") as logs:
            self.assertEqual(rate_limit_mode(), "shadow")
        self.assertIn(MODE_ENV, logs.output[0])
        self.assertNotIn("enforec-typo", logs.output[0])

    def test_a_bad_setting_is_reported_once(self):
        os.environ[MODE_ENV] = "nope"
        with self.assertLogs("api.rate_limit", level="ERROR") as logs:
            rate_limit_mode()
            rate_limit_mode()
        self.assertEqual(len(logs.output), 1)

    def test_unparseable_numbers_fall_back_to_defaults(self):
        for raw in ("abc", "0", "-5", "1.5"):
            with self.subTest(raw):
                rate_limit.reset_limiter()
                os.environ[PER_MIN_ENV] = raw
                os.environ[BURST_ENV] = raw
                with self.assertLogs("api.rate_limit", level="ERROR") as logs:
                    limiter = rate_limit.get_limiter()
                self.assertEqual(limiter._capacity, rate_limit.DEFAULT_BURST)
                self.assertEqual(limiter._rate, rate_limit.DEFAULT_PER_MIN / 60.0)
                self.assertEqual(
                    logs.output,
                    [
                        f"ERROR:api.rate_limit:{PER_MIN_ENV} is not a positive integer; "
                        f"using {rate_limit.DEFAULT_PER_MIN}",
                        f"ERROR:api.rate_limit:{BURST_ENV} is not a positive integer; "
                        f"using {rate_limit.DEFAULT_BURST}",
                    ],
                )

    def test_changed_numbers_rebuild_the_limiter(self):
        first = rate_limit.get_limiter()
        self.assertIs(rate_limit.get_limiter(), first)
        os.environ[BURST_ENV] = "7"
        self.assertIsNot(rate_limit.get_limiter(), first)


class ModeTests(EnvTestCase):
    env = {"QUANTCORE_JWT_SECRET": SECRET, BURST_ENV: "2", PER_MIN_ENV: "60"}

    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(_make_app())

    def _get(self, sub: str = "alice"):
        return self.client.get("/api/things/TST", headers=_auth(sub))

    def test_shadow_logs_once_and_serves_everything(self):
        os.environ[MODE_ENV] = "shadow"
        with self.assertLogs("quantcore.security", level="WARNING") as logs:
            statuses = [self._get().status_code for _ in range(5)]
        self.assertEqual(statuses, [200] * 5)
        self.assertEqual(
            logs.output,
            ["WARNING:quantcore.security:SECURITY rate_limited mode=shadow identity=alice "
             "at=/api/things/{ticker}"],
        )

    def test_enforce_answers_429_with_retry_after(self):
        os.environ[MODE_ENV] = "enforce"
        self.assertEqual([self._get().status_code for _ in range(2)], [200, 200])
        with self.assertLogs("quantcore.security", level="WARNING") as logs:
            r = self._get()
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers["Retry-After"], "1")
        self.assertEqual(r.json()["message"], "Too many requests. Try again in 1 seconds.")
        self.assertIn("SECURITY rate_limited mode=enforce identity=alice", logs.output[0])

    def test_the_429_body_names_no_one_and_no_limit(self):
        os.environ[MODE_ENV] = "enforce"
        os.environ[BURST_ENV] = "1"
        os.environ[PER_MIN_ENV] = "7"
        rate_limit.reset_limiter()
        self._get()
        with self.assertLogs("quantcore.security", level="WARNING"):
            body = self._get().text
        self.assertNotIn("alice", body)
        self.assertIn("Try again in 9 seconds", body)  # 60/7 s for one token
        self.assertNotIn("7", body)

    def test_enforce_limits_each_identity_separately(self):
        os.environ[MODE_ENV] = "enforce"
        for _ in range(2):
            self._get("alice")
        with self.assertLogs("quantcore.security", level="WARNING"):
            self.assertEqual(self._get("alice").status_code, 429)
        self.assertEqual(self._get("bob").status_code, 200)

    def test_off_counts_nothing(self):
        os.environ[MODE_ENV] = "off"
        with self.assertNoLogs("quantcore.security", level="WARNING"):
            statuses = [self._get().status_code for _ in range(5)]
        self.assertEqual(statuses, [200] * 5)

    def test_missing_token_is_401_before_the_bucket(self):
        os.environ[MODE_ENV] = "enforce"
        for _ in range(3):
            with self.assertLogs("quantcore.security", level="WARNING") as logs:
                self.assertEqual(self.client.get("/api/things/TST").status_code, 401)
            self.assertNotIn("rate_limited", " ".join(logs.output))


class LocalPrincipalTests(EnvTestCase):
    env = {"AUTH_DISABLED": "1", MODE_ENV: "enforce", BURST_ENV: "1"}

    def test_the_local_principal_is_never_limited(self):
        client = TestClient(_make_app())
        with self.assertNoLogs("quantcore.security", level="WARNING"):
            statuses = [client.get("/api/things/TST").status_code for _ in range(5)]
        self.assertEqual(statuses, [200] * 5)


class WiringTests(EnvTestCase):
    """The real app: every protected operation counts, the health check doesn't.

    Behavioural on purpose: this FastAPI version keeps included routers as lazy
    containers whose include-time dependencies aren't on the routes' dependants,
    so walking them can't see the limiter. Instead the limiter is made to refuse
    everyone and every operation in the OpenAPI schema must answer 429 -- which
    also proves it runs before route-level dependencies such as require_owner.
    """

    env = {"QUANTCORE_JWT_SECRET": SECRET, MODE_ENV: "enforce"}

    class _RefuseAll:
        def check(self, identity: str) -> rate_limit.Decision:
            return rate_limit.Decision(5.0, False)

    def test_every_protected_operation_is_limited_and_health_is_not(self):
        import re

        # api.main builds its app at import, so the import goes inside the patch too.
        with patch("quantcore.db.ensure_schema"):
            from api.main import create_app

            app = create_app()
        operations = [
            (method.upper(), path)
            for path, item in app.openapi()["paths"].items()
            for method in item
        ]
        self.assertGreater(len(operations), 50)
        client = TestClient(app)
        with patch.object(rate_limit, "get_limiter", return_value=self._RefuseAll()):
            for method, path in operations:
                url = re.sub(r"\{[^}]+\}", "1", path)
                with self.subTest(f"{method} {path}"):
                    if path == "/api/health":
                        r = client.request(method, url)
                        self.assertNotEqual(r.status_code, 429)
                    else:
                        r = client.request(method, url, headers=_auth())
                        self.assertEqual(r.status_code, 429)
                        self.assertEqual(r.headers["Retry-After"], "5")


if __name__ == "__main__":
    unittest.main()
