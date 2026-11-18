"""Registry-content tests for the portfolio Sidekick components (issue #126,
PR 4 Step 4.7).

Generic cross-registry consistency (show_component enum matches
BACKEND_COMPONENT_REGISTRY, every registry prop is declared in the tool
schema, etc.) is already covered for all components by
tests/test_chat_protocol.py. This file covers the portfolio-specific
components: their exact prop specs, and the guarantee that none of them can
ever be pointed at another user's data via an `owner` prop.

There is no automated cross-language check against
frontend/src/chat/componentRegistry.tsx — parity is maintained by mirroring
these cases in frontend/src/chat/componentRegistry.test.ts, same convention
already used for every other component here.
"""
import unittest

from quantcore.services.chat_tools import (
    BACKEND_COMPONENT_REGISTRY,
    validate_directive,
)


class TestPortfolioTableDirective(unittest.TestCase):
    def test_registered_with_no_props(self):
        self.assertEqual(BACKEND_COMPONENT_REGISTRY["portfolio_table"], {})

    def test_empty_props_accepted(self):
        ok, reason = validate_directive("portfolio_table", {})
        self.assertTrue(ok, reason)

    def test_owner_prop_rejected(self):
        """A model-settable owner would let the LLM request another user's
        portfolio; the card must always resolve the owner from the caller's
        own authenticated session instead."""
        ok, reason = validate_directive("portfolio_table", {"owner": "someone_else"})
        self.assertFalse(ok)
        self.assertIn("owner", reason)

    def test_ticker_prop_rejected(self):
        """No ticker either — this component is portfolio-wide, not per-symbol."""
        ok, _ = validate_directive("portfolio_table", {"ticker": "INTC"})
        self.assertFalse(ok)


class TestPortfolioAllocationDirective(unittest.TestCase):
    """Issue #147 Part A. Same no-props contract as portfolio_table — it charts
    the same holdings, so it inherits the same cross-user hazard."""

    def test_registered_with_no_props(self):
        self.assertEqual(BACKEND_COMPONENT_REGISTRY["portfolio_allocation"], {})

    def test_empty_props_accepted(self):
        ok, reason = validate_directive("portfolio_allocation", {})
        self.assertTrue(ok, reason)

    def test_owner_prop_rejected(self):
        ok, reason = validate_directive("portfolio_allocation", {"owner": "someone_else"})
        self.assertFalse(ok)
        self.assertIn("owner", reason)

    def test_ticker_prop_rejected(self):
        ok, _ = validate_directive("portfolio_allocation", {"ticker": "INTC"})
        self.assertFalse(ok)


class TestRealizedPnlDirective(unittest.TestCase):
    """#235. The realized card reads the caller's own sales, so it takes the
    same no-props contract: no owner, no book and no period to name."""

    def test_registered_with_no_props(self):
        self.assertEqual(BACKEND_COMPONENT_REGISTRY["realized_pnl"], {})

    def test_empty_props_accepted(self):
        ok, reason = validate_directive("realized_pnl", {})
        self.assertTrue(ok, reason)

    def test_owner_book_and_period_props_rejected(self):
        for prop, value in (("owner", "someone_else"), ("book_id", 7), ("period", "all")):
            with self.subTest(prop=prop):
                ok, reason = validate_directive("realized_pnl", {prop: value})
                self.assertFalse(ok)
                self.assertIn(prop, reason)

    def test_described_to_the_model(self):
        from quantcore.services.chat_tools import TOOL_SCHEMAS

        show = next(t for t in TOOL_SCHEMAS if t["name"] == "show_component")
        self.assertIn("'realized_pnl'", show["description"])


class TestCashLedgerDirective(unittest.TestCase):
    """#431 step 3.1. The cash card reads the caller's own real book, so it
    takes the same no-props contract: no owner and no book to name."""

    def test_registered_with_no_props(self):
        self.assertEqual(BACKEND_COMPONENT_REGISTRY["cash_ledger"], {})

    def test_empty_props_accepted(self):
        ok, reason = validate_directive("cash_ledger", {})
        self.assertTrue(ok, reason)

    def test_owner_and_book_props_rejected(self):
        for prop, value in (("owner", "someone_else"), ("book_id", 7)):
            with self.subTest(prop=prop):
                ok, reason = validate_directive("cash_ledger", {prop: value})
                self.assertFalse(ok)
                self.assertIn(prop, reason)

    def test_described_to_the_model(self):
        from quantcore.services.chat_tools import TOOL_SCHEMAS

        show = next(t for t in TOOL_SCHEMAS if t["name"] == "show_component")
        self.assertIn("'cash_ledger'", show["description"])


