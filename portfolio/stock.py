from decimal import Decimal
from typing import Optional, Dict, List, Union

from datetime import date
from portfolio.metrics import Metrics
from portfolio.money import Money
from portfolio.yfinance_gateway import get_latest_prices

Number = Union[int, float, Decimal]


def parse_quantity(text) -> Decimal:
    """A share count from CSV text. Fractional shares are valid, so this is a
    ``Decimal``: ``int()`` rejected "0.5" outright. Raises ``ValueError`` on
    anything that isn't a finite number, as ``int()`` did."""
    try:
        quantity = Decimal(str(text).strip())
    except ArithmeticError:
        raise ValueError(f"invalid quantity: {text!r}") from None
    if not quantity.is_finite():
        raise ValueError(f"invalid quantity: {text!r}")
    return quantity


def format_quantity(quantity) -> str:
    """A share count for display, without the padding the database's
    ``NUMERIC(18,6)`` gives it: ``10.000000`` reads "10" and ``0.062500``
    reads "0.0625"."""
    if quantity is None:
        return ""
    text = format(Decimal(str(quantity)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class Stock:
    name: str
    symbol: str
    quantity: Number
    purchase_price: Optional[Money] = None
    purchase_date: Optional[date] = None
    sale_price: Optional[Money] = None
    sale_date: Optional[date] = None
    current_price: Optional[Money] = None
    earnings_date: Optional[date] = None
    metrics: Metrics = Metrics()

    def __init__(
        self,
        name: str,
        symbol: str,
        quantity: Optional[Number],
        purchase_price: Optional[Number],
        purchase_date: Optional[date],
        currency: str = "USD",
        sale_price: Optional[Number] = None,
        sale_date: Optional[date] = None,
        current_price: Optional[Number] = None,
        tags: Optional[List[str]] = None
    ):
        self.name = name
        self.symbol = symbol
        self.quantity = quantity if quantity is not None else 0
        self.purchase_price = Money(purchase_price, currency) if purchase_price is not None else None
        self.purchase_date = purchase_date if purchase_date is not None else date.today()
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

    def get_dollars_per_day(self) -> Optional[float]:
        """Calculate the average dollars per day based on purchase price and quantity"""
        if self.purchase_date and self.current_price:
            days_held = (date.today() - self.purchase_date).days
            if days_held > 0:
                gain_loss_amount = self.calculate_gain_loss().amount
                dollars_per_day = gain_loss_amount / days_held
                return Money(dollars_per_day, self.purchase_price.currency)
        return None