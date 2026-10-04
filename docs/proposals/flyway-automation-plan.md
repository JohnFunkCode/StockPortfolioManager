# Plan: automated Flyway migrations as a gated deploy step (issue #200)

**Status:** decisions agreed (2026-10-04). Implementation not started. This doc is the decision
record and the checkpoint log.

## Context

Since #165 (see [`schema-ownership-plan.md`](schema-ownership-plan.md)), Flyway is the only thing
that writes DDL to the test and prod databases. `QUANTCORE_SCHEMA_MODE=auto` resolves to `verify`
there, so a new image whose schema has not been migrated fails its health check with
`SchemaDriftError`. Migrating is therefore a mandatory **manual** step today:

- `./scripts/flyway.sh migrate` before merging to `main`, because `deploy.yml` auto-rolls test.
- `./scripts/flyway.sh --prod migrate` before dispatching `prod-rollout.yml`.

Neither workflow contains a migration step. Both run it from a developer's checkout through the
Cloud SQL Auth Proxy, so nothing ties the migrations applied to the image that gets deployed.

Decision D1 of #165 kept migrations manual for one reason: `deploy.yml` holds no database
credentials, and running migrations in CD seemed to need them. This design keeps that property.
**CI never sees the DSN.** The migration runs as a Cloud Run Job, which reads the DSN from Secret
Manager the same way the report Job does. CI only updates the Job's image and waits for it.

## Decisions (agreed with John, 2026-10-04)

| # | Question | Decision |
|---|---|---|
| D1 | Who approves a prod migration? | The **existing** `environment: prod` approval on `promote-and-deploy`. Migration runs inside that job, so one approval covers migrate and roll-out. There is no separate migrate gate. |
| D2 | Migrate on every release, or only when pending? | **Every release.** `flyway migrate` with nothing pending is a no-op costing about a minute of Job start-up. The path is exercised on every deploy, so it can't rot between schema changes. |
| D3 | Dedicated migration DB role? | **Shared for now, split later.** The Job uses the same DSN secret as the app, because that role owns every object and only an owner can `ALTER`. It runs as its **own runtime service account**. The least-privilege split is a follow-up issue: `ALTER OWNER` to a migrator role, and remove DDL rights from the app role. |
| D4 | Contract migrations (DROP / RENAME / type change)? | **Block the automated run; use the manual fallback.** See below. |
| D5 | Rollback | **Forward-fix only.** See Failure and recovery. |
| D6 | Order with #120 (deploy an arbitrary ref to test) | **#200 first.** #120 then inherits a migrate step that already uses the image built from the dispatched ref. |

### Why contract migrations stay manual (D4)

Migration runs **before** the new revision rolls out. The previous revision therefore has to keep
working on the migrated schema. Two cases:

- **Expand** migrations (add a table, column or index): the old image's snapshot reports the new
  object as `EXTRA`, which only warns. This is safe.
- **Contract** migrations (drop or rename something the old snapshot expects): the old image would
  report it `MISSING`. Instances that are already running keep serving. But if the roll-out then
  fails, the prior revision **cannot cold-start**, because `verify` refuses. That breaks the
  acceptance criterion "leaves the prior revision serving".

The parity rule (`_SCHEMA` == baseline + migrations, in one commit) means a release can't ship
the code half of a contract a release ahead of the drop. So the Job checks before it migrates:
- If any **pending** migration contains `DROP`, `RENAME` or `ALTER … TYPE`, it refuses with a
  clear message and a non-zero exit.
- That release is migrated by hand with `./scripts/flyway.sh [--prod] migrate`, with someone
  watching. The re-run of the workflow then finds nothing pending and proceeds.

Contract migrations are rare (V10 is the only one so far). Expand migrations, the common case,
are fully automatic.

## Design

### The migration image: `quantcore-migrate`

- **`Dockerfile.migrate`:** `FROM flyway/flyway:<pinned>`, the same major version as the local
  CLI (13.8.0 when this was written; check the tag exists when building). It adds the Cloud SQL
  Postgres JDBC socket factory jar, pinned by version and sha256, then copies `db/flyway.conf`,
  `db/migrations/` and the entrypoint. The migrations are **baked in**, so the image *is* the
  exact migration set for that commit.
- **Build:** a 7th step in `cloudbuild.yaml` with the standard cache wiring. `check_cloudbuild.py`
  enforces it, and the image is tagged `${_TAG}` like the others.
- **Prod promotion:** `quantcore-migrate` joins the digest-copy loop in `prod-rollout.yml`, so prod
  migrates with the **same bytes** test did, bound to the promoted SHA.

### The entrypoint: `db/migrate-entrypoint.sh`

1. It reads `QUANTCORE_DB_DSN`. This is the unix-socket form
   `postgresql://user:***@/db?host=/cloudsql/<conn>`. It also accepts a `host:port` form, for local
   tests.
2. It translates the DSN into `FLYWAY_USER`, `FLYWAY_PASSWORD` and `FLYWAY_URL`, with the password
   URL-decoded:
   - For Cloud SQL, `FLYWAY_URL` is `jdbc:postgresql:///<db>?cloudSqlInstance=<conn>&socketFactory=com.google.cloud.sql.postgres.SocketFactory`.
   - **JDBC cannot use the libpq `host=/cloudsql/...` socket form directly.** That is why the socket
     factory is needed.
3. It prints **only** `target: cloudsql:<conn>/<db>` (or `host:port/db`), never the DSN or the
   user's password. Flyway's own output names the JDBC URL, which carries no credentials.
