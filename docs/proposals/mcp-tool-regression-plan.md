# Plan: regression tests for every MCP tool (issue #44)

## Context

Issue #44 asks for regression tests that exercise every tool in every MCP server without an AI
driving them, in a form CI can run. Before this, the wrappers were covered in two ways. CI's
`ci_wrapper_smoke.py` boots each one and counts its tools. `tests/test_mcp_seam.py` tests the
shared seam, `mcp_gateway/rest_client.py`. Neither checks what any **individual** tool sends. A
tool could pass the wrong path, drop a parameter, or change a default, and nothing would fail until
an AI client got a wrong answer.

The work has three parts:

1. **Offline contract test, one per tool ([#329](https://github.com/JohnFunkCode/StockPortfolioManager/pull/329)).** Every tool runs through fastmcp's in-memory
   `Client`, with the REST seam stubbed.
2. **Completeness guard ([#329](https://github.com/JohnFunkCode/StockPortfolioManager/pull/329)).** A tool with no contract case fails the build.
3. **Opt-in live smoke against test (never prod).** `scripts/mcp_live_smoke.py`, a separate PR ([#330](https://github.com/JohnFunkCode/StockPortfolioManager/pull/330)),
   because it needs a test JWT and the deployed URLs.

## Design

`tests/test_mcp_tool_contracts.py`, with its cases in `scripts/mcp_tool_cases.py`:

- **`CASES`** maps `(module, tool)` to a list of `(args, expected call)` entries. An expected call
  holds the method, path, query and JSON body. A health check maps to `None`, meaning no REST call.
  Each variant covers a parameter the tool sends only some of the time: `anchor_date`, `sector`,
  `kinds`, `references`, list-valued `expirations`/`strikes`, and so on.
- **The stub** patches `rest_client.httpx.Client`. The patch returns a real `httpx.Client` on an
  `httpx.MockTransport`, so the seam runs unchanged: param dropping, list expansion, `_path`
  validation and error mapping all execute as they do in production.
- **Assertions:**
  - Each tool makes exactly one REST call (zero for a health check).
  - The call's method, path, query and body match the case.
  - The method and path match a route in `docs/openapi-surface.txt`.
  - The response passes through unchanged. Two exceptions: `get_symbol_lots` and
    `get_portfolio_summary` transform the response by design, and `POST_PROCESSED` pins what
    their transforms return.
  - A REST error reaches the client as a tool error that carries the status and the payload.
  - A path-reshaping symbol never reaches the network.
- **The completeness guard** compares each module's `list_tools()` with `CASES` and fails in both
  directions: on a tool with no case and on a case whose tool no longer exists. The modules come
  from `WRAPPERS` in `scripts/ci_wrapper_smoke.py`, so a new wrapper is covered as soon as CI boots
  it. The guard also checks that the total is 62, the documented count.

No database, no network and no `api.main` import. The whole module runs in about 0.2 s.

### Part 3: the live smoke

`scripts/mcp_live_smoke.py` reuses `CASES`. For each tool it takes the **first** case and runs it
against the deployed test wrapper only if that case's call is a `GET`, a wrapper-local health
check, or a POST listed in `READ_ONLY_POSTS` in `scripts/mcp_tool_cases.py`. Everything else is
printed as `[skip]`, so a new write tool is skipped by default rather than run by accident. Today
that selects 60 calls and skips 2: `add_to_watchlist` and `collect_news`.

`READ_ONLY_POSTS` holds the two POST calculators, `price_vertical_spread` and
`get_fundamental_scores_batch`. The first cut selected by method alone and skipped them, which the
PR review flagged: the smoke claimed every read-only tool and missed two. They change no user
data. The batch scorer writes the fundamentals cache the GET scorer also writes, and the spread
pricer's live fetch stores an options snapshot the way the GET contract lookup does. A test fails
if an entry names a case that doesn't exist or isn't a POST, so a rename can't silently drop one.

- **Targets** come from the `test` environment and each wrapper's `SERVER_MODULE` in
  `deploy/cloudrun-services.toml`. `--env prod` is refused, and so is a test environment or URL
  that carries prod's project number.
- **Token:** `QUANTCORE_TEST_MCP_TOKEN`, a test JWT. Deliberately not `QUANTCORE_MCP_TOKEN`, which
  holds the prod JWT AI clients use; one variable for both would make pointing it at the wrong
  project a matter of which token happened to be exported.
- **Output** is metadata only: `[ok]`/`[FAIL]`, wrapper, tool, the HTTP status parsed from
  `REST tier returned NNN`, and timing. Never a result, an error payload, or the token.
- Wrappers run concurrently, the tools within a wrapper in sequence over one session.
- `tests/test_mcp_live_smoke.py` checks the refusals, the selection, and the output offline by
  driving the in-memory wrappers through the contract test's stubbed REST seam.

## Checkpoint log

| Step | Commit | Result |
|---|---|---|
| Parts 1+2: contract test + completeness guard | [#329](https://github.com/JohnFunkCode/StockPortfolioManager/pull/329) | 9 tests, 62 tools / 70 cases, ~0.2 s. A mutation check confirmed each failure is caught and named by module, tool and args: a changed default (`get_rsi` period 14→21), a deleted case (both guards fire, `61 != 62`), and a wrong path. |
| Part 3: opt-in live smoke against test | [#330](https://github.com/JohnFunkCode/StockPortfolioManager/pull/330) | 10 offline tests. Live on test (2026-10-05): 58 calls, 4 skipped. First run with `--sub live-smoke`: 53/58; the 3 portfolio tools 403'd (gotcha 8), `get_news` 504'd once at 60.9 s and passed in 0.2 s on the rerun, and `analyze_options_watchlist` 504'd. Rerun with `--sub john`: all portfolio tools ok; `analyze_options_watchlist` 504'd again at 60.4 s, so it reproduces (gotcha 9). |
| Part 3 review fix: run the two read-only POSTs | [#330](https://github.com/JohnFunkCode/StockPortfolioManager/pull/330) | `READ_ONLY_POSTS` allowlist + 2 offline tests (12 in the module). Live on test: `get_fundamental_scores_batch` ok in 23.7 s, `price_vertical_spread` ok in 17.9 s. Now 60 calls, 2 skipped. |
| Spread case expiration moved out | [#330](https://github.com/JohnFunkCode/StockPortfolioManager/pull/330) | `price_vertical_spread` first case `2026-11-20` → `2029-01-19` (gotcha 10). 21 offline tests pass. Live on test: ok in 2.4 s, and the result is a real priced spread (`liquidity: thin`; the LEAPS bid/ask makes the natural debit 12.00 against a 10-wide spread, mid 7.72). That is enough for a smoke, which checks that the call works, not that the trade is good. |
| #331: concurrent watchlist fetch | [#337](https://github.com/JohnFunkCode/StockPortfolioManager/pull/337) | Local, test DB through the proxy, 229 entries / 217 scanned / 216 fetched: sequential 1361 s; concurrent (8 workers) 293–574 s run to run. A 12-symbol profile (market open) put ~5.8 s on each symbol, mostly DB round trips through the local proxy (~0.37 s each: `store_bars`, `get_bars`, `has_open_bar`, `count_cached`), with Yahoo ~1.8 s. So local timing says little about Cloud Run, where the database is close; the 60 s answer is the live smoke on test after merge (or a #120 dispatch). Crumb 401s: 18 → 7 with the warm-up, and the calendar retry recovered 4 of the 6 empty calendars it retried (the other 2 are ETFs, QQQ/VOO, correctly empty) (gotcha 11). |
| #337 review | [#337](https://github.com/JohnFunkCode/StockPortfolioManager/pull/337) | Cache cleanup moved into each fetch's `finally`: peewee keeps connection state thread-local (checked: a close on one thread leaves another's open), so the caller's single close after the pool missed every worker. Tests now assert the closes happen on the fetching threads, on success and on a raising fetch. `_run_analysis` split into `_fetch_one`/`_fetch_all`/`_top_by`/`_build_put_trades` (radon CC 13 → 5, complexipy 10 → 5); `fetch_earnings_proximity` into `_fetch_calendar`/`_calendar_dates`/`_as_date`/`_days_until_next` (CC 17 → 3, cognitive 24 → 2). 90 tests pass across the options suites. |
| #338 | [#338](https://github.com/JohnFunkCode/StockPortfolioManager/issues/338) | The same thread-local cleanup bug in `OptionsService`: `refresh_options_snapshots` closed the caches once on the caller after its pool (its comment claimed it closed each worker's), and `get_options_flow_signals`' two-task pool never closed them at all. Both now close in a per-task `finally` on the worker. The refresh run's outer close is gone, since the caller never touches yfinance. New tests assert the closes land on the fetching threads and never on the caller, including when a fetch raises; they fail against the old code. |
| #339 review | [#339](https://github.com/JohnFunkCode/StockPortfolioManager/pull/339) | Review flagged `refresh_options_snapshots` over the complexity limits (cyclomatic 16 / 15, cognitive 33 / 25). Split into `_refresh_symbols` (source selection, module-level), `_refresh_in_batches` (one executor, a `batch_delay` pause between batches) and `_refresh_one` (one retry after `REFRESH_RETRY_PAUSE_SECONDS`, worker-side cache close in `finally`). Now 4 / 3; the helpers are 8 / 12, 6 / 8 and 3 / 2 (radon / complexipy). Behaviour unchanged; new tests pin the retry that succeeds, that the reported error is the retry's, the pause count between batches, the empty selection, and each source's selection. Gotcha: deduping the selection with `dict.fromkeys` looks equivalent but is not: only `source="all"` dedupes, and only the watchlist against the portfolio, so a symbol repeated within one list is still fetched (and counted in `total`) twice. |
| Cache-first watchlist screen (gotcha 9) | [#342](https://github.com/JohnFunkCode/StockPortfolioManager/pull/342) | `analyze_watchlist` reads only the database by default. Local run against test through the proxy, with yfinance booby-trapped so any Yahoo call fails: 229 entries, 6.72 s cold and 3.64 s warm, with 200 fetched, 0 failed and 17 stale (29 with `include_non_us`). That clears the 10 s gate the plan set so a ~16 s cold start still fits inside 60 s, so the wrapper timeout is unchanged. The stale symbols are non-US listings and OTC ADRs, plus DOMO, for which the 17:00 capture has no full chain. They are expected to stay stale. `EXPLAIN ANALYZE` of the latest-full-snapshot query: a seq scan and sort of `options_snapshots` (14,965 rows, 201 kept), 70 ms. No index is needed yet, but the cost grows linearly with capture history. The live path's `expirations` and nearest-chain fetches are deduped (`_prefetch_chain`), which halves `analyze_options_symbol`'s Yahoo calls. Gotcha: on this laptop Postgres.app rejected the Claude app's connection (`FATAL: Postgres.app rejected …`) even unsandboxed, because Postgres.app gates trust auth per client app. The DB tests ran on Cloud SQL test (`QUANTCORE_UNITTEST_DB=cloudsql`) instead. |
| Cache-first screen verified on test | — | After #342 deployed to test (deploy run 37552882170), `scripts/mcp_live_smoke.py --env test --tool analyze_options_watchlist` answered in **7.5 s** end to end through the wrapper, compared with a 504 at 60 s and a ~194 s server time before. The `quantcore-api` logs around the request (200 at 01:00:25Z, 2026-10-07) show no yfinance requests and no `Invalid Crumb`. Gotcha: the smoke script must be run as `PYTHONPATH=. python scripts/mcp_live_smoke.py` (or `python -m scripts.mcp_live_smoke`) — plain `python scripts/mcp_live_smoke.py` fails with `No module named 'scripts'`. It also needs `QUANTCORE_TEST_MCP_TOKEN`, a TEST JWT (`mint_prod_jwt.py --project quantcore-test-20260606`), not the prod `QUANTCORE_MCP_TOKEN`. |

## Gotchas

1. **A `/` in a symbol is allowed, by design (#297).** `_path` splits the path on `/` and
   checks each segment against an allowlist, rejecting `.` and `..`. So `a/b` is two valid
   segments and *does* reach the REST tier. The rejection test therefore uses `../portfolio`.
   Don't "fix" the test, or the seam, to reject `/`.
2. **fastmcp logs a full rich traceback for every tool error**, including the expected errors in
   this test. `TestToolErrorShape.setUp` calls `logging.disable(logging.CRITICAL)` and restores it
   with `addCleanup`. Without that, a green run prints pages of tracebacks that look like failures.
3. **Don't yield from a generator inside `subTest`.** The first version looped over a generator of
   rows and opened a `subTest` around each. When an assertion failed, the run reported
   `generator ignored GeneratorExit` ERRORs on top of the real FAIL. The rows are now built into a
   list first, and `_sub(row)` opens the `subTest`.
4. **Query values arrive as strings.** By the time httpx has encoded the URL, `True` is `"true"`
   and `1000.0` is `"1000.0"`. The cases are written in that form, and `_expected_items` sorts the
   multi-items so that a repeated key like `expirations` compares in a fixed order.
5. **The route check reads `docs/openapi-surface.txt` instead of importing `api.main`.** Importing
   the app builds it, which calls `ensure_schema()` and so needs a database. The surface file is
   what CI's OpenAPI diff already keeps current. `{param}` segments become `[^/]+`.
6. **Some tool parameters are never forwarded on purpose.** Examples are
   `analyze_options_watchlist`'s `watchlist_path` and `get_option_contracts`'s
   `max_snapshot_age_minutes`/`allow_live_fetch`. Each has a variant case that passes the
   parameter and expects it to be absent from the request, which pins the current behaviour.
7. **The cases live in `scripts/`, not in the test module.** The live smoke needs `CASES`, and
   importing anything under `tests.` runs `tests/__init__.py`, which swaps the DSN to the test
   database and takes the suite's advisory lock (#248). A script must not do either, so `CASES`
   moved to `scripts/mcp_tool_cases.py` and both consumers import it from there.
8. **The test JWT's `--sub` must be a provisioned owner.** `require_owner` answers 403
   `not_provisioned` for an unmapped subject, so a token minted as `--sub live-smoke` fails
   `get_portfolio`, `get_symbol_lots` and `get_portfolio_summary` while every other tool passes.
   That is the isolation working, not a wrapper fault. Mint with `--sub john` (or another
   provisioned owner).
9. **`analyze_options_watchlist` 504s on test at ~60 s, reproducibly.** 60 s is
   `rest_client.DEFAULT_TIMEOUT` (`QUANTCORE_REST_TIMEOUT`), and the 504 is the wrapper's own:
   `rest_client` maps an `httpx.TimeoutException` to a 504. The REST tier takes longer than that
   to analyze the whole watchlist, so the wrapper gives up first. It was a real finding, not a
   smoke defect. **#331 did not fix it.** Fetching `SCREEN_MAX_WORKERS` (8) symbols at a time
   (after one warm-up fetch, gotcha 11, each fetch closing its own thread's yfinance caches in a
   `finally`, which #338 applied to the other two pools) still left the live screen at ~194 s on
   test, and the extra concurrency produced `Invalid Crumb` 401s. **The fix is that the screen no
   longer calls Yahoo:** `analyze_watchlist` defaults to `source="cache"` and scores from the daily
   Job's 17:00 ET full-chain capture, cached daily bars, the cached earnings calendar and the news
   Job's sentiment, in about five set-based queries for the whole roster (3.6–6.7 s for 229
   entries, see the checkpoint log). A symbol without a capture from the last trading day is
   listed under `stale` instead of being fetched. **Don't add a per-symbol live fallback**, since
   that is the path that timed out. The live path survives only as `?source=live` on the REST
   route, for an operator. The MCP tool has no `source`. The timeout that applies is still the
   wrapper's 60 s (`quantcore-api` allows 300 s, the wrappers 900 s), and it was deliberately not
   raised. A single `get_news` 504 at 60.9 s was transient; rerun one tool with `--tool` before
   chasing a failure.
10. **`price_vertical_spread`'s case carries a fixed expiration, now `2029-01-19`.** The live
    smoke sends the first case's arguments verbatim, so after that date it asks for an expired
    contract. It was `2026-11-20` and was moved to BRK-B's longest-dated listed expiration (a
    January LEAPS, checked on 2026-10-05 to list both the 400 and 410 call strikes). Move it forward again in
    `scripts/mcp_tool_cases.py` (args and expected body) before then. Only the first case
    matters here: the `2026-11-20` dates in other tools' later variants are offline-only, and the
    offline contract test doesn't care which date it is.
11. **Concurrent yfinance calls flip each other's cookie strategy (#331).** yfinance 1.2.1 shares
    one cookie/crumb across threads, and on any response ≥400 `YfData._make_request` switches the
    strategy (basic ↔ csrf), clearing the crumb, and retries. Eight workers that all start without
    a crumb keep switching it under each other, which logs bursts of `401 Invalid Crumb`. Two
    fixes, both in `options_screening.py`: the first symbol is fetched alone on the calling thread
    so the crumb is settled before the pool starts, and an empty calendar is retried once after
    `CALENDAR_RETRY_PAUSE_SECONDS`. The retry matters more than it looks: yfinance's quote
    fetch swallows the 401 (`hide_exceptions=True`) and hands back `{}`, so a lost crumb reads as
    "no earnings date" and **silently disarms the earnings-blackout guardrail**. ETFs answer 404
    "No fundamentals data" and stay empty after the retry, which is correct. Expect a few 401 lines
    per run even so, and wide run-to-run timing variance (293–574 s locally for the same list).
