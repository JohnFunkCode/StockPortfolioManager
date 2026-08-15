"""NAV math tests — the regression guard against the wrong-denominator error.

The centrepiece is ``MstrRegressionTest``: the July 2026 MSTR assessment
inputs — recorded verbatim in ``MSTR`` below, which is now their only home —
must keep reproducing that assessment's conclusions: a ~10% net discount
rather than the ~37% headline, ~2.2%/yr of carry drag, and ~1.6x of
underlying exposure per dollar of equity. If a future refactor starts
dividing gross assets by share count again, this file fails.
"""
import unittest

from quantcore.analytics import nav

# Figures as of the assessment: MSTR $96.99, BTC $64,709, 843,775 BTC held,
# ~$6.7B converts + ~$8.5-10B preferred, ~$820M preferred service + ~$34M
# coupon interest, market cap ~$34B implying ~350M diluted shares.
MSTR = {
    "price": 96.99,
    "units": 843_775,
    "spot": 64_709,
    "diluted_shares": 350_000_000,
    "senior_claims": 16_500_000_000,
    "annual_senior_cost": 854_000_000,
}


class MstrRegressionTest(unittest.TestCase):
    def setUp(self):
        self.result = nav.analyze_nav_vehicle(**MSTR)

    def test_net_discount_is_about_ten_percent_not_thirty_seven(self):
        self.assertAlmostEqual(self.result["premium_discount_pct"], -10.9, delta=0.5)

    def test_gross_discount_is_reported_separately_and_is_much_larger(self):
        gross = self.result["gross_premium_discount_pct"]
        self.assertAlmostEqual(gross, -37.8, delta=0.5)
        # The whole point: the headline overstates the real gap by ~27 points.
        self.assertGreater(abs(gross) - abs(self.result["premium_discount_pct"]), 20)

    def test_senior_claims_are_netted_out_of_nav(self):
        self.assertAlmostEqual(self.result["gross_nav"], 54.6e9, delta=0.2e9)
        self.assertAlmostEqual(self.result["net_nav"], 38.1e9, delta=0.2e9)
        self.assertEqual(
            self.result["gross_nav"] - self.result["net_nav"],
            MSTR["senior_claims"],
        )

    def test_carry_drag_is_about_two_percent_per_year(self):
        self.assertAlmostEqual(self.result["carry_drag_pct"], 2.24, delta=0.1)

    def test_discount_funds_about_five_years_of_burn(self):
        self.assertAlmostEqual(self.result["years_of_burn"], 4.9, delta=0.3)

    def test_each_dollar_of_equity_carries_about_1_6_of_underlying(self):
        self.assertAlmostEqual(self.result["exposure_ratio"], 1.6, delta=0.1)


class NetNavPerShareTest(unittest.TestCase):
    def test_subtracts_senior_claims_before_dividing(self):
        # 100 units x $10 = $1,000 gross, less $400 senior, over 100 shares.
        self.assertEqual(
            nav.net_nav_per_share(units=100, spot=10, diluted_shares=100,
                                  senior_claims=400),
            6.0,
        )

    def test_other_assets_are_added(self):
        self.assertEqual(
            nav.net_nav_per_share(units=100, spot=10, diluted_shares=100,
                                  senior_claims=400, other_assets=200),
            8.0,
        )

    def test_insolvent_capital_structure_returns_none(self):
        """Senior claims above the stack leave the common an option, not a claim."""
        self.assertIsNone(
            nav.net_nav_per_share(units=100, spot=10, diluted_shares=100,
                                  senior_claims=1500)
        )

    def test_non_positive_share_count_returns_none(self):
        self.assertIsNone(nav.net_nav_per_share(100, 10, 0))
        self.assertIsNone(nav.net_nav_per_share(100, 10, -5))

    def test_missing_inputs_return_none(self):
        self.assertIsNone(nav.net_nav_per_share(None, 10, 100))
        self.assertIsNone(nav.net_nav_per_share(100, None, 100))
        self.assertIsNone(nav.net_nav_per_share(100, 10, "not-a-number"))


