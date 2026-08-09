"""
Options analysis tool — screens a watchlist (the DB-backed one over MCP/REST,
or a YAML file via ``--watchlist`` on the CLI), fetches live data via yfinance,
scores each security using Bollinger Band position and options Put/Call ratio,
and prints ranked long candidates and put trade setups.

No LLM required — all scoring logic is rule-based.

This module is a *dual-purpose adapter* (architectural standard v2 §5.2, §11):

  - **The MCP tools are HTTP gateway wrappers** (Rule 6 —
    ``AI Agent → MCP wrapper → REST tier → Service``): each service-backed
    ``@mcp.tool()`` translates its call into a single HTTP request against the
    FastAPI front door via ``mcp_gateway.rest_client``. ``mcp_health_check`` is
    the one exception — it returns local version/config only and makes no
    service call.

  - **The CLI ``main()`` + ``print_*`` console helpers stay in-process** and call
    ``get_services()`` directly. The CLI is a local batch entry point, NOT an AI
    agent, so it correctly bypasses the HTTP tier (Rule 6 applies to agents; the
    report/CLI paths call services in-process — anti-pattern 5 forbids the
    reverse). Hence this module imports *both* ``rest_client`` (tools) and
    ``get_services`` (CLI).

Usage:
    python options_analysis.py
    python options_analysis.py --watchlist /path/to/watchlist.yaml
    python options_analysis.py --puts-budget 1000
    python options_analysis.py --watchlist /path/to/watchlist.yaml --puts-budget 1000 --top-n 15
"""

