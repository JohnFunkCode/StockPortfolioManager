"""Unit tests for PricesService's indicator/pattern/screening surface.

Coverage uplift (July 2026): every method here follows the same testable
shape — get_history() -> pure computation -> dict — so the suite patches
``get_history`` with engineered OHLCV frames whose expected outcomes are
known analytically (a monotonic rally MUST read RSI 100; an engineered
hammer MUST classify as a hammer; a bar spanning a gap MUST fill it).
No DB, no network: repositories and the gateway are Mocks.

The get_history fetch-when-stale policy itself is pinned separately in
test_prices_history_policy.py.
"""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from quantcore.services.prices import PricesService, _safe_int, _summarize_options


def bars(closes, volumes=None, opens=None, highs=None, lows=None, end="2026-06-30"):
    """OHLCV frame from a close series; OHLC default to a tight bar around close."""
    closes = [float(c) for c in closes]
    n = len(closes)
    idx = pd.bdate_range(end=end, periods=n)
    opens = [float(o) for o in opens] if opens is not None else [c * 0.999 for c in closes]
    highs = [float(h) for h in highs] if highs is not None else [
        max(o, c) * 1.002 for o, c in zip(opens, closes)
    ]
    lows = [float(low) for low in lows] if lows is not None else [
        min(o, c) * 0.998 for o, c in zip(opens, closes)
    ]
    volumes = [float(v) for v in volumes] if volumes is not None else [1_000_000.0] * n
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=idx,
    )


def flat(n, price=100.0, **kw):
    return bars([price] * n, **kw)


class PricesServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.ohlcv = Mock()
        self.yf = Mock()
        self.options = Mock()
        self.sentiment = Mock()
        self.service = PricesService(
            ohlcv_repository=self.ohlcv,
            yfinance_gateway=self.yf,
            options_repository=self.options,
            sentiment_repository=self.sentiment,
        )

    def with_history(self, df):
        return patch.object(self.service, "get_history", return_value=df)


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

class TestSafeInt(unittest.TestCase):
    def test_conversions(self):
        self.assertEqual(_safe_int(5.7), 5)
        self.assertEqual(_safe_int(None), 0)
        self.assertEqual(_safe_int(float("nan")), 0)
        self.assertEqual(_safe_int("12"), 12)
        self.assertEqual(_safe_int("garbage"), 0)


class TestSummarizeOptions(unittest.TestCase):
    def chain(self):
        return pd.DataFrame({
            "strike": [90.0, 95.0, 100.0, 105.0, 110.0, 150.0, 0.0],
            "lastPrice": [11.0, 7.0, 4.0, 2.0, 1.0, 0.1, 0.0],
            "bid": [10.8, 6.8, 3.9, 1.9, 0.9, 0.05, 0.0],
            "ask": [11.2, 7.2, 4.1, 2.1, 1.1, 0.15, 0.0],
            "impliedVolatility": [0.5, 0.48, 0.45, 0.47, 0.5, 0.9, 0.0],
            "volume": [10, 20, 300, 40, 5, float("nan"), 0],
            "openInterest": [100, 200, 3000, 400, 50, 1, 0],
            "inTheMoney": [True, True, False, False, False, False, False],
        })

    def test_atm_selection_and_aggregates(self):
        out = _summarize_options(self.chain(), price=101.0, kind="call")
        strikes = [c["strike"] for c in out["atm_contracts"]]
        self.assertEqual(strikes, sorted(strikes))
        self.assertEqual(len(strikes), 5)
        self.assertNotIn(150.0, strikes)  # far strike excluded from ATM-5
        self.assertNotIn(0.0, strikes)    # zero strikes filtered entirely
        self.assertEqual(out["total_open_interest"], 3751)
        self.assertEqual(out["total_volume"], 375)  # NaN volume counted as 0
        self.assertGreater(out["avg_iv_pct"], 0)


# ---------------------------------------------------------------------------
# Quote + options summary
# ---------------------------------------------------------------------------

