"""Boundary tests for bounded multi-symbol REST and MCP requests."""

import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from api.main import create_app
from api.routers import fundamentals as fundamentals_router
from fastMCPTest import company_fundamentals_server as fundamentals_mcp
from quantcore.services.batch_limits import normalize_symbol_batch


class BatchLimitTest(unittest.TestCase):
    def test_normalization_trims_uppercases_and_deduplicates_before_limit(self):
        self.assertEqual(
            normalize_symbol_batch([" aapl ", "AAPL", "msft", " "]),
            ["AAPL", "MSFT"],
        )

    def test_empty_batch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            normalize_symbol_batch([" ", ""])

    def test_over_limit_batch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "limited to 25"):
            normalize_symbol_batch([f"SYM{i}" for i in range(26)])

    def test_exact_limit_batch_is_accepted(self):
        normalized = normalize_symbol_batch([f"SYM{i}" for i in range(25)])
        self.assertEqual(len(normalized), 25)


class FundamentalsBatchBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(), raise_server_exceptions=False)

    def setUp(self):
        self.service = Mock()
        self.service.get_fundamental_scores_batch.return_value = {"ok": True}
        patcher = patch.object(
            fundamentals_router, "services", lambda: Mock(fundamentals=self.service)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_valid_batch_is_normalized_before_service_call(self):
        response = self.client.post(
            "/api/securities/fundamentals/scores-batch",
            json={"symbols": [" aapl ", "AAPL", "msft"]},
        )

        self.assertEqual(response.status_code, 200)
        self.service.get_fundamental_scores_batch.assert_called_once_with(
            ["AAPL", "MSFT"]
        )

    def test_over_limit_batch_is_422_without_service_work(self):
        response = self.client.post(
            "/api/securities/fundamentals/scores-batch",
            json={"symbols": [f"SYM{i}" for i in range(26)]},
        )

        self.assertEqual(response.status_code, 422)
        self.service.get_fundamental_scores_batch.assert_not_called()

    def test_empty_batch_is_422_without_service_work(self):
        response = self.client.post(
            "/api/securities/fundamentals/scores-batch",
            json={"symbols": [" ", ""]},
        )

        self.assertEqual(response.status_code, 422)
        self.service.get_fundamental_scores_batch.assert_not_called()


class FundamentalsMcpBoundaryTest(unittest.TestCase):
    def test_mcp_wrapper_normalizes_before_forwarding(self):
        with patch.object(fundamentals_mcp.rest_client, "post", return_value={}) as post:
            fundamentals_mcp.get_fundamental_scores_batch([" aapl ", "AAPL", "msft"])

        post.assert_called_once_with(
            "/api/securities/fundamentals/scores-batch",
            json={"symbols": ["AAPL", "MSFT"]},
        )

    def test_mcp_wrapper_rejects_over_limit_without_http_call(self):
        with patch.object(fundamentals_mcp.rest_client, "post") as post:
            with self.assertRaisesRegex(ValueError, "limited to 25"):
                fundamentals_mcp.get_fundamental_scores_batch(
                    [f"SYM{i}" for i in range(26)]
                )

        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
