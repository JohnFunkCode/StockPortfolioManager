"""ChatUsageRepository — per-user daily Sidekick token accounting (G2, #461).

SQL only. Admission is one conditional upsert per model call, so two calls that
can't both fit are serialized by Postgres on the ``(owner, usage_day)`` row and
exactly one is admitted, whichever api instance each runs on
(docs/proposals/rate-limiting-g2-plan.md D3). The budget figure and the mode
are decided in quantcore/services/chat_budget.py, not here.

``owner`` is the raw principal identity, the key ``user_settings`` uses. Every
statement binds it (CLAUDE.md "Unified Database"; tests/test_owner_isolation_coverage.py).
"""

from __future__ import annotations

from contextlib import closing
from datetime import date
from typing import Optional

from quantcore.db import get_connection

# Inserts a first row or adds to today's, but only while stored usage plus
# outstanding reservations plus this one fits the budget. A returned row means
# admitted; none means over budget. (No `::` casts: the connection wrapper
# rewrites `:name` placeholders with a regex.)
SQL_RESERVE_WITHIN_BUDGET = """
INSERT INTO chat_usage (owner, usage_day, turns, reserved_tokens)
SELECT :owner, :usage_day, :turns, :tokens
WHERE :tokens <= :budget
ON CONFLICT (owner, usage_day) DO UPDATE SET
    turns = chat_usage.turns + EXCLUDED.turns,
    reserved_tokens = chat_usage.reserved_tokens + EXCLUDED.reserved_tokens,
    updated_at = now()
WHERE chat_usage.input_tokens + chat_usage.output_tokens
      + chat_usage.reserved_tokens + EXCLUDED.reserved_tokens <= :budget
RETURNING input_tokens + output_tokens + reserved_tokens AS committed;
"""

# Shadow mode's fallback: record the reservation regardless, so the accounting
# stays true while nothing is refused.
SQL_RESERVE_UNCONDITIONALLY = """
INSERT INTO chat_usage (owner, usage_day, turns, reserved_tokens)
VALUES (:owner, :usage_day, :turns, :tokens)
ON CONFLICT (owner, usage_day) DO UPDATE SET
    turns = chat_usage.turns + EXCLUDED.turns,
    reserved_tokens = chat_usage.reserved_tokens + EXCLUDED.reserved_tokens,
    updated_at = now()
RETURNING input_tokens + output_tokens + reserved_tokens AS committed;
"""

SQL_RECONCILE = """
UPDATE chat_usage SET
    reserved_tokens = GREATEST(reserved_tokens - :reserved, 0),
    input_tokens = input_tokens + :input_tokens,
    output_tokens = output_tokens + :output_tokens,
    updated_at = now()
WHERE owner = :owner AND usage_day = :usage_day;
"""

SQL_USED = """
SELECT input_tokens + output_tokens + reserved_tokens AS used
FROM chat_usage
WHERE owner = :owner AND usage_day = :usage_day;
"""


class ChatUsageRepository:
    """SQL persistence for the Sidekick daily token budget."""

    def reserve(
        self,
        *,
        owner: str,
        usage_day: date,
        tokens: int,
        budget: int,
        new_turn: bool,
        enforce: bool,
    ) -> tuple[bool, Optional[int]]:
        """Reserve ``tokens`` against ``budget``. Returns ``(within_budget, committed)``.

        ``within_budget`` False means the reservation didn't fit. In enforce
        mode nothing was written; otherwise it was recorded anyway.
        ``committed`` is the day's total after the write, or ``None`` when
        nothing was written.
        """
        params = {
            "owner": owner,
            "usage_day": usage_day,
            "turns": 1 if new_turn else 0,
            "tokens": tokens,
            "budget": budget,
        }
        with closing(get_connection()) as conn:
            try:
                row = conn.execute(SQL_RESERVE_WITHIN_BUDGET, params).fetchone()
                within = row is not None
                if not within and not enforce:
                    row = conn.execute(SQL_RESERVE_UNCONDITIONALLY, params).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return within, (row["committed"] if row else None)

    def reconcile(
        self,
        *,
        owner: str,
        usage_day: date,
        reserved: int,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Release a reservation and add the call's actual usage."""
        params = {
            "owner": owner,
            "usage_day": usage_day,
            "reserved": reserved,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        with closing(get_connection()) as conn:
            try:
                conn.execute(SQL_RECONCILE, params)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def used(self, *, owner: str, usage_day: date) -> int:
        """Stored usage plus outstanding reservations for one user's day."""
        with closing(get_connection()) as conn:
            row = conn.execute(
                SQL_USED, {"owner": owner, "usage_day": usage_day}
            ).fetchone()
        return int(row["used"]) if row else 0
