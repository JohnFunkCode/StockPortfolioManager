"""Unit tests for OptionsService.get_gex_profile / get_gex_history (Phase 5,
issue #93). No DB, no network — a stub yfinance gateway serves synthetic
chains and a Mock OptionsStore captures the persistence call."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from quantcore.services.options import OptionsService

EXP_NEAR = (date.today() + timedelta(days=30)).isoformat()
EXP_FAR = (date.today() + timedelta(days=60)).isoformat()


def _side(strikes, ois, iv=0.30):
    return pd.DataFrame({
        "strike": strikes,
        "openInterest": ois,
        "impliedVolatility": [iv] * len(strikes),
    })


class _StubGateway:
    """Minimal YFinanceGateway stand-in: fixed price + canned chains."""

    def __init__(self, price=100.0, expirations=None, chains=None,
                 failing_expirations=()):
        self._price = price
        self._expirations = expirations if expirations is not None else [EXP_NEAR]
        self._chains = chains or {}
        self._failing = set(failing_expirations)

    def fast_info(self, symbol):
        return SimpleNamespace(last_price=self._price)

    def expirations(self, symbol):
        return self._expirations

    def option_chain(self, symbol, exp):
        if exp in self._failing:
            raise RuntimeError(f"chain fetch failed for {exp}")
        return self._chains[exp]


def _service(gateway, store=None):
    return OptionsService(Mock(), gateway, store or Mock(), Mock(), Mock())


def _default_chains():
    # Calls heavy above spot (call wall at 105), puts light below —
    # cumulative net GEX flips negative→positive between 95 and 105.
    return {
        EXP_NEAR: SimpleNamespace(
            calls=_side([105.0, 110.0], [5000, 2000]),
            puts=_side([90.0, 95.0], [100, 100]),
        )
    }


class TestGexProfile(unittest.TestCase):
    def test_signs_follow_dealer_convention(self):
        result = _service(_StubGateway(chains=_default_chains())).get_gex_profile("nvda")

        self.assertEqual(result["symbol"], "NVDA")
        self.assertEqual(result["convention"], "dealers long calls / short puts")
        ladder = {row["strike"]: row for row in result["gex_ladder"]}
        # All four strikes sit within ±10% of spot 100 → all in the ladder.
        for k in (90.0, 95.0):
            self.assertLess(ladder[k]["put_gex"], 0.0)
            self.assertEqual(ladder[k]["call_gex"], 0.0)
        for k in (105.0, 110.0):
            self.assertGreater(ladder[k]["call_gex"], 0.0)
            self.assertEqual(ladder[k]["put_gex"], 0.0)
        # Calls dominate → positive-gamma (dampening) regime.
        self.assertGreater(result["net_gex"], 0.0)
        self.assertEqual(result["regime"], "positive_gamma")
        self.assertEqual(result["top_positive_gex_strike"], 105.0)
        self.assertIn(result["top_negative_gex_strike"], (90.0, 95.0))

    def test_zero_gamma_interpolated_at_the_sign_flip(self):
        result = _service(_StubGateway(chains=_default_chains())).get_gex_profile("NVDA")
        # Cumulative net GEX is negative through the put strikes and flips
        # positive at 105 → the interpolated flip lies strictly between.
        self.assertIsNotNone(result["zero_gamma_level"])
        self.assertGreater(result["zero_gamma_level"], 95.0)
        self.assertLess(result["zero_gamma_level"], 105.0)
        self.assertAlmostEqual(
            result["dist_to_zero_gamma_pct"],
            round((result["zero_gamma_level"] - 100.0) / 100.0 * 100, 2),
            places=2,
        )

    def test_put_dominated_chain_is_negative_gamma(self):
        chains = {
            EXP_NEAR: SimpleNamespace(
                calls=_side([105.0], [100]),
                puts=_side([95.0], [8000]),
            )
        }
        result = _service(_StubGateway(chains=chains)).get_gex_profile("NVDA")
        self.assertLess(result["net_gex"], 0.0)
        self.assertEqual(result["regime"], "negative_gamma")
        self.assertIn("amplifies", result["regime_note"])

    def test_vanna_and_charm_aggregates_present_with_notes(self):
        result = _service(_StubGateway(chains=_default_chains())).get_gex_profile("NVDA")
        self.assertIn("net_vanna_exposure", result)
        self.assertIn("net_charm_exposure", result)
        expected_vanna_note = ("Positive dealer vanna" if result["net_vanna_exposure"] > 0
                               else "Negative dealer vanna")
        expected_charm_note = ("Positive dealer charm" if result["net_charm_exposure"] > 0
                               else "Negative dealer charm")
        self.assertTrue(result["vanna_note"].startswith(expected_vanna_note))
        self.assertTrue(result["charm_note"].startswith(expected_charm_note))

    def test_summary_persisted_via_store(self):
        store = Mock()
        result = _service(_StubGateway(chains=_default_chains()), store).get_gex_profile("nvda")
        store.save_gex_summary.assert_called_once_with("NVDA", result)

    def test_skips_zero_oi_and_zero_strike_contracts(self):
        chains = {
            EXP_NEAR: SimpleNamespace(
                calls=_side([0.0, 105.0], [1000, 0]),   # zero strike + zero OI
                puts=_side([95.0], [500]),
            )
        }
        result = _service(_StubGateway(chains=chains)).get_gex_profile("NVDA")
        strikes = {row["strike"] for row in result["gex_ladder"]}
        self.assertEqual(strikes, {95.0})

    def test_failed_expiration_is_skipped_not_fatal(self):
        chains = _default_chains()
        gw = _StubGateway(expirations=[EXP_FAR, EXP_NEAR], chains=chains,
                          failing_expirations=[EXP_FAR])
        result = _service(gw).get_gex_profile("NVDA")
        self.assertEqual(result["expirations_scanned"], [EXP_NEAR])
        self.assertEqual(len(result["by_expiration"]), 1)

    def test_no_expirations_degrades_gracefully(self):
        store = Mock()
        result = _service(_StubGateway(expirations=[]), store).get_gex_profile("NVDA")
        self.assertEqual(result["signal"], "none")
        self.assertEqual(result["interpretation"], "No options data available.")
        store.save_gex_summary.assert_not_called()

    def test_all_expirations_failing_degrades_gracefully(self):
        gw = _StubGateway(expirations=[EXP_NEAR], chains={},
                          failing_expirations=[EXP_NEAR])
        result = _service(gw).get_gex_profile("NVDA")
        self.assertEqual(result["signal"], "none")
        self.assertIn("Could not compute GEX profile", result["interpretation"])

    def test_missing_price_raises(self):
        with self.assertRaises(ValueError):
            _service(_StubGateway(price=None)).get_gex_profile("NVDA")

    def test_max_expirations_caps_the_scan(self):
        chains = {
            EXP_NEAR: _default_chains()[EXP_NEAR],
            EXP_FAR: SimpleNamespace(calls=_side([105.0], [100]),
                                     puts=_side([95.0], [100])),
        }
        gw = _StubGateway(expirations=[EXP_NEAR, EXP_FAR], chains=chains)
        result = _service(gw).get_gex_profile("NVDA", max_expirations=1)
        self.assertEqual(result["expirations_scanned"], [EXP_NEAR])


class TestGexHistoryWrapper(unittest.TestCase):
    def _service_with_rows(self, rows):
        store = Mock()
        store.get_gex_history.return_value = rows
        return _service(_StubGateway(), store)

    def test_empty_history_note(self):
        result = self._service_with_rows([]).get_gex_history("nvda", since_days=30)
        self.assertEqual(result["symbol"], "NVDA")
        self.assertEqual(result["since_days"], 30)
        self.assertEqual(result["data_points"], 0)
        self.assertEqual(result["history"], [])
        self.assertIn("No history yet", result["note"])

    def test_history_passthrough(self):
        rows = [{"date": "2026-07-14", "captured_at": "2026-07-14T20:00:00Z",
                 "price": 100.0, "net_gex": 5.0, "zero_gamma_level": 99.0,
                 "regime": "positive_gamma"}]
        result = self._service_with_rows(rows).get_gex_history("NVDA")
        self.assertEqual(result["data_points"], 1)
        self.assertEqual(result["history"], rows)
        self.assertEqual(result["note"], "One row per calendar day, last write wins.")


if __name__ == "__main__":
    unittest.main()
