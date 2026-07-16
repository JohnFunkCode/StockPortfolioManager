"""Pure technical-indicator functions — pandas Series in, Series/float out.

No I/O, no network, no database. Shared by PricesService for the REST
technicals table and the securities screener (single home for the RSI/MACD
math that previously had a third copy inside api/app.py).
"""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd


def safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (TypeError, ValueError):
        return None


def rsi_series(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def macd_series(closes: pd.Series):
    ema12 = closes.ewm(span=12, min_periods=12).mean()
    ema26 = closes.ewm(span=26, min_periods=26).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, min_periods=9).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def true_range_series(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range: max(H−L, |H−prev C|, |L−prev C|); first bar has no prior close → H−L."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    tr.iloc[0] = float(high.iloc[0] - low.iloc[0])
    return tr


def atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder ATR: recursive smoothing ATR_t = ATR_{t-1} + (TR_t − ATR_{t-1})/period.

    Seed convention: ewm(alpha=1/period, adjust=False) seeds from the first TR
    value (not an SMA of the first `period` TRs), so early values converge to
    Wilder's within a few periods.
    """
    tr = true_range_series(high, low, close)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def anchored_vwap(df: pd.DataFrame, anchor_idx: int) -> Optional[float]:
    """Volume-weighted average of typical price (H+L+C)/3 from anchor_idx to the last bar."""
    window = df.iloc[anchor_idx:]
    typical = (window["High"] + window["Low"] + window["Close"]) / 3
    total_volume = float(window["Volume"].sum())
    if total_volume <= 0:
        return None
    return float((typical * window["Volume"]).sum() / total_volume)


def find_swings(highs: pd.Series, lows: pd.Series, swing_bars: int = 3) -> dict:
    """Confirmed swing pivots: positional indices of local extremes.

    A swing low at i requires low[i] <= low[i±k] for k in 1..swing_bars (the
    `<=` semantics match PricesService.get_higher_lows); a swing high requires
    high[i] >= high[i±k]. The last `swing_bars` bars are skipped — they cannot
    be confirmed until enough right-side bars complete.
    """
    swing_lows: list[int] = []
    swing_highs: list[int] = []
    scan_end = len(lows) - swing_bars
    for i in range(swing_bars, scan_end):
        l = float(lows.iloc[i])
        if all(l <= float(lows.iloc[i - k]) for k in range(1, swing_bars + 1)) and all(
            l <= float(lows.iloc[i + k]) for k in range(1, swing_bars + 1)
        ):
            swing_lows.append(i)
        h = float(highs.iloc[i])
        if all(h >= float(highs.iloc[i - k]) for k in range(1, swing_bars + 1)) and all(
            h >= float(highs.iloc[i + k]) for k in range(1, swing_bars + 1)
        ):
            swing_highs.append(i)
    return {"lows": swing_lows, "highs": swing_highs}
