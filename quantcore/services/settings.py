"""SettingsService — per-user Sidekick settings (issue #124).

Owns the only business logic for the settings: allow-list validation on
write, and default-safe resolution on read (a stored-but-retired model
degrades to the default rather than erroring). Since G2 (#461) it also owns
the user's daily Sidekick token cap: a cap may only lower the server ceiling,
and a stored cap above a since-lowered ceiling resolves to the ceiling. SQL
lives entirely in UserSettingsRepository and ChatUsageRepository.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Callable, Optional

from quantcore.repositories.user_settings_repository import UserSettingsRepository
from quantcore.services import chat_budget


# "Leave the budget as it is" for update(): None already means "clear the cap".
UNCHANGED = object()


def _effective(cap: Optional[int], ceiling: int) -> int:
    return ceiling if cap is None else min(cap, ceiling)


@dataclass(frozen=True)
class SettingsView:
    chat_model: str
    models: list[dict] = field(default_factory=list)
    # The user's own cap (None: the ceiling applies), the ceiling, and today's
    # usage including outstanding reservations.
    chat_daily_token_budget: Optional[int] = None
    chat_daily_token_budget_ceiling: int = 0
    chat_tokens_used_today: int = 0
    # What a turn is measured against: the cap, never above the ceiling.
    chat_daily_token_budget_effective: int = 0


class SettingsService:
    def __init__(
        self,
        repo: UserSettingsRepository,
        *,
        allowed: frozenset[str],
        default: str,
        catalog: list[dict],
        usage_repo=None,
        ceiling: Callable[[], int] = chat_budget.ceiling,
        today: Callable[[], datetime.date] = chat_budget.today,
    ) -> None:
        self._repo = repo
        self._allowed = allowed
        self._default = default
        self._catalog = catalog
        self._usage_repo = usage_repo
        self._ceiling = ceiling
        self._today = today

    def get_chat_model(self, owner: str) -> str:
        """Default-safe resolver: stored value if still allow-listed, else default."""
        stored = self._repo.get_chat_model(owner)
        if stored in self._allowed:
            return stored
        return self._default

    def get_settings(self, owner: str) -> SettingsView:
        used = (
            self._usage_repo.used(owner=owner, usage_day=self._today())
            if self._usage_repo is not None
            else 0
        )
        ceiling = self._ceiling()
        cap = self._repo.get_chat_daily_token_budget(owner)
        return SettingsView(
            chat_model=self.get_chat_model(owner),
            models=self._catalog,
            chat_daily_token_budget=cap,
            chat_daily_token_budget_ceiling=ceiling,
            chat_tokens_used_today=used,
            chat_daily_token_budget_effective=_effective(cap, ceiling),
        )

    def effective_chat_budget(self, owner: str) -> int:
        """The user's cap if set, else the ceiling, and never above the ceiling."""
        return _effective(self._repo.get_chat_daily_token_budget(owner), self._ceiling())

    def set_chat_model(self, owner: str, chat_model: str) -> None:
        if chat_model not in self._allowed:
            raise ValueError(f"unsupported chat model: {chat_model!r}")
        self._repo.set_chat_model(owner, chat_model)

    def update(
        self,
        owner: str,
        *,
        chat_model: Optional[str] = None,
        chat_daily_token_budget=UNCHANGED,
    ) -> SettingsView:
        """Apply whichever settings were sent, then return the resulting view.

        Validation runs before either write, so a bad budget doesn't leave a
        changed model behind.
        """
        if chat_model is not None and chat_model not in self._allowed:
            raise ValueError(f"unsupported chat model: {chat_model!r}")
        if chat_daily_token_budget is not UNCHANGED:
            self._check_budget(chat_daily_token_budget)
        if chat_model is not None:
            self.set_chat_model(owner, chat_model)
        if chat_daily_token_budget is not UNCHANGED:
            self.set_chat_daily_token_budget(owner, chat_daily_token_budget)
        return self.get_settings(owner)

    def set_chat_daily_token_budget(self, owner: str, budget: Optional[int]) -> None:
        """Lower (or with ``None`` clear) the user's cap. It can't exceed the ceiling."""
        self._check_budget(budget)
        self._repo.set_chat_daily_token_budget(
            owner, budget, chat_model=self.get_chat_model(owner)
        )

    def _check_budget(self, budget) -> None:
        if budget is None:
            return
        if isinstance(budget, bool) or not isinstance(budget, int):
            raise ValueError("chat_daily_token_budget must be a whole number of tokens")
        ceiling = self._ceiling()
        if not 1 <= budget <= ceiling:
            raise ValueError(
                f"chat_daily_token_budget must be between 1 and the system limit "
                f"of {ceiling:,} tokens; to go above it, please contact the "
                "development team"
            )
