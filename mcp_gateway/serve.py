"""
serve.py — container entrypoint that serves a wrapper module's MCP server over
streamable HTTP.

One image (Dockerfile.mcp) is reused for all five wrappers; the specific server
and port are selected purely by environment variables at run time:

    SERVER_MODULE   importable module exposing a module-level ``mcp`` object,
                    e.g. "fastMCPTest.stock_price_server" (default below).
    PORT            TCP port to bind (default 8000).

This imports the target module and runs ``module.mcp.run(transport="http", ...)``.
Driving the launch from the module-level ``mcp`` object (rather than the file's
``__main__`` block) keeps it uniform across every wrapper — notably
``options_analysis``, whose ``__main__`` is the in-process CLI (Rule 6), not an
``mcp.run`` call. No business logic or DB access lives here.
"""

import importlib
import os

from fastmcp.server.middleware import PingMiddleware


DEFAULT_PING_INTERVAL_MS = 30_000
PING_INTERVAL_ENV = "MCP_PING_INTERVAL_MS"


def _ping_interval_ms() -> int:
    """Return the positive FastMCP keepalive interval for HTTP sessions."""
    raw = os.environ.get(PING_INTERVAL_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_PING_INTERVAL_MS
    try:
        interval = int(raw)
    except ValueError:
        return DEFAULT_PING_INTERVAL_MS
    return interval if interval > 0 else DEFAULT_PING_INTERVAL_MS


def _configure_keepalive(mcp) -> None:  # noqa: ANN001 — FastMCP is a runtime dependency
    """Keep streamable HTTP sessions active through idle proxy windows."""
    mcp.add_middleware(PingMiddleware(interval_ms=_ping_interval_ms()))


def main() -> None:
    module_name = os.environ.get("SERVER_MODULE", "fastMCPTest.stock_price_server")
    port = int(os.environ.get("PORT", "8000"))
    module = importlib.import_module(module_name)
    _configure_keepalive(module.mcp)
    module.mcp.run(transport="http", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
