"""UserSettingsRepository — owner-scoped CRUD over the `user_settings` table.

SQL only, no analytics or validation — model allow-listing and the budget
bounds live in SettingsService (quantcore/services/settings.py).
"""

from __future__ import annotations

from contextlib import closing
from typing import Optional

from quantcore.db import get_connection

SQL_GET_CHAT_MODEL = """
SELECT chat_model FROM user_settings WHERE owner = :owner;
"""

SQL_UPSERT_CHAT_MODEL = """
INSERT INTO user_settings (owner, chat_model)
VALUES (:owner, :chat_model)
ON CONFLICT (owner) DO UPDATE SET
    chat_model = excluded.chat_model,
    updated_at = now();
"""

SQL_GET_CHAT_BUDGET = """
SELECT chat_daily_token_budget FROM user_settings WHERE owner = :owner;
"""

# A user who sets only a budget still needs a row, and chat_model is NOT NULL,
# so a first write stores the caller's resolved model alongside it.
SQL_UPSERT_CHAT_BUDGET = """
INSERT INTO user_settings (owner, chat_model, chat_daily_token_budget)
VALUES (:owner, :chat_model, :budget)
ON CONFLICT (owner) DO UPDATE SET
    chat_daily_token_budget = excluded.chat_daily_token_budget,
    updated_at = now();
"""


class UserSettingsRepository:
    """SQL persistence for per-user Sidekick settings."""

    def get_chat_model(self, owner: str) -> Optional[str]:
        with closing(get_connection()) as conn:
            row = conn.execute(SQL_GET_CHAT_MODEL, {"owner": owner}).fetchone()
        return row["chat_model"] if row else None

    def set_chat_model(self, owner: str, chat_model: str) -> None:
        with closing(get_connection()) as conn:
            try:
                conn.execute(SQL_UPSERT_CHAT_MODEL, {"owner": owner, "chat_model": chat_model})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get_chat_daily_token_budget(self, owner: str) -> Optional[int]:
        with closing(get_connection()) as conn:
            row = conn.execute(SQL_GET_CHAT_BUDGET, {"owner": owner}).fetchone()
        return row["chat_daily_token_budget"] if row else None

    def set_chat_daily_token_budget(
        self, owner: str, budget: Optional[int], *, chat_model: str
    ) -> None:
        """Store (or with ``None`` clear) the user's cap. ``chat_model`` fills a new row."""
        with closing(get_connection()) as conn:
            try:
                conn.execute(
                    SQL_UPSERT_CHAT_BUDGET,
                    {"owner": owner, "chat_model": chat_model, "budget": budget},
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
