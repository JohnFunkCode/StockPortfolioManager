"""
Options analysis tool — reads watchlist.yaml, fetches live data via yfinance,
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
        "watchlist_default": str(PROJECT_ROOT / "watchlist.yaml"),
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
    # screens the server-side watchlist.yaml) and is accepted here for signature
    # stability; the parameter is intentionally not forwarded.
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
    from quantcore.db import init_schema
    init_schema()

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
