"""Tests for the MCP gateway seam and the company-fundamentals wrapper
(85%-campaign part 9): mcp_gateway/rest_client.py — the SINGLE conduit every
wrapper uses to reach the REST tier — plus the last 0%-covered wrapper's tool
bodies (one rest_client call deep, Rule 6). httpx is mocked at the transport
boundary; no network.
"""
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from fastMCPTest import company_fundamentals_server as cfs  # noqa: E402
from mcp_gateway import rest_client  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
