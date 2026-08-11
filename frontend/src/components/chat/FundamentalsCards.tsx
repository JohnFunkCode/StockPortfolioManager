/**
 * Sidekick cards for the two fundamentals panels worth pulling into a chat rail
 * (issue #147 Part D): the ranking, and what moved.
 *
 * These are wrappers, not copies. The panels already carry a `variant="rail"`
 * rendering, so the card and the /fundamentals page share one component, one
 * hook and one react-query key — rendering a card next to the open page costs
 * no extra fetch, and a change to how a score is painted lands in both.
 *
 * Neither takes props: both cover the whole tracked universe (the shared
 * watchlist plus every owner's positions), so there is no symbol and no owner
 * for the model to name — the same reason `watchlist_fundamentals` has none.
 */
import TopFundamentalsPanel from '../fundamentals/TopFundamentalsPanel';
import ScoreChangesPanel from '../fundamentals/ScoreChangesPanel';

export function FundamentalsTopCard() {
  return <TopFundamentalsPanel variant="rail" />;
}

export function FundamentalsScoreChangesCard() {
  return <ScoreChangesPanel variant="rail" />;
}
