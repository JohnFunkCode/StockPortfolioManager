"""PricesService — price quotes, technical indicators, and pattern detection.

Absorbs the 12 price/technical tools from fastMCPTest/stock_price_server.py
and the REST /ohlcv, /technicals, /signals/technical, and /securities/screen
routes from api/app.py (architectural standard v2, Phase 1 Step 4).

Bodies are verbatim from the original adapters — behavioral parity is the
contract. OHLCV access goes through OhlcvRepository, live quotes/chains
through YFinanceGateway, snapshot persistence through OptionsStore, and the
screener's sentiment overlay through SentimentStore.
"""

from __future__ import annotations

import datetime
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from quantcore.analytics.indicators import atr_series, macd_series, rsi_series, safe_float
from quantcore.analytics.market_time import latest_completed_session, period_to_days
from quantcore.gateways.yfinance_gateway import YFinanceGateway
from quantcore.repositories.ohlcv_repository import OhlcvRepository
from quantcore.repositories.options_repository import OptionsStore
from quantcore.repositories.sentiment_repository import SentimentStore

VALID_INTERVALS = {"1d", "1wk", "1mo", "1h", "30m", "15m"}

# How many days of history to pre-populate on a cold start, per interval.
WARM_DAYS: dict[str, int] = {
    "1d":  730,
    "1wk": 1825,
    "1mo": 3650,
    "1h":  59,
    "30m": 59,
    "15m": 59,
}


def _safe_int(val):
    try:
        f = float(val) if val is not None else 0.0
        return 0 if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return 0


def _summarize_options(chain_df, price, kind):
    """Return ATM-nearest strikes and aggregate stats for calls or puts."""
    df = chain_df.copy()
    df = df[df["strike"] > 0].copy()
    df["moneyness"] = abs(df["strike"] - price)

    # 5 strikes nearest to ATM
    atm = df.nsmallest(5, "moneyness")

    contracts = []
    for _, row in atm.iterrows():
        contracts.append({
            "strike": round(float(row["strike"]), 2),
            "last": round(float(row.get("lastPrice", 0)), 2),
            "bid": round(float(row.get("bid", 0)), 2),
            "ask": round(float(row.get("ask", 0)), 2),
            "iv": round(float(row.get("impliedVolatility", 0)) * 100, 1),
            "volume": _safe_int(row.get("volume")),
            "open_interest": _safe_int(row.get("openInterest")),
            "in_the_money": bool(row.get("inTheMoney", False)),
        })

    total_oi = int(df["openInterest"].fillna(0).sum())
    total_vol = int(df["volume"].fillna(0).sum())
    avg_iv = round(float(df["impliedVolatility"].fillna(0).mean()) * 100, 1)

    return {
        "atm_contracts": sorted(contracts, key=lambda x: x["strike"]),
        "total_open_interest": total_oi,
        "total_volume": total_vol,
        "avg_iv_pct": avg_iv,
    }


