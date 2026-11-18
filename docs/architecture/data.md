# Data: unified database, positions, watchlist

> Moved verbatim from `CLAUDE.md`, which keeps the rules an agent must not break as a short
> summary and links here for the explanation. Edit the detail here, the rule there.

## Unified Database (`quantcore/`)

All persistence is consolidated into a single **QuantCore** PostgreSQL database, accessed via `psycopg2`:

- **`quantcore/db.py`** — Shared connection factory (`get_connection()`) backed by `psycopg2`, connecting via the `QUANTCORE_DB_DSN` environment variable. Centralized schema DDL for all 29 tables (`init_schema()`), using `SERIAL` primary keys and `ON CONFLICT` upserts. Imported as `from quantcore.db import get_connection`.
- **Schema** includes: symbols, OHLCV (merged from daily + intraday intervals), fetch_log, books/positions/lot_sales/cash_entries/orders/stops/book_value_snapshots/owner_identities, watchlist (the global shared list, #83), plan_templates/instances/rungs/alerts (Harvester), options_snapshots/expirations/contracts/options_capture_claims/gamma_wall_history/gex_history/options_positions (per-owner since V12, #460), news_articles, sentiment_snapshots, fundamentals_history, user_settings (per-owner UI preferences: the Sidekick chat model and daily token cap), chat_usage (per-owner, per-ET-day Sidekick token use and outstanding reservations, G2 #461), arb_nav_snapshots (curated holdings/capital-structure history for the arbitrage scanner's NAV vehicles).

All repositories under `quantcore/repositories/` and the REST API (`api/main.py`) use the shared factory instead of managing individual database connections.

**`ohlcv` has two history writers, and they disagree about `adj_close`.** (A third writer, the
live quote writer, writes only today's forming bar and leaves `adj_close` alone; see
[The live quote writer](#the-live-quote-writer).) `OhlcvRepository` (the prices
cache) fetches with `auto_adjust=True`, so the adjustment is already baked into `close` and it
writes `adj_close = NULL`; `HarvesterPlanDB.build_plan` fetches with `auto_adjust=False,
include_adj_close=True` and writes a real adjusted close for the same `(symbol, '1d', ts)` rows.
Two rules follow, and both exist because breaking either produced the same crash — `float(None)`
escaping as a bare `TypeError` into the Create Plan dialog:

- **A price refresh may fill `adj_close`, never blank it.** `OhlcvRepository`'s upsert uses
  `adj_close = COALESCE(EXCLUDED.adj_close, ohlcv.adj_close)`; with plain `EXCLUDED` a routine
  symbol lookup silently destroyed every adjusted close the harvester had computed.
- **A window measured in bars must be fetched in trading days.** `PlanBuildParams.history_window_days`
  is a row count (`LIMIT history_window_days`), so `HarvesterService.build_plan` scales it by
  `365/252` before calling `fetch_history`, which takes calendar days. The old `max(n + 60, 420)`
  covered ~288 sessions of a 360-bar read, and the shortfall was served by whatever the prices cache
  had left there. When a gap survives anyway, the repository raises a `RuntimeError` naming it
  (→ 422) rather than substituting `close` — an unadjusted bar beside adjusted ones is a fake step
  that inflates the volatility sizing the ladder.

**The prices cache drops bars the table would reject; the constraint stays** (#369). yfinance
sometimes returns impossible bars. For NIB.F it returned a daily bar at −4.08 across all of OHLC,
and because `_store_bars` writes the whole fetch in one `executemany`, `CHECK(close > 0)` failed the
whole batch, and `get_stock_price` with it (a 500). `_store_bars` now drops any bar with a
non-positive or NaN price, or a negative volume, before the upsert, and logs one warning naming the
symbol and the count. If every bar is dropped, nothing is written, not even `fetch_log`. Don't
relax the CHECK to let these through: the data is wrong, not the constraint. The harvester's
writer (`SQL_UPSERT_DAILY_BAR`) does not filter yet.

**A re-fetch can't remove a bad bar.** Both writers upsert, so a stored row on a date Yahoo no longer
returns stays forever. The 2026-07 misalignment repair corrected every date the real symbol traded
and left 83 stolen bars on dates it didn't, such as ASSA-B.ST's Memorial Day session on JCI and
DELL. To remove a bad row, delete it by key, as `scripts/repair_ohlcv_orphan_bars.py` does. Don't
make `_store_bars` delete what a fetch omits: a gappy reply would then erase real history. Why and
the evidence: [`ohlcv-orphan-bars-repair-plan.md`](../proposals/ohlcv-orphan-bars-repair-plan.md).

**The options tables store NULL, never NaN; the API answers null, never a 500.** Postgres `REAL`
accepts NaN, and the options capture wrote it: a chain side with no contracts averages to NaN
(pandas mean of nothing), and Yahoo's NaN bid/ask is truthy, so it slips past the usual `or 0`. Read
back, one NaN failed JSON encoding (`Out of range float values are not JSON compliant: nan`).
`/options/iv-rank` and `/options/latest` 500'd: 230 of prod's 500s over 30 days, and 123 of its 190
iv-rank calls. A NaN also orders wrongly in `max`/`min`. There are three layers:

- `OptionsStore` writes NULL for a non-finite float (`_finite`), in all four writers. `strike` is
  the exception: it is NOT NULL and part of the contracts' key, and the capture already drops
  strike <= 0, which includes NaN.
- `V11__null_nan_option_floats.sql` cleared the rows already stored. It is a data migration, so it
  is not mirrored into `_SCHEMA` (same rule as V8).
- `QuantCoreJSONResponse` turns any NaN/±inf float into `null`.

The encoder's `default()` always meant to null NaN, but `json` never calls `default()` for a plain
float, and `np.float64` is one. So the response retries once with non-finite floats replaced, only
when encoding fails for that reason. `get_iv_rank` also ignores non-finite IVs, so a NaN can't skew
the rank. The iv-rank route logs its failures as `get_iv_rank failed ticker=… (<module.Class>)`:
class only, because the text may carry a DSN (`route_internal_error` in `api/deps.py`, as every
guarded route does). Before this, those 500s left an empty log entry.

**Migrating from a legacy SQLite database:** `scripts/migrate_sqlite_to_postgres.py` performs a one-shot copy of an existing `quantcore.sqlite` file into PostgreSQL — it initializes the schema, migrates all 16 tables in FK-safe order via batched `execute_values()` inserts, resets `SERIAL` sequences, and verifies row counts. Run it with `--sqlite <path>` and `--dsn <postgresql-uri>`.

## Live quotes

The Portfolio's live prices come from `PricesService.get_quotes`, a narrowed view of
`get_batch_quotes`, which calls `YFinanceGateway.batch_quotes` (#518). Each layer has one job:

- **Gateway:** `batch_quotes(symbols)` calls Yahoo's `v7/finance/quote` endpoint through
  `yfinance.data.YfData().get_raw_json` (which handles the cookie and crumb), in chunks of 100 symbols.
  The chunks run at once on daemon threads under one deadline (8 s), the same abandon-don't-wait
  pattern as `ticker_info`. A failed or timed-out chunk maps its symbols to `None`, and so does a
  symbol Yahoo silently leaves out of its reply (it does: DOMO, once). It never raises, and it logs a
  failure by exception class only. Non-finite numbers become `None`.
- **Service:** `get_batch_quotes` keeps a per-process cache: 60 s TTL, and a 10 s floor even under
  `force=True` so the Refresh button can't hammer Yahoo. A symbol without a usable quote keeps its
  previous cache entry, or falls back to its last stored daily close marked `stale` with that close's
  `as_of`. `price` is the newest of the regular, pre- and post-market prices **by time**, so after the
  close and overnight it is the post-market price, as it was when quotes came from a `prepost=True`
  minute download.

Why not `yf.download`: given many tickers it loops and sends one chart request per ticker. For the
260-symbol watchlist that is 260 requests; the batch endpoint answered the same list in 3 requests and
1.3 s. yfinance has no public batch quote (`yf.screen` can't filter equities by ticker, and
`yf.WebSocket` only reports symbols as they trade), which is why this code depends on `YfData`.
`tests/test_yfinance_batch_quotes.py` pins `YfData.get_raw_json`'s signature, and the module-level
import makes a moved `YfData` fail the import on the weekly dependency-upgrade PR.

Plan and what comes next (writing these quotes into `ohlcv` as today's forming bar):
[`live-quotes-return-columns-plan.md`](../proposals/live-quotes-return-columns-plan.md).

## Today's forming bar

A daily bar in `ohlcv` has `status = 'OPEN'` while its session is still trading: its close is the
latest price and its volume and range are a partial day's. Each reader chooses whether it may see
that bar (#518):

| May read the `OPEN` bar (live display) | Closed bars only (`include_open=False`) |
|---|---|
| returns columns (`bulk_period_returns`) and the last-close quote fallback | options watchlist screen (`OptionsScreeningService`, a ranking): its cached history read, `fetch_bollinger_bands`, `fetch_iv_analysis` |
| price-level tools: support levels, VWAP, anchored VWAP, ATR bands, gap analysis, higher lows, drawdown | volume-based: `get_obv`, `get_volume_profile`, `get_dark_pool` |
| price-based indicators: RSI, MACD, stochastic, candlestick patterns, the technicals table | `screen_securities` and the arbitrage scanner's closes (rankings) |
| `get_stock_price`, `get_ohlcv_bars`, `get_volume_analysis`, Sidekick and MCP analysis | anything that alerts or changes stored state |

The rule: **a reader that ranks, alerts, or uses volume reads closed bars only.** A partial day's
volume and range can't be compared with a full session's, so a ranking built on them changes
through the day for no market reason. The same split is the common practice elsewhere:

- **TradingView** recalculates indicators on every update of the live bar and offers
  [`barstate.isconfirmed`](https://tradingview.com/pine-script-docs/concepts/repainting) to act only
  on a bar's final update.
- **StockCharts** lets daily scans run on intraday updates, treating the latest as
  [the current period's close](https://scan.stockcharts.com/discussion/comment/5713). It warns that
  partial-day results [aren't "true" readings](https://scan.stockcharts.com/discussion/comment/3211)
  and [excludes volume-based indicators](https://scan.stockcharts.com/discussion/comment/8854) from
  intraday scans.
- **QuantConnect LEAN** emits a daily bar consolidated from minute data only
  [at market close or later](https://quantconnect.com/docs/v2/writing-algorithms/consolidating-data/consolidator-types/mixed-mode-consolidators).

How it is enforced:

- `include_open` is a **required keyword-only** argument on `OhlcvRepository.get_bars`,
  `daily_history_for_symbols` and `daily_bars_for_symbols`, so every caller states its choice.
  `False` excludes `OPEN` rows in SQL, with a bound boolean.
- `PricesService.get_history(..., include_open=True)` defaults to the live-display side, because
  most of its callers are displays. A closed-only reader passes `False`.
- `TestReaderPolicy` in `tests/test_prices_history_policy.py` lists every function in
  `quantcore/services/` on each side: those that pass `include_open=False`, and those that call
  `get_history` with the default. It fails when a list and the code disagree, so a new reader's
  side is chosen, not inherited.
- A closed-only `get_history` call doesn't re-fetch a forming bar it won't return. It still
  fetches when the latest `CLOSED` bar is behind the last completed session.
- A `PricesService` response built on a frame whose newest bar is `OPEN` carries
  `latest_bar_open: true` (otherwise `false`), so the UI and the Sidekick can label it. `get_bars`
  records it in the frame's `DataFrame.attrs`; a frame from anywhere else reads as `false`. The
  early "no data" returns don't carry the key. The composites `get_risk_signals` and
  `get_technical_signals` report `true` when any of their parts does.

### When a forming bar is re-fetched

`get_history` serves a cached `OPEN` bar as it is until the bar is due a refresh
(`PricesService._open_bar_is_due`). It is due when either holds:

- it was written more than `OPEN_BAR_MAX_AGE_SECONDS` (300) ago, or
- it is dated before today's market date. That session is over, so the bar is re-fetched at any
  age and the fetch stores it as `CLOSED`. A missed close-out heals on the first read this way.

Before #518 PR 3 the trigger was "an `OPEN` bar exists". That was too eager, because a re-fetch
downloads the caller's **whole window** (365 days, say) in one per-symbol `yf.download`, and
`get_history` sits under most `PricesService` tools, each recommendation (four calls: the symbol,
SPY, QQQ, the sector ETF) and the options and arbitrage services. During the session every one of
those calls downloaded the window again.

The rule is the same for every interval. `OhlcvRepository.open_bar_state` supplies its two inputs in
one query: the oldest `OPEN` bar's `ts` and the oldest `ingested_at` among `OPEN` bars. It reports
the oldest of each because one overdue bar is enough, and during the session an intraday interval
has several `OPEN` bars. `ingested_at` is `NOT NULL`, so every `OPEN` bar has an age.

The bar's date is its `ts` read in UTC. That is the session's date for every exchange, because a
daily bar's `ts` is midnight UTC of its session date (see the writer below).

### The live quote writer

`PricesService.get_batch_quotes` writes each fetched quote through to `ohlcv` as today's forming
daily bar (`_write_live_bars` → `OhlcvRepository.upsert_live_daily_bars`), so the returns columns
and price tools see today's price without a history download. It does not go through `store_bars`,
which picks a bar's status from the clock. Rules:

1. **`ts` is midnight UTC of the session's date on its exchange** (`market_time.session_bar_ts`):
   the date of the quote's `regular_time` in its `exchange_tz`, then midnight UTC of that date.
   Never "now": on a weekend or holiday the quote still carries the last session's time. Never the
   UTC date of the instant: 10:30 in Sydney is 23:30 UTC the day before. This matches what the
   history fetch stores, because `yf.download` returns a tz-naive daily index and `.timestamp()`
   reads it as UTC. `ts` is in the primary key, so a different convention writes a second bar for
   the same day that no fetch ever replaces.
2. **Regular-session fields only** (`regular_open/high/low/price/volume`). A pre- or post-market
   price never reaches a bar, although it can be the `price` the caller gets.
3. **A `CLOSED` bar is never overwritten.** The upsert updates only `WHERE ohlcv.status = 'OPEN'`.
   After the close-out, or on a weekend, the quote's session is one whose bar is final and the
   write does nothing.
4. **Always `OPEN`,** even after 4pm. The nightly close-out or a `get_history` re-fetch closes it.
5. **Unusable quotes are skipped** (`_live_daily_bar`): any of open/high/low/price missing,
   non-finite or ≤ 0 (a foreign listing before its open sends 0.0 for open, high and low), a
   negative volume, or a session that can't be dated. A missing volume is stored as 0. The
   repository drops a bar the table's CHECK constraints would reject as well, so it doesn't depend
   on its caller: one bad row would fail the whole batch.
6. **No bar for a symbol with no daily history.** The insert is conditional on the symbol already
   having a daily row. A lone live bar would make `count_cached` non-zero, and `get_history` would
   then skip its cold-start warm and serve a history as short as its caller's window.
7. `adj_close` is written NULL and the `COALESCE` keeps a harvester value. One `executemany`, bound
   parameters only.
8. **A failed write never fails the quote.** It is logged by exception class and the caller gets
   its quote.

Because the quote path rewrites `ingested_at` on each refresh, a symbol whose quotes are being read
rarely reaches the 300 s age that triggers a history re-fetch.

### The live 1d columns

Both pages get their 1d figure from `PricesService.quote_changes(symbols, force, return_1d)` (#518).
Per symbol it returns `regular_change_pct`, `pre_change_pct`, `post_change_pct`, `market_state`,
`quote_as_of`, `quote_stale`, `quote_earlier_session`, and the figure to show:

- `return_1d_display` is the quote's regular-session change when the quote is fresh, and otherwise
  the cached close-to-close `return_1d`. `return_1d_stale` is true in the second case. The choice
  is `analytics.returns.display_1d`, so no page makes it (arch-v2 Rules 8 and 9).
- **A fresh quote from an earlier session is shown and marked stale.** `quote_earlier_session` is
  true when the quote's last regular trade, dated on its exchange, is before the latest NYSE
  session that has opened (`market_time.is_earlier_session`): a thin symbol with no trade yet
  today, or a foreign listing on its home-market holiday. The figure is still the quote's change,
  with `return_1d_stale` true, because it is the previous session's move. The comparison is by
  session date, never by age: an Asian listing's close is hours old every US afternoon and is
  still that day's session. `quote_stale` stays false; it means Yahoo sent no quote. Numbers
  behind the decision:
  [`live-quotes-return-columns-plan.md`](../proposals/live-quotes-return-columns-plan.md).
- The fallback is read from the cache only for the symbols that need it, so a call where every
  quote is fresh costs no SQL. A caller that already holds `return_1d` passes it in.
- It is one `get_batch_quotes` call: the 60 s TTL and the 10 s force floor apply. It never raises
  for a missing quote, and a failed database read for either fallback (the cached `return_1d`, or
  the quote cache's last close) is logged and leaves those rows stale with nothing to show. The
  rows with a live quote are unaffected.

Where it is served:

| Route | Source | Network |
|---|---|---|
| `GET /api/portfolio/symbols` | `PortfolioService.symbol_rows`: `bulk_period_returns` for `return_1d/5d/30d/60d/ytd/1y`, plus `quote_changes` | the quote call `list_lots` already makes |
| `GET /api/watchlist/quotes` | `WatchlistService.quotes` | one batched quote call |
| `GET /api/watchlist/mine` and `/fundamentals` | gain the cached `return_1d` only | none, as before |

The Portfolio's return columns used to come from one `get_history` per symbol, each of which could
reach Yahoo. They now come from one cached read. One network path is left on purpose:
`_warm_cold_history` calls `get_history` for a held symbol with **no** cached bars, because nothing
else would fetch its history (the nightly close-out skips such a symbol). It runs once per newly
held symbol. A symbol whose history can't be fetched (delisted, a bad ticker) stays cold, so each
is tried at most once an hour per process (`COLD_WARM_RETRY_SECONDS`), not on every page load.

The 1d to 60d windows count trading bars and YTD and 1y are calendar-anchored, as
`bulk_period_returns` has always computed them.

Two readers sit on the display side although they are near the line. `get_volume_analysis` reports
today's volume against its average, which is the point of the tool during the session, and its
response says so through `latest_bar_open`. `get_stock_price` saves a snapshot, but of the live
price it was asked for, not of a ranking.

The other services that call `get_history` keep the default and are listed in the test as
live-side: the Portfolio's cold-start history warm, the options-chain Bollinger context, relative-strength
history, the support-confluence Fibonacci and previous-bar sources, and the bid-ask spread proxy.
`ArbitrageService` has one history read (`_closes`), so the single-pair analysis and pair
discovery read closed bars along with the scan.

Today's alert paths don't read `ohlcv`: `notifier.py` uses the legacy `portfolio/` metrics and the
harvester scan runs after the close. A future alert that reads the cache belongs on the closed side.

## Positions and the watchlist

### Books

Every position is in a **book** (`books`, V14, #431): a named container that belongs to one owner.
An owner has exactly one `real` book and any number of `paper` books. The real book holds what
the owner actually owns. A paper book holds simulated positions, for trying a strategy without
money.

Why a column on `positions` and not a second table: a paper lot is a lot. It has the same fields,
the same close and split rules and the same P/L arithmetic, so a separate table would mean two
copies of every statement. One table means the difference has to be enforced, and it is, in SQL:

- Every `PortfolioRepository` statement carries the same subquery,
  `book_id IN (SELECT b.book_id FROM books b WHERE b.owner = :owner AND CASE WHEN :book_id IS NULL
  THEN b.kind = 'real' ELSE b.book_id = :book_id END)`. With no book named it selects the owner's
  real book. With a book named it selects that book only if it is this owner's.
- `book_id` is a keyword-only argument that defaults to `None`. Nothing that existed before books
  passes it, so every earlier reader (Harvester, the daily job's notifications,
  `/watchlist/mine`, the MCP portfolio tools) reads the real book without being changed.
- The CSV full-sync (`replace_owner_positions`) deletes and reloads one book. It used to delete
  every position the owner had, which would now wipe their paper books.
- `list_owners` (the daily job's walk) counts only owners who hold something in a real book.
- `lot_sales` has no `book_id`. A sale is in its lot's book, the same way it has its lot's owner.
- The remainder lot of a partial close goes into its parent's book.

The real book is created by the owner's first write, never by a read. The migration created one
for every owner who already held a position. `BooksRepository` lists, creates and archives books;
a book is never deleted, because position rows point at it, and the real book can't be archived.

`BooksService` serves them over REST (`api/routers/books.py`, step 1.2):

| Route | Does |
|---|---|
| `GET /api/books` | the owner's books, the real book first; archived ones only with `include_archived=true`. Empty for an owner who has never written anything |
| `POST /api/books` | create a paper book (`name` up to 60 characters, `starting_cash` zero or more). 422 for bad input or a name already used |
| `POST /api/books/{book_id}/archive` | archive a paper book. 404 for a book that isn't the owner's, 422 for the real book; archiving twice is not an error |

The portfolio reads `GET /api/portfolio/lots`, `/symbols`, `/sales` and `/realized` take an optional
`book_id`. Without it they read the real book. With a book that isn't the owner's they answer
404: `PortfolioService._require_book` looks the book up before reading, because the SQL alone
would return an empty table. A paper book's rows carry no `active_plan_id`. The lot writes take
no book and act on the real book only; paper orders are step 3.5.

A book is **not** an account. `positions.account` is the brokerage account inside a book (an IRA
and a taxable account can both be in the real book).

`starting_cash` is a paper book's opening balance in its cash ledger (below). The plan and its
checkpoint log are [`paper-books-realized-pnl-plan.md`](../proposals/paper-books-realized-pnl-plan.md).

### Cash ledger

A book holds cash as well as positions. `cash_entries` (V16, #431 step 3.1) is its ledger: one
signed USD amount per row, positive when cash comes in. It carries `owner` and `book_id`, like
`positions`, so every statement binds both.

| Kind | Written by | Amount |
|---|---|---|
| `opening_balance` | the owner, for the real book; the system, for a paper book (its `starting_cash`, dated the day it is made) | the cash held at the start of `entry_date`. One per book; setting it again replaces it |
| `deposit`, `withdrawal` | the owner, real book only | positive, negative |
| `buy` | every write to a lot | `-(purchase_price × quantity)`, one row per lot |
| `sell` | every sale | `sale_price × shares_sold`, one row per `lot_sales` row |

Rules to keep:

- **A buy or sell row is derived from its lot or sale, in SQL, and rewritten in the same
  transaction as every write that can change it.** `cash_entry_sql.sync_lot_cash` deletes the
  lot's rows and runs the two `INSERT ... SELECT` statements again: after a new lot, a lot edit, a
  sale, a split (both halves), and a filled FX rate. Those two statements are the only definition
  of a trade's cash amount. The write path, the CSV import and the backfill all run them, so they
  can't disagree. A deleted lot takes its rows with it through `ON DELETE CASCADE`.
- **A new write path for a lot or a sale calls `sync_lot_cash` before it commits**, the way it goes
  through `_quantity` and the FX capture.
- **Amounts are gross of fees**, like realized P/L. The fee columns can't be summed: a sale across
  several lots repeats its fee on each row, and the remainder of a split copies its parent's.
- **A missing FX rate is no amount, never 1.0.** A non-USD lot is converted at the rate stored
  with the trade. Without one the row's `amount` is NULL: it is flagged, counted in
  `fx_missing_count`, and left out of the balance. Filling the rate later gives it its amount.
- **Only a lot whose `acquisition_type` is `BUY` writes a buy row.** A lot that arrived another
  way cost no cash. A lot with no trade date, price or quantity writes none either.
- **A ledger starts at its opening balance.** That is the cash held at the *start* of the opening
  date, so an entry dated on or after it counts and one dated before it is neither listed nor
  counted (`earlier_entry_count` says how many). Lots opened earlier are holdings, not purchases
  (plan decision 9). With no opening balance there is no balance and nothing is listed: a book's
  lots only take cash out, so a sum of them alone would be a large negative number that means
  nothing.
- **The real book is never refused a fact** (plan decision 8). A buy or withdrawal larger than the
  cash is saved and the ledger reports `negative`. A paper book refusing a buy it can't afford is
  the orders step (3.3).
- **Only the real book takes what the owner types.** `CashService` refuses an opening balance, a
  deposit or a withdrawal for a paper book, and the three write routes take no `book_id` at all.
  Only a deposit or withdrawal can be deleted; an opening balance is replaced.
- **`purchase_price` is a `REAL` column, so the SQL casts it through `TEXT`.** Cast straight to
  `NUMERIC`, Postgres keeps six significant digits (1234.567 becomes 1234.57), and the ledger
  would disagree with the price the lot shows.
- **The balance is the sum of the amounts shown.** Each row is rounded to the cent where it is
  stored (a half away from zero, in SQL and in `analytics.cash.to_cents` alike), so the total
  reconciles with the rows on screen. The cost of that: a partial close rounds each half of the
  split on its own, so it can move the book's cash by a cent.

The maths (`analytics/cash.py`) decides what counts and the running balance; `CashRepository` is
the SQL; `CashService` validates what the owner types. Served by `api/routers/cash.py`:

| Route | What it does |
|---|---|
| `GET /api/portfolio/cash?book_id=` | the ledger: opening balance, balance, totals by kind, entries newest first with a running balance. 404 for a book that isn't the owner's. Never creates a book |
| `PUT /api/portfolio/cash/opening-balance` | set the real book's opening balance (`date`, `amount`, which may be negative) |
| `POST /api/portfolio/cash/entries` | record a `deposit` or `withdrawal` in the real book. `amount` is positive; the kind gives its sign |
| `DELETE /api/portfolio/cash/entries/{entry_id}` | remove a deposit or withdrawal. 404 for anything else |

Each write answers with the ledger. Bad input is a 422.

**`scripts/backfill_cash_entries.py`** writes the rows that lots, sales and paper books from
before V16 lack. It is `CashService.backfill` per owner, every book. Dry run by default: it runs
the same statements and rolls them back, so its counts are exactly what `--apply` would write.
It only adds a missing row, never changes one, and makes no network call. Run
`backfill_fx_rates.py` first, so fewer rows are written without an amount. It does not set a
real book's opening balance: the owner enters that on the Portfolio page.

### Paper orders

A paper book trades through orders (`orders`, V17, #431 step 3.3). An order is a request to buy
or sell at the market; the system sets the fill, and the owner never types a price or a date.
The real book takes no orders: a real trade is recorded with Add Lot, at what the broker gave.

| Status | Meaning |
|---|---|
| `pending` | queued for `session_date`, and nothing else |
| `filled` | `fill_price`, `filled_shares`, `price_source`, `quote_time` and `resolved_at` are set |
| `cancelled` | the owner withdrew a pending order |
| `rejected` | `reject_reason` says why |

The last three are final. The rules, and why each is there:

- **An order placed while the market is open is filled or rejected in the transaction that
  creates it.** It is never left pending, so a crash can't leave one half done and the daily job
  never has to guess what a pending order was waiting for. `pending` means queued.
- **An order placed outside the session is queued for one session**: the next to open
  (`market_time.upcoming_session`, which knows the holidays). The daily job fills it at that
  session's opening price, after the close: see *Queued fills* below. Until then it can be
  cancelled.
- **A fill happens exactly once.** The only statement that takes an order out of `pending` is
  `UPDATE … WHERE status = 'pending'`. It runs on the same connection as the fill's other rows:
  the lot (a buy) or the `lot_sales` rows (a sell), and their cash rows. They commit together or
  not at all. `OrdersRepository.transaction` locks the book's row first, so two fills in one
  book run one after the other.
- **Placing the same `idempotency_key` twice returns the first order.** The key is unique per
  owner. A repeat is answered before a quote is fetched, and the insert's own
  `ON CONFLICT DO NOTHING` catches two requests that arrive together.
- **"Open" is three tests** (`analytics.orders.placement`): the clock says 9:30 to 16:00 ET, the
  day is an NYSE trading day, and the quote's `market_state` doesn't say the session is over.
  `is_market_open` alone is wrong on a holiday, and on a half day after 13:00.
- **A quote is too old when its last regular-session trade is** (`Quote.regular_time`, more than
  five minutes), whatever the time it was fetched. The quote comes straight from
  `batch_quotes`, never the price cache. Yahoo has no halted flag, so a halted symbol is refused
  the same way. That leaves a gap, accepted: a symbol halted less than five minutes ago still
  fills, at its last price before the halt.
- **USD-listed symbols only**, by the quote's own `currency`.
- **A paper book has no margin.** A buy's lot and cash row are written first, the ledger is read
  on the same connection, and a balance below zero undoes the trade to a savepoint and rejects
  the order. The check therefore reads exactly what the ledger will show.
- **Cash and shares are checked when the order fills.** At placement a sell is refused when it
  is for more than the shares held less those promised to queued sells, and a dollar buy when
  it is for more than the cash. A queued buy in shares is not checked: its cost isn't known.
- **A fill is all or nothing.** A dollar buy's share count is rounded down to six decimals, so
  it never costs more than the amount. A sell takes the oldest lots first.
- **A fill price is rounded to the tick**: the cent at a dollar or more, a hundredth of a cent
  below it. `positions.purchase_price` is a `REAL` column and holds seven significant digits,
  which is every price below $100,000 to the cent. At or above that the lot and its cash row
  hold a rounded price and the order keeps the exact one. Accepted for paper books as they are.
  The column has to change (a hand-applied migration) before a paper book holds such a symbol.
- **A share count is capped at 999,999,999,999**, the largest whole number `NUMERIC(18,6)`
  holds, whether it is asked for or worked out from a dollar amount. Over the cap is bad input
  (a 422) and nothing is stored.
- **A rejection is a stored order, not an error.** `reject_reason` is one of `stale_quote`,
  `no_quote`, `non_usd`, `insufficient_cash`, `insufficient_shares` and `no_open_price` (a
  queued order whose session has no opening price). A `CHECK` holds the list, and `tests/test_orders_analytics.py` fails if the code's
  list and the schema's differ.

**Queued fills** (step 3.4). The daily job fills a queued order once its session has come
(`OrdersService.queued_orders` and `fill_queued_order`; the step is in
[daily-jobs.md](daily-jobs.md#queued-paper-fills)). The rules, and why each is there:

- **The price is the open of the session's closed daily bar** (`PricesService.session_open`),
  not `Quote.regular_open`. A quote only has today's open, so a job that ran a day late would
  have nothing to fill at. A stored bar is keyed by its date, so the order fills at the same
  price on whatever night it is reached.
- **Closed bars only.** Today's forming bar is written from a live quote, and its open can
  differ from the one in the day's history. The job runs after the close, when the session's
  bar is stored `CLOSED`.
- **`price_source` is `session_open` and `quote_time` is NULL.** The trade is dated the
  session; `resolved_at` is when the job got to it.
- **No bar is not always "no opening price"** (`analytics.orders.queued_fill`). A closed bar
  dated after the session shows the symbol's history has moved past that day, and the order is
  rejected `no_open_price`. With no later bar the history may only be late (a failed or empty
  fetch looks the same), so the order waits, for at most three sessions, and is then rejected.
  It never rolls to a later session's open.
- **The price is fetched before the book is locked, and the order is read again under the
  lock.** An order cancelled while Yahoo was answering is not filled. `_fill` also raises when
  its `UPDATE … WHERE status = 'pending'` matches no row, which rolls the trade's rows back.
- **Orders in one book fill in the order they were placed**, each in its own transaction. A
  queued sell placed before a queued buy pays for it; the other way round, the buy is rejected.
- **An archived book's queued orders still fill.** The order was accepted before the book was
  archived, and the page shows no Cancel there, so nothing else would ever take it out of
  `pending`.

**Where a trade came from.** `orders.source` and `lot_sales.source` are one of `manual`, `stop`,
`harvester`, `evaluator`, `watch` and `ai`, with `source_ref` pointing at what prompted it.
`manual` is the owner's own order and `stop` a sale the stop checker made (`source_ref` is
`stop:<stop_id>`); the others have no writer yet. Every sale from before V17 is `manual`. The lot a buy fills
(`positions.order_id`) and the sale rows a sell fills (`lot_sales.order_id`) name their order,
and the remainder lot of a split keeps the order that bought its parent.

`analytics/orders.py` decides; `OrdersRepository` is the SQL; `OrdersService` validates and
orchestrates. Served by `api/routers/orders.py`:

| Route | What it does |
|---|---|
| `GET /api/portfolio/orders?book_id=` | a book's orders, newest first, and how many are pending. The real book has none and is not looked up. 404 for a book that isn't the owner's |
| `POST /api/portfolio/orders` | place a market order in a paper book: `book_id`, `symbol`, `side`, `shares` or (a buy) `amount`, `idempotency_key`. 201 with the order as it now is, 200 for a repeated key, 422 for bad input or the real book |
| `POST /api/portfolio/orders/{order_id}/cancel?book_id=` | withdraw a pending order. 422 for one that is no longer pending |
| `POST /api/portfolio/orders/seed` | copy the real book's open holdings into a paper book that has had no orders: `book_id`. 201 with one order per symbol and the counts. 422 for the real book, an archived book, a book with orders or an empty real book |
| `POST /api/portfolio/orders/manual-close` | sell a paper book's whole position in a symbol that can't be quoted: `book_id`, `symbol`, `idempotency_key`. 201, 200 for a repeated key, 422 when the symbol has a quote, the book holds none, no price is on record, or a sell of the symbol is still queued |

No route takes a price, a date or a `source`.

**An order has a kind** (`orders.kind`, V18, step 3.5). `market` is a buy or sell that moves
cash. The other two exist so that every paper lot and sale still comes from an order:

- **`seed` copies a real holding into a new paper book** (`OrdersService.seed_book`). One buy
  order per real-book symbol, for the shares held across its open lots. It is priced like any
  order: the quote in market hours, the next session's open otherwise, so an off-hours seed
  waits in the same queue and is filled by the same job step. Its lot is
  `acquisition_type = 'SEED'`, and only `BUY` writes a cash row, so a seed costs nothing and
  is never rejected for cash. Selling a seeded lot moves cash like any sale.
  - Only a book that has had no orders can be seeded, and the whole seed is one transaction:
    a failure part way leaves the book unseeded.
  - The real book is read and never changed. A holding that can't be priced (no quote, a
    non-USD symbol) is a rejected order with its reason. A symbol an order can't name is
    returned under `skipped`.
- **`manual_close` sells a position that can't be quoted** (`OrdersService.close_unquoted`),
  a delisted symbol for one. It is allowed only when `analytics.orders.unquotable` says so:
  no quote, or a last regular trade three or more sessions old. A symbol with a live quote is
  refused and sold with an ordinary order.
  - The price is the close of the newest stored closed daily bar
    (`PricesService.last_close`, no network), or the quote's last regular trade when no bar
    is stored. With neither, the close is refused.
  - It fills at once, for the whole position, dated today. `price_source` is NULL,
    `quote_time` is when the price is from, and each sale row's note says it was closed by
    hand and from which date.
  - It is refused while a sell of the symbol is queued. The close sells every share, so the
    queued sell would be rejected `insufficient_shares` at its session with nothing saying why.
    Cancel the queued sell first.

A `CHECK` (`orders_kind_rule`) holds the three kinds and their shapes: a seed is a buy for a
share count, a manual close is a sell.

#### A stop on a buy

A `market` buy can ask for a stop (V21, #431 plan step 4.3): `stop` on
`POST /api/portfolio/orders`, with `kind`, `stop_level` (fixed) or `trail_pct` (trailing),
`method` and `proposed_level`. The order keeps what was asked in `stop_kind`, `stop_level`,
`stop_trail_pct`, `stop_method` and `stop_proposed_level`. `analytics.stored_stops` has the
rules (`requested_stop`, `order_stop_terms`).

- **The stop row is written by the fill, in the fill's transaction**
  (`OrdersService._attach_stop`, `stop_sql.set_stop_on`). The lot, its cash row, the stop and
  the order's status commit together. A queued buy has no stop until it fills. A rejected or
  cancelled order never gets one.
- **The stop is set against the fill price.** That price is its `reference_price`, a trailing
  stop's first high-water mark, and the price a fixed level must be under. A stop set by hand
  uses the last stored close for all three ([stored stops](#stored-stops)); a buy has a better
  figure, the price the position was opened at.
- **A fixed level at or above the fill price is no stop.** In market hours the fill price is
  known before anything is stored, so the request is a 422 and no order exists. A queued buy
  can only be checked at the open: when it opens at or under its level the buy is **rejected**
  with the reason `stop_not_below_fill`, and nothing is written. No position opens without
  the stop it was asked for (John's decision, 2026-11-17). The stop is checked before the
  cash. A trailing stop is treated the same way when its percentage is too small to give a
  price below the fill (0.0001% under $50.00 rounds back to $50.00).
- **The reason needed a hand-applied migration** (V23). `reject_reason` has a `CHECK`, and
  widening one means replacing the constraint, which the migrate Job refuses. V24, applied the
  same way, makes `orders_stop_rule` require `stop_kind` on a requested stop: V21's rule came
  out NULL, which a `CHECK` passes, for a whole fixed or trailing stop with no kind.
- **`stop_set` says what became of the request**: `true` once the fill wrote the stop, NULL
  before the fill and on an order that asked for none. `false` is allowed by the column and
  no longer written.
- **A buy that fills at its session's open has a stop that is live from that session**
  (`stops.first_session`, V22). The checker tests it against that day's own bar, so a low
  under the level sells the position the day it was bought, and a fill recorded days late is
  tested from its own session. This is what a broker's attached stop does: it is active once
  its entry fills. The whole bar came after a fill at the open, so the reason a hand-set
  stop skips the bar it was set in doesn't apply.
- **A buy filled at a live quote in market hours keeps the general rule**: its stop is first
  tested against the next session to open. A daily bar can't say whether its low came before
  the fill.
- The stop's `created_at` is the moment the fill was recorded, its `source` is `manual` and
  its `source_ref` is `order:<order_id>`.
- **It replaces the stop the symbol already has in the book.** A buy with no stop leaves that
  stop alone, and the new lot is under it.
- `orders_stop_rule` holds the shapes: only a `market` buy carries a stop, a fixed one has a
  level and no percentage, a trailing one a percentage and no level, and `stop_set` is set only
  on a filled order. Each comparison in it is guarded with `IS NOT NULL`: a `CHECK` passes when
  its expression is NULL.

### Stored stops

A stop is the owner's decision to get out of a holding at a level (`stops`, V19 and V20, #431
phase 2 and #221, plan steps 4.1 and 4.2). The table records the decision, and the daily job
tests it against closed bars ([the stop checker](#the-stop-checker), below). Nothing here places
an order at a broker or reads one: a stop marked `at_broker` mirrors an order the owner placed
there (step 4.5, below).

- **A stop is set on a symbol in one book, not on a lot.** Lots bought later are covered by the
  same stop. Both kinds of book take stops.
- **One active stop per symbol per book**, held by a unique partial index
  (`ux_stops_one_active`). Setting another replaces it in one transaction, with the book's row
  locked: the old row becomes `replaced` and the new one is inserted.
- **Only `active` is live.** `triggered`, `cancelled`, `replaced` and `closed` are final, and a
  stop leaves `active` only through `UPDATE … WHERE status = 'active'`. A stop is never deleted:
  what was set, how, and what became of it is the record that says which stop method works.
- **A stop needs a holding.** `StopsRepository.set_stop` reads the book's OPEN lots of the
  symbol on its own connection and raises `SymbolNotHeldError` (a 422) when there are none.
  When the position goes flat the stop is marked `closed`.
- **Fixed or trailing.** A fixed stop is a price. A trailing stop is `trail_pct` percent below
  `high_water_mark`, and `stop_level` holds the level worked out from the two, so no reader has
  to. The mark starts at the last close. The checker raises it and never lowers it.
- **A stop sits below the last close**, which is the newest stored closed daily bar's
  (`PricesService.last_close`, zero network). The checker tests against closed bars too, so
  a stop at or above the last close would be reached by the next bar almost by definition. With no stored close a fixed
  stop is taken as typed and a trailing stop is refused. The owner types no high-water mark.
- **A level is rounded to the market's tick** (`analytics.orders.fill_price`), so a stop and the
  fill it later causes can be compared exactly.
- **Every stop records where its level came from**: `method` (`manual`, `technical`, `trailing`
  or `confluence`, the stop-loss card's three candidates), `proposed_level` (what the analysis
  suggested), `stop_level` (what was saved), `reference_price` (the close it was set against)
  and `as_of` (that close's date). `method` has no `CHECK`, on purpose: widening a `CHECK` is a
  dropped constraint, which the migrate Job refuses, and new methods to compare are expected.
  `StopsService` checks it against `analytics.stored_stops.METHODS`.
- **`source` says what set the stop**: `manual`, `harvester`, `evaluator`, `watch` or `ai`, with
  `source_ref`. It is a trade's source list without `stop`. Only `manual` has a writer, and no
  route takes a source.
- **An archived book takes no new stop.** One it already has can still be cancelled.
- **A real book's stop can mirror an order placed at an outside broker** (`at_broker`, V26,
  plan step 4.5). The owner says the order exists and, if they like, its share count
  (`broker_quantity`), order type (`broker_order_type`: `stop` or `stop_limit` for a fixed
  stop, `trailing_stop` for a trailing one) and `brokerage`. `stops_broker_details_rule` lets
  only a stop at a broker carry the three. A paper book has no broker, so `StopsService` refuses
  `at_broker` there (422). The share count is checked against the book's holding under the
  book's lock, and is otherwise only a record. QuantCore never reads the broker: a mirrored
  stop is set, replaced, checked and alerted like any other, its trailing mark is QuantCore's
  own, and a replacement does not inherit the mirror. The breach alert says the broker's order
  should have filled, and names where it was placed. `broker_order_type` has no `CHECK`, for
  `method`'s reason.

`analytics/stored_stops.py` works out a stop's terms; `StopsRepository` is the SQL;
`StopsService` validates and orchestrates. Served by `api/routers/stops.py`:

| Route | What it does |
|---|---|
| `GET /api/portfolio/stops?book_id=&include_inactive=` | a book's active stops, newest first, and how many are active. `include_inactive=true` adds the ones that ended. Without `book_id`, the real book. The read creates no book. 404 for a book that isn't the owner's |
| `POST /api/portfolio/stops` | set the stop on a holding: `symbol`, `kind`, `stop_level` (fixed) or `trail_pct` (trailing), and optionally `method`, `proposed_level`, `book_id`, and `at_broker` with `broker_quantity`, `broker_order_type` and `brokerage`. 201 with the stop, whose `replaced_stop_id` names the one it took over from. 422 for a symbol the book doesn't hold, a level at or above the last close, an archived book, a paper book's stop said to be at a broker, broker details without `at_broker`, or a broker order for more shares than the book holds |
| `POST /api/portfolio/stops/{stop_id}/cancel?book_id=` | take an active stop off. 404 for a stop that isn't in that book, 422 for one that is no longer active |
| `GET /api/portfolio/stops/holdings?book_id=` | the one stop each holding shows, in symbol order: the symbol's newest stop when it is active, or was reached while a lot was already open. 404 for a book that isn't the owner's |

**The server picks the stop a holding shows** (`analytics.stored_stops.holding_stops`,
`StopsService.holding_stops`; John's decision, 2026-11-17). Only a symbol's newest stop is
looked at, so a cancelled, replaced or closed one shows nothing whatever came before it. A
stop a bar reached is shown only when a lot was opened on or before that bar's session: a
position bought back later was never under it. The read is two queries, makes no network
call and creates no book.

The Portfolio page reads the holdings route and can take an active stop off through the cancel
route, in any book (plan step 4.3 and decision 67). The **stop panel** on the Security page's
Technical Analysis tab (`StopPanel`, step 4.5) sets one through `POST /api/portfolio/stops`, in
the real book or a paper book: from one of the stop-loss card's candidates, shown with the
card's reasoning and open to an override, or by mirroring a broker order (real book only). A
paper buy still sets its own [on its fill](#a-stop-on-a-buy). No MCP tool or Sidekick tool
reads a stop (step 5.4).

#### The stop checker

The daily job tests every active stop against closed daily bars
([daily-jobs.md](daily-jobs.md#stop-checks) has the step). The rules are
`analytics.stored_stops.check_stop`; `StopsService.check_stop` gathers the bars and writes the
outcome. Four columns from V20 hold what it finds: `last_bar_checked`, `triggered_session`,
`trigger_price` and `alerted_at`.

- **Closed bars only.** A forming bar's low is not final. A stop reached by it could be
  un-reached by the finished bar.
- **The first bar tested is the first session that opens after the stop was set**
  (`first_session`), except for the stop of a buy that filled at a session's open, which
  starts with that session ([a stop on a buy](#a-stop-on-a-buy)). The bar that was already trading when the stop was set is never tested:
  its low may be from before the stop existed, and a paper fill at that price would be a trade
  at a price the stop never saw. What it costs: a stop set at 11:00 and crossed at 14:00 is not
  caught that evening. The next bar catches it, usually as an open under the level.
- **Every bar since `last_bar_checked` is tested, oldest first.** A night the job missed is
  made up on the next run, and the stop is triggered on the bar that reached it, not on the
  night it was noticed.
- **A bar reaches a stop when its low is at or under the level.** A close back above the level
  doesn't undo it: a real stop order fills when price touches it.
- **The price is the level, or the open when the stock opened under the level** (plan
  decision 5). `trigger_price` holds it, and `trigger_price < stop_level` is how a reader
  tells a gap.
- **A trailing stop's mark rises to a bar's high, after that bar is tested.** A daily bar
  doesn't say whether its high or its low came first, so a bar is tested against the level it
  started with. A bar whose high lifts the level above its own low is settled by the next bar.
  The level never falls.
- **Real books alert, paper books act.** A real book's stop is marked `triggered` and nothing
  is sold. A paper book's whole position in the symbol is sold by `OrdersService.sell_for_stop`:
  one `market` sell order with `source = 'stop'`, `source_ref = 'stop:<id>'` and the
  idempotency key `stop:<id>`, dated the bar's session. Its `price_source` is `session_open`
  for a gap and NULL at the level (a new value would widen a `CHECK`, which the migrate Job
  refuses). Each sale row carries a note saying a stop sold it.
- **The stop and its sale commit together.** The stop is marked `triggered` first, on the
  order's transaction, then the sale is written. If the sale fails, both roll back and the stop
  is still active for the next run. A stop that stopped being active once the book is locked
  sells nothing.
- **Only lots opened on or before the bar's session are sold.** On a late run a later
  session's queued buy has already filled; that lot was never under the stop and stays.
- **A queued sell of the same symbol is left alone.** It is rejected `insufficient_shares` at
  its session if the stop sold the shares first.
- **`triggered` is final.** A real-book stop that was reached is done even if the owner keeps
  the holding. A new stop is a new row.
- **The breach alert is owed until Discord takes it** (`alerted_at`), is sent once, and covers
  the job owner's real book only. "Once" rests on the mark alone: the stop alerts skip
  `notification.log`'s dedupe (#581), whose titles (a count and a day) can't tell two batches
  apart. So a mark that fails after a send leaves the stops owed, and the next run sends the
  same message again. That repeat is accepted: the alternative is a stop never announced. The
  approach alert below works the same way.
- **A stop that held is checked for being close** (plan step 4.4, V25). When the newest bar
  tested closed no more than `STOP_APPROACH_ATR_MULTIPLE` (default 1) ATRs above the level, its
  session, close and ATR are recorded (`approached_session`, `approach_close`,
  `approach_atr`); a close further away sets all three to NULL. The ATR is a 14-session Wilder
  ATR as of that bar (`analytics.stored_stops.approach`), from the same read as the bars the
  stop is tested against, which reaches back 90 days for it. The level is the one after that
  bar, so a trailing stop is measured from its raised level. Too little history (under 15
  usable bars) is no answer: the columns are left as they were, never read as "far". A rule
  (`stops_approach_rule`) keeps the three together.
- **The approach alert is owed until Discord takes it, once per approach**
  (`approach_alerted_at`). A stop that stays close is announced once; a close that moves away
  clears the mark with the approach, so the next approach is announced again. A close that
  goes back and forth across the distance is announced each time it comes back. Real book,
  job owner only, like the breach alert. A paper book's approach is recorded and not sent:
  its stop sells by itself.
- **A stop is closed when its position goes flat.** `PortfolioRepository.close_lots_on` runs
  `stop_sql.close_flat_stops_on` in the sale's own transaction, for a real sale and a paper one
  alike, and every checker run does the same for each live book. The second covers a deleted
  lot, an import, and a stop set in the moment its last lot was sold: setting a stop and a
  real-book sale don't share a lock.
- **An archived book's stops are not checked.** They stay `active` and nothing reads them.
- **A split looks like a fall** (#391). Nothing adjusts a stop for one, so a 2-for-1 split
  triggers every stop on the symbol.

### Book value snapshots

What each book was worth at a session's close, kept as a daily series for Performance (#431
phase 3). `book_value_snapshots` (V27, plan step 5.1) holds one row a book and session, written
by the daily job's last step ([daily-jobs.md](daily-jobs.md)). It carries `owner` and `book_id`,
like every per-owner table, so every statement binds both. What a row holds is worked out in
`analytics.performance.book_value`; `PerformanceService` only gathers the inputs.

| Column | Holds |
|---|---|
| `cash` | the ledger's balance at the close: every entry dated on or before the session, from the opening balance on |
| `holdings_value` | the open lots at the newest closed daily bar on or up to 7 days before the session, in USD. NULL when a holding had no such bar (`unpriced_count`) or no FX rate (`fx_missing_count`) |
| `book_value` | `cash + holdings_value`. NULL when either is incomplete. A `CHECK` holds it to the sum |
| `flow` | value that came in or went out without being earned, since the book's previous row: the opening balance, deposits, withdrawals, and lots that arrived without cash. NULL when one of those lots couldn't be valued |
| `stale_count` | holdings priced from an earlier session's close (a day the symbol didn't trade, or a bar that hadn't arrived) |
| `restated_at` | when a rebuild changed the row after it was first taken (V28, step 5.2). NULL for a row nothing has corrected |

`books.restate_from` (V28) is the earliest session whose row a later write has made wrong, or
NULL. It is what the daily job restates a book from.

Rules to keep:

- **A book has no value before its opening balance.** A real book with none writes no row (plan
  decision 9); a paper book has one from the day it is made.
- **An incomplete value is NULL, never a guess.** A holding with no close in the window, or a
  non-USD one with no FX rate, leaves the value NULL and is counted. A missing rate is never
  1.0. A holding priced from an earlier close is valued and counted as stale.
- **A flow is what the book didn't earn.** A buy or a sale only moves value between cash and
  holdings, so it is not one. A lot that arrived without cash (any `acquisition_type` other than
  `BUY`: a seeded paper lot, a transfer into the real book) is one, valued at the session's
  close. So seeded holdings count in a paper book's opening value and its first day shows no
  false gain.
- **The return is time-weighted, with a flow invested from the start of its session**
  (`analytics.performance.return_index`): a session's return is `value / (previous value +
  flow) - 1`. The first valued row is the base. A row with no value carries its flow to the next;
  an unknown flow, or nothing invested, is a gap where the chain rebases. **The drawdown is
  measured on that index, not on the value**, so a withdrawal is not a drawdown.
- **Only the daily job and the history backfill write a row.** The job writes the session that
  just closed, after the day's fills and stop sales; a second run on the same session replaces
  that session's row.
- **A change to history restates the rows it affects** (step 5.2, plan decisions 85 and 88 as
  revised 2026-11-17, the GIPS practice). Every write the owner makes to a book's cash or lots
  (a cash entry added, deleted or its opening balance moved; a lot added, edited or deleted; a
  symbol removed; a sale; a CSV import; an FX rate filled in later) calls
  `restate_sql.mark_restate` in its own transaction with the dates it touched. When the earliest
  is on or before the book's newest row, `books.restate_from` becomes the earlier of itself and
  that date. An order's fill never marks: it is dated today, after every row.
- **A marked book is rebuilt from its mark the same night** (`PerformanceService.restate_book`),
  before today's row is taken, and the rebuild takes today's row too. Each session is valued
  from the book's whole history: the lots held at its close (a lot from its trade date until its
  sale, `PortfolioRepository.list_lot_history`), the ledger as dated, each symbol's close as of
  the session. Each row's flow is measured from the rebuilt row before it, so a deposit, or a lot
  that cost no cash, counts on the session it is dated on however late it was typed in. A row
  that comes out different is replaced and stamped `restated_at`, keeping its `taken_at`; one
  that comes out the same isn't touched; a missing session in the range is filled; a session
  the book now has no value for (its opening balance moved later) loses its row.
- **A restatement never reaches back past the book's first row.** It starts at the later of the
  mark and that row; a change dated earlier only changes the first row, which is the base
  and has no return of its own. Extending the series further back is the backfill's job.
- **The rebuild holds the book's row lock** (`BookSnapshotsRepository.restating`), and the mark is
  an update of that row in the writer's transaction. So a write either committed before the
  rebuild read anything, or waits for it to commit and marks the book again; clearing the mark
  can't lose a change. The clear also requires the mark it started from (`WHERE restate_from =`).
- **A failed price or FX fetch writes nothing and leaves the mark**, unlike the nightly row,
  which counts a missing rate: a rebuild that cleared the mark with rows left wrong would never
  be retried. The step's alarm names a book still marked (`restate`).
- **`scripts/backfill_book_snapshots.py`** rebuilds a book's history from its opening balance
  (or `--from`) through the latest completed session, with the same rebuild. Dry run by default;
  `--apply` writes; it names every session it couldn't value and every one priced from an
  earlier close, and refuses prod without `--allow-prod`. An applied run that covers the mark
  clears it.
- **`contributed` stays a check.** With flows on their real dates the ledger and the stored
  total agree; a row where they don't is one a missed mark left behind, and the backfill
  restates it.
- **A real book's value is unreliable while its cash is negative** (plan decision 8): a buy with
  no recorded deposit behind it. The row stores the cash, so Performance can mark those days.
- **A split looks like a fall** (#391), here as in the stop checker, until splits are handled.
- **The performance read only reads the stored rows** (`PerformanceService.book_performance`
  and `compare_books`, step 5.3; `GET /api/books/performance` and `/performance/compare`). A
  period is the Realized view's rolling window, and the row just before it is the base, so the
  return covers the whole window. Each statement binds the owner and the book like every other
  read; with no book named it reads the real book, and an owner with none yet gets a stand-in,
  never a new book. The one possible network call is SPY's closed bars
  (`PricesService.closed_bars_since`, which fetches only when they are behind). SPY's index takes
  each session's close as a holding's is taken, a price return like the book's, and "vs SPY" is
  the simple difference of the two returns. A failed SPY read is logged by class and leaves the
  benchmark out (`benchmark_available: false`); it never fails the read. Realized P/L by sale
  `source` comes from the window's sales (`portfolio_math.realized_by_source`).

### Realized P/L

`GET /api/portfolio/realized?period=&book_id=` (#235) answers what the owner made and lost on the
lots they closed, by symbol, for one book. It is `PortfolioService.realized_pnl`: two reads (the
sales joined to their lots, and the book's open lots for the "still held" marker) and no network
call. The maths is `portfolio_math.realized_pnl`.

- **Only `lot_sales` rows count.** The legacy `positions.sale_price` / `sale_date` columns (the
  `portfolio.csv` format) are not a source.
- **Realized P/L is `shares_sold × (sale_price − purchase_price)`, gross of fees.** The fee columns
  are not read, and `basis_adjustment` and `wash_sale_disallowed` are not applied.
- **A sale belongs to a period by `sale_trade_date`**, never by when the row was written. `period`
  is `month`, `quarter`, `year` (the default) or `all`: the last 30, 90 or 365 **calendar** days
  ending today in market time, both ends included (`market_time.rolling_window`). Anything else
  is a 422. The window is two bound dates in the query, so the totals are computed over the
  filtered set on the server. `all` has no upper bound, so a sale dated ahead of today shows
  there and in no window.
- **A total is the sum of the rounded rows under it.** Each sale's cost basis and proceeds are
  rounded to the cent and its P/L is their difference; a symbol's figures and the page totals add
  those up. Rounding a sum of unrounded figures could differ from the rows on screen by a cent.
- **Everything is in USD, each side at its own trade-date rate.** Cost basis converts at
  `positions.fx_rate_at_purchase` and proceeds at `lot_sales.fx_rate_at_sale`, so a currency move
  during the hold is part of the result. A USD sale needs no rate. The `*_native` figures stay in
  the lot's currency and are shown on the expanded sale, never summed.
- **A missing rate is never read as 1.0.** A non-USD sale without both rates is listed with its
  native figures and `fx_missing: true`, has no USD figures, and is in no total;
  `fx_missing_count` says how many. A sale whose lot has no purchase price is treated the same
  way (`basis_missing`). How the rates get onto the rows: [FX rates](#fx-rates).
- **Each sale says what prompted it**: `source` (`manual`, or `stop` for a paper book's stop
  selling the position), `source_ref` and `order_id`, the paper order that made it. They are
  shown, and no total is split by them yet (plan step 5.3).
- **A symbol with no summable sale has `None` money figures and no bar**, not zeroes that would
  read as "broke even", and sorts last. Symbols are otherwise ordered best realized P/L first.
- The bar segments come from `allocation_segments`, as on the allocation chart: the base is the
  cost basis of the shares closed and the gain or loss is the realized P/L, so the top of the bar
  is the proceeds.

The Portfolio page's Realized section and the Sidekick `realized_pnl` card read the route
([`quantui.md`](quantui.md)); they show the payload and compute nothing.

### FX rates

A rate is **USD per one unit of the lot's currency**, stored to eight decimals
(`NUMERIC(18,8)`) on the row it belongs to: `positions.fx_rate_at_purchase` for the lot's
`trade_date` and `lot_sales.fx_rate_at_sale` for the sale's `sale_trade_date` (#235, V15). NULL
means no rate is recorded, which is always the case for a USD row.

- **The rate is captured when the trade is recorded, so the realized read makes no network call.**
  `PortfolioService` looks it up on Add Lot, on a CSV import, on a lot edit that moves the trade
  date or changes the currency, and on a close. The lookup runs before the transaction opens, one
  request per currency however many dates are wanted.
- **A lookup that fails costs the rate, never the trade.** `_fx_rates` never raises: the lot or
  sale is written with a NULL rate, the sale shows as `fx_missing`, and the backfill below fills
  it in later. The log line names the exception class only.
- **A lot edit replaces the rate, by NULL when the new one can't be had.** The stored rate belongs
  to the old date and currency; keeping it would be a confidently wrong number. A currency edit
  also clears the sale-date rate of the lot's sales, in the same transaction, and looks them up
  again once the edit has committed.
- **The source is `YFinanceGateway.fx_daily_closes`**: daily closes of Yahoo's `<CCY>USD=X` pair,
  under the download lock, with an 8-second timeout. It accepts a three-letter code only, because
  the code becomes part of the symbol requested.
- **Which close answers for a date is `analytics.fx.prior_close_rate`**: that day's close, or the
  most recent one before it (a weekend or a holiday), reaching back at most 7 days. Never a later
  close. A trade dated after today gets no rate.
- **The remainder lot of a partial close keeps its parent's purchase rate.** It has the same
  trade date, so no second lookup is made.
- **A rate captured on the trade's own day is that moment's rate, not the day's close.** FX trades
  around the clock and Yahoo's bar for today is still forming. The backfill would store the final
  close for the same date, so the two can differ in the fourth decimal.
- **A currency is a three-letter code here.** Lots store the code upper-cased, so a London stock
  priced in pence (`GBp` at Yahoo) can't be told from one priced in pounds; enter its prices in
  pounds with currency `GBP`.

**`scripts/backfill_fx_rates.py`** fills the rates that are missing: rows written before V15, and
trades recorded while Yahoo was unreachable. It is `PortfolioService.backfill_fx_rates` per owner,
real books only. Dry run by default (`--apply` writes, `--owner` narrows, `--allow-prod` is
needed for the production database). It fills only a rate that is NULL, so it never replaces one
captured with the trade and is safe to run twice. A row it can't rate is reported, left NULL, and
makes the run exit 1.

### Positions

Positions are DB-backed with multi-owner support (`positions` table, `owner` column); `portfolio.csv` is a per-owner import format (`scripts/import_portfolio.py --csv portfolio.csv --owner john`, full-sync replace). The REST `/api/portfolio*` routes resolve the owner from the caller's token (`require_owner`); there is no `?owner=` parameter. `main.py`'s report/notifications stay on John's portfolio.

**Share counts are fractional, to six decimals.** `positions.quantity` and `lot_sales.shares_sold` are `NUMERIC(18,6)`. Rules that keep a fraction intact from the form to the database and back:

- **The service rounds a share count once, before any arithmetic** (`_quantity` in `quantcore/services/portfolio.py`): on add, on a lot edit, and on a sale (the total and each manual allocation). Don't leave the rounding to the column. A sale of 0.3333335 from one share would then store 0.333334 sold and 0.666667 left, which is more than the lot held. A count that rounds to zero is refused as "must be positive".
- **Requests carry `Decimal`, responses carry JSON numbers.** `QuantCoreJSONResponse` turns a `Decimal` into a float; a six-decimal share count survives that round trip. The MCP portfolio tools hand the REST payload on unchanged.
- **The UI shows six decimals** (`formatShares`, `SHARE_DECIMALS` in `frontend/src/utils/formatting.ts`) and every quantity input uses `step: 'any'` and `parseFloat`. Fewer decimals would show a lot as fewer shares than it holds.
- **The stop-loss analysis takes fractional `shares`** on the route, the service and the `get_stop_loss_analysis` MCP tool. `stops.share_count` keeps a whole count an `int`, so the payload says `10`, not `10.0`.
- **Text output uses `format_quantity`** (`portfolio/stock.py`), which drops the padding `NUMERIC(18,6)` adds: the loss alert and the HTML report said "10.000000". The CSV readers use `parse_quantity`.
- **Harvester is whole-share and is not part of this.** `plan_instances.shares_initial` and the `plan_rungs` share columns are `INTEGER`, and the ladder search walks whole share counts. A plan's share counts are whole numbers whatever the holding is. `positions.shares` is a dead legacy `INTEGER` column, left NULL for a fractional lot.

The watchlist is DB-backed too (`watchlist` table, `WatchlistRepository` → `WatchlistService`, issue #83) but — unlike positions — it is **global**: one shared list, no `owner` column, with the writing principal recorded in `added_by` for audit only. `watchlist.yaml` is now purely an import format (`scripts/import_watchlist.py`, full-sync replace); every consumer (the daily report, the options screener, the fundamentals report, the REST tier) reads the table, and there is deliberately **no fallback to the YAML file** — an empty table is a loud Discord alarm (`alert_if_watchlist_empty` in `main.py`), not a quiet degrade. Surfaced as `GET/POST /api/watchlist`, `PATCH /api/watchlist/{ticker}` (tags only — it **replaces** the set rather than merging, because the UI sends the chip set it is displaying and a merge could never remove the last tag; PATCH not PUT because the currency is resolved server-side and must not be client-writable), `DELETE /api/watchlist/{ticker}`, `GET /api/watchlist/quotes` (live 1d columns for the list, the one watchlist read that may call Yahoo; see [The live 1d columns](#the-live-1d-columns)), `GET /api/watchlist/mine` (the same rows plus the caller's `held`/`quantity`/`avg_price`, from `WatchlistService.owner_view` in **seven queries, zero network** — the six plus one `list_positions(owner)`; issue #224, it replaced `GET /api/securities`), and `GET /api/watchlist/fundamentals` (returns + cached fundamentals for the whole list — `WatchlistService.returns_and_fundamentals` composes `PricesService` and `FundamentalsService` to serve it in **six queries and zero network calls**, which is what let the nightly HTML report become a page; the query count is constant in list size and `tests/test_watchlist_service.py` guards it, so do not "simplify" it into a per-symbol loop). All three routes must stay **declared before** `/watchlist/{ticker}` in `api/routers/portfolio.py` — FastAPI matches in declaration order. Plus `list_watchlist` / `add_to_watchlist` on the portfolio MCP wrapper. Removal is a UI-only action: the seam `mcp_gateway/rest_client.py` has no `delete` verb, so no agent can drop symbols off a list the whole team shares.

**The currency on a watchlist entry is resolved server-side, not supplied.**
`WatchlistService.add_entry` reads it off the exchange via `YFinanceGateway.ticker_info`
(`info["currency"]` — the *trading* currency, which is the unit `marketCap` is quoted in;
`financialCurrency` is a different thing and using it mislabels the cap). The `currency`
argument survives in the signature but has demoted to a **fallback**, used only when the
lookup comes back empty, and a disagreement is logged. Three entries seeded from
`watchlist.yaml` were declared USD and are not — ASSA-B.ST is Stockholm, AUTO.OL Oslo,
NIB.F Frankfurt — which renders a foreign market cap as dollars: a wrong number, not a
missing one. Consequences to keep straight:

- The lookup **fails soft, never closed** — Yahoo being down must not block adding a symbol.
  A miss falls back to the supplied value and logs a warning; it never raises.
- Soft is not enough on its own: it must also be **fast**, because this runs inside the user's
  `POST /api/watchlist`. `add_entry` passes `ADD_LOOKUP_TIMEOUT_SECONDS` (6s, under the
  gateway's 15s default) and `YFinanceGateway.ticker_info` enforces it with a bare **daemon
  thread + `join(timeout)`** — deliberately *not* a `ThreadPoolExecutor`. `Executor.__exit__`
  calls `shutdown(wait=True)`, so raising `TimeoutError` inside `with ThreadPoolExecutor(...)`
  blocks on the way out until the hung worker returns anyway: the timeout picks when the
  exception is *built*, not when the caller regains control (measured — a 1s timeout against a
  6s hang took 6.01s, and the add path held a caller 30s behind a 0.25s deadline). Don't
  "tidy" it back into an executor; `tests/test_yfinance_gateway.py` and
  `tests/test_watchlist_service.py` assert on elapsed wall clock for exactly that reason.
- `add_to_watchlist` on the portfolio MCP wrapper has **no `currency` parameter** at all, and
  the Add Security dialog shows its currency picker on the Portfolio tab only. Don't add
  either back; `tests/test_mcp_seam.py` and `AddSecurityDialog.test.tsx` guard both.
- `POST /api/watchlist` returns `{symbol, destination, currency}` — the currency that was
  *stored*, which is not necessarily what was posted.
- Rows already in the table are repaired by **`scripts/repair_watchlist_currency.py`**
  (dry run by default, `--apply` to write, `--symbols A,B` to scope, refuses prod without
  `--allow-prod`), which drives `WatchlistService.resync_currencies`.
