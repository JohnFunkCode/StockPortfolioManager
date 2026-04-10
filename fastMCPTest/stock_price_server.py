import datetime
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yfinance as yf
from fastmcp import FastMCP
from ohlcv_cache import get_history, period_to_days
from options_store import OptionsStore

# Shared store — persists every get_stock_price call for backtesting (issue #10)
_options_store = OptionsStore()

mcp = FastMCP("stock-price-server")


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


@mcp.tool()
def get_news(symbol: str, max_articles: int = 10) -> dict:
    """Get recent news articles for a given ticker symbol from Yahoo Finance."""
    ticker = yf.Ticker(symbol.upper())
    raw = ticker.news or []

    articles = []
    for item in raw[:max_articles]:
        content = item.get("content", {})
        pub_ts = content.get("pubDate", "")
        articles.append({
            "title": content.get("title", ""),
            "publisher": content.get("provider", {}).get("displayName", ""),
            "published": pub_ts,
            "summary": content.get("summary", ""),
            "url": content.get("canonicalUrl", {}).get("url", ""),
        })

    return {
        "symbol": symbol.upper(),
        "article_count": len(articles),
        "articles": articles,
    }


@mcp.tool()
def get_stock_price(symbol: str) -> dict:
    """Get the current stock price, Bollinger Bands (20-day, 2σ), and options chain summary for a given ticker symbol."""
    ticker = yf.Ticker(symbol.upper())
    info = ticker.fast_info

    price = info.last_price
    if price is None:
        raise ValueError(f"Could not retrieve price for symbol: {symbol}")

    # Bollinger Bands
    hist = get_history(symbol.upper(), "1d", 90)
    close = hist["Close"]
    sma20 = close.rolling(window=20).mean().iloc[-1]
    std20 = close.rolling(window=20).std().iloc[-1]
    upper_band = sma20 + 2 * std20
    lower_band = sma20 - 2 * std20

    # Options chain (nearest expiration)
    options_data = None
    expirations = ticker.options
    if expirations:
        nearest_exp = expirations[0]
        chain = ticker.option_chain(nearest_exp)
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
        "bollinger_bands": {
            "upper": round(upper_band, 2),
            "middle": round(sma20, 2),
            "lower": round(lower_band, 2),
            "period": 20,
            "std_dev": 2,
        },
        "options": options_data,
    }

@mcp.tool()
def get_rsi(symbol: str, period: int = 14, interval: str = "1d") -> dict:
    """Calculate the Relative Strength Index (RSI) for a stock symbol.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
        period: Number of periods for RSI calculation (default: 14)
        interval: Price interval — '1d' for daily (default), '1wk' for weekly, '1mo' for monthly
    """
    valid_intervals = {"1d", "1wk", "1mo"}
    if interval not in valid_intervals:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

    fetch_period = {"1d": "90d", "1wk": "2y", "1mo": "5y"}[interval]

    hist = get_history(symbol.upper(), interval, period_to_days(fetch_period))
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


@mcp.tool()
def get_macd(symbol: str, interval: str = "1d") -> dict:
    """Calculate MACD (Moving Average Convergence Divergence) for a stock symbol.

    Uses standard parameters: fast EMA=12, slow EMA=26, signal EMA=9.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
        interval: Price interval — '1d' for daily (default), '1wk' for weekly, '1mo' for monthly
    """
    valid_intervals = {"1d", "1wk", "1mo"}
    if interval not in valid_intervals:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

    fetch_period = {"1d": "6mo", "1wk": "3y", "1mo": "10y"}[interval]

    hist = get_history(symbol.upper(), interval, period_to_days(fetch_period))
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


@mcp.tool()
def get_stochastic(symbol: str, k_period: int = 14, d_period: int = 3, interval: str = "1d") -> dict:
    """Calculate the Stochastic Oscillator (%K and %D) for a stock symbol.

    %K = (Close - Lowest Low) / (Highest High - Lowest Low) × 100
    %D = d_period-SMA of %K (the signal line)

    Readings above 80 indicate overbought; below 20 indicate oversold.
    A %K crossover above %D while below 20 is a bullish reversal signal.
    A %K crossover below %D while above 80 is a bearish reversal signal.

    Args:
        symbol:   Stock ticker symbol (e.g. 'AAPL')
        k_period: Lookback window for %K (default: 14)
        d_period: SMA period for %D signal line (default: 3)
        interval: Price interval — '1d' daily (default), '1wk' weekly, '1mo' monthly
    """
    valid_intervals = {"1d", "1wk", "1mo"}
    if interval not in valid_intervals:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

    fetch_period = {"1d": "90d", "1wk": "2y", "1mo": "5y"}[interval]

    hist = get_history(symbol.upper(), interval, period_to_days(fetch_period))

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


@mcp.tool()
def get_volume_analysis(symbol: str, lookback: int = 20, interval: str = "1d") -> dict:
    """Analyse volume for climax / capitulation signals to help identify bounce bottoms.

    Detects three conditions across recent bars:

    CLIMAX (exhaustion top or capitulation bottom):
      A bar whose volume is ≥ climax_threshold × the rolling average AND whose
      price range (High - Low) is unusually wide.  On a strong down day this is
      bearish capitulation — sellers exhausted, bounce likely.  On a strong up
      day it can signal an exhaustion top.

    QUIET FOLLOW-THROUGH:
      A low-volume day (≤ 0.6 × avg) immediately after a climax down day.
      Sellers dried up — classic two-bar bottom pattern.

    OBV DIVERGENCE:
      On-Balance Volume stopped declining while price made a new low in the
      lookback window — accumulation beneath the surface.

    Args:
        symbol:    Stock ticker symbol (e.g. 'AAPL')
        lookback:  Number of bars for rolling average and divergence check (default: 20)
        interval:  '1d' daily (default), '1wk' weekly, '1mo' monthly
    """
    valid_intervals = {"1d", "1wk", "1mo"}
    if interval not in valid_intervals:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

    fetch_period = {"1d": "6mo", "1wk": "3y", "1mo": "10y"}[interval]

    hist = get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

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

    recent = hist.iloc[-lookback:]
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


