# money.py
from decimal import Decimal, ROUND_HALF_UP
import requests
from typing import Union

# A slow endpoint must not hang the legacy report run (#348).
EXCHANGE_RATE_TIMEOUT_SECONDS = 10

class Money:
    def __init__(self, amount: Union[float, Decimal], currency: str):
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

    def __mul__(self, factor: Union[float, Decimal]):
        return Money(self.amount * Decimal(str(factor)), self.currency)

    def __truediv__(self, divisor: Union[float, Decimal]):
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
        # The endpoint takes a single base code (/v6/latest/EUR). It ignores anything after the
        # first three letters, so the old /latest/USD{base} URL silently returned USD-based rates
        # for every holding -- a EUR->USD conversion came back at a rate of 1 (#348). The
        # base_code check below catches that class of mistake instead of trusting the URL.
        base = base_currency.upper()
        target = target_currency.upper()
        url = f"https://open.er-api.com/v6/latest/{base}"
        try:
            response = requests.get(url, timeout=EXCHANGE_RATE_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise ConnectionError("Failed to fetch exchange rates.") from exc
        if response.status_code != 200:
            raise ConnectionError("Failed to fetch exchange rates.")
        body = response.json()
        if body.get("result") != "success":
            raise ConnectionError(f"Exchange-rate API returned an error: {body.get('error-type', 'unknown')}")
        if body.get("base_code", base).upper() != base:
            raise ValueError(f"Exchange-rate API returned rates for {body.get('base_code')}, not {base}.")
        rates = body.get("rates", {})
        if target not in rates:
            raise ValueError(f"Currency {target_currency} not supported.")
        return rates[target]

    def __repr__(self):
        symbol = self._get_currency_symbol()
        if symbol == self.currency:  # If no special symbol was found
            return f"{self.currency} {self.amount:,.2f}"
        else:
            return f"{symbol}{self.amount:,.2f}"

    def __float__(self) -> float:
        return float(self.amount)

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
