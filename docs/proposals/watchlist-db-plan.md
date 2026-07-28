# Watchlist persistence — move `watchlist.yaml` into the database

**Source issue:** [#83](https://github.com/JohnFunkCode/StockPortfolioManager/issues/83)
**Status:** PLANNED — no code written
**Shape:** one branch `feat/watchlist-db`, one commit per step, **one PR**
**Related:** #126 / [`portfolio-lots-plan.md`](portfolio-lots-plan.md) (owner scoping + principal plumbing this plan reuses, COMPLETE)

---

## The bug

`POST /api/watchlist` ([`api/routers/portfolio.py:246`](../../api/routers/portfolio.py)) appends a
YAML block to `PROJECT_ROOT / "watchlist.yaml"` — a file on the Cloud Run container
filesystem, which is in-memory and per-instance, and is reset to the image's copy on
every deploy.

The failure mode is worse than an error: the write often **succeeds**, so the symbol
appears, is invisible to any read served by another instance, and vanishes on
scale-to-zero or the next deploy. A hard read-only-filesystem error would be the better
outcome.

---

## How to use this plan

Numbered steps, executed **one at a time, in order**, on a single branch. **Each step is one
commit**; the four stages are review and context boundaries, not deploy boundaries. There
is one PR at the end. Line numbers were captured on 2026-07-28 against branch
`docs/portfolio-lots-checkpoint-log`; if one has drifted, search for the quoted code.

**Rules for the executing agent:**

1. **Do not make design decisions.** The open questions are answered in *Decisions* below.
   If a step seems to need a decision that isn't written down, stop and ask.
2. **Do not commit without explicit authorization, and do not open the PR without it
   either.** "Open a PR" is not implicit commit authorization. `main` requires review +
   approval and cannot be self-merged.
3. **Verify each step before committing it** — the step's own Verify block, not just "it
   imports". A commit that doesn't stand on its own defeats the point of committing per
   step.
4. **Stop and report at the end of each stage.** Stages 1 and 2 are the two that matter
   most (see *Stage 2 checkpoint* below).
5. **Compact context between stages.** The last commit is the saved state.
6. Run the full backend suite before declaring any stage complete:
   ```bash
   python -m unittest discover -s tests -t .
   ```

### Why one PR (recorded 2026-07-28)

Considered against four staged PRs and chosen deliberately. The deciding factor: stages 1
and 2 cannot be separated safely. Merging stage 1 alone auto-deploys test with the REST
tier DB-backed while `main.py` still reads the YAML — a symbol added through the UI appears
in the UI and never reaches the daily report or the options-capture universe. Secondary
reasons: one migration-and-seed rollout to coordinate instead of four, and the
`load_watchlist()` row-shape contract (repository → `deps` → `/api/securities` → frontend)
is verifiable in a single diff.

The accepted cost: a live bug stays live longer, and ~1,400 lines is a large review
surface. Every one of the four staged portfolio-lots PRs needed a follow-up `fix:` commit
after review, so review granularity has demonstrable value in this codebase. Two
mitigations, both binding on the executing agent:

- **Commit hygiene is the substitute for PR granularity.** The repo merges with merge
  commits, not squash, so these commits survive on `main` for bisect and selective revert.
  One step per commit, message naming the step, no drive-by changes.
- **The branch can be cut short at commit 2.4.** Stages 1–2 are a complete, coherent,
  shippable fix for #83 on their own. If review drags or the bug needs to ship, open the PR
  at that point and move stages 3–4 to a follow-up. Nothing in stages 3–4 changes anything
  stages 1–2 established.

---

## Decisions

Answered by the repo owner on 2026-07-28, before any code was written.

| # | Question | Decision | Consequence |
|---|----------|----------|-------------|
| 1 | Per-owner or global watchlist? | **Global** — one shared list, no `owner` column | Fixes persistence without inventing a second ownership model. The "watchlist is global while the portfolio is per-user" inconsistency #83 raises is **accepted for now** and recorded as a future step, not built. |
| 2 | What do the server-side jobs iterate? | The single global list | With one list, "union of all owners" is automatic — the daily report, the options-chain capture loop, sentiment and the screener all read the same rows. |
| 3 | Scope | Backend + frontend delete UI + repoint every YAML consumer + MCP tools | Four stages (below), one PR. |
| 4 | MCP surface | **Read + add** (`list_watchlist`, `add_to_watchlist`) | Uses the existing `get`/`post` verbs on `mcp_gateway/rest_client.py`. **No `delete` verb is added** — the #126 Q19 no-delete stance on the shared seam stands. |
| 5 | Auth on writes | `require_principal` on `POST`/`DELETE` | Not `require_owner`: the list is global, so an authenticated-but-unmapped identity should not 403. `GET` stays open, as today. The principal is recorded in an `added_by` audit column. |
| 6 | Does `watchlist.yaml` stay in the repo? | **Yes**, demoted to an import format | Exactly the role `portfolio.csv` plays since Phase 1 Step 6: full-sync/replace via a `scripts/import_watchlist.py` companion. |
| 7 | Fallback to the YAML when the table is empty? | **No** — no silent fallback | A silent fallback would re-hide the very failure mode this issue is about. Instead, an empty watchlist is a **loud** condition (Decision 8). |
| 8 | What if the table comes up empty in prod? | Alert, don't degrade quietly | The daily job logs an error and fires a Discord notification if the watchlist reads back empty while symbols were expected. Layered mitigation plus a loud last-resort alert — the "bilge pump" principle. |
| 9 | One PR or staged PRs? | **One PR**, one commit per step | Rationale and mitigations above. |

---

## Global guardrails

- **Every schema change is a Flyway-style file under `db/migrations/` AND is mirrored into
  `quantcore/db.py init_schema()`.** Both, always — `init_schema()` is what runs on
  startup; the migration file is the record. Latest is `V4__portfolio_lots.sql`, so this
  plan's file is `V5__watchlist.sql`.
- **Arch-v2 layering** (`architectural-standard-v2.md`): gateways → repositories (SQL only,
  no analytics) → analytics (pure, no I/O) → services → adapters (routers/MCP, exactly one
  service call deep). Services never import each other or the registry.
- **Preserve the row shape exactly.** `load_watchlist()` emits
  `{name, symbol, currency, purchase_price, quantity, purchase_date, sale_price, sale_date,
  source: "watchlist", tags}`. The frontend, `PricesService.screen_securities`,
  `SentimentService` and `GET /api/securities` all depend on it. Every key stays, including
  the `None` placeholders.
- **No `::` casts in parameterized SQL.** [`quantcore/db.py:445`](../../quantcore/db.py) `_adapt_sql`
  rewrites `:(\w+)` → `%(\1)s` whenever params is a dict, so `foo::date` becomes
  `foo:%(date)s`. Casts are fine in the DDL (which executes with `params=None`); they are a
  bug in repository queries.
- **Adding, renaming, or removing any REST route fails CI** until the snapshot is
  regenerated and committed — do it in the step that adds the route, not at the end:
  ```bash
  PYTHONPATH=. python scripts/check_openapi_snapshot.py --update
  ```
- **BYOK never-log policy applies.** No tokens, `Authorization` headers, or request bodies
  in logs. A principal's email in an `added_by` column or a security event is *not* a
  credential — `owner_identities` already stores emails.

---

## Current-state reference

| Concern | Where | Today's behaviour |
|---------|-------|-------------------|
| Write path | `api/routers/portfolio.py:246-273` | `open(wl_path, "a")` — appends YAML to the container filesystem |
| Read path | `api/deps.py:53-76` | `yaml.safe_load` of `watchlist.yaml`, returns `[]` if missing |
| Delete path | — | **Does not exist** |
| Auth | `api/routers/portfolio.py:241,247` | No dependency at all on either route |
| Combined view | `api/routers/portfolio.py:279` | `GET /api/securities` merges portfolio + watchlist, sets `source: "both"` |
| Daily report | `main.py:609-617` | `WatchList().read_stocks_from_yaml(script_dir / "watchlist.yaml")` |
| Capture universe | `main.py:643-660` | portfolio + watchlist symbols, then every *other* owner's positions (#126 decision 5) |
| Options screener | `quantcore/services/options_screening.py:1155-1165` | `analyze_watchlist(watchlist_path=None)` reads `_PROJECT_ROOT / "watchlist.yaml"` directly |
| Sentiment / screener | `api/routers/sentiment.py:32`, `api/routers/prices.py:262` | Both go through `deps.load_watchlist()` — repointing `deps` fixes them for free |
| Fundamentals report | `scripts/generate_watchlist_fundamentals_report.py:128,397` | Own `load_watchlist(path)` + `--watchlist` arg |
| Frontend add | `frontend/src/api/securities.ts:157`, `hooks/useSecurities.ts:177` | `addToWatchlist`; `removeFromPortfolio` exists but has no watchlist twin |
| MCP seam verbs | `mcp_gateway/rest_client.py:126,140` | `get` and `post` only — no `delete` |
| MCP wrappers | `scripts/ci_wrapper_smoke.py:25-31` | six wrappers; `portfolio` has a floor of 4 tools |
| Data | `watchlist.yaml` | 227 entries with `name`/`symbol`/`currency`/optional `tags` |

---

# Stage 1 — Schema, repository, service, routes

Commits 1.0–1.7. The stage that actually closes the bug.

### Step 1.0 — Carry-over fix from PR #156 review

**Files:** `tests/test_schema_bootstrap.py`.

**Do:** `test_create_app_does_not_re_run_the_ddl` asserts
`assertLessEqual(init.call_count, 1)`, which also passes at 0 — removing the
`ensure_schema()` call from `create_app()` entirely would keep it green. `setUp` clears
`_schema_ready_dsns`, so the count is deterministic and the inequality buys nothing. Change
it to `assertEqual(init.call_count, 1)`.

**Don't:** touch anything else from the #156 review in this branch. The two stray
`init_schema()` callers (`main.py:591`, `fastMCPTest/options_analysis.py:442`) are
functionally harmless and unrelated to the watchlist — leave them.

**Verify:** `python -m unittest tests.test_schema_bootstrap`

**Commit when:** the tightened assertion passes, and deleting the `ensure_schema()` call
from `create_app()` makes it fail (check, then restore).

### Step 1.1 — `db/migrations/V5__watchlist.sql`

**Files:** new `db/migrations/V5__watchlist.sql`; `quantcore/db.py` (mirror into `_SCHEMA`,
after the `lot_sales` block at `:141`).

**Do:** create the table. Global list → `symbol_id` is `UNIQUE`, which is what makes the
409-on-duplicate behaviour a database invariant rather than a race:

```sql
CREATE TABLE IF NOT EXISTS watchlist (
    watchlist_id SERIAL PRIMARY KEY,
    symbol_id    INTEGER NOT NULL UNIQUE REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    name         TEXT,
    currency     TEXT NOT NULL DEFAULT 'USD',
    tags         TEXT[] NOT NULL DEFAULT '{}',
    added_by     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Don't:** add an `owner` column "for later" (Decision 1 — speculative), and don't store
tags as a comma-joined string. `psycopg2` adapts a Python `list` to `TEXT[]` and back
natively, with no import and no parsing. Note this is the schema's first array column —
flag it in the PR description.

**Constraints on the `_SCHEMA` mirror, post-#156** (`fix: stop schema DDL from deadlocking
concurrent writers`, merged 2026-07-28):

- The DDL now runs in **autocommit, one statement at a time**, so each statement must be
  individually idempotent and convergent on re-run. `CREATE TABLE IF NOT EXISTS` is.
- [`_split_schema`](../../quantcore/db.py) splits on a bare `;`, so no semicolon may appear
  inside a string literal or default expression. `DEFAULT '{}'` is safe.
- `tests/test_schema_bootstrap.py::test_ddl_executes_every_statement` compares against
  `_split_schema(_SCHEMA)` rather than a hard-coded count, so adding this table needs **no
  test change**. Do not add one.

**Verify:** `flyway migrate` against the test DB, then `\d watchlist`.

**Commit when:** the table exists in the test DB and `init_schema()` creates it in a fresh
empty database.

### Step 1.2 — `WatchlistRepository`

**Files:** new `quantcore/repositories/watchlist_repository.py`.

**Do:** SQL only, mirroring `PortfolioRepository`'s shape — module-level `SQL_*` constants,
`_resolve_symbol_id(conn, ticker)` (upsert into `symbols` then select), `_row_to_dict(row)`
emitting the exact shape in the guardrails above. Methods:
`list_entries()`, `add_entry(symbol, name, currency, tags, added_by)` returning the new id
(or `None` on `ON CONFLICT (symbol_id) DO NOTHING`), `remove_entry(symbol)` returning a
rowcount, `replace_all(rows)` — single-transaction `DELETE` + insert for the importer,
`count()`.

**Don't:** put the "already in the watchlist" 409 decision here; the repository reports
what SQL did, the service decides what it means.

**Verify:** `python -m unittest tests.test_watchlist_repository` (written in this commit,
not deferred to 1.7 — the repository is the riskiest code in the branch).

**Commit when:** round-trip add → list → remove works against the test DB, tags included.

### Step 1.3 — `WatchlistService`

**Files:** new `quantcore/services/watchlist.py`.

**Do:** constructor-inject the repository. `list_entries()`; `add_entry(...)` normalizing
symbol to upper / currency to upper / name defaulting to the symbol / tags stripped of
blanks, raising `DuplicateSymbolError` when the insert conflicts (import it from
`quantcore.services.portfolio`, don't define a second one) and `ValueError` on an empty
symbol; `remove_entry(symbol)` returning the rowcount; `import_yaml(path)` parsing the
YAML and calling `replace_all` (full-sync/replace, matching `PortfolioService.import_csv`).

**Don't:** import another service or the registry.

**Verify:** `python -m unittest tests.test_watchlist_service`

**Commit when:** duplicate add raises, empty symbol raises, `import_yaml` is idempotent on
re-run.

### Step 1.4 — Registry wiring

**Files:** [`quantcore/services/registry.py:60-92`](../../quantcore/services/registry.py)
(dataclass fields), `:94-252` (builder).

**Do:** add `watchlist_repository: WatchlistRepository` and `watchlist: WatchlistService`,
constructed and passed like `portfolio_repository` / `portfolio`.

**Verify:** `python -c "from quantcore.services.registry import get_services; print(get_services().watchlist)"`

**Commit when:** the frozen `Services` dataclass exposes both.

### Step 1.5 — REST routes

**Files:** `api/deps.py:53-76`, `api/routers/portfolio.py:241-273`,
`api/schemas/portfolio.py`, `docs/openapi-surface.txt`.

**Do:**
- `deps.load_watchlist()` becomes `return get_services().watchlist.list_entries()`. Delete
  the `yaml` import if nothing else in the module uses it. This one edit fixes
  `GET /api/securities`, the sentiment summary and the screener at the same time.
- `GET /api/watchlist` — unchanged body, now DB-backed.
- `POST /api/watchlist` — `principal: Principal = Depends(require_principal)`; one service
  call; `DuplicateSymbolError` → `route_error_plain(..., 409)` (same message string as
  today), `ValueError` → 400 for the missing symbol. Pass `added_by=principal.owner`.
  **Delete the file-append block entirely**, and drop the now-unused `PROJECT_ROOT` /
  `yaml` imports from the router if no other route uses them.
- `DELETE /api/watchlist/{ticker}` — new; `require_principal`; 404 with
  `f"{ticker} not found in watchlist"` when the rowcount is 0; body
  `{"symbol": ticker, "removed": True}`, mirroring `remove_from_portfolio` at `:95`. Add a
  `RemoveWatchlistResponse` schema (or reuse `RemovePositionResponse` if the shape is
  identical).
- Regenerate the OpenAPI snapshot **in this commit**.

**Don't:** use `require_owner` on these routes (Decision 5). Don't change the `GET` route's
auth.

**Verify:**
```bash
PYTHONPATH=. python scripts/check_openapi_snapshot.py --update && python -m unittest tests.test_api_smoke
```

**Commit when:** add → GET → DELETE → GET round-trips through the API against the test DB,
and the snapshot diff shows exactly one added route.

### Step 1.6 — `scripts/import_watchlist.py`

**Files:** new `scripts/import_watchlist.py` (companion to `scripts/import_portfolio.py`).

**Do:** copy the `import_portfolio.py` skeleton in spirit: `--yaml` (default
`watchlist.yaml`), `--allow-prod` gated by `quantcore.db_safety.assert_not_production()`
(the same guard at `scripts/import_portfolio.py:39-41`), one
`get_services().watchlist.import_yaml(...)` call, print the imported count. Print a
**loud warning** if the resulting count is 0 or drops by more than half versus the current
row count — this script is the only thing standing between prod and an empty capture
universe.

**Verify:**
```bash
python scripts/import_watchlist.py --yaml watchlist.yaml
```

**Commit when:** the test DB holds all 227 entries with tags intact, and a re-run leaves the
count unchanged.

### Step 1.7 — Round out the tests

**Files:** `tests/test_watchlist_repository.py`, `tests/test_watchlist_service.py`
(extend those from 1.2/1.3); `tests/test_api_smoke.py:229-247`.

**Do:** fill the gaps the earlier commits left — route-level cases (duplicate → 409, empty
symbol → 400, delete → 200, delete-missing → 404, `require_principal` rejects an
unauthenticated write), list-shape parity (every legacy key present, `source ==
"watchlist"`), and `GET /api/securities` still marking a symbol in both lists as
`source: "both"`.

**Commit when:** `python -m unittest discover -s tests -t .` is green and coverage has not
regressed below the `.coveragerc` ratchet floor.

**Stage 1 complete when:** the REST tier is entirely DB-backed and nothing in `api/` reads
`watchlist.yaml`. **Do not stop here** — stage 2 is what keeps the report in sync. Stop,
report, compact, continue.

---

# Stage 2 — Repoint the remaining YAML consumers

Commits 2.1–2.4. After stage 1 the REST tier is DB-backed but the daily job and two scripts
still read the file, so a symbol added through the UI never reaches the report or the
options-snapshot capture loop. **Stages 1 and 2 must ship together** — that split-brain
window is the reason this plan is one PR.

### Step 2.1 — The daily report

**Files:** `main.py:609-617`, `portfolio/watch_list.py:85-111`.

**Do:** add `WatchList.read_stocks_from_records(records)` next to `read_stocks_from_yaml`
(which stays — it's what the importer's format documents), mirroring
`Portfolio.read_stocks_from_records` added in Phase 1 Step 6. In `main.py`, build the
watchlist from `get_services().watchlist.list_entries()`.

**Don't:** delete `read_stocks_from_yaml`.

**Commit when:** the report renders the DB list, and `capture_symbols` at `main.py:643` is
built from it.

### Step 2.2 — The empty-watchlist alarm (Decision 8)

**Files:** `main.py` (near the capture loop), `notifier.py`.

**Do:** if `list_entries()` returns empty, log an error and send a Discord notification —
"watchlist is empty; options-chain capture is running on portfolio symbols only". Then
continue: the report and the portfolio-symbol capture still run.

**Don't:** fall back to reading `watchlist.yaml` (Decision 7).

**Commit when:** a test with a stubbed-empty service asserts the alert fires and the run
still completes.

### Step 2.3 — The options screener

**Files:** `quantcore/services/options_screening.py:1155-1165`,
`api/routers/options.py:232-240`, `fastMCPTest/options_analysis.py:87-103`.

**Do:** `analyze_watchlist` takes `entries: list[dict] | None`; when `None`, the **route**
supplies `deps.load_watchlist()` (adapters inject; services don't reach for files). Keep
`watchlist_path` accepted for the CLI path so the standalone script still works, and update
the `watchlist_default` line in the MCP wrapper's health/config output.

**Verify:** `python -m unittest tests.test_options_screening_service tests.test_options_analysis_cli`

**Commit when:** `GET /api/options/screen-watchlist` screens the DB list, with no filesystem
read on that path.

### Step 2.4 — The fundamentals report script

**Files:** `scripts/generate_watchlist_fundamentals_report.py:128,397,413`.

**Do:** default to the service; keep `--watchlist <path>` as an explicit override for
ad-hoc lists.

**Commit when:** running it with no `--watchlist` reports the DB row count.

### Stage 2 checkpoint — the cut-short point

`grep -rn "watchlist.yaml"` outside `scripts/import_watchlist.py`, the importer's tests and
documentation should return only explicit-override call sites.

**Everything through here is a complete, shippable fix for #83.** If review is dragging or
the bug needs to ship, stop and open the PR at this commit; stages 3–4 become a follow-up
branch. Otherwise stop, report, compact, and continue — the decision is the repo owner's,
not the agent's.

---

# Stage 3 — The remove-from-watchlist UI

Commits 3.1–3.2.

### Step 3.1 — API client + hook

**Files:** `frontend/src/api/securities.ts:157-172`, `frontend/src/hooks/useSecurities.ts:164-172`.

**Do:** `removeFromWatchlist(ticker)` (`DELETE /api/watchlist/${ticker}`) and
`useRemoveFromWatchlist()`, both copied from the `removeFromPortfolio` /
`useRemoveFromPortfolio` pair, invalidating the `['securities']` query key.

**Commit when:** the hook is exported and typechecks.

### Step 3.2 — The Securities page action

**Files:** `frontend/src/components/securities/SecuritiesPage.tsx`.

**Do:** a remove action on watchlist rows, matching the existing portfolio remove
affordance (same confirmation pattern, same disabled/pending states). A row whose `source`
is `"both"` must offer the two removals distinguishably — removing it from the watchlist
must not read as removing the position.

**Don't:** invent a new interaction pattern; mirror what the portfolio remove already does.

**Verify:** `cd frontend && npx vitest run --coverage`

**Commit when:** vitest covers loading/error/success plus the `"both"` case, and the
coverage thresholds have not been lowered.

**Stage 3 complete when:** a symbol can be added and removed from the watchlist entirely
through the UI, and the removal survives a reload.

---

# Stage 4 — MCP tools (read + add)

Commits 4.1–4.2.

### Step 4.1 — Tools on the `portfolio-server` wrapper

**Files:** `fastMCPTest/portfolio_server.py`, `scripts/ci_wrapper_smoke.py:30`.

**Do:** add `list_watchlist()` → `rest_client.get("/api/watchlist")` and
`add_to_watchlist(symbol, name=None, currency="USD", tags=None)` →
`rest_client.post("/api/watchlist", ...)`. Update the module docstring: the wrapper is no
longer strictly read-only, and the docstring must say **why** — the watchlist is a shared,
global, low-risk list, and add-only writes need no new seam verb. Raise the smoke floor
from 4 to 6.

**Don't:** add a `delete` verb to `mcp_gateway/rest_client.py`, and don't add a
`remove_from_watchlist` tool (Decision 4). Don't stand up a seventh Cloud Run service —
these tools live on the existing wrapper.

**Verify:** `python scripts/ci_wrapper_smoke.py`

**Commit when:** both tools appear in the wrapper's tool list and CI smoke passes.

### Step 4.2 — Docs

**Files:** `CLAUDE.md`, `readme.md` (whichever documents the MCP tool surface and the
watchlist configuration line).

**Do:** update the `watchlist.yaml` bullet under **Configuration** to say it is an import
format, note the new `DELETE` route, and list the two new tools.

**Commit when:** the docs match the shipped surface.

---

## PR acceptance

- Adding a symbol through the deployed test UI survives a revision deploy — the bug in #83
  is closed.
- No code path outside `scripts/import_watchlist.py` writes `watchlist.yaml`, and no
  non-override path reads it.
- Daily report, capture loop, screener and fundamentals script all run off the database;
  the empty-list alarm is covered by a test.
- Full backend suite green; frontend vitest green with thresholds not lowered; OpenAPI
  snapshot committed; six MCP wrappers boot and the smoke floor holds.
- **The PR description must call out the two user-initiated deploy steps** (migration +
  seed, below) and the new `TEXT[]` column. Merging without those steps run leaves test
  serving an empty watchlist.

---

## Rollout (user-initiated, per database)

Because this is one PR, this happens once for test and once for prod rather than being
spread across four merges.

**Test** — before merging to `main` (the merge auto-deploys test):

1. `flyway migrate` `V5` against the test DB.
2. `python scripts/import_watchlist.py --yaml watchlist.yaml` — verify the printed count.

**Prod** — before dispatching `prod-rollout.yml`:

3. `flyway migrate` `V5` against the prod DB.
4. `python scripts/import_watchlist.py --yaml watchlist.yaml --allow-prod` — one time, to
   seed the 227 entries. **Verify the printed count before continuing.**
5. Dispatch `prod-rollout.yml` with the commit's 7-char SHA.
6. Confirm `GET /api/watchlist` on prod returns 227 entries, then add and delete one symbol
   through the prod UI and confirm it survives.

Order matters in both environments: a revision serving the new code against an unseeded
database reads an empty watchlist, which is precisely the alarm condition Step 2.2 exists
to make loud.

---

## Deferred / explicitly out of scope

- **Per-owner watchlists.** Decision 1. The forward path if it's ever wanted: add
  `owner TEXT` to `watchlist`, swap `require_principal` for `require_owner` on the write
  routes, and make the capture loop iterate the union across owners — the same shape #126
  used for positions. Nothing in this plan blocks it.
- **Editing an entry** (rename, re-tag). No `PATCH /api/watchlist/{ticker}`; remove and
  re-add.
- **`remove_from_watchlist` as an MCP tool.** Decision 4.
- **Tag management UI.** Tags round-trip through the API but are still authored in the
  import YAML.
- **Decoupling `main.py`'s report** — that's #147.

---

## Checkpoint log

| Date | Step | Status | Notes |
|------|------|--------|-------|
| 2026-07-28 | — | Plan written | Decisions 1-8 answered by the repo owner; no code written |
| 2026-07-28 | — | Revised to one PR | Decision 9: four staged PRs → one branch, one commit per step; cut-short point at 2.4 |
| 2026-07-28 | — | Branch cut | `feat/watchlist-db` off `main` @ `969bf2c` (post-#156). Step 1.0 added to carry the PR #156 review fix; Step 1.1 gained the post-#156 `_SCHEMA` constraints |
