# Books, paper trading and realized P/L: build plan

**Status: plan agreed 2026-11-14. Milestones 1 and 2 are built, which closes #235. Milestone 3 (cash, orders and paper buys) has started: 3.1 (the cash ledger), 3.2 (the prior-art review), 3.3 (orders and fills), 3.4 (queued fills in the daily job) and 3.5 (buy and sell on paper) are built, which completes it. Milestone 4 (stops) is built: 4.1 (the stops table, service and routes), 4.2 (the bar-close checker), 4.3 (stops in the UI), 4.4 (approach alerts) and 4.5 (the stop panel, with broker mirroring), which closes #221. Milestone 5 (performance) has started: 5.1 (the snapshots and the maths), 5.2 (history backfill and restatement) and 5.3 (the Performance and Compare views) are built, and 5.4 is next. Decisions 19 to 24 were made during the 3.1 build, 31 to 34 during the 3.4 build, 35 to 41 during the 3.5 build, 42 to 47 during the 4.1 build, 48 to 55 during the 4.2 build and 56 to 62 during the 4.3 build, 63 after comparing those with other systems (section 5.15), and John made 64 to 67 on the questions that comparison left open; those made in a build up to 4.3 are John's to confirm or change. 68 to 73 were made during the 4.4 build and 74 to 81 during the 4.5 build, and John confirmed both sets as built (section 3). 82 to 89 were made during the 5.1 build; John confirmed 82 to 84, 86, 87 and 89 as built and revised 85 and 88 to the GIPS practice of restating history, which step 5.2 builds (section 3). 90 to 97 were made during the 5.2 build, and John confirmed them as built. 98 to 105 were made during the 5.3 build and are John's to confirm or change. The checkpoint log in section 8 says what has landed.**
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
  `quantcore/analytics`. (Step 2.1 has since added it.)
- **No FX source.** Nothing in `quantcore/` supplies a rate and `positions.fx_rate_at_purchase` is
  never written. Daily book-value snapshots add a second consumer: a real book with a non-USD lot
  needs a rate for every day, not only the trade dates.
- **No stops table and no checker.** #221 is open with no code. Its open questions (how often to
  check, whether a stop closes with its position, whether a trailing level ratchets) are settled
  for this plan in section 3.
- **Splits are unhandled** (#391 is open). Long-run performance numbers can't be trusted until it
  lands; the Performance view says so on screen.
- **The schema had 24 tables** (27 after step 3.3, 28 after 4.1, 29 after 5.1; 4.2, 4.3, decision 63 and 5.2 add columns only). This plan adds five (`books`, `cash_entries`, `orders`, `stops`,
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
- **A book is not an account.** `positions.account` stays as an account within a book: the
  brokerage account for the real book, and later the mapped Alpaca paper account for a paper
  book. The UI and the docs never use one word for the other (PRs 1.1 and 1.2).
- Every trade records its `source` and a link to what prompted it, from the first row (PR 3.3),
  so attribution works once automated triggers arrive (shown in PRs 4.3 and 5.3).
- A delisted symbol in a paper book is closed by hand at the last good price (PR 3.5).

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
    holdings. Seeding is therefore not an order: see the gotcha in section 7. (As built in 3.5 a seed is stored as an order that costs no cash: decision 36.)
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

### Made on 2026-11-15, during the 3.1 build (Claude; John to confirm or change)

These came up while building the cash ledger. Each is what the code does now. None was asked
first, so each is open to being overturned in review; the cost of changing it later is noted
where it is more than a small edit.

19. **The opening balance is the cash held at the start of its date.** An entry dated on or after
    the opening date counts, and one dated before it does not. This follows decision 9 ("lots
    opened earlier enter as holdings") and is what makes a paper book work: it opens with its
    starting cash on the day it is made, and a buy the same day must count. What it costs: a
    balance copied from a month-end statement is entered with the next day's date.
20. **With no opening balance the real book shows no balance and no entries.** Its lots only take
    cash out, so a sum of them would be a large negative number that means nothing. The section
    asks for an opening balance and says how many entries are waiting for one.
21. **Cash amounts are gross of fees**, the same as realized P/L. The stored fees can't be summed:
    a sale across several lots repeats its fee on every `lot_sales` row, and the remainder of a
    split copies its parent's. A fee can be entered as a withdrawal. Counting fees properly needs
    the fee columns fixed first, which is its own issue.
22. **A trade's cash row is derived from its lot or sale and stored, not typed and not computed at
    read time.** Only a lot whose `acquisition_type` is `BUY` writes one (a transferred or, later,
    a seeded lot cost no cash). A non-USD trade with no FX rate gets a row with no amount: flagged,
    counted, and left out of the balance, never converted at 1.0.
23. **A paper book's opening balance is its starting cash, written when the book is made**, and
    the owner can't type into a paper ledger at all: no opening balance, deposit, withdrawal or
    delete. The three write routes take no `book_id`.
24. **The summary strip shows cash beside the holdings and adds nothing up.** While cash is shown,
    its first figure is labelled "Holdings value", not "Book value". One book value that counts
    both is a server-side figure and arrives with the daily snapshots (5.1). Before 3.1 the plan
    expected the strip's book value to grow to include cash at this step.

### Made on 2026-11-15, during the 3.2 review (Claude; confirmed by John the same day)

These came out of reading the prior art (section 5.8). John confirmed all five when he said to
start 3.3, which builds each of them.

25. **`pending` means queued, and nothing else.** An order placed while the market is open is
    filled or rejected in the same transaction that creates it. Only an order placed outside the
    session is stored as pending. So a crash can never leave an in-hours order half done, and the
    daily job never has to guess whether a pending order was meant for a quote or for an open.
26. **A fill is all or nothing.** There is no partly filled order. The simulator has no order
    book to run out of shares, and a partial fill would add a state that only a broker needs.
27. **A queued order is for one session, and is rejected if it can't fill there.** It stores the
    date of the session it is for (the next NYSE session, holidays skipped). It fills at that
    session's opening price whenever the daily job gets to it, even days late. If the symbol has
    no opening price for that session, the order is rejected with a reason; it does not roll to
    the next day. Rolling it would fill at a price from a day the user never chose, and would hide
    a halted symbol.
28. **Cash and shares are checked when the order fills; the check at placement is a courtesy.**
    Both are read again inside the fill transaction. A queued buy's cost isn't known until the
    open, so it can be accepted in the evening and rejected the next day for too little cash. A
    sell is refused at placement when it is for more than the shares held less the sells already
    queued for that symbol.
29. **A rejection stores a reason from a fixed list** (stale quote, no quote, non-USD symbol, not
    enough cash, not enough shares, no opening price), never an exception's text. The list is
    what the UI shows and what a test can pin, and exception text may not reach a caller.

### Made on 2026-11-15, during the 3.3 build (Claude; confirmed by John the same day)

30. **Orders are their own list under the cash entries, not rows in the same list.** Section 4's
    mock-up has one list. A filled order already appears there as its buy or sell entry, so one
    list would show every fill twice or need a rule for hiding one of the two. The Orders list
    shows what was asked for and what became of it, and no amounts. Changing it later is a
    front-end change only.

### Made on 2026-11-15, during the 3.4 build (Claude; John to confirm or change)

Each is what the code does now. None was asked first, so each is open to being overturned in
review.

31. **A queued order fills at the open of its session's closed daily bar**, not at
    `Quote.regular_open`. Section 7 left this for 3.4 to pick. A quote only carries today's
    open, so it can't serve decision 27's "even days late". A stored bar is keyed by its date,
    so the price does not depend on the night the job ran. What it costs: the price is Yahoo's
    daily-history open, which can differ by a cent or two from the first trade a quote showed
    that morning.
32. **With no bar for its session, an order waits before it is rejected.** Decision 27 says an
    order with no opening price is rejected. Taken literally on the first night, one failed or
    empty Yahoo reply would reject every queued order. So: a closed bar dated after the session
    is proof the symbol did not trade that day, and the order is rejected at once; with no later
    bar it waits, and is rejected on the run three sessions after its own. It still never rolls
    to another session's open. What it costs: a symbol halted all day, or delisted, shows as
    pending for up to three more sessions, with the alarm firing each night.
33. **The step runs second, straight after the notifications, with a 120-second budget.** It is
    a few queries an order, and a fill is something an owner looks for that evening. It could
    have gone last, since a missed fill is made up on any later night at the same price. It was
    put early because the last steps get whatever time is left, which on a slow night is one
    second. What it costs: up to 120 seconds taken from in front of the options capture.
34. **A queued order in a book archived since still fills.** The order was accepted while the
    book was open. The page shows no Cancel in an archived book, so nothing else would take it
    out of `pending`, and the alarm would fire every night. The alternative, cancelling a
    book's queued orders when it is archived, is a change to archiving and was left alone.

### Made on 2026-11-16, during the 3.5 build (Claude; John to confirm or change)

Each is what the code does now. None was asked first, so each is open to being overturned in
review.

35. **An order has a kind: `market`, `seed` or `manual_close`** (`orders.kind`, V18). The column
    is added with a default, so CI applies the migration. The alternative was a new value in
    `source` or `price_source`, and widening either `CHECK` means dropping a constraint, which
    the migrate Job refuses. What it costs: one more column to read when asking what an order
    was.
36. **A seed is stored as an order.** This changes decision 11's wording ("seeding is therefore
    not an order"), not its substance: a seed is still not a purchase, costs no cash and is on
    top of the starting cash. One `seed` buy order is written per real-book symbol, for the
    shares held across its open lots. It is priced by the same rule as any order (the quote in
    market hours, the next session's open otherwise) and fills a lot with
    `acquisition_type = 'SEED'`, which writes no cash row. The reason: a seed asked for outside
    market hours has to wait for a price, and the order queue already gives a session, an
    exactly-once fill and the alarm. What it costs: a seeded book's Orders list starts with one
    row per holding, and a holding that can't be priced (no quote, a non-USD symbol) is a
    rejected order and is left out of the book.
37. **Only a paper book that has had no orders can be seeded, and the whole seed is one
    transaction.** A second seed would double the holdings. The seed is its own route, which the
    New paper book dialog calls after the create, so a failed copy leaves an empty book that
    offers the copy again. What it costs: a book can't be topped up from the real book later.
38. **A symbol can be closed by hand when it has no quote, or its last regular trade is three or
    more sessions old** (`UNQUOTABLE_AFTER_SESSIONS`). The price is the close of the newest
    stored closed daily bar, or the quote's last regular trade when no bar is stored. The owner
    types no price, and the whole position is closed. The trade is dated today. What it costs: a
    symbol halted for a day or two can't be closed by hand, and the price is the last one
    QuantCore stored, which may be older than the last one the market printed.
39. **A manual close is marked by its kind and a note, not by `price_source`.** `price_source`
    stays NULL (decision 35's reason), `quote_time` holds when the price is from, and each sale
    row carries the note "Closed by hand at the last good price, from <date>: no quote".
40. **`[PAPER]` fills go to Discord as one message from the daily job, for the job owner's
    books only.** The api service has no Discord webhook, and giving it one is a secret and
    inventory change in both projects. So the job reads the day's fills after the queued-fills
    step and sends one message, capped at 20 lines. It covers John's paper books only: books are
    private and the channel is shared. `PAPER_DISCORD_WEBHOOK_URL` sends it to its own webhook.
    What it costs: a fill made in market hours reaches Discord that evening, and another owner's
    fills are never posted.
41. **Selling on paper is by symbol, not by lot.** An order sells shares oldest lot first, so
    the Sell button is on the symbol's row and a paper lot still has no actions. The Buy button
    on the Security page shows only for an owner with an open paper book.

### Made on 2026-11-16, during the 4.1 build (Claude; John to confirm or change)

Each is what the code does now. None was asked first, so each is open to being overturned in
review. A change to a column or a `CHECK` after rows exist is a hand-applied migration, so 42,
43 and 46 are the ones worth settling before 4.2.

42. **A stop is set on a symbol in one book, not on a lot.** The checklist said "symbol or lot".
    #221 decided symbol ("lots added later inherit it"), and a paper sell is by symbol too
    (decision 41). One active stop per book and symbol is a unique index. What it costs: two
    lots of one symbol can't have different stops.
43. **Setting a stop on a symbol that has one replaces it, and the old row is kept.** A stop has
    five statuses: `active`, `triggered`, `cancelled`, `replaced` and `closed` (the position
    went flat). Only `active` is live and the rest are final. There is no edit: a new level is
    a new row, so the history of what was set stays whole. What it costs: a symbol whose stop
    is revised often collects rows.
44. **A stop must sit below the last stored close, and a trailing stop starts from it.** The
    reference is the newest closed daily bar QuantCore holds, read with no network call, not a
    live quote. It is the price the checker (decision 4) will test the stop against. The close
    is a trailing stop's first high-water mark, and the owner types no mark. With no stored
    close a fixed stop is taken as typed and a trailing stop is refused. What it costs: during
    the session the price may already be under a stop that is still above yesterday's close,
    or the other way round. The checker settles it that evening.
45. **Every stop stores where its level came from, and `method` has no `CHECK`.** The columns
    are `method`, `proposed_level`, `stop_level`, `reference_price` and `as_of` (#221's
    provenance list, plus the close it was set against). The service checks `method` against
    a list in `analytics.stored_stops`. A database `CHECK` would make each new method to
    compare a hand-applied migration (decision 35's reason). `source` does have one, and its
    list is a trade's without `stop`.
46. **4.1 stores a stop for either kind of book and nothing acts on it.** The table has no
    column yet for "exists at a broker" (4.5) or "last bar checked" (4.2, section 5.8). Both
    are additive, and each belongs with the code that reads it. A stop is not closed when its
    position goes flat until 4.2, as the checklist says. Until then a stop can outlive its
    holding, and nothing reads it.
47. **The level is stored on the row for both kinds, rounded to the tick.** A trailing stop's
    `stop_level` is worked out from `trail_pct` and `high_water_mark` when it is set (and, in
    4.2, when the mark rises). Section 5.8 asks for this. The rounding is a fill price's, so a
    stop and the fill it causes compare exactly.

### Made on 2026-11-16, during the 4.2 build (Claude; John to confirm or change)

Each is what the code does now. None was asked first, so each is open to being overturned in
review. 48, 50 and 51 change what a paper book's results mean or what John is told, so they
are the ones worth a look before 4.3 puts stops on the page.

48. **A stop is first tested against the first session that opens after it was set.** This
    narrows decision 44's "the checker settles it that evening". A stop set at 11:00 is not
    tested against that day's bar, whose low may be from 10:00, before the stop existed. A
    paper fill at the level would then be a sale at a price the stop never saw (section 5.8:
    a past price can't be filled honestly). A stop set before 9:30 on a trading day is tested
    against that day's bar. What it costs: a stop set in the session and crossed later the same
    day is not caught that evening. The next bar catches it, and when the price is still under
    the level that is a fill at the open, below the stop. For the real book it is an alert one
    evening later.
49. **A trailing stop's mark rises to a bar's high, and the bar is tested first against the
    level it started with.** A daily bar doesn't say whether its high or its low came first.
    Testing the low against the level raised by the same bar's high would trigger stops that
    a real trailing order would not have. What it costs: a bar that ran up and then fell
    through its own raised level is settled by the next bar, at that bar's open if it opens
    under the level. The mark starts at a close (decision 44) and rises on highs.
50. **A stop's sale is a `market` sell order with `source = 'stop'`, for the whole position.**
    `source_ref` and the idempotency key are both `stop:<id>`, so a stop can cause one order
    and no more. `price_source` is `session_open` when the stock opened under the level and
    NULL at the level: a `stop_level` value would widen a `CHECK` (decision 35's reason). The
    sale rows carry a note. Only lots opened on or before the bar's session are sold, so a
    late run can't sell a lot at a date before it was bought. A queued sell of the same symbol
    is left alone and is rejected `insufficient_shares` at its session. What it costs: a
    reader tells a stop sale at its level from a manual close by the order's source and kind,
    not by `price_source`.
51. **A real book's stop that was reached is `triggered` and final, and John is told once.**
    Nothing is sold and nothing is recorded as sold. The alert is one Discord message for the
    job owner's real book only (decision 40's reason), owed until Discord accepts it
    (`stops.alerted_at`) and never repeated. What it costs: a holding John decides to keep
    after its stop was reached has no stop until he sets another, and another owner's
    real-book stop is marked with no message until the page shows stops (4.3).
52. **The checker runs third, straight after the queued fills, with its own 120-second
    budget** (`STOP_CHECK_BUDGET_SECONDS`). After the fills because a queued order fills at
    its session's open, before that session's bar can reach a stop. Its own budget, as 5.10
    asked. What it costs: up to another 120 seconds in front of the options capture.
53. **The alarm fires for any active stop not tested against today's bar**, the first night.
    A stop that isn't checked can't fire, so there is no grace period. What it costs: a stop
    on a symbol that has stopped trading alarms every night until it is cancelled or the
    position is closed. A queued order gives up after three sessions (decision 32); a stop
    has nothing to give up to.
54. **An archived book's stops are not checked.** A queued order in an archived book still
    fills (decision 34) because it is for one session. A stop is open-ended, and a book that
    kept selling after it was archived would not be archived. They stay `active` and unread.
55. **A stop is closed when its position goes flat, in the sale's own transaction and on every
    checker run.** `close_lots_on` is the one place a real sale and a paper sale both pass
    through. The checker run covers a deleted lot, an import, and the race section 7 names.
    Until the run, a stop on a lot that was deleted (not sold) is still `active`.

### Made on 2026-11-16, during the 4.3 build (Claude; John to confirm or change)

Each is what the code does now. None was asked first, so each is open to being overturned in
review. 57 and 58 change what a paper book's results mean, so they are the ones worth a look
before a stop is put on a real buy.

56. **The stop a buy asks for is stored on the order, and the stop row is written by the
    fill.** V21 adds five columns for what was asked (`stop_kind`, `stop_level`,
    `stop_trail_pct`, `stop_method`, `stop_proposed_level`) and `stop_set` for what became of
    it. Section 5.8 asked for this order of events: a queued buy has no stop until it fills,
    and a rejected or cancelled one never gets one. What it costs: six columns on `orders`
    that most orders leave empty.
57. **A buy's stop is set against the fill price, not the last stored close.** This is
    decision 44 for a stop that arrives with its position. The fill price is the stop's
    `reference_price` and a trailing stop's first high-water mark, and a fixed level must be
    under it. The stop is dated the moment of the fill, so the checker first tests it against
    the next session to open (decision 48). What it costs: a queued buy that the job fills
    days late is unprotected for those days, and its stop is tested from the session after
    the night it filled, not the one after its own.
58. **A fixed level at or above the fill price is no stop, and the buy still stands.** In
    market hours the price is known before anything is stored, so the order is refused as bad
    input (a 422). A queued buy can't be checked until the open: when it opens at or under
    its level it fills with no stop, `stop_set` is false, and the Orders list and the dialog
    say so. Rejecting it instead would need a new reject reason, which widens a `CHECK`
    (decision 35's reason), and a real stop order placed that way would sell at once. What it
    costs: a position the owner meant to protect is not, until they act on the notice, and
    nothing on the page sets a stop on a holding until 4.5.
59. **A buy with a stop replaces the stop its symbol already has in the book; a buy without
    one leaves it.** That is decision 43, and the new lot is covered by the old stop either
    way (decision 42). The stop's `source` is `manual` and its `source_ref` is `order:<id>`.
60. **The page picks which stop a holding shows.** It reads `GET /api/portfolio/stops` with
    `include_inactive=true` as its own request and shows a symbol its newest stop when that
    one is active, or was reached while one of its lots was already open. The second case is
    how another owner learns their real-book stop was reached (decision 51). The pick
    compares two dates the rows already hold and works nothing out. What it costs: a book's
    whole stop history is read on every page load. A server-side "current stop per holding"
    read would replace it if that grows.
61. **4.3 shows stops and sets them only through a buy.** There is no Cancel and no "set a
    stop" on a holding: both belong to the stop panel (4.5). A lot row shows its symbol's
    live level, since a stop is set on the symbol.
62. **A sale's source is a column in the Realized view's expanded row.** `source`,
    `source_ref` and `order_id` join each sale on the payload. A stop's sale is drawn as a
    chip, because nobody chose that moment. Realized P/L added up by source is 5.3.

### Made on 2026-11-17, after comparing 56 to 62 with other systems (John asked for the alignment)

John asked for decisions 56 to 62 to be checked against what market-leading systems do, and
for each to be brought into line where a norm could be found. Section 5.15 has what was found.
One decision changed:

63. **The stop of a buy that fills at a session's open is live from that session.** This
    changes decision 57's "first tested against the next session to open" for a queued buy.
    Alpaca and Interactive Brokers both make an attached stop active when its entry fills, so
    it can trigger the same day. A buy that fills at the open has the whole day's bar after
    it, so no low in that bar is from before the stop existed, which was decision 48's reason
    for skipping a bar. `stops.first_session` (V22) holds the session. A fill recorded days
    late is tested from its own session, so the days the job missed are made up. A buy
    filled at a live quote in market hours keeps decision 48's rule: a daily bar can't say
    whether its low came before the fill. What it costs: a paper buy at the open can be sold
    by its stop the same day, which the 4.3 build did not do, and one more column on `stops`.

Where no norm was found, the decision stood as built until John decided. He did the same
day: the next block.

### Made on 2026-11-17 (John), on the six questions of section 5.15

John answered each question that had no norm to follow. Four change the code.

- **57 stands for a fill in market hours.** Its stop is first tested against the next
  session to open. A daily bar can't say whether its low came before the fill.
- **62 stands.** Every sale shows its source.

64. **A queued buy that opens at or under the stop it asked for is rejected.** This replaces
    decision 58's "fills with no stop". The reason is `stop_not_below_fill`, and no position
    is opened without its stop. The stop is checked before the cash. A trailing stop too
    small to give a price under the open is rejected the same way. In market hours the
    request is still a 422 with nothing stored. What it costs: V23 widens the
    `reject_reason` `CHECK`, which the migrate Job refuses, so an operator applies it by
    hand on test and on prod before the roll-out. `orders.stop_set` is now only ever NULL or
    true.
65. **A buy's stop still replaces the one its symbol has, and the dialog says so first.**
    Decision 59 with a warning. The stop field reads the book's holding stops when it is
    opened and names the stop that will be replaced.
66. **The server picks the stop a holding shows.** This replaces decision 60.
    `GET /api/portfolio/stops/holdings` answers one stop a symbol at most
    (`analytics.stored_stops.holding_stops`), and the page no longer downloads a book's stop
    history or compares dates. What it costs: one more route (129).
67. **A live stop can be taken off from the Portfolio page now.** This replaces decision
    61's "no Cancel until 4.5". A control beside the stop asks first, then calls the cancel
    route. It is offered in every book, an archived one included, and never on a stop that
    was reached. Setting a stop on a holding is still 4.5.

### Made on 2026-11-17, during the 4.4 build (Claude; confirmed by John the same day)

Each is what the code does now. None was asked first; John confirmed all six as built after
the 4.5 build, 69 (once per approach, with no band) and 71 (too little history is no answer)
included.

68. **Close means the newest bar tested closed within one ATR above the level.** The ATR is a
    14-session Wilder ATR as of that bar (the stop-loss card's period), the level is the one
    after that bar (a trailing stop's raised one), and one is `STOP_APPROACH_ATR_MULTIPLE`'s
    default. Measured on the close, as the step says, not the low: a low within the distance
    that closed well above it is a stock that tested the area and left. What it costs: a
    default nobody has tuned yet. On a stock with a 2% daily range it is a close within about
    2% of the stop.
69. **Once per approach, re-armed when a close moves away.** A stop that stays close is
    announced once. A close further than the distance clears the record and the mark, and
    the next close inside it is announced again. There is no band between the two. What it
    costs: a stock that closes just inside, just outside and just inside again is announced
    twice in three days. A wider band to leave than to enter would stop that, at the price of
    a second setting.
70. **The real book only, the job owner's only.** A paper book's approach is recorded on its
    stop and not sent: the stop sells by itself, and there is nothing for John to do. Another
    owner's is recorded and not sent, for decision 40's reason.
71. **Too little history is no answer.** Under 15 usable bars (a new listing, or a symbol
    with gaps) the ATR can't be measured, and the check leaves the approach as it was rather
    than calling it far. What it costs: a stop on a symbol listed under three weeks ago is
    never announced as close.
72. **One read per stop.** The bars the stop is tested against and the bars the ATR needs come
    from one `closed_bars_since`, which now starts 90 days back. Only the bars after the last
    one checked are tested. What it costs: a longer read from the stored history on every
    check. It fetches no more from Yahoo: `get_history` fetches when the newest closed bar is
    behind, which the shorter read did too.
73. **The approach alert goes after the breach alert, in its own message.** A stop reached
    tonight is in the breach alert and not in this one (a triggered stop is not active). Its
    own message, because the two ask different things of John: one says sell at the broker,
    the other says look. Nearest first.

### Made on 2026-11-17, during the 4.5 build (Claude; confirmed by John the same day)

Each is what the code does now. None was asked first; John confirmed all eight as built once
the PR was open, 76 (a mirror is never synced) and 80 (the panel is its own form) included.

74. **A stop at a broker is a flag and three optional details on the stop row** (V26:
    `at_broker`, `broker_quantity`, `broker_order_type`, `brokerage`), as #221 asked: "store
    that as a field rather than as two tables". `stops_broker_details_rule` lets only a stop at
    a broker carry the details. `ADD COLUMN` only, so CI applies it. A stop that replaces a
    mirrored one does not inherit the mirror. What it costs: four columns most stops leave
    empty.
75. **Only a real book's stop can be at a broker.** A paper book has none. The service refuses
    it (422), because a `CHECK` can't see the book's kind.
76. **A mirrored stop is checked and alerted like any other, and is never synced.** Its level
    must be below the last stored close, the daily job tests it against closed bars, and a
    trailing mirror's mark is QuantCore's own, starting at the last close. QuantCore reads
    nothing from the broker. What it costs: the broker's level and QuantCore's can disagree,
    most of all for a trailing stop, whose broker tracks intraday highs. The owner keeps them
    in line by setting the stop again.
77. **The share count may be fewer than the holding, never more, and is checked only when the
    stop is set.** It is checked against the book's OPEN lots under the book's lock. A later
    partial sale leaves it as typed. What it costs: after a sale, the count can say more shares
    than are held, until the owner sets the stop again.
78. **The order type is `stop`, `stop_limit` or `trailing_stop`, and must suit the kind.** A
    fixed stop is a stop or a stop limit; a trailing stop is a trailing stop. No `CHECK`, for
    `method`'s reason (decision 45). A stop limit's limit price is not stored. What it costs:
    the alert can't say a stop limit may not have filled below its limit.
79. **A mirrored stop's breach alert says the broker's order should have filled, and names
    where it was placed.** The approach alert names it too. The breach message's closing line
    now reads "Nothing was recorded as sold: sell at your broker, or check the fill of a stop
    order placed there", since for a mirrored stop something probably was sold.
80. **The panel is its own form under the stop card, not a button inside the card.** #221's
    2026-10-24 comment asked for "a Set stop button" on #399's card. The card is a registered
    Sidekick component, so a button in it would be a Sidekick gesture under arch-v2 Rule 9
    (both interaction registries, locked and consumed history). The panel is a form, like the
    Buy dialog's stop field, and is not registered; the card stays read-only. It reads the
    card's candidates from the same query, so it adds no stop-loss request. What it costs: the
    suggestion and the action are two boxes, and Sidekick can show the card but can't set a
    stop from it.
81. **The panel offers the real book when it holds the symbol, and every open paper book.**
    Whether a paper book holds the symbol is the server's to say: a save answers "a stop
    needs a holding". Knowing beforehand would mean a holdings read per paper book on the
    Security page. What it costs: a paper book that doesn't hold the symbol is offered, and
    its save is refused with that reason.

### Made on 2026-11-17, during the 5.1 build (Claude; John ruled the same day)

Each is what the 5.1 code does. None was asked first. After comparing them with Alpaca's
portfolio history, Interactive Brokers' TWR method and GIPS, John confirmed 82, 83, 84, 86, 87
and 89 as built and revised 85 and 88: GIPS corrects history and restates the returns, with a
flow on its real date, so QuantCore will too. Step 5.2 builds the revision; until it lands, the
code does what 85 and 88 first said.

What the comparison found, in brief: 82 matches IBKR's daily linked sub-period return and GIPS's
requirement for a time-weighted return (Alpaca reports a simple return from a base value
instead). 83 matches GIPS, which starts measuring after the first contribution, and Alpaca, which
skips a zero first point. 84 matches GIPS's valuing of an in-kind contribution at market value on
the day. Using the last available price (86) is accepted by GIPS when it is the best estimate;
leaving a holding unvalued after seven days has no outside match, and John kept it. 87 and 89 are
internal choices with no outside analogue.

82. **The return is time-weighted, with a flow invested from the start of its session.** A
    session's return is `value / (previous value + flow) - 1`, and the sessions chain. The other
    convention (a flow at the end of the day) divides by the previous value alone, and gives no
    return at all on a book's first day. For a deposit sitting in cash the two agree. What it
    costs: a deposit made late in the day is treated as if it had been invested all day.
83. **A book's first row is the base and has no return of its own.** A paper book's first day
    therefore doesn't count the move from an in-hours fill to that day's close. Counting it
    would need the opening value at the fill prices, which the snapshot (a value at the close)
    doesn't hold. It satisfies the gotcha in section 7: seeded holdings are in the opening
    value, so the first day shows no false gain.
84. **A lot that arrives without cash is a flow worth its value at the session's close**, not
    its entry price. That covers a seeded paper lot and a lot transferred into the real book,
    whose `purchase_price` is a cost basis, not a value on the day. One rule for both, using the
    same close the row uses. What it costs: a seed's move from its fill price to that day's close
    is not in the return.
85. **The cash flow is the change in what was contributed since the previous row** (the
    `contributed` column), not the entries dated since it. A deposit typed in after a row was
    taken, but dated before it, would otherwise show in the next row's cash with no flow, and
    read as a gain. The first draft of this step did exactly that; the test that pinned it is
    what showed it was wrong. What it costs: such a deposit counts on the day it was typed in,
    not the day it is dated, because a stored row is never rewritten (decision 88).

    **Revised by John, 2026-11-17: a late flow is placed on its real date, and the rows from that
    date are restated** (the GIPS practice of correcting history). A deposit or withdrawal, and
    a lot that arrived without cash, count against the session they are dated on, however late
    they are typed in. That also removes the limit the #573 review found, where a transfer typed
    in late read as a gain, because lots have no stored total like `contributed`. Built in 5.2.
86. **A holding is valued at the newest closed bar up to seven days before the session.** An
    earlier close than the session's is valued and counted as stale; no close in the week is
    unpriced, and the book's value is then NULL. The window matches the FX prior-close rule. What
    it costs: a symbol that has stopped trading leaves its book with no value, and an alarm,
    every night until the holding is closed.
87. **The step runs last, after the OHLCV close-out**, and it alarms on any book owed a complete
    row: missing, incomplete or stale. After the close-out, most holdings' closes are stored, so
    the step costs few downloads; the earlier steps would otherwise pay its Yahoo calls. What it
    costs: on a night the earlier steps spend the deadline, it gets one second and the books
    have a hole that night. The chain carries over a hole (decision 85's flow covers the gap).
88. **A row is written once, for the session that has just closed, and never rewritten by a
    later edit.** Editing a lot or a cash entry changes no stored row; a re-run on the same
    session replaces that session's row. Rebuilding past rows is step 5.2's script.

    **Revised by John, 2026-11-17: a change to history restates the rows it affects** (GIPS).
    A write that changes a book's cash or lots with a date on or before its newest row marks the
    book to be restated from that date, and the daily job rebuilds those rows before it takes
    the session's own. A rebuilt row says when it was rebuilt, so a correction is visible, as
    GIPS requires it to be disclosed. Built in 5.2; the design is in that step.
89. **No route and no screen yet.** The strip's one book value counting cash and holdings
    (decision 24) arrives with 5.3, which reads these rows. 5.1 adds no route, so the OpenAPI
    surface and the capabilities matrix's operation counts are unchanged.

    **John, 2026-11-17: confirmed, and the route and screen must be planned.** They are step 5.3:
    `GET /api/books/performance`, the Performance section, the strip's return against SPY and
    the Compare view. 5.4 adds Sidekick and MCP read access.

### Made on 2026-11-17, during the 5.2 build (Claude; confirmed by John the same day)

Each is what the 5.2 code does. None was asked first. John confirmed all eight as built on
2026-11-17.

90. **A restatement never reaches back past the book's first stored row.** It starts at the
    later of the mark and that row. A change dated earlier only changes the first row, which is
    the base and has no return of its own (decision 83). Extending the series further back is the
    backfill script's job, run on purpose. What it costs: after a late edit dated before the
    first row, the history still starts where it did until someone runs the backfill.
91. **A rebuild fills a missing session in its range.** A night the job missed becomes a row,
    written as new (no `restated_at`), since it corrects nothing that was reported. What it
    costs: a hole's value is reconstructed from stored bars rather than taken on the night.
92. **`restated_at` is set only on a row whose values changed.** One that comes out the same is
    left alone, `taken_at` and all, so a correction shows only where there was one. A replaced
    row keeps its `taken_at`.
93. **The rebuild holds the book's row lock.** The plan's conditional clear (`WHERE restate_from
    = <the date it read>`) alone misses a write made during the rebuild and dated on or after the
    mark: `LEAST` leaves the mark where it is, so the clear succeeds and that write's change is
    lost. The mark is an update of the book's row in the writer's transaction, so under the lock
    a write either committed before the rebuild read anything or waits and marks again. The
    conditional clear is kept as well. What it costs: the price and FX fetches happen while the
    lock is held, and an owner's write to that book (or a paper order on it) waits for the
    rebuild, at night, for seconds.
94. **A rebuild whose price or FX fetch fails writes nothing and leaves the mark.** The nightly
    row counts a failed FX lookup as missing and moves on, but a rebuild that did the same would
    clear the mark with rows left wrong, and nothing would retry it. What it costs: a currency
    whose lookup keeps failing keeps its book marked, and the alarm names it every night.
95. **What a write marks from.** A lot write: the lot's trade date (else its purchase date),
    before and after an edit, so a date moved later restates from the earlier. A sale: its
    date. A filled FX rate: its lot's trade date, also for a sale's rate (earlier than it needs
    to be, which only costs a longer rebuild). A removed symbol and a CSV import: the earliest
    lot either side. The cash backfill script: the book's earliest ledger row, since its
    statements don't return what they wrote. A paper order's fill and seeding go through the
    `_on` methods, which never mark.
96. **A lot a rebuild can't place in time is left out of every session, and counted** (no trade
    or purchase date, or closed with no sale date). A closed lot is held until its `lot_sales`
    date, else the legacy `sale_date`.
97. **The daily step restates a marked book instead of snapshotting it.** The rebuild runs
    through today, so it takes today's row too. A marked book's reason is `restate` whatever its
    row for today says, and the alarm names it while it stays marked.

### Made on 2026-11-18, during the 5.3 build (Claude; John to confirm or change)

Each is what the 5.3 code does. None was asked first.

98. **The period is the Realized view's rolling window, and the row before it is the base.**
    `month`, `quarter`, `year` and `all` are `market_time.rolling_window`'s last 30, 90 and 365
    days, or everything, so the Performance and Realized sections mean the same thing by a word.
    The stored row just before the window's start is read as the base (`previous_snapshot`), so
    the return covers the whole window rather than starting a session late. Only the window's own
    rows are counted for gaps, restatement and negative cash. What it costs: a window that starts
    before the book's first row starts at that row, and the range shown says so.
99. **SPY is a price return from its closed daily bars, and a failed read leaves it out.** Each
    session takes SPY's close the way a holding's is taken (`close_as_of`, seven days back), so
    neither side counts dividends. The bars come through `PricesService.closed_bars_since`, which
    fetches only when they are behind; an exception is logged by class and the payload says
    `benchmark_available: false`, never failing the book's own figures. What it costs: a
    comparison that leaves out SPY's dividend yield as it leaves out the book's, said on screen.
100. **"vs SPY" is the simple difference of the two returns**, in fractions of the same window,
    not an excess return compounded or annualized. What it costs: over a long window it is not
    the same as the ratio of the two growths, but it reads the way the figures above it read.
101. **The strip and the Performance section share one request, and nothing is lazy-loaded.** The
    strip's "Return, last year" and the section's default period are the same `year` read, which
    TanStack Query caches under one key, so the page makes one performance request, not two. The
    gotcha in section 7 asked for the series to load only on scroll; the strip needs the figure at
    the top, so a lazy load would have made a second request anyway. The section still has its
    own loading and error state and never gates Holdings.
102. **Compare is its own route, `/books/compare`, reading each book separately.** It is a
    drill-down (`nav: false`) opened from the Performance section, with its own range control.
    `compare_books` reads each open book with the same owner- and book-bound statements as
    `book_performance`, and reads SPY's bars once, from the earliest first row of any of them.
    There is no total across books. What it costs: one read a book; a few books, so a few reads.
103. **Attribution is realized P/L by `lot_sales.source`, over the same window.**
    `portfolio_math.realized_by_source` groups the window's sales; a sale with no source counts
    as `manual`, as the Realized view shows it. Best first. Unrealized gain is not attributed:
    it has no source until it is sold.
104. **The `book_performance` Sidekick card takes no props** (#126 decision 20). It shows the
    caller's real book over the default year, with its own period control and no Compare link,
    so the model can never name an owner, a book or a period.
105. **An owner with no real book yet gets a stand-in, never a new book.** The real book is made
    by its first write, and a read must not make it, so both routes describe it (`book_id`
    null, "Real") and read it by default. Its series is empty and the section says how its
    history starts.

No other proposals are open. A new question that comes up during the build is added here as a numbered
decision with its date, not settled silently in a PR.

The proposals were numbered P1 to P13 while they were open. Each closed as a decision:

| Proposal | Decision | | Proposal | Decision |
|---|---|---|---|---|
| P1 page structure | 7 | | P8 history start | 9 |
| P2 book selector | 12 | | P9 Buy dialog | 16 |
| P3 Compare books | 15 | | P10 scope of #221 | 10 |
| P4 period control | 13 | | P11 seeding | 11 |
| P5 realized sort | 14 | | P12 FX timing | 17 |
| P6 checker cadence | 4 (5 and 6 came out of the same discussion) | | P13 MCP tools | 18 |
| P7 negative cash | 8 | | | |

Decisions 1 to 3 answered the first three scoping questions and had no P-number.

## 4. Mock-ups

Shown in the planning sessions on 2026-11-13 and 2026-11-14 (not committed as images): the
Portfolio page as tabs and as stacked sections; the Compare books view against SPY; the paper Buy
dialog; the real book's cash ledger. John chose the stacked layout (decision 7) and the linked
Compare books view (decision 15).

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

- [x] **1.1 Books schema and the real-by-default rule.**
  `books` table; `book_id` on `positions` (sales reach their book through their lot); the migration
  creates one real book per owner and backfills. Repository methods take an optional book and
  default to the owner's real book. A coverage test fails on SQL that reads a book-scoped table
  without restricting the book. `CLAUDE.md` gains the book rule beside the owner rule.
- [x] **1.2 Books API and the book selector.**
  `BooksService` and routes to list, create and archive paper books; `book_id` on the portfolio
  routes; the selector (chips, with a "More" pulldown past five books) and the paper banner; a
  lot's account shown as a field inside its book, never as a second kind of book on the Portfolio page; Add Lot hidden for paper books.
- [x] **1.3 Fractional-share audit.**
  Confirm fractions survive the Add Lot and Close Lot dialogs, CSV import, and
  `get_symbol_lots` and the other MCP outputs; fix what doesn't. Harvester's whole-share columns
  are left for phase 4 and recorded here. What the audit found is in section 5.1.
- [x] **1.4 Complexity follow-up from the 1.3 review.**
  A refactor with no behaviour change, in its own PR. The #548 review measured three functions
  that 1.3 touched by one line each as over or at the complexity threshold: `create_portfolio_html`
  in `scripts/generate_portfolio_report.py` (cyclomatic 32) and in `html_summary.py` (15), and
  `Notifier.calculate_and_send_notifications` in `notifier.py` (17). Extract the per-stock row
  builder from both report functions, and the underwater-lot alert block from the notifier. The
  existing tests (`tests/test_generate_portfolio_report.py`, `tests/test_notifier.py`) must pass
  unchanged; add a direct test for each extracted helper. Nothing in milestone 2 depends on it.
  What it measured is in section 5.2.

#### 5.1 What the fractional-share audit found (1.3)

Storage is `NUMERIC(18,6)` for `positions.quantity` and `lot_sales.shares_sold`, so "survives"
means six decimals in and six decimals back.

| Path | Result |
|---|---|
| Add Lot dialog, lot edit, Close Lot dialog | Already correct (`step: 'any'`, `parseFloat`). Now pinned by tests |
| Add Security dialog, Portfolio tab | **Broken, fixed.** `parseInt` and `step: 1`: 2.5 shares were sent as 2, and 0.5 as 0, which the server refused |
| Add to Portfolio on the Security page | Sent the fraction; the input had the browser's whole-number step. Now `step: 'any'` |
| Share counts on screen (`formatShares`) | **Broken, fixed.** Four decimals for a six-decimal value: 0.00001 shares read "0" |
| Watchlist Qty column | **Broken, fixed.** The default `toLocaleString` keeps three decimals: 0.0625 read "0.063" |
| Stop card and Dashboard share counts | Printed the raw number. Now `formatShares` |
| REST lot routes and `/portfolio/sales` | Correct: `Decimal` in, JSON number out |
| A sale or lot edit finer than six decimals | **Broken, fixed.** The column rounded each side of a split on its own, so 0.3333335 sold from 1 share left 0.666667: 1.000001 shares in total. An edit to 0.0000004 passed the "positive" check and stored 0. The service now rounds once, first |
| CSV import (`import_csv`) | Correct. Now pinned by a test |
| Legacy CSV readers (`Portfolio.read_stocks_from_csv`, `html_summary.py`, `simple_text_summary.py`) | **Broken, fixed.** `int()` raised on "0.5" and the reader returned an empty portfolio |
| `get_portfolio`, `get_symbol_lots`, `get_symbol_sales`, `get_portfolio_summary` (MCP) | Correct: the REST payload passes through. The contract cases now carry fractions |
| Sidekick's lots tool and `SymbolLotsCard` | Correct (`_plain` gives a float; the card uses `formatShares`) |
| `get_stop_loss_analysis` (MCP) and `GET /securities/{ticker}/stop-loss` | **Broken, fixed.** `shares` was an `int`, so the half share `get_symbol_lots` reports was a 422. `position=mine` already handled fractions |
| Loss alert in Discord, the HTML report | Showed the column's padding ("x 10.000000"). Now trimmed |

**Left whole-share, for phase 4:** `plan_instances.shares_initial`, `plan_rungs.shares_before`,
`shares_sold_planned`, `shares_after_planned` and `shares_sold_actual` (all `INTEGER`);
`ExecuteRungRequest.shares_sold` and the `int(...)` casts in `HarvesterService` and
`api/routers/rungs.py`; and the ladder search in `experiments/HarvesterExperiment.py`, which walks
whole share counts from 1 to `max_s0`. A plan's share counts are whole numbers
whatever the holding is. `positions.shares` is a dead `INTEGER` column that stays NULL for a fractional lot.

#### 5.2 What the complexity follow-up measured (1.4)

radon 6.0.1 for cyclomatic complexity and complexipy 9.0.0 for cognitive complexity, the review's
goals being 10 and 15. Neither is a project dependency; both were run from a scratch virtualenv.

| Unit | Cyclomatic | Cognitive |
|---|---|---|
| `create_portfolio_html` in `scripts/generate_portfolio_report.py` | 32 → 4 | 59 → 1 |
| `create_portfolio_html` in `html_summary.py` | 15 → 10 | 18 → 7 |
| `Notifier.calculate_and_send_notifications` | 17 → 10 | 22 → 13 |

The new helpers: `_portfolio_row` (4, 4), `_watchlist_row` (2, 1), `_or_na`, `_amount_or_na` and
`_percentage_or_na` (2, 1 each) in the report script; `_stock_row` (4, 3) and `_stock_row_html`
(3, 3) in `html_summary.py`; `Notifier._send_loss_alert` (8, 6).

The old and new report functions were also run side by side on 64 stocks (every mix of a missing
price, a zero, positive or negative metric, an earnings date and a same-day purchase): the row
dictionaries were equal and `html_summary.py`'s page was byte-identical.

### Milestone 2: realized P/L (#235)

- [x] **2.1 Realized P/L backend.**
  `portfolio_math.realized_pnl` and the rolling-window date maths in `quantcore/analytics`; an
  owner- and book-scoped repository read; `GET /api/portfolio/realized?period=&book_id=` declared
  before any path-parameter sibling; wrong-owner and wrong-book tests; `docs/openapi-surface.txt`.
  The payload's shape is in section 5.3.
- [x] **2.2 Realized view.**
  The Realized section, and the summary strip with jump links above Holdings: period pulldown, summary, chart on `allocation_segments`, expandable sales from
  the same payload, the "still held" marker, the empty state, the "excludes fees and tax
  adjustments" line. Registered as a Sidekick component in both registries, with vitest and
  parity cases. Updates the capabilities matrix row. What was built, and what was left for later
  steps, is in section 5.4.
- [x] **2.3 FX.**
  Historical FX in `YFinanceGateway`; the prior-close rule in analytics;
  `lot_sales.fx_rate_at_sale`; rates captured when a lot is opened and closed; a backfill script
  for existing non-USD rows; the "no FX rate" flag and count. Closes #235. What was built, and
  what an operator still has to run, is in section 5.5.
- [x] **2.4 Complexity follow-up from the 2.1 review.**
  A refactor with no behaviour change, in its own PR, as 1.4 was. The #552 review measured six
  units that 2.1 did not change as over the cyclomatic goal of 10 (radon 6.0.1):
  `portfolio_math.allocate` (12); `_row_to_dict` in `portfolio_repository.py` (14);
  `_validate_lot_fields` (13) and `_normalize_row` (11) in `quantcore/services/portfolio.py`; and
  `_child_ok` (15) and `_check_level` (12) in `tests/test_book_isolation_coverage.py`. Measure cognitive complexity too
  before starting: complexipy failed on `quantcore/services/portfolio.py` and
  `tests/test_book_isolation_coverage.py` in that review, so those scores are unknown. The
  existing tests must pass unchanged; add a direct test for each extracted helper. Nothing in
  2.2, 2.3 or milestone 3 depends on it, so it can be done at any point. What it measured is in
  section 5.6.

#### 5.3 The realized payload (2.1), for the view in 2.2

`GET /api/portfolio/realized` returns `period`, `start_date`, `end_date`, `symbols` and `totals`.

| Where | Fields |
|---|---|
| `totals` | `symbol_count`, `sale_count`, `fx_missing_count`, `basis_missing_count`, `cost_basis`, `proceeds`, `realized_pnl`, `realized_pnl_pct` |
| each of `symbols` | the same without `symbol_count`, plus `symbol`, `still_held`, `shares_closed`, `bar_base`, `bar_gain`, `bar_loss` and `sales` |
| each of `sales` | `sale_id`, `lot_id`, `symbol`, `currency`, `account`, `trade_date` (the purchase), `sale_trade_date`, `shares_sold`, `purchase_price`, `sale_price`, the USD `cost_basis`, `proceeds`, `realized_pnl`, `realized_pnl_pct`, the three `*_native` figures, both `fx_rate_*`, `fx_missing`, `basis_missing`, `allocation_method`, `notes` |

The money fields are `null` when nothing could be summed, never zero. Symbols come best realized
P/L first (decision 14), and each symbol's sales oldest first. The rules behind the numbers are in
[`data.md`](../architecture/data.md#realized-pl).

#### 5.4 The Realized view as built (2.2)

| Piece | Where | Notes |
|---|---|---|
| Realized section | `RealizedPnl.tsx` | One request feeds the summary, chart, table and expanded sales. Table columns are symbol, cost basis, proceeds and realized $ (section 4); shares closed and realized % are in the expanded row |
| Period pulldown | in `RealizedPnl` | Month, Quarter, Year, All; opens on Year. Its own state (decision 13): not shared, not in the URL |
| Chart | `PortfolioAllocationChart`, `variant="realized"` | Same bars and segments. It keeps the payload's order, best realized first (decision 14) |
| Summary strip | `BookSummaryStrip.tsx` | Book value, unrealized, realized for the last year, and jump links to Holdings and Realized. Return vs SPY is left out until 5.3, which adds it |
| Sidekick card | `realized_pnl`, no props | `RealizedPnlCard` in both registries. Always the real book |

Left for later steps, on purpose:

- **A sale's reason is shown but can't be edited here.** `useUpdateSale` still has no caller. The
  capabilities matrix lists it as the remaining gap.
- **Sidekick can show realized P/L but not reason about it.** There is no data tool for it, and
  the tool description tells the model to show the card and quote no figure of its own. The
  read-only book tool is step 5.4.
- **The source of a sale** (stop, manual, and so on) joins the expanded row in 4.3.
- **The holdings totals row (`PortfolioSummary`) stays inside Holdings.** Until the cash ledger
  (3.1), the strip's book value is the same number as "Total Current Value" below it.

#### 5.5 FX as built (2.3)

| Piece | Where | Notes |
|---|---|---|
| Sale-side rate column | `V15__lot_sales_fx_rate.sql`, `_SCHEMA`, the snapshot | `lot_sales.fx_rate_at_sale NUMERIC(18,8)`, nullable. Plain `ADD COLUMN`, so CI applies it. The table count stays 25 |
| FX source | `YFinanceGateway.fx_daily_closes` | Daily closes of `<CCY>USD=X`, USD per unit, under the download lock, 8-second timeout, three-letter codes only |
| Prior-close rule | `quantcore/analytics/fx.py` | The day's close or the most recent one before it, at most 7 days back, never a later one |
| Capture on write | `PortfolioService._fx_rates` | Add Lot, CSV import, a lot edit that moves the date or currency, and close. One request per currency. Never raises |
| Backfill | `PortfolioService.backfill_fx_rates`, `scripts/backfill_fx_rates.py` | Per owner, real books, dry run by default, fills only NULL rates |
| The view | `RealizedPnl.tsx` | A converted sale shows its native figure and both rates under the dollar figure. The "no FX rate" chip and count were already there from 2.2 |

The rules are in [`data.md`](../architecture/data.md#fx-rates).

**Still to run, by an operator, after this reaches each environment:**
`python scripts/backfill_fx_rates.py` (dry run), then `--apply`; for prod, through the proxy with
`--allow-prod`. Until then every existing non-USD sale keeps its "no FX rate" flag. Prod's
positions were empty when this was written, so the prod run may find nothing.

#### 5.6 What the complexity follow-up measured (2.4)

radon 6.0.1 for cyclomatic complexity and complexipy 9.0.0 for cognitive complexity, the goals
being 10 and 15, run from a scratch virtualenv as in 1.4. complexipy read all four files this
time, so the two scores the #552 review could not get are in the table.

| Unit | Cyclomatic | Cognitive |
|---|---|---|
| `portfolio_math.allocate` | 12 → 6 | 12 → 3 |
| `_row_to_dict` in `portfolio_repository.py` | 14 → 1 | 13 → 0 |
| `_validate_lot_fields` in `quantcore/services/portfolio.py` | 13 → 7 | 16 → 6 |
| `_normalize_row` in `quantcore/services/portfolio.py` | 11 → 2 | 9 → 1 |
| `_child_ok` in `tests/test_book_isolation_coverage.py` | 15 → 3 | 13 → 1 |
| `_check_level` in `tests/test_book_isolation_coverage.py` | 12 → 6 | 10 → 3 |

`_validate_lot_fields` was the only one over the cognitive goal (16).

The new helpers, as (cyclomatic, cognitive): `_ordered_lots` (1, 0) and `_take_in_order` (4, 4)
in `portfolio_math`; `_text` and `_or_none` (2, 1 each) in both the repository and the service,
and `_int_or_none` (2, 1) in the repository; `_require_positive` (3, 2) and `_require_iso_date`
(2, 1) in the service; `_joins_restricted_parent` (4, 1), `_restricts_its_parents` (4, 1),
`_in_subqueries_on` (8, 6) and `_table_problems` (7, 7) in the book scan. No unit in the four
files is above the cyclomatic goal now.

**The cognitive figures depend on the complexipy version** (#556 review). The table above is
9.0.0. The review's preflight runs 8.0.1, which scores some units differently, so both were
measured on the same code:

| Unit | complexipy 9.0.0 | complexipy 8.0.1 |
|---|---|---|
| `allocate` | 3 | 5 |
| `_child_ok` | 1 (was 13) | 2 (was 20) |
| `_check_level` | 3 (was 10) | 5 (was 14) |
| `_joins_restricted_parent`, `_restricts_its_parents` | 1 each | 3 each |
| `_in_subqueries_on` | 6 | 9 |
| `_table_problems` | 7 | 9 |
| `_text` (both copies) | 1 | 0 |

`_row_to_dict`, `_validate_lot_fields`, `_normalize_row` and the other helpers score the same in
both. Every unit this step changed or added is within the goal of 15 under either version.

8.0.1 does read `tests/test_book_isolation_coverage.py`: it prints a score for every unit in it.
But it exits 1 on that file, because it scores `_restricted_names` at 16, one over the goal
(9.0.0 says 12; radon says cyclomatic 10). That exit status is the likely reason three reviews
reported that complexipy "failed" on the file, though the review's preflight was not available
to confirm it. `_restricted_names` was not changed by this step or by the review that listed the
six units, so it was left as it was in #556 and done in its own PR straight after:

| Unit | Cyclomatic | complexipy 9.0.0 | complexipy 8.0.1 |
|---|---|---|---|
| `_restricted_names` | 10 → 4 | 12 → 0 | 16 → 2 |
| `_is_book_subquery` | 4 → 5 | 2 → 3 | 3 → 3 |
| `_alias_for` (new) | 4 | 4 | 4 |
| `_book_qualifiers` (new) | 6 | 1 | 4 |

`_is_book_subquery` gained one branch: it now answers False for a subquery the level doesn't
hold, a check its one caller used to make. With that, 8.0.1 exits 0 on the file. The old and new
scans were run side by side on the same 230 SQL literals and the fixtures, comparing
`check_statement` on each statement and `_restricted_names` on each of its query levels.

The old and new versions were also run side by side, the old ones loaded from `HEAD`: `allocate`
on 20,000 generated lot sets (every method, an unknown one, closed and empty lots, over-sells and
bad manual pairs), `_row_to_dict` on 30,000 rows, `_normalize_row` on 40,000 raw dicts and
`_validate_lot_fields` on the same dicts with and without `require_all`, and `check_statement` on
all 230 SQL literals in the repository plus the scan's own accept and reject fixtures. Results,
key order and error messages were equal in every case.

### Milestone 3: cash, orders and paper buys

- [x] **3.1 Cash ledger.**
  `cash_entries` table; opening balance, deposit and withdrawal for the real book; opening and
  closing a lot writes its buy or sell entry in the same transaction; a script that writes the
  entries for existing lots and sales; the Cash and orders section, showing cash entries. What
  was built, and what an operator still has to run, is in section 5.7.
- [x] **3.2 Review the prior art.**
  Read `JohnFunkCode/alpaca_robot_trading_workflow_experiment` and #222; record in this document
  which ideas the orders design adopts and which it drops. No code. The record is section 5.8.
- [x] **3.3 Orders and fills.**
  `orders` table with an idempotency key; `source` (manual, stop, harvester, evaluator, watch, AI)
  and a link to what prompted the trade, on orders and on `lot_sales`, with existing sales
  backfilled as manual; `OrdersService` with exactly-once market fills at the current quote from
  `batch_quotes`; refusal on a stale quote, a halted symbol, a non-USD symbol or not enough cash;
  off-hours orders queue; orders join the Cash and orders section with their status. What was
  built, and what was left for later steps, is in section 5.9.
- [x] **3.4 Queued fills in the daily job.**
  A step that fills queued orders at their session's opening price, placed and budgeted under the
  daily job's ordering rules: clamped to the remaining budget, never raises, and alarms on what
  actually landed. What was built, and what was left for later steps, is in section 5.10.
- [x] **3.5 Buy and sell on paper.**
  The Buy dialog on the Security page (no stop field yet); selling a paper position from the
  Portfolio page; seeding a paper book from the real book, on top of its starting cash (decision
  11); a manual close for a paper position whose symbol is delisted or can't be quoted, at the
  last good price and marked as such; `[PAPER]` Discord messages. What was built, and what
  was left for later steps, is in section 5.11.

#### 5.7 The cash ledger as built (3.1)

| Piece | Where | Notes |
|---|---|---|
| Table | `V16__cash_entries.sql`, `_SCHEMA`, the snapshot | `cash_entries`: `owner`, `book_id`, `kind`, `entry_date`, signed USD `amount NUMERIC(18,2)` (nullable), `lot_id`, `sale_id`, `notes`. One buy row per lot, one sell row per sale and one opening balance per book are unique partial indexes. Plain `CREATE`, so CI applies it. 26 tables |
| Trade rows | `cash_entry_sql.py` | Two `INSERT ... SELECT` statements define a buy and a sell amount. `sync_lot_cash` deletes a lot's rows and runs them again, in the transaction of every write that can change them: add, edit, sale, close (both halves of a split), CSV replace, a filled FX rate |
| Manual rows | `CashRepository`, `CashService` | Opening balance (replaced, never added to), deposit, withdrawal, delete of a deposit or withdrawal. Real book only |
| Paper books | `BooksRepository.create_paper_book(opened_on=)` | The starting cash is written as the opening balance, dated today in market time, in the same transaction as the book |
| The maths | `analytics/cash.py` | What counts (on or after the opening date), the running balance, totals by kind, the missing-rate count |
| Routes | `api/routers/cash.py` | `GET /api/portfolio/cash?book_id=`, `PUT .../opening-balance`, `POST .../entries`, `DELETE .../entries/{entry_id}`. Each write answers with the ledger |
| The view | `CashLedger.tsx`, `BookSummaryStrip` | The Cash and orders section: balance and totals, the latest five entries with "Show all entries", forms for the real book only, the below-zero warning, the "no FX rate" chip. The strip gains a Cash figure and a jump link |
| Sidekick card | `cash_ledger`, no props | `CashLedgerCard` in both registries. Read-only, always the real book |
| Backfill | `CashService.backfill`, `scripts/backfill_cash_entries.py` | Every book of every owner, dry run by default, adds only what is missing |

The rules are in [`data.md`](../architecture/data.md#cash-ledger).

**Still to run, by an operator, after this reaches each environment:**
`python scripts/backfill_fx_rates.py` first if it has not been run (section 5.5), then
`python scripts/backfill_cash_entries.py` (dry run), then `--apply`; for prod, through the proxy
with `--allow-prod`. Until then lots and sales from before V16 have no cash rows, and a paper
book made before V16 has no opening balance. Then John enters the real book's opening balance on
the Portfolio page: until he does, the real book shows no cash balance.

Left for later steps, on purpose:

- **Orders are not in the section yet** (3.3), though it is already titled "Cash and orders".
- **A paper book never refuses a buy it can't afford** yet. Nothing can buy in a paper book until
  3.3, which owns the no-margin rule.
- **No "return is unreliable while cash is negative" mark** (decision 8). There is no return
  figure to mark until 5.3. The ledger reports `negative` and the section warns.
- **The strip has no single book value** (decision 24). That is 5.1.
- **Sidekick can show the ledger but not reason about it**: no data tool, as with realized P/L.
  The read-only book tool is 5.4.

#### 5.8 The prior art, and what the orders design takes from it (3.2)

**What was read.** #222 in full, and every file of
`JohnFunkCode/alpaca_robot_trading_workflow_experiment`: its one commit (`7ab1e6b`) plus the
uncommitted changes in John's local clone, which add the clean-symbol check and a fill-quantity
guard cited below. Nothing in that repository was changed.

**What the experiment is.** A command-line loop that manages one long position in one symbol on
an Alpaca paper account. It buys with a limit order that carries a fixed stop, swaps the fixed
stop for a trailing stop once the trail would lock in a set profit, sells part at a price target,
and puts the trail back on the rest. Alpaca holds the orders and fills them; the program is a
state machine (nine states, one SQLite row per strategy) that polls, cancels, waits and
reconciles. Most of its code exists because the broker is a second system that can change state
while the program isn't looking.

That is the main difference from #431. Here QuantCore is the only system: the order, the lot and
the cash row are in one database. So the experiment's *aims* carry over and most of its
*mechanisms* don't.

**Adopted in the orders design (3.3 to 3.5):**

| Idea in the prior art | What the orders design does with it |
|---|---|
| A small set of states with an explicit terminal set (`OrderStatus.is_terminal`) | `pending`, `filled`, `cancelled`, `rejected`. The last three are terminal and no statement moves an order out of one. Alpaca's other fourteen statuses are not copied |
| Never leave state unknown after a crash. The experiment saves its record before and after every broker call | One transaction does it: the status change, the lot and the cash row commit together or not at all. An in-hours order never exists as pending (decision 25) |
| Never assume a quantity; read the position again before every sell (safety rules 2 and 7) | The fill reads cash and shares held inside its own transaction (decision 28). The number checked at placement is not trusted at fill time |
| No two full-size sells for one position at once (safety rule 9), and the clean-symbol check before a new workflow starts | A sell is refused at placement when it exceeds the shares held less the sells already queued. Buys are not limited to one per symbol: the idempotency key is what stops a double-click |
| What was asked and what happened are stored apart (`entry_limit_price` and `actual_entry_price`) | An order keeps the request (shares or a dollar amount) and the fill (price, time, where the price came from, the quote's own time) in separate columns. A dollar buy's share count is worked out from the fill price and rounded once by `_quantity` |
| The fill-quantity guard: a position that exists is not proof this order filled | The lot a fill writes carries its order's id. Nothing infers a fill from the holdings |
| `last_error` on the record, with secrets redacted first | A reason from a fixed list on a rejected order (decision 29) |
| A critical alert when a position is left unprotected | The 3.4 alarm: a queued order still pending after its session's daily job is the same kind of failure, and is reported from what the database holds |
| Tests step the loop one cycle at a time (`--once`) with a fake gateway | The fill functions take `now` and the quotes as arguments, as `market_time` already does, so a test walks an order from the evening to the open with no clock and no Yahoo |
| #222: a past price can't be filled honestly | No backdated paper fills. A seeded lot is priced by the same rule as an order (section 7) |
| #222: market-on-open is the simplest honest order type | An off-hours order fills at its session's opening price (decision 27) |
| #222: symbols Alpaca can't trade, and non-USD holdings, need a stated fallback | A non-USD symbol is rejected with its reason. The test is the quote's `currency`, the listing's own, which is the field the watchlist rule already uses |
| #222: the outside call runs after the local write commits, and only logs on failure | The `[PAPER]` Discord message in 3.5 is sent after the fill commits. A webhook failure never undoes or fails a fill |

**Dropped:**

| Idea in the prior art | Why |
|---|---|
| Polling, the order-update stream, cancel-and-wait-for-terminal | There is no second system to wait for. A cancel is one `UPDATE … WHERE status = 'pending'` |
| Reconciling against the broker on every start | Nothing to reconcile against until phase 5. #222's reconcile step, and its point that drift is the normal state, belong there |
| Partial fills | Decision 26 |
| Limit orders, and time-in-force (`GTC`, `DAY`) | Out of scope in #431. A queued order's one-session life (decision 27) is the only time-in-force there is |
| The order with a stop attached (`OTO`) | No stop field until 4.3. See the first row of the next table |
| One row per strategy, with a JSON config and four order-id columns | Orders are rows. What prompted one is its `source` and a link, not a column per kind of order |
| "Let the broker run the stop; don't trigger it in application code" (the experiment's non-goal) | Reversed on purpose: #431 makes QuantCore the engine. What the experiment warned about still applies and is in the next table |
| Registering credentials with a log redactor | The orders path holds no credential. The never-log policy already covers the rest |
| Replaying existing positions into the mirror (#222's open question) | #222 itself calls starting from today the truthful option. Seeding (decision 11) does that |

**Carried to later steps:**

| For | What the prior art says |
|---|---|
| 4.3, the stop field in the Buy dialog | The experiment can only find its stop after the buy fills. The same order applies here: the stop row is written in the fill transaction. A queued order has no stop until it fills, and a rejected order never gets one |
| 4.1 and 4.2, trailing stops | Alpaca forgets a trail's high-water mark when the order is cancelled and placed again. The `stops` row must hold its own level, so a partial sale leaves the trail where it was |
| 4.2, the checker | With no broker holding the stop, a daily job that doesn't run means a stop that doesn't fire. The checker has to work through every closed bar since the one it last checked, not only the latest. That needs a "last bar checked" on each stop, which decision 4's wording ("the latest closed daily bar") doesn't ask for |
| Milestone 4 or #221, as an idea only | The experiment's stop method: keep a fixed stop until a trail of `t` would sit at or above entry × (1 + locked profit), which is at price entry × (1 + locked profit) / (1 − `t`), then trail. Not in 4.1's scope. It could be one more `method` to compare on paper |
| Phase 4, Harvester on paper books | The partial sale at a target is a Harvester rung. The experiment's guard against taking the same partial twice is what exactly-once fills give a rung |
| Phase 5, Alpaca | The `TradingGateway` protocol and its fakes are the shape of the gateway #222 asks for. Per-owner account mapping is the account within a book. Its 18 statuses get mapped onto the four here, at the gateway |

**What the current code offers 3.3, checked while reading:**

- `Quote` (from `batch_quotes`) already has what the refusals need: `regular_price`,
  `regular_time`, `regular_open`, `market_state`, `currency` and `exchange_tz`.
- `lot_sales` has no `source` column and `positions` has `acquisition_type`, which defaults to
  `BUY`. A seeded lot needs a value other than `BUY`, because only a `BUY` lot writes a cash row
  (decision 22).
- Three things in that code will mislead 3.3 if taken at face value. They are in section 7.

#### 5.9 Orders and fills as built (3.3)

| Piece | Where | Notes |
|---|---|---|
| Table and columns | `V17__orders.sql`, `_SCHEMA`, the snapshot | `orders`, with `CHECK`s for the status, side, source, price-source and reject-reason lists and for what each status must carry. `positions.order_id`; `lot_sales.order_id`, `source` (default `manual`, which is the backfill) and `source_ref`. Plain `CREATE` and `ADD COLUMN`, so CI applies it. 27 tables |
| The rules | `analytics/orders.py`, `market_time.next_trading_day` and `upcoming_session` | `placement` answers fill, queue or reject from the clock, the calendar and the quote. Also the fill price's rounding, a dollar buy's share count, and the no-margin test |
| The rows | `OrdersRepository`, `OrderTransaction` | `transaction()` gives the service one connection with the book's row locked. Reads, the insert with its `ON CONFLICT`, the one `UPDATE … WHERE status = 'pending'`, a savepoint for a trade that has to be undone |
| Shared lot writes | `PortfolioRepository.add_lot_on`, `close_lots_on`, `open_lots_on` | The bodies of `add_position` and `close_lots`, on the caller's connection with no commit. The two public methods now call them, so a recorded trade and a filled order write a lot the same way |
| The service | `OrdersService` | `place_order`, `cancel_order`, `list_orders`. Paper books only. The quote is fetched before the transaction opens |
| Routes | `api/routers/orders.py` | `GET /api/portfolio/orders?book_id=`, `POST /api/portfolio/orders`, `POST .../{order_id}/cancel?book_id=`. A rejected order is a 201 with its reason, a repeated key a 200 |
| The view | `OrdersList.tsx`, inside `CashLedger` | Paper books only: session, request, status chip, result, latest five, Cancel on a pending order in a book that isn't archived (decision 30) |

The rules are in [`data.md`](../architecture/data.md#paper-orders).

How decisions 25 to 29 came out in code:

| Decision | In the code |
|---|---|
| 25, pending means queued | `OrdersService._act`: a FILL placement fills or rejects before the transaction commits. Only a QUEUE placement leaves the row pending |
| 26, all or nothing | No partial state exists. A sell that the lots can't cover raises in `allocate` and is rejected |
| 27, one session | `orders.session_date`, set at placement by `upcoming_session`. The fill's trade date is that date. `no_open_price` is in the list for 3.4 |
| 28, checked at fill | `_buy` reads the ledger after writing the lot; `_sell` allocates from the lots as the transaction sees them. `_placement_refusal` is the courtesy check |
| 29, fixed reasons | `REJECT_REASONS`, a `CHECK` on the column, and a test that compares the two |

**Nothing for an operator to run.** V17 needs no backfill: the column default marks every
existing sale `manual`, and no order exists yet.

Left for later steps, on purpose:

- **A queued order is not filled by anything yet** (3.4). It stays pending and can be cancelled.
  Nothing in the UI places an order until 3.5, so only a REST caller can queue one before then.
- **No Buy dialog and no Sell on a paper position** (3.5). `ordersApi.place` exists with no
  caller, and the capabilities matrix lists it as a gap.
- **No `[PAPER]` Discord message** (3.5).
- **Only `manual` is written as a source.** The route always sends it. The other five values
  have no writer until stops (4.2), Harvester on paper books and the phase 4 triggers.
- **A sale's source is stored but not shown.** It joins the Realized view's expanded row in 4.3.
- **No Sidekick or MCP access to orders** (decision 18, step 5.4). The tool count stays 63.
- **A seeded lot's `acquisition_type`** (3.5). Only `BUY` writes a cash row.

#### 5.10 Queued fills as built (3.4)

| Piece | Where | Notes |
|---|---|---|
| The rule | `analytics.orders.queued_fill`, `QueuedFill`, `QUEUED_FILL_GIVE_UP_SESSIONS` | Fill, reject or wait, from the session, today's market date, the session bar's open and the date of the newest closed bar. Pure |
| The price | `PricesService.session_open` | The open of the closed daily bar dated the session, read through `get_history(include_open=False)`. Listed in `TestReaderPolicy` as a closed-only reader |
| The rows | `OrdersRepository.list_due_orders`, `SQL_LIST_DUE_ORDERS` | One book's pending orders with `session_date <= :through`, oldest session first. Bound to an owner and a book like every other order query, so no `ALLOWED` entry |
| The service | `OrdersService.queued_orders`, `fill_queued_order` | The walk over every owner's paper books, and one order's fill. `OrdersService` now takes `PricesService` (the registry passes it) |
| The step | `main.py`: `fill_queued_orders`, `alert_if_orders_still_queued`, `run_queued_fills` | Second in the job, after the notifications. `QUEUED_FILLS_BUDGET_SECONDS`, default 120 |
| The alarm | `Notifier.send_queued_orders_alert` | Marked `[PAPER]`. Count, up to 10 symbols, the oldest session. No owner |
| The page | `OrdersList.tsx` | The pending notice says again that a queued order fills at its session's opening price and shows that evening (taken out in the #560 review until this step) |

No schema change: `price_source = 'session_open'` and `reject_reason = 'no_open_price'` were
both in V17. The table count stays 27. The rules are in
[`data.md`](../architecture/data.md#paper-orders) and the step in
[`daily-jobs.md`](../architecture/daily-jobs.md#queued-paper-fills).

How the daily job's rules came out:

| Rule | In the code |
|---|---|
| Clamped to the remaining budget | `_remaining_budget(QUEUED_FILLS_BUDGET_SECONDS…)` at the call in `__main__`; the walk checks the clock before each order |
| Never raises | `run_queued_fills` guards the fills and the alarm apart, so the alarm runs when the walk fails. One order's failure is counted and the walk goes on |
| Alarms on what landed | `alert_if_orders_still_queued` calls `queued_orders` again after the walk. It does not read the walk's summary |
| An unparseable setting falls back | `_env_float`, as for every other budget |

**Nothing for an operator to run.** `QUEUED_FILLS_BUDGET_SECONDS` is optional. No order is
queued on test or prod unless someone placed one through the REST route since #560.

Left for later steps, on purpose:

- **No `[PAPER]` message for a fill** (3.5). Only the alarm is sent.
- **Nothing in the UI places an order yet** (3.5), so the step has work only for an order placed
  through the REST route.
- **Stops are not checked in this step** (4.2). Decision 4's one run after the close does two
  things; this is the first. 4.2 adds the second beside it and should share its guard pattern,
  not its budget.
- **A seeded lot's price** (3.5) follows the same rule as an order (section 7): a seed made
  outside market hours needs the next session's open, which is what `session_open` reads.

#### 5.11 Buy and sell on paper as built (3.5)

| Piece | Where | Notes |
|---|---|---|
| The schema | V18 `orders.kind`, `orders_kind_rule` | `market`, `seed` (a buy for a share count) or `manual_close` (a sell). Additive, mirrored in `_SCHEMA` and the snapshot. The table count stays 27 |
| The rules | `analytics.orders`: `lot_acquisition_type`, `moves_cash`, `seed_quantities`, `unquotable`, `last_good_price` | Pure. A seed's lot is `SEED` and moves no cash; a symbol is unquotable with no quote or a last trade three sessions old |
| The price of a manual close | `PricesService.last_close` | The newest stored closed daily bar. Zero network. Listed in `TestReaderPolicy` as a closed-only reader |
| The service | `OrdersService.seed_book`, `close_unquoted`, `fills_on` | Paper books only, like `place` |
| The routes | `POST /api/portfolio/orders/seed`, `POST /api/portfolio/orders/manual-close` | Neither takes a price, a share count or an owner |
| The Discord message | `main.report_paper_fills`, `Notifier.send_paper_fills` | After `run_queued_fills`. Never raises. Nothing is sent on a day with no fills |
| Buy | `PaperOrderDialog`, opened from "Buy on paper" on the Security page | Paper book picker, shares or dollars, the book's cash. No stop field (4.3) |
| Sell | `PaperOrderDialog`, opened from Sell on a paper symbol row | The share count comes from the row's `total_quantity`, which the server adds up |
| Manual close | The same dialog | Offered when a sell is rejected `no_quote` or `stale_quote`, or at once for a position with no current price. The server decides |
| Seeding | A checkbox in `CreateBookDialog`; `SeedEmptyBook` on an empty paper book with no orders | The dialog chains create then seed |
| The Orders list | `orderWords.ts` | A seed reads "Copy 5 INTC from the real book … at no cost"; a manual close names the last price on record and its date |

The dialog ends by saying what the server did: filled, queued for a session, or rejected with
its reason. A retry after a network error repeats the idempotency key. A try after the server
answered is a new order.

**Nothing for an operator to run.** V18 is applied by CI. `PAPER_DISCORD_WEBHOOK_URL` is
optional; without it the message goes to `DISCORD_WEBHOOK_URL`.

Left for later steps, on purpose:

- **No stop field in the Buy dialog** (4.3).
- **Performance must count seeded holdings in a book's opening value** (5.1), or the first day
  shows a false gain. A seeded lot is `acquisition_type = 'SEED'` and its order is `kind = 'seed'`.
- **No Sidekick or MCP access to orders** (decision 18, step 5.4). The tool count stays 63.
- **Not tried in a browser against a live server.** The front end is covered by vitest only.

### Milestone 4: stops (#431 phase 2, and all of #221)

- [x] **4.1 Stops table and service.**
  `stops` table (owner, book, symbol or lot, fixed or trailing, level, method, source, status);
  `StopsService` and routes; owner and book isolation tests. What was built, and what was left
  for later steps, is in section 5.12.
- [x] **4.2 Bar-close checker.**
  A daily-job step over closed bars only (`include_open=False`): ratchet trailing levels, detect
  breaches (the bar's low reached the level), alert for real books with dedup across runs, and for
  paper books place a sell that fills at the stop level, or at the open when the stock opened
  below the stop (decision 5). A stop closes when its position goes flat. Updates #431's text to
  match the new fill rule. What was built, and what was left for later steps, is in section 5.13.
- [x] **4.3 Stops in the UI.**
  The stop field in the Buy dialog, with the stop card's suggestion loaded only when the field is
  opened; the stop shown on holdings and lot rows; the source of a stop-driven sale shown in the
  Realized view. What was built, and what was left for later steps, is in section 5.14.
- [x] **4.4 Approach alerts.**
  A Discord alert when a holding closes within an ATR-relative distance of its stop, in the same
  checker step, deduplicated across runs so a stop that stays close alerts once. The distance is
  an env var with a documented default that falls back on an unparseable value. What was built,
  and what was left for later steps, is in section 5.17.
- [x] **4.5 The stop panel, with broker mirroring.**
  #221's one panel with two entry paths on the Security page: mirror a stop already placed at an
  outside broker (price, and optionally quantity, order type and brokerage), or create one from
  the stop card's recommendation with its reasoning. Both write the same row; a field records
  whether the order exists at a broker. Real books only for mirroring. Closes #221. What was
  built, and what was left for later steps, is in section 5.18.

#### 5.12 The stops table, service and routes as built (4.1)

| Piece | Where | Notes |
|---|---|---|
| The table | `V19__stops.sql`, `_SCHEMA`, the snapshot | `stops`: `owner`, `book_id`, `symbol_id`, `kind`, `stop_level`, `trail_pct`, `high_water_mark`, `method`, `proposed_level`, `reference_price`, `as_of`, `source`, `source_ref`, `status`, `created_at`, `resolved_at`. `CHECK`s for the kind, source and status lists and for what each kind and status must carry. `ux_stops_one_active` is the one-active-stop rule. Plain `CREATE`, so CI applies it. 28 tables |
| The rules | `analytics/stored_stops.py` | Pure. `stop_terms` gives a fixed or trailing stop's stored terms or says what is wrong; `trailing_level`; the kind, status, method and source lists. Not `analytics/stops.py`, which places a stop for the stop-loss card |
| The rows | `StopsRepository` | `list_stops`, `get_stop`, `set_stop` (book resolved and locked, an OPEN lot required, the active stop replaced, the new one inserted, all in one transaction), `resolve_stop` (the one way out of `active` by id) |
| The service | `StopsService` | `list_stops`, `set_stop`, `cancel_stop`. Either kind of book. Takes `PricesService` for `last_close` |
| The routes | `api/routers/stops.py` | `GET /api/portfolio/stops?book_id=&include_inactive=`, `POST /api/portfolio/stops`, `POST .../{stop_id}/cancel?book_id=`. No route takes a source or a status |
| Isolation | `tests/test_stops_repository.py` (`IsolationTest`), `tests/test_stops_api.py` | `stops` is mapped in `OWNER_SCOPED` and `BOOK_SCOPED`. No `ALLOWED` entry in either scan |

The rules are in [`data.md`](../architecture/data.md#stored-stops). How decisions 42 to 47
came out is the table above; the decisions say what each costs.

**Nothing for an operator to run.** V19 is applied by CI and needs no backfill: no stop exists.

Left for later steps, on purpose:

- **Nothing checks a stop** (4.2). No ratchet, no breach, no alert, no paper sale. A stored
  stop is a record until then.
- **A stop is not closed when its position goes flat** (4.2). `closed` is in the status list
  and `resolve_stop` can write it. Nothing calls it yet.
- **No "last bar checked" column** (4.2, section 5.8), and **no "exists at a broker" field**
  or the mirror's quantity, order type and brokerage (4.5).
- **No UI** (4.3, 4.5). The three routes are a gap in the capabilities matrix.
- **`triggered` has no writer**, and nothing writes an `alerts` row for a stop (4.2, 4.4).
- **Only `manual` is written as a source.**
- **No Sidekick or MCP access to stops** (decision 18, step 5.4). The tool count stays 63.

#### 5.13 The bar-close checker as built (4.2)

| Piece | Where | Notes |
|---|---|---|
| The schema | `V20__stop_checks.sql`, `_SCHEMA`, the snapshot | `stops.last_bar_checked`, `triggered_session`, `trigger_price`, `alerted_at`, and `stops_triggered_has_trigger`: a triggered stop has a session and a price, and no other stop has. `ADD COLUMN` only, so CI applies it. The table count stays 28 |
| The rules | `analytics.stored_stops`: `first_session`, `next_bar_date`, `is_due`, `check_stop`, `trigger_for` | Pure. A run of bars in, the stop's terms and (when reached) the bar and the price out |
| The bars | `PricesService.closed_bars_since` | Closed daily bars from a date, through `get_history(include_open=False)`. Listed in `TestReaderPolicy` |
| Statements a trade runs | `repositories/stop_sql.py` | `trigger_stop_on`, `resolve_stop_on`, `close_flat_stops_on`, on the caller's connection. Its own module because `OrdersRepository` and `PortfolioRepository` need them and `StopsRepository` needs both |
| The rows | `StopsRepository.record_check`, `trigger_stop`, `close_flat_stops`, `mark_alerted` | Each bound to an owner and a book. No new `ALLOWED` entry in either scan |
| The service | `StopsService.close_flat_stops`, `due_stops`, `check_stop`, `unalerted_triggers`, `mark_alerted` | `StopsService` now takes `OrdersService` (the registry passes it) |
| A paper stop's sale | `OrdersService.sell_for_stop`, `OrderTransaction.trigger_stop` and `close_stop` | The stop's status and the sale in one transaction, with the book locked |
| Closing a flat stop | `PortfolioRepository.close_lots_on` | Every sale, real or paper |
| The step | `main.py`: `check_stops`, `alert_if_stops_unchecked`, `run_stop_checks`, `report_triggered_stops` | Third in the job. `STOP_CHECK_BUDGET_SECONDS`, default 120 |
| The messages | `Notifier.send_stop_alert`, `send_unchecked_stops_alert`; `_paper_fill_line` | `send_notifications` now answers False when a message did not go out: Discord refused it, or no webhook is set |
| The page | `orderWords.ts` | A stop's sale reads "Sell 5 INTC, by its stop" and "its stop level" or "the opening price, below its stop" |

The rules are in [`data.md`](../architecture/data.md#the-stop-checker) and the step in
[`daily-jobs.md`](../architecture/daily-jobs.md#stop-checks). #431's text now gives the fill
rule of decision 5.

How the daily job's rules came out:

| Rule | In the code |
|---|---|
| Clamped to the remaining budget | `_remaining_budget(STOP_CHECK_BUDGET_SECONDS…)` at the call in `__main__`; the walk checks the clock before each stop |
| Never raises | `run_stop_checks` guards the checks and the alarm apart. `report_triggered_stops` has its own guard. One stop's failure is counted and the walk goes on |
| Alarms on what landed | `alert_if_stops_unchecked` calls `due_stops` again after the walk. The breach alert reads `triggered` rows, not the walk's summary |
| An unparseable setting falls back | `_env_float`, as for every other budget |

**Nothing for an operator to run.** V20 is applied by CI and needs no backfill.
`STOP_CHECK_BUDGET_SECONDS` is optional. No stop exists on test or prod unless someone set one
through the REST route since #563.

Left for later steps, on purpose:

- **No UI** (4.3, 4.5). A triggered stop, its trigger price and the bar are on the
  `GET /api/portfolio/stops?include_inactive=true` answer and nowhere on a page.
- **The source of a stop-driven sale is stored and worded in the Orders list, but not shown in
  the Realized view** (4.3).
- **No approach alert** (4.4). It belongs in `check_stops`, with its own dedup column.
- **No "exists at a broker" field** (4.5).
- **Another owner's real-book stop is marked and not announced** (decision 51) until 4.3.
- **Not run against Yahoo.** The bar read is `get_history`, the one 3.4 uses; the tests stub
  the fetch.
- **No Sidekick or MCP access to stops** (decision 18, step 5.4). The tool count stays 63.

#### 5.14 Stops in the UI as built (4.3)

| Piece | Where | Notes |
|---|---|---|
| The schema | `V21__order_stop.sql`, `_SCHEMA`, the snapshot | `orders.stop_kind`, `stop_level`, `stop_trail_pct`, `stop_method`, `stop_proposed_level`, `stop_set`, and `orders_stop_rule`: only a `market` buy carries a stop, a fixed one has a level and a trailing one a percentage, and `stop_set` is set only on a filled order. `ADD COLUMN` only, so CI applies it. The table count stays 28 |
| The rules | `analytics.stored_stops`: `requested_stop`, `order_stop_terms`, `RequestedStop`, `MAX_LEVEL` | Pure. What a buy may ask for, checked with no price; and its terms once there is a fill price, or None when no stop can be set there |
| The statements | `stop_sql.set_stop_on` | The replace and the insert, on the caller's connection. `StopsRepository.set_stop` and `OrderTransaction.set_stop` both run it |
| The service | `OrdersService.place_order` (five `stop_*` arguments), `_attach_stop` in `_fill` | The stop is written after the buy is known to stand and before the order is marked, so the lot, the stop and `stop_set` commit together |
| The route | `POST /api/portfolio/orders` takes `stop` (`kind`, `stop_level` or `trail_pct`, `method`, `proposed_level`) | No status, source or reference price is taken. The order answers with the six `stop_*` fields. The route count stays 128 |
| A sale's source | `PortfolioRepository.list_realized_sales`, `RealizedSale` | `source`, `source_ref` and `order_id` on every sale of the realized payload |
| The Buy dialog | `PaperOrderDialog`, `OrderStopField` | "Add a stop" opens a fixed level or a trailing percentage. The field is mounted only when opened, and that is when `useStopLoss` asks for the card. Its three candidates are buttons that fill the field. An edited suggestion keeps its method and the level that was suggested |
| Holdings | `useStops`, `stopWords.stopsBySymbol`, `StopCell` (a row-bound fragment) | A Stop column in every book: the level, "trails 8%" for a trailing stop, or "Reached $44.10" for a stop a bar reached while the holding was open |
| Lot rows | `LotRow` | A Stop column with the symbol's live level |
| Realized | `RealizedPnl` (`SaleSource`), `stopWords.SALE_SOURCES` | A Source column in the expanded row. A stop's sale is a chip |
| The words | `orderWords.ts` | "Buy 5 INTC, with a stop at $45.00"; "Its stop is set when the order fills"; "Stop not set: filled at or below it" |

The rules are in [`data.md`](../architecture/data.md#a-stop-on-a-buy).

How section 5.8's row for this step came out:

| What 5.8 asked | In the code |
|---|---|
| The stop row is written in the fill transaction | `_attach_stop` runs on the order's transaction, between the lot and the order's `UPDATE` |
| A queued order has no stop until it fills | The order holds the request in its own columns. No `stops` row exists until `fill_queued_order` |
| A rejected order never gets one | A rejection returns before `_attach_stop`, and `orders_stop_rule` refuses `stop_set` on an order that isn't filled |

**Nothing for an operator to run.** V21 is applied by CI and needs no backfill: every existing
order asked for no stop.

Left for later steps, on purpose:

- **No way to set or cancel a stop on a holding from a page** (4.5). `POST /api/portfolio/stops`
  and the cancel route are still reached by no component.
- **No approach alert** (4.4), and **no "exists at a broker" field** (4.5).
- **Realized P/L by source** (5.3). The Realized view shows each sale's source and adds nothing
  up by it.
- **The stop field is not a registered Sidekick component.** It is part of a form, like the
  dialog it sits in.
- **No Sidekick or MCP access to stops** (decision 18, step 5.4). The tool count stays 63.
- **Not tried in a browser against a live server.** The front end is covered by vitest only.

#### 5.15 Decisions 56 to 62 against other systems (2026-11-17)

Read: Alpaca's order documentation, backtrader's bracket, execution and trailing-stop pages,
QuantConnect's equity fill model, and TradingView's strategy documentation. Interactive
Brokers' pages refused automated reading, so its lines are from quotes of its documentation
in search results. Nothing was tested against a live account.

| Decision | What other systems do | Outcome |
|---|---|---|
| 56, the request waits on the order | The norm. Alpaca: the child orders "won't be active until the first order is completely filled", and cancelling one cancels the group. Interactive Brokers: an attached stop "will not be submitted until the parent order fills". backtrader: "the children are not active until the main side is executed" | Already in line |
| 57, a trailing stop starts at the fill | Alpaca tracks the high "since the order was submitted", and takes no trailing stop as a bracket leg, so a trail is submitted after the fill. backtrader starts from the latest close. No system states "the fill price" in those words | In line in effect. No change |
| 57, when the stop goes live | Alpaca and Interactive Brokers: when the entry fills, so the same day. TradingView assumes a path inside each bar. backtrader: "starting with the next price bar" | Changed for a fill at the open (decision 63). A fill in market hours is open, below |
| 58, a level at or above the price, at placement | Alpaca refuses a stop that isn't at least $0.01 under its base price (the entry limit, or the current price for a market entry) | Already in line |
| 58, a queued buy that opens at or under its level | Nothing found | Open, below |
| 59, a second stop on a held symbol | Nothing documented. A broker's stop belongs to its order, so two brackets give two stops, and users report resizing stops by hand | Open, below |
| 60, 61, 62 | Nothing found | Open, below |
| Decision 5, the fill price | QuantConnect: `min(open, stop)`. backtrader and TradingView: the open when price gaps through | Already in line |

**Open, for John to decide.** John answered all six on 2026-11-17: 1, keep; 2, reject the
buy (decision 64); 3, replace with a warning (65); 4, move it server-side now (66); 5, add
Cancel now (67); 6, keep. The questions as they were put:

1. **57, a buy filled in market hours.** A broker's stop is live from the fill. A daily bar
   can't say whether its low came before the fill, so the stop is first tested the next
   session. The choices: keep that; test the fill day's bar anyway and accept a sale at a
   price from before the buy; or wait for intraday checking (decision 6).
2. **58, a queued buy that opens at or under its fixed level.** As built it fills with no
   stop and says so. The choices: keep that; reject the buy (a hand-applied migration for the
   new reject reason); or fill and let the stop sell at once, as a broker's would.
3. **59, a buy with a stop on a symbol that has one.** As built the new stop replaces the
   old. The choices: keep that; refuse the second stop; or warn in the dialog before
   replacing.
4. **60, which stop a holding shows.** As built the page picks from the book's whole
   history. The choice is whether to move that to a server-side read now or when it grows.
5. **61, no Cancel in 4.3.** As built a stop can't be taken off from a page until 4.5. The
   choice is whether to add Cancel to the Stop cell first.
6. **62, the Source column.** As built every sale shows its source. The choice is whether to
   show it only when it is not `manual`.

#### 5.16 John's four changes as built (decisions 64 to 67)

| Piece | Where | Notes |
|---|---|---|
| The reject reason | `V23__reject_stop_not_below_fill.sql`, `_SCHEMA`, the snapshot; `analytics.orders.STOP_NOT_BELOW_FILL` | **Applied by hand.** The migration replaces `orders_reject_reason_check`, and the migrate Job refuses a `DROP`. `_SCHEMA` lists the value in `CREATE TABLE` and repeats the replacement for a database made before V23 |
| The rejection | `OrdersService._fill` | The stop's terms are worked out before anything is written. No terms, no trade: the order is rejected and the lot, cash row and stop are never written. `_attach_stop` now only writes |
| The stop a holding shows | `analytics.stored_stops.holding_stops`, `StopsService.holding_stops`, `StopsRepository.open_lots`, `GET /api/portfolio/stops/holdings` | Two queries. Owner and book are bound as on every other stop read, so no new `ALLOWED` entry |
| The page | `useHoldingStops`, `PortfolioPage` | `stopWords.stopsBySymbol` is gone |
| Taking a stop off | `stopsApi.cancel`, `useCancelStop`, a control in the Stop cell, `ConfirmDialog` | Only on an active stop. A refusal from the server is shown in the question, which stays open |
| The warning | `OrderStopField` | Reads the chosen book's holding stops once the field is opened |
| The words | `orderWords.ts` | "it opened at or below the stop it asked for, so no position was opened". The "Stop not set" wording is gone with the case it described |
| The stop rule (the #567 review) | `V24__order_stop_rule_kind.sql`, `_SCHEMA`, the snapshot | **Applied by hand**, for the same reason. `orders_stop_rule` now requires `stop_kind` on a requested stop |

**For an operator, before the roll-out:** V23 and V24 must be applied by hand on each database
(one `migrate` applies both).
`./scripts/flyway.sh migrate` for test, then re-run the deploy workflow, which will have
stopped at the migrate step. `./scripts/flyway.sh --prod migrate` before the prod promotion.
V21 and V22 are applied by the same command if CI has not already applied them.

Left for later steps: setting a stop on a holding (4.5), the approach alert (4.4), and a page
for a book's stop history (the list route is reached by no component again).

#### 5.17 Approach alerts as built (4.4)

| Piece | Where | Notes |
|---|---|---|
| The schema | `V25__stop_approach.sql`, `_SCHEMA`, the snapshot | `stops.approached_session`, `approach_close`, `approach_atr`, `approach_alerted_at`, and `stops_approach_rule`: the first three are set together, and the mark needs an approach. Each side is an `IS NULL` test, so the rule can't come out NULL (the 4.3 gotcha). `ADD COLUMN` only, so CI applies it. The table count stays 28 |
| The rule | `analytics.stored_stops.approach`, `_atr`, `atr_distance` | Pure. The bars and the level in; the session, close, ATR and whether it is near out, or None when it can't be judged |
| The read | `StopsService._bars` | One `closed_bars_since` from 90 days back, split into the history and the bars not yet tested (decision 72) |
| The record | `StopsService._approach`, `StopsRepository.record_check` | The check that held writes the approach in the same `UPDATE`. `approach_known` false leaves the four columns; a far close sets them to NULL |
| What is owed | `StopsService.unalerted_approaches`, `mark_approach_alerted`; `StopsRepository.mark_approach_alerted` | Real book, active, close, not yet marked. Nearest first. Owner and book bound as on every stop statement, so no new `ALLOWED` entry in either scan |
| The step | `main.py`: `approach_atr_multiple`, `report_approaching_stops` | The walk reads the setting once and passes it to every check. The alert goes after the breach alert. `STOP_APPROACH_ATR_MULTIPLE`, default 1, falls back on an unparseable or non-positive value |
| The message | `Notifier.send_stop_approach_alert`, `_stop_approach_line` | "INTC: closed at $20.60 on 2026-06-16, 0.6 ATR above its stop at $20.00 (fixed)". Says the stops have not been reached |
| The tests | `tests/test_stop_approach.py` | The rule, the rows and their rule, wrong owner and wrong book, the service, the step, the setting and the message. Two tests in `tests/test_stop_checks.py` now expect the longer read |

The rules are in [`data.md`](../architecture/data.md#the-stop-checker) and the step in
[`daily-jobs.md`](../architecture/daily-jobs.md#stop-checks).

**Nothing for an operator to run.** V25 is applied by CI and needs no backfill: every stop
starts with no approach and gets one on its next check. `STOP_APPROACH_ATR_MULTIPLE` is
optional.

Left for later steps, on purpose:

- **No page shows that a holding is close to its stop.** The four columns are on the stop
  routes' answers. The Stop cell could mark it; that is UI work for 4.5 or later.
- **Not run against Yahoo.** The read is the one 4.2 uses, with a longer window; the tests
  stub it.
- **No band between leaving and coming back** (decision 69).

#### 5.18 The stop panel as built (4.5)

| Piece | Where | Notes |
|---|---|---|
| The schema | `V26__stop_at_broker.sql`, `_SCHEMA`, the snapshot | `stops.at_broker` (default false), `broker_quantity`, `broker_order_type`, `brokerage`, and `stops_broker_details_rule`. `ADD COLUMN` only, so CI applies it. The table count stays 28 |
| The rule | `analytics.stored_stops.broker_order`, `BrokerOrder`, `BROKER_ORDER_TYPES` | Pure. Details without `at_broker` are refused, not dropped; the order type must suit the kind; the brokerage is trimmed and at most 60 characters |
| The rows | `stop_sql.set_stop_on` (the insert), `StopsRepository.set_stop` | The share count is checked against the book's OPEN lots on the transaction that holds the book's lock (`BrokerQuantityError`, a 422). Every stop read returns the four columns |
| The service | `StopsService.set_stop` (four new keyword arguments) | A paper book's stop at a broker is refused before anything is read |
| The route | `POST /api/portfolio/stops` takes `at_broker`, `broker_quantity`, `broker_order_type` (a `Literal`), `brokerage` | The `Stop` model documents the four, and the four approach columns 4.4 added, which the answers already carried. The route count stays 129 |
| The alerts | `notifier._stop_alert_line`, `_stop_approach_line`, `_broker_words` | "The stop order at Schwab for 100 shares (stop-limit) should have filled: check it" |
| The panel | `StopPanel` on the Technical Analysis tab, under the two cards; `useSetStop`, `stopsApi.set` | Book chips; the book's stop on the symbol, "At a broker" or "Only here", and its tooltip words; "Set a stop" or "Replace this stop"; the two paths; a warning naming the stop it replaces, and for a mirrored one, that the broker's order must be changed there too |
| Shared with the Buy dialog | `stopDraft.ts` (`StopDraft`, `stopPayload`, `stopSuggestions`) | Moved out of `OrderStopField`. A suggestion now carries the card's reasoning, which the Buy dialog shows as the button's title |
| The words | `stopWords.brokerWords`, `stopDetail` | "Placed at Schwab as a stop-limit order for 100 shares." A reached mirrored stop says its order should have filled |
| The tests | `tests/test_stops_api.py` (`BrokerMirrorTest`, `test_a_broker_mirror_over_the_route`), `tests/test_stops_repository.py`, `tests/test_stored_stops_analytics.py` (`BrokerOrderTest`), `tests/test_stop_checks.py`, `tests/test_stop_approach.py`; `StopPanel.test.tsx`, `SecurityDetailPage.test.tsx`, `stopWords.test.ts`, `stops.test.ts` | Owner and book isolation are unchanged: no new statement, and the insert binds the same owner and book |

The rules are in [`data.md`](../architecture/data.md#stored-stops) and the panel in
[`quantui.md`](../architecture/quantui.md).

**Nothing for an operator to run.** V26 is applied by CI and needs no backfill: every existing
stop is one that exists only here.

Left, on purpose:

- **The Add Lot dialog's stop field**, #221's first entry point, is not built. A real lot is
  recorded and then its stop is set from the panel.
- **No page lists a book's stop history.** `GET /api/portfolio/stops?include_inactive=true` is
  still reached by no component (a gap in the capabilities matrix).
- **No page shows that a holding is close to its stop** (left by 4.4). The panel and the Stop
  cell don't mark it.
- **A mirror is never reconciled with the broker** (decision 76), and a stop limit's limit
  price is not stored (decision 78).
- **Not tried in a browser against a live server.** The front end is covered by vitest only.
- **No Sidekick or MCP access to stops** (decision 18, step 5.4). The tool count stays 63.

### Milestone 5: performance (#431 phase 3)

- [x] **5.1 Snapshots and the maths.**
  `book_value_snapshots` table; a daily-job step writing cash plus positions at the close for
  every book that isn't archived; `time_weighted_return` and `max_drawdown` in
  `quantcore/analytics`.
- [x] **5.2 History backfill and restatement** (decisions 85 and 88 as revised).
  One rebuild, in `PerformanceService`, that recomputes a book's snapshots for a range of sessions
  from its lots, sales, cash entries and closed `ohlcv` bars, with each flow on its real date. Two
  callers:
  - **The backfill script** rebuilds a real book's history from its opening balance, and reports
    every day it could not value.
  - **Restatement.** A `books.restate_from` date (a migration, `_SCHEMA` and the snapshot) is set
    to the earlier of itself and the change's date by every write dated on or before the book's
    newest row: a cash entry, lot or sale added, edited or deleted, a CSV import, and an FX rate
    filled in later. An order's fill is dated today and never sets it. The daily job's snapshot
    step rebuilds each marked book from that date before it takes the session's row, inside its
    budget, and clears the mark only if no write moved it meanwhile (`UPDATE … WHERE
    restate_from = <the date it read>`). A book it doesn't finish stays marked for the next
    night, and the owed-books alarm names the reason.
  - **A rebuilt row says so** (`restated_at` beside `taken_at`), so a correction can be shown,
    as GIPS asks for it to be disclosed.
  - With flows on their real dates, `contributed` stays as a check: a row whose stored total
    disagrees with the ledger is one a missed mark left behind.
  Each restating write is a SQL statement on a per-owner table, so it restricts the owner and
  the book like every other (both isolation scans).
- [x] **5.3 Performance and Compare views.**
  `GET /api/books/performance`; the Performance section, the return-vs-SPY figure in the summary
  strip, and the Compare books view against SPY; attribution, as realized P/L by `source` for the
  selected book; the
  dividend and split caveats on screen; a book-performance Sidekick card in both registries.
- [ ] **5.4 Read access for Sidekick and MCP.**
  A read-only book tool in `READ_ONLY_TOOLS`; an MCP tool with its contract case and the tool
  count moved from 63. Closes #431 phases 1 to 3.

#### 5.19 Snapshots and the maths as built (5.1)

| Piece | Where | Notes |
|---|---|---|
| The table | `V27__book_value_snapshots.sql`, `_SCHEMA`, the snapshot | `book_value_snapshots`: `owner`, `book_id`, `session_date`, `cash`, `holdings_value`, `book_value`, `flow`, `contributed`, `holding_count`, `unpriced_count`, `stale_count`, `fx_missing_count`, `taken_at`. One row a book and session (`book_value_snapshots_one_a_session`); `book_value_snapshots_value_rule` holds the value to cash plus holdings, with `holdings_value IS NOT NULL` in front of the sum (the 4.3 gotcha). Plain `CREATE`, so CI applies it. 29 tables |
| The maths | `quantcore/analytics/performance.py` | `book_value` (one row from the cash rows, open lots, closes and rates), `return_index`, `time_weighted_return`, `max_drawdown`. Pure, Decimal throughout; returns to eight places |
| The rows | `BookSnapshotsRepository` (`upsert_snapshot`, `list_snapshots`, `previous_snapshot`) | The upsert picks its book through `INSERT … SELECT FROM books` with the real-by-default subquery, so another owner's book writes nothing and a write never creates a book. No `ALLOWED` entry in either scan |
| The service | `PerformanceService` (`due_snapshots`, `snapshot_book`, `list_snapshots`), in the registry as `performance` | Gathers the inputs: cash rows and open lots (no network), `PricesService.close_on` per held symbol, `fx_daily_closes` once a non-USD currency |
| A holding's close | `PricesService.close_on`, `CLOSE_ON_LOOKBACK_DAYS = 7` | Through `closed_bars_since`, so it fetches only a symbol whose closed bars are behind |
| The step | `main.py`: `take_book_snapshots`, `alert_if_snapshots_owed`, `run_book_snapshots`, last, after the close-out | `BOOK_SNAPSHOT_BUDGET_SECONDS`, default 120, clamped to the deadline and falling back on an unparseable value. Never raises; skips an NYSE-closed day |
| The alarm | `Notifier.send_snapshots_owed_alert` | Books counted by reason (no snapshot, no close or rate, an earlier close). Names no book or owner |
| The tests | `tests/test_performance_analytics.py`, `tests/test_book_snapshots_repository.py`, `tests/test_performance_service.py`, `tests/test_book_snapshots_step.py`; the owner and book coverage maps | Owner and book isolation, the value rule's bad shapes, a late-typed deposit, a failed FX lookup, an archived book, the step's order in `main.py` |

The rules are in [`data.md`](../architecture/data.md#book-value-snapshots) and the step in
[`daily-jobs.md`](../architecture/daily-jobs.md).

**Nothing for an operator to run.** V27 is applied by CI. The first rows are written the first
trading-day night after the roll-out: from then on for every paper book, and for a real book
only once its owner has entered an opening balance.

Left, on purpose:

- **No past rows.** The real book's history before the roll-out is step 5.2's backfill. A paper
  book has no history before its first night either; one made before the roll-out starts its
  series then.
- **No route, page, Sidekick or MCP access** (5.3, 5.4). The strip still shows cash beside the
  holdings value, not one book value (decision 24).
- **No split or dividend handling** (#391). A split reads as a fall in the return, as in the stop
  checker.
- **Not run against Yahoo.** Every test stubs the closes and rates. Watch the first night's log
  line (`Book snapshots: … valued, … incomplete`) and alarm on test.

#### 5.20 History backfill and restatement as built (5.2)

| Piece | Where | Notes |
|---|---|---|
| The columns | `V28__book_restatement.sql`, `_SCHEMA`, the snapshot | `books.restate_from` (DATE) and `book_value_snapshots.restated_at` (TIMESTAMPTZ), both nullable. Plain `ADD COLUMN`, so CI applies it. Still 29 tables |
| The mark | `quantcore/repositories/restate_sql.py` (`mark_restate`, `SQL_MARK_RESTATE`) | One `UPDATE books` that sets `restate_from` to the earlier of itself and the date, only when the date is on or before the book's newest row. Called in the write's own transaction by `PortfolioRepository` (`replace_owner_positions`, `add_position`, `remove_position`, `update_lot`, `delete_lot`, `insert_sale`, `close_lots`, both FX fills) and `CashRepository` (`set_opening_balance`, `add_entry`, `delete_entry`, `backfill_entries`). Not by `add_lot_on` or `close_lots_on`, which a fill uses |
| The history read | `PortfolioRepository.list_lot_history` | Every lot of a book, open and closed, with `sold_on` (its sale's date) from a join through the restricted lot |
| The maths | `analytics.performance`: `held_lots`, `undated_lots`, `close_as_of`, `rebuild_snapshots`; `CLOSE_LOOKBACK_DAYS` moved here from `prices.py` | Pure. Each session through `book_value`, its flow measured from the row rebuilt before it |
| The writes | `BookSnapshotsRepository.restating` (a `RestateTransaction`: `write`, `delete`, `clear_mark`), `restate_mark` | The book's row is locked `FOR UPDATE` for the whole rebuild. `SQL_WRITE_RESTATED` inserts a new session with no `restated_at` and replaces a stored row only when a value differs (`IS DISTINCT FROM` on the `DO UPDATE`) |
| The service | `PerformanceService.restate_book`, `backfill_book`, `_rebuild_in`; `due_snapshots` gains reason `restate` and `restate_from` | One read a held symbol's closed bars over the range, one FX fetch a currency |
| The step | `main.take_book_snapshots` and `_restate_one` | A marked book is restated through today instead of snapshotted; `restated` in the summary line. Same budget |
| The script | `scripts/backfill_book_snapshots.py` | Dry run by default, `--apply`, `--owner`, `--book-id`, `--from`, `--through`, `--allow-prod`. Names every session with no value and every one priced from an earlier close |
| The tests | `tests/test_book_restatement.py` (marking, the rebuild, the backfill, against the database), `tests/test_performance_analytics.py` (`HeldLotsTest`, `CloseAsOfTest`, `RebuildSnapshotsTest`), `tests/test_book_snapshots_step.py`, `tests/test_backfill_book_snapshots_script.py` | Wrong-owner and wrong-book marks, a failed fetch keeping the mark, an unchanged row left alone, a hole filled, a session losing its row |

The rules are in [`data.md`](../architecture/data.md#book-value-snapshots) and the step in
[`daily-jobs.md`](../architecture/daily-jobs.md).

**For an operator, once it has rolled out:** V28 is applied by CI. To give a real book its
history before 5.1's roll-out, run the backfill against prod, dry run first:

```bash
python scripts/backfill_book_snapshots.py --owner john --allow-prod
```

then again with `--apply` once its list of unvalued days looks right. A book needs its opening
balance entered first; the script skips one without.

Left, on purpose:

- **A lot that cost no cash and is sold on the session it arrived is in no flow.** It is held at
  no close, so its value is never counted coming in, but its sale's cash is: a false gain that
  day. Only a seed followed by a same-day manual close, or a transfer recorded as sold the same
  day, can do it.
- **Not run against Yahoo or prod.** Every test stubs the closes and rates. The first real
  backfill is the check: watch its unvalued list.
- **No route or screen shows `restated_at` yet** (5.3).
- **No split or dividend handling** (#391), as in 5.1.

#### 5.21 The Performance and Compare views as built (5.3)

| Piece | Where | Notes |
|---|---|---|
| The maths | `analytics.performance`: `BENCHMARK_SYMBOL`, `benchmark_index`, `book_performance`; `analytics.portfolio_math.realized_by_source` | Pure. `book_performance` returns `twr`, `benchmark_return`, `vs_benchmark`, the drawdown and its peak and trough, the `series` (`session_date`, `book_value`, `index`, `benchmark_index`), the window's `gaps`, `restated_count` and `last_restated_at`, `cash_negative_count` and the newest row (`latest`) |
| The service | `PerformanceService.book_performance`, `compare_books` (and `_book`, `_base_and_rows`, `_benchmark_bars`, `_performance`) | Reads the stored rows and the window's sales; the one possible network call is SPY's closed bars. Raises `BookNotFoundError` for another owner's book |
| The routes | `GET /api/books/performance?period=&book_id=`, `GET /api/books/performance/compare?period=` in `api/routers/books.py`; models in `api/schemas/books.py` | 404 for a book that isn't the owner's, 422 for an unknown period. 131 routes |
| The section | `BookPerformance.tsx`, `PerformanceChart.tsx`, on the Portfolio page between Realized and Cash (`#performance`) | Return, SPY, vs SPY, max drawdown and book value; the growth-of-$1 chart with SPY dashed; one note each for negative cash, restated rows, gaps, an incomplete or stale newest row and a missing benchmark; realized by source; the dividend and split caveat |
| The strip | `BookSummaryStrip.tsx` | "Book value, <date> close" from the newest snapshot and "Return, last year" with "vs SPY"; "(unreliable)" when cash went below zero |
| Compare | `CompareBooksPage.tsx` at `/books/compare` (`nav: false`) | Every open book in one chart and a table: value, return, vs SPY, drawdown, realized; paper and unreliable chips; each name links to the Portfolio page with that book selected |
| The card | `BookPerformanceCard.tsx`, `book_performance` in `BACKEND_COMPONENT_REGISTRY` and `componentRegistry.tsx` | No props (decision 104) |
| The tests | `tests/test_book_performance.py` (service and routes against the database), `tests/test_performance_analytics.py` (`BenchmarkIndexTest`, `BookPerformanceTest`), `tests/test_realized_pnl.py` (`TestRealizedBySource`), `tests/test_chat_tools.py`; vitest for each new component, the strip, the page, navigation and registry parity | Wrong-owner and wrong-book reads, the base row, a failed SPY read, the stand-in, negative cash, a restated row |

**Nothing for an operator to run.** No migration; the routes read what 5.1 and 5.2 write.

Left, on purpose:

- **No dividends, and no split handling** (#391). Both sides are price returns, and a split
  reads as a fall. Said under every figure.
- **No Sidekick tool or MCP tool yet** (5.4). The card shows the section; the model can't read
  the figures.
- **Not run against Yahoo.** The tests stub SPY's bars. On test, check that the first read
  fetches SPY once and the next is served from stored bars.

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
- **The backfill is mirrored into `_SCHEMA` after all** (found in 1.1; the plan first said to
  follow `V8__close_orphan_plans.sql` and leave it out). `init_schema()` also runs on create-mode
  databases that already hold position rows, such as a local or unit-test database. There,
  `SET NOT NULL` fails without the backfill. Both statements are idempotent, and both are cross-owner
  on purpose, so each has a reasoned `ALLOWED` entry in the owner and book coverage tests.
- **A snapshot regenerated on Postgres 18 is wrong for CI** (found in 1.1).
  `check_schema_snapshot.py --update` on the local PG18 wrote 159 `*_not_null` constraint rows
  that Postgres 16 (CI) doesn't report. They were stripped by hand; the diff for a schema change
  should only show the objects that change added.
- **`book_id` is NOT NULL with no default, so the roll-out has a short gap** (accepted in 1.1). A
  revision from before V14 writes no `book_id`, so its Add Lot fails between the migrate step and
  the new revision serving. Rolling an image back past V14 breaks lot inserts for the same reason,
  which is the forward-fix-only rule doing its job. A default was rejected: it could not name the
  right owner's book.
- **Harvester plans are not book-scoped** (decided in 1.1). `plan_instances` has a foreign key to
  `positions`, so the book coverage test derives it; it is listed in `NOT_BOOK_SCOPED` because a
  plan follows the owner's real holdings. A paper-book plan would need that revisited.
- **`_row_to_dict` still does not return `book_id`** (checked in 1.2). The page already knows
  which book it asked for, so no reader needed it. Add it when a payload mixes books, which the
  Compare view (5.4) may.
- **Only the reads take a book in 1.2.** `GET /portfolio/lots`, `/symbols` and `/sales` accept
  `book_id`. The lot writes (add, edit, delete, close) stay real-only, because the system sets
  every paper fill and paper orders arrive in 3.5. So a paper book made through the UI is empty
  until then, and the page hides the lot actions as well as Add Lot. The read tests put paper
  lots in through the repository.
- **The Real chip sends no `book_id`** (1.2). It selects "no book", which the server reads as the
  real book. Sending the real book's id would work, but an owner who has never written anything
  has no book row, and a read must not create one. The selector therefore draws a Real chip even
  when `GET /api/books` returns an empty list, is still loading, or fails.
- **A book that isn't the owner's is a 404, not an empty table** (1.2). The repository SQL returns
  no rows for such an id, which would look like a book that holds nothing. `PortfolioService`
  looks the book up first (`_require_book`) and raises `BookNotFoundError`. That lookup runs only
  when a book is named, so the default read still touches no `books` row. The error is a
  `ValueError` subclass, so a route must catch it before `ValueError` to answer 404 and not 422.
- **A paper book shows no Plan column** (1.2). Harvest plans follow real holdings (see the 1.1
  note above), and `symbol_rows` skips the plan lookup for a paper book. Without that, a paper
  row for a symbol the owner also holds for real would have shown the real plan's chip.
- **An archived book is still readable by id** (1.2). Archiving takes it out of the selector
  (`GET /api/books` leaves it out unless `include_archived=true`), but `?book=<id>` on the page
  still opens it, read-only, with a banner that can't name it. There is no un-archive yet.
- **"Read-only" and "paper" are two questions on the page** (1.2, #547 review). A named book is
  editable only once the loaded list says it is the real one. It is called paper (the banner, the
  empty-state text) only once the list has loaded. While the list loads, or after it fails, the
  page shows no banner and no Add Lot. The first version asked one question and so called the
  real book paper when it was opened by its own id and the list failed.
- **Round a share count before the arithmetic, not after** (1.3). `NUMERIC(18,6)` rounds whatever
  it is given, silently. A partial sale writes two numbers derived from one input (the shares sold
  and the remainder), and rounding each on its own does not conserve the total. `_quantity` in
  the portfolio service rounds the input once. Paper orders (3.3) and seeding (3.5) write lots
  too and must go through the same function.
- **A whole count stays an `int` in the stop-loss payload** (1.3). Making `shares` a float on the
  route would have turned "10 shares" into "10.0 shares" in the summary and broken the golden
  tests; `stops.share_count` converts it back.
- **`formatShares` is the only way a share count is shown** (1.3). Two components printed the raw
  number and one used `toLocaleString`, and each showed a different number of decimals. The
  Realized view (2.2) and the orders list (3.3) should use it from the start.
- **Moving a block does not lower its complexity, only its address** (1.4). The plan said to
  extract the per-stock row builder. Moved as it was, the report's row dictionary would have
  taken its 17 conditional expressions with it and the helper would have measured about 18. The
  rows are built from two tuples of field names and three one-line "or N/A" helpers instead.
- **The report's "N/A" rules are not uniform, and the refactor kept them** (1.4). In the
  holdings table a moving average or return is "N/A" only when the stock has no metrics, so a
  zero prints as 0. `percent_change_today` is "N/A" at zero as well, and is read without checking
  for metrics, so a stock with `metrics = None` raises. In the watchlist table every zero is
  "N/A". `html_summary.py` raises on a stock with no current price, because "N/A" can't take the
  `.2f` format. Each is pinned by a test now; none was changed, because 1.4 changes no behaviour.
- **A test that draws with matplotlib must pick the Agg backend first** (1.4). On a Mac the
  default backend starts a GUI application from inside the test run. CI is headless and never
  shows it. `tests/test_html_summary.py` calls `matplotlib.use("Agg")` before importing the script.
- **The isolation scans refuse an `OR` in a WHERE clause** (2.1). The optional date window was
  first written `(:start IS NULL OR sale_trade_date >= :start)`, which both the owner and the book
  scan failed, because an `OR` can widen the restriction beside it. It is
  `sale_trade_date >= COALESCE(:start, sale_trade_date)` instead: still bound, nothing formatted
  into the SQL, and no `ALLOWED` entry. It relies on the column being NOT NULL.
- **Realized P/L does not require the lot to be CLOSED** (2.1). #235 says "`lot_sales` rows joined
  to their CLOSED parent lot". Every sale `close_lots` writes is on a CLOSED lot, but
  `insert_sale` (used only by tests today) can put a sale on a lot that stays OPEN. Filtering on
  the status would hide a sale that `GET /api/portfolio/sales` lists, so the read takes every
  `lot_sales` row and uses `shares_sold`, never the lot's `quantity`.
- **`fx_rate_at_sale` is in the payload before the column exists** (2.1). The repository returns
  it as None so that 2.3 changes one line of SQL and no shape. Until then every non-USD sale is
  flagged `fx_missing` (decision 17), and the 2.2 view has to show that count from the start.
- **Rounding each sale, then adding, is what makes the totals reconcile** (2.1). #235 requires
  that the total equals the transactions shown. A symbol total computed from unrounded figures can
  be a cent off the sum of its rows.
- **`still_held` costs a second read** (2.1): the book's open lots. It is one query for the page,
  and it is why `realized_pnl` takes the held symbols as an argument and reads nothing itself.
- **The capabilities matrix had not counted the three `/api/books` operations** (found in 2.1).
  Its totals were corrected in the same PR: 115 operations, 62 reached.
- **The strip and the Realized section share one request, until they don't** (2.2). Both ask for
  the Year period with the same query key, so the page makes one realized request on load. When
  the section's period changes it makes a second, and the strip stays on the Year figure. A
  strip that read the section's data instead would have relabelled a 30-day figure "last year".
- **A book with no open lots still has a Realized section** (2.2). The page used to show
  `EmptyPortfolio` in place of everything when there were no holdings. A fully sold book is
  exactly the one whose realized figures matter, so only the Holdings section is empty now.
- **Refresh re-reads the holdings only** (2.2). Realized figures don't depend on prices. A close,
  a lot edit and a sale-note edit all invalidate the realized query.
- **A second table on the page breaks page-wide test lookups** (2.2). `getByRole('table')` was how
  the page tests found the holdings; they use `holdings-table` now. A catch-all mock route such
  as `'/api/portfolio'` also started answering the realized request with the wrong body. The
  1.4 note about ticker text applies again: the realized chart and table repeat the symbols.
- **The realized chart must not sort by size** (2.2). The allocation chart orders its bars
  largest first. Reused as it was, it would have reordered the realized rows away from decision
  14's "best first", and put its bars in a different order from the table beneath it.
- **A `;` inside a `--` comment in `_SCHEMA` breaks every connection** (2.3). `init_schema()`
  splits the DDL on semicolons, so the comment's second half became a statement and the first
  half an empty query: 108 tests errored with "can't execute an empty query". Migration files are
  not split this way, so the same comment is fine in `db/migrations/`.
- **Adding a column to `SQL_INSERT_SALE` changes its fingerprint** (2.3). The owner and book
  isolation scans key their reviewed exceptions by a hash of the SQL, so both `ALLOWED` entries
  for that INSERT had to be re-keyed. The reason on each still holds: the lot is read owner- and
  book-scoped on the same connection first.
- **The backfill goes owner by owner on purpose** (2.3). One cross-owner UPDATE would have been
  shorter, and would have needed four `ALLOWED` entries in the isolation scans. Looping
  `list_owners()` keeps every statement bound to an owner and a book, with none.
- **`list_owners()` skips an owner who holds only paper positions** (2.3), so the backfill does
  too. Paper orders are USD-only for now (section 3), so there is nothing for it to fill there.
  Revisit when a paper book can hold a non-USD lot.
- **A same-day rate is not the day's close** (2.3). FX trades around the clock, so Yahoo's bar
  for today is still forming when a trade is recorded today. The stored rate is that moment's;
  a backfill of the same date later would store the final close. Accepted: both are within the
  day's range, and the alternative (yesterday's close) is further from the trade.
- **In tests a rate lookup is a soft miss, not an error** (2.3). `tests/__init__.py` blocks
  Yahoo, `yf.download` swallows that and returns nothing, and the lot is written with no rate.
  So every existing test that adds a non-USD lot kept passing unchanged; a test that wants a
  rate stubs `fx_daily_closes`.
- **Pence can't be told from pounds** (2.3, not fixed). A lot's currency is stored upper-cased,
  so Yahoo's `GBp` becomes `GBP`, and a price typed in pence would convert at the pound rate:
  100 times too high. Lots are entered by hand, so the prices are whatever the user types; the
  docs say to enter pounds. A check against the exchange's quote currency would be a new issue.
- **A lot's currency can be edited after it has sales** (2.3). The sale-date rates on those sales
  were for the old currency, and the backfill only fills a NULL, so it would never have corrected
  them. `update_lot` clears them in the same transaction as the edit, and the service looks them
  up again afterwards. A trade-date edit needs none of this: it moves only the purchase rate.
- **An existing test file gains tests without losing its claim to be unchanged** (2.4). The step
  says the existing tests must pass unchanged, and asks for a direct test of each helper. The
  existing test methods were run before any test was added and none was edited. The new cases are
  new classes: appended to `tests/test_portfolio_math_alloc.py` and the book scan, and in a new
  `tests/test_portfolio_row_helpers.py`, which needs no database.
- **`_text` and `_or_none` exist twice, on purpose** (2.4): once in the repository and once in the
  service. A service importing a repository's private names would be the worse trade for two
  one-line functions. One test class runs the same cases against both copies.
- **`_text` does not treat a blank string as empty** (2.4, kept). `(v or default).strip()` uses
  the default only for NULL or `''`, so a currency stored as `'   '` reads as `''`, not `'USD'`.
  That is what the code did before; a test pins it. Changing it would be a behaviour change.
- **Quote the analyzer's version with every complexity figure** (2.4, #556 review). complexipy
  8.0.1 and 9.0.0 disagree by up to 7 points on the same function (the old `_child_ok` is 20 in
  one and 13 in the other), so a figure without its version can't be checked by anyone else.
  And "the tool failed on this file" may mean it exited 1 because one unit is over its
  threshold, not that it could not read the file. Look at the output before the exit status.
- **A side-by-side comparison must not use `==` on the results** (2.4). `Decimal('NaN')` is not
  equal to itself, so the first run "failed" on two identical dicts. Comparing `repr()` fixed
  that and also checks the key order.
- **`purchase_price` is a `REAL` column, and `REAL::NUMERIC` keeps six significant digits** (3.1).
  A lot bought at 1234.567 came out of the first buy-row SQL as 1234.57 a share. Python never
  sees this, because psycopg2 reads the column's text. The SQL now casts through `TEXT`
  (`CAST(CAST(purchase_price AS TEXT) AS NUMERIC)`), and a test holds a 1,000-share lot to the
  cent. Any later SQL that does arithmetic on `purchase_price` needs the same cast.
- **A helper module, because two repositories need the same rows** (3.1). `PortfolioRepository`
  writes the trade rows and `BooksRepository` the paper opening balance, while `CashRepository`
  needs `BooksRepository.resolve_book_id`. Putting the shared SQL in `CashRepository` would have
  been an import cycle, so it is in `cash_entry_sql.py`, which imports no repository.
- **SQL on a book-scoped table can't live in `books_repository.py`** (3.1). The book scan's
  signature check covers every public method of a module whose SQL touches a book-scoped table,
  and `BooksRepository.get_book(owner, book_id)` rightly has a required `book_id`. One
  `cash_entries` statement in that file would have failed it.
- **A table with a foreign key to `books` breaks every test purge that deletes books** (3.1).
  `cash_entries.book_id` has no cascade, because a book is never deleted. A test that makes a
  paper book through `BooksService` now has an opening row, so its purge deletes
  `cash_entries` before `books` (`tests/test_books_api.py` was the one existing suite affected).
- **`Decimal.quantize` and Postgres `ROUND` disagree on a half cent** (3.1). Python's default
  rounds 10.005 to 10.00; Postgres rounds it to 10.01. `analytics.cash.to_cents` rounds half up
  so a typed amount and a derived one round the same way.
- **A partial close can move a book's cash by a cent** (3.1, accepted). Each half of a split gets
  its own buy row, each rounded to the cent, so the two can sum to a cent more or less than the
  one row they replace. The alternative, giving the remainder whatever is left, would make the
  backfill and a later edit of that lot disagree with the write path.
- **A dry run that rolls back is exact, and costs no second set of SQL** (3.1). The cash backfill
  runs its real INSERTs and rolls them back without `--apply`, so the counts it prints are the
  rows an applied run writes. A separate counting query could have drifted from the INSERTs.
- **One new cross-owner statement** (3.1): `SQL_LIST_BOOK_OWNERS`, the backfill's walk. 2.3's
  backfill avoided one by using `list_owners()`, but that skips an owner who holds only paper
  positions or only an empty paper book, and those are exactly the books that need an opening
  row. It returns owner names only and has a reasoned entry in the owner scan's `ALLOWED`.
- **The new section's request reaches every Portfolio page test** (3.1). `mount()` in
  `PortfolioPage.test.tsx` gives it a default route placed after the test's own routes, because
  `mockApi` takes the first match. The test that lists the page's requests gained the cash URL.
- **The dev server could not be run against a local API from the agent session** (3.1). The
  sandbox refuses a write to `.claude/launch.json`, and the checked-in config starts only the
  front end. So the Cash section was verified by vitest and by the API tests, not in a browser.
- **`is_market_open` does not know about holidays or early closes** (3.2, for 3.3). Its docstring
  says so. On a holiday it answers True from 9:30 to 16:00, so an order placed then would be
  treated as in-hours, find a stale quote and be refused, when #431 says it should queue. On a
  half day the same happens after 13:00. Deciding "fill now or queue" needs `is_trading_day` as
  well, or the quote's own `market_state`.
- **"Stale" already means something else in the prices service** (3.2, for 3.3). A quote-cache
  entry's `stale` flag and `quote_as_of` are about when QuantCore fetched it. A fill must be
  refused on the age of the last trade, which is `Quote.regular_time`. A quote fetched one
  second ago can carry a trade from yesterday.
- **Yahoo's quote has no "halted" flag** (3.2, for 3.3). The plan lists a stale quote and a
  halted symbol as two refusals. With the fields there are, they are one test: the market is
  open and the last regular-session trade is older than the limit. The reason shown can't tell
  them apart, and decision 29's list has one entry for both.
- **A queued order must record its session when it is placed** (3.2, for 3.3 and 3.4). Worked out
  later, on a job that runs a day late, "the next session" would be the wrong day. The opening
  price for that session then has two possible sources: the stored daily bar, or
  `Quote.regular_open` on the day itself. 3.4 has to pick one and say what happens when the job
  runs after that day.
- **A quote still in pre-market after 9:30 is refused, not queued** (3.3). The clock and the
  calendar say open and Yahoo's `market_state` says `PRE`, which happens for a moment at the
  open. Queued, the order would go to tomorrow's open. Rejected as `stale_quote`, the owner can
  place it again a minute later. Only a state that says the session is over (`POST`,
  `POSTPOST`, `CLOSED`) queues an order while the clock says open: that is the half day.
- **Cash is checked to the cent, so a buy can cost a fraction of a cent more than the book
  holds** (3.3, accepted). 20.000001 shares at $50.00 is $1,000.00005, which is $1,000.00 in the
  ledger, and fills against $1,000.00 of cash. The check reads the ledger's own rows, so it
  can't disagree with the balance shown.
- **A fill price of $100,000 or more can lose its cents in the lot** (3.3, not fixed).
  `positions.purchase_price` is `REAL`, seven significant digits. The order keeps the exact
  price in `fill_price`; the lot and its cash row use what the column stored. One US listing
  trades up there. Fixing it is a column change (`ALTER … TYPE`, a hand-applied migration).
- **A new foreign key makes a new parent in the isolation scans** (3.3). `lot_sales.order_id`
  made `orders` a second per-owner and book-scoped parent of `lot_sales`, and both scans'
  derivation tests pin the parent set. The scans' rules were not loosened: a `lot_sales`
  statement still has to be tied to a parent that is restricted.
- **The scans read one SQL literal at a time** (3.3). The order queries first shared their
  column list through string concatenation, which left the scans looking at a fragment that
  starts with `FROM` and is not classified as a statement at all. They are written out in full.
- **The pre-check for a repeated key is a saving, not the guarantee** (3.3). `place_order` looks
  the key up before it asks for a quote, so a repeat costs no Yahoo call. Two requests that
  arrive together both pass that look-up. The insert's `ON CONFLICT DO NOTHING`, behind the
  book lock, is what makes the second one a replay; a test patches the look-up away to prove it.
- **A test that deletes `symbols` rows must delete `orders` first** (3.3). `orders.symbol_id` has
  no cascade, like `orders.book_id`. `tests/test_orders_repository.purge` has the order:
  positions, cash entries, orders, books, then symbols.
- **Stripping the Postgres 18 rows from a regenerated snapshot is a few lines of script** (3.3).
  Load the committed snapshot from `git show HEAD:db/schema_snapshot.json` and drop every
  `*_not_null` constraint the new file has that the old one lacks. 176 rows this time. Keep the
  committed file's indent, or the whole file shows as changed.
- **A symbol halted less than five minutes ago still fills** (3.3, #560 review, accepted). The
  halt refusal is the last trade's age, so inside the five-minute window the order fills at the
  price before the halt. Revisit if Yahoo ever exposes a halt flag.
- **A cap one above what the column holds is no cap** (3.3, #560 review). `MAX_ORDER_SHARES` was
  1e12, and `NUMERIC(18,6)` stops just short of it, so exactly 1e12 shares passed the check and
  failed at the INSERT as a 500. A dollar buy's share count was not capped at all: a large
  amount at a sub-dollar price overflowed the same column. Both are a 422 now.
- **The page must not promise a step that isn't built** (3.3, #560 review). The pending notice
  said a queued order fills at its session's opening price and shows that evening. That is 3.4.
  It now says nothing fills a waiting order yet. **3.4 must put the promise back**, in
  `OrdersList.tsx` and its test.
- **The Orders list was not checked in a browser** (3.3), for the reason given under 3.1. It is
  covered by vitest and the API tests.
- **The fill step was not run against Yahoo** (3.4). Every test stubs the history fetch. That
  Yahoo's daily history already holds the day's bar at 17:00 ET is taken from the OHLCV
  close-out, which makes the same fetch later in the same job and stores those bars `CLOSED`.
  If it turns out not to hold, the cost is the alarm that night and the fill on the next run,
  at the same price. Watch the first night an order is queued on test.
- **The alarm fires the first night an order waits** (3.4), not only when it gives up. A due
  order that is still pending after the step is a fill that did not happen, whatever the cause.
- **`session_open` works out its window from the real date, not the caller's `today`** (3.4).
  `get_bars` cuts its window from the clock, so a window sized from a test's pretend "today"
  would miss a session months back. It is the session's age in real days plus ten.
- **A symbol nobody has looked at costs a full history download** (3.4). `session_open` reads
  through `get_history`, which warms 730 days for a symbol with no bars. That is on purpose: a
  short window stored for a cold symbol stops `get_history` warming it later (the close-out
  leaves such symbols out for the same reason). It happens once per symbol.
- **Each order for a symbol whose fetch came back empty fetches again** (3.4). `get_history`
  only stops fetching once the bar is stored. Ten queued orders on a delisted symbol are ten
  downloads a night for three nights. The budget bounds it; a per-run memo was left out.
- **The job's budgets now add up to more than its timeout** (3.4): 120 + 600 + 300 + 900
  seconds against 1,800. They did before, counting the close-out's 300. `_remaining_budget` is
  what keeps a late step inside the deadline.
- **`_fill` now raises when its order is not pending** (3.4). In 3.3 the order had just been
  inserted in the same transaction, so the `UPDATE`'s row count was not checked. A queued fill
  reads the order earlier, and a fill that wrote a lot and then matched no order would be a lot
  with no order. `fill_queued_order` reads the order again under the lock first; the raise is
  the backstop, and it rolls the lot back.
- **A lot read through `PortfolioRepository` does not return its `order_id`** (3.4). The test
  that checks a filled lot names its order reads the column directly.
- **A multi-column `CHECK` written inside `ADD COLUMN` gets the name `orders_check`** (3.5).
  Postgres names it for the table, not the column, so the snapshot showed a constraint nobody
  would recognise. V18 names it (`CONSTRAINT orders_kind_rule`).
- **A daily bar's timestamp is midnight UTC, so its session is the UTC date** (3.5). Converting
  it to Eastern time gives the day before. `LastGoodPrice.session` carries the right date for
  the manual-close note.
- **The fills message is its own function, not part of `run_queued_fills`** (3.5). The tests for
  that step assert the exact calls it makes, in order.
- **A seeded lot has no `source` of its own** (3.5). The older gotcha below says it needs one.
  It has a kind instead (decision 35): the order is `kind = 'seed'` with `source = 'manual'`.
- **The Portfolio's symbol rows now carry `total_quantity`** (3.5). The Sell dialog needed the
  shares held, and adding lots up in the page would be display maths in the front end.
- **A try after a rejected order must send a new idempotency key** (3.5). With the old key the
  server replays the rejection, and the dialog would show the same refusal for ever.
- **A test that deletes `books` or `symbols` rows must delete `stops` first** (4.1). Both
  foreign keys are without a cascade, like the ones on `orders`.
  `tests/test_stops_repository.purge` deletes the stops and then calls the orders purge.
- **Setting a stop and closing a lot don't share a lock** (4.1, for 4.2). `set_stop` locks the
  book's row and then reads the OPEN lots. A real-book close takes no book lock, so a stop can
  be set in the moment its last lot closes. The close-when-flat step in 4.2 has to tolerate a
  stop that appears just after it looked, for example by closing stops for flat symbols on
  every checker run and not only on the closing write.
- **`analytics/stops.py` was already taken** (4.1). It places a stop for the stop-loss card.
  The stored-stop rules are in `analytics/stored_stops.py`, and the service module is
  `services/stops.py` because no service had the name.
- **A stop's method list and the request model's `Literal` are two copies** (4.1).
  `SetStopRequest.method` lists the four methods so the OpenAPI document shows them, and the
  service checks `analytics.stored_stops.METHODS`. A new method is added in both.
- **The snapshot and OpenAPI scripts need a database the sandbox can reach** (4.1). `.env`'s
  `QUANTCORE_DB_DSN` is the prod proxy, which is not running in an agent session. Both were
  run with `QUANTCORE_DB_DSN` set to the local unit-test DSN, and `check_openapi_snapshot.py`
  also needs `PYTHONPATH=.`. The Postgres 18 strip (3.3) dropped 188 rows this time.
- **A semicolon in a `_SCHEMA` comment breaks startup** (4.2). `init_schema` splits the DDL on
  `;`, so a comment that contains one leaves an empty statement and every entry point fails
  with "can't execute an empty query". The migration file is not split that way, so only the
  mirror shows it.
- **A stop can no longer be marked `triggered` through `resolve_stop`** (4.2).
  `stops_triggered_has_trigger` needs the bar and the price, which only `trigger_stop_on`
  writes. Two 4.1 tests used `triggered` as "any final status" and now use `closed`.
- **A paper stop is marked before its sale is written, not after** (4.2). The sale leaves the
  symbol flat, and `close_lots_on` closes a flat symbol's active stop in the same transaction.
  Marked afterwards, the stop would already be `closed` and the `triggered` update would
  match nothing.
- **`stops_repository` can't be imported by `orders_repository`** (4.2). It imports
  `SQL_LOCK_BOOK` from there. The statements both need are in `stop_sql.py`, as the cash rows
  are in `cash_entry_sql.py`.
- **The daily walk covers every owner in the database, test owners included** (4.2). A test
  that runs `run_stop_checks` on real services filters `due_stops` to its own owner, as the
  3.4 test does, or another suite's leftover stop changes its counts.
- **A "close to the stop" bar in a test must keep its low above the level** (4.4). The first
  draft of the nearest-first test put a stop at 91 under a bar whose low was 91: the stop
  was reached, not approached, and the test found one approach where it expected two. An
  approach is only ever recorded on a stop that held.
- **A push that fails can look like one that worked** (4.4). `git push -q 2>&1 | tail -1` on a
  branch with no upstream printed a blank line, and the review fixes stayed local while #568
  merged. Push with `-u` the first time, and check `git ls-remote origin <branch>` against
  `git rev-parse HEAD` before saying a fix is on the PR.
- **A lot's `trade_date` is a date and its `purchase_date` is text** (4.2). `sell_for_stop`
  compares the first ten characters of whichever is set with the session's ISO date.
- **`send_notifications` writes `notification.log` before it posts** (4.2). Locally, a message
  Discord refused, or one with no webhook to go to, is then skipped as a duplicate on the next
  try and looks sent. On Cloud Run the file starts empty each run, so the breach alert's retry
  works there.
- **A stop's snapshot rows were added by hand** (4.2). Four columns and one constraint, in the
  shape `check_schema_snapshot.py` writes, which avoids the Postgres 18 strip (3.3, 4.1).
  `tests/test_schema_parity.py` and the snapshot check both pass on it.
- **A `CHECK` passes when a comparison is NULL** (4.3). The first draft of `orders_stop_rule`
  said `stop_kind = 'fixed' AND stop_level > 0`. With no level that is NULL, the whole rule is
  NULL, and Postgres accepts the row. A test that inserts each bad shape caught it. Write
  `IS NOT NULL AND` in front of a comparison in a `CHECK`. The fix guarded the level and
  missed `stop_kind` itself, which the two branches compare: the #567 review found it, and V24
  adds the guard. The test inserts a bad shape per column, so add one when a column joins a rule.
- **`ADD COLUMN IF NOT EXISTS` skips a column that exists, with its constraint** (4.3). After
  the rule above was corrected, the local unit-test database still had the first draft, and
  the tests kept failing. `stop_set` was dropped there by hand so startup added it again. A
  migration that has been applied anywhere is never edited: this was a draft that had only
  run locally.
- **The snapshot rows can be copied by a script** (4.3). The snapshot was generated on the
  local Postgres 18 into a scratch copy, and only the `orders` columns and the one constraint
  were copied into the committed file. That gives Postgres's own spelling of the `CHECK`
  without the `*_not_null` rows (3.3, 4.2).
- **`PortfolioPage.test.tsx`'s `mount` lists its own symbols route first** (4.3). A route a
  test passes in never wins over it, so a test that needs its own holdings calls `mockApi`
  itself. The first stop tests passed for the wrong reason until one needed a later lot date.
- **A test pins the exact shape of a realized sale** (4.3).
  `test_a_sale_carries_its_lots_basis` compares the whole dict, so a new field on
  `list_realized_sales` is added there too.
- **A flow counted by date misses what is typed in late** (5.1). The first draft summed the
  deposits dated since the previous row. A deposit typed in after a row was taken, but dated
  before it, then showed in the next row's cash and in no flow: a false gain. A test written to
  pin the behaviour was what exposed it. Each row now stores what the ledger counted as
  contributed, and the flow is the change (decision 85). Any later reader that rebuilds flows
  from the ledger (5.2's backfill) must use the same rule. Lots have no such total, so a transfer or seed typed in
  late, dated on or before the previous row, still reads as a gain (#573's second review). It is
  documented and pinned by a test rather than fixed: counting a lot when it first appears needs the
  previous row's lots, which a row doesn't store. 5.2's backfill rebuilds those rows. John has
  since revised decisions 85 and 88 (2026-11-17): 5.2 restates the rows from a late change's
  date instead, which removes both cases.
- **`CREATE TABLE IF NOT EXISTS` skips a table that exists, with its old columns** (5.1). After
  `contributed` was added, the local unit-test database still had the first draft and every
  snapshot test failed with "column does not exist". The draft table was dropped there by hand
  so startup made it again. Like the 4.3 case, it had only ever run locally.
- **A test purge that deletes `books` must delete `book_value_snapshots` first** (5.1). The
  foreign key has no cascade, like `orders` and `stops`. `tests/test_orders_repository.purge`,
  which the other book suites call, does it.
- **`due_snapshots` walks every owner in the database, test owners included** (5.1), as the stop
  checker's walk does (4.2). The service tests filter it to their own owner.
- **The plan's conditional clear alone loses a change** (5.2). Clearing with `WHERE
  restate_from = <the date it read>` only notices a write that moves the mark earlier. One dated
  on or after the mark, made while the rebuild runs, leaves the mark as it was, so the clear
  succeeds and the write is never restated. The rebuild holds the book's row lock instead
  (decision 93).
- **A deposit dated before the opening balance isn't in the ledger** (5.2). The first draft of a
  rebuild test dated a deposit on the Saturday before a Monday opening balance and expected it
  as a flow; `cash_ledger` starts at the opening balance, so it counted nothing. Not a bug: the
  fixture was wrong.
- **A change dated before a book's first row only changes the base** (5.2). A test expected a
  Monday deposit to be Tuesday's flow when Tuesday was the first row. The first row is the base
  (decision 83), so the deposit is in its value and its flow is never used. Also not a bug.
- **radon and complexipy can't be installed in an agent sandbox** (5.2): `uvx` isn't on the
  path and pip can't reach the index. The new units' complexity was counted by hand, and the
  rebuild's summary split out (`_summary`) to keep `_rebuild_in` small.
- **`test_schema_parity` can fail on its own cleanup when run beside other database suites**
  (5.2): "permission denied to terminate process" from `DROP DATABASE … WITH (FORCE)`, because
  another module's session was still on the scratch database. Run alone it passes.
- **A `mockApi` string matcher matches by `includes`** (5.3). `PortfolioPage.test.tsx` mocked
  `'/api/books'` for the book list, which also answered `/api/books/performance`, so the
  section got a list of books as its payload and the failure looked like a render bug. The
  list is now matched by a regex (`/\/api\/books(\?|$)/`); a new route under an existing prefix
  needs the same care.
- **`npx` fails in the agent sandbox** on root-owned files in the npm cache (EPERM). Run
  `node_modules/.bin/tsc` and `node_modules/.bin/vitest` directly.
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
  (5.3 kept it eager instead, decision 101: the strip needs the same read at the top.)
- **The summary strip fills in over three milestones.** Book value and unrealized exist at 1.2,
  realized arrives at 2.2 and return vs SPY at 5.3. A figure that isn't built yet is left out,
  never shown as a zero or a dash that reads as a real value.
- **A new daily-job step changes an ordering that is deliberate.** Steps 3.4, 4.2 and 5.1 each
  need a place in `main.py`'s order and an entry in `docs/architecture/daily-jobs.md`.
- **`check_schema_snapshot.py --update` against the local Postgres 18 rewrites every table.**
  PG18 names each `NOT NULL` as a constraint (`<table>_<column>_not_null`), which CI's Postgres
  doesn't, so the update adds hundreds of lines across all 28 tables. For 4.5 only the `stops`
  table's entry was taken from the regenerated file, without its `*_not_null` constraints, and
  the rest was left as committed. `tests/test_schema_parity.py` passed against the same server,
  since both sides of it come from PG18.
- **The owner-isolation coverage test derives per-owner tables from the snapshot.** Each new table
  with an `owner` column or a foreign key into one needs mapped wrong-owner tests in the same PR,
  or that test fails.

## 8. Checkpoint log

| Date | Step | PR | Notes |
|---|---|---|---|
| 2026-11-13 | Plan drafted | none yet | Decisions 1 to 3 (scope, order, real-book cash); P1 to P13 open |
| 2026-11-14 | Checker decided | none yet | Daily job, stop-level fills (replaces #431's close-fill rule), intraday noted as a later upgrade. Decisions 4 to 6; closes P6 |
| 2026-11-14 | Layout decided | none yet | Stacked sections. Decision 7; closes P1 |
| 2026-11-14 | Seven proposals decided | none yet | Decisions 8 to 14. All of #221 is now in scope (milestone 4 grows to five PRs, 20 in total); seeding is on top of starting cash. Closes P2, P4, P5, P7, P8, P10, P11 |
| 2026-11-14 | Compare books placed | none yet | Linked view from Performance. Decision 15; closes P3 |
| 2026-11-14 | Plan agreed | none yet | Decisions 16 to 18; closes P9, P12, P13, so none are open. Next: commit this document, then PR 1.1 |
| 2026-11-14 | 1.1 Books schema | #546 | V14: `books`, `positions.book_id` (NOT NULL, backfilled). `BooksRepository`; every `PortfolioRepository` statement restricted to a book, real by default. `tests/test_book_isolation_coverage.py`. No route or UI change: nothing can create a paper book outside tests until 1.2 |
| 2026-11-14 | Review fixes | #545 | Count corrected to 20 PRs; homes given to #431's delisted-symbol, book-vs-account and attribution items; P-numbers mapped to decisions |
| 2026-11-14 | 1.2 Books API and the book selector | #547 | `BooksService`, `/api/books` (list, create, archive), `book_id` on the three portfolio read routes (404 for a book that isn't the owner's), the selector, the paper banner, the Account column. Lot writes stay real-only until 3.5 |
| 2026-11-14 | 1.3 Fractional-share audit | #548 | Audit table in section 5.1. Fixed: `parseInt` in the Add Security dialog, share counts shown to 4 (or 3) decimals, sales and edits finer than six decimals, integer-only `shares` on the stop-loss route and MCP tool, `int()` in the legacy CSV readers, padded counts in the loss alert and report. Harvester's whole-share columns recorded for phase 4. The book work of milestone 1 is done |
| 2026-11-14 | 1.3 review | #548 | Two regressions from the rounding change fixed (a blank quantity on a lot edit, a missing share count on a sale). The review's three complexity warnings predate the PR; they became step 1.4, a separate PR |
| 2026-11-14 | 1.4 Complexity follow-up | #551 | No behaviour change. Row builders extracted from both `create_portfolio_html` functions and the underwater-lot block from the notifier; all three are now at or under the goals (section 5.2). A direct test for each helper, and `html_summary.py` has tests for the first time. Milestone 1 is done |
| 2026-11-14 | 2.1 Realized P/L backend | #552 | `portfolio_math.realized_pnl` and `market_time.rolling_window`; `PortfolioRepository.list_realized_sales` (owner, book and date window, all bound); `PortfolioService.realized_pnl`; `GET /api/portfolio/realized?period=&book_id=`. USD only: a non-USD sale is flagged and left out until 2.3. No UI and no schema change. Payload in section 5.3 |
| 2026-11-14 | 2.2 Realized view | #554 | The Portfolio page is stacked sections: summary strip, Holdings, Realized. `RealizedPnl` (period pulldown, summary, chart, sortable table, expandable sales, "still held", the left-out count, the empty states, the fees line), `BookSummaryStrip`, `useRealized`, and the Sidekick `realized_pnl` card in both registries. No route or schema change. As built, and what was left: section 5.4 |
| 2026-11-14 | 2.3 FX | #555 | V15: `lot_sales.fx_rate_at_sale`. `YFinanceGateway.fx_daily_closes`, `analytics/fx.py` (prior-close rule), rates captured on add, import, a date or currency edit and close, `scripts/backfill_fx_rates.py`, and the conversion line on a converted sale. A lookup that fails stores no rate and never fails the write. Closes #235. The backfill is still to be run on each environment (section 5.5) |
| 2026-11-14 | 2.3 review | #555 | Three units this PR added or grew were in the complexity warning band (radon, goal 10): `close_lot` 12 → 8, `fx_daily_closes` 11 → 4, the backfill script's `main` 11 → 7. Extracted `_sale_fx_rates`, `_currency_code`, `_daily_closes`, `_backfill` and `_parse_args` (all 5 or under), each with a direct test. No behaviour change. They were fixed here, not left for 2.4, because 2.4 is for units a PR did not change |
| 2026-11-14 | 2.4 Complexity follow-up | #556 | No behaviour change. The six units the #552 review measured are all at or under the goals (section 5.6): the sale order is a table and the draw its own function in `allocate`; the two row shapes are lists of fields over `_text`, `_or_none` and `_int_or_none`; the lot-field checks are `_require_positive` and `_require_iso_date`; the book scan's `_child_ok` and `_check_level` are split into four helpers. A direct test for each helper. Milestone 2 is done |
| 2026-11-15 | 2.4 review | #556 | Approved, with two suggestions about the cognitive figures, both answered in section 5.6: the figures are now given for complexipy 9.0.0 and for 8.0.1 (the review's version), and the review's "failed on the test file" is traced to 8.0.1 scoring the unchanged `_restricted_names` at 16. No code change |
| 2026-11-15 | `_restricted_names` follow-up | #557 | No behaviour change, test file only. The one unit complexipy 8.0.1 scored over the cognitive goal (16) is split into `_alias_for` and `_book_qualifiers`, each with a direct test (section 5.6). 8.0.1 now exits 0 on the book scan, so a review's preflight should be able to report its scores. Not a plan step: it came out of the #556 review |
| 2026-11-15 | 3.1 Cash ledger | #558 | V16: `cash_entries`. A lot's buy row and a sale's sell row are derived in SQL and rewritten in the same transaction as every write that can change them; the owner enters the real book's opening balance, deposits and withdrawals; a new paper book opens with its starting cash. `analytics/cash.py`, `CashRepository`, `CashService`, four `/api/portfolio/cash` routes, the Cash and orders section, the strip's Cash figure, the Sidekick `cash_ledger` card, `scripts/backfill_cash_entries.py`. Decisions 19 to 24 were made in the build and are John's to confirm. The backfill is still to be run on each environment, and the real book needs its opening balance (section 5.7) |
| 2026-11-15 | 3.1 review | #558 | Approved, with one suggestion: `CashLedgerCard`'s docstring cited "decision #20" for the no-props rule, which reads as this plan's decision 20 (no opening balance, no balance). The other chat cards mean #126's decision 20 by it. The comment now names the #235 `RealizedPnlCard` precedent and says which plan the number belongs to. Comment only, no code change |
| 2026-11-15 | 3.2 Prior-art review | #559 | No code. Read #222 and the Alpaca experiment (its one commit and John's uncommitted local changes). Section 5.8 records what the orders design adopts, drops and carries to later steps. Decisions 25 to 29 (pending means queued, all-or-nothing fills, one-session queued orders, checks at fill time, fixed rejection reasons) are John's to confirm before 3.3. Four gotchas for 3.3 and 3.4 added to section 7 |
| 2026-11-15 | 3.3 Orders and fills | #560 | V17: `orders`, `positions.order_id`, `lot_sales.order_id`, `source` and `source_ref`. `analytics/orders.py` (fill, queue or reject), `OrdersRepository` with one locked transaction per fill, `OrdersService` (place, cancel, list; paper books only), three `/api/portfolio/orders` routes, and the Orders list in the Cash and orders section. In-hours orders fill at the live quote or are rejected with a fixed reason; off-hours orders queue for the next session and wait for 3.4. John confirmed decisions 25 to 29 before the build, and decision 30 (orders as their own list) once the PR was open. Nothing for an operator to run (section 5.9) |
| 2026-11-15 | 3.3 review | #560 | Approved, with five suggestions. Fixed: the share cap is now the largest count the column holds and a dollar buy's computed share count is capped too (both a 422, were a 500); the pending notice no longer says queued orders fill, since 3.4 isn't built; a cancelled order's Result reads "Withdrawn". Accepted and written down: a symbol halted under five minutes ago still fills, and a fill at $100,000 or more loses its cents in the lot until `purchase_price` stops being `REAL` |
| 2026-11-15 | 3.4 Queued fills in the daily job | #561 | No schema change. `analytics.orders.queued_fill` (fill, reject or wait), `PricesService.session_open` (the open of the session's closed daily bar), `OrdersRepository.list_due_orders`, `OrdersService.queued_orders` and `fill_queued_order`, and the job step `run_queued_fills` with its alarm, second in `main.py`'s order behind `QUEUED_FILLS_BUDGET_SECONDS` (120). The Orders list promises the fill again. Decisions 31 to 34 (bar open as the price, wait up to three sessions before rejecting, the step's place and budget, archived books still fill) were made in the build and are John's to confirm. Not run against Yahoo (section 7). Nothing for an operator to run (section 5.10) |
| 2026-11-15 | 3.4 review | #561 | Approved, with one suggestion. Fixed: `OrdersService` now requires its `prices` argument. It was optional, and a service built without it failed with an `AttributeError` on its first queued fill. Confirmed as intended: the alarm fires on the first night an order waits for its bar. The review also noted that no test pins the step's place in `__main__`; that is still read from the code |
| 2026-11-16 | 3.5 Buy and sell on paper | #562 | V18: `orders.kind` (`market`, `seed`, `manual_close`). `OrdersService.seed_book`, `close_unquoted` and `fills_on`, `PricesService.last_close`, two routes (`/orders/seed`, `/orders/manual-close`), and the job's `[PAPER]` fills message after the queued fills. In the UI: `PaperOrderDialog` (Buy on paper on the Security page, Sell on a paper symbol row, the manual close), seeding from the New paper book dialog and from an empty book, and the Orders list's wording for the two new kinds. Symbol rows carry `total_quantity`. Decisions 35 to 41 (the kind column, a seed as an order, seed once, what counts as unquotable and its price, the manual-close mark, the evening fills message for John's books, selling by symbol) were made in the build and are John's to confirm. Not tried in a browser. Nothing for an operator to run (section 5.11) |
| 2026-11-16 | 3.5 review | #562 | Changes requested, two findings. Fixed: a manual close is now refused while a sell of the symbol is queued (it sold every share, so the queued sell would have been rejected at its session with nothing saying why); and the symbol rows' share total moved to `portfolio_math.total_quantity`, which puts `symbol_rows` back inside the complexity goal and gives the no-quantity case a test. The review did not re-run the suites and could not measure the front end's complexity |
| 2026-11-16 | 4.1 Stops table, service and routes | #563 | V19: `stops`. `analytics/stored_stops.py` (a fixed or trailing stop's terms), `StopsRepository` (set with replace, in one locked transaction; one conditional `UPDATE` out of `active`), `StopsService` (list, set, cancel; either kind of book) and three `/api/portfolio/stops` routes. A stop is on a symbol in a book, needs an OPEN lot, and sits below the last stored close. Owner and book isolation tests are mapped in both coverage tests. Decisions 42 to 47 were made in the build and are John's to confirm. No UI, and nothing checks a stop until 4.2. Nothing for an operator to run (section 5.12) |
| 2026-11-16 | 4.1 review | #563 | Two suggestions, both taken. A refused cancel re-reads the stop before naming its status, so a stop resolved between the read and the `UPDATE` is not reported as `active`. The readme's route count was stale (115) and is now 128; the capabilities matrix said 127 because it had never counted `GET /api/watchlist/quotes` (#518), which the Watchlist page reaches: now 73 of 128 reached, 55 left, with a row for it. |
| 2026-11-16 | 4.2 Bar-close checker | #564 | V20: `stops.last_bar_checked`, `triggered_session`, `trigger_price` and `alerted_at`. `analytics.stored_stops.check_stop` (a run of closed bars against a stop), `PricesService.closed_bars_since`, `stop_sql.py`, `StopsService.due_stops` and `check_stop`, `OrdersService.sell_for_stop`, and the job step `run_stop_checks` with its alarm, third in `main.py`'s order behind `STOP_CHECK_BUDGET_SECONDS` (120), then `report_triggered_stops`. A trailing stop rises with each bar's high; a bar whose low reached the level triggers the stop at the level, or at the open for a gap; a real book alerts once and a paper book sells the position in the same transaction; a flat position closes its stop. #431's fill rule is updated. Decisions 48 to 55 were made in the build and are John's to confirm. Not run against Yahoo. Nothing for an operator to run (section 5.13) |
| 2026-11-16 | 4.2 review | #564 | Approved, with one suggestion, taken. `send_notifications` returned None with no webhook set, which `send_stop_alert` counted as sent, so a real-book breach alert would have been marked delivered without going anywhere. It now returns False there, and only a skipped duplicate returns None. The deployed job has its webhook, so nothing was lost. The review did not re-run the suites and could not measure cognitive complexity for `notifier.py`, `prices.py` or the front end |
| 2026-11-16 | 4.3 Stops in the UI | #565 | V21: `orders.stop_kind`, `stop_level`, `stop_trail_pct`, `stop_method`, `stop_proposed_level` and `stop_set`. A paper buy can ask for a fixed or trailing stop (`analytics.stored_stops.requested_stop`, `order_stop_terms`); the stop row is written in the fill's transaction against the fill price (`stop_sql.set_stop_on`, `OrdersService._attach_stop`). In the UI: "Add a stop" in the Buy dialog with the stop card's candidates loaded only when it is opened (`OrderStopField`), a Stop column on holdings and lot rows (`useStops`, `StopCell`), and a Source column on each sale in the Realized view. Decisions 56 to 62 were made in the build and are John's to confirm. Not tried in a browser. Nothing for an operator to run (section 5.14) |
| 2026-11-16 | 4.3 review | #565 | Approved, with four suggestions, all taken. The readme's "No page shows a stop yet" was stale and now says no page sets or cancels one on a holding. A trailing stop whose percentage is too small to give a price below the fill got the fixed stop's wording ("below the price", "filled at or below it") though the owner gave no level: the 422 and the page now say the trail is too small. `_stop_request` let a `stop_method` through with no kind, and now refuses it like the other stop terms. `list_realized_sales` had reached cyclomatic 12 with the three new fields, so its row mapping is `_realized_sale_row`, over the helpers the other row shapes use. The review did not run the suites and could not measure the front end's complexity |
| 2026-11-17 | Decisions 56 to 62 against other systems | #566 | Not a plan step: John asked for the 4.3 decisions to be compared with market-leading systems and aligned. Section 5.15 records what was found. One change, decision 63: V22 `stops.first_session`, so the stop of a buy that fills at a session's open is tested against that session's own bar, as a broker's attached stop is live once its entry fills. Six questions with no norm to follow are listed there for John |
| 2026-11-17 | John's decisions 64 to 67 | #567 | John answered the six questions of section 5.15. Built: a queued buy that opens at or under its stop is rejected `stop_not_below_fill` (V23, **applied by hand**: it replaces a `CHECK`); `GET /api/portfolio/stops/holdings` gives the one stop each holding shows, picked on the server; the Portfolio page can take an active stop off; and the Buy dialog warns when a stop will replace one the symbol has. Decision 57 for an in-hours fill and decision 62 stand. 129 routes (section 5.16) |
| 2026-11-17 | Decision 63 review | #566 | Approved, with one suggestion, taken. A reached stop's tooltip said "Nothing was sold: a real book's stop is an alert" whatever the book. It is only reachable for the real book today, but `StopCell` and `stopDetail` are generic, so they now take the book's kind and say a paper book's position was sold. The review did not run the suites and could not measure the front end's complexity |
| 2026-11-17 | Decisions 64 to 67 review | #567 | Two reviews. Guppy found that V21's `orders_stop_rule` let through a requested stop with `stop_kind` NULL: `stop_kind = 'fixed'` is NULL there, the branch came out NULL, and a `CHECK` passes on NULL. The same trap as the 4.3 gotcha in section 7, missed on the column the branches compare: a comparison on a nullable column needs its `IS NOT NULL`. V24 replaces the rule with `stop_kind IS NOT NULL` in that branch (**applied by hand**, with V23), mirrored in `_SCHEMA` and the snapshot, with two database cases. Clippy approved with three suggestions: the capabilities matrix header still said 122 operations and 2026-11-13 (fixed); V21's header comment still describes `stop_set` FALSE, but V21 is applied, so it is left and `data.md` holds the right wording; and the V23 operator step, now V23 and V24. Neither review ran the suites. |
| 2026-11-17 | 4.4 Approach alerts | #568 | V25: `stops.approached_session`, `approach_close`, `approach_atr`, `approach_alerted_at`. `analytics.stored_stops.approach` (a close within `STOP_APPROACH_ATR_MULTIPLE`, default 1, of a 14-session ATR above the level), recorded by the check that held, from one read reaching back 90 days; `StopsService.unalerted_approaches` and `mark_approach_alerted`; the job's `report_approaching_stops` after the breach alert, once per approach and real book only. Decisions 68 to 73 were made in the build; John confirmed them as built the same day. Not run against Yahoo. Nothing for an operator to run (section 5.17) |
| 2026-11-17 | 4.4 review | #568 | Approved, with two suggestions, both taken. The checkpoint row said `#PR` (fixed in the PR's second commit). A message names at most 20 stops, and both the approach alert and the breach alert marked every owed stop, so one past the twentieth was marked as announced and never named. Now only the stops a message names are marked; the rest stay owed and are named on the next run, and the message says so. The review did not run the suites and could not measure `notifier.py`'s cognitive complexity |
| 2026-11-17 | 4.4 review fixes | #569 | The two fixes above never reached #568: the branch had no upstream, so their push failed, and #568 merged without them. #569 carries the same two commits. Its review approved, with one suggestion, taken: no test showed the stops a long list left out being named on the next run. Each long-list test now has a second run, against a fake whose owed list shrinks as stops are marked, which names the rest; a third run sends nothing. See the gotcha in section 7 |
| 2026-11-17 | 4.5 The stop panel | #570 | V26: `stops.at_broker`, `broker_quantity`, `broker_order_type`, `brokerage`. `analytics.stored_stops.broker_order`; the share count checked under the book's lock; `StopsService.set_stop` and `POST /api/portfolio/stops` take the four; a mirrored stop's alerts say where it was placed and that its order should have filled. `StopPanel` on the Security page's Technical Analysis tab sets a stop from the stop card's candidates, with their reasoning, or by mirroring a broker order (real book only). Closes #221 and milestone 4. Decisions 74 to 81 were made in the build; John confirmed them as built the same day. Not tried in a browser. Nothing for an operator to run (section 5.18) |
| 2026-11-17 | 4.5 review | #570 | Two reviews. Clippy requested changes for one warning: `test_a_broker_mirror_over_the_route` sat after `unittest.main()` under the `__main__` block, so no runner collected it. It is now a `StopsRoutesTest` method, passes, and also checks that the two refusals wrote nothing. Two suggestions, both taken: `broker_order` (cyclomatic 14) checks each detail in its own helper, and switching book in the stop panel clears the broker draft. Guppy reported no findings; its gaps were a truncated diff, a Python run in its sandbox whose failure it could not name, and #221's intraday checker and Add Lot field, which decisions 4 and 6 and section 5.18 cover. Neither review ran the suites here; the stop suites and the front end pass locally |
| 2026-11-17 | 5.1 Snapshots and the maths | #573 | V27: `book_value_snapshots`, one row a book and session with cash, holdings at the newest closed bar in USD, the book value, the flow since the previous row and what was contributed. `analytics.performance` (`book_value`, `return_index`, `time_weighted_return`, `max_drawdown`); `BookSnapshotsRepository`; `PerformanceService`; `PricesService.close_on`; the job's `run_book_snapshots`, last, after the close-out, with an alarm for any book still owed a complete row. Decisions 82 to 89 were made in the build and are John's to confirm. Not run against Yahoo. Nothing for an operator to run (section 5.19) |
| 2026-11-17 | 5.1 review | #573 | Clippy commented with two suggestions, both taken. `PricesService.close_on` had no direct test: `CloseOnTest` now covers the session's own close, an earlier one, a later bar never used, nothing in the window and a fetch error. `book_value` returned a `cash_negative` flag that nothing stored or read; it is gone, and decision 8's days are read from the stored `cash`. The review did not run the suites, and complexipy could not measure `performance.py`, `prices.py` or `notifier.py` |
| 2026-11-17 | 5.1 second review | #573 | Clippy commented with one suggestion, taken as its documentation option: a lot that cost no cash, typed in late with a date on or before the previous row's session, is in no flow and reads as a gain. The module docstring, `data.md` and the 5.1 gotcha say so, and a test pins it. The docstring now also says that with no previous row only a lot dated on the session counts, which another test pins. The review did not run the suites |
| 2026-11-17 | Decisions 82 to 89 | #575 | John ruled after a comparison with Alpaca, Interactive Brokers and GIPS: 82, 83, 84, 86, 87 and 89 confirmed as built; 85 and 88 revised to restatement, with a late flow on its real date and the affected rows rebuilt. Step 5.2 now builds that alongside the backfill, and decision 89 points at 5.3 for the route and screen. Docs only |
| 2026-11-17 | 5.2 History backfill and restatement | #576 | V28: `books.restate_from`, `book_value_snapshots.restated_at`. Every owner write to a book's cash or lots dated on or before its newest row marks it in the write's transaction (`restate_sql.mark_restate`); the daily step rebuilds a marked book from the mark through today under the book's row lock, with each flow on its real date, stamps a changed row `restated_at`, fills a hole and clears the mark. `PortfolioRepository.list_lot_history`, `analytics.performance.rebuild_snapshots`, `PerformanceService.restate_book` and `backfill_book`, `scripts/backfill_book_snapshots.py`. Decisions 90 to 97 were made in the build; John confirmed them as built the same day. Not run against Yahoo. The backfill is still to be run on prod (section 5.20) |
| 2026-11-18 | 5.3 Performance and Compare views | #577 | `GET /api/books/performance` and `/performance/compare` (131 routes); `analytics.performance.book_performance` and `benchmark_index`, `portfolio_math.realized_by_source`, `PerformanceService.book_performance` and `compare_books`. The Portfolio page's Performance section, the strip's book value and return vs SPY, the Compare books view at `/books/compare`, and the no-props `book_performance` Sidekick card in both registries. Decisions 98 to 105 were made in the build. No migration. Not run against Yahoo |