class PricesService:
    """Price quotes, technical indicators, pattern detection, and screening."""

    def __init__(
        self,
        ohlcv_repository: OhlcvRepository,
        yfinance_gateway: YFinanceGateway,
        options_repository: OptionsStore,
        sentiment_repository: SentimentStore,
    ):
        self._ohlcv = ohlcv_repository
        self._yf = yfinance_gateway
        self._options = options_repository
        self._sentiment = sentiment_repository

    # ------------------------------------------------------------------
    # OHLCV history — fetch-when-stale policy (issue #74; moved here from
    # the repository per Rule 5: caching policy is a service concern).
    # Other services reach history via a constructor-injected PricesService,
    # keeping YFinanceGateway the single fetch seam.
    # ------------------------------------------------------------------

    def get_history(self, symbol: str, interval: str = "1d", days: int = 365) -> pd.DataFrame:
        """Cached OHLCV history; fetches via the gateway only when: no cache
        (cold start), an OPEN bar needs refreshing, or the latest CLOSED bar
        predates the most recent started session."""
        if interval not in VALID_INTERVALS:
            raise ValueError(f"Invalid interval '{interval}'. Valid: {VALID_INTERVALS}")
        symbol = symbol.upper()

        needs_fetch = False
        if self._ohlcv.count_cached(symbol, interval) == 0:
            days = max(days, WARM_DAYS.get(interval, 730))
            needs_fetch = True
        elif self._ohlcv.has_open_bar(symbol, interval):
            needs_fetch = True
        else:
            latest_ts = self._ohlcv.latest_closed_ts(symbol, interval)
            if latest_ts is not None:
                last_date = datetime.datetime.utcfromtimestamp(latest_ts).date()
                if last_date < latest_completed_session():
                    needs_fetch = True

        if needs_fetch:
            fresh = self._yf.fetch_history(symbol, interval, days)
            if not fresh.empty:
                self._ohlcv.store_bars(symbol, interval, fresh)

        return self._ohlcv.get_bars(symbol, interval, days)

    def get_fast_price(self, symbol: str):
        """Lightweight last-trade price via fast_info; None when unavailable.

        Used by the notifier's alert loop (issue #76) — adapters call this
        instead of importing yfinance.
        """
        try:
            info = self._yf.fast_info(symbol.upper())
            price = getattr(info, "last_price", None)
            if price is None or float(price) <= 0:
                return None
            return float(price)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Quote + options summary
    # ------------------------------------------------------------------

    def get_stock_price(self, symbol: str) -> dict:
        info = self._yf.fast_info(symbol.upper())

        price = info.last_price
        if price is None:
            raise ValueError(f"Could not retrieve price for symbol: {symbol}")

        # Bollinger Bands
        hist = self.get_history(symbol.upper(), "1d", 90)
        close = hist["Close"].dropna()
        if len(close) >= 20:
            sma20 = float(close.rolling(window=20).mean().iloc[-1])
            std20 = float(close.rolling(window=20).std().iloc[-1])
            bollinger_bands: dict | None = {
                "upper":    round(sma20 + 2 * std20, 2),
                "middle":   round(sma20, 2),
                "lower":    round(sma20 - 2 * std20, 2),
                "period":   20,
                "std_dev":  2,
            }
        else:
            bollinger_bands = None

        # Options chain (nearest expiration)
        options_data = None
        expirations = self._yf.expirations(symbol.upper())
        if expirations:
            nearest_exp = expirations[0]
            chain = self._yf.option_chain(symbol.upper(), nearest_exp)
            calls_summary = _summarize_options(chain.calls, price, "call")
            puts_summary = _summarize_options(chain.puts, price, "put")

            total_call_oi = calls_summary["total_open_interest"]
            total_put_oi = puts_summary["total_open_interest"]
            put_call_ratio = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else None

            options_data = {
                "expiration": nearest_exp,
                "put_call_ratio": put_call_ratio,
                "calls": calls_summary,
                "puts": puts_summary,
            }

        result = {
            "symbol": symbol.upper(),
            "price": round(price, 2),
            "currency": getattr(info, "currency", "USD"),
            "bollinger_bands": bollinger_bands,
            "options": options_data,
        }

        self._options.save_snapshot(
            symbol=symbol.upper(),
            price=price,
            bollinger_bands=bollinger_bands,
            options=options_data,
        )

        return result

    # ------------------------------------------------------------------
    # Momentum indicators
    # ------------------------------------------------------------------

    def get_rsi(self, symbol: str, period: int = 14, interval: str = "1d") -> dict:
        valid_intervals = {"1d", "1wk", "1mo"}
        if interval not in valid_intervals:
            raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

        fetch_period = {"1d": "90d", "1wk": "2y", "1mo": "5y"}[interval]

        hist = self.get_history(symbol.upper(), interval, period_to_days(fetch_period))
        closes = hist["Close"].dropna()

        if len(closes) < period + 1:
            raise ValueError(f"Not enough data for {symbol} (got {len(closes)} periods, need {period + 1})")

        delta = closes.diff().dropna()
        gains = delta.clip(lower=0)
        losses = -delta.clip(upper=0)

        avg_gain = gains.ewm(com=period - 1, min_periods=period).mean().iloc[-1]
        avg_loss = losses.ewm(com=period - 1, min_periods=period).mean().iloc[-1]

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        if rsi >= 70:
            signal = "overbought"
        elif rsi <= 30:
            signal = "oversold"
        else:
            signal = "neutral"

        return {
            "symbol": symbol.upper(),
            "interval": interval,
            "period": period,
            "rsi": round(float(rsi), 2),
            "signal": signal,
            "last_close": round(float(closes.iloc[-1]), 4),
        }

    def get_macd(self, symbol: str, interval: str = "1d") -> dict:
        valid_intervals = {"1d", "1wk", "1mo"}
        if interval not in valid_intervals:
            raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

        fetch_period = {"1d": "6mo", "1wk": "3y", "1mo": "10y"}[interval]

        hist = self.get_history(symbol.upper(), interval, period_to_days(fetch_period))
        closes = hist["Close"].dropna()

        if len(closes) < 35:
            raise ValueError(f"Not enough data for {symbol} (got {len(closes)} periods, need 35)")

        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line

        macd_val = float(macd_line.iloc[-1])
        signal_val = float(signal_line.iloc[-1])
        hist_val = float(histogram.iloc[-1])
        prev_hist_val = float(histogram.iloc[-2])

        if macd_val > signal_val and prev_hist_val < 0 <= hist_val:
            crossover = "bullish_crossover"
        elif macd_val < signal_val and prev_hist_val > 0 >= hist_val:
            crossover = "bearish_crossover"
        elif macd_val > signal_val:
            crossover = "bullish"
        else:
            crossover = "bearish"

        return {
            "symbol": symbol.upper(),
            "interval": interval,
            "macd": round(macd_val, 4),
            "signal": round(signal_val, 4),
            "histogram": round(hist_val, 4),
            "crossover": crossover,
            "last_close": round(float(closes.iloc[-1]), 4),
        }

    def get_stochastic(self, symbol: str, k_period: int = 14, d_period: int = 3, interval: str = "1d") -> dict:
        valid_intervals = {"1d", "1wk", "1mo"}
        if interval not in valid_intervals:
            raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

        fetch_period = {"1d": "90d", "1wk": "2y", "1mo": "5y"}[interval]

        hist = self.get_history(symbol.upper(), interval, period_to_days(fetch_period))

        if len(hist) < k_period + d_period:
            raise ValueError(
                f"Not enough data for {symbol} "
                f"(got {len(hist)} periods, need {k_period + d_period})"
            )

        low_min  = hist["Low"].rolling(window=k_period).min()
        high_max = hist["High"].rolling(window=k_period).max()
        range_   = high_max - low_min

        k = ((hist["Close"] - low_min) / range_.replace(0, float("nan"))) * 100
        d = k.rolling(window=d_period).mean()

        k_val      = float(k.iloc[-1])
        d_val      = float(d.iloc[-1])
        k_prev     = float(k.iloc[-2])
        d_prev     = float(d.iloc[-2])

        # Momentum signal
        if k_val >= 80:
            signal = "overbought"
        elif k_val <= 20:
            signal = "oversold"
        else:
            signal = "neutral"

        # Crossover — only meaningful near extremes
        if k_prev <= d_prev and k_val > d_val:
            crossover = "bullish_crossover"
        elif k_prev >= d_prev and k_val < d_val:
            crossover = "bearish_crossover"
        elif k_val > d_val:
            crossover = "bullish"
        else:
            crossover = "bearish"

        return {
            "symbol":    symbol.upper(),
            "interval":  interval,
            "k_period":  k_period,
            "d_period":  d_period,
            "k":         round(k_val, 2),
            "d":         round(d_val, 2),
            "signal":    signal,
            "crossover": crossover,
            "last_close": round(float(hist["Close"].iloc[-1]), 4),
        }

    # ------------------------------------------------------------------
    # Volume studies
    # ------------------------------------------------------------------

    def get_volume_analysis(self, symbol: str, lookback: int = 20, interval: str = "1d") -> dict:
        valid_intervals = {"1d", "1wk", "1mo"}
        if interval not in valid_intervals:
            raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

        fetch_period = {"1d": "6mo", "1wk": "3y", "1mo": "10y"}[interval]

        hist = self.get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

        if len(hist) < lookback + 5:
            raise ValueError(f"Not enough data for {symbol} (got {len(hist)} bars, need {lookback + 5})")

        close  = hist["Close"]
        high   = hist["High"]
        low    = hist["Low"]
        volume = hist["Volume"].astype(float)

        # Rolling volume average and ratio
        vol_avg   = volume.rolling(window=lookback).mean()
        vol_ratio = volume / vol_avg

        # Price range as % of close (normalised bar size)
        bar_range_pct = (high - low) / close * 100

        # OBV
        direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        obv = (volume * direction).cumsum()

        # --- Climax detection (last `lookback` bars) ---
        CLIMAX_VOL_MULT   = 2.0   # volume ≥ 2× average
        CLIMAX_RANGE_MULT = 1.5   # bar range ≥ 1.5× average bar range

        avg_range = bar_range_pct.rolling(window=lookback).mean()

        climax_events = []
        for i in range(max(1, len(hist) - lookback), len(hist)):
            vr  = float(vol_ratio.iloc[i])
            br  = float(bar_range_pct.iloc[i])
            abr = float(avg_range.iloc[i])
            if math.isnan(vr) or math.isnan(br) or math.isnan(abr):
                continue
            if vr >= CLIMAX_VOL_MULT and br >= CLIMAX_RANGE_MULT * abr:
                day_close  = float(close.iloc[i])
                day_open   = float(hist["Open"].iloc[i])
                direction_ = "down" if day_close < day_open else "up"
                quiet_next = (
                    i + 1 < len(hist)
                    and float(vol_ratio.iloc[i + 1]) <= 0.6
                )
                climax_events.append({
                    "date":              hist.index[i].strftime("%Y-%m-%d"),
                    "direction":         direction_,
                    "volume_ratio":      round(vr, 2),
                    "bar_range_pct":     round(br, 2),
                    "close":             round(day_close, 2),
                    "quiet_follow_through": quiet_next,
                    "interpretation": (
                        "bearish_capitulation — potential bounce bottom"
                        if direction_ == "down" else
                        "bullish_exhaustion — potential near-term top"
                    ),
                })

        # --- Most recent bar stats ---
        last_vol_ratio  = round(float(vol_ratio.iloc[-1]), 2)
        last_close      = round(float(close.iloc[-1]), 2)
        avg_vol_20      = round(float(vol_avg.iloc[-1]), 0)
        last_volume     = int(volume.iloc[-1])

        # --- OBV divergence: price lower low but OBV higher low in lookback window ---
        window_close = close.iloc[-lookback:]
        window_obv   = obv.iloc[-lookback:]
        price_made_new_low = float(window_close.iloc[-1]) == float(window_close.min())
        obv_did_not_confirm = float(window_obv.iloc[-1]) > float(window_obv.min())
        obv_divergence = price_made_new_low and obv_did_not_confirm

        # --- Overall bottom signal ---
        has_recent_cap = any(e["direction"] == "down" for e in climax_events[-3:]) if climax_events else False
        has_quiet_follow = any(e["quiet_follow_through"] for e in climax_events if e["direction"] == "down")

        if has_recent_cap and has_quiet_follow and obv_divergence:
            bottom_signal = "strong — capitulation + quiet follow-through + OBV divergence"
        elif has_recent_cap and has_quiet_follow:
            bottom_signal = "moderate — capitulation + quiet follow-through"
        elif has_recent_cap and obv_divergence:
            bottom_signal = "moderate — capitulation + OBV divergence"
        elif has_recent_cap:
            bottom_signal = "weak — capitulation bar only, no confirmation"
        elif obv_divergence:
            bottom_signal = "weak — OBV divergence only"
        else:
            bottom_signal = "none — no capitulation or divergence detected"

        return {
            "symbol":           symbol.upper(),
            "interval":         interval,
            "lookback":         lookback,
            "last_close":       last_close,
            "last_volume":      last_volume,
            "avg_volume_20":    int(avg_vol_20),
            "last_volume_ratio": last_vol_ratio,
            "obv_divergence":   obv_divergence,
            "climax_events":    climax_events,
            "bottom_signal":    bottom_signal,
        }

    def get_obv(self, symbol: str, lookback: int = 20, interval: str = "1d") -> dict:
        valid_intervals = {"1d", "1wk", "1mo"}
        if interval not in valid_intervals:
            raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

        fetch_period = {"1d": "6mo", "1wk": "3y", "1mo": "10y"}[interval]

        hist = self.get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

        if len(hist) < lookback + 5:
            raise ValueError(f"Not enough data for {symbol} (got {len(hist)} bars, need {lookback + 5})")

        close  = hist["Close"]
        volume = hist["Volume"].astype(float)

        # --- Build OBV series ---
        direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        obv = (volume * direction).cumsum()

        # --- Trend over lookback window (linear slope normalised to range) ---
        window_obv   = obv.iloc[-lookback:].values.astype(float)
        window_price = close.iloc[-lookback:].values.astype(float)

        def _slope(arr):
            """Normalised slope: (last - first) / range, clipped to [-1, 1]."""
            rng = float(max(arr) - min(arr))
            if rng == 0:
                return 0.0
            return float(arr[-1] - arr[0]) / rng

        def _trend_label(slope, threshold=0.1):
            if slope > threshold:
                return "rising"
            if slope < -threshold:
                return "falling"
            return "flat"

        obv_slope   = _slope(window_obv)
        price_slope = _slope(window_price)
        obv_trend   = _trend_label(obv_slope)
        price_trend = _trend_label(price_slope)

        # --- Divergence ---
        if obv_trend == "rising" and price_trend == "falling":
            divergence = "bullish"
        elif obv_trend == "falling" and price_trend == "rising":
            divergence = "bearish"
        else:
            divergence = "none"

        # Strength: how far apart are the two normalised slopes?
        slope_diff = abs(obv_slope - price_slope)
        if divergence == "none":
            divergence_strength = "none"
        elif slope_diff > 0.6:
            divergence_strength = "strong"
        elif slope_diff > 0.3:
            divergence_strength = "moderate"
        else:
            divergence_strength = "weak"

        # --- Recent OBV history (last 10 bars) for context ---
        recent_bars = []
        for i in range(max(0, len(hist) - 10), len(hist)):
            bar_close = float(close.iloc[i])
            bar_obv   = float(obv.iloc[i])
            bar_vol   = int(volume.iloc[i])
            bar_dir   = int(direction.iloc[i])
            recent_bars.append({
                "date":      hist.index[i].strftime("%Y-%m-%d"),
                "close":     round(bar_close, 2),
                "volume":    bar_vol,
                "obv":       round(bar_obv, 0),
                "direction": "up" if bar_dir == 1 else ("down" if bar_dir == -1 else "flat"),
            })

        # --- Human-readable interpretation ---
        if divergence == "bullish" and divergence_strength in ("strong", "moderate"):
            interpretation = (
                "Bullish OBV divergence — institutions accumulating while price falls. "
                "Strong bounce-bottom signal."
            )
        elif divergence == "bullish":
            interpretation = (
                "Mild bullish OBV divergence — early accumulation signs. "
                "Monitor for confirmation."
            )
        elif divergence == "bearish" and divergence_strength in ("strong", "moderate"):
            interpretation = (
                "Bearish OBV divergence — distribution while price rises. "
                "Potential topping signal."
            )
        elif divergence == "bearish":
            interpretation = "Mild bearish OBV divergence — watch for distribution to intensify."
        elif obv_trend == "rising" and price_trend == "rising":
            interpretation = "OBV confirming price uptrend — healthy bullish momentum."
        elif obv_trend == "falling" and price_trend == "falling":
            interpretation = "OBV confirming price downtrend — no accumulation yet."
        else:
            interpretation = "No clear divergence. OBV and price trends are aligned or flat."

        return {
            "symbol":               symbol.upper(),
            "interval":             interval,
            "lookback":             lookback,
            "last_close":           round(float(close.iloc[-1]), 2),
            "last_obv":             round(float(obv.iloc[-1]), 0),
            "obv_trend":            obv_trend,
            "price_trend":          price_trend,
            "divergence":           divergence,
            "divergence_strength":  divergence_strength,
            "interpretation":       interpretation,
            "recent_bars":          recent_bars,
        }

    # ------------------------------------------------------------------
    # VWAP
    # ------------------------------------------------------------------

    def get_vwap(self, symbol: str, lookback: int = 20, interval: str = "1d") -> dict:
        valid_intervals = {"1d", "1wk", "1mo"}
        if interval not in valid_intervals:
            raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

        fetch_period = {"1d": "6mo", "1wk": "3y", "1mo": "10y"}[interval]

        hist = self.get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

        if len(hist) < lookback + 5:
            raise ValueError(f"Not enough data for {symbol} (got {len(hist)} bars, need {lookback + 5})")

        close   = hist["Close"]
        high    = hist["High"]
        low     = hist["Low"]
        volume  = hist["Volume"].astype(float)

        # Typical price and rolling VWAP
        typical = (high + low + close) / 3
        cum_tp_vol = (typical * volume).rolling(window=lookback).sum()
        cum_vol    = volume.rolling(window=lookback).sum()
        vwap       = cum_tp_vol / cum_vol

        # Rolling average volume (for reclaim strength)
        avg_vol = volume.rolling(window=lookback).mean()

        # Boolean: is price above VWAP each bar?
        above = close > vwap

        # --- Current position ---
        last_close = float(close.iloc[-1])
        last_vwap  = float(vwap.iloc[-1])
        dist_pct   = round((last_close - last_vwap) / last_vwap * 100, 2)
        position   = "above_vwap" if last_close >= last_vwap else "below_vwap"

        # --- Consecutive bars in current position ---
        streak = 0
        for i in range(len(hist) - 1, -1, -1):
            if math.isnan(float(vwap.iloc[i])):
                break
            if bool(above.iloc[i]) == (position == "above_vwap"):
                streak += 1
            else:
                break

        # --- Crossover events in the lookback window ---
        crossover_events = []
        start = max(1, len(hist) - lookback)
        for i in range(start, len(hist)):
            if math.isnan(float(vwap.iloc[i])) or math.isnan(float(vwap.iloc[i - 1])):
                continue
            was_above = bool(above.iloc[i - 1])
            is_above  = bool(above.iloc[i])
            if was_above == is_above:
                continue
            bar_vol     = float(volume.iloc[i])
            bar_avg_vol = float(avg_vol.iloc[i])
            vol_ratio   = round(bar_vol / bar_avg_vol, 2) if bar_avg_vol > 0 else None
            crossover_events.append({
                "date":        hist.index[i].strftime("%Y-%m-%d"),
                "type":        "reclaim" if is_above else "breakdown",
                "close":       round(float(close.iloc[i]), 2),
                "vwap":        round(float(vwap.iloc[i]), 2),
                "volume_ratio": vol_ratio,
                "high_volume": vol_ratio is not None and vol_ratio >= 1.0,
            })

        # --- Reclaim signal and strength ---
        # Look at last 3 bars for a reclaim
        recent_reclaims = [e for e in crossover_events[-3:] if e["type"] == "reclaim"]
        reclaim_signal  = len(recent_reclaims) > 0

        if not reclaim_signal:
            reclaim_strength = "none"
        else:
            last_reclaim = recent_reclaims[-1]
            high_vol     = last_reclaim["high_volume"]
            # How many consecutive bars above VWAP since the reclaim?
            bars_held    = streak if position == "above_vwap" else 0
            if bars_held >= 2 and high_vol:
                reclaim_strength = "strong"
            elif high_vol or bars_held >= 2:
                reclaim_strength = "moderate"
            else:
                reclaim_strength = "weak"

        # --- Interpretation ---
        if reclaim_signal and reclaim_strength == "strong":
            interpretation = (
                "Strong VWAP reclaim — price crossed above VWAP on above-average volume "
                "and has held for ≥2 bars. High-confidence bounce signal."
            )
        elif reclaim_signal and reclaim_strength == "moderate":
            interpretation = (
                "Moderate VWAP reclaim — price crossed above VWAP. "
                "One confirming condition met (volume or follow-through). Watch for continuation."
            )
        elif reclaim_signal:
            interpretation = (
                "Weak VWAP reclaim — price just crossed above VWAP on light volume. "
                "Unconfirmed — needs follow-through bar to validate."
            )
        elif position == "below_vwap":
            interpretation = (
                f"Price is {abs(dist_pct):.1f}% below VWAP. "
                "No reclaim yet — monitor for a cross back above as a bounce entry trigger."
            )
        elif dist_pct > 3:
            interpretation = (
                f"Price is {dist_pct:.1f}% above VWAP — extended. "
                "VWAP reversion risk; not an ideal long entry."
            )
        else:
            interpretation = (
                f"Price is {dist_pct:.1f}% above VWAP — healthy positioning, "
                "trend intact."
            )

        return {
            "symbol":                   symbol.upper(),
            "interval":                 interval,
            "lookback":                 lookback,
            "last_close":               round(last_close, 2),
            "vwap":                     round(last_vwap, 2),
            "distance_pct":             dist_pct,
            "position":                 position,
            "consecutive_bars_above" if position == "above_vwap" else "consecutive_bars_below": streak,
            "reclaim_signal":           reclaim_signal,
            "reclaim_strength":         reclaim_strength,
            "crossover_events":         crossover_events,
            "interpretation":           interpretation,
        }

    def get_vwap_history(self, symbol: str, since_days: int = 90, lookback: int = 20, interval: str = "1d") -> dict:
        symbol = symbol.upper().strip()
        # Fetch enough extra bars to seed the first rolling window
        fetch_days = since_days + lookback + 5
        df = self.get_history(symbol, interval=interval, days=fetch_days)
        if df is None or df.empty:
            return {"symbol": symbol, "error": "No OHLCV data in cache", "history": []}

        df = df.sort_index()
        df["vwap"] = (df["Close"] * df["Volume"]).rolling(lookback).sum() / df["Volume"].rolling(lookback).sum()
        df = df.dropna(subset=["vwap"]).tail(since_days)

        history = [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "close": round(row["Close"], 2),
                "vwap": round(row["vwap"], 2),
                "distance_pct": round((row["Close"] - row["vwap"]) / row["vwap"] * 100, 2),
                "position": "above_vwap" if row["Close"] >= row["vwap"] else "below_vwap",
            }
            for idx, row in df.iterrows()
        ]
        return {"symbol": symbol, "lookback_bars": lookback, "data_points": len(history), "history": history}

    # ------------------------------------------------------------------
    # Pattern detection
    # ------------------------------------------------------------------

    def get_candlestick_patterns(self, symbol: str, lookback: int = 10, interval: str = "1d") -> dict:
        valid_intervals = {"1d", "1wk", "1mo"}
        if interval not in valid_intervals:
            raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

        fetch_period = {"1d": "6mo", "1wk": "3y", "1mo": "10y"}[interval]

        hist = self.get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

        if len(hist) < lookback + 25:
            raise ValueError(f"Not enough data for {symbol} (got {len(hist)} bars, need {lookback + 25})")

        close  = hist["Close"]
        open_  = hist["Open"]
        high   = hist["High"]
        low    = hist["Low"]
        volume = hist["Volume"].astype(float)

        # Bollinger Bands for context
        sma20   = close.rolling(window=20).mean()
        std20   = close.rolling(window=20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20
        avg_vol  = volume.rolling(window=20).mean()

        def _classify_bar(i: int) -> dict | None:
            """Classify a single bar and return a pattern dict, or None if no pattern."""
            o = float(open_.iloc[i])
            h = float(high.iloc[i])
            l = float(low.iloc[i])
            c = float(close.iloc[i])
            v = float(volume.iloc[i])
            avg_v = float(avg_vol.iloc[i])
            bbl   = float(bb_lower.iloc[i])
            bbu   = float(bb_upper.iloc[i])

            if any(math.isnan(x) for x in [o, h, l, c, bbl, bbu]):
                return None

            total_range  = h - l
            if total_range == 0:
                return None

            body         = abs(c - o)
            body_top     = max(c, o)
            body_bottom  = min(c, o)
            upper_wick   = h - body_top
            lower_wick   = body_bottom - l
            body_ratio   = body / total_range        # 0 = doji, 1 = marubozu
            upper_ratio  = upper_wick / total_range
            lower_ratio  = lower_wick / total_range

            # --- Preceding trend: consecutive closes lower ---
            down_days = 0
            for j in range(i - 1, max(i - 6, 0), -1):
                if float(close.iloc[j]) < float(close.iloc[j - 1]):
                    down_days += 1
                else:
                    break

            up_days = 0
            for j in range(i - 1, max(i - 6, 0), -1):
                if float(close.iloc[j]) > float(close.iloc[j - 1]):
                    up_days += 1
                else:
                    break

            near_lower_bb = c <= bbl * 1.03   # within 3% of lower band
            near_upper_bb = c >= bbu * 0.97
            high_volume   = v >= avg_v * 1.0 and avg_v > 0

            pattern     = None
            bias        = None
            strength_pts = 0
            notes       = []

            # ---- Doji family (body ≤ 10% of range) ----
            if body_ratio <= 0.10:
                if lower_ratio >= 0.40 and upper_ratio <= 0.15:
                    pattern = "dragonfly_doji"
                    bias    = "bullish"
                    strength_pts += 3
                    notes.append("long lower wick with close near high")
                elif upper_ratio >= 0.40 and lower_ratio <= 0.15:
                    pattern = "gravestone_doji"
                    bias    = "bearish"
                    strength_pts += 2
                    notes.append("long upper wick with close near low")
                elif lower_ratio >= 0.30 and upper_ratio >= 0.30:
                    pattern = "long_legged_doji"
                    bias    = "neutral"
                    strength_pts += 1
                    notes.append("long wicks both sides — high indecision")
                else:
                    pattern = "doji"
                    bias    = "neutral"
                    strength_pts += 1
                    notes.append("open ≈ close — indecision")

            # ---- Hammer / Hanging Man (body 10–35% of range) ----
            elif body_ratio <= 0.35 and lower_ratio >= 0.55 and upper_ratio <= 0.10:
                if down_days >= 2:
                    pattern = "hammer"
                    bias    = "bullish"
                    strength_pts += 3
                    notes.append(f"after {down_days} down days — reversal context strong")
                else:
                    pattern = "hanging_man"
                    bias    = "bearish"
                    strength_pts += 2
                    notes.append("hammer shape after uptrend — potential top")

            # ---- Inverted Hammer / Shooting Star ----
            elif body_ratio <= 0.35 and upper_ratio >= 0.55 and lower_ratio <= 0.10:
                if down_days >= 2:
                    pattern = "inverted_hammer"
                    bias    = "bullish"
                    strength_pts += 2
                    notes.append(f"after {down_days} down days — buyers tested higher prices")
                else:
                    pattern = "shooting_star"
                    bias    = "bearish"
                    strength_pts += 3
                    notes.append(f"after {up_days} up days — rejection at highs")

            if pattern is None:
                return None

            # ---- Contextual strength modifiers ----
            if bias == "bullish":
                if near_lower_bb:
                    strength_pts += 2
                    notes.append("near lower Bollinger Band — oversold context")
                if high_volume:
                    strength_pts += 1
                    notes.append(f"above-average volume ({v/avg_v:.1f}× avg)")
                if down_days >= 3:
                    strength_pts += 1
                    notes.append(f"{down_days} consecutive down days — exhaustion likely")
            elif bias == "bearish":
                if near_upper_bb:
                    strength_pts += 2
                    notes.append("near upper Bollinger Band — overbought context")
                if high_volume:
                    strength_pts += 1
                    notes.append(f"above-average volume ({v/avg_v:.1f}× avg)")

            if   strength_pts >= 6: strength = "strong"
            elif strength_pts >= 4: strength = "moderate"
            elif strength_pts >= 2: strength = "weak"
            else:                   strength = "minimal"

            bb_pos = round((c - bbl) / (bbu - bbl), 3) if (bbu - bbl) > 0 else None

            return {
                "date":          hist.index[i].strftime("%Y-%m-%d"),
                "pattern":       pattern,
                "bias":          bias,
                "strength":      strength,
                "strength_score": strength_pts,
                "open":          round(o, 2),
                "high":          round(h, 2),
                "low":           round(l, 2),
                "close":         round(c, 2),
                "body_ratio":    round(body_ratio, 3),
                "upper_wick_ratio": round(upper_ratio, 3),
                "lower_wick_ratio": round(lower_ratio, 3),
                "volume_ratio":  round(v / avg_v, 2) if avg_v > 0 else None,
                "bb_position":   bb_pos,
                "near_lower_bb": near_lower_bb,
                "prior_down_days": down_days,
                "notes":         notes,
            }

        # --- Scan the lookback window ---
        patterns_found = []
        start = max(5, len(hist) - lookback)   # need at least 5 prior bars for trend
        for i in range(start, len(hist)):
            result = _classify_bar(i)
            if result:
                patterns_found.append(result)

        # --- Overall bounce signal ---
        bullish = [p for p in patterns_found if p["bias"] == "bullish"]
        bearish = [p for p in patterns_found if p["bias"] == "bearish"]

        if bullish:
            best = max(bullish, key=lambda p: p["strength_score"])
            if best["strength"] == "strong":
                bounce_signal = "strong bullish reversal pattern detected"
            elif best["strength"] == "moderate":
                bounce_signal = "moderate bullish reversal pattern detected"
            else:
                bounce_signal = "weak bullish pattern — watch for confirmation"
        elif bearish:
            bounce_signal = "bearish topping pattern detected — avoid long entries"
        else:
            bounce_signal = "no reversal pattern in lookback window"

        return {
            "symbol":        symbol.upper(),
            "interval":      interval,
            "lookback":      lookback,
            "last_close":    round(float(close.iloc[-1]), 2),
            "patterns_found": patterns_found,
            "pattern_count": len(patterns_found),
            "bounce_signal": bounce_signal,
        }

    def get_atr_bands(self, symbol: str, period: int = 14, band_mult: float = 2.0,
                      stop_mult: float = 3.0, interval: str = "1d", lookback: int = 250) -> dict:
        valid_intervals = {"1d", "1h", "1wk"}
        if interval not in valid_intervals:
            raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(sorted(valid_intervals))}")

        fetch_period = {"1d": "2y", "1h": "60d", "1wk": "5y"}[interval]

        hist = self.get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

        min_bars = period * 2 + 5
        if len(hist) < min_bars:
            raise ValueError(f"Not enough data for {symbol} (got {len(hist)} bars, need {min_bars})")

        if len(hist) > lookback:
            hist = hist.tail(lookback)

        high  = hist["High"]
        low   = hist["Low"]
        close = hist["Close"]

        atr = atr_series(high, low, close, period)

        last_close = float(close.iloc[-1])
        last_atr   = float(atr.iloc[-1])
        atr_pct    = last_atr / last_close * 100

        upper_band = last_close + band_mult * last_atr
        lower_band = last_close - band_mult * last_atr

        # ATR regime: current reading vs its ~3-month mean (63 daily bars)
        trend_window = min(63, len(atr))
        atr_mean = float(atr.tail(trend_window).mean())
        if last_atr > atr_mean * 1.1:
            atr_trend = "expanding"
        elif last_atr < atr_mean * 0.9:
            atr_trend = "contracting"
        else:
            atr_trend = "stable"

        # Chandelier exit: trailing stop hung from the highest high of the
        # last 22 bars, offset by stop_mult ATRs.
        chan_window   = min(22, len(hist))
        highest_high  = float(high.tail(chan_window).max())
        chandelier_stop   = highest_high - stop_mult * last_atr
        stop_distance_pct = (last_close - chandelier_stop) / last_close * 100

        fmt = "%Y-%m-%d %H:%M" if interval == "1h" else "%Y-%m-%d"
        bands_history = []
        tail = min(20, len(hist))
        for ts, c, a in zip(hist.index[-tail:], close.iloc[-tail:], atr.iloc[-tail:]):
            c_f, a_f = float(c), float(a)
            bands_history.append({
                "date":  ts.strftime(fmt),
                "atr":   round(a_f, 4),
                "upper": round(c_f + band_mult * a_f, 4),
                "lower": round(c_f - band_mult * a_f, 4),
            })

        interpretation = (
            f"ATR({period}) = {last_atr:.2f} ({atr_pct:.1f}% of price), {atr_trend} vs its "
            f"{trend_window}-bar mean. Chandelier stop ({chan_window}-bar high − {stop_mult}×ATR) "
            f"sits at {chandelier_stop:.2f}, {stop_distance_pct:.1f}% below the last close. "
            f"Unlike a drawdown-calibrated trailing %, ATR re-adapts within ~{period} bars after "
            f"an earnings gap, so the stop reflects current volatility rather than stale gap history."
        )

        return {
            "symbol":            symbol.upper(),
            "interval":          interval,
            "period":            period,
            "band_mult":         band_mult,
            "stop_mult":         stop_mult,
            "last_close":        round(last_close, 4),
            "atr":               round(last_atr, 4),
            "atr_pct":           round(atr_pct, 3),
            "atr_trend":         atr_trend,
            "upper_band":        round(upper_band, 4),
            "lower_band":        round(lower_band, 4),
            "chandelier_stop":   round(chandelier_stop, 4),
            "stop_distance_pct": round(stop_distance_pct, 3),
            "bands_history":     bands_history,
            "interpretation":    interpretation,
        }

    def get_higher_lows(self, symbol: str, swing_bars: int = 3, lookback_swings: int = 6,
                        interval: str = "1h") -> dict:
        valid_intervals = {"15m", "30m", "1h", "1d"}
        if interval not in valid_intervals:
            raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(sorted(valid_intervals))}")

        fetch_period = {"15m": "60d", "30m": "60d", "1h": "60d", "1d": "2y"}[interval]

        hist = self.get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

        min_bars = swing_bars * 2 + 10
        if len(hist) < min_bars:
            raise ValueError(f"Not enough data for {symbol} (got {len(hist)} bars, need {min_bars})")

        low   = hist["Low"]
        close = hist["Close"]
        high  = hist["High"]

        # --- Identify swing lows ---
        # A swing low at index i requires: low[i] < low[i-k] and low[i] < low[i+k]
        # for all k in 1..swing_bars.  We skip the last `swing_bars` bars since they
        # cannot be confirmed yet (right-side bars not yet complete).
        swing_low_indices = []
        scan_end = len(hist) - swing_bars
        for i in range(swing_bars, scan_end):
            l = float(low.iloc[i])
            left_ok  = all(l <= float(low.iloc[i - k]) for k in range(1, swing_bars + 1))
            right_ok = all(l <= float(low.iloc[i + k]) for k in range(1, swing_bars + 1))
            if left_ok and right_ok:
                swing_low_indices.append(i)

        # Keep only the most recent `lookback_swings` swing lows
        recent_indices = swing_low_indices[-lookback_swings:]

        if len(recent_indices) < 2:
            return {
                "symbol":              symbol.upper(),
                "interval":            interval,
                "swing_bars":          swing_bars,
                "last_close":          round(float(close.iloc[-1]), 4),
                "swing_lows_found":    len(recent_indices),
                "higher_low_pattern":  False,
                "pattern_strength":    "none",
                "swing_lows":          [],
                "interpretation":      "Insufficient swing lows detected — market may be trending strongly or data window too short.",
            }

        # Build swing low records
        swing_lows = []
        for idx in recent_indices:
            ts  = hist.index[idx]
            date_str = ts.strftime("%Y-%m-%d %H:%M") if interval != "1d" else ts.strftime("%Y-%m-%d")
            swing_lows.append({
                "date":  date_str,
                "low":   round(float(low.iloc[idx]), 4),
                "close": round(float(close.iloc[idx]), 4),
                "high":  round(float(high.iloc[idx]), 4),
            })

        # --- Detect higher-low sequence from the most recent swing lows ---
        consecutive_higher = 0
        min_rise_pct = float("inf")
        for j in range(len(swing_lows) - 1, 0, -1):
            curr_low = swing_lows[j]["low"]
            prev_low = swing_lows[j - 1]["low"]
            if curr_low > prev_low:
                rise_pct = (curr_low - prev_low) / prev_low * 100
                consecutive_higher += 1
                min_rise_pct = min(min_rise_pct, rise_pct)
            else:
                break   # sequence broken — stop counting

        if min_rise_pct == float("inf"):
            min_rise_pct = 0.0

        higher_low_pattern = consecutive_higher >= 2

        # --- Strength ---
        if consecutive_higher >= 3 and min_rise_pct >= 0.3:
            pattern_strength = "strong"
        elif consecutive_higher >= 2 and min_rise_pct >= 0.3:
            pattern_strength = "moderate"
        elif consecutive_higher >= 2:
            pattern_strength = "weak"
        else:
            pattern_strength = "none"

        # --- Trend before the lows ---
        # Compare close at the first swing low vs close 20 bars before it
        first_idx   = recent_indices[0]
        anchor_idx  = max(0, first_idx - 20)
        prior_close = float(close.iloc[anchor_idx])
        first_close = float(close.iloc[first_idx])
        price_chg   = (first_close - prior_close) / prior_close * 100
        if price_chg <= -3:
            trend_before = "downtrend"
        elif price_chg >= 3:
            trend_before = "uptrend"
        else:
            trend_before = "sideways"

        # --- Interpretation ---
        last_close_val = round(float(close.iloc[-1]), 4)
        if pattern_strength == "strong" and trend_before == "downtrend":
            interpretation = (
                f"{consecutive_higher} consecutive higher lows after a downtrend — "
                "strong structural reversal. High-confidence bounce bottom signal."
            )
        elif pattern_strength == "strong":
            interpretation = (
                f"{consecutive_higher} consecutive higher lows — strong structure forming. "
                "Confirm with volume or MACD crossover."
            )
        elif pattern_strength == "moderate" and trend_before == "downtrend":
            interpretation = (
                "2 consecutive higher lows after a downtrend — early reversal structure. "
                "Watch for a third higher low to confirm."
            )
        elif pattern_strength == "moderate":
            interpretation = (
                "2 consecutive higher lows detected. Moderate signal — "
                "trend context is not a prior downtrend, so strength is limited."
            )
        elif pattern_strength == "weak":
            interpretation = (
                "2 higher lows but with very small separation — possible base forming. "
                "Not yet a reliable reversal signal."
            )
        else:
            interpretation = (
                "No higher-low pattern in the recent swing lows. "
                "Downtrend or sideways structure remains intact."
            )

        return {
            "symbol":               symbol.upper(),
            "interval":             interval,
            "swing_bars":           swing_bars,
            "last_close":           last_close_val,
            "swing_lows_found":     len(recent_indices),
            "higher_low_pattern":   higher_low_pattern,
            "consecutive_higher_lows": consecutive_higher,
            "min_rise_between_lows_pct": round(min_rise_pct, 3),
            "pattern_strength":     pattern_strength,
            "trend_before_lows":    trend_before,
            "swing_lows":           swing_lows,
            "interpretation":       interpretation,
        }

    def get_gap_analysis(self, symbol: str, min_gap_pct: float = 0.5, lookback: int = 60,
                         interval: str = "1d") -> dict:
        valid_intervals = {"1d", "1h"}
        if interval not in valid_intervals:
            raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

        fetch_period = {"1d": "2y", "1h": "60d"}[interval]

        hist = self.get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

        if len(hist) < lookback + 5:
            raise ValueError(f"Not enough data for {symbol} (got {len(hist)} bars, need {lookback + 5})")

        open_  = hist["Open"]
        high   = hist["High"]
        low    = hist["Low"]
        close  = hist["Close"]

        fmt = "%Y-%m-%d %H:%M" if interval == "1h" else "%Y-%m-%d"

        # --- Detect gaps in the lookback window ---
        gaps = []
        start = max(1, len(hist) - lookback)

        for i in range(start, len(hist)):
            prev_close = float(close.iloc[i - 1])
            curr_open  = float(open_.iloc[i])
            if prev_close == 0:
                continue

            gap_pct = (curr_open - prev_close) / prev_close * 100

            if abs(gap_pct) < min_gap_pct:
                continue

            direction  = "gap_up" if gap_pct > 0 else "gap_down"
            gap_top    = max(prev_close, curr_open)
            gap_bottom = min(prev_close, curr_open)
            gap_size   = gap_top - gap_bottom

            # Determine fill status by checking all subsequent bars
            fill_status   = "unfilled"
            fill_date     = None
            for j in range(i + 1, len(hist)):
                bar_high = float(high.iloc[j])
                bar_low  = float(low.iloc[j])
                bar_close= float(close.iloc[j])

                if bar_low <= gap_top and bar_high >= gap_bottom:
                    # Price entered the gap zone
                    if bar_low <= gap_bottom and bar_high >= gap_top:
                        fill_status = "filled"
                    else:
                        fill_status = "partially_filled"
                    fill_date = hist.index[j].strftime(fmt)
                    # Keep scanning — a partial may later become full
                    if fill_status == "filled":
                        break

            gaps.append({
                "date":         hist.index[i].strftime(fmt),
                "direction":    direction,
                "gap_pct":      round(gap_pct, 2),
                "gap_top":      round(gap_top, 4),
                "gap_bottom":   round(gap_bottom, 4),
                "gap_size":     round(gap_size, 4),
                "prev_close":   round(prev_close, 4),
                "open":         round(curr_open, 4),
                "fill_status":  fill_status,
                "fill_date":    fill_date,
            })

        # --- Current price context ---
        last_close = float(close.iloc[-1])

        unfilled = [g for g in gaps if g["fill_status"] == "unfilled"]
        partial  = [g for g in gaps if g["fill_status"] == "partially_filled"]

        # Nearest unfilled gap above current price (overhead resistance)
        gaps_above = [g for g in unfilled if g["gap_bottom"] > last_close]
        nearest_above = min(gaps_above, key=lambda g: g["gap_bottom"]) if gaps_above else None

        # Nearest unfilled gap below current price (support / prior gap-down target)
        gaps_below = [g for g in unfilled if g["gap_top"] < last_close]
        nearest_below = max(gaps_below, key=lambda g: g["gap_top"]) if gaps_below else None

        # Nearest partial gap (price in transition)
        gaps_partial_above = [g for g in partial if g["gap_bottom"] > last_close]
        gaps_partial_below = [g for g in partial if g["gap_top"] < last_close]

        # Distance helpers
        def _dist(gap, price):
            mid = (gap["gap_top"] + gap["gap_bottom"]) / 2
            return round((mid - price) / price * 100, 2)

        # --- Bounce context ---
        bounce_targets = []
        if nearest_above:
            dist = _dist(nearest_above, last_close)
            bounce_targets.append({
                "level":      "nearest_unfilled_gap_above",
                "gap_bottom": nearest_above["gap_bottom"],
                "gap_top":    nearest_above["gap_top"],
                "gap_date":   nearest_above["date"],
                "direction":  nearest_above["direction"],
                "distance_pct": dist,
                "note": f"Gap fill target {dist:+.1f}% from current price — resistance to clear on bounce",
            })
        if nearest_below:
            dist = _dist(nearest_below, last_close)
            bounce_targets.append({
                "level":      "nearest_unfilled_gap_below",
                "gap_bottom": nearest_below["gap_bottom"],
                "gap_top":    nearest_below["gap_top"],
                "gap_date":   nearest_below["date"],
                "direction":  nearest_below["direction"],
                "distance_pct": dist,
                "note": f"Unfilled gap {dist:+.1f}% below — potential support / magnet if price falls further",
            })

        # --- Interpretation ---
        lines = []
        if nearest_above and nearest_above["direction"] == "gap_down":
            lines.append(
                f"Unfilled gap-down at {nearest_above['gap_bottom']:.2f}–{nearest_above['gap_top']:.2f} "
                f"({_dist(nearest_above, last_close):+.1f}%) — first overhead target for a bounce."
            )
        elif nearest_above and nearest_above["direction"] == "gap_up":
            lines.append(
                f"Unfilled gap-up at {nearest_above['gap_bottom']:.2f}–{nearest_above['gap_top']:.2f} "
                f"({_dist(nearest_above, last_close):+.1f}%) — strong resistance zone above."
            )
        if nearest_below and nearest_below["direction"] == "gap_down":
            lines.append(
                f"Unfilled gap-down at {nearest_below['gap_bottom']:.2f}–{nearest_below['gap_top']:.2f} "
                f"({_dist(nearest_below, last_close):+.1f}%) — potential support. "
                "Price may bounce here before continuing lower."
            )
        if partial:
            lines.append(
                f"{len(partial)} partially-filled gap(s) nearby — price has begun filling "
                "these zones, indicating active buyer/seller interest."
            )
        if not lines:
            lines.append("No nearby unfilled gaps — price is trading in a clean zone with no nearby gap magnets.")

        return {
            "symbol":           symbol.upper(),
            "interval":         interval,
            "lookback":         lookback,
            "min_gap_pct":      min_gap_pct,
            "last_close":       round(last_close, 4),
            "total_gaps_found": len(gaps),
            "unfilled_count":   len(unfilled),
            "partial_count":    len(partial),
            "filled_count":     len([g for g in gaps if g["fill_status"] == "filled"]),
            "bounce_targets":   bounce_targets,
            "nearest_gap_above": nearest_above,
            "nearest_gap_below": nearest_below,
            "all_gaps":         gaps,
            "interpretation":   " ".join(lines),
        }

    # ------------------------------------------------------------------
    # Drawdown / stop calibration
    # ------------------------------------------------------------------

    def get_historical_drawdown(self, symbol: str, lookback_days: int = 252) -> dict:
        # Request enough calendar days to cover the trading-day lookback.
        # ~252 trading days ≈ 365 calendar days; use 2× to be safe with the cache.
        calendar_days = max(lookback_days * 2, 365)
        hist = self.get_history(symbol.upper(), "1d", calendar_days).copy()

        if len(hist) < 10:
            raise ValueError(
                f"Not enough data for {symbol} (got {len(hist)} bars, need at least 10)"
            )

        # Trim to the requested trading-day lookback
        hist  = hist.tail(lookback_days)
        close = hist["Close"].dropna()
        high  = hist["High"].dropna()
        low   = hist["Low"].dropna()

        if len(close) < 6:
            raise ValueError(f"Insufficient price data for {symbol}")

        # ------------------------------------------------------------------ #
        # 1-day close-to-close drawdown
        # ------------------------------------------------------------------ #
        daily_returns   = close.pct_change().dropna()
        min_1day_return = float(daily_returns.min())
        worst_1day_idx  = daily_returns.idxmin()

        max_1day_drawdown_pct = round(min_1day_return * 100, 2)   # negative number
        worst_1day_date       = worst_1day_idx.strftime("%Y-%m-%d")

        # ------------------------------------------------------------------ #
        # 5-day rolling close-to-close drawdown
        # ------------------------------------------------------------------ #
        rolling_5day    = close.pct_change(periods=5).dropna()
        min_5day_return = float(rolling_5day.min())
        worst_5day_end  = rolling_5day.idxmin()

        end_pos   = close.index.get_loc(worst_5day_end)
        start_pos = max(0, end_pos - 5)

        max_5day_drawdown_pct  = round(min_5day_return * 100, 2)
        worst_5day_end_str     = worst_5day_end.strftime("%Y-%m-%d")
        worst_5day_start_str   = close.index[start_pos].strftime("%Y-%m-%d")

        # ------------------------------------------------------------------ #
        # Worst intraday drop (High → Low within a single session)
        # ------------------------------------------------------------------ #
        intraday_drops        = ((low - high) / high * 100).dropna()
        max_intraday_drop_pct = round(float(intraday_drops.min()), 2)
        worst_intraday_date   = intraday_drops.idxmin().strftime("%Y-%m-%d")

        # ------------------------------------------------------------------ #
        # Recent 30-bar regime (has volatility changed lately?)
        # ------------------------------------------------------------------ #
        recent_returns      = daily_returns.tail(30)
        recent_max_1day_pct = round(float(recent_returns.min()) * 100, 2) if len(recent_returns) > 0 else max_1day_drawdown_pct

        # ------------------------------------------------------------------ #
        # Trailing stop recommendation — average of 1-day and 5-day worst
        # ------------------------------------------------------------------ #
        abs_1day          = abs(max_1day_drawdown_pct)
        abs_5day          = abs(max_5day_drawdown_pct)
        avg_drawdown_pct  = round((abs_1day + abs_5day) / 2, 2)
        trailing_stop_pct = avg_drawdown_pct   # positive %, for broker trailing stop input

        # ------------------------------------------------------------------ #
        # Stop validation notes
        # ------------------------------------------------------------------ #
        abs_recent = abs(recent_max_1day_pct)
        notes = []

        if abs_recent > abs_1day * 1.4:
            notes.append(
                f"Recent 30-bar volatility ({abs_recent:.1f}% max 1-day) is significantly "
                f"higher than the {len(close)}-day average ({abs_1day:.1f}%). "
                "Consider widening the trailing stop to reflect the current regime."
            )
        elif abs_recent < abs_1day * 0.6:
            notes.append(
                f"Recent 30-bar volatility ({abs_recent:.1f}%) is well below the "
                f"{len(close)}-day average ({abs_1day:.1f}%). "
                "A tighter trailing stop may be appropriate in the current low-volatility regime."
            )

        notes.append(
            f"Any fixed stop within {abs_1day:.1f}% of current price risks a false trigger "
            f"on a single bad session. Minimum safe trailing stop: {trailing_stop_pct:.1f}%."
        )

        if abs_5day > abs_1day * 2.0:
            notes.append(
                f"5-day drawdown ({abs_5day:.1f}%) exceeds 2× the single-day worst "
                f"({abs_1day:.1f}%), indicating this stock can trend down steadily across "
                "multiple sessions. A trailing stop may lag significantly in a sustained sell-off."
            )

        return {
            "symbol":               symbol.upper(),
            "lookback_trading_days": len(close),
            "last_close":           round(float(close.iloc[-1]), 2),

            "max_1day_drawdown_pct":  max_1day_drawdown_pct,
            "worst_1day_date":        worst_1day_date,

            "max_5day_drawdown_pct":  max_5day_drawdown_pct,
            "worst_5day_start":       worst_5day_start_str,
            "worst_5day_end":         worst_5day_end_str,

            "max_intraday_drop_pct":  max_intraday_drop_pct,
            "worst_intraday_date":    worst_intraday_date,

            "recent_max_1day_pct":    recent_max_1day_pct,

            "avg_drawdown_pct":       avg_drawdown_pct,
            "trailing_stop_pct":      trailing_stop_pct,

            "stop_width_note":        " ".join(notes),
        }

    # ------------------------------------------------------------------
    # REST surfaces (api/app.py)
    # ------------------------------------------------------------------

    def get_ohlcv_bars(self, ticker: str, days: int = 180) -> dict:
        ticker = ticker.upper()
        df = self.get_history(ticker, "1d", days)

        if df.empty:
            return {"ticker": ticker, "bars": []}

        df = df.tail(days)
        bars = []
        for ts, row in df.iterrows():
            ts_str = pd.Timestamp(ts).strftime("%Y-%m-%d")
            bars.append({
                "date": ts_str,
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low":  round(float(row["Low"]),  4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            })
        return {"ticker": ticker, "bars": bars}

    def get_risk_signals(self, ticker: str) -> dict:
        """Historical drawdown metrics for stop-loss calibration, plus VWAP context.

        Composes get_historical_drawdown + get_vwap. Mirrors the
        /api/securities/<ticker>/signals/risk route: drawdown failure degrades
        to ``{"drawdown": None, "error": ...}`` (HTTP 200), and the VWAP enrich
        is best-effort (swallowed on failure).
        """
        ticker = ticker.upper()
        try:
            dd = self.get_historical_drawdown(ticker)
        except Exception as exc:
            return {"ticker": ticker, "drawdown": None, "error": str(exc)}
        # Derive a simple stop-loss recommendation from drawdown stats
        price_data: dict = {}
        try:
            vd = self.get_vwap(ticker)
            price_data = {"vwap": vd.get("vwap"), "vwap_position": vd.get("position")}
        except Exception:
            pass
        return {"ticker": ticker, "drawdown": dd, **price_data}

    def get_technicals_table(self, ticker: str, days: int = 365) -> dict:
        ticker = ticker.upper()
        df = self.get_history(ticker, "1d", max(days, 400))

        if df.empty:
            return {"ticker": ticker, "indicators": []}

        closes = df["Close"]

        # Moving averages
        ma10  = closes.rolling(10).mean()
        ma30  = closes.rolling(30).mean()
        ma50  = closes.rolling(50).mean()
        ma100 = closes.rolling(100).mean()
        ma200 = closes.rolling(200).mean()

        # Bollinger Bands (20-day)
        sma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20

        # RSI (14-day)
        rsi = rsi_series(closes)

        # MACD
        macd_line, signal_line, histogram = macd_series(closes)

        df_out = df.tail(days)
        indicators = []
        for ts, row in df_out.iterrows():
            idx = closes.index.get_loc(ts)
            indicators.append({
                "date":      pd.Timestamp(ts).strftime("%Y-%m-%d"),
                "close":     safe_float(row["Close"]),
                "volume":    int(row["Volume"]),
                "ma10":      safe_float(ma10.iloc[idx]),
                "ma30":      safe_float(ma30.iloc[idx]),
                "ma50":      safe_float(ma50.iloc[idx]),
                "ma100":     safe_float(ma100.iloc[idx]),
                "ma200":     safe_float(ma200.iloc[idx]),
                "bb_upper":  safe_float(bb_upper.iloc[idx]),
                "bb_middle": safe_float(sma20.iloc[idx]),
                "bb_lower":  safe_float(bb_lower.iloc[idx]),
                "rsi":       safe_float(rsi.iloc[idx]),
                "macd":      safe_float(macd_line.iloc[idx]),
                "macd_signal": safe_float(signal_line.iloc[idx]),
                "macd_hist": safe_float(histogram.iloc[idx]),
            })
        return {"ticker": ticker, "indicators": indicators}

    def get_technical_signals(self, ticker: str) -> dict:
        ticker = ticker.upper()

        tasks = {
            "stochastic":           lambda: self.get_stochastic(ticker),
            "vwap":                 lambda: self.get_vwap(ticker),
            "obv":                  lambda: self.get_obv(ticker),
            "volume_analysis":      lambda: self.get_volume_analysis(ticker),
            "candlestick_patterns": lambda: self.get_candlestick_patterns(ticker),
            "higher_lows":          lambda: self.get_higher_lows(ticker, interval="1d"),
            "gap_analysis":         lambda: self.get_gap_analysis(ticker),
        }

        results: dict = {}
        errors: dict = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(fn): key for key, fn in tasks.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    results[key] = None
                    errors[key] = str(e)

        return {"ticker": ticker, "_errors": errors if errors else None, **results}

    def screen_securities(self, filters: dict, portfolio: list[dict], watchlist: list[dict]) -> dict:
        rsi_max        = filters.get("rsi_max")
        rsi_min        = filters.get("rsi_min")
        above_ma50     = filters.get("above_ma50", False)
        below_ma50     = filters.get("below_ma50", False)
        above_ma200    = filters.get("above_ma200", False)
        below_ma200    = filters.get("below_ma200", False)
        near_bb_low    = filters.get("near_bb_lower", False)
        near_bb_high   = filters.get("near_bb_upper", False)
        macd_bull      = filters.get("macd_bullish", False)
        macd_bear      = filters.get("macd_bearish", False)
        news_sentiment = filters.get("news_sentiment")
        src_filter     = filters.get("source", "all")

        # Always pre-load sentiment so results carry news_sentiment even when
        # the filter is not active.  Cheap read — non-fatal if missing.
        _sentiment_map: dict[str, str] = {}
        try:
            _sentiment_map = {
                sym: snap["overall_sentiment"]
                for sym, snap in self._sentiment.get_all_latest().items()
                if snap.get("overall_sentiment")
            }
        except Exception:
            pass

        # Load all securities
        portfolio = {s["symbol"]: s for s in portfolio}
        watchlist = {s["symbol"]: s for s in watchlist}
        combined: dict[str, dict] = {}
        for sym, s in portfolio.items():
            combined[sym] = s
        for sym, s in watchlist.items():
            if sym in combined:
                combined[sym]["source"] = "both"
                combined[sym]["tags"] = s["tags"]
            else:
                combined[sym] = s

        if src_filter == "portfolio":
            symbols = [s for s in combined if combined[s]["source"] in ("portfolio", "both")]
        elif src_filter == "watchlist":
            symbols = [s for s in combined if combined[s]["source"] in ("watchlist", "both")]
        else:
            symbols = list(combined.keys())

        if not symbols:
            return {"results": [], "count": 0}

        # Pull all daily bars for the symbols in one SQL query
        rows = self._ohlcv.daily_bars_for_symbols(symbols)

        # Group rows by symbol and compute indicators
        bars_by_sym: dict[str, list] = defaultdict(list)
        for r in rows:
            bars_by_sym[r["symbol"]].append(r)

        results = []
        for sym in symbols:
            bars = bars_by_sym.get(sym, [])
            if len(bars) < 30:
                continue

            closes  = np.array([b["close"] for b in bars], dtype=float)
            volumes = np.array([b["volume"] for b in bars], dtype=float)

            # Use pandas Series for rolling computation
            cs = pd.Series(closes)
            ma50  = cs.rolling(50).mean().iloc[-1]   if len(closes) >= 50  else None
            ma200 = cs.rolling(200).mean().iloc[-1]  if len(closes) >= 200 else None
            sma20 = cs.rolling(20).mean()
            std20 = cs.rolling(20).std()
            bb_upper_s = (sma20 + 2 * std20).iloc[-1]
            bb_lower_s = (sma20 - 2 * std20).iloc[-1]

            # RSI
            rsi_val = rsi_series(cs).iloc[-1]

            # MACD
            macd_line, signal_line, _ = macd_series(cs)
            macd_val   = float(macd_line.iloc[-1])   if not pd.isna(macd_line.iloc[-1])   else None
            macd_sig   = float(signal_line.iloc[-1]) if not pd.isna(signal_line.iloc[-1]) else None

            last_close = float(closes[-1])
            rsi        = float(rsi_val)   if rsi_val is not None and not pd.isna(rsi_val) else None
            ma50_f     = float(ma50)      if ma50 is not None and not pd.isna(ma50)       else None
            ma200_f    = float(ma200)     if ma200 is not None and not pd.isna(ma200)     else None
            bb_upper_f = float(bb_upper_s) if not pd.isna(bb_upper_s)                    else None
            bb_lower_f = float(bb_lower_s) if not pd.isna(bb_lower_s)                    else None

            # Apply filters
            if rsi_max  is not None and (rsi is None or rsi > rsi_max):       continue
            if rsi_min  is not None and (rsi is None or rsi < rsi_min):       continue
            if above_ma50  and (ma50_f  is None or last_close <= ma50_f):     continue
            if below_ma50  and (ma50_f  is None or last_close >= ma50_f):     continue
            if above_ma200 and (ma200_f is None or last_close <= ma200_f):    continue
            if below_ma200 and (ma200_f is None or last_close >= ma200_f):    continue
            if near_bb_low and (bb_lower_f is None or
                                abs(last_close - bb_lower_f) / bb_lower_f > 0.03): continue
            if near_bb_high and (bb_upper_f is None or
                                 abs(last_close - bb_upper_f) / bb_upper_f > 0.03): continue
            if macd_bull and (macd_val is None or macd_sig is None or macd_val <= macd_sig): continue
            if macd_bear and (macd_val is None or macd_sig is None or macd_val >= macd_sig): continue
            if news_sentiment and _sentiment_map.get(sym) != news_sentiment:                 continue

            sec = combined[sym]
            results.append({
                **sec,
                "last_close":      round(last_close, 4),
                "rsi":             round(rsi, 1) if rsi is not None else None,
                "ma50":            round(ma50_f, 2) if ma50_f is not None else None,
                "ma200":           round(ma200_f, 2) if ma200_f is not None else None,
                "bb_upper":        round(bb_upper_f, 2) if bb_upper_f is not None else None,
                "bb_lower":        round(bb_lower_f, 2) if bb_lower_f is not None else None,
                "macd":            round(macd_val, 4) if macd_val is not None else None,
                "macd_signal":     round(macd_sig, 4) if macd_sig is not None else None,
                "news_sentiment":  _sentiment_map.get(sym),
            })

        results.sort(key=lambda x: x["rsi"] if x["rsi"] is not None else 50)
        return {"results": results, "count": len(results)}
