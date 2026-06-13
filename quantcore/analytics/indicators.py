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
