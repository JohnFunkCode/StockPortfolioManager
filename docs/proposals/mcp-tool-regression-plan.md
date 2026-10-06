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
   smoke defect. **Fixed in #331:** `OptionsScreeningService._run_analysis` used to fetch one
   symbol after another; it now fetches `SCREEN_MAX_WORKERS` (8) at a time on one
   `ThreadPoolExecutor`, keeping watchlist order, and closes yfinance's per-thread caches in a
   `finally`. The timeout that applies is still the wrapper's 60 s (`quantcore-api` allows 300 s,
   the wrappers 900 s); it was deliberately not raised, so the screen has to fit inside it. If the
   watchlist grows until it doesn't, raise the worker count only with Yahoo's rate limits in mind
   (`refresh_options_snapshots` runs 4). A single
   `get_news` 504 at 60.9 s was transient; rerun one tool with `--tool` before chasing a failure.
10. **`price_vertical_spread`'s case carries a fixed expiration, now `2029-01-19`.** The live
    smoke sends the first case's arguments verbatim, so after that date it asks for an expired
    contract. It was `2026-11-20` and was moved to BRK-B's longest-dated listed expiration (a
    January LEAPS, checked on 2026-10-05 to list both the 400 and 410 call strikes). Move it forward again in
    `scripts/mcp_tool_cases.py` (args and expected body) before then. Only the first case
    matters here: the `2026-11-20` dates in other tools' later variants are offline-only, and the
    offline contract test doesn't care which date it is.
