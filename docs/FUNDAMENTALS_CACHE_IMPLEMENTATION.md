# Fundamentals Cache Implementation Summary

## Overview

Implemented a persistent SQLite-backed cache layer for the `company_fundamentals_server.py` MCP server, plus 8 new cross-symbol analytics tools. This converts the fundamentals server from a single-symbol lookup tool into a portfolio-wide analysis surface.

**Date Completed:** 2026-05-09  
**Files Modified:**
- `fastMCPTest/fundamentals_cache.py` — NEW
- `fastMCPTest/company_fundamentals_server.py` — MODIFIED (added 8 new tools, wrapped 4 existing tools)

---

## Key Features

### 1. Cache Layer (`fundamentals_cache.py`)

**Location:** SQLite database at `fastMCPTest/fundamentals_history.db`

**Append-only design:** Each cache miss appends a new row, building a time series for trend analysis.

**Configuration:**
- `FUNDAMENTALS_CACHE_TTL_HOURS` env var (default: 24 hours)
- Setting to 0 disables cache entirely (useful for testing)
- TTL is read on every call, so changes take effect without restart

**Public API:**
- `cache_get(symbol, data_type)` — retrieve if fresh
- `cache_set(symbol, data_type, payload)` — write new entry
- `cache_history(symbol, data_type, since_days)` — all snapshots in window
- `cache_invalidate(symbol, data_type=None)` — delete entries
- `cache_get_all_latest(data_type)` — most recent per symbol (no TTL)
- `cache_stats()` — inventory of cached data

**Professional features:**
- Double-checked locking for thread-safe schema initialization
- WAL (Write-Ahead Logging) mode for safe concurrent reads
- NORMAL synchronous pragma for balanced safety/performance
- Comprehensive exception handling with logging at all layers
- 30-second SQLite timeout for lock contention
- Graceful degradation: missing yfinance data returns null fields, not errors

---

### 2. Wrapped Existing Tools (Cache-Transparent)

The 4 existing tools now use the cache automatically with zero API changes:

| Tool | Data Type Key | Behavior |
|---|---|---|
| `get_earnings_calendar(symbol)` | `earnings_calendar` | Returns cache if fresh, fetches & caches on miss |
| `get_fundamental_score(symbol)` | `fundamental_score` | Returns cache if fresh, fetches & caches on miss |
| `get_revenue_growth(symbol)` | `revenue_growth` | Returns cache if fresh, fetches & caches on miss |
| `get_earnings_acceleration(symbol)` | `earnings_acceleration` | Returns cache if fresh, fetches & caches on miss |

**Backward compatible:** `stock_price_server.py` sees no changes; same signatures, same output.

---

### 3. New Cross-Symbol Analytics Tools

#### `get_fundamental_scores_batch(symbols: list[str])`
Batch scoring with cache hits/misses tracking. Eliminates N separate calls to score a watchlist.

Returns: Cache hit counts, fetch counts, error counts, results sorted by composite_score descending.

#### `get_full_fundamental_profile(symbol)`
All 4 fundamental metrics in one call: earnings calendar, score, revenue trajectory, EPS acceleration.

Synthesizes a summary with overall signal (bullish/bearish/neutral/caution) and key highlights.

#### `get_top_fundamental_stocks(n=10, min_coverage=0.5)`
Ranks all cached symbols by composite_score. Zero network calls. Pure cache read.

Returns ranked list with coverage validation (excludes sparse yfinance coverage).

#### `get_upcoming_earnings(days=14, include_stale=False)`
Surfaces stocks with earnings within N days. Days-to-earnings recomputed at query time (stays accurate).

Respects TTL by default; can include stale entries if flagged explicitly.

#### `get_cache_stats()`
Inventory of what's cached: symbol counts, date ranges, DB file size per data type.

#### `get_sector_fundamental_breakdown(sector=None, top_n=5)`
Groups cached scores by sector. Returns top N per sector or single sector if specified.

#### `get_fundamental_score_changes(min_delta=2, since_days=90, direction="both")`
Surfaces stocks whose fundamentals improved or deteriorated. Compares first and latest snapshots in window.

Turns append-only cache design into an early-warning system for fundamental shifts.

#### `get_fundamental_history(symbol, data_type, since_days=365)`
All historical snapshots for a metric with trend detection (comparing first vs. last in window).

---

## Benefits

### Responsiveness
Once warm, cache serves results in milliseconds instead of seconds. A 20-symbol watchlist ranking completes in seconds on first call, instantly on subsequent calls.

### Historical Tracking
Append-only design builds a time series automatically. After 90 days, score-change detection identifies fundamentals that shifted even when price hasn't reacted.

### Portfolio Analytics
New ranking, sector, and earnings tools enable questions the original server couldn't answer: "Top 5 fundamental stocks?", "Which sector has best fundamentals?", "Earnings in next 14 days?"

### Earnings Risk Awareness
`get_upcoming_earnings` + `get_full_fundamental_profile` create a pre-earnings review workflow for the first time.

---

## Verification

✓ All 12 tools defined and working  
✓ All 23 existing unit tests pass  
✓ Cache correctly persists and retrieves data  
✓ Batch scoring works with hits/misses tracked  
✓ Ranking, sector, and earnings tools return correct results  
✓ TTL-based freshness checking works  
✓ Database file created in correct location with correct size  
✓ No changes needed to `stock_price_server.py` (transparent wrapper)

---

## Database Schema

```sql
CREATE TABLE fundamentals_history (
    symbol      TEXT    NOT NULL,
    data_type   TEXT    NOT NULL,
    fetched_at  INTEGER NOT NULL,   -- Unix timestamp
    payload     TEXT    NOT NULL,   -- JSON string
    PRIMARY KEY (symbol, data_type, fetched_at)
);

CREATE INDEX idx_fundamentals_latest
    ON fundamentals_history (symbol, data_type, fetched_at DESC);
```

---

## Error Handling

- **Corrupt cache entries:** Logged with warning, skipped gracefully
- **yfinance failures:** Logged and returned as None fields (not raised)
- **DB lock timeouts:** 30-second timeout allows retry without hanging
- **JSON serialization errors:** Logged, entry skipped
- **Missing data:** Null fields returned, not errors

All error conditions are logged at appropriate levels (DEBUG for expected misses, WARNING for data corruption, ERROR for system issues).

---

## Next Steps (Optional, Out of Scope)

- Automated cache warm-up on server startup
- TTL differentiation by data type (earnings calendar might need shorter TTL)
- Discord notifications when `get_fundamental_score_changes` detects deterioration
- Integration of composite score into `get_trade_recommendation` signal weighting
- Scheduled cache refresh job
