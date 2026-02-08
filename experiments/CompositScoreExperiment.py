from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple, Any, List

import numpy as np
import pandas as pd
import yfinance as yf

try:
    import yaml  # PyYAML
except Exception:  # pragma: no cover
    yaml = None


DEFAULT_SYMBOLS = ["NVDA", "CAT", "GLW", "WDC", "GOOGL", "AAPL", "QCOM", "GEV", "TER"]
# WATCHLIST_YAML_PATH = "/Users/johnfunk/Documents/code/StockPortfolioManager/watchlist.yaml"
WATCHLIST_YAML_PATH = "none"


# Default weights (sum to 1.00)
WEIGHTS = {
    "RevCAGR3Y": 0.20,
    "RevAccel": 0.10,
    "QoQVol4": 0.05,          # penalty (we invert via z-score sign flip)
    "OpMargin3Y": 0.15,
    "OpMarginTrend": 0.10,
    "FCFMargin3Y": 0.10,
    "ValMetric": 0.20,        # penalty (we invert via z-score sign flip)
    "Mom12_1": 0.10,
}

# Which metrics are "lower is better"
PENALTY_METRICS = {"QoQVol4", "ValMetric"}


def _to_numeric_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _zscore(series: pd.Series) -> pd.Series:
    """
    Robust z-score: if insufficient data or std==0, return zeros (not NaN).
    """
    x = pd.to_numeric(series, errors="coerce")
    valid = x.dropna()
    if len(valid) < 3:
        return pd.Series(0.0, index=series.index)
    mu = valid.mean()
    sd = valid.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return pd.Series(0.0, index=series.index)
    return (x - mu) / sd


def _get_annual_revenue_and_operating_income(t: yf.Ticker) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    """
    Returns annual revenue and operating income series sorted oldest->newest.
    Uses yfinance .financials (annual income statement).
    """
    fin = t.financials
    if fin is None or fin.empty:
        return None, None

    rev = fin.loc["Total Revenue"] if "Total Revenue" in fin.index else None
    op = fin.loc["Operating Income"] if "Operating Income" in fin.index else None

    if rev is None:
        return None, None

    rev = _to_numeric_series(rev).dropna().sort_index()
    op = _to_numeric_series(op).dropna().sort_index() if op is not None else None

    if rev.empty:
        return None, None

    return rev.astype(float), (op.astype(float) if op is not None and not op.empty else None)


def _get_annual_cfo_and_capex(t: yf.Ticker) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    """
    Returns annual CFO and Capex series sorted oldest->newest.
    Uses yfinance .cashflow (annual cashflow statement).
    """
    cf = t.cashflow
    if cf is None or cf.empty:
        return None, None

    cfo = None
    for label in ["Total Cash From Operating Activities", "Operating Cash Flow"]:
        if label in cf.index:
            cfo = cf.loc[label]
            break

    capex = None
    for label in ["Capital Expenditures", "Capital Expenditure"]:
        if label in cf.index:
            capex = cf.loc[label]
            break

    if cfo is None or capex is None:
        return None, None

    cfo = _to_numeric_series(cfo).dropna().sort_index().astype(float)
    capex = _to_numeric_series(capex).dropna().sort_index().astype(float)

    if cfo.empty or capex.empty:
        return None, None

    return cfo, capex


def _get_quarterly_revenue(t: yf.Ticker) -> Optional[pd.Series]:
    """
    Returns quarterly revenue sorted oldest->newest.
    Uses yfinance .quarterly_financials.
    """
    qf = t.quarterly_financials
    if qf is None or qf.empty or "Total Revenue" not in qf.index:
        return None

    qrev = _to_numeric_series(qf.loc["Total Revenue"]).dropna().sort_index().astype(float)
    if qrev.empty:
        return None
    return qrev


def _rev_cagr_3y(annual_rev: pd.Series) -> float:
    if annual_rev is None or len(annual_rev) < 4:
        return np.nan
    r_now = float(annual_rev.iloc[-1])
    r_then = float(annual_rev.iloc[-4])
    if r_then <= 0:
        return np.nan
    return (r_now / r_then) ** (1 / 3) - 1


def _rev_accel(annual_rev: pd.Series) -> float:
    if annual_rev is None or len(annual_rev) < 3:
        return np.nan
    r0, r1, r2 = float(annual_rev.iloc[-1]), float(annual_rev.iloc[-2]), float(annual_rev.iloc[-3])
    if r1 <= 0 or r2 <= 0:
        return np.nan
    yoy_t = r0 / r1 - 1
    yoy_prev = r1 / r2 - 1
    return yoy_t - yoy_prev


