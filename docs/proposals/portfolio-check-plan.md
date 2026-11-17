# Portfolio Check: per-user version (plan)

Status: **proposal, not started** (2026-11-12). Decisions marked **D?** are open for John.

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

1. Analytics module + parity tests.
2. Market-state snapshot + refresh script (D1a), with the SIC map.
3. Service + route + tests.
4. Native page replacing the sample; registries.
5. Optional MCP tool.

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
