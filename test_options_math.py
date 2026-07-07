"""Pure-math unit tests for quantcore.analytics.options_math.

These pin the deduplicated Black-Scholes / max-pain / expected-move / chain-side
math (single home for what used to be copied across stock_price_server.py,
api/app.py and options_contract_tools.py). No I/O, no network — synthetic inputs
with exact expected values.
"""

import math
import unittest

import pandas as pd

from quantcore.analytics import options_math as om


class TestBlackScholes(unittest.TestCase):
    def test_atm_call_delta_near_half(self):
        # At-the-money call delta is slightly above 0.5 (positive drift from r).
        d = om.bs_delta(100, 100, 0.25, 0.40, 0.045, is_call=True)
        self.assertTrue(0.5 < d < 0.62)

    def test_put_call_delta_relationship(self):
        # call_delta - put_delta == 1 for the same strike/expiry (same d1).
        call = om.bs_delta(100, 95, 0.5, 0.35, 0.045, is_call=True)
        put = om.bs_delta(100, 95, 0.5, 0.35, 0.045, is_call=False)
        self.assertAlmostEqual(call - put, 1.0, places=12)

    def test_degenerate_inputs_return_half(self):
        self.assertEqual(om.bs_delta(0, 100, 0.25, 0.4, 0.045, True), 0.5)
        self.assertEqual(om.bs_delta(100, 100, 0.0, 0.4, 0.045, False), -0.5)

    def test_gamma_positive_and_peaks_atm(self):
        atm = om.bs_gamma(100, 100, 0.25, 0.4, 0.045)
        otm = om.bs_gamma(100, 130, 0.25, 0.4, 0.045)
        self.assertGreater(atm, 0.0)
        self.assertGreater(atm, otm)

    def test_d1_degenerate_returns_none(self):
        self.assertIsNone(om.bs_d1(100, 100, 0.0, 0.4, 0.045))
        self.assertIsNone(om.bs_d1(100, 100, 0.25, 0.0, 0.045))


class TestChainSideFull(unittest.TestCase):
    def _df(self):
        return pd.DataFrame({
            "strike": [90, 100, 0],
            "lastPrice": [12.0, 5.1234, 9],
            "bid": [11.8, 5.0, 9],
            "ask": [12.2, 5.25, 9],
            "impliedVolatility": [0.4123, 0.3876, 0.9],
            "volume": [100, 200, 5],
            "openInterest": [500, 300, 5],
            "inTheMoney": [True, False, False],
        })

    def test_filters_zero_strike_and_sorts(self):
        out = om.chain_side_full(self._df(), iv_decimals=1)
        strikes = [c["strike"] for c in out["contracts"]]
        self.assertEqual(strikes, [90.0, 100.0])  # zero-strike row dropped
        self.assertEqual(out["total_open_interest"], 800)
        self.assertEqual(out["total_volume"], 300)

    def test_iv_decimals_precision(self):
        one = om.chain_side_full(self._df(), iv_decimals=1)
        two = om.chain_side_full(self._df(), iv_decimals=2)
        self.assertEqual(one["contracts"][0]["iv"], 41.2)
        self.assertEqual(two["contracts"][0]["iv"], 41.23)


class TestMaxPain(unittest.TestCase):
    def test_max_pain_strike(self):
        contracts = [
            {"kind": "call", "strike": 95, "open_interest": 100},
            {"kind": "call", "strike": 100, "open_interest": 300},
            {"kind": "put", "strike": 100, "open_interest": 250},
            {"kind": "put", "strike": 105, "open_interest": 40},
        ]
        strike, curve = om.compute_max_pain(contracts)
        self.assertIn(strike, curve)
        # Max pain minimises total dollar pain across the curve.
        self.assertEqual(curve[strike], min(curve.values()))

    def test_empty_contracts(self):
        self.assertEqual(om.compute_max_pain([]), (None, {}))


class TestExpectedMove(unittest.TestCase):
    def test_atm_straddle(self):
        contracts = [
            {"kind": "call", "strike": 100, "last_price": 3.5},
            {"kind": "put", "strike": 100, "last_price": 3.0},
        ]
        em_dollar, em_pct, atm = om.compute_expected_move(contracts, 100.0)
        self.assertAlmostEqual(em_dollar, 6.5)
        self.assertAlmostEqual(em_pct, 6.5)
        self.assertEqual(atm, 100.0)

    def test_no_contracts_or_zero_price(self):
        self.assertEqual(om.compute_expected_move([], 100.0), (0.0, 0.0, None))
        self.assertEqual(
            om.compute_expected_move([{"kind": "call", "strike": 100}], 0.0),
            (0.0, 0.0, None),
        )


if __name__ == "__main__":
    unittest.main()


class TestBsPrice(unittest.TestCase):
    """bs_price — reference implementation for the frontend spreadMath twin."""

    def test_textbook_call_and_put(self):
        # Classic fixture: S=100, K=100, T=1y, sigma=20%, r=5%
        call = om.bs_price(100, 100, 1.0, 0.20, 0.05, "call")
        put = om.bs_price(100, 100, 1.0, 0.20, 0.05, "put")
        self.assertAlmostEqual(call, 10.4506, places=3)
        self.assertAlmostEqual(put, 5.5735, places=3)

    def test_put_call_parity(self):
        S, K, T, sigma, r = 137.42, 150.0, 0.35, 0.61, 0.045
        call = om.bs_price(S, K, T, sigma, r, "call")
        put = om.bs_price(S, K, T, sigma, r, "put")
        self.assertAlmostEqual(call - put, S - K * math.exp(-r * T), places=6)

    def test_expiry_returns_intrinsic(self):
        self.assertAlmostEqual(om.bs_price(110, 100, 0.0, 0.3, 0.05, "call"), 10.0)
        self.assertAlmostEqual(om.bs_price(90, 100, 0.0, 0.3, 0.05, "call"), 0.0)
        self.assertAlmostEqual(om.bs_price(90, 100, 0.0, 0.3, 0.05, "put"), 10.0)

    def test_zero_vol_returns_intrinsic_floor(self):
        self.assertGreaterEqual(om.bs_price(120, 100, 0.5, 0.0, 0.05, "call"), 20.0 - 1e-9)

    def test_deep_itm_call_approaches_forward_intrinsic(self):
        price = om.bs_price(500, 100, 0.25, 0.2, 0.045, "call")
        self.assertAlmostEqual(price, 500 - 100 * math.exp(-0.045 * 0.25), places=2)

    def test_deep_otm_near_zero(self):
        self.assertLess(om.bs_price(50, 100, 0.1, 0.2, 0.045, "call"), 1e-6)

    def test_invalid_kind_raises(self):
        with self.assertRaises(ValueError):
            om.bs_price(100, 100, 1.0, 0.2, 0.05, "straddle")
