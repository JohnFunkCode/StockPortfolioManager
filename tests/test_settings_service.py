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

    # -- daily Sidekick token budget (G2, #461) ---------------------------

    def _with_ceiling(self, ceiling):
        return SettingsService(
            UserSettingsRepository(),
            allowed=ALLOWED,
            default=DEFAULT,
            catalog=CATALOG,
            ceiling=lambda: ceiling,
        )

    def test_effective_budget_is_the_ceiling_until_a_cap_is_set(self):
        service = self._with_ceiling(1000)
        self.assertEqual(service.effective_chat_budget(OWNER), 1000)
        service.set_chat_daily_token_budget(OWNER, 400)
        self.assertEqual(service.effective_chat_budget(OWNER), 400)
        service.set_chat_daily_token_budget(OWNER, None)
        self.assertEqual(service.effective_chat_budget(OWNER), 1000)

    def test_a_cap_above_a_since_lowered_ceiling_resolves_to_the_ceiling(self):
        self._with_ceiling(1000).set_chat_daily_token_budget(OWNER, 900)
        lowered = self._with_ceiling(500)
        self.assertEqual(lowered.effective_chat_budget(OWNER), 500)
        view = lowered.get_settings(OWNER)
        self.assertEqual(
            (view.chat_daily_token_budget, view.chat_daily_token_budget_effective), (900, 500)
        )

    def test_a_cap_must_be_a_whole_number_within_the_ceiling(self):
        service = self._with_ceiling(1000)
        for bad in (0, -5, 1001, 2.5, True, "100"):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    service.set_chat_daily_token_budget(OWNER, bad)
        self.assertIsNone(UserSettingsRepository().get_chat_daily_token_budget(OWNER))

    def test_a_budget_only_write_keeps_the_resolved_model(self):
        service = self._with_ceiling(1000)
        service.set_chat_model(OWNER, "claude-fable-5")
        service.set_chat_daily_token_budget(OWNER, 10)
        self.assertEqual(service.get_chat_model(OWNER), "claude-fable-5")

    def test_update_validates_both_before_writing_either(self):
        service = self._with_ceiling(1000)
        with self.assertRaises(ValueError):
            service.update(OWNER, chat_model="claude-fable-5", chat_daily_token_budget=5000)
        self.assertEqual(service.get_chat_model(OWNER), DEFAULT)

    def test_get_settings_returns_current_value_and_catalog(self):
        self.service.set_chat_model(OWNER, "claude-fable-5")
        view = self.service.get_settings(OWNER)
        self.assertEqual(view.chat_model, "claude-fable-5")
        self.assertEqual(view.models, CATALOG)


if __name__ == "__main__":
    unittest.main()
