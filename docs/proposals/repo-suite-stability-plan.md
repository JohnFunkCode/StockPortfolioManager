# Repository integration-suite stability (issue #248)

## Context

`tests.test_repositories_db` failed intermittently: a different subset of tests each time, spread
across Fundamentals, News, Ohlcv, OptionsPosition and UserSettings. Every failure passed when the
module was rerun on its own. The failures were first seen against the shared Cloud SQL test
instance through the proxy, so the proxy was the first suspect.

## Root cause

**It was never the proxy.** Two runs against one database delete each other's rows.
`test_repositories_db` seeds fixed synthetic keys (`SYM = "ZZREPOS"`,
`TEST_OWNER = "zzrepos-owner"`) and runs `purge()` from `setUp` and from `addCleanup`. When a
second run's `purge()` lands between the first run's seed and its assertion, the first run's test
fails. The other 23 DB-backed modules follow the same pattern with their own keys.

Reproduction, deterministic once identified: launch two `python -m unittest
tests.test_repositories_db` processes at the same time against one database.

| Setup | Result |
|---|---|
| One run, local Postgres or Cloud SQL test via the proxy | 30/30 pass, every time |
| Two concurrent runs on one database (3 trials) | 12–17 failures **per run**, a different subset each time |

The Cloud SQL test instance is shared: the developer's machine, a second terminal, and an agent
session all point at it. That is how the collisions happened without anyone running two suites on
purpose. #289's parallel-runs blocker is the same root cause.

## Fix

**One run per database at a time: a session-level Postgres advisory lock, taken in
`tests/__init__.py`** after the DSN is chosen, and held on a dedicated connection until the process
exits.

- `pg_try_advisory_lock` first. If another run holds the lock, print
  `Another test run is using host:port/name; waiting up to Ns …` to stderr, then block in
  `pg_advisory_lock` under a `statement_timeout`.
- The timeout comes from `QUANTCORE_UNITTEST_LOCK_TIMEOUT` (default 1800 s). In house style, an
  unparseable or non-positive value falls back to the default. `0` would mean "no timeout" to
  Postgres and wait forever.
- When the timeout expires, raise `RuntimeError` naming the database. **Never the DSN**: it carries
  the password, and the message goes through `describe_dsn()`.
- If the database is unreachable, skip the lock and return `None`. DB-backed modules fail with
  their own errors, and pure modules still run without a database.
- The holder connection is in autocommit, so it idles cleanly instead of sitting
  "idle in transaction" for the whole run. It sets `application_name = 'quantcore-unittest'`, so
  `pg_stat_activity` shows who holds the lock.

### Why not namespace the keys per run instead

Per-run unique keys (`ZZREPOS_<pid>`) would fix the purges but not the **global** reads. Several
repository calls see the whole table: `FundamentalsRepository.stats()`, `get_all_latest`, the
options-position expiry sweeps, and the watchlist's full-sync import. Tests that assert on counts
from those would still race. The lock fixes all 24 modules at once with no test rewrites. The cost
is that concurrent runs serialize instead of running in parallel, which was never safe anyway.

### Acceptance criteria

| Criterion | How it's met |
|---|---|
| Passes repeatedly against clean local Postgres | Three trials of two concurrent runs: 6/6 green (they were 0/6 before), with one run per pair waiting |
| Full backend suite passes in CI | CI runs one suite per throwaway database. The lock is taken uncontended. |
| Failures diagnosable and safely logged | Wait and timeout messages name `host:port/name`. `FundamentalsRepository`'s six swallow-and-log handlers now log the error class and SQLSTATE (`_db_error`). |
| No dependence on stale shared rows or wall-clock timing | No other run can purge mid-test. The lock's timeout is only an upper bound on the wait; nothing asserts on timing. |
| Remote proxy testing stable or separated from CI | Separated since #289: local runs default to `QUANTCORE_UNITTEST_DB_DSN`, Cloud SQL is opt-in, and CI never uses it. Stable now that concurrent runs on the shared instance wait instead of colliding. |
| No blind retries of non-idempotent writes | None were added. The flake was a cross-run race, not a transient error, so a retry would have hidden it. |

## Gotchas

- **The failures look like proxy flakiness, and aren't.** Single runs through the proxy passed
  every time. Collect the per-run failure lists from two concurrent runs before suspecting the
  network.
- **`tail -1` of a unittest log is not the result line.** Tests log at ERROR on purpose (the
  alarm tests), so the tail is often a log line. Grep `^Ran` and `^(OK|FAILED)` instead.
- **Postgres.app rejected trust auth from the agent's shell** (as it did in #289), so I verified
  against a throwaway cluster (`initdb` + `pg_ctl` on `127.0.0.1:5440`) under the scratchpad.
  Starting it there needed two workarounds:
  - `initdb` hit `shmget: Operation not permitted` inside the sandbox, so it has to run
    unsandboxed.
  - `pg_ctl` failed because the scratchpad path makes the Unix-socket path longer than macOS's
    103-byte limit. Start it with `-o "-k ''"` to listen on TCP only.
- **Running against a copy of the tree** (so `.env` doesn't redirect the suite) needs the copy to
  lack `.env`. Otherwise `tests/__init__.py` re-reads the real `.env` and overrides
  `QUANTCORE_DB_DSN`.
- **A test can't take the lock "fresh" from inside the suite**, because the suite already holds it.
  `tests/test_suite_lock.py` uses that: a second acquire from the same process is a genuine
  contended case, so it exercises the wait message and the timeout for real.
- **A test that spawns `python -c "import tests"` as a subprocess would wait for its own parent**
  until the timeout. No current test does this; I checked when designing the fix. Keep it that way,
  or give such a subprocess a database of its own.

## Checkpoint log

| Step | Commit | Result | Gotcha |
|---|---|---|---|
| Reproduce | — | Single runs: 30/30 locally and through the proxy. Two concurrent runs: 12–17 failures each, 3 of 3 trials. | Proxy was a red herring |
| Lock + tests + SQLSTATE logging + docs | _this PR_ | Two concurrent `test_repositories_db` runs: 6/6 OK across 3 trials, one waiter per pair. Two concurrent full suites: both 1589 OK (skipped=5), the second waited. Single full suite: 1589 OK in 25 s. The password appeared nowhere in the logs. | See Gotchas |
