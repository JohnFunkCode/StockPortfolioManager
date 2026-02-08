from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Dict, Any, List

import numpy as np
import pandas as pd
import yfinance as yf


SYMBOLS = ["NVDA", "CAT", "GLW", "WDC", "GOOGL", "AAPL", "QCOM", "GEV", "TER"]


@dataclass(frozen=True)
class ScoreResult:
    symbol: str
    weighted_qoq_score: float
    quarters_used: int
    growth_rates: List[float]  # last N QoQ growth rates used


def get_quarterly_revenue(symbol: str) -> Optional[pd.Series]:
    """
    Fetch quarterly Total Revenue from yfinance, return as float Series sorted oldest->newest.
    Returns None if missing.
    """
    t = yf.Ticker(symbol)
    df = t.quarterly_financials  # columns are quarter end dates, index contains line items

    if df is None or df.empty or "Total Revenue" not in df.index:
        return None

    rev = df.loc["Total Revenue"]

    # Make sure it's numeric and clean (avoid object dtype warnings downstream)
    rev = pd.to_numeric(rev, errors="coerce").dropna()

    # Ensure chronological order
    rev = rev.sort_index()

    # If it’s still empty after coercion
    if rev.empty:
        return None

    return rev.astype(float)


def compute_weighted_score(revenue: pd.Series, periods: int = 4) -> ScoreResult:
    """
    Weighted score over last `periods` QoQ changes:
        sum(max(0, r_i)) / sum(|r_i|)
    where r_i are pct changes quarter-to-quarter.
    """
    symbol = getattr(revenue, "name", "UNKNOWN")

    # Need periods+1 revenue points to compute `periods` growth rates
    if revenue is None or len(revenue) < periods + 1:
        return ScoreResult(symbol=symbol, weighted_qoq_score=np.nan, quarters_used=0, growth_rates=[])

    recent = revenue.iloc[-(periods + 1):].copy()

    # No forward-filling; explicitly compute pct_change without implicit padding
    growth = recent.pct_change(fill_method=None)

    # Drop any NA growth points (e.g., first element)
    growth = growth.dropna()

    if growth.empty:
        return ScoreResult(symbol=symbol, weighted_qoq_score=np.nan, quarters_used=0, growth_rates=[])

    rates = growth.to_numpy(dtype=float)

    num = np.sum(np.maximum(0.0, rates))
    den = np.sum(np.abs(rates))

    if den == 0.0:
        score = 0.0
    else:
        score = float(num / den)

    return ScoreResult(
        symbol=symbol,
        weighted_qoq_score=score,
        quarters_used=int(len(rates)),
        growth_rates=[float(x) for x in rates],
    )


def calculate_scores(symbols: Iterable[str], periods: int = 4) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []

    for sym in symbols:
        rev = get_quarterly_revenue(sym)
        if rev is None:
            rows.append(
                {"Symbol": sym, "Weighted_QoQ_Score": np.nan, "QuartersUsed": 0, "GrowthRates": []}
            )
            continue

        # stash symbol in series name for better defaults
        rev.name = sym

        res = compute_weighted_score(rev, periods=periods)
        rows.append(
            {
                "Symbol": sym,
                "Weighted_QoQ_Score": round(res.weighted_qoq_score, 4)
                if not np.isnan(res.weighted_qoq_score)
                else np.nan,
                "QuartersUsed": res.quarters_used,
                "GrowthRates": [round(x, 4) for x in res.growth_rates],
            }
        )

    df = pd.DataFrame(rows).sort_values(by="Weighted_QoQ_Score", ascending=False, na_position="last")
    return df.reset_index(drop=True)


if __name__ == "__main__":
    df = calculate_scores(SYMBOLS, periods=4)
    print("\nWeighted Sequential Growth Score (Last 4 Quarters):\n")
    print(df.to_string(index=False))