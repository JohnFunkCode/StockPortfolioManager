"""Tests for the MCP gateway seam and the company-fundamentals wrapper
(85%-campaign part 9): mcp_gateway/rest_client.py — the SINGLE conduit every
wrapper uses to reach the REST tier — plus the last 0%-covered wrapper's tool
bodies (one rest_client call deep, Rule 6). httpx is mocked at the transport
boundary; no network.
"""
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import yaml

from fastMCPTest import company_fundamentals_server as cfs  # noqa: E402
from fastMCPTest import arbitrage_server as arb  # noqa: E402
from fastMCPTest import options_analysis as oa  # noqa: E402
from fastMCPTest import portfolio_server as pfs  # noqa: E402
from fastMCPTest import stock_price_server as sps  # noqa: E402
from mcp_gateway import rest_client  # noqa: E402
from mcp_gateway import serve  # noqa: E402
from scripts import ci_wrapper_smoke  # noqa: E402


def http_response(status=200, json_body=None, text_body=None):
    request = httpx.Request("GET", "http://testserver/x")
    if text_body is not None:
        return httpx.Response(status, text=text_body, request=request)
    return httpx.Response(status, json=json_body if json_body is not None else {},
                          request=request)


class TestRestClientConfig(unittest.TestCase):
    def test_base_url_and_timeout_env_overrides(self):
        with patch.dict(os.environ, {"QUANTCORE_REST_URL": "https://api.example.com/"}):
            self.assertEqual(rest_client._base_url(), "https://api.example.com")
        with patch.dict(os.environ, {"QUANTCORE_REST_TIMEOUT": "5.5"}):
            self.assertEqual(rest_client._timeout(), 5.5)
        with patch.dict(os.environ, {"QUANTCORE_REST_TIMEOUT": "not-a-number"}):
            self.assertEqual(rest_client._timeout(), rest_client.DEFAULT_TIMEOUT)

    def test_path_normalization(self):
        self.assertEqual(rest_client._path("api/x"), "/api/x")
        self.assertEqual(rest_client._path("/api/x"), "/api/x")

    def test_headers_prefer_explicit_token(self):
        headers = rest_client._headers("tok-123")
        self.assertEqual(headers["Authorization"], "Bearer tok-123")

    def test_incoming_bearer_lifted_from_mcp_request(self):
        with patch("fastmcp.server.dependencies.get_http_headers",
                   return_value={"authorization": "Bearer agent-jwt"}):
            self.assertEqual(rest_client._incoming_auth_token(), "agent-jwt")
        with patch("fastmcp.server.dependencies.get_http_headers",
                   return_value={}):
            self.assertIsNone(rest_client._incoming_auth_token())

    def test_handle_success_error_and_non_json(self):
        self.assertEqual(rest_client._handle(http_response(200, {"ok": 1})), {"ok": 1})
        with self.assertRaises(rest_client.RestError) as ctx:
            rest_client._handle(http_response(500, {"error": "boom"}))
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.payload, {"error": "boom"})
        with self.assertRaises(rest_client.RestError) as ctx:
            rest_client._handle(http_response(502, text_body="<html>bad gateway"))
        self.assertIn("error", ctx.exception.payload)


class TestRestClientVerbs(unittest.TestCase):
    def arm_client(self, response):
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.return_value = response
        client.post.return_value = response
        return client

    def test_get_drops_none_params_and_returns_payload(self):
        client = self.arm_client(http_response(200, {"ok": True}))
        with patch.object(httpx, "Client", return_value=client):
            out = rest_client.get("/api/x", days=30, kind=None,
                                  strikes=[120.0, 125.0])
        self.assertEqual(out, {"ok": True})
        _, kwargs = client.get.call_args
        self.assertEqual(kwargs["params"], {"days": 30, "strikes": [120.0, 125.0]})

    def test_post_forwards_json_body_and_raises_on_error(self):
        client = self.arm_client(http_response(402, {"error": "plan"}))
        with patch.object(httpx, "Client", return_value=client):
            with self.assertRaises(rest_client.RestError) as ctx:
                rest_client.post("/api/y", json={"a": 1})
        self.assertEqual(ctx.exception.status_code, 402)
        _, kwargs = client.post.call_args
        self.assertEqual(kwargs["json"], {"a": 1})

    def test_get_translates_upstream_timeout_without_leaking_exception_details(self):
        client = self.arm_client(http_response(200))
        client.get.side_effect = httpx.ReadTimeout("upstream credentials must not appear")
        with patch.object(httpx, "Client", return_value=client):
            with self.assertRaises(rest_client.RestError) as ctx:
                rest_client.get("/api/slow")
        self.assertEqual(ctx.exception.status_code, 504)
        self.assertEqual(ctx.exception.payload, {
            "error": "REST_TIMEOUT",
            "message": "REST tier request timed out",
        })

    def test_post_translates_upstream_connection_failure(self):
        client = self.arm_client(http_response(200))
        client.post.side_effect = httpx.ConnectError("connection details")
        with patch.object(httpx, "Client", return_value=client):
            with self.assertRaises(rest_client.RestError) as ctx:
                rest_client.post("/api/unavailable")
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.payload["error"], "REST_UNAVAILABLE")


