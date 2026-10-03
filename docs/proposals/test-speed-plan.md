# Test-suite speed (issue #289)

Issue #289 combines #157, #196 and #197.

## Context

Two of the causes those issues named were fixed before this plan:

- The 500 single-row bar inserts in `build_plan` are now one `executemany` (`b8b6951`, PR #206).
- Real Yahoo requests are blocked in the suite (`c172d99`, PR #288).

Nobody had re-measured since.

CI's `gate` job runs the backend suite in about 65 seconds, against a throwaway `postgres:16`
service on localhost. The slow runs happen **locally** (#289 cited ~31 minutes; baseline 1 below measured 17). `tests/__init__.py` copied
`QUANTCORE_TEST_DB_DSN` from `.env` into `QUANTCORE_DB_DSN`, and that DSN is Cloud SQL test
through the proxy on `127.0.0.1:5434`, at about 29 ms per round trip. `quantcore.db.get_connection()`
also opens a new `psycopg2.connect` on every call, so every operation pays the proxy's connection
setup as well.

**Decision (John, 2026-10-03):** local runs default to a local Postgres, the same shape as CI.
Cloud SQL test stays available as an opt-in.

## Changes

1. **Measure.**
   - Backend: `--durations 25` (Python 3.12+ `unittest`) on the `gate` command in `deploy.yml`,
     on the promotion run in `prod-rollout.yml`, and on the documented local commands.
   - Frontend: `slowTestThreshold: 300` is pinned in `frontend/vitest.config.ts`.
   - Timings are information only and are never asserted on.
2. **Local Postgres by default.**
   - `tests/__init__.py` prefers a new `.env` key, `QUANTCORE_UNITTEST_DB_DSN`, and falls back
     to `QUANTCORE_TEST_DB_DSN`.
   - `QUANTCORE_UNITTEST_DB=cloudsql` forces the fallback for one run.
   - `tests/test_suite_dsn_selection.py` covers the precedence.
   - Team setup guide: [`docs/local-unit-test-db.md`](../local-unit-test-db.md).
3. **Duplicate harvester runs removed.** `HarvesterScanTest` and `HarvesterOwnerIsolationTest`
   took `HarvesterRepoTest` as a base, so its 7 tests ran three times. Both now take
   `HarvesterRepoFixture`.
   - Each of the 7 already had an explicit two-owner counterpart in the isolation class, so none
     needed rewriting.
   - The module drops from 39 to 25 tests.

## Checked and not a cost

- `tests/test_watchlist_service.py`'s `time.sleep(30)` runs in the gateway's daemon thread,
  behind a patched 0.25 s timeout. The test returns in about 0.25 s. Don't chase it.
- The `test_keyproxy_*` and `test_yfinance_gateway.py` delays are short and configurable. Act on
  them only if `--durations` ranks them.

## Deliberately deferred

- **Connection reuse in `get_connection()`.** It is production code, and once local runs are
  local the gain is small. Revisit only if the local-Postgres baseline still has DB-heavy modules
  at the top.
- **Parallel runs / a database per worker.** Blocked on DB-backed modules purging each other's
  synthetic rows, which is the same shared-database root cause as #248. Tracked there.
- **CI runs the suite twice** (`gate`, then `prod-rollout.yml` at promotion). This is expected.
  Count it when estimating release time.

## Gotchas

- **`QUANTCORE_TEST_DB_DSN` could not simply be repointed** at a local database. `flyway.sh`,
  `with-test-db.sh`, `schema_check.py`, both import scripts, `grant_quantui_iap_access.sh`,
  `runUI-CONTAINERS.sh` and `db_safety.py` all read it as "Cloud SQL test". That is why the suite
  has its own key.
- **Wrapping the suite in `with-test-db.sh` no longer sends it to Cloud SQL.** `tests/__init__.py`
  re-reads `.env` and overrides `QUANTCORE_DB_DSN`. Use `QUANTCORE_UNITTEST_DB=cloudsql`.
- **Postgres.app refused trust auth from the agent's shell**, so creating the local role and
  database is a manual step. The guide covers it.
- **Vitest's default reporter already lists slow tests** with their times above
  `slowTestThreshold`. No extra reporter was needed, despite the plan's first draft.

## Checkpoint log

| Step | Commit | Result | Gotcha |
|---|---|---|---|
| Baseline 1: local, Cloud SQL test (`:5434`), before changes | `2244012` | 1591 tests in 1022 s (~17 min), OK, skipped=5. 21 of the top 25 were harvester-module tests (10–18 s each, 19 of them in `HarvesterOwnerIsolationTest`); 9 of those 21 were inherited duplicates that change 3 removes. The module's own tests are each 10 s+ through the proxy, so it stays the heaviest until the local-DB baseline. Others in the top 25: `test_schema_parity` 16.2 s, `test_api_smoke` LotRoutesTest 13.5 s and watchlist-fundamentals 11.8 s, `test_schema_introspect_live` 11.0 s. | #289's ~31 min figure didn't reproduce; docs now say ~17. |
| Changes 1–3 + docs | _this PR_ | backend unit tests pass; harvester module 39 → 25 tests | |
| Baseline 2: local Postgres (`:5432`) | `d53583e` | 1582 tests in 38 s, OK, skipped=5: **27× faster** than baseline 1. Slowest test is `test_collect_news_records_heartbeat` at 2.3 s. Then come keyproxy streaming tests (0.5–1.4 s, deliberate short delays) and harvester-isolation tests (0.3–0.7 s, down from 10–18 s). `test_schema_parity` went from 16.2 s to 0.45 s. No DB-heavy module stands out, so connection reuse in `get_connection()` stays deferred. | Beats CI's 48 s only because local runs skip coverage. Measured on John's Mac (Postgres.app); the step-3 check in `docs/local-unit-test-db.md` printed `localhost:5432/quantcore_test`. |
| Baseline 3: CI `gate` / `frontend-gate` | `a849e2d` | Backend: 1582 tests in 48 s under coverage (1591 − 14 duplicates + 5 new). The slowest test is 1.5 s, and the top 11 are harvester-isolation and keyproxy streaming tests. Frontend: 620 tests in 83 s wall time, of which 74 s is import/transform. No single test is slow. | The same harvester test is 17.9 s via the proxy and 1.5 s on local Postgres: the proxy round trip is the whole cost. |
