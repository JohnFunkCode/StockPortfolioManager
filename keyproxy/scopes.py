"""Scope v1 validation, canonical hashing, and budget counters (packet 2a).

The scope is the cleartext object accompanying every envelope; its canonical
SHA-256 rides inside the GCM-authenticated AAD (``aad.scope_hash``), so the
proxy can read it while tampering breaks decryption. Canonicalization
delegates to :mod:`keyproxy.crypto` — the same functions pinned by the
Phase 1 cross-runtime vectors — so the scope a browser hashed is the scope
this module hashes, byte for byte.

Validation is fail closed: unknown top-level or budget keys, any version
other than 1, non-integer budgets, and non-canonicalizable content are all
rejected. ``budget.max_tokens`` may narrow but never widen the server-side
ceiling (``KEYPROXY_MAX_SESSION_TOKENS``); minting a scope above it — or a
session's cumulative usage crossing it — yields the system-threshold
rejection copy verbatim.

Logging policy: error messages name fields, never values — no scope
contents, subs, or key material appear in any exception text.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional

from keyproxy import crypto

SCOPE_VERSION = 1
DEFAULT_MAX_SESSION_TOKENS = 250_000

# Verbatim rejection copy required by the plan ("Scope schema (v1)"): the cap
# is a breach-exposure ceiling, not spend throttling, and user testing tunes
# the default through exactly this feedback loop.
TOKEN_BUDGET_MESSAGE = (
    "Session token budget rejected: you hit a system-imposed threshold. "
    "Please contact the development team to have it raised."
)

_SCOPE_KEYS = frozenset({"v", "provider", "action", "params", "budget"})
_BUDGET_KEYS = frozenset({"max_calls", "max_mutations", "max_tokens", "ttl"})
_BUDGET_REQUIRED = frozenset({"max_calls", "max_mutations", "ttl"})

# Re-exported so the rest of the keyproxy hashes scopes through this module.
canonical_scope_json = crypto.canonical_json
compute_scope_hash = crypto.compute_scope_hash


class ScopeError(ValueError):
    """A scope failed validation. Messages name fields, never values."""


class BudgetExceededError(Exception):
    """A session budget line was crossed — the session must be killed."""


def _max_session_tokens() -> int:
    return int(
        os.environ.get("KEYPROXY_MAX_SESSION_TOKENS", str(DEFAULT_MAX_SESSION_TOKENS))
    )


def _require_positive_int(budget: Mapping, field: str, minimum: int) -> int:
    value = budget[field]
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ScopeError(f"invalid scope budget field: {field}")
    return value


@dataclass(frozen=True)
class Scope:
    """A validated v1 scope. ``raw`` is the exact dict the hash covers."""

    provider: str
    action: str
    params: Mapping
    max_calls: int
    max_mutations: int
    max_tokens: int
    ttl: int
    raw: Mapping

    @property
    def scope_hash(self) -> str:
        return compute_scope_hash(dict(self.raw))


def validate_scope(
    scope_obj: object, *, max_session_tokens: Optional[int] = None
) -> Scope:
    """Validate a wire scope object into a :class:`Scope` (fail closed)."""
    if max_session_tokens is None:
        max_session_tokens = _max_session_tokens()

    if not isinstance(scope_obj, dict) or set(scope_obj.keys()) != _SCOPE_KEYS:
        raise ScopeError("invalid scope structure")
    version = scope_obj["v"]
    if isinstance(version, bool) or version != SCOPE_VERSION:
        raise ScopeError("unsupported scope version")
    provider = scope_obj["provider"]
    action = scope_obj["action"]
    if not isinstance(provider, str) or not provider:
        raise ScopeError("invalid scope field: provider")
    if not isinstance(action, str) or not action:
        raise ScopeError("invalid scope field: action")
    params = scope_obj["params"]
    if not isinstance(params, dict):
        raise ScopeError("invalid scope field: params")

    budget = scope_obj["budget"]
    if not isinstance(budget, dict):
        raise ScopeError("invalid scope field: budget")
    keys = set(budget.keys())
    if not _BUDGET_REQUIRED <= keys or not keys <= _BUDGET_KEYS:
        raise ScopeError("invalid scope budget structure")
    max_calls = _require_positive_int(budget, "max_calls", 1)
    max_mutations = _require_positive_int(budget, "max_mutations", 0)
    ttl = _require_positive_int(budget, "ttl", 1)
    if "max_tokens" in budget:
        max_tokens = _require_positive_int(budget, "max_tokens", 1)
        if max_tokens > max_session_tokens:
            raise ScopeError(TOKEN_BUDGET_MESSAGE)
    else:
        # Absent means "no narrower than the server allows" — still bounded.
        max_tokens = max_session_tokens

    try:
        canonical_scope_json(scope_obj)
    except crypto.EnvelopeError:
        raise ScopeError("scope is not canonicalizable") from None

    return Scope(
        provider=provider,
        action=action,
        params=MappingProxyType(dict(params)),
        max_calls=max_calls,
        max_mutations=max_mutations,
        max_tokens=max_tokens,
        ttl=ttl,
        raw=MappingProxyType(dict(scope_obj)),
    )


class BudgetTracker:
    """Thread-safe budget counters for one session.

    Calls and mutations are charged *before* the key is attached; token usage
    is charged *after* each call from provider-reported usage (the plan's
    "sums the provider-reported usage ... kills the session when the total
    crosses the line"). Once any line is crossed the tracker latches
    exhausted and every subsequent charge fails — the session is dead.
    """

    def __init__(self, scope: Scope) -> None:
        self._scope = scope
        self._lock = threading.Lock()
        self._calls = 0
        self._mutations = 0
        self._tokens = 0
        self._exhausted: Optional[str] = None

    @property
    def calls_used(self) -> int:
        return self._calls

    @property
    def mutations_used(self) -> int:
        return self._mutations

    @property
    def tokens_used(self) -> int:
        return self._tokens

    def _check_latch(self) -> None:
        if self._exhausted is not None:
            raise BudgetExceededError(self._exhausted)

    def _exhaust(self, message: str) -> None:
        self._exhausted = message
        raise BudgetExceededError(message)

    def charge_call(self) -> None:
        with self._lock:
            self._check_latch()
            if self._calls >= self._scope.max_calls:
                self._exhaust("session call budget exhausted")
            self._calls += 1

    def charge_mutation(self) -> None:
        with self._lock:
            self._check_latch()
            if self._mutations >= self._scope.max_mutations:
                self._exhaust("session mutation budget exhausted")
            self._mutations += 1

    def charge_tokens(self, count: int) -> None:
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("token count must be a non-negative integer")
        with self._lock:
            self._check_latch()
            self._tokens += count
            if self._tokens > self._scope.max_tokens:
                self._exhaust(TOKEN_BUDGET_MESSAGE)
