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
