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


def _zone(center, low, methods, high=None, score=1.0):
    return {"center": center, "zone_low": low, "zone_high": high or center,
            "method_count": methods, "score": score}


class ConfluenceStopTests(unittest.TestCase):
    def test_nearest_qualifying_zone_below_price_with_buffer(self):
        zones = [_zone(105.0, 104.0, 3), _zone(97.0, 96.5, 1), _zone(95.0, 94.5, 2),
                 _zone(90.0, 89.5, 4)]
        stop, reason = stops.confluence_stop(zones, 100.0, 4.0)
        self.assertEqual((stop["center"], stop["stop"]), (95.0, 93.74))  # 94.5 × 0.992
        self.assertEqual(stop["distance_pct"], -6.26)
        self.assertFalse(stop["stepped_past_noise"])
        self.assertIsNone(reason)

    def test_steps_down_past_a_zone_inside_the_noise_floor(self):
        zones = [_zone(98.0, 97.5, 2), _zone(90.0, 89.5, 2)]
        stop, reason = stops.confluence_stop(zones, 100.0, 4.0)
        self.assertEqual(stop["center"], 90.0)
        self.assertTrue(stop["stepped_past_noise"])
        self.assertIn("$98.00", reason)

    def test_stop_exactly_on_the_noise_floor_clears_it(self):
        # 97.5 x 0.992 = 96.72 -> -3.28%; a 3.28% floor is not "inside".
        stop, _ = stops.confluence_stop([_zone(98.0, 97.5, 2)], 100.0, 3.28)
        self.assertEqual(stop["stop"], 96.72)

    def test_none_when_no_zone_qualifies(self):
        stop, reason = stops.confluence_stop(
            [_zone(95.0, 94.5, 1), _zone(105.0, 104.0, 5)], 100.0, 4.0)
        self.assertIsNone(stop)
        self.assertIn("2 or more methods", reason)
        self.assertEqual(stops.confluence_stop([], 100.0, 4.0)[0], None)

    def test_none_when_every_zone_is_inside_the_noise_floor(self):
        stop, reason = stops.confluence_stop([_zone(98.0, 97.5, 2)], 100.0, 10.0)
        self.assertIsNone(stop)
        self.assertIn("Every qualifying zone ($98.00)", reason)


class DrawdownReplayTests(unittest.TestCase):
    _DROPS = {"recent_1day": -2.0, "max_1day": -5.0, "max_intraday": -7.0, "max_5day": -12.0}

    def test_rows_in_order_with_hits(self):
        rows = stops.drawdown_replay(100.0, self._DROPS, 95.0, 92.0, 90.0)
        self.assertEqual([r["key"] for r in rows],
                         ["recent_1day", "max_1day", "max_intraday", "max_5day"])
        self.assertEqual([r["price"] for r in rows], [98.0, 95.0, 93.0, 88.0])
        self.assertEqual([r["drop_pct"] for r in rows], [-2.0, -5.0, -7.0, -12.0])
        # Reaching a stop exactly counts as a hit.
        self.assertEqual([r["hits_technical"] for r in rows], [False, True, True, True])
        self.assertEqual([r["hits_trailing"] for r in rows], [False, False, False, True])
        self.assertEqual([r["hits_confluence"] for r in rows], [False, False, False, True])

    def test_a_window_with_only_gains_replays_as_no_drop(self):
        # A +2% worst day is not a 2% loss: today's price, no stop hit (#418 review).
        rows = stops.drawdown_replay(
            100.0, {**self._DROPS, "recent_1day": 2.0, "max_5day": 0.0}, 99.5, 99.0, 99.8)
        by_key = {r["key"]: r for r in rows}
        for key in ("recent_1day", "max_5day"):
            self.assertEqual((by_key[key]["drop_pct"], by_key[key]["price"]), (0.0, 100.0))
            self.assertEqual((by_key[key]["hits_technical"], by_key[key]["hits_trailing"],
                              by_key[key]["hits_confluence"]), (False, False, False))

    def test_no_confluence_stop_means_no_verdict(self):
        rows = stops.drawdown_replay(100.0, self._DROPS, 95.0, 92.0, None)
        self.assertTrue(all(r["hits_confluence"] is None for r in rows))


if __name__ == "__main__":
    unittest.main()
