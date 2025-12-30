import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch
import pandas as pd
from portfolio.money import Money
from portfolio import portfolio as spm


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

    @patch('stock_portfolio_manager.get_latest_prices')
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

    @patch('stock_portfolio_manager.get_latest_prices')
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

    @patch('money.Money.convert_to')
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


if __name__ == "__main__":
    unittest.main()