@mcp.tool()
def get_obv(symbol: str, lookback: int = 20, interval: str = "1d") -> dict:
    """Calculate On-Balance Volume (OBV) and detect bullish/bearish divergence.

    OBV accumulates volume on up days and subtracts it on down days.  When OBV
    rises while price falls (bullish divergence), institutions are quietly
    accumulating — a strong bounce-bottom signal.  When OBV falls while price
    rises (bearish divergence), distribution is occurring beneath the surface.

    Signals returned:
      obv_trend         — 'rising', 'falling', or 'flat' over the lookback window
      price_trend       — 'rising', 'falling', or 'flat' over the lookback window
      divergence        — 'bullish', 'bearish', or 'none'
      divergence_strength — 'strong', 'moderate', 'weak', or 'none'
        Strong  : OBV and price trends move in clearly opposite directions
                  (OBV slope and price slope differ by > 30% of their range)
        Moderate: Trends differ but one is near flat
        Weak    : Minor divergence — one trend is barely sloping

    Args:
        symbol:   Stock ticker symbol (e.g. 'AAPL')
        lookback: Number of bars to evaluate trend and divergence (default: 20)
        interval: '1d' daily (default), '1wk' weekly, '1mo' monthly
    """
    valid_intervals = {"1d", "1wk", "1mo"}
    if interval not in valid_intervals:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

    fetch_period = {"1d": "6mo", "1wk": "3y", "1mo": "10y"}[interval]

    hist = get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

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
    x            = list(range(lookback))

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


@mcp.tool()
def get_vwap(symbol: str, lookback: int = 20, interval: str = "1d") -> dict:
    """Calculate rolling VWAP and detect reclaim events to identify bounce bottoms.

    VWAP (Volume Weighted Average Price) = Σ(typical_price × volume) / Σ(volume)
    where typical_price = (High + Low + Close) / 3

    A VWAP reclaim occurs when price crosses back above the rolling VWAP after
    trading below it — one of the most reliable intraday-to-swing bounce signals
    used by institutional traders.

    Signals returned:
      position        — 'above_vwap' or 'below_vwap'
      distance_pct    — how far price is from VWAP as a percentage
      reclaim_signal  — True if price just crossed above VWAP (last 1–3 bars)
      reclaim_strength — 'strong', 'moderate', 'weak', or 'none'
        Strong  : reclaim + price closed above VWAP for ≥ 2 consecutive bars
                  + volume on reclaim bar was above average
        Moderate: reclaim confirmed for 1 bar with above-average volume
        Weak    : reclaim bar only, volume below average (unconfirmed)
      crossover_events — recent bars where price crossed VWAP (last lookback bars)
      consecutive_bars_above/below — streak length for current position

    Args:
        symbol:   Stock ticker symbol (e.g. 'AAPL')
        lookback: Rolling window for VWAP calculation (default: 20)
        interval: '1d' daily (default), '1wk' weekly, '1mo' monthly
    """
    valid_intervals = {"1d", "1wk", "1mo"}
    if interval not in valid_intervals:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

    fetch_period = {"1d": "6mo", "1wk": "3y", "1mo": "10y"}[interval]

    hist = get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

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


@mcp.tool()
def get_candlestick_patterns(symbol: str, lookback: int = 10, interval: str = "1d") -> dict:
    """Detect Hammer and Doji candlestick patterns to identify potential bounce bottoms.

    Patterns detected:
      BULLISH (bounce-bottom signals):
        hammer          — small body near top of range, lower wick ≥ 2× body,
                          tiny upper wick.  Strongest single-bar reversal signal.
        dragonfly_doji  — open ≈ close near the high, long lower wick.
                          Bullish version of doji at a low.
        inverted_hammer — small body near bottom of range, upper wick ≥ 2× body.
                          Bullish when appearing after a downtrend.

      NEUTRAL / INDECISION:
        doji            — open ≈ close (body ≤ 10% of range), no dominant wick.
                          Signals indecision; context (prior trend) determines bias.
        long_legged_doji — doji with long wicks both sides — high indecision.

      BEARISH (topping signals):
        gravestone_doji — open ≈ close near the low, long upper wick.
        shooting_star   — small body near bottom of range, upper wick ≥ 2× body,
                          tiny lower wick.  Bearish when appearing after an uptrend.
        hanging_man     — same shape as hammer but appearing after an uptrend.

    Each detected pattern is scored for strength based on:
      - Wick/body ratios (how textbook-perfect is the shape)
      - Preceding trend (consecutive down days amplify bullish reversal signals)
      - Volume (above-average volume on the pattern bar adds conviction)
      - Proximity to lower Bollinger Band (confirms oversold context)

    Args:
        symbol:   Stock ticker symbol (e.g. 'AAPL')
        lookback: Number of recent bars to scan for patterns (default: 10)
        interval: '1d' daily (default), '1wk' weekly, '1mo' monthly
    """
    valid_intervals = {"1d", "1wk", "1mo"}
    if interval not in valid_intervals:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

    fetch_period = {"1d": "6mo", "1wk": "3y", "1mo": "10y"}[interval]

    hist = get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

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


