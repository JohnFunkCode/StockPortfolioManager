# Retire the legacy HTML report into QuantUI

**Source issue:** [#147](https://github.com/JohnFunkCode/StockPortfolioManager/issues/147)
**Status:** COMPLETE (2026-08-11) — all nine PRs (0–8) merged to `main` and issue #147 closed; see
the [Checkpoint log](#checkpoint-log). Prod is migrated through **`V8`** and verified: 22 tables,
0 missing / 0 extra against `db/schema_snapshot.json`,
`ux_one_active_plan_per_owner_symbol` present with the old per-symbol index gone, and zero
violations of the H5 invariant (the backfill closed **0 rows** — prod holds no plans, so the row
belongs in the changelog rather than doing work). **One deliberately manual step remains:** a
`prod-rollout.yml` dispatch to promote Part G's UI to prod, which still runs `e17d21c`. **Two
things stay unproven until they happen on their own schedule** — see
[Verification](#verification): the first prod execution of the post-#147 `main.py`, and which code
path the Raspberry Pi is actually running
**Shape:** nine PRs (0–8) across three tracks that different people can work in parallel; see
[Sequencing](#sequencing) and [Working alongside other people](#working-alongside-other-people)
**Related:** [`watchlist-db-plan.md`](watchlist-db-plan.md) (#83, the DB-backed watchlist this plan
reads), [`portfolio-lots-plan.md`](portfolio-lots-plan.md) (#126, the owner-scoping and principal
plumbing Part H reuses), [`architectural-standard-v2.md`](architectural-standard-v2.md) (the rules
every part below is written against), [issue #165](https://github.com/JohnFunkCode/StockPortfolioManager/issues/165)
(two owners of the schema — Part H1 escalates it)

## How to use this plan

Nine PRs, executed in the order given in [Sequencing](#sequencing) — which is deliberately *not*
feature order: conflict-free and seam-clearing work lands first. Each PR is independently shippable,
independently revertable, and leaves the system in a whole state. **Keep at most two in flight**
(mitigation 6). Append a row to the [Checkpoint log](#checkpoint-log) as each one lands, not at the
end.

Line numbers were captured on 2026-07-29 against `docs/seven-mcp-wrappers`, since merged to `main`
as [#167](https://github.com/JohnFunkCode/StockPortfolioManager/pull/167) (`0b5409d`); if one has
drifted, search for the quoted code.

---

## Context

`main.py` still carries the project's original artifact: a static HTML report built for a
Raspberry Pi, with a portfolio summary, a holdings table, two matplotlib charts, and a watchlist
table. Since the move to GCP the Cloud Run Job has never had `BUCKET_NAME`/`BUCKET_KEY` or AWS
credentials, and `save_html_to_s3()` returns `None` **silently** when they are absent — so the
deployed job has been generating a report nobody can read. The page still live at
`www.johnfunk.com/portfolio/portfolio_report.html` is published by the Pi running the old code, not
by the deployed job.

The information has diverged in four directions:

- **Portfolio Summary** and **Individual Stock Holdings** are superseded by the QuantUI Portfolio
  page. **The pie chart** is dead.
- **Stock Purchases vs Current Value (stacked bar)** has no QuantUI equivalent — the Portfolio page
  is currently 100% tables and text, zero charts. It should move.
- **The watchlist table** was superseded and improved by
  `scripts/generate_watchlist_fundamentals_report.py` (24 columns: returns, composite fundamental
  score and its seven sub-scores, sector, market cap, revenue trajectory, earnings date, EPS
  acceleration). It has no home in QuantUI — there is no `/watchlist` route; watchlist is only a
  source filter on `/securities`.
- Separately, **universe-wide fundamentals is a total gap.** Five endpoints exist and the UI calls
  **none** of them: `/api/securities/fundamentals/{top,sector-breakdown,score-changes,upcoming-earnings,cache-stats}`.
  Every fundamentals view in QuantUI today is per-ticker on the security detail page.

### Why the "it takes several minutes" blocker doesn't apply

That is a *refresh* cost, not a *read* cost:

- Prod `fundamentals_history` coverage is essentially complete — 237 symbols for
  `fundamental_score`, 232 each for `earnings_calendar` / `earnings_acceleration` /
  `revenue_growth`, against a 215-symbol watchlist.
- But it is **stale**: newest `fundamental_score` 2026-07-27, the other three 2026-07-21, against a
  24h `FUNDAMENTALS_CACHE_TTL_HOURS`. Nothing warms it on a schedule — `main.py` makes zero
  fundamentals calls, and the report script is the de-facto manual warmer.
- Because the cache is stale, `get_full_fundamental_profile` refetches: ~10 yfinance round-trips per
  symbol, 215 symbols, strictly serial, a fresh psycopg2 connection per call.

Reading in bulk is a different animal. `FundamentalsRepository.get_all_latest()` returns
latest-per-symbol for the whole universe in one query with no TTL filter, and
`OhlcvRepository.daily_bars_for_symbols()` does the same for daily bars.
`PricesService.screen_securities()` (`prices.py:1950`) is the working precedent for
one-query-then-pandas with zero network. **All five unused fundamentals endpoints are already
`get_all_latest`-backed**, so they are fast today — they simply have no UI.

### Outcome

The stacked bar moves to the Portfolio page. Two new pages split along a clean line:
**`/watchlist` answers "how are my ideas doing?"** (curated roster, performance + quality columns)
and **`/fundamentals` answers "what's good?"** (ranked discovery, sector cuts, score changes over
time). The daily job warms the cache instead of publishing HTML, and the legacy report survives
verbatim as a manually-run script.

Corrections ride along, since the UI is open anyway: `/symbols` collapses into the pages that already
cover it, the top bar regroups into **My Positions / Research / Settings**, and — the gap that
removing `/symbols` exposes — **plans get tied to holdings**, so the Portfolio page can finally answer
"is this position managed?" and `/plans` can flag one that outlived its position.

---

## Part A — Stacked bar chart onto the Portfolio page

No new endpoint. `GET /api/portfolio/symbols` — already `PortfolioPage`'s only read via
`useSymbolRows()` — returns `total_investment`, `total_current_value`, and `total_gain_loss` per
symbol, exactly what the matplotlib chart consumed.

**Backend** (keeps displayed math out of the front end, arch-v2 Rule 8.4):

- `quantcore/analytics/portfolio_math.py` — add `allocation_segments(invested, gain_loss)` returning
  the three scalar bar segments `{bar_base, bar_gain, bar_loss}`, beside the existing
  `summary_totals()` and `period_return()`.
- `quantcore/services/portfolio.py` — `symbol_rows()` merges those three keys into each row. Purely
  additive to the response shape: **no route change, `docs/openapi-surface.txt` untouched.**

**Frontend:**

- New `frontend/src/components/portfolio/PortfolioAllocationChart.tsx` — d3 v7, copying the structure
  of `frontend/src/components/securities/charts/PriceChart.tsx`: single `useEffect`,
  `svg.selectAll('*').remove()`, width from `ref.current.parentElement!.clientWidth`, grid `#374151`
  dasharray `2,4`, gain `#10b981` / loss `#ef4444`, tick text `#9ca3af`, legend `Stack` above a
  `<svg style={{ width: '100%', display: 'block' }} />`. Renders `bar_base` as the orange cost-basis
  block with `bar_gain` stacked green or `bar_loss` stacked red on top, with value labels — the
  matplotlib `ax1` semantics.
- Mounted in `frontend/src/components/portfolio/PortfolioPage.tsx` between `PortfolioSummary` and the
  holdings table, consuming the existing `useSymbolRows()` result. No second fetch.

**GenUI (Rules 8–9)** — the chart displays analytical data, so it must be sidekick-renderable:

- `quantcore/services/chat_tools.py` — add `"portfolio_allocation": {}` to
  `BACKEND_COMPONENT_REGISTRY` and to the `show_component` tool description. No props, same rationale
  as the existing `"portfolio_table": {}`: the card fetches the caller's own portfolio through the
  browser's authenticated session, and an `owner` prop would be a cross-user read.
- `frontend/src/chat/componentRegistry.tsx` — matching entry (`spec: {}`, `titled: false`).
- Tests: co-located `PortfolioAllocationChart.test.tsx` (loading / error / success / key values) plus
  registry-parity cases on both sides.

---

## Part B — Shared backend for both new pages

### B1. Analytics (pure functions, no I/O)

**Not new math — one correct copy replacing three inconsistent ones.** These metrics already exist
in `portfolio/metrics.py:132 get_historical_metrics()` (5d/30d/90d/YTD/1y off a
`yf.download(period="2y")` frame) and again as `pct_return()` in
`scripts/generate_watchlist_fundamentals_report.py`. Three defects in the existing code are the
reason to consolidate rather than reuse in place:

- `metrics.py:165-167` compute **open-to-close** (`Open.iloc[-6]` → `Close.iloc[-1]`); the report
  script's `pct_return` is close-to-close. Two conventions coexist today.
- `metrics.py:175` anchors YTD by counting business days from Jan 1 and indexing positionally.
  `bdate_range` counts market holidays as business days, so the anchor drifts off the actual first
  trading bar of the year.
- `metrics.py:176` takes `iloc[0]` of a **two-year** frame and labels the result
  `one_year_return`. It is a two-year return.

New `quantcore/analytics/returns.py`, close-to-close throughout:

- `trailing_return(closes, days)` — delegates the percentage to the existing
  `portfolio_math.period_return()`; do not reimplement it. Matches the report script's `pct_return`
  convention, which is the correct one.
- `ytd_return(bars, as_of)` — calendar-aware, anchored to the first close on/after Jan 1 (fixes the
  holiday drift).
- `one_year_return(bars, as_of)` — 365-calendar-day lookback with the same nearest-available-bar
  rule (fixes the two-year bug).

`portfolio/metrics.py` is left alone — it is the legacy domain layer feeding the preserved report
script (Part F), and rewiring it is out of scope. Note the divergence in a comment there pointing at
`returns.py` as the current implementation.

**Why not yfinance's own return fields.** Checked against yfinance 0.2.61 live: an equity's
`Ticker.info` carries only `52WeekChange` / `fiftyTwoWeekChangePercent` — **one** of the five
columns. YTD (`ytdReturn`) and 90d (`trailingThreeMonthReturns`) appear **only on funds/ETFs**;
there are no 5d/30d/60d fields at any security type. Worse, all of them live on `Ticker.info`, a
per-symbol network call — 215 of them, serial, to populate one column that a single
`daily_bars_for_symbols()` query already yields for free. Yahoo's conventions are also undocumented
(adjustment basis, anchor date), so a Yahoo-sourced 1y column would not be comparable to the
OHLCV-sourced 60d column beside it. Compute all five from the cache, one convention, zero network.

Also add `normalize_market_cap(value, currency, fx_rate)` to `quantcore/analytics/`. The current
`fmt_market_cap` in the report script does not FX-normalize, which is why SK hynix renders
`1456.62T`. That bug becomes visible in both new pages, so it is in scope.

### B2. Bulk returns on `PricesService`

`quantcore/services/prices.py` — add `bulk_period_returns(symbols, periods=(5, 30, 60))` returning
5d/30d/60d/YTD/1y per symbol. **One** `OhlcvRepository.daily_bars_for_symbols(symbols)` call, then
pandas, then the `returns.py` helpers. Zero yfinance calls.

Model it on `screen_securities()` (`prices.py:1950`) — **not** on `PortfolioService._period_returns()`
(`portfolio.py:217`), which is a per-symbol `get_history` loop and would reintroduce the slowness.

### B3. `WatchlistService.returns_and_fundamentals()`

`quantcore/services/watchlist.py` is bare CRUD today (`list_entries`, `count`, `add_entry`,
`remove_entry`, `import_yaml`). Add a method returning one row per watchlist entry with the 24 report
columns, reading:

- `self._repo.list_entries()` — symbols, names, currency, tags
- `self._prices.bulk_period_returns(symbols)` — one query
- `self._fundamentals.get_all_latest(dt)` for `fundamental_score`, `revenue_growth`,
  `earnings_calendar`, `earnings_acceleration` — four queries

Six queries total, no network. Each row carries `fetched_at` and a derived `is_stale` flag using the
bulk-staleness idiom already proven in `FundamentalsService.get_upcoming_earnings` (read
`self._repo.ttl_seconds()` once, compare per entry, count `stale_excluded` / `total_in_cache`). Rows
are shown stale rather than dropped, with the age surfaced.

### B4. Scoping the five fundamentals endpoints to tracked symbols

All five operate on the whole cache today. The `/fundamentals` page is scoped to
**watchlist ∪ portfolio**, and top-N of the cache ≠ top-N of the roster, so the filter must be applied
*before* ranking:

- `quantcore/services/fundamentals.py` — inject `watchlist` + `portfolio` deps, add a private
  `_tracked_symbols()` helper, and give `get_top_fundamental_stocks`, `get_sector_fundamental_breakdown`,
  `get_fundamental_score_changes`, and `get_upcoming_earnings` a `scope: str = "all"` parameter
  (`"all"` | `"tracked"`) applied immediately after `get_all_latest()`.
- `api/routers/fundamentals.py` — thread `scope` through as a query param on those four routes.

**Default `"all"` preserves every existing caller** (MCP tools, tests) byte-for-byte, and because
query params don't appear in `docs/openapi-surface.txt` (it lists `METHOD PATH -> operation_id`),
**this adds zero snapshot churn and zero new routes.**

### B5. The one genuinely new route

`api/routers/portfolio.py` — add `GET /api/watchlist/fundamentals` next to the existing
`GET/POST /api/watchlist` and `DELETE /api/watchlist/{ticker}` (`portfolio.py:245-278`). Exactly one
service call deep (Rule 6). Declare it **before** any `/{ticker}`-style route so the literal path
wins. Pydantic response schema under `api/schemas/`.

**Regenerate `docs/openapi-surface.txt`** (106 → 107 lines) or `scripts/check_openapi_snapshot.py`
fails CI.

---

## Part C — `/watchlist` page: "how are my ideas doing?"

- `frontend/src/api/watchlist.ts` + `watchlistTypes.ts`, `frontend/src/hooks/useWatchlist.ts` — the
  established three-layer pattern (`api/client.ts` → `api/<domain>.ts` → `hooks/use<Domain>.ts`),
  react-query with `staleTime: 5 * 60 * 1000` to match `useSecurities`.
- `frontend/src/components/watchlist/WatchlistPage.tsx` — MUI X DataGrid mirroring
  `SecuritiesPage.tsx`: tag filter and `sortModel` persisted to `localStorage`
  (`watchlist-tag-filter`, `watchlist-sort-model`); default sort composite score desc then 30d
  return, matching the report's
  `(composite_score is not None, composite_score or -999, return_30d or -999)`; green/red on return
  columns; `T/B/M` market-cap formatting; symbol linking to `/securities/:symbol` (better than the
  report's Yahoo Finance link); header chip with cache age plus per-row staleness indicators.
- **Inline add/remove and tag editing**, reusing the existing mutations in
  `frontend/src/hooks/useSecurities.ts` (`useAddSecurity().watchlist`) and the removal flow from
  `SecurityDetailPage.tsx:686` — including its "the watchlist is shared" confirmation. This finally
  gives list management a home that isn't a dialog buried on another page. Do **not** add a `delete`
  verb to `mcp_gateway/rest_client.py` — removal stays UI-only by design.
- `frontend/src/components/watchlist/WatchlistFundamentalsCard.tsx` — sidekick-renderable variant
  following the four-branch state machine in
  `frontend/src/components/securities/SupportConfluenceCard.tsx` (`isLoading` → transport `error` →
  `data.error` server payload → success → null), zero math in the component.
- `frontend/src/navigation.tsx` — one appended entry (path, label, icon, element). That is the whole
  wiring change, courtesy of the seam PR; `App.tsx` itself is untouched.
- GenUI dual registration: `"watchlist_fundamentals": {}` in `chat_tools.py`
  `BACKEND_COMPONENT_REGISTRY` + the `show_component` description, matching entry in
  `frontend/src/chat/componentRegistry.tsx`.

---

## Part D — `/fundamentals` page: "what's good?"

Pure front-end work on top of B4. Five parallel react-query reads, all cache-backed and sub-second,
all scoped `scope=tracked`:

- **Top by composite score** — `/fundamentals/top?n=25&min_coverage=0.5`, ranked table with label and
  coverage.
- **Sector breakdown** — `/fundamentals/sector-breakdown?top_n=5`, sector cards with their best names.
- **Score changes** — `/fundamentals/score-changes?min_delta=2&since_days=90&direction=both`,
  upgrades and downgrades. This is the view a static table cannot express, and the strongest single
  argument for the split.
- **Upcoming earnings** — `/fundamentals/upcoming-earnings?days=14`, calendar strip.
- **Cache freshness** — `/fundamentals/cache-stats`, a small header banner showing per-`data_type`
  coverage and age, so staleness is visible rather than inferred.

Files: `frontend/src/api/fundamentals.ts` + types, `frontend/src/hooks/useFundamentals.ts`,
`frontend/src/components/fundamentals/FundamentalsPage.tsx` plus one component per panel, route and
nav entry in `App.tsx`. GenUI: register `"fundamentals_top"` (`{}`) and `"fundamentals_score_changes"`
(`{}`) as sidekick cards on both sides — those two are the ones worth pulling into a chat rail.

---

## Part E — Fundamentals cache warmer in `main.py`

Both new pages are only as good as the cache behind them, and today nothing refreshes it.

Add a warming pass to `main.py`'s `__main__`, **after** the existing options-chain capture loop, over
the union of watchlist and portfolio symbols, calling
`get_services().fundamentals.get_full_fundamental_profile(sym)` per symbol inside its own
`try/except` so one bad symbol degrades one row. The TTL check inside the service means fresh symbols
cost nothing.

**Bound the work.** A cold pass over 215 symbols is ~10 serial yfinance calls each and can run tens of
minutes against a Cloud Run Job task timeout. Order symbols **oldest-`fetched_at` first** and stop on
a wall-clock budget (env var, default ~15 min). The universe then fully refreshes over a few nights
and never blocks the notifications or options capture, which run first.

**Isolate it from the rest of the job.** The per-symbol `try/except` is not sufficient on its own —
wrap the whole warming pass in an outer `try/except` that logs and continues, so the job's exit
status still reflects the notifications and options capture that already succeeded. This is the
explicit ask in [#147](https://github.com/JohnFunkCode/StockPortfolioManager/issues/147) ("a failure
in the report path can fail the job and take the *useful* side effects down with it"), and the
warmer is the new long-running step that would otherwise inherit exactly that defect from the
report it replaces. Ordering — notifications, then options capture, then warming — is a deliberate
isolation property, not incidental: the cheap, high-value work completes before anything with a
15-minute budget starts.

**Alarm on it, don't degrade quietly** — same bilge-pump shape as
`alert_if_watchlist_empty(watchlist, portfolio, notifier)`: if post-run coverage of
`fundamental_score` falls below a floor, or the oldest row exceeds an age ceiling, send a Discord
alert. A silent stale cache is exactly the failure mode that put us here. Note that the outer guard
above and this alarm are the same pattern applied at two levels: swallow the exception so the job
survives, then make the resulting degradation loud.

---

## Part F — Retire the report from `main.py`, preserve it as a script

**New `scripts/generate_portfolio_report.py`** — move `create_portfolio_charts()`,
`create_portfolio_html()`, `create_template_file()`, and `save_html_to_s3()` out of `main.py`
**verbatim**, with a `__main__` that runs `init_schema()`, loads John's positions and the shared
watchlist via `get_services().portfolio` / `.watchlist`, fetches prices and metrics, and writes or
uploads the HTML. Keep the pie chart and both retired sections intact — this is the legacy artifact
preserved as-is.

Two flags, because publishing is now a deliberate act rather than a side effect of the daily job:

- `--output PATH` — write the HTML locally, no S3 credentials needed. The default when neither flag
  is given.
- `--publish` — upload to S3 (`www.johnfunk.com/portfolio/portfolio_report.html`). The silent
  `return None` on missing `BUCKET_NAME`/`BUCKET_KEY` is the original defect: with `--publish`
  explicitly requested, absent credentials must **exit non-zero with a clear message**, never
  succeed quietly.

**`runOnPi.sh` is repointed at the new script** rather than `main.py`:

```bash
python ~/Documents/code/StockPortfolioManager/scripts/generate_portfolio_report.py --publish
```

Deliberately the *report script only*, not `main.py` — the Cloud Run Job already sends the Discord
notifications, runs the Harvester rung checks, and captures the options snapshots, so pointing the Pi
at `main.py` would double every alert and write a second snapshot per symbol per day. The Pi becomes
what it originally was: the thing that publishes the public HTML page.

**The Pi needs one-time setup before this works.** Its checkout predates the PostgreSQL move, so it
needs `QUANTCORE_DB_DSN` in `.env` and a Cloud SQL Auth Proxy running alongside it (the same
host:port indirection the local dev setup uses) plus the AWS credentials for the bucket. Note this in
the script's module docstring — it is the non-obvious part, and the Pi is the one environment nobody
touches for months at a time.

**`main.py` keeps** everything that is not the report: `init_schema()`, DB load of positions and
watchlist, `Notifier(portfolio).calculate_and_send_notifications()`, `alert_if_watchlist_empty()`,
the Harvester rung checks, the options-chain capture loop (issue #93 Phases 4/5), and the new
fundamentals warmer. It drops from **703 lines** (#147 says 658 — it has grown since) to roughly a
third of that. What remains is job orchestration, which per arch-v2 could itself become a service;
that is deliberately **not** in scope, and worth saying so on #147 rather than leaving the issue
half-satisfied.

### The dependency split (#147's "Consequences", widened)

Drop the now-unused `matplotlib` / `jinja2` / `boto3` imports from `main.py` (`:7`, `:14`, `:585`).
Then follow them upstream, because #147 understates the blast radius: it blames the dead report for
inflating `Dockerfile.report`, but all three packages live in **`requirements-base.txt`** (`:20`,
`:22`, `:23`) — the lean set that `Dockerfile.api` and `Dockerfile.mcp` install as well. The dead
artifact has been padding **three** deployed images.

All three are report-path-only. The only importers are `main.py` and the two legacy root scripts
`html_summary.py` / `simple_text_summary.py`, neither of which is any container's `CMD`.

- New **`requirements-report.txt`** — `matplotlib`, `boto3`, `jinja2`, with a header saying it exists
  for `scripts/generate_portfolio_report.py` and the Pi.
- Remove those three lines from `requirements-base.txt` and correct its header comment, which
  currently names the report job as one of the consumers it covers.
- `requirements.txt` becomes base + ml + **report**, so `pip install -r requirements.txt` for local
  dev and the Pi is unchanged. Also fix `requirements-base.txt:10-16`, whose numpy justification
  cites "pandas/matplotlib/yfinance all pull it in" — still true via pandas and yfinance, but the
  matplotlib half no longer applies to that file.
- **Verify before rollout, don't assume.** Removing a package from a shared lean image is the one
  change here that can break a running service through a transitive import nobody grepped for. The
  api container must boot and the wrapper smoke test must pass against the rebuilt images — see
  Verification.

**`Dockerfile.report` needs four edits, all of which are documentation the change makes false:**

- The module docstring describes the job as "builds the HTML report … renders the Jinja2 template,
  and (optionally) uploads to S3". Rewrite it to notifications + options capture + fundamentals
  warming.
- `ENV MPLBACKEND=Agg` and `MPLCONFIGDIR=/tmp/matplotlib` become dead — remove both.
- The `chown appuser:appuser /app` comment justifies itself with "main.py writes
  portfolio_report.html into the workdir" and "matplotlib's cache goes to the writable MPLCONFIGDIR
  above". The `chown` itself stays (the job still runs unprivileged); its rationale needs rewriting.
- `CMD ["python", "main.py"]` is unchanged.

**Also fix while there:** the duplicated `get_services` import at
`scripts/generate_watchlist_fundamentals_report.py:24-25`.

**Docs (same PR, per CLAUDE.md):**

- `CLAUDE.md` — rewrite "Report Generation (`main.py`)" to describe notifications + options capture +
  fundamentals warming, not HTML; add `/watchlist` and `/fundamentals` to the QuantUI section; note
  the `WatchlistService` composition, the `scope` parameter on the four fundamentals methods, and
  `quantcore/analytics/returns.py`; list the new script.
- `readme.md` — UI tour gains both pages and the grouped nav; scripts section gains
  `generate_portfolio_report.py` with its two flags and the Pi's DSN/proxy prerequisite; the install
  section notes that `requirements.txt` still installs everything and that a container-lean install
  (`requirements-base.txt`) can no longer run the report script.
- `CLAUDE.md` — the "Key Dependencies" line currently lists matplotlib, jinja2, and boto3 among the
  project's dependencies without qualification; mark them as report-script-only.
- `docs/openapi-surface.txt` — regenerated (one added line).
- **Comment on #147 and close it with the PR**, recording the two things it asked for that this plan
  deliberately does *not* do: the rendered-report archive (see "Decisions already settled") and
  extracting `main.py`'s remaining orchestration into a service. An issue closed with its declined
  options written down is reusable; one closed silently gets reopened from scratch.

---

## Part G — Navigation: collapse `/symbols`, then group the bar

Both nav changes ship together so `App.tsx` churns once.

### G1. Collapse `/symbols` into the pages that already cover it

`frontend/src/components/symbols/SymbolsPage.tsx` is 52 lines rendering four columns:
`ticker`, `name`, `currency` — all three already on `/securities` — and `active_plan_id`, which
links to `/plans/:id`. But `PlansPage.tsx:27` already has a **Symbol** column and row-click to that
same detail page, with status, rungs, and targets alongside. The page duplicates `/securities` three
ways and `/plans` the fourth, with less context than either.

It is not a general symbol registry architecturally, either: `api/routers/symbols.py:16` calls
`services().harvester.list_all_symbols()`, typed by `api/schemas/harvester.py`, whose SQL
(`harvester_repository.py:807`) is a `symbols LEFT JOIN plan_instances` — the Harvester's registry,
promoted to top-level nav.

**Remove:**

- `frontend/src/components/symbols/SymbolsPage.tsx` and its `.test.tsx`
- the `/symbols` entry in `frontend/src/navigation.tsx` — one object, covering both the route and the
  nav button — plus the now-unused `SymbolsPage` import and `ShowChartIcon`
- `symbolsApi.list` (`frontend/src/api/symbols.ts:5`) and the `useSymbols()` list hook, leaving the
  per-ticker price hook

**Keep, deliberately:**

- **`frontend/src/components/symbols/LivePrice.tsx`** — used by `PlanDetailPage.tsx:55` and
  registered as the `live_price` sidekick component in `frontend/src/chat/componentRegistry.tsx:42`.
  Delete the page, not the folder.
- **Both `/api/symbols` routes.** `/api/symbols/{ticker}/price` backs `LivePrice`; dropping either
  route would churn `docs/openapi-surface.txt` and break MCP callers for no gain. This is a
  front-end-only removal.

**The half that's actually missing** — the symbol→plan link — moves to the Portfolio page rather
than the security detail page, because under Part H a plan can only exist for a stock you hold. See
**Part H4**.

**Not carried over:** symbols in the `symbols` table on neither list — OHLCV residue from past
ingests (the fundamentals cache holds 237 symbols against a 215-symbol watchlist, so there is a
tail). That is a quarterly data-hygiene question, not a page. If it ever needs a home, it belongs as
a count in the `/fundamentals` cache-freshness banner, not as nav.

Docs: drop `/symbols` from the `readme.md` UI tour.

### G2. Group the nav

Removing `/symbols` and adding `/watchlist` + `/fundamentals` leaves eight destinations. Group them
rather than keeping a flat row:

- **My Positions** — Portfolio, Plans, Harvester
- **Research** — Securities, Watchlist, Fundamentals, Arbitrage
- **Settings** — stays top-level (it is not research and not a position)

**The nav is a horizontal `AppBar`, not a sidebar.** `App.tsx:32-106` renders a `<Toolbar>` with the
`QuantUI` wordmark and then a `<Stack direction="row" spacing={1}>` of MUI `Button`s built from a
`navItems` array (`:39-47`), with the Sidekick and theme `IconButton`s pushed right via `ml: 'auto'`
(`:109-147`). So "grouped" means **dropdown menus in the top bar**, not collapsible sections.

**Today** — seven flat buttons, each a `Link`, no hierarchy, `Symbols` sitting at the same level as
`Portfolio`:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ QuantUI    Portfolio  Harvester  Securities  Arbitrage  Plans  Symbols  Settings   [💬] [🌗] │
│            ═════════                                                                         │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
             ▲ active item: primary color + 2px bottom border + neon textShadow
```

**After G1 + G2** — three top-level controls, eight destinations behind them. Adding `/watchlist`
and `/fundamentals` to the flat row would have made it nine buttons wide; grouped, the bar gets
*shorter* than it is today:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ QuantUI    My Positions ▾    Research ▾    Settings                                [💬] [🌗] │
│                              ══════════                                                      │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │  (open on /watchlist — group renders active because a
                                    ▼   child matches: items.some(isPathActive))
                              ┌─────────────────────────┐
                              │ 📊  Securities          │
                              │ 👁   Watchlist       ◀──┼── selected MenuItem
                              │ 🏅  Fundamentals        │
                              │ ⇄   Arbitrage           │
                              └─────────────────────────┘

  My Positions ▾                Research ▾                     Settings
  ├── 💼 Portfolio      /       ├── 📊 Securities   /securities  └── ⚙ /settings  (ungrouped —
  ├── 📋 Plans          /plans  ├── 👁  Watchlist    /watchlist       neither a position nor
  └── 🎛 Harvester  /harvester  ├── 🏅 Fundamentals /fundamentals     research; one click, not two)
                                └── ⇄  Arbitrage    /arbitrage

  Not in the bar (nav: false, reached by drill-down):  /securities/:symbol   /plans/:id
  Removed entirely (G1):                               /symbols
```

The icons above are the existing ones from `navItems` (`:39-47`) carried through unchanged as
`ListItemIcon`s — `AccountBalanceWalletIcon`, `ListAltIcon`, `DashboardIcon`, `BarChartIcon`,
`CompareArrowsIcon`, `SettingsIcon` — plus one new icon each for Watchlist and Fundamentals.
`ShowChartIcon` is dropped with `/symbols`.

Implementation notes:

- Add a `group` field to the entries in `frontend/src/navigation.tsx` (from the seam PR) and derive
  the groups from it, with `Settings` left ungrouped. Because the seam PR already made that array the
  single source for both the route list and the button list, this touches one render site rather
  than rewriting two parallel lists.
- Each group renders one `Button` (with `endIcon={<ExpandMoreIcon />}`) that opens an MUI `Menu`
  anchored to it, holding `MenuItem component={Link}` entries — one per child route, keeping the
  existing per-item icons as `ListItemIcon`. Close the menu on selection so the SPA navigation isn't
  left with an open popover.
- **Active-state logic changes.** Today `active` is computed per item at `:71-72`
  (`location.pathname === item.path || (item.path !== '/' && location.pathname.startsWith(item.path))`).
  The group button must light up when **any** child matches — hoist that predicate into a
  `isPathActive(path)` helper and have the group compute `items.some(isPathActive)`. Note the `'/'`
  special case: Portfolio is the index route, so the plain `startsWith` shortcut would make every
  route match it. Keep the existing neon-glow `sx` treatment on the active group button, and apply
  `selected` to the active `MenuItem`.
- Keyboard/a11y comes from MUI for free (`Menu` handles focus trap and arrow keys) provided the
  trigger carries `aria-haspopup="true"` and `aria-expanded`.

Tests: a co-located test on `frontend/src/components/layout/NavBar.tsx` (extract the toolbar here —
it now holds menu-anchor state, and `App.tsx` should not grow a `useState` per group) asserting that
opening **Research** reveals all four items, that clicking one navigates, and that landing on
`/watchlist` renders **Research** in the active state while **My Positions** is not.

---

## Part H — A plan belongs to an owner, and to a holding

A harvest plan exists to tell **one person** when to sell portions of **their** position as the price
rises. The schema does not currently express either half of that sentence:

- **No owner.** `plan_instances` has no `owner` column while `positions` does, and
  `ux_one_active_plan_per_symbol` (`db.py:196-197`) is keyed on `symbol_id` alone — so exactly one
  ACTIVE plan per symbol can exist across *all* users. The second person to build an AAPL plan
  silently supersedes the first one's.
- **No holding.** `plan_instances.position_id` exists (`db.py:173`, FK to `positions`
  `ON DELETE SET NULL`) but is inserted as a literal `NULL` (`harvester_repository.py:429`) and read
  nowhere — the link has never carried data. Plans float free of holdings, so "does this stock I own
  have a plan?" and "is this plan for something I still own?" are both unanswerable in the UI.

Every plan route today — `plans.py`, `rungs.py`, `dashboard.py`, `symbols.py` — has **no auth
dependency at all**, which is why the gap hasn't bitten yet: there is no caller identity to enforce
against.

**The new baseline: a plan has an owner, and an ACTIVE plan requires at least one OPEN lot of that
symbol held by that owner.** When the last share is sold, the plan closes with it. The orphan flag
(H7) then stops being a routine state and becomes an invariant-violation indicator.

### H1. Schema — `owner` on `plan_instances`

`db/migrations/V7__plan_owner.sql`, mirrored into `init_schema()` (both, per CLAUDE.md):

> **Renumbered 2026-08-10.** This part originally claimed `V6`, which #165's PR 3 took for
> `V6__parity_backfill.sql`. `V7` here and `V8` in H8; the uniqueness guard added in PR 0
> (`tests/test_schema_parity.MigrationOrderTests.test_migration_versions_are_unique`) fails CI on a
> collision rather than letting it reach `flyway migrate`.


- `ALTER TABLE plan_instances ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'john'` — the
  exact V2 move that gave `positions` its owner (`V2__positions_multi_owner.sql:13`). Existing rows
  backfill to `john`, which is correct: he is the only owner holding positions.
- **Swap the uniqueness key**: `DROP INDEX IF EXISTS ux_one_active_plan_per_symbol`, then
  `CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_plan_per_owner_symbol ON plan_instances(owner, symbol_id) WHERE status = 'ACTIVE'`.
  Both statements go in `init_schema()` too, **in that order** — see the ordering note below.
- `CREATE INDEX IF NOT EXISTS idx_instances_owner_status ON plan_instances(owner, status)`, mirroring
  `idx_positions_owner_status`.

`position_id` stays as-is — still unused, still NULL. Ownership is carried by `owner`, not by a lot
FK, because a plan spans *all* lots of a symbol and lots come and go beneath it.

**Ordering in `_SCHEMA` is load-bearing here, for a reason that changed under this plan's feet.**
That `DROP INDEX` is the first non-additive statement to enter `init_schema()`. When this part was
written, two owners of the schema were both executing DDL on deployed databases, and the escalation
this bullet asked to record on
[#165](https://github.com/JohnFunkCode/StockPortfolioManager/issues/165) was that they could now
disagree rather than merely duplicate each other. **That escalation is resolved** —
[#165](https://github.com/JohnFunkCode/StockPortfolioManager/issues/165) shipped 2026-08-09/10, and
`QUANTCORE_SCHEMA_MODE=auto` now resolves to `verify` wherever a `flyway_schema_history` ledger
exists, so on test and prod `init_schema()` runs no DDL at all. Flyway performs the DROP exactly
once; startup only checks the result. There is nothing left to interleave, and
`tests/test_schema_parity.py` proves both owners still express the swap identically.

What survives is narrower and worth a comment in `_SCHEMA` explaining *why*, not just pointing at
the issue: `QUANTCORE_SCHEMA_MODE=create` is the escape hatch an operator reaches for mid-incident.
Today every statement in `_SCHEMA` is additive and idempotent, so flipping to `create` costs only
wasted work. After this part, flipping to `create` on a deployed database executes a `DROP INDEX` —
recreated by the very next statement, so it converges, but there is a window with no uniqueness
constraint on a database serving traffic. Keep the two statements adjacent and in that order, and
say so in the comment.

### H2. A terminal status distinct from SUPERSEDED

Add `CLOSED`, meaning "the position was exited". `SUPERSEDED` already means something specific —
replaced by a newer plan, recorded in `supersedes_instance_id` — and conflating them would lose the
reason a plan ended. `status` is free-text `TEXT` with **no CHECK constraint** (`db.py:174`), so this
is code + data with no DDL.

Touch: `api/routers/plans.py:28` and `harvester_repository.py:531` (the two validation sets),
`Plan.status`'s `Literal["ACTIVE","SUPERSEDED"]` in `api/schemas/harvester.py:33`, the matching type
in `frontend/src/api/types.ts`, and the status toggle in `PlansPage.tsx:93-95`.

### H3. Owner isolation at the SQL layer

Every plan/rung/alert query filters on `plan_instances.owner`. **Enforce it in the SQL, not in the
routes** — `api/routers/portfolio.py:141-146` already documents that pattern for lots ("owner
isolation is enforced at the SQL layer … so these routes trust a None/False/empty-rowcount result
rather than doing their own fetch-then-check"). A wrong-owner `instance_id` then 404s naturally,
with no extra branch.

`HarvesterPlanDB` methods gain a leading `owner` argument: `build_plan`, `display_all_plans`,
`get_plan_by_id`, `get_rungs_for_plan`, `get_rung_by_id`, `update_plan_metadata`, `delete_plan`,
`purge_superseded_plans`, `list_all_symbols`, `get_dashboard_stats`, `symbols_at_harvest_points`,
`harvest_hit_for_symbol`, `mark_rungs_achieved`, `record_execution`, `get_alerts_for_plan`. Rung- and
alert-level methods have no owner of their own and scope through their join back to
`plan_instances`. `HarvesterService` passes it through unchanged (it is a thin wrapper).

**The one easy-to-miss line:** the supersede lookup at `harvester_repository.py:404` selects the
existing ACTIVE plan for a symbol and marks it SUPERSEDED (`:410`). Unscoped, building a plan
supersedes *someone else's*. It must filter on owner — this is the bug the new unique index is
designed to prevent, and both halves are needed.

### H4. Owner at the edges

- **Routes**: `owner: str = Depends(require_owner)` on every route in `api/routers/plans.py`,
  `rungs.py`, `dashboard.py`, and `symbols.py`. `require_owner` (`api/auth.py:240`) resolves the
  authenticated principal and is already what the portfolio routes use; local/compose still resolves
  to `"john"`, so nothing breaks unauthenticated. Deps and query params don't appear in
  `docs/openapi-surface.txt` (it lists `METHOD PATH -> operation_id`), so **zero snapshot churn**.
  `GET /api/dashboard/stats` becomes per-owner, which is what the Harvester dashboard always meant.
- **`notifier.py`**: `calculate_and_send_notifications` (`:45-57`) calls `harvest_hit_for_symbol` and
  `mark_rungs_achieved` for each stock in `self.portfolio`. `Notifier.__init__` (`:29`) gains
  `owner: str = "john"` and threads it into both — the portfolio it holds is already John's, so this
  makes an existing implicit assumption explicit rather than changing behaviour.
- **Frontend**: the `Plan` type gains `owner`; no owner column is added to any table. Every plan the
  UI can see is the viewer's own, so displaying it would be noise.

### H5. Enforce the holding invariant

**On creation** — `HarvesterService.build_plan()` rejects a symbol with no OPEN lot for that owner.
Inject `portfolio_repository` and reuse `PortfolioRepository.list_positions(owner, status="OPEN")`.
`POST /api/plans` returns **422** with a clear message; `CreatePlanDialog.tsx` surfaces it and, better,
narrows its symbol picker to the caller's portfolio symbols so the invalid case is hard to reach.

**On exit** — a symbol's open-share count reaches zero through `PortfolioService.close_lot()`
(`portfolio.py:301`), `delete_lot()` (`:297`), and `remove_position()`. After each, if no OPEN lot
remains for that owner+symbol, close its active plan: new
`HarvesterPlanDB.close_active_plan_for_symbol(owner, ticker, reason)` setting `status='CLOSED'`.

**Both directions inject repositories, not services**, because services in both directions would be a
cycle. That is the established precedent — `OptionsScreeningService` and `RecommendationsService` both
take `ohlcv_repository`, which is `PricesService`'s. Wire in `registry.py:239-243`:
`harvester_repository` into `PortfolioService`, `portfolio_repository` into `HarvesterService`.

**The exit seam is eventually-consistent on purpose.** `close_lots()` is one transaction inside
`PortfolioRepository` (`portfolio_repository.py:428`); the plan close runs after it on a separate
connection. If that second step fails, the lot is closed and the plan is not — exactly the state the
H7 flag exists to make loud. Layered mitigation with a visible last-resort alarm, not a distributed
transaction.

### H6. The indicator on the Portfolio page

`PortfolioService.symbol_rows()` (`portfolio.py:251`) merges `active_plan_id` into each row from a new
narrow `HarvesterPlanDB.active_plan_ids(owner) -> {ticker: instance_id}` — one query over that owner's
ACTIVE plans, hitting the new `idx_instances_owner_status`. (Not `list_all_symbols()`: it scans the
whole `symbols` table — 237 rows against a handful of plans — and its only job was backing the page
Part G deletes.) Purely additive to the response shape, so **no route change and
`docs/openapi-surface.txt` stays untouched** — and the `portfolio_table` sidekick card inherits it.

`PortfolioPage.tsx` renders a `Plan` chip beside the symbol (`:178-182`) linking to `/plans/:id`,
following the `mm_hedge_bias` chip pattern already in that row (`:201-211`). No plan → a quiet
"Create plan" affordance rather than an em dash, since under this baseline every held symbol is
eligible and nothing else is.

Deliberately **not** the next-rung target price: the row already carries eleven columns, and the
Harvester dashboard and `/plans/:id` both show rung detail one click away. The chip answers "is this
managed?", nothing more.

### H7. The flag on `/plans`

`display_all_plans(owner, status)` adds `in_portfolio: bool` per plan, from
`list_positions(owner, status="OPEN")` (reuse, no new SQL). `PlansPage.tsx` renders a warning chip on
any row where `status == 'ACTIVE'` and `in_portfolio` is false. Flag only, per decision — no filter
toggle, no bulk action. Under the new baseline this should never appear, so it is a defect signal;
treating it as routine housekeeping would defeat the point.

### H8. Migrations — two files, because only one of them is dangerous

`V7__plan_owner.sql` is the H1 DDL and nothing else.

> **This paragraph used to say the DDL would report "already exists, skipping" against a deployed
> database, and that this was expected and harmless. That is no longer true**, and the inversion
> matters. It relied on `init_schema()` having already converged the shape at startup; since
> [#165](https://github.com/JohnFunkCode/StockPortfolioManager/issues/165) shipped (2026-08-10),
> startup on test and prod only *verifies*. So the `ALTER TABLE … ADD COLUMN owner`, the
> `DROP INDEX`, and the `CREATE UNIQUE INDEX` all genuinely execute there, against live tables. That
> is a strict improvement — the migration is now the thing that actually happens, which was the whole
> point of #165 — but it is no longer a rehearsed no-op, and it inherits the rule that came with it:
> **migrate before the image carrying the schema change deploys, in both projects**
> (`./scripts/flyway.sh migrate` before merging to `main`, `./scripts/flyway.sh --prod migrate`
> before dispatching `prod-rollout.yml`). Nothing creates the column for you now, and a drifted
> schema aborts startup with `SchemaDriftError`.

`V8__close_orphan_plans.sql` is one data statement: close every ACTIVE plan with no OPEN lot for its
owner. **This is the part that does real work**, and the only statement in this whole plan that
mutates existing rows, so it ships as its own reviewable file rather than hiding behind the DDL.

Run this read-only pre-flight in **both** environments and read the output before migrating:

```sql
SELECT pi.owner, s.ticker, pi.instance_id, pi.created_at FROM plan_instances pi JOIN symbols s ON s.symbol_id = pi.symbol_id WHERE pi.status = 'ACTIVE' AND NOT EXISTS (SELECT 1 FROM positions p WHERE p.symbol_id = pi.symbol_id AND p.owner = pi.owner AND p.status = 'OPEN');
```

The lot predicate **correlates on `pi.owner`**, exactly as `V8` does — not a hardcoded `'john'`.
Since H1 made plans owned, a hardcoded owner reports another owner's plan as an orphan while `V8`
would correctly leave it alone, and a pre-flight that disagrees with the migration is worse than no
pre-flight: it is read once, believed, and never checked again.

**Both environments return zero rows, because there are no plans anywhere.** Pre-flight run
2026-08-11, both read-only:

| | `plan_templates` / `plan_instances` / `plan_rungs` | OPEN positions | Rows `V8` closes |
|---|---|---|---|
| **test** | 0 / 0 / 0 | 5 (`john`, `thomas`) | **0** |
| **prod** | 0 / 0 / 0 | 4 — GEV, GLW, TER, ZS, all `john` | **0** |

So `V8` is a **no-op in both environments**: it repairs a class of row that does not currently
exist, and it ships as the guard for plans built before the invariant lands rather than as a
cleanup of plans built before it was written. Two corrections to what this section used to claim,
both of which would have misled whoever ran it:

- **"Prod holds exactly one open position — ZS"** was true on 2026-07-29 and is not now; prod holds
  four. Position counts date faster than the documents that quote them, which is the argument for
  running the pre-flight rather than trusting a number written three weeks earlier.
- **"Expect prod to close every ACTIVE plan except any on ZS"** set the operator up to read an empty
  result as a broken query. Expect **zero rows**, and read that as the migration having nothing to
  do — confirmed against the row counts above, not inferred from the empty result alone.

Migrating either environment therefore proves the statement parses and Flyway applies it, and
proves *nothing* about the backfill's behaviour. That is fine — but do not let a green `--prod
migrate` be read as evidence that the orphan-closing logic works. The tests are that evidence.

**Mirror the DDL into `init_schema()`; never the backfill.** That function runs on every application
startup, so a data statement living there would silently close plans on every boot — including ones
created seconds earlier. DDL in both, data in Flyway only.

**Docs:** `CLAUDE.md`'s Harvester section gains the owner column, the holding invariant, and the
`CLOSED` status; the Unified Database section's schema list notes `plan_instances.owner`;
`readme.md`'s UI tour gains the portfolio plan chip.

---

## Working alongside other people

This plan is large enough that, executed carelessly, it would sit across `main` for weeks and make
every other branch painful to rebase. Two structural facts make that worse: `main` requires review
and approval (nobody self-merges), so a stack of eight open PRs is itself the bottleneck; and the
work is concentrated in a handful of files that *any* feature branch also touches.

### The hot files, named

| File | Touched by | Why it collides |
|------|-----------|-----------------|
| `frontend/src/navigation.tsx` | C, D, G | ~~Flat `navItems` array **and** a flat `<Routes>` list in `App.tsx` — two places, same PR~~ **Resolved by PR 0:** one `routes` array, and adding a page is a one-object append. Still the only place anyone adds a page, so appends can still collide — but on adjacent lines rather than in two files |
| `frontend/vitest.config.ts` | A, C, D, G | One 4-line `thresholds` block with a dated comment — a per-PR ratchet conflicts with *every* concurrent frontend PR |
| `quantcore/services/registry.py` | B4, H5 | Single `Services` dataclass + single `get_services()` body; anyone adding a service edits it |
| `db/migrations/` + `init_schema()` | H1 | `V6` is the latest, so `V7`/`V8` are unclaimed — a duplicate version number used to be a **runtime** Flyway failure rather than a merge conflict, so git wouldn't warn anyone; PR 0 added the CI guard that now catches it |
| `quantcore/repositories/harvester_repository.py` | H3 | ~15 method signatures at once — the single largest mechanical diff in the plan |
| `chat_tools.py` + `componentRegistry.tsx` | A, C, D | Dual GenUI registration; two literals every new component appends to |
| `requirements-base.txt` | F | Installed by `Dockerfile.{api,mcp,report}`; anyone adding a dependency edits it, and a bad merge here breaks three images rather than one file |
| `CLAUDE.md`, `readme.md`, `docs/openapi-surface.txt` | most parts | Docs-in-the-same-PR rule means everyone edits them |

### Six mitigations

**1. Land a seam PR first (PR 0).** Pure refactor, no behaviour change, reviewable in minutes, and it
converts the worst hot spot from "everyone edits the same lines" into "everyone appends one object":

- Extract the nav and route lists out of `App.tsx` into `frontend/src/navigation.tsx` — one exported
  array of `{ path, label, icon, element, group?, nav?: false }`, which both the `<Stack>` of nav
  buttons and the `<Routes>` block map over. Detail routes (`/securities/:symbol`, `/plans/:id`)
  carry `nav: false`; `/` stays the index route.
- After this, **adding a page is a one-object append** instead of two edits in two places, and Part
  G2's grouping becomes "add a `group` field + change one render site" rather than a rewrite of both
  lists — so it stops competing with anyone else's new page.
- Add a CI guard that migration version numbers are unique (`db/migrations/V*.sql`). Cheap, and it
  catches the one collision class git silently allows.

**2. Ratchet coverage once, not per PR.** `frontend/vitest.config.ts` thresholds get raised in the
**last** frontend PR of the series only; every earlier PR just has to clear the existing floors
(89/87/86/70). This removes a guaranteed 4-line conflict from every frontend PR in this plan and
from anyone else's.

**3. Make Part H3 mechanical first, strict second.** Add `owner` as a **keyword-only argument with a
`"john"` default** (`def get_plan_by_id(self, instance_id, *, owner: str = "john")`) in the first
pass. Zero call sites change, so any in-flight harvester branch rebases cleanly and the diff is
signature lines only. A short follow-up in the same series drops the default once every caller passes
it. *Tradeoff, stated plainly:* this leaves a window where a caller that forgets `owner` silently
reads John's data. That is behaviour-identical in today's single-owner world, but the tightening PR
must land in this series — not be deferred — or the window becomes permanent.

**4. Everything this plan adds to an existing response is additive.** `symbol_rows()` gains
`bar_base`/`bar_gain`/`bar_loss` (A) and `active_plan_id` (H6); `display_all_plans()` gains
`in_portfolio` (H7); the four fundamentals methods gain `scope` defaulting to `"all"` (B4). **No
existing route, field, or default changes**, so nobody's branch breaks on a rebase and no coordinated
front-end/back-end landing is needed.

**5. Announce the reservations up front.** Comment on the tracking issue claiming `V7`/`V8`, the
`CLOSED` plan status, and the `owner` column before PR 0 merges. Migration numbers and status
vocabularies are the two things another branch can duplicate without git noticing. **Done
2026-08-10**, while PR 0 (#177) was open — see [the reservations comment on
#147](https://github.com/JohnFunkCode/StockPortfolioManager/issues/147).

**6. Cap in-flight PRs at two, and keep branches short-lived.** The merge cost is a function of how
long a branch lives, not how big the plan is. Eight sequential small PRs over three weeks conflict
with far less than three big ones held open simultaneously. Each PR here is independently revertable
— none leaves the system in a half-migrated state.

### Three tracks, workable in parallel by different people

| Track | Parts | Owns | Overlaps |
|-------|-------|------|----------|
| **Job & report** | E, F | `main.py`, `scripts/`, `runOnPi.sh`, `requirements*.txt`, `Dockerfile.report` | **None with the other tracks.** But the dependency split rebuilds the api and mcp images too, so it is the one item here needing an image smoke test rather than just unit tests |
| **Data pages** | A, B, C, D | `quantcore/analytics/`, `services/{prices,watchlist,fundamentals}.py`, `frontend/src/components/{watchlist,fundamentals}/` | `registry.py`, `App.tsx`, the two chat registries |
| **Plan ownership** | H | `harvester_repository.py`, `services/harvester.py`, `api/routers/plans*`, `db.py`, migrations | `registry.py`, `PortfolioPage.tsx` |

The **Job & report** track is genuinely conflict-free, which is why it moves earlier in the sequencing
below — it also fixes the complaint that started this (nobody can read the report) without waiting on
anything. The other two overlap in `registry.py` only, and in two places that are additions to
different lines of the same function.

**If the series needs to shrink**, Part G is the piece to drop or defer: it is the highest-conflict,
lowest-value part (nav cosmetics plus deleting a page nobody needs), and nothing else depends on it.

---

## Sequencing

Nine PRs, each independently shippable and verifiable on test. Reordered from the obvious feature
order to put conflict-free and seam-clearing work first:

0. **The seam PR** — extract `frontend/src/navigation.tsx`, add the migration-version CI guard. Pure
   refactor, no behaviour change. Everything else rebases onto it, including other people's work.
1. **Parts E + F** — warmer, report extraction, the dependency split (`requirements-report.txt`,
   slimmed `requirements-base.txt`, `Dockerfile.report` cleanup), `runOnPi.sh`, docs. Zero overlap
   with any other track, and it fixes the originating complaint. Can be worked concurrently with
   anything below. The one item in the series whose blast radius is *containers* rather than files —
   it rebuilds the api and mcp images, so it needs the image smoke check in Verification before
   rollout.
2. **Part A** — stacked bar on the Portfolio page. Small, no new route, immediate visible win.
3. **Parts B1–B3, B5** — analytics, bulk returns, `WatchlistService.returns_and_fundamentals()`, the
   new route.
4. **Part C** — the `/watchlist` page and sidekick card. *This is the report replacement; everything
   after it is additive.*
5. **Parts B4 + D** — `scope` plumbing and the `/fundamentals` page.
6. **Parts H1–H4** — `owner` on `plan_instances` (`V7`), the index swap, owner threaded through the
   repository/service/routes/`notifier.py` (keyword-only with a default, per mitigation 3), `CLOSED`
   in the vocabulary. Behaviour-preserving for the single-owner world, which is what makes it safe to
   ship on its own.
7. **Parts H5–H8** — drop the `owner` default, the holding invariant, the portfolio chip, the orphan
   flag, and the `V8` orphan-close backfill. Separate because this is the one PR that mutates existing
   rows and the one that can break a live plan; it deserves an unhurried review on its own.
8. **Part G** — remove `/symbols` and group the nav; raise the vitest floors once, here. Ships after H
   (which supplies the symbol→plan link `/symbols` was carrying) and after the two new pages, so the
   nav churns once and the grouping is done against the final set of eight destinations.

Only three real dependencies: everything follows **0**; C follows B; G follows H and C/D. **1** is
free-floating. Track 1 (item 1), Track 2 (items 2–5), and Track 3 (items 6–7) can run on different
branches at the same time — keep two in flight, not three.

---

## Verification

**Backend, local:**

```bash
python -m unittest discover -s tests -t .
```

```bash
coverage run -m unittest discover -s tests -t . && coverage report
```

New tests: `tests/test_returns_analytics.py` (YTD across a year boundary, YTD when Jan 1 falls on a
market holiday — the case `metrics.py` gets wrong, 1y with a missing bar, 1y against a frame longer
than a year — the other case `metrics.py` gets wrong, market-cap FX normalization); extend the
portfolio-math suite for `allocation_segments()` (gain, loss, zero cost basis); a `WatchlistService`
test asserting the composition issues **six queries and zero yfinance calls** — that assertion is the
whole point of the design, so it belongs as a regression guard, not a comment; and a `scope="all"`
default test proving existing fundamentals callers are unchanged.

Part H gets its own suite, since the whole part is two rules — a plan has an owner, and a plan has a
holding. **Owner isolation:**

- two owners hold ACTIVE plans on the **same symbol** at the same time — the case
  `ux_one_active_plan_per_symbol` makes impossible today and the new index must permit
- `build_plan()` for owner B does **not** supersede owner A's plan on that symbol (the
  `harvester_repository.py:404` lookup)
- every read/mutate with the wrong owner 404s: `get_plan_by_id`, `update_plan_metadata`,
  `delete_plan`, `get_rungs_for_plan`, `get_rung_by_id`, `mark_rungs_achieved`, `record_execution`
- `get_dashboard_stats()` counts only the caller's plans, and `display_all_plans()` only theirs

**Holding invariant:**

- closing the **last** open lot for a symbol flips that owner's ACTIVE plan to `CLOSED`; closing a
  *partial* lot leaves it ACTIVE (the off-by-one that would silently kill live plans); owner B's plan
  on the same symbol is untouched
- `delete_lot()` and `remove_position()` reach the same end state as `close_lot()`
- `build_plan()` on a symbol with no OPEN lot **for that owner** raises, and `POST /api/plans`
  returns **422** — including when a *different* owner holds it
- `display_all_plans()` returns `in_portfolio: false` for a plan whose symbol has no OPEN lot, and
  `CLOSED` is accepted by both status validators (`api/routers/plans.py:28`,
  `harvester_repository.py:531`) while an unknown status still 400s
- `symbol_rows()` carries `active_plan_id` — present for a planned symbol, `None` otherwise — and the
  existing `portfolio_table` response assertions still pass (additive shape)

**Route + snapshot:**

```bash
python scripts/check_openapi_snapshot.py
```

Start the API and hit the new route against the test DB, confirming sub-second response and populated
rows:

```bash
uvicorn api.main:app --host 127.0.0.1 --port 5001
```

```bash
time curl -s localhost:5001/api/watchlist/fundamentals | head -c 2000
```

```bash
time curl -s "localhost:5001/api/securities/fundamentals/top?scope=tracked&n=25"
```

**Frontend:**

```bash
cd frontend && npx vitest run --coverage
```

Co-located `.test.tsx` per new component using `mockApi`/`renderWithProviders` from
`src/testUtils.tsx` (network-mocked, not hook-mocked), plus registry parity on both sides. Each PR
must clear the **existing** floors (89/87/86/70); the ratchet itself happens **once**, in the final
frontend PR, re-measured with a dated comment matching the existing convention — see mitigation 2, it
is otherwise a guaranteed conflict on every parallel frontend branch.

Then run the dev server and verify in the browser preview: the Portfolio page renders the stacked bar
with segments matching the table's numbers; `/watchlist` renders ~215 rows sorted by composite score
with tag filtering, sort persistence across reload, working inline add/remove, and the staleness chip;
`/fundamentals` renders all five panels scoped to tracked symbols; the Portfolio page shows a `Plan`
chip linking to `/plans/:id` on a held symbol with a plan and a "Create plan" affordance on one
without; `/plans` shows the orphan warning chip on a deliberately orphaned row; `/symbols` 404s while
`PlanDetailPage`'s `LivePrice` still renders; and the top bar shows three controls (My Positions,
Research, Settings) whose menus reach all eight destinations, with the correct group highlighted on
each route — checked at both `desktop` and `tablet` widths, since three dropdowns plus the wordmark
and the two right-hand `IconButton`s is a tighter bar than the flat seven were.

**Part H migration** — run the read-only pre-flight from H8 against **test first, then prod**, and
review the list of plans it would close before running the migration anywhere:

```bash
./scripts/with-test-db.sh sh -c 'psql "$QUANTCORE_DB_DSN" -f /tmp/orphan_plans_preflight.sql'
```

The `sh -c` wrapper is load-bearing: the helper **exports** `QUANTCORE_DB_DSN`, and `psql` does not
read that variable — invoked as `with-test-db.sh psql -f …` it falls through to the local socket and
connects to the wrong database, or fails authentication and looks like the proxy is down. Same reason
the DSN is passed as `"$QUANTCORE_DB_DSN"` rather than interpolated: it carries a password and must
never reach a log, a shell history, or a terminal.

```bash
./scripts/flyway.sh info
```

```bash
./scripts/flyway.sh migrate
```

Re-run the pre-flight afterwards: it should return zero rows. Only then repeat with `--prod`
(which prompts). **Expect zero rows on prod too** — see H8 for the row counts behind that; both
environments have no plans at all, so this migration is a no-op in both and its value is entirely
prospective.

**Status:** test migrated 2026-08-11, schema version 7 → 8, pre-flight clean before and after. Prod
still at **V7** — its `--prod migrate` runs before `prod-rollout.yml` is dispatched.

Then verify the schema **directly**, not with `flyway info` — the index swap is the one change where
`init_schema()` and Flyway could each believe they succeeded while the database holds the other's
answer:

```sql
SELECT to_regclass('ux_one_active_plan_per_symbol') AS old_gone, to_regclass('ux_one_active_plan_per_owner_symbol') AS new_present, (SELECT count(*) FROM information_schema.columns WHERE table_name='plan_instances' AND column_name='owner') AS owner_col;
```

Expect `NULL`, a non-NULL oid, and `1`. Restart the API once afterwards and re-run it, confirming
`init_schema()` converges to the same state rather than recreating the dropped index.

**Cache warmer** — run against the test DB with a short budget, confirming notifications and options
capture still fire first:

```bash
./scripts/with-test-db.sh python main.py
```

Confirm freshness actually moved via the fundamentals MCP server's `get_cache_stats` — prod `newest`
timestamps are 2026-07-21/27 as of 2026-07-29; after a warmer run against test they should be
same-day.

**Preserved report:**

```bash
python scripts/generate_portfolio_report.py --output /tmp/portfolio_report.html
```

Open it and diff against the Pi's current output — summary, holdings table, both charts, and the
watchlist table should be comparable modulo prices and timestamps.

**The slimmed lean image — the one check the existing CI gate cannot make.**
`scripts/ci_wrapper_smoke.py` boots each wrapper in the *runner's* Python, where
`requirements.txt` is installed, so it would pass even if `matplotlib` were still a hidden
transitive need of the api or an mcp wrapper. The removal has to be verified against images built
from the slimmed `requirements-base.txt`. `requirements-ml.txt` is `-r requirements-base.txt` plus
torch/transformers/anthropic, so `Dockerfile.api` inherits the slimming too — all three images
must be rebuilt and booted:

```bash
docker compose build quantcore-api stock-price portfolio
```

```bash
docker compose up -d quantcore-api stock-price portfolio && docker compose logs -f quantcore-api
```

Expect a clean uvicorn start (no `ModuleNotFoundError`), then hit `/api/portfolio/symbols` and one
wrapper's `/mcp` `listTools` through the compose stack. Run the wrapper smoke **inside** the mcp
container rather than on the host, since that is the environment the change actually alters:

```bash
docker compose exec stock-price python scripts/ci_wrapper_smoke.py
```

Finally confirm the report path still works from a full install (`requirements.txt` = base + ml +
report), which is what the Pi and local dev use — that is the `generate_portfolio_report.py` run
just above.

**Deploy:** merge to `main` → `deploy.yml` rolls test automatically (api, ui, report job). Verify on
`https://quantui-493357101423.us-central1.run.app`, then promote with a manual `prod-rollout.yml`
dispatch using the 7-char SHA.

**The two checks that cannot be made by finishing the code** — both are about prod running on its own
schedule, and both look like success from the outside if they are wrong:

1. **The first prod execution of the post-#147 `main.py`.** The Job (`quantcore-report`, Cloud
   Scheduler `0 17 * * 1-5 America/New_York`) was carrying the new image within hours of the
   2026-08-11 rollout, but that day's 17:00 ET run *predates* it and therefore ran the old code — so
   the first real one is the next weekday firing. What it proves and nothing earlier can: Discord
   notifications arrive **once**, no HTML rendering or S3 upload is attempted, and the fundamentals
   warmer plus `alert_if_fundamentals_stale` still behave with the report path gone. Supporting
   evidence already gathered: the Job's env is exactly `QUANTCORE_DB_DSN` + `DISCORD_WEBHOOK_URL`,
   with **no `BUCKET_NAME`/`BUCKET_KEY`** — correct for a job that no longer publishes.
2. **Which code path the Pi is running.** `runOnPi.sh` is repointed at
   `scripts/generate_portfolio_report.py --publish` in the repo, but the box has its own checkout and
   its own crontab, and neither is visible from here. On 2026-08-11 the published page was fresh
   (`last-modified: Tue, 11 Aug 2026 20:41:16 GMT`, 657KB, HTTP/2 200) and it cannot have come from
   Cloud Run, since that Job has no bucket credentials — so the Pi published it. That does **not**
   establish it used the new script. If the checkout is stale and cron still runs `main.py`, the page
   updates correctly *and every Discord alert doubles*, which is precisely the failure the Part F
   split exists to prevent and is invisible in the artifact. Check `git log -1` and the crontab on
   the Pi; this is the one item in this plan with no remote evidence.

---

## Decisions already settled

Recorded here so implementation doesn't re-litigate them:

- **The public page becomes a manual artifact.** No S3 credentials go into the Cloud Run Job — under
  this plan it no longer generates HTML at all. `www.johnfunk.com/portfolio/portfolio_report.html` is
  published by `scripts/generate_portfolio_report.py --publish`, run by hand or by the Pi.
- **The Pi is repointed, not retired**, and runs the **report script only** (Part F) — never
  `main.py`, which would duplicate the deployed job's alerts and options snapshots.
- **Issue #165 gets a comment, not a successor issue** (Part H1).
- **Nav is grouped, not flat** (Part G2) — My Positions / Research / Settings, as top-bar dropdowns.
- **`/watchlist` and `/fundamentals` are two pages, not one.** `/watchlist` is the curated roster
  ("how are my ideas doing?"); `/fundamentals` is ranked discovery over watchlist ∪ portfolio ("what's
  good?"). The score-changes-over-time view is the thing a single static table cannot express.
- **Metrics are 5d / 30d / 60d / YTD / 1y**, computed from the OHLCV cache in one convention
  (close-to-close), not sourced from yfinance's per-symbol `info` fields (Part B1).
- **No nightly report archive.** #147 proposes persisting each night's rendered report so you can
  see what the portfolio looked like on a given date, calling it "the one capability the live UI does
  *not* provide". Declined, and the reason belongs on the issue: **that history is already in the
  database.** `positions.opened_at` / `shares` / `cost_basis_total` plus
  `lot_sales.sale_trade_date` / `shares_sold` / `sale_price` reconstruct the open lots on any past
  date, and the `ohlcv` daily cache values them. Storing rendered HTML would make a blob store the
  third owner of portfolio history, behind the lot ledger and the price cache — and an archive of
  pages is the least queryable form the answer could take. If the capability is ever wanted, it is a
  reconstruction function in `quantcore/analytics/` plus a value-over-time chart, not an HTML
  archive. **The honest caveat, recorded so nobody rediscovers it:** historical FX rates are not
  stored anywhere, so a reconstruction would value non-USD lots at today's rate. That is a real
  limitation of the reconstruction approach, and it is still a better place to spend the effort than
  a page archive.
- **`main.py`'s remaining orchestration stays in `main.py`.** #147 notes that per arch-v2 it should
  be a service with a thin entry point. True, and out of scope — removing the report is what the
  issue is actually blocked on. Recorded on #147 rather than silently dropped.
- **The dead report's dependencies come out of the shared lean image**, not just the report image
  (Part F) — the wider reading of #147's own "Consequences" section.

## Open items to decide during implementation

- Whether Part H3 actually needs the permissive `owner="john"` default (mitigation 3). It exists
  purely to keep the diff off other people's harvester branches — **if nothing is in flight against
  `harvester_repository.py` when that PR opens, make `owner` required immediately** and skip the
  window entirely. Check before choosing; the safe version is strictly better when it's free.

---

## Checkpoint log

Append a row as each PR lands. Any dev machine can resume by reading the last row, running
`git pull`, and continuing with the next item in [Sequencing](#sequencing).

| Date | PR / Item | Status | Notes |
|------|-----------|--------|-------|
| 2026-07-29 | — | Plan written | Approved after nine review rounds; all decisions in "Decisions already settled" answered by the repo owner. No code written |
| 2026-08-10 | 0 | Merged ([#177](https://github.com/JohnFunkCode/StockPortfolioManager/pull/177), `923f188`) | Seam PR. `frontend/src/navigation.tsx` holds one `routes` array (`{path,label,icon,element,group?,nav?:false}`) that both the `<Stack>` of nav buttons and `<Routes>` map over; the two detail routes carry `nav: false`, `/` stays the index route, and the active-path predicate is hoisted to an exported `isPathActive(path, pathname)` so Part G2's group buttons can reuse it. `App.tsx` drops 16 imports and both lists — behaviour identical, 454 frontend tests green (up 12; new `navigation.test.tsx`), floors cleared at 89.75/87.74/87.63/71.06 and **not raised** (mitigation 2). Migration-version guard landed as `MigrationOrderTests.test_migration_versions_are_unique` rather than a new workflow step — it runs in the existing `gate` job, needs no database, and names both colliding files; verified by planting a duplicate `V6`. Also folded in the plan corrections below |
| 2026-08-10 | 1 | Merged ([#178](https://github.com/JohnFunkCode/StockPortfolioManager/pull/178), `5a21bb5`) | Parts E + F. **E:** new `FundamentalsService.cache_freshness(symbols, data_type)` returns per-symbol age/staleness sorted **never-fetched first, then oldest-first**, plus `coverage`/`oldest_age_seconds` — it reports on the symbols *asked about*, not the cache's contents, so a symbol added this morning shows as a hole. `main.py` gains `warm_fundamentals_cache()` (budgeted, per-symbol guard), `alert_if_fundamentals_stale()` (two independent triggers: coverage floor **or** age ceiling), and `run_fundamentals_warming()` (outer guard, never raises; re-reads freshness *after* warming so the alarm describes the state the run left behind). Three env vars, all falling back loudly on a typo. The alert title carries the date — `send_notifications()` dedupes on title against `notification.log`, so a dateless title would alarm once and then go quiet while the cache stayed stale. **F:** `scripts/generate_portfolio_report.py` (`--output` / `--publish`); the four moved functions are byte-identical apart from one line, `template_dir`, which was `Path(__file__).parent` and would have pointed at `scripts/`. `save_html_to_s3` now **raises** instead of returning `None`, and `main()` checks `BUCKET_NAME`/`BUCKET_KEY` up front — that silent-success path is the original defect. `main.py` 704 → 282 lines. Notes below |
| 2026-08-10 | 2 | Merged ([#182](https://github.com/JohnFunkCode/StockPortfolioManager/pull/182), `50d99a4`) | Part A. New `portfolio_math.allocation_segments()` returns `bar_base`/`bar_gain`/`bar_loss` (loss deliberately **negative**, all three `None` when unpriced); `PortfolioService.symbol_rows()` spreads them in and `SymbolRowResponse` carries them, so the chart scales pixels and computes nothing (Rule 8.4). The legacy matplotlib overlay is reproduced exactly — the red loss block sits on the **top** of the orange basis bar rather than under it, so the visible top of every bar is current value up or down; a test pins that geometry, since a plausible-looking "fix" would silently redefine what a loser's bar means. Unpriced symbols are **dropped**, not flattened: a zero-height bar reads as a worthless position rather than an unknown one. Ships as GenUI `portfolio_allocation` with an empty prop spec in both registries (decision #20 — no `owner` prop, ever), self-fetching on the shared `['portfolio-symbols']` key so the card costs no second request while the page passes down rows it already has. Both mounts guard nothing-to-draw rather than leaving an empty panel. Six existing `PortfolioPage` tests needed scoping to the holdings table — the chart's x axis labels the same tickers, so page-wide `getByText('INTC')` became ambiguous; that ambiguity will recur in every later part that adds a ticker-labelled visual to an existing page. 468 frontend tests green (up 18), floors cleared at 89.95/87.96/87.82/71.26 and **not raised** (mitigation 2); 122 backend tests OK; OpenAPI surface unchanged (the gate is route-level, so new response fields need no snapshot update) |
| 2026-08-10 | 3 | Merged ([#183](https://github.com/JohnFunkCode/StockPortfolioManager/pull/183), `7990bbf`) | Parts B1–B3, B5. **B1:** `quantcore/analytics/returns.py` is one correct copy of return math replacing three inconsistent ones. It is close-to-close throughout — the report script's convention — and documents the three defects that made consolidation the only option: `portfolio/metrics.py` computes **open-to-close** (`Open.iloc[-6]` → `Close.iloc[-1]`, so a 5-day return includes the sixth day's overnight gap), anchors YTD by counting business days with `bdate_range` (which counts market holidays as business days, walking the positional index off the real first bar of the year), and divides by `iloc[0]` of a **two-year** frame while calling the result `one_year_return`. `metrics.py` is deliberately left alone with a divergence comment: it feeds the preserved report script, and "fixing" it would move numbers on a page nobody is watching. `normalize_market_cap` returns **three** fields — native figure, its currency, and USD only when a rate is known — rather than one converted number; it does **not** fetch FX, because the repo's only FX source (`portfolio/money.py`) is a network call to open.er-api.com and would destroy the zero-network property below. The bug it exists for is SK hynix's ₩1,456T rendering as `1456.62T` in a column of dollars and sorting to the top. **B2:** `PricesService.bulk_period_returns` is modelled on `screen_securities` (one `daily_bars_for_symbols` read, group in memory, then pure analytics) and deliberately *not* on `PortfolioService._period_returns`, which loops per symbol. The `ts` epoch → `date` conversion happens in the service so analytics never sees a repository row shape (Rule 8.4), and the calendar anchor defaults to the **newest bar rather than today** — on a Sunday, or with a symbol whose cache is days behind, "today" silently widens the YTD/1y window past the data. **B3:** `FundamentalsService.get_all_latest` / `cache_ttl_seconds` are thin passthroughs placed on the *service* rather than letting `WatchlistService` reach into `FundamentalsRepository`: crossing into another domain's repository would mean re-deriving that domain's staleness rule at each call site. `ttl_seconds()` is an env read, not a query — which is what makes the query count below exact. `WatchlistService.returns_and_fundamentals` composes those services (the `RecommendationsService` precedent, arch-v2 §5.2), both injected optionally so the CRUD half stays constructible from a bare repository (import scripts, tests). **Six queries, zero network:** one `list_entries`, one `bulk_period_returns`, four `get_all_latest`. `tests/test_watchlist_service.py` asserts both halves at 1, 3 and 25 symbols — constant in list size — against the **real** `PricesService`/`FundamentalsService` with counting fakes beneath them and an `ExplodingYFinance` gateway whose `__getattr__` raises, because the easy wrong version of this method is a per-symbol loop that still returns the right answer. Rows are shown **stale rather than dropped** (`fundamentals_stale`, `fundamentals_age_hours`): a table that loses rows overnight reads as "we stopped watching that" instead of "this number is a week old". **B5:** `GET /api/watchlist/fundamentals`, declared **before** `/watchlist/{ticker}` — FastAPI matches in declaration order, so the reverse lets the DELETE path template swallow `fundamentals` as a ticker; nothing enforces the ordering but the comment above it, and a `DELETE /api/watchlist/ZZZZ` → 404 check confirms it. No `?owner=` — the watchlist is global (#83). Unlike PR 2, this one *does* move the OpenAPI snapshot (106 → 107): the gate is route-level, so new response fields are invisible to it but a new route is not. 23 new analytics tests, `test_watchlist_service.py` at 21, no schema change (three-files rule N/A) and no frontend (floors untouched). Smoke-tested against a live database: 228 rows, `stale_count: 0`, `unscored_count: 0`, first row `SBMO.AS` (EUR) carrying `market_cap_usd: null` — the currency bug behaving exactly as designed rather than inventing a dollar figure. **Follow-up out of band:** the smoke test also showed three rows *declared* USD that are not (ASSA-B.ST Stockholm, AUTO.OL Oslo, NIB.F Frankfurt) — a wrong dollar figure rather than a missing one, since the seed came from `watchlist.yaml`. Fixed in [#184](https://github.com/JohnFunkCode/StockPortfolioManager/pull/184) (`a19e607`): `add_entry` resolves the currency from the exchange and the supplied value demotes to a fallback, plus `scripts/repair_watchlist_currency.py` for the rows already stored |
| 2026-08-10 | 4 | Merged ([#186](https://github.com/JohnFunkCode/StockPortfolioManager/pull/186), `db913b1`; coverage follow-up [#187](https://github.com/JohnFunkCode/StockPortfolioManager/pull/187), `c5a5ce0`) | Part C — the `/watchlist` page and the sidekick card, **the report replacement**. `WatchlistPage.tsx` renders the whole list from the single `GET /api/watchlist/fundamentals` (one request, not one per row); the server does the ranking and the `stale_count`/`unscored_count` arithmetic, so the page computes nothing (Rule 8.4). The tag editor needed one new route — `PATCH /api/watchlist/{ticker}` — carrying `{tags: [...]}` and **replacing** rather than merging, because the UI sends the chip set it is displaying and a merge could never remove the last tag; an empty array is a legitimate edit, which is why the service returns the stored list instead of a row count. PATCH rather than PUT: tags are the only editable field, and the currency in particular is now resolved from the exchange (#184) and must not become client-writable. Ships as GenUI `watchlist_fundamentals` with an empty prop spec in both registries — no `owner` prop and no `symbol` prop, because the watchlist is one global list (#83). Search + tag filter + sort model persist in `localStorage`, each parsed in a `try/catch` that falls back rather than blanking the page on corrupt JSON. Removal keeps the shared-list warning ("this removes it for everyone") and stays a UI-only action — no `delete` verb was added to `mcp_gateway/rest_client.py`. Review of #186 turned up two more, both fixed on the branch: the default sort model's second item (`return_30d`, the report's tie-break) was being **silently dropped** by the community grid's `sanitizeSortModel`, so the tie-break now lives inside the score comparator; and the persisted tag filter accepted valid-JSON-wrong-shape (`null`, `{}`) from `localStorage` and blanked the page — both restores now validate the shape rather than only catching a parse error. 535 frontend tests green (up 61, four new suites), floors cleared at 90.34 lines / 88.35 statements / 88.28 functions / 72.02 branches and **not raised** (mitigation 2 — Part G ratchets them once); OpenAPI surface 107 → 108 for the new route; no schema change; backend 1379 tests OK against the test database. **Follow-up [#187](https://github.com/JohnFunkCode/StockPortfolioManager/pull/187) (`c5a5ce0`):** the diff-coverage gate came back red at 81% against an 85% floor over seven uncovered lines — the PATCH route's 400 branch and the watchlist repository's commit/rollback arms. Two things are worth remembering. `git diff -U0` anchored part of the insertion onto `set_currency`'s tail because `set_tags` is textually near-identical, so the gate attributed pre-existing lines to this PR; the fix was to cover them rather than to argue with the anchoring. And the repository's rollback arms are only reachable through a **real** connection, so those tests bind the same parameters against a missing column — a fake cursor would have proved nothing about `except`/`rollback`/`raise`. The tag-editor test also stopped waiting on the MUI exit transition and now waits on the PATCH itself. Re-running the exact 37-line diff: 0 missing, 100%. Sort gotcha below |
| 2026-08-10 | 5 | Merged ([#188](https://github.com/JohnFunkCode/StockPortfolioManager/pull/188), `e2eb0ae`) | Parts B4 + D — `scope` plumbing and the `/fundamentals` page. **B4:** the four collection reads on `FundamentalsService` (`get_top_fundamental_stocks`, `get_upcoming_earnings`, `get_sector_fundamental_breakdown`, `get_fundamental_score_changes`) take `scope="all"\|"tracked"` and echo it back in the payload. "Tracked" is the shared watchlist plus **every** owner's positions — deliberately the same set `main.py` builds for the nightly options capture and hands to the fundamentals warmer (issue #126 decision #5), which is what makes the scope honest in both directions: it never shows a symbol the warmer isn't refreshing, and never hides one the warmer pays for every night. Two small readers exist for the halves (`WatchlistService.symbols()`, `PortfolioService.all_symbols()`) rather than having the caller pick `symbol` out of `list_entries()`, so each roster stays its own service's answer to give. **`_apply_scope` runs immediately after `get_all_latest()` and before any ranking** — the cache holds every symbol anyone has ever asked about, so the top N of the cache is not the top N of the roster, and filtering after ranking would quietly serve a shorter list of the wrong names; on `score-changes` it also cuts real work, since the loop below it runs a history query per surviving symbol. **Deviation from the plan's wording:** Part B4 says to inject the watchlist and portfolio services into `FundamentalsService`, which cannot be done — `WatchlistService` composes `FundamentalsService` (Part B3), so naming them in its constructor is a construction cycle in the registry. What ships instead is a late-bound `tracked_symbols: Callable[[], Iterable[str]] \| None` closure over locals bound further down `get_services()` (the two constructions are hoisted above the `Services(...)` literal so the closure resolves to the live instances the REST tier serves from). Optional, because most construction sites — tests, scripts, the MCP tools — only ever ask for `all`; asking for `tracked` without a provider raises rather than silently returning everything. Late binding is also what would let a rename or a re-order fail *silently* until a request arrived, so new `tests/test_registry_wiring.py` calls the closure, asserts it equals the union of the two live reads (composition, not a count — prod's positions table is deliberately empty), runs `scope="tracked"` end to end, and pins that `get_services()` hands back the same instances it closed over. At the route boundary the parameter is typed `Scope = Literal["all","tracked"]`, so an unrecognized value is a **422 from FastAPI** rather than a service `ValueError` surfacing as a 500, and the allowed values land in the OpenAPI schema. The OpenAPI **surface snapshot does not move** (108 routes) — the gate is route-level, so query parameters are invisible to it, same as PR 2's response fields. `cache-stats` deliberately takes no scope: it describes the cache itself, and scoping it would answer a different question than "how fresh is the data behind this page". **D:** `/fundamentals` is five panels over five cache-backed reads — `TopFundamentalsPanel`, `SectorBreakdownPanel`, `ScoreChangesPanel`, `UpcomingEarningsStrip`, `CacheFreshnessBanner` — each owning its own loading and error state with **no page-level gate**, so one slow or failing query can't blank the other four (tests pin exactly that for a failed `score-changes` and a failed `cache-stats`). Four are fetched with `scope=tracked`; the banner is scope-less and fails **silently** — it is a freshness annotation, and an error alert about the annotation is noisier than the missing chip it replaces. The banner also never paints the database identifier, which a test asserts: the never-log policy covers responses, and `cache_stats()` leaking the DSN is the bug `tests/test_dsn_redaction.py` exists for. Two sidekick cards ship as GenUI `fundamentals_top` / `fundamentals_score_changes`, both with **empty prop specs** in both registries (whole-universe views — there is neither a symbol nor an owner to name), and both are the panels' `variant="rail"` sharing one hook and react-query key rather than second copies; `FundamentalsCards.test.tsx` asserts the rail shape (`n=10`, no `Coverage` column; no `Window` column) precisely so a future copy-paste shows up as a failure. **Found only by previewing the page against the live test database, and fixed here:** the earnings strip rendered `Aug 9, 2026 · today` for an API value of `2026-08-10`. `formatDate` handed the bare `YYYY-MM-DD` to `new Date()`, which reads a date-only string as **UTC midnight** — one day early for every viewer west of Greenwich, all day. It parses component-wise into local time now, with strings carrying a time (`…T12:00:00Z`) still treated as real instants. Two things worth keeping straight: it is a **shared-helper** fix, not a page-local one, so `/watchlist`'s `prices <as_of>` chip and both plans pages were a day behind too; and the existing test passed through the whole defect because it asserted `/Jul/` and `/2026/` on a date whose month and year survive the shift — the new case asserts the exact string. 585 frontend tests green (up 50, five new suites), floors cleared at 90.65 lines / 88.7 statements / 88.7 functions / 72.88 branches and **not raised** (mitigation 2 — Part G ratchets them once); backend 1404 tests OK against the test database; no schema change (three-files rule N/A) |
| 2026-08-10 | 6 | Merged ([#190](https://github.com/JohnFunkCode/StockPortfolioManager/pull/190), `3a8989b`; lean-install follow-ups [#191](https://github.com/JohnFunkCode/StockPortfolioManager/pull/191) `bcebcb4` and [#192](https://github.com/JohnFunkCode/StockPortfolioManager/pull/192) `f838394`) | Parts H1–H4 — `owner` on `plan_instances`, index swap, `CLOSED`. Behaviour-preserving for the single-owner world: every existing row is John's, which is exactly what the column's `DEFAULT 'john'` backfills, and every route resolves the same `"john"` it always implied. **The invariant moves**, from "one active plan per symbol" to "one active plan per *owner* per symbol" — `V7__plan_owner.sql` drops `ux_one_active_plan_per_symbol` and creates `ux_one_active_plan_per_owner_symbol ON plan_instances(owner, symbol_id) WHERE status = 'ACTIVE'`, and the DROP and CREATE must stay adjacent and in that order or a migration that half-applies leaves the table with *neither* guard. **The three-files rule bit in a way worth recording:** `_SCHEMA` uses `CREATE TABLE IF NOT EXISTS`, so adding `owner` to the `plan_instances` block is a no-op on any database that already has the table — and the new index, which is *built on that column*, then fails with `UndefinedColumn`. All 29 tests in `tests/test_harvester_repository_db.py` errored in `setUp` before the fix, which is an explicit `ALTER TABLE plan_instances ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'john';` placed after the CREATE TABLE and **before** the index pair (the `positions` block at `quantcore/db.py:108` is the precedent). Verified against the live test database afterwards: old index gone, new index present, exactly one `owner` column. **Isolation is enforced in SQL, not in the routes.** Every statement carries an owner predicate — including the JOINs for rungs and alerts, which have no `owner` column of their own and inherit it from the plan they hang off — so another owner's plan reads as `None`/zero rowcount and the route reports **404**, the same answer as an id that does not exist. That is deliberate: fetching the row and comparing owners in Python would be a second place for the rule to live, and a 403 would confirm which instance ids are taken. `owner` is a **required keyword-only** argument on all 14 repository methods and every service passthrough rather than defaulting to `"john"` — a caller that forgets it is a `TypeError` at the call site instead of a silent read of John's ladders. **Review of #190 removed the one exception, and the reviewer had the better argument.** `_ensure_next_rung_alert` and the two SQL constants the scan path executes directly (`SQL_GET_NEXT_PENDING_RUNG`, `SQL_GET_ACTIVE_ALERT_FOR_RUNG`) were left unscoped on the reasoning that they are only ever reached with an id a caller already resolved through a scoped query — true of all four call sites, so there was no live defect. The reasoning was still wrong, and in a way worth recording: it treated the required keyword and the predicate as alternatives when they answer different failures. The keyword catches the caller that forgot to scope; the predicate bounds the blast radius when a caller scopes *incorrectly*, which no signature can catch. And the two outcomes are not symmetric — without the predicate a mismatched `instance_id` writes an alert onto another owner's ladder, where with it the lookup returns nothing and the call no-ops, exactly the shape every other cross-owner access here already takes. Three of the four call sites had `owner` in scope already; the fourth needed one keyword. `tests/test_harvester_service.py` now asserts the containment on the helper directly rather than through a route, so the property survives a caller being rewritten. Routes take **no `?owner=` parameter** — the owner comes from `Depends(require_owner)`, since unlike `/api/portfolio*` there is no legitimate cross-user read path for plans; `/api/symbols/{ticker}/price` reads nothing owned and takes no owner at all, while `/api/symbols` is scoped because its `active_plan_id` must be *this* owner's plan or the UI links to something that 404s. `CLOSED` joins the status vocabulary (`PLAN_STATUSES`, the `Plan` schema `Literal`, the `list_plans` filter, and a Closed toggle on the plans page) — `status` is free-text `TEXT` with no CHECK constraint, so that set is what validates a filter; it is deliberately distinct from `SUPERSEDED`, which means "replaced by a newer ladder", where `CLOSED` will mean "the holding is gone" (Part H8, PR 7). `Notifier(portfolio, owner=…)` defaults to `"john"` so the daily job is unchanged. OpenAPI surface **unchanged at 108 routes** — the gate is route-level, so a new dependency-injected parameter is invisible to it, same as PR 2's response fields and PR 5's query params. 586 frontend tests green (up 1), floors cleared at 90.65 lines / 88.7 statements / 88.7 functions / 72.88 branches and **not raised** (mitigation 2 — Part G ratchets them once). Three-files rule satisfied: `V7__plan_owner.sql`, `_SCHEMA`, and a regenerated `db/schema_snapshot.json` whose diff is exactly the `owner` column, the two-index swap, and `idx_instances_owner_status`. Full backend suite green against the test database: **1422 tests, `OK (skipped=5)`**, 31min (every `build_plan` is a real yfinance fetch, and the seven new isolation cases each build two ladders in `setUp`). **Deploy step:** test is **migrated** — V6 and V7 applied 2026-08-10, and `scripts/schema_check.py` confirms it against live objects rather than the changelog (22 tables, 0 missing, 0 extra, old index gone). **Prod still needs `./scripts/flyway.sh --prod migrate` before `prod-rollout.yml` is dispatched**; `init_schema()` is no longer the safety net on a Flyway-managed database, so the image must not land ahead of the schema. **Two follow-ups, both about the lean install rather than about #147.** `tests/test_generate_portfolio_report.py` imported matplotlib/jinja2/boto3 at module level, which is fine under `requirements-dev.txt` (what `deploy.yml` installs) and a hard import error under `requirements-base.txt` alone (what `prod-rollout.yml` runs `unittest discover` on) — a green PR that fails at the **promotion**, the worst place to learn it. #191 wraps the import and raises `unittest.SkipTest`, tolerating only those three packages so anything else failing to import stays a real failure, and adds `deploy.yml`'s `lean-import` job + `scripts/ci_lean_import_smoke.py` to catch the class at PR time: it installs `requirements-base.txt` into its own runner and *imports* — never runs — `main.py`, `api/main.py`, every MCP wrapper (list read from `scripts/ci_wrapper_smoke.py`, so a new server is covered for free), and every `tests/test_*.py`. It needs a Postgres service despite being import-only, because `api/main.py` builds the app at module level and that calls `ensure_schema()`. #192 then fixed the smoke reporting its own failures as a missing module instead of printing the traceback that said what actually broke |
| 2026-08-11 | 7 | Merged ([#199](https://github.com/JohnFunkCode/StockPortfolioManager/pull/199), `e17d21c`) | Parts H5–H8 — the holding invariant, the Plan chip, the orphan flag, the `V8` backfill. **The invariant runs in both directions, and both directions inject the *repository* rather than the service** — `HarvesterService` takes `portfolio_repository`, `PortfolioService` takes `harvester_repository`. Naming the services would close a construction cycle in `registry.py`, the same shape Part B4 hit with `FundamentalsService`/`WatchlistService` and solved with a late-bound callable; here a repository is enough, because neither side needs the other's analytics. Both stay optional (`None`), so a unit test can build either stack alone — and the absent case is defined rather than incidental: `HarvesterService._open_symbols()` returns **`None`, not an empty set**, because "we cannot tell what they hold" and "they hold nothing" are different answers, and conflating them would refuse every build and flag every plan as an orphan. **Entry (H5).** `build_plan` normalises `symbol.upper().strip()` and then calls `_require_open_lot` **before** the yfinance fetch — refusing after paying for two years of bars is the same answer several seconds later. `RuntimeError`, because `POST /api/plans` already maps it to **422**: the request is well-formed, the portfolio just doesn't support it. The test pins the ordering by construction rather than by reading the code — the gateway it injects raises `AssertionError` on any fetch, so a future reordering fails rather than merely slows. **Exit (H5).** `PortfolioService._close_plan_if_flat` closes the owner's ACTIVE plan to **`CLOSED`** (not `SUPERSEDED` — nothing replaced it) on the three paths that can empty a position: `close_lot`, `delete_lot`, `remove_position`. It runs **after** the write commits, on its own connection, inside a `try/except` that only logs — a harvester outage must not surface as a failed sale, and the sale is the fact while the plan is bookkeeping about it. Two details are the whole difficulty. A **partial** close leaves lots open and therefore leaves the plan ACTIVE; that off-by-one is the difference between finishing a harvest and silently killing a live ladder, and three tests cover it (partial close, second lot still open, full close). And `delete_lot` has to read the lot's symbol **before** deleting it — afterwards there is no row to read it from. `close_active_plan_for_symbol` **appends** to `notes` rather than overwriting (`notes \|\| ' \| ' \|\| reason`), and a `None` reason leaves notes alone: the plan's history is why anyone opens a closed ladder. **The seam is eventually consistent by design**, so H7 is its backstop: `display_all_plans` carries `in_portfolio` per plan, and `/plans` renders a **No position** chip on an ACTIVE row whose shares are gone. Flag only — no filter, no bulk action; closing a live ladder is the owner's call. `undefined` is not `false`: only the list route carries the field, the page flags an explicit `false`, and `None` from the service means the holdings could not be read. **H6.** `GET /api/symbols` gains `active_plan_id`, from a new `HarvesterPlanDB.active_plan_ids()` — deliberately narrower than `list_all_symbols()`, which walks the whole shared `symbols` table to answer a question about the handful of tickers one owner holds; this one is driven by `idx_instances_owner_status` and returns at most one row per ACTIVE plan. Looked up **once per request, not once per row** (a test asserts the call count at two symbols), degrading to `None` rather than failing the table when the harvester is down. The page turns it into a **Plan** chip linking to the ladder, or a quiet *Create plan* affordance where there is none — deliberately not the next rung's price, which would be a second number competing with the return columns. `CreatePlanDialog` takes `initialSymbol` and narrows its picker to the caller's holdings, which makes the usual way to hit the 422 stop existing; the picker is the courtesy and the 422 is the rule. Three things there are easy to get wrong: the dialog stays **mounted while shut**, so `useSecurities` gained an `enabled` flag rather than fetching a symbol list nobody is looking at, and the seed effect has to re-run on `open` or reopening for a different row shows the previous symbol; the field waits on **`isPending`, not `isLoading`**, because a disabled query is never "loading" and treating that as "loaded and empty" flashes the no-holdings notice on every open; and a failed holdings read **degrades to free text** rather than locking the user out — refusing to submit would be the UI enforcing an invariant it has no evidence about, when the server checks anyway. **H8.** `V8__close_orphan_plans.sql` closes the rows that predate the invariant. It is a **data** migration and is therefore deliberately **not** mirrored into `_SCHEMA` or `db/schema_snapshot.json` — the three-files rule is about DDL, and `init_schema()` runs on every startup, so a mirrored backfill would re-run and close plans built moments earlier. `check_schema_snapshot.py` reporting **no change** is the confirmation, not an omission. Read-only pre-flight against the test database (`127.0.0.1:5434/quantcore`): **0 ACTIVE plans, 0 rows V8 would close** — the migration is a no-op there, which is expected and does not test it; prod is where it has anything to do. OpenAPI surface **unchanged at 108 routes** — `in_portfolio` and `active_plan_id` are response fields and the gate is route-level, same as PR 2's bar segments, PR 5's query params, and PR 6's injected dependency. 23 new backend tests across the three modules (5 repository/DB for the two new SQL statements, 8 service, 10 portfolio-service), split on purpose: the DB tests pin **what the SQL does**, the faked service tests pin **when the seam is called**. Targeted run of the three modules: `Ran 100 tests in 1894.569s`, `OK`. Full backend suite `Ran 1446 tests in 2223.385s` with **one failure, unrelated to this PR and pre-existing**: `tests.test_repositories_db.TestOhlcvRepositoryFacade.test_store_and_read_back` expects 5 bars back and gets 4. Neither file it touches is in this branch's diff, and the cause is the calendar, not the change — the fixture pins `bdate_range(end="2026-07-17", periods=5)` while the read asks for `days=30`, so the oldest bar aged out of the window on **2026-08-11**. Underneath it is a real latent defect worth fixing separately: `_query_cache` computes its cutoff as `datetime.utcnow().timestamp()`, and `.timestamp()` on a **naive** datetime interprets it as *local* time — so on a UTC−6 developer machine the cutoff lands 6 hours late, which is what tipped this run over a day before UTC would have. CI runs in UTC and is green today; it fails everywhere from 2026-08-12. Wants its own issue rather than a fix here, to keep this PR's diff about the holding invariant. **Resolved** in [#198](https://github.com/JohnFunkCode/StockPortfolioManager/pull/198) (`a8fb97c`), which merged ahead of this PR: both the cutoff and the `fetch_log.fetched_at` write now go through one `_utc_now()` helper, and the fixture is anchored three business days before today rather than to a fixed date. Two things worth carrying forward — the same naive-`utcnow()` call was *writing* the staleness column, so every comparison against it was off by the writer's offset, not just the read window; and the skew is invisible on a UTC runner, so the guard that had to be added is `test_utc_now_is_timezone_aware` on the helper rather than anything about the window. 598 frontend tests green (up 12), floors cleared at 90.66 lines / 88.73 statements / 88.54 functions / 73.13 branches and **not raised** (mitigation 2 — Part G ratchets them once). **Deploy step:** `./scripts/flyway.sh migrate` on test **before the merge**, and `./scripts/flyway.sh --prod migrate` **before dispatching `prod-rollout.yml`** — `init_schema()` is no longer the safety net on a Flyway-managed database. Run the read-only orphan pre-flight against prod first: V8 closes plans, and it is worth knowing how many before it does |
| 2026-08-11 | 8 | Merged ([#201](https://github.com/JohnFunkCode/StockPortfolioManager/pull/201), `3458491`) — 608 frontend tests green | Part G — `/symbols` removed, the nav grouped, the floors ratcheted once. **G1** deleted `SymbolsPage.tsx` + its test, `symbolsApi.list`, the `useSymbols()` list hook, and the now-orphaned `SymbolInfo` type; `components/symbols/LivePrice.tsx` stays (it backs `PlanDetailPage` and the `live_price` sidekick component), as do both `/api/symbols*` routes — this was front-end-only. **The gotcha is a doc that was wrong before the change:** CLAUDE.md said "`GET /api/symbols` carries `active_plan_id` for the Portfolio page's Plan chip", which reads as though removing the front-end list hook breaks the chip. It doesn't — the chip reads `/api/portfolio/symbols` (`PortfolioService._active_plan_ids`), and the Harvester-era list route independently carries the same field from its own owner-scoped join. Two routes, two lookups, one sentence conflating them; corrected in CLAUDE.md, and worth remembering that "which route feeds this column" is the kind of claim to verify at the consumer rather than at the schema. **G2** groups by **label in first-appearance order** (`buildNavSections`), not by adjacency, so a page appended mid-`routes` joins the existing menu rather than opening a second one with the same name — and to get the plan's menu order out of that, `routes` itself was reordered (Portfolio, Plans, Plan detail, Harvester, Securities, Security detail, Watchlist, Fundamentals, Arbitrage, Settings) instead of adding a parallel ordered group declaration, which keeps each detail route adjacent to its parent at the cost of two existing `navigation.test.tsx` assertions. `buildNavSections` is exported **only** so a test can prove the label-keying: the real array is already sorted, so `navSections` alone cannot tell label-keyed from adjacency-keyed grouping apart. The toolbar moved to `components/layout/NavBar.tsx` because each group owns a menu-anchor `useState` and `App.tsx` should not grow one per group; `App.tsx` is now just `Layout` + `<Routes>`. Active state is **`aria-current`** — `'page'` on links, plain `true` on a group trigger that is not itself the page — chosen over asserting computed CSS: a real a11y signal and a stable assertion target. Two things cost time: clearing the anchor in the `MenuItem` `onClick` is load-bearing (without it the popover sits over the page it just navigated to), and `testUtils.tsx`'s `Providers` is not enough for this component — `useChat()` throws outside `<ChatProvider>`, so `NavBar.test.tsx` copies `App.test.tsx`'s `AppThemeProvider → ChatProvider → MemoryRouter` stack. Floors ratcheted once as the plan reserved (mitigation 2): 89/87/86/70 → **90/88/88/72** against a measured 90.75/88.81/88.65/73.39. Docs: readme's Pages table gained a **Menu** column and lost the Symbols row; CLAUDE.md gained the `navigation.tsx` declaration rules. **Browser check done** (2026-08-11, on a local static serve of `frontend/dist`): at `desktop` and at `tablet` (768) the bar holds the wordmark, `My Positions ▾`, `Research ▾`, top-level `Settings`, and both right-hand `IconButton`s on one line with no wrap and no overflow; both menus open with the right pages (Portfolio/Plans/Harvester and Securities/Watchlist/Fundamentals/Arbitrage), the drill-down `/plans/:id` stays out of them, no `Symbols` entry remains, and `aria-current` marks Portfolio inside its menu with the `My Positions` trigger underlined. **What it found that jsdom could not:** the toolbar's natural width is **547px**, so below a ~550px viewport it overflows — and because the document does not scroll horizontally (`documentElement.scrollWidth == clientWidth`), `Settings` and both icon buttons are *unreachable*, not merely off-screen (at 375: header `scrollWidth` 547 vs `clientWidth` 373, `Settings` left edge at 381). Phone width was never in this plan's scope and the flat seven-button bar would have been worse, so this is filed as a pre-existing responsive gap rather than a Part G regression — but it is a real one, and grouping did not fix it: [#202](https://github.com/JohnFunkCode/StockPortfolioManager/issues/202), which also records that no jsdom test at any coverage level could have caught it. **Gotcha for the next agent that needs a browser:** the preview spawner's sandbox had no read access under `~/Documents` (a rejected over-broad permission prompt), which surfaces as `EPERM … uv_cwd` and then `EPERM … open '…/node_modules/vite/bin/vite.js'` — nothing in `launch.json` can widen that grant, and `cwd` must be repo-relative. The way through was to `vite build` and serve `dist/` with a ~30-line dependency-free `node:http` script from the session scratchpad, outside the denied subtree; a static bundle is enough whenever the question is layout rather than data |

**Corrections folded in with PR 0** (2026-08-10), all of them consequences of
[#165](https://github.com/JohnFunkCode/StockPortfolioManager/issues/165) landing after this plan was
approved:

1. **Part H's migration versions were renumbered `V6`/`V7` → `V7`/`V8`.** #165's PR 3 took `V6` for
   `V6__parity_backfill.sql`. PR 0's CI guard now catches exactly this class of collision.
2. **H8's "already exists, skipping … harmless" paragraph was wrong as written** and is replaced.
   `init_schema()` no longer converges the schema on test or prod, so Part H's DDL genuinely
   executes there and must be migrated ahead of the deploy in both projects.
3. **Part H1's escalation to #165 is closed rather than pending.** The two schema owners can no
   longer disagree on a deployed database, because only one of them executes DDL there. What
   replaces it is narrower: the `QUANTCORE_SCHEMA_MODE=create` escape hatch stops being trivially
   safe once a `DROP` lives in `_SCHEMA`, which makes statement ordering there load-bearing for the
   first time.

### Notes from PR 1 (2026-08-10)

**The dependency split had a second consumer the plan didn't name: CI.** Part F says to remove
matplotlib/jinja2/boto3 from `requirements-base.txt`, which is correct — but `requirements-dev.txt`
is `-r requirements-base.txt` + coverage/diff-cover, and the `gate` job installs *that*. Pulling the
three packages out of the base set therefore also pulled them out of CI, which would have made
`tests/test_generate_portfolio_report.py` unimportable there. Fixed by adding `-r
requirements-report.txt` to `requirements-dev.txt`: CI-only, so no image gains a byte, and the
script that just moved is the last thing that should be untested. Anyone doing a similar split
should check the `-r` graph, not just the file being edited.

**What was deliberately left for later PRs.** Part F's docs bullet is written as though the whole
plan shipped at once — it asks for `/watchlist` and `/fundamentals` in the QuantUI section, the
`WatchlistService` composition, the `scope` parameter, and `quantcore/analytics/returns.py`, none of
which exist until PRs 3–5. Only the true half was written. Likewise **`docs/openapi-surface.txt` was
not regenerated**: PR 1 adds no REST route, so its "one added line" belongs to a later PR, not this
one.

**#147 was commented on but deliberately *not* closed**, against Part F's instruction. #147 is the
tracking issue for all nine PRs and now carries the mitigation-5 reservations; closing it here would
close the tracker two PRs into a nine-PR plan. Its first comment already says as much ("this issue
stays open — it closes with the implementing PRs, not with the plan"). The declined options Part F
wants recorded — the rendered-report archive, and extracting `main.py`'s remaining orchestration
into a service — were written into the comment anyway, so nothing is lost by closing it at PR 8
instead.

**Review caught a documented knob that was dead code.** `FUNDAMENTALS_WARM_BUDGET_SECONDS` was
declared, defaulted, and written into three documents, but `warm_fundamentals_cache()` resolved its
default as `DEFAULT_WARM_BUDGET_SECONDS if budget_seconds is None else …` — never through
`_env_float`. The two *alarm* thresholds a few lines below did read the environment, which is what
made it invisible: the block looked consistent. Because the one production caller
(`run_fundamentals_warming`) passes no budget, setting the env var on the Cloud Run Job would have
changed nothing. The general shape: **a default resolved at the call site is only as good as the
call sites, and the one that matters here passes nothing** — so the resolution belongs inside the
function. Tests now pin all three directions (env honoured, explicit argument wins, and the value
reaching the warmer through the real caller).

The same review flagged `tests/test_generate_portfolio_report.py` asserting
`REPO_ROOT.name == "StockPortfolioManager"`, which ties the suite to the checkout directory name —
it passed in CI only because Actions checks out into a directory named for the repo, and failed in
a checkout at `/tmp/spm-pr178`. Now asserted as a path relationship (`REPO_ROOT` is the script's
parent, and holds `main.py` and `templates/`). Worth remembering that **CI agreeing is not evidence
a path assumption is portable** — CI has the most conventional layout there is.

**Unrelated defect found while reading the fundamentals repository, not fixed here:**
`quantcore/repositories/fundamentals_repository.py` returns the full `QUANTCORE_DB_DSN` as
`db_path` from `stats()` — on both the success path (`:289`) and the error path (`:296`) — and
`FundamentalsService.get_cache_stats()` passes it straight through to the `get_cache_stats` MCP
tool, so a password-bearing DSN is reachable from any AI client holding a token. Out of scope for
this PR; needs its own issue.

**Resolved** as [#179](https://github.com/JohnFunkCode/StockPortfolioManager/issues/179), fixed in
[#180](https://github.com/JohnFunkCode/StockPortfolioManager/pull/180) (`295f5b8`). `quantcore/db.py`
gained `describe_dsn()` → `host:port/database`, and `cache_stats()` returns that as `database` on
both paths; the `db_path` key is gone, having had no consumer. Two things worth carrying forward
from that fix:

1. **The never-log policy didn't cover this case, and now says so.** It was written for logs, about
   API keys and envelopes — so a DSN in a *response* fell outside both halves of what it named.
   `CLAUDE.md` now states that the DSN counts as a credential and that the policy covers API/MCP
   responses, not just log lines.
2. **"No substring of the password" is not a testable assertion against CI's DSN**, where user,
   password and database name are all `quantcore` — a correctly redacted `host:port/quantcore`
   trips it. `tests/test_dsn_redaction.py` therefore checks the password substring offline against
   a synthetic DSN, and `tests/test_repositories_db.py` checks *structure* (no `://`, no `@`, no
   `user:`) against the live one. Any future redaction guard needs the same split.

### Notes from PR 4 (2026-08-10)

**MUI X DataGrid uses `getSortComparator`'s return value verbatim — it does not negate it for
`desc`.** This cost real time and produced a genuine bug, not a test failure. The page needs
nulls-at-the-bottom on three columns (score, USD cap, fundamentals age), because DataGrid's own
number comparator treats `null` as smaller than everything and therefore parks every unpriced or
unscored name at the *top* of a "biggest first" sort, where a blank reads as an answer. The obvious
implementation — return `a - b` and let the grid flip it — is wrong in both halves: the numbers came
out ascending under `desc`, and the nulls, being outside the comparison, ended up first. The fix is
for the comparator to apply the direction itself (`sign * (a - b)`) and pin the nulls with an
unconditional `1`/`-1` *outside* the sign. The general shape worth remembering: a sort hook that
receives the direction as an argument is almost certainly asking you to apply it, not merely to
observe it.

What is uncomfortable is how the bug looked while it was live. The default sort is
`composite_score desc`, so the page opened on a table that was ranked — just backwards, with the
unscored names on top. Nothing threw, nothing logged, and the column header showed the arrow it was
supposed to. **It was the first render of the first test that caught it**, by asserting the symbol
column top-to-bottom rather than that "a sort happened".

**The same column had a second, quieter defect, found in review of #186: the Community DataGrid
silently truncates a multi-item sort model.** The default sort was written as the report's ranking —
`composite_score desc`, then `return_30d desc` — but `disableMultipleColumnsSorting` is a *forced*
prop in the community build, and `sanitizeSortModel` returns `[model[0]]` when the model is longer
than one. The tie-break was never applied, and the only trace was a dev-mode `warnOnce`. Same shape
as the bug above: a plausible declaration that the grid quietly declines to honour. The fix is that
a tie-break belongs *inside* one comparator, not in a second sort item — `scoreThenReturn` compares
the scores, and on a tie reads the other column off the row via `params.api.getRow(params.id)`,
because a comparator is only handed its own column's two values. Note `GridSortCellParams.api` is
typed `any`, so the generic form `getRow<T>(id)` is a TS2347 (untyped calls take no type arguments)
— cast the result instead.

Persisted UI state was the other review finding, and it is the more general lesson:
**`JSON.parse` succeeding tells you nothing about the shape it returned.** Both restores were
wrapped in `try/catch`, which only covers unparseable text; a stored `null` or `{}` in
`watchlist-tag-filter` — a user-writable key — parsed cleanly, became the filter state, and blanked
the page at the first `tagFilter.length`. Validate the shape (`Array.isArray` plus the element
type), and treat "valid JSON, wrong shape" as its own test case rather than assuming the corrupt-
text case covers it.

**A near-duplicate method makes the diff blame you for its neighbour's uncovered lines.** #186
merged with the `gate` job red — `diff-cover` at 81% against an 85% floor — naming
`quantcore/repositories/watchlist_repository.py:202-206`, which is the *tail of `set_currency`*, a
method that landed in #184 and is untouched by this PR. The first instinct, that the reviewer's
diff base was stale, was wrong: `git diff -U0` reports the hunk as `@@ -195,0 +202,22 @@`, because
the new `set_tags` is textually near-identical to `set_currency` and git anchored the insertion
*early* — marking `set_currency`'s tail plus `set_tags`'s head as added, and treating `set_tags`'s
own tail as unchanged context. The line numbers are real, the attribution is an artifact, and the
gate is right to fail either way: neither rollback arm had a test, and `set_currency` had no direct
repository test at all (only a fake in `tests/test_watchlist_service.py`). The lesson is not to
argue with the gate when a diff looks misattributed — write the missing test for whichever body the
lines belong to, since a method worth copying is a method worth covering.

The other thing that run surfaced: **`WatchlistPage.test.tsx`'s tag-editor saves timed out under
full-suite CPU contention while passing in isolation.** The cause was waiting on the MUI dialog's
*removal* at the default 1s timeout — an exit transition, not the behaviour under test. Wait on the
PATCH going out, which settles immediately, and give the transition its own generous timeout
afterwards.

Three smaller traps in the same suite, all of them the kind that make a correct assertion fail:

1. **DataGrid emits `onSortModelChange` during mount normalization**, so `localStorage` already
   holds a sort model before any user click. An assertion that the key is `null` before the click
   fails against the *component*, not the code under test — capture the value before and compare
   after.
2. **MUI's default `sortingOrder` is `['asc','desc',null]`**, so clicking a column that is already
   `desc` yields *unsorted*. That is why the sort cases drive the unsorted `Cap (USD)` column rather
   than the default-sorted `Score` one — two clicks there reach both directions, where two clicks on
   `Score` reach one direction and nothing.
3. **Assert against the formatter's source, not against what the number looks like.**
   `formatMarketCap` drops to one decimal once the scaled figure reaches 100, so a $140B cap renders
   `$140.0B` and not `$140.00B`; `formatPercentRaw` emits no `+` sign. Both were caught by reading
   `formatting.ts` before running, which is cheaper than reading a diff of failures.
