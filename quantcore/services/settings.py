"""SettingsService — per-user Sidekick settings (issue #124).

Owns the only business logic for the setting: allow-list validation on
write, and default-safe resolution on read (a stored-but-retired model
degrades to the default rather than erroring). SQL lives entirely in
UserSettingsRepository.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quantcore.repositories.user_settings_repository import UserSettingsRepository


@dataclass(frozen=True)
class SettingsView:
    chat_model: str
    models: list[dict] = field(default_factory=list)


class SettingsService:
    def __init__(
        self,
        repo: UserSettingsRepository,
        *,
        allowed: frozenset[str],
        default: str,
        catalog: list[dict],
    ) -> None:
        self._repo = repo
        self._allowed = allowed
        self._default = default
        self._catalog = catalog

    def get_chat_model(self, owner: str) -> str:
        """Default-safe resolver: stored value if still allow-listed, else default."""
        stored = self._repo.get_chat_model(owner)
        if stored in self._allowed:
            return stored
        return self._default

    def get_settings(self, owner: str) -> SettingsView:
        return SettingsView(chat_model=self.get_chat_model(owner), models=self._catalog)

    def set_chat_model(self, owner: str, chat_model: str) -> None:
        if chat_model not in self._allowed:
            raise ValueError(f"unsupported chat model: {chat_model!r}")
        self._repo.set_chat_model(owner, chat_model)
