import unittest
from contextlib import closing

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.user_settings_repository import (  # noqa: E402
    UserSettingsRepository,
)
from quantcore.services.settings import SettingsService  # noqa: E402

OWNER = "zz_settings_owner"
ALLOWED = frozenset({"claude-sonnet-5", "claude-opus-4-8", "claude-fable-5"})
DEFAULT = "claude-sonnet-5"
CATALOG = [{"id": m, "name": m, "description": ""} for m in sorted(ALLOWED)]


class SettingsServiceTest(unittest.TestCase):
    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.service = SettingsService(
            UserSettingsRepository(), allowed=ALLOWED, default=DEFAULT, catalog=CATALOG
        )

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM user_settings WHERE owner = %s", (OWNER,))
            conn.commit()

    def test_get_chat_model_defaults_when_unset(self):
        self.assertEqual(self.service.get_chat_model(OWNER), DEFAULT)

    def test_set_then_get_round_trip(self):
        self.service.set_chat_model(OWNER, "claude-opus-4-8")
        self.assertEqual(self.service.get_chat_model(OWNER), "claude-opus-4-8")

    def test_set_rejects_unlisted_model(self):
        with self.assertRaises(ValueError):
            self.service.set_chat_model(OWNER, "gpt-4o")

    def test_stored_but_retired_model_degrades_to_default(self):
        # Simulate a model that used to be allow-listed but no longer is by
        # writing directly through the repository (bypassing the service
        # validation that would otherwise reject it).
        UserSettingsRepository().set_chat_model(OWNER, "claude-2.1-retired")
        self.assertEqual(self.service.get_chat_model(OWNER), DEFAULT)

    def test_get_settings_returns_current_value_and_catalog(self):
        self.service.set_chat_model(OWNER, "claude-fable-5")
        view = self.service.get_settings(OWNER)
        self.assertEqual(view.chat_model, "claude-fable-5")
        self.assertEqual(view.models, CATALOG)


if __name__ == "__main__":
    unittest.main()
