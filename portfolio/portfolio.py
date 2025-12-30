from typing import Optional, Dict, List

from datetime import datetime
import csv
from portfolio.stock import Stock
from portfolio.money import Money
from portfolio.metrics import Metrics, get_historical_metrics
from portfolio.yfinance_gateway import get_latest_prices

class Portfolio:
    def __init__(self, default_currency: str = "USD"):
        self.stocks: Dict[str, Stock] = {}
        self.default_currency = default_currency

    def add_stock(self, stock: Stock) -> None:
        """Add a stock to the portfolio"""
        self.stocks[stock.symbol] = stock

    def remove_stock(self, symbol: str) -> None:
        """Remove a stock from the portfolio"""
        if symbol in self.stocks:
            del self.stocks[symbol]

    def get_stock(self, symbol: str) -> Optional[Stock]:
        """Get a stock by its symbol"""
        return self.stocks.get(symbol)

    def update_all_prices(self) -> None:
        """Update current prices for all stocks in the portfolio"""
        symbols = list()
        for stock in self.stocks.values():
            symbols.append(stock.symbol)

        prices = get_latest_prices(symbols, self.default_currency)
        for stock in self.stocks.values():
            stock.current_price = prices[stock.symbol] if stock.symbol in prices else None

    def get_total_investment(self, currency: str = None) -> Money:
        """Calculate the total investment (sum of purchase prices) in the portfolio"""
        target_currency = currency or self.default_currency
        total = Money(0, target_currency)

        for stock in self.stocks.values():
            stock_investment = stock.purchase_price * stock.quantity
            if stock_investment.currency != target_currency:
                stock_investment = stock_investment.convert_to(target_currency)
            total += stock_investment

        return total

    def get_total_current_value(self, currency: str = None) -> Money:
        """Calculate the total current value of the portfolio"""
        target_currency = currency or self.default_currency
        total = Money(0, target_currency)

        for stock in self.stocks.values():
            current_value = stock.get_current_value()
            if current_value is not None:
                if current_value.currency != target_currency:
                    current_value = current_value.convert_to(target_currency)
                total += current_value

        return total

    def get_total_gain_loss(self, currency: str = None) -> Money:
        """Calculate the total gain/loss for the portfolio"""
        target_currency = currency or self.default_currency
        total = Money(0, target_currency)

        for stock in self.stocks.values():
            gain_loss = stock.calculate_gain_loss()
            if gain_loss is not None:
                if gain_loss.currency != target_currency:
                    gain_loss = gain_loss.convert_to(target_currency)
                total += gain_loss

        return total

    def get_total_gain_loss_percentage(self) -> float:
        """Calculate the total gain/loss percentage for the portfolio"""
        total_gain_loss = self.get_total_gain_loss()
        total_cost = self.get_total_investment()

        if total_cost.amount > 0:
            return (total_gain_loss.amount / total_cost.amount) * 100
        return 0.0

    def get_total_dollars_per_day(self) -> Money:
        """Calculate the total dollars per day for the portfolio"""
        total_dollars_per_day_amount = Money(0, self.default_currency)

        for stock in self.stocks.values():
            dollars_per_day_amount = stock.get_dollars_per_day()
            if dollars_per_day_amount is not None:
                total_dollars_per_day_amount += dollars_per_day_amount
        return total_dollars_per_day_amount

    def list_stocks(self) -> List[Stock]:
        """Return a list of all stocks in the portfolio"""
        return list(self.stocks.values())

    def update_metrics(self):
        symbols = [stock.symbol for stock in self.stocks.values()]
        metrics = get_historical_metrics(symbols)
        for stock in self.stocks.values():
            stock.metrics = metrics.get(stock.symbol, Metrics())

    def read_stocks_from_csv(self, file_path) -> List[Stock]:
        """
        Read stock data from a CSV file and return a list of Stock objects.

        Expected CSV format:
        name,symbol,purchase_price,quantity,purchase_date,currency,sale_price,sale_date,current_price
        """
        try:
            with open(file_path, 'r', newline='') as csvfile:
                reader = csv.DictReader(csvfile)

                for row in reader:
                    # Parse required fields
                    name = row['name']
                    symbol = row['symbol']
                    purchase_price = float(row['purchase_price'])
                    quantity = int(row['quantity'])
                    purchase_date = datetime.strptime(row['purchase_date'], '%Y-%m-%d').date()

                    # Parse optional fields
                    currency = row.get('currency', 'USD')

                    sale_price = None
                    if row.get('sale_price') and row['sale_price'].strip():
                        sale_price = float(row['sale_price'])

                    sale_date = None
                    if row.get('sale_date') and row['sale_date'].strip():
                        sale_date = datetime.strptime(row['sale_date'], '%Y-%m-%d').date()

                    current_price = None
                    if row.get('current_price') and row['current_price'].strip():
                        current_price = float(row['current_price'])

                    # Create Stock object
                    stock = Stock(
                        name=name,
                        symbol=symbol,
                        purchase_price=purchase_price,
                        quantity=quantity,
                        purchase_date=purchase_date,
                        currency=currency,
                        sale_price=sale_price,
                        sale_date=sale_date,
                        current_price=current_price
                    )
                    self.add_stock(stock)
                return self.stocks

        except FileNotFoundError:
            print(f"Error: File '{file_path}' not found.")
            return []
        except (KeyError, ValueError) as e:
            print(f"Error parsing CSV data: {e}")
            return []

