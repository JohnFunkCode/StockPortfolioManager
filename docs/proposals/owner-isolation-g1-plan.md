# Owner isolation for every per-owner table (Security G1, #460)

Issue: [#460](https://github.com/JohnFunkCode/StockPortfolioManager/issues/460) · OWASP A01 ·
gap **G1** in [`docs/architecture/security.md`](../architecture/security.md).

## Problem, as the code stands (main @ `c88dff4`)

"Every statement carries an `owner` predicate" is a written rule for Harvester only
([harvester.md](../architecture/harvester.md)); positions follow it by practice. Nothing tells the
author of the *next* per-owner table to do the same, and nothing fails if they don't.

`options_positions` is the live instance of the gap:

- No `owner` column (`quantcore/db.py` `_SCHEMA`, baseline V1, snapshot).
- `OptionsPositionStore` (`quantcore/repositories/options_position_repository.py`) has eight SQL
  statements in seven methods, none with an owner predicate: `add_position`, `close_position`,
  `expire_position`, `auto_expire_past_positions` (SELECT + UPDATE), `get_active_positions`,
  `get_position`, `position_count`. `get_pending_alerts` reads through `get_active_positions`.
- One live reader: `Notifier.check_options_alerts` (`notifier.py`), run by `main.py`'s notification
  pass. It expires and alerts on **every** row and posts to the one shared Discord webhook. The
  notifier already carries `self.owner` (default `"john"`) and passes it to Harvester — the options
  store is the only part of the notifier that ignores it.
- `registry.py` constructs `OptionsPositionStore` as `options_position_repository`, but no service,
  route, MCP tool or Sidekick tool reads it.
- No writer in the application: positions are added by hand/CLI (`add_position`).

Today's per-owner tables (columns from `db/schema_snapshot.json`):

| Table | Scoped by | Repository | Wrong-owner test today |
|---|---|---|---|
| `positions` | `owner` | `PortfolioRepository` | `test_*_is_owner_scoped` (`tests/test_portfolio_repository.py`) |
| `lot_sales` | FK `lot_id → positions` | `PortfolioRepository` | `test_update_sale_notes_is_owner_scoped`, `test_sales_are_owner_scoped` |
| `plan_instances` | `owner` | `HarvesterPlanDB` | `test_wrong_owner_*` (`tests/test_harvester_repository_db.py`) |
| `plan_rungs`, `alerts` | FK `instance_id → plan_instances` | `HarvesterPlanDB` | same |
| `user_settings` | `owner` (PK) | `UserSettingsRepository` | `test_owners_are_isolated` (`tests/test_settings_api.py`, API level only) |
| `owner_identities` | maps principal → owner | `OwnerIdentityRepository` | n/a — it *is* the mapping; exempt |
| `watchlist` | global by design (`added_by` is audit) | `WatchlistRepository` | exempt (CLAUDE.md: one shared list) |
| `options_positions` | **nothing** | `OptionsPositionStore` | **none** |

## Decisions

**D1 — keep `options_positions` and give it an owner (recommended), or drop it.**
Keep, because:

- Adding a column is an *expand* migration: CI applies it automatically on both projects.
  Dropping the table is a *contract* migration (`DROP`), which `db/migrate-entrypoint.sh` refuses;
  it would need a manual `./scripts/flyway.sh --prod migrate` and a forward-only commitment.
- The feature (#12, options ITM/expiry/profit-target alerts) runs daily and costs nothing.

Check before step 1, read-only: `SELECT status, count(*) FROM options_positions GROUP BY status`
on prod and test. If prod has zero rows ever and John doesn't want the feature, switch to
the drop path (step 1′ below). Either way the general rule and guard (step 2) land.

**D2 — the notifier stays single-owner.** `check_options_alerts` reads `owner=self.owner` only.
It must **not** loop over `list_owners()`: the webhook is John's channel, so alerting on another
owner's options there is exactly the cross-user leak this issue is about. Another owner gets options
alerts only when they have their own notification destination (out of scope; same position Harvester
is in).

**D3 — `owner` keeps `DEFAULT 'john'`**, as V2 (`positions`) and V7 (`plan_instances`) did. It is
the backfill for existing rows. Removing the default later is `ALTER … DROP DEFAULT`, which the
refusal scan treats as a contract statement, so it isn't worth it; the guard is instead that every
`INSERT` passes `owner` explicitly (step 1 test + step 2 scan).

## Plan

Two PRs. Each is independently mergeable and leaves main green.

### Step 1 — `options_positions` gets an owner (schema + store + notifier)

Schema (the three files, CI-enforced):

- `db/migrations/V12__options_positions_owner.sql`:
  ```sql
  ALTER TABLE options_positions ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'john';
  CREATE INDEX IF NOT EXISTS idx_options_positions_owner_status
      ON options_positions(owner, status);
  ```
  Header comment in the V7 style: why, and that existing rows are John's via the DEFAULT.
- `_SCHEMA` in `quantcore/db.py`: the same `ALTER TABLE … ADD COLUMN IF NOT EXISTS` **after** the
  `CREATE TABLE options_positions` (the V2 pattern at line ~109 — `CREATE TABLE IF NOT EXISTS`
  won't add a column to an existing table), then the index. The column must exist before the index.
- `db/schema_snapshot.json`: `python scripts/check_schema_snapshot.py --update` against a scratch DB.

Repository (`OptionsPositionStore`):

- `owner` is a **required keyword-only** argument on every public method (`add_position`,
  `close_position`, `expire_position`, `auto_expire_past_positions`, `get_active_positions`,
  `get_position`, `get_pending_alerts`, `position_count`) — never defaulted, matching Harvester.
- Every statement carries `owner = %s`, including the `UPDATE … WHERE position_id = ANY(%s)` in
  `auto_expire_past_positions` and the by-id `close`/`expire`/`get`. Another owner's id reads as
  `None` / changes nothing.
- While there: switch the three remaining `?` placeholders to `%s` (they work only through
  `db.py`'s translation shim; the rest of the file already uses `%s`) and fix the doubled
  `ALERT_EXPIRATION_1D = ALERT_EXPIRATION_1D =` assignment. Update the module docstring's usage
  example, which still says `from options_position_store import …`.

Callers:

- `notifier.py` `check_options_alerts`: pass `owner=self.owner` to `auto_expire_past_positions`,
  `get_active_positions`, `get_pending_alerts` (D2).
- `registry.py`: leave the construction (stateless). Note in the PR that it has no consumer.

Tests (`tests/test_repositories_db.py::TestOptionsPositionStore`, real DB):

- Existing tests gain `owner=`.
- `test_wrong_owner_reads_come_back_empty` — `get_position`, `get_active_positions`,
  `position_count`, `get_pending_alerts` for owner B see nothing of A's.
- `test_wrong_owner_mutations_change_nothing` — B's `close_position`/`expire_position` on A's id
  leave it ACTIVE; B's `auto_expire_past_positions` doesn't expire A's past position.
- `test_add_position_records_owner`.
- Teardown deletes by synthetic `SYM` as today (both owners' rows).
- `tests/test_notifier.py`: the store is called with `owner=notifier.owner` (mock assertion).

Docs in the same PR: `docs/architecture/data.md` (table now per-owner), `docs/quantcore_erd.md`
(`OPTIONS_POSITIONS.owner`), `docs/capabilities-matrix.md` rows that say it "reads
`options_positions`" (now "John's `options_positions`"), and the security map: the G1 row's second
sentence changes to "`options_positions` is per-owner (V12)" — G1 stays open until step 2.

Rollout: CI applies V12 on test with the merge, on prod with the next `prod-rollout.yml`. Verify
with `python scripts/schema_check.py --prod` (not `flyway info`, see CLAUDE.md), then confirm the
next daily run's options alerts are unchanged for John.

Ordering: the new image needs the column, and V12 runs before roll-out in both workflows. Rolling
the image back past V12 is harmless here (the old image ignores the column and the DEFAULT fills
it), but schema changes stay forward-fix only.

### Step 1′ — drop path (only if D1 goes the other way)

`V12__drop_options_positions.sql` (`DROP TABLE options_positions`), applied by hand
(`./scripts/flyway.sh migrate`, then `--prod migrate`), removed from `_SCHEMA` and the snapshot;
delete `OptionsPositionStore`, its registry field, `check_options_alerts`, the alert constants'
users, and their tests; doc rows deleted rather than edited. Then step 2 unchanged.

### Step 2 — the general rule and the guard that enforces it

Rule (one home, others link):

- `CLAUDE.md`, *Unified Database*: a new bullet — **every per-owner table: `owner` is a required
  keyword-only argument on every public repository method, every statement carries the `owner`
  predicate (private helpers and SQL constants included), a child table is scoped through its
  parent's owner, and the repository has wrong-owner read and mutation tests.** Exempt tables
  (`owner_identities`, global `watchlist`) are named in the guard, not in prose.
- The Harvester bullet "Isolation is enforced in SQL" shortens to a link to that rule plus the
  Harvester-specific part (routes 404 via `Depends(require_owner)`).
- `docs/proposals/architectural-standard-v2.md`: add the rule to the standard (or link to
  CLAUDE.md's bullet if the standard already has a data-access rule to hang it on).

Guard (`tests/test_owner_isolation_coverage.py`, no DB, fast):

1. From `db/schema_snapshot.json`, derive the owner-scoped set: every table with an `owner` column,
   plus every table with a FOREIGN KEY into one (transitively — `alerts → plan_rungs →
   plan_instances`).
2. A committed map `OWNER_SCOPED = {table: (repository module, test module, [wrong-owner test
   names])}` plus `EXEMPT = {"owner_identities": "<reason>", "watchlist": "<reason>"}`. The test
   fails if a derived table is in neither, if a map entry names a table that is no longer
   owner-scoped, or if a named test function doesn't exist (AST loo3. A static scan of each mapped repository module. It is a **heuristic tripwire, not the proof
   of isolation** — the wrong-owner read and mutation tests from (2) are what the security map cites
   as the guard. "The string contains `owner`" is not the test: `SELECT owner, symbol FROM
   options_positions` names the column and returns every owner's rows. The scan instead checks,
   per statement kind:
   - **SELECT / UPDATE / DELETE on a top-level owner table** (`positions`, `plan_instances`,
     `user_settings`, `options_positions`): the `WHERE` clause (or a `JOIN … ON`) has a
     *restricting* predicate binding that table's `owner` to a parameter — `owner = %s`,
     `<alias>.owner = %s`, or the named form `owner = %(owner)s`. `owner` in the select list,
     `ORDER BY`, `GROUP BY` or `RETURNING` doesn't count.
   - **INSERT into one**: `owner` is in the column list with a bound value. An omitted column would
     fall through to `DEFAULT 'john'` (D3), so omission is a failure, not a pass.
   - **A child table** (`lot_sales`, `plan_rungs`, `alerts`): the statement must restrict the
     *owning ancestor's* `owner` to a parameter, through the FK chain. Joining the parent isn't
     enough — `SELECT ls.* FROM lot_sales ls JOIN positions p ON p.position_id = ls.lot_id` joins
     and never restricts.
   - An `INSERT … SELECT` or a sub-select is checked as the statements it contains.

   The scan works on the SQL text with a small tokenizer (strip comments and string literals,
   split on clause keywords). It is not a parser, and that limit is written in the test's
   docstring. A statement it can't classify fails, so a new shape gets a human look instead of a
   silent pass. Legitimately cross-owner statements go on an allow-list keyed by module + a
   normalized-SQL fingerprint, each with a one-line reason; a changed statement drops off the list.
   The known one is the daily job's walk, `SELECT DISTINCT owner FROM positions` in
   `portfolio_repository.py`; the Harvester scan path is already owner-scoped. Each allow-list
   entry also needs a reviewer, so adding one shows up in the PR diff.

   Regression fixtures for the scan itself (`tests/test_owner_isolation_coverage.py`, pure strings,
   no DB) — the scan must **reject**:
   - `SELECT owner, symbol FROM options_positions` (owner only in the select list);
   - `SELECT * FROM options_positions ORDER BY owner`;
   - `SELECT ls.* FROM lot_sales ls JOIN positions p ON p.position_id = ls.lot_id` (parent joined,
     not restricted);
   - `UPDATE options_positions SET status = 'CLOSED' WHERE position_id = %s` and the same `DELETE`
     (mutation without the owner predicate);
   - `INSERT INTO options_positions (symbol, kind) VALUES (%s, %s)` (owner column omitted);
   - `SELECT * FROM positions WHERE owner IS NOT NULL` / `WHERE owner <> %s` (not a restriction to
     one owner);

   and **accept** the real shapes (`… WHERE position_id = %s AND owner = %s`, the aliased
   `p.owner = %s` join form, an INSERT with `owner` bound). Start report-only locally, then make it
   strict in the same PR once the allow-list is settled.

ist is settled.

`user_settings` is isolated only at the API level today; add a repository-level wrong-owner test
(`get`/`set` for owner B never sees or overwrites A's row) so the map can point at one.

Docs: security map — A01's "written down for Harvester only" row becomes Enforced, citing the
per-repository wrong-owner tests as the guard and `tests/test_owner_isolation_coverage.py` as the
inventory + heuristic scan that keeps the map complete; delete G1 from *Gaps*; close #460.

## Out of scope

- Per-owner Discord destinations (needed before options alerts run for anyone but John).
- A REST/MCP surface for options positions. When one is added, it resolves the owner with
  `Depends(require_owner)` and has no `?owner=` — the rule above already covers it.
- G3 (bound parameters, #462): step 2's scan is a cousin of it but checks predicates, not
  interpolation.

## Checkpoint log

| Step | Status | PR | Date | Notes |
|---|---|---|---|---|
| D1 check (prod/test row counts) | skipped | — | 2026-10-29 | D1 decided *keep* by John. No Cloud SQL proxy was running, so the read-only count wasn't taken; the DEFAULT backfills whatever rows exist, so it doesn't change the design. Taken on prod after step 1 rolled out: `options_positions` is empty, so the DEFAULT backfilled nothing |
| 1 — options_positions owner | done | #480 | 2026-10-29 | V12, not V10: V10/V11 had landed meanwhile. Review: split `get_pending_alerts` (CC 16 / cognitive 27) into pure helpers (max 6 / 6) with byte-identical output on a 1,166-alert grid; added no-DB branch tests and an SQL-shape guard for every statement. Merged as `a4a23de`; on test 2026-10-29 (migrate Job `quantcore-migrate-4gbsr` succeeded, roll-out green); on prod 2026-10-29 (prod-rollout green; `schema_check.py --prod`: 23 tables, 0 missing, 0 extra, ledger through V12; `owner` is `NOT NULL DEFAULT 'john'` and `idx_options_positions_owner_status` exists) |
| 2 — general rule + guard | done | #482 | 2026-10-29 | Rule in CLAUDE.md (+ arch-v2 Rule 12, AGENTS.md); `tests/test_owner_isolation_coverage.py` scans every tracked `.py` outside `tests/`, not just the mapped repositories; its first run found four unscoped Harvester statements, fixed here; 10 reviewed `ALLOWED` entries; G1 closed. Review (Guppy, 50ab3e5): a child is credited only through a correlated join or an `IN` subquery on the ancestor (an uncorrelated `EXISTS` no longer counts); a `NOT` around an owner predicate is unclassifiable; each `UNION` branch and each self-join alias is restricted on its own; 12 new reject and 5 new accept fixtures; no `ALLOWED` change. Merged as `66063e0` before Guppy reviewed that fix commit (`b367f76`); prod-rollout run 40960326106 dispatched the same day (no migration) |
| Plan review (Guppy, #476) | done | #476 | 2026-10-29 | Tightened the step-2 scan: restricting predicate, not token presence; child tables restrict the ancestor; reject fixtures; statement count 7 → 8 |

## Gotchas

_(Append here as each step lands — what misled you, not just the outcome.)_

- **`ls db/migrations | tail` lies about the next version** (step 1). Lexical order puts
  `V10__`/`V11__` *before* `V2__`, so the tail showed V8/V9 and the plan said V10 — both V10 and
  V11 were already on main. `tests/test_schema_parity.py` caught the duplicate version (it fails
  even without a database). Use `ls db/migrations | sort -V | tail -1`.
- **`notifier.check_options_alerts` re-imports `get_services` locally**, so a test patching only
  `notifier.get_services` hits the real registry; patch `quantcore.services.registry.get_services`
  too.
- **A semicolon in a `_SCHEMA` comment breaks every test** (step 1, CI on 093ed5c). `init_schema()`
  splits `_SCHEMA` on `;` (`quantcore/db.py`, `schema.split(';')`), so `-- … owner.sql; existing
  rows …` produced a comment-only chunk, and psycopg2 raised `can't execute an empty query` for
  every module that builds the app: 216 errors plus 10 lean-import failures. It couldn't show up
  locally because the DB never connected here. Keep `;` out of `_SCHEMA` comments.
- **A behavioural wrong-owner test can't see a redundant predicate** (step 1 review, #480). In
  `auto_expire_past_positions` the owner-scoped SELECT picks the ids, so deleting `AND owner = %s`
  from the bulk UPDATE after it left every DB test green. Only an SQL-shape assertion
  (`tests/test_options_position_alerts.py`) catches it. Step 2's scan generalises that check.

- **"Contains `owner`" looks like a check and isn't one** (plan review, #476). The first draft of
  the step-2 scan passed any SQL string mentioning `owner`, or any child query joining its parent.
  `SELECT owner, symbol FROM options_positions` and a `lot_sales JOIN positions` with no owner
  restriction both pass that and both read every owner's rows. The check has to be on a predicate
  that restricts to a bound owner, and the behavioural wrong-owner tests stay the actual guard.
- **"Every Harvester statement carries the predicate" wasn't true** (step 2). CLAUDE.md and
  harvester.md said so, and the wrong-owner tests passed, but the guard's first run found four
  statements without it: the supersede `UPDATE plan_instances … WHERE instance_id=?` in
  `build_plan` (now `_supersede_active_plan`), the rung read in `harvest_hit_for_symbol`, `SQL_DISABLE_OTHER_ALERTS_FOR_INSTANCE`
  and `SQL_MARK_ALERT_FIRED`. Each took an id from an owner-scoped query first, so nothing
  leaked: the same redundant-predicate blind spot as the options bulk UPDATE. A prose rule plus
  behavioural tests can't tell you a statement is missing; only reading every statement can.
- **The FK walk has to stop at the first owner table.** Walking `plan_rungs → plan_instances →
  positions` made `positions` an "ancestor" of rungs, so a rung query restricted through
  `plan_instances.owner` would have been asked to restrict `positions` too.
- **A child INSERT can't carry an owner predicate** (`INSERT INTO alerts`, `plan_rungs`,
  `lot_sales` have no WHERE). Each is safe because its parent id was resolved owner-scoped on the
  same connection. They are `ALLOWED` entries with that reason rather than `INSERT … SELECT`
  rewrites, which would change behaviour in a guard PR.
- **`user_settings` already had a repository-level wrong-owner test**
  (`tests/test_repositories_db.py::TestUserSettingsRepository::test_owners_are_isolated`). The
  plan's "isolated only at the API level" came from searching the wrong test files.
- **Keyword-only `owner` is enforced as "never defaulted".** `PortfolioRepository` and
  `UserSettingsRepository` take `owner` positionally, and it's required, so it can't be forgotten
  either. Converting 16 methods and their callers would add no isolation, so the rule says
  keyword-only for new code and the guard checks only for defaults.
- **"The ancestor is restricted somewhere below" credits the wrong rows** (step 2 review, #482).
  The first scan accepted a child whenever its ancestor was restricted at the same level *or any
  subquery under it*, so `SELECT ls.sale_id FROM lot_sales ls WHERE EXISTS (SELECT 1 FROM
  positions p WHERE p.owner = ?)` passed while returning every owner's sales: the EXISTS is true
  for anyone who owns a position. A restriction only protects the child's rows when it's tied to
  them, a join on the child's column or `child_col IN (…)`. The same review found `NOT (owner =
  ?)`, an unscoped `UNION` branch and an unscoped self-join alias passing; the scanner now splits
  set-op branches and checks every reference, not every table. Every real statement already had a
  tied shape, so tightening it changed no `ALLOWED` entry.