class TestGetStockPrice(PricesServiceTestBase):
    def arm_gateway(self, price=100.0, expirations=("2026-08-21",)):
        self.yf.fast_info.return_value = SimpleNamespace(last_price=price, currency="USD")
        self.yf.expirations.return_value = tuple(expirations)
        chain_df = TestSummarizeOptions().chain()
        self.yf.option_chain.return_value = SimpleNamespace(calls=chain_df, puts=chain_df)

    def test_full_quote_with_bands_and_chain_snapshot_saved(self):
        self.arm_gateway()
        with self.with_history(flat(60, 100.0)):
            out = self.service.get_stock_price("intc")
        self.assertEqual(out["symbol"], "INTC")
        self.assertEqual(out["price"], 100.0)
        self.assertAlmostEqual(out["bollinger_bands"]["middle"], 100.0, places=2)
        self.assertEqual(out["options"]["expiration"], "2026-08-21")
        self.assertEqual(out["options"]["put_call_ratio"], 1.0)  # same df both sides
        self.options.save_snapshot.assert_called_once()

    def test_no_expirations_degrades_to_null_options(self):
        self.arm_gateway(expirations=())
        with self.with_history(flat(60)):
            out = self.service.get_stock_price("INTC")
        self.assertIsNone(out["options"])

    def test_short_history_means_no_bands(self):
        self.arm_gateway()
        with self.with_history(flat(10)):
            out = self.service.get_stock_price("INTC")
        self.assertIsNone(out["bollinger_bands"])

    def test_missing_price_raises(self):
        self.yf.fast_info.return_value = SimpleNamespace(last_price=None)
        with self.assertRaises(ValueError):
            self.service.get_stock_price("ZZNONE")


# ---------------------------------------------------------------------------
# Momentum indicators
# ---------------------------------------------------------------------------

class TestGetRsi(PricesServiceTestBase):
    def test_monotonic_rally_pins_rsi_at_100(self):
        with self.with_history(bars(np.linspace(50, 150, 60))):
            out = self.service.get_rsi("intc")
        self.assertEqual(out["rsi"], 100.0)
        self.assertEqual(out["signal"], "overbought")
        self.assertEqual(out["symbol"], "INTC")

    def test_monotonic_selloff_reads_oversold(self):
        with self.with_history(bars(np.linspace(150, 50, 60))):
            out = self.service.get_rsi("INTC")
        self.assertLess(out["rsi"], 5)
        self.assertEqual(out["signal"], "oversold")

    def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            self.service.get_rsi("INTC", interval="5m")

    def test_insufficient_data_rejected(self):
        with self.with_history(flat(10)):
            with self.assertRaises(ValueError):
                self.service.get_rsi("INTC", period=14)


class TestGetMacd(PricesServiceTestBase):
    def test_sustained_uptrend_reads_bullish(self):
        with self.with_history(bars(np.linspace(80, 120, 80))):
            out = self.service.get_macd("INTC")
        self.assertTrue(out["crossover"].startswith("bullish"), out["crossover"])
        self.assertGreater(out["macd"], out["signal"])

    def test_sustained_downtrend_reads_bearish(self):
        with self.with_history(bars(np.linspace(120, 80, 80))):
            out = self.service.get_macd("INTC")
        self.assertTrue(out["crossover"].startswith("bearish"), out["crossover"])

    def test_v_bottom_produces_a_bullish_crossover_event(self):
        closes = list(np.linspace(120, 90, 60)) + list(np.linspace(90, 112, 12))
        with self.with_history(bars(closes)):
            out = self.service.get_macd("INTC")
        self.assertTrue(out["crossover"].startswith("bullish"), out["crossover"])

    def test_insufficient_data_rejected(self):
        with self.with_history(flat(20)):
            with self.assertRaises(ValueError):
                self.service.get_macd("INTC")


class TestGetStochastic(PricesServiceTestBase):
    def test_close_at_window_high_is_overbought(self):
        closes = list(np.linspace(90, 100, 40))
        df = bars(closes, highs=[c + 0.5 for c in closes], lows=[c - 0.5 for c in closes])
        with self.with_history(df):
            out = self.service.get_stochastic("INTC")
        self.assertGreater(out["k"], 80)
        self.assertEqual(out["signal"], "overbought")

    def test_close_at_window_low_is_oversold(self):
        closes = list(np.linspace(100, 90, 40))
        df = bars(closes, highs=[c + 0.5 for c in closes], lows=[c - 0.5 for c in closes])
        with self.with_history(df):
            out = self.service.get_stochastic("INTC")
        self.assertLess(out["k"], 20)
        self.assertEqual(out["signal"], "oversold")

    def test_insufficient_data_rejected(self):
        with self.with_history(flat(10)):
            with self.assertRaises(ValueError):
                self.service.get_stochastic("INTC")


