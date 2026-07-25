"""Unit tests for RecommendationsService — the cross-domain synthesis layer.

Per the Phase 1 plan's testing strategy: "For RecommendationsService, inject
fake services returning canned dicts and assert scoring outcomes." These tests
are fully offline — no DB, no yfinance — exercising the 19-signal decision
engine and the stop-loss synthesis through stub collaborators.

Each fake is a generic object whose method calls return pre-canned dicts; a
method absent from the canned map raises AttributeError, which the service's
per-signal ``try/except`` swallows (that signal simply contributes nothing) —
exactly the production behavior when an upstream service errors.
"""

import unittest

from quantcore.services.recommendations import RecommendationsService


class _Fake:
    """Stub collaborator: ``obj.method(...)`` returns the canned value for
    ``method`` (ignoring args); unknown methods raise AttributeError."""

    def __init__(self, **canned):
        self._canned = canned

    def __getattr__(self, name):
        if name.startswith("_") or name not in self.__dict__.get("_canned", {}):
            raise AttributeError(name)
        value = self._canned[name]
        return lambda *a, **k: value


def _build(prices=None, options=None, microstructure=None, sentiment=None,
           fundamentals=None, yf=None, ohlcv=None) -> RecommendationsService:
    return RecommendationsService(
        prices=_Fake(**(prices or {})),
        options=_Fake(**(options or {})),
        microstructure=_Fake(**(microstructure or {})),
        sentiment=_Fake(**(sentiment or {})),
        fundamentals=_Fake(**(fundamentals or {})),
        ohlcv_repository=_Fake(**(ohlcv or {})),
        yfinance_gateway=_Fake(**(yf or {})),
    )


# Canned price payload reused across scenarios: price 100, BB 90/100/110.
def _price(**overrides):
    data = {
        "price": 100.0,
        "bollinger_bands": {"upper": 110.0, "middle": 100.0, "lower": 90.0},
    }
    data.update(overrides)
    return data


