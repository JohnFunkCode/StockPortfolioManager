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
        # A negative cash is stored as it is: Performance reads it from the
        # row to mark the day (plan decision 8).
        self.assertEqual(snap["cash"], D("-500.00"))
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

    def test_a_lot_that_cost_no_cash_typed_in_late_is_not_a_flow(self):
        # A known limit (#573 review): a transfer dated MON but typed in after
        # MON's row was taken is valued in TUE's holdings and in no flow, so
        # it reads as a gain until step 5.2's backfill rebuilds the rows.
        transferred = lot(acquisition_type="TRANSFER", day=MON)
        previous = {"session_date": MON, "contributed": D("1000.00")}
        snap = perf.book_value(session=TUE, entries=OPENING, lots=[transferred],
                               closes={"AAA": (D("12"), TUE)}, rates={}, previous=previous)
        self.assertEqual(snap["holdings_value"], D("120.00"))
        self.assertEqual(snap["flow"], D("0.00"))

    def test_with_no_previous_snapshot_only_a_lot_dated_on_the_session_is_a_flow(self):
        lots = [lot(acquisition_type="SEED", day=MON), lot("BBB", acquisition_type="SEED", day=TUE)]
        snap = perf.book_value(session=TUE, entries=OPENING, lots=lots,
                               closes={"AAA": (D("12"), TUE), "BBB": (D("5"), TUE)}, rates={})
        self.assertEqual(snap["flow"], D("50.00"))

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


def history_lot(symbol="AAA", quantity="10", day=MON, status="OPEN", sold_on=None,
                acquisition_type="BUY", sale_date=None, currency="USD"):
    """A lot as `PortfolioRepository.list_lot_history` gives it (step 5.2)."""
    return {**lot(symbol, quantity, currency, acquisition_type, day),
            "status": status, "sold_on": sold_on, "sale_date": sale_date}


class HeldLotsTest(unittest.TestCase):
    """Which lots a book held at a past close (#431 step 5.2)."""

    def test_a_lot_is_held_from_its_trade_date_until_the_day_it_is_sold(self):
        sold = history_lot(status="CLOSED", sold_on=WED)
        self.assertEqual(perf.held_lots([sold], SAT), [])
        self.assertEqual(perf.held_lots([sold], MON), [sold])
        self.assertEqual(perf.held_lots([sold], TUE), [sold])
        self.assertEqual(perf.held_lots([sold], WED), [], "sold on the session: gone at its close")

    def test_a_split_lot_adds_up_to_the_lot_as_bought_before_its_sale(self):
        # Ten bought Monday, four sold Wednesday: the closed half holds the
        # four, the open remainder six, both dated Monday.
        closed = history_lot(quantity="4", status="CLOSED", sold_on=WED)
        remainder = history_lot(quantity="6")
        before = perf.held_lots([closed, remainder], TUE)
        self.assertEqual(sum(l["quantity"] for l in before), D("10"))
        self.assertEqual(perf.held_lots([closed, remainder], WED), [remainder])

    def test_a_legacy_close_uses_its_sale_date(self):
        legacy = history_lot(status="CLOSED", sale_date=TUE.isoformat())
        self.assertEqual(perf.held_lots([legacy], MON), [legacy])
        self.assertEqual(perf.held_lots([legacy], TUE), [])

    def test_a_lot_that_cant_be_placed_is_left_out_and_reported(self):
        no_day = {**history_lot(), "trade_date": None, "purchase_date": None}
        no_sale = history_lot(status="CLOSED")
        lots = [no_day, no_sale, history_lot(symbol="BBB")]
        self.assertEqual([l["symbol"] for l in perf.held_lots(lots, WED)], ["BBB"])
        self.assertEqual(perf.undated_lots(lots), [no_day, no_sale])


class CloseAsOfTest(unittest.TestCase):
    BARS = [(MON, 10), (WED, 12), (THU, 13)]

    def test_the_newest_close_on_or_before_the_session(self):
        self.assertEqual(perf.close_as_of(self.BARS, WED, 7), (D("12"), WED))
        self.assertEqual(perf.close_as_of(self.BARS, TUE, 7), (D("10"), MON))

    def test_never_a_later_close_or_one_past_the_lookback(self):
        self.assertIsNone(perf.close_as_of(self.BARS, SAT, 7))
        self.assertIsNone(perf.close_as_of(self.BARS, MON + datetime.timedelta(days=9), 1))
        self.assertIsNone(perf.close_as_of([], WED, 7))