import argparse
import platform
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Optional

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parent
for path in (PROJECT_ROOT, MCP_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from fastmcp import FastMCP

from mcp_gateway import rest_client
from quantcore.services.registry import get_services
from quantcore.services.options_screening import (
    SecurityAnalysis,
    BB_PERIOD,
    BB_STD_DEV,
    HISTORY_PERIOD,
    PC_VERY_BULLISH,
    PC_BULLISH,
    PC_NEUTRAL_HIGH,
    PC_VERY_BEARISH,
    CONVICTION_WEIGHT,
    ROI_WEIGHT,
    ROI_CAP_FOR_RANKING,
)


mcp = FastMCP("options-analysis-server")


# ---------------------------------------------------------------------------
# MCP tools — one service call deep
# ---------------------------------------------------------------------------

@mcp.tool()
def mcp_health_check() -> dict:
    """Return lightweight version/config info for MCP diagnostics."""
    try:
        fastmcp_version = importlib_metadata.version("fastmcp")
    except importlib_metadata.PackageNotFoundError:
        fastmcp_version = "unknown"

    return {
        "server": "options-analysis-server",
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "fastmcp_version": fastmcp_version,
        "history_period": HISTORY_PERIOD,
        "bb_period": BB_PERIOD,
        "bb_std_dev": BB_STD_DEV,
        # What the *tools* screen is the server-side DB watchlist (issue #83);
        # this path is only the CLI's --watchlist default.
        "watchlist_default": "database (watchlist table)",
        "watchlist_cli_default": str(PROJECT_ROOT / "watchlist.yaml"),
    }


@mcp.tool()
def analyze_options_watchlist(
    watchlist_path: str | None = None,
    puts_budget: float = 1000.0,
    top_n: int = 10,
    include_non_us: bool = False,
) -> dict:
    """Analyze the watchlist and return ranked long/put candidates plus put trade ideas."""
    # ``watchlist_path`` is not exposed over HTTP (Step 1 curation — the REST tier
    # screens the server-side watchlist, which since issue #83 is the DB table
    # rather than a file) and is accepted here for signature stability; the
    # parameter is intentionally not forwarded.
    return rest_client.get(
        "/api/options/screen-watchlist",
        puts_budget=puts_budget,
        top_n=top_n,
        include_non_us=include_non_us,
    )


@mcp.tool()
def analyze_options_symbol(symbol: str, puts_budget: float = 1000.0, top_n: int = 10) -> dict:
    """Analyze a single symbol using the same scoring rules as the watchlist run."""
    return rest_client.get(
        f"/api/securities/{symbol}/options/screen", puts_budget=puts_budget, top_n=top_n
    )


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
    """
    # ``max_snapshot_age_minutes`` / ``allow_live_fetch`` are not exposed over HTTP
    # (Step 1 curation) and are accepted here for signature stability.
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


# ---------------------------------------------------------------------------
# Chain, flow and positioning tools (issue #159).
#
# Moved here from stock_price_server, which had accreted the whole options
# surface alongside the technicals it owns. One domain per server: anything
# reading an options chain, its open interest, or its dealer positioning
# belongs on this server. Bodies are unchanged — one rest_client call deep.
# ---------------------------------------------------------------------------

@mcp.tool()
def get_full_options_chain(symbol: str) -> dict:
    """Fetch the full options chain (all strikes, all expirations) for a ticker and persist to DB."""
    return rest_client.get(f"/api/securities/{symbol}/options/full-chain")


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
def get_oi_change_analysis(
    symbol: str,
    days: int = 30,
    top_n: int = 10,
    min_oi: int = 100,
    expiration: str = None,
) -> dict:
    """Analyse open-interest CHANGES over time — the classic 2×2 OI/price read.

    Comparing OI across stored full-chain snapshots reveals what kind of
    positioning drove a move, which a single-day chain cannot show:

      OI rising + price rising   → new_longs        (fresh bullish positioning)
      OI rising + price falling  → new_shorts       (fresh bearish positioning)
      OI falling + price rising  → short_covering   (rally without fresh sponsorship)
      OI falling + price falling → long_liquidation (longs exiting)

    Options-specific overlay:
      put_oi_support_strikes     — strikes below spot with large PUT OI builds:
                                   put writers defend these (support)
      call_oi_resistance_strikes — strikes above spot with large CALL OI builds:
                                   the call wall (resistance / pin candidates)

    IMPORTANT — sparse-history caveat: OI history accumulates only when
    full-chain snapshots are captured (get_full_options_chain; the daily report
    job captures portfolio + watchlist symbols automatically). If fewer than 2
    distinct snapshot days exist in the window, the tool returns a note payload
    with empty oi_changes rather than an error — that is expected on symbols
    without accumulated history, not a failure.

    Outputs:
      snapshot_dates_used   — earliest/latest/previous snapshot days compared
      underlying_change_pct — spot move between earliest and latest snapshot
      top_oi_builds / top_oi_drains — classified movers with |ΔOI| ≥ min_oi
      latest_day_change     — call/put OI shift latest vs previous snapshot day
      summary               — plain-English recap

    Args:
        symbol:     Stock ticker symbol (e.g. 'AAPL')
        days:       Rolling window of snapshot history to compare (default: 30)
        top_n:      Max movers to return per side (default: 10)
        min_oi:     Minimum |ΔOI| in contracts to count as a mover (default: 100)
        expiration: Optional 'YYYY-MM-DD' to restrict to one expiration
    """
    params = {"days": days, "top_n": top_n, "min_oi": min_oi}
    if expiration:
        params["expiration"] = expiration
    return rest_client.get(f"/api/securities/{symbol}/options/oi-change", **params)


@mcp.tool()
def get_gex_profile(
    symbol: str,
    max_expirations: int = 6,
    risk_free_rate: float = 0.045,
) -> dict:
    """Build a signed Gamma Exposure (GEX) profile — dealer hedging pressure by strike.

    GEX signs each contract's dollar gamma by the standard dealer convention
    (dealers long calls +, short puts −) and aggregates per strike:
      GEX = gamma × OI × 100 × spot² × 1%   (dollar gamma per 1% move)

    Key levels returned:
      zero_gamma_level        — where cumulative net GEX flips sign: the
                                volatility-regime boundary. Above it dealers
                                dampen moves; below it they amplify them.
      top_positive_gex_strike — the call wall: heaviest positive GEX; acts as
                                resistance / an expiry pin magnet.
      top_negative_gex_strike — put support / the vol trigger: heaviest
                                negative GEX; breaking it accelerates selling.
      regime                  — positive_gamma (mean-reverting, dealers buy dips
                                and sell rips) or negative_gamma (trending /
                                volatile, dealers chase moves).

    Hedge-flow tilts (aggregate dealer greeks, share-equivalent):
      net_vanna_exposure — sensitivity of dealer deltas to IV changes: with
                           positive vanna a vol crush forces dealer BUYING
                           (supportive), a vol spike forces selling.
      net_charm_exposure — dealer delta drift from time decay: systematic
                           buy/sell pressure into expiry (strongest near OpEx).

    INTENDED USE — support/resistance from dealer positioning:
      - top_negative_gex_strike below price is a defended support zone.
      - price crossing below zero_gamma_level = regime change: expect wider
        ranges and momentum moves; tighten stops.
      - gex_ladder gives the full strike-by-strike wall map near spot.

    A daily summary (net GEX, zero-gamma, regime) is auto-persisted per call —
    history accumulates like gamma_wall_history.

    Args:
        symbol:          Stock ticker symbol (e.g. 'AAPL')
        max_expirations: Number of nearest expirations to scan (default: 6)
        risk_free_rate:  Annualised risk-free rate as decimal (default: 0.045)
    """
    return rest_client.get(
        f"/api/securities/{symbol}/options/gex-profile",
        max_expirations=max_expirations,
        risk_free_rate=risk_free_rate,
    )


# ---------------------------------------------------------------------------
# Reporting (console presentation — calls the service for analytics)
# ---------------------------------------------------------------------------

SEPARATOR = "─" * 72


def _bb_bar(pos: float, width: int = 20) -> str:
    """Visual ASCII bar: [====|====] where | is the price position."""
    pos = max(-0.2, min(1.2, pos))
    idx = int(pos * width)
    idx = max(0, min(width - 1, idx))
    bar = ["-"] * width
    bar[idx] = "█"
    return "[" + "".join(bar) + "]"


def _pc_label(pc: Optional[float]) -> str:
    if pc is None:
        return "n/a"
    if pc < PC_VERY_BULLISH:
        return f"{pc:.2f} ▲▲ very bullish"
    if pc < PC_BULLISH:
        return f"{pc:.2f} ▲  bullish"
    if pc < PC_NEUTRAL_HIGH:
        return f"{pc:.2f} ─  neutral"
    if pc < PC_VERY_BEARISH:
        return f"{pc:.2f} ▼  bearish"
    return f"{pc:.2f} ▼▼ very bearish"


def print_section(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def print_long_candidates(
    results: list[SecurityAnalysis],
    top_n: int = 10,
    budget: float = 1000.0,
) -> None:
    svc = get_services().options_screening
    print_section("LONG / BOUNCE CANDIDATES  (oversold + bullish options)")
    candidates = sorted(
        [s for s in results if s.long_score > 0],
        key=lambda s: s.long_score,
        reverse=True,
    )[:top_n]

    if not candidates:
        print("  No candidates met the scoring threshold.")
        return

    call_trades = []
    for rank, sec in enumerate(candidates, 1):
        pc = sec.options.put_call_ratio if sec.options else None
        call_vol = sec.options.total_call_volume if sec.options else 0
        iv_label = sec.iv.label if sec.iv else "n/a"
        print(f"\n  #{rank}  {sec.symbol:<6}  ${sec.price:<8.2f}  long_score={sec.long_score}")
        print(f"       BB   lower={sec.bands.lower}  mid={sec.bands.middle}  upper={sec.bands.upper}")
        print(f"            {_bb_bar(sec.bb_pos)}  pos={sec.bb_pos:.2f}")
        print(f"       P/C  {_pc_label(pc)}", end="")
        if sec.pc is not None:
            atm = f"  atm={sec.pc.near_atm_pc:.2f}" if sec.pc.near_atm_pc else ""
            vol = f"  vol={sec.pc.near_vol_pc:.2f}" if sec.pc.near_vol_pc else ""
            mid = f"  mid={sec.pc.mid_oi_pc:.2f}" if sec.pc.mid_oi_pc else ""
            flags = ""
            if sec.pc.put_unwinding:  flags += " [UNWINDING]"
            if sec.pc.near_term_fear: flags += " [NEAR-TERM-FEAR]"
            print(f"{atm}{vol}{mid}{flags}")
        else:
            print()
        print(f"       IV   {iv_label}")
        print(f"       Vol  call_volume={call_vol:,}")
        if sec.days_to_earnings is not None:
            print(f"       ERN  {sec.days_to_earnings}d to earnings")
        if sec.news_signal:
            headline = f'  "{sec.news_top_headline[:70]}"' if sec.news_top_headline else ""
            print(f"       NEWS {sec.news_signal}{headline}")
        print(f"       Why  {sec.long_reason}")

        # Primary recommendation: call trade
        call_guardrail = svc.call_guardrail_reason(sec)
        if call_guardrail:
            print(f"       *** GUARDRAIL (CALL): {call_guardrail} — call trade skipped ***")
        call_trade = svc.build_call_trade(
            sec,
            budget_per_trade=budget / max(len(candidates), 1),
            total_budget=budget,
        )
        if call_trade:
            call_trades.append(call_trade)
            _print_call_trade(call_trade)

        # Comparison: put trade for the same security
        put_guardrail = svc.put_guardrail_reason(sec)
        if put_guardrail:
            print(f"       *** GUARDRAIL (PUT):  {put_guardrail} — put trade skipped ***")
        put_trade = svc.build_put_trade(
            sec,
            budget_per_trade=budget / max(len(candidates), 1),
            total_budget=budget,
        )
        if put_trade:
            _print_put_trade(put_trade)

    print_call_portfolio_summary(call_trades, budget)


def _print_call_trade(trade: dict) -> None:
    """Print a single call trade spec in a consistent format."""
    spread_note = "  *** high IV — consider bull call spread ***" if trade["suggest_spread"] else ""
    print(f"       CALL strike={trade['strike']}  exp={trade['expiration']}"
          f"  ask=${trade['ask']:.2f}/share  IV={trade['iv']:.0f}%{spread_note}")
    print(f"            target=${trade['target_price']}  "
          f"est. P&L/contract=${trade['profit_at_target_per_contract']:.0f}  "
          f"ROI={trade['roi_at_target_pct']:.0f}%")
    print(f"            cost={trade['contracts']}x @ ${trade['total_cost']:.0f} total")


def _print_put_trade(trade: dict) -> None:
    """Print a single put trade spec in a consistent format."""
    spread_note = "  *** high IV — consider debit spread ***" if trade["suggest_spread"] else ""
    print(f"       PUT  strike={trade['strike']}  exp={trade['expiration']}"
          f"  ask=${trade['ask']:.2f}/share  IV={trade['iv']:.0f}%{spread_note}")
    print(f"            target=${trade['target_price']}  "
          f"est. P&L/contract=${trade['profit_at_target_per_contract']:.0f}  "
          f"ROI={trade['roi_at_target_pct']:.0f}%")
    print(f"            cost={trade['contracts']}x @ ${trade['total_cost']:.0f} total")


def print_put_candidates(results: list[SecurityAnalysis], budget: float, top_n: int = 7) -> None:
    svc = get_services().options_screening
    print_section("PUT / BEARISH CANDIDATES  (overbought or heavy put positioning)")
    candidates = sorted(
        [s for s in results if s.put_score > 0],
        key=lambda s: s.put_score,
        reverse=True,
    )[:top_n]

    if not candidates:
        print("  No candidates met the scoring threshold.")
        return

    trades = []
    for rank, sec in enumerate(candidates, 1):
        pc = sec.options.put_call_ratio if sec.options else None
        put_oi = sec.options.total_put_oi if sec.options else 0
        iv_label = sec.iv.label if sec.iv else "n/a"
        print(f"\n  #{rank}  {sec.symbol:<6}  ${sec.price:<8.2f}  put_score={sec.put_score}  long_score={sec.long_score}")
        print(f"       BB   lower={sec.bands.lower}  mid={sec.bands.middle}  upper={sec.bands.upper}")
        print(f"            {_bb_bar(sec.bb_pos)}  pos={sec.bb_pos:.2f}")
        print(f"       P/C  {_pc_label(pc)}", end="")
        if sec.pc is not None:
            atm = f"  atm={sec.pc.near_atm_pc:.2f}" if sec.pc.near_atm_pc else ""
            vol = f"  vol={sec.pc.near_vol_pc:.2f}" if sec.pc.near_vol_pc else ""
            mid = f"  mid={sec.pc.mid_oi_pc:.2f}" if sec.pc.mid_oi_pc else ""
            flags = ""
            if sec.pc.fresh_put_buying: flags += " [FRESH-PUTS]"
            if sec.pc.near_term_fear:   flags += " [NEAR-TERM-FEAR]"
            print(f"{atm}{vol}{mid}{flags}")
        else:
            print()
        print(f"       IV   {iv_label}")
        print(f"       OI   put_oi={put_oi:,}")
        if sec.days_to_earnings is not None:
            print(f"       ERN  {sec.days_to_earnings}d to earnings")
        if sec.news_signal:
            headline = f'  "{sec.news_top_headline[:70]}"' if sec.news_top_headline else ""
            print(f"       NEWS {sec.news_signal}{headline}")
        print(f"       Why  {sec.put_reason}")

        # Primary recommendation: put trade
        guardrail_reason = svc.put_guardrail_reason(sec)
        if guardrail_reason:
            print(f"       *** GUARDRAIL (PUT):  {guardrail_reason} — put trade skipped ***")
        trade = svc.build_put_trade(
            sec,
            budget_per_trade=budget / max(len(candidates), 1),
            total_budget=budget,
        )
        if trade:
            trades.append(trade)
            _print_put_trade(trade)

        # Comparison: call trade for the same security
        call_guardrail = svc.call_guardrail_reason(sec)
        if call_guardrail:
            print(f"       *** GUARDRAIL (CALL): {call_guardrail} — call trade skipped ***")
        call_trade = svc.build_call_trade(
            sec,
            budget_per_trade=budget / max(len(candidates), 1),
            total_budget=budget,
        )
        if call_trade:
            _print_call_trade(call_trade)

    print_put_portfolio_summary(trades, budget)


def print_put_portfolio_summary(trades: list[dict], total_budget: float) -> None:
    """Greedy budget fill sorted by blended conviction + ROI score."""
    if not trades:
        return
    svc = get_services().options_screening
    print_section(f"PUT PORTFOLIO SUMMARY  (budget ${total_budget:,.0f})")
    print(f"  Ranking: {int(CONVICTION_WEIGHT*100)}% conviction (put score) + "
          f"{int(ROI_WEIGHT*100)}% ROI (capped at {ROI_CAP_FOR_RANKING:.0f}%)\n")

    selected = svc.greedy_fill(trades, total_budget, svc.combined_put_rank_score)
    if not selected:
        print("  No trades fit within budget.")
        return

    total_spent = sum(t["total_cost"] for t in selected)
    for t in selected:
        print(
            f"  {t['symbol']:<6}  ${t['strike']} put  exp={t['expiration']}"
            f"  {t['contracts']}x  cost=${t['total_cost']:.0f}"
            f"  target=${t['target_price']}  est ROI={t['roi_at_target_pct']:.0f}%"
            f"  conviction={t.get('put_score', 0):.0f}  rank={t['rank_score']:.2f}"
        )
    print(f"\n  Total deployed: ${total_spent:,.0f} / ${total_budget:,.0f}")
    leftover = total_budget - total_spent
    if leftover > 0:
        print(f"  Remaining:      ${leftover:,.0f}  (consider longer-dated puts or debit spreads)")


def print_call_portfolio_summary(trades: list[dict], total_budget: float) -> None:
    """Greedy budget fill for call trades sorted by blended conviction + ROI score."""
    if not trades:
        return
    svc = get_services().options_screening
    print_section(f"CALL PORTFOLIO SUMMARY  (budget ${total_budget:,.0f})")
    print(f"  Ranking: {int(CONVICTION_WEIGHT*100)}% conviction (long score) + "
          f"{int(ROI_WEIGHT*100)}% ROI (capped at {ROI_CAP_FOR_RANKING:.0f}%)\n")

    selected = svc.greedy_fill(trades, total_budget, svc.combined_call_rank_score)
    if not selected:
        print("  No trades fit within budget.")
        return

    total_spent = sum(t["total_cost"] for t in selected)
    for t in selected:
        print(
            f"  {t['symbol']:<6}  ${t['strike']} call  exp={t['expiration']}"
            f"  {t['contracts']}x  cost=${t['total_cost']:.0f}"
            f"  target=${t['target_price']}  est ROI={t['roi_at_target_pct']:.0f}%"
            f"  conviction={t.get('long_score', 0):.0f}  rank={t['rank_score']:.2f}"
        )
    print(f"\n  Total deployed: ${total_spent:,.0f} / ${total_budget:,.0f}")
    leftover = total_budget - total_spent
    if leftover > 0:
        print(f"  Remaining:      ${leftover:,.0f}  (consider longer-dated calls or bull call spreads)")


def print_skip_list(results: list[SecurityAnalysis]) -> None:
    """Print securities with no actionable signal in either direction."""
    skipped = [s for s in results if s.long_score == 0 and s.put_score == 0]
    if skipped:
        symbols = ", ".join(s.symbol for s in skipped)
        print(f"\n  Skipped (no signal): {symbols}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    from quantcore.db import ensure_schema
    ensure_schema()

    parser = argparse.ArgumentParser(description="Options analysis from watchlist.yaml")
    parser.add_argument(
        "--watchlist",
        type=Path,
        default=PROJECT_ROOT / "watchlist.yaml",
        help="Path to watchlist YAML file",
    )
    parser.add_argument(
        "--puts-budget",
        type=float,
        default=1000.0,
        help="Total budget for put trades ($)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Max candidates to show per section",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Analyse a single symbol instead of full watchlist",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Deprecated: persistence targets QUANTCORE_DB_DSN from the environment",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip saving snapshots to the database (useful for quick one-off runs)",
    )
    parser.add_argument(
        "--no-news",
        action="store_true",
        help="Skip RSS/FinBERT news collection and sentiment scoring",
    )
    args = parser.parse_args()

    services = get_services()
    svc = services.options_screening

    if args.symbol:
        entries = [{"symbol": args.symbol.upper(), "name": args.symbol.upper(), "tags": []}]
    else:
        if not args.watchlist.exists():
            print(f"ERROR: watchlist not found at {args.watchlist}", file=sys.stderr)
            sys.exit(1)
        entries = svc.load_watchlist(args.watchlist)
        entries = [e for e in entries if svc.is_us_listed(e["symbol"])]

    if args.db:
        print("NOTE: --db is deprecated; persistence targets QUANTCORE_DB_DSN from the environment.",
              file=sys.stderr)

    # Set up persistence (unless suppressed) via the shared options repository
    store = None if args.no_persist else services.options_repository

    # Set up news sentiment (optional — degrades gracefully if unavailable)
    news_store = None
    if not args.no_news:
        try:
            news_store = services.news_repository
            symbols_to_collect = [e["symbol"] for e in entries]
            print(f"\nCollecting news for {len(symbols_to_collect)} symbols (RSS + yfinance + FinBERT)…",
                  flush=True)
            services.sentiment.collect(symbols_to_collect, score=True)
            total_news = news_store.article_count()
            print(f"News DB   : {total_news} articles scored")
        except Exception as exc:
            print(f"News      : unavailable ({exc})", file=sys.stderr)
            news_store = None

    print(f"\nOptions Analysis Engine")
    print(f"Watchlist : {args.watchlist}")
    print(f"Symbols   : {len(entries)} (US-listed)")
    print(f"Put Budget: ${args.puts_budget:,.0f}")
    if store is not None:
        print(f"Database  : QuantCore (PostgreSQL)")
    if news_store is not None:
        print(f"News      : enabled (FinBERT)")
    elif args.no_news:
        print(f"News      : disabled (--no-news)")
    else:
        print(f"News      : unavailable")
    print(f"\nFetching data", end="", flush=True)

    results: list[SecurityAnalysis] = []
    failed: list[str] = []
    saved = 0

    for entry in entries:
        sym = entry["symbol"]
        print(".", end="", flush=True)
        sec = svc.fetch_security(sym, entry["name"], entry["tags"], news_store=news_store)
        if sec is None:
            failed.append(sym)
            continue
        svc.score(sec)
        results.append(sec)

        # Persist snapshot for backtesting (issue #10)
        if store is not None and sec.options:
            bb_dict = {
                "upper": sec.bands.upper,
                "middle": sec.bands.middle,
                "lower": sec.bands.lower,
                "period": 20,
            }
            opts_dict = {
                "expiration": sec.options.expiration,
                "put_call_ratio": sec.options.put_call_ratio,
                "calls": {
                    "total_open_interest": sec.options.total_call_oi,
                    "total_volume": sec.options.total_call_volume,
                    "avg_iv_pct": sec.options.avg_call_iv,
                    "atm_contracts": sec.options.atm_calls,
                },
                "puts": {
                    "total_open_interest": sec.options.total_put_oi,
                    "total_volume": sec.options.total_put_volume,
                    "avg_iv_pct": sec.options.avg_put_iv,
                    "atm_contracts": sec.options.atm_puts,
                },
            }
            if store.save_snapshot(sym, sec.price, bb_dict, opts_dict) is not None:
                saved += 1

    print(f" done ({len(results)} fetched, {len(failed)} failed)")
    if store is not None:
        print(f"Persisted : {saved} new snapshots")
    if failed:
        print(f"Failed    : {', '.join(failed)}")

    if not results:
        print("No data retrieved. Check network / symbols.")
        sys.exit(1)

    print_long_candidates(results, top_n=args.top_n, budget=args.puts_budget)
    print_put_candidates(results, budget=args.puts_budget, top_n=args.top_n)
    print_skip_list(results)
    print(f"\n{SEPARATOR}\n")


if __name__ == "__main__":
    main()
