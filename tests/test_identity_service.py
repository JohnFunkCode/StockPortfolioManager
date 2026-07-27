import unittest
from contextlib import closing

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.owner_identity_repository import (  # noqa: E402
    OwnerIdentityRepository,
)
from quantcore.services.identity import IdentityService, UnknownIdentityError  # noqa: E402

IDENTITY = "zz_identity_test@example.com"
OWNER = "zz_identity_owner"


class IdentityServiceTest(unittest.TestCase):
    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.repo = OwnerIdentityRepository()
        self.service = IdentityService(owner_identity_repository=self.repo)

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM owner_identities WHERE identity = %s", (IDENTITY,))
            conn.commit()

    def test_resolve_owner_raises_for_unmapped_identity(self):
        with self.assertRaises(UnknownIdentityError):
            self.service.resolve_owner(IDENTITY)

    def test_resolve_owner_returns_mapped_owner(self):
        self.repo.upsert(IDENTITY, OWNER)
        self.assertEqual(self.service.resolve_owner(IDENTITY), OWNER)

    def test_resolve_owner_lowercases_identity(self):
        self.repo.upsert(IDENTITY, OWNER)
        self.assertEqual(self.service.resolve_owner(IDENTITY.upper()), OWNER)

    def test_upsert_updates_existing_mapping(self):
        self.repo.upsert(IDENTITY, OWNER)
        self.repo.upsert(IDENTITY, "zz_identity_owner2")
        self.assertEqual(self.service.resolve_owner(IDENTITY), "zz_identity_owner2")

    def test_list_all_includes_seeded_mapping(self):
        self.repo.upsert(IDENTITY, OWNER, notes="test note")
        rows = self.repo.list_all()
        self.assertIn((IDENTITY, OWNER, "test note"), rows)


if __name__ == "__main__":
    unittest.main()
