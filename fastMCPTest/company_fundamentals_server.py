from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).parent))

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
                # yfinance dict: keys are field names, "Earnings Date" value may be a list or single date
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
    except Exception:
        pass

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
    except Exception:
        pass

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
    t = yf.Ticker(sym)

    try:
        info: dict[str, Any] = t.info or {}
    except Exception:
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
    except Exception:
        pass

    return result


if __name__ == "__main__":
    mcp.run()
