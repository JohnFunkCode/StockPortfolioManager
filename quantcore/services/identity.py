"""IdentityService — resolves an authenticated identity to its owner handle.

Issue #126 decision #2: unknown identities are never auto-provisioned. A
lookup miss raises UnknownIdentityError; the caller (api/auth.py's
require_owner dependency) is responsible for the 403 + security-event log.
"""

from __future__ import annotations

from quantcore.repositories.owner_identity_repository import OwnerIdentityRepository


class UnknownIdentityError(Exception):
    """Raised when an identity has no owner_identities mapping."""


class IdentityService:
    def __init__(self, owner_identity_repository: OwnerIdentityRepository) -> None:
        self._repo = owner_identity_repository

    def resolve_owner(self, identity: str) -> str:
        owner = self._repo.resolve(identity.strip().lower())
        if owner is None:
            raise UnknownIdentityError(identity)
        return owner
