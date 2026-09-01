# One owner for the deployed schema — make drift provable

**Source issue:** [#165](https://github.com/JohnFunkCode/StockPortfolioManager/issues/165)
**Status:** **COMPLETE** — all four PRs merged ([#172](https://github.com/JohnFunkCode/StockPortfolioManager/pull/172), [#173](https://github.com/JohnFunkCode/StockPortfolioManager/pull/173), [#174](https://github.com/JohnFunkCode/StockPortfolioManager/pull/174), [#175](https://github.com/JohnFunkCode/StockPortfolioManager/pull/175)) and enforcing on **both** projects as of 2026-08-10: `schema check: mode=auto resolved=verify tables=22 missing=0 mismatch=0 extra=0`. Flyway owns the DDL on test and prod; `init_schema()` is now confined to databases nobody else manages. **Remaining:** Step 3 — close [#165](https://github.com/JohnFunkCode/StockPortfolioManager/issues/165) with the decision record
**Shape:** four PRs, in order; PRs 1–3 are code, PR 4 is a config flip plus docs
**Written for:** an executing agent at **Sonnet 5** capability — every step names its files, its
commands, and how to know it worked
**Related:** [`legacy-report-retirement-plan.md`](legacy-report-retirement-plan.md) (#147 — Part H1
puts the **first non-additive statement** into `init_schema()` and escalates this issue; see
[Interaction with #147](#interaction-with-147)), [`phase1-migration-plan.md`](phase1-migration-plan.md)
(where Flyway was introduced), [`watchlist-db-plan.md`](watchlist-db-plan.md) (#83 — the rollout
that exposed the problem)

Line numbers were captured on **2026-08-09** against `main` at `70dabf0`. If one has drifted,
search for the quoted code.

---

## The problem

Two systems create the schema of a deployed database, and neither can see the other:

| | Where | Runs when |
|---|---|---|
| `init_schema()` | [`quantcore/db.py:567`](../../quantcore/db.py) — the 22-table `_SCHEMA` string at [`db.py:19`](../../quantcore/db.py) | **Every process start**, via `ensure_schema()` ([`api/main.py:49`](../../api/main.py), [`quantcore/repositories/options_repository.py:52`](../../quantcore/repositories/options_repository.py)) or directly ([`main.py:621`](../../main.py)) |
| Flyway | `db/migrations/V*.sql` | Only when a human runs `./scripts/flyway.sh migrate` |

Because the project rule is to mirror every schema change into `_SCHEMA`, and because
`init_schema()` runs first and on every pod start, deployed databases reach the correct *shape*
without Flyway's participation. Flyway's ledger then records history that never happened — which is
how the #162 rollout saw two "Pending" migrations against a database that already had every object
they create.

The defect is **not** that there are two writers. It is that there are two writers and **no
comparison between them**, so:

- `flyway info` cannot answer the question it is run to answer, in either direction — "Pending" did
  not mean missing, and "Success" would not mean present.
- Genuine drift (a hand-applied change, a half-failed migration) is invisible, because
  `init_schema()` silently re-converges the shape on the next restart.
- A pure-DDL migration is silently redundant while a backfill migration is silently required, and
  nothing distinguishes them at a glance.
- If the two ever disagree, the loser is whichever process starts last, and the symptom appears at
  an arbitrary later pod restart rather than at deploy time.

**This plan adds the comparison, then narrows the ownership.**

---

## How to use this plan

Four PRs, in order. Each is independently shippable and independently revertable. Within a PR,
steps are numbered and **each step is one commit**. Append a row to the
[Checkpoint log](#checkpoint-log) as each PR lands — not at the end.

**Rules for the executing agent:**

1. **Do not make design decisions.** They are answered in [Decisions](#decisions). If a step seems
   to need one that isn't written down, stop and ask.
2. **Do not commit, and do not open a PR, without explicit authorization.** "Open a PR" is not
   implicit commit authorization. `main` requires review + approval and cannot be self-merged.
3. **Verify each step before committing it**, using the step's own Verify block.
4. **When the parity check fails, the live prod database is the tiebreaker** (decision D6). Never
   "fix" parity by editing a migration that has already been applied to prod — Flyway checksums it,
   and prod's ledger already records it. Fix forward with a new `V6__*.sql`.
5. **Compact context between PRs.** The last commit is the saved state.
6. Run the full backend suite before declaring any PR complete:
   ```bash
   python -m unittest discover -s tests -t .
   ```
   DB-backed suites need a reachable Postgres. Locally, run them against **test**, never prod:
   ```bash
   ./scripts/with-test-db.sh .venv/bin/python -m unittest discover -s tests -t .
   ```
7. CI enforces `diff-cover --fail-under 85` on changed lines. Every new module in this plan ships
   its own unit tests in the same commit.

---

## Decisions

**D1 — Adopt the issue's Option 2 ("keep both, add a drift check") now, built so Option 1
("retire `init_schema()`'s DDL on deployed environments") becomes a one-line flip later.**
PR 3 delivers the substance of Option 1; PR 4 is the flip.

Why not Option 1 outright: it makes `flyway migrate` a mandatory pre-deploy step, and
`deploy.yml` deliberately holds **no database credentials** ("CI never needs the DB DSN or JWT
secret" — [`.github/workflows/deploy.yml:9`](../../.github/workflows/deploy.yml)). Automating
migrations in CD would break that constraint; leaving them manual makes a hard-fail startup check
a foot-gun on day one. Sequencing it after the drift check is what makes it safe.

Why not Option 3 ("baseline honestly"): it renames the status quo without changing any of the four
failure modes above.

**D2 — The committed expectation is `db/schema_snapshot.json`, generated from `init_schema()` on
an empty database.** This mirrors the existing OpenAPI surface guard
([`scripts/check_openapi_snapshot.py`](../../scripts/check_openapi_snapshot.py) →
`docs/openapi-surface.txt`): every schema change becomes a reviewable diff, and the verifier gets an
offline expectation so it needs no reference database at runtime.

**D3 — The baseline SQL lives at `db/baseline/V1__quantcore_baseline.sql`, outside
`flyway.locations`.** It is the 16-table start state that `db/flyway.conf`'s
`baselineVersion=1` already claims exists but never wrote down. It is a reference start-state for
building a database from empty, **not** a migration — adding it under `db/migrations/` would put a
version at or below the baseline, which Flyway ignores at best and errors on at worst.

**D4 — Missing objects are errors; extra objects are warnings.** A deployed database legitimately
carries objects the snapshot does not: `flyway_schema_history`, and leftovers from the one-shot
SQLite migration. Those are noise, not drift the app must refuse to run on. A *missing* object is
the failure the code will actually hit.

**D5 — Verify mode ships warn-only, then flips to hard-fail after one full deploy cycle on both
projects.** The first honest look at prod's real schema is likely to surface something; discovering
it in a log line is fine, discovering it as a failed prod rollout is not.

**D6 — When the CI parity check fails, prod reality wins.** Confirm what prod actually contains
with `scripts/schema_check.py --prod` (read-only), then make both sources agree with it. If a new
migration is needed, it is a **new version** — never an edit to an applied one.

**D7 — Flyway's role narrows to a changelog.** After PR 4 it is (a) the mechanism that applies
schema changes to deployed databases, and (b) the marker that a database *is* deployed. It stops
being quoted as evidence of state anywhere. `scripts/schema_check.py` is the evidence command.

---

## What ships

| Artifact | PR | Purpose |
|---|---|---|
| `db/baseline/V1__quantcore_baseline.sql` | 1 | The 16-table start state, extracted verbatim from git history |
| `quantcore/schema_introspect.py` | 1 | `describe_schema()` / `diff_schemas()` / `scratch_database()` — pure introspection and comparison, no policy |
| `db/schema_snapshot.json` | 1 | The committed expected shape |
| `scripts/check_schema_snapshot.py` | 1 | CI guard: the snapshot matches `init_schema()` |
| `scripts/schema_check.py` | 1 | **The evidence command.** Read-only: live DB vs snapshot |
| `tests/test_schema_parity.py` | 2 | CI proof that `init_schema()` == baseline + migrations |
| `QUANTCORE_SCHEMA_MODE` + verify path in `ensure_schema()` | 3 | The app stops creating schema where Flyway already runs |
| Hard-fail default + docs + issue close | 4 | The flip |

---

## PR 1 — Evidence

**Branch:** `feat/schema-evidence` · **Nothing in the running application changes.** Every artifact
here is read-only or CI-only.

### Step 1 — Extract the baseline

The `_SCHEMA` string as it stood immediately before `V2__positions_multi_owner.sql` was added is
the 16-table baseline `db/flyway.conf` describes. It is in git:

```bash
git show 9a9d204^:quantcore/db.py | sed -n '/^_SCHEMA = """/,/^"""/p'
```

Write the SQL between the triple quotes — **verbatim, no reformatting** — to
`db/baseline/V1__quantcore_baseline.sql`, preceded by a header comment recording:

- that this is the start state `flyway.conf`'s `baselineVersion=1` refers to;
- that it was extracted from `9a9d204^` (`quantcore/db.py`, `_SCHEMA`) on the date of the commit;
- that it is **not** under `flyway.locations` and Flyway never runs it (decision D3);
- that it is **frozen** — schema changes go in `db/migrations/V*.sql`, never here.

**Verify:** the file contains exactly 16 `CREATE TABLE IF NOT EXISTS` statements and zero
`ALTER TABLE` statements.

```bash
grep -c 'CREATE TABLE IF NOT EXISTS' db/baseline/V1__quantcore_baseline.sql   # 16
grep -c 'ALTER TABLE' db/baseline/V1__quantcore_baseline.sql                  # 0
```

### Step 2 — `quantcore/schema_introspect.py`

Infrastructure beside `db.py`, not a service. Comparison only — it decides nothing about what to do
with a difference.

```python
def describe_schema(conn, *, ignore_tables=IGNORED_TABLES) -> dict
```

Introspects `public` into a stable, sorted, JSON-serializable dict:

```json
{
  "tables": {
    "watchlist": {
      "columns": {
        "watchlist_id": {"type": "integer", "nullable": false,
                         "default": "nextval('watchlist_watchlist_id_seq'::regclass)"},
        "tags":        {"type": "ARRAY", "nullable": false, "default": "'{}'::text[]"}
      },
      "indexes":     {"watchlist_pkey": "CREATE UNIQUE INDEX watchlist_pkey ON public.watchlist USING btree (watchlist_id)"},
      "constraints": {"watchlist_symbol_id_fkey": "FOREIGN KEY (symbol_id) REFERENCES symbols(symbol_id) ON DELETE CASCADE"}
    }
  }
}
```

Sources: `information_schema.tables` (`table_type='BASE TABLE'`), `information_schema.columns`
(name, `data_type`, `character_maximum_length`, `numeric_precision`, `numeric_scale`, `is_nullable`,
`column_default`), `pg_indexes.indexdef`, and `pg_get_constraintdef()` over `pg_constraint`. Compose
the numeric/length fields into one `type` string (`numeric(18,6)`, `character varying(8)`) so a
column-type change is one readable diff line, not three.

`IGNORED_TABLES = {"flyway_schema_history"}` — decision D4.

```python
def diff_schemas(expected: dict, actual: dict) -> list[str]
```

Returns human-readable lines, each prefixed `MISSING`, `EXTRA`, or `MISMATCH`, sorted, empty when
identical:

```
MISSING  table watchlist
MISSING  column positions.trade_date
MISMATCH column positions.quantity  expected numeric(18,6)  actual integer
EXTRA    index  positions.idx_positions_legacy
```

```python
@contextmanager
def scratch_database(dsn: str, name: str) -> Iterator[str]
```

Connects to the `postgres` maintenance database on the same host (swap the path component of the
DSN), `CREATE DATABASE <name>` in autocommit, yields the DSN pointing at it, and drops it on exit
(`DROP DATABASE IF EXISTS <name> WITH (FORCE)`). Raise a clear, catchable error if the role lacks
`CREATEDB` — CI's `quantcore` role is the container superuser and has it; a Cloud SQL application
role may not.

```python
def snapshot_from_dsn(dsn: str) -> dict
```

`scratch_database` → `quantcore.db.init_schema(scratch_dsn)` → `describe_schema` → dict. This is the
one definition of "what the code expects", used by both Step 3 and Step 4.

**Unit tests** (`tests/test_schema_introspect.py`) cover `diff_schemas` exhaustively against
hand-built dicts — no database — for: identical, missing table, missing column, extra column, type
mismatch, nullability mismatch, default mismatch, index/constraint added and removed, and
`flyway_schema_history` ignored on both sides.

**Verify:**
```bash
python -m unittest tests.test_schema_introspect
```

### Step 3 — `db/schema_snapshot.json` + `scripts/check_schema_snapshot.py`

Model the script on [`scripts/check_openapi_snapshot.py`](../../scripts/check_openapi_snapshot.py),
including its docstring conventions and `--update` flag:

- default: `snapshot_from_dsn(QUANTCORE_DB_DSN)` vs the committed `db/schema_snapshot.json`; print
  the `diff_schemas` lines and exit 1 on any difference, with the remediation command in the
  message.
- `--update`: rewrite the snapshot (`json.dumps(..., indent=2, sort_keys=True)` + trailing newline,
  so the diff is reviewable line by line).

Generate the snapshot and commit it in this step.

**Verify:**
```bash
./scripts/with-test-db.sh python scripts/check_schema_snapshot.py    # exits 0, prints "schema snapshot up to date"
```

Then confirm it actually bites: add a throwaway `CREATE TABLE IF NOT EXISTS zzz_drift (id INT);` to
`_SCHEMA`, re-run (expect exit 1 naming `zzz_drift`), and revert.

### Step 4 — Wire it into CI

In [`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml), immediately after the
existing OpenAPI surface step (`deploy.yml:113`):

```yaml
      - name: Schema snapshot up to date
        run: python scripts/check_schema_snapshot.py
```

The `gate` job already provides a Postgres service and `QUANTCORE_DB_DSN`, and its `quantcore` role
can create databases, so no new services or secrets are needed.

**Verify:** `grep -n "check_schema_snapshot" .github/workflows/deploy.yml`, and confirm the step
sits inside the `gate` job (which has the `postgres` service), not a deploy job.

### Step 5 — `scripts/schema_check.py`, the evidence command

Read-only. Reuses `scripts/flyway.sh`'s DSN handling so the two agree on what `--prod` means:
`--test` (default, `QUANTCORE_TEST_DB_DSN`) / `--prod` (`QUANTCORE_DB_DSN`), read from `.env`,
echoing `host:port/database` before connecting and never printing the password.

It prints two blocks, explicitly labelled:

```
target: prod  127.0.0.1:5433/quantcore  (user: quantcore)

SCHEMA (evidence — live objects vs db/schema_snapshot.json)
  22 tables, 0 missing, 1 extra
  EXTRA  table legacy_sqlite_scratch

FLYWAY LEDGER (changelog only — NOT evidence of schema state, issue #165)
  applied through V5__watchlist  (2026-07-28)
```

Exit codes: `0` clean or extras-only, `1` on any `MISSING`/`MISMATCH`. It opens no write
transaction and takes no DDL locks.

**Verify** — against test first, then prod (read-only, safe):
```bash
python scripts/schema_check.py --test
python scripts/schema_check.py --prod
```

Record both outputs in the [Checkpoint log](#checkpoint-log). **This is the first honest look at
what those databases contain** — if either reports `MISSING` or `MISMATCH`, stop and report it
rather than proceeding to PR 2. That finding is the point of the exercise.

### Step 6 — Docs for PR 1

- **`readme.md`** (Migrations section, ~line 140): replace "check the objects directly" with the
  actual command, `python scripts/schema_check.py --prod`, and state that `flyway info` is a
  changelog view, not evidence.
- **`CLAUDE.md`** (Migrations, ~line 281): same substitution; add `db/schema_snapshot.json` to the
  list of things a schema change touches.
- **`scripts/flyway.sh`** header: the existing `NOTE` block gains one line pointing at
  `scripts/schema_check.py` as the evidence command.
- **This file:** append the PR 1 checkpoint row.

---

## PR 2 — Enforcement

**Branch:** `feat/schema-parity` · Makes divergence between the two owners impossible to merge.

### Step 1 — `tests/test_schema_parity.py`

Against the CI Postgres (or a local test Postgres), build two scratch databases and prove they are
identical:

| Database | Built by |
|---|---|
| `quantcore_parity_code` | `quantcore.db.init_schema(dsn)` |
| `quantcore_parity_sql` | `db/baseline/V1__quantcore_baseline.sql`, then every `db/migrations/V*.sql` in numeric version order |

Apply each SQL file as a single `cur.execute(path.read_text())` on an autocommit connection —
psycopg2 executes multi-statement strings, and autocommit matches how `init_schema()` runs
(`db.py:567`'s docstring explains why that matters).

Assert `diff_schemas(describe_schema(code), describe_schema(sql)) == []`, and on failure raise with
the full diff in the message — the diff *is* the bug report.

`skipTest` with a clear message when the DSN is unreachable or the role lacks `CREATEDB`, following
the existing DB-backed suites' pattern. **CI is the authoritative run**, and it must not skip there:
assert that the skip path is not taken when `CI` is set in the environment, so a silently-skipping
guard can't pass as a green build.

Sort migration files by parsed integer version (`V10` after `V9`), not lexicographically.

**Verify:**
```bash
./scripts/with-test-db.sh python -m unittest tests.test_schema_parity -v
```

**Expect the first run to fail.** The two owners have never been compared. Likely differences:
constraint names dropped by name in `_SCHEMA` (`db.py:100-102`) versus how `V2` expresses the same
change, index names, and `positions.quantity`'s numeric type. Resolve each under decision D6 —
determine what **prod actually has** with `scripts/schema_check.py --prod`, make both sources agree
with it, and add a new `V6__*.sql` if a real change is needed. Do not edit `V2`–`V5`.

### Step 2 — Record what the first run found

Whatever step 1 surfaced goes in this file under a new **"What the first parity run found"**
section: each difference, which side was wrong, and how it was resolved. Per the project's
docs rule, record the *gotcha*, not just the outcome — this is the part nobody can reconstruct
later.

### Step 3 — Promote the rule from convention to guarantee

- **`CLAUDE.md`** (~line 16 and the Migrations section) and **`AGENTS.md`** (~line 65): the "every
  schema change ships twice" rule now says it is **enforced by `tests/test_schema_parity.py` in
  CI**, and names the three files a schema change touches — `db/migrations/V*.sql`,
  `_SCHEMA` in `quantcore/db.py`, and `db/schema_snapshot.json`.
- **`readme.md`** Migrations section: same, in human voice.

### What the first parity run found

Three tables, and **nothing else**:

```
MISSING  table arb_nav_snapshots
MISSING  table gex_history
MISSING  table user_settings
```

All three were added to `_SCHEMA` in `quantcore/db.py` and never given a migration, so a database
built purely from `db/baseline` + `db/migrations` lacked them entirely. **The migrations were the
wrong side.** Prod has all three (confirmed read-only on 2026-08-09 against
`quantcore-prod-20260606`: `schema_check.py --prod` reports `22 tables, 0 missing, 0 extra`, plus a
direct `information_schema.tables` query naming the three), so under D6 prod reality wins and the
resolution is `db/migrations/V6__parity_backfill.sql` — idempotent `CREATE TABLE IF NOT EXISTS`
DDL copied verbatim from `_SCHEMA`. `V2`–`V5` were not touched.

Three things worth recording, because they are the parts nobody can reconstruct later:

1. **The predicted failures did not happen.** This plan expected constraint names dropped by name
   in `_SCHEMA` (`db.py:100-102`), index-name drift, and a `positions.quantity` numeric-type
   mismatch. The parity run produced **zero MISMATCH lines** — every table both sides have is
   identical down to columns, indexes, and constraints. The drift was purely *additive*: new
   tables shipped through `init_schema()` only. That is a much better start state than assumed,
   and it means the interesting failure mode here is "someone added a table and forgot the
   migration", not "the two sources describe the same table differently".

2. **The drift was invisible precisely because nothing broke.** `init_schema()` runs on every
   application startup, so it created these tables on every deployed database before Flyway ever
   looked. There was no outage, no error, no log line — the migrations silently stopped describing
   the schema and the only symptom was that `flyway info` said `V5` while the database had 22
   tables. This is the concrete instance of the claim in `CLAUDE.md` that `flyway info` is a
   changelog view, not evidence.

3. **V6 will be a no-op everywhere it runs, and that is correct.** Every deployed database already
   has these three tables, so applying V6 finds nothing to do and just records the version. Do not
   mistake the no-op for the migration being unnecessary: its job is to make the *file set* a
   complete description of the schema, which is what the parity test now enforces. A future
   database built from empty — a new environment, a scratch database, a restore drill — is the
   case that would actually have failed.

**Gotcha caught while running the evidence command:** both PR 1 scripts shipped without the
`sys.path.insert(REPO_ROOT)` preamble every other script in `scripts/` has, so
`python scripts/schema_check.py --prod` from the repo root died with `ModuleNotFoundError: No
module named 'quantcore'` before printing anything. CI sets `PYTHONPATH: .` job-wide, which is
exactly why it passed review — the failure only reproduces interactively, which is the only way
`schema_check.py` is ever invoked. Fixed in PR 2 for both `scripts/schema_check.py` and
`scripts/check_schema_snapshot.py`. The general lesson: a command whose entire purpose is
hand-invocation cannot be validated solely by a CI job that pre-configures its environment.

---

## PR 3 — Verify mode

**Branch:** `feat/schema-verify-mode` · The app stops creating schema on databases that Flyway
already manages. Warn-only (decision D5) — no deploy can fail because of this PR.

### Step 1 — Mode resolution in `quantcore/db.py`

Add `QUANTCORE_SCHEMA_MODE`, **read at call time** (not frozen at import like `DB_DSN`, so tests and
`--update-env-vars` both work):

| Mode | Behaviour |
|---|---|
| `create` | Today's behaviour — run the DDL. The escape hatch. |
| `warn` | Introspect, diff against `db/schema_snapshot.json`, log differences, run no DDL. |
| `verify` | As `warn`, but raise `SchemaDriftError` on any `MISSING`/`MISMATCH`. |
| `auto` *(default)* | `create` when `to_regclass('public.flyway_schema_history')` is NULL, otherwise **`warn`** — flipped to `verify` in PR 4. |

`ensure_schema()` keeps its once-per-process-per-DSN gate for every mode; verification is one
connection and three read-only queries, not something to repeat per repository construction.

Log lines are single-line and greppable: `schema check: mode=auto resolved=warn tables=22 missing=0
extra=1`, then one line per difference. Never log the DSN password (the BYOK never-log policy
applies to every log line in this repo, not just the keyproxy's).

Deployed databases lose the ~3s, 22-table-locking DDL from every pod start. Say so in the docstring
— that is a real secondary win, and the reason the AB-BA deadlock note at `db.py:567` becomes
mostly historical for prod.

### Step 2 — Route `main.py` through `ensure_schema()`

[`main.py:621`](../../main.py) calls `init_schema()` directly, bypassing both the once-per-process
gate and — after step 1 — the mode. Change the import at `main.py:17` and the call at `main.py:621`
to `ensure_schema()`. This is the only remaining direct caller outside tests.

**Verify:** `grep -rn "init_schema" --include="*.py" .` shows callers only in `quantcore/db.py`,
`quantcore/schema_introspect.py`, and `tests/`.

### Step 3 — Tests

Extend [`tests/test_schema_bootstrap.py`](../../tests/test_schema_bootstrap.py) — its existing
contracts must still hold, because a database with no Flyway ledger (local, CI, compose) still
resolves to `create`:

- `auto` + no `flyway_schema_history` → DDL runs (the existing `test_create_app_does_not_re_run_the_ddl`
  contract is preserved, and should be asserted to still pass unchanged).
- `auto` + ledger present → no DDL runs.
- `verify` + a missing table → raises `SchemaDriftError` naming the table.
- `warn` + a missing table → logs, does not raise.
- `create` + ledger present → DDL runs anyway (the escape hatch works).
- Extras never raise in any mode (decision D4).

Use the existing `FakeConnection`/`FakeCursor` fixtures so these stay database-free.

### Step 4 — Roll out and soak

No Cloud Run configuration change is required — `auto` does the right thing on both projects. Merge,
let `deploy.yml` roll test, then:

1. `gcloud run services logs read quantcore-api --project quantcore-test-20260606 --region us-central1 | grep "schema check"` — confirm `resolved=warn` and read the difference lines.
2. Promote to prod via `prod-rollout.yml` (`workflow_dispatch`, 7-char SHA) and repeat against `quantcore-prod-20260606`.
3. Record both logs in the [Checkpoint log](#checkpoint-log).

Any `MISSING`/`MISMATCH` in those logs is real drift and is resolved (under D6) **before** PR 4.

---

## PR 4 — The flip

**Branch:** `feat/schema-verify-hard-fail` · One line of code; the rest is documentation and the
issue. Do not start until PR 3 has soaked through one full deploy cycle on **both** projects with
zero `MISSING`/`MISMATCH` (decision D5).

### Step 1 — `auto` resolves to `verify`

Change the one mapping in `quantcore/db.py`, update its tests, and document the rollback in the same
docstring:

```bash
gcloud run services update quantcore-api --project <project> --region us-central1 \
  --update-env-vars QUANTCORE_SCHEMA_MODE=create
```

`--update-env-vars`, never `--set-env-vars` — the latter replaces the whole set and has broken prod
before.

### Step 2 — Document the behavioural change loudly

This is the part that changes how people work, and it belongs in `readme.md`, `CLAUDE.md`, and
`AGENTS.md`:

- **Migrations are now load-bearing.** `./scripts/flyway.sh --prod migrate` must run **before** an
  image carrying a schema change is deployed. `init_schema()` is no longer the safety net on a
  deployed database.
- **A migration must now be complete DDL.** Previously a forgotten column was invisible because
  `_SCHEMA` created it anyway. Now it fails the deploy.
- **Failing early is the feature.** A new Cloud Run revision that fails its startup check never
  takes traffic — the previous revision keeps serving. The old behaviour deferred the same problem
  to an arbitrary later restart.
- Add the migrate-first step to the promotion runbook,
  [`docs/operations/prod-promotion.md`](../operations/prod-promotion.md) — *not*
  [`prod-rollout-plan.md`](prod-rollout-plan.md), which is the closed checkpoint log for the
  one-time buildout. And say **both projects**: `deploy.yml` auto-rolls test on every merge to
  `main`, so an un-migrated schema change fails the test revision before prod ever sees it.

### Step 3 — Close the loop on #165

Comment on [#165](https://github.com/JohnFunkCode/StockPortfolioManager/issues/165) with: the
decision taken (D1), what the first parity run and the first `schema_check.py --prod` found, and the
three commands that replace `flyway info` as evidence. Then close it. Also add the follow-up comment
that [`legacy-report-retirement-plan.md`](legacy-report-retirement-plan.md) Part H1 asks for, noting
that the escalation it flagged is now covered by the parity test.

---

## Interaction with #147

[`legacy-report-retirement-plan.md`](legacy-report-retirement-plan.md) Part H1 adds a
`DROP INDEX IF EXISTS ux_one_active_plan_per_symbol` followed by a `CREATE UNIQUE INDEX` to both
`init_schema()` and a migration — **the first non-additive statement in `_SCHEMA`**. Until now the
two owners could only duplicate each other; a `DROP` is the first case where they can actively
disagree about what the database holds, and where "whichever process starts last wins" has teeth.

**PR 2 of this plan should land before #147's Part H.** It costs #147 nothing (H is late in its
sequencing) and it means the first destructive statement arrives with a test that proves both owners
express it identically. If the two land out of order, #147 Part H1 must run
`tests/test_schema_parity.py` as part of its own verification.

---

## Risks

| Risk | Mitigation |
|---|---|
| The first parity run fails messily and blocks the PR | Expected, and the point. D6 gives the tiebreaker rule; PR 2 Step 2 requires writing the findings down. Nothing is deployed by PR 2. |
| A false `MISSING` breaks a prod deploy | D5: warn-only for a full deploy cycle on both projects first, and the `QUANTCORE_SCHEMA_MODE=create` escape hatch is a `--update-env-vars` away. |
| A forgotten `flyway migrate` blocks a prod rollout after PR 4 | Correct behaviour, made survivable: the failing revision never takes traffic, and the runbook (PR 4 Step 2) puts migrate before deploy. |
| The parity test silently skips in CI and passes green | PR 2 Step 1 asserts the skip path is not taken when `CI` is set. |
| `CREATE DATABASE` unavailable to some role | `scratch_database()` raises a clear, catchable error; CI's superuser role has it, and no production role ever runs this code. |
| The snapshot becomes another stale copy | It is generated, never hand-edited, and `scripts/check_schema_snapshot.py` fails CI the moment it drifts from `_SCHEMA`. |

---

## Documentation to update

Per the project's "documentation is part of the change" rule, tracked here so nothing lands stale:

| Doc | PR | What changes |
|---|---|---|
| `readme.md` | 1, 2, 4 | Migrations section: evidence command; the three-file rule; migrate-before-deploy |
| `CLAUDE.md` | 1, 2, 4 | Migrations section + the schema line in "Documentation is part of the change"; `quantcore/db.py` description gains the mode |
| `AGENTS.md` | 2, 4 | The "ships twice" non-negotiable becomes "ships three times, CI-enforced" |
| `scripts/flyway.sh` | 1, 4 | Header `NOTE` points at the evidence command; loses the "shape converges on startup" claim after PR 4 |
| `db/flyway.conf` | 1 | Comment points at `db/baseline/V1__quantcore_baseline.sql` as the written-down baseline |
| [`docs/operations/prod-promotion.md`](../operations/prod-promotion.md) | 4 | Runbook gains the migrate-first step (**corrected target** — the plan originally named `prod-rollout-plan.md`, which is a finished checkpoint log, not the runbook operators follow) |
| This file | all | Checkpoint log, and PR 2's "What the first parity run found" |

---

## Checkpoint log

Append one row per PR **as it lands**, not at the end.

| PR | Date | Commit | What landed | What it found |
|----|------|--------|-------------|---------------|
| 1 (Evidence) | 2026-08-09 | — | `db/baseline/V1__quantcore_baseline.sql` (extracted from `9a9d204^`'s `init_schema()`, 16 tables / 0 `ALTER TABLE`, D3: lives outside `flyway.locations`); `quantcore/schema_introspect.py` (`describe_schema`/`diff_schemas`/`scratch_database`/`snapshot_from_dsn`, D4: MISSING/MISMATCH are errors, EXTRA is a warning) + `tests/test_schema_introspect.py` (13 tests, DB-free); `db/schema_snapshot.json` (generated from a live `init_schema()` run, 22 tables) + `scripts/check_schema_snapshot.py` (`--update`/diff, drift-detection proven with an injected table then reverted); wired into `.github/workflows/deploy.yml`'s `gate` job as a "Schema snapshot up to date" step; `scripts/schema_check.py` (read-only `--test`/`--prod` evidence command, mirrors `flyway.sh`'s DSN selection); docs updated (`readme.md`, `CLAUDE.md`, `scripts/flyway.sh` header) to point at the evidence command instead of "check the objects directly". | Ran `scripts/schema_check.py` against both live databases per Step 5 — **both clean**: test (`127.0.0.1:5434`) and prod (`127.0.0.1:5433`) each report `22 tables, 0 missing, 0 extra`, both with Flyway ledger `applied through V5__watchlist (2026-07-28)`. No MISSING/MISMATCH on either side, so no drift to report — `init_schema()` and Flyway agree on both databases today. This is the evidence PR 2's parity test will hold going forward. |
| 2 (Enforcement) | 2026-08-09 | — | `tests/test_schema_parity.py` (7 tests): builds one scratch database from `init_schema()` and another from `db/baseline/V1__*.sql` + every `db/migrations/V*.sql` in parsed-integer version order, asserts `diff_schemas(...) == []` with the full diff as the failure message; `MigrationOrderTests` pins `V10`-after-`V9` ordering and baseline-first contiguity; `SkipGuardTests` pins that the skip path *fails* rather than skips when `CI` is set, so a silently-skipping guard can't pass as a green build. `db/migrations/V6__parity_backfill.sql` resolves the first run's finding under D6. Coverage repair carried over from PR 1: `tests/test_schema_introspect_live.py` (5 live-DB tests) + 4 more cases in `tests/test_schema_introspect.py` lift `quantcore/schema_introspect.py` from 52% → 98%. `sys.path.insert(REPO_ROOT)` preamble added to both PR 1 scripts. Docs: `CLAUDE.md`, `AGENTS.md`, `readme.md` now state the three-file rule as CI-enforced rather than a convention. | The first parity run failed as designed, with exactly three `MISSING table` lines and **zero MISMATCH lines** — see ["What the first parity run found"](#what-the-first-parity-run-found) above for the detail, the D6 resolution, and the three lessons. Two process findings alongside it: PR 1's CI run never actually reached the new "Schema snapshot up to date" step (it aborted earlier at the PR-only `Diff coverage` gate, which the post-merge push run then skips), so the step's first real execution was the `main` run `31336550437` — green, 22 tables; and `scripts/schema_check.py` could not be run by hand at all as shipped (the `PYTHONPATH` gotcha recorded above). |
| 3 (Verify mode) | 2026-08-09 | `6926dd4` (PR #174) | `QUANTCORE_SCHEMA_MODE` in `quantcore/db.py`: `SchemaDriftError`, `SCHEMA_MODES`, `SCHEMA_SNAPSHOT`, `_flyway_managed()` (one `to_regclass('public.flyway_schema_history')` probe), `_resolve_schema_mode()` (read at **call** time, not frozen at import like `DB_DSN`, so `--update-env-vars` and per-test patching both work), `_check_schema()` (diffs live vs `db/schema_snapshot.json`, one greppable summary line + one line per difference, no DDL), and a mode-aware `ensure_schema()` keeping the once-per-process-per-DSN gate in every mode. `auto` → `create` where there is no Flyway ledger, `warn` where there is (PR 4 flips that half to `verify`). Entry points routed onto `ensure_schema()`: `main.py` **and** `fastMCPTest/options_analysis.py`. Tests: `SchemaModeTest` in `tests/test_schema_bootstrap.py` (13 cases, DB-free — the connection is faked and `describe_schema` stubbed, but `diff_schemas` is the real one, so the shipped MISSING/EXTRA/MISMATCH classification is what's under test), covering both `auto` branches, the `create` escape hatch, a typo'd mode, warn-vs-verify on a missing table, verify leaving the DSN unrecorded so a retry re-checks, EXTRA never raising in any mode (D4), the once-per-process gate on the *check* path, a missing snapshot file, and an assertion that no log line carries the DSN. Docs: `CLAUDE.md` (mode table + rationale) and `readme.md` (env-var bullet, three-file table, the `init_schema()`-races-Flyway paragraph rewritten in past tense); `AGENTS.md` deliberately unchanged — it points at `CLAUDE.md` rather than copying it. | Three things the plan did not anticipate. **(a) The plan's "the only remaining direct caller outside tests" claim about `main.py:621` was wrong** — `fastMCPTest/options_analysis.py` called `init_schema()` directly too, and would have kept creating tables on a Flyway-managed database; found by grepping rather than trusting the plan. **(b) `auto` makes the whole test suite environment-dependent.** The local test database carries a Flyway ledger (`scripts/flyway.sh` defaults to test) and CI's throwaway Postgres does not, so `auto` resolves `warn` locally and `create` in CI — a split that changes behaviour for all ~1228 tests and breaks `test_create_app_does_not_re_run_the_ddl` locally only. Pinned `QUANTCORE_SCHEMA_MODE=create` in `tests/__init__.py`, which also let that existing test stay untouched as the plan required. **(c) Two judgment calls the plan left open, both resolved toward *don't block startup*:** an unrecognized mode value logs an ERROR and falls back to `create` (a typo is most likely an operator reaching for the escape hatch mid-incident — failing closed there denies them the very thing they reached for), and a missing snapshot file logs an ERROR in `warn` but raises in `verify` (told to enforce and unable to = fail loudly, never pass silently). Live evidence before merge: against the test DB `auto` resolved `warn` and logged `schema check: mode=auto resolved=warn tables=22 missing=0 mismatch=0 extra=0` with the DDL skipped; read-only `scripts/schema_check.py --prod` is clean (22 tables, 0 missing, 0 extra), so prod should report the same clean line when this rolls out. Noted for Step 4: prod's ledger is still at `V5__watchlist` and has never had PR 2's `V6__parity_backfill.sql` applied — that migration is idempotent no-op DDL and the 0-missing check proves the objects exist, so this is ledger catch-up only, not drift. |
| 3 · Step 4 (soak) | 2026-08-09 | — | Rolled to test by `deploy.yml` (run `31341132233`, all five jobs green) and promoted to prod by `prod-rollout.yml` `workflow_dispatch` with SHA `63ae4dd` (run `31342203483`). Both projects logged **exactly one** `schema check` line and **zero** difference lines: test `quantcore-api-00073-khx` at `23:20:21Z` and prod `quantcore-api-00021-z9c` at `23:38:56Z`, both `mode=auto resolved=warn tables=22 missing=0 mismatch=0 extra=0`. A `textPayload:"schema check:" OR "SchemaDriftError" OR "LockNotAvailable"` sweep across **all** services in both projects returns nothing else. Decision D5's gate is therefore met and PR 4 is unblocked. Prod's ledger is still at `V5__watchlist` — the 0-missing check confirms `V6__parity_backfill.sql`'s objects already exist, so that remains ledger catch-up, not drift. | **The failure this PR exists to prevent had already happened in test, 53 minutes before the fix rolled.** The immediately preceding revision `quantcore-api-00072-62n` (PR #173's deploy) died during startup at `api/main.py:49 ensure_schema()` → `db.py:550 init_schema()` → `cur.execute(stmt)` with `psycopg2.errors.LockNotAvailable: canceling statement due to lock timeout`, and logged no `Application startup complete` in that window — unlike every revision before it going back to 2026-08-02. So the "two owners" problem was not a tidiness argument: an application startup was already losing a lock race against the DDL it was itself running, and turning startup into a read-only check is what removed it. Worth noting for PR 4's risk assessment: a revision that fails its startup check never takes traffic, which is the same containment that applied here — the previous revision kept serving. |
| 4 (The flip) | 2026-08-09 | `1a0be8c` (PR #175) | One line of behaviour: `_resolve_schema_mode()` now returns `verify` (was `warn`) where a Flyway ledger exists, so on test and prod a `MISSING`/`MISMATCH` raises `SchemaDriftError` during startup instead of being logged and ignored. `EXTRA` still never raises (D4), and `auto` still resolves to `create` where no ledger exists, so local dev, CI and compose are untouched. Two tests: `test_auto_enforces_on_a_flyway_managed_database` pins the flip, and `test_extras_never_raise_in_any_mode` now covers `auto` alongside `warn`/`verify`. Docs carry the rest of the PR: `CLAUDE.md`, `readme.md` and `AGENTS.md` all state the four consequences (migrate before deploy **in both projects**, a migration must be complete DDL, failing early is the feature, and the one-command `--update-env-vars QUANTCORE_SCHEMA_MODE=create` escape hatch); `scripts/flyway.sh`'s header loses the "shape converges on startup" claim and gains "migrate before deploying"; the escape hatch and the never-take-traffic rationale are also written into `_resolve_schema_mode()`'s and `ensure_schema()`'s docstrings, where an operator reading the code mid-incident will actually find them. | **The migrate-first step went into [`docs/operations/prod-promotion.md`](../operations/prod-promotion.md), not `prod-rollout-plan.md` as the plan's documentation table said.** `prod-rollout-plan.md` is a finished checkpoint log for the one-time buildout; the live team runbook it points at is `prod-promotion.md`, and a step nobody executes from is a step nobody runs. It is now step 2 of "Promote (the actual procedure)", between "validate on test" and "dispatch the workflow", with `scripts/schema_check.py --prod` as the confirmation and an explicit note that getting the order wrong is contained rather than an outage. Second finding: the plan's Step 2 wording says "`./scripts/flyway.sh --prod migrate` must run before an image carrying a schema change is deployed", which is only half the exposure — `deploy.yml` auto-rolls **test** on every merge to `main`, and test is Flyway-managed too, so an un-migrated merge fails the test revision first. The docs say "both projects" rather than "prod". |
| 4 · test rollout | 2026-08-10 | — | `deploy.yml` run `31346105734` (green) rolled `1a0be8c` onto test `quantcore-api-00074-fk4`. **First `resolved=verify` line ever logged by a deployed revision:** `schema check: mode=auto resolved=verify tables=22 missing=0 mismatch=0 extra=0` at `01:16:59Z`, immediately followed by `Application startup complete`; a second cold start at `02:19:59Z` logged the identical line. A 3-hour `schema check` / `SchemaDriftError` / `LockNotAvailable` sweep across **all** services in `quantcore-test-20260606` returns only those two lines plus PR #174's pre-flip `resolved=warn` line — no drift, no errors, and none of the lock-timeout startup crashes that killed `quantcore-api-00072-62n`. | Only `quantcore-api` emits a check line, and that is correct rather than a gap in the evidence: the MCP wrappers are HTTP gateways that reach the database only through the api (Rule 6), so `ensure_schema()` runs in exactly one place per project. The report Cloud Run Job is the other DB-touching entry point and logs its own line on its next scheduled run. |
| 4 · prod rollout | 2026-08-10 | — | Promoted under the new rule, in its own order: **`./scripts/flyway.sh --prod migrate` first** (bringing prod's ledger from `V5__watchlist` to `V6__parity_backfill` — idempotent no-op DDL, since `init_schema()` had already created all three tables), **then** `prod-rollout.yml` run `31349852531` on `1a0be8c`, green in 6m6s, copying all five images by digest test→prod (api `sha256:8a040fea…`, mcp `e09e8f2c…`, report `bc458d29…`, ui `3bcff344…`, keyproxy `912f6a50…`). Prod `quantcore-api-00022-g29` logged `schema check: mode=auto resolved=verify tables=22 missing=0 mismatch=0 extra=0` at `02:32:35Z`, `Application startup complete` 1.3s later, TCP probe green on the first attempt. A 3-hour sweep for `SchemaDriftError` / `LockNotAvailable` / `Traceback` across **all** prod services returns nothing. Issue #165 is closed in behaviour; only the write-up remains. | The pre-flip evidence is what made this a boring deploy, and it is worth naming: prod's *previous* revision had logged `resolved=warn … missing=0 mismatch=0` at `23:38:56Z` the night before, i.e. the same comparison the new build would enforce, already passing. The flip changed what happens on a mismatch, not whether there was one — so the risk was never "does prod's schema match" but "does the check itself misfire", which the test soak had already answered. **The V6 ledger catch-up was not required for safety**: `verify` diffs live objects against `db/schema_snapshot.json` and never reads `flyway_schema_history`, so a stale ledger cannot raise. It was run because the rule this PR wrote into `AGENTS.md` and `prod-promotion.md` says *migrate before you deploy*, and the first promotion under a new rule is a bad place to make an exception. |
