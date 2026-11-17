"""`analytics.stored_stops`: the terms of a stored stop (#431 plan step 4.1).

Pure functions, no database.
"""

import datetime
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


class BrokerOrderTest(unittest.TestCase):
    """What a stop at an outside broker records (plan step 4.5)."""

    def test_a_stop_only_here_has_no_details(self):
        self.assertIs(stops_math.broker_order("fixed", at_broker=False), stops_math.NOT_AT_BROKER)
        self.assertIs(
            stops_math.broker_order("fixed", at_broker=False, brokerage="   "),
            stops_math.NOT_AT_BROKER)

    def test_details_without_a_broker_are_refused_not_dropped(self):
        for details in ({"quantity": D("1")}, {"order_type": "stop"}, {"brokerage": "Schwab"}):
            with self.subTest(details=details), self.assertRaisesRegex(ValueError, "at_broker"):
                stops_math.broker_order("fixed", at_broker=False, **details)

    def test_at_a_broker_each_detail_is_optional(self):
        self.assertEqual(
            stops_math.broker_order("fixed", at_broker=True),
            stops_math.BrokerOrder(True, None, None, None))
        self.assertEqual(
            stops_math.broker_order(
                "fixed", at_broker=True, quantity=D("2.5"), order_type="stop_limit",
                brokerage=" Schwab "),
            stops_math.BrokerOrder(True, D("2.5"), "stop_limit", "Schwab"))

    def test_the_order_type_suits_the_kind(self):
        for kind, allowed in (("fixed", ("stop", "stop_limit")), ("trailing", ("trailing_stop",))):
            for order_type in stops_math.BROKER_ORDER_TYPES:
                with self.subTest(kind=kind, order_type=order_type):
                    if order_type in allowed:
                        self.assertEqual(stops_math.broker_order(
                            kind, at_broker=True, order_type=order_type).order_type, order_type)
                    else:
                        with self.assertRaisesRegex(ValueError, "must be one of"):
                            stops_math.broker_order(kind, at_broker=True, order_type=order_type)

    def test_a_quantity_is_positive_and_a_lots_precision(self):
        for quantity, message in ((D("0"), "more than zero"), (D("-3"), "more than zero"),
                                  (D("0.0000001"), "six decimal places")):
            with self.subTest(quantity=quantity), self.assertRaisesRegex(ValueError, message):
                stops_math.broker_order("fixed", at_broker=True, quantity=quantity)

    def test_a_brokerage_has_a_length_limit(self):
        name = "x" * stops_math.MAX_BROKERAGE_LENGTH
        self.assertEqual(stops_math.broker_order("fixed", at_broker=True, brokerage=name).brokerage, name)
        with self.assertRaisesRegex(ValueError, "at most 60"):
            stops_math.broker_order("fixed", at_broker=True, brokerage=name + "x")


class HoldingStopsTest(unittest.TestCase):
    """`holding_stops` (plan decision 66): the one stop each holding shows."""

    JAN = datetime.date(2026, 1, 2)
    JUNE = datetime.date(2026, 6, 16)

    @staticmethod
    def stop(stop_id, symbol="AAA", status="active", triggered_session=None):
        return {"stop_id": stop_id, "symbol": symbol, "status": status,
                "triggered_session": triggered_session}

    def shown(self, stops, opened):
        return [s["stop_id"] for s in stops_math.holding_stops(stops, opened)]

    def test_a_held_symbol_shows_its_active_stop(self):
        self.assertEqual(self.shown([self.stop(3)], {"AAA": [self.JAN]}), [3])

    def test_only_the_newest_stop_of_a_symbol_is_looked_at(self):
        stops = [self.stop(9, status="cancelled"), self.stop(8)]
        self.assertEqual(self.shown(stops, {"AAA": [self.JAN]}), [])
        stops = [self.stop(9), self.stop(8, status="replaced")]
        self.assertEqual(self.shown(stops, {"AAA": [self.JAN]}), [9])

    def test_a_symbol_the_book_does_not_hold_shows_nothing(self):
        self.assertEqual(self.shown([self.stop(3)], {}), [])
        self.assertEqual(self.shown([self.stop(3)], {"BBB": [self.JAN]}), [])

    def test_a_reached_stop_is_shown_only_to_a_lot_opened_by_its_bar(self):
        reached = [self.stop(3, status="triggered", triggered_session=self.JUNE)]
        self.assertEqual(self.shown(reached, {"AAA": [self.JUNE]}), [3])
        self.assertEqual(self.shown(reached, {"AAA": [self.JAN]}), [3])
        later = self.JUNE + datetime.timedelta(days=1)
        self.assertEqual(self.shown(reached, {"AAA": [later]}), [])
        self.assertEqual(self.shown(reached, {"AAA": [later, self.JAN]}), [3])
        self.assertEqual(self.shown(reached, {"AAA": []}), [])

    def test_an_active_stop_is_shown_even_when_no_lot_has_a_date(self):
        self.assertEqual(self.shown([self.stop(3)], {"AAA": []}), [3])

    def test_stops_that_ended_any_other_way_show_nothing(self):
        for status in ("cancelled", "replaced", "closed"):
            with self.subTest(status=status):
                self.assertEqual(self.shown([self.stop(3, status=status)], {"AAA": [self.JAN]}), [])

    def test_the_answer_is_in_symbol_order(self):
        stops = [self.stop(1, "ZZZ"), self.stop(2, "AAA"), self.stop(3, "MMM")]
        opened = {"ZZZ": [self.JAN], "AAA": [self.JAN], "MMM": [self.JAN]}
        self.assertEqual(self.shown(stops, opened), [2, 3, 1])



if __name__ == "__main__":
    unittest.main()
