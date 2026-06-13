import math
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parent
for path in (PROJECT_ROOT, MCP_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

import yfinance as yf
from fastmcp import FastMCP
from ohlcv_cache import get_history, period_to_days
from quantcore.services.registry import get_services

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
    return get_services().sentiment.get_news(symbol, max_articles)


@mcp.tool()
def get_stock_price(symbol: str) -> dict:
    """Get the current stock price, Bollinger Bands (20-day, 2σ), and options chain summary for a given ticker symbol."""
    return get_services().prices.get_stock_price(symbol)

@mcp.tool()
def get_full_options_chain(symbol: str) -> dict:
    """Fetch the full options chain (all strikes, all expirations) for a ticker and persist to DB."""
    return get_services().options.get_full_options_chain(symbol)


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
    return get_services().options.get_option_contracts(
        symbol=symbol,
        expirations=expirations,
        strikes=strikes,
        kind=kind,
        max_snapshot_age_minutes=max_snapshot_age_minutes,
        allow_live_fetch=allow_live_fetch,
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
    return get_services().options.price_vertical_spread(
        symbol=symbol,
        expiration=expiration,
        long_strike=long_strike,
        short_strike=short_strike,
        kind=kind,
        max_snapshot_age_minutes=max_snapshot_age_minutes,
        allow_live_fetch=allow_live_fetch,
    )


@mcp.tool()
def get_rsi(symbol: str, period: int = 14, interval: str = "1d") -> dict:
    """Calculate the Relative Strength Index (RSI) for a stock symbol.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
        period: Number of periods for RSI calculation (default: 14)
        interval: Price interval — '1d' for daily (default), '1wk' for weekly, '1mo' for monthly
    """
    return get_services().prices.get_rsi(symbol, period, interval)


@mcp.tool()
def get_macd(symbol: str, interval: str = "1d") -> dict:
    """Calculate MACD (Moving Average Convergence Divergence) for a stock symbol.

    Uses standard parameters: fast EMA=12, slow EMA=26, signal EMA=9.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
        interval: Price interval — '1d' for daily (default), '1wk' for weekly, '1mo' for monthly
    """
    return get_services().prices.get_macd(symbol, interval)


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
    return get_services().prices.get_stochastic(symbol, k_period, d_period, interval)


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
    return get_services().prices.get_volume_analysis(symbol, lookback, interval)


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
    return get_services().prices.get_obv(symbol, lookback, interval)


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
    return get_services().prices.get_vwap(symbol, lookback, interval)


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
    return get_services().prices.get_candlestick_patterns(symbol, lookback, interval)


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
    return get_services().prices.get_higher_lows(symbol, swing_bars, lookback_swings, interval)


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
    return get_services().prices.get_gap_analysis(symbol, min_gap_pct, lookback, interval)


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
    return get_services().options.get_unusual_calls(
        symbol, min_volume, min_vol_oi_ratio, max_expirations
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
    return get_services().options.get_delta_adjusted_oi(
        symbol, max_expirations, risk_free_rate
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
    return get_services().options.get_gamma_wall_history(symbol, since_days)


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
    return get_services().prices.get_historical_drawdown(symbol, lookback_days)


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

_SECTOR_ETF_MAP: dict[str, str] = {
    "Technology":             "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples":       "XLP",
    "Energy":                 "XLE",
    "Financials":             "XLF",
    "Health Care":            "XLV",
    "Industrials":            "XLI",
    "Materials":              "XLB",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
}


def _get_sector_etf(symbol: str) -> str:
    """
    Determine the sector ETF for a symbol. Defaults to 'XLK' if not found.
    """
    try:
        info = yf.Ticker(symbol.upper()).info or {}
        sector = info.get("sector")
        return _SECTOR_ETF_MAP.get(sector or "", "XLK")
    except Exception:
        return "XLK"


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
    return get_services().prices.get_vwap_history(symbol, since_days, lookback, interval)


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
    import pandas as pd
    symbol = symbol.upper().strip()
    fetch_days = since_days + rs_period + 10
    sym_df  = get_history(symbol, interval=interval, days=fetch_days)
    spy_df  = get_history("SPY",  interval=interval, days=fetch_days)
    qqq_df  = get_history("QQQ",  interval=interval, days=fetch_days)
    if sym_df is None or sym_df.empty or spy_df is None or spy_df.empty:
        return {"symbol": symbol, "error": "Insufficient OHLCV data in cache", "history": []}

    # Determine sector ETF
    sector_etf = _get_sector_etf(symbol)
    sec_df = get_history(sector_etf, interval=interval, days=fetch_days) if sector_etf else None

    def rolling_return(df):
        closes = df["Close"]
        return closes.pct_change(rs_period) * 100   # trailing rs_period-bar % return

    sym_ret  = rolling_return(sym_df)
    spy_ret  = rolling_return(spy_df)
    qqq_ret  = rolling_return(qqq_df)
    sec_ret  = rolling_return(sec_df) if sec_df is not None and not sec_df.empty else None

    aligned = sym_ret.to_frame("sym").join(spy_ret.rename("spy")).join(qqq_ret.rename("qqq"))
    if sec_ret is not None:
        aligned = aligned.join(sec_ret.rename("sec"))
    aligned = aligned.dropna(subset=["sym", "spy", "qqq"]).tail(since_days)

    def rs_label(vs_spy, vs_qqq):
        avg = (vs_spy + vs_qqq) / 2
        if avg >= 5:   return "leader"
        if avg >= 1:   return "outperforming"
        if avg >= -1:  return "neutral"
        if avg >= -5:  return "laggard"
        return "weak"

    history = []
    for idx, row in aligned.iterrows():
        close = sym_df.loc[sym_df.index <= idx, "Close"].iloc[-1] if idx in sym_df.index else None
        vs_spy = round(row["sym"] - row["spy"], 2)
        vs_qqq = round(row["sym"] - row["qqq"], 2)
        vs_sec = round(row["sym"] - row.get("sec"), 2) if "sec" in row and pd.notna(row.get("sec")) else None
        history.append({
            "date": idx.strftime("%Y-%m-%d"),
            "close": round(close, 2) if close else None,
            "return_pct": round(row["sym"], 2),
            "rs_vs_spy": vs_spy,
            "rs_vs_qqq": vs_qqq,
            "rs_vs_sector": vs_sec,
            "sector_etf": sector_etf,
            "rs_label": rs_label(vs_spy, vs_qqq),
        })
    return {
        "symbol": symbol,
        "rs_period_bars": rs_period,
        "sector_etf": sector_etf,
        "data_points": len(history),
        "history": history,
    }


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
    sym = symbol.upper()

    result: dict = {
        "symbol":                   sym,
        "sector":                   None,
        "sector_etf":               None,
        "returns": {
            "stock":  {"1m": None, "3m": None, "6m": None, "12m": None},
            "spy":    {"1m": None, "3m": None, "6m": None, "12m": None},
            "qqq":    {"1m": None, "3m": None, "6m": None, "12m": None},
            "sector": {"1m": None, "3m": None, "6m": None, "12m": None},
        },
        "rs_ratio_vs_spy":          None,
        "rs_ratio_vs_qqq":          None,
        "rs_ratio_vs_sector":       None,
        "relative_strength_label":  "unknown",
        "sector_momentum":          "unknown",
    }

    try:
        info = yf.Ticker(sym).info or {}
        sector = info.get("sector")
        result["sector"] = sector
        sector_etf = _SECTOR_ETF_MAP.get(sector or "", "XLK")
        result["sector_etf"] = sector_etf
    except Exception:
        sector_etf = "XLK"

    tickers_to_fetch = list(dict.fromkeys([sym, "SPY", "QQQ", sector_etf]))

    try:
        data = yf.download(
            tickers_to_fetch,
            period="400d",
            auto_adjust=True,
            progress=False,
        )
        if "Close" in data.columns:
            closes = data["Close"]
        else:
            closes = data  # single-ticker fallback

        def _pct_return(series: "pd.Series", trading_days: int) -> "float | None":
            s = series.dropna()
            if len(s) < trading_days + 1:
                return None
            start = float(s.iloc[-trading_days - 1])
            end = float(s.iloc[-1])
            if start <= 0:
                return None
            return round((end / start - 1) * 100, 2)

        periods = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
        ticker_keys = {
            "stock":  sym,
            "spy":    "SPY",
            "qqq":    "QQQ",
            "sector": sector_etf,
        }

        for key, ticker in ticker_keys.items():
            col = closes[ticker] if ticker in closes.columns else None
            if col is None and closes.ndim == 1:
                col = closes
            if col is None:
                continue
            for period_name, days in periods.items():
                result["returns"][key][period_name] = _pct_return(col, days)

        s12   = result["returns"]["stock"]["12m"]
        spy12 = result["returns"]["spy"]["12m"]
        qqq12 = result["returns"]["qqq"]["12m"]
        sec12 = result["returns"]["sector"]["12m"]

        if s12 is not None and spy12 is not None:
            result["rs_ratio_vs_spy"] = round(s12 - spy12, 2)
        if s12 is not None and qqq12 is not None:
            result["rs_ratio_vs_qqq"] = round(s12 - qqq12, 2)
        if s12 is not None and sec12 is not None:
            result["rs_ratio_vs_sector"] = round(s12 - sec12, 2)

        rs = result["rs_ratio_vs_spy"]
        if rs is not None:
            if rs >= 20:
                result["relative_strength_label"] = "leader"
            elif rs >= 5:
                result["relative_strength_label"] = "outperforming"
            elif rs >= -5:
                result["relative_strength_label"] = "neutral"
            elif rs >= -20:
                result["relative_strength_label"] = "laggard"
            else:
                result["relative_strength_label"] = "weak"

        spy3 = result["returns"]["spy"]["3m"]
        sec3 = result["returns"]["sector"]["3m"]
        if spy3 is not None and sec3 is not None:
            diff = sec3 - spy3
            if diff >= 5:
                result["sector_momentum"] = "sector_leading"
            elif diff >= -5:
                result["sector_momentum"] = "sector_neutral"
            else:
                result["sector_momentum"] = "sector_lagging"

    except Exception:
        pass

    return result


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
    sym = symbol.upper()
    signals_collected = 0
    drivers: list[str] = []
    warnings: list[str] = []

    bull_score = 0
    bear_score = 0

    # ── 1. Price + Bollinger Bands + Options ──────────────────────────────
    price      = None
    bb_upper   = None
    bb_lower   = None
    bb_pos     = None
    avg_iv     = None
    put_call_ratio = None
    atm_call_ask   = None
    atm_put_ask    = None

    try:
        price_data = get_stock_price(sym)
        price = price_data["price"]
        bb = price_data.get("bollinger_bands")
        if bb:
            bb_upper  = bb["upper"]
            bb_lower  = bb["lower"]
            if bb_upper != bb_lower:
                bb_pos = round((price - bb_lower) / (bb_upper - bb_lower), 3)

            if bb_pos is not None:
                if bb_pos <= 0:
                    bull_score += 2
                    drivers.append(f"BB position {bb_pos:.2f} — at/below lower band (oversold)")
                elif bb_pos >= 1:
                    bear_score += 2
                    drivers.append(f"BB position {bb_pos:.2f} — at/above upper band (overbought)")

        options = price_data.get("options")
        if options:
            put_call_ratio = options.get("put_call_ratio")
            calls_data = options.get("calls", {})
            puts_data  = options.get("puts",  {})
            avg_iv     = calls_data.get("avg_iv_pct", 0.0)

            atm_calls = calls_data.get("atm_contracts", [])
            if atm_calls:
                atm_c = min(atm_calls, key=lambda c: abs(c["strike"] - price))
                atm_call_ask = atm_c.get("ask", 0.0) or 0.0

            atm_puts = puts_data.get("atm_contracts", [])
            if atm_puts:
                atm_p = min(atm_puts, key=lambda c: abs(c["strike"] - price))
                atm_put_ask = atm_p.get("ask", 0.0) or 0.0

            if put_call_ratio is not None and put_call_ratio > 2.0:
                bear_score += 1
                drivers.append(f"P/C ratio {put_call_ratio:.2f} — elevated put activity (bearish)")

        signals_collected += 1
    except Exception:
        pass

    if price is None:
        return {
            "symbol":     sym,
            "error":      f"Could not retrieve price for {sym}. Cannot generate recommendation.",
            "trade_type": "SKIP",
            "action":     "HOLD",
        }

    # ── 2. RSI ────────────────────────────────────────────────────────────
    rsi_val = None
    try:
        rsi_data = get_rsi(sym)
        rsi_val  = rsi_data["rsi"]

        if rsi_val < 30:
            bull_score += 3   # +2 for <35, +1 extra for <30
            drivers.append(f"RSI {rsi_val:.1f} — deeply oversold")
        elif rsi_val < 35:
            bull_score += 2
            drivers.append(f"RSI {rsi_val:.1f} — oversold")
        elif rsi_val > 70:
            bear_score += 3   # +2 for >65, +1 extra for >70
            drivers.append(f"RSI {rsi_val:.1f} — deeply overbought")
        elif rsi_val > 65:
            bear_score += 2
            drivers.append(f"RSI {rsi_val:.1f} — overbought")

        signals_collected += 1
    except Exception:
        pass

    # ── 3. MACD ───────────────────────────────────────────────────────────
    macd_crossover = None
    try:
        macd_data      = get_macd(sym)
        macd_crossover = macd_data["crossover"]

        if macd_crossover == "bullish_crossover":
            bull_score += 2
            drivers.append("MACD bullish crossover — momentum turning up")
        elif macd_crossover == "bullish":
            bull_score += 1
            drivers.append("MACD bullish — positive momentum")
        elif macd_crossover == "bearish_crossover":
            bear_score += 2
            drivers.append("MACD bearish crossover — momentum turning down")
        elif macd_crossover == "bearish":
            bear_score += 1
            drivers.append("MACD bearish — negative momentum")

        signals_collected += 1
    except Exception:
        pass

    # ── 4. Stochastic ─────────────────────────────────────────────────────
    stoch_k = None
    try:
        stoch_data = get_stochastic(sym)
        stoch_k    = stoch_data["k"]

        if stoch_k < 25:
            bull_score += 2
            drivers.append(f"Stochastic %K {stoch_k:.1f} — oversold")
        elif stoch_k > 75:
            bear_score += 2
            drivers.append(f"Stochastic %K {stoch_k:.1f} — overbought")

        signals_collected += 1
    except Exception:
        pass

    # ── 5. Volume Analysis ────────────────────────────────────────────────
    obv_divergence = False
    try:
        vol_data       = get_volume_analysis(sym)
        bottom_signal  = vol_data.get("bottom_signal", "none")
        obv_divergence = vol_data.get("obv_divergence", False)
        climax_events  = vol_data.get("climax_events", [])

        bs_lower = bottom_signal.lower()
        if "strong" in bs_lower or "moderate" in bs_lower:
            bull_score += 2
            drivers.append(f"Volume bottom signal: {bottom_signal}")

        if obv_divergence:
            bull_score += 1
            drivers.append("OBV bullish divergence — accumulation beneath the surface")

        recent_up_climax = any(
            e.get("direction") == "up" for e in climax_events[-3:]
        ) if climax_events else False
        if recent_up_climax:
            bear_score += 2
            drivers.append("Volume exhaustion top — high-volume climax on up day, potential reversal")

        signals_collected += 1
    except Exception:
        pass

    # ── 6. Candlestick Patterns ───────────────────────────────────────────
    try:
        cs_data   = get_candlestick_patterns(sym)
        patterns  = cs_data.get("patterns_found", [])

        bullish_pats = [p for p in patterns if p.get("bias") == "bullish"]
        bearish_pats = [p for p in patterns if p.get("bias") == "bearish"]

        if bullish_pats:
            best = max(bullish_pats, key=lambda p: p.get("strength_score", 0))
            bull_score += 1
            drivers.append(
                f"Candlestick: {best['pattern']} ({best['strength']} bullish reversal)"
            )
        elif bearish_pats:
            best = max(bearish_pats, key=lambda p: p.get("strength_score", 0))
            bear_score += 1
            drivers.append(
                f"Candlestick: {best['pattern']} ({best['strength']} bearish signal)"
            )

        signals_collected += 1
    except Exception:
        pass

    # ── 7. Unusual Calls ─────────────────────────────────────────────────
    unusual_call_activity = False
    try:
        uc_data      = get_unusual_calls(sym)
        sweep_signal = uc_data.get("sweep_signal", "none")

        if sweep_signal == "strong":
            unusual_call_activity = True
            bull_score += 2
            drivers.append("Unusual call activity: strong sweep signal — aggressive institutional buying")
        elif sweep_signal == "moderate":
            unusual_call_activity = True
            bull_score += 1
            drivers.append("Unusual call activity: moderate sweep signal")

        signals_collected += 1
    except Exception:
        pass

    # ── 8. Stop Loss Analysis ─────────────────────────────────────────────
    technical_stop    = None
    trailing_stop_pct = None
    try:
        sl_data           = get_stop_loss_analysis(sym)
        stops             = sl_data.get("stops", {})
        technical_stop    = stops.get("technical_stop")
        trailing_stop_pct = stops.get("trailing_stop_pct")

        signals_collected += 1
    except Exception:
        pass

    # ── 9. Short Interest ─────────────────────────────────────────────────
    squeeze_potential = "LOW"
    try:
        si_data           = get_services().microstructure.get_short_interest(sym)
        squeeze_potential = si_data.get("squeeze_potential", "LOW")

        if squeeze_potential == "HIGH":
            bull_score += 1
            drivers.append(
                f"Short squeeze potential HIGH — {si_data.get('squeeze_note', '')}"
            )

        signals_collected += 1
    except Exception:
        pass

    # ── 10. Dark Pool ─────────────────────────────────────────────────────
    dark_pool_signal = "none"
    try:
        dp_data          = get_services().microstructure.get_dark_pool(sym)
        dark_pool_signal = dp_data.get("net_signal", "none")

        if dark_pool_signal == "accumulation":
            bull_score += 2
            drivers.append("Dark pool: accumulation — institutions absorbing sell pressure")
        elif dark_pool_signal == "distribution":
            bear_score += 2
            drivers.append("Dark pool: distribution — institutions absorbing buy pressure")

        signals_collected += 1
    except Exception:
        pass

    # ── 11. Bid/Ask Spread ────────────────────────────────────────────────
    spread_vs_norm = "unknown"
    try:
        bas_data       = get_services().microstructure.get_bid_ask_spread(sym)
        spread_vs_norm = bas_data.get("spread_vs_norm", "unknown")

        if spread_vs_norm == "narrowing":
            bull_score += 1
            drivers.append("Bid/ask spread narrowing — liquidity returning (bounce setup)")
        elif spread_vs_norm == "widening":
            bear_score += 1
            drivers.append("Bid/ask spread widening — liquidity stress / fear elevated")

        signals_collected += 1
    except Exception:
        pass

    # ── 12. Delta-Adjusted OI (MM Hedge Flows) ───────────────────────────
    daoi_signal     = "none"
    mm_hedge_bias   = None
    gamma_wall      = None
    delta_flip      = None
    net_daoi_shares = None
    try:
        daoi_data       = get_delta_adjusted_oi(sym)
        daoi_signal     = daoi_data.get("signal", "none")
        mm_hedge_bias   = daoi_data.get("mm_hedge_bias")
        gamma_wall      = daoi_data.get("gamma_wall_strike")
        delta_flip      = daoi_data.get("delta_flip_strike")
        net_daoi_shares = daoi_data.get("net_daoi_shares")

        if daoi_signal == "strong":
            bull_score += 2
            drivers.append("DAOI: strong MM buy-on-rally flow — mechanical support amplifies upside")
        elif daoi_signal == "moderate":
            bull_score += 2
            drivers.append("DAOI: moderate MM buy-on-rally flow — hedging supports rally")
        elif daoi_signal == "weak":
            bull_score += 1
            drivers.append("DAOI: weak MM buy-on-rally bias")

        if mm_hedge_bias == "sell_on_rally":
            warnings.append(
                "MM hedge bias: sell_on_rally — market makers must sell stock as price rises (mechanical resistance)"
            )

        signals_collected += 1
    except Exception:
        pass

    # ── 13. Options Market Directional Positioning ───────────────────────
    if net_daoi_shares is not None:
        if net_daoi_shares > 50_000:
            bull_score += 1
            drivers.append(
                f"Options positioning: {net_daoi_shares:,.0f} net call delta — institutions overwhelmingly long"
            )
        elif net_daoi_shares < -50_000:
            bear_score += 1
            drivers.append(
                f"Options positioning: {net_daoi_shares:,.0f} net delta — heavy put/defensive hedging"
            )
        signals_collected += 1

    # ── 14. Earnings Calendar ─────────────────────────────────────────────
    earnings_days_out = None
    earnings_risk = "UNKNOWN"
    pre_earnings_setup = False
    try:
        ec_data = get_services().fundamentals.get_earnings_calendar(sym)
        earnings_days_out = ec_data.get("days_to_earnings")
        earnings_risk = ec_data.get("risk_level", "UNKNOWN")
        pre_earnings_setup = ec_data.get("pre_earnings_setup", False)
        avg_move_pct = ec_data.get("historical_avg_move_pct")

        if earnings_days_out is not None:
            if earnings_days_out < 7:
                warnings.append(
                    f"CRITICAL: Earnings in {earnings_days_out} days — avoid new options positions (IV crush imminent)"
                )
            elif earnings_days_out < 14:
                warnings.append(
                    f"Earnings in {earnings_days_out} days — options positions carry IV crush risk post-earnings"
                )
            elif pre_earnings_setup:
                bull_score += 1
                note = f"{earnings_days_out} days to earnings"
                if avg_move_pct:
                    note += f", avg historical move ±{avg_move_pct:.1f}%"
                drivers.append(f"Pre-earnings IV expansion: {note} — long calls benefit from IV buildup")

        signals_collected += 1
    except Exception:
        pass

    # ── 15. Fundamental Score ─────────────────────────────────────────────
    fund_composite = None
    try:
        fund_data = get_services().fundamentals.get_fundamental_score(sym)
        fund_composite = fund_data.get("composite_score", 0)
        fund_label = fund_data.get("fundamental_label", "average")

        if fund_composite >= 8:
            bull_score += 2
            drivers.append(f"Fundamentals: {fund_label} — strong compounder (score {fund_composite})")
        elif fund_composite >= 4:
            bull_score += 1
            drivers.append(f"Fundamentals: {fund_label} (score {fund_composite})")
        elif fund_composite <= -4:
            bear_score += 2
            drivers.append(f"Fundamentals: {fund_label} — deteriorating business (score {fund_composite})")
        elif fund_composite < 0:
            bear_score += 1
            drivers.append(f"Fundamentals: {fund_label} (score {fund_composite})")

        signals_collected += 1
    except Exception:
        pass

    # ── 16. Revenue Growth Trajectory ────────────────────────────────────
    try:
        rev_data = get_services().fundamentals.get_revenue_growth(sym)
        trajectory = rev_data.get("trajectory", "")

        if trajectory in ("accelerating", "inflecting_positive"):
            bull_score += 1
            drivers.append(f"Revenue: {trajectory} — fundamental momentum building")
        elif trajectory in ("decelerating", "inflecting_negative"):
            bear_score += 1
            drivers.append(f"Revenue: {trajectory} — fundamental headwind")

        signals_collected += 1
    except Exception:
        pass

    # ── 17. Earnings Acceleration (CAN SLIM 'A') ─────────────────────────
    try:
        ea_data = get_services().fundamentals.get_earnings_acceleration(sym)
        ea_score = ea_data.get("acceleration_score", 0)
        ea_label = ea_data.get("acceleration_label", "")

        if ea_score > 0:
            bull_score += ea_score
            drivers.append(f"EPS acceleration: {ea_label} — institutional accumulation signal")
        elif ea_score < 0:
            bear_score += abs(ea_score)
            drivers.append(f"EPS acceleration: {ea_label} — earnings deceleration warning")

        signals_collected += 1
    except Exception:
        pass

    # ── 18. Relative Strength vs Market & Sector ─────────────────────────
    try:
        rs_data = get_relative_strength(sym)
        rs_label = rs_data.get("relative_strength_label", "")
        rs_vs_spy = rs_data.get("rs_ratio_vs_spy")
        sector_momentum = rs_data.get("sector_momentum", "")

        if rs_label == "leader":
            bull_score += 2
            drivers.append(f"Relative strength: market leader ({rs_vs_spy:+.1f}% vs SPY over 12m)")
        elif rs_label == "outperforming":
            bull_score += 1
            drivers.append(f"Relative strength: outperforming SPY ({rs_vs_spy:+.1f}% over 12m)")
        elif rs_label == "laggard":
            bear_score += 1
            drivers.append(f"Relative strength: laggard ({rs_vs_spy:+.1f}% vs SPY over 12m)")
        elif rs_label == "weak":
            bear_score += 1
            drivers.append(f"Relative strength: weak vs SPY ({rs_vs_spy:+.1f}% over 12m)")

        if sector_momentum == "sector_leading" and bull_score > bear_score:
            bull_score += 1
            drivers.append(
                f"Sector momentum: {rs_data.get('sector_etf', 'sector ETF')} leading market — macro tailwind"
            )

        signals_collected += 1
    except Exception:
        pass

    # ── 19. News Sentiment (FinBERT) ──────────────────────────────────────
    news_sentiment = None
    try:
        news_data = get_services().sentiment.get_news(sym)
        sentiment_summary = news_data.get("sentiment_summary")
        if sentiment_summary:
            scored_count = sentiment_summary.get("scored_count", 0)
            if scored_count > 0:
                pos_pct = sentiment_summary["positive_count"] / scored_count
                neg_pct = sentiment_summary["negative_count"] / scored_count

                if pos_pct >= 0.60:
                    bull_score += 2
                    drivers.append(f"News sentiment: strongly positive ({pos_pct:.0%} of articles bullish)")
                elif pos_pct >= 0.40:
                    bull_score += 1
                    drivers.append(f"News sentiment: moderately positive ({pos_pct:.0%} of articles bullish)")
                elif neg_pct >= 0.60:
                    bear_score += 2
                    drivers.append(f"News sentiment: strongly negative ({neg_pct:.0%} of articles bearish)")
                elif neg_pct >= 0.40:
                    bear_score += 1
                    drivers.append(f"News sentiment: moderately negative ({neg_pct:.0%} of articles bearish)")

            news_sentiment = sentiment_summary
        signals_collected += 1
    except Exception:
        pass

    # ── Aggregate Scores ──────────────────────────────────────────────────
    net_score = bull_score - bear_score

    if abs(net_score) >= 5:
        confidence = "HIGH"
    elif abs(net_score) >= 3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    avg_iv_val = avg_iv if avg_iv is not None else 0.0
    high_iv    = avg_iv_val >= 40.0

    # Squeeze override: HIGH short interest + sufficient bull score → force LONG_CALL
    squeeze_override = squeeze_potential == "HIGH" and net_score >= 3

    # ── Trade Type Selection ──────────────────────────────────────────────
    if squeeze_override:
        trade_type = "LONG_CALL"
        action     = "BUY"
    elif net_score >= 5:
        trade_type = "BULL_CALL_SPREAD" if high_iv else "LONG_CALL"
        action     = "BUY"
    elif net_score >= 3:
        trade_type = "LONG_STOCK"
        action     = "BUY"
    elif net_score >= 1:
        trade_type = "WEAK_LONG"
        action     = "BUY"
        warnings.append("Low confidence — consider reducing position size")
    elif net_score >= -2:
        trade_type = "SKIP"
        action     = "HOLD"
        warnings.append("Signals conflicting or neutral — no clear directional edge")
    elif net_score >= -4:
        trade_type = "LONG_PUT"
        action     = "BUY"
    else:   # net_score <= -5
        trade_type = "BEAR_PUT_SPREAD" if high_iv else "LONG_PUT"
        action     = "BUY"

    is_options_trade = trade_type in ("LONG_CALL", "BULL_CALL_SPREAD", "LONG_PUT", "BEAR_PUT_SPREAD")
    if earnings_days_out is not None and earnings_days_out < 7 and is_options_trade and net_score < 7:
        trade_type = "SKIP"
        action = "HOLD"
        warnings.append(
            f"Earnings override: {earnings_days_out} days to earnings — options trade suppressed to avoid IV crush"
        )

    is_long  = trade_type in ("LONG_CALL", "BULL_CALL_SPREAD", "LONG_STOCK", "WEAK_LONG")
    is_short = trade_type in ("LONG_PUT", "BEAR_PUT_SPREAD")

    # ── Target ────────────────────────────────────────────────────────────
    target = None
    if is_long and bb_upper is not None:
        target = bb_upper
    elif is_short and bb_lower is not None:
        target = bb_lower

    # ── Stop Loss ─────────────────────────────────────────────────────────
    stop_loss = None
    if is_long:
        stop_loss = technical_stop   # get_stop_loss_analysis places this below price
        if stop_loss is None or stop_loss >= price:
            if trailing_stop_pct is not None:
                stop_loss = round(price * (1 - trailing_stop_pct / 100), 2)
            else:
                stop_loss = round(price * 0.95, 2)
            if technical_stop is not None and technical_stop >= price:
                warnings.append(
                    f"Technical stop ${technical_stop:.2f} was above entry — using trailing stop fallback"
                )
    elif is_short:
        if trailing_stop_pct is not None:
            stop_loss = round(price * (1 + trailing_stop_pct / 100), 2)
        else:
            stop_loss = round(price * 1.05, 2)

    # ── Risk/Reward ───────────────────────────────────────────────────────
    risk_reward_ratio = None
    if target is not None and stop_loss is not None:
        reward = abs(target - price)
        risk   = abs(price - stop_loss)
        if risk > 0:
            risk_reward_ratio = round(reward / risk, 2)

    # ── Position Sizing (2% risk budget) ─────────────────────────────────
    risk_budget    = capital * 0.02
    position_size  = 0
    estimated_cost = 0.0

    is_options = trade_type in ("LONG_CALL", "BULL_CALL_SPREAD", "LONG_PUT", "BEAR_PUT_SPREAD")

    if trade_type == "SKIP":
        position_size  = 0
        estimated_cost = 0.0
    elif is_options:
        option_ask = atm_call_ask if is_long else atm_put_ask
        if option_ask and option_ask > 0:
            contracts     = math.floor(risk_budget / (option_ask * 100))
            position_size = max(1, contracts)
            estimated_cost = min(position_size * option_ask * 100, capital)
        else:
            position_size  = 1
            estimated_cost = 0.0
    else:
        if stop_loss is not None:
            risk_per_share = abs(price - stop_loss)
            if risk_per_share > 0:
                position_size  = max(0, math.floor(risk_budget / risk_per_share))
                estimated_cost = min(position_size * price, capital)

    # ── Contradiction Warnings ────────────────────────────────────────────
    if bull_score > 0 and bear_score > 0:
        warnings.append(
            f"Mixed signals: {bull_score} bull pts vs {bear_score} bear pts — some signals contradict"
        )
    if rsi_val is not None and macd_crossover is not None:
        if rsi_val < 30 and "bearish" in str(macd_crossover):
            warnings.append("RSI deeply oversold but MACD still bearish — wait for momentum confirmation")
        elif rsi_val > 70 and "bullish" in str(macd_crossover):
            warnings.append("RSI deeply overbought with MACD still bullish — caution, late in the move")
    if unusual_call_activity and mm_hedge_bias == "sell_on_rally":
        warnings.append(
            "Strong call sweeps vs MM sell-on-rally: smart money buying but structure caps upside — confirm with price action"
        )
    if net_daoi_shares is not None and net_daoi_shares > 50_000 and net_score < -2:
        warnings.append(
            "Heavy call positioning conflicts with bearish technical signals — options market disagrees with price action"
        )

    # ── Options Context ───────────────────────────────────────────────────
    options_context = None
    if is_options:
        options_context = {
            "avg_iv_pct":      avg_iv_val,
            "high_iv":         high_iv,
            "atm_call_ask":    atm_call_ask,
            "atm_put_ask":     atm_put_ask,
            "put_call_ratio":  put_call_ratio,
            "mm_hedge_bias":   mm_hedge_bias,
            "gamma_wall":      gamma_wall,
            "delta_flip":      delta_flip,
            "net_daoi_shares": net_daoi_shares,
        }

    return {
        "symbol":            sym,
        "price":             price,
        "trade_type":        trade_type,
        "action":            action,
        "confidence":        confidence,
        "bull_score":        bull_score,
        "bear_score":        bear_score,
        "net_score":         net_score,
        "entry":             price,
        "target":            target,
        "stop_loss":         stop_loss,
        "risk_reward_ratio": risk_reward_ratio,
        "position_size":     position_size,
        "estimated_cost":    round(estimated_cost, 2),
        "drivers":           drivers,
        "warnings":          warnings,
        "signals_collected": signals_collected,
        "options_context":   options_context,
        "news_sentiment":    news_sentiment,
    }


if __name__ == "__main__":
    from quantcore.db import init_schema
    init_schema()
    mcp.run()
