# Consistent return columns and live quotes on Portfolio and Watchlist (#518)

**Status:** PR 1 (batch quotes) merged as #520; PR 2 (reader policy) merged as #521, with its follow-up #522; PR 3 (re-fetch rule) merged as #523; PR 4 (nightly close-out) merged as #524; PR 5 (live cache writer) merged as #525; PR 6 (API) merged as #526; PR 7 (UI) merged as #527, with its follow-up #528 (the unsorted-column icon). The measurements owed on test were run on 2026-11-12 and 2026-11-13 and are at the end of the checkpoint log; two questions they raised are open. Issue [#518](https://github.com/JohnFunkCode/StockPortfolioManager/issues/518).
Related: #126 (asked for "today's gain/loss", never built), #373 (Yahoo 429s at the daily job's call
volume), #224 (`/watchlist/mine` became the Watchlist page's source).

## Why

This section describes the code as it was before the work started. PR 1 (#520) has since retired
`get_latest_quotes`; live quotes now come from `YFinanceGateway.batch_quotes`.

The two pages show different return windows, computed different ways:

| | Portfolio | Watchlist |
|---|---|---|
| Columns | 7d / 30d / 90d | 5d / 30d / 60d / YTD / 1y |
| Returns | `PortfolioService._period_returns`: per-symbol loop, can call Yahoo once per symbol | `PricesService.bulk_period_returns`: one SQL query, no network |
| Current price | live, `PricesService.get_quotes` → `YFinanceGateway.get_latest_quotes` | last cached daily close |

Neither page has a 1-day column. `get_latest_quotes` uses
`yf.download(period="1d", interval="1m", prepost=True)`, which looks batched but sends one chart
request per ticker (see gotcha 1). A live 1d column on the 260-symbol watchlist would cost 260 Yahoo
requests per refresh.

## Decisions (2026-11-10)

1. **Both pages show 1d / 5d / 30d / 60d / YTD / 1y.** The 1d–60d windows count **trading bars**, not
   calendar days (`analytics.returns.trailing_return`): "30d" is about 6 calendar weeks. YTD and 1y
   stay calendar-anchored, as `bulk_period_returns` computes them today. A column tooltip says
   so, and nobody "fixes" it to calendar days.
2. **One batch-quote path for both pages**, through Yahoo's `v7/finance/quote` endpoint in chunks of
   **100**. The server-side quote cache TTL goes from 30 s to **60 s**; the 10 s force floor stays.
3. **Pages refresh on load and on the Refresh button only.** No polling.
4. **1d is the regular-session change** (`regularMarketChangePercent` from the quote). Next to it,
   show the **pre-market** or **after-hours** change when that session is active or just happened.
   The session is labelled **per row**, because foreign listings run on their own hours.
5. **The Portfolio's current price and gain/loss keep today's behaviour:** the latest price, which
   after 4pm is the post-market price. The after-hours change shown next to 1d is what reconciles the
   two.
6. **Fail soft and fast.** A symbol missing from the response, or a failed or timed-out request,
   falls back to cached daily bars, marked `stale` with its true `as_of`. A page never waits on Yahoo
   past a short timeout and never fails because of it.
7. **Stale rows sort last** when sorting by 1d, in both directions, like empty cells. They stay
   visible with their stale marker.
8. **Live quotes are written through to the OHLCV cache** as `OPEN` daily bars, under the writer
   rules below.
9. **`get_history` re-fetches an `OPEN` bar only once it is 5 minutes old**, not whenever one exists.
10. **The nightly job closes out `OPEN` daily bars** with Yahoo's final values, and alarms if any
    from before today remain.
11. **Reader policy, following the industry pattern:** live displays may use today's forming bar;
    anything that ranks, alerts or is volume-based reads closed bars only (below).
12. **`/api/watchlist/mine` stays zero-network.** It is a guarded rule (CLAUDE.md;
    `tests/test_watchlist_service.py` fails on any attribute access that would be a network call).
    Watchlist quotes come from a new endpoint the page calls next to it.

Out of scope: streaming (`yf.WebSocket`), a scheduled intraday snapshot job, intraday intervals in the
cache, and changing the bar-count meaning of the windows.

## Design

### The batch quote

`YFinanceGateway.batch_quotes(symbols) -> dict[str, Quote | None]`:

- calls `https://query1.finance.yahoo.com/v7/finance/quote` with `symbols=<comma list>` and
  `formatted=false`, through `yfinance.data.YfData().get_raw_json` (which handles the cookie and crumb)
- chunks at 100 symbols; a chunk that fails maps its symbols to `None` and the others still return
- never raises; a short timeout per chunk
- `YfData` is imported **at module level**, so a yfinance release that moves it fails import (and the
  `lean-import` CI job) on the weekly dependency-upgrade PR, not in production

A `Quote` carries: `regular_open`, `regular_high`, `regular_low`, `regular_price`, `regular_volume`,
`regular_time` (epoch), `previous_close`, `regular_change_pct`, `pre_price`, `pre_change_pct`,
`post_price`, `post_change_pct`, `market_state`, `currency`, `exchange_tz`, plus `pre_time` and
`post_time` (added in PR 1 for the latest-price rule below).

`PricesService.get_batch_quotes(symbols, force=False)` holds the per-process TTL cache (60 s, force
floor 10 s, the same shape as today's `_quote_cache`). It returns per symbol the quote fields plus:

- `price`: the **latest** price: whichever of `regular_price`, `pre_price` and `post_price` has the
  newest time. After 4pm and overnight that is the post-market price, before the open the pre-market
  one. This keeps decision 5. (PR 1 chose by time rather than by `market_state`: in `PREPRE` Yahoo
  sends no pre price but still sends last night's post price, and a state rule would have dropped it.)
- `as_of` (the chosen price's time), `stale`, and `quote` (the full `Quote`, None on the fallback)

`get_quotes` (today's Portfolio path) is routed through it, and `get_latest_quotes` is retired.

### The live cache writer

`OhlcvRepository.upsert_live_daily_bars(quotes)`, called by `get_batch_quotes` after each successful
fetch. It does **not** go through `store_bars`, which picks the status from the clock. Rules:

1. **Same timestamp as the cache's daily bars.** `ts` = midnight **UTC** of `regular_time`'s date
   in `exchange_tz`, as epoch seconds. Never "now", and never the UTC date of the instant (gotcha 4,
   corrected in PR 5: this line first said midnight in `exchange_tz`).
2. **Regular-session fields only:** `regular_open/high/low/price/volume`. Never a pre- or post-market
   price.
3. **Never overwrite a `CLOSED` bar:**
   `ON CONFLICT (symbol, interval, ts) DO UPDATE ... WHERE ohlcv.status = 'OPEN'`. On weekends and
   holidays the quote's date is the last session's, whose bar is already `CLOSED`, so the write does
   nothing.
4. **Always status `OPEN`**, even after the close. The nightly close-out or the next `get_history`
   re-fetch finalizes it.
5. **Skip unusable quotes:** any of open/high/low/price that is `None`, NaN, or ≤ 0 (gotcha 5), and
   any symbol the response left out.
6. `adj_close` is written as NULL; the existing `COALESCE` keeps any value the harvester stored.
7. One `executemany`, bound parameters only (#462). `ohlcv` is not a per-owner table, so the owner
   isolation scan doesn't apply.

### The re-fetch rule

`get_history`'s second condition changes from "an `OPEN` bar exists" to "an `OPEN` bar exists **and**
its `ingested_at` is more than `OPEN_BAR_MAX_AGE_SECONDS` (300) old". It needs a repository read,
`open_bar_state(symbol, interval)`, returning the oldest `OPEN` bar's `ts` and the oldest
`ingested_at` among `OPEN` bars (as built in PR 3; the plan first named it `open_bar_ingested_at`).

Why it matters: a re-fetch downloads the caller's **whole window** (for example 365 days) as one
per-symbol `yf.download`. `get_history` sits under about 16 `PricesService` call sites,
`RecommendationsService` (4 calls per recommendation: the symbol, SPY, QQQ, the sector ETF),
`ArbitrageService` and `OptionsService`. On a symbol the quote path keeps fresh, `ingested_at` is
rewritten every quote refresh, so `get_history` almost never re-fetches it.

The rule applies to every interval (an `OPEN` 1h bar is just as costly), and an `OPEN` bar from a
**previous** session is always re-fetched regardless of age, so a missed close-out heals on the first
touch.

### The nightly close-out

A new tail step in `main.py`, `run_ohlcv_closeout`. It runs **last**, after
`run_fundamentals_warming`, so the tail becomes notifications → options capture → gamma/GEX → capture
health check → news staleness → fundamentals warming → **OHLCV close-out**. Daily bars can be
re-fetched any later night and an options chain can't, so the close-out yields to everything else.

- **Symbols:** every symbol with an `OPEN` `1d` bar, plus every watchlist and held symbol whose newest
  `CLOSED` bar is older than today's session.
- **Fetch:** per symbol through the existing `fetch_history` + `store_bars`, which reclassifies the
  bar `CLOSED` once its session is over. The window starts at the **earlier** of 10 calendar days ago
  and the symbol's oldest outstanding `OPEN` `1d` bar, so an `OPEN` bar older than 10 days (a run
  that missed several nights) is still finalized instead of tripping the alarm every night. Paced in chunks, with
  a pause between them, to stay clear of the #373 429s.
- **Budget:** `OHLCV_CLOSEOUT_BUDGET_SECONDS`, clamped by `_remaining_budget`, falling back to the
  default on an unparseable value (the CLAUDE.md tuning-env-var rule).
- **Guard:** an outer `try/except` that never raises, like the other tail steps.
- **Alarm:** after the step, re-read the database. If any `OPEN` `1d` bars dated before today remain,
  send one Discord alarm with the count and up to 10 symbols.
- Skipped on NYSE-closed days along with the rest of the job.

### Reader policy: who may see today's forming bar

The common industry pattern is that **live displays use the forming bar, and anything that ranks,
triggers, or is tested historically waits for the bar to close.**

- **TradingView** recalculates indicators on every update of the live bar and offers
  [`barstate.isconfirmed`](https://tradingview.com/pine-script-docs/concepts/repainting) to act only
  on a bar's final update.
- **StockCharts** lets daily scans run on intraday updates, treating the latest as
  [the current period's close](https://scan.stockcharts.com/discussion/comment/5713). It also warns
  that partial-day results [aren't "true" readings](https://scan.stockcharts.com/discussion/comment/3211)
  and [excludes volume-based indicators](https://scan.stockcharts.com/discussion/comment/8854) from
  intraday scans.
- **QuantConnect LEAN** emits a daily bar consolidated from minute data only
  [at market close or later](https://quantconnect.com/docs/v2/writing-algorithms/consolidating-data/consolidator-types/mixed-mode-consolidators).

Applied here:

| May use the `OPEN` bar (live display) | Closed bars only |
|---|---|
| returns columns (`bulk_period_returns`) | options watchlist screen (`OptionsScreeningService`, a ranking) |
| price-level tools: support levels, VWAP, ATR bands, gap analysis | volume-based: `get_obv`, `get_volume_profile` |
| price-based indicators: RSI, MACD, stochastic, moving averages | anything that alerts or changes stored state |
| Sidekick and MCP analysis | `screen_securities` (a ranking) |

Mechanism:

- `include_open` is a **required keyword-only** argument on the repository reads (`get_bars`,
  `daily_history_for_symbols`, `daily_bars_for_symbols`), so every caller states its choice. When it
  is `False`, rows with `status = 'OPEN'` are excluded in SQL.
- `PricesService.get_history(..., include_open: bool = True)`: its ~16 callers are live displays.
  Closed-only callers pass `include_open=False` explicitly, and a test lists them.
- A response built on a forming bar carries `latest_bar_open: true`, so the UI and the Sidekick can
  label it.
- Today's alert paths don't read `ohlcv`: `notifier.py` uses the legacy `portfolio/` metrics, and the
  harvester scan runs after the close. They're listed so a future reader of the cache knows which side
  it's on.

### Endpoints

- `GET /api/portfolio/symbols` (existing): rows gain `return_1d`, `return_5d`, `return_60d`,
  `return_ytd`, `return_1y` from `bulk_period_returns`, plus `quote` fields: `regular_change_pct`,
  `pre_change_pct`, `post_change_pct`, `market_state`, `quote_as_of`, `quote_stale`.
  `return_7d`/`return_90d` remain until PR 7 removes them.
- `GET /api/watchlist/quotes` (new): the quote fields above for every watchlist symbol, from
  `get_batch_quotes`. Declared **before** `/watchlist/{ticker}` in `api/routers/portfolio.py`, as
  CLAUDE.md requires for `/watchlist/fundamentals` and `/watchlist/mine`. Protected by
  `require_principal` and counted by the rate limiter like every other route. Errors go through
  `route_internal_error`. The watchlist is global, so there's no owner predicate.
- `GET /api/watchlist/mine` (existing): gains `return_1d` from `bulk_period_returns` (zero-network,
  unchanged shape otherwise) as the fallback value.
- `?force=true` on both quote-bearing routes maps to `get_batch_quotes(force=True)` (the 10 s floor).

The page computes nothing: the 1d it shows is `regular_change_pct` when the quote is fresh, otherwise
`return_1d` with the stale marker. That choice is made server-side and returned as `return_1d_display`
and `return_1d_stale`, so the front end only renders (arch-v2 Rules 8–9).

## PR sequence

Each PR updates its own docs (CLAUDE.md's first rule) and gets its one Guppy review. The guards (PRs
2–4) land before the writer (PR 5), so the cache never holds universal `OPEN` bars without them. The
API stays additive until the UI switches (PR 7). PRs 1 and 4 are independent of the rest. **PR 3
follows PR 2:** both change `get_history` (PR 2 adds `include_open`, PR 3 changes its re-fetch
condition) and both add to the "Today's forming bar" docs section, so run in parallel they would
conflict on merge.

```
PR 1 batch quotes ─────────────────┐
PR 2 reader policy ─▶ PR 3 re-fetch ┼─▶ PR 5 live writer ─▶ PR 6 API ─▶ PR 7 UI
PR 4 close-out ────────────────────┘
```

### PR 1: batch quote gateway and service (backend, no visible change)

1. `YFinanceGateway.batch_quotes` with module-level `YfData` import, 100-symbol chunking, a per-chunk
   timeout, and never raising.
2. `PricesService.get_batch_quotes` with the TTL cache; `QUOTE_TTL_SECONDS = 60`; the latest-price
   rule (decision 5).
3. Route `get_quotes` through it and delete `get_latest_quotes`. `list_lots`/`symbol_rows` keep their
   response shape.
4. Tests (Yahoo always stubbed): chunking at 100 (260 symbols → 3 calls); a missing symbol → `None`
   → last-close fallback; a failed chunk leaves the other chunks' results; timeout; TTL 60 s and the
   force floor; latest-price selection per `market_state`; `YfData` and `get_raw_json` exist with the
   expected signature.
5. Update the existing tests of the deleted method: `tests/test_prices_history_policy.py` (the
   `get_quotes` cases that stub `gateway.get_latest_quotes`) moves to stubbing `batch_quotes`, and
   `tests/test_api_smoke.py`'s `get_latest_quotes` patch (and its docstring) follows.
6. Docs: CLAUDE.md, under *Unified Database* or the gateways bullet, says the v7 batch endpoint is the
   quote path, `yf.download` is per-ticker, and `YfData` is the one yfinance internal in use.
   `docs/architecture/data.md`: the quote path.

### PR 2: reader policy (`include_open`)

1. Required keyword-only `include_open` on `get_bars`, `daily_history_for_symbols`,
   `daily_bars_for_symbols`; update every caller.
2. `get_history(..., include_open=True)`; pass `False` from `get_obv`, `get_volume_profile`,
   `screen_securities` and the options screening history read.
3. `latest_bar_open` on responses built on a forming bar (the price/indicator responses that
   `get_history` feeds).
4. Tests: `include_open=False` drops an `OPEN` row; each closed-only reader passes `False` (a test that
   lists them, so a new ranking reader has to be added consciously); `latest_bar_open` set and unset.
5. Behaviour change to note in the PR: today, symbols touched during the day already have `OPEN` bars,
   so the options screen, OBV and volume profile stop including a partial day starting with this PR.
6. Docs: `docs/architecture/data.md` gets the new **"Today's forming bar"** section, holding the
   reader-policy table and the three sources. CLAUDE.md, under *Unified Database*, gets the rule line
   "a reader that ranks, alerts, or uses volume reads closed bars only (`include_open=False`)", linking
   there.

### PR 3: `OPEN`-bar re-fetch rule

1. `OhlcvRepository.open_bar_state` (first planned as `open_bar_ingested_at`).
2. `get_history`: re-fetch an `OPEN` bar only when it is older than `OPEN_BAR_MAX_AGE_SECONDS = 300`,
   or when it is dated before the current session.
3. Tests: a fresh `OPEN` bar is not re-fetched; one 301 s old is; an `OPEN` bar from yesterday is
   re-fetched at any age; intraday intervals follow the same rule.
4. Docs: the "Today's forming bar" section gains the re-fetch rule and why (the full-window download).

### PR 4: nightly close-out

1. `run_ohlcv_closeout` in `main.py`, the last tail step (after `run_fundamentals_warming`), with
   budget, clamp, guard and alarm as designed.
2. Repository reads: symbols with an `OPEN` `1d` bar and each one's oldest such bar date; the count of
   `OPEN` `1d` bars dated before today.
3. Tests: it finalizes an `OPEN` bar (stubbed fetch); an `OPEN` bar more than 10 days old is inside
   the requested window and is finalized; it respects the budget clamp; an unparseable
   budget env var falls back to the default; an exception inside never escapes; the alarm fires with
   the count and stays quiet at zero; it doesn't run on a closed day.
4. Measure on test after merge: the step's wall time for ~260 symbols, and whether any 429s appear.
   Record the numbers in the checkpoint log, and tune chunk size and pause before prod.
5. Docs: `docs/architecture/daily-jobs.md` (the step, its order and why, the env var, the alarm); the
   CLAUDE.md order sentence in *The daily job*.

### PR 5: live cache writer (after PRs 2–4 are on main)

1. `OhlcvRepository.upsert_live_daily_bars` with rules 1–7.
2. `get_batch_quotes` calls it after each successful fetch, wrapped so a write failure is logged and
   never fails the quote.
3. Tests against the real database: the `ts` rule for a US symbol, a foreign symbol (`Asia/Seoul`), a
   weekend and a holiday; a `CLOSED` bar is never overwritten; zero/NaN prices are skipped; pre/post
   prices never reach the bar; a repeat write updates the same row (no duplicate `ts`);
   `ingested_at` advances; `adj_close` keeps a harvester value.
4. Verify on test after deploy: load the Watchlist page during market hours, then check that each
   watchlist symbol has exactly one `1d` row for today and that the row is `OPEN`. After the nightly
   run, check that none are left `OPEN`.
5. Docs: the "Today's forming bar" section gains the writer and its rules. CLAUDE.md, under *Unified
   Database*, gets the rule line "live quotes write `OPEN` daily bars from regular-session fields only
   and never overwrite `CLOSED`", and the "two writers" sentence becomes three.

### PR 6: API (additive)

1. `PortfolioService.symbol_rows`: returns from `bulk_period_returns(periods=(1, 5, 30, 60))`, whose
   `return_ytd` and `return_1y` come from the same read — calendar-anchored (`returns.ytd_return` and
   `returns.one_year_return` on the dated closes), not trailing bar counts — plus the
   quote fields and `return_1d_display`/`return_1d_stale`. Keep `return_7d`/`return_90d` for now.
   Retire `_period_returns`'s per-symbol loop by computing the old fields from the same batch read.
2. `WatchlistService`: `return_1d` on `/watchlist/mine` (still zero-network; the guard test stays
   green unchanged).
3. `GET /api/watchlist/quotes`, declared before `/watchlist/{ticker}`, with `force`.
4. `api/schemas/portfolio.py` additions; regenerate `docs/openapi-surface.txt`.
5. MCP: if `get_portfolio`'s payload changes, update its case in `scripts/mcp_tool_cases.py`.
   Decide in review whether to add a `get_watchlist_quotes` MCP tool; if added, it needs its contract
   case and the 63-tool pin moves.
6. Tests: route order; the stale fallback end to end; error redaction for the new route (add it to
   `tests/test_error_redaction.py`); the rate limiter counts it.
7. Security review (Rule 11): a new route (A01: global data, principal required; A05: error
   redaction; G2: rate-limited). Note it in the PR and update `docs/architecture/security.md` if a row
   changes.
8. Docs: `docs/architecture/quantui.md` and readme endpoint list.

### PR 7: UI

1. `PortfolioPage.tsx`: columns 1d / 5d / 30d / 60d / YTD / 1y; the pre/post badge next to 1d; the
   stale marker; stale-last sort; tooltips on the windows.
2. `WatchlistPage.tsx`: a `1d` column fed by a `useWatchlistQuotes` query merged onto `/watchlist/mine`
   rows by symbol; the same badge, marker, sort and tooltips. `staleTime` 60 s, no `refetchInterval`;
   the page's Refresh button refetches with `force=true`.
3. Colours via `var(--qc-…)` and `tint()`, never literal hex (the themes rule).
4. Remove `return_7d`/`return_90d` from the schema and service now that nothing reads them.
5. Sidekick: if `PortfolioTableCard` or `WatchlistFundamentalsCard` show return columns, update their
   prop specs in both `chat_tools.py` and `componentRegistry.tsx`, plus parity cases.
6. Vitest: loading/error/success; key values; the badge per `market_state`; stale marker; stale-last in
   both sort directions; no polling (no `refetchInterval`). Coverage thresholds only ratchet up.
7. Verify in the browser against test: both pages during market hours and after the close.
8. Docs: `docs/architecture/quantui.md` (columns, quote source, fallback, stale-last, refresh on
   load/button only); readme UI section.

## Gotchas

1. **`yf.download` with many tickers is not one request.** `multi.py` loops `for ticker in tickers`
   and issues one chart request per ticker in threads. It looks batched from the call site, which is
   why the Portfolio's quote path looked cheap. 20 symbols took 4.3 s.
2. **yfinance has no public batch quote.** It calls `v7/finance/quote` only from `Ticker.info`, with
   one symbol. `yf.screen` with `EquityQuery('is-in', ['ticker', ...])` is rejected:
   `Invalid field ... "ticker"`, because `ticker` is a valid field only for `FundQuery`.
   `yf.WebSocket` sends a message only when a symbol trades, so quiet symbols and closed markets give
   nothing.
3. **The batch response silently omits symbols.** DOMO was simply absent from 260 requested, with no
   error entry. Compare requested to returned; never assume a 1:1 response.
4. **A daily bar's `ts` is midnight UTC of the exchange's local session date, not the quote time.**
   AAPL 2026-11-10 is `1794268800` in `ohlcv`, while `regularMarketTime` is `1794344400` (4pm ET).
   `ts` is in the primary key, so the wrong value makes a second bar for the same day that no fetch
   removes. *This gotcha first said `1794286800`, midnight ET, and "midnight in Seoul" for
   `000660.KS`. That is what `Ticker.history`'s tz-aware index gives. The cache is filled by
   `yf.download`, whose daily index is tz-naive, and `.timestamp()` reads a naive value as UTC.
   Measured in PR 5 through `fetch_history` for AAPL, `000660.KS` and `ASSA-B.ST`: every stored
   `ts % 86400` is 0.*
5. **A foreign listing before its open returns `0.0` for open, high and low** (`000660.KS`, state
   `PRE`). `ohlcv` has `CHECK(close > 0)`; skip, don't write.
6. **`/api/watchlist/mine` must stay zero-network.** Putting the quote call inside it would fail
   `tests/test_watchlist_service.py`'s guard. That's why the Watchlist gets a separate quotes endpoint.
7. **Running Yahoo probes from the sandbox:** `$TMPDIR` differs between a sandboxed and an
   unsandboxed shell, so a file written by one isn't at the same path in the other.

## Checkpoint log

- 2026-11-10: design agreed in discussion; issue #518 filed; this plan written. Measured the v7 batch
  endpoint against the 260 prod watchlist symbols: 3 requests, 1.32 s, 259 returned (DOMO missing).
- 2026-11-10: review fixes on PR #519. PR 3 now follows PR 2 (both edit `get_history`); PR 1 updates
  the existing `get_latest_quotes` tests; the close-out's position is stated as the last tail step;
  its fetch window reaches back to the oldest outstanding `OPEN` bar; YTD/1y named as calendar-anchored.
- 2026-11-10: PR 1 (batch quotes) opened. `YFinanceGateway.batch_quotes` runs the chunks
  concurrently on daemon threads under one 8 s deadline (the `ticker_info` pattern), so a hung chunk
  is abandoned rather than waited on. Live probe: AAPL/MSFT/000660.KS/DOMO/a fake symbol in 0.4 s;
  DOMO and the fake came back None, as gotcha 3 predicts. The plan had named the returns method
  `batch_returns`; its real name is `bulk_period_returns`, now corrected throughout.
- 2026-11-11: PR 1 merged as #520. PR 2 (reader policy) opened. `include_open` is required on the
  three repository reads and excluded in SQL as `(%s OR status != 'OPEN')` with a bound boolean, so
  `tests/test_sql_bound_params.py` needed no new `ALLOWED` entry. The options screen has three
  history reads, not one: its cached bulk read plus the live path's `fetch_bollinger_bands` and
  `fetch_iv_analysis`; all three are closed-only. `latest_bar_open` travels from `get_bars` to the
  service in `DataFrame.attrs`, which costs no extra query and survives `.copy()`; it is on the 15
  `PricesService` responses that read the forming bar. Two calls made here: `get_volume_analysis`
  stays on the display side (the plan listed only OBV and volume profile as volume-based), and so
  does `get_stock_price` although it saves a snapshot. Gotcha: `tests/test_volume_profile.py` pins
  `get_history`'s exact call arguments, so a reader moving sides shows up there too.
- 2026-11-11: review fixes on PR #521. A closed-only `get_history` call no longer re-fetches a
  forming bar it won't return. `get_risk_signals` and `get_technical_signals` carry a top-level
  `latest_bar_open`. The test now lists the live-side `get_history` callers too. Open decision
  recorded in `data.md`: `get_dark_pool` (volume-based) and the arbitrage scan (a ranking) still
  read the forming bar.
- 2026-11-11: follow-up to PR 2, decided by John after the #521 review: `get_dark_pool`
  (volume-based) and the arbitrage scanner's `_closes` (a ranking) move to closed bars only.
  `_closes` is `ArbitrageService`'s only history read, so `analyze_arbitrage_pair` and pair
  discovery move with the scan.
- 2026-11-11: PR 2 merged as #521 and its follow-up as #522. PR 3 (re-fetch rule) opened. The
  repository read is `open_bar_state`, not the planned `open_bar_ingested_at`: the "earlier session"
  half of the rule needs the bar's `ts` as well, and one query returns both. It reports the
  **oldest** `ts` and `ingested_at` among `OPEN` bars, not the newest, because an intraday interval
  holds several `OPEN` bars during the session and a leftover one from an earlier session must not
  hide behind today's. "Dated before the current session" is implemented as "before `market_date()`".
  `latest_completed_session()` can't be used for it: despite the name it returns the latest session
  that has *started*, which is today during the session. Gotcha: today's bar stays `OPEN` after the
  4pm close until a re-fetch older than 300 s or the close-out (PR 4) finalizes it, and once the
  live writer (PR 5) keeps `ingested_at` fresh, only the close-out or the date rolling over does.
  `has_open_bar` is no longer called by the service; it stays on the repository.
- 2026-11-12: review fixes on PR #523. The review suggested guarding against an `OPEN` row with a
  NULL `ingested_at` hiding behind a sibling's. `ohlcv.ingested_at` is `NOT NULL`, so that can't
  happen; the first cut's "no `ingested_at` counts as due" branch, its test and the docs sentence
  were dead and are removed. Added a repository test that a bar left `OPEN` is stored `CLOSED` when
  its session's bars are stored again.
- 2026-11-12: PR 3 merged as #523. PR 4 (nightly close-out) opened. The rules live in
  `PricesService` (`closeout_plan`, `close_out_daily_bars`, `open_daily_bars_before`) and `main.py`
  holds the loop, budget and alarm, like the other tail steps. Two things the design didn't say:
  a universe symbol with **no** cached daily bars is left out, because a 10-day window stored for
  it makes `count_cached > 0` and `get_history` would then never run its cold-start warm; and
  `run_ohlcv_closeout` checks `is_trading_day` itself so the closed-day rule is testable. Gotcha:
  the capture, GEX and warming budgets already sum to the 1800 s task timeout, so the close-out
  only runs in the time the earlier steps leave unspent. A starved night doesn't alarm that
  night (today's bar isn't "before today"); it alarms on the next run if the bars are still open.
  Pacing is 25 symbols then a 2 s pause, default budget 300 s; both unmeasured until step 4
  (wall time and 429s on test for ~260 symbols).
- 2026-11-12: review fixes on PR #524. The walk now pauses before it checks the budget, so no
  symbol starts after a pause that spent the last of it, and a test drives the budget with a clock
  that only moves on sleep. The summary's `stored` count is renamed `fetched`: `store_bars` drops
  invalid bars silently, so the walk can only say Yahoo sent bars; the alarm's database read is
  what says whether they were closed. The *Why* section is marked as the pre-#520 state.
- 2026-11-12: PR 4 merged as #524 and deployed to test; its step-4 measurement (wall time and 429s
  for the full universe) is still to be read from the first nightly run. PR 5 (live cache writer)
  opened. **The plan's `ts` rule was wrong** and is corrected above (rule 1, gotcha 4): the cache
  stores a daily bar at midnight UTC of the session date, not midnight on the exchange. Built as
  planned, every live write would have added a second bar per symbol per day. The rule is one pure
  function, `market_time.session_bar_ts`; `PricesService._live_daily_bar` maps and filters a quote,
  and the repository takes plain bar dicts so it doesn't import the gateway's `Quote`. One rule
  added to the design: **no live bar for a symbol with no daily history** (the insert is
  `INSERT … SELECT … WHERE EXISTS`), for the same cold-start reason as PR 4. It also corrects a
  paragraph PR 3 put in `data.md`, which said an exchange east of UTC has bars dated the day before.
  Gotcha: the v7 quote endpoint needs `www.yahoo.com` for its cookie, so a sandboxed probe that
  allows only the `finance.yahoo.com` hosts gets history but an `HTTPError` on quotes.
- 2026-11-12: review fixes on PR #525. `upsert_live_daily_bars` now drops a bar the table would
  reject (non-finite or non-positive price, negative or non-finite volume) itself, as `store_bars`
  does, so the batch doesn't depend on the service's filter. Added a test of a bar's whole life:
  written `OPEN` by a quote, stored `CLOSED` by the close-out on the **same row** (which also
  proves the two writers agree on `ts`), and not reopened by a later quote.
- 2026-11-12: PR 5 merged as #525. PR 6 (API) opened. `PricesService.quote_changes` builds the
  quote columns for both pages and `analytics.returns.display_1d` makes the display choice. Three
  things the plan didn't settle:
  - **The quotes endpoint reads the `return_1d` fallback itself, but only for symbols without a
    live change.** `daily_bars_for_symbols` has no date bound, so reading it for the whole list on
    every quote refresh would load every bar of 260 symbols a second time per page load.
  - **Retiring the Portfolio's per-symbol `get_history` loop removed the only thing that fetched a
    newly held symbol's history.** The close-out skips a symbol with no bars (PR 4) and so does the
    live writer (PR 5), so its return columns would have stayed empty until someone opened an
    analysis tool. `_warm_cold_history` keeps that one fetch, for symbols with zero cached bars.
  - **No `get_watchlist_quotes` MCP tool.** Left for review as the plan says; the tool count stays
    at 63. `get_portfolio`'s payload grew (additive) and its docstring is updated; its contract case
    pins the request, not the payload, so it is unchanged.
  `return_7d`/`return_30d`/`return_90d` are now floats from the bulk read, not `Decimal`s; the JSON
  is the same, since the response class already wrote a `Decimal` as a number. `/watchlist/fundamentals`
  gains `return_1d` too, because it shares its row builder with `/watchlist/mine`. Gotcha:
  `scripts/check_openapi_snapshot.py --update` imports `api.main`, which runs `ensure_schema()`
  against `QUANTCORE_DB_DSN`; run it as `python -c "import tests, ..."` so the unit-test DSN is
  swapped in first.
- 2026-11-12: review fixes on PR #526 (verdict REQUEST_CHANGES, two warnings). `quote_changes` is
  split: the per-symbol row is `_quote_change_row` and the fallback read is `_cached_return_1d`.
  A failed fallback read no longer fails `/watchlist/quotes`; the test for it found a second,
  older raise on the same path (`_last_closes`, the quote cache's own last-close fallback), which
  is now guarded too. A symbol that stays cold is warmed at most once an hour per process.
  Gotcha: the review measures complexity with radon, which counts comprehensions and boolean
  operators; ruff's C901 (mccabe) does not, so "nothing over 10 on ruff" said nothing about the
  reviewer's threshold. Radon isn't installed in the development environment; the second review
  measured the fixed head with radon 6.0.1: `quote_changes` 7, `_quote_change_row` 5,
  `_cached_return_1d` 4, all within the goal of 10.
- 2026-11-12: second review of PR #526 (COMMENT), three suggestions. A NaN or infinite quote
  change is now treated as a missing one (`display_1d`, `_pct2`), `data.md` says all three watchlist
  routes are declared before `/watchlist/{ticker}`, and a test pins that `get_batch_quotes` returns
  an entry for every requested symbol when Yahoo's reply omits some.
- 2026-11-12: PR 6 merged as #526. PR 7 (UI) opened, the last of the sequence. Both tables render
  1d through one component, `OneDayChange`, and sort it with `compareOneDay` (stale last both
  ways). `return_7d`/`return_90d` are gone from the service, the schema and the page. Decisions
  made here:
  - **The Watchlist shows the cached `return_1d` (stale) until the quotes request answers**, so the
    table never waits on Yahoo, and keeps showing it for a symbol the quotes leave out.
  - **The Watchlist got a Refresh button** (it had none); it refetches the quotes with
    `force=true`. The Portfolio's existing Refresh is unchanged: it refetches without `force`, so
    the server's 60 s quote cache still applies there.
  - **No Sidekick card changes.** No registered component shows return columns, so plan step 5
    had nothing to update.
  Gotcha: a DataGrid cell's line box is the row height, so an inline wrapper in `renderCell` sits
  lower than the bare `Typography` cells beside it. The 1d cell's wrapper is a block-level `body2`.
  Verified in a browser against a **stub API with canned data** (both pages, dark and light
  themes, the Refresh button sending `force=true`); not against the deployed test service.
- 2026-11-13: the three measurements owed on test, run against the deployed test service (229
  watchlist symbols, 5 holdings) after the close on 2026-11-12 and in market hours on 2026-11-13.
  1. **Close-out cost (PR 4 step 4): no tuning needed, but the full universe is still unmeasured.**
     The nightly run of 2026-11-12 logged `Close-out: 7 fetched, 2 empty, 0 failed, 0 not
     attempted, 2s`, with no 429s and exit 0. Only 9 symbols had an `OPEN` bar that night, because
     the live writer had deployed that afternoon and few symbols had been quoted. `OHLCV_CLOSEOUT_CHUNK_SIZE` and
     `_PAUSE_SECONDS` are unchanged. The number to look at is the close-out line of a run that
     follows a market-hours page load (the first is 2026-11-13's).
  2. **Open bars (PR 5 step 4): pass.** After the nightly run there were no `OPEN` `1d` rows, and
     that day's 257 bars were all `CLOSED`. After market-hours loads of both pages there were 227
     `OPEN` rows for 227 symbols, every `ts` at midnight UTC, and no symbol with two bars for one
     day in the last five. The three watchlist symbols without one are explained: Yahoo returned
     no quote for DOMO and KVUE, and HSYDF's last trade was the day before, a bar already `CLOSED`.
  3. **Both pages (PR 7 step 7): pass, after the close and in market hours.** The Watchlist makes
     one `/watchlist/mine` and one `/watchlist/quotes` request per load, Refresh sends
     `?force=true`, DOMO and KVUE read `-2.5% stale`, and after-hours badges show after the close
     and not during the session. Warm, `/watchlist/quotes` answered in 0.3 to 1.0 s. All 229 rows
     sort correctly on 1d in both directions with the stale rows last. The Portfolio shows its six
     return columns from one `/portfolio/symbols` request. No console errors on either page.

  What the runs showed that the design did not anticipate:
  - **A quote can be a day old and not stale.** A symbol that has not traded yet today (AIXXF and
    HSYDF at midday, Korean listings in `PRE` state overnight) comes back with yesterday's
    `quote_as_of` and yesterday's change, and the page shows that as today's 1d with no marker.
    `quote_stale` is true only when Yahoo returns no quote. Whether such a row should read as
    stale is undecided.
  - **A live bar can be dated yesterday.** AIXXF's Nov 12 bar was not in the database after the
    nightly run, so the next day's quote, still carrying Nov 12's session, wrote an `OPEN` bar
    for Nov 12. That follows the rules (the bar's date is the session's, and a `CLOSED` bar is
    never overwritten); the next close-out is expected to close it, which was not checked.
  - **The live 1d and the close-to-close 1d differ on an ex-dividend day.** GLW went ex-dividend
    on 2026-11-13: Yahoo's `regularMarketPreviousClose` was 119.22, the stored close 119.50 less
    the 0.28 dividend, so the quote said -5.82% where the bars said -6.04%. The page shows the
    quote's figure. The two agree again the next day.
  - **The first load after idle is a cold start.** `/watchlist/quotes` took 17.7 s and
    `/watchlist/mine` 25.7 s while three api instances started, and one `sentiment-summary`
    request got a 500 for want of an instance. That is instance start-up, not the quote path: the
    next load took 1.0 s and 2.8 s.
  Gotcha: `get_connection()` returns a wrapper with `execute` but no `cursor()`, and `watchlist`
  has `symbol_id`, not `symbol` (join `symbols` for the ticker), so an ad-hoc read script written
  from memory fails twice before it runs.
