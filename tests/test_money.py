import unittest
from decimal import Decimal
import requests
from portfolio.money import Money, EXCHANGE_RATE_TIMEOUT_SECONDS
from unittest.mock import MagicMock, patch

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

    @patch("portfolio.money.Money._fetch_exchange_rate", return_value=0.9)
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



def _response(status=200, body=None):
    resp = MagicMock(status_code=status)
    resp.json.return_value = body if body is not None else {}
    return resp


class TestFetchExchangeRate(unittest.TestCase):
    """The URL and response handling, mocked (#348): the live test above can't see a wrong URL,
    because the API ignores the suffix and still answers 200 with USD-based rates."""

    OK_EUR = {"result": "success", "base_code": "EUR", "rates": {"USD": 1.12, "EUR": 1}}

    @patch("portfolio.money.requests.get")
    def test_url_uses_the_base_currency_alone_with_a_timeout(self, get):
        get.return_value = _response(body=self.OK_EUR)
        self.assertEqual(Money._fetch_exchange_rate("eur", "usd"), 1.12)
        get.assert_called_once_with("https://open.er-api.com/v6/latest/EUR",
                                    timeout=EXCHANGE_RATE_TIMEOUT_SECONDS)

    @patch("portfolio.money.requests.get")
    def test_error_result_in_a_200_raises(self, get):
        get.return_value = _response(body={"result": "error", "error-type": "unsupported-code"})
        with self.assertRaisesRegex(ConnectionError, "unsupported-code"):
            Money._fetch_exchange_rate("XXX", "USD")

    @patch("portfolio.money.requests.get")
    def test_rates_for_the_wrong_base_raise(self, get):
        get.return_value = _response(body={"result": "success", "base_code": "USD",
                                           "rates": {"USD": 1, "EUR": 0.89}})
        with self.assertRaisesRegex(ValueError, "USD, not EUR"):
            Money._fetch_exchange_rate("EUR", "USD")

    @patch("portfolio.money.requests.get")
    def test_unknown_target_raises(self, get):
        get.return_value = _response(body=self.OK_EUR)
        with self.assertRaisesRegex(ValueError, "not supported"):
            Money._fetch_exchange_rate("EUR", "ZZZ")

    @patch("portfolio.money.requests.get")
    def test_http_failure_and_timeout_raise_connection_error(self, get):
        get.return_value = _response(status=503)
        with self.assertRaises(ConnectionError):
            Money._fetch_exchange_rate("EUR", "USD")
        get.side_effect = requests.Timeout()
        with self.assertRaises(ConnectionError):
            Money._fetch_exchange_rate("EUR", "USD")

if __name__ == "__main__":
    unittest.main()