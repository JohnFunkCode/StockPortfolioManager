# Plan: Migrate quantcore.sqlite to PostgreSQL

> **Status: ✅ COMPLETE.** This plan has been fully executed — `quantcore/db.py` and all store modules now run on PostgreSQL via `psycopg2`, `scripts/migrate_sqlite_to_postgres.py` exists and has migrated the full 137,293-row dataset (verified row-for-row against the SQLite source), and both a primary and an isolated test database are seeded and reachable via `QUANTCORE_DB_DSN` / `QUANTCORE_TEST_DB_DSN`. The databases additionally run on a managed PostgreSQL service (Cloud SQL), reached locally through the Cloud SQL Auth Proxy — no further code changes were required to make that switch, since the app only cares about the DSN in `.env`. The steps below are preserved as a historical record of the migration approach and as a reference for anyone repeating this process against a fresh SQLite snapshot.

## Context
The project currently uses SQLite via `quantcore/db.py` as a unified database for all persistence (OHLCV, Harvester plans, options, news, fundamentals). The goal is to migrate to PostgreSQL 18 running locally, with a clean path to Cloud SQL. A dedicated `quantcore` PG user with password will be used so the connection string works identically on local and cloud (just swap the host in `QUANTCORE_DB_DSN`).

Existing SQLite data in `data/quantcore.sqlite` will be migrated to PostgreSQL.

---

## Files to Change

| File | What changes |
|---|---|
| `requirements.txt` | Add `psycopg2-binary>=2.9` |
| `.env` | Replace `QUANTCORE_DB_PATH` with `QUANTCORE_DB_DSN` |
| `quantcore/db.py` | Full rewrite — psycopg2, PostgreSQL DDL, compat wrapper |
| `experiments/HarvesterPlanStore.py` | `:name` → `%(name)s`, `?` → `%s`, fix `lastrowid`, remove `BEGIN` |
| `fastMCPTest/ohlcv_cache.py` | `?` → `%s`, remove `sqlite3` import |
| `fastMCPTest/options_store.py` | Fix test path (DSN not file path), `?` → `%s`, PG DDL in `_init_db` |
| `fastMCPTest/options_position_store.py` | `?` → `%s` |
| `fastMCPTest/fundamentals_cache.py` | Remove `DB_PATH` import, replace file-size health stat with DSN info |
| `fastMCPTest/news_store.py` | `?` → `%s` |
| `fastMCPTest/sentiment_store.py` | `?` → `%s` |
| `scripts/migrate_sqlite_to_postgres.py` | **New** — one-shot data migration script |

Server entry points (`fastMCPTest/*.py` MCP servers, `api/app.py`, `main.py`) only call `init_schema()` — no SQL changes needed there beyond what `quantcore/db.py` provides.

---

## Step 1 — Prerequisites (run once manually)

```bash
# Create the user and both databases in PostgreSQL
psql postgres -c "CREATE USER quantcore WITH PASSWORD 'changeme';"
psql postgres -c "CREATE DATABASE quantcore OWNER quantcore;"
psql postgres -c "CREATE DATABASE quantcore_test OWNER quantcore;"
```

Both `quantcore` (dev) and `quantcore_test` (test) databases will be seeded with the existing SQLite data so tests can run against a realistic dataset from day one.

---

## Step 2 — `requirements.txt`

Add one line:
```
psycopg2-binary>=2.9
```

---

## Step 3 — `.env`

Replace:
```
QUANTCORE_DB_PATH=data/quantcore.sqlite
```
With:
```
QUANTCORE_DB_DSN=postgresql://quantcore:changeme@localhost:5432/quantcore
QUANTCORE_TEST_DB_DSN=postgresql://quantcore:changeme@localhost:5432/quantcore_test
```

`quantcore/db.py` reads `QUANTCORE_DB_DSN` at import time. Tests that need an isolated database should set `QUANTCORE_DB_DSN=$QUANTCORE_TEST_DB_DSN` in their environment, or the `options_store.py` constructor (updated in Step 6) can accept an explicit DSN argument.

---

## Step 4 — Rewrite `quantcore/db.py`

### 4a — Imports & DSN config
```python
import os, re
import psycopg2
import psycopg2.extras

DB_DSN = os.getenv("QUANTCORE_DB_DSN", "postgresql://quantcore:changeme@localhost:5432/quantcore")
```

