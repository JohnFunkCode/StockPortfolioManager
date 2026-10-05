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
a case for a tool that no longer exists) fails. The cases live in
``scripts/mcp_tool_cases.py``; a new tool needs one there.
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
from scripts.mcp_tool_cases import AR, CASES, OA, PF

ROOT = Path(__file__).resolve().parent.parent
SURFACE = ROOT / "docs" / "openapi-surface.txt"


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
                    f"{module_path}: add a case to CASES in scripts/mcp_tool_cases.py "
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
