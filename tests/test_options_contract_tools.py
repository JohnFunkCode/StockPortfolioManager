import os
import sys
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fastMCPTest"))

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.services.options_contracts import (  # noqa: E402
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

    def test_save_full_chain_batches_across_page_boundaries(self):
        """A MSTR-sized chain (>500 contracts per side) must survive the
        execute_batch paging in _PGConn.executemany with every row intact —
        pins the fix for the ~5-minute per-row persist over the proxy."""
        n = 601  # crosses the 500-row page boundary within one executemany
        contracts = [
            {
                "strike": 10.0 + i,
                "last": 1.0,
                "bid": 0.9,
                "ask": 1.1,
                "iv": 50.0,
                "volume": i,
                "open_interest": i,
                "in_the_money": i % 2 == 0,
            }
            for i in range(n)
        ]
        big = [
            {
                "expiration": "2026-06-19",
                "put_call_ratio": 1.0,
                "calls": {
                    "contracts": contracts,
                    "total_open_interest": n,
                    "total_volume": n,
                    "avg_iv_pct": 50.0,
                },
                "puts": {
                    "contracts": contracts,
                    "total_open_interest": n,
                    "total_volume": n,
                    "avg_iv_pct": 50.0,
                },
            }
        ]
        store = self._store()
        snapshot_id = store.save_full_chain(
            symbol=TEST_SYMBOL,
            price=100.0,
            bollinger_bands=None,
            expirations_data=big,
            captured_at="2026-05-19T12:00:00Z",
        )
        self.assertIsNotNone(snapshot_id)
        snapshot = store.get_full_chain(TEST_SYMBOL)
        rows = snapshot["expirations"][0]["contracts"]
        self.assertEqual(len(rows), 2 * n)
        # Spot-check a row from the second page.
        strikes = {(c["kind"], c["strike"]) for c in rows}
        self.assertIn(("call", 10.0 + 550), strikes)
        self.assertIn(("put", 10.0 + 600), strikes)

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

    def test_curves_absent_by_default(self):
        """LLM-facing callers never opt in — the default response must stay lean."""
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
        self.assertIsNotNone(result["legs"]["long"])
        self.assertNotIn("curves", result)

    def test_curves_present_and_well_formed_when_requested(self):
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
            include_curves=True,
        )
        curves = result["curves"]
        self.assertEqual(len(curves["prices"]), 121)
        self.assertEqual(len(curves["expiry"]), 121)
        self.assertEqual(len(curves["now"]), 121)
        # Grid spans both strikes and the snapshot price (166.6).
        self.assertLess(curves["prices"][0], 150.0)
        self.assertGreater(curves["prices"][-1], 170.0)
        self.assertEqual(curves["params"]["r"], 0.045)
        self.assertEqual(curves["params"]["spot"], 166.6)
        # Curve debit prefers the mid debit, matching the card's chips.
        self.assertEqual(curves["params"]["debit"], result["mid_debit"])
        # Expiry curve extremes match the analytic bounds per share.
        self.assertAlmostEqual(curves["expiry"][0], -curves["params"]["debit"], places=6)
        self.assertAlmostEqual(
            curves["expiry"][-1], 20.0 - curves["params"]["debit"], places=6
        )

    def test_curves_omitted_when_a_leg_is_missing(self):
        store = self._store()
        self._seed_store(store)
        result = price_vertical_spread_data(
            symbol=TEST_SYMBOL,
            expiration="2026-05-22",
            long_strike=150.0,
            short_strike=190.0,  # not in the snapshot
            kind="call",
            max_snapshot_age_minutes=10_000_000,
            allow_live_fetch=False,
            store=store,
            include_curves=True,
        )
        self.assertIsNone(result["legs"]["short"])
        self.assertNotIn("curves", result)

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