### 4b — Thin sqlite3-compatible wrapper
Add a `_PGConn` wrapper class so every call site using `conn.execute(sql, params).fetchone()` continues to work without changes. The wrapper's `execute()` method:
- Creates a `DictCursor` (supports both `row[0]` and `row['col']` access, matching `sqlite3.Row`)
- Auto-converts `?` → `%s` and `:name` → `%(name)s` parameter styles

```python
class _PGConn:
    def __init__(self, pg_conn):
        self._c = pg_conn

    def execute(self, sql: str, params=None):
        sql, params = _adapt_sql(sql, params)
        cur = self._c.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, seq):
        sql, _ = _adapt_sql(sql, seq[0] if seq else None)
        cur = self._c.cursor()
        cur.executemany(sql, seq)
        return cur

    def commit(self):   self._c.commit()
    def rollback(self): self._c.rollback()
    def close(self):    self._c.close()
    def __enter__(self): return self
    def __exit__(self, *a): self._c.__exit__(*a)

def _adapt_sql(sql: str, params):
    if params is None:
        return sql, params
    if isinstance(params, dict):
        sql = re.sub(r':(\w+)', r'%(\1)s', sql)
    else:
        sql = sql.replace('?', '%s')
    return sql, params
```

### 4c — Update `get_connection()` and `init_schema()`
```python
def get_connection() -> _PGConn:
    conn = psycopg2.connect(DB_DSN)
    return _PGConn(conn)

def init_schema() -> None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            for stmt in _split_schema(_SCHEMA):
                cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()
```

### 4d — Rewrite `_SCHEMA` for PostgreSQL
- **Remove** the three PRAGMA lines at the top
- Change all `INTEGER PRIMARY KEY` / `INTEGER PRIMARY KEY AUTOINCREMENT` on single-column PKs to `SERIAL PRIMARY KEY`  
  Affected columns: `symbols.symbol_id`, `plan_templates.template_id`, `positions.position_id`, `plan_instances.instance_id`, `plan_rungs.rung_id`, `alerts.alert_id`, `options_snapshots.snapshot_id`, `options_expirations.expiration_id`, `options_contracts.contract_id`, `gamma_wall_history.id`, `options_positions.position_id`, `news_articles.article_id`, `sentiment_snapshots.id`
- Composite PK tables (`ohlcv`, `fetch_log`, `fundamentals_history`) — no change needed
- `REAL` and `TEXT` types work identically in PostgreSQL — no change
- `ON CONFLICT ... DO UPDATE` / `DO NOTHING` syntax is identical in PostgreSQL — no change needed in DDL
- Partial indexes (`WHERE status IN (...)`) — supported identically in PostgreSQL

---

## Step 5 — `experiments/HarvesterPlanStore.py`

### 5a — SQL constants: `:name` → `%(name)s`
All SQL string constants at the top of the file use `:name` style. Change every `:param` to `%(param)s`. Pattern: `re.sub(r':(\w+)', r'%(\1)s', sql)` applied to each constant string.

Affected constants: `SQL_INSERT_SYMBOL`, `SQL_GET_SYMBOL_ID`, `SQL_UPSERT_DAILY_BAR`, `SQL_GET_ACTIVE_INSTANCE_FOR_TICKER`, `SQL_GET_NEXT_PENDING_RUNG`, `SQL_UPSERT_ALERT_FOR_RUNG`, `SQL_DISABLE_OTHER_ALERTS_FOR_INSTANCE`, `SQL_MARK_ALERT_FIRED`, `SQL_MARK_RUNG_ACHIEVED`, `SQL_GET_ACTIVE_ALERT_FOR_RUNG`, `SQL_MARK_RUNG_EXECUTED`, `SQL_GET_RUNG_INSTANCE`.

### 5b — Inline SQL: `?` → `%s`
A handful of inline queries in `build_plan()` and other methods use `?`. Change to `%s`.

### 5c — Fix `lastrowid`
Two INSERTs capture the auto-generated PK via `cur.lastrowid` (lines ~411, ~472). Change to use `RETURNING`:

```python
# plan_templates INSERT — append RETURNING template_id to the SQL string
template_id = cur.fetchone()[0]

# plan_instances INSERT — append RETURNING instance_id  
instance_id = int(cur.fetchone()[0])
```

