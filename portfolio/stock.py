from typing import Optional, Dict, List

from datetime import date
from portfolio.metrics import Metrics
from portfolio.money import Money


class Stock:
    name: str
    symbol: str
    quantity: int
    purchase_price: Money
    purchase_date: date
    sale_price: Optional[Money] = None
    sale_date: Optional[date] = None
    current_price: Optional[Money] = None
    metrics: Metrics = Metrics()

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
        current_price: Optional[float] = None,

    ):
        self.name = name
        self.symbol = symbol
        self.quantity = quantity
        self.purchase_price = Money(purchase_price, currency)
        self.purchase_date = purchase_date
        self.sale_price = Money(sale_price, currency) if sale_price is not None else None
        self.sale_date = sale_date
        self.current_price = Money(current_price, currency) if current_price is not None else None


    # def update_current_price(self) -> None:
    #     """Update the current price using yfinance"""
    #     prices = get_latest_prices([self.symbol], self.purchase_price.currency)
    #     self.current_price = prices[self.symbol]

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
