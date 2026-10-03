# Setting up a local unit-test database

The backend test suite runs in about a minute against a PostgreSQL server on your own machine.
Against the Cloud SQL test instance it takes about 30 minutes, because every query crosses the
Cloud SQL Auth Proxy at roughly 29 ms per round trip (issue #289). CI already runs against a
throwaway local `postgres:16`, and this setup gives you the same thing.

You do this once per machine. It takes about five minutes.

## What you need

- A local PostgreSQL 16 server, such as Postgres.app, listening on `localhost:5432`.
- `psql` on your `PATH`, connecting as your macOS user. Postgres.app makes that user a superuser.

Check both:

```bash
psql --version
```

```bash
psql -h localhost -d postgres -c 'select current_user, version();'
```

If the second command asks for a password you don't know, or fails with
`password authentication failed`, see [Troubleshooting](#troubleshooting).

## 1. Create the role and the database

The suite connects as a `quantcore` role, which must have **`CREATEDB`**.
`tests/test_schema_parity.py` builds two scratch databases on every run and fails without it.

Open `psql` as your superuser:

```bash
psql -h localhost -d postgres
```

Then, at the `postgres=#` prompt:

```sql
-- Skip this if the role already exists; run the ALTER below instead.
CREATE ROLE quantcore LOGIN CREATEDB;
-- Or, if it already exists from earlier local work:
ALTER ROLE quantcore LOGIN CREATEDB;

-- Prompts twice; the password is not echoed or written to history.
\password quantcore

CREATE DATABASE quantcore_test OWNER quantcore;
\q
```

Pick a password used only for this local database. Don't reuse the Cloud SQL password.

`\du quantcore` lists the role's attributes, so you can confirm `Create DB` is set.

## 2. Point the suite at it

Add one line to the repo's `.env`, next to the existing `QUANTCORE_TEST_DB_DSN`:

```
QUANTCORE_UNITTEST_DB_DSN=postgresql://quantcore:<your-local-password>@localhost:5432/quantcore_test
```

Leave `QUANTCORE_TEST_DB_DSN` exactly as it is. It still means "the Cloud SQL test instance" to
`scripts/flyway.sh`, `scripts/with-test-db.sh`, `scripts/schema_check.py` and the import scripts.
Only the unit suite reads the new key.

You don't need a Flyway step or a schema load. The suite creates all 22 tables on first connection
(it pins `QUANTCORE_SCHEMA_MODE=create`).

## 3. Check which database the suite will use

This prints the target as `host:port/name` and never shows the password:

```bash
python -c "import os, tests; from quantcore.db import describe_dsn; print(describe_dsn(os.environ['QUANTCORE_DB_DSN']))"
```

You should see `localhost:5432/quantcore_test`. If you see `127.0.0.1:5434/quantcore`, the new key
was not picked up: check its spelling, and check that the line isn't commented out.

## 4. Run the suite

```bash
python -m unittest discover -s tests -t . --durations 25
```

`--durations 25` ends the run with the 25 slowest tests. On a local database a full run should
take a minute or two. If it takes many minutes, run step 3 again.

## Running against Cloud SQL test instead

You can send one run to Cloud SQL test without editing `.env`. Start the test proxy
(`./runProxy-MAC.sh --test`), then:

```bash
QUANTCORE_UNITTEST_DB=cloudsql python -m unittest discover -s tests -t .
```

If you remove `QUANTCORE_UNITTEST_DB_DSN` from `.env`, every run goes to Cloud SQL test.

## Resetting

The database is disposable. Test modules purge their own synthetic rows, but if an interrupted
run leaves the database in a strange state, recreate it:

```bash
dropdb -h localhost quantcore_test && createdb -h localhost -O quantcore quantcore_test
```

## Safety

`quantcore/db_safety.assert_not_production()` refuses to run when the effective DSN matches the
production `QUANTCORE_DB_DSN` in `.env` (same host, port and database). A local DSN passes that
check, and production is still refused. Never set `QUANTCORE_UNITTEST_DB_DSN` to anything other
than a database on your own machine.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `password authentication failed for user "<you>"` from `psql -h localhost` | Your server requires passwords for TCP connections, not trust auth. | Connect over the local socket with `psql -d postgres` (no `-h`). If that also fails, ask the person who installed the server for the superuser password. |
| `role "quantcore" already exists` | You created it for earlier local work. | Run the `ALTER ROLE quantcore LOGIN CREATEDB;` line and `\password quantcore` instead. |
| `test_schema_parity` fails with `permission denied to create database` | The role lacks `CREATEDB`. | `ALTER ROLE quantcore CREATEDB;` |
| `database "quantcore_test" does not exist` | Step 1 was skipped or failed partway. | `createdb -h localhost -O quantcore quantcore_test` |
| `connection refused` on port 5432 | The server isn't running, or it listens on another port. | Start Postgres.app. For a different port, put that port in the DSN. |
| The run still takes ~30 minutes | The suite is still using Cloud SQL test. | Run step 3 and fix `.env` until it prints `localhost:5432/quantcore_test`. |
