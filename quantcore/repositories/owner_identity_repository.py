"""OwnerIdentityRepository — SQL-only CRUD over the `owner_identities` table.

Maps an authenticated identity (IAP email or MCP token sub) to the canonical
short-handle owner value. No auto-provisioning logic here — that policy lives
in IdentityService (quantcore/services/identity.py).
"""

from __future__ import annotations

from contextlib import closing
from typing import List, Optional, Tuple

from quantcore.db import get_connection

SQL_RESOLVE = """
SELECT owner FROM owner_identities WHERE identity = :identity;
"""

SQL_UPSERT = """
INSERT INTO owner_identities (identity, owner, notes)
VALUES (:identity, :owner, :notes)
ON CONFLICT (identity) DO UPDATE SET
    owner = excluded.owner,
    notes = excluded.notes;
"""

SQL_LIST_ALL = """
SELECT identity, owner, notes FROM owner_identities ORDER BY identity;
"""


class OwnerIdentityRepository:
    """SQL persistence for the identity -> owner mapping."""

    def resolve(self, identity: str) -> Optional[str]:
        with closing(get_connection()) as conn:
            row = conn.execute(SQL_RESOLVE, {"identity": identity}).fetchone()
        return row["owner"] if row else None

    def upsert(self, identity: str, owner: str, notes: Optional[str] = None) -> None:
        with closing(get_connection()) as conn:
            try:
                conn.execute(SQL_UPSERT, {"identity": identity, "owner": owner, "notes": notes})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list_all(self) -> List[Tuple[str, str, Optional[str]]]:
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_ALL).fetchall()
        return [(r["identity"], r["owner"], r["notes"]) for r in rows]