class TradeRecommendationScoringTests(unittest.TestCase):
    def test_strong_bull_case_recommends_long_call(self):
        svc = _build(
            prices={
                "get_stock_price": _price(options={
                    "put_call_ratio": 1.0,
                    "calls": {"avg_iv_pct": 20.0,
                              "atm_contracts": [{"strike": 100, "ask": 2.0}]},
                    "puts": {"atm_contracts": [{"strike": 100, "ask": 2.0}]},
                }),
                "get_rsi": {"rsi": 25.0},
                "get_macd": {"crossover": "bullish_crossover"},
                "get_stochastic": {"k": 20.0},
                "get_volume_analysis": {"bottom_signal": "strong bottom",
                                        "obv_divergence": True, "climax_events": []},
                "get_candlestick_patterns": {"patterns_found": [
                    {"pattern": "hammer", "bias": "bullish",
                     "strength": "strong", "strength_score": 5}]},
                "get_vwap": {"vwap": 98.0, "position": "above_vwap",
                             "consecutive_bars_above": 4},
                "get_historical_drawdown": {"trailing_stop_pct": 8.0,
                                            "max_1day_drawdown_pct": -5,
                                            "max_5day_drawdown_pct": -10,
                                            "max_intraday_drop_pct": -6,
                                            "recent_max_1day_pct": -4},
            },
            options={
                "get_unusual_calls": {"sweep_signal": "strong"},
                "get_delta_adjusted_oi": {"signal": "strong",
                                          "mm_hedge_bias": "buy_on_dip",
                                          "gamma_wall_strike": 95.0,
                                          "net_daoi_shares": 100_000},
            },
            microstructure={
                "get_short_interest": {"squeeze_potential": "LOW"},
                "get_dark_pool": {"net_signal": "accumulation"},
                "get_bid_ask_spread": {"spread_vs_norm": "narrowing"},
            },
            fundamentals={
                "get_earnings_calendar": {"days_to_earnings": 30,
                                          "risk_level": "LOW",
                                          "pre_earnings_setup": False},
                "get_fundamental_score": {"composite_score": 9,
                                          "fundamental_label": "elite"},
                "get_revenue_growth": {"trajectory": "accelerating"},
                "get_earnings_acceleration": {"acceleration_score": 2,
                                              "acceleration_label": "accelerating"},
            },
            sentiment={"get_news": {"sentiment_summary": {
                "scored_count": 5, "positive_count": 4, "negative_count": 0}}},
            yf={"info": {"sector": "Technology", "shortPercentOfFloat": 0.05,
                         "shortRatio": 2}},
        )

        rec = svc.get_trade_recommendation("XYZ", capital=5000.0)

        self.assertEqual(rec["action"], "BUY")
        self.assertEqual(rec["trade_type"], "LONG_CALL")  # low IV → outright call
        self.assertEqual(rec["confidence"], "HIGH")
        self.assertGreater(rec["bull_score"], rec["bear_score"])
        self.assertGreaterEqual(rec["net_score"], 5)
        self.assertIsNotNone(rec["options_context"])

    def test_strong_bear_case_recommends_long_put(self):
        svc = _build(
            prices={
                "get_stock_price": _price(options={
                    "put_call_ratio": 3.0,  # elevated puts → bearish
                    "calls": {"avg_iv_pct": 20.0,
                              "atm_contracts": [{"strike": 100, "ask": 2.0}]},
                    "puts": {"atm_contracts": [{"strike": 100, "ask": 2.0}]},
                }),
                "get_rsi": {"rsi": 78.0},
                "get_macd": {"crossover": "bearish_crossover"},
                "get_stochastic": {"k": 82.0},
                "get_volume_analysis": {"bottom_signal": "none",
                                        "obv_divergence": False,
                                        "climax_events": [{"direction": "up"}]},
                "get_candlestick_patterns": {"patterns_found": [
                    {"pattern": "shooting_star", "bias": "bearish",
                     "strength": "strong", "strength_score": 5}]},
            },
            options={
                "get_delta_adjusted_oi": {"signal": "none",
                                          "mm_hedge_bias": "sell_on_rally",
                                          "net_daoi_shares": -100_000},
            },
            microstructure={
                "get_short_interest": {"squeeze_potential": "LOW"},
                "get_dark_pool": {"net_signal": "distribution"},
                "get_bid_ask_spread": {"spread_vs_norm": "widening"},
            },
            fundamentals={
                "get_earnings_calendar": {"days_to_earnings": 30,
                                          "risk_level": "LOW"},
                "get_fundamental_score": {"composite_score": -5,
                                          "fundamental_label": "weak"},
                "get_revenue_growth": {"trajectory": "decelerating"},
                "get_earnings_acceleration": {"acceleration_score": -2,
                                              "acceleration_label": "decelerating"},
            },
            sentiment={"get_news": {"sentiment_summary": {
                "scored_count": 5, "positive_count": 0, "negative_count": 4}}},
        )

        rec = svc.get_trade_recommendation("XYZ")

        self.assertEqual(rec["action"], "BUY")
        self.assertEqual(rec["trade_type"], "LONG_PUT")  # low IV → outright put
        self.assertEqual(rec["confidence"], "HIGH")
        self.assertGreater(rec["bear_score"], rec["bull_score"])
        self.assertLessEqual(rec["net_score"], -5)

    def test_neutral_signals_skip(self):
        # Only a price is available (bb_pos mid-band → no score); every other
        # signal errors out and contributes nothing → net 0 → SKIP/HOLD.
        svc = _build(prices={"get_stock_price": _price()})

        rec = svc.get_trade_recommendation("XYZ")

        self.assertEqual(rec["trade_type"], "SKIP")
        self.assertEqual(rec["action"], "HOLD")
        self.assertEqual(rec["net_score"], 0)

    def test_missing_price_returns_skip_error(self):
        svc = _build()  # get_stock_price absent → price stays None

        rec = svc.get_trade_recommendation("XYZ")

        self.assertEqual(rec["trade_type"], "SKIP")
        self.assertEqual(rec["action"], "HOLD")
        self.assertIn("error", rec)

    def test_high_short_interest_squeeze_override_forces_long_call(self):
        # Modest bull score (RSI oversold +3, squeeze HIGH +1 = net 4) would be
        # LONG_STOCK, but the squeeze override (HIGH SI + net>=3) forces LONG_CALL.
        svc = _build(
            prices={
                "get_stock_price": _price(),
                "get_rsi": {"rsi": 25.0},
            },
            microstructure={"get_short_interest": {"squeeze_potential": "HIGH",
                                                   "squeeze_note": "tight float"}},
        )

        rec = svc.get_trade_recommendation("XYZ")

        self.assertEqual(rec["trade_type"], "LONG_CALL")
        self.assertEqual(rec["action"], "BUY")
        self.assertGreaterEqual(rec["net_score"], 3)

    def test_imminent_earnings_suppresses_options_trade(self):
        # Bull score of 5 (RSI +3, MACD +2) → LONG_CALL, but earnings in 3 days
        # with net_score < 7 triggers the earnings override → SKIP.
        svc = _build(
            prices={
                "get_stock_price": _price(),
                "get_rsi": {"rsi": 25.0},
                "get_macd": {"crossover": "bullish_crossover"},
            },
            fundamentals={"get_earnings_calendar": {"days_to_earnings": 3,
                                                    "risk_level": "HIGH"}},
        )

        rec = svc.get_trade_recommendation("XYZ")

        self.assertEqual(rec["trade_type"], "SKIP")
        self.assertEqual(rec["action"], "HOLD")
        self.assertTrue(any("Earnings override" in w for w in rec["warnings"]))


