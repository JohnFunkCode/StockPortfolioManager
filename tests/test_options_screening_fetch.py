"""Tests for OptionsScreeningService's fetch_* pipeline (85%-campaign) —
the data-assembly half that test_options_screening_service.py's pure-logic
suite deliberately skipped. Gateway/prices are Mocks; every branch is driven
by literal payloads (chain frames, calendar shapes, news items).
"""
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from quantcore.services.options_screening import (  # noqa: E402
    OptionsScreeningService,
    OptionsSummary,
    POSITIVE_CATALYST_KEYWORDS,
)


def chain_df(oi, vol, strikes=(90.0, 95.0, 100.0, 105.0, 110.0, 130.0), iv=0.42):
    n = len(strikes)
    return pd.DataFrame({
        "strike": list(strikes),
        "openInterest": [oi] * n,
        "volume": [vol] * n,
        "impliedVolatility": [iv] * n,
        "lastPrice": [2.0] * n,
        "bid": [1.9] * n,
        "ask": [2.1] * n,
        "inTheMoney": [s < 100 for s in strikes],
    })


def chains(calls, puts):
    return SimpleNamespace(calls=calls, puts=puts)


def history(n=320, base=100.0, daily_move=0.01):
    rng = np.random.default_rng(7)
    rets = rng.normal(0, daily_move, n)
    closes = base * np.exp(np.cumsum(rets))
    idx = pd.bdate_range(end="2026-07-17", periods=n)
    return pd.DataFrame({"Close": closes}, index=idx)


class FetchTestBase(unittest.TestCase):
    def setUp(self):
        self.yf = Mock()
        self.prices = Mock()
        self.service = OptionsScreeningService(
            ohlcv_repository=Mock(), yfinance_gateway=self.yf, prices=self.prices
        )


class TestFetchBollingerBands(FetchTestBase):
    def test_flat_tape_centers_the_bands(self):
        self.prices.get_history.return_value = pd.DataFrame(
            {"Close": [100.0] * 60}, index=pd.bdate_range(end="2026-07-17", periods=60)
        )
        bands = self.service.fetch_bollinger_bands("INTC")
        self.assertEqual(bands.middle, 100.0)
        self.assertEqual(bands.upper, 100.0)   # zero std on a flat tape

    def test_thin_history_and_errors_return_none(self):
        self.prices.get_history.return_value = pd.DataFrame({"Close": [1.0] * 5})
        self.assertIsNone(self.service.fetch_bollinger_bands("INTC"))
        self.prices.get_history.side_effect = RuntimeError("db away")
        self.assertIsNone(self.service.fetch_bollinger_bands("INTC"))


class TestFetchOptions(FetchTestBase):
    def test_aggregates_and_atm_selection(self):
        self.yf.expirations.return_value = ("2026-08-21", "2026-09-18")
        self.yf.option_chain.return_value = chains(
            chain_df(oi=100, vol=10), chain_df(oi=200, vol=40)
        )
        out = self.service.fetch_options("INTC", price=100.0)
        self.assertEqual(out.expiration, "2026-08-21")
        self.assertEqual(out.total_call_oi, 600)
        self.assertEqual(out.total_put_oi, 1200)
        self.assertEqual(out.put_call_ratio, 2.0)
        self.assertEqual(len(out.atm_calls), 5)
        strikes = [c["strike"] for c in out.atm_calls]
        self.assertEqual(strikes, sorted(strikes))
        self.assertNotIn(130.0, strikes)       # far strike excluded from ATM-5

    def test_no_expirations_or_empty_side_returns_none(self):
        self.yf.expirations.return_value = ()
        self.assertIsNone(self.service.fetch_options("INTC", 100.0))
        self.yf.expirations.return_value = ("2026-08-21",)
        self.yf.option_chain.return_value = chains(chain_df(1, 1), pd.DataFrame())
        self.assertIsNone(self.service.fetch_options("INTC", 100.0))


