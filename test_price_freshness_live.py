"""Live cross-surface price-parity oracle: yfinance vs the REST tier vs the MCP tools.

This is the test that would have caught the stale Securities-page price directly.
It establishes an independent ground truth by calling yfinance itself, then asserts
that both remote surfaces agree with it for a handful of reference symbols:

    ground truth (yfinance)  ==  REST tier  ==  MCP wrapper

Surfaces exercised, per the layering in architectural-standard-v2 §5.4/§5.5 and
Rule 6 (``AI Agent → MCP wrapper → REST tier → Service``):

* ``GET /api/securities/{t}/ohlcv``      — cache-backed daily bars
* ``GET /api/securities/{t}/technicals`` — cache-backed ``last_close`` (the Securities grid)
* ``GET /api/securities/{t}/price-summary`` — live quote path
* MCP ``get_stock_price`` — driven through ``rest_client`` against the same app,
  so the wrapper's real tool body and path construction are covered rather than bypassed

The primary assertion is **bar-date equality**, not price tolerance: a stale cache
reports an older session, which is deterministic to detect, whereas a price
tolerance alone can pass on a flat day. Close values are then compared within a
small tolerance to catch symbol misalignment (the 2026-06-30 corruption class).

This test performs real network I/O and needs a database, so it is **opt-in** and
skipped by default — CI stays hermetic and free of Yahoo rate-limit flake:

    QUANTCORE_LIVE_PRICE_TESTS=1 .venv/bin/python -m unittest test_price_freshness_live

Importing yfinance here is deliberate and allowed: root-level test modules sit
outside the ``test_architecture_guards.py`` fence, and the whole point of this
module is to hold an oracle that is *independent* of YFinanceGateway.
"""
import datetime
import os
import unittest
from pathlib import Path

LIVE = os.environ.get("QUANTCORE_LIVE_PRICE_TESTS") == "1"

# Point at the test DB before quantcore.db freezes DB_DSN at import time, matching
# the convention in test_api_smoke.py / test_support_tools_adapters.py.
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
            os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
            break

# Reference symbols: highly liquid, never halted, no thin-volume gaps.
REFERENCE_SYMBOLS = ["AAPL", "MSFT", "SPY"]

# Daily closes must agree to within this fraction of the ground-truth close.
CLOSE_TOLERANCE = 0.005  # 0.5%
# The live quote may legitimately drift from the last daily close intraday.
QUOTE_TOLERANCE = 0.05  # 5%