@mcp.tool()
def get_higher_lows(symbol: str, swing_bars: int = 3, lookback_swings: int = 6,
                    interval: str = "1h") -> dict:
    """Detect higher-low price structure to identify the first signs of a bounce reversal.

    A higher low occurs when a swing low (local price minimum) is above the
    previous swing low — the single most reliable early signal that a downtrend
    is losing momentum and a reversal is forming.

    A swing low is defined as a bar whose low is lower than the `swing_bars`
    bars on each side of it (a local minimum in the price structure).

    Supports intraday intervals for fine-grained structure analysis:
      '15m'  — 15-minute bars (last 60 days)
      '30m'  — 30-minute bars (last 60 days)
      '1h'   — hourly bars (last 60 days, default)
      '1d'   — daily bars (last 2 years)

    Signals returned:
      higher_low_pattern  — True if the most recent swing lows form a rising series
      pattern_strength    — 'strong' / 'moderate' / 'weak' / 'none'
        Strong  : ≥ 3 consecutive higher lows, each meaningfully higher (> 0.3%)
        Moderate: 2 consecutive higher lows with meaningful separation
        Weak    : 2 consecutive higher lows with small separation (< 0.3%)
      swing_lows          — detected swing lows (date, price, index) in the lookback
      trend_before_lows   — 'downtrend' / 'uptrend' / 'sideways' before the pattern
                            (higher lows after a downtrend are more significant)
      interpretation       — plain-English summary

    Args:
        symbol:         Stock ticker symbol (e.g. 'AAPL')
        swing_bars:     Bars on each side that must be higher for a swing-low pivot (default: 3)
        lookback_swings: Number of recent swing lows to analyse (default: 6)
        interval:       Bar interval — '15m', '30m', '1h' (default), or '1d'
    """
    valid_intervals = {"15m", "30m", "1h", "1d"}
    if interval not in valid_intervals:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(sorted(valid_intervals))}")

    fetch_period = {"15m": "60d", "30m": "60d", "1h": "60d", "1d": "2y"}[interval]

    hist = get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

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


@mcp.tool()
def get_gap_analysis(symbol: str, min_gap_pct: float = 0.5, lookback: int = 60,
                     interval: str = "1d") -> dict:
    """Detect price gaps and identify unfilled gap levels to help locate bounce targets.

    A gap occurs when the open of a bar is meaningfully above or below the
    prior bar's close, leaving a price zone that was never traded through.
    Markets have a strong statistical tendency to return and "fill" these gaps,
    making unfilled gaps powerful support/resistance magnets.

    For bounce-bottom analysis:
      - Unfilled gap-DOWN zones ABOVE current price = overhead resistance to clear
      - Unfilled gap-DOWN zones at or below current price = potential support / bounce target
      - A gap DOWN that is partially filled signals buyers stepping in — early bounce sign
      - A gap UP that forms near a prior low = breakaway gap (strong — often doesn't fill)

    Gap classification:
      gap_up    — today's open > yesterday's close by ≥ min_gap_pct
      gap_down  — today's open < yesterday's close by ≥ min_gap_pct

    Fill status:
      filled        — price has fully traded back through the gap zone
      partially_filled — price has entered but not closed the gap zone
      unfilled      — price has not returned to the gap zone

    Args:
        symbol:      Stock ticker symbol (e.g. 'AAPL')
        min_gap_pct: Minimum gap size as % of prior close to qualify (default: 0.5%)
        lookback:    Number of bars to scan for gaps (default: 60)
        interval:    '1d' daily (default), '1h' hourly
    """
    valid_intervals = {"1d", "1h"}
    if interval not in valid_intervals:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

    fetch_period = {"1d": "2y", "1h": "60d"}[interval]

    hist = get_history(symbol.upper(), interval, period_to_days(fetch_period)).copy()

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


