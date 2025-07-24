# money.py
from decimal import Decimal, ROUND_HALF_UP
import requests

class Money:
    def __init__(self, amount: float | Decimal, currency: str):
        self.amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.currency = currency.upper()

    def __add__(self, other):
        if self.currency != other.currency:
            raise ValueError("Cannot add amounts with different currencies")
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other):
        if self.currency != other.currency:
            raise ValueError("Cannot subtract amounts with different currencies")
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: float | Decimal):
        return Money(self.amount * Decimal(str(factor)), self.currency)

    def __truediv__(self, divisor: float | Decimal):
        return Money(self.amount / Decimal(str(divisor)), self.currency)

    def convert_to(self, target_currency: str) -> "Money":
        """
        Convert this Money to the target currency using a real-time exchange rate.
        """
        rate = self._fetch_exchange_rate(self.currency, target_currency)
        converted_amount = self.amount * Decimal(str(rate))
        return Money(converted_amount, target_currency.upper())

    @staticmethod
    def _fetch_exchange_rate(base_currency: str, target_currency: str) -> float:
        url = f"https://open.er-api.com/v6/latest/USD{base_currency}"
        response = requests.get(url)
        if response.status_code == 200:
            rates = response.json().get("rates", {})
            if target_currency.upper() in rates:
                return rates[target_currency.upper()]
            else:
                raise ValueError(f"Currency {target_currency} not supported.")
        else:
            raise ConnectionError("Failed to fetch exchange rates.")

    def __repr__(self):
        symbol = self._get_currency_symbol()
        if symbol == self.currency:  # If no special symbol was found
            return f"{self.currency} {self.amount:,.2f}"
        else:
            return f"{symbol}{self.amount:,.2f}"

    def _get_currency_symbol(self) -> str:
        """Get the currency symbol for the current currency."""
        symbols = {
            "USD": "$",
            "EUR": "€",
            "GBP": "£",
            "JPY": "¥",
            "CAD": "CA$",
            "AUD": "A$",
            "CHF": "CHF",
            "CNY": "¥",
            "INR": "₹",
            "BRL": "R$"
        }
        return symbols.get(self.currency, self.currency)