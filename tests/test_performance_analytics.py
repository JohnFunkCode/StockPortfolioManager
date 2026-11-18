"""`analytics.performance`: a book's value at a close, its time-weighted
return and its drawdown (#431 plan step 5.1). No database."""

import datetime
import unittest
from decimal import Decimal

from quantcore.analytics import performance as perf

D = Decimal
MON = datetime.date(2026, 11, 9)
TUE = datetime.date(2026, 11, 10)
WED = datetime.date(2026, 11, 11)
THU = datetime.date(2026, 11, 12)
SAT = datetime.date(2026, 11, 7)


def entry(kind, day, amount, entry_id=1):
    return {"entry_id": entry_id, "kind": kind, "entry_date": day.isoformat(),
            "amount": None if amount is None else D(amount)}


def lot(symbol="AAA", quantity="10", currency="USD", acquisition_type="BUY", day=MON):
    return {"symbol": symbol, "quantity": D(quantity), "currency": currency,
            "acquisition_type": acquisition_type, "trade_date": day.isoformat(),
            "purchase_date": day.isoformat()}


OPENING = [entry("opening_balance", MON, "1000.00", 1)]


class BookValueTest(unittest.TestCase):
    def test_cash_plus_holdings_at_the_close(self):
        snap = perf.book_value(
            session=TUE,
            entries=OPENING + [entry("buy", MON, "-500.00", 2)],
            lots=[lot()], closes={"AAA": (D("55.123"), TUE)}, rates={},
        )
        self.assertEqual(snap["cash"], D("500.00"))
        self.assertEqual(snap["holdings_value"], D("551.23"))
        self.assertEqual(snap["book_value"], D("1051.23"))
        self.assertEqual(snap["holding_count"], 1)
        self.assertEqual((snap["unpriced_count"], snap["stale_count"], snap["fx_missing_count"]),
                         (0, 0, 0))
        self.assertFalse(snap["cash_negative"])
        self.assertEqual(snap["session_date"], TUE)

    def test_no_opening_balance_by_the_session_is_no_value(self):
        self.assertIsNone(perf.book_value(session=TUE, entries=[], lots=[lot()],
                                          closes={"AAA": (D("1"), TUE)}, rates={}))
        later = [entry("opening_balance", WED, "1000.00")]
        self.assertIsNone(perf.book_value(session=TUE, entries=later, lots=[],
                                          closes={}, rates={}))

    def test_cash_counts_only_entries_up_to_the_session(self):
        snap = perf.book_value(
            session=TUE, entries=OPENING + [entry("deposit", WED, "200.00", 2)],
            lots=[], closes={}, rates={},
        )
        self.assertEqual(snap["cash"], D("1000.00"))
        self.assertEqual(snap["book_value"], D("1000.00"))

    def test_a_holding_with_no_close_leaves_the_value_unknown(self):
        snap = perf.book_value(session=TUE, entries=OPENING, lots=[lot(), lot("BBB")],
                               closes={"AAA": (D("10"), TUE)}, rates={})
        self.assertIsNone(snap["holdings_value"])
        self.assertIsNone(snap["book_value"])
        self.assertEqual(snap["unpriced_count"], 1)
        self.assertEqual(snap["cash"], D("1000.00"))

    def test_an_earlier_close_values_the_holding_and_is_counted(self):
        snap = perf.book_value(session=TUE, entries=OPENING, lots=[lot()],
                               closes={"AAA": (D("10"), MON)}, rates={})
        self.assertEqual(snap["book_value"], D("1100.00"))
        self.assertEqual(snap["stale_count"], 1)

    def test_a_non_usd_holding_converts_at_the_rate_and_never_at_one(self):
        gbp = lot(currency="GBP")
        snap = perf.book_value(session=TUE, entries=OPENING, lots=[gbp],
                               closes={"AAA": (D("10"), TUE)}, rates={"GBP": D("1.25")})
        self.assertEqual(snap["holdings_value"], D("125.00"))
        missing = perf.book_value(session=TUE, entries=OPENING, lots=[gbp],
                                  closes={"AAA": (D("10"), TUE)}, rates={})
        self.assertIsNone(missing["book_value"])
        self.assertEqual(missing["fx_missing_count"], 1)

    def test_a_cash_row_with_no_amount_leaves_the_value_unknown(self):
        snap = perf.book_value(session=TUE, entries=OPENING + [entry("buy", MON, None, 2)],
                               lots=[], closes={}, rates={})
        self.assertIsNone(snap["book_value"])
        self.assertEqual(snap["fx_missing_count"], 1)
        self.assertEqual(snap["holdings_value"], D("0.00"))

    def test_negative_cash_is_said(self):
        snap = perf.book_value(session=TUE, entries=OPENING + [entry("buy", MON, "-1500.00", 2)],
                               lots=[lot()], closes={"AAA": (D("150"), TUE)}, rates={})
        self.assertTrue(snap["cash_negative"])
        self.assertEqual(snap["book_value"], D("1000.00"))

    def test_rounds_the_holdings_once_not_each_lot(self):
        lots = [lot(quantity="1"), lot(quantity="1")]
        snap = perf.book_value(session=TUE, entries=OPENING, lots=lots,
                               closes={"AAA": (D("0.005"), TUE)}, rates={})
        self.assertEqual(snap["holdings_value"], D("0.01"))


class FlowTest(unittest.TestCase):
    def test_with_no_previous_snapshot_only_the_session_counts(self):
        entries = OPENING + [entry("deposit", TUE, "50.00", 2)]
        snap = perf.book_value(session=TUE, entries=entries, lots=[], closes={}, rates={})
        self.assertEqual(snap["flow"], D("50.00"))
        self.assertEqual(snap["contributed"], D("1050.00"))

    def test_counts_what_was_contributed_since_the_previous_snapshot(self):
        entries = [entry("opening_balance", SAT, "1000.00", 1),
                   entry("deposit", SAT, "100.00", 2),
                   entry("withdrawal", TUE, "-30.00", 3),
                   entry("deposit", WED, "999.00", 4)]
        previous = {"session_date": datetime.date(2026, 11, 6), "contributed": D("0.00")}
        snap = perf.book_value(session=TUE, entries=entries, lots=[], closes={},
                               rates={}, previous=previous)
        self.assertEqual(snap["contributed"], D("1070.00"))
        self.assertEqual(snap["flow"], D("1070.00"))

    def test_a_deposit_typed_in_late_is_a_flow_of_the_next_row(self):
        # MON's row was taken with 1000 contributed. A deposit dated MON was
        # typed in afterwards: TUE's cash has it, so TUE's flow must too, or
        # the return would read it as a gain.
        entries = OPENING + [entry("deposit", MON, "7.00", 2)]
        previous = {"session_date": MON, "contributed": D("1000.00")}
        snap = perf.book_value(session=TUE, entries=entries, lots=[], closes={},
                               rates={}, previous=previous)
        self.assertEqual(snap["flow"], D("7.00"))
        self.assertEqual(snap["cash"], D("1007.00"))

    def test_a_deposit_removed_later_is_a_negative_flow(self):
        previous = {"session_date": MON, "contributed": D("1050.00")}
        snap = perf.book_value(session=TUE, entries=OPENING, lots=[], closes={},
                               rates={}, previous=previous)
        self.assertEqual(snap["flow"], D("-50.00"))

    def test_buys_and_sells_are_not_flows(self):
        entries = OPENING + [entry("buy", TUE, "-100.00", 2), entry("sell", TUE, "40.00", 3)]
        previous = {"session_date": MON, "contributed": D("1000.00")}
        snap = perf.book_value(session=TUE, entries=entries, lots=[lot(day=TUE)],
                               closes={"AAA": (D("10"), TUE)}, rates={}, previous=previous)
        self.assertEqual(snap["flow"], D("0.00"))

    def test_a_lot_that_cost_no_cash_is_a_flow_at_the_close(self):
        seeded = lot(acquisition_type="SEED", day=TUE)
        previous = {"session_date": MON, "contributed": D("1000.00")}
        snap = perf.book_value(session=TUE, entries=OPENING, lots=[seeded],
                               closes={"AAA": (D("12"), TUE)}, rates={}, previous=previous)
        self.assertEqual(snap["flow"], D("120.00"))
        older = perf.book_value(session=WED, entries=OPENING, lots=[seeded],
                                closes={"AAA": (D("12"), WED)}, rates={},
                                previous={"session_date": TUE, "contributed": D("1000.00")})
        self.assertEqual(older["flow"], D("0.00"))

    def test_an_unpriced_lot_that_cost_no_cash_leaves_the_flow_unknown(self):
        seeded = lot(acquisition_type="TRANSFER", day=TUE)
        previous = {"session_date": MON, "contributed": D("1000.00")}
        snap = perf.book_value(session=TUE, entries=OPENING, lots=[seeded],
                               closes={}, rates={}, previous=previous)
        self.assertIsNone(snap["flow"])


