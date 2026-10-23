"""Unit tests for quantcore.analytics.stops — technical and trailing stop math."""

import unittest

from quantcore.analytics import stops


class SqueezePotentialTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(stops.squeeze_potential(20, 5), "HIGH")
        self.assertEqual(stops.squeeze_potential(20, 4.9), "MEDIUM")
        self.assertEqual(stops.squeeze_potential(10, 0), "MEDIUM")
        self.assertEqual(stops.squeeze_potential(0, 3), "MEDIUM")
        self.assertEqual(stops.squeeze_potential(9.9, 2.9), "LOW")


class CandidateSupportsTests(unittest.TestCase):
    def test_only_levels_below_price_highest_first(self):
        result = stops.candidate_supports(100.0, 97.0, 98.0, 96.0, 90.0)
        self.assertEqual(result, [("vwap", 98.0), ("gamma_wall", 97.0),
                                  ("sma_20", 96.0), ("bb_lower", 90.0)])

    def test_gamma_wall_above_price_or_missing_and_vwap_resistance_excluded(self):
        self.assertEqual(stops.candidate_supports(100.0, 105.0, 101.0, 96.0, 90.0),
                         [("sma_20", 96.0), ("bb_lower", 90.0)])
        self.assertEqual(stops.candidate_supports(100.0, None, 101.0, 102.0, 103.0), [])


class TechnicalStopTests(unittest.TestCase):
    def test_buffer_depends_on_support_kind(self):
        self.assertEqual(stops.technical_stop([("gamma_wall", 100.0)], 90.0),
                         ("gamma_wall", 100.0, 99.2))
        self.assertEqual(stops.technical_stop([("vwap", 100.0)], 90.0),
                         ("vwap", 100.0, 98.6))
        self.assertEqual(stops.technical_stop([("bb_lower", 100.0)], 90.0),
                         ("bb_lower", 100.0, 98.0))

    def test_falls_back_to_two_pct_under_lower_band(self):
        self.assertEqual(stops.technical_stop([], 90.0), ("bb_lower", 90.0, 88.2))


class TrailingTests(unittest.TestCase):
    def test_high_si_downtrend_tightens(self):
        pct, note = stops.calibrate_trailing(10.0, 15.0, above_vwap=False)
        self.assertEqual(pct, 9.0)
        self.assertTrue(note.startswith("tightened"))

    def test_high_si_uptrend_widens(self):
        pct, note = stops.calibrate_trailing(10.0, 20.0, above_vwap=True)
        self.assertEqual(pct, 11.0)
        self.assertTrue(note.startswith("widened"))

    def test_otherwise_neutral(self):
        self.assertEqual(stops.calibrate_trailing(10.0, 19.9, above_vwap=True), (10.0, "neutral"))

    def test_price_and_distance(self):
        self.assertEqual(stops.trailing_stop_price(180.0, 7.15), 167.13)
        self.assertEqual(stops.distance_pct(171.12, 180.0), -4.93)


class PositionPnlTests(unittest.TestCase):
    def test_empty_without_cost_basis(self):
        self.assertEqual(stops.position_pnl(100.0, 0.0, 10, 95.0, 92.0), {})

    def test_totals_scale_by_shares(self):
        pnl = stops.position_pnl(180.0, 142.5, 120, 171.12, 167.13)
        self.assertEqual(pnl["total_unrealized_pnl"], 4500.0)
        self.assertEqual(pnl["pnl_at_technical_stop"], 3434.4)
        self.assertEqual(pnl["unrealized_pnl_pct"], 26.32)

    def test_no_shares_means_per_share(self):
        pnl = stops.position_pnl(110.0, 100.0, 0, 105.0, 104.0)
        self.assertEqual(pnl["total_unrealized_pnl"], 10.0)
        self.assertEqual(pnl["pnl_at_trailing_stop"], 4.0)


class FlagsAndSummaryTests(unittest.TestCase):
    def _flags(self, **over):
        kw = dict(vwap_bars=3, above_vwap=True, macd_crossover="bullish", rsi=50.0,
                  stop_inside_noise=False, squeeze="LOW", recent_1day_pct=2.0,
                  max_1day_pct=5.0)
        kw.update(over)
        return stops.stop_flags(**kw)

    def test_baseline(self):
        self.assertEqual(self._flags(), ["3_bars_above_vwap", "bullish_macd"])

    def test_every_optional_flag(self):
        flags = self._flags(above_vwap=False, macd_crossover="bearish_crossover", rsi=20.0,
                            stop_inside_noise=True, squeeze="HIGH", recent_1day_pct=8.0)
        self.assertEqual(flags, ["3_bars_below_vwap", "bearish_macd", "rsi_oversold_20",
                                 "technical_stop_inside_noise_floor", "squeeze_potential_high",
                                 "elevated_recent_volatility"])

    def test_summary_mentions_warning_and_position_only_when_relevant(self):
        base = dict(symbol="XYZ", price=100.0, above_vwap=True, vwap_bars=3, vwap=98.0,
                    macd_crossover="bullish", rsi=50.0, primary_name="sma_20",
                    primary_level=96.0, technical_stop_price=94.66, technical_dist_pct=-5.34,
                    stop_inside_noise=False, max_1day_pct=4.0, trailing_pct=8.0,
                    trailing_stop_price=92.0, cost_basis=0.0, shares=0, position={})
        plain = stops.stop_summary(**base)
        self.assertNotIn("WARNING", plain)
        self.assertNotIn("Position:", plain)
        self.assertIn("Primary support: sma 20 at $96.00.", plain)
        loaded = stops.stop_summary(**{**base, "stop_inside_noise": True, "cost_basis": 80.0,
                                       "shares": 10, "position": {"pnl_at_trailing_stop": 120.0}})
        self.assertIn("WARNING", loaded)
        self.assertIn("P&L at trailing stop: $+120.00.", loaded)


if __name__ == "__main__":
    unittest.main()
