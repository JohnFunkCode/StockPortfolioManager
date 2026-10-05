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
| R2 | Which role do the services use? | A new **`quantcore_app`**: `LOGIN`, with `SELECT, INSERT, UPDATE, DELETE` on every table in `public` and `USAGE, SELECT` on every sequence. It gets no `TRUNCATE`, `REFERENCES` or `TRIGGER`, no `CREATE` on the schema or the database, and no membership in any role. Default privileges, set by `quantcore`, cover tables that a later migration creates. It can read `flyway_schema_history` but cannot write it. |
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
- **Local DB tests are blocked by Postgres.app's trust auth.** The DB-backed test
  (`DatabaseTests` in `tests/test_ensure_app_db_role.py`) skips locally and fails rather than
  skips in CI, where the `postgres:16` service uses scram auth and a superuser.

## Checkpoint log

| Step | Date | Commit | What landed | Notes |
|---|---|---|---|---|
| 1. Code + docs | 2026-10-05 | _this PR_ | `create` degrades to `warn` on `InsufficientPrivilege` (`quantcore/db.py`, 2 tests in `test_schema_bootstrap.py`). `ensure_migrate_job.sh --migrator-secret` (default `quantcore-<env>-migrator-dsn`): fails before any change if the secret is absent, grants the SA access to it, points the Job's `QUANTCORE_DB_DSN` at it, then removes the SA's binding on the app secret (19 tests). `scripts/ensure_app_db_role.py` plus `tests/test_ensure_app_db_role.py`: the SCRAM verifier, `--swap-dsn`, the statement plan, and a DB test that logs in as a scratch role and is refused DDL and ledger writes. Docs: CLAUDE.md, AGENTS.md, readme, `flyway.sh` header, `prod-access-grants.md`, and pointers from the flyway-automation and schema-ownership plans. | Nothing applied to either project. Steps 1–7 of the runbook are John's. |
| 2. Test rollout | | | | |
| 3. Prod rollout | | | | |
