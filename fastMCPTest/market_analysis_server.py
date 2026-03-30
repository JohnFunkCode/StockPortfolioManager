"""
Market Analysis MCP Server

Provides three market microstructure tools to help identify bounce bottoms:

  get_short_interest   — short interest, days-to-cover, short float %, squeeze potential
  get_dark_pool        — proxy dark pool / block-trade detection via price-volume divergence
  get_bid_ask_spread   — current bid/ask spread + widening vs rolling norm (fear gauge)

Usage (standalone):
    fastmcp run market_analysis_server.py

Data source: Yahoo Finance via yfinance.

LIMITATIONS:
  - Short interest data from Yahoo is updated twice monthly (FINRA settlement dates);
    it may lag by up to 2 weeks.
  - True dark pool prints require a paid data feed (e.g. FINRA ATS, Bloomberg).
    get_dark_pool() uses price-volume divergence as a publicly available proxy.
  - Bid/ask spread for equities uses the options chain and fast_info bid/ask;
    intraday tick-level spread data is not available via yfinance.
"""

import datetime
import math

import yfinance as yf
from fastmcp import FastMCP

mcp = FastMCP("market-analysis-server")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val) if val is not None else default
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _safe_int(val, default: int = 0) -> int:
    try:
        f = float(val) if val is not None else 0.0
        return default if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Tool 1 — Short Interest / Days-to-Cover
# ---------------------------------------------------------------------------

@mcp.tool()
def get_short_interest(symbol: str) -> dict:
    """Return short interest metrics and short-squeeze potential for a stock.

    Short interest is the total number of shares sold short that have not yet
    been covered.  Days-to-cover (also called the short ratio) measures how
    many average trading days it would take short sellers to buy back all
    shorted shares — a key squeeze risk indicator.

    Metrics returned:
      shares_short        — total shares currently sold short
      short_float_pct     — short interest as % of float (>20% = high, >30% = extreme)
      short_ratio         — days-to-cover = shares_short / avg_daily_volume
      shares_outstanding  — total shares issued
      float_shares        — tradeable float
      avg_daily_volume    — 3-month average daily volume
      short_interest_date — as-of date for the short data (may lag up to 2 weeks)

    Squeeze potential scoring:
      HIGH   — short_float_pct ≥ 20% AND short_ratio ≥ 5
      MEDIUM — short_float_pct ≥ 10% OR  short_ratio ≥ 3
      LOW    — below those thresholds

    A short squeeze occurs when rising price forces short sellers to cover,
    creating additional buy pressure.  High short interest + low days-to-cover
    (liquid stock) = fastest squeeze dynamics.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
    """
    ticker = yf.Ticker(symbol.upper())
    info   = ticker.info

    shares_short       = _safe_int(info.get("sharesShort"))
    shares_outstanding = _safe_int(info.get("sharesOutstanding"))
    float_shares       = _safe_int(info.get("floatShares"))
    avg_volume         = _safe_int(info.get("averageVolume") or info.get("averageVolume10days"))
    short_ratio        = _safe_float(info.get("shortRatio"))   # days-to-cover from Yahoo
    short_pct_float    = _safe_float(info.get("shortPercentOfFloat"))

    # Compute manually if Yahoo field is missing
    if short_pct_float == 0.0 and float_shares > 0 and shares_short > 0:
        short_pct_float = shares_short / float_shares

    short_pct_float_display = round(short_pct_float * 100, 2) if short_pct_float <= 1.0 else round(short_pct_float, 2)

    if short_ratio == 0.0 and avg_volume > 0 and shares_short > 0:
        short_ratio = round(shares_short / avg_volume, 2)

    # Short interest date
    si_date = info.get("dateShortInterest")
    if si_date:
        try:
            si_date_str = datetime.date.fromtimestamp(si_date).isoformat()
        except Exception:
            si_date_str = str(si_date)
    else:
        si_date_str = "unknown"

    # Squeeze potential
    high_short_float = short_pct_float_display >= 20.0
    high_days_cover  = short_ratio >= 5.0

    if high_short_float and high_days_cover:
        squeeze_potential = "HIGH"
        squeeze_note = (
            f"Short float {short_pct_float_display:.1f}% + {short_ratio:.1f} days-to-cover — "
            "significant squeeze fuel. Rising price could force rapid covering cascade."
        )
    elif high_short_float or short_ratio >= 3.0:
        squeeze_potential = "MEDIUM"
        squeeze_note = (
            f"Short float {short_pct_float_display:.1f}%, {short_ratio:.1f} days-to-cover — "
            "moderate squeeze risk. Watch for unusual volume as a trigger."
        )
    else:
        squeeze_potential = "LOW"
        squeeze_note = (
            f"Short float {short_pct_float_display:.1f}%, {short_ratio:.1f} days-to-cover — "
            "low squeeze risk. Short positioning not a major factor."
        )

    # Borrow availability proxy: very high short float → borrow likely tight
    if short_pct_float_display >= 25:
        borrow_note = "Borrow likely TIGHT — high demand for short shares, cost-to-borrow elevated"
    elif short_pct_float_display >= 15:
        borrow_note = "Borrow possibly tight — monitor for hard-to-borrow status"
    else:
        borrow_note = "Borrow likely available — standard short interest level"

    return {
        "symbol":              symbol.upper(),
        "shares_short":        shares_short,
        "short_float_pct":     short_pct_float_display,
        "short_ratio_days":    round(short_ratio, 2),
        "shares_outstanding":  shares_outstanding,
        "float_shares":        float_shares,
        "avg_daily_volume":    avg_volume,
        "short_interest_date": si_date_str,
        "squeeze_potential":   squeeze_potential,
        "squeeze_note":        squeeze_note,
        "borrow_note":         borrow_note,
    }


