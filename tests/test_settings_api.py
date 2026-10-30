"""Integration tests for GET/PUT /api/settings (issue #124).

Runs the FastAPI app through TestClient against the real SettingsService /
UserSettingsRepository / test database. The test DSN is swapped in by the
``tests`` package initializer before ``quantcore.db`` is imported.
"""

import os
import unittest
from contextlib import closing

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.chat_usage_repository import ChatUsageRepository  # noqa: E402
from quantcore.services import chat_budget  # noqa: E402
from api.main import create_app  # noqa: E402

SECRET = "test-secret-key-at-least-32-bytes-long-000"

_AUTH_ENV_KEYS = (
    "AUTH_DISABLED",
    "QUANTCORE_JWT_SECRET",
    "QUANTCORE_JWT_PUBLIC_KEY",
    "QUANTCORE_JWT_ALGORITHMS",
    "QUANTCORE_JWT_ISSUER",
    "QUANTCORE_JWT_AUDIENCE",
    "QUANTCORE_JWT_LEEWAY",
    "SIDEKICK_DAILY_TOKEN_BUDGET",  # so the default ceiling applies
)

OWNER_A = "zz_settings_api_a"
OWNER_B = "zz_settings_api_b"


def _auth_headers(sub: str) -> dict:
    token = jwt.encode({"sub": sub}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


class SettingsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(), raise_server_exceptions=False)

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _AUTH_ENV_KEYS}
        for k in _AUTH_ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["QUANTCORE_JWT_SECRET"] = SECRET
        self._purge()
        self.addCleanup(self._purge)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM user_settings WHERE owner IN (%s, %s)", (OWNER_A, OWNER_B)
            )
            conn.execute(
                "DELETE FROM chat_usage WHERE owner IN (%s, %s)", (OWNER_A, OWNER_B)
            )
            conn.commit()

    # -- happy path -------------------------------------------------------

    def test_get_defaults_when_unset(self):
        resp = self.client.get("/api/settings", headers=_auth_headers(OWNER_A))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["chat_model"], "claude-sonnet-5")
        self.assertEqual(
            [m["id"] for m in body["models"]],
            ["claude-sonnet-5", "claude-opus-4-8", "claude-fable-5"],
        )

    def test_put_then_get_round_trips(self):
        put_resp = self.client.put(
            "/api/settings",
            json={"chat_model": "claude-opus-4-8"},
            headers=_auth_headers(OWNER_A),
        )
        self.assertEqual(put_resp.status_code, 200)
        self.assertEqual(put_resp.json()["chat_model"], "claude-opus-4-8")

        get_resp = self.client.get("/api/settings", headers=_auth_headers(OWNER_A))
        self.assertEqual(get_resp.json()["chat_model"], "claude-opus-4-8")

    def test_put_invalid_model_400(self):
        resp = self.client.put(
            "/api/settings",
            json={"chat_model": "gpt-4o"},
            headers=_auth_headers(OWNER_A),
        )
        self.assertEqual(resp.status_code, 400)

    def test_owners_are_isolated(self):
        self.client.put(
            "/api/settings",
            json={"chat_model": "claude-fable-5"},
            headers=_auth_headers(OWNER_A),
        )
        resp_b = self.client.get("/api/settings", headers=_auth_headers(OWNER_B))
        self.assertEqual(resp_b.json()["chat_model"], "claude-sonnet-5")

    # -- daily Sidekick token budget (G2, #461) ---------------------------

    def _put(self, body, owner=OWNER_A):
        return self.client.put("/api/settings", json=body, headers=_auth_headers(owner))

    def test_budget_defaults_to_the_ceiling(self):
        body = self.client.get("/api/settings", headers=_auth_headers(OWNER_A)).json()
        self.assertIsNone(body["chat_daily_token_budget"])
        self.assertEqual(body["chat_daily_token_budget_ceiling"], chat_budget.DEFAULT_CEILING)
        self.assertEqual(body["chat_daily_token_budget_effective"], chat_budget.DEFAULT_CEILING)
        self.assertEqual(body["chat_tokens_used_today"], 0)

    def test_a_cap_lowers_the_effective_budget_and_null_clears_it(self):
        resp = self._put({"chat_daily_token_budget": 50_000})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["chat_daily_token_budget"], 50_000)
        self.assertEqual(resp.json()["chat_daily_token_budget_effective"], 50_000)
        # Setting the model alone leaves the cap alone.
        resp = self._put({"chat_model": "claude-opus-4-8"})
        self.assertEqual(resp.json()["chat_daily_token_budget"], 50_000)
        resp = self._put({"chat_daily_token_budget": None})
        self.assertIsNone(resp.json()["chat_daily_token_budget"])
        self.assertEqual(resp.json()["chat_model"], "claude-opus-4-8")

    def test_a_cap_outside_the_bounds_is_400_and_changes_nothing(self):
        for bad in (0, -1, chat_budget.DEFAULT_CEILING + 1):
            with self.subTest(bad):
                resp = self._put({"chat_model": "claude-fable-5", "chat_daily_token_budget": bad})
                self.assertEqual(resp.status_code, 400)
        body = self.client.get("/api/settings", headers=_auth_headers(OWNER_A)).json()
        self.assertEqual(body["chat_model"], "claude-sonnet-5")
        self.assertIsNone(body["chat_daily_token_budget"])

    def test_todays_usage_is_reported(self):
        ChatUsageRepository().reserve(
            owner=OWNER_A, usage_day=chat_budget.today(), tokens=1234,
            budget=10**9, new_turn=True, enforce=True,
        )
        body = self.client.get("/api/settings", headers=_auth_headers(OWNER_A)).json()
        self.assertEqual(body["chat_tokens_used_today"], 1234)

    def test_budgets_are_owner_scoped(self):
        self._put({"chat_daily_token_budget": 50_000})
        ChatUsageRepository().reserve(
            owner=OWNER_A, usage_day=chat_budget.today(), tokens=1234,
            budget=10**9, new_turn=True, enforce=True,
        )
        body_b = self.client.get("/api/settings", headers=_auth_headers(OWNER_B)).json()
        self.assertIsNone(body_b["chat_daily_token_budget"])
        self.assertEqual(body_b["chat_tokens_used_today"], 0)

    # -- auth ---------------------------------------------------------------

    def test_jwt_enforced(self):
        resp = self.client.get("/api/settings")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
