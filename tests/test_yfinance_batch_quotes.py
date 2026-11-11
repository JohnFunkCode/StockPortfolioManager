"""Tests for YFinanceGateway.batch_quotes, the v7 batch quote seam (#518).

Yahoo is always stubbed: ``YfData`` is patched where the gateway module bound
it, so no test reaches the network (tests/__init__.py would fail it anyway).
"""
import inspect
import threading
import time
import unittest
from unittest.mock import patch

from quantcore.gateways import yfinance_gateway as gw_mod
from quantcore.gateways.yfinance_gateway import (
    BATCH_QUOTE_CHUNK,
    Quote,
    YFinanceGateway,
)


def raw_quote(symbol, price=100.0, **extra):
    row = {
        "symbol": symbol,
        "regularMarketPrice": price,
        "regularMarketOpen": price - 1,
        "regularMarketDayHigh": price + 2,
        "regularMarketDayLow": price - 2,
        "regularMarketVolume": 12345,
        "regularMarketTime": 1794344400,
        "regularMarketPreviousClose": price - 1,
        "regularMarketChangePercent": 1.01,
        "marketState": "REGULAR",
        "currency": "USD",
        "exchangeTimezoneName": "America/New_York",
    }
    row.update(extra)
    return row


def response(*rows):
    return {"quoteResponse": {"result": list(rows), "error": None}}


class FakeYfData:
    """Stands in for yfinance's YfData singleton; records each request."""

    calls: list = []
    handler = None

    def get_raw_json(self, url, params=None, timeout=30):
        FakeYfData.calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeYfData.handler(params["symbols"].split(","))


class TestBatchQuotes(unittest.TestCase):
    def setUp(self):
        FakeYfData.calls = []
        FakeYfData.handler = lambda syms: response(*(raw_quote(s) for s in syms))
        patcher = patch.object(gw_mod, "YfData", FakeYfData)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gateway = YFinanceGateway()

    def test_260_symbols_take_three_requests_of_at_most_100(self):
        symbols = [f"S{i:03d}" for i in range(260)]
        quotes = self.gateway.batch_quotes(symbols)
        self.assertEqual(BATCH_QUOTE_CHUNK, 100)
        sizes = sorted(len(c["params"]["symbols"].split(",")) for c in FakeYfData.calls)
        self.assertEqual(sizes, [60, 100, 100])
        self.assertEqual(len(quotes), 260)
        self.assertTrue(all(isinstance(q, Quote) for q in quotes.values()))

    def test_request_shape(self):
        self.gateway.batch_quotes(["aapl", "MSFT", "AAPL"])
        (call,) = FakeYfData.calls
        self.assertEqual(call["url"], "https://query1.finance.yahoo.com/v7/finance/quote")
        self.assertEqual(call["params"], {"symbols": "AAPL,MSFT", "formatted": "false"})

    def test_parses_fields(self):
        FakeYfData.handler = lambda syms: response(raw_quote(
            "AAPL", 200.0, marketState="POST", postMarketPrice=201.5,
            postMarketChangePercent=0.75, postMarketTime=1794350000,
        ))
        q = self.gateway.batch_quotes(["AAPL"])["AAPL"]
        self.assertEqual(q.regular_price, 200.0)
        self.assertEqual(q.regular_open, 199.0)
        self.assertEqual(q.regular_high, 202.0)
        self.assertEqual(q.regular_low, 198.0)
        self.assertEqual(q.regular_volume, 12345)
        self.assertEqual(q.regular_time, 1794344400)
        self.assertEqual(q.previous_close, 199.0)
        self.assertEqual(q.regular_change_pct, 1.01)
        self.assertEqual(q.post_price, 201.5)
        self.assertEqual(q.post_change_pct, 0.75)
        self.assertEqual(q.post_time, 1794350000)
        self.assertIsNone(q.pre_price)
        self.assertEqual(q.market_state, "POST")
        self.assertEqual(q.currency, "USD")
        self.assertEqual(q.exchange_tz, "America/New_York")

    def test_non_finite_and_non_numeric_values_become_none(self):
        FakeYfData.handler = lambda syms: response(raw_quote(
            "AAPL", float("nan"), regularMarketDayHigh="n/a", regularMarketDayLow=True,
        ))
        q = self.gateway.batch_quotes(["AAPL"])["AAPL"]
        self.assertIsNone(q.regular_price)
        self.assertIsNone(q.regular_high)
        self.assertIsNone(q.regular_low)

    def test_symbol_yahoo_omits_is_none(self):
        # Gotcha 3: DOMO was silently missing from a 260-symbol response.
        FakeYfData.handler = lambda syms: response(*(raw_quote(s) for s in syms if s != "DOMO"))
        quotes = self.gateway.batch_quotes(["AAPL", "DOMO"])
        self.assertIsInstance(quotes["AAPL"], Quote)
        self.assertIsNone(quotes["DOMO"])

    def test_unrequested_symbol_in_response_is_ignored(self):
        FakeYfData.handler = lambda syms: response(raw_quote("AAPL"), raw_quote("EXTRA"))
        self.assertEqual(set(self.gateway.batch_quotes(["AAPL"])), {"AAPL"})

    def test_failed_chunk_leaves_other_chunks_results(self):
        def handler(syms):
            if "S000" in syms:
                raise RuntimeError("429 Too Many Requests")
            return response(*(raw_quote(s) for s in syms))

        FakeYfData.handler = handler
        symbols = [f"S{i:03d}" for i in range(150)]
        with self.assertLogs(gw_mod.logger, "WARNING") as logs:
            quotes = self.gateway.batch_quotes(symbols)
        self.assertTrue(all(quotes[f"S{i:03d}"] is None for i in range(100)))
        self.assertTrue(all(isinstance(quotes[f"S{i:03d}"], Quote) for i in range(100, 150)))
        # Class name only, never the message (which can carry the URL).
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("429", "".join(logs.output))

    def test_malformed_payload_maps_to_none(self):
        for payload in (None, [], {"quoteResponse": None}, {"quoteResponse": {"result": "x"}}):
            FakeYfData.handler = lambda syms, p=payload: p
            self.assertEqual(self.gateway.batch_quotes(["AAPL"]), {"AAPL": None})

    def test_empty_input_makes_no_request(self):
        self.assertEqual(self.gateway.batch_quotes([]), {})
        self.assertEqual(FakeYfData.calls, [])

    def test_hung_chunk_is_abandoned_at_the_deadline(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def handler(syms):
            if "S000" in syms:
                release.wait(5)
            return response(*(raw_quote(s) for s in syms))

        FakeYfData.handler = handler
        symbols = [f"S{i:03d}" for i in range(150)]
        started = time.monotonic()
        with self.assertLogs(gw_mod.logger, "WARNING"):
            quotes = self.gateway.batch_quotes(symbols, timeout=0.3)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertIsNone(quotes["S000"])
        self.assertIsInstance(quotes["S120"], Quote)
        self.assertEqual(FakeYfData.calls[0]["timeout"], 0.3)


class TestYfDataContract(unittest.TestCase):
    """The one yfinance internal in use (#518). If a release moves or reshapes
    it, this fails on the weekly dependency-upgrade PR rather than in prod."""

    def test_yfdata_get_raw_json_signature(self):
        from yfinance.data import YfData

        params = inspect.signature(YfData.get_raw_json).parameters
        self.assertEqual(list(params)[:4], ["self", "url", "params", "timeout"])
        self.assertIs(gw_mod.YfData, YfData)


if __name__ == "__main__":
    unittest.main()
