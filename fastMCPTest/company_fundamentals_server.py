from __future__ import annotations

import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from fastmcp import FastMCP

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parent
for path in (PROJECT_ROOT, MCP_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from fundamentals_cache import (
    cache_get,
    cache_set,
    cache_history,
    cache_get_all_latest,
    cache_stats,
    _get_ttl_seconds,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

mcp = FastMCP("company-fundamentals-server")

# ---------------------------------------------------------------------------
# Internal helpers — lifted from experiments/CompositScoreExperiment.py
# ---------------------------------------------------------------------------

def _to_numeric_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _get_annual_revenue_and_operating_income(
    t: yf.Ticker,
) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
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


def _get_annual_cfo_and_capex(
    t: yf.Ticker,
) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    cf = t.cashflow
    if cf is None or cf.empty:
        return None, None
    cfo = None
    for label in ("Total Cash From Operating Activities", "Operating Cash Flow"):
        if label in cf.index:
            cfo = cf.loc[label]
            break
    capex = None
    for label in ("Capital Expenditures", "Capital Expenditure"):
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
    qf = t.quarterly_financials
    if qf is None or qf.empty or "Total Revenue" not in qf.index:
        return None
    qrev = _to_numeric_series(qf.loc["Total Revenue"]).dropna().sort_index().astype(float)
    return qrev if not qrev.empty else None


def _rev_cagr_3y(annual_rev: Optional[pd.Series]) -> Optional[float]:
    if annual_rev is None or len(annual_rev) < 4:
        return None
    r_now = float(annual_rev.iloc[-1])
    r_then = float(annual_rev.iloc[-4])
    if r_then <= 0:
        return None
    return (r_now / r_then) ** (1 / 3) - 1


def _rev_accel(annual_rev: Optional[pd.Series]) -> Optional[float]:
    if annual_rev is None or len(annual_rev) < 3:
        return None
    r0, r1, r2 = (
        float(annual_rev.iloc[-1]),
        float(annual_rev.iloc[-2]),
        float(annual_rev.iloc[-3]),
    )
    if r1 <= 0 or r2 <= 0:
        return None
    return (r0 / r1 - 1) - (r1 / r2 - 1)


def _qoq_vol_4(qrev: Optional[pd.Series]) -> Optional[float]:
    if qrev is None or len(qrev) < 5:
        return None
    growth = qrev.pct_change(fill_method=None).dropna()
    if len(growth) < 4:
        return None
    return float(np.std(growth.iloc[-4:].to_numpy(dtype=float), ddof=0))


def _op_margin_3y_and_trend(
    annual_rev: Optional[pd.Series],
    annual_op: Optional[pd.Series],
) -> Tuple[Optional[float], Optional[float]]:
    if annual_rev is None or annual_op is None:
        return None, None
    df = pd.DataFrame({"rev": annual_rev, "op": annual_op}).dropna().sort_index()
    if len(df) < 3:
        return None, None
    df["om"] = df["op"] / df["rev"]
    om = df["om"]
    return float(om.iloc[-3:].mean()), float(om.iloc[-1] - om.iloc[-3:-1].mean())


def _fcf_margin_3y(
    annual_rev: Optional[pd.Series],
    cfo: Optional[pd.Series],
    capex: Optional[pd.Series],
) -> Optional[float]:
    if annual_rev is None or cfo is None or capex is None:
        return None
    df = pd.DataFrame({"rev": annual_rev, "cfo": cfo, "capex": capex}).dropna().sort_index()
    if len(df) < 3:
        return None
    df["fcf"] = df["cfo"] - df["capex"]
    df["fcf_margin"] = df["fcf"] / df["rev"]
    return float(df["fcf_margin"].iloc[-3:].mean())


def _valuation_metric(info: dict[str, Any]) -> Tuple[Optional[float], str]:
    evs = info.get("enterpriseToRevenue")
    if isinstance(evs, (int, float)) and evs > 0:
        return float(np.log(evs)), "EV/Sales"
    pe = info.get("trailingPE")
    if isinstance(pe, (int, float)) and pe > 0:
        return float(np.log(pe)), "P/E"
    return None, "NA"


def _mom_12_1(t: yf.Ticker) -> Optional[float]:
    hist = t.history(period="400d", auto_adjust=True)
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None
    close = hist["Close"].dropna()
    if len(close) < 260:
        return None
    p_252 = float(close.iloc[-252])
    p_21 = float(close.iloc[-21])
    if p_252 <= 0:
        return None
    return p_21 / p_252 - 1


def _score_metric(value: Optional[float], metric: str) -> Tuple[int, str]:
    """Score a single fundamental metric on absolute thresholds (-2 to +2)."""
    if value is None:
        return 0, "no data"

    if metric == "RevCAGR3Y":
        if value >= 0.25:
            return 2, f"{value:.1%} (strong)"
        if value >= 0.10:
            return 1, f"{value:.1%} (moderate)"
        if value >= 0:
            return 0, f"{value:.1%} (slow)"
        return -1, f"{value:.1%} (declining)"

    if metric == "RevAccel":
        if value > 0.05:
            return 1, "accelerating"
        if value < -0.05:
            return -1, "decelerating"
        return 0, "stable"

    if metric == "OpMargin3Y":
        if value >= 0.20:
            return 2, f"{value:.1%} (strong)"
        if value >= 0.10:
            return 1, f"{value:.1%} (moderate)"
        if value >= 0:
            return 0, f"{value:.1%} (thin)"
        return -1, f"{value:.1%} (negative)"

    if metric == "OpMarginTrend":
        if value > 0.02:
            return 1, f"{value:+.1%} (expanding)"
        if value < -0.02:
            return -1, f"{value:+.1%} (contracting)"
        return 0, f"{value:+.1%} (flat)"

    if metric == "FCFMargin3Y":
        if value >= 0.15:
            return 2, f"{value:.1%} (strong)"
        if value >= 0.05:
            return 1, f"{value:.1%} (positive)"
        if value >= 0:
            return 0, f"{value:.1%} (minimal)"
        return -1, f"{value:.1%} (burning cash)"

    if metric == "ValMetric":  # log scale, lower is better → invert sign
        if value <= 1.5:
            return 2, f"log={value:.2f} (cheap)"
        if value <= 2.5:
            return 1, f"log={value:.2f} (fair)"
        if value <= 3.5:
            return 0, f"log={value:.2f} (rich)"
        return -1, f"log={value:.2f} (expensive)"

    if metric == "Mom12_1":
        if value >= 0.20:
            return 2, f"{value:.1%} (strong momentum)"
        if value >= 0:
            return 1, f"{value:.1%} (positive)"
        if value >= -0.10:
            return 0, f"{value:.1%} (slightly negative)"
        return -1, f"{value:.1%} (weak momentum)"

    return 0, str(value)


# Earnings acceleration helper — lifted from experiments/EarningsAccelerationExperiment.py
def _compute_earnings_acceleration(
    quarterly_income_stmt: Any,
) -> Tuple:
    if quarterly_income_stmt is None or quarterly_income_stmt.empty:
        return None, None, None, [], []
    if "Net Income" not in quarterly_income_stmt.index:
        return None, None, None, [], []
    incomes = quarterly_income_stmt.loc["Net Income"].dropna()
    if len(incomes) < 5:
        return None, None, None, [], list(incomes.values)
    incomes_slice = incomes.iloc[:5].values[::-1]
    net_incomes = [float(v) for v in incomes_slice]
    qoq_rates: list[Optional[float]] = []
    for i in range(1, 5):
        prev = net_incomes[i - 1]
        curr = net_incomes[i]
        qoq_rates.append(None if prev == 0 else (curr - prev) / abs(prev))
    accel_deltas = [
        qoq_rates[j + 1] - qoq_rates[j]
        for j in range(len(qoq_rates) - 1)
        if qoq_rates[j] is not None and qoq_rates[j + 1] is not None
    ]
    if not accel_deltas:
        return None, None, None, [r for r in qoq_rates if r is not None], net_incomes
    accel_count = sum(1 for d in accel_deltas if d > 0)
    avg_accel_delta = sum(accel_deltas) / len(accel_deltas)
    return accel_count, len(accel_deltas), avg_accel_delta, [r for r in qoq_rates if r is not None], net_incomes


# ---------------------------------------------------------------------------
# Private compute functions — extracted from tools for cache wrapping
# ---------------------------------------------------------------------------


def _compute_earnings_calendar_internal(sym: str) -> dict:
    """Compute earnings calendar data for a symbol (called by cache wrapper)."""
    t = yf.Ticker(sym)

    result: dict[str, Any] = {
        "symbol":                  sym,
        "earnings_date":           None,
        "days_to_earnings":        None,
        "risk_level":              "UNKNOWN",
        "pre_earnings_setup":      False,
        "historical_avg_move_pct": None,
        "note":                    "",
    }

    # ── Next earnings date ────────────────────────────────────────────────
    try:
        cal = t.calendar
        today = date.today()

        if cal is not None:
            if hasattr(cal, "index"):
                raw_dates: list = []
                for label in ("Earnings Date", "Earnings High", "Earnings Low"):
                    if label in cal.index:
                        raw_dates = cal.loc[label].tolist()
                        break
                else:
                    raw_dates = list(cal.columns)
            elif isinstance(cal, dict):
                earn_val = cal.get("Earnings Date")
                raw_dates = earn_val if isinstance(earn_val, list) else ([earn_val] if earn_val else [])
            else:
                raw_dates = []

            future: list[date] = []
            for d in raw_dates:
                if d is None:
                    continue
                if hasattr(d, "date"):
                    d = d.date()
                elif isinstance(d, str):
                    try:
                        d = datetime.strptime(d[:10], "%Y-%m-%d").date()
                    except ValueError:
                        continue
                if isinstance(d, date) and d >= today:
                    future.append(d)

            if future:
                next_date = min(future)
                days_out = (next_date - today).days
                result["earnings_date"] = next_date.isoformat()
                result["days_to_earnings"] = days_out

                if days_out < 7:
                    result["risk_level"] = "CRITICAL"
                    result["note"] = "Earnings within 7 days — avoid new options positions"
                elif days_out < 14:
                    result["risk_level"] = "HIGH"
                    result["note"] = "Earnings blackout zone — IV crush risk post-earnings"
                elif days_out <= 30:
                    result["risk_level"] = "MODERATE"
                    result["pre_earnings_setup"] = True
                    result["note"] = "Pre-earnings zone — IV expansion may benefit long calls"
                else:
                    result["risk_level"] = "LOW"
                    result["note"] = "Earnings > 30 days out — no near-term options risk"
    except Exception as e:
        logger.warning(f"Error fetching earnings calendar for {sym}: {e}")

    # ── Historical earnings-day moves ────────────────────────────────────
    try:
        price_hist = t.history(period="2y", auto_adjust=True)
        if not price_hist.empty and "Close" in price_hist.columns:
            earn_dates = t.earnings_dates
            if earn_dates is not None and not earn_dates.empty:
                close_ser = price_hist["Close"].copy()
                close_ser.index = close_ser.index.normalize()
                moves: list[float] = []
                for ed in earn_dates.index[:4]:
                    try:
                        idx = pd.Timestamp(ed.date() if hasattr(ed, "date") else ed)
                        if idx not in close_ser.index:
                            continue
                        i_pos = close_ser.index.get_loc(idx)
                        if i_pos == 0:
                            continue
                        prev = float(close_ser.iloc[i_pos - 1])
                        curr = float(close_ser.iloc[i_pos])
                        if prev > 0:
                            moves.append(abs(curr / prev - 1))
                    except Exception:
                        continue
                if moves:
                    result["historical_avg_move_pct"] = round(
                        sum(moves) / len(moves) * 100, 2
                    )
    except Exception as e:
        logger.warning(f"Error fetching historical earnings moves for {sym}: {e}")

    return result


def _compute_fundamental_score_internal(sym: str) -> dict:
    """Compute fundamental score for a symbol (called by cache wrapper)."""
    t = yf.Ticker(sym)

    try:
        info: dict[str, Any] = t.info or {}
    except Exception as e:
        logger.warning(f"Error fetching info for {sym}: {e}")
        info = {}

    annual_rev, annual_op = _get_annual_revenue_and_operating_income(t)
    cfo, capex = _get_annual_cfo_and_capex(t)
    qrev = _get_quarterly_revenue(t)

    raw: dict[str, Optional[float]] = {
        "RevCAGR3Y":     _rev_cagr_3y(annual_rev),
        "RevAccel":      _rev_accel(annual_rev),
        "OpMargin3Y":    None,
        "OpMarginTrend": None,
        "FCFMargin3Y":   _fcf_margin_3y(annual_rev, cfo, capex),
        "ValMetric":     None,
        "Mom12_1":       _mom_12_1(t),
    }
    opm3, opm_trend = _op_margin_3y_and_trend(annual_rev, annual_op)
    raw["OpMargin3Y"] = opm3
    raw["OpMarginTrend"] = opm_trend

    val_metric, val_type = _valuation_metric(info)
    raw["ValMetric"] = val_metric

    metric_scores: dict[str, Any] = {}
    total_score = 0
    metrics_with_data = 0

    for name, value in raw.items():
        score, label = _score_metric(value, name)
        metric_scores[name] = {
            "value": round(value, 4) if value is not None else None,
            "score": score,
            "label": label,
        }
        total_score += score
        if value is not None:
            metrics_with_data += 1

    coverage = metrics_with_data / len(raw)

    if total_score >= 8:
        fund_label = "strong_compounder"
    elif total_score >= 4:
        fund_label = "solid"
    elif total_score >= 0:
        fund_label = "average"
    elif total_score >= -3:
        fund_label = "weak"
    else:
        fund_label = "deteriorating"

    return {
        "symbol":            sym,
        "composite_score":   total_score,
        "fundamental_label": fund_label,
        "coverage":          round(coverage, 2),
        "val_type":          val_type,
        "metric_scores":     metric_scores,
        "sector":            info.get("sector"),
        "market_cap":        info.get("marketCap"),
    }


def _compute_revenue_growth_internal(sym: str) -> dict:
    """Compute revenue growth for a symbol (called by cache wrapper)."""
    t = yf.Ticker(sym)

    result: dict[str, Any] = {
        "symbol":              sym,
        "quarterly_revenues":  [],
        "qoq_growth_rates":    [],
        "weighted_score":      None,
        "cagr_3y":             None,
        "rev_accel":           None,
        "trajectory":          "insufficient_data",
    }

    qrev = _get_quarterly_revenue(t)
    if qrev is not None and len(qrev) >= 5:
        recent = qrev.iloc[-5:]
        result["quarterly_revenues"] = [
            {
                "date":    str(idx.date() if hasattr(idx, "date") else idx),
                "revenue": float(v),
            }
            for idx, v in recent.items()
        ]
        growth = recent.pct_change(fill_method=None).dropna()
        rates = growth.to_numpy(dtype=float)
        result["qoq_growth_rates"] = [round(float(r), 4) for r in rates]

        num = float(np.sum(np.maximum(0.0, rates)))
        den = float(np.sum(np.abs(rates)))
        result["weighted_score"] = round(num / den, 4) if den > 0 else 0.0

        if len(rates) >= 2:
            accel = rates[-1] - rates[-2]
            if rates[-1] > 0 and rates[-2] <= 0:
                result["trajectory"] = "inflecting_positive"
            elif rates[-1] <= 0 and rates[-2] > 0:
                result["trajectory"] = "inflecting_negative"
            elif accel > 0.02:
                result["trajectory"] = "accelerating"
            elif accel < -0.02:
                result["trajectory"] = "decelerating"
            else:
                result["trajectory"] = "stable"

    annual_rev, _ = _get_annual_revenue_and_operating_income(t)
    cagr = _rev_cagr_3y(annual_rev)
    if cagr is not None:
        result["cagr_3y"] = round(cagr, 4)
    accel = _rev_accel(annual_rev)
    if accel is not None:
        result["rev_accel"] = round(accel, 4)

    return result


def _compute_earnings_acceleration_tool(sym: str) -> dict:
    """Compute earnings acceleration for a symbol (called by cache wrapper).

    Note: Different from _compute_earnings_acceleration() which is a helper
    that operates on quarterly_income_stmt DataFrame.
    """
    t = yf.Ticker(sym)

    result: dict[str, Any] = {
        "symbol":              sym,
        "net_incomes_M":       [],
        "qoq_rates":           [],
        "accel_count":         None,
        "accel_total":         None,
        "avg_accel_delta":     None,
        "acceleration_label":  "insufficient_data",
        "acceleration_score":  0,
    }

    try:
        stmt = t.quarterly_income_stmt
        accel_count, accel_total, avg_delta, qoq_rates, net_incomes = (
            _compute_earnings_acceleration(stmt)
        )

        if net_incomes:
            result["net_incomes_M"] = [round(v / 1e6, 1) for v in net_incomes]
        if qoq_rates:
            result["qoq_rates"] = [round(r, 4) for r in qoq_rates]

        if accel_count is not None:
            result["accel_count"] = accel_count
            result["accel_total"] = accel_total
            result["avg_accel_delta"] = round(avg_delta, 4)

            if accel_count == accel_total and avg_delta > 0.05:
                result["acceleration_label"] = "strong"
                result["acceleration_score"] = 2
            elif accel_count >= accel_total - 1 and avg_delta > 0:
                result["acceleration_label"] = "moderate"
                result["acceleration_score"] = 1
            elif accel_count == 0 or avg_delta < -0.05:
                result["acceleration_label"] = "decelerating"
                result["acceleration_score"] = -1
            else:
                result["acceleration_label"] = "mixed"
                result["acceleration_score"] = 0
    except Exception as e:
        logger.warning(f"Error computing earnings acceleration for {sym}: {e}")

    return result


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_earnings_calendar(symbol: str) -> dict:
    """Return the next earnings date and options-risk profile for a stock.

    Fetches the next scheduled earnings date from Yahoo Finance, computes days
    until earnings, and classifies the risk level for options positions:

      CRITICAL  — earnings within 7 days; avoid new options (IV crush imminent)
      HIGH      — earnings within 14 days (blackout zone); IV crush risk
      MODERATE  — earnings 15–30 days out; pre-earnings IV expansion tailwind
      LOW       — earnings > 30 days out; no near-term options risk
      UNKNOWN   — no earnings date available

    Also returns the average absolute price move on last 4 earnings days
    (historical_avg_move_pct) for position sizing context.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
    """
    sym = symbol.upper()
    cached = cache_get(sym, "earnings_calendar")
    if cached is not None:
        return cached
    result = _compute_earnings_calendar_internal(sym)
    cache_set(sym, "earnings_calendar", result)
    return result


@mcp.tool()
def get_fundamental_score(symbol: str) -> dict:
    """Compute a composite fundamental quality score for a single stock.

    Scores 7 metrics on absolute thresholds (each -2 to +2) to produce a
    composite_score (-14 to +14) and a qualitative fundamental_label:

      strong_compounder — composite ≥ 8  (high growth, strong margins, fair value)
      solid             — composite 4–7
      average           — composite 0–3
      weak              — composite -1 to -3
      deteriorating     — composite ≤ -4  (declining revenue, negative margins)

    Each metric_score entry includes the raw value, numeric score, and a
    human-readable label explaining the score.

    Args:
        symbol: Stock ticker symbol (e.g. 'NVDA')
    """
    sym = symbol.upper()
    cached = cache_get(sym, "fundamental_score")
    if cached is not None:
        return cached
    result = _compute_fundamental_score_internal(sym)
    cache_set(sym, "fundamental_score", result)
    return result


@mcp.tool()
def get_revenue_growth(symbol: str) -> dict:
    """Return quarterly revenue trajectory and growth quality for a stock.

    Fetches the last 5 quarters of revenue (oldest→newest), computes 4 QoQ
    growth rates, a weighted sequential growth score (0–1 where 1 = all
    quarters positive), a 3-year CAGR, and a trajectory label:

      accelerating       — latest QoQ rate meaningfully above prior rate
      decelerating       — latest QoQ rate meaningfully below prior rate
      inflecting_positive — flipped from negative to positive QoQ
      inflecting_negative — flipped from positive to negative QoQ
      stable             — consistent growth rate, little change
      insufficient_data  — fewer than 5 quarters available

    Args:
        symbol: Stock ticker symbol (e.g. 'NVDA')
    """
    sym = symbol.upper()
    cached = cache_get(sym, "revenue_growth")
    if cached is not None:
        return cached
    result = _compute_revenue_growth_internal(sym)
    cache_set(sym, "revenue_growth", result)
    return result


@mcp.tool()
def get_earnings_acceleration(symbol: str) -> dict:
    """Compute the EPS acceleration score — the CAN SLIM 'A' criterion.

    Measures whether quarterly earnings growth is itself accelerating, the
    fundamental signal most correlated with institutional accumulation and
    pre-breakout setups per O'Neil's CAN SLIM research.

    Given 5 quarters of Net Income, computes 4 QoQ growth rates and 3
    acceleration deltas. Returns:

      acceleration_label:
        strong       — all 3 deltas positive, avg delta > 5 pp
        moderate     — ≥ 2 deltas positive and avg delta > 0
        mixed        — mixed signals
        decelerating — 0 positive deltas or avg delta < -5 pp
      acceleration_score: +2 (strong), +1 (moderate), 0 (mixed), -1 (decelerating)

    Args:
        symbol: Stock ticker symbol (e.g. 'NVDA')
    """
    sym = symbol.upper()
    cached = cache_get(sym, "earnings_acceleration")
    if cached is not None:
        return cached
    result = _compute_earnings_acceleration_tool(sym)
    cache_set(sym, "earnings_acceleration", result)
    return result


@mcp.tool()
def get_fundamental_scores_batch(symbols: list[str]) -> dict:
    """Score multiple stocks in one call, using the cache for hits.

    For each symbol: returns the cached score if fresh, otherwise fetches
    from yfinance and caches the result. Progress is reported in the summary.

    Args:
        symbols: List of ticker symbols (e.g. ['NVDA', 'AAPL', 'MSFT'])
    """
    cache_hits = 0
    fetched = 0
    errors = 0
    results = []

    for symbol in symbols:
        sym = symbol.upper()
        try:
            cached = cache_get(sym, "fundamental_score")
            if cached is not None:
                cached_copy = cached.copy()
                cached_copy["cache_hit"] = True
                results.append(cached_copy)
                cache_hits += 1
            else:
                result = _compute_fundamental_score_internal(sym)
                cache_set(sym, "fundamental_score", result)
                result_copy = result.copy()
                result_copy["cache_hit"] = False
                results.append(result_copy)
                fetched += 1
        except Exception as e:
            logger.error(f"Error scoring {sym}: {e}")
            errors += 1

    results.sort(key=lambda x: x.get("composite_score", -999), reverse=True)

    return {
        "requested": len(symbols),
        "cache_hits": cache_hits,
        "fetched": fetched,
        "errors": errors,
        "results": results,
    }


@mcp.tool()
def get_full_fundamental_profile(symbol: str) -> dict:
    """Return all 4 fundamental metrics for a stock in a single call.

    Returns earnings calendar, fundamental score, revenue growth, and EPS
    acceleration plus a synthesized summary with overall signal and highlights.

    Args:
        symbol: Stock ticker symbol (e.g. 'NVDA')
    """
    sym = symbol.upper()

    earnings = get_earnings_calendar(sym)
    score = get_fundamental_score(sym)
    revenue = get_revenue_growth(sym)
    eps = get_earnings_acceleration(sym)

    composite = score.get("composite_score", 0)
    trajectory = revenue.get("trajectory", "unknown")
    accel_lbl = eps.get("acceleration_label", "unknown")
    risk_lvl = earnings.get("risk_level", "UNKNOWN")

    highlights = []
    if composite >= 8:
        highlights.append(f"Strong fundamentals (score {composite})")
    elif composite <= -4:
        highlights.append(f"Deteriorating fundamentals (score {composite})")

    if trajectory in ("accelerating", "inflecting_positive"):
        highlights.append("Revenue accelerating")
    elif trajectory == "decelerating":
        highlights.append("Revenue decelerating")

    if accel_lbl in ("strong", "moderate"):
        highlights.append("EPS acceleration confirmed")

    days_to_earnings = earnings.get("days_to_earnings")
    if risk_lvl in ("CRITICAL", "HIGH"):
        if days_to_earnings:
            highlights.append(f"Earnings in {days_to_earnings}d — options risk ({risk_lvl})")

    if revenue.get("cagr_3y"):
        cagr = revenue.get("cagr_3y")
        if cagr >= 0.25:
            highlights.append(f"Strong 3Y CAGR ({cagr:.1%})")

    overall_signal = "neutral"
    if composite >= 8 and trajectory in ("accelerating", "inflecting_positive", "stable"):
        overall_signal = "bullish"
    elif composite <= -4 or trajectory in ("decelerating", "inflecting_negative"):
        overall_signal = "bearish"
    elif composite >= 4 and trajectory not in ("decelerating", "inflecting_negative"):
        overall_signal = "bullish"
    elif composite < 0 or trajectory in ("decelerating",):
        overall_signal = "bearish"

    if risk_lvl in ("CRITICAL", "HIGH"):
        overall_signal = "caution"

    return {
        "symbol": sym,
        "summary": {
            "overall_signal": overall_signal,
            "highlights": highlights,
        },
        "earnings_calendar": earnings,
        "fundamental_score": score,
        "revenue_growth": revenue,
        "earnings_acceleration": eps,
    }


@mcp.tool()
def get_top_fundamental_stocks(n: int = 10, min_coverage: float = 0.5) -> dict:
    """Return the top N stocks ranked by composite fundamental score from the cache.

    Reads from the local SQLite cache — does NOT fetch from yfinance.
    Only symbols previously scored via get_fundamental_score() appear here.
    Call get_fundamental_score(symbol) for each symbol to populate the cache first.

    Args:
        n:            Number of top stocks to return (default 10)
        min_coverage: Minimum data coverage fraction to include (default 0.5,
                      meaning at least 50% of the 7 metrics had data)
    """
    all_entries = cache_get_all_latest("fundamental_score")
    ranked_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    eligible = [
        e for e in all_entries
        if e.get("coverage", 0) >= min_coverage and e.get("composite_score") is not None
    ]
    eligible.sort(key=lambda e: e["composite_score"], reverse=True)
    top = eligible[:n]

    rankings = [
        {
            "rank":              i + 1,
            "symbol":            e.get("symbol"),
            "composite_score":   e.get("composite_score"),
            "fundamental_label": e.get("fundamental_label"),
            "coverage":          e.get("coverage"),
            "cached_at":         e.get("fetched_at"),
        }
        for i, e in enumerate(top)
    ]

    return {
        "ranked_at":         ranked_at,
        "n_requested":       n,
        "total_in_cache":    len(all_entries),
        "eligible_count":    len(eligible),
        "min_coverage":      min_coverage,
        "rankings":          rankings,
    }


@mcp.tool()
def get_upcoming_earnings(days: int = 14, include_stale: bool = False) -> dict:
    """Return stocks with earnings scheduled within the next N days, from the cache.

    Reads cached earnings_calendar data. Only symbols previously fetched via
    get_earnings_calendar() appear here. Call get_earnings_calendar(symbol)
    first to populate the cache.

    Days-to-earnings is recomputed from the stored earnings_date vs. today,
    so it remains accurate even if the cached data is a few hours old.

    By default, excludes symbols whose cache entry is older than
    FUNDAMENTALS_CACHE_TTL_HOURS (default 24h). Set include_stale=True
    to include all cached symbols regardless of age (entries will be
    flagged with stale=True).

    Args:
        days:          How many days ahead to look (default 14)
        include_stale: If True, include entries beyond the TTL window
                       (flagged as stale=True in the response)
    """
    all_entries = cache_get_all_latest("earnings_calendar")
    queried_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = date.today()
    now_ts = int(time.time())
    ttl_seconds = _get_ttl_seconds()

    upcoming = []
    stale_excluded = 0

    for entry in all_entries:
        fetched_ts = entry.get("_fetched_at_ts")
        is_stale = ttl_seconds > 0 and (now_ts - fetched_ts) > ttl_seconds if fetched_ts else True
        if is_stale and not include_stale:
            stale_excluded += 1
            continue

        earnings_date_str = entry.get("earnings_date")
        if not earnings_date_str:
            continue
        try:
            earn_date = date.fromisoformat(earnings_date_str[:10])
        except (ValueError, TypeError):
            continue
        computed_days = (earn_date - today).days
        if computed_days < 0 or computed_days > days:
            continue

        upcoming.append({
            "symbol":                  entry.get("symbol"),
            "earnings_date":           earnings_date_str[:10],
            "days_to_earnings":        computed_days,
            "risk_level":              entry.get("risk_level"),
            "pre_earnings_setup":      entry.get("pre_earnings_setup", False),
            "historical_avg_move_pct": entry.get("historical_avg_move_pct"),
            "cached_at":               entry.get("fetched_at"),
            "stale":                   is_stale,
        })

    upcoming.sort(key=lambda x: x["days_to_earnings"])

    return {
        "queried_at":     queried_at,
        "days_window":    days,
        "include_stale":  include_stale,
        "stale_excluded": stale_excluded,
        "total_in_cache": len(all_entries),
        "count":          len(upcoming),
        "upcoming":       upcoming,
    }


@mcp.tool()
def get_cache_stats() -> dict:
    """Return a summary of what is stored in the fundamentals cache.

    Reports symbol counts, date ranges, and DB file size per data type.
    Zero network calls — reads only from the local SQLite database.
    """
    return cache_stats()


@mcp.tool()
def get_sector_fundamental_breakdown(sector: str | None = None, top_n: int = 5) -> dict:
    """Return top stocks by fundamental score, grouped by sector.

    If sector is specified, returns only stocks in that sector (case-insensitive).
    If sector is None, returns top_n stocks for every sector found in the cache.
    Only symbols previously scored via get_fundamental_score() appear here.

    Args:
        sector: Sector name to filter (e.g. 'Technology'), or None for all sectors
        top_n:  Number of top stocks to return per sector (default 5)
    """
    all_entries = cache_get_all_latest("fundamental_score")
    queried_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    sectors_dict: dict[str, list] = {}
    for entry in all_entries:
        sect = entry.get("sector") or "Unknown"
        if sect not in sectors_dict:
            sectors_dict[sect] = []
        sectors_dict[sect].append(entry)

    for sect_list in sectors_dict.values():
        sect_list.sort(key=lambda e: e.get("composite_score", -999), reverse=True)

    if sector:
        filtered_sectors = {}
        sector_lower = sector.lower()
        for sect, entries in sectors_dict.items():
            if sect.lower() == sector_lower:
                filtered_sectors[sect] = entries[:top_n]
        sectors_dict = filtered_sectors

    result_sectors = {}
    for sect, entries in sorted(sectors_dict.items()):
        result_sectors[sect] = [
            {
                "rank": i + 1,
                "symbol": e.get("symbol"),
                "composite_score": e.get("composite_score"),
                "fundamental_label": e.get("fundamental_label"),
                "coverage": e.get("coverage"),
            }
            for i, e in enumerate(entries[:top_n])
        ]

    return {
        "queried_at": queried_at,
        "sector_filter": sector,
        "top_n": top_n,
        "sectors": result_sectors,
        "sector_count": len(result_sectors),
        "total_symbols": len(all_entries),
    }


@mcp.tool()
def get_fundamental_score_changes(
    min_delta: int = 2,
    since_days: int = 90,
    direction: str = "both",
) -> dict:
    """Return stocks whose composite fundamental score changed significantly.

    Compares the earliest and latest cached snapshots within since_days.
    Only stocks with ≥ 2 snapshots in the window are evaluated.

    Args:
        min_delta:   Minimum absolute score change to report (default 2,
                     on the -14 to +14 composite_score scale)
        since_days:  How far back to look for snapshots (default 90)
        direction:   "improving" | "deteriorating" | "both" (default "both")
    """
    all_symbols = cache_get_all_latest("fundamental_score")
    queried_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    symbols_evaluated = 0
    symbols_insufficient = 0
    changes = []

    for entry in all_symbols:
        sym = entry.get("symbol")
        if not sym:
            continue

        history = cache_history(sym, "fundamental_score", since_days=since_days)
        if len(history) < 2:
            symbols_insufficient += 1
            continue

        symbols_evaluated += 1
        first = history[0]
        last = history[-1]

        score_then = first.get("composite_score")
        score_now = last.get("composite_score")
        if score_then is None or score_now is None:
            continue

        delta = score_now - score_then
        if abs(delta) < min_delta:
            continue

        if direction == "improving" and delta <= 0:
            continue
        if direction == "deteriorating" and delta >= 0:
            continue

        changes.append({
            "symbol": sym,
            "delta": delta,
            "direction": "improving" if delta > 0 else "deteriorating",
            "score_then": score_then,
            "score_now": score_now,
            "label_then": first.get("fundamental_label"),
            "label_now": last.get("fundamental_label"),
            "first_snapshot": first.get("fetched_at"),
            "last_snapshot": last.get("fetched_at"),
        })

    if direction == "deteriorating":
        changes.sort(key=lambda x: x["delta"])
    else:
        changes.sort(key=lambda x: x["delta"], reverse=True)

    return {
        "queried_at": queried_at,
        "since_days": since_days,
        "min_delta": min_delta,
        "direction": direction,
        "symbols_evaluated": symbols_evaluated,
        "symbols_with_insufficient_history": symbols_insufficient,
        "changes": changes,
    }


@mcp.tool()
def get_fundamental_history(symbol: str, data_type: str, since_days: int = 365) -> dict:
    """Return historical snapshots and trend for a cached fundamental data type.

    Does NOT hit yfinance. Call the corresponding tool first to populate the cache.

    Args:
        symbol: Stock ticker symbol (e.g. 'NVDA')
        data_type: One of: fundamental_score, revenue_growth, earnings_acceleration, earnings_calendar
        since_days: How many days back to look (default 365)
    """
    sym = symbol.upper()
    valid = {"fundamental_score", "revenue_growth", "earnings_acceleration", "earnings_calendar"}

    if data_type not in valid:
        return {
            "error": f"Invalid data_type. Must be one of: {sorted(valid)}",
            "symbol": sym,
            "data_type": data_type,
        }

    snapshots = cache_history(sym, data_type, since_days=since_days)

    trend = "flat"
    if len(snapshots) >= 2:
        first = snapshots[0]
        last = snapshots[-1]

        if data_type == "fundamental_score":
            val_then = first.get("composite_score")
            val_now = last.get("composite_score")
            if val_then is not None and val_now is not None:
                delta = val_now - val_then
                if delta >= 1:
                    trend = "improving"
                elif delta <= -1:
                    trend = "deteriorating"
        elif data_type == "revenue_growth":
            val_then = first.get("weighted_score")
            val_now = last.get("weighted_score")
            if val_then is not None and val_now is not None:
                delta = val_now - val_then
                if delta >= 0.05:
                    trend = "improving"
                elif delta <= -0.05:
                    trend = "deteriorating"
        elif data_type == "earnings_acceleration":
            val_then = first.get("acceleration_score")
            val_now = last.get("acceleration_score")
            if val_then is not None and val_now is not None:
                delta = val_now - val_then
                if delta >= 1:
                    trend = "improving"
                elif delta <= -1:
                    trend = "deteriorating"
        elif data_type == "earnings_calendar":
            days_then = first.get("days_to_earnings")
            days_now = last.get("days_to_earnings")
            if days_then is not None and days_now is not None:
                delta = days_then - days_now
                if delta >= 7:
                    trend = "improving"
                elif delta <= -7:
                    trend = "deteriorating"

    return {
        "symbol": sym,
        "data_type": data_type,
        "since_days": since_days,
        "snapshot_count": len(snapshots),
        "trend": trend,
        "snapshots": snapshots,
    }


if __name__ == "__main__":
    from quantcore.db import init_schema
    init_schema()
    mcp.run()
