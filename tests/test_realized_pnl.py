"""Realized P/L maths and its rolling windows (#235, plan step 2.1).

Pure functions: no database, no clock, no mocks.
"""
import datetime
import unittest
from decimal import Decimal

from quantcore.analytics.market_time import ROLLING_WINDOW_DAYS, rolling_window
from quantcore.analytics.portfolio_math import realized_pnl, realized_sale

D = Decimal


def sale(symbol="AAA", shares="10", buy="100", sell="120", **extra):
    row = {
        "sale_id": extra.pop("sale_id", 1),
        "lot_id": 1,
        "symbol": symbol,
        "shares_sold": D(shares),
        "purchase_price": D(buy) if buy is not None else None,
        "sale_price": D(sell),
        "currency": "USD",
        "trade_date": "2026-01-02",
        "sale_trade_date": "2026-03-02",
        "fx_rate_at_purchase": None,
        "fx_rate_at_sale": None,
    }
    row.update(extra)
    return row


class TestRollingWindow(unittest.TestCase):
    TODAY = datetime.date(2026, 11, 14)

    def test_each_period_is_that_many_calendar_days_ending_today(self):
        for period, days in (("month", 30), ("quarter", 90), ("year", 365)):
            with self.subTest(period=period):
                start, end = rolling_window(period, self.TODAY)
                self.assertEqual(end, self.TODAY)
                self.assertEqual((end - start).days, days)

    def test_month_starts_thirty_days_back(self):
        self.assertEqual(rolling_window("month", self.TODAY)[0], datetime.date(2026, 10, 15))

    def test_all_has_no_bounds(self):
        self.assertEqual(rolling_window("all", self.TODAY), (None, None))

    def test_the_options_are_exactly_the_four_in_the_issue(self):
        self.assertEqual(list(ROLLING_WINDOW_DAYS), ["month", "quarter", "year", "all"])

    def test_an_unknown_period_is_refused_not_defaulted(self):
        for bad in ("ytd", "", "Year", None, 30):
            with self.subTest(period=bad):
                with self.assertRaises(ValueError):
                    rolling_window(bad, self.TODAY)

    def test_today_defaults_to_the_market_date(self):
        from quantcore.analytics.market_time import market_date
        self.assertEqual(rolling_window("year")[1], market_date())


class TestRealizedSale(unittest.TestCase):
    def test_a_gain(self):
        row = realized_sale(sale())
        self.assertEqual(row["cost_basis"], D("1000.00"))
        self.assertEqual(row["proceeds"], D("1200.00"))
        self.assertEqual(row["realized_pnl"], D("200.00"))
        self.assertEqual(row["realized_pnl_pct"], D("20.00"))
        self.assertFalse(row["fx_missing"])
        self.assertFalse(row["basis_missing"])

    def test_a_loss(self):
        row = realized_sale(sale(sell="90"))
        self.assertEqual(row["realized_pnl"], D("-100.00"))
        self.assertEqual(row["realized_pnl_pct"], D("-10.00"))

    def test_usd_native_figures_equal_the_usd_ones(self):
        row = realized_sale(sale())
        for key in ("cost_basis", "proceeds", "realized_pnl"):
            self.assertEqual(row[f"{key}_native"], row[key])

    def test_fees_are_ignored(self):
        self.assertEqual(realized_sale(sale(fees=D("9.99")))["realized_pnl"], D("200.00"))

    def test_a_fractional_sale_adds_up_after_rounding(self):
        row = realized_sale(sale(shares="0.333333", buy="10.005", sell="10.015"))
        self.assertEqual(row["cost_basis"], D("3.33"))   # 3.334996...
        self.assertEqual(row["proceeds"], D("3.34"))     # 3.338330...
        self.assertEqual(row["realized_pnl"], row["proceeds"] - row["cost_basis"])

    def test_the_input_is_not_changed(self):
        original = sale()
        realized_sale(original)
        self.assertNotIn("realized_pnl", original)

    def test_the_sales_own_fields_are_carried_through(self):
        row = realized_sale(sale(notes="trim", sale_id=7))
        self.assertEqual((row["sale_id"], row["notes"], row["trade_date"]), (7, "trim", "2026-01-02"))

    def test_a_zero_cost_basis_has_no_percentage(self):
        row = realized_sale(sale(buy="0"))
        self.assertEqual(row["realized_pnl"], D("1200.00"))
        self.assertIsNone(row["realized_pnl_pct"])

    def test_a_lot_with_no_purchase_price_is_flagged_and_has_no_pnl(self):
        row = realized_sale(sale(buy=None))
        self.assertTrue(row["basis_missing"])
        self.assertEqual(row["proceeds_native"], D("1200.00"))
        for key in ("cost_basis_native", "realized_pnl_native", "cost_basis", "proceeds",
                    "realized_pnl", "realized_pnl_pct"):
            self.assertIsNone(row[key], key)

    def test_non_usd_converts_each_side_at_its_own_rate(self):
        # 10 shares bought at 100 EUR when EUR was 1.10, sold at 120 EUR at 1.00.
        row = realized_sale(sale(
            currency="EUR", fx_rate_at_purchase=D("1.10"), fx_rate_at_sale=D("1.00")))
        self.assertEqual(row["cost_basis_native"], D("1000.00"))
        self.assertEqual(row["realized_pnl_native"], D("200.00"))
        self.assertEqual(row["cost_basis"], D("1100.00"))
        self.assertEqual(row["proceeds"], D("1200.00"))
        # The 200 EUR gain is 100 USD: the currency fell during the hold.
        self.assertEqual(row["realized_pnl"], D("100.00"))
        self.assertFalse(row["fx_missing"])

    def test_non_usd_without_both_rates_is_flagged_never_converted_at_one(self):
        for rates in (
            {},
            {"fx_rate_at_purchase": D("1.10")},
            {"fx_rate_at_sale": D("1.00")},
        ):
            with self.subTest(rates=rates):
                row = realized_sale(sale(currency="EUR", **rates))
                self.assertTrue(row["fx_missing"])
                self.assertEqual(row["realized_pnl_native"], D("200.00"))
                for key in ("cost_basis", "proceeds", "realized_pnl", "realized_pnl_pct"):
                    self.assertIsNone(row[key], key)

    def test_a_blank_currency_is_usd(self):
        self.assertEqual(realized_sale(sale(currency=None))["realized_pnl"], D("200.00"))


