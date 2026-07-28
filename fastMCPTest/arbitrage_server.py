"""
arbitrage_server.py — MCP server for the arbitrage scanner: structural spreads
between a security and the underlying it is linked to (a treasury company vs
its coin stack, a fund vs its reference future, a miner vs the metal it sells).

Its own server rather than tools bolted onto stock_price_server: arbitrage is a
distinct domain with its own curated universe (arb_universe.yaml), its own
scoring model, and its own failure modes, so it gets the same one-domain-per-
server treatment as prices, options, fundamentals, news and microstructure.

HTTP gateway wrapper (architectural standard v2 §11, Rule 6 —
``AI Agent → MCP wrapper → REST tier → Service``): each tool translates its call
into a single HTTP request against the FastAPI front door via
``mcp_gateway.rest_client``; no business logic or DB access lives here.
``mcp_health_check`` is the one exception — local version info, no service call.
"""

import os
import platform
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parent
for path in (PROJECT_ROOT, MCP_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from fastmcp import FastMCP

from mcp_gateway import rest_client

mcp = FastMCP("arbitrage-server")


@mcp.tool()
def mcp_health_check() -> dict:
    """Return lightweight version/config info for MCP diagnostics."""
    try:
        fastmcp_version = importlib_metadata.version("fastmcp")
    except importlib_metadata.PackageNotFoundError:
        fastmcp_version = "unknown"

    return {
        "server": "arbitrage-server",
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "fastmcp_version": fastmcp_version,
        "rest_base_url": rest_client._base_url(),
        "universe_file": str(PROJECT_ROOT / "arb_universe.yaml"),
    }


@mcp.tool()
def list_arbitrage_universe() -> dict:
    """List the curated arbitrage universe: which securities are mapped to which underlyings.

    Each entry declares a structural link and how it would be traded:

      kind                  nav_vehicle   — treasury company / trust / closed-end
                                            fund; fair value is computable
                            commodity_etf — fund vs its reference future
                            producer      — miner/E&P vs the commodity it sells
      underlying            the reference symbol (BTC-USD, GC=F, CL=F, ...)
      hedge_instrument      the equity/ETF that hedges the underlying leg, or
                            null when only futures would work (not tradeable
                            from an equity-only account)
      convergence_mechanism none | redemption | conversion | buyback |
                            index_event | deal_terms — what would actually force
                            the gap to close
      holdings_age_days     age of the hand-maintained holdings figures;
                            holdings_stale flags anything over 45 days

    NAV math only runs for entries with curated holdings — no free API publishes
    a treasury's coin count or its preferred stack.
    """
    return rest_client.get("/api/arbitrage/universe")


@mcp.tool()
def analyze_arbitrage_pair(
    security: str,
    underlying: str = "",
    days: int = 365,
    zscore_window: int = 0,
) -> dict:
    """Analyse one security against its underlying: gap size, mean reversion, and whether anything closes it.

    Returns four blocks:

      statistics  hedge ratio (with cross-period stability), Engle-Granger
                  cointegration, spread z-score, Ornstein-Uhlenbeck half-life
                  in days, and whether the spread is widening or converging
      nav         for nav_vehicles only — gross vs NET discount (senior claims
                  netted out), carry drag %/yr, years_of_burn, exposure_ratio
                  (dollars of underlying per dollar of equity)
      hedge       the equity/ETF hedge instrument, or a futures_only flag
      score       0-100 with the multiplicative `factors` that produced it,
                  a verdict (candidate / watch / reject), `reasons`, and
                  `breaks_on`

    Read `nav.premium_discount_pct`, never `gross_premium_discount_pct` — the
    gross figure ignores converts and preferred that sit senior to the common
    and routinely overstates the discount by 20+ points.

    Scoring is inverted by design: spread width only qualifies a candidate,
    the convergence mechanism ranks it. A wide gap with no forcing mechanism,
    negative carry, and a widening trend scores near zero — that combination is
    a directional bet wearing a hedge costume, not an arbitrage.

    Args:
        security:      Ticker of the listed vehicle/company (e.g. 'MSTR')
        underlying:    Override/supply the underlying symbol for an ad-hoc pair
                       not in the curated universe (e.g. 'BTC-USD')
        days:          Days of daily history for the spread statistics (30-3650)
        zscore_window: Trailing window for the z-score (2-3650); 0 uses the
                       full sample
    """
    params: dict = {"days": days}
    if underlying:
        params["underlying"] = underlying
    if zscore_window:
        params["zscore_window"] = zscore_window
    return rest_client.get(f"/api/arbitrage/pairs/{security}", **params)


@mcp.tool()
def scan_arbitrage(kinds: str = "", top_n: int = 20, days: int = 365) -> dict:
    """Rank the curated arbitrage universe by opportunity quality.

    Runs analyze_arbitrage_pair across every mapped pair and sorts by score.
    Candidates carry the same `factors` / `reasons` / `breaks_on` explanation
    as the single-pair tool, so a low score is always attributable.

    Expect most scans to return nothing above `watch`. That is the honest
    result: genuine convergence trades are rare, and the scanner is built to
    say so rather than to manufacture candidates.

    Args:
        kinds:  Comma-separated families to include — nav_vehicle,
                commodity_etf, producer. Empty scans all three.
        top_n:  Maximum candidates to return (1-100)
        days:   Days of daily history per pair (30-3650)
    """
    params: dict = {"top_n": top_n, "days": days}
    if kinds:
        params["kinds"] = kinds
    return rest_client.get("/api/arbitrage/scan", **params)


@mcp.tool()
def discover_arbitrage_pairs(
    symbols: str,
    references: str = "",
    days: int = 365,
    min_abs_correlation: float = 0.4,
    require_economic_link: bool = True,
) -> dict:
    """Sweep symbols against commodity/crypto/FX references for undeclared cointegrated links.

    Tests each symbol against a reference panel (GC=F, HG=F, CL=F, NG=F,
    BTC-USD, DX-Y.NYB), keeping pairs that clear the correlation floor AND
    pass the Engle-Granger cointegration test.

    `require_economic_link` gates results on the symbol's sector/industry
    plausibly connecting to the reference. Leave it True: over a wide sweep,
    cointegration tests reliably find statistically significant pairs with no
    causal relationship whatsoever.

    Discovered pairs get no NAV math and no convergence claim — they are
    candidates for curation, not trades.

    Args:
        symbols:               Comma-separated tickers to sweep (max 25 per
                               call — each one costs a history fetch plus a
                               profile lookup)
        references:            Comma-separated reference series (max 10); empty
                               uses the panel
        days:                  Days of daily history (30-3650)
        min_abs_correlation:   Minimum |return correlation| to keep a pair (0-1)
        require_economic_link: Drop pairs with no plausible sector link
    """
    params: dict = {
        "symbols": symbols,
        "days": days,
        "min_abs_correlation": min_abs_correlation,
        "require_economic_link": require_economic_link,
    }
    if references:
        params["references"] = references
    return rest_client.get("/api/arbitrage/discover", **params)



if __name__ == "__main__":
    # Streamable HTTP transport (Rule 6). PORT is overridable so the same image
    # can be reused per wrapper in docker-compose / Cloud Run; default is this
    # server's assigned port.
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "6006")))