class StopLossSynthesisTests(unittest.TestCase):
    def test_stop_loss_places_technical_stop_below_price(self):
        svc = _build(
            prices={
                "get_stock_price": _price(),
                "get_vwap": {"vwap": 98.0, "position": "above_vwap",
                             "consecutive_bars_above": 5},
                "get_macd": {"crossover": "bullish_crossover"},
                "get_rsi": {"rsi": 45.0},
                "get_historical_drawdown": {"trailing_stop_pct": 8.0,
                                            "max_1day_drawdown_pct": -5,
                                            "max_5day_drawdown_pct": -10,
                                            "max_intraday_drop_pct": -6,
                                            "recent_max_1day_pct": -4},
            },
            options={"get_delta_adjusted_oi": {"gamma_wall_strike": 95.0}},
            yf={"info": {"shortPercentOfFloat": 0.05, "shortRatio": 2,
                         "sector": "Technology"}},
        )

        result = svc.get_stop_loss_analysis("xyz", cost_basis=80.0, shares=100)

        self.assertEqual(result["symbol"], "XYZ")
        self.assertEqual(result["price"], 100.0)
        stops = result["stops"]
        self.assertLess(stops["technical_stop"], 100.0)
        self.assertGreater(stops["technical_stop"], 0)
        # VWAP (98) is the highest support below price → primary support.
        self.assertEqual(result["technical"]["primary_support"], "vwap")
        # P&L block populated when cost_basis + shares supplied.
        self.assertEqual(result["position"]["cost_basis"], 80.0)
        self.assertEqual(result["position"]["shares"], 100)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Relative strength (85%-campaign part 8)
# ---------------------------------------------------------------------------

import numpy as _np  # noqa: E402
import pandas as _pd  # noqa: E402
from unittest.mock import Mock as _Mock, patch as _patch  # noqa: E402

from quantcore.services.recommendations import RecommendationsService as _RS  # noqa: E402


def _mock_service():
    return _RS(
        prices=_Mock(), options=_Mock(), microstructure=_Mock(),
        sentiment=_Mock(), fundamentals=_Mock(),
        ohlcv_repository=_Mock(), yfinance_gateway=_Mock(),
    )


def _trend_df(total_return_pct, n=160):
    idx = _pd.bdate_range(end="2026-07-17", periods=n)
    closes = _np.linspace(100.0, 100.0 * (1 + total_return_pct / 100.0), n)
    return _pd.DataFrame({"Close": closes}, index=idx)


class RelativeStrengthHistoryTests(unittest.TestCase):
    def test_outperformer_reads_positive_rs(self):
        svc = _mock_service()
        frames = {"WINNR": _trend_df(30), "SPY": _trend_df(8),
                  "QQQ": _trend_df(10), "XLK": _trend_df(12)}
        svc._prices.get_history.side_effect = (
            lambda sym, interval="1d", days=0: frames[sym]
        )
        with _patch.object(svc, "_get_sector_etf", return_value="XLK"):
            out = svc.get_relative_strength_history("winnr", since_days=30)
        self.assertEqual(out["symbol"], "WINNR")
        self.assertEqual(out["sector_etf"], "XLK")
        self.assertEqual(out["data_points"], 30)
        last = out["history"][-1]
        self.assertGreater(last["rs_vs_spy"], 0)
        self.assertGreater(last["rs_vs_qqq"], 0)
        self.assertIsNotNone(last["rs_vs_sector"])
        self.assertIn(last["rs_label"],
                      {"leader", "outperforming", "neutral", "laggard", "weak"})

    def test_missing_history_degrades(self):
        svc = _mock_service()
        svc._prices.get_history.return_value = _pd.DataFrame()
        with _patch.object(svc, "_get_sector_etf", return_value=None):
            out = svc.get_relative_strength_history("GHOST")
        self.assertEqual(out["history"], [])
        self.assertIn("error", out)


class RelativeStrengthTests(unittest.TestCase):
    def _download_frame(self, returns_by_ticker, n=300):
        idx = _pd.bdate_range(end="2026-07-17", periods=n)
        closes = _pd.DataFrame(
            {t: _np.linspace(100.0, 100.0 * (1 + r / 100.0), n)
             for t, r in returns_by_ticker.items()},
            index=idx,
        )
        return _pd.concat({"Close": closes}, axis=1)

    def test_market_leader_labels_and_sector_momentum(self):
        svc = _mock_service()
        svc._yf.info.return_value = {"sector": "Technology"}
        svc._yf.download.return_value = self._download_frame(
            {"HERO": 45.0, "SPY": 10.0, "QQQ": 15.0, "XLK": 20.0}
        )
        out = svc.get_relative_strength("hero")
        self.assertEqual(out["sector"], "Technology")
        returns = out["returns"]
        for leg in ("stock", "spy", "qqq", "sector"):
            self.assertIsNotNone(returns[leg]["12m"], leg)
        # Self-consistent ratio math.
        self.assertAlmostEqual(
            out["rs_ratio_vs_spy"],
            round(returns["stock"]["12m"] - returns["spy"]["12m"], 2),
        )
        self.assertEqual(out["relative_strength_label"], "leader")
        self.assertIn(out["sector_momentum"],
                      {"sector_leading", "sector_neutral", "sector_lagging"})

    def test_provider_failures_leave_safe_defaults(self):
        svc = _mock_service()
        svc._yf.info.side_effect = RuntimeError("info down")
        svc._yf.download.side_effect = RuntimeError("download down")
        out = svc.get_relative_strength("GHOST")
        self.assertEqual(out["relative_strength_label"], "unknown")
        self.assertEqual(out["sector_momentum"], "unknown")
        self.assertIsNone(out["rs_ratio_vs_spy"])
