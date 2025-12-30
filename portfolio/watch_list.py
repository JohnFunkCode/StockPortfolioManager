from typing import Optional, Dict, List

from datetime import datetime
import csv
import yaml
from portfolio.stock import Stock
from portfolio.money import Money
from portfolio.metrics import Metrics, get_historical_metrics
from portfolio.yfinance_gateway import get_latest_prices




class WatchList:
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
        with open(file_path, 'r', newline='') as csvfile:
            reader = csv.DictReader(csvfile)

            for row in reader:
                # Parse required fields
                name = row['name']
                symbol = row['symbol']

                # Parse optional fields
                currency = row.get('currency', 'USD')

                # Create Stock object
                stock = Stock(
                    name=name,
                    symbol=symbol,
                    purchase_price=None,
                    quantity=None,
                    purchase_date=None,
                    currency=currency,
                    sale_price=None,
                    sale_date=None,
                    current_price=None
                )
                self.add_stock(stock)
            return self.stocks

    def read_stocks_from_yaml(self, file_path) -> List[Stock]:
        """
        Read stock data from a YAML file and return a list of Stock objects.
        """
        with open(file_path, 'r', encoding='utf-8') as yamlfile:
            records = yaml.safe_load(yamlfile) or []

        for record in records:
            name = record['name']
            symbol = record['symbol']
            currency = record.get('currency', self.default_currency)
            tags = record.get('tags') or []

            stock = Stock(
                name=name,
                symbol=symbol,
                purchase_price=None,
                quantity=None,
                purchase_date=None,
                currency=currency,
                sale_price=None,
                sale_date=None,
                current_price=None
            )
            setattr(stock, 'tags', tags if isinstance(tags, list) else [tags])
            self.add_stock(stock)
        return self.stocks