class PremiumDiscountTest(unittest.TestCase):
    def test_premium_is_positive_and_discount_negative(self):
        self.assertEqual(nav.premium_discount(110, 100), 10.0)
        self.assertEqual(nav.premium_discount(90, 100), -10.0)

    def test_non_positive_nav_returns_none(self):
        self.assertIsNone(nav.premium_discount(100, 0))
        self.assertIsNone(nav.premium_discount(100, -5))

    def test_missing_inputs_return_none(self):
        self.assertIsNone(nav.premium_discount(None, 100))
        self.assertIsNone(nav.premium_discount(100, None))


class CarryDragTest(unittest.TestCase):
    def test_cost_as_percentage_of_net_nav(self):
        self.assertEqual(nav.carry_drag(200, 10_000), 2.0)

    def test_non_positive_nav_returns_none(self):
        self.assertIsNone(nav.carry_drag(200, 0))


class YearsOfBurnTest(unittest.TestCase):
    def test_discount_divided_by_drag(self):
        self.assertAlmostEqual(nav.years_of_burn(-10.0, 2.0), 5.0)

    def test_a_premium_funds_nothing(self):
        self.assertIsNone(nav.years_of_burn(5.0, 2.0))

    def test_no_drag_means_no_burn_to_fund(self):
        self.assertIsNone(nav.years_of_burn(-10.0, 0.0))


class ExposureRatioTest(unittest.TestCase):
    def test_gross_over_market_cap(self):
        self.assertEqual(nav.exposure_ratio(160, 100), 1.6)

    def test_non_positive_cap_returns_none(self):
        self.assertIsNone(nav.exposure_ratio(160, 0))


class PremiumHistoryTest(unittest.TestCase):
    """Per-date series; capital structure held constant by design."""

    PRICES = [('2026-07-01', 96.99), ('2026-07-02', 90.0)]
    SPOTS = [('2026-07-01', 64_709), ('2026-07-02', 64_709)]

    def series(self, **overrides):
        kwargs = {
            "prices": self.PRICES, "spots": self.SPOTS, "units": 843_775,
            "diluted_shares": 350_000_000, "senior_claims": 16_500_000_000,
        }
        kwargs.update(overrides)
        return nav.premium_history(**kwargs)

    def test_each_point_matches_the_point_in_time_workup(self):
        rows = self.series()
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]["premium_discount_pct"], -10.9, delta=0.5)
        # Same NAV per share throughout — only price moves.
        self.assertEqual(rows[0]["nav_per_share"], rows[1]["nav_per_share"])
        self.assertLess(rows[1]["premium_discount_pct"], rows[0]["premium_discount_pct"])

    def test_dates_are_carried_through(self):
        self.assertEqual([r["date"] for r in self.series()],
                         ['2026-07-01', '2026-07-02'])

    def test_unusable_points_are_dropped_not_nulled(self):
        """An insolvent structure yields no per-share value; emitting a null
        hole would draw a broken line."""
        self.assertEqual(self.series(senior_claims=10**15), [])

    def test_empty_input_yields_empty_series(self):
        self.assertEqual(nav.premium_history([], [], 100, 100), [])


class AnalyzeNavVehicleTest(unittest.TestCase):
    def test_zero_senior_claims_makes_gross_and_net_agree(self):
        result = nav.analyze_nav_vehicle(price=95, units=100, spot=10,
                                         diluted_shares=100)
        self.assertEqual(result["premium_discount_pct"],
                         result["gross_premium_discount_pct"])
        # No senior stack means no bleed — 0.0 is the true drag, not "unknown".
        self.assertEqual(result["carry_drag_pct"], 0.0)
        self.assertIsNone(result["years_of_burn"])

    def test_insolvent_structure_degrades_without_raising(self):
        result = nav.analyze_nav_vehicle(price=5, units=100, spot=10,
                                         diluted_shares=100,
                                         senior_claims=2000)
        self.assertIsNone(result["nav_per_share"])
        self.assertIsNone(result["premium_discount_pct"])
        self.assertIsNone(result["years_of_burn"])


if __name__ == "__main__":
    unittest.main()