class TestRealizedPnl(unittest.TestCase):
    def test_no_sales(self):
        result = realized_pnl([])
        self.assertEqual(result["symbols"], [])
        self.assertEqual(result["totals"], {
            "symbol_count": 0, "sale_count": 0, "fx_missing_count": 0,
            "basis_missing_count": 0, "cost_basis": None, "proceeds": None,
            "realized_pnl": None, "realized_pnl_pct": None,
        })

    def test_sales_roll_up_by_symbol(self):
        result = realized_pnl([
            sale(sale_id=1, shares="10", buy="100", sell="120"),   # +200
            sale(sale_id=2, shares="5", buy="100", sell="80"),     # -100
        ])
        (row,) = result["symbols"]
        self.assertEqual(row["symbol"], "AAA")
        self.assertEqual(row["sale_count"], 2)
        self.assertEqual(row["shares_closed"], D("15"))
        self.assertEqual(row["cost_basis"], D("1500.00"))
        self.assertEqual(row["proceeds"], D("1600.00"))
        self.assertEqual(row["realized_pnl"], D("100.00"))
        self.assertEqual(row["realized_pnl_pct"], D("6.67"))
        self.assertEqual([s["sale_id"] for s in row["sales"]], [1, 2])

    def test_symbols_are_ordered_best_first(self):
        result = realized_pnl([
            sale("LOSS", sell="90"),
            sale("BEST", sell="150"),
            sale("FLAT", sell="100"),
            sale("GOOD", sell="110"),
        ])
        self.assertEqual([r["symbol"] for r in result["symbols"]], ["BEST", "GOOD", "FLAT", "LOSS"])

    def test_equal_pnl_falls_back_to_the_symbol(self):
        result = realized_pnl([sale("ZZZ"), sale("AAA")])
        self.assertEqual([r["symbol"] for r in result["symbols"]], ["AAA", "ZZZ"])

    def test_bar_segments_for_a_gain_and_a_loss(self):
        result = realized_pnl([sale("UP", sell="120"), sale("DOWN", sell="90")])
        up, down = result["symbols"]
        self.assertEqual((up["bar_base"], up["bar_gain"], up["bar_loss"]),
                         (D("1000.00"), D("200.00"), D("0.00")))
        self.assertEqual((down["bar_base"], down["bar_gain"], down["bar_loss"]),
                         (D("1000.00"), D("0.00"), D("-100.00")))
        # The top of the bar is the proceeds.
        self.assertEqual(up["bar_base"] + up["bar_gain"], up["proceeds"])
        self.assertEqual(down["bar_base"] + down["bar_loss"], down["proceeds"])

    def test_still_held_marks_a_partial_exit(self):
        result = realized_pnl([sale("HELD"), sale("GONE")], held_symbols=["HELD", "OTHER"])
        flags = {r["symbol"]: r["still_held"] for r in result["symbols"]}
        self.assertEqual(flags, {"HELD": True, "GONE": False})

    def test_totals_equal_the_sum_of_the_rows_shown(self):
        sales = [
            sale("AAA", sale_id=1, shares="0.333333", buy="10.005", sell="10.015"),
            sale("AAA", sale_id=2, shares="0.333333", buy="10.005", sell="10.015"),
            sale("BBB", sale_id=3, shares="0.666667", buy="7.775", sell="7.785"),
        ]
        result = realized_pnl(sales)
        shown = [s for row in result["symbols"] for s in row["sales"]]
        for key in ("cost_basis", "proceeds", "realized_pnl"):
            self.assertEqual(result["totals"][key], sum(s[key] for s in shown), key)
            for row in result["symbols"]:
                self.assertEqual(row[key], sum(s[key] for s in row["sales"]), key)
        self.assertEqual(result["totals"]["symbol_count"], 2)
        self.assertEqual(result["totals"]["sale_count"], 3)

    def test_a_sale_without_a_rate_is_counted_and_left_out_of_the_totals(self):
        result = realized_pnl([
            sale("AAA", sale_id=1),                                   # +200 USD
            sale("AAA", sale_id=2, currency="EUR"),                   # no rate
            sale("EUR", sale_id=3, currency="EUR", sell="500"),       # no rate
        ])
        by_symbol = {r["symbol"]: r for r in result["symbols"]}
        self.assertEqual(by_symbol["AAA"]["realized_pnl"], D("200.00"))
        self.assertEqual(by_symbol["AAA"]["fx_missing_count"], 1)
        self.assertEqual(by_symbol["AAA"]["shares_closed"], D("20"))

        only_eur = by_symbol["EUR"]
        for key in ("cost_basis", "proceeds", "realized_pnl", "realized_pnl_pct",
                    "bar_base", "bar_gain", "bar_loss"):
            self.assertIsNone(only_eur[key], key)
        # A symbol with no figure sorts last, not as a zero in the middle.
        self.assertEqual(result["symbols"][-1]["symbol"], "EUR")

        totals = result["totals"]
        self.assertEqual(totals["realized_pnl"], D("200.00"))
        self.assertEqual(totals["cost_basis"], D("1000.00"))
        self.assertEqual((totals["sale_count"], totals["fx_missing_count"]), (3, 2))

    def test_a_sale_with_no_basis_is_counted_and_left_out(self):
        result = realized_pnl([sale(sale_id=1), sale(sale_id=2, buy=None)])
        self.assertEqual(result["totals"]["realized_pnl"], D("200.00"))
        self.assertEqual(result["totals"]["basis_missing_count"], 1)

    def test_losses_only_sort_least_bad_first(self):
        result = realized_pnl([sale("BIG", sell="50"), sale("SMALL", sell="99")])
        self.assertEqual([r["symbol"] for r in result["symbols"]], ["SMALL", "BIG"])