# ---------------------------------------------------------------------------
# Volume studies
# ---------------------------------------------------------------------------

class TestGetVolumeAnalysis(PricesServiceTestBase):
    def test_quiet_tape_reports_no_signal(self):
        with self.with_history(flat(60)):
            out = self.service.get_volume_analysis("INTC")
        self.assertEqual(out["climax_events"], [])
        self.assertTrue(out["bottom_signal"].startswith("none"))

    def test_capitulation_bar_with_quiet_follow_through_detected(self):
        n = 60
        closes = [100.0] * (n - 2) + [88.0, 88.5]
        opens = [99.9] * (n - 2) + [100.0, 88.4]
        highs = [100.2] * (n - 2) + [100.5, 88.9]
        lows = [99.8] * (n - 2) + [87.0, 88.0]
        volumes = [1_000_000.0] * (n - 2) + [3_500_000.0, 300_000.0]
        df = bars(closes, volumes=volumes, opens=opens, highs=highs, lows=lows)
        with self.with_history(df):
            out = self.service.get_volume_analysis("INTC")
        self.assertGreaterEqual(len(out["climax_events"]), 1)
        event = out["climax_events"][0]
        self.assertEqual(event["direction"], "down")
        self.assertTrue(event["quiet_follow_through"])
        self.assertIn("capitulation", out["bottom_signal"])


class TestGetObv(PricesServiceTestBase):
    def test_accumulation_under_falling_price_is_bullish_divergence(self):
        closes, volumes = [], []
        price = 100.0
        for i in range(60):
            if i % 2 == 0:
                price -= 1.0   # down days: light volume
                volumes.append(200_000.0)
            else:
                price += 0.4   # up days: heavy volume -> OBV rises
                volumes.append(5_000_000.0)
            closes.append(price)
        with self.with_history(bars(closes, volumes=volumes)):
            out = self.service.get_obv("INTC")
        self.assertEqual(out["divergence"], "bullish")
        self.assertEqual(out["obv_trend"], "rising")
        self.assertEqual(out["price_trend"], "falling")
        self.assertIn("divergence", out["interpretation"].lower())
        self.assertEqual(len(out["recent_bars"]), 10)

    def test_confirming_uptrend_reports_no_divergence(self):
        with self.with_history(bars(np.linspace(90, 110, 60))):
            out = self.service.get_obv("INTC")
        self.assertEqual(out["divergence"], "none")
        self.assertIn("confirming price uptrend", out["interpretation"])


# ---------------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------------

class TestGetVwap(PricesServiceTestBase):
    def test_constant_tape_sits_exactly_on_vwap(self):
        df = flat(40, 100.0, opens=[100.0] * 40, highs=[100.0] * 40, lows=[100.0] * 40)
        with self.with_history(df):
            out = self.service.get_vwap("INTC")
        self.assertEqual(out["vwap"], 100.0)
        self.assertEqual(out["distance_pct"], 0.0)
        self.assertEqual(out["position"], "above_vwap")  # close >= vwap

    def test_high_volume_reclaim_held_two_bars_is_strong(self):
        n = 40
        closes = [90.0] * (n - 3) + [95.0, 95.5, 96.0]
        volumes = [1_000_000.0] * (n - 3) + [2_500_000.0, 1_200_000.0, 1_100_000.0]
        df = bars(closes, volumes=volumes,
                  opens=closes, highs=[c + 0.2 for c in closes], lows=[c - 0.2 for c in closes])
        with self.with_history(df):
            out = self.service.get_vwap("INTC")
        self.assertEqual(out["position"], "above_vwap")
        self.assertTrue(out["reclaim_signal"])
        self.assertEqual(out["reclaim_strength"], "strong")
        self.assertGreaterEqual(out["consecutive_bars_above"], 2)
        events = [e["type"] for e in out["crossover_events"]]
        self.assertIn("reclaim", events)

    def test_below_vwap_names_the_bounce_trigger(self):
        n = 40
        closes = [100.0] * (n - 5) + [92.0] * 5
        df = bars(closes, opens=closes,
                  highs=[c + 0.2 for c in closes], lows=[c - 0.2 for c in closes])
        with self.with_history(df):
            out = self.service.get_vwap("INTC")
        self.assertEqual(out["position"], "below_vwap")
        self.assertIn("below VWAP", out["interpretation"])


