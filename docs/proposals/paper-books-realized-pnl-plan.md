# Books, paper trading and realized P/L: build plan

**Status: plan agreed 2026-11-14; all design questions decided (section 3). Nothing is built.**
Issues: [#431](https://github.com/JohnFunkCode/StockPortfolioManager/issues/431) (books and paper
trading) and [#235](https://github.com/JohnFunkCode/StockPortfolioManager/issues/235) (realized
P/L). This plan also takes in all of
[#221](https://github.com/JohnFunkCode/StockPortfolioManager/issues/221) (stored stops, the
bar-close checker, approach alerts and broker mirroring), part of which #431 phase 2 needs.

The issues hold the design and its reasons. This document holds the merged scope, the decisions
made since, the PR checklist, and (as the work lands) the checkpoint log and gotchas.

## 1. The merged body of work

A **book** is a named, owned container of positions, cash and orders. Each owner has one **real**
book and any number of **paper** books. The Portfolio page shows one book at a time, as stacked
sections on one page (decision 7; Cash and Orders share a section):

| Section | Source issue | What it shows |
|---|---|---|
| Holdings | exists today | open lots, allocation chart, returns, Plan chips, for the selected book |
| Realized | #235 | closed lots: per-symbol realized P/L, expandable sales, bar chart, period filter |
| Performance | #431 phase 3 | time-weighted return and drawdown against SPY; a table comparing books |
| Cash | #431 phase 1 | the book's cash ledger; deposits and withdrawals for the real book |
| Orders | #431 phase 1 | paper orders: pending, filled, cancelled, rejected |

#235 fits inside #431 as the Realized view of whichever book is selected. Its rules carry over
unchanged: period by `sale_trade_date`, rolling Month / Quarter / Year / All (default Year), gross
of fees, `lot_sales` only, USD at each side's trade-date FX rate, a missing rate flagged and never
treated as 1.0.

**In this plan:** #235 in full, and #431 phases 1 (foundations), 2 (paper stops that fill) and 3
(performance).
**Not in this plan:** #431 phase 4 (Harvester on paper books, evaluator and watch triggers, AI
trading) and phase 5 (Alpaca). Section 6 lists what each needs before it can start.

## 2. What the code says today

Checked on `main` at `945319e`.

- **Fractional shares mostly exist.** Lots use `positions.quantity NUMERIC(18,6)` and the REST
  schemas take `Decimal`. `positions.shares INTEGER` is a dead legacy column
  (`PortfolioRepository._legacy_values`). The whole-share places left are Harvester's
  (`plan_instances.shares_initial`, the `plan_rungs` share columns, `int(...)` in
  `HarvesterService`), which only phase 4 needs. So #431's "fractional shares" item shrinks to an
  audit of the UI, CSV import and MCP outputs.
- **Sale history is half-surfaced.** `GET /api/portfolio/sales` and `PATCH /api/portfolio/sales/{id}`
  exist (#266) with their hooks, but no component calls them. No realized-P/L maths exists in
  `quantcore/analytics`.
- **No FX source.** Nothing in `quantcore/` supplies a rate and `positions.fx_rate_at_purchase` is
  never written. Daily book-value snapshots add a second consumer: a real book with a non-USD lot
  needs a rate for every day, not only the trade dates.
- **No stops table and no checker.** #221 is open with no code. Its open questions (how often to
  check, whether a stop closes with its position, whether a trailing level ratchets) are settled
  for this plan in section 3.