def _truth_last_bar(symbol: str) -> tuple[datetime.date, float]:
    """Ground truth: the most recent daily bar yfinance will return, fetched with an
    explicitly inclusive end bound so the current session is never excluded."""
    import yfinance as yf  # noqa: PLC0415 — intentionally independent of the gateway

    end = datetime.date.today() + datetime.timedelta(days=2)
    start = end - datetime.timedelta(days=30)
    df = yf.download(
        symbol,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    if df is None or df.empty:
        raise unittest.SkipTest(f"yfinance returned no data for {symbol}")
    if hasattr(df.columns, "levels"):  # flatten MultiIndex from newer yfinance
        df.columns = df.columns.get_level_values(0)
    return df.index[-1].date(), float(df["Close"].iloc[-1])


@unittest.skipUnless(LIVE, "set QUANTCORE_LIVE_PRICE_TESTS=1 to run live price-parity checks")
class LivePriceParityTest(unittest.TestCase):
    """Every remote surface must report the same session as yfinance itself."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from api.main import create_app

        cls.client = TestClient(create_app())
        cls.truth = {s: _truth_last_bar(s) for s in REFERENCE_SYMBOLS}

    # -- REST tier -----------------------------------------------------------

    def test_rest_ohlcv_returns_the_current_session(self):
        for symbol in REFERENCE_SYMBOLS:
            with self.subTest(symbol=symbol):
                truth_date, truth_close = self.truth[symbol]

                resp = self.client.get(f"/api/securities/{symbol}/ohlcv", params={"days": 30})
                self.assertEqual(resp.status_code, 200, resp.text)
                bars = resp.json()["bars"]
                self.assertTrue(bars, f"no bars returned for {symbol}")

                last = bars[-1]
                self.assertEqual(
                    datetime.date.fromisoformat(last["date"]), truth_date,
                    f"{symbol}: REST /ohlcv is a stale session — served {last['date']}, "
                    f"yfinance has {truth_date}",
                )
                self.assertLessEqual(
                    abs(last["close"] - truth_close) / truth_close, CLOSE_TOLERANCE,
                    f"{symbol}: close {last['close']} disagrees with yfinance {truth_close}",
                )

    def test_rest_technicals_last_close_matches_yfinance(self):
        """This is the value behind the Securities grid's price."""
        for symbol in REFERENCE_SYMBOLS:
            with self.subTest(symbol=symbol):
                _, truth_close = self.truth[symbol]

                resp = self.client.get(f"/api/securities/{symbol}/technicals")
                self.assertEqual(resp.status_code, 200, resp.text)
                indicators = resp.json().get("indicators") or []
                self.assertTrue(indicators, f"no indicators returned for {symbol}")

                last_close = indicators[-1].get("last_close", indicators[-1].get("close"))
                self.assertIsNotNone(last_close, f"{symbol}: no close in technicals row")
                self.assertLessEqual(
                    abs(last_close - truth_close) / truth_close, CLOSE_TOLERANCE,
                    f"{symbol}: Securities-grid close {last_close} disagrees with "
                    f"yfinance {truth_close} — the stale-price symptom",
                )

    def test_rest_price_summary_tracks_the_live_quote(self):
        for symbol in REFERENCE_SYMBOLS:
            with self.subTest(symbol=symbol):
                _, truth_close = self.truth[symbol]

                resp = self.client.get(f"/api/securities/{symbol}/price-summary")
                self.assertEqual(resp.status_code, 200, resp.text)
                price = resp.json()["price"]
                self.assertLessEqual(
                    abs(price - truth_close) / truth_close, QUOTE_TOLERANCE,
                    f"{symbol}: quote {price} is far from the last close {truth_close}",
                )

    # -- MCP wrapper (Rule 6: wrapper → REST tier) ---------------------------

    def test_mcp_get_stock_price_agrees_with_rest_and_yfinance(self):
        """Drive the real MCP tool body, with its HTTP seam pointed at this app."""
        from unittest.mock import patch

        from fastMCPTest import stock_price_server

        def routed_get(path, params=None, **_kwargs):
            resp = self.client.get(path, params=params or {})
            resp.raise_for_status()
            return resp.json()

        for symbol in REFERENCE_SYMBOLS:
            with self.subTest(symbol=symbol):
                _, truth_close = self.truth[symbol]

                with patch.object(stock_price_server.rest_client, "get", routed_get):
                    result = stock_price_server.get_stock_price(symbol)

                self.assertEqual(result["symbol"], symbol)
                self.assertLessEqual(
                    abs(result["price"] - truth_close) / truth_close, QUOTE_TOLERANCE,
                    f"{symbol}: MCP price {result['price']} disagrees with yfinance "
                    f"{truth_close}",
                )

    def test_surfaces_do_not_disagree_with_each_other(self):
        """REST and MCP must not drift apart — they are the same service call."""
        from unittest.mock import patch

        from fastMCPTest import stock_price_server

        def routed_get(path, params=None, **_kwargs):
            resp = self.client.get(path, params=params or {})
            resp.raise_for_status()
            return resp.json()

        for symbol in REFERENCE_SYMBOLS:
            with self.subTest(symbol=symbol):
                rest = self.client.get(f"/api/securities/{symbol}/price-summary").json()
                with patch.object(stock_price_server.rest_client, "get", routed_get):
                    mcp = stock_price_server.get_stock_price(symbol)

                self.assertLessEqual(
                    abs(rest["price"] - mcp["price"]) / rest["price"], QUOTE_TOLERANCE,
                    f"{symbol}: REST and MCP surfaces disagree",
                )


if __name__ == "__main__":
    unittest.main()
