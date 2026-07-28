import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch
import pandas as pd
import yaml
from portfolio.money import Money
from portfolio import portfolio as spm
from portfolio import watch_list


class TestStockPortfolioManager(unittest.TestCase):
    def setUp(self):
        # Create test Money objects
        self.usd_money_10 = Money(10, "USD")
        self.usd_money_20 = Money(20, "USD")

        # Create a test stock
        self.test_stock = spm.Stock(
            name="Test Stock",
            symbol="TEST",
            quantity=10,
            purchase_price=100.00,
            purchase_date=date(2023, 1, 1),
            currency="USD"
        )

    @patch('yfinance.download')
    def test_get_latest_prices_single_symbol(self, mock_download):
        # Mock the yfinance response for a single symbol
        mock_data = pd.DataFrame({
            'Close': [150.0]
        })
        mock_download.return_value = mock_data

        prices = spm.get_latest_prices(["TEST"])

        self.assertEqual(len(prices), 1)
        self.assertEqual(prices["TEST"].amount, Decimal("150.00"))
        self.assertEqual(prices["TEST"].currency, "USD")

    @patch('yfinance.download')
    def test_get_latest_prices_multiple_symbols(self, mock_download):
        # Mock the yfinance response for multiple symbols
        close_data = pd.DataFrame({
            'AAPL': [150.0],
            'MSFT': [250.0]
        })
        mock_data = pd.DataFrame(
            columns=pd.MultiIndex.from_product([['Close'], ['AAPL', 'MSFT']])
        )
        mock_data[('Close', 'AAPL')] = [150.0]
        mock_data[('Close', 'MSFT')] = [250.0]
        mock_download.return_value = mock_data

        prices = spm.get_latest_prices(["AAPL", "MSFT"])

        self.assertEqual(len(prices), 2)
        self.assertEqual(prices["AAPL"].amount, Decimal("150.00"))
        self.assertEqual(prices["MSFT"].amount, Decimal("250.00"))

    @patch('yfinance.download')
    def test_get_latest_prices_empty_data(self, mock_download):
        # Mock empty response
        mock_download.return_value = pd.DataFrame()

        prices = spm.get_latest_prices(["TEST"])

        self.assertEqual(len(prices), 1)
        self.assertIsNone(prices["TEST"])

    def test_stock_init(self):
        stock = self.test_stock

        self.assertEqual(stock.name, "Test Stock")
        self.assertEqual(stock.symbol, "TEST")
        self.assertEqual(stock.quantity, 10)
        self.assertEqual(stock.purchase_price.amount, Decimal("100.00"))
        self.assertEqual(stock.purchase_price.currency, "USD")
        self.assertEqual(stock.purchase_date, date(2023, 1, 1))
        self.assertIsNone(stock.sale_price)
        self.assertIsNone(stock.sale_date)
        self.assertIsNone(stock.current_price)

    @patch('portfolio.stock.get_latest_prices')
    def test_stock_update_current_price(self, mock_get_prices):
        mock_get_prices.return_value = {"TEST": Money(120, "USD")}

        self.test_stock.update_current_price()

        self.assertEqual(self.test_stock.current_price.amount, Decimal("120.00"))

    def test_stock_calculate_gain_loss_with_current_price(self):
        self.test_stock.current_price = Money(120, "USD")

        gain_loss = self.test_stock.calculate_gain_loss()

        self.assertEqual(gain_loss.amount, Decimal("200.00"))  # (120-100)*10

    def test_stock_calculate_gain_loss_with_sale_price(self):
        self.test_stock.sale_price = Money(90, "USD")

        gain_loss = self.test_stock.calculate_gain_loss()

        self.assertEqual(gain_loss.amount, Decimal("-100.00"))  # (90-100)*10

    def test_stock_calculate_gain_loss_percentage(self):
        self.test_stock.current_price = Money(120, "USD")

        percentage = self.test_stock.calculate_gain_loss_percentage()

        self.assertEqual(percentage, 20.0)  # ((120-100)*10)/(100*10)*100

    def test_stock_get_current_value(self):
        self.test_stock.current_price = Money(120, "USD")

        value = self.test_stock.get_current_value()

        self.assertEqual(value.amount, Decimal("1200.00"))  # 120*10

    def test_portfolio_add_get_remove_stock(self):
        portfolio = spm.Portfolio()

        # Test add_stock and get_stock
        portfolio.add_stock(self.test_stock)
        self.assertEqual(portfolio.get_stock("TEST"), self.test_stock)

        # Test remove_stock
        portfolio.remove_stock("TEST")
        self.assertIsNone(portfolio.get_stock("TEST"))

    @patch('portfolio.portfolio.get_latest_prices')
    def test_portfolio_update_all_prices(self, mock_get_prices):
        portfolio = spm.Portfolio()
        stock1 = spm.Stock("Stock1", "S1", 10, 100, date(2023, 1, 1))
        stock2 = spm.Stock("Stock2", "S2", 20, 200, date(2023, 1, 1))
        portfolio.add_stock(stock1)
        portfolio.add_stock(stock2)

        def side_effect(symbols, currency=None):
            result = {}
            if "S1" in symbols:
                result["S1"] = Money(110, "USD")
            if "S2" in symbols:
                result["S2"] = Money(220, "USD")
            return result

        mock_get_prices.side_effect = side_effect

        portfolio.update_all_prices()

        self.assertEqual(stock1.current_price.amount, Decimal("110.00"))
        self.assertEqual(stock2.current_price.amount, Decimal("220.00"))

    def test_portfolio_get_total_investment(self):
        portfolio = spm.Portfolio()
        stock1 = spm.Stock("Stock1", "S1", 10, 100, date(2023, 1, 1))
        stock2 = spm.Stock("Stock2", "S2", 20, 200, date(2023, 1, 1))
        portfolio.add_stock(stock1)
        portfolio.add_stock(stock2)

        total = portfolio.get_total_investment()

        self.assertEqual(total.amount, Decimal("5000.00"))  # (100*10 + 200*20)

    def test_portfolio_get_total_current_value(self):
        portfolio = spm.Portfolio()
        stock1 = spm.Stock("Stock1", "S1", 10, 100, date(2023, 1, 1))
        stock2 = spm.Stock("Stock2", "S2", 20, 200, date(2023, 1, 1))
        stock1.current_price = Money(110, "USD")
        stock2.current_price = Money(220, "USD")
        portfolio.add_stock(stock1)
        portfolio.add_stock(stock2)

        total = portfolio.get_total_current_value()

        self.assertEqual(total.amount, Decimal("5500.00"))  # (110*10 + 220*20)

    def test_portfolio_get_total_gain_loss(self):
        portfolio = spm.Portfolio()
        stock1 = spm.Stock("Stock1", "S1", 10, 100, date(2023, 1, 1))
        stock2 = spm.Stock("Stock2", "S2", 20, 200, date(2023, 1, 1))
        stock1.current_price = Money(110, "USD")
        stock2.current_price = Money(220, "USD")
        portfolio.add_stock(stock1)
        portfolio.add_stock(stock2)

        total = portfolio.get_total_gain_loss()

        self.assertEqual(total.amount, Decimal("500.00"))  # ((110-100)*10 + (220-200)*20)

    def test_portfolio_get_total_gain_loss_percentage(self):
        portfolio = spm.Portfolio()
        stock1 = spm.Stock("Stock1", "S1", 10, 100, date(2023, 1, 1))
        stock2 = spm.Stock("Stock2", "S2", 20, 200, date(2023, 1, 1))
        stock1.current_price = Money(110, "USD")
        stock2.current_price = Money(220, "USD")
        portfolio.add_stock(stock1)
        portfolio.add_stock(stock2)

        percentage = portfolio.get_total_gain_loss_percentage()

        self.assertEqual(percentage, 10.0)  # 500/5000*100

    @patch('portfolio.money.Money.convert_to')
    def test_portfolio_currency_conversion(self, mock_convert):
        portfolio = spm.Portfolio()
        stock = spm.Stock("Stock", "S1", 10, 100, date(2023, 1, 1), currency="USD")
        stock.current_price = Money(110, "USD")
        portfolio.add_stock(stock)

        # Setup the mock for convert_to
        mock_convert.return_value = Money(90, "EUR")

        # Test conversion in get_total_investment
        total_eur = portfolio.get_total_investment("EUR")
        mock_convert.assert_called()
        self.assertEqual(total_eur.currency, "EUR")

        # Reset mock and test get_total_current_value
        mock_convert.reset_mock()
        mock_convert.return_value = Money(99, "EUR")
        total_current_eur = portfolio.get_total_current_value("EUR")
        mock_convert.assert_called()
        self.assertEqual(total_current_eur.currency, "EUR")


