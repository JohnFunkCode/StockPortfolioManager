"""Per-principal inbound rate limiting for the REST tier (Security G2, #461).

One token bucket per authenticated identity (``Principal.owner``), held in this
process. The api runs up to ``max_instances = 3``, so the effective ceiling is at
most three times the setting; that is enough to stop a runaway loop or a leaked
token, which is the goal, and needs no shared store (plan D1). Every protected
route shares the one bucket (plan D2).

Modes (``QUANTCORE_RATE_LIMIT_MODE``, read at call time):

* ``shadow`` *(default)* — an empty bucket is logged and the request is served
  anyway. Nothing is refused, delayed or slowed (plan D4).
* ``enforce`` — an empty bucket is logged and answered 429 with ``Retry-After``.
* ``off`` — no counting at all.

An unrecognized mode falls back to ``shadow`` and logs an error naming the
variable, never its value: a typo must neither lock every user out nor silently
disarm the logging (plan D5). An unparseable number falls back to its default the
same way. The local principal (``AUTH_DISABLED``) is never limited (plan D6).

Each refusal (or would-be refusal) writes one ``SECURITY rate_limited`` line on
the ``quantcore.security`` logger, which the G6 ``security_events`` metric
counts; the wording is a contract (``tests/test_auth_alerting.py``). The line is
deduplicated to one per principal per minute so a runaway loop can't flood the
log. Plan: docs/proposals/rate-limiting-g2-plan.md.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Callable, Optional

from fastapi import Depends, HTTPException, Request, status

from .auth import Principal, require_principal

MODE_ENV = "QUANTCORE_RATE_LIMIT_MODE"
PER_MIN_ENV = "QUANTCORE_RATE_LIMIT_PER_MIN"
BURST_ENV = "QUANTCORE_RATE_LIMIT_BURST"

MODES = ("off", "shadow", "enforce")
DEFAULT_MODE = "shadow"
# Step 0 (2026-10-29, 30 days of prod): the busiest single source peaked at 24
# requests a minute and 24 in a 10-second window, so these leave 5x and 2.5x.
DEFAULT_PER_MIN = 120
DEFAULT_BURST = 60

LOG_INTERVAL_SECONDS = 60.0

logger = logging.getLogger(__name__)
_security_logger = logging.getLogger("quantcore.security")

# Settings are read on every request, so a bad value is reported once per process.
_reported: set[str] = set()


def _report_bad_setting(name: str, expected: str, fallback: object) -> None:
    if name not in _reported:
        _reported.add(name)
        logger.error("%s is not %s; using %s", name, expected, fallback)


def rate_limit_mode() -> str:
    raw = os.environ.get(MODE_ENV, "").strip().lower()
    if not raw:
        return DEFAULT_MODE
    if raw not in MODES:
        _report_bad_setting(MODE_ENV, "one of " + "/".join(MODES), DEFAULT_MODE)
        return DEFAULT_MODE
    return raw


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        _report_bad_setting(name, "a positive integer", default)
        return default
    return value


class PrincipalRateLimiter:
    """Thread-safe token bucket per identity.

    A full bucket holds ``burst`` requests and refills continuously at
    ``per_minute``/60 per second. The bucket map is size-capped like the
    keyproxy's ``SubRateLimiter``: when full, idle (fully refilled) buckets are
    pruned, and if none can be freed a *new* identity is refused (fail closed);
    existing identities keep their state.
    """

    def __init__(
        self,
        per_minute: int,
        burst: int,
        max_principals: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = float(burst)
        self._rate = per_minute / 60.0
        self._max_principals = max_principals
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = {}  # identity -> [tokens, last_refill]
        self._last_logged: dict[str, float] = {}

    def check(self, identity: str) -> float:
        """Take one token. Returns 0.0 when allowed, else seconds until one is free."""
        with self._lock:
            now = self._clock()
            bucket = self._buckets.get(identity)
            if bucket is None:
                if len(self._buckets) >= self._max_principals:
                    self._prune(now)
                if len(self._buckets) >= self._max_principals:
                    return 1.0 / self._rate
                bucket = [self._capacity, now]
                self._buckets[identity] = bucket
            else:
                tokens, last = bucket
                bucket[0] = min(self._capacity, tokens + (now - last) * self._rate)
                bucket[1] = now
            if bucket[0] >= 1.0:
                bucket[0] -= 1.0
                return 0.0
            return (1.0 - bucket[0]) / self._rate

    def should_log(self, identity: str) -> bool:
        """True at most once per ``LOG_INTERVAL_SECONDS`` per identity."""
        with self._lock:
            now = self._clock()
            last = self._last_logged.get(identity)
            if last is not None and now - last < LOG_INTERVAL_SECONDS:
                return False
            if len(self._last_logged) >= self._max_principals:
                self._last_logged = {
                    k: t for k, t in self._last_logged.items() if now - t < LOG_INTERVAL_SECONDS
                }
            self._last_logged[identity] = now
            return True

    def _prune(self, now: float) -> None:
        idle = [
            identity
            for identity, (tokens, last) in self._buckets.items()
            if tokens + (now - last) * self._rate >= self._capacity
        ]
        for identity in idle:
            del self._buckets[identity]


_limiter_lock = threading.Lock()
_limiter: Optional[PrincipalRateLimiter] = None
_limiter_config: Optional[tuple[int, int]] = None


def get_limiter() -> PrincipalRateLimiter:
    """The process's limiter, rebuilt if the configured numbers changed."""
    global _limiter, _limiter_config
    config = (_positive_int(PER_MIN_ENV, DEFAULT_PER_MIN), _positive_int(BURST_ENV, DEFAULT_BURST))
    with _limiter_lock:
        if _limiter is None or _limiter_config != config:
            _limiter = PrincipalRateLimiter(per_minute=config[0], burst=config[1])
            _limiter_config = config
        return _limiter


def reset_limiter() -> None:
    """Forget every bucket and every reported setting (tests)."""
    global _limiter, _limiter_config
    with _limiter_lock:
        _limiter = None
        _limiter_config = None
    _reported.clear()


def _route_of(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unknown"


def enforce_rate_limit(
    request: Request, principal: Principal = Depends(require_principal)
) -> None:
    """FastAPI dependency: count this request against the caller's bucket.

    Runs after ``require_principal`` (FastAPI resolves it once per request), so an
    unauthenticated request is a 401 and never reaches the bucket.
    """
    mode = rate_limit_mode()
    if mode == "off" or principal.is_local:
        return
    limiter = get_limiter()
    retry_after = limiter.check(principal.owner)
    if retry_after <= 0:
        return
    if limiter.should_log(principal.owner):
        _security_logger.warning(
            "SECURITY rate_limited mode=%s identity=%s at=%s",
            mode,
            principal.owner,
            _route_of(request),
        )
    if mode == "enforce":
        seconds = max(1, math.ceil(retry_after))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many requests. Try again in {seconds} seconds.",
            headers={"Retry-After": str(seconds)},
        )
