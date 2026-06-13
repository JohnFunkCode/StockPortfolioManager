import os
import sys
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fastMCPTest"))

# DB-backed tests run against the test database only. Swap the test DSN in
# BEFORE quantcore.db is imported (it freezes DB_DSN at import time), then let
# the guard abort if this process would still reach production.
for _line in (Path(__file__).parent / ".env").read_text().splitlines():
    if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
        os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
        break

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from options_contract_tools import (  # noqa: E402
    get_option_contracts_data,
    price_vertical_spread_data,
)
from quantcore.repositories.options_repository import OptionsStore  # noqa: E402
from quantcore.db import get_connection  # noqa: E402

# Synthetic ticker that won't collide with real cached snapshots — keeps these
# tests isolated from whatever data already lives in the configured QuantCore database.
TEST_SYMBOL = "ZZTEST"


def _sample_expirations_data():
    return [
        {
            "expiration": "2026-05-22",
            "put_call_ratio": 1.2,
            "calls": {
                "contracts": [
                    {
                        "strike": 150.0,
                        "last": 14.5,
                        "bid": 13.4,
                        "ask": 15.8,
                        "iv": 70.0,
                        "volume": 90,
                        "open_interest": 740,
                        "in_the_money": True,
                    },
                    {
                        "strike": 170.0,
                        "last": 4.1,
                        "bid": 3.5,
                        "ask": 4.7,
                        "iv": 79.0,
                        "volume": 621,
                        "open_interest": 263,
                        "in_the_money": False,
                    },
                ],
                "total_open_interest": 1003,
                "total_volume": 711,
                "avg_iv_pct": 74.5,
            },
            "puts": {
                "contracts": [
                    {
                        "strike": 150.0,
                        "last": 4.6,
                        "bid": 4.0,
                        "ask": 5.0,
                        "iv": 85.0,
                        "volume": 80,
                        "open_interest": 300,
                        "in_the_money": False,
                    },
                    {
                        "strike": 170.0,
                        "last": 13.2,
                        "bid": 12.0,
                        "ask": 14.0,
                        "iv": 92.0,
                        "volume": 75,
                        "open_interest": 250,
                        "in_the_money": True,
                    },
                ],
                "total_open_interest": 550,
                "total_volume": 155,
                "avg_iv_pct": 88.5,
            },
        }
    ]


class OptionsContractToolsTest(unittest.TestCase):
    def _purge_test_symbol(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM options_snapshots WHERE symbol = %s", (TEST_SYMBOL,)
            )
            conn.commit()

    def _store(self):
        self._purge_test_symbol()
        self.addCleanup(self._purge_test_symbol)
        return OptionsStore()

    def _seed_store(self, store, captured_at="2026-05-19T12:00:00Z"):
        return store.save_full_chain(
            symbol=TEST_SYMBOL,
            price=166.6,
            bollinger_bands=None,
            expirations_data=_sample_expirations_data(),
            captured_at=captured_at,
        )

    def test_save_full_chain_commits_contract_rows(self):
        store = self._store()
        snapshot_id = self._seed_store(store)

        self.assertIsNotNone(snapshot_id)
        snapshot = store.get_full_chain(TEST_SYMBOL)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["symbol"], TEST_SYMBOL)
        self.assertEqual(len(snapshot["expirations"]), 1)
        self.assertEqual(len(snapshot["expirations"][0]["contracts"]), 4)

    def test_duplicate_snapshot_returns_none_without_corrupting_rows(self):
        store = self._store()
        first = self._seed_store(store)
        duplicate = self._seed_store(store)

        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)
        snapshot = store.get_full_chain(TEST_SYMBOL)
        self.assertEqual(len(snapshot["expirations"][0]["contracts"]), 4)

    def test_get_option_contracts_uses_fresh_cache(self):
        store = self._store()
        self._seed_store(store)

        result = get_option_contracts_data(
            symbol=TEST_SYMBOL,
            expirations=["2026-05-22"],
            strikes=[150.0, 170.0],
            kind="call",
            max_snapshot_age_minutes=10_000_000,
            allow_live_fetch=False,
            store=store,
        )

        self.assertEqual(result["source"], "cache")
        self.assertEqual(result["missing"], [])
        self.assertEqual([c["strike"] for c in result["contracts"]], [150.0, 170.0])

    def test_get_option_contracts_reports_missing_contracts(self):
        store = self._store()
        self._seed_store(store)

        result = get_option_contracts_data(
            symbol=TEST_SYMBOL,
            expirations=["2026-05-22"],
            strikes=[150.0, 190.0],
            kind="call",
            max_snapshot_age_minutes=10_000_000,
            allow_live_fetch=False,
            store=store,
        )

        self.assertEqual(len(result["contracts"]), 1)
        self.assertEqual(result["missing"], [
            {"expiration": "2026-05-22", "strike": 190.0, "kind": "call"}
        ])

    def test_stale_cache_can_refresh_with_injected_live_fetcher(self):
        store = self._store()
        self._seed_store(store, captured_at="2000-01-01T00:00:00Z")

        def live_fetcher(symbol, target_store):
            snapshot_id = target_store.save_full_chain(
                symbol=symbol,
                price=166.6,
                bollinger_bands=None,
                expirations_data=_sample_expirations_data(),
            )
            return {
                "snapshot": target_store.get_full_chain(symbol),
                "snapshot_id": snapshot_id,
                "expiration_count": 1,
                "total_contracts": 4,
            }

        result = get_option_contracts_data(
            symbol=TEST_SYMBOL,
            expirations=["2026-05-22"],
            strikes=[150.0, 170.0],
            kind="call",
            max_snapshot_age_minutes=15,
            allow_live_fetch=True,
            store=store,
            live_fetcher=live_fetcher,
        )

        self.assertEqual(result["source"], "live")
        self.assertTrue(result["storage_status"]["persisted"])
        self.assertEqual(result["missing"], [])

    def test_prices_bull_call_spread(self):
        store = self._store()
        self._seed_store(store)

        result = price_vertical_spread_data(
            symbol=TEST_SYMBOL,
            expiration="2026-05-22",
            long_strike=150.0,
            short_strike=170.0,
            kind="call",
            max_snapshot_age_minutes=10_000_000,
            allow_live_fetch=False,
            store=store,
        )

        self.assertEqual(result["strategy"], "bull_call_spread")
        self.assertEqual(result["debit"], 12.3)
        self.assertEqual(result["mid_debit"], 10.5)
        self.assertEqual(result["max_profit"], 7.7)
        self.assertEqual(result["max_loss"], 12.3)
        self.assertEqual(result["breakeven"], 162.3)

    def test_prices_bear_put_spread(self):
        store = self._store()
        self._seed_store(store)

        result = price_vertical_spread_data(
            symbol=TEST_SYMBOL,
            expiration="2026-05-22",
            long_strike=170.0,
            short_strike=150.0,
            kind="put",
            max_snapshot_age_minutes=10_000_000,
            allow_live_fetch=False,
            store=store,
        )

        self.assertEqual(result["strategy"], "bear_put_spread")
        self.assertEqual(result["debit"], 10.0)
        self.assertEqual(result["max_profit"], 10.0)
        self.assertEqual(result["max_loss"], 10.0)
        self.assertEqual(result["breakeven"], 160.0)


if __name__ == "__main__":
    unittest.main()