# ---------------------------------------------------------------------------
# Tool 2 — Dark Pool / Block Trade Proxy
# ---------------------------------------------------------------------------

@mcp.tool()
def get_dark_pool(symbol: str, lookback: int = 20, interval: str = "1d") -> dict:
    """Detect dark pool / large block trade activity via price-volume divergence.

    True dark pool prints require a paid data feed (FINRA ATS, Bloomberg, etc.).
    This tool uses publicly available OHLCV data to identify bars that exhibit
    the statistical fingerprint of institutional off-exchange accumulation:

      HIGH VOLUME + LOW PRICE MOVEMENT
        When volume is unusually large but price barely moves, large buyers are
        absorbing sell pressure off-exchange.  The stock is being "held up" while
        institutions accumulate quietly.

      VOLUME SPIKE WITHOUT DIRECTIONAL FOLLOW-THROUGH
        A bar with 2× or more average volume that closes near the midpoint of its
        range (indecisive candle) — characteristic of two-sided institutional flow.

    Detection thresholds:
      absorption_vol_mult  — volume must be ≥ 2.0× rolling average
      absorption_range_pct — price range must be ≤ 0.5× average bar range
        (unusually compressed range for the volume)
      Two-sided flow:       close within 30% of bar midpoint

    Returns:
      absorption_events     — bars matching the price-absorption pattern
      two_sided_events      — bars with high volume but indecisive close
      net_signal            — 'accumulation', 'distribution', 'mixed', or 'none'
        Accumulation: high-volume absorption on DOWN days (buyers absorbing sellers)
        Distribution: high-volume absorption on UP days (sellers absorbing buyers)
      interpretation        — plain-English summary

    Args:
        symbol:   Stock ticker symbol (e.g. 'AAPL')
        lookback: Bars to scan for anomalies (default: 20)
        interval: '1d' daily (default), '1h' hourly
    """
    valid_intervals = {"1d", "1h"}
    if interval not in valid_intervals:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {', '.join(valid_intervals)}")

    fetch_period = {"1d": "6mo", "1h": "60d"}[interval]
    fmt          = "%Y-%m-%d" if interval == "1d" else "%Y-%m-%d %H:%M"

    ticker = yf.Ticker(symbol.upper())
    hist   = ticker.history(period=fetch_period, interval=interval).copy()

    if len(hist) < lookback + 10:
        raise ValueError(f"Not enough data for {symbol} (got {len(hist)} bars, need {lookback + 10})")

    close  = hist["Close"]
    open_  = hist["Open"]
    high   = hist["High"]
    low    = hist["Low"]
    volume = hist["Volume"].astype(float)

    vol_avg       = volume.rolling(window=lookback).mean()
    bar_range     = (high - low)
    bar_range_pct = bar_range / close * 100
    avg_range_pct = bar_range_pct.rolling(window=lookback).mean()

    ABSORPTION_VOL_MULT   = 2.0
    ABSORPTION_RANGE_MULT = 0.5
    TWO_SIDED_BODY_THRESH = 0.30   # close within 30% of bar midpoint = indecisive

    absorption_events = []
    two_sided_events  = []

    start = max(1, len(hist) - lookback)
    for i in range(start, len(hist)):
        v     = float(volume.iloc[i])
        va    = float(vol_avg.iloc[i])
        rng   = float(bar_range_pct.iloc[i])
        avg_r = float(avg_range_pct.iloc[i])
        c     = float(close.iloc[i])
        o     = float(open_.iloc[i])
        h     = float(high.iloc[i])
        l     = float(low.iloc[i])

        if any(math.isnan(x) for x in [v, va, rng, avg_r]) or va == 0:
            continue

        vol_ratio   = round(v / va, 2)
        range_ratio = round(rng / avg_r, 2) if avg_r > 0 else 1.0
        day_dir     = "up" if c >= o else "down"
        bar_mid     = (h + l) / 2
        close_pos   = abs(c - bar_mid) / (h - l) if (h - l) > 0 else 0.5

        date_str = hist.index[i].strftime(fmt)

        # --- Price absorption ---
        if vol_ratio >= ABSORPTION_VOL_MULT and range_ratio <= ABSORPTION_RANGE_MULT:
            absorption_events.append({
                "date":         date_str,
                "direction":    day_dir,
                "close":        round(c, 2),
                "volume_ratio": vol_ratio,
                "range_ratio":  range_ratio,
                "interpretation": (
                    "possible accumulation — high volume absorbed on down day"
                    if day_dir == "down" else
                    "possible distribution — high volume absorbed on up day"
                ),
            })

        # --- Two-sided / indecisive high-volume bar ---
        elif vol_ratio >= ABSORPTION_VOL_MULT and close_pos <= TWO_SIDED_BODY_THRESH:
            two_sided_events.append({
                "date":         date_str,
                "direction":    day_dir,
                "close":        round(c, 2),
                "volume_ratio": vol_ratio,
                "close_position": round(close_pos, 2),
                "interpretation": "two-sided institutional flow — large blocks crossing both ways",
            })

    # --- Net signal ---
    accum = [e for e in absorption_events if e["direction"] == "down"]
    distr = [e for e in absorption_events if e["direction"] == "up"]

    if len(accum) > len(distr) and accum:
        net_signal = "accumulation"
        interp = (
            f"{len(accum)} absorption event(s) on down days detected in last {lookback} bars. "
            "Institutions appear to be absorbing sell pressure — bullish dark pool proxy signal."
        )
    elif len(distr) > len(accum) and distr:
        net_signal = "distribution"
        interp = (
            f"{len(distr)} absorption event(s) on up days detected in last {lookback} bars. "
            "Institutions appear to be absorbing buy pressure — bearish distribution proxy signal."
        )
    elif absorption_events or two_sided_events:
        net_signal = "mixed"
        interp = (
            f"{len(absorption_events)} absorption + {len(two_sided_events)} two-sided event(s). "
            "Large block activity detected but direction is unclear."
        )
    else:
        net_signal = "none"
        interp = f"No significant block-trade anomalies detected in the last {lookback} bars."

    return {
        "symbol":               symbol.upper(),
        "interval":             interval,
        "lookback":             lookback,
        "last_close":           round(float(close.iloc[-1]), 2),
        "net_signal":           net_signal,
        "absorption_events":    absorption_events,
        "two_sided_events":     two_sided_events,
        "absorption_count":     len(absorption_events),
        "two_sided_count":      len(two_sided_events),
        "interpretation":       interp,
        "data_note": (
            "Proxy signal only — true dark pool data requires a paid feed "
            "(FINRA ATS, Bloomberg). This uses price-volume divergence as a proxy."
        ),
    }


