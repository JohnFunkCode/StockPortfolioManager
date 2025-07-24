import unittest
from decimal import Decimal
from portfolio.money import Money
from unittest.mock import patch

class TestMoney(unittest.TestCase):
    def test_add_same_currency(self):
        m1 = Money(10, "USD")
        m2 = Money(5, "USD")
        result = m1 + m2
        self.assertEqual(result.amount, Decimal("15.00"))
        self.assertEqual(result.currency, "USD")

    def test_add_different_currency_raises(self):
        m1 = Money(10, "USD")
        m2 = Money(5, "EUR")
        with self.assertRaises(ValueError):
            _ = m1 + m2

    def test_sub_same_currency(self):
        m1 = Money(10, "USD")
        m2 = Money(3, "USD")
        result = m1 - m2
        self.assertEqual(result.amount, Decimal("7.00"))

    def test_mul(self):
        m = Money(10, "USD")
        result = m * 2
        self.assertEqual(result.amount, Decimal("20.00"))

    def test_div(self):
        m = Money(10, "USD")
        result = m / 4
        self.assertEqual(result.amount, Decimal("2.50"))

    @patch("money.Money._fetch_exchange_rate", return_value=0.9)
    def test_convert_to(self, mock_fetch):
        m = Money(10, "USD")
        converted = m.convert_to("EUR")
        self.assertEqual(converted.amount, Decimal("9.00"))
        self.assertEqual(converted.currency, "EUR")

    def test_fetch_exchange_rate_live(self):
        # This will call the real API and may fail if the service is down or rate-limited
        rate = Money._fetch_exchange_rate("USD", "EUR")
        self.assertIsInstance(rate, float)
        self.assertGreater(rate, 0)

if __name__ == "__main__":
    unittest.main()