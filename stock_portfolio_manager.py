from dataclasses import dataclass
from datetime import date
from typing import Optional, Dict, List
import yfinance as yf
import pandas as pd
from money import Money


def get_latest_prices(symbols: list[str], currency: str = "USD") -> dict[str, Optional[Money]]:
    """
    Return a dict of {symbol → latest closing price today as Money object or None}.
    """
    data = yf.download(
        tickers=" ".join(symbols),
        period="1d",
        interval="1m",
        progress=False,
        threads=False,
        group_by="column",
        auto_adjust=False,
    )

    prices: dict[str, Optional[Money]] = {s: None for s in symbols}

    if not data.empty:
        if isinstance(data.columns, pd.MultiIndex):
            # Multiple symbols case
            for sym in symbols:
                try:
                    price_value = float(data["Close"][sym].iloc[-1])
                    prices[sym] = Money(price_value, currency)
                except KeyError:
                    pass
        else:
            # Single symbol case
            try:
                price_value = float(data["Close"].iloc[-1])
                prices[symbols[0]] = Money(price_value, currency)
            except KeyError:
                pass

    return prices


@dataclass
class Stock:
    name: str
    symbol: str
    quantity: int
    purchase_price: Money
    purchase_date: date
    sale_price: Optional[Money] = None
    sale_date: Optional[date] = None
    current_price: Optional[Money] = None

    def __init__(
        self,
        name: str,
        symbol: str,
        quantity: int,
        purchase_price: float,
        purchase_date: date,
        currency: str = "USD",
        sale_price: Optional[float] = None,
        sale_date: Optional[date] = None,
        current_price: Optional[float] = None
    ):
        self.name = name
        self.symbol = symbol
        self.quantity = quantity
        self.purchase_price = Money(purchase_price, currency)
        self.purchase_date = purchase_date
        self.sale_price = Money(sale_price, currency) if sale_price is not None else None
        self.sale_date = sale_date
        self.current_price = Money(current_price, currency) if current_price is not None else None

    def update_current_price(self) -> None:
        """Update the current price using yfinance"""
        prices = get_latest_prices([self.symbol], self.purchase_price.currency)
        self.current_price = prices[self.symbol]

    def calculate_gain_loss(self) -> Optional[Money]:
        """Calculate the gain/loss based on purchase and current/sale price"""
        if self.sale_price is not None:
            return (self.sale_price - self.purchase_price) * self.quantity
        elif self.current_price is not None:
            return (self.current_price - self.purchase_price) * self.quantity
        return None

    def calculate_gain_loss_percentage(self) -> Optional[float]:
        """Calculate the gain/loss percentage"""
        gain_loss = self.calculate_gain_loss()
        if gain_loss is not None:
            total_cost = self.purchase_price * self.quantity
            return (gain_loss.amount / total_cost.amount) * 100
        return None

    def get_current_value(self) -> Optional[Money]:
        """Calculate the current total value of the stock holding"""
        if self.sale_price is not None:
            return self.sale_price * self.quantity
        elif self.current_price is not None:
            return self.current_price * self.quantity
        return None


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
        for stock in self.stocks.values():
            stock.update_current_price()

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

    def list_stocks(self) -> List[Stock]:
        """Return a list of all stocks in the portfolio"""
        return list(self.stocks.values())