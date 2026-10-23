# Stop-loss card on Security Detail (#399)

**Status:** PR 1 (#417) and PR 2 (#418) merged; PR 3 (the UI) in review. Issue [#399](https://github.com/JohnFunkCode/StockPortfolioManager/issues/399).
Related: #221 (store stops and alert on them, deferred), #416 (Support Confluence Sidekick-renderable),
#69 / #219 (trade recommendation).

## Why

`get_stop_loss_analysis` combines seven sub-analyses into concrete stop prices. It is reachable over
MCP and REST, but the UI never shows it. The Signals tab's *Risk & Stop-Loss Calibration* panel
shows only one of its inputs, the drawdown-calibrated trailing **width**, not a stop price.

Before this work, the only way to reuse a computed result was to call the whole method again.
Each analysis method fetched its own inputs and computed in the same body, and options data
(`get_delta_adjusted_oi`, `get_gex_profile`) is fetched live from Yahoo on every call. A stop that
uses confluence, plus the Support Confluence panel next to it, would have fetched the same option
chains three times per page view. Even today `get_stop_loss_analysis` and `get_support_confluence`
each fetch the gamma wall separately.

## Decisions (2026-10-22)

- **One new Sidekick-renderable stop-loss card** on the Technical Analysis tab, above Support Confluence.
  - The Signals tab's *Risk & Stop-Loss Calibration* panel is **retired**, not moved; its data folds into the card.
  - The card shows three stops side by side: **technical** (exists), **trailing** (exists) and a new **confluence stop**.
  - A **price ladder** replays the worst 1-day, worst intraday and worst 5-day drops, plus the recent 30-bar drop, from today's price. It shows which stop each drop would have hit.
  - No gamma-wall marker for now.
- **Confluence stop:** the nearest support zone below price where at least 2 independent methods agree. The stop sits under the zone's low, with a buffer.
  - If the stop falls inside the one-day noise floor, **step down** to the next qualifying zone.
  - It is computed from **all** zones, not the top 5 (see gotchas).
- **Confluence is opt-in** on `/stop-loss` and the MCP tool: `confluence=none|stop|full`, default `none`. Today's callers keep today's latency and output; the page asks for `full`.
- **Position comes from the owner's open lots, server-side** (`position=mine`). Working out average cost from lots in the browser would be displayed maths (arch-v2 Rules 8–9).
- **The Support Confluence table marks the zone the stop sits under.**
- **Double computation is fixed by structure, not a cache:** gather the inputs once, then run the pure computations on them.
- **#221 is deferred** until a decision step chooses which stop method to store.

## Plan

### PR 1: refactor, no behaviour change

1. Characterization tests first, committed before any code moved. `tests/test_stop_loss_golden.py` freezes full output dicts as JSON under `tests/golden/recommendations/` for:
   - `get_stop_loss_analysis`, six scenarios;
   - `get_support_confluence`, all sources, and with options failed;
   - `get_trade_recommendation`.
2. `quantcore/analytics/levels.py`: weights, level validation, clustering, zone scoring, ranking and interpretation.
3. `quantcore/analytics/stops.py`: squeeze potential, candidate supports, technical stop, trailing calibration, distances, position P&L, flags and summary.
4. The service only gathers.
   - `_gather_stop_inputs` returns a frozen `_StopInputs`.
   - `_gather_confluence_levels` runs 14 `_src_*` methods, each adding through a `_LevelContext`.
   - The public methods are gather → compute → assemble, still sequential.

### PR 2: backend additions

1. **Parallel gather.** Use the `get_technical_signals` pool pattern, with `close_thread_caches()` per worker in a `finally` (#338). The gamma wall is fetched once and shared.
2. **`confluence_stop`** with the rule above, or `null` plus a reason. The technical and trailing stops always return.
3. **`drawdown_replay`**: for each drop, `{label, drop_pct, price, hits_technical, hits_trailing, hits_confluence}`. Every verdict is computed server-side.
4. **`confluence=none|stop|full`.** With `full`, the full confluence result is embedded, the zone under the stop carries `is_stop_zone`, and a zone outside the top 5 is appended with `outside_top`.
5. **`position=mine`**: owner-scoped open lots through `PortfolioRepository`, rolled up by `portfolio_math`. An explicit `cost_basis`/`shares` still wins.
6. **`get_trade_recommendation`** keeps `confluence="none"`, so its golden output is unchanged.
7. **Measure** `/support-confluence`, `/stop-loss` and `/stop-loss?confluence=full` on test, for a liquid name and a thin-options name. This decides whether the card loads with the page or on demand.

### PR 3: UI

1. `StopLossCard`, following the agreed mock-up.
2. Sidekick `stop_loss: {ticker}` registered on both sides, with parity tests.
3. On the Technical Analysis tab, the card sits above Support Confluence.
   - One `confluence=full&position=mine` request seeds the confluence panel's query.
   - The confluence panel fetches on its own only if that request fails.
4. The confluence table marks the stop's zone.
5. Retire the Signals-tab Risk panel. Keep `useRiskSignals` for the summary line.
6. Docs: the capabilities matrix (counts 56→57, 55→54, gaps 34→33), `docs/architecture/quantui.md` and the readme.
7. Added while building it: `position.pnl_at_confluence_stop` (`stops.pnl_at_stop`), because the card shows P&L at each of the three stops and the maths belongs server-side. It is the PR's only backend change, and it appears only with a confluence stop and a position.

### Issue housekeeping

- **#399:** a comment with these decisions.
- **#221:** add a "choose the stop method" step.
  - Criteria, across the watchlist: which stop stays outside the noise floor, and which `drawdown_replay` hits least. Backtest if the result is close.
  - Fix its claims that the analysis uses volume profile and an ATR trailing stop; it uses neither.
- **#416:** linked.

## Latency (PR 2 step 7, gate for PR 3)

Measured on test on 2026-10-23, after #418 deployed (`8061654`). Three sequential rounds per symbol followed one untimed warm-up call. Every call returned 200. NVDA has liquid options; CRVL's are very thin.

| Symbol | `/support-confluence` | `/stop-loss` | `/stop-loss?confluence=full` |
|---|---|---|---|
| NVDA | 3.93 / 3.87 / 3.55 s (mean 3.78) | 2.60 / 2.28 / 2.47 s (mean 2.45) | 4.06 / 4.43 / 4.26 s (mean 4.25) |
| CRVL | 3.76 / 3.54 / 2.99 s (mean 3.43) | 2.17 / 2.86 / 2.38 s (mean 2.47) | 4.28 / 4.19 / 3.67 s (mean 4.05) |

**Decision: the card loads with the page.**

- One `confluence=full` request costs about 0.5–0.6 s (13–18%) more than `/support-confluence` alone.
- It replaces two requests, the separate confluence and stop calls, which take about 3.8 s even when run in parallel and about 6.2 s run back to back.
- Thin options didn't make it slower: CRVL was slightly faster than NVDA.

The 4 s is mostly the confluence gather itself, since `/support-confluence` alone takes 3.4–3.8 s. Both panels therefore keep loading states.

The script was a scratch `curl -w %{time_total}` loop with a one-hour test JWT. These are timings from one client in one sequence, not a load test.

## Gotchas

- **`support_zones` is cut to `max_zones` (default 5) by score, not by distance.** The nearest qualifying zone, which is the one a stop should sit under, can fall outside the list. The confluence stop must use every zone, and the table must still show the stop zone.
- **complexipy counts nested functions in their parent's cognitive score.**
  - On main, `get_support_confluence` scored 66 (radon CC 20), mostly from its 14 nested `_src_*` closures.
  - After the first pass, `_gather_confluence_levels` still scored 37 because the closures were still nested.
  - Lifting them to methods that take a `_LevelContext` brought it to 5.
  - The `add` callback is deliberate: a source that fails part-way keeps the levels it already added, as before. `test_source_failing_part_way_keeps_levels_it_already_added` passes on both the old and new code.
- **`get_trade_recommendation` calls `get_stop_loss_analysis` internally** (`recommendations.py`, the stop block). Any change to the stop changes the trade recommendation's stop, and its latency, which is why confluence is opt-in.
- **A parallel gather must merge in a fixed order.** Each confluence source collects into its own list and `_assemble_levels` concatenates them in source order. Appending to one shared list from the pool would make the level order, and with it tie-breaks in zone contributors, depend on thread timing. The golden files caught nothing only because the merge is ordered.
- **The test fakes had no `close_thread_caches`.** The pool calls it in each worker's `finally`, so every stub `yfinance_gateway` needs `close_thread_caches=None`. Without it every pooled task failed with an `AttributeError` raised from the `finally`, which looked like every confluence source failing.
- **`position=mine` resolves the owner only when asked.** The route depends on `require_principal` and calls `require_owner` only for `position=mine`. Depending on `require_owner` for the whole route would 403 an unprovisioned caller who never asked about a position.
- **The OpenAPI surface snapshot lists method, path and operation id only**, so the new query parameters don't change `docs/openapi-surface.txt`. (Regenerating it locally also needs a database: `api.main` runs `ensure_schema()` at import.)
- **`get_historical_drawdown`'s fields are signed worst returns, not loss magnitudes.** Each is the *minimum* return over its window, so a window with only gains reports a positive number. The first replay took `abs()` of each field, copying the stop calibration above it, and turned a +2% worst day into a 2% loss that "hit" the technical stop (Guppy on #418, reproduced through the real producer). The replay now takes `max(0, −return)`, so a gain replays as 0% at today's price. The calibration's own `abs()` is older behaviour and is left alone here. Every fixture drawdown was negative, so 100% changed-line coverage never reached this case; the regression tests feed the replay from the real `get_historical_drawdown`.
- **Only the gamma wall is shared between the two gathers.** `get_vwap` is still called once by the stop inputs and once by the rolling-VWAP source. Sharing more would mean threading a memo through `_LevelContext` for every input; the gamma wall is the one that pulls option chains from Yahoo.
- **A disabled react-query query with no data reports `isLoading: false`.** In v5, `isLoading` is `isPending && isFetching`, and a disabled query isn't fetching, so the confluence card, held off while the stop-loss request runs, rendered nothing instead of a spinner. It now treats "disabled and no data" as loading.
- **The vitest client's `gcTime: 0` drops a seeded entry nobody observes.** `setQueryData` on `['support-confluence', ticker]` with no mounted observer is collected at once under the test client, so the seeding test builds its own client with the default `gcTime`. On the page the confluence card observes the key (disabled), so the seeded entry survives.
- **Not touched, still over the limits:** `get_trade_recommendation` (CC 145, cognitive 177), `get_relative_strength` and `get_relative_strength_history`.

## Checkpoint log

| Step | Commit | Result | Gotcha |
|---|---|---|---|
| Golden characterization tests | `e546a73` | 4 tests, 9 frozen outputs | Every first-draft stop scenario tripped the noise-floor flag, so an outside-the-floor scenario was added |
| Review follow-up (Guppy on #417) | `7a09782` | 3 Fibonacci-source tests through the real `find_swings`: up leg, down leg, and no usable swing (`hi <= lo` and too few bars). They pass on main's code and the refactor | The all-sources golden fixture has no confirmed 5-bar swings, so it never reached the retracement path |
| Analytics extraction + gather/compute split | `835addb` | Golden files unchanged. `get_stop_loss_analysis` went from CC 40 / cognitive 42 to 3 / 2, `get_support_confluence` from 20 / 66 to 4 / 3. Every touched unit is under 15 / 25 | Nested closures count toward the parent (above) |
| PR 2: confluence stop, drawdown replay, `confluence` and `position=mine` | `8bb8b05` | Stop and confluence gathers share one pool of 6 workers, with the gamma wall fetched once (a test counts the calls). The trade-rec and confluence golden files are unchanged; the six stop files gained `drawdown_replay` and `position_source` and nothing else. Every new unit is at CC ≤ 10 and cognitive ≤ 10 | Fakes needed `close_thread_caches` (above) |
| Review follow-up (Guppy on #418) | `a44e93c` | The replay treats a positive worst return as no drop. Tests cover it through the real `get_historical_drawdown`, with a rally after a selloff and an all-gain history. Golden files unchanged | `abs()` of a signed minimum (above) |
| Latency on test (PR 2 step 7) | *(PR 3)* | `confluence=full` takes 4.05–4.25 s on average, against 3.4–3.8 s for `/support-confluence` alone, so the card loads with the page (Latency, above) | None |