if __name__ == "__main__":
    unittest.main()


class TestRealizedBySource(unittest.TestCase):
    """Attribution: realized P/L grouped by what prompted each sale (#431
    step 5.3)."""

    def test_sales_are_summed_by_source_best_first(self):
        from quantcore.analytics.portfolio_math import realized_by_source
        result = realized_by_source([
            sale(sale_id=1, source="manual", sell="110"),    # +100
            sale(sale_id=2, source="stop", sell="90"),       # -100
            sale(sale_id=3, source="manual", sell="130"),    # +300
        ])
        self.assertEqual([r["source"] for r in result["sources"]], ["manual", "stop"])
        manual, stop = result["sources"]
        self.assertEqual((manual["sale_count"], manual["realized_pnl"]), (2, D("400.00")))
        self.assertEqual((stop["sale_count"], stop["realized_pnl"]), (1, D("-100.00")))
        self.assertEqual(result["totals"]["realized_pnl"], D("300.00"))
        self.assertEqual(result["totals"]["sale_count"], 3)

    def test_a_sale_with_no_source_is_manual(self):
        from quantcore.analytics.portfolio_math import realized_by_source
        result = realized_by_source([sale(source=None)])
        self.assertEqual(result["sources"][0]["source"], "manual")

    def test_a_source_with_no_dollar_figure_is_last_and_counted(self):
        from quantcore.analytics.portfolio_math import realized_by_source
        result = realized_by_source([
            sale(sale_id=1, source="ai", buy=None),
            sale(sale_id=2, source="stop", sell="90"),
        ])
        self.assertEqual([r["source"] for r in result["sources"]], ["stop", "ai"])
        self.assertIsNone(result["sources"][1]["realized_pnl"])
        self.assertEqual(result["sources"][1]["basis_missing_count"], 1)
        self.assertEqual(result["totals"]["realized_pnl"], D("-100.00"))

    def test_no_sales_is_empty_with_no_total(self):
        from quantcore.analytics.portfolio_math import realized_by_source
        result = realized_by_source([])
        self.assertEqual(result["sources"], [])
        self.assertIsNone(result["totals"]["realized_pnl"])