### 5d — Remove explicit `BEGIN`
Remove `conn.execute("BEGIN;")` (line ~416). psycopg2 starts transactions automatically; calling `BEGIN` on an already-open transaction causes a warning. The `conn.commit()` / `conn.rollback()` calls stay as-is.

---

## Step 6 — `fastMCPTest/` store modules

**General pattern for all six files:**
- Remove `import sqlite3` (no longer needed after db.py wraps it)
- Change `?` → `%s` in all SQL strings

**`ohlcv_cache.py`** — also fix `INSERT OR REPLACE` (line 201):
```sql
-- Before
INSERT OR REPLACE INTO ohlcv (...) VALUES (...)
-- After
INSERT INTO ohlcv (...) VALUES (%s,...) ON CONFLICT(symbol,interval,ts) DO UPDATE SET open=EXCLUDED.open, ...
```
And `INSERT OR REPLACE INTO fetch_log` → same upsert pattern.

**`options_store.py`** — the constructor `__init__(self, db_path=None)` needs updating:
- Change parameter to `dsn=None`; when provided, connect via `psycopg2.connect(dsn)` and return `_PGConn`
- Update `_init_db()` DDL to PostgreSQL syntax (same SERIAL/partial-index fixes as main schema)
- Remove `import sqlite3`

**`fundamentals_cache.py`** — remove `DB_PATH` import; replace the two lines that call `DB_PATH.stat().st_size` and `str(DB_PATH)` with the DSN string (or omit the file-size stat since it doesn't apply to a server database).

---

## Step 7 — Write `scripts/migrate_sqlite_to_postgres.py` (new file)

One-shot migration script:
Accepts two optional CLI args: `--sqlite path/to/quantcore.sqlite` and `--dsn postgresql://...`. Defaults to `QUANTCORE_DB_PATH` and `QUANTCORE_DB_DSN` env vars. Pass `--dsn $QUANTCORE_TEST_DB_DSN` to also seed the test database.

Steps executed for each target database:
1. Connect to SQLite source and PostgreSQL target
2. Call `init_schema()` on the target (creates all tables if not present)
3. Temporarily disable FK checks in PG session: `SET session_replication_role = replica`
4. Insert tables in dependency order (independent tables first, then FK-dependent ones):
   - Tier 1 (no FKs): `symbols`, `plan_templates`, `ohlcv`, `fetch_log`, `fundamentals_history`, `gamma_wall_history`, `options_snapshots`, `options_positions`, `news_articles`, `sentiment_snapshots`
   - Tier 2: `positions` (→ symbols)
   - Tier 3: `plan_instances` (→ plan_templates, symbols, positions)
   - Tier 4: `plan_rungs` (→ plan_instances), `options_expirations` (→ options_snapshots)
   - Tier 5: `alerts` (→ plan_rungs, symbols, plan_instances), `options_contracts` (→ options_expirations)
5. After all inserts, reset each SERIAL sequence: `SELECT setval(pg_get_serial_sequence('table','col'), MAX(col)) FROM table`
6. Re-enable FK checks: `SET session_replication_role = DEFAULT`
7. Print row counts for each table (SQLite vs PG) to verify

Run twice — once for dev, once for test:
```bash
python scripts/migrate_sqlite_to_postgres.py
python scripts/migrate_sqlite_to_postgres.py --dsn $QUANTCORE_TEST_DB_DSN
```

---

## Verification

```bash
# 1. Install new dependency
pip install psycopg2-binary

# 2. Create schema on both databases (done by migration script, but can test standalone)
python -c "from quantcore.db import init_schema; init_schema()"

# 3. Migrate SQLite data into dev DB
python scripts/migrate_sqlite_to_postgres.py

# 4. Migrate same SQLite data into test DB
python scripts/migrate_sqlite_to_postgres.py --dsn "$QUANTCORE_TEST_DB_DSN"

# 5. Run main app against dev DB
python main.py

# 6. Run test suite (no DB env var needed — tests don't hit DB directly today)
python -m unittest discover

# 7. Spot-check data via psql
psql "$QUANTCORE_DB_DSN" -c "SELECT COUNT(*) FROM ohlcv;"
psql "$QUANTCORE_TEST_DB_DSN" -c "SELECT COUNT(*) FROM ohlcv;"
```