class TestReadStocksFromRecordsFractionalLots(unittest.TestCase):
    """issue #126 Step 3.4: repository rows carry Decimal quantity/price
    (fractional shares) — read_stocks_from_records() must not truncate
    them via int()/float()."""

    def test_fractional_quantity_and_decimal_price_round_trip_exactly(self):
        portfolio = spm.Portfolio()
        records = [{
            "name": "Fractional Co",
            "symbol": "ZZFRAC",
            "purchase_price": Decimal("133.3300"),
            "quantity": Decimal("0.0625"),
            "purchase_date": "2026-01-10",
            "currency": "USD",
            "sale_price": None,
            "sale_date": None,
        }]

        portfolio.read_stocks_from_records(records)
        stock = portfolio.get_stock("ZZFRAC")

        self.assertEqual(stock.quantity, Decimal("0.0625"))
        self.assertEqual(stock.purchase_price.amount, Decimal("133.33"))

        stock.current_price = Money("100.00", "USD")
        gain_loss = stock.calculate_gain_loss()
        # (100 - 133.33) * 0.0625 = -2.083125 -> quantized to cents by Money
        self.assertEqual(gain_loss.amount, Decimal("-2.08"))


class TestWatchListReadStocksFromRecords(unittest.TestCase):
    """issue #83: the daily report builds its watchlist from the DB rather
    than watchlist.yaml, so WatchList has to accept the dict shape
    WatchlistService.list_entries() returns."""

    def test_records_become_stocks_with_tags_attached(self):
        wl = watch_list.WatchList()

        wl.read_stocks_from_records([
            {"name": "Zscaler", "symbol": "ZS", "currency": "USD",
             "tags": ["Cybersecurity"], "purchase_price": None,
             "quantity": None, "purchase_date": None, "sale_price": None,
             "sale_date": None, "source": "watchlist"},
            {"name": "SK Hynix", "symbol": "000660.KS", "currency": "KRW",
             "tags": []},
        ])

        self.assertEqual(len(wl.list_stocks()), 2)
        zs = wl.get_stock("ZS")
        self.assertEqual(zs.name, "Zscaler")
        # tags is set by the loader, not by Stock.__init__ — the report groups
        # watchlist rows by it.
        self.assertEqual(zs.tags, ["Cybersecurity"])
        # A watchlist row owns nothing, so nothing may be priced.
        self.assertIsNone(zs.purchase_price)
        self.assertIsNone(zs.sale_price)
        self.assertEqual(wl.get_stock("000660.KS").tags, [])

    def test_missing_name_falls_back_to_the_symbol(self):
        """A row added through the UI with no name still has to render in the
        report, which prints stock.name."""
        wl = watch_list.WatchList()

        wl.read_stocks_from_records([{"symbol": "ZZWL", "name": None}])

        self.assertEqual(wl.get_stock("ZZWL").name, "ZZWL")
        self.assertEqual(wl.get_stock("ZZWL").tags, [])

    def test_matches_what_the_yaml_loader_produced(self):
        """The DB path replaces the YAML path in main.py, so the two have to
        build the same objects from the same data."""
        record = {"name": "Zscaler", "symbol": "ZS", "currency": "USD",
                  "tags": ["Cybersecurity"]}

        from_records = watch_list.WatchList()
        from_records.read_stocks_from_records([record])

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            yaml.safe_dump([record], fh)
            path = fh.name
        self.addCleanup(os.unlink, path)
        from_yaml = watch_list.WatchList()
        from_yaml.read_stocks_from_yaml(path)

        a, b = from_records.get_stock("ZS"), from_yaml.get_stock("ZS")
        for attr in ("name", "symbol", "quantity", "purchase_price",
                     "sale_price", "sale_date", "current_price", "tags"):
            self.assertEqual(getattr(a, attr), getattr(b, attr), attr)


if __name__ == "__main__":
    unittest.main()