class RebuildSnapshotsTest(unittest.TestCase):
    """Each flow on its real date, however late it was typed in (decisions
    85 and 88 as John revised them, step 5.2)."""

    BARS = {"AAA": [(MON, 50), (TUE, 50), (WED, 55)]}

    def rebuild(self, entries, lots, previous=None, fx_closes=None, bars=None):
        return perf.rebuild_snapshots(
            sessions=[MON, TUE, WED], entries=entries, lots=lots,
            bars=self.BARS if bars is None else bars, fx_closes=fx_closes or {},
            previous=previous, lookback_days=7)

    def test_a_late_deposit_is_a_flow_on_its_own_date(self):
        rows = dict(self.rebuild(OPENING + [entry("deposit", TUE, "500.00", 2)], []))
        self.assertEqual([rows[d]["flow"] for d in (MON, TUE, WED)],
                         [D("1000.00"), D("500.00"), D("0.00")])
        chain = perf.time_weighted_return([rows[d] for d in (MON, TUE, WED)])
        self.assertEqual(chain["twr"], D("0E-8"), "a deposit is never a gain")

    def test_a_lot_that_cost_no_cash_is_a_flow_on_its_trade_date(self):
        transfer = history_lot(acquisition_type="TRANSFER", day=TUE)
        rows = dict(self.rebuild(OPENING, [transfer]))
        self.assertEqual(rows[MON]["holdings_value"], D("0.00"))
        self.assertEqual(rows[TUE]["flow"], D("500.00"))
        self.assertEqual(rows[WED]["flow"], D("0.00"))
        twr = perf.time_weighted_return([rows[d] for d in (MON, TUE, WED)])["twr"]
        # Only Wednesday's move from 50 to 55 on the ten shares is earned.
        self.assertEqual(twr, (D("1550") / D("1500") - 1).quantize(perf.RETURN_QUANTUM))

    def test_a_sale_moves_value_to_cash_and_is_not_a_flow(self):
        sold = history_lot(status="CLOSED", sold_on=WED)
        entries = OPENING + [entry("buy", MON, "-500.00", 2), entry("sell", WED, "550.00", 3)]
        rows = dict(self.rebuild(entries, [sold]))
        self.assertEqual((rows[TUE]["cash"], rows[TUE]["holdings_value"]),
                         (D("500.00"), D("500.00")))
        self.assertEqual((rows[WED]["cash"], rows[WED]["holdings_value"], rows[WED]["flow"]),
                         (D("1050.00"), D("0.00"), D("0.00")))

    def test_flows_are_measured_from_the_stored_row_before_the_range(self):
        # Friday's stored row came before a deposit dated Saturday was typed
        # in. Rebuilt from Monday, the deposit is Monday's flow.
        fri = SAT - datetime.timedelta(days=1)
        opening = [entry("opening_balance", fri, "1000.00")]
        previous = {"session_date": fri, "contributed": D("1000.00")}
        rows = dict(self.rebuild(opening + [entry("deposit", SAT, "100.00", 2)], [],
                                 previous=previous))
        self.assertEqual(rows[MON]["flow"], D("100.00"))

    def test_a_session_before_the_opening_balance_has_no_row(self):
        late = [entry("opening_balance", TUE, "1000.00")]
        rows = self.rebuild(late, [])
        self.assertEqual([(d, r is None) for d, r in rows],
                         [(MON, True), (TUE, False), (WED, False)])
        self.assertEqual(dict(rows)[TUE]["flow"], D("1000.00"))

    def test_a_missing_close_or_rate_leaves_the_session_unvalued(self):
        rows = dict(self.rebuild(OPENING, [history_lot()], bars={"AAA": [(WED, 55)]}))
        self.assertIsNone(rows[MON]["book_value"])
        self.assertEqual(rows[WED]["book_value"], D("1550.00"))
        gbp = history_lot(symbol="AAA", currency="GBP")
        rows = dict(self.rebuild(OPENING, [gbp], fx_closes={"GBP": {TUE: D("1.25")}}))
        self.assertIsNone(rows[MON]["book_value"], "never 1.0")
        self.assertEqual(rows[TUE]["holdings_value"], D("625.00"))
        self.assertEqual(rows[WED]["holdings_value"], D("687.50"), "Tuesday's rate carries")


def _day(n):
    return datetime.date(2026, 11, 2) + datetime.timedelta(days=n)


def _row(n, value, flow="0", cash="100", restated_at=None):
    return {"session_date": _day(n), "book_value": D(value) if value is not None else None,
            "flow": D(flow) if flow is not None else None,
            "cash": D(cash) if cash is not None else None, "holdings_value": None,
            "unpriced_count": 0, "stale_count": 0, "fx_missing_count": 0,
            "restated_at": restated_at}


class BenchmarkIndexTest(unittest.TestCase):
    def test_the_growth_of_one_over_the_sessions(self):
        bars = [(_day(0), 100), (_day(1), 110), (_day(2), 99)]
        self.assertEqual(perf.benchmark_index([_day(0), _day(1), _day(2)], bars),
                         [(_day(0), D(1)), (_day(1), D("1.1")), (_day(2), D("0.99"))])

    def test_a_session_with_no_close_uses_the_last_one_in_the_lookback(self):
        bars = [(_day(0), 100), (_day(1), 120)]
        self.assertEqual(perf.benchmark_index([_day(0), _day(3)], bars)[1], (_day(3), D("1.2")))

    def test_no_close_for_the_first_session_gives_no_index(self):
        bars = [(_day(5), 100)]
        self.assertEqual(perf.benchmark_index([_day(0), _day(5)], bars),
                         [(_day(0), None), (_day(5), None)])

    def test_no_sessions_is_empty(self):
        self.assertEqual(perf.benchmark_index([], [(_day(0), 1)]), [])