class TestBookPerformanceDirective(unittest.TestCase):
    """#431 step 5.3. The performance card reads the caller's own real book,
    so it takes the same no-props contract: no owner, no book and no
    period to name."""

    def test_registered_with_no_props(self):
        self.assertEqual(BACKEND_COMPONENT_REGISTRY["book_performance"], {})

    def test_empty_props_accepted(self):
        ok, reason = validate_directive("book_performance", {})
        self.assertTrue(ok, reason)

    def test_owner_book_and_period_props_rejected(self):
        for prop, value in (("owner", "someone_else"), ("book_id", 7), ("period", "all")):
            with self.subTest(prop=prop):
                ok, reason = validate_directive("book_performance", {prop: value})
                self.assertFalse(ok)
                self.assertIn(prop, reason)

    def test_described_to_the_model(self):
        from quantcore.services.chat_tools import TOOL_SCHEMAS

        show = next(t for t in TOOL_SCHEMAS if t["name"] == "show_component")
        self.assertIn("'book_performance'", show["description"])


class TestSymbolLotsDirective(unittest.TestCase):
    def test_registered_with_ticker_only(self):
        self.assertEqual(BACKEND_COMPONENT_REGISTRY["symbol_lots"], {"ticker": str})

    def test_valid_ticker_accepted(self):
        ok, reason = validate_directive("symbol_lots", {"ticker": "INTC"})
        self.assertTrue(ok, reason)

    def test_missing_ticker_rejected(self):
        ok, reason = validate_directive("symbol_lots", {})
        self.assertFalse(ok)
        self.assertIn("ticker", reason)

    def test_owner_prop_rejected(self):
        ok, reason = validate_directive("symbol_lots", {"ticker": "INTC", "owner": "someone_else"})
        self.assertFalse(ok)
        self.assertIn("owner", reason)


class TestStopLossDirective(unittest.TestCase):
    """#399. The card reads the caller's own lots server-side (position=mine),
    so a model-settable position or owner would be a way to misstate — or read —
    someone's holdings. Mirrored in componentRegistry.test.ts."""

    def test_registered_with_ticker_only(self):
        self.assertEqual(BACKEND_COMPONENT_REGISTRY["stop_loss"], {"ticker": str})

    def test_valid_ticker_accepted(self):
        ok, reason = validate_directive("stop_loss", {"ticker": "INTC"})
        self.assertTrue(ok, reason)

    def test_position_and_owner_props_rejected(self):
        for extra in ({"owner": "someone_else"}, {"cost_basis": 150.0}, {"shares": 10}):
            ok, _ = validate_directive("stop_loss", {"ticker": "INTC", **extra})
            self.assertFalse(ok, extra)


class TestSentimentTrendDirective(unittest.TestCase):
    """#411. The 4w/13w/26w window is the card's own view state, so nothing
    but the ticker is a prop. Mirrored in componentRegistry.test.ts."""

    def test_registered_with_ticker_only(self):
        self.assertEqual(BACKEND_COMPONENT_REGISTRY["sentiment_trend"], {"ticker": str})

    def test_valid_ticker_accepted(self):
        ok, reason = validate_directive("sentiment_trend", {"ticker": "WDC"})
        self.assertTrue(ok, reason)

    def test_window_props_rejected(self):
        for extra in ({"weeks": 26}, {"days": 91}, {"bucket": "week"}):
            ok, _ = validate_directive("sentiment_trend", {"ticker": "WDC", **extra})
            self.assertFalse(ok, extra)


class TestWatchlistFundamentalsDirective(unittest.TestCase):
    """Issue #147 Part C. The watchlist is one global list (#83), so the card
    takes no props at all — not a ticker, and not an owner."""

    def test_registered_with_no_props(self):
        self.assertEqual(BACKEND_COMPONENT_REGISTRY["watchlist_fundamentals"], {})

    def test_empty_props_accepted(self):
        ok, reason = validate_directive("watchlist_fundamentals", {})
        self.assertTrue(ok, reason)

    def test_ticker_prop_rejected(self):
        ok, reason = validate_directive("watchlist_fundamentals", {"ticker": "INTC"})
        self.assertFalse(ok)
        self.assertIn("ticker", reason)

    def test_owner_prop_rejected(self):
        ok, reason = validate_directive("watchlist_fundamentals", {"owner": "someone_else"})
        self.assertFalse(ok)
        self.assertIn("owner", reason)


class TestFundamentalsDirectives(unittest.TestCase):
    """Issue #147 Part D. Both cards cover the whole tracked universe — the
    shared watchlist plus every owner's positions — so there is neither a
    symbol nor an owner for the model to name."""

    NAMES = ("fundamentals_top", "fundamentals_score_changes")

    def test_registered_with_no_props(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                self.assertEqual(BACKEND_COMPONENT_REGISTRY[name], {})

    def test_empty_props_accepted(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                ok, reason = validate_directive(name, {})
                self.assertTrue(ok, reason)

    def test_ticker_prop_rejected(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                ok, reason = validate_directive(name, {"ticker": "INTC"})
                self.assertFalse(ok)
                self.assertIn("ticker", reason)

    def test_owner_prop_rejected(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                ok, reason = validate_directive(name, {"owner": "someone_else"})
                self.assertFalse(ok)
                self.assertIn("owner", reason)


if __name__ == "__main__":
    unittest.main()
