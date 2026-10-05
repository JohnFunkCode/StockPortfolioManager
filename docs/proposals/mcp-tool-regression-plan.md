# Plan: regression tests for every MCP tool (issue #44)

## Context

Issue #44 asks for regression tests that exercise every tool in every MCP server without an AI
driving them, in a form CI can run. Before this, the wrappers were covered in two ways. CI's
`ci_wrapper_smoke.py` boots each one and counts its tools. `tests/test_mcp_seam.py` tests the
shared seam, `mcp_gateway/rest_client.py`. Neither checks what any **individual** tool sends. A
tool could pass the wrong path, drop a parameter, or change a default, and nothing would fail until
an AI client got a wrong answer.

The work has three parts:

1. **Offline contract test, one per tool (this PR).** Every tool runs through fastmcp's in-memory
   `Client`, with the REST seam stubbed.
2. **Completeness guard (this PR).** A tool with no contract case fails the build.
3. **Opt-in live smoke against test (later; never prod).** This part needs a test JWT and a target
   URL, so it is a separate PR.

## Design

`tests/test_mcp_tool_contracts.py`:

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

## Checkpoint log

| Step | Commit | Result |
|---|---|---|
| Parts 1+2: contract test + completeness guard | (this PR) | 9 tests, 62 tools / 70 cases, ~0.2 s. A mutation check confirmed each failure is caught and named by module, tool and args: a changed default (`get_rsi` period 14→21), a deleted case (both guards fire, `61 != 62`), and a wrong path. |
| Part 3: opt-in live smoke against test | — | not started |

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
