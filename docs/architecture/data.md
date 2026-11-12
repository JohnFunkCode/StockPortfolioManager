# Data: unified database, positions, watchlist

> Moved verbatim from `CLAUDE.md`, which keeps the rules an agent must not break as a short
> summary and links here for the explanation. Edit the detail here, the rule there.

## Unified Database (`quantcore/`)

All persistence is consolidated into a single **QuantCore** PostgreSQL database, accessed via `psycopg2`:

- **`quantcore/db.py`** — Shared connection factory (`get_connection()`) backed by `psycopg2`, connecting via the `QUANTCORE_DB_DSN` environment variable. Centralized schema DDL for all 24 tables (`init_schema()`), using `SERIAL` primary keys and `ON CONFLICT` upserts. Imported as `from quantcore.db import get_connection`.
- **Schema** includes: symbols, OHLCV (merged from daily + intraday intervals), fetch_log, positions/lot_sales/owner_identities, watchlist (the global shared list, #83), plan_templates/instances/rungs/alerts (Harvester), options_snapshots/expirations/contracts/options_capture_claims/gamma_wall_history/gex_history/options_positions (per-owner since V12, #460), news_articles, sentiment_snapshots, fundamentals_history, user_settings (per-owner UI preferences: the Sidekick chat model and daily token cap), chat_usage (per-owner, per-ET-day Sidekick token use and outstanding reservations, G2 #461), arb_nav_snapshots (curated holdings/capital-structure history for the arbitrage scanner's NAV vehicles).

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
`quote_as_of`, `quote_stale`, and the figure to show:

- `return_1d_display` is the quote's regular-session change when the quote is fresh, and otherwise
  the cached close-to-close `return_1d`. `return_1d_stale` is true in the second case. The choice
  is `analytics.returns.display_1d`, so no page makes it (arch-v2 Rules 8 and 9).
- The fallback is read from the cache only for the symbols that need it, so a call where every
  quote is fresh costs no SQL. A caller that already holds `return_1d` passes it in.
- It is one `get_batch_quotes` call: the 60 s TTL and the 10 s force floor apply. It never raises
  for a missing quote, and a failed database read for either fallback (the cached `return_1d`, or
  the quote cache's last close) is logged and leaves those rows stale with nothing to show. The
  rows with a live quote are unaffected.

Where it is served:

| Route | Source | Network |
|---|---|---|
| `GET /api/portfolio/symbols` | `PortfolioService.symbol_rows`: `bulk_period_returns` for `return_1d/5d/30d/60d/ytd/1y` (and the old `7d`/`90d`), plus `quote_changes` | the quote call `list_lots` already makes |
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

Positions are DB-backed with multi-owner support (`positions` table, `owner` column); `portfolio.csv` is a per-owner import format (`scripts/import_portfolio.py --csv portfolio.csv --owner john`, full-sync replace). The REST `GET/POST/DELETE /api/portfolio*` routes take an `?owner=` param defaulting to `john`; `main.py`'s report/notifications stay on John's portfolio.

The watchlist is DB-backed too (`watchlist` table, `WatchlistRepository` → `WatchlistService`, issue #83) but — unlike positions — it is **global**: one shared list, no `owner` column, with the writing principal recorded in `added_by` for audit only. `watchlist.yaml` is now purely an import format (`scripts/import_watchlist.py`, full-sync replace); every consumer (the daily report, the options screener, the fundamentals report, the REST tier) reads the table, and there is deliberately **no fallback to the YAML file** — an empty table is a loud Discord alarm (`alert_if_watchlist_empty` in `main.py`), not a quiet degrade. Surfaced as `GET/POST /api/watchlist`, `PATCH /api/watchlist/{ticker}` (tags only — it **replaces** the set rather than merging, because the UI sends the chip set it is displaying and a merge could never remove the last tag; PATCH not PUT because the currency is resolved server-side and must not be client-writable), `DELETE /api/watchlist/{ticker}`, `GET /api/watchlist/quotes` (live 1d columns for the list, the one watchlist read that may call Yahoo; see [The live 1d columns](#the-live-1d-columns)), `GET /api/watchlist/mine` (the same rows plus the caller's `held`/`quantity`/`avg_price`, from `WatchlistService.owner_view` in **seven queries, zero network** — the six plus one `list_positions(owner)`; issue #224, it replaced `GET /api/securities`), and `GET /api/watchlist/fundamentals` (returns + cached fundamentals for the whole list — `WatchlistService.returns_and_fundamentals` composes `PricesService` and `FundamentalsService` to serve it in **six queries and zero network calls**, which is what let the nightly HTML report become a page; the query count is constant in list size and `tests/test_watchlist_service.py` guards it, so do not "simplify" it into a per-symbol loop). Both routes must stay **declared before** `/watchlist/{ticker}` in `api/routers/portfolio.py` — FastAPI matches in declaration order. Plus `list_watchlist` / `add_to_watchlist` on the portfolio MCP wrapper. Removal is a UI-only action: the seam `mcp_gateway/rest_client.py` has no `delete` verb, so no agent can drop symbols off a list the whole team shares.

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