class TestFetchPutCallAnalysis(FetchTestBase):
    def arm(self, near_put_vol, mid_put_oi=100):
        near = chains(chain_df(oi=100, vol=100), chain_df(oi=150, vol=near_put_vol))
        mid = chains(chain_df(oi=100, vol=10), chain_df(oi=mid_put_oi, vol=10))
        self.yf.expirations.return_value = ("2026-08-21", "2026-09-18")
        self.yf.option_chain.side_effect = [near, mid]

    def test_put_unwinding_detected(self):
        # OI P/C 1.5, vol P/C 0.5 -> ratio 0.33 <= 0.75 -> unwinding.
        self.arm(near_put_vol=50)
        out = self.service.fetch_put_call_analysis("INTC", price=100.0)
        self.assertEqual(out.near_oi_pc, 1.5)
        self.assertEqual(out.near_vol_pc, 0.5)
        self.assertTrue(out.put_unwinding)
        self.assertFalse(out.fresh_put_buying)
        self.assertEqual(out.mid_expiry, "2026-09-18")
        self.assertEqual(out.term_skew, 0.5)   # 1.5 near - 1.0 mid
        self.assertTrue(out.near_term_fear)    # skew >= 0.30
        self.assertIsNotNone(out.near_atm_pc)

    def test_fresh_put_buying_detected(self):
        # OI P/C 1.5, vol P/C 4.0 -> ratio 2.67 >= 1.5 -> fresh buying.
        self.arm(near_put_vol=400)
        out = self.service.fetch_put_call_analysis("INTC", price=100.0)
        self.assertTrue(out.fresh_put_buying)
        self.assertFalse(out.put_unwinding)

    def test_no_expirations_returns_none(self):
        self.yf.expirations.return_value = ()
        self.assertIsNone(self.service.fetch_put_call_analysis("INTC", 100.0))


class TestFetchIvAnalysis(FetchTestBase):
    def options_with_iv(self, call_iv, put_iv):
        return OptionsSummary(
            expiration="2026-08-21", put_call_ratio=1.0,
            total_call_oi=1, total_put_oi=1, total_call_volume=1,
            total_put_volume=1, avg_call_iv=call_iv, avg_put_iv=put_iv,
        )

    def test_current_iv_averages_the_chain_sides(self):
        self.prices.get_history.return_value = history()
        out = self.service.fetch_iv_analysis("INTC", self.options_with_iv(40.0, 50.0))
        self.assertEqual(out.current_iv, 45.0)
        self.assertGreater(out.hv_30, 0)
        self.assertGreaterEqual(out.iv_rank, 0.0)
        self.assertLessEqual(out.iv_rank, 100.0)
        self.assertGreaterEqual(out.iv_percentile, 0.0)
        self.assertTrue(out.label)

    def test_without_options_falls_back_to_hv(self):
        self.prices.get_history.return_value = history()
        out = self.service.fetch_iv_analysis("INTC", None)
        self.assertEqual(out.current_iv, out.hv_30)
        self.assertEqual(out.iv_vs_hv, 1.0)

    def test_thin_history_returns_none(self):
        self.prices.get_history.return_value = pd.DataFrame({"Close": [1.0] * 10})
        self.assertIsNone(self.service.fetch_iv_analysis("INTC", None))


class TestEarningsProximity(FetchTestBase):
    def test_dataframe_calendar(self):
        earn = date.today() + timedelta(days=10)
        self.yf.calendar.return_value = pd.DataFrame(
            {0: [pd.Timestamp(earn)]}, index=["Earnings Date"]
        )
        self.assertEqual(self.service.fetch_earnings_proximity("INTC"), 10)

    def test_dict_calendar_and_past_dates(self):
        earn = date.today() + timedelta(days=25)
        self.yf.calendar.return_value = {"Earnings Date": earn}
        self.assertEqual(self.service.fetch_earnings_proximity("INTC"), 25)
        self.yf.calendar.return_value = {"Earnings Date": date(2020, 1, 1)}
        self.assertIsNone(self.service.fetch_earnings_proximity("INTC"))
        self.yf.calendar.return_value = None
        self.assertIsNone(self.service.fetch_earnings_proximity("INTC"))


class TestPositiveCatalyst(FetchTestBase):
    def news_item(self, title, days_ago=1.0):
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp()
        return {"title": title, "providerPublishTime": int(ts)}

    def test_recent_keyword_headline_triggers(self):
        keyword = sorted(POSITIVE_CATALYST_KEYWORDS)[0]
        headline = f"Big news: {keyword} announced"
        self.yf.news.return_value = [self.news_item(headline)]
        hit, title = self.service.fetch_recent_positive_catalyst("INTC")
        self.assertTrue(hit)
        self.assertEqual(title, headline)

    def test_stale_or_neutral_news_does_not_trigger(self):
        keyword = sorted(POSITIVE_CATALYST_KEYWORDS)[0]
        self.yf.news.return_value = [
            self.news_item(f"{keyword} long ago", days_ago=45.0),
            self.news_item("Quarterly filing published", days_ago=1.0),
        ]
        self.assertEqual(
            self.service.fetch_recent_positive_catalyst("INTC"), (False, "")
        )
        self.yf.news.return_value = []
        self.assertEqual(
            self.service.fetch_recent_positive_catalyst("INTC"), (False, "")
        )


