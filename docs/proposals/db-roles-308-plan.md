# Least-privilege database roles (issue #308)

## Context

Before this change every deployed service, both Jobs and the migrate Job connected as the one
login that owns the schema, `quantcore`, through the same secret `quantcore-<env>-db-dsn`. Any
SQL injection, or a bug in the API, could therefore `DROP` or `ALTER` a table. The migrate Job
(#200) also read the same secret as the app, which is decision D3 in
[`flyway-automation-plan.md`](flyway-automation-plan.md): "shared for now, split later". This is
the split.

## Decisions

| # | Question | Decision |
|---|---|---|
| R1 | Which role owns the schema and migrates? | **The existing `quantcore`**, not a new `quantcore_migrator`. The issue's sketch named a new migrator role. But `quantcore` already owns every object, and on Cloud SQL it is a BUILT_IN user, a member of `cloudsqlsuperuser` and not a real superuser. Moving ownership with `ALTER … OWNER` on 22 tables plus their sequences gains nothing, and a contract migration would have to run as a role nobody uses day to day. |
| R2 | Which role do the services use? | A new **`quantcore_app`**: `LOGIN`, with `SELECT, INSERT, UPDATE, DELETE` on every table in `public` and `USAGE, SELECT` on every sequence. It gets no `TRUNCATE`, `REFERENCES` or `TRIGGER`, no `CREATE` on the schema or the database, and no membership in any role. Default privileges, set by `quantcore`, cover tables that a later migration creates. It can read `flyway_schema_history` but cannot write it. Every run also **revokes** `TRUNCATE`/`REFERENCES`/`TRIGGER` on all tables and `CREATE` on the schema and database, so a grant made by hand before the script ran is repaired rather than left in place, and `verify()` reports any that survive (`has TRUNCATE on watchlist`). |
| R3 | Flyway migration or a script? | **A script, `scripts/ensure_app_db_role.py`.** Roles are cluster-global, not per-database, so a migration would also run in the parity test's scratch databases and in every developer's local Postgres. It would also need a password, which cannot be in git. `_SCHEMA` and `db/schema_snapshot.json` are unchanged, so the parity test stays green by construction. |
| R4 | How does the password stay out of logs? | The script computes a SCRAM-SHA-256 verifier locally (RFC 7677, 4096 iterations) and sends `PASSWORD 'SCRAM-SHA-256$…'`. If the server logs the statement, it logs a verifier, not the password. The DSN for the app secret is built by `--swap-dsn`, a pipe from `gcloud secrets versions access` into `gcloud secrets versions add`, which refuses to write to a terminal. |
| R5 | Secrets | The app's secret keeps its name, `quantcore-<env>-db-dsn`, and gets a new version holding the app role's DSN. Every service and Job already references it as `:latest`, checked in both projects on 2026-10-05, so no deploy config changes. The owner's DSN moves to a new secret, `quantcore-<env>-migrator-dsn`, which only `quantcore-migrate@` can read. `ensure_migrate_job.sh` grants that access, points the Job at the new secret, and then removes the SA's access to the app secret. |
| R6 | What happens to the `QUANTCORE_SCHEMA_MODE=create` escape hatch? | As the app role it can't run DDL, so `ensure_schema()` catches `InsufficientPrivilege`, logs an error and degrades to a `warn` check, rather than failing startup. The documented hatch is now `QUANTCORE_SCHEMA_MODE=warn`, which stops a drift from failing startup. Creating a missing object is a migration, run as the owner. |
| R7 | `.env` | **Unchanged.** Its DSNs stay the owner's, because `scripts/flyway.sh`, `ensure_app_db_role.py` and the import scripts need it. The script refuses to run if the `.env` user is already the app role. |

## Runbook (John: test first, then prod)

Everything here creates a role with a password, creates secrets or changes IAM, so it is the
operator's to run, not CI's or an agent's. `P` is the project and `E` the environment:
`quantcore-test-20260606` with `test`, then `quantcore-prod-20260606` with `prod`. Run it from a
bash shell at the repo root, with the matching proxy running (`./runProxy-MAC.sh --test`, or no
flag for prod).

**Run it with the script.** `scripts/rollout_app_db_role.sh` runs steps 0–5 below in order, so
nothing has to be copied out of this page, then prints steps 6–7:

```bash
./scripts/rollout_app_db_role.sh
```

```bash
./scripts/rollout_app_db_role.sh --prod
```

It is safe to re-run. It reads only the *user name* out of the secrets, never prints a DSN, and
decides from it what is left: if the app secret already connects as `quantcore_app` it only
verifies (`--dry-run`); an existing migrator secret is kept once it is checked to hold
`quantcore`; any other user stops it before a change. If it fails before step 5, fix the cause and
run it again — step 1 sets a fresh password, which nothing uses until step 5. `--rollback` does the
rollback below. `--prod` prompts here, and again in each sub-script.

The commands it runs, for reference:

**Order matters.** Step 2 copies the *current* app secret, which still holds the owner's DSN, so
it must happen before step 5 replaces that secret.

```bash
P=quantcore-test-20260606; E=test

# 0. A password the terminal never shows; both steps below read it from here.
export QUANTCORE_APP_DB_PASSWORD="$(openssl rand -base64 33)"

# 1. Create the role, grant it, verify it, and log in as it (prod: add --prod, which prompts).
python scripts/ensure_app_db_role.py

# 2. The owner's DSN, in its own secret (copied, never displayed).
gcloud secrets versions access latest --secret "quantcore-$E-db-dsn" --project "$P" \
  | gcloud secrets create "quantcore-$E-migrator-dsn" --project "$P" --data-file=-

# 3. Point the migrate Job at it and drop the Job's access to the app secret
#    (prod: --prod; --dry-run first to see the changes), then run a migrate once.
./scripts/ensure_migrate_job.sh --execute

# 4. The migrate execution above must succeed. It is a no-op when nothing is pending.

# 5. The app secret's new version: the same DSN with the app role's user and password.
gcloud secrets versions access latest --secret "quantcore-$E-db-dsn" --project "$P" \
  | python scripts/ensure_app_db_role.py --swap-dsn \
  | gcloud secrets versions add "quantcore-$E-db-dsn" --project "$P" --data-file=-
unset QUANTCORE_APP_DB_PASSWORD
```

6. **Roll.** Secrets referenced as `:latest` are resolved when an instance or a Job execution
   starts. The report and news Jobs pick up the change on their next run. The api picks it up on
   its next revision: the next `deploy.yml` on test, or the next `prod-rollout.yml` on prod. Until
   then, instances still running hold the owner's DSN, which keeps working, so there is no window
   where anything breaks.
7. **Verify.** `python scripts/ensure_app_db_role.py --dry-run [--prod]` reports no problems. The
   new api revision logs `schema check: mode=auto resolved=verify … missing=0 mismatch=0`. In
   `pg_stat_activity` the api's sessions show `usename = quantcore_app`. The next migrate Job
   execution succeeds.

**Rollback** (any time after step 5): put the owner's DSN back as the app secret's newest
version. New instances and executions then use it, and nothing else changes.

```bash
gcloud secrets versions access latest --secret "quantcore-$E-migrator-dsn" --project "$P" \
  | gcloud secrets versions add "quantcore-$E-db-dsn" --project "$P" --data-file=-
```

The role itself can stay in place; it is unused until a secret points at it.

## Gotchas

- **`quantcore` is not a superuser.** On Cloud SQL the BUILT_IN user is a member of
  `cloudsqlsuperuser`. It has CREATEROLE, which is all the script needs, but nothing that requires
  real superuser.
- **On PG16, `ALTER ROLE` naming `SUPERUSER`, `REPLICATION` or `BYPASSRLS` at all, even as
  `NO…`, needs a superuser.** So the create path sets every `NO…` attribute. The re-run path
  (`ALTER`) sets only `LOGIN NOCREATEDB NOCREATEROLE`, and `verify()` reads all five flags from
  `pg_roles` instead of asserting them.
- **`information_schema` shows a role only the tables it has privileges on.** The startup schema
  check introspects through it. If the app role were missing a grant on a table, the check would
  report that table as `MISSING`, and in `verify` mode the api would refuse to start. That is why
  the grant is on *every* table, and why default privileges are part of the change and not an
  extra.
- **Default privileges apply only to objects that the role setting them creates.** They are set
  by `quantcore`, so a table that a migration creates as `quantcore` is covered. One created by
  any other login is not, and `--dry-run` reports it.
- **`create` as the app role** would otherwise fail startup with `InsufficientPrivilege` on the
  first `CREATE TABLE IF NOT EXISTS`, even when every table exists. It now degrades to `warn`
  (R6).
- **Remove the SA's access to the app secret only after the Job reads the migrator secret.** In
  the other order, a migrate execution in between fails on secret access.
  `ensure_migrate_job.sh` does it in that order and stops before changing anything if the migrator
  secret is missing.
- **Granting is not enough on a re-run; the script must revoke too.** The first version only GRANTed and `verify()` only checked for *missing* rights, so a role that had been given `TRUNCATE` by hand (or by an earlier experiment) passed verification. Revoking a privilege the role doesn't hold is a no-op, so the REVOKEs are safe on every run. PostgreSQL 16 has no `MAINTAIN` privilege, so it isn't in the list; add it if the instance moves to 17.
- **PostgreSQL does not evaluate `WHERE` conditions in the order they are written.** The sequence
  check was `relkind = 'S' AND NOT has_sequence_privilege(role, oid, 'USAGE')`, and Cloud SQL test
  ran the function on a table first: `"watchlist" is not a sequence`, and step 1 failed (it rolled
  back, so nothing changed). CI's `postgres:16` happened to plan it the safe way, so the DB test
  passed. Only `CASE WHEN relkind = 'S' THEN … ELSE false END` guarantees the order;
  `has_table_privilege` takes any relation and needs no guard.
- **Local DB tests are blocked by Postgres.app's trust auth.** The DB-backed test
  (`DatabaseTests` in `tests/test_ensure_app_db_role.py`) skips locally and fails rather than
  skips in CI, where the `postgres:16` service uses scram auth and a superuser.

## Checkpoint log

| Step | Date | Commit | What landed | Notes |
|---|---|---|---|---|
| 1. Code + docs | 2026-10-05 | [#325](https://github.com/JohnFunkCode/StockPortfolioManager/pull/325) | `create` degrades to `warn` on `InsufficientPrivilege` (`quantcore/db.py`, 2 tests in `test_schema_bootstrap.py`). `ensure_migrate_job.sh --migrator-secret` (default `quantcore-<env>-migrator-dsn`): fails before any change if the secret is absent, grants the SA access to it, points the Job's `QUANTCORE_DB_DSN` at it, then removes the SA's binding on the app secret (19 tests). `scripts/ensure_app_db_role.py` plus `tests/test_ensure_app_db_role.py`: the SCRAM verifier, `--swap-dsn`, the statement plan, and a DB test that logs in as a scratch role and is refused DDL and ledger writes. Docs: CLAUDE.md, AGENTS.md, readme, `flyway.sh` header, `prod-access-grants.md`, and pointers from the flyway-automation and schema-ownership plans. | Nothing applied to either project. Steps 1–7 of the runbook are John's. |
| 1a. Review fixes | 2026-10-05 | [#325](https://github.com/JohnFunkCode/StockPortfolioManager/pull/325) | Guppy review on PR #325: the plan now REVOKEs excess table, schema and database rights and `verify()` reports them (R2). `main()` and `verify()` split into helpers: radon cyclomatic `main` D(21)→A(5), `verify` C(14)→max B(7) in `_one_table_problems`; complexipy cognitive `main` 23→4, `verify` 17→max 6. 17 new unit tests (fake cursor/conn) for `verify`'s helpers and `main`'s steps; the DB test now pre-grants `TRUNCATE`/`REFERENCES`/`TRIGGER` and schema `CREATE`, checks `verify()` reports them, then checks the plan repairs them. | The review's "applied on both projects" criterion is rows 2–3, the operator runbook. |
| 1b. Runbook script | 2026-10-05 | [#326](https://github.com/JohnFunkCode/StockPortfolioManager/pull/326) | `scripts/rollout_app_db_role.sh [--prod] [--rollback]`: steps 0–5 in one script, after John hit copy/paste problems with the bash block. Guards on the secrets' user names (read with `sed`, never printed); 12 tests in `tests/test_rollout_app_db_role.py` against a stub `gcloud` whose secrets are files. | Gotcha in the stub, not the script: `access | swap | versions add` on the **same** secret raced when the stub's `add` was `cat > file` — it truncated the file before `access` read it. Real Secret Manager versions are immutable, so the stub writes then renames. |
| 1c. Sequence-check fix | 2026-10-05 | [#326](https://github.com/JohnFunkCode/StockPortfolioManager/pull/326) | John's first test run failed in step 1 with `WrongObjectType: "watchlist" is not a sequence`. `_sequence_problems` now guards `has_sequence_privilege` with `CASE` (see Gotchas), with a regression test on the query; the script's step-1 message no longer blames only the proxy. | The failed run changed nothing: role creation and verify are one transaction, and no secret step had run. |
| 2. Test rollout | 2026-10-05 | `d6b8721` | **Done on test.** First run (after 1c): steps 0–5 succeeded (migrate `quantcore-migrate-ggjpg`, app secret v2), then rolled back with `--rollback` (v3 = owner) because #325 had merged without the script and the 1c fix, which went in via #326. Second run from main: role updated and verified, migrator secret kept, migrate `quantcore-migrate-knjcc` succeeded, app secret v4 = `quantcore_app` (21:58Z). Step 6: `deploy.yml` dispatched from main (run 37379467307): migrate 22:08–22:11Z, then phase 1 (api revision `quantcore-api-00142-2wf`, created 22:12Z, `QUANTCORE_DB_DSN` → `quantcore-test-db-dsn:latest`), then phase 2. Step 7: verify-only rerun exit 0; api log `schema check: mode=auto resolved=verify tables=23 missing=0 mismatch=0 extra=0`; `/api/health` `db_connected: true`; no ERROR logs. | The api picks up a new secret version only on a new revision, so the rollout has to land *before* the deploy that rolls the api, not after it: the #326 deploy ran before the second run and still connected as the owner. Migrate executions take about 4 min to start. You can't see the api's user in `pg_stat_activity`: it opens a connection per call and closes it, and Cloud SQL test does not log connections. The proof is the secret version, the revision's reference to it, and the creation times. |
| 3. Prod rollout | 2026-10-05 | `54a86b0` | **Done on prod.** Promoted main first (`prod-rollout.yml` run 37382364748, migrate `quantcore-migrate-ct997`). Then John ran the script: role created, `quantcore-prod-migrator-dsn` created, migrate `quantcore-migrate-4vtkz` succeeded, app secret v3 = `quantcore_app` (22:47:57Z). Step 6: `prod-rollout.yml` again (run 37385038812): migrate `quantcore-migrate-989pl` (as the owner) succeeded, then api revision `quantcore-api-00037-bn9` (created 22:59:55Z, `QUANTCORE_DB_DSN` → `quantcore-prod-db-dsn:latest`). Step 7: api log `schema check: mode=auto resolved=verify tables=23 missing=0 mismatch=0 extra=0`; `/api/health` `db_connected: true`; no ERROR logs from the api or any Job; verify-only rerun: role exists, nothing changed. | Promote **before** running the script, then roll the api once more: on prod both use the `quantcore-migrate` Job, so they must not overlap, and the promotion that brings the script's code rolls the api too early to pick up the secret. On prod, `ensure_app_db_role.py` asks for a second `yes` (step 1), after the script's own prompt. In the Actions UI, "Run workflow" must be clicked twice: the first dispatch after the script never started a run. |