class TestMcpServeConfig(unittest.TestCase):
    def test_ping_interval_defaults_and_rejects_invalid_values(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(serve._ping_interval_ms(), serve.DEFAULT_PING_INTERVAL_MS)
        with patch.dict(os.environ, {serve.PING_INTERVAL_ENV: "45000"}):
            self.assertEqual(serve._ping_interval_ms(), 45000)
        for value in ("0", "-1", "not-a-number"):
            with patch.dict(os.environ, {serve.PING_INTERVAL_ENV: value}):
                self.assertEqual(serve._ping_interval_ms(), serve.DEFAULT_PING_INTERVAL_MS)

    def test_configure_keepalive_uses_fastmcp_ping_middleware(self):
        mcp = Mock()
        with patch.dict(os.environ, {serve.PING_INTERVAL_ENV: "15000"}):
            serve._configure_keepalive(mcp)
        middleware = mcp.add_middleware.call_args.args[0]
        self.assertEqual(type(middleware).__name__, "PingMiddleware")
        self.assertEqual(middleware.interval_ms, 15000)

    def test_main_wires_keepalive_before_starting_server(self):
        module = Mock()
        module.mcp = Mock()
        with patch.dict(os.environ, {
            "SERVER_MODULE": "fake.wrapper",
            "PORT": "6123",
        }), patch.object(serve.importlib, "import_module", return_value=module), \
                patch.object(serve, "_configure_keepalive") as configure:
            serve.main()
        configure.assert_called_once_with(module.mcp)
        module.mcp.run.assert_called_once_with(
            transport="http", host="0.0.0.0", port=6123
        )


class TestMcpDeploymentPolicy(unittest.TestCase):
    REPO = Path(__file__).resolve().parent.parent

    def test_every_wrapper_rollout_carries_the_timeout_policy(self):
        services = {
            friendly_name for _, friendly_name, _ in ci_wrapper_smoke.WRAPPERS
        }
        self.assertEqual(services, {
            "stock-price", "options-analysis", "company-fundamentals",
            "news-sentiment", "market-analysis", "portfolio", "arbitrage",
        })
        for workflow_name in ("deploy.yml", "prod-rollout.yml"):
            text = (self.REPO / ".github" / "workflows" / workflow_name).read_text()
            self.assertIn("MCP_REQUEST_TIMEOUT: 900s", text, workflow_name)
            self.assertIn(
                "for s in stock-price options-analysis company-fundamentals "
                "news-sentiment market-analysis; do\n"
                "            gcloud run deploy \"quantcore-$s\"",
                text,
                workflow_name,
            )
            for service in ("portfolio", "arbitrage"):
                marker = f"gcloud run deploy quantcore-{service}"
                start = text.index(marker)
                block = text[start:text.find("\n      - name:", start)]
                self.assertIn(
                    '--timeout "$MCP_REQUEST_TIMEOUT"', block,
                    f"{workflow_name}: {service}",
                )


class TestCompanyFundamentalsWrapper(unittest.TestCase):
    """Every tool body must be exactly one rest_client call deep (Rule 6)."""

    def test_symbol_tools_hit_their_routes(self):
        cases = [
            (lambda: cfs.get_earnings_calendar("intc"), "get", "earnings-calendar"),
            (lambda: cfs.get_fundamental_score("intc"), "get", "fundamentals/score"),
            (lambda: cfs.get_revenue_growth("intc"), "get", "revenue-growth"),
            (lambda: cfs.get_earnings_acceleration("intc"), "get", "earnings-acceleration"),
            (lambda: cfs.get_full_fundamental_profile("intc"), "get", "/fundamentals"),
        ]
        for call, verb, fragment in cases:
            with patch.object(cfs, "rest_client") as rc:
                getattr(rc, verb).return_value = {"ok": True}
                out = call()
                self.assertEqual(out, {"ok": True}, fragment)
                path = getattr(rc, verb).call_args[0][0]
                self.assertIn(fragment, path)
                self.assertIn("intc", path)

    def test_collection_tools(self):
        with patch.object(cfs, "rest_client") as rc:
            rc.post.return_value = {"ok": True}
            rc.get.return_value = {"ok": True}

            cfs.get_fundamental_scores_batch(["a", "b"])
            self.assertIn("scores-batch", rc.post.call_args[0][0])
            self.assertEqual(rc.post.call_args[1]["json"], {"symbols": ["a", "b"]})

            cfs.get_top_fundamental_stocks(n=5, min_coverage=0.7)
            self.assertIn("fundamentals/top", rc.get.call_args[0][0])
            self.assertEqual(rc.get.call_args[1]["n"], 5)

    def test_remaining_collection_tools_route_correctly(self):
        with patch.object(cfs, "rest_client") as rc:
            rc.get.return_value = {"ok": True}
            cfs.get_upcoming_earnings(days=7)
            self.assertIn("upcoming-earnings", rc.get.call_args[0][0])
            cfs.get_cache_stats()
            self.assertIn("cache-stats", rc.get.call_args[0][0])
            cfs.get_sector_fundamental_breakdown(sector="Tech")
            self.assertIn("sector-breakdown", rc.get.call_args[0][0])
            cfs.get_fundamental_score_changes(min_delta=3)
            self.assertIn("score-changes", rc.get.call_args[0][0])
            cfs.get_fundamental_history("intc", "fundamental_score")
            self.assertIn("history", rc.get.call_args[0][0])


class TestArbitrageWrapperTools(unittest.TestCase):
    """The four arbitrage tools on their own wrapper (Rule 6)."""

    def test_universe_and_scan_route_correctly(self):
        with patch.object(arb, "rest_client") as rc:
            rc.get.return_value = {"ok": True}

            arb.list_arbitrage_universe()
            self.assertEqual(rc.get.call_args[0][0], "/api/arbitrage/universe")

            arb.scan_arbitrage(kinds="nav_vehicle", top_n=5, days=90)
            self.assertEqual(rc.get.call_args[0][0], "/api/arbitrage/scan")
            self.assertEqual(rc.get.call_args[1]["kinds"], "nav_vehicle")
            self.assertEqual(rc.get.call_args[1]["top_n"], 5)
            self.assertEqual(rc.get.call_args[1]["days"], 90)

    def test_empty_optional_args_are_not_forwarded(self):
        """Blank kinds/underlying must be omitted, not sent as empty strings."""
        with patch.object(arb, "rest_client") as rc:
            rc.get.return_value = {"ok": True}

            arb.scan_arbitrage()
            self.assertNotIn("kinds", rc.get.call_args[1])

            arb.analyze_arbitrage_pair("MSTR")
            self.assertNotIn("underlying", rc.get.call_args[1])
            self.assertNotIn("zscore_window", rc.get.call_args[1])

    def test_analyze_pair_routes_with_its_options(self):
        with patch.object(arb, "rest_client") as rc:
            rc.get.return_value = {"ok": True}
            arb.analyze_arbitrage_pair("MSTR", underlying="BTC-USD",
                                       days=180, zscore_window=60)
            self.assertEqual(rc.get.call_args[0][0], "/api/arbitrage/pairs/MSTR")
            self.assertEqual(rc.get.call_args[1]["underlying"], "BTC-USD")
            self.assertEqual(rc.get.call_args[1]["zscore_window"], 60)

    def test_discover_routes_with_its_gate(self):
        with patch.object(arb, "rest_client") as rc:
            rc.get.return_value = {"ok": True}
            arb.discover_arbitrage_pairs("GDX,FCX", references="GC=F",
                                         min_abs_correlation=0.6,
                                         require_economic_link=False)
            self.assertEqual(rc.get.call_args[0][0], "/api/arbitrage/discover")
            self.assertEqual(rc.get.call_args[1]["symbols"], "GDX,FCX")
            self.assertEqual(rc.get.call_args[1]["references"], "GC=F")
            self.assertFalse(rc.get.call_args[1]["require_economic_link"])

    def test_arbitrage_tools_live_only_on_the_arbitrage_server(self):
        """Pins the split. These tools were originally bolted onto the
        stock-price wrapper to avoid standing up a sixth Cloud Run service;
        that was the wrong trade, and this stops them drifting back."""
        for tool in ("list_arbitrage_universe", "analyze_arbitrage_pair",
                     "scan_arbitrage", "discover_arbitrage_pairs"):
            self.assertTrue(hasattr(arb, tool), f"{tool} missing from arbitrage_server")
            self.assertFalse(hasattr(sps, tool),
                             f"{tool} is still on stock_price_server")

    def test_server_reports_its_own_identity(self):
        self.assertEqual(arb.mcp.name, "arbitrage-server")
        health = arb.mcp_health_check()
        self.assertEqual(health["server"], "arbitrage-server")


class TestWrapperPortMap(unittest.TestCase):
    """Every wrapper must own a distinct local port.

    Added after a real collision: the portfolio wrapper claimed 6006 while the
    arbitrage server was being written against a port map that predated it.
    Cloud Run gives each service its own host so CI stayed green, but the
    compose stack cannot bind the same port twice and the two `-local` MCP
    entries pointed at one URL. These checks fail on the next stale port map
    instead of leaving it for review.
    """

    REPO = Path(__file__).resolve().parent.parent

    def wrapper_defaults(self) -> dict[str, int]:
        """Port default parsed from each wrapper's __main__ guard.

        Not every wrapper has one — options_analysis is CLI-first and takes its
        port purely from the environment — so compose is the authority below
        and these are cross-checked only where they exist.
        """
        ports = {}
        pattern = re.compile(r'os\.environ\.get\("PORT",\s*"(\d+)"\)')
        for path in sorted((self.REPO / "fastMCPTest").glob("*.py")):
            match = pattern.search(path.read_text())
            if match:
                ports[path.stem] = int(match.group(1))
        return ports

    def compose_wrappers(self) -> dict[str, dict]:
        """Compose services that run an MCP wrapper, keyed by module stem."""
        compose = yaml.safe_load((self.REPO / "docker-compose.yml").read_text())
        out = {}
        for service in compose["services"].values():
            env = service.get("environment") or {}
            module = env.get("SERVER_MODULE")
            if module:
                out[module.rsplit(".", 1)[-1]] = {
                    "port": int(env["PORT"]),
                    "ports": service["ports"],
                }
        return out

    def test_every_wrapper_publishes_a_distinct_port(self):
        wrappers = self.compose_wrappers()
        self.assertGreaterEqual(len(wrappers), 7, "wrappers not discovered")
        ports = [w["port"] for w in wrappers.values()]
        duplicates = {p for p in ports if ports.count(p) > 1}
        self.assertEqual(duplicates, set(), f"port collision in {wrappers}")

    def test_module_defaults_agree_with_compose(self):
        defaults = self.wrapper_defaults()
        duplicates = {p for p in defaults.values() if list(defaults.values()).count(p) > 1}
        self.assertEqual(duplicates, set(), f"duplicate module defaults: {defaults}")
        for stem, service in self.compose_wrappers().items():
            if stem in defaults:
                self.assertEqual(defaults[stem], service["port"],
                                 f"{stem}: module default vs compose PORT")
            self.assertIn(f"{service['port']}:{service['port']}", service["ports"], stem)

    def test_local_mcp_entries_are_unique_and_match_compose(self):
        config = json.loads((self.REPO / ".mcp.json").read_text())
        local_urls = [
            entry["url"] for name, entry in config["mcpServers"].items()
            if name.endswith("-local")
        ]
        self.assertEqual(len(local_urls), len(set(local_urls)),
                         f"duplicate local MCP URLs: {local_urls}")
        published = {w["port"] for w in self.compose_wrappers().values()}
        for url in local_urls:
            port = int(url.rsplit(":", 1)[1].split("/")[0])
            self.assertIn(port, published, f"{url} has no compose service")


class TestOptionsWrapperConsolidation(unittest.TestCase):
    """Issue #159: the options surface lives on options-analysis, only.

    `get_option_contracts` and `price_vertical_spread` previously existed on
    BOTH servers with identical bodies hitting identical routes — an agent
    connected to both saw duplicate tool names with no way to choose. These
    tests pin the single owner so the duplication cannot return.
    """

    MOVED = (
        "get_full_options_chain",
        "get_option_contracts",
        "price_vertical_spread",
        "get_unusual_calls",
        "get_delta_adjusted_oi",
        "get_gamma_wall_history",
        "get_oi_change_analysis",
        "get_gex_profile",
    )

    def test_options_tools_live_on_options_analysis(self):
        for tool in self.MOVED:
            self.assertTrue(hasattr(oa, tool), f"{tool} missing from options_analysis")

    def test_options_tools_are_gone_from_stock_price(self):
        for tool in self.MOVED:
            self.assertFalse(hasattr(sps, tool),
                             f"{tool} is still on stock_price_server")

    def test_moved_tools_stay_one_rest_call_deep(self):
        cases = [
            (lambda: oa.get_full_options_chain("INTC"), "options/full-chain"),
            (lambda: oa.get_unusual_calls("INTC"), "unusual-calls"),
            (lambda: oa.get_delta_adjusted_oi("INTC"), "delta-adjusted-oi"),
            (lambda: oa.get_gamma_wall_history("INTC"), "gamma-wall-history"),
            (lambda: oa.get_oi_change_analysis("INTC"), "oi-change"),
            (lambda: oa.get_gex_profile("INTC"), "gex-profile"),
        ]
        for call, fragment in cases:
            with patch.object(oa, "rest_client") as rc:
                rc.get.return_value = {"ok": True}
                self.assertEqual(call(), {"ok": True}, fragment)
                self.assertEqual(rc.get.call_count, 1, fragment)
                self.assertIn(fragment, rc.get.call_args[0][0])

    def test_the_deduplicated_pair_still_routes_correctly(self):
        with patch.object(oa, "rest_client") as rc:
            rc.get.return_value = {"ok": True}
            oa.get_option_contracts("INTC", ["2026-08-21"], [120.0])
            self.assertIn("options/contracts", rc.get.call_args[0][0])
        with patch.object(oa, "rest_client") as rc:
            rc.post.return_value = {"ok": True}
            oa.price_vertical_spread("INTC", "2026-08-21", 120.0, 125.0)
            self.assertIn("options/vertical-spread", rc.post.call_args[0][0])

    def test_no_tool_name_is_served_by_two_wrappers(self):
        """The general form of the duplication this issue fixed.

        Covers the arbitrage server too, which landed on main in #158 while
        this branch was open — a new wrapper is exactly when this class of
        mistake reappears.
        """
        seen: dict[str, str] = {}
        for module, label in ((oa, "options-analysis"), (sps, "stock-price"),
                              (cfs, "company-fundamentals"), (arb, "arbitrage")):
            for name in dir(module):
                obj = getattr(module, name)
                if callable(obj) and getattr(obj, "__module__", "") == module.__name__ \
                        and not name.startswith("_") and name.startswith(("get_", "price_", "analyze_")):
                    if name in seen:
                        self.fail(f"'{name}' is on both {seen[name]} and {label}")
                    seen[name] = label


class TestPortfolioWatchlistTools(unittest.TestCase):
    """The watchlist tools on the portfolio wrapper (issue #83).

    ``add_to_watchlist`` is the only write tool on this wrapper, so the
    boundary it sits on is worth pinning: one POST deep, no delete verb.
    """

    def test_list_watchlist_is_one_get_deep(self):
        with patch.object(pfs, "rest_client") as rc:
            rc.get.return_value = {"securities": [{"symbol": "INTC"}]}
            out = pfs.list_watchlist()
            self.assertEqual(rc.get.call_args[0][0], "/api/watchlist")
            self.assertEqual(out["securities"][0]["symbol"], "INTC")

    def test_add_to_watchlist_posts_the_full_body(self):
        """No ``currency`` in the body, deliberately: the REST tier reads it
        off the exchange, and a currency an agent inferred from a ticker
        suffix would label a foreign market cap as dollars.
        """
        with patch.object(pfs, "rest_client") as rc:
            rc.post.return_value = {
                "symbol": "INTC", "destination": "watchlist", "currency": "USD",
            }
            out = pfs.add_to_watchlist("INTC", name="Intel", tags=["Semis"])
            self.assertEqual(rc.post.call_args[0][0], "/api/watchlist")
            self.assertEqual(
                rc.post.call_args[1]["json"],
                {"symbol": "INTC", "name": "Intel", "tags": ["Semis"]},
            )
            self.assertEqual(out["destination"], "watchlist")
            self.assertEqual(out["currency"], "USD")

    def test_add_to_watchlist_takes_no_currency_argument(self):
        """A guard on the tool signature itself — the currency came out of the
        agent's hands on purpose, and putting it back is a regression.
        """
        import inspect

        params = inspect.signature(pfs.add_to_watchlist).parameters
        self.assertNotIn("currency", params)

    def test_the_seam_still_has_no_destructive_verb(self):
        """Decision 4: agents may add to the shared watchlist, never remove.

        The wrapper's docstring claims this, and the claim is only true as
        long as nobody adds the verb — so assert it rather than trusting the
        prose. Removal is a UI action.
        """
        for verb in ("delete", "put", "patch"):
            self.assertFalse(hasattr(rest_client, verb), verb)
        self.assertFalse(hasattr(pfs, "remove_from_watchlist"))


if __name__ == "__main__":
    unittest.main()