class TestFetchSecurity(FetchTestBase):
    def arm_components(self):
        self.yf.fast_info.return_value = SimpleNamespace(last_price=100.0)
        patches = {
            "fetch_bollinger_bands": Mock(return_value=Mock(lower=90, middle=100, upper=110)),
            "fetch_options": Mock(return_value=None),
            "fetch_iv_analysis": Mock(return_value=None),
            "fetch_put_call_analysis": Mock(return_value=None),
            "fetch_earnings_proximity": Mock(return_value=45),
        }
        for name, mock in patches.items():
            patcher = patch.object(self.service, name, mock)
            patcher.start()
            self.addCleanup(patcher.stop)
        return patches

    def test_assembles_with_news_store_bullish_signal(self):
        self.arm_components()
        news_store = Mock()
        news_store.get_sentiment_summary.return_value = {
            "signal": "BULLISH", "scored_articles": 3,
            "top_positive": ["Upgraded to buy"], "top_negative": [],
        }
        sec = self.service.fetch_security("intc", "Intel", ["chips"],
                                          news_store=news_store)
        self.assertEqual(sec.symbol, "INTC")
        self.assertEqual(sec.news_signal, "BULLISH")
        self.assertTrue(sec.recent_positive_catalyst)      # bullish news = catalyst
        self.assertEqual(sec.catalyst_headline, "Upgraded to buy")
        self.assertEqual(sec.days_to_earnings, 45)

    def test_falls_back_to_keyword_scan_without_news_store(self):
        self.arm_components()
        with patch.object(self.service, "fetch_recent_positive_catalyst",
                          return_value=(True, "Upgrade!")):
            sec = self.service.fetch_security("INTC", "Intel", [])
        self.assertTrue(sec.recent_positive_catalyst)
        self.assertEqual(sec.catalyst_headline, "Upgrade!")

    def test_missing_price_or_bands_returns_none(self):
        self.yf.fast_info.return_value = SimpleNamespace(last_price=None)
        self.assertIsNone(self.service.fetch_security("INTC", "Intel", []))
        self.yf.fast_info.return_value = SimpleNamespace(last_price=100.0)
        with patch.object(self.service, "fetch_bollinger_bands", return_value=None):
            self.assertIsNone(self.service.fetch_security("INTC", "Intel", []))


if __name__ == "__main__":
    unittest.main()


class TestFetchAndStoreFullChain(unittest.TestCase):
    """fetch_and_store_full_chain — the shared live-refresh helper in
    quantcore/services/options_contracts.py (85%-campaign closer)."""

    def test_fetches_every_expiration_and_persists(self):
        from quantcore.services.options_contracts import fetch_and_store_full_chain

        gw = Mock()
        gw.fast_info.return_value = SimpleNamespace(last_price=100.0)
        gw.expirations.return_value = ("2026-08-21", "2026-09-18")
        gw.option_chain.side_effect = [
            chains(chain_df(oi=100, vol=10), chain_df(oi=200, vol=20)),
            RuntimeError("second expiry unavailable"),   # skipped, not fatal
        ]
        store = Mock()
        store.save_full_chain.return_value = 77
        store.get_full_chain.return_value = {"price": 100.0}

        out = fetch_and_store_full_chain("intc", store, gateway=gw)
        self.assertEqual(out["snapshot_id"], 77)
        self.assertEqual(out["expiration_count"], 1)     # bad expiry skipped
        self.assertGreater(out["total_contracts"], 0)
        _, kwargs = store.save_full_chain.call_args
        self.assertEqual(kwargs["symbol"], "INTC")
        self.assertEqual(kwargs["expirations_data"][0]["put_call_ratio"], 2.0)

    def test_missing_price_raises(self):
        from quantcore.services.options_contracts import fetch_and_store_full_chain

        gw = Mock()
        gw.fast_info.return_value = SimpleNamespace(last_price=None)
        with self.assertRaises(ValueError):
            fetch_and_store_full_chain("ZZNONE", Mock(), gateway=gw)
