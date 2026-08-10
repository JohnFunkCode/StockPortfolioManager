"""
Portfolio MCP Server (issue #126 PR 4, Step 4.8; watchlist tools from issue #83)

Provides three read-only tools over the caller's own portfolio:

  get_portfolio         — per-symbol roll-up (value, gain/loss, $/day) + totals
  get_symbol_lots        — the individual lots making up one symbol's position
  get_portfolio_summary  — just the portfolio-wide totals

plus two tools over the shared watchlist:

  list_watchlist         — every symbol on the global watchlist
  add_to_watchlist       — put a symbol on it

Usage (standalone):
    fastmcp run portfolio_server.py

HTTP gateway wrapper (architectural standard v2 §11, Rule 6 —
``AI Agent → MCP wrapper → REST tier → Service``): each tool translates its call
into a single HTTP request against the FastAPI front door via
``mcp_gateway.rest_client``; no business logic or DB access lives here. The
REST tier resolves the owner from the caller's authenticated token
(``require_owner``) — there is no ``owner``/``ticker``-as-cross-user-filter
argument on any tool here, so a caller can only ever see their own portfolio.

The **portfolio** side stays deliberately read-only: there are no ``create_lot``,
``close_lot``, or ``delete_lot`` tools. Adding portfolio write tools is a
decision the plan defers (#126 Q19), not an oversight — a position is
per-owner financial record-keeping, and a mis-parsed agent call there would
corrupt cost basis.

``add_to_watchlist`` is the one write tool, and it is here for reasons that do
not generalise back to the portfolio: the watchlist is a shared, global,
low-risk list of symbols to track, an agent that can research a name should be
able to put it on that list without a human retyping it, and the worst case of
a bad call is a spurious row someone deletes in the UI. It needs no new seam
verb either — it is a plain ``POST``.

Removal stays out (issue #83 decision 4): ``mcp_gateway.rest_client`` has no
``put``/``patch``/``delete`` verb, and adding one so an agent could quietly drop
symbols off a list the whole team shares is not a trade worth making. Removal
is a UI action.
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

mcp = FastMCP("portfolio-server")


@mcp.tool()
def mcp_health_check() -> dict:
    """Return lightweight version/config info for MCP diagnostics."""
    try:
        fastmcp_version = importlib_metadata.version("fastmcp")
    except importlib_metadata.PackageNotFoundError:
        fastmcp_version = "unknown"

    return {
        "server": "portfolio-server",
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "fastmcp_version": fastmcp_version,
    }


@mcp.tool()
def get_portfolio() -> dict:
    """Return the caller's portfolio as a per-symbol roll-up, with totals.

    Each entry aggregates every open lot held in that symbol: total
    investment, current value, gain/loss ($ and %), $/day, and 7/30/90-day
    returns. Always the caller's own holdings — the owner is resolved from
    the caller's authenticated token, never from an argument.

    Returns:
        symbols — list of per-symbol roll-up dicts
        totals  — portfolio-wide investment/value/gain-loss/$-per-day
    """
    return rest_client.get("/api/portfolio/symbols")


@mcp.tool()
def get_symbol_lots(ticker: str) -> dict:
    """Return the caller's individual lots for one symbol.

    Each lot carries its own purchase price, quantity, purchase date,
    current price, and per-lot gain/loss — useful for questions about cost
    basis or when a specific lot was opened, which the per-symbol roll-up
    from get_portfolio collapses away.

    Args:
        ticker: Stock ticker symbol (e.g. 'AAPL')
    """
    ticker = ticker.strip().upper()
    lots = rest_client.get("/api/portfolio/lots").get("lots", [])
    return {"ticker": ticker, "lots": [lot for lot in lots if lot.get("symbol") == ticker]}


@mcp.tool()
def get_portfolio_summary() -> dict:
    """Return just the caller's portfolio-wide totals (no per-symbol detail).

    Returns:
        total_investment, total_current_value, total_gain_loss,
        total_gain_loss_pct, total_dollars_per_day
    """
    return rest_client.get("/api/portfolio/symbols").get("totals") or {}


@mcp.tool()
def list_watchlist() -> dict:
    """Return every symbol on the shared watchlist.

    The watchlist is global, not per-owner: this is the same list the QuantUI
    Securities page shows, the daily report covers, and the options screener
    scores. Use it to check whether a symbol is already tracked before
    calling add_to_watchlist.

    Returns:
        securities — list of dicts with symbol, name, currency, tags, source
    """
    return rest_client.get("/api/watchlist")


@mcp.tool()
def add_to_watchlist(
    symbol: str,
    name: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Add a symbol to the shared watchlist.

    Writes to a list the whole team sees, so add symbols the user actually
    asked to track — not every ticker that comes up in analysis. Adding a
    symbol enrolls it in the daily report and the nightly options-chain
    capture; there is no remove tool, so a mistake has to be undone in the UI.

    There is deliberately no `currency` argument: the REST tier reads it off
    the exchange. A guessed currency is worse than none — it labels a foreign
    market cap as dollars, which is a wrong number rather than a missing one.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
        name: Company name; the REST tier looks it up when omitted
        tags: Optional grouping labels, e.g. ['Semiconductors']

    Returns:
        symbol, destination, and the currency that was resolved and stored. A
        symbol already on the list is a 409 error rather than a silent no-op.
    """
    return rest_client.post(
        "/api/watchlist",
        json={"symbol": symbol, "name": name, "tags": tags},
    )


if __name__ == "__main__":
    # Streamable HTTP transport (Rule 6). PORT is overridable so the same image
    # can be reused per wrapper in docker-compose / Cloud Run.
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "6006")))
