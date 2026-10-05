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
| D4a | Non-transactional migrations (`CREATE INDEX CONCURRENTLY`, `VACUUM`, …)? | **Block them the same way.** See below. Added after review on PR #301. |
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

### Why non-transactional migrations stay manual too (D4a)

The automated path is safe to fail only because a failing migration rolls back whole. That holds
for ordinary Postgres DDL, but **not** for statements that refuse to run inside a transaction:
`CREATE INDEX CONCURRENTLY` / `DROP INDEX CONCURRENTLY` / `REINDEX … CONCURRENTLY`, `VACUUM`,
`ALTER SYSTEM`, `CREATE DATABASE` / `DROP DATABASE`.

- `db/flyway.conf` does not set `flyway.mixed`, so it defaults to false. Flyway therefore already
  **rejects** a migration that mixes these with transactional statements. **Keep it that way.**
- A migration made **only** of such statements is still run by Flyway, outside a transaction. If it
  fails, it can leave partial effects behind. A failed `CREATE INDEX CONCURRENTLY`, for example,
  leaves an `INVALID` index that must be dropped before a retry, and the Flyway history shows a
  `Failed` row that needs `flyway repair`.

So the pre-migrate check also refuses any pending migration that contains one of those statements.
The release is applied by hand, the same way as a contract migration. Recovery from a partial
failure on that path is: inspect (`\d <table>`, look for `INVALID`), drop the leftover object, run
`flyway repair`, and re-run. Nothing in the repo uses these statements today.

## Design

### The migration image: `quantcore-migrate`

- **`Dockerfile.migrate`:** `FROM flyway/flyway:13.8.0@sha256:…`, the same version as the local
  CLI, pinned by its multi-arch index digest. It adds two **junixsocket** 2.11.1 jars
  (`junixsocket-common`, `junixsocket-native-common`), pinned by version and sha256 and verified
  with `sha256sum -c` in a fetch stage, into `/flyway/drivers/`. It then copies `db/flyway.conf`,
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
   - For a socket, `FLYWAY_URL` is
     `jdbc:postgresql://localhost/<db>?socketFactory=org.newsclub.net.unix.AFUNIXSocketFactory$FactoryArg&socketFactoryArg=/cloudsql/<conn>/.s.PGSQL.5432`.
     The Job's Cloud SQL attachment already provides that socket.
   - **JDBC cannot use the libpq `host=/cloudsql/...` socket form directly.** It needs a socket
     factory. The plan first named Google's Cloud SQL socket factory, but that ships no single jar
     with its dependencies. junixsocket only opens the unix socket the attachment already
     provides, which needs fewer moving parts (see the checkpoint log).
3. It prints **only** `migrate: target cloudsql:<conn>/<db> (user: <user>)` (or `host:port/db`),
   never the DSN or the password. Flyway's own output names the JDBC URL, which carries no credentials.
4. It runs `flyway info` and checks the pending versions' files for contract statements (D4) and
   non-transactional statements (D4a). If either is present, it refuses with **exit 3** and prints
   the manual path. The scan strips `--` and `/* */` comments and `'...'` strings first, so a
   keyword there does not refuse. A dollar-quoted body is scanned, because a `DO` block executes.
5. It runs `flyway migrate`. Because of step 4, every migration that reaches this point is
   transactional DDL that Flyway runs in its own transaction, so a failing version rolls back whole
   and leaves nothing half-applied. This guarantee rests on the step 4 refusal. It is not true of
   Postgres DDL in general. Flyway also takes a Postgres advisory lock, so two runs against one
   database serialize.
6. It runs `flyway info` again, so the log ends with the resulting state.

### The Job: `quantcore-migrate`, one per project

- **Created once by hand**, like every other first deploy here, using
  `scripts/ensure_migrate_job.sh [--prod]`, modelled on `ensure_news_job.sh`. The script:
  - creates a dedicated SA `quantcore-migrate@` with `roles/cloudsql.client` and
    `secretmanager.secretAccessor` on the DSN secret only;
  - creates the Job with the Cloud SQL attachment copied from `quantcore-report`;
  - sets the DSN secret, `--task-timeout 600s` and `--max-retries 0`;
  - grants the CI deployer `roles/iam.serviceAccountUser` on the new SA (next bullet);
  - on a Job that already exists, re-asserts the same shape with `--update-secrets` and
    `--add-cloudsql-instances`, never `--set-*`, and leaves the image alone unless one is given.

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
   `gcloud logging read`, then emit one `::error::`: `migration refused` (the entrypoint's
   exit 3, so the manual path applies) or `migration failed` (fix forward). The deployer gets
   `roles/logging.viewer` from `ensure_migrate_job.sh`. Reading the logs is best effort: if it
   fails, the step still fails for the migration and prints the console URL instead.

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
| Contract or non-transactional migration pending | Nothing applied, no roll-out | `./scripts/flyway.sh [--prod] migrate` by hand, then re-run the workflow. For a non-transactional failure on that path, see D4a's recovery |
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
     and non-transactional checks' matches and non-matches (including `CONCURRENTLY` inside a
     comment or a string not tripping it, if the scanner strips them); `db/flyway.conf` not
     setting `flyway.mixed=true`.