class TestGetVwapHistory(PricesServiceTestBase):
    def test_history_shape_and_positions(self):
        with self.with_history(flat(80, 100.0)):
            out = self.service.get_vwap_history("intc", since_days=30)
        self.assertEqual(out["symbol"], "INTC")
        self.assertEqual(out["data_points"], 30)
        self.assertTrue(all(p["position"] == "above_vwap" for p in out["history"]))

    def test_empty_cache_degrades_cleanly(self):
        with self.with_history(pd.DataFrame()):
            out = self.service.get_vwap_history("INTC")
        self.assertEqual(out["history"], [])
        self.assertIn("error", out)


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

class TestGetCandlestickPatterns(PricesServiceTestBase):
    def test_flat_tape_finds_no_patterns(self):
        # Bars with real bodies and no meaningful wicks: no doji/hammer family.
        n = 60
        closes = [100.0 + (i % 2) for i in range(n)]
        opens = [c - 0.8 for c in closes]
        df = bars(closes, opens=opens,
                  highs=[max(o, c) + 0.05 for o, c in zip(opens, closes)],
                  lows=[min(o, c) - 0.05 for o, c in zip(opens, closes)])
        with self.with_history(df):
            out = self.service.get_candlestick_patterns("INTC")
        self.assertEqual(out["pattern_count"], 0)
        self.assertIn("no reversal pattern", out["bounce_signal"])

    def test_hammer_after_two_down_days_is_bullish(self):
        n = 60
        closes = [100.0] * (n - 3) + [97.0, 94.0, 102.0]
        opens = [99.9] * (n - 3) + [100.0, 97.0, 100.5]
        highs = [100.2] * (n - 3) + [100.3, 97.2, 102.5]
        lows = [99.8] * (n - 3) + [96.8, 93.8, 92.5]  # last bar: long lower wick
        df = bars(closes, opens=opens, highs=highs, lows=lows)
        with self.with_history(df):
            out = self.service.get_candlestick_patterns("INTC")
        patterns = {p["pattern"]: p for p in out["patterns_found"]}
        self.assertIn("hammer", patterns)
        self.assertEqual(patterns["hammer"]["bias"], "bullish")
        self.assertIn("bullish", out["bounce_signal"])


class TestGetHigherLows(PricesServiceTestBase):
    @staticmethod
    def v_shapes(lows):
        """Price path carving a V down to each given swing low."""
        closes = []
        for low_val in lows:
            closes += [low_val + 4, low_val + 2, low_val, low_val + 2, low_val + 4]
        closes += [lows[-1] + 5] * 6
        return closes

    def test_three_rising_swing_lows_form_the_pattern(self):
        closes = self.v_shapes([90.0, 91.5, 93.0, 94.5])
        df = bars(closes, opens=closes,
                  highs=[c + 0.1 for c in closes], lows=[c - 0.1 for c in closes])
        with self.with_history(df):
            out = self.service.get_higher_lows("INTC", interval="1d")
        self.assertTrue(out["higher_low_pattern"])
        self.assertGreaterEqual(out["consecutive_higher_lows"], 2)
        self.assertIn(out["pattern_strength"], {"moderate", "strong"})

    def test_monotonic_tape_reports_insufficient_swings(self):
        with self.with_history(bars(np.linspace(90, 120, 40))):
            out = self.service.get_higher_lows("INTC", interval="1d")
        self.assertFalse(out["higher_low_pattern"])
        self.assertEqual(out["pattern_strength"], "none")

    def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            self.service.get_higher_lows("INTC", interval="1wk")