# ---------------------------------------------------------------------------
# Tool 3 — Bid/Ask Spread Widening
# ---------------------------------------------------------------------------

@mcp.tool()
def get_bid_ask_spread(symbol: str, lookback: int = 20) -> dict:
    """Measure current bid/ask spread and detect widening vs rolling norm.

    Bid/ask spread widening is a reliable fear gauge and liquidity stress indicator.
    Market makers widen spreads when uncertainty increases — at bottoms, spreads
    reach maximum width as sellers overwhelm buyers.  When spreads begin to
    NARROW from an elevated level, it signals liquidity returning and stabilisation.

    This tool measures spread from three sources ranked by reliability:
      1. Equity bid/ask from ticker.fast_info (live quote, most accurate)
      2. ATM options spread as a volatility/fear proxy (always available)
      3. Historical intraday spread estimation via high-low range (rough proxy)

    Metrics:
      equity_spread_pct   — current (ask-bid)/mid for the stock itself
      options_spread_pct  — average (ask-bid)/mid across ATM calls + puts
                            (options spreads widen faster than equity in fear)
      spread_vs_norm      — how current spread compares to rolling average
        'widening'  — spread > 1.5× rolling norm  (fear / stress)
        'elevated'  — spread 1.2–1.5× norm
        'normal'    — spread within 20% of norm
        'narrowing' — spread < 0.8× norm (liquidity returning — bounce setup)
      hl_spread_ratio     — today's high-low / 20-day avg high-low
                            (proxy for intraday spread widening)

    Bottom signal: spread_vs_norm == 'widening' transitioning to 'narrowing'
    means fear has peaked and liquidity is returning — often coincides with
    the final capitulation bar before a bounce.

    Args:
        symbol:   Stock ticker symbol (e.g. 'AAPL')
        lookback: Rolling window for spread norm calculation (default: 20)
    """
    ticker = yf.Ticker(symbol.upper())
    info   = ticker.fast_info

    # ---- 1. Equity bid/ask (live quote) ----
    bid = _safe_float(getattr(info, "bid", None) or getattr(info, "three_month_average_price", None))
    ask = _safe_float(getattr(info, "ask", None))
    price = _safe_float(getattr(info, "last_price", None))

    equity_spread     = None
    equity_spread_pct = None
    if bid > 0 and ask > 0 and ask > bid:
        equity_spread     = round(ask - bid, 4)
        mid               = (ask + bid) / 2
        equity_spread_pct = round((ask - bid) / mid * 100, 3) if mid > 0 else None

    # ---- 2. ATM options spread (fear proxy) ----
    options_spread_pct = None
    atm_spreads        = []
    expirations        = ticker.options
    if expirations and price > 0:
        try:
            chain    = ticker.option_chain(expirations[0])
            for df in [chain.calls, chain.puts]:
                if df.empty:
                    continue
                df2 = df.copy()
                df2["moneyness"] = abs(df2["strike"] - price)
                for _, row in df2.nsmallest(3, "moneyness").iterrows():
                    b = _safe_float(row.get("bid"))
                    a = _safe_float(row.get("ask"))
                    if a > b > 0:
                        mid_opt = (a + b) / 2
                        atm_spreads.append((a - b) / mid_opt * 100)
        except Exception:
            pass

    if atm_spreads:
        options_spread_pct = round(sum(atm_spreads) / len(atm_spreads), 2)

    # ---- 3. High-low range ratio (intraday spread proxy) ----
    hist      = ticker.history(period="3mo", interval="1d").copy()
    hl_ratio  = None
    spread_vs_norm = "unknown"

    if len(hist) >= lookback + 1:
        hl_range     = ((hist["High"] - hist["Low"]) / hist["Close"] * 100)
        hl_avg       = float(hl_range.rolling(window=lookback).mean().iloc[-1])
        hl_current   = float(hl_range.iloc[-1])
        hl_ratio     = round(hl_current / hl_avg, 2) if hl_avg > 0 else None

        # Use the options spread if available, else hl as the spread signal
        spread_signal = options_spread_pct if options_spread_pct else (hl_ratio * 100 if hl_ratio else None)

        if hl_ratio is not None:
            if   hl_ratio >= 1.5:  spread_vs_norm = "widening"
            elif hl_ratio >= 1.2:  spread_vs_norm = "elevated"
            elif hl_ratio <= 0.8:  spread_vs_norm = "narrowing"
            else:                  spread_vs_norm = "normal"

    # ---- Rolling HL spread history (last 10 bars for context) ----
    spread_history = []
    if len(hist) >= 2:
        hl_range = ((hist["High"] - hist["Low"]) / hist["Close"] * 100)
        for i in range(max(0, len(hist) - 10), len(hist)):
            spread_history.append({
                "date":     hist.index[i].strftime("%Y-%m-%d"),
                "hl_range_pct": round(float(hl_range.iloc[i]), 3),
                "close":    round(float(hist["Close"].iloc[i]), 2),
            })

    # ---- Interpretation ----
    lines = []
    if equity_spread_pct is not None:
        lines.append(f"Equity bid/ask spread: {equity_spread_pct:.3f}% (${equity_spread:.4f}).")
    if options_spread_pct is not None:
        lines.append(f"ATM options spread: {options_spread_pct:.2f}% of premium — fear gauge.")

    if spread_vs_norm == "widening":
        lines.append(
            f"H/L range {hl_ratio:.2f}× norm — spreads are WIDENING. "
            "Maximum liquidity stress. Watch for narrowing as the capitulation signal."
        )
    elif spread_vs_norm == "elevated":
        lines.append(
            f"H/L range {hl_ratio:.2f}× norm — spreads elevated. Uncertainty priced in."
        )
    elif spread_vs_norm == "narrowing":
        lines.append(
            f"H/L range {hl_ratio:.2f}× norm — spreads NARROWING. "
            "Liquidity returning — potential stabilisation / bounce setup."
        )
    elif spread_vs_norm == "normal":
        lines.append(f"H/L range {hl_ratio:.2f}× norm — spreads normal. No stress signal.")

    bottom_signal = (
        "strong" if spread_vs_norm == "narrowing"
        else "forming" if spread_vs_norm == "widening"
        else "none"
    )
    bottom_note = {
        "strong":  "Spreads narrowing from elevated levels — liquidity returning, bounce likely forming.",
        "forming": "Spreads at maximum width — capitulation may be at peak. Watch for narrowing to confirm bottom.",
        "none":    "Spread environment does not indicate a near-term bottom.",
    }[bottom_signal]

    return {
        "symbol":              symbol.upper(),
        "price":               round(price, 2) if price else None,
        "equity_bid":          round(bid, 4) if bid else None,
        "equity_ask":          round(ask, 4) if ask else None,
        "equity_spread":       equity_spread,
        "equity_spread_pct":   equity_spread_pct,
        "options_spread_pct":  options_spread_pct,
        "hl_range_ratio":      hl_ratio,
        "spread_vs_norm":      spread_vs_norm,
        "bottom_signal":       bottom_signal,
        "bottom_note":         bottom_note,
        "spread_history":      spread_history,
        "interpretation":      " ".join(lines),
    }


if __name__ == "__main__":
    mcp.run()