- **Splits are unhandled** (#391 is open). Long-run performance numbers can't be trusted until it
  lands; the Performance view says so on screen.
- **The schema has 24 tables.** This plan adds five (`books`, `cash_entries`, `orders`, `stops`,
  `book_value_snapshots`). The count is stated in `CLAUDE.md`, `data.md` and the schema-check log
  line, so each PR that adds a table updates them.

## 3. Decisions

### Carried from the issues

Everything under "Decided" in #235 and "Design decisions" in #431 stands. The ones that shape the
PRs most:

- Real is the default everywhere; a test guards it the way the owner rule is guarded.
- `owner` is required and keyword-only; every statement binds the owner, and now the book.
- The system sets every paper fill. Fill exactly once (`UPDATE … WHERE status = 'pending'`), with
  an idempotency key per order.
- Paper fills are permanent; a mistake is corrected with a visible reversing entry.
- No combined total across real and paper books.
- USD-listed symbols only for paper orders at first.

### Made on 2026-11-13 (John)

1. **Scope:** #235 plus #431 phases 1, 2 and 3.
2. **Order:** books first, so the realized view is written book-aware once.
3. **Real-book cash:** a full cash ledger. John enters an opening balance and each deposit and
   withdrawal; buys and sells write their own entries. The real book's return then includes idle
   cash, the same as a paper book's.

### Made on 2026-11-14 (John)

4. **The checker rides the daily job.** One run after the close does two things: it fills queued
   paper orders at their session's opening price, and it tests every active stop against the
   latest closed daily bar. There is no separate intraday job. What that costs: a queued order's
   fill is recorded in the evening, not minutes after the open, and a real-book breach alert
   arrives after the close.
5. **A paper stop fills at the stop level, not at the bar's close.** When the bar's low reached the
   stop, the sale fills at the stop level; when the stock opened below the stop, it fills at the
   open. **This replaces #431's rule** ("at the close of the bar that broke it"). A real stop order
   fills when price touches it, so a close fill would sell a stock that dipped and recovered above
   its stop, and one that kept falling well below it. That would skew the comparison of stop
   methods, which is what paper books are for.
6. **Intraday checking is a later upgrade, to explore** if after-close alerts on the real book
   turn out to be too late. See section 6.

7. **The Portfolio page is one page of stacked sections, not tabs.** In order: a summary strip
   (book value, unrealized, realized for the last year, return vs SPY) with jump links, then
   Holdings, Realized, Performance, and Cash and orders as one section. This is what #235
   describes, and it shows everything about a book in one scroll. What it costs, accepted: each
   section is narrower than a full tab would be, and Realized and Performance sit below a holdings
   table that is already long.
8. **The real book may go into negative cash, and says so.** A buy larger than the recorded cash
   is saved, and the cash section warns that a deposit is missing. The real book records what
   happened at the broker, so it never refuses a fact. Its return is marked unreliable while cash
   is negative. Paper books keep the no-margin rule and refuse the buy.
9. **Real-book history starts at the opening-balance date.** John picks the date and enters the
   cash held then. Lots opened earlier enter as holdings valued at that day's close. Nothing
   before that date is shown.
10. **All of #221 is in this plan.** Beyond the minimum paper stops need, that adds the
    "approaching the stop" alert at an ATR-relative distance, and the panel for mirroring a stop
    placed at an outside broker. Milestone 4 closes #221. With after-close checking, the approach
    alert is the warning the evening before a likely breach.
11. **A seeded paper book gets its holdings on top of its starting cash.** The book opens with the
    real book's current holdings plus the full starting cash, so its opening value is cash plus
    holdings. Seeding is therefore not an order: see the gotcha in section 7.
12. **Book selector: chips, then a dropdown.** One chip per book, real first, while there are five
    or fewer; the rest collapse into a "More" pulldown.
13. **Realized and Performance keep separate period controls.** A paper book two months old would
    show an almost empty chart under a shared "Year".
14. **The realized chart and table default to realized P/L, best first.** The columns stay
    sortable.
15. **Compare books is its own view, opened from a link in the Performance section.** It has the
    full-size chart of every book against SPY, the comparison table (value, return, vs SPY, max
    drawdown, realized) and its own range control. The Portfolio page stays about one book, and
    the heaviest data on it loads only when asked for. Chosen over a fifth stacked section after
    seeing both mocked up.

16. **The Buy dialog is for paper books only.** A real buy is still recorded with Add Lot, where
    John types the price and date the broker gave. A simulated fill and a real one never share a
    form.
17. **FX ships as its own PR after the USD realized view** (2.3 after 2.2). Until it lands, a
    non-USD sale is listed with the "no FX rate" flag and left out of the totals, which is the
    missing-rate behaviour #235 already requires.
18. **AI read access arrives with Performance** (5.4): one read-only book tool for MCP and
    Sidekick. Until then no MCP tool can see a paper book and the tool count stays at 63.

No proposals are open. A new question that comes up during the build is added here as a numbered
decision with its date, not settled silently in a PR.

## 4. Mock-ups

Shown in the planning sessions on 2026-11-13 and 2026-11-14 (not committed as images): the
Portfolio page as tabs and as stacked sections; the Compare books view against SPY; the paper Buy
dialog; the real book's cash ledger. John chose the stacked layout (decision 7) and the linked Compare books view (decision 15).

What the stacked mock-up fixes for the build:

- The realized table in its section shows symbol, cost basis, proceeds and realized $. Shares
  closed and realized % move into the expanded row with the individual sales.
- Cash entries and orders are one list, newest first, cut to the latest few with "Show all
  entries".
- The Performance section shows this book against SPY; "Compare books" is a link from it.

## 5. Checklist

One box per PR. Each PR carries its own tests and docs, gets one Guppy review, and follows the
three-file schema rule where it touches the schema. Tick the box and add a line to the checkpoint
log when the PR merges.

### Milestone 1: books

- [ ] **1.1 Books schema and the real-by-default rule.**
  `books` table; `book_id` on `positions` (sales reach their book through their lot); the migration
  creates one real book per owner and backfills. Repository methods take an optional book and
  default to the owner's real book. A coverage test fails on SQL that reads a book-scoped table
  without restricting the book. `CLAUDE.md` gains the book rule beside the owner rule.
- [ ] **1.2 Books API and the book selector.**
  `BooksService` and routes to list, create and archive paper books; `book_id` on the portfolio
  routes; the selector (chips, with a "More" pulldown past five books) and the paper banner on the Portfolio page; Add Lot hidden for paper books.
- [ ] **1.3 Fractional-share audit.**
  Confirm fractions survive the Add Lot and Close Lot dialogs, CSV import, and
  `get_symbol_lots` and the other MCP outputs; fix what doesn't. Harvester's whole-share columns
  are left for phase 4 and recorded here.

### Milestone 2: realized P/L (#235)

- [ ] **2.1 Realized P/L backend.**
  `portfolio_math.realized_pnl` and the rolling-window date maths in `quantcore/analytics`; an
  owner- and book-scoped repository read; `GET /api/portfolio/realized?period=&book_id=` declared
  before any path-parameter sibling; wrong-owner and wrong-book tests; `docs/openapi-surface.txt`.
- [ ] **2.2 Realized view.**
  The Realized section, and the summary strip with jump links above Holdings: period pulldown, summary, chart on `allocation_segments`, expandable sales from
  the same payload, the "still held" marker, the empty state, the "excludes fees and tax
  adjustments" line. Registered as a Sidekick component in both registries, with vitest and
  parity cases. Updates the capabilities matrix row.
- [ ] **2.3 FX.**
  Historical FX in `YFinanceGateway`; the prior-close rule in analytics;
  `lot_sales.fx_rate_at_sale`; rates captured when a lot is opened and closed; a backfill script
  for existing non-USD rows; the "no FX rate" flag and count. Closes #235.

### Milestone 3: cash, orders and paper buys

- [ ] **3.1 Cash ledger.**
  `cash_entries` table; opening balance, deposit and withdrawal for the real book; opening and
  closing a lot writes its buy or sell entry in the same transaction; a script that writes the
  entries for existing lots and sales; the Cash and orders section, showing cash entries.
- [ ] **3.2 Review the prior art.**
  Read `JohnFunkCode/alpaca_robot_trading_workflow_experiment` and #222; record in this document
  which ideas the orders design adopts and which it drops. No code.
- [ ] **3.3 Orders and fills.**
  `orders` table with an idempotency key and a `source` column; `OrdersService` with exactly-once
  market fills at the current quote from `batch_quotes`; refusal on a stale quote, a halted
  symbol, a non-USD symbol or not enough cash; off-hours orders queue; orders join the Cash and orders section with their status.
- [ ] **3.4 Queued fills in the daily job.**
  A step that fills queued orders at their session's opening price, placed and budgeted under the
  daily job's ordering rules: clamped to the remaining budget, never raises, and alarms on what
  actually landed.
- [ ] **3.5 Buy and sell on paper.**
  The Buy dialog on the Security page (no stop field yet); selling a paper position from the
  Portfolio page; seeding a paper book from the real book, on top of its starting cash (decision
  11); `[PAPER]` Discord messages.

### Milestone 4: stops (#431 phase 2, and all of #221)

- [ ] **4.1 Stops table and service.**
  `stops` table (owner, book, symbol or lot, fixed or trailing, level, method, source, status);
  `StopsService` and routes; owner and book isolation tests.
- [ ] **4.2 Bar-close checker.**
  A daily-job step over closed bars only (`include_open=False`): ratchet trailing levels, detect
  breaches (the bar's low reached the level), alert for real books with dedup across runs, and for
  paper books place a sell that fills at the stop level, or at the open when the stock opened
  below the stop (decision 5). A stop closes when its position goes flat. Updates #431's text to
  match the new fill rule.
- [ ] **4.3 Stops in the UI.**
  The stop field in the Buy dialog, with the stop card's suggestion loaded only when the field is
  opened; the stop shown on holdings and lot rows; the source of a stop-driven sale shown in the
  Realized view.
- [ ] **4.4 Approach alerts.**
  A Discord alert when a holding closes within an ATR-relative distance of its stop, in the same
  checker step, deduplicated across runs so a stop that stays close alerts once. The distance is
  an env var with a documented default that falls back on an unparseable value.
- [ ] **4.5 The stop panel, with broker mirroring.**
  #221's one panel with two entry paths on the Security page: mirror a stop already placed at an
  outside broker (price, and optionally quantity, order type and brokerage), or create one from
  the stop card's recommendation with its reasoning. Both write the same row; a field records
  whether the order exists at a broker. Real books only for mirroring. Closes #221.

### Milestone 5: performance (#431 phase 3)

- [ ] **5.1 Snapshots and the maths.**
  `book_value_snapshots` table; a daily-job step writing cash plus positions at the close for
  every book that isn't archived; `time_weighted_return` and `max_drawdown` in
  `quantcore/analytics`.
- [ ] **5.2 Real-book history backfill.**
  A script that rebuilds past snapshots from lots, sales, cash entries and closed `ohlcv` bars, and
  reports every day it could not value.
- [ ] **5.3 Performance and Compare views.**
  `GET /api/books/performance`; the Performance section, the return-vs-SPY figure in the summary
  strip, and the Compare books view against SPY; the
  dividend and split caveats on screen; a book-performance Sidekick card in both registries.
- [ ] **5.4 Read access for Sidekick and MCP.**
  A read-only book tool in `READ_ONLY_TOOLS`; an MCP tool with its contract case and the tool
  count moved from 63. Closes #431 phases 1 to 3.

## 6. Later phases: what each needs first

- **Phase 4, Harvester on paper books:** fractional rungs (the whole-share columns in section 2)
  and a `book_id` on `plan_instances`.
- **Phase 4, system triggers:** #227 (scheduled evaluator) and #245 (conditional watches) built.
- **Phase 4, AI trading:** arch-v2 section 8 write gating (confirmation, idempotency key, audit
  record) and a per-book switch. Real books stay unwritable by AI.
- **Phase 5, Alpaca:** #222.
- **Intraday checker (explore; decision 6):** worth taking up if after-close breach alerts on the
  real book prove too late. It would be its own Cloud Scheduler job during market hours, giving
  in-session alerts and queued orders that show minutes after the open. Its costs to weigh then:
  a new Job to deploy and monitor, intraday Yahoo calls for every symbol with a stop (the daily
  job has drawn 429s before), and its own dedup. The stop-level fill rule doesn't change.
- **Trustworthy long-run returns:** #391 (splits), then dividends into the cash ledger.

## 7. Gotchas to watch for

Known before any code is written. Add to this list as the work finds more.

- **Every owner needs a real book, not only the ones the migration saw.** A fresh database and a
  newly granted user have no rows to backfill, so the real book must also be created on first use.
  One real book per owner is a unique partial index, not a convention.
- **The backfill is data, the columns are schema.** The DDL goes in all three schema files; the
  backfill statements follow the `V8__close_orphan_plans.sql` precedent and are not mirrored into
  `_SCHEMA`. The parity test has to pass with that split.
- **`SET NOT NULL` on `book_id` comes after the backfill, in the same migration.** Check the
  migration against the entrypoint's refusal list (`DROP`, `RENAME`, `ALTER … TYPE`) before
  relying on CI to apply it.
- **Paper rows must not reach real consumers.** Harvester, the daily notifications,
  `/watchlist/mine`, `position=mine` on the stop card and the MCP portfolio tools all read
  positions. The real-by-default test in 1.1 is what protects them; it lands before any paper row
  can exist.
- **Seeding is a second way for a paper lot to exist** (decision 11). Seeded lots aren't bought, so
  they bypass the order path, write no cash entry, and need their own `source`. They still need
  an honest price: the same rule as an order (the current quote while the market is open, the next
  open otherwise), never a stale close. Performance must count seeded holdings in the book's
  opening value, or the first day shows a false gain.
- **A real book with negative cash has a wrong return** (decision 8). The Performance section and
  the Compare view must mark it, not plot it as if it were sound.
- **A stacked page loads every section at once.** Today the page makes one request. Each new
  section is its own request, so the page must not gate on all of them: Holdings renders as soon
  as its rows arrive, and each section below shows its own loading and error state. The
  performance series is the heavy one and should load only when its section scrolls into view.
- **The summary strip fills in over three milestones.** Book value and unrealized exist at 1.2,
  realized arrives at 2.2 and return vs SPY at 5.3. A figure that isn't built yet is left out,
  never shown as a zero or a dash that reads as a real value.
- **A new daily-job step changes an ordering that is deliberate.** Steps 3.4, 4.2 and 5.1 each
  need a place in `main.py`'s order and an entry in `docs/architecture/daily-jobs.md`.
- **The owner-isolation coverage test derives per-owner tables from the snapshot.** Each new table
  with an `owner` column or a foreign key into one needs mapped wrong-owner tests in the same PR,
  or that test fails.

## 8. Checkpoint log

| Date | Step | PR | Notes |
|---|---|---|---|
| 2026-11-13 | Plan drafted | none yet | Scope, order and real-book cash decided; P1 to P13 open |
| 2026-11-14 | Checker decided | none yet | Daily job, stop-level fills (replaces #431's close-fill rule), intraday noted as a later upgrade; P6 closed, the other twelve open |
| 2026-11-14 | Layout decided | none yet | Stacked sections (decision 7); P1 closed, eleven open (P2 to P5, P7 to P13) |
| 2026-11-14 | Seven proposals decided | none yet | Decisions 8 to 14. All of #221 is now in scope (milestone 4 grows to five PRs, 21 in total); seeding is on top of starting cash. Open: P3, P9, P12, P13 |
| 2026-11-14 | Compare books placed | none yet | Linked view from Performance (decision 15). Open: P9, P12, P13 |
| 2026-11-14 | Plan agreed | none yet | Decisions 16 to 18; no proposals open. Next: commit this document, then PR 1.1 |