2. **Local proof against local Postgres:**
   - Build the image and run it against a scratch database: from empty to V10; a no-op re-run; a
     representative new expand migration; a deliberately broken one (non-zero exit, nothing
     half-applied); a contract migration (refused); a
     `CREATE INDEX CONCURRENTLY`-only migration (refused).
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
   and the first prod run observed. The prod migrate step passes `quantcore-migrate@sha256:…`, the
   digest the promotion copied, never a tag, and a wiring test checks that (PR #305 review).
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
| Review on PR #301 | _this PR_ | — | "Each migration runs in a transaction" is not true of `CONCURRENTLY`/`VACUUM`-style statements, so those are refused too (D4a) |
| 1–2. Image, entrypoint, tests, proof | _this PR_ | **Local**, using a flyway 13.8.0 CLI and a scratch cluster over a unix socket: once V1 was in place, V2–V10 were refused with exit 3 and nothing applied; the manual `flyway migrate` applied them; a re-run reported "nothing pending" and exited 0; an expand V11 with `DROP`/`CONCURRENTLY` only in comments or strings was applied; a broken V12 exited 1 and was rolled back (no table, no `Failed` row); a contract V12 and a `CONCURRENTLY` V12 were each refused with exit 3, nothing applied; the TCP DSN form worked too. **Cloud Build** (build `5076581c`, test project, trial tag, not pushed): the image ran against `postgres:16` through a socket mounted at `/cloudsql/proj:us-central1:inst`, with the same refusal → manual → no-op sequence; bash 5.2 and mawk 1.3.4 are present; the password never appeared in output. 20 unit tests in `tests/test_migrate_entrypoint.py` | (a) Google's Cloud SQL socket factory has no fat jar (its transitive dependencies run to dozens of jars), so junixsocket is used instead. (b) The socket-factory jar must be on Flyway's **main** classpath (`/flyway/drivers`, or `CLASSPATH` for a local CLI). `-jarDirs` loads it in a child loader that the Postgres driver cannot see, so it fails with `ClassNotFoundException`. (c) macOS caps AF_UNIX paths at 104 bytes, so a socket directory under the scratchpad is too long; use a short path such as `/tmp/claude/pgs`. (d) Postgres.app gated trust auth behind a GUI prompt, so the local proof used its own `initdb` cluster rather than changing Postgres.app settings. (e) `db/flyway.conf`'s locations have no V1, so an **empty** database needs `db/baseline/V1__*.sql` applied by psql first; deployed databases are non-empty and get `baselineOnMigrate`. (f) The scanner flags the already-applied V2, V4, V7 and V10, which is correct and harmless, because only *pending* files are scanned. (g) The entrypoint is sourced by tests under the local macOS bash, which is 3.2, so it avoids `mapfile` and never expands an empty array under `set -u`. (h) `flyway info -outputType=json` sorts keys, so `filepath` comes before `state` in each entry. The first parser relied on that; since the #303 review it decides at the end of each entry, so key order no longer matters |
| Merge of `main` after #302 | _this PR_ | `build-migrate` converted to #302's buildx form: `--builder quantcore`, registry cache at `quantcore-migrate:buildcache-${_CACHE_TAG}` (`mode=max`), `--provenance=false`, `--push`. Its `images:` entries went with the block, and `tag-latest` now moves `quantcore-migrate:${_LATEST_TAG}` and waits for `build-migrate`. `check_cloudbuild.py` passes | A new image now needs **three** edits in `cloudbuild.yaml`: the build step, a `move` line, and `tag-latest`'s `waitFor`. The checker catches a missing one |
| #303 review | _this PR_ | `pending_files` now decides at the end of each migration object, so it no longer depends on Flyway's key order; tests added for two pending files (in `info` order) and for `state` written before `filepath` (22 entrypoint tests). The `jars` fetch stage is pinned by the `python:3.12-slim` index digest, like the runtime base. CLAUDE.md's description of `check_cloudbuild.py` updated: it no longer checks `images:` but the `tag-latest` set | Both reviews' REQUEST_CHANGES were about #200's unmet criteria (the Job and workflow steps), which are Steps 3–5 by design. The PR does not close #200 |
| 3. `ensure_migrate_job.sh` | _this PR_ | Script written: creates `quantcore-migrate@` (cloudsql.client on the project, secretAccessor on the DSN secret only, serviceAccountUser for `quantcore-deployer@`), then creates the Job or updates it in place. The Cloud SQL instance and the `QUANTCORE_DB_DSN` secret reference are read from `quantcore-report`; the DSN value is never read. `--tag`/`--image`, `--execute`, `--dry-run`, `--prod` (prompts). 13 tests in `tests/test_ensure_migrate_job.py` against a stub `gcloud`. **Run on test 2026-10-04** by John (`--tag 85195d8 --execute`, the #304 merge's CI-built image): SA, grants and Job created; execution `quantcore-migrate-5kbr9` exited 0 with `migrate: nothing pending`, schema version 10, target logged as `cloudsql:…/quantcore`, no credentials in the log. Start-up took ~2m14s (image pull on a first execution) | Copying the report Job's secrets wholesale, as `ensure_news_job.sh` does, would hand the migrate Job the Discord webhook and the JWT key too, so only `QUANTCORE_DB_DSN` is copied and only that secret is granted. `gcloud run jobs update` has no `--update-cloudsql-instances`; `--add-cloudsql-instances` is the additive form. For prod, the image must be in the prod AR before the Job can be created: copy it by digest first (as `prod-rollout.yml` does), since Step 5's promotion needs the Job to exist. Every check that can refuse the run (here, "no image to create the Job with") must come before the first mutation; the first draft checked after the SA and grants, so a bad invocation left IAM behind (PR #304 review) |
| 4. `ci_migrate.sh` and the `deploy.yml` step | _this PR_ | `scripts/ci_migrate.sh --project --region --image`: fails (never skips) if the Job is missing, updates `--image` only, `execute --wait`; on failure prints the newest execution's log lines in a `::group::` and one tailored `::error::`. New `deploy.yml` step "Migrate the database" between the Cloud Build step and the roll-out, with `quantcore-migrate:${GITHUB_SHA::7}`. `ensure_migrate_job.sh` now also grants the deployer `roles/logging.viewer`. 9 tests in `tests/test_ci_migrate.py` (stub `gcloud` plus the `deploy.yml` wiring). **Failure-path proof on test, 2026-10-04:** two throwaway images (Cloud Build, tags `failproof-200` and `refuseproof-200`, never `:latest`; their V11 files were never committed) each run through `ci_migrate.sh` against the test Job. A V11 that creates a table and then divides by zero: execution `quantcore-migrate-w8f54` failed with SQL state 22012, the script printed the Flyway log in reading order and `::error title=migration failed::`, exit 1, ~2m54s. A V11 with `DROP TABLE`: execution `quantcore-migrate-jj4rj` printed `migrate: REFUSED … contract: DROP`, container exit 3, `::error title=migration refused::`, exit 1, ~1m31s. Then the Job was pointed back at `85195d8` (`quantcore-migrate-r6wlj`: `nothing pending`, schema version 10), and a direct query showed no `ci_failure_proof_200` table and no version-11 row in `flyway_schema_history`, so the failed version rolled back whole. Both log reads succeeded on the first try. | **Merge-ordering hazard:** once this merges, every test deploy fails at the migrate step until the Job exists on test, so `ensure_migrate_job.sh --tag <trial> --execute` must run on test **before** the merge. `gcloud run jobs execute --wait` doesn't print the execution name on failure in a parseable form, so the script takes the newest from `executions list --sort-by=~metadata.creationTimestamp` (safe because the `concurrency:` group serializes deploys). Cloud Logging can lag an execution by seconds, so an empty read is reported as such, not as "no output". `logging read --order desc` gives the newest lines, reversed with `sed -n '1!G;h;$p'` because macOS has no `tac`. The review asked for the migrate image by digest rather than by the `${GITHUB_SHA::7}` tag. That was deferred to Step 5 (prod promotion by digest), because on test only this repo's Cloud Build writes that tag, for that same commit, and the roll-out deploys every service by the same tag. A digest looked up after the build would race a tag move just as the tag does |
