"""Every MCP tool's REST contract, offline (issue #44, parts 1 and 2).

Each of the 62 tools is called through fastmcp's in-memory ``Client`` with the
REST seam (``mcp_gateway.rest_client``) stubbed by an ``httpx.MockTransport``.
Per tool this pins:

- exactly one REST call (Rule 6: a wrapper is one ``rest_client`` call deep),
  or none for the wrapper-local ``mcp_health_check`` tools;
- the method, path, query string and JSON body that call sends -- with the
  tool's defaults, plus the optional arguments that change what is sent;
- that the call names a route the REST tier actually serves
  (``docs/openapi-surface.txt``, so no database is needed to build the app);
- that the response comes back unchanged (or, for the two portfolio tools that
  post-process, transformed as documented);
- the error shape: a non-2xx from the REST tier is a tool error whose text
  carries the status.

``TestEveryToolHasAContract`` is the completeness guard: a tool registered on
any wrapper in ``scripts/ci_wrapper_smoke.WRAPPERS`` without a case below (or
a case for a tool that no longer exists) fails. A new tool needs a case here.
"""
import asyncio
import importlib
import json
import logging
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastmcp import Client

from mcp_gateway import rest_client
from scripts.ci_wrapper_smoke import WRAPPERS

ROOT = Path(__file__).resolve().parent.parent
SURFACE = ROOT / "docs" / "openapi-surface.txt"

SP = "fastMCPTest.stock_price_server"
OA = "fastMCPTest.options_analysis"
CF = "fastMCPTest.company_fundamentals_server"
NS = "fastMCPTest.news_sentiment_server"
MA = "fastMCPTest.market_analysis_server"
PF = "fastMCPTest.portfolio_server"
AR = "fastMCPTest.arbitrage_server"

SEC = "/api/securities/BRK-B"


def call(method, path, query=None, body=None):
    """The REST call a tool is expected to make. Query values are as sent
    on the wire (strings); a list value is a repeated key."""
    return {"method": method, "path": path, "query": query or {}, "body": body}


def G(path, **query):
    return call("GET", path, query)


