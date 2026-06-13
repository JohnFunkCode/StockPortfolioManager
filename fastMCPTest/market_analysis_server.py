"""
Market Analysis MCP Server

Provides three market microstructure tools to help identify bounce bottoms:

  get_short_interest   — short interest, days-to-cover, short float %, squeeze potential
  get_dark_pool        — proxy dark pool / block-trade detection via price-volume divergence
  get_bid_ask_spread   — current bid/ask spread + widening vs rolling norm (fear gauge)

Usage (standalone):
    fastmcp run market_analysis_server.py

Data source: Yahoo Finance via yfinance.

Thin adapter (architectural standard v2): all business logic lives in
quantcore.services.microstructure.MicrostructureService; each tool here is
exactly one service call deep.
"""

import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parent
for path in (PROJECT_ROOT, MCP_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from fastmcp import FastMCP

from quantcore.services.registry import get_services

mcp = FastMCP("market-analysis-server")


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
    return get_services().microstructure.get_short_interest(symbol)


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
    return get_services().microstructure.get_dark_pool(symbol, lookback, interval)


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
    return get_services().microstructure.get_bid_ask_spread(symbol, lookback)


if __name__ == "__main__":
    from quantcore.db import init_schema
    init_schema()
    mcp.run()
