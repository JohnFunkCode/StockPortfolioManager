"""
stock_price_server.py — MCP server for prices, technicals, options chains, and
trade synthesis (the largest tool surface: price/RSI/MACD/stochastic/volume/OBV/
VWAP/candlesticks/higher-lows/gaps/drawdown, full options chain + contract/spread
pricing, unusual calls, delta-adjusted OI, gamma-wall + VWAP + relative-strength
history, stop-loss, and the composite trade recommendation).

HTTP gateway wrapper (architectural standard v2 §11, Rule 6 —
``AI Agent → MCP wrapper → REST tier → Service``): each tool translates its call
into a single HTTP request against the FastAPI front door via
``mcp_gateway.rest_client``; no business logic or DB access lives here.
"""

import os
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parent
for path in (PROJECT_ROOT, MCP_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from fastmcp import FastMCP

from mcp_gateway import rest_client

mcp = FastMCP("stock-price-server")


@mcp.tool()
def get_news(symbol: str, max_articles: int = 10) -> dict:
    """Get recent news articles for a given ticker symbol from Yahoo Finance.

    Each article is scored by FinBERT (ProsusAI/finbert), a BERT model
    fine-tuned on financial text, and tagged with:
      sentiment        — 'positive', 'negative', or 'neutral'
      sentiment_score  — model confidence (0–1)

    The response also includes aggregate sentiment counts and an overall
    sentiment label (plurality wins).  If the transformers library is not
    installed the sentiment fields are omitted and a 'sentiment_note' field
    explains why.

    Args:
        symbol:       Stock ticker symbol (e.g. 'AAPL')
        max_articles: Maximum number of articles to return (default: 10)
    """
    return rest_client.get(f"/api/securities/{symbol}/news", max_articles=max_articles)


@mcp.tool()
def get_stock_price(symbol: str) -> dict:
    """Get the current stock price, Bollinger Bands (20-day, 2σ), and options chain summary for a given ticker symbol."""
    return rest_client.get(f"/api/securities/{symbol}/price-summary")

@mcp.tool()
def get_full_options_chain(symbol: str) -> dict:
    """Fetch the full options chain (all strikes, all expirations) for a ticker and persist to DB."""
    return rest_client.get(f"/api/securities/{symbol}/options/full-chain")


@mcp.tool()
def get_option_contracts(
    symbol: str,
    expirations: list[str],
    strikes: list[float],
    kind: str = "call",
    max_snapshot_age_minutes: int = 15,
    allow_live_fetch: bool = True,
) -> dict:
    """Return specific option contracts by expiration and strike.

    Uses the latest cached full-chain snapshot first. If the cache is missing,
    stale, or incomplete and allow_live_fetch is True, fetches the live full
    chain, persists it, and returns the requested contracts.

    Args:
        symbol: Stock ticker symbol (e.g. 'CRDO')
        expirations: Expiration dates in YYYY-MM-DD format
        strikes: Option strikes to retrieve
        kind: 'call' or 'put'
        max_snapshot_age_minutes: Cache freshness window before live refresh
        allow_live_fetch: Whether to fetch yfinance when cache is stale/missing
    """
    # The REST route uses the service's default cache-freshness / live-fetch
    # behaviour; max_snapshot_age_minutes & allow_live_fetch are not exposed
    # over HTTP (Step 1 curation) and are accepted here for signature stability.
    return rest_client.get(
        f"/api/securities/{symbol}/options/contracts",
        expirations=expirations,
        strikes=strikes,
        kind=kind,
    )


@mcp.tool()
def price_vertical_spread(
    symbol: str,
    expiration: str,
    long_strike: float,
    short_strike: float,
    kind: str = "call",
    max_snapshot_age_minutes: int = 15,
    allow_live_fetch: bool = True,
) -> dict:
    """Price an exact two-leg vertical spread from full-chain contracts.

    Returns conservative bid/ask debit, mid-debit estimate, max profit/loss,
    breakeven, risk/reward, leg liquidity, source, and cache/persistence status.

    Args:
        symbol: Stock ticker symbol (e.g. 'CRDO')
        expiration: Expiration date in YYYY-MM-DD format
        long_strike: Strike of the long option leg
        short_strike: Strike of the short option leg
        kind: 'call' for call verticals or 'put' for put verticals
        max_snapshot_age_minutes: Cache freshness window before live refresh
        allow_live_fetch: Whether to fetch yfinance when cache is stale/missing
    """
    return rest_client.post(
        f"/api/securities/{symbol}/options/vertical-spread",
        json={
            "expiration": expiration,
            "long_strike": long_strike,
            "short_strike": short_strike,
            "kind": kind,
            "max_snapshot_age_minutes": max_snapshot_age_minutes,
            "allow_live_fetch": allow_live_fetch,
        },
    )


@mcp.tool()
def get_rsi(symbol: str, period: int = 14, interval: str = "1d") -> dict:
    """Calculate the Relative Strength Index (RSI) for a stock symbol.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
        period: Number of periods for RSI calculation (default: 14)
        interval: Price interval — '1d' for daily (default), '1wk' for weekly, '1mo' for monthly
    """
    return rest_client.get(f"/api/securities/{symbol}/rsi", period=period, interval=interval)


@mcp.tool()
def get_macd(symbol: str, interval: str = "1d") -> dict:
    """Calculate MACD (Moving Average Convergence Divergence) for a stock symbol.

    Uses standard parameters: fast EMA=12, slow EMA=26, signal EMA=9.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
        interval: Price interval — '1d' for daily (default), '1wk' for weekly, '1mo' for monthly
    """
    return rest_client.get(f"/api/securities/{symbol}/macd", interval=interval)


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
    return rest_client.get(
        f"/api/securities/{symbol}/stochastic",
        k_period=k_period,
        d_period=d_period,
        interval=interval,
    )


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
    return rest_client.get(
        f"/api/securities/{symbol}/volume", lookback=lookback, interval=interval
    )


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
    return rest_client.get(
        f"/api/securities/{symbol}/obv", lookback=lookback, interval=interval
    )


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
    return rest_client.get(
        f"/api/securities/{symbol}/vwap", lookback=lookback, interval=interval
    )


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
    return rest_client.get(
        f"/api/securities/{symbol}/candlestick", lookback=lookback, interval=interval
    )


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
    return rest_client.get(
        f"/api/securities/{symbol}/higher-lows",
        swing_bars=swing_bars,
        lookback_swings=lookback_swings,
        interval=interval,
    )


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
    return rest_client.get(
        f"/api/securities/{symbol}/gaps",
        min_gap_pct=min_gap_pct,
        lookback=lookback,
        interval=interval,
    )


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
    return rest_client.get(
        f"/api/securities/{symbol}/options/unusual-calls",
        min_volume=min_volume,
        min_vol_oi_ratio=min_vol_oi_ratio,
        max_expirations=max_expirations,
    )


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
      • Gamma wall (highest Black-Scholes gamma × OI strike) acting as a price magnet
      • Net DAOI shifting toward zero as price approaches the flip → hedging flow
        accelerating

    Outputs per expiration and aggregated across all scanned expirations:
      net_daoi_shares   — net share-equivalent exposure (positive = MM net long delta)
      call_daoi_shares  — calls contribution (always positive)
      put_daoi_shares   — puts contribution (always negative)
      delta_flip_strike — strike nearest to zero net delta (price magnet)
      gamma_wall        — strike with highest aggregate gamma × OI across calls+puts
      mm_hedge_bias     — 'buy_on_rally' or 'sell_on_rally' (direction MM must trade)
      signal            — bounce signal strength

    Args:
        symbol:          Stock ticker symbol (e.g. 'AAPL')
        max_expirations: Number of nearest expirations to analyse (default: 3)
        risk_free_rate:  Annualised risk-free rate as decimal (default: 0.045)
    """
    return rest_client.get(
        f"/api/securities/{symbol}/options/delta-adjusted-oi",
        max_expirations=max_expirations,
        risk_free_rate=risk_free_rate,
    )


@mcp.tool()
def get_gamma_wall_history(symbol: str, since_days: int = 90) -> dict:
    """
    Return historical daily gamma wall strike and MM hedge bias snapshots for a symbol.

    Data is auto-collected whenever get_delta_adjusted_oi() is called — no manual
    collection step needed. One row per calendar day; post-close (4:15pm+ ET) calls
    overwrite intraday calls so settled EOD open interest is always stored.

    INTENDED USE — post-hoc analysis only:
      - Did price pin near the gamma wall on expiration Fridays?
      - How did the gamma wall shift after monthly OpEx events?
      - Is the MM hedge bias (buy_on_rally vs sell_on_rally) persistent for this symbol?

    NOT intended for:
      - Predicting future gamma wall levels (past values have weak autocorrelation)
      - Making hold/sell decisions on multi-week equity positions
      - Replacing VWAP or relative-strength trend analysis

    Note: the gamma wall is computed from Black-Scholes gamma × OI. Each history
    row carries a `gamma_wall_method` field: "bs_gamma_oi" for current rows, or
    "abs_daoi" for rows captured before the migration, which used a |delta × OI|
    proxy biased toward deep-ITM high-OI strikes. Wall levels are only comparable
    across rows with the same method — filter on it before cross-date analysis.

    Args:
        symbol: Ticker symbol (e.g. "MSFT")
        since_days: Number of calendar days of history to return (default 90)

    Returns:
        dict with keys: symbol, since_days, data_points, history (list of daily rows), note
    """
    return rest_client.get(
        f"/api/securities/{symbol}/options/gamma-wall-history", since_days=since_days
    )


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
    return rest_client.get(
        f"/api/securities/{symbol}/drawdown", lookback_days=lookback_days
    )


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
    return rest_client.get(
        f"/api/securities/{symbol}/stop-loss",
        cost_basis=cost_basis,
        shares=shares,
        max_expirations=max_expirations,
    )


@mcp.tool()
def get_vwap_history(symbol: str, since_days: int = 90, lookback: int = 20, interval: str = "1d") -> dict:
    """
    Return historical daily VWAP values computed from cached OHLCV data.

    VWAP is computed as a rolling sum(close × volume) / sum(volume) over `lookback` bars,
    matching the same formula used by get_vwap(). Because all price/volume data lives in
    ohlcv_cache.db, this tool can backfill up to 2 years of history with no network calls.

    INTENDED USE — trend analysis for multi-week equity holds:
      - Is price sustaining above VWAP (healthy uptrend) or repeatedly failing at it?
      - When did the most recent VWAP reclaim/breakdown occur?
      - Compare VWAP position across your hold period to assess trend quality

    Each row includes: date, close, vwap, distance_pct ((close-vwap)/vwap×100),
    position ('above_vwap' / 'below_vwap').

    Args:
        symbol: Ticker symbol (e.g. "MSFT")
        since_days: Number of calendar days of history to return (default 90)
        lookback: Rolling window for VWAP calculation in bars (default 20)
        interval: Bar interval — '1d' daily (default), '1wk' weekly, '1mo' monthly

    Returns:
        dict with keys: symbol, lookback_bars, data_points, history (list of daily rows)
    """
    return rest_client.get(
        f"/api/securities/{symbol}/vwap/history",
        since_days=since_days,
        lookback=lookback,
        interval=interval,
    )


@mcp.tool()
def get_relative_strength_history(symbol: str, since_days: int = 90, rs_period: int = 21, interval: str = "1d") -> dict:
    """
    Return historical daily relative strength vs SPY, QQQ, and the symbol's sector ETF.

    RS is computed as the symbol's trailing `rs_period`-bar return minus the benchmark's
    return over the same period. Positive = outperforming, negative = underperforming.
    All data comes from ohlcv_cache.db — no network calls if benchmarks are cached.

    INTENDED USE — essential for multi-week equity holds:
      - Is relative strength improving (rotation into this stock) or deteriorating?
      - Identify when a stock transitions from laggard to outperformer
      - Spot early divergence: stock price rising but RS falling = weak rally

    Each row includes: date, close, return_pct (trailing rs_period bars), rs_vs_spy,
    rs_vs_qqq, rs_vs_sector, rs_label ('leader'/'outperforming'/'neutral'/'laggard'/'weak').

    Note: sector ETF is looked up from the symbol's cached sector tag. If the sector ETF
    is not in ohlcv_cache.db, rs_vs_sector will be null for that row.

    Args:
        symbol: Ticker symbol (e.g. "MSFT")
        since_days: Number of calendar days of history to return (default 90)
        rs_period: Rolling window for return calculation in bars (default 21 = ~1 month)
        interval: Bar interval — '1d' daily (default), '1wk' weekly, '1mo' monthly

    Returns:
        dict with keys: symbol, rs_period_bars, sector_etf, data_points, history
    """
    return rest_client.get(
        f"/api/securities/{symbol}/relative-strength/history",
        since_days=since_days,
        rs_period=rs_period,
        interval=interval,
    )


@mcp.tool()
def get_relative_strength(symbol: str) -> dict:
    """Compute relative strength vs SPY, QQQ, and the stock's sector ETF.

    Returns 1/3/6/12-month total returns (%) for the stock and three
    benchmarks, excess return vs SPY over 12 months (rs_ratio_vs_spy),
    and two summary labels:

      relative_strength_label:
        leader        — outperforms SPY by ≥ 20 pp over 12 months
        outperforming — outperforms SPY by 5–19 pp
        neutral       — within ±5 pp of SPY
        laggard       — underperforms SPY by 5–19 pp
        weak          — underperforms SPY by ≥ 20 pp

      sector_momentum:
        sector_leading  — sector ETF beats SPY by ≥ 5 pp over 3 months
        sector_neutral  — sector within ±5 pp of SPY
        sector_lagging  — sector trails SPY by > 5 pp

    The best long-entry combination is leader + sector_leading.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
    """
    return rest_client.get(f"/api/securities/{symbol}/relative-strength")


@mcp.tool()
def get_trade_recommendation(symbol: str, capital: float = 5000.0) -> dict:
    """Synthesise all available technical and market signals into a single actionable trade recommendation.

    Runs the full analysis suite — RSI, MACD, Stochastic, Bollinger Bands, volume
    analysis, candlestick patterns, dark pool proxy, short interest, bid/ask spread,
    unusual call sweeps, delta-adjusted OI (MM hedge flows), options market
    positioning, stop-loss analysis, and news sentiment (FinBERT) — then scores each
    signal and produces a concrete recommendation with entry, target, stop, position
    size, and risk/reward.

    Trade types:
      LONG_CALL        — strong bull signal (net_score ≥ 5), low IV (avg_iv < 40%)
      BULL_CALL_SPREAD — strong bull signal, high IV (avg_iv ≥ 40%)
      LONG_STOCK       — moderate bull signal (net_score 3–4)
      WEAK_LONG        — marginal bull signal (net_score 1–2); small position
      SKIP             — neutral / conflicting signals (net_score -2 to 0)
      LONG_PUT         — moderate/strong bear signal
      BEAR_PUT_SPREAD  — strong bear signal (net_score ≤ -5), high IV

    Squeeze override: squeeze_potential == HIGH AND net_score ≥ 3 → forces LONG_CALL.docs/

    Signal scoring additions vs prior version:
      Unusual calls upgraded: strong sweep → +2 bull (was +1); moderate → +1
      Signal 12 (DAOI bounce): MM buy-on-rally flow → +2 bull (strong/moderate), +1 (weak)
      Signal 13 (Options positioning): net call delta > 50K share-equiv → +1 bull;
                                       net put delta < -50K → +1 bear
      Signal 19 (News sentiment): strong positive (≥60% positive articles) → +2 bull;
                                   moderate positive (≥40%) → +1 bull;
                                   strong negative (≥60% negative) → +2 bear;
                                   moderate negative (≥40%) → +1 bear

    Position sizing uses 2% of capital as the risk budget:
      Stock:   shares    = floor(risk_budget / (price − technical_stop))
      Options: contracts = floor(risk_budget / (atm_ask × 100)), minimum 1

    Args:
        symbol:  Stock ticker symbol (e.g. 'AAPL')
        capital: Total capital available for this trade (default: $5,000)
    """
    return rest_client.get(f"/api/securities/{symbol}/recommendation", capital=capital)


if __name__ == "__main__":
    # Streamable HTTP transport (Rule 6). PORT is overridable so the same image
    # can be reused per wrapper in docker-compose / Cloud Run; default is this
    # server's assigned port.
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "6001")))
