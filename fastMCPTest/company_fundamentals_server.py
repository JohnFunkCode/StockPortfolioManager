"""
company_fundamentals_server.py — MCP server for company fundamentals.

Tools: earnings calendar/risk, composite fundamental scoring, revenue growth
trajectory, EPS acceleration (CAN SLIM 'A'), batch scoring, full profiles, and
cache-backed rankings (top stocks, upcoming earnings, sector breakdown, score
changes, history).

HTTP gateway wrapper (architectural standard v2 §11, Rule 6 —
``AI Agent → MCP wrapper → REST tier → Service``): each tool translates its call
into a single HTTP request against the FastAPI front door via
``mcp_gateway.rest_client``; no business logic or DB access lives here.
"""

import logging
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

logging.basicConfig(level=logging.INFO)

mcp = FastMCP("company-fundamentals-server")


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
    return rest_client.get(f"/api/securities/{symbol}/earnings-calendar")


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
    return rest_client.get(f"/api/securities/{symbol}/fundamentals/score")


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
    return rest_client.get(f"/api/securities/{symbol}/fundamentals/revenue-growth")


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
    return rest_client.get(f"/api/securities/{symbol}/fundamentals/earnings-acceleration")


@mcp.tool()
def get_fundamental_scores_batch(symbols: list[str]) -> dict:
    """Score multiple stocks in one call, using the cache for hits.

    For each symbol: returns the cached score if fresh, otherwise fetches
    from yfinance and caches the result. Progress is reported in the summary.

    Args:
        symbols: List of ticker symbols (e.g. ['NVDA', 'AAPL', 'MSFT'])
    """
    return rest_client.post(
        "/api/securities/fundamentals/scores-batch", json={"symbols": symbols}
    )


@mcp.tool()
def get_full_fundamental_profile(symbol: str) -> dict:
    """Return all 4 fundamental metrics for a stock in a single call.

    Returns earnings calendar, fundamental score, revenue growth, and EPS
    acceleration plus a synthesized summary with overall signal and highlights.

    Args:
        symbol: Stock ticker symbol (e.g. 'NVDA')
    """
    return rest_client.get(f"/api/securities/{symbol}/fundamentals")


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
    return rest_client.get(
        "/api/securities/fundamentals/top", n=n, min_coverage=min_coverage
    )


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
    return rest_client.get(
        "/api/securities/fundamentals/upcoming-earnings", days=days, include_stale=include_stale
    )


@mcp.tool()
def get_cache_stats() -> dict:
    """Return a summary of what is stored in the fundamentals cache.

    Reports symbol counts, date ranges, and DB file size per data type.
    Zero network calls — reads only from the local SQLite database.
    """
    return rest_client.get("/api/securities/fundamentals/cache-stats")


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
    return rest_client.get(
        "/api/securities/fundamentals/sector-breakdown", sector=sector, top_n=top_n
    )


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
    return rest_client.get(
        "/api/securities/fundamentals/score-changes",
        min_delta=min_delta,
        since_days=since_days,
        direction=direction,
    )


@mcp.tool()
def get_fundamental_history(symbol: str, data_type: str, since_days: int = 365) -> dict:
    """Return historical snapshots and trend for a cached fundamental data type.

    Does NOT hit yfinance. Call the corresponding tool first to populate the cache.

    Args:
        symbol: Stock ticker symbol (e.g. 'NVDA')
        data_type: One of: fundamental_score, revenue_growth, earnings_acceleration, earnings_calendar
        since_days: How many days back to look (default 365)
    """
    return rest_client.get(
        f"/api/securities/{symbol}/fundamentals/history",
        data_type=data_type,
        since_days=since_days,
    )


if __name__ == "__main__":
    # Streamable HTTP transport (Rule 6). PORT is overridable so the same image
    # can be reused per wrapper in docker-compose / Cloud Run; default is this
    # server's assigned port.
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "6003")))