def _qoq_vol_4(qrev: pd.Series) -> float:
    """
    Std dev of last 4 quarterly QoQ revenue growth rates (requires 5 quarters of revenue).
    """
    if qrev is None or len(qrev) < 5:
        return np.nan
    growth = qrev.pct_change(fill_method=None).dropna()
    if len(growth) < 4:
        return np.nan
    last4 = growth.iloc[-4:].to_numpy(dtype=float)
    return float(np.std(last4, ddof=0))


def _op_margin_3y_and_trend(annual_rev: pd.Series, annual_op: Optional[pd.Series]) -> Tuple[float, float]:
    if annual_rev is None or annual_op is None:
        return np.nan, np.nan

    df = pd.DataFrame({"rev": annual_rev, "op": annual_op}).dropna().sort_index()
    if len(df) < 3:
        return np.nan, np.nan

    df["om"] = df["op"] / df["rev"]
    om = df["om"]

    om3 = float(om.iloc[-3:].mean())
    trend = float(om.iloc[-1] - om.iloc[-3:-1].mean())
    return om3, trend


def _fcf_margin_3y(annual_rev: pd.Series, cfo: Optional[pd.Series], capex: Optional[pd.Series]) -> float:
    if annual_rev is None or cfo is None or capex is None:
        return np.nan

    df = pd.DataFrame({"rev": annual_rev, "cfo": cfo, "capex": capex}).dropna().sort_index()
    if len(df) < 3:
        return np.nan

    # FCF = CFO - Capex (capex is typically negative; subtracting increases FCF appropriately)
    df["fcf"] = df["cfo"] - df["capex"]
    df["fcf_margin"] = df["fcf"] / df["rev"]
    return float(df["fcf_margin"].iloc[-3:].mean())


def _valuation_metric(info: Dict[str, Any]) -> Tuple[float, str]:
    """
    Returns (log multiple, label). Lower is better.
    Prefers EV/Sales, falls back to trailing PE.
    """
    evs = info.get("enterpriseToRevenue", None)
    if isinstance(evs, (int, float)) and evs > 0:
        return float(np.log(evs)), "EV/Sales"

    pe = info.get("trailingPE", None)
    if isinstance(pe, (int, float)) and pe > 0:
        return float(np.log(pe)), "P/E"

    return np.nan, "NA"


def _mom_12_1(t: yf.Ticker) -> float:
    """
    12-1 momentum: approx P[-21]/P[-252]-1 using adjusted close.
    """
    hist = t.history(period="400d", auto_adjust=True)
    if hist is None or hist.empty or "Close" not in hist.columns:
        return np.nan

    close = hist["Close"].dropna()
    if len(close) < 260:
        return np.nan

    p_252 = float(close.iloc[-252])
    p_21 = float(close.iloc[-21])
    if p_252 <= 0:
        return np.nan
    return p_21 / p_252 - 1


