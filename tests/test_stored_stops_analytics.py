"""`analytics.stored_stops`: the terms of a stored stop (#431 plan step 4.1).

Pure functions, no database.
"""

import unittest
from decimal import Decimal

from quantcore.analytics import stored_stops as stops_math
from quantcore.analytics.orders import SOURCES as ORDER_SOURCES

D = Decimal


class VocabularyTest(unittest.TestCase):
    def test_only_active_is_not_final(self):
        self.assertEqual(
            set(stops_math.STATUSES) - set(stops_math.TERMINAL_STATUSES), {stops_math.ACTIVE}
        )

    def test_a_stop_can_be_set_by_anything_that_can_trade_except_a_stop(self):
        self.assertEqual(set(stops_math.SOURCES), set(ORDER_SOURCES) - {"stop"})

    def test_manual_is_a_method_and_a_source(self):
        self.assertIn(stops_math.MANUAL, stops_math.METHODS)
        self.assertIn(stops_math.MANUAL, stops_math.SOURCES)


class TrailTest(unittest.TestCase):
    def test_trail_percent_keeps_four_decimals(self):
        self.assertEqual(stops_math.trail_percent(D("7.123456")), D("7.1235"))

    def test_trail_percent_must_be_inside_0_and_100(self):
        for bad in ("0", "-1", "100", "250", "0.00001"):
            with self.subTest(pct=bad), self.assertRaises(ValueError):
                stops_math.trail_percent(D(bad))

    def test_trailing_level_is_the_percentage_below_the_mark_to_the_tick(self):
        self.assertEqual(stops_math.trailing_level(D("100.00"), D("8")), D("92.00"))
        self.assertEqual(stops_math.trailing_level(D("33.33"), D("7.5")), D("30.83"))
        # Under a dollar the tick is a hundredth of a cent.
        self.assertEqual(stops_math.trailing_level(D("0.5000"), D("10")), D("0.4500"))


class StopTermsTest(unittest.TestCase):
    def test_a_fixed_stop_is_its_level_to_the_tick(self):
        terms = stops_math.stop_terms("fixed", stop_level=D("91.456"), reference=D("100.00"))
        self.assertEqual(terms, stops_math.StopTerms(kind="fixed", stop_level=D("91.46")))

    def test_a_trailing_stop_starts_from_the_last_close(self):
        terms = stops_math.stop_terms("trailing", trail_pct=D("8"), reference=D("100.00"))
        self.assertEqual(
            terms,
            stops_math.StopTerms(
                kind="trailing", stop_level=D("92.00"), trail_pct=D("8.0000"),
                high_water_mark=D("100.00"),
            ),
        )

    def test_a_stop_must_be_below_the_last_close(self):
        for level in ("100.00", "100.004", "150"):
            with self.subTest(level=level), self.assertRaisesRegex(ValueError, "below the last close"):
                stops_math.stop_terms("fixed", stop_level=D(level), reference=D("100.00"))
        # One tick under is a stop.
        self.assertEqual(
            stops_math.stop_terms("fixed", stop_level=D("99.99"), reference=D("100.00")).stop_level,
            D("99.99"),
        )

    def test_with_no_close_a_fixed_stop_is_taken_as_typed(self):
        terms = stops_math.stop_terms("fixed", stop_level=D("500"), reference=None)
        self.assertEqual(terms.stop_level, D("500.00"))

    def test_with_no_close_a_trailing_stop_is_refused(self):
        with self.assertRaisesRegex(ValueError, "none is on record"):
            stops_math.stop_terms("trailing", trail_pct=D("8"), reference=None)

    def test_a_trail_too_small_to_move_the_level_a_tick_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no price below the last close"):
            stops_math.stop_terms("trailing", trail_pct=D("0.0001"), reference=D("10.00"))

    def test_each_kind_takes_only_its_own_field(self):
        cases = [
            ("fixed", {}, "needs a stop_level"),
            ("fixed", {"stop_level": D("9"), "trail_pct": D("5")}, "not a trail_pct"),
            ("trailing", {}, "needs a trail_pct"),
            ("trailing", {"stop_level": D("9"), "trail_pct": D("5")}, "not a stop_level"),
            ("limit", {"stop_level": D("9")}, "fixed or trailing"),
        ]
        for kind, fields, message in cases:
            with self.subTest(kind=kind, fields=fields), self.assertRaisesRegex(ValueError, message):
                stops_math.stop_terms(kind, reference=D("10.00"), **fields)

    def test_a_level_must_be_a_price(self):
        for level in ("0", "-5", "0.00004"):
            with self.subTest(level=level), self.assertRaises(ValueError):
                stops_math.stop_terms("fixed", stop_level=D(level), reference=D("10.00"))


if __name__ == "__main__":
    unittest.main()
