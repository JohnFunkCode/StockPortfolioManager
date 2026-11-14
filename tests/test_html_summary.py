"""The row helpers extracted from html_summary.create_portfolio_html (#431
plan step 1.4). html_summary.py is a legacy root script; it imports matplotlib
at module level, so the import is guarded the way
tests/test_generate_portfolio_report.py guards its own: prod-rollout runs the
suite on the lean install, and only a report-only package may be missing.
"""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

REPORT_ONLY_DEPENDENCIES = {"matplotlib", "jinja2", "boto3"}

try:
    import matplotlib

    # Before pyplot is imported: the default backend on a Mac opens a window.
    matplotlib.use("Agg")
    import html_summary
except ImportError as exc:
    if (exc.name or "").split(".")[0] not in REPORT_ONLY_DEPENDENCIES:
        raise
    raise unittest.SkipTest(
        f"{exc.name} is not installed; html_summary's tests need the report "
        "dependency set (pip install -r requirements-report.txt)"
    ) from exc

from portfolio.stock import Stock


def _stock(purchase_price=50.0, current_price=40.0, quantity=Decimal("2.500000")):
    return Stock(
        name="ZZ Row Test", symbol="ZZROW", quantity=quantity,
        purchase_price=purchase_price, purchase_date=date(2026, 1, 2),
        current_price=current_price,
    )


class StockRowTest(unittest.TestCase):
    def test_a_row_carries_the_holding(self):
        row = html_summary._stock_row(_stock())

        self.assertEqual(row["name"], "ZZ Row Test")
        self.assertEqual(row["symbol"], "ZZROW")
        self.assertEqual(row["purchase_price"], 50.0)
        self.assertEqual(row["current_price"], 40.0)
        self.assertEqual(row["quantity"], "2.5")
        self.assertAlmostEqual(row["gain_loss"], -25.0)
        self.assertEqual(row["gain_loss_pct"], "-20.00%")

    def test_a_row_without_a_price_says_not_available(self):
        row = html_summary._stock_row(_stock(current_price=None))

        self.assertEqual(row["current_price"], "N/A")
        self.assertEqual(row["gain_loss"], "N/A")
        self.assertEqual(row["gain_loss_pct"], "N/A")


class StockRowHtmlTest(unittest.TestCase):
    def test_a_loss_is_marked_as_a_loss(self):
        html = html_summary._stock_row_html(html_summary._stock_row(_stock()))

        self.assertIn("<td>ZZ Row Test</td>", html)
        self.assertIn("<td>ZZROW</td>", html)
        self.assertIn("<td>$50.00</td>", html)
        self.assertIn("<td>$40.00</td>", html)
        self.assertIn("<td>2.5</td>", html)
        self.assertIn('<td class="loss">$-25.00</td>', html)
        self.assertIn('<td class="loss">-20.00%</td>', html)

    def test_a_gain_and_a_flat_position_are_marked_as_a_gain(self):
        for current_price, amount, pct in ((60.0, "$10.00", "20.00%"),
                                           (50.0, "$0.00", "0.00%")):
            with self.subTest(current_price=current_price):
                row = html_summary._stock_row(
                    _stock(current_price=current_price, quantity=1)
                )
                html = html_summary._stock_row_html(row)

                self.assertIn(f'<td class="gain">{amount}</td>', html)
                self.assertIn(f'<td class="gain">{pct}</td>', html)

    def test_a_row_without_a_price_still_raises(self):
        """"N/A" can't take the .2f format. The script updates every price
        before it renders, so this was never reached; the extraction left it
        as it was."""
        row = html_summary._stock_row(_stock(current_price=None))

        with self.assertRaises(ValueError):
            html_summary._stock_row_html(row)


class CreatePortfolioHtmlTest(unittest.TestCase):
    def test_the_report_has_one_table_row_per_stock(self):
        from portfolio.portfolio import Portfolio

        portfolio = Portfolio()
        portfolio.add_stock(_stock())
        portfolio.add_stock(Stock(
            name="ZZ Gain Test", symbol="ZZGAIN", quantity=2, purchase_price=10.0,
            purchase_date=date(2026, 1, 2), current_price=15.0,
        ))

        with patch.object(html_summary, "fig_to_base64", return_value="aW1n"):
            html = html_summary.create_portfolio_html(portfolio)

        for stock in portfolio.list_stocks():
            self.assertIn(
                html_summary._stock_row_html(html_summary._stock_row(stock)), html
            )
        self.assertEqual(html.count("<tr>"), 3)  # the header and two stocks
        self.assertIn('src="data:image/png;base64,aW1n"', html)


if __name__ == "__main__":
    unittest.main()