@mcp.tool()
def get_unusual_calls(
    symbol: str,
    min_volume: int = 100,
    min_vol_oi_ratio: float = 0.5,
    max_expirations: int = 3,
) -> dict:
    """Detect unusual call activity (sweeps) to identify smart-money bullish positioning.

    A call sweep is a large, aggressive options buy that crosses multiple exchanges
    at or above the ask price, signalling urgency and institutional conviction.
    yfinance does not expose individual trade prints, so this tool identifies
    sweep-like behaviour from the options chain using four proxy signals:

      1. vol/OI ratio ≥ min_vol_oi_ratio
         Volume exceeds a meaningful fraction of open interest → fresh positioning,
         not just rolling existing contracts.  vol/OI > 1.0 means more contracts
         traded today than exist in OI — a strong sweep indicator.

      2. Aggressive fill: last_price ≥ ask
         The most recent trade printed AT or ABOVE the ask.  Buyers paid up —
         the hallmark of an urgent institutional sweep.

      3. OTM positioning
         Out-of-the-money calls with high volume signal a directional speculative
         bet, not a covered-call write or hedge.  Strikes > 5% OTM with unusual
         volume are the most bullish sweep signal.

      4. Absolute volume threshold (min_volume)
         Filters out noise from low-liquidity strikes.

    Each qualifying contract receives a sweep_score (0–10):
      +3  vol/OI ≥ 2.0  (major fresh positioning)
      +2  vol/OI 1.0–2.0
      +1  vol/OI 0.5–1.0
      +2  last ≥ ask (aggressive fill confirmed)
      +1  last ≥ mid (paid above midpoint — somewhat aggressive)
      +2  strike 5–15% OTM (pure directional bet)
      +1  strike 1–5% OTM (near-money directional)
      -1  in-the-money (more likely a hedge than a speculative sweep)

    Args:
        symbol:           Stock ticker symbol (e.g. 'AAPL')
        min_volume:       Minimum contract volume to consider (default: 100)
        min_vol_oi_ratio: Minimum volume/OI ratio to flag (default: 0.5)
        max_expirations:  Number of nearest expirations to scan (default: 3)
    """
    ticker     = yf.Ticker(symbol.upper())
    info       = ticker.fast_info
    price      = getattr(info, "last_price", None)
    if price is None or math.isnan(float(price)):
        raise ValueError(f"Could not retrieve price for {symbol}")
    price = float(price)

    expirations = ticker.options
    if not expirations:
        return {
            "symbol": symbol.upper(),
            "price": round(price, 2),
            "sweep_signal": "none",
            "unusual_calls": [],
            "interpretation": "No options data available.",
        }

    unusual_calls = []

    for exp in expirations[:max_expirations]:
        try:
            chain    = ticker.option_chain(exp)
            calls_df = chain.calls.copy()
        except Exception:
            continue

        if calls_df.empty:
            continue

        for _, row in calls_df.iterrows():
            volume = _safe_int(row.get("volume"))
            if volume < min_volume:
                continue

            oi = _safe_int(row.get("openInterest"))
            if oi == 0:
                oi = 1   # avoid /0; vol/OI will be large, which is correct

            vol_oi = round(volume / oi, 2)
            if vol_oi < min_vol_oi_ratio:
                continue

            strike  = round(float(row.get("strike", 0)), 2)
            last    = round(float(row.get("lastPrice", 0) or 0), 2)
            bid     = round(float(row.get("bid", 0) or 0), 2)
            ask     = round(float(row.get("ask", 0) or 0), 2)
            iv      = round(float(row.get("impliedVolatility", 0) or 0) * 100, 1)
            itm     = bool(row.get("inTheMoney", False))
            mid     = round((bid + ask) / 2, 2) if ask > 0 else 0.0
            otm_pct = round((strike - price) / price * 100, 1) if price > 0 else 0.0

            # --- Sweep score ---
            score = 0

            if   vol_oi >= 2.0: score += 3
            elif vol_oi >= 1.0: score += 2
            else:               score += 1   # already ≥ min_vol_oi_ratio

            if ask > 0 and last >= ask:
                score += 2   # paid at or above ask — aggressive fill
            elif mid > 0 and last >= mid:
                score += 1   # paid above midpoint

            if 5.0 <= otm_pct <= 15.0:
                score += 2   # pure directional bet
            elif 1.0 <= otm_pct < 5.0:
                score += 1   # near-money directional
            elif itm:
                score -= 1   # likely a hedge, not a sweep

            # Interpretation per contract
            if score >= 7:
                conviction = "very high"
            elif score >= 5:
                conviction = "high"
            elif score >= 3:
                conviction = "moderate"
            else:
                conviction = "low"

            notes = []
            if vol_oi >= 1.0:
                notes.append(f"vol/OI {vol_oi:.1f}× — more contracts traded than exist in OI")
            if ask > 0 and last >= ask:
                notes.append("paid AT or ABOVE ask — aggressive sweep fill")
            elif mid > 0 and last >= mid:
                notes.append("paid above mid — motivated buyer")
            if 5.0 <= otm_pct <= 15.0:
                notes.append(f"{otm_pct:+.1f}% OTM — pure directional bet")
            elif 1.0 <= otm_pct < 5.0:
                notes.append(f"{otm_pct:+.1f}% OTM — near-money directional")
            elif itm:
                notes.append(f"{otm_pct:+.1f}% ITM — possible hedge/spread leg")

            unusual_calls.append({
                "expiration":  exp,
                "strike":      strike,
                "last":        last,
                "bid":         bid,
                "ask":         ask,
                "mid":         mid,
                "iv":          iv,
                "volume":      volume,
                "open_interest": oi if oi > 1 else 0,
                "vol_oi_ratio": vol_oi,
                "otm_pct":     otm_pct,
                "in_the_money": itm,
                "sweep_score": score,
                "conviction":  conviction,
                "notes":       notes,
            })

    # Sort by sweep score descending, then volume
    unusual_calls.sort(key=lambda x: (x["sweep_score"], x["volume"]), reverse=True)

    # --- Overall signal ---
    if not unusual_calls:
        sweep_signal  = "none"
        interpretation = (
            f"No unusual call activity detected above the volume ({min_volume}) "
            f"and vol/OI ({min_vol_oi_ratio}) thresholds."
        )
    else:
        top = unusual_calls[0]
        high_conviction = [c for c in unusual_calls if c["conviction"] in ("very high", "high")]
        aggressive_fills = [c for c in unusual_calls if "paid AT or ABOVE ask" in " ".join(c["notes"])]

        if len(high_conviction) >= 3 or (top["sweep_score"] >= 7 and aggressive_fills):
            sweep_signal = "strong"
            interpretation = (
                f"{len(unusual_calls)} unusual call(s) detected across "
                f"{len(set(c['expiration'] for c in unusual_calls))} expiry(ies). "
                f"Strong sweep signal — {len(high_conviction)} high-conviction contract(s), "
                f"{len(aggressive_fills)} aggressive fill(s) at/above ask. "
                "Institutional buyers are positioning bullishly."
            )
        elif len(high_conviction) >= 1 or len(aggressive_fills) >= 1:
            sweep_signal = "moderate"
            interpretation = (
                f"{len(unusual_calls)} unusual call(s) detected. "
                f"Moderate sweep signal — {len(aggressive_fills)} aggressive fill(s). "
                "Smart money showing interest; watch for follow-through volume."
            )
        else:
            sweep_signal = "weak"
            interpretation = (
                f"{len(unusual_calls)} elevated-volume call(s) detected but no confirmed "
                "aggressive fills at/above ask. Possible sweep activity — monitor for confirmation."
            )

    return {
        "symbol":          symbol.upper(),
        "price":           round(price, 2),
        "expirations_scanned": list(expirations[:max_expirations]),
        "sweep_signal":    sweep_signal,
        "unusual_call_count": len(unusual_calls),
        "unusual_calls":   unusual_calls[:20],   # cap output at top 20
        "interpretation":  interpretation,
    }


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf — no scipy required."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _bs_delta(S: float, K: float, T: float, sigma: float,
              r: float, is_call: bool) -> float:
    """
    Black-Scholes delta for a European option.

    S     — current underlying price
    K     — strike price
    T     — time to expiry in years
    sigma — implied volatility (decimal, e.g. 0.40 for 40%)
    r     — risk-free rate (decimal)
    is_call — True for call, False for put

    Returns delta in [-1, 1].  Returns ±0.5 for degenerate inputs.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.5 if is_call else -0.5
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        if is_call:
            return _norm_cdf(d1)
        else:
            return _norm_cdf(d1) - 1.0
    except (ValueError, ZeroDivisionError):
        return 0.5 if is_call else -0.5


@mcp.tool()
def get_delta_adjusted_oi(
    symbol: str,
    max_expirations: int = 3,
    risk_free_rate: float = 0.045,
) -> dict:
    """Calculate Delta-Adjusted Open Interest (DAOI) to identify market-maker hedging flows.

    Delta-Adjusted OI multiplies each option contract's open interest by its
    Black-Scholes delta to convert raw contract counts into share-equivalent
    directional exposure.  This reveals the NET directional position of market
    makers — and therefore the direction they must trade the underlying to stay
    hedged.

    Key concepts:
      net_daoi > 0  — market makers are net SHORT calls / net LONG puts overall
                      → they must BUY stock as price rises (supports upward moves)
      net_daoi < 0  — market makers are net LONG calls / net SHORT puts
                      → they must SELL stock as price rises (caps upward moves)
      delta_flip    — the price level where net delta crosses zero.  Price moving
                      TOWARD the flip level triggers mechanical MM buying or selling.

    Bounce-bottom signals:
      • Negative net DAOI + price BELOW the delta flip → MM short gamma, forced to
        buy stock as price recovers → mechanical amplification of any bounce
      • Gamma wall (highest |gamma × OI| strike) acting as a price magnet
      • Net DAOI shifting toward zero as price approaches the flip → hedging flow
        accelerating

    Outputs per expiration and aggregated across all scanned expirations:
      net_daoi_shares   — net share-equivalent exposure (positive = MM net long delta)
      call_daoi_shares  — calls contribution (always positive)
      put_daoi_shares   — puts contribution (always negative)
      delta_flip_strike — strike nearest to zero net delta (price magnet)
      gamma_wall        — strike with highest aggregate |delta × OI| across calls+puts
      mm_hedge_bias     — 'buy_on_rally' or 'sell_on_rally' (direction MM must trade)
      signal            — bounce signal strength

    Args:
        symbol:          Stock ticker symbol (e.g. 'AAPL')
        max_expirations: Number of nearest expirations to analyse (default: 3)
        risk_free_rate:  Annualised risk-free rate as decimal (default: 0.045)
    """
    ticker = yf.Ticker(symbol.upper())
    info   = ticker.fast_info
    price  = getattr(info, "last_price", None)
    if price is None or math.isnan(float(price)):
        raise ValueError(f"Could not retrieve price for {symbol}")
    price = float(price)

    expirations = ticker.options
    if not expirations:
        return {
            "symbol": symbol.upper(),
            "price": round(price, 2),
            "signal": "none",
            "interpretation": "No options data available.",
        }

    today = datetime.date.today()

    # ------------------------------------------------------------------ #
    # Accumulate DAOI across all scanned expirations
    # ------------------------------------------------------------------ #
    total_call_daoi = 0.0
    total_put_daoi  = 0.0

    # Per-strike aggregation for flip and gamma-wall detection
    strike_net_daoi: dict[float, float] = {}   # strike → net delta × OI
    strike_abs_daoi: dict[float, float] = {}   # strike → |delta| × OI (gamma proxy)

    expiry_summaries = []

    for exp in expirations[:max_expirations]:
        try:
            exp_date = datetime.date.fromisoformat(exp)
            T = max((exp_date - today).days / 365.0, 1 / 365.0)
            chain    = ticker.option_chain(exp)
            calls_df = chain.calls.copy()
            puts_df  = chain.puts.copy()
        except Exception:
            continue

        exp_call_daoi = 0.0
        exp_put_daoi  = 0.0

        for df, is_call in [(calls_df, True), (puts_df, False)]:
            if df.empty:
                continue
            for _, row in df.iterrows():
                K   = float(row.get("strike", 0) or 0)
                oi  = _safe_int(row.get("openInterest"))
                raw_iv = float(row.get("impliedVolatility", 0) or 0)
                sigma = raw_iv if raw_iv > 0 else 0.30   # fallback 30% if IV missing

                if K <= 0 or oi <= 0:
                    continue

                delta  = _bs_delta(price, K, T, sigma, risk_free_rate, is_call)
                daoi   = delta * oi

                if is_call:
                    exp_call_daoi += daoi
                else:
                    exp_put_daoi += daoi

                # Aggregate by strike for flip/wall detection
                strike_net_daoi[K] = strike_net_daoi.get(K, 0.0) + daoi
                strike_abs_daoi[K] = strike_abs_daoi.get(K, 0.0) + abs(daoi)

        total_call_daoi += exp_call_daoi
        total_put_daoi  += exp_put_daoi

        expiry_summaries.append({
            "expiration":      exp,
            "days_to_expiry":  (datetime.date.fromisoformat(exp) - today).days,
            "call_daoi_shares": round(exp_call_daoi, 0),
            "put_daoi_shares":  round(exp_put_daoi, 0),
            "net_daoi_shares":  round(exp_call_daoi + exp_put_daoi, 0),
        })

    if not expiry_summaries:
        return {
            "symbol": symbol.upper(),
            "price": round(price, 2),
            "signal": "none",
            "interpretation": "Could not compute delta-adjusted OI — check symbol or options availability.",
        }

    net_daoi = total_call_daoi + total_put_daoi

    # ------------------------------------------------------------------ #
    # Delta flip strike — nearest strike where net DAOI crosses zero
    # ------------------------------------------------------------------ #
    sorted_strikes = sorted(strike_net_daoi.keys())

    delta_flip_strike = None
    min_abs_net = float("inf")
    for k in sorted_strikes:
        abs_net = abs(strike_net_daoi[k])
        if abs_net < min_abs_net:
            min_abs_net       = abs_net
            delta_flip_strike = k

    # Also find where cumulative net DAOI changes sign (true crossing)
    flip_crossing = None
    cum = 0.0
    for k in sorted_strikes:
        prev = cum
        cum += strike_net_daoi[k]
        if prev * cum < 0:   # sign change
            flip_crossing = round(k, 2)
            break

    # ------------------------------------------------------------------ #
    # Gamma wall — strike with highest |delta × OI| (most hedging activity)
    # ------------------------------------------------------------------ #
    gamma_wall = max(strike_abs_daoi, key=strike_abs_daoi.get) if strike_abs_daoi else None

    # ------------------------------------------------------------------ #
    # Market maker hedge bias
    # ------------------------------------------------------------------ #
    # MM net delta = negative of their customer book delta
    # If customers net bought calls (positive customer delta) → MM short delta
    # → MM must BUY stock as price rises to hedge
    mm_net_delta = -net_daoi
    if mm_net_delta < 0:
        mm_hedge_bias = "sell_on_rally"   # MM long delta, sells as price rises
        mm_note       = "MM are net LONG delta — they sell stock on rallies (resistance)"
    else:
        mm_hedge_bias = "buy_on_rally"    # MM short delta, buys as price rises
        mm_note       = "MM are net SHORT delta — they buy stock on rallies (support / amplifies bounce)"

    dist_to_flip_pct = None
    if delta_flip_strike:
        dist_to_flip_pct = round((delta_flip_strike - price) / price * 100, 2)

    # ------------------------------------------------------------------ #
    # Bounce signal
    # ------------------------------------------------------------------ #
    # Strong bounce: MM short delta (buy_on_rally) + price below flip + large magnitude
    magnitude = abs(net_daoi)
    near_flip  = dist_to_flip_pct is not None and abs(dist_to_flip_pct) <= 5.0

    if mm_hedge_bias == "buy_on_rally" and near_flip and magnitude > 10_000:
        signal = "strong"
        signal_note = (
            "MM are net short delta and price is within 5% of the delta flip. "
            "Mechanical buy pressure will amplify any bounce."
        )
    elif mm_hedge_bias == "buy_on_rally" and magnitude > 5_000:
        signal = "moderate"
        signal_note = (
            "MM net short delta — hedging flows will support a rally, "
            "but price is not yet near the delta flip level."
        )
    elif mm_hedge_bias == "buy_on_rally":
        signal = "weak"
        signal_note = "MM net short delta but magnitude is small — limited mechanical support."
    else:
        signal = "none"
        signal_note = "MM net long delta — selling pressure on rallies. Not a bounce setup."

    return {
        "symbol":              symbol.upper(),
        "price":               round(price, 2),
        "expirations_scanned": [s["expiration"] for s in expiry_summaries],
        "net_daoi_shares":     round(net_daoi, 0),
        "call_daoi_shares":    round(total_call_daoi, 0),
        "put_daoi_shares":     round(total_put_daoi, 0),
        "mm_hedge_bias":       mm_hedge_bias,
        "mm_note":             mm_note,
        "delta_flip_strike":   delta_flip_strike,
        "delta_flip_crossing": flip_crossing,
        "dist_to_flip_pct":    dist_to_flip_pct,
        "gamma_wall_strike":   gamma_wall,
        "signal":              signal,
        "signal_note":         signal_note,
        "by_expiration":       expiry_summaries,
    }

@mcp.tool()
def get_historical_drawdown(
    symbol: str,
    lookback_days: int = 252,
) -> dict:
    """Calculate historical max drawdown metrics to calibrate trailing stop loss orders.

    Computes the worst single-day and worst 5-day close-to-close drawdowns over
    the lookback period, then averages them to produce a recommended trailing stop
    percentage.  This answers the key stop-loss calibration question: "How wide
    does a stop need to be to survive normal volatility without false-triggering?"

    A fixed technical stop set CLOSER than the max 1-day drawdown will be triggered
    by routine market noise rather than a genuine breakdown.  Use trailing_stop_pct
    as the minimum safe distance when placing a trailing stop order with a broker.

    Metrics returned
    ----------------
    max_1day_drawdown_pct  — worst single close-to-close decline over the period
    max_5day_drawdown_pct  — worst rolling 5-bar close-to-close decline
    avg_drawdown_pct       — average of the two → minimum trailing stop distance
    trailing_stop_pct      — same value, formatted as a positive % for broker input
    max_intraday_drop_pct  — worst high-to-low % within a single session (gap/halt risk)
    recent_max_1day_pct    — worst 1-day drop in the last 30 bars (current volatility regime)
    stop_validation        — assessment of whether a given stop distance is adequate
    worst_1day_date        — when the worst single-day drop occurred
    worst_5day_date_range  — start/end of the worst 5-day window

    Args:
        symbol:        Stock ticker symbol (e.g. 'AAPL')
        lookback_days: Trading days to analyse (default: 252 ≈ 1 year)
    """
    # Request enough calendar days to cover the trading-day lookback.
    # ~252 trading days ≈ 365 calendar days; use 2× to be safe with the cache.
    calendar_days = max(lookback_days * 2, 365)
    hist = get_history(symbol.upper(), "1d", calendar_days).copy()

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


@mcp.tool()
def get_stop_loss_analysis(
    symbol: str,
    cost_basis: float = 0.0,
    shares: int = 0,
    max_expirations: int = 4,
) -> dict:
    """Synthesise a complete stop loss recommendation combining options/technical analysis
    and historical drawdown calibration.

    Runs seven sub-analyses in sequence — price/BB, VWAP, MACD, RSI, delta-adjusted OI
    (gamma wall), historical drawdown, and short interest — then produces two stops:

      technical_stop  — the conceptual floor derived from the nearest meaningful support
                        level (gamma wall > VWAP > 20-day SMA > lower BB), with a small
                        buffer below it.  Watch this level manually; it tells you when the
                        thesis is breaking.

      trailing_stop   — a percentage-based stop calibrated to the stock's historical
                        noise floor (avg of max 1-day and max 5-day drawdowns), adjusted
                        for short interest dynamics.  Place this as the automated order
                        in your broker so it trails up as the stock rises.

    The tool also flags when the technical stop falls inside the historical noise floor
    (meaning it would false-trigger on a single bad session) and computes position-level
    P&L at each stop level when cost_basis and shares are provided.

    Args:
        symbol:          Stock ticker symbol (e.g. 'AAPL')
        cost_basis:      Average cost per share (optional — enables P&L output)
        shares:          Number of shares held (optional — enables total P&L output)
        max_expirations: Options expirations to scan for gamma wall (default: 4)
    """
    sym = symbol.upper()

    # ── 1. Price + Bollinger Bands ────────────────────────────────────────
    price_data = get_stock_price(sym)
    price      = price_data["price"]
    bb         = price_data["bollinger_bands"]
    bb_upper   = bb["upper"]
    bb_middle  = bb["middle"]   # 20-day SMA
    bb_lower   = bb["lower"]

    # ── 2. VWAP ──────────────────────────────────────────────────────────
    vwap_data  = get_vwap(sym)
    vwap       = vwap_data["vwap"]
    above_vwap = vwap_data["position"] == "above_vwap"
    vwap_bars  = vwap_data.get(
        "consecutive_bars_above" if above_vwap else "consecutive_bars_below", 0
    )

    # ── 3. Momentum ───────────────────────────────────────────────────────
    macd_data  = get_macd(sym)
    macd_cross = macd_data["crossover"]

    rsi_data   = get_rsi(sym)
    rsi        = rsi_data["rsi"]

    # ── 4. Options — gamma wall ───────────────────────────────────────────
    gamma_wall = None
    try:
        daoi_data  = get_delta_adjusted_oi(sym, max_expirations=max_expirations)
        gamma_wall = daoi_data.get("gamma_wall_strike")
    except Exception:
        pass

    # ── 5. Historical drawdown ────────────────────────────────────────────
    dd               = get_historical_drawdown(sym)
    base_trailing    = dd["trailing_stop_pct"]
    max_1day_pct     = abs(dd["max_1day_drawdown_pct"])
    max_5day_pct     = abs(dd["max_5day_drawdown_pct"])
    max_intraday_pct = abs(dd["max_intraday_drop_pct"])
    recent_1day_pct  = abs(dd["recent_max_1day_pct"])

    # ── 6. Short interest ─────────────────────────────────────────────────
    short_float_pct = 0.0
    short_ratio     = 0.0
    short_available = False
    try:
        t_info          = yf.Ticker(sym).info
        raw_si          = float(t_info.get("shortPercentOfFloat") or 0)
        short_float_pct = round(raw_si * 100 if raw_si <= 1.0 else raw_si, 2)
        short_ratio     = round(float(t_info.get("shortRatio") or 0), 2)
        short_available = True
    except Exception:
        pass

    if short_float_pct >= 20 and short_ratio >= 5:
        squeeze = "HIGH"
    elif short_float_pct >= 10 or short_ratio >= 3:
        squeeze = "MEDIUM"
    else:
        squeeze = "LOW"

    # ── 7. Support levels below current price ─────────────────────────────
    # Gamma wall takes priority; VWAP only if price is above it (otherwise
    # VWAP is resistance); then 20-day SMA; then lower BB as last resort.
    supports = {}
    if gamma_wall and float(gamma_wall) < price:
        supports["gamma_wall"] = float(gamma_wall)
    if vwap < price:
        supports["vwap"] = float(vwap)
    if bb_middle < price:
        supports["sma_20"] = float(bb_middle)
    if bb_lower < price:
        supports["bb_lower"] = float(bb_lower)

    sorted_supports = sorted(supports.items(), key=lambda x: x[1], reverse=True)

    # Buffer below primary support — tighter for options-defined levels,
    # slightly wider for statistical levels that get tested more frequently.
    _buf = {"gamma_wall": 0.008, "vwap": 0.014, "sma_20": 0.014, "bb_lower": 0.020}

    if sorted_supports:
        primary_name, primary_level = sorted_supports[0]
        technical_stop = round(primary_level * (1 - _buf.get(primary_name, 0.014)), 2)
    else:
        primary_name   = "bb_lower"
        primary_level  = bb_lower
        technical_stop = round(bb_lower * 0.980, 2)

    technical_dist_pct = round((technical_stop - price) / price * 100, 2)
    stop_inside_noise  = abs(technical_dist_pct) < max_1day_pct

    # ── 8. Trailing stop — calibrate and adjust for short interest ─────────
    if short_float_pct >= 15 and not above_vwap:
        # High SI in a downtrend: shorts add selling pressure on breakdown
        adj_trailing = round(base_trailing * 0.90, 2)
        si_impact    = "tightened — high short float in downtrend adds breakdown pressure"
    elif short_float_pct >= 20 and above_vwap:
        # High SI in an uptrend: squeeze cushions pullbacks
        adj_trailing = round(base_trailing * 1.10, 2)
        si_impact    = "widened — high short float with upward momentum adds squeeze cushion"
    else:
        adj_trailing = base_trailing
        si_impact    = "neutral"

    trailing_stop_price = round(price * (1 - adj_trailing / 100), 2)

    # ── 9. Position P&L at each stop ──────────────────────────────────────
    position = {}
    if cost_basis > 0:
        pnl_per_share = round(price - cost_basis, 2)
        pnl_pct       = round((price - cost_basis) / cost_basis * 100, 2)
        n             = shares if shares > 0 else 1
        position = {
            "cost_basis":               round(cost_basis, 2),
            "shares":                   shares,
            "unrealized_pnl_per_share": pnl_per_share,
            "unrealized_pnl_pct":       pnl_pct,
            "total_unrealized_pnl":     round(pnl_per_share * n, 2),
            "pnl_at_technical_stop":    round((technical_stop - cost_basis) * n, 2),
            "pnl_at_trailing_stop":     round((trailing_stop_price - cost_basis) * n, 2),
        }

    # ── 10. Flags ──────────────────────────────────────────────────────────
    flags = []
    flags.append(f"{vwap_bars}_bars_{'above' if above_vwap else 'below'}_vwap")
    flags.append(
        "bearish_macd"          if "bearish" in macd_cross else
        "bullish_macd_crossover" if macd_cross == "bullish_crossover" else
        "bullish_macd"
    )
    if rsi < 35:
        flags.append(f"rsi_oversold_{rsi:.0f}")
    elif rsi > 70:
        flags.append(f"rsi_overbought_{rsi:.0f}")
    if stop_inside_noise:
        flags.append("technical_stop_inside_noise_floor")
    if squeeze == "HIGH":
        flags.append("squeeze_potential_high")
    elif squeeze == "MEDIUM":
        flags.append("squeeze_potential_medium")
    if recent_1day_pct > max_1day_pct * 1.4:
        flags.append("elevated_recent_volatility")

    # ── 11. Human-readable summary ─────────────────────────────────────────
    trend = "uptrend" if above_vwap else "downtrend"
    mom   = "bullish" if "bullish" in macd_cross else "bearish"
    lines = [
        f"{sym} at ${price:.2f} — {trend} "
        f"({vwap_bars} bars {'above' if above_vwap else 'below'} VWAP ${vwap:.2f}).",
        f"Momentum: MACD {mom}, RSI {rsi:.0f}.",
        f"Primary support: {primary_name.replace('_', ' ')} at ${primary_level:.2f}.",
        f"Technical stop: ${technical_stop:.2f} ({technical_dist_pct:.1f}% from price).",
    ]
    if stop_inside_noise:
        lines.append(
            f"WARNING — technical stop is inside the {max_1day_pct:.1f}% "
            "historical 1-day noise floor and will false-trigger on a bad session."
        )
    lines.append(
        f"Broker trailing stop: {adj_trailing:.1f}% → ${trailing_stop_price:.2f}."
    )
    if cost_basis > 0 and shares > 0:
        lines.append(
            f"Position: {shares} shares @ ${cost_basis:.2f}. "
            f"P&L at trailing stop: ${position['pnl_at_trailing_stop']:+.2f}."
        )

    return {
        "symbol":   sym,
        "price":    price,
        "position": position,
        "technical": {
            "above_vwap":            above_vwap,
            "vwap":                  round(float(vwap), 2),
            "consecutive_bars":      vwap_bars,
            "vwap_direction":        "above" if above_vwap else "below",
            "sma_20":                round(bb_middle, 2),
            "bb_upper":              round(bb_upper, 2),
            "bb_lower":              round(bb_lower, 2),
            "rsi":                   round(rsi, 1),
            "macd_crossover":        macd_cross,
            "gamma_wall":            gamma_wall,
            "primary_support":       primary_name,
            "primary_support_price": round(primary_level, 2),
            "support_levels":        {k: round(v, 2) for k, v in sorted_supports},
        },
        "short_interest": {
            "short_float_pct":   short_float_pct,
            "short_ratio_days":  short_ratio,
            "squeeze_potential": squeeze,
            "stop_impact":       si_impact,
            "data_available":    short_available,
        },
        "drawdown": {
            "max_1day_pct":           -round(max_1day_pct, 2),
            "max_5day_pct":           -round(max_5day_pct, 2),
            "max_intraday_pct":       -round(max_intraday_pct, 2),
            "recent_max_1day_pct":    -round(recent_1day_pct, 2),
            "base_trailing_stop_pct": base_trailing,
        },
        "stops": {
            "technical_stop":                    technical_stop,
            "technical_stop_distance_pct":       technical_dist_pct,
            "technical_stop_inside_noise_floor":  stop_inside_noise,
            "trailing_stop_pct":                 adj_trailing,
            "trailing_stop_price":               trailing_stop_price,
        },
        "flags":   flags,
        "summary": " ".join(lines),
    }

if __name__ == "__main__":
    mcp.run()