def point(day, value, flow="0"):
    return {"session_date": day, "book_value": None if value is None else D(value),
            "flow": None if flow is None else D(flow)}


class TimeWeightedReturnTest(unittest.TestCase):
    def test_chains_the_sessions(self):
        result = perf.time_weighted_return(
            [point(MON, "100"), point(TUE, "110"), point(WED, "99")])
        self.assertEqual(result["twr"], D("-0.01000000"))
        self.assertEqual((result["start"], result["end"]), (MON, WED))
        self.assertEqual(result["gaps"], [])

    def test_a_deposit_is_not_a_gain(self):
        result = perf.time_weighted_return([point(MON, "100"), point(TUE, "200", "100")])
        self.assertEqual(result["twr"], D("0E-8"))

    def test_a_withdrawal_is_not_a_loss(self):
        result = perf.time_weighted_return(
            [point(MON, "100"), point(TUE, "55", "-50"), point(WED, "60.5")])
        self.assertEqual(result["twr"], D("0.21000000"))

    def test_the_first_session_is_the_base(self):
        self.assertIsNone(perf.time_weighted_return([point(MON, "100", "100")])["twr"])
        self.assertIsNone(perf.time_weighted_return([])["twr"])

    def test_order_does_not_matter(self):
        rows = [point(WED, "121"), point(MON, "100"), point(TUE, "110")]
        self.assertEqual(perf.time_weighted_return(rows)["twr"], D("0.21000000"))

    def test_an_unvalued_session_carries_its_flow_on(self):
        rows = [point(MON, "100"), point(TUE, None, "50"), point(WED, "165")]
        result = perf.time_weighted_return(rows)
        self.assertEqual(result["twr"], D("0.10000000"))
        self.assertEqual(result["gaps"], [TUE])

    def test_an_unknown_flow_breaks_the_chain_and_rebases(self):
        rows = [point(MON, "100"), point(TUE, "500", None), point(WED, "550")]
        result = perf.time_weighted_return(rows)
        self.assertEqual(result["twr"], D("0.10000000"))
        self.assertEqual(result["gaps"], [TUE])

    def test_nothing_invested_is_a_gap_not_a_division(self):
        rows = [point(MON, "100"), point(TUE, "10", "-100"), point(WED, "11")]
        result = perf.time_weighted_return(rows)
        self.assertEqual(result["gaps"], [TUE])
        self.assertEqual(result["twr"], D("0.10000000"))


class MaxDrawdownTest(unittest.TestCase):
    def index(self, *values):
        days = [MON, TUE, WED, THU]
        return [(days[i], D(v)) for i, v in enumerate(values)]

    def test_the_largest_fall_from_a_peak(self):
        result = perf.max_drawdown(self.index("1", "1.2", "0.9", "1.1"))
        self.assertEqual(result["max_drawdown"], D("-0.25000000"))
        self.assertEqual((result["peak"], result["trough"]), (TUE, WED))

    def test_never_below_a_peak_is_zero(self):
        result = perf.max_drawdown(self.index("1", "1.1", "1.2"))
        self.assertEqual(result["max_drawdown"], D("0E-8"))
        self.assertIsNone(result["peak"])

    def test_too_short_is_none(self):
        self.assertIsNone(perf.max_drawdown(self.index("1"))["max_drawdown"])

    def test_measured_on_the_index_so_a_withdrawal_is_not_a_drawdown(self):
        chain = perf.return_index([point(MON, "100"), point(TUE, "50", "-50")])
        self.assertEqual(perf.max_drawdown(chain["index"])["max_drawdown"], D("0E-8"))


if __name__ == "__main__":
    unittest.main()
