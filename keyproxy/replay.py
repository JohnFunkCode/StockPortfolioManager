"""Replay protection + per-sub redemption rate limiting (packet 2a).

Two small in-memory, thread-safe structures for the trust boundary:

* :class:`JtiReplaySet` — the burned-``jti`` set that makes every envelope
  redeemable exactly once. Entries are TTL'd: a ``jti`` only needs to be
  remembered while its envelope could still pass the ``iat`` skew check
  (``|now - iat| <= KEYPROXY_MAX_SKEW``), so the default retention is twice
  the skew window.
* :class:`SubRateLimiter` — a per-``sub`` token bucket counting **envelope
  redemptions (user actions), not fan-out calls** — an 8-turn chat costs 1.

Both are size-capped and fail closed: when a cap is hit, new work is refused
rather than old security state evicted. Coherence across requests relies on
the single-instance deployment (``--max-instances=1``, decision #5).

Logging policy: nothing in this module logs; error messages are constant and
carry no ``jti``/``sub`` values.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

DEFAULT_MAX_SKEW_SECONDS = 60
DEFAULT_RATE_LIMIT_PER_MIN = 30


class ReplayCapacityError(RuntimeError):
    """The replay set is full — refuse new redemptions (fail closed)."""

    def __init__(self) -> None:
        super().__init__("replay protection at capacity")


def _default_jti_ttl() -> float:
    skew = int(os.environ.get("KEYPROXY_MAX_SKEW", str(DEFAULT_MAX_SKEW_SECONDS)))
    return 2.0 * skew


class JtiReplaySet:
    """TTL'd set of burned envelope ``jti`` values.

    ``burn`` is the single atomic operation: it returns ``True`` when the
    ``jti`` was fresh (and is now burned) and ``False`` when it was already
    burned — under concurrency, exactly one caller wins.

    Entries share a uniform TTL, so insertion order == expiry order and
    pruning pops from the front of the (insertion-ordered) dict.
    """

    def __init__(
        self,
        ttl_seconds: Optional[float] = None,
        max_entries: int = 100_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = _default_jti_ttl() if ttl_seconds is None else float(ttl_seconds)
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._burned: dict[str, float] = {}  # jti -> expiry

    def burn(self, jti: str) -> bool:
        with self._lock:
            now = self._clock()
            self._prune(now)
            if jti in self._burned:
                return False
            if len(self._burned) >= self._max_entries:
                raise ReplayCapacityError()
            self._burned[jti] = now + self._ttl
            return True

    def _prune(self, now: float) -> None:
        while self._burned:
            oldest = next(iter(self._burned))
            if self._burned[oldest] > now:
                break
            del self._burned[oldest]


class SubRateLimiter:
    """Per-``sub`` token bucket over envelope redemptions.

    Capacity and refill rate both come from ``per_minute`` (default
    ``KEYPROXY_RATE_LIMIT_PER_MIN``, 30): a full bucket holds ``per_minute``
    redemptions and refills continuously at ``per_minute``/60 per second.

    The bucket map is size-capped; when full, idle (fully refilled) buckets
    are pruned, and if none can be freed a *new* sub is refused (fail
    closed) — existing subs keep their state.
    """

    def __init__(
        self,
        per_minute: Optional[int] = None,
        max_subs: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if per_minute is None:
            per_minute = int(
                os.environ.get(
                    "KEYPROXY_RATE_LIMIT_PER_MIN", str(DEFAULT_RATE_LIMIT_PER_MIN)
                )
            )
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0
        self._max_subs = max_subs
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = {}  # sub -> [tokens, last_refill]

    def allow(self, sub: str) -> bool:
        with self._lock:
            now = self._clock()
            bucket = self._buckets.get(sub)
            if bucket is None:
                if len(self._buckets) >= self._max_subs:
                    self._prune(now)
                if len(self._buckets) >= self._max_subs:
                    return False
                bucket = [self._capacity, now]
                self._buckets[sub] = bucket
            else:
                tokens, last = bucket
                bucket[0] = min(self._capacity, tokens + (now - last) * self._rate)
                bucket[1] = now
            if bucket[0] >= 1.0:
                bucket[0] -= 1.0
                return True
            return False

    def _prune(self, now: float) -> None:
        idle = [
            sub
            for sub, (tokens, last) in self._buckets.items()
            if tokens + (now - last) * self._rate >= self._capacity
        ]
        for sub in idle:
            del self._buckets[sub]
