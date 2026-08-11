"""Composition-root tests — the wiring itself, not any one service.

Everything here is about ``get_services()`` succeeding and handing back
instances that can actually answer. The registry has no logic to test, which is
the point: what can break in it is a cycle, a stale local, or a dependency that
silently arrives as ``None``, and none of those show up in a service's own unit
tests.
"""

import unittest

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.services.registry import get_services  # noqa: E402


class TrackedSymbolsWiringTest(unittest.TestCase):
    """Issue #147 Part B4.

    ``FundamentalsService`` receives the tracked roster as a *callable* closing
    over locals bound further down ``get_services()`` — the only way to give it
    the watchlist and portfolio services when ``WatchlistService`` already
    composes it (Part B3). Late binding is what makes that legal, and late
    binding is also what would let a rename or a re-order fail silently until a
    request arrived. So call it.
    """

    def setUp(self):
        self.services = get_services()

    def test_the_provider_is_wired(self):
        self.assertIsNotNone(self.services.fundamentals._tracked_symbols_provider)

    def test_the_roster_resolves_to_the_live_services(self):
        roster = self.services.fundamentals._tracked_symbols_provider()
        self.assertIsInstance(roster, list)
        # The union of two live reads. Either half may legitimately be empty
        # (prod's positions table deliberately is), so assert the composition,
        # not a count.
        self.assertEqual(
            sorted(roster),
            sorted(self.services.watchlist.symbols() + self.services.portfolio.all_symbols()),
        )

    def test_scope_tracked_runs_end_to_end(self):
        # Reaches the repository and the roster in one call; a broken closure
        # raises here rather than in production.
        out = self.services.fundamentals.get_top_fundamental_stocks(n=5, scope="tracked")
        self.assertEqual(out["scope"], "tracked")
        self.assertLessEqual(len(out["rankings"]), 5)

    def test_the_registry_hands_back_the_same_instances_it_closed_over(self):
        # The hoisted locals and the dataclass fields must be one object each —
        # a second construction would give the roster a different view of the
        # world than the REST tier serves from.
        again = get_services()
        self.assertIs(again.watchlist, self.services.watchlist)
        self.assertIs(again.portfolio, self.services.portfolio)
        self.assertIs(again.fundamentals, self.services.fundamentals)


if __name__ == "__main__":
    unittest.main()