def load_symbols_from_watchlist_yaml(path: str) -> List[str]:
    """
    Load symbols from a YAML watchlist file (list of dicts with a 'symbol' key).
    This does NOT modify the YAML; it only reads it.
    """
    if yaml is None:
        raise RuntimeError("PyYAML is not installed. Install it with: pip install pyyaml")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected a YAML list at top-level in {path}")

    symbols: List[str] = []
    for item in data:
        if isinstance(item, dict):
            sym = item.get("symbol")
            if isinstance(sym, str) and sym.strip():
                symbols.append(sym.strip())

    # Preserve order but de-duplicate
    seen = set()
    out: List[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def print_legend() -> None:
    legend = [
        "Legend (all % values are in decimal form; e.g., 0.25 = 25%):",
        "  RevCAGR3Y       : 3-year revenue CAGR from annual Total Revenue.",
        "  RevAccel        : Revenue acceleration = (YoY revenue growth this year) - (YoY revenue growth prior year).",
        "  QoQVol4         : Std dev of last 4 quarterly revenue QoQ growth rates (requires 5 quarters; lower is better; penalty).",
        "  OpMargin3Y      : 3-year average Operating Income / Total Revenue.",
        "  OpMarginTrend   : Last-year operating margin minus average of prior 2 years.",
        "  FCFMargin3Y     : 3-year average (Operating Cash Flow - Capex) / Total Revenue.",
        "  ValMetric       : log(EV/Sales) if available else log(Trailing P/E) (lower is better; penalty).",
        "  ValType         : Which valuation multiple was used (EV/Sales, P/E, or NA).",
        "  Mom12_1         : 12-1 momentum = price return from ~252 trading days ago to ~21 trading days ago.",
        "  Z_*             : Cross-sectional z-score of the metric across the universe (penalties inverted so higher is better).",
        "  Score           : Weighted average of available Z_* metrics (weights re-normalized per ticker when data is missing).",
        "  Coverage        : Sum of weights actually used for that ticker (0–1).",
        "",
        "Notes:",
        "  - yfinance fundamentals can be missing/inconsistent across tickers.",
        "  - Z-scores are computed across your current watchlist universe (not sector-neutralized).",
    ]
    print("\n" + "\n".join(legend) + "\n")


def compute_fundamental_rank(symbols: Iterable[str], weights: Dict[str, float] = WEIGHTS) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []

    for sym in symbols:
        t = yf.Ticker(sym)
        try:
            info = t.info or {}
        except Exception:
            info = {}

        sector = info.get("sector", None)
        market_cap = info.get("marketCap", None)

        annual_rev, annual_op = _get_annual_revenue_and_operating_income(t)
        cfo, capex = _get_annual_cfo_and_capex(t)
        qrev = _get_quarterly_revenue(t)

        rev_cagr3y = _rev_cagr_3y(annual_rev) if annual_rev is not None else np.nan
        rev_accel = _rev_accel(annual_rev) if annual_rev is not None else np.nan
        qoq_vol4 = _qoq_vol_4(qrev) if qrev is not None else np.nan

        opm3, opm_trend = _op_margin_3y_and_trend(annual_rev, annual_op)
        fcfm3 = _fcf_margin_3y(annual_rev, cfo, capex)

        val_metric, val_type = _valuation_metric(info)
        mom = _mom_12_1(t)

        rows.append(
            {
                "Symbol": sym,
                "Sector": sector,
                "MarketCap": market_cap,
                "RevCAGR3Y": rev_cagr3y,
                "RevAccel": rev_accel,
                "QoQVol4": qoq_vol4,
                "OpMargin3Y": opm3,
                "OpMarginTrend": opm_trend,
                "FCFMargin3Y": fcfm3,
                "ValMetric": val_metric,
                "ValType": val_type,
                "Mom12_1": mom,
            }
        )

    df = pd.DataFrame(rows).set_index("Symbol")

    # Z-scores across the universe
    zcols: Dict[str, pd.Series] = {}
    for col in weights.keys():
        z = _zscore(df[col])
        if col in PENALTY_METRICS:
            z = -z
        zcols[f"Z_{col}"] = z

    zdf = pd.DataFrame(zcols, index=df.index)

    # Weighted score with missing-data reweighting
    available = df[list(weights.keys())].notna()

    num = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)

    for metric, weight in weights.items():
        zcol = f"Z_{metric}"
        mask = available[metric]
        num[mask] += weight * zdf.loc[mask, zcol]
        den[mask] += weight

    score = num / den.replace({0.0: np.nan})
    coverage = den

    out = df.join(zdf)
    out["Score"] = score
    out["Coverage"] = coverage

    out = out.reset_index().sort_values(by="Score", ascending=False, na_position="last")

    # Score is 4th column after Symbol, Sector, MarketCap
    preferred = ["Symbol", "Sector", "MarketCap", "Score", "Coverage"]
    remaining = [c for c in out.columns if c not in preferred]
    out = out[preferred + remaining]

    # Display rounding only (keeps run stable / readable)
    numeric_cols = out.select_dtypes(include=[np.number]).columns
    out[numeric_cols] = out[numeric_cols].round(4)

    return out


if __name__ == "__main__":
    try:
        symbols = load_symbols_from_watchlist_yaml(WATCHLIST_YAML_PATH)
        if not symbols:
            symbols = DEFAULT_SYMBOLS
    except Exception as e:
        print(f"Warning: could not load {WATCHLIST_YAML_PATH}: {e}")
        symbols = DEFAULT_SYMBOLS

    print_legend()

    ranked = compute_fundamental_rank(symbols)
    print("Fundamental Rank (yfinance-only):\n")

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 50)
    pd.set_option("display.max_rows", 200)

    print(ranked.to_string(index=False))