class TestGetGapAnalysis(PricesServiceTestBase):
    def test_gap_up_later_spanned_is_filled(self):
        n = 70
        closes = [100.0] * (n - 3) + [105.5, 99.0, 99.5]
        opens = [100.0] * (n - 3) + [105.0, 105.2, 99.2]
        highs = [100.1] * (n - 3) + [106.0, 105.4, 99.8]
        lows = [99.9] * (n - 3) + [104.8, 98.5, 98.9]  # bar n-2 spans 100..105
        df = bars(closes, opens=opens, highs=highs, lows=lows)
        with self.with_history(df):
            out = self.service.get_gap_analysis("INTC", min_gap_pct=0.5)
        self.assertEqual(out["total_gaps_found"], 1)
        self.assertEqual(out["filled_count"], 1)
        gap = out["all_gaps"][0]
        self.assertEqual(gap["direction"], "gap_up")
        self.assertEqual(gap["fill_status"], "filled")

    def test_unfilled_gap_down_above_price_is_the_bounce_target(self):
        n = 70
        closes = [100.0] * (n - 2) + [92.0, 91.5]
        opens = [100.0] * (n - 2) + [92.5, 91.9]
        highs = [100.1] * (n - 2) + [93.0, 92.2]
        lows = [99.9] * (n - 2) + [91.8, 91.2]
        df = bars(closes, opens=opens, highs=highs, lows=lows)
        with self.with_history(df):
            out = self.service.get_gap_analysis("INTC", min_gap_pct=0.5)
        self.assertEqual(out["unfilled_count"], 1)
        self.assertIsNotNone(out["nearest_gap_above"])
        self.assertEqual(out["nearest_gap_above"]["direction"], "gap_down")
        levels = [t["level"] for t in out["bounce_targets"]]
        self.assertIn("nearest_unfilled_gap_above", levels)


# ---------------------------------------------------------------------------
# Drawdown / stops
# ---------------------------------------------------------------------------

class TestGetHistoricalDrawdown(PricesServiceTestBase):
    def test_engineered_worst_days_measured_exactly(self):
        closes = [100.0] * 100
        closes[50] = 92.0            # -8% close-to-close
        closes[51:56] = [90, 88, 86, 85, 85]
        df = bars(closes, opens=closes,
                  highs=[c + 0.1 for c in closes], lows=[c - 0.1 for c in closes])
        with self.with_history(df):
            out = self.service.get_historical_drawdown("INTC", lookback_days=100)
        self.assertEqual(out["max_1day_drawdown_pct"], -8.0)
        self.assertLessEqual(out["max_5day_drawdown_pct"], -15.0)
        self.assertGreater(out["trailing_stop_pct"], 8.0)
        self.assertIn("trailing stop", out["stop_width_note"])

    def test_insufficient_data_rejected(self):
        with self.with_history(flat(5)):
            with self.assertRaises(ValueError):
                self.service.get_historical_drawdown("INTC")


# ---------------------------------------------------------------------------
# REST surfaces
# ---------------------------------------------------------------------------

class TestRestSurfaces(PricesServiceTestBase):
    def test_ohlcv_bars_shape(self):
        with self.with_history(flat(20, 123.4567)):
            out = self.service.get_ohlcv_bars("intc", days=10)
        self.assertEqual(out["ticker"], "INTC")
        self.assertEqual(len(out["bars"]), 10)
        self.assertEqual(out["bars"][-1]["close"], 123.4567)
        self.assertEqual(set(out["bars"][0]), {"date", "open", "high", "low", "close", "volume"})

    def test_ohlcv_bars_empty(self):
        with self.with_history(pd.DataFrame()):
            self.assertEqual(self.service.get_ohlcv_bars("INTC")["bars"], [])

    def test_technicals_table_carries_indicator_columns(self):
        # Ramp + oscillation: rsi_series needs both gains AND losses present
        # (a pure ramp has zero average loss -> NaN RSI by construction).
        ramp = np.linspace(80, 120, 260) + 2 * np.sin(np.arange(260) / 3)
        with self.with_history(bars(ramp)):
            out = self.service.get_technicals_table("INTC", days=250)
        self.assertEqual(len(out["indicators"]), 250)
        last = out["indicators"][-1]
        for key in ("ma10", "ma50", "ma200", "bb_upper", "rsi", "macd", "macd_hist"):
            self.assertIsNotNone(last[key], key)

    def test_risk_signals_compose_drawdown_and_vwap(self):
        with patch.object(self.service, "get_historical_drawdown",
                          return_value={"trailing_stop_pct": 9.9}), \
             patch.object(self.service, "get_vwap",
                          return_value={"vwap": 101.0, "position": "above_vwap"}):
            out = self.service.get_risk_signals("intc")
        self.assertEqual(out["drawdown"]["trailing_stop_pct"], 9.9)
        self.assertEqual(out["vwap_position"], "above_vwap")

    def test_risk_signals_degrade_when_drawdown_fails(self):
        with patch.object(self.service, "get_historical_drawdown",
                          side_effect=ValueError("thin history")):
            out = self.service.get_risk_signals("INTC")
        self.assertIsNone(out["drawdown"])
        self.assertIn("thin history", out["error"])

    def test_technical_signals_aggregate_and_error_isolation(self):
        ok = {"fine": True}
        with patch.object(self.service, "get_stochastic", return_value=ok), \
             patch.object(self.service, "get_vwap", return_value=ok), \
             patch.object(self.service, "get_obv", return_value=ok), \
             patch.object(self.service, "get_volume_analysis", return_value=ok), \
             patch.object(self.service, "get_candlestick_patterns", return_value=ok), \
             patch.object(self.service, "get_higher_lows", return_value=ok), \
             patch.object(self.service, "get_gap_analysis",
                          side_effect=RuntimeError("gap boom")):
            out = self.service.get_technical_signals("intc")
        self.assertEqual(out["ticker"], "INTC")
        self.assertEqual(out["stochastic"], ok)
        self.assertIsNone(out["gap_analysis"])
        self.assertIn("gap boom", out["_errors"]["gap_analysis"])


