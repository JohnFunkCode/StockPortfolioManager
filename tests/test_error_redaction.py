"""An unexpected failure never echoes its exception text to the caller.

quantcore-api is public (allUsers, JWT-gated), and exception text can carry a
DSN with its password. Every catch-all 500 answers a generic message, and its
log line names the exception class only (CLAUDE.md never-log policy, which
covers responses as well as logs). A ValueError's text is a user-facing
validation message and still reaches the caller.
"""
import logging
import unittest
from unittest.mock import patch

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

from api.auth import require_owner  # noqa: E402
from api.deps import INTERNAL_ERROR  # noqa: E402
from api.main import create_app  # noqa: E402
from api.routers import options as options_router  # noqa: E402
from api.routers import portfolio as portfolio_router  # noqa: E402
from api.routers import prices as prices_router  # noqa: E402
from api.routers import sentiment as sentiment_router  # noqa: E402
from api.sse import event_stream  # noqa: E402
from quantcore.services.chat import TextDelta  # noqa: E402
from tests.test_routers_passthrough import OPTIONS_GETS, PRICES_GETS  # noqa: E402

SECRET = "postgresql://quantcore:s3cr3t-pw@10.0.0.5:5432/quantcore"


class LeakyError(Exception):
    """Neither ValueError nor RuntimeError: the framework's catch-all 500."""


def exploding_bag(exc):
    class Service:
        def __getattr__(self, name):
            def boom(*a, **k):
                raise exc
            return boom

    class Bag:
        def __getattr__(self, name):
            return Service()

    return lambda: Bag()


class RouteRedactionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=False)

    def patch_services(self, exc, *routers):
        for router in routers:
            p = patch.object(router, "services", exploding_bag(exc))
            p.start()
            self.addCleanup(p.stop)

    def assert_redacted(self, resp, logs, path):
        self.assertEqual(resp.status_code, 500, path)
        self.assertNotIn("s3cr3t", resp.text, path)
        self.assertNotIn("10.0.0.5", resp.text, path)
        self.assertIn(INTERNAL_ERROR, resp.text, path)
        joined = "\n".join(logs.output)
        self.assertNotIn("s3cr3t", joined, path)
        self.assertIn("LeakyError", joined, path)

    def test_prices_and_options_routes(self):
        self.patch_services(LeakyError(f"could not connect {SECRET}"),
                            prices_router, options_router)
        for path in PRICES_GETS + OPTIONS_GETS:
            with self.assertLogs("api", level="WARNING") as logs:
                resp = self.client.get(path)
            self.assert_redacted(resp, logs, path)

    def test_guarded_route_logs_route_ticker_and_class(self):
        self.patch_services(LeakyError(SECRET), options_router)
        with self.assertLogs("api.routers.options", level="WARNING") as logs:
            resp = self.client.get("/api/securities/wdc/options/iv-rank")
        self.assertEqual(resp.json(), {"error": INTERNAL_ERROR})
        self.assertEqual(logs.output, [
            "WARNING:api.routers.options:get_iv_rank failed ticker=WDC "
            "(tests.test_error_redaction.LeakyError)"
        ])

    def test_guarded_route_keeps_a_value_errors_text(self):
        # A ValueError is the services' "bad input"; MCP clients act on it.
        self.patch_services(ValueError("bad interval"), prices_router)
        with self.assertLogs("api.routers.prices", level="WARNING"):
            resp = self.client.get("/api/securities/TST/ohlcv")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "bad interval"})

    def test_unguarded_route_uses_the_framework_body(self):
        # signals/technical has no try/except: api/errors.py answers it.
        self.patch_services(LeakyError(SECRET), prices_router)
        with self.assertLogs("api.errors", level="WARNING") as logs:
            resp = self.client.get("/api/securities/TST/signals/technical")
        self.assertEqual(resp.json(), {
            "error": "InternalServerError", "message": INTERNAL_ERROR, "status": 500,
        })
        self.assertEqual(logs.output, [
            "WARNING:api.errors:GET /api/securities/TST/signals/technical failed "
            "(tests.test_error_redaction.LeakyError)"
        ])

    def test_sentiment_routes_keep_their_shapes(self):
        self.patch_services(LeakyError(SECRET), sentiment_router)
        self.app.dependency_overrides[require_owner] = lambda: "tester"
        self.addCleanup(self.app.dependency_overrides.pop, require_owner, None)
        for name in ("load_portfolio", "load_watchlist"):
            p = patch.object(sentiment_router, name, lambda *a: [])
            p.start()
            self.addCleanup(p.stop)

        with self.assertLogs("api.routers.sentiment", level="WARNING") as logs:
            resp = self.client.get("/api/securities/news/sentiment-summary")
        self.assert_redacted(resp, logs, "sentiment-summary")
        self.assertEqual(resp.json(), {"error": INTERNAL_ERROR, "items": []})

        with self.assertLogs("api.routers.sentiment", level="WARNING") as logs:
            resp = self.client.get("/api/securities/tst/news")
        self.assert_redacted(resp, logs, "news")
        self.assertEqual(resp.json(), {"ticker": "TST", "error": INTERNAL_ERROR, "articles": []})

    def test_symbol_lookup(self):
        self.patch_services(LeakyError(SECRET), portfolio_router)
        with self.assertLogs("api.routers.portfolio", level="WARNING") as logs:
            resp = self.client.get("/api/securities/lookup?symbol=tst")
        self.assert_redacted(resp, logs, "lookup")
        self.assertIn("lookup_security failed symbol=TST", logs.output[0])


class ChatStreamRedactionTest(unittest.TestCase):
    def frames(self, exc):
        def events():
            yield TextDelta(delta="partial")
            raise exc

        logging.disable(logging.CRITICAL)  # the stream's own logger.exception
        self.addCleanup(logging.disable, logging.NOTSET)
        return list(event_stream(events()))

    def test_unexpected_failure_frame_is_generic(self):
        frames = self.frames(LeakyError(SECRET))
        self.assertTrue(frames[-1].startswith("event: error\n"))
        self.assertIn(INTERNAL_ERROR, frames[-1])
        self.assertNotIn("s3cr3t", "".join(frames))

    def test_validation_message_still_reaches_the_user(self):
        frames = self.frames(ValueError("unknown action 'explode'"))
        self.assertIn("unknown action 'explode'", frames[-1])


if __name__ == "__main__":
    unittest.main()
