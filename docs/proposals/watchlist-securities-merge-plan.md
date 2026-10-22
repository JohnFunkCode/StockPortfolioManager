# Proposal: merge the Securities page into the Watchlist page (#224)

**Status:** agreed 2026-10-21, in implementation · **Issue:** [#224](https://github.com/JohnFunkCode/StockPortfolioManager/issues/224)
**Sequencing:** lands **before** #217 (stars) and #219 (Evaluate Purchase), which both build on the merged page.

## The problem

QuantUI has two research lists that overlap heavily:

| | **Securities** (`/securities`) | **Watchlist** (`/watchlist`) |
|---|---|---|
| Rows | your positions **plus** the shared watchlist | the shared watchlist only |
| Columns | Source, Cost Basis, Qty, News sentiment, tags | returns (5d/30d/60d/YTD/1y), fundamentals score, sector, cap, editable tags |
| Tools | screener presets, sentiment dashboard, "refresh options snapshots" | — |
| Backed by | `GET /api/securities` | `GET /api/watchlist/fundamentals` |

You have to visit both pages to answer one question: "what am I watching or holding, and how does it
look?" `GET /api/securities` also has a latent bug. It merges positions into a dict keyed by symbol,
so a holding with several lots shows only the **last** lot's quantity and cost.

## What we've decided

1. **The portfolio is a subset of the watchlist.** Every held symbol must already be on the shared
   watchlist, so the merged page is the watchlist with your holdings marked; there are no
   held-only rows. (This replaces the earlier "held-only symbols appear" decision; see the team's
   answers below.)
2. A **new endpoint** marks, on every row, whether the symbol is "in my portfolio".
3. The Securities page and `GET /api/securities` are **retired**. The list comes from the watchlist
   side.
4. The screener, sentiment dashboard and refresh button **move** to the Watchlist page.
5. This work lands before #217 and #219.
6. The fundamentals **score** stays in the list. It remains sortable and filterable and is still
   the default sort (descending).

## The proposal

One page at `/watchlist` that shows the shared list with your own holdings marked. A held row
carries a **Held** chip plus its quantity and average price, so shared and personal stay obvious at
a glance.

### Backend

- **New route `GET /api/watchlist/mine`**, owner-scoped (`require_owner`) and one service call deep
  like every other route. It returns the same row shape as `/watchlist/fundamentals`, plus:
  - `held` (bool)
  - `quantity` (summed across open lots) and `avg_price` (quantity-weighted purchase price across
    open lots); both `null` on an unheld row
  - `held_count` in the envelope
- **New service method `WatchlistService.owner_view(owner=...)`.** It reads the shared watchlist and
  the owner's open lots, marks the held rows, and reuses the existing bulk returns and
  fundamentals reads. That is a **fixed seven queries with zero network calls**, whatever the list
  size, which keeps the six-query, no-Yahoo shape the current watchlist endpoint is guarded for.
- **Lot roll-up math** goes in a new pure function, `position_rollup(lots)`, in
  `quantcore/analytics/portfolio_math.py` (displayed math lives in analytics, never in the front
  end). This also fixes the multi-lot bug described above.
- **Wiring:** the registry hands `WatchlistService` the `PortfolioRepository`, not
  `PortfolioService`. That avoids a service-to-service edge and keeps the construction graph
  acyclic, the same pattern the Harvester/Portfolio pair uses. Owner isolation stays in SQL
  (`list_positions(owner)` already carries the predicate).
- **Route order:** the new route is declared next to `/watchlist/fundamentals`, ahead of
  `/watchlist/{ticker}`, and the route-order test is extended to cover it.
- **Retired:** `GET /api/securities` and its response model. **Kept:** `/api/securities/lookup`
  and every `/api/securities/{ticker}/*` route, because the detail page and the Add Security dialog
  depend on them. `/api/watchlist/fundamentals` is also kept, since it is a documented route and
  removing it is a separate decision.

### Frontend

- `WatchlistPage` switches its data source to `/watchlist/mine` and gains:
  - a **Held** chip on rows you own, styled so shared and personal are obvious at a glance
  - **Qty**, **Avg Price** and **News** sentiment columns
  - the **screener** presets and panel, the **sentiment dashboard** and the **refresh** button, all
    moved from Securities
  - a **Show** filter (All / Held) and the tag filter
- **Unchanged:** Score is the default sort. Native Cap stays unsortable, and you sort on the USD
  cap instead.
- The moved pieces become their own components under `components/watchlist/` (for example
  `ScreenerPanel`, `SentimentDashboard`) so the page doesn't become one 1,000-line file.
- **Retiring the page:** `/securities` becomes a `nav: false` **redirect** to `/watchlist`, so old
  links and bookmarks keep working instead of returning 404. `SecuritiesPage` and its test are
  deleted, and the nav bar loses the Securities button.
- **The detail page stays at `/securities/:symbol`.** It sits on the `/api/securities/{ticker}/*`
  routes we're keeping. Renaming it would touch seven
  inbound links for no functional gain. Its Back button changes to return to `/watchlist`.

## Team answers (2026-10-21)

1. **Endpoint name:** keep `GET /api/watchlist/mine`.
2. **Detail URL:** keep `/securities/:symbol`.
3. **`/api/watchlist/fundamentals`:** keep it.
4. **Held-only rows:** there are none. Stocks in the portfolio must already be in the watchlist, so
   the merged page needs no "Add to watchlist" promotion action.
5. **Column:** "Avg Price", the quantity-weighted average purchase price over the open lots.

**What the subset rule means for the code.** The view is built from the watchlist, so a held symbol
that is somehow *not* on it (data that predates the rule) is left off the page and logged as a
warning naming the symbol, rather than silently shown or silently dropped. This PR does not add
write-side enforcement (refusing a lot add for an unwatched symbol, or refusing to remove a held
symbol from the watchlist); that changes the Add Lot and remove flows and is left for a follow-up.

## Testing

- **Python:**
  - `owner_view` cases: held flags, multi-lot roll-up, a held symbol off the watchlist being
    excluded and logged, and another owner's lots being invisible.
  - A seven-query, zero-yfinance guard, modelled on the existing six-query one.
  - `position_rollup` unit tests.
  - API smoke tests for the new route and for `/api/securities` being gone.
- **Vitest:**
  - Merged page: loading, error and success states; Held chips, Qty and Avg Price; the Show
    filter; the default Score sort.
  - The screener, sentiment and refresh cases carried over from the deleted Securities test.
  - Tests for the split-out components.
  - The `/securities` → `/watchlist` redirect, nav-bar membership, and the detail page's Back
    target.
- Coverage thresholds only move up.

## Docs (same PR)

- `readme.md`: the Pages table, the routers table and the drill-down note
- `docs/architecture/quantui.md`: the page list and the merge rationale
- `docs/architecture/data.md`: the watchlist routes and the shared-vs-owner split
- `CLAUDE.md`: the watchlist rules (route-order rule and the fixed-query shape of `owner_view`)
- `docs/openapi-surface.txt`: drop the retired route and add the new one

## Rollout and risk

- No schema change: every column needed already exists.
- It ships through the normal path. A merge to main deploys to test; prod is a manual
  `prod-rollout.yml` dispatch.
- **Risk:** anything outside the UI that calls `GET /api/securities` breaks. The repo has no such
  caller (no MCP tool or script uses it), but anyone with a personal script against it should say
  so.
- **Risk:** the merged page is denser. Splitting the moved panels into components and adding the
  Show filter are the mitigations, and the team should look at it on test before prod.

## Checkpoint log

_Append an entry as each step lands._

- **2026-10-21 — implemented on `issue-224-merge-securities-into-watchlist`.** Backend:
  `position_rollup`, `WatchlistService.owner_view` (seven queries, zero network, guarded),
  `GET /api/watchlist/mine` declared before `/watchlist/{ticker}`, `GET /api/securities` removed,
  OpenAPI surface updated. Frontend: Watchlist page reads `/watchlist/mine`, adds the Held chip,
  Qty, Avg Price, News and the All / Held Show filter; the screener, sentiment dashboard and
  options refresh moved into three components with their own tests; `/securities` redirects;
  `SecuritiesPage` deleted. 635 vitest tests green with thresholds held; `tsc` clean.
- **Gotchas.** The local unit-test Postgres on :5432 was down, so the backend suite ran against
  Cloud SQL test through the proxy (`QUANTCORE_UNITTEST_DB=cloudsql`, ~17 min).
  `scripts/check_openapi_snapshot.py` only finds the database when run through
  `scripts/with-test-db.sh`. The sentiment type is `BulkSentimentResponse`, not a
  `SentimentSummaryResponse` — the old page named it inline.
- **Follow-up, not in this PR:** nothing yet *enforces* "portfolio ⊆ watchlist" on writes; a lot
  added for an unlisted symbol is only logged and hidden here.
- **Review follow-up (PR #397).** Both reviews flagged complexity in `WatchlistService`:
  `_build_rows` (radon D/24) and `owner_view` (C/11). Split them into module helpers
  (`_row_for`, `_score_columns`, `_price_columns`, `_freshness`, `_rank_key`, `_lots_by_symbol`,
  `_warn_unwatched`, `_mark_holdings`). Both are now B/6 and A, and no new function scores above
  B/10. Query counts, row key order and behaviour are unchanged. `held` is documented as
  "has an OPEN lot record": it stays true when no lot has usable figures, which leaves the
  quantity and average price `None`. A test pins that.
