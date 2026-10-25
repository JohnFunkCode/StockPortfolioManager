# Data: unified database, positions, watchlist

> Moved verbatim from `CLAUDE.md`, which keeps the rules an agent must not break as a short
> summary and links here for the explanation. Edit the detail here, the rule there.

## Unified Database (`quantcore/`)

All persistence is consolidated into a single **QuantCore** PostgreSQL database, accessed via `psycopg2`:

- **`quantcore/db.py`** — Shared connection factory (`get_connection()`) backed by `psycopg2`, connecting via the `QUANTCORE_DB_DSN` environment variable. Centralized schema DDL for all 23 tables (`init_schema()`), using `SERIAL` primary keys and `ON CONFLICT` upserts. Imported as `from quantcore.db import get_connection`.
- **Schema** includes: symbols, OHLCV (merged from daily + intraday intervals), fetch_log, positions/lot_sales/owner_identities, watchlist (the global shared list, #83), plan_templates/instances/rungs/alerts (Harvester), options_snapshots/expirations/contracts/options_capture_claims/gamma_wall_history/gex_history/options_positions, news_articles, sentiment_snapshots, fundamentals_history, user_settings (per-owner UI preferences, e.g. the Sidekick chat model), arb_nav_snapshots (curated holdings/capital-structure history for the arbitrage scanner's NAV vehicles).

All repositories under `quantcore/repositories/` and the REST API (`api/main.py`) use the shared factory instead of managing individual database connections.

**`ohlcv` has two writers, and they disagree about `adj_close`.** `OhlcvRepository` (the prices
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

## Positions and the watchlist

Positions are DB-backed with multi-owner support (`positions` table, `owner` column); `portfolio.csv` is a per-owner import format (`scripts/import_portfolio.py --csv portfolio.csv --owner john`, full-sync replace). The REST `GET/POST/DELETE /api/portfolio*` routes take an `?owner=` param defaulting to `john`; `main.py`'s report/notifications stay on John's portfolio.

The watchlist is DB-backed too (`watchlist` table, `WatchlistRepository` → `WatchlistService`, issue #83) but — unlike positions — it is **global**: one shared list, no `owner` column, with the writing principal recorded in `added_by` for audit only. `watchlist.yaml` is now purely an import format (`scripts/import_watchlist.py`, full-sync replace); every consumer (the daily report, the options screener, the fundamentals report, the REST tier) reads the table, and there is deliberately **no fallback to the YAML file** — an empty table is a loud Discord alarm (`alert_if_watchlist_empty` in `main.py`), not a quiet degrade. Surfaced as `GET/POST /api/watchlist`, `PATCH /api/watchlist/{ticker}` (tags only — it **replaces** the set rather than merging, because the UI sends the chip set it is displaying and a merge could never remove the last tag; PATCH not PUT because the currency is resolved server-side and must not be client-writable), `DELETE /api/watchlist/{ticker}`, `GET /api/watchlist/mine` (the same rows plus the caller's `held`/`quantity`/`avg_price`, from `WatchlistService.owner_view` in **seven queries, zero network** — the six plus one `list_positions(owner)`; issue #224, it replaced `GET /api/securities`), and `GET /api/watchlist/fundamentals` (returns + cached fundamentals for the whole list — `WatchlistService.returns_and_fundamentals` composes `PricesService` and `FundamentalsService` to serve it in **six queries and zero network calls**, which is what let the nightly HTML report become a page; the query count is constant in list size and `tests/test_watchlist_service.py` guards it, so do not "simplify" it into a per-symbol loop). Both routes must stay **declared before** `/watchlist/{ticker}` in `api/routers/portfolio.py` — FastAPI matches in declaration order. Plus `list_watchlist` / `add_to_watchlist` on the portfolio MCP wrapper. Removal is a UI-only action: the seam `mcp_gateway/rest_client.py` has no `delete` verb, so no agent can drop symbols off a list the whole team shares.

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