# (module, tool) -> list of (args, expected call or None for "no REST call").
# The first variant of each tool uses only required arguments, which pins the
# defaults; later variants exercise arguments that are sent conditionally.
CASES = {
    # ---- stock-price (21) ------------------------------------------------
    (SP, "get_news"): [({"symbol": "BRK-B"}, G(f"{SEC}/news", max_articles="10"))],
    (SP, "get_stock_price"): [({"symbol": "BRK-B"}, G(f"{SEC}/price-summary"))],
    (SP, "get_rsi"): [({"symbol": "BRK-B"}, G(f"{SEC}/rsi", period="14", interval="1d"))],
    (SP, "get_macd"): [({"symbol": "BRK-B"}, G(f"{SEC}/macd", interval="1d"))],
    (SP, "get_stochastic"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/stochastic", k_period="14", d_period="3", interval="1d"))],
    (SP, "get_volume_analysis"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/volume", lookback="20", interval="1d"))],
    (SP, "get_obv"): [({"symbol": "BRK-B"}, G(f"{SEC}/obv", lookback="20", interval="1d"))],
    (SP, "get_vwap"): [({"symbol": "BRK-B"}, G(f"{SEC}/vwap", lookback="20", interval="1d"))],
    (SP, "get_candlestick_patterns"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/candlestick", lookback="10", interval="1d"))],
    (SP, "get_higher_lows"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/higher-lows", swing_bars="3", lookback_swings="6", interval="1h"))],
    (SP, "get_gap_analysis"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/gaps", min_gap_pct="0.5", lookback="60", interval="1d"))],
    (SP, "get_atr_bands"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/atr-bands", period="14", band_mult="2.0", stop_mult="3.0",
        interval="1d", lookback="250"))],
    (SP, "get_anchored_vwap"): [
        ({"symbol": "BRK-B"}, G(f"{SEC}/anchored-vwap", lookback_days="365")),
        ({"symbol": "BRK-B", "anchor_date": "2026-01-02"}, G(
            f"{SEC}/anchored-vwap", anchor_date="2026-01-02", lookback_days="365")),
    ],
    (SP, "get_volume_profile"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/volume-profile", days="365", interval="1d", bins="50",
        value_area_pct="0.7"))],
    (SP, "get_support_confluence"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/support-confluence", tolerance_pct="1.0", max_expirations="4",
        max_zones="5"))],
    (SP, "get_historical_drawdown"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/drawdown", lookback_days="252"))],
    (SP, "get_stop_loss_analysis"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/stop-loss", cost_basis="0.0", shares="0", max_expirations="4"))],
    (SP, "get_vwap_history"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/vwap/history", since_days="90", lookback="20", interval="1d"))],
    (SP, "get_relative_strength_history"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/relative-strength/history", since_days="90", rs_period="21",
        interval="1d"))],
    (SP, "get_relative_strength"): [({"symbol": "BRK-B"}, G(f"{SEC}/relative-strength"))],
    (SP, "get_trade_recommendation"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/recommendation", capital="5000.0"))],

    # ---- options-analysis (11) -------------------------------------------
    (OA, "mcp_health_check"): [({}, None)],
    # watchlist_path is deliberately not forwarded: the REST tier reads the DB.
    (OA, "analyze_options_watchlist"): [
        ({}, G("/api/options/screen-watchlist", puts_budget="1000.0", top_n="10",
               include_non_us="false")),
        ({"watchlist_path": "/etc/passwd"}, G(
            "/api/options/screen-watchlist", puts_budget="1000.0", top_n="10",
            include_non_us="false")),
    ],
    (OA, "analyze_options_symbol"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/options/screen", puts_budget="1000.0", top_n="10"))],
    # max_snapshot_age_minutes / allow_live_fetch are deliberately not
    # forwarded: the route takes only expirations, strikes and kind.
    (OA, "get_option_contracts"): [
        ({"symbol": "BRK-B", "expirations": ["2026-11-20", "2026-12-18"],
          "strikes": [400.0, 410.5], "max_snapshot_age_minutes": 5,
          "allow_live_fetch": False},
         G(f"{SEC}/options/contracts", expirations=["2026-11-20", "2026-12-18"],
           strikes=["400.0", "410.5"], kind="call")),
    ],
    (OA, "price_vertical_spread"): [
        ({"symbol": "BRK-B", "expiration": "2026-11-20", "long_strike": 400.0,
          "short_strike": 410.0},
         call("POST", f"{SEC}/options/vertical-spread", body={
             "expiration": "2026-11-20", "long_strike": 400.0, "short_strike": 410.0,
             "kind": "call", "max_snapshot_age_minutes": 15, "allow_live_fetch": True})),
    ],
    (OA, "get_full_options_chain"): [({"symbol": "BRK-B"}, G(f"{SEC}/options/full-chain"))],
    (OA, "get_unusual_calls"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/options/unusual-calls", min_volume="100", min_vol_oi_ratio="0.5",
        max_expirations="3"))],
    (OA, "get_delta_adjusted_oi"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/options/delta-adjusted-oi", max_expirations="3", risk_free_rate="0.045"))],
    (OA, "get_gamma_wall_history"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/options/gamma-wall-history", since_days="90"))],
    (OA, "get_oi_change_analysis"): [
        ({"symbol": "BRK-B"}, G(f"{SEC}/options/oi-change", days="30", top_n="10",
                                min_oi="100")),
        ({"symbol": "BRK-B", "expiration": "2026-11-20"}, G(
            f"{SEC}/options/oi-change", days="30", top_n="10", min_oi="100",
            expiration="2026-11-20")),
    ],
    (OA, "get_gex_profile"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/options/gex-profile", max_expirations="6", risk_free_rate="0.045"))],

    # ---- company-fundamentals (12) ---------------------------------------
    (CF, "get_earnings_calendar"): [({"symbol": "BRK-B"}, G(f"{SEC}/earnings-calendar"))],
    (CF, "get_fundamental_score"): [({"symbol": "BRK-B"}, G(f"{SEC}/fundamentals/score"))],
    (CF, "get_revenue_growth"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/fundamentals/revenue-growth"))],
    (CF, "get_earnings_acceleration"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/fundamentals/earnings-acceleration"))],
    (CF, "get_fundamental_scores_batch"): [
        ({"symbols": ["aapl", " MSFT ", "AAPL"]},
         call("POST", "/api/securities/fundamentals/scores-batch",
              body={"symbols": ["AAPL", "MSFT"]})),
    ],
    (CF, "get_full_fundamental_profile"): [({"symbol": "BRK-B"}, G(f"{SEC}/fundamentals"))],
    (CF, "get_top_fundamental_stocks"): [({}, G(
        "/api/securities/fundamentals/top", n="10", min_coverage="0.5"))],
    (CF, "get_upcoming_earnings"): [({}, G(
        "/api/securities/fundamentals/upcoming-earnings", days="14",
        include_stale="false"))],
    (CF, "get_cache_stats"): [({}, G("/api/securities/fundamentals/cache-stats"))],
    (CF, "get_sector_fundamental_breakdown"): [
        ({}, G("/api/securities/fundamentals/sector-breakdown", top_n="5")),
        ({"sector": "Technology"}, G("/api/securities/fundamentals/sector-breakdown",
                                     sector="Technology", top_n="5")),
    ],
    (CF, "get_fundamental_score_changes"): [({}, G(
        "/api/securities/fundamentals/score-changes", min_delta="2", since_days="90",
        direction="both"))],
    (CF, "get_fundamental_history"): [({"symbol": "BRK-B", "data_type": "score"}, G(
        f"{SEC}/fundamentals/history", data_type="score", since_days="365"))],

    # ---- news-sentiment (4) ----------------------------------------------
    (NS, "collect_news"): [({"symbol": "BRK-B"}, call(
        "POST", f"{SEC}/news/collect", {"score": "true"}))],
    (NS, "get_news_sentiment"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/news/sentiment", days="7", scored_only="false"))],
    (NS, "get_sentiment_trend"): [({"symbol": "BRK-B"}, G(f"{SEC}/news/trend", days="30"))],
    (NS, "list_news_symbols"): [({}, G("/api/securities/news/symbols"))],

    # ---- market-analysis (3) ---------------------------------------------
    (MA, "get_short_interest"): [({"symbol": "BRK-B"}, G(f"{SEC}/short-interest"))],
    (MA, "get_dark_pool"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/dark-pool", lookback="20", interval="1d"))],
    (MA, "get_bid_ask_spread"): [({"symbol": "BRK-B"}, G(
        f"{SEC}/bid-ask-spread", lookback="20"))],

    # ---- portfolio (6) ---------------------------------------------------
    (PF, "mcp_health_check"): [({}, None)],
    (PF, "get_portfolio"): [({}, G("/api/portfolio/symbols"))],
    (PF, "get_symbol_lots"): [({"ticker": " brk-b "}, G("/api/portfolio/lots"))],
    (PF, "get_portfolio_summary"): [({}, G("/api/portfolio/symbols"))],
    (PF, "list_watchlist"): [({}, G("/api/watchlist"))],
    (PF, "add_to_watchlist"): [
        ({"symbol": "BRK-B"}, call("POST", "/api/watchlist",
                                   body={"symbol": "BRK-B", "name": None, "tags": None})),
        ({"symbol": "BRK-B", "name": "Berkshire", "tags": ["value"]},
         call("POST", "/api/watchlist",
              body={"symbol": "BRK-B", "name": "Berkshire", "tags": ["value"]})),
    ],

    # ---- arbitrage (5) ---------------------------------------------------
    (AR, "mcp_health_check"): [({}, None)],
    (AR, "list_arbitrage_universe"): [({}, G("/api/arbitrage/universe"))],
    (AR, "analyze_arbitrage_pair"): [
        ({"security": "PSLV"}, G("/api/arbitrage/pairs/PSLV", days="365")),
        ({"security": "PSLV", "underlying": "SLV", "zscore_window": 60}, G(
            "/api/arbitrage/pairs/PSLV", underlying="SLV", days="365",
            zscore_window="60")),
    ],
    (AR, "scan_arbitrage"): [
        ({}, G("/api/arbitrage/scan", top_n="20", days="365")),
        ({"kinds": "nav_vehicle"}, G("/api/arbitrage/scan", kinds="nav_vehicle",
                                     top_n="20", days="365")),
    ],
    (AR, "discover_arbitrage_pairs"): [
        ({"symbols": "GLD,GDX"}, G(
            "/api/arbitrage/discover", symbols="GLD,GDX", days="365",
            min_abs_correlation="0.4", require_economic_link="true")),
        ({"symbols": "GLD,GDX", "references": "SPY"}, G(
            "/api/arbitrage/discover", symbols="GLD,GDX", references="SPY", days="365",
            min_abs_correlation="0.4", require_economic_link="true")),
    ],
}

# Tools whose result is not the REST payload verbatim: canned payload -> result.
LOTS = {"lots": [{"symbol": "BRK-B", "qty": 1}, {"symbol": "AAPL", "qty": 2}]}
POST_PROCESSED = {
    (PF, "get_symbol_lots"): (LOTS, {"ticker": "BRK-B", "lots": [LOTS["lots"][0]]}),
    (PF, "get_portfolio_summary"): ({"symbols": [], "totals": {"value": 10}}, {"value": 10}),
}

_HEALTH_SERVER = {OA: "options-analysis-server", PF: "portfolio-server",
                  AR: "arbitrage-server"}


def _expected_items(query):
    items = []
    for key, value in query.items():
        for v in value if isinstance(value, list) else [value]:
            items.append((key, v))
    return sorted(items)


def _surface_routes():
    routes = []
    for line in SURFACE.read_text().splitlines():
        method, path = line.split()[:2]
        routes.append((method, re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")))
    return routes


class _StubbedRest:
    """Patch rest_client's httpx.Client onto a MockTransport that records
    every request and answers with a fixed status and payload."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.payload = {}
        real = httpx.Client

        def factory(**kw):
            return real(transport=httpx.MockTransport(self._handle), **kw)

        self._patch = patch.object(rest_client.httpx, "Client", factory)

    def _handle(self, request):
        self.requests.append(request)
        return httpx.Response(self.status, json=self.payload)

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


def _run_module_cases(module_path, status=200):
    """Call every case for one wrapper; return [(tool, args, expected,
    requests, result, canned)]."""
    module = importlib.import_module(module_path)
    cases = [(tool, args, exp) for (mod, tool), variants in CASES.items()
             if mod == module_path for args, exp in variants]

    async def go():
        out = []
        with _StubbedRest() as rest:
            async with Client(module.mcp) as client:
                for tool, args, expected in cases:
                    canned, _ = POST_PROCESSED.get(
                        (module_path, tool), ({"canned": tool, "nested": {"n": [1, 2]}}, None))
                    rest.requests.clear()
                    rest.status, rest.payload = status, (
                        {"error": "UPSTREAM_DOWN"} if status != 200 else canned)
                    result = await client.call_tool(tool, args, raise_on_error=False)
                    out.append((tool, args, expected, list(rest.requests), result, canned))
        return out

    return asyncio.run(go())


def _modules():
    return [module_path for module_path, _name, _floor in WRAPPERS]


class TestToolContracts(unittest.TestCase):
    """Part 1: each tool makes the documented REST call and passes the answer back."""

    @classmethod
    def setUpClass(cls):
        cls.results = {m: _run_module_cases(m) for m in _modules()}
        cls.routes = _surface_routes()

    def _each(self):
        """Every (module, tool, args, expected, requests, result, canned) row."""
        return [(module_path, *row) for module_path, rows in self.results.items()
                for row in rows]

    def _sub(self, row):
        return self.subTest(module=row[0], tool=row[1], args=row[2])

    def test_one_rest_call_per_tool(self):
        for row in self._each():
            with self._sub(row):
                _m, _t, _a, expected, requests, result, _c = row
                self.assertFalse(result.is_error, result.content)
                self.assertEqual(len(requests), 0 if expected is None else 1)

    def test_request_method_path_query_and_body(self):
        for row in self._each():
            with self._sub(row):
                _m, _t, _a, expected, requests, _r, _c = row
                if expected is None:
                    continue
                req = requests[0]
                self.assertEqual(req.method, expected["method"])
                self.assertEqual(req.url.path, expected["path"])
                self.assertEqual(sorted(req.url.params.multi_items()),
                                 _expected_items(expected["query"]))
                body = json.loads(req.content) if req.content else None
                self.assertEqual(body, expected["body"])

    def test_every_call_names_a_served_route(self):
        for row in self._each():
            with self._sub(row):
                _m, _t, _a, expected, requests, _r, _c = row
                if expected is None:
                    continue
                req = requests[0]
                self.assertTrue(
                    any(m == req.method and rx.match(req.url.path) for m, rx in self.routes),
                    f"{req.method} {req.url.path} is not in {SURFACE.name}")

    def test_response_passes_through(self):
        for row in self._each():
            with self._sub(row):
                module_path, tool, _a, expected, _q, result, canned = row
                if expected is None:
                    self.assertEqual(result.data["server"], _HEALTH_SERVER[module_path])
                    self.assertIn("fastmcp_version", result.data)
                elif (module_path, tool) in POST_PROCESSED:
                    self.assertEqual(result.structured_content,
                                     POST_PROCESSED[(module_path, tool)][1])
                else:
                    self.assertEqual(result.structured_content, canned)


class TestToolErrorShape(unittest.TestCase):
    """A non-2xx from the REST tier is a tool error carrying the status."""

    def setUp(self):
        # fastmcp logs a rich traceback for every tool error; these are expected.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_rest_error_is_a_tool_error(self):
        for module_path in _modules():
            for tool, args, expected, requests, result, _c in _run_module_cases(
                    module_path, status=503):
                if expected is None:
                    continue
                with self.subTest(module=module_path, tool=tool, args=args):
                    self.assertTrue(result.is_error)
                    self.assertEqual(len(requests), 1)
                    self.assertIn("REST tier returned 503", result.content[0].text)
                    self.assertIn("UPSTREAM_DOWN", result.content[0].text)

    def test_reshaping_symbol_never_reaches_the_network(self):
        # #297: rest_client._path refuses it before any connection opens.
        from fastMCPTest import stock_price_server

        async def go():
            with _StubbedRest() as rest:
                async with Client(stock_price_server.mcp) as client:
                    result = await client.call_tool(
                        "get_rsi", {"symbol": "../portfolio"}, raise_on_error=False)
                return result, rest.requests

        result, requests = asyncio.run(go())
        self.assertTrue(result.is_error)
        self.assertIn("INVALID_PATH", result.content[0].text)
        self.assertEqual(requests, [])


class TestEveryToolHasAContract(unittest.TestCase):
    """Part 2: a tool without a case here fails, and so does a stale case."""

    def test_cases_match_registered_tools(self):
        async def names(module_path):
            module = importlib.import_module(module_path)
            async with Client(module.mcp) as client:
                return {t.name for t in await client.list_tools()}

        for module_path in _modules():
            with self.subTest(module=module_path):
                registered = asyncio.run(names(module_path))
                covered = {tool for (mod, tool) in CASES if mod == module_path}
                self.assertEqual(
                    registered - covered, set(),
                    f"{module_path}: add a case to CASES in {Path(__file__).name} "
                    "for each new tool")
                self.assertEqual(covered - registered, set(),
                                 f"{module_path}: stale CASES entries")

    def test_every_case_module_is_a_deployed_wrapper(self):
        self.assertEqual({mod for mod, _ in CASES} - set(_modules()), set())

    def test_total_matches_the_documented_count(self):
        # CLAUDE.md states the count (anchored `grep -c "^@mcp.tool"`); keep them in step.
        self.assertEqual(len(CASES), 62)


if __name__ == "__main__":
    unittest.main()
