# Portfolio Check: per-user version (plan)

Status: **steps 1–3 done** (2026-11-17; see the checkpoint log). Decisions marked **D?** are open for John.

## Where it stands

The Research menu has a **Portfolio Check** page (`/portfolio-check`) that frames a pre-built
report for a **made-up sample portfolio**
([quantui.md](../architecture/quantui.md)). It comes from the Market Casting prototype's
`portfolio_check.py`, which reads a holdings CSV, ranks each holding's industry with the Wind
Map's tested rules, and writes a static HTML report. This plan replaces the sample with a report
built from **the logged-in user's own lots**, computed server-side and rendered with QuantUI
components.

A static report can never show real holdings: it is served identically to every user and lives in
git. So the per-user version must go through the API.

## What the prototype computes

Two halves, which change at very different rates:

| Half | Inputs | Changes | Same for every user? |
|---|---|---|---|
| **Market state** | Fama-French 49-industry monthly returns, size and book/market (Ken French library); S&P 500 constituents and share counts; S&P 500 daily closes at two dates (12 months back, skipping the latest month) | monthly | yes |
| **Per-holding decision** | the user's lots (symbol, shares, purchase date, cost basis); latest prices; each symbol's SEC SIC code mapped to an FF49 industry | every request | no |

The decision per holding: the industry's label (`Sell` after two reviews in the bottom group,
`Hold, on watch` after one, `Buy zone` in the top group, else `Hold`), adjusted for the position
and industry size caps, and, when every lot has a date and cost, a tax note (short- vs long-term
gain, and whether waiting for long-term saves more tax than the expected cost of waiting). A
stock-vs-industry-peers measure is shown as a secondary, unconfirmed signal.

QuantUI already has the per-user inputs: `PortfolioService.list_lots(owner, status="OPEN")`
returns the owner's open lots with live prices, and `positions` carries `opened_at` and
`cost_basis_total`, so **tax notes work for every lot that has them** (the broker export the
prototype was tried on had neither).

## Proposed shape (arch-v2)