class BookPerformanceTest(unittest.TestCase):
    BARS = [(_day(0), 100), (_day(1), 105), (_day(2), 110)]

    def test_the_return_against_the_benchmark(self):
        rows = [_row(0, "1000", flow="1000"), _row(1, "1100"), _row(2, "1210")]
        result = perf.book_performance(rows, self.BARS)
        self.assertEqual(result["twr"], D("0.21000000"))
        self.assertEqual(result["benchmark_return"], D("0.10000000"))
        self.assertEqual(result["vs_benchmark"], D("0.11000000"))
        self.assertEqual(result["benchmark_symbol"], "SPY")
        self.assertTrue(result["benchmark_available"])
        self.assertEqual(result["first_session"], _day(0))
        self.assertEqual(result["last_session"], _day(2))
        self.assertEqual(
            [(p["session_date"], p["index"], p["benchmark_index"]) for p in result["series"]],
            [(_day(0), D("1.00000000"), D("1.00000000")),
             (_day(1), D("1.10000000"), D("1.05000000")),
             (_day(2), D("1.21000000"), D("1.10000000"))])
        self.assertEqual(result["series"][2]["book_value"], D("1210"))

    def test_a_deposit_is_not_a_gain(self):
        rows = [_row(0, "1000"), _row(1, "2000", flow="1000")]
        self.assertEqual(perf.book_performance(rows, self.BARS)["twr"], D("0E-8"))

    def test_the_drawdown(self):
        rows = [_row(0, "1000"), _row(1, "1200"), _row(2, "900")]
        result = perf.book_performance(rows, self.BARS)
        self.assertEqual(result["max_drawdown"], D("-0.25000000"))
        self.assertEqual((result["drawdown_peak"], result["drawdown_trough"]), (_day(1), _day(2)))

    def test_unreadable_benchmark_bars_leave_the_benchmark_out(self):
        rows = [_row(0, "1000"), _row(1, "1100")]
        result = perf.book_performance(rows, None)
        self.assertFalse(result["benchmark_available"])
        self.assertIsNone(result["benchmark_return"])
        self.assertIsNone(result["vs_benchmark"])
        self.assertEqual(result["twr"], D("0.10000000"))
        self.assertIsNone(result["series"][1]["benchmark_index"])

    def test_one_row_has_no_return(self):
        result = perf.book_performance([_row(0, "1000")], self.BARS)
        self.assertIsNone(result["twr"])
        self.assertIsNone(result["benchmark_return"])
        self.assertIsNone(result["vs_benchmark"])
        self.assertIsNone(result["max_drawdown"])

    def test_no_rows(self):
        result = perf.book_performance([], [])
        self.assertEqual(result["series"], [])
        self.assertIsNone(result["latest"])
        self.assertIsNone(result["first_session"])
        self.assertEqual(result["cash_negative_count"], 0)

    def test_the_base_row_before_the_window_starts_the_chain_but_is_not_counted(self):
        stamp = datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc)
        rows = [_row(0, "1000", cash="-5", restated_at=stamp), _row(1, "1100"), _row(2, "1210")]
        result = perf.book_performance(rows, self.BARS, window_start=_day(1))
        self.assertEqual(result["twr"], D("0.21000000"))
        self.assertEqual(result["first_session"], _day(0))
        self.assertEqual(result["restated_count"], 0)
        self.assertEqual(result["cash_negative_count"], 0)

    def test_restated_rows_are_disclosed(self):
        early = datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc)
        late = datetime.datetime(2026, 11, 21, tzinfo=datetime.timezone.utc)
        rows = [_row(0, "1000", restated_at=late), _row(1, "1100"),
                _row(2, "1210", restated_at=early)]
        result = perf.book_performance(rows, self.BARS)
        self.assertEqual((result["restated_count"], result["last_restated_at"]), (2, late))

    def test_negative_cash_is_counted(self):
        rows = [_row(0, "1000", cash="-1"), _row(1, "1100", cash="0"), _row(2, "1210", cash="-3")]
        self.assertEqual(perf.book_performance(rows, self.BARS)["cash_negative_count"], 2)

    def test_a_gap_in_the_window_is_listed(self):
        rows = [_row(0, "1000"), _row(1, None), _row(2, "1210")]
        result = perf.book_performance(rows, self.BARS)
        self.assertEqual(result["gaps"], [_day(1)])
        self.assertEqual([p["session_date"] for p in result["series"]], [_day(0), _day(2)])

    def test_the_latest_row(self):
        rows = [_row(0, "1000"), _row(1, "1100", cash="40")]
        latest = perf.book_performance(rows, self.BARS)["latest"]
        self.assertEqual((latest["session_date"], latest["book_value"], latest["cash"]),
                         (_day(1), D("1100"), D("40")))