4. It runs `flyway info` and runs the contract check (D4) on the pending versions' files.
5. It runs `flyway migrate`. Each Postgres migration runs in its own transaction. A failing version
   therefore rolls back whole and leaves nothing half-applied. Flyway also takes a Postgres advisory
   lock, so two runs against one database serialize.
6. It runs `flyway info` again, so the log ends with the resulting state.

### The Job: `quantcore-migrate`, one per project

- **Created once by hand**, like every other first deploy here, using
  `scripts/ensure_migrate_job.sh [--prod]`, modelled on `ensure_news_job.sh`. The script:
  - creates a dedicated SA `quantcore-migrate@` with `roles/cloudsql.client` and
    `secretmanager.secretAccessor` on the DSN secret only;
  - creates the Job with the Cloud SQL attachment copied from `quantcore-report`;
  - sets the DSN secret, `--task-timeout 600s` and `--max-retries 0`.

  **John runs it**, because it creates IAM.
- **The CI deployer SA needs `roles/iam.serviceAccountUser` on `quantcore-migrate@`** in both
  projects. This is the same gotcha as `keyproxy-runtime@` in the BYOK rollout.
- `--max-retries 0`, because a failure is deterministic (bad SQL, drift, refused contract). An
  automatic retry adds nothing and makes the log harder to read.

### Workflow wiring (both workflows, kept in agreement)

A new step **between** the image step and the roll-out step, using a shared
`scripts/ci_migrate.sh`:

1. `gcloud run jobs update quantcore-migrate --image <tag or digest>`
2. `gcloud run jobs execute quantcore-migrate --wait`
3. If the execution failed, print the execution's last log lines into the step from
   `gcloud logging read`, then emit `::error title=migration failed::`. The deploy SAs need
   `roles/logging.viewer` for this, to be confirmed.

If the Job does not exist yet, the step **fails**. It does not skip with a warning, unlike the
guards in #163. A silent skip here would recreate exactly the "image ahead of schema" failure this
issue removes.

The step fails the job, so the roll-out step, phase 1 included, never starts. The previous
revisions keep serving on an unchanged schema. The existing `concurrency:` groups serialize runs,
and Flyway's advisory lock covers anything else, such as an operator running `flyway.sh` at the
same time.

### Failure and recovery (D5)

| Failure | State afterwards | Recovery |
|---|---|---|
| A migration's SQL fails | That version rolled back (it ran in a transaction); earlier versions in the same run stay applied; no roll-out | Fix forward: a corrected **new** commit. Never edit an applied version. `flyway repair` only from the manual path, and only if `flyway info` shows a `Failed` row |
| Contract migration pending | Nothing applied, no roll-out | `./scripts/flyway.sh [--prod] migrate` by hand, then re-run the workflow |
| Roll-out fails after a successful (expand) migration | New schema, old revisions serving; they see the new objects as `EXTRA` | Fix forward, re-deploy. No schema rollback |
| Job missing or the deployer lacks permission | Nothing applied, no roll-out | Run `ensure_migrate_job.sh`, or grant the role |

There are no down-migrations. Flyway OSS has no undo, and expand-only automation makes them
unnecessary.

### What stays

- **`scripts/flyway.sh`** stays as the operator fallback, and remains the only path for contract
  migrations. Its header and the docs stop calling it a mandatory pre-merge step.
- **`QUANTCORE_SCHEMA_MODE`** is unchanged. `verify` is the backstop if a migration somehow didn't
  happen.

## Steps

1. **Image and entrypoint:**
   - `Dockerfile.migrate`, `db/migrate-entrypoint.sh`, the 7th `cloudbuild.yaml` step, and
     `.dockerignore` if needed.
   - Unit tests (bash subprocess, like `test_ci_parallel.py`) for: DSN translation in both forms;
     URL-decoding of the password; the target line never containing the password; the contract
     check's matches and non-matches.
2. **Local proof against local Postgres:**
   - Build the image and run it against a scratch database: from empty to V10; a no-op re-run; a
     representative new expand migration; a deliberately broken one (non-zero exit, nothing
     half-applied); a contract migration (refused).
3. **`scripts/ensure_migrate_job.sh` plus a one-time test-project setup (John runs it):**
   - Execute it once on test with a trial-tag image. Never `:latest`.
   - Expect "Schema is up to date" and a log free of credentials.
4. **`scripts/ci_migrate.sh` and the `deploy.yml` step:**
   - Wiring tests next to `test_ci_parallel.py`: the step sits between image and roll-out; it is
     `jobs execute --wait`; no `--set-*`.
   - Prove the failure path on test: execute the Job against a scratch database with a trial
     image carrying a broken migration, and check that the workflow step fails before the
     roll-out.
5. **`prod-rollout.yml`:** the digest promotion, the migrate step, a one-time prod setup (John),
   and the first prod run observed.
6. **Docs:**
   - The CLAUDE.md Migrations section ("migrate before merge" becomes "CI migrates; manual for
     contract migrations").
   - readme Migrations; the `flyway.sh` header.
   - A pointer in `schema-ownership-plan.md`, because D1 there assumed CD would need credentials.
7. **Follow-up issue:** the dedicated migrator role (D3).

## Checkpoint log

| Step | Commit | Result | Gotcha |
|---|---|---|---|
| Decisions D1–D6 agreed | _this PR_ | — | The libpq unix-socket DSN can't be used by JDBC as is: it needs the Cloud SQL socket factory. Contract migrations conflict with `verify` on the prior revision (D4) |
