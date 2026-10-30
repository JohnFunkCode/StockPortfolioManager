"""The per-user daily Sidekick token budget (Security G2, #461).

Plain helpers and one small class, not a service: ChatService and
SettingsService both need them, and service modules never import each other,
so the registry injects what each needs. SQL lives in ChatUsageRepository.
Plan: docs/proposals/rate-limiting-g2-plan.md, D3 and D8.

Admission is an atomic reservation per model call. Before each call, ChatService
reserves that call's worst case: its output cap plus a deliberately high input
estimate. The repository adds it only if stored usage plus outstanding
reservations plus this one fits the budget, so concurrent turns, on any api
instance, can't jointly overshoot. After the call the reservation is released
and the provider-reported usage added. A reservation never reconciled (the call
may have reached the provider before failing) stays charged until the day
rolls over; only a call the keyproxy refused before reaching the provider is
released.

Modes (``SIDEKICK_BUDGET_MODE``, read at call time):

* ``shadow`` *(default)* — reservations are recorded, a would-be refusal is
  logged, nothing is refused. A database failure is logged and the turn carries
  on (plan D4: shadow throttles nothing).
* ``enforce`` — an over-budget call is refused, and a database failure stops
  the turn with a message (plan D8, fail closed).
* ``off`` — no accounting at all.

An unrecognized mode falls back to ``shadow`` and an unparseable ceiling to its
default, each reported once, naming only the variable.
"""

from __future__ import annotations

import datetime
import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional

from quantcore.analytics.market_time import ET

MODE_ENV = "SIDEKICK_BUDGET_MODE"
CEILING_ENV = "SIDEKICK_DAILY_TOKEN_BUDGET"

MODES = ("off", "shadow", "enforce")
DEFAULT_MODE = "shadow"
# A shadow threshold, not yet a limit: step 0 (2026-10-29) saw one user's
# busiest day at ~112k tokens, too little use to set a budget from. The soak
# sets the enforced value (plan step 3).
DEFAULT_CEILING = 2_000_000

# The output cap every Sidekick model call is made with (thinking included), so
# a call's output can never exceed its reservation. The registry hands the same
# constant to the client it builds.
MAX_OUTPUT_TOKENS = 16_384
# Input is estimated from characters. Tool results are number-heavy JSON, which
# tokenizes denser than prose, so 2 characters per token errs high.
CHARS_PER_TOKEN = 2

EXHAUSTED_MESSAGE = (
    "You've used today's Sidekick budget ({used:,} of {budget:,} tokens). "
    "It resets at midnight ET. If you need more than the system limit, "
    "please contact the development team."
)
UNAVAILABLE_MESSAGE = (
    "Sidekick can't check your usage budget right now, so this turn was "
    "stopped. Please try again in a few minutes."
)

logger = logging.getLogger(__name__)
_security_logger = logging.getLogger("quantcore.security")

_reported: set[str] = set()


def _report_bad_setting(name: str, expected: str, fallback: object) -> None:
    if name not in _reported:
        _reported.add(name)
        logger.error("%s is not %s; using %s", name, expected, fallback)


def budget_mode() -> str:
    raw = os.environ.get(MODE_ENV, "").strip().lower()
    if not raw:
        return DEFAULT_MODE
    if raw not in MODES:
        _report_bad_setting(MODE_ENV, "one of " + "/".join(MODES), DEFAULT_MODE)
        return DEFAULT_MODE
    return raw


def ceiling() -> int:
    """The server-wide daily token budget per user; no user cap may exceed it."""
    raw = os.environ.get(CEILING_ENV, "").strip()
    if not raw:
        return DEFAULT_CEILING
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        _report_bad_setting(CEILING_ENV, "a positive integer", DEFAULT_CEILING)
        return DEFAULT_CEILING
    return value


def reset_reported() -> None:
    """Forget which bad settings were reported (tests)."""
    _reported.clear()


def today() -> datetime.date:
    """The America/New_York calendar day, the budget's day boundary."""
    return datetime.datetime.now(ET).date()


