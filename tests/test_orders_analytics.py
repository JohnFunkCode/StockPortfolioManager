"""`analytics.orders` and the session helpers it uses (#431 step 3.3). Pure
functions: no database, no network."""

import datetime
import json
import re
import unittest
from decimal import Decimal
from pathlib import Path

from quantcore.analytics import orders as om
from quantcore.analytics.market_time import ET, next_trading_day, upcoming_session

D = Decimal
REPO = Path(__file__).resolve().parent.parent


def et(year, month, day, hour=12, minute=0):
    return ET.localize(datetime.datetime(year, month, day, hour, minute))


# Monday 2026-06-15 is an ordinary trading day.
OPEN_NOW = et(2026, 6, 15, 11, 0)


def place(now=OPEN_NOW, **quote):
    facts = {
        "has_quote": True, "currency": "USD", "market_state": "REGULAR",
        "regular_price": 123.456, "regular_time": int(now.timestamp()) - 20,
    }
    facts.update(quote)
    return om.placement(now=now, **facts)


class SessionHelpersTest(unittest.TestCase):
    def test_next_trading_day_skips_weekends_and_holidays(self):
        self.assertEqual(next_trading_day(datetime.date(2026, 6, 15)), datetime.date(2026, 6, 16))
        self.assertEqual(next_trading_day(datetime.date(2026, 6, 19)), datetime.date(2026, 6, 22))
        # Thursday 2026-07-02, then Independence Day observed on Friday the 3rd.
        self.assertEqual(next_trading_day(datetime.date(2026, 7, 2)), datetime.date(2026, 7, 6))

    def test_upcoming_session_is_today_only_before_the_open(self):
        self.assertEqual(upcoming_session(et(2026, 6, 15, 8, 0)), datetime.date(2026, 6, 15))
        self.assertEqual(upcoming_session(et(2026, 6, 15, 9, 30)), datetime.date(2026, 6, 16))
        self.assertEqual(upcoming_session(et(2026, 6, 15, 18, 0)), datetime.date(2026, 6, 16))

    def test_upcoming_session_on_a_weekend_or_holiday_is_the_next_trading_day(self):
        self.assertEqual(upcoming_session(et(2026, 6, 13, 8, 0)), datetime.date(2026, 6, 15))
        self.assertEqual(upcoming_session(et(2026, 7, 3, 8, 0)), datetime.date(2026, 7, 6))

    def test_upcoming_session_reads_the_clock_in_market_time(self):
        # 01:00 UTC on the 16th is 21:00 ET on the 15th.
        late = datetime.datetime(2026, 6, 16, 1, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(upcoming_session(late), datetime.date(2026, 6, 16))


class PlacementTest(unittest.TestCase):
    def test_in_hours_with_a_fresh_quote_fills_at_the_last_trade(self):
        plan = place()

        self.assertEqual((plan.action, plan.reason), (om.FILL, None))
        self.assertEqual(plan.session_date, datetime.date(2026, 6, 15))
        self.assertEqual(plan.price, D("123.46"))
        self.assertEqual(plan.quote_time.timestamp(), OPEN_NOW.timestamp() - 20)

    def test_no_quote_is_rejected_at_any_hour(self):
        for now in (OPEN_NOW, et(2026, 6, 15, 20, 0), et(2026, 6, 13, 12, 0)):
            with self.subTest(now=now):
                plan = place(now=now, has_quote=False, currency=None, market_state=None,
                             regular_price=None, regular_time=None)
                self.assertEqual((plan.action, plan.reason), (om.REJECT, om.NO_QUOTE))

    def test_a_quote_with_no_currency_is_rejected(self):
        for currency in (None, "", "  "):
            with self.subTest(currency=currency):
                self.assertEqual(place(currency=currency).reason, om.NO_QUOTE)

    def test_a_non_usd_symbol_is_rejected_at_any_hour(self):
        for now in (OPEN_NOW, et(2026, 6, 15, 20, 0)):
            with self.subTest(now=now):
                plan = place(now=now, currency="SEK")
                self.assertEqual((plan.action, plan.reason), (om.REJECT, om.NON_USD))
        self.assertEqual(place(currency="usd").action, om.FILL)

    def test_a_last_trade_older_than_the_limit_is_stale(self):
        at_limit = place(regular_time=int(OPEN_NOW.timestamp()) - om.MAX_QUOTE_AGE_SECONDS)
        past_it = place(regular_time=int(OPEN_NOW.timestamp()) - om.MAX_QUOTE_AGE_SECONDS - 1)
        no_time = place(regular_time=None)

        self.assertEqual(at_limit.action, om.FILL)
        self.assertEqual((past_it.action, past_it.reason), (om.REJECT, om.STALE_QUOTE))
        self.assertEqual(no_time.reason, om.STALE_QUOTE)

    def test_in_hours_with_no_price_is_rejected(self):
        for price in (None, 0, -1.0):
            with self.subTest(price=price):
                self.assertEqual(place(regular_price=price).reason, om.NO_QUOTE)

    def test_after_the_close_queues_for_the_next_session(self):
        plan = place(now=et(2026, 6, 15, 16, 0), market_state="POST")

        self.assertEqual((plan.action, plan.reason, plan.price), (om.QUEUE, None, None))
        self.assertEqual(plan.session_date, datetime.date(2026, 6, 16))

    def test_before_the_open_queues_for_today(self):
        plan = place(now=et(2026, 6, 15, 7, 0), market_state="PRE")
        self.assertEqual((plan.action, plan.session_date), (om.QUEUE, datetime.date(2026, 6, 15)))

    def test_a_weekend_queues_for_monday(self):
        plan = place(now=et(2026, 6, 13, 11, 0), market_state="CLOSED")
        self.assertEqual((plan.action, plan.session_date), (om.QUEUE, datetime.date(2026, 6, 15)))

    def test_a_holiday_queues_though_the_clock_says_open(self):
        # Friday 2026-07-03, 11:00: a weekday in market hours, and the NYSE is shut.
        plan = place(now=et(2026, 7, 3, 11, 0), market_state="CLOSED",
                     regular_time=int(et(2026, 7, 2, 16, 0).timestamp()))
        self.assertEqual((plan.action, plan.session_date), (om.QUEUE, datetime.date(2026, 7, 6)))

    def test_a_half_day_afternoon_queues_for_the_next_session(self):
        # The clock and the calendar both say open. Only the exchange knows
        # it closed at 13:00.
        for state in ("POST", "POSTPOST", "CLOSED"):
            with self.subTest(state=state):
                plan = place(now=et(2026, 11, 27, 14, 0), market_state=state)
                self.assertEqual(
                    (plan.action, plan.session_date), (om.QUEUE, datetime.date(2026, 11, 30))
                )

    def test_a_quote_still_in_premarket_after_the_open_is_stale(self):
        plan = place(now=et(2026, 6, 15, 9, 31), market_state="PRE")
        self.assertEqual((plan.action, plan.reason), (om.REJECT, om.STALE_QUOTE))

    def test_an_unknown_market_state_falls_back_to_the_clock_and_the_trade_time(self):
        self.assertEqual(place(market_state=None).action, om.FILL)
        self.assertEqual(place(market_state="", regular_time=1).reason, om.STALE_QUOTE)

    def test_a_rejection_is_recorded_against_a_trading_day(self):
        plan = place(now=et(2026, 6, 13, 11, 0), currency="SEK")
        self.assertEqual(plan.session_date, datetime.date(2026, 6, 15))


class FillNumbersTest(unittest.TestCase):
    def test_fill_price_rounds_to_the_tick(self):
        self.assertEqual(om.fill_price(123.45999908447266), D("123.46"))
        self.assertEqual(om.fill_price(10.005), D("10.01"))
        self.assertEqual(om.fill_price(1), D("1.00"))
        self.assertEqual(om.fill_price(0.123449), D("0.1234"))
        self.assertEqual(om.fill_price(0.99995), D("1.0000"))

    def test_a_dollar_buy_never_costs_more_than_its_amount(self):
        for amount, price in (("1000", "123.45"), ("0.01", "3"), ("500", "0.3333"), ("99.99", "7")):
            with self.subTest(amount=amount, price=price):
                shares = om.shares_for_amount(D(amount), D(price))
                self.assertLessEqual(om.trade_value(shares, D(price)), D(amount))
                self.assertEqual(shares, shares.quantize(D("0.000001")))
        self.assertEqual(om.shares_for_amount(D("1000"), D("123.45")), D("8.100445"))
        self.assertEqual(om.shares_for_amount(D("0.01"), D("700000")), D("0"))

    def test_buy_shares_prefers_the_count_asked_for(self):
        self.assertEqual(om.buy_shares(D("3"), None, D("10")), D("3"))
        self.assertEqual(om.buy_shares(None, D("25"), D("10")), D("2.5"))

    def test_trade_value_is_to_the_cent(self):
        self.assertEqual(om.trade_value(D("3"), D("10.005")), D("30.02"))

    def test_a_paper_book_has_no_margin(self):
        self.assertTrue(om.can_afford(D("0")))
        self.assertTrue(om.can_afford(D("0.01")))
        self.assertFalse(om.can_afford(D("-0.01")))
        self.assertFalse(om.can_afford(None))

    def test_sellable_shares_leaves_out_what_queued_sells_have_promised(self):
        self.assertEqual(om.sellable_shares(D("10"), D("4")), D("6"))
        self.assertEqual(om.sellable_shares(D("10"), D("12")), D("0"))

    def test_the_vocabularies_match_the_schema(self):
        # The CHECK constraints in the committed snapshot are the schema's
        # word lists. A value added on one side only fails here.
        snapshot = json.loads((REPO / "db" / "schema_snapshot.json").read_text())["tables"]
        orders = snapshot["orders"]["constraints"]

        def words(check):
            return set(re.findall(r"'(\w+)'::text", check))

        self.assertEqual(words(orders["orders_status_check"]), {om.PENDING, *om.TERMINAL_STATUSES})
        self.assertEqual(words(orders["orders_side_check"]), set(om.SIDES))
        self.assertEqual(words(orders["orders_source_check"]), set(om.SOURCES))
        self.assertEqual(words(orders["orders_reject_reason_check"]), set(om.REJECT_REASONS))
        self.assertEqual(
            words(orders["orders_price_source_check"]),
            {om.PRICE_FROM_QUOTE, om.PRICE_FROM_SESSION_OPEN},
        )
        self.assertEqual(
            words(snapshot["lot_sales"]["constraints"]["lot_sales_source_check"]), set(om.SOURCES)
        )


if __name__ == "__main__":
    unittest.main()