# ---------------------------------------------------------------------------
# Screener
# ---------------------------------------------------------------------------

def screener_rows(symbol, closes):
    return [
        {"symbol": symbol, "close": float(c), "volume": 1_000_000}
        for c in closes
    ]


class TestScreenSecurities(PricesServiceTestBase):
    PORTFOLIO = [{"symbol": "UPP", "source": "portfolio", "tags": []}]
    WATCHLIST = [
        {"symbol": "DWN", "source": "watchlist", "tags": ["dip"]},
        {"symbol": "UPP", "source": "watchlist", "tags": ["both-tag"]},
    ]

    def arm(self):
        self.sentiment.get_all_latest.return_value = {
            "DWN": {"overall_sentiment": "negative"},
        }
        self.ohlcv.daily_bars_for_symbols.return_value = (
            screener_rows("UPP", np.linspace(80, 120, 60))
            + screener_rows("DWN", np.linspace(120, 80, 60))
        )

    def test_rsi_filter_selects_the_oversold_name(self):
        self.arm()
        out = self.service.screen_securities(
            {"rsi_max": 40}, self.PORTFOLIO, [dict(s) for s in self.WATCHLIST]
        )
        self.assertEqual(out["count"], 1)
        row = out["results"][0]
        self.assertEqual(row["symbol"], "DWN")
        self.assertLess(row["rsi"], 40)
        self.assertEqual(row["news_sentiment"], "negative")

    def test_above_ma50_selects_the_uptrend_and_marks_dual_source(self):
        self.arm()
        out = self.service.screen_securities(
            {"above_ma50": True}, self.PORTFOLIO, [dict(s) for s in self.WATCHLIST]
        )
        self.assertEqual(out["count"], 1)
        row = out["results"][0]
        self.assertEqual(row["symbol"], "UPP")
        self.assertEqual(row["source"], "both")
        self.assertGreater(row["last_close"], row["ma50"])

    def test_source_filter_watchlist_only(self):
        self.arm()
        out = self.service.screen_securities(
            {"source": "watchlist"}, self.PORTFOLIO, [dict(s) for s in self.WATCHLIST]
        )
        self.assertEqual({r["symbol"] for r in out["results"]}, {"UPP", "DWN"})

    def test_sentiment_filter(self):
        self.arm()
        out = self.service.screen_securities(
            {"news_sentiment": "negative"}, self.PORTFOLIO, [dict(s) for s in self.WATCHLIST]
        )
        self.assertEqual([r["symbol"] for r in out["results"]], ["DWN"])

    def test_thin_history_symbols_are_skipped(self):
        self.sentiment.get_all_latest.return_value = {}
        self.ohlcv.daily_bars_for_symbols.return_value = screener_rows(
            "UPP", np.linspace(80, 120, 10)  # < 30 bars
        )
        out = self.service.screen_securities({}, self.PORTFOLIO, [])
        self.assertEqual(out["count"], 0)

    def test_no_symbols_short_circuits(self):
        out = self.service.screen_securities({"source": "portfolio"}, [], [])
        self.assertEqual(out, {"results": [], "count": 0})


if __name__ == "__main__":
    unittest.main()