def estimate_tokens(chars: int) -> int:
    return -(-chars // CHARS_PER_TOKEN)  # ceiling division: round up, never down


def call_usage(final) -> Optional[tuple[int, int]]:
    """``(input_tokens, output_tokens)`` from a final message, or ``None``.

    Reads both shapes ChatService sees: the keyproxy's ``KeyProxyMessage``
    (a ``raw`` dict) and the Anthropic SDK's message (a ``usage`` object).
    """
    raw = getattr(final, "raw", None)
    usage = raw.get("usage") if isinstance(raw, dict) else getattr(final, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        values = (usage.get("input_tokens"), usage.get("output_tokens"))
    else:
        values = (getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return None
    return values


class BudgetExhausted(Exception):
    """Enforce mode refused a call: the user's daily budget is spent."""

    def __init__(self, used: int, budget: int) -> None:
        super().__init__(EXHAUSTED_MESSAGE.format(used=used, budget=budget))


class BudgetUnavailable(Exception):
    """Enforce mode couldn't read or write the budget, so the turn stops (D8)."""

    def __init__(self) -> None:
        super().__init__(UNAVAILABLE_MESSAGE)


@dataclass(frozen=True)
class Reservation:
    tokens: int
    usage_day: datetime.date
    tracked: bool  # False: nothing was written, so there is nothing to settle


class TurnBudget:
    """One chat turn's view of the budget: reserve before a call, settle after."""

    def __init__(
        self,
        repo,
        owner: str,
        budget_for: Callable[[str], int],
        day: Callable[[], datetime.date],
    ) -> None:
        self._repo = repo
        self._owner = owner
        self._budget_for = budget_for
        self._day = day
        self._first_call = True
        self._exhaustion_logged = False

    def reserve(self, tokens: int) -> Reservation:
        """Reserve one call's worst case, or raise BudgetExhausted/BudgetUnavailable."""
        mode = budget_mode()
        usage_day = self._day()
        if mode == "off":
            return Reservation(tokens, usage_day, tracked=False)
        new_turn, self._first_call = self._first_call, False
        enforce = mode == "enforce"
        try:
            budget = self._budget_for(self._owner)
            within, committed = self._repo.reserve(
                owner=self._owner,
                usage_day=usage_day,
                tokens=tokens,
                budget=budget,
                new_turn=new_turn,
                enforce=enforce,
            )
            if not within and enforce:
                committed = self._repo.used(owner=self._owner, usage_day=usage_day)
        except Exception as exc:  # noqa: BLE001 — D8 decides, per mode
            self._log_unavailable(mode, "reserve", exc)
            if enforce:
                raise BudgetUnavailable() from None
            return Reservation(tokens, usage_day, tracked=False)
        if not within:
            if not self._exhaustion_logged:
                self._exhaustion_logged = True
                _security_logger.warning(
                    "SECURITY chat_budget_exhausted mode=%s identity=%s", mode, self._owner
                )
            if enforce:
                raise BudgetExhausted(used=committed or 0, budget=budget)
        return Reservation(tokens, usage_day, tracked=True)

    def settle(self, reservation: Reservation, usage: Optional[tuple[int, int]]) -> None:
        """Release the reservation and add what the call really used.

        With no reported usage, the reservation itself is charged, as input.
        Raises BudgetUnavailable in enforce mode if the write fails.
        """
        if not reservation.tracked:
            return
        input_tokens, output_tokens = usage if usage is not None else (reservation.tokens, 0)
        self._write(reservation, input_tokens, output_tokens)

    def release(self, reservation: Reservation) -> None:
        """Hand back a reservation for a call that never reached the provider."""
        if reservation.tracked:
            self._write(reservation, 0, 0)

    def _write(self, reservation: Reservation, input_tokens: int, output_tokens: int) -> None:
        mode = budget_mode()
        try:
            self._repo.reconcile(
                owner=self._owner,
                usage_day=reservation.usage_day,
                reserved=reservation.tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception as exc:  # noqa: BLE001 — D8 decides, per mode
            self._log_unavailable(mode, "reconcile", exc)
            if mode == "enforce":
                raise BudgetUnavailable() from None

    @staticmethod
    def _log_unavailable(mode: str, stage: str, exc: Exception) -> None:
        # The class name only: exception text can carry a DSN (never-log policy).
        _security_logger.warning(
            "SECURITY chat_budget_unavailable mode=%s stage=%s error=%s",
            mode,
            stage,
            type(exc).__name__,
        )


class SidekickBudget:
    """Hands each chat turn its TurnBudget. One instance lives in the registry."""

    def __init__(
        self,
        repo,
        budget_for: Callable[[str], int],
        day: Callable[[], datetime.date] = today,
    ) -> None:
        self._repo = repo
        self._budget_for = budget_for
        self._day = day

    def start_turn(self, owner: str) -> TurnBudget:
        return TurnBudget(self._repo, owner, self._budget_for, self._day)
