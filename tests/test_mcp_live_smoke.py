"""Offline tests for the opt-in live smoke (issue #44, part 3).

``scripts/mcp_live_smoke.py`` is run by hand against the deployed test
wrappers. These tests pin what must hold without the network: it refuses prod,
it selects only read-only tools, and it prints metadata, never a payload. The
wrappers are driven in-memory through the same stubbed REST seam the contract
test uses.
"""
import asyncio
import importlib
import io
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client

from scripts import mcp_live_smoke as smoke
from scripts.ci_wrapper_smoke import WRAPPERS
from scripts.mcp_tool_cases import CASES
from tests.test_mcp_tool_contracts import _StubbedRest

PROD_NUMBER = "127961694257"
TEST_NUMBER = "493357101423"


class TestTargets(unittest.TestCase):
    def test_refuses_prod_by_name(self):
        with self.assertRaises(smoke.RefusedTarget):
            smoke.targets("prod")

    def test_every_wrapper_targets_the_test_project(self):
        urls = smoke.targets("test")
        self.assertEqual(set(urls), {name for _m, name, _f in WRAPPERS})
        for url in urls.values():
            self.assertIn(TEST_NUMBER, url)
            self.assertNotIn(PROD_NUMBER, url)
            self.assertTrue(url.startswith("https://quantcore-") and url.endswith("/mcp"))

    def test_refuses_a_test_environment_pointing_at_prod(self):
        text = smoke.MANIFEST.read_text().replace(
            f'project_number = "{TEST_NUMBER}"', f'project_number = "{PROD_NUMBER}"')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.toml"
            path.write_text(text)
            with self.assertRaises(smoke.RefusedTarget):
                smoke.targets("test", path)

    def test_main_refuses_prod_before_reading_a_token(self):
        with self.assertRaises(SystemExit) as caught, \
                patch("sys.stderr", io.StringIO()):
            smoke.main(["--env", "prod"])
        self.assertEqual(caught.exception.code, 2)


class TestPlan(unittest.TestCase):
    def test_only_reads_are_selected_and_everything_is_accounted_for(self):
        selected, skipped = smoke.plan()
        names = {module_path: name for module_path, name, _f in WRAPPERS}
        chosen = {(name, tool) for name, calls in selected.items() for tool, _a in calls}
        for (module_path, tool), variants in CASES.items():
            expected = variants[0][1]
            key = (names[module_path], tool)
            with self.subTest(tool=tool):
                if expected is None or expected["method"] == "GET":
                    self.assertIn(key, chosen)
                else:
                    self.assertIn(key, skipped)
        self.assertEqual(len(chosen) + len(skipped), len(CASES))

    def test_known_writes_are_skipped(self):
        _selected, skipped = smoke.plan()
        self.assertIn(("portfolio", "add_to_watchlist"), skipped)
        self.assertIn(("news-sentiment", "collect_news"), skipped)


class TestErrorStatus(unittest.TestCase):
    def test_reports_status_not_payload(self):
        text = "REST tier returned 503: {'error': 'secret-ish detail'}"
        self.assertEqual(smoke.error_status(text), "status=503")
        self.assertEqual(smoke.error_status("boom: detail"), "tool error")


class TestRunInMemory(unittest.TestCase):
    """Drive the real wrappers in-memory: endpoints are the ``mcp`` objects."""

    def setUp(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)
        logging.disable(logging.CRITICAL)  # fastmcp logs a traceback per tool error
        self.endpoints = {name: importlib.import_module(module_path).mcp
                          for module_path, name, _f in WRAPPERS}

    def _run(self, selected, status=200):
        out = io.StringIO()
        with _StubbedRest() as rest:
            rest.status = status
            rest.payload = {"error": "PAYLOAD-MARKER"} if status != 200 else {"v": "PAYLOAD-MARKER"}
            failures = asyncio.run(smoke.run(self.endpoints, selected,
                                             client_factory=Client, out=out))
        return failures, out.getvalue()

    def test_all_read_only_tools_pass_and_no_payload_is_printed(self):
        selected, _ = smoke.plan()
        # The two post-processing portfolio tools need their own payload shape.
        selected["portfolio"] = [(t, a) for t, a in selected["portfolio"]
                                 if t not in ("get_symbol_lots", "get_portfolio_summary")]
        failures, text = self._run(selected)
        self.assertEqual(failures, 0, text)
        self.assertNotIn("PAYLOAD-MARKER", text)
        self.assertEqual(text.count("[ok]"), sum(map(len, selected.values())))

    def test_rest_errors_are_failures_reported_by_status_only(self):
        selected = {"stock-price": [("get_rsi", {"symbol": "BRK-B"})]}
        failures, text = self._run(selected, status=503)
        self.assertEqual(failures, 1)
        self.assertIn("[FAIL] stock-price get_rsi: status=503", text)
        self.assertNotIn("PAYLOAD-MARKER", text)

    def test_a_tool_the_wrapper_does_not_advertise_fails(self):
        failures, text = self._run({"arbitrage": [("no_such_tool", {})]})
        self.assertEqual(failures, 1)
        self.assertIn("[FAIL] arbitrage no_such_tool: not advertised", text)


if __name__ == "__main__":
    unittest.main()