1. **`quantcore/analytics/portfolio_check.py`**: pure functions, no I/O. Industry labels from the
   49-industry series (`rules.py`'s `ranks`/`label_at`), stock-vs-peers wind, and
   `decide(lots, prices, industry_state, caps, tax_rates)` returning one row per holding with its
   action, `reasons` and tax note. Self-documenting like the arbitrage scorer: every rule that
   fires adds its own reason string.
2. **Market state storage (D1)**, one of:
   - **(a) a committed JSON snapshot** (labels, ranks and winds for the 49 industries, plus S&P 500
     peer caps at the two window dates), refreshed monthly by an operator script and a PR. No
     schema change, reviewable diffs, but someone has to run it.
   - **(b) new tables** (a Flyway migration, `_SCHEMA`, the snapshot: three files) filled by a
     monthly step in the daily Job or a new Job. Automatic, but more moving parts.
   Recommendation: **(a) first**; move to (b) if monthly refresh by hand proves annoying.
   Step 2 built (a) on that recommendation; John can still choose (b), which reuses the same
   builder and validator and only changes where the result is stored.
3. **SIC lookup for holdings outside the S&P 500 (D2).** The prototype asks SEC EDGAR per ticker
   (10 requests/s fair-access limit, a descriptive User-Agent required) and caches it. In QuantUI
   that becomes a gateway (`SecGateway`) behind a cache: a small global table, or the JSON snapshot
   pre-filled for the watchlist. A symbol with no mapping comes back as `Review`, as now.
4. **`PortfolioCheckService`**, constructor-injected in `registry.py`: gathers the owner's open
   lots (through `PortfolioRepository` or a late-bound callable, not by importing
   `PortfolioService`), batched quotes, the market state and SIC codes, then calls the analytics
   once.
5. **`GET /api/portfolio/check`** in `api/routers/portfolio.py`, owner from
   `Depends(require_owner)` (no `?owner=`), one service call deep, errors through
   `route_internal_error`. Tax rates and caps (D3): query parameters with the prototype's defaults
   (32% / 15%, 10% position, 25% industry), or per-user values in `user_settings`.
6. **Frontend**: the page swaps the iframe for native components (summary chips, recommendations
   table, industry ranks), GenUI-compliant per arch-v2 Rules 8–9 (registered in both component
   registries) so the Sidekick can show a holding's check. The sample file, its `SANDBOXED_PAGES`
   entry and the `Alert` go away.
7. **MCP tool (D4, optional)**: a `portfolio_check` tool on the portfolio server, needing a contract
   case in `scripts/mcp_tool_cases.py` and the pinned tool count bumped.

## Rules it must keep

- **Owner isolation (A01).** It reads `positions` only through owner-scoped calls; if it ever
  persists results per user, that table is per-owner and needs wrong-owner tests
  (`test_owner_isolation_coverage.py` will insist).
- **yfinance only through `YFinanceGateway`**; tests stub it (`tests/__init__.py` blocks Yahoo).
- **No new dependency.** The prototype's logic uses pandas, numpy, requests and yfinance, all
  already in `requirements-base.txt`; `openpyxl` (its spreadsheet output) and `lxml` (its
  Wikipedia constituents scrape) stay with the refresh script, not the API.
- **Not financial advice**: keep the prototype's wording, and the page's notice.

## Tests

- **Parity**: the analytics on the prototype's `holdings_example.csv` and a frozen market state give
  the same actions and tax notes as the prototype's report for the same inputs.
- Service tests with stubbed lots and quotes; a route test, including another owner's lots never
  appearing; error-redaction coverage for the new route.
- Vitest for the page (loading / error / success / key values) and registry parity cases.

## Steps (append a checkpoint as each lands)

1. Analytics module + parity tests. **Done 2026-11-17** (checkpoint below).
2. Market-state snapshot + refresh script (D1a), with the SIC map. **Done 2026-11-17**
   (checkpoint below).
3. Service + route + tests. **Done 2026-11-17** (checkpoint below).
4. Native page replacing the sample; registries.
5. Optional MCP tool.

## Review on test

John suggested reviewing the per-user version on the **test** site. That works, but not because
test is less locked down: test and prod run the same image with the same IAP gate, per-user
tokens and CSP (`deploy/cloudrun-services.toml` differs only in the environment label and
URLs). The route reads the signed-in user's lots the same way in both. Two things make test the
right place:

- A branch can go on test without merging (`deploy.yml` dispatched from main with `ref`), and
  prod is untouched until `prod-rollout.yml`. That path refuses a ref that touches a migration,
  which D1 (a) avoids.
- Test has **its own database**. A reviewer's real holdings must be entered there first (by hand
  or the import); prod's lots are not visible to test.

## Checkpoint log

### Step 1: analytics module + parity tests (2026-11-17)

- `quantcore/analytics/portfolio_check.py`: `industry_inputs` (the FF49 tables into monthly
  returns, starting caps and book/market), `relative_log_share` / `wind` / `percentile_ranks` /
  `label_industries` / `industry_state` (the 49-industry labels), `peer_table` and
  `relative_wind` (stock vs S&P 500 industry peers), `decide` (one holding's action, reasons and
  tax note) and `check_portfolio` (the whole report: holdings sorted sells first, plus ideas).
  No I/O: today's date, prices, SIC codes and the market state are all arguments, so the service
  (step 3) owns the clock (`market_date()`) and the data. Output is JSON-ready (no NaN, no numpy
  scalars); ideas' examples are structured, not the prototype's pre-formatted string.
- `tests/test_analytics_portfolio_check.py`, 24 tests. **Parity**: the prototype's own run on its
  made-up `holdings_example.csv`, frozen in `tests/fixtures/portfolio_check/parity.json`, is
  reproduced exactly (order, actions, every reason and tax note word for word, weights, peer winds,
  ideas). The industry state is also recomputed from the raw FF49 tail in the fixture and matches
  the prototype's full-history run. Unit tests cover what the sample doesn't reach: "Sell after"
  a long-term date, tax-loss harvesting, missing cost or date, both size caps, and unclassified
  holdings. Two deliberate mutations (`SELL_P` 0.40→0.45, `MIN_PEERS` 3→2) each fail the parity
  tests.
- `tests/fixtures/portfolio_check/make_fixture.py` regenerates the fixture by running the
  prototype's `main()` unchanged and wrapping the functions it calls, so the expectation is the
  prototype's output, not a second copy of its logic. It needs the prototype and the network; it
  is not part of the test run.

