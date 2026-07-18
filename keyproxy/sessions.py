"""In-memory scoped session store (packet 2a).

A session is what an envelope redeems into — ``{plaintext key, scope,
verified sub, budget counters}`` held in process memory under a 128-bit
random id, alive for one user action's fan-out:

* **Sliding TTL** — each successful ``get`` extends the session by the
  effective TTL, which is ``min(KEYPROXY_SESSION_TTL, scope.budget.ttl)``
  (a scope may narrow but never widen the server default).
* **Hard lifetime cap** — ~900 s from creation regardless of activity.
* **Teardown discards plaintext** — explicit ``delete`` (best-effort from
  quantcore-api) or expiry both drop the key reference; a stale
  :class:`Session` object cannot read the key afterwards.

The store is thread-safe, size-capped (fail closed), and deliberately
in-memory only: session state holds plaintext keys and must never leave
process memory (architectural-standard Rule 4 deviation, documented in the
plan). Coherence relies on ``--max-instances=1``.

Logging policy: nothing here logs; every rejection raises
:class:`SessionError` with the same generic message, so a missing session,
an expired session, and a ``sub`` mismatch are indistinguishable to callers.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
import uuid
from typing import Callable, Optional

from keyproxy.scopes import BudgetTracker, Scope

DEFAULT_SESSION_TTL_SECONDS = 300.0
HARD_LIFETIME_CAP_SECONDS = 900.0

_REJECT = "invalid session"


class SessionError(Exception):
    """Any session rejection. The message is always the same generic text."""

    def __init__(self) -> None:
        super().__init__(_REJECT)


def _default_ttl() -> float:
    return float(
        os.environ.get("KEYPROXY_SESSION_TTL", str(DEFAULT_SESSION_TTL_SECONDS))
    )


class Session:
    """One redeemed envelope: identity, scope, budget, and the plaintext key."""

    def __init__(
        self,
        *,
        session_id: str,
        correlation_id: str,
        sub: str,
        provider: str,
        api_key: str,
        scope: Scope,
        created_at: float,
        ttl: float,
    ) -> None:
        self.session_id = session_id
        self.correlation_id = correlation_id
        self.sub = sub
        self.provider = provider
        self.scope = scope
        self.budget = BudgetTracker(scope)
        self.created_at = created_at
        self.ttl = ttl
        self._api_key: Optional[str] = api_key
        self._last_activity = created_at

    @property
    def api_key(self) -> str:
        key = self._api_key
        if key is None:
            raise SessionError()
        return key

    def expires_at(self) -> float:
        """Sliding expiry, clamped by the hard lifetime cap."""
        return min(
            self._last_activity + self.ttl,
            self.created_at + HARD_LIFETIME_CAP_SECONDS,
        )

    def _touch(self, now: float) -> None:
        self._last_activity = now

    def _teardown(self) -> None:
        self._api_key = None


class SessionStore:
    """Thread-safe, size-capped, TTL'd map of live sessions."""

    def __init__(
        self,
        ttl_seconds: Optional[float] = None,
        max_sessions: int = 1_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = _default_ttl() if ttl_seconds is None else float(ttl_seconds)
        self._max_sessions = max_sessions
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    def create(self, *, sub: str, provider: str, api_key: str, scope: Scope) -> Session:
        with self._lock:
            now = self._clock()
            self._sweep(now)
            if len(self._sessions) >= self._max_sessions:
                raise SessionError()
            session = Session(
                session_id=secrets.token_hex(16),
                correlation_id=str(uuid.uuid4()),
                sub=sub,
                provider=provider,
                api_key=api_key,
                scope=scope,
                created_at=now,
                ttl=min(self._ttl, float(scope.ttl)),
            )
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str, *, sub: str) -> Session:
        """Look up a live session for ``sub`` and slide its TTL."""
        with self._lock:
            now = self._clock()
            session = self._sessions.get(session_id)
            if session is None or now > session.expires_at():
                if session is not None:
                    session._teardown()
                    del self._sessions[session_id]
                raise SessionError()
            if not hmac.compare_digest(
                session.sub.encode("utf-8"), sub.encode("utf-8")
            ):
                raise SessionError()
            session._touch(now)
            return session

    def delete(self, session_id: str) -> None:
        """Best-effort teardown — idempotent, never raises for unknown ids."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                session._teardown()

    def _sweep(self, now: float) -> None:
        expired = [
            sid for sid, s in self._sessions.items() if now > s.expires_at()
        ]
        for sid in expired:
            self._sessions[sid]._teardown()
            del self._sessions[sid]