Gotchas from this step:

- **The industry state from 72 months equals the prototype's from 1926 on**, because every wind is
  a difference of the cumulative log-share series. That is what lets the fixture (and a future
  market-state snapshot) carry only the tail of the FF49 tables.
- **A rank read back from JSON is a float**, and the reasons then say "ranks 3.0 of 49".
  `check_portfolio` casts it back to int.
- **Verified locally on Python 3.13 / pandas 3.1.1** (the prototype's environment; there is no
  project venv on that machine). CI pins Python 3.12 / pandas 3.1.0, so CI is the real check of
  the parity tolerances.

### Step 2: market-state file + refresh script (2026-11-17)

- `quantcore/gateways/market_reference_gateway.py`, `MarketReferenceGateway`: Ken French's FF49
  tables and `Siccodes49`, Wikipedia's S&P 500 table (parsed with the standard library's
  `HTMLParser`, because `pandas.read_html` needs `lxml`, which isn't in the base set), and SEC
  EDGAR SIC codes. Parsers are module-level pure functions. Yahoo stays behind `YFinanceGateway`
  (the architecture fence allows no other importer).
- `quantcore/analytics/portfolio_check.py` gains `ff49_for_sic`, `label_forward_edge` (the
  prototype's label event study, which supplies the "Sell industries trailed by about x%" figure),
  `window_dates`, `build_market_state`, `validate_market_state` and `market_state_inputs` (the file
  back into `check_portfolio`'s market arguments, for step 3).
- `scripts/refresh_portfolio_check_market_state.py` fetches everything, builds the state,
  **validates it before writing**, and writes `quantcore/data/portfolio_check_market_state.json`
  (`--check` validates the existing file with no network; `--extra-tickers` pre-classifies
  holdings outside the S&P 500, which is all of D2 this step takes on).
- The file shape: `schema`, `generated`, `industries_asof`, the 49 `industries` rows,
  `sell_edge` (value and period), `window` (dates `a`, `b` and `prices_asof`), the S&P 500
  `peers` (caps at `a`, `b` and now) and `classes` (ticker → FF49 code, SIC, SEC name).
- `tests/test_portfolio_check_market_state.py`, 23 tests (one skipped until the file is
  committed): the parsers on saved text, the gateway through a fake session (no SEC call without a
  User-Agent, calls spaced to SEC's 10/s, a 403 or 429 stops the run while a 404 is just
  unclassified), the Sell edge on a hand-worked case, every validator rejection, a full refresh on
  fake gateways over a synthetic 49-industry, 343-company world whose result drives
  `check_portfolio`, and the script's command line.
- **The ported Sell edge equals the prototype's on the full 1926–2026 history exactly**
  (−0.017856398193547788, difference 0.0), as do the Buy zone, Watch and Hold figures. Checked
  once against `label_backtest.json`; the full history is too large for a fixture, so the test
  pins a hand-worked case instead.

**First real run (2026-11-17)**, about two minutes: 49 industries as of 2026-09 (10 Buy zone, 20
Hold, 6 Watch, 13 Sell), 497 S&P 500 peers, window 2025-11-14 → 2026-10-19, 500 tickers classified
(none unclassified), sell edge −1.79% (1931–2026). Cross-checked against the prototype's run the
same day: identical industry labels and ranks, identical sell edge, and the same 497 peers in the
same industries. Three recent listings (FDXF, HONA, VYLR) had under a year of prices and are left
out, as the prototype does. The SEC contact is not stored in the file. No `--extra-tickers`: the
file is committed, and a handful of non-S&P tickers in it would read as someone's holdings, so
holdings outside the S&P 500 are left to the service to classify (step 3).

Gotchas from this step:

- **The window dates move from request time to refresh time.** The prototype measured stock vs
  peers over the 12 months before the day it ran; the market state fixes them when the file is
  built, so the service must price the owner's stocks at those two dates (`window.a`, `window.b`),
  and the measure is up to a month older than the prototype's.
- **SEC refusals must stop the run.** The prototype treated any failed SEC lookup as "no SIC
  code"; a rate-limited run would then quietly mark hundreds of companies unclassified. The
  gateway raises on 403/429 and only a 404 means unclassified.
- **Check the User-Agent before downloading anything.** The first version only found a missing
  `SEC_USER_AGENT` at its first SEC call, after minutes of Ken French, Wikipedia and Yahoo traffic.

### Step 3: service + route (2026-11-17)

- `quantcore/services/portfolio_check.py`, `PortfolioCheckService.check(owner, params=...)`,
  wired in `registry.py` as `services().portfolio_check`. It gathers and the analytics compute:
  the owner's open lots from **`PortfolioService.list_lots`**, so it adds no SQL and no per-owner
  table (D2's runtime SEC lookup would be the first new I/O, and it isn't built); each held
  stock's close on or before the file's window dates, from one
  `OhlcvRepository.daily_history_for_symbols` read, falling back to `PricesService.get_history`
  only for a stock whose stored history starts after the first date (one stock's failure costs
  only its peer measure, logged); and the market-state file, validated once per process.
- Costs are purchase price × quantity, the basis the portfolio pages use for gain/loss (fees are
  left out); a lot's date is its `trade_date`, else its `purchase_date`. Lots with no live price
  count as zero value, as in the prototype, and are listed in `unpriced`.
- `GET /api/portfolio/check` in `api/routers/portfolio.py`: the owner from `require_owner` (an
  `?owner=` is ignored), the five `CheckParams` as bounded query parameters (D3: query
  parameters with the prototype's defaults, not `user_settings`, for now), one service call deep,
  failures through `route_internal_error`. `PortfolioCheckResponse` in `api/schemas/portfolio.py`;
  the route added to `docs/openapi-surface.txt`.
- Tests: `tests/test_portfolio_check_service.py` (12, stand-ins for the portfolio service, the
  OHLCV repository and the prices service, over step 2's synthetic world: it asks for exactly the
  given owner's open lots, the one-read window prices and both fallbacks, costs/dates/unpriced,
  `Review` for unclassified holdings, parameters reaching the decision, an empty portfolio, the
  state loaded once); `tests/test_portfolio_check_route.py` (5, the router on a bare app:
  principal over `?owner=`, parameters through, 422s, a redacted 500); and a
  `test_portfolio_check` case in `tests/test_error_redaction.py`, which needs the full app and so
  runs only where the database is (CI).

Not verified on this machine (no database): `test_error_redaction.py` and
`scripts/check_openapi_snapshot.py`, both of which build the real app. The surface line was added
by hand in sorted position; CI's snapshot check is the proof.

Gotchas from this step:

- **Stored daily bars are UTC timestamps at midnight ET** (05:00 or 04:00 UTC), so the service
  converts the index to ET before taking dates; a plain `.date()` on a later-in-the-day timestamp
  could land on the wrong trading day.
- **`tests/test_owner_isolation_coverage.py` fails on Windows** with a `UnicodeDecodeError`,
  before and after this change: it reads files with the platform encoding, and `notifier.py` holds
  a character cp1252 can't decode. Run it with `PYTHONUTF8=1` on Windows; Linux CI is unaffected.
- **Locally the route tests mount the router on a bare `FastAPI()`**: importing `api.main` builds
  the app, which calls `ensure_schema()` and needs a database.

## Gotchas found while building the demo

- **The prototype names its output by date** (`reports/portfolio_check_<date>.*`), so a second run on
  the same day silently overwrites the first. The demo sample was generated with its output folder
  pointed elsewhere, to keep a real report from being replaced.
- **SIC codes put some companies in surprising industries**: Corning (SIC 3357, fiber optic wire)
  lands in FF49 *Steel*. That's the SEC's code, not a bug, but users will ask.
- **Both share classes held** (GOOG and GOOGL) appear as two rows in the same industry. The prototype
  de-duplicates share classes only among the S&P 500 peers.
- **A broker export without purchase dates or cost basis** gives a report with no tax notes; the
  prototype also ignores the export's price column and fetches its own prices.
- **Local test runs on Windows**: an old OpenSSL ahead of Git's on `PATH` lacks `-addext`, which fails
  two `frontend/server/proxy.test.mjs` TLS tests; put Git's `mingw64\bin` first.
