# Proposal: Fundamentals Cache and Cross-Symbol Analytics Layer

## Executive Summary

This proposal recommends adding a persistent cache layer and a suite of cross-symbol analytics tools to the `company_fundamentals_server.py` MCP server. The current server exposes four tools that hit Yahoo Finance on every call with no caching. Every invocation is slow, burns unnecessary network bandwidth, and discards the data the moment the call completes — making it impossible to rank, compare, or track fundamentals over time.

The proposed work introduces two deliverables:

- `fundamentals_cache.py` — a SQLite-backed, append-only persistence layer that stores fundamental snapshots per symbol and exposes a clean public API for the tool layer
- Eight new MCP tools built on top of the cache, covering batch scoring, cross-symbol ranking, sector comparison, earnings-proximity alerting, trend detection, historical lookback, health monitoring, and full-profile synthesis

Together these changes move the fundamentals server from a single-symbol lookup tool into a portfolio-wide analysis surface that can identify the strongest names, flag approaching earnings risk, detect deteriorating businesses, and do all of this with zero repeated network calls once the cache is warm.

---

## Problem Statement

The current `company_fundamentals_server.py` has four tools — `get_earnings_calendar`, `get_fundamental_score`, `get_revenue_growth`, and `get_earnings_acceleration` — each of which makes fresh yfinance calls on every invocation. This creates several problems in practice:

**Latency.** Each tool call takes several seconds as yfinance fetches income statements, cash flow statements, quarterly financials, and price history. Calling all four tools on a single symbol takes 10–20 seconds total. Doing so across a 20-stock watchlist is not practical in an interactive session.

**No historical record.** Fundamental scores are computed and discarded. There is no way to know whether a company's score has improved or deteriorated over the last quarter. The append-only cache design fixes this at no extra cost — each cache miss adds a new row, building a time series that can be queried as-is.

**No cross-symbol analytics.** Because there is no persistence, there is no way to rank the universe of tracked stocks by fundamental quality, group them by sector, or flag which ones have the most urgent earnings approaching. Each MCP call is isolated from all others.

**Repeated work.** In a typical research session the same symbols are scored multiple times — before and after different analyses, or when composing a recommendation. Without caching, every call hits the network even when nothing has changed since the last call.

---

## Proposed Architecture

```
fundamentals_cache.py               [NEW]
  SQLite — fastMCPTest/fundamentals_history.db
  append-only schema: (symbol, data_type, fetched_at, payload)
  TTL-based staleness (default 24h, env-var configurable)
  Public API:
    cache_get(symbol, data_type)           → dict | None
    cache_set(symbol, data_type, payload)  → None
    cache_history(symbol, data_type, ...)  → list[dict]
    cache_invalidate(symbol, data_type)    → None
    cache_get_all_latest(data_type)        → list[dict]
    cache_stats()                          → dict

company_fundamentals_server.py      [MODIFIED]
  Existing 4 tools → wrapped with cache_get / cache_set
  8 new tools built on cache layer
```

The cache follows the same implementation pattern as `ohlcv_cache.py`: WAL journal mode, NORMAL synchronous, double-checked locking for one-time schema init, `closing()` on every connection, 30-second lock timeout. The DB file lives in `fastMCPTest/` alongside all other project databases.

The append-only design means every cache miss inserts a new row rather than updating an existing one. The primary key `(symbol, data_type, fetched_at)` prevents duplicates. Old rows are never deleted — they accumulate into a daily time series that can be queried for trend analysis, score change detection, and historical snapshots.

---

## Cache Configuration

The TTL is controlled by an environment variable and read on every call so changes take effect without restarting the server:

```
FUNDAMENTALS_CACHE_TTL_HOURS   default: 24
FUNDAMENTALS_CACHE_DB          default: fastMCPTest/fundamentals_history.db
```

Setting `FUNDAMENTALS_CACHE_TTL_HOURS=0` disables the cache entirely, useful for testing.

---

## Tool Inventory

### Existing Tools (cache-wrapped, behavior unchanged externally)

| Tool | Data type key | Network call |
|---|---|---|
| `get_earnings_calendar(symbol)` | `earnings_calendar` | on cache miss only |
| `get_fundamental_score(symbol)` | `fundamental_score` | on cache miss only |
| `get_revenue_growth(symbol)` | `revenue_growth` | on cache miss only |
| `get_earnings_acceleration(symbol)` | `earnings_acceleration` | on cache miss only |

These tools return identical output after the cache is added. Callers in `stock_price_server.py` see no behavioral change.

---

### New Tools

#### `get_fundamental_scores_batch(symbols: list[str])`

Score multiple stocks in a single call. For each symbol, returns the cached result if fresh; otherwise fetches from yfinance, caches, and returns. Tracks cache hits, fetches, and errors in the summary. Results sorted by `composite_score` descending so the output is immediately readable.

This is the prerequisite for the ranking and sector tools to be useful — it is how the cache gets populated efficiently across an entire watchlist or portfolio.

Key outputs:
- `cache_hits` / `fetched` / `errors` counts
- `results` — list of scored stocks sorted by `composite_score` descending, each with `cache_hit` boolean

---

#### `get_full_fundamental_profile(symbol)`

Returns all four fundamental data types for a single symbol in one call — earnings calendar, composite score, revenue trajectory, and EPS acceleration — plus a synthesized `summary` block with an overall signal and key highlights. Replaces four separate tool calls with a single request.

The summary derives a qualitative `overall_signal` from the combination of score, trajectory, acceleration, and earnings risk, and lists `highlights` like "Strong fundamentals (score 12)", "Revenue accelerating", or "Earnings in 4d — options risk."

---

#### `get_top_fundamental_stocks(n: int = 10, min_coverage: float = 0.5)`

Ranks all cached symbols by `composite_score` descending and returns the top N. Pure cache read — zero network calls. `min_coverage` excludes symbols where fewer than 50% of the seven metrics had data, avoiding misleading rankings for tickers with sparse yfinance coverage.

Returns rank, symbol, score, label, coverage fraction, and cache timestamp for each entry. Also reports `total_in_cache` and `eligible_count` so the caller knows the size of the pool.

This tool is only useful after the cache has been populated — `get_fundamental_scores_batch` is the intended way to do that.

---

#### `get_upcoming_earnings(days: int = 14, include_stale: bool = False)`

Returns all cached symbols with earnings scheduled within the next N days, sorted by urgency ascending. Designed to surface which tracked stocks need extra attention before their earnings event.

Days-to-earnings is recomputed from the stored `earnings_date` against today's date at query time — the stored field was accurate at cache time but will drift as the days pass. This keeps the output fresh without a network call.

By default, only entries within the `FUNDAMENTALS_CACHE_TTL_HOURS` freshness window are returned. Earnings dates can shift, so stale cached dates may no longer be accurate. Set `include_stale=True` to include all cached symbols regardless of age; stale entries are flagged with `stale: true` in the response.

Each entry includes:
- `symbol`, `earnings_date`, `days_to_earnings` (recomputed)
- `risk_level` — CRITICAL / HIGH / MODERATE / LOW (from cached payload)
- `pre_earnings_setup` — boolean indicating IV expansion window
- `historical_avg_move_pct` — average absolute price move at last 4 earnings events
- `cached_at` and `stale` flag

---

#### `get_cache_stats()`

Returns an inventory of what is stored in the fundamentals cache: symbol count per data type, oldest and newest entry dates, and database file size. Zero network calls. Useful for verifying that the cache is populated before running ranking or comparison tools, and for routine operational monitoring.

---

#### `get_sector_fundamental_breakdown(sector: str | None = None, top_n: int = 5)`

Groups cached fundamental scores by sector (the `sector` field is already stored in each `fundamental_score` payload from yfinance). If `sector` is provided, returns only stocks in that sector; if `None`, returns the top `top_n` stocks for every sector found in the cache.

This tool requires no new network calls — it is a grouping and ranking operation over the cache. Symbols without a sector field in their payload are grouped under `"Unknown"`.

---

#### `get_fundamental_score_changes(min_delta: int = 2, since_days: int = 90, direction: str = "both")`

Surfaces stocks whose composite fundamental score has changed significantly between the earliest and latest cached snapshots within the lookback window. Only symbols with at least two snapshots in the window are evaluated.

`direction` accepts `"improving"`, `"deteriorating"`, or `"both"`. Deteriorating stocks are sorted by largest absolute drop first; improving stocks by largest gain first.

Each result includes score then, score now, delta, label then, label now, and the timestamps of both snapshots. This turns the append-only cache design from a nice-to-have into an active monitoring signal — it can identify a business whose fundamentals have quietly deteriorated quarter over quarter even when the stock price has not yet reacted.

---

#### `get_fundamental_history(symbol, data_type, since_days: int = 365)`

Returns all historical snapshots for a symbol and data type from the cache as an ordered list, oldest first. Computes a `trend` label by comparing the key score field between the first and last snapshots in the window.

Trend thresholds by data type:
- `fundamental_score` — composite_score change ± 1 on a -14 to +14 scale
- `revenue_growth` — weighted_score change ± 0.05 on a 0–1 scale
- `earnings_acceleration` — acceleration_score change ± 1 on a -1 to +2 scale
- `earnings_calendar` — days_to_earnings change ± 7 (inverted: more days = improving)

---

## Benefits

### Responsiveness

Once warm, the cache serves tool results in milliseconds rather than seconds. A full 20-symbol watchlist ranking via `get_fundamental_scores_batch` completes in seconds on the first call; subsequent calls return instantly.

### Historical tracking

The append-only schema builds a daily time series automatically. After 90 days of normal usage, `get_fundamental_score_changes` can surface names whose fundamentals have quietly shifted — both opportunities and risks that price action alone would miss.

### Portfolio-wide analytics

`get_top_fundamental_stocks`, `get_sector_fundamental_breakdown`, and `get_upcoming_earnings` all operate across the full cached universe. These tools enable questions the current server cannot answer at all: "What are the five fundamentally strongest names in our watchlist?", "Which sector has the best fundamentals among tracked stocks?", "Which holdings have earnings in the next 14 days and what is the historical move magnitude?"

### Earnings risk awareness

`get_upcoming_earnings` gives the team a single call that identifies which tracked stocks need pre-event attention. Combined with `get_full_fundamental_profile`, this creates a natural pre-earnings review workflow: identify upcoming events, pull the full profile on each, and decide whether the position sizing is appropriate given the earnings risk level.

### Operational visibility

`get_cache_stats` gives immediate visibility into the health of the cache — how many symbols are scored, how fresh the data is, and how large the database has grown. This is a small addition that pays for itself the first time someone needs to debug why a ranking tool returned an empty result.

---

## Implementation Notes

### Naming collision

There is an existing private helper `_compute_earnings_acceleration(quarterly_income_stmt)` at line 228 of `company_fundamentals_server.py`. The new private compute function for the tool wrapper must be named `_compute_earnings_acceleration_tool(sym)` to avoid shadowing the helper. The existing helper is called internally by the new function and is otherwise unchanged.

### Cache-reader tools

`get_top_fundamental_stocks`, `get_sector_fundamental_breakdown`, `get_upcoming_earnings`, `get_cache_stats`, `get_fundamental_score_changes`, and `get_fundamental_history` are all pure cache readers. They make zero network calls. They will return empty or minimal results on a fresh database — the expected workflow is to populate the cache first via `get_fundamental_scores_batch`, then use these tools for analysis.

### TTL and the upcoming earnings tool

`get_upcoming_earnings` applies a freshness filter by default. Earnings dates can be revised after the initial fetch, so returning a cached date from 72 hours ago without flagging it creates risk. The `include_stale=False` default is intentionally conservative.

### No changes to `stock_price_server.py`

The four existing functions imported by `stock_price_server.py` retain their signatures and return shapes. The cache layer is entirely internal to `company_fundamentals_server.py`. No changes are needed in the caller.

---

## Phasing

### Phase 1 — Foundation (prerequisite for everything else)

- Implement `fundamentals_cache.py` with full public API
- Wrap the four existing tools with cache logic
- Verify DB creation, TTL behavior, and append-only writes

### Phase 2 — Single-symbol improvements

- `get_full_fundamental_profile` — most immediately useful for research sessions
- `get_fundamental_history` — enables trend tracking from day one

### Phase 3 — Cross-symbol analytics

- `get_fundamental_scores_batch` — populates the cache efficiently
- `get_top_fundamental_stocks` — first ranking tool
- `get_upcoming_earnings` — earnings proximity alerting
- `get_cache_stats` — operational health

### Phase 4 — Comparison and alerting

- `get_sector_fundamental_breakdown` — sector grouping
- `get_fundamental_score_changes` — deterioration/improvement detection

---

## Risks and Constraints

**Cache population is manual.** The ranking and comparison tools only know about symbols that have been previously scored. If the watchlist grows, someone has to call `get_fundamental_scores_batch` to include the new names. An automated scheduled refresh would address this but is out of scope for this proposal.

**yfinance data availability varies by symbol.** The `coverage` field in each fundamental score payload captures what fraction of the seven metrics had data. For thinly covered names, the composite score may be based on three or four metrics rather than seven. The `min_coverage` parameter in `get_top_fundamental_stocks` mitigates misleading rankings.

**Quarterly data changes infrequently.** The 24-hour TTL is appropriate for the daily research workflow, but users should be aware that fundamental data refreshes at most quarterly for most metrics. The cache is a staleness control on yfinance calls, not a real-time feed.

**Database growth.** At the default 24-hour TTL, the database accumulates one row per symbol per data type per day. For a 30-symbol watchlist with all four data types, that is 120 rows per day, approximately 43,000 rows per year. This is well within SQLite's practical limits and the file size will remain small.

---

## Open Questions for the Team

- Should `get_upcoming_earnings` be the primary trigger for a pre-earnings review workflow, or should that remain manual?
- Is a 24-hour TTL appropriate for earnings calendar data, which can shift closer to the event date, or should earnings calendar have a shorter TTL than other data types?
- Should `get_fundamental_score_changes` send a Discord notification when it detects a deteriorating name? This would connect the cache's historical tracking to the existing notification system.
- Should `get_fundamental_scores_batch` be wired to the portfolio and watchlist files at startup to warm the cache automatically on each server start?
- Is there a threshold on `composite_score` below which the `get_trade_recommendation` signal weighting should be capped, regardless of technical signals? This would be the natural integration point between the fundamentals server and `stock_price_server.py`.

---

## Summary

The four existing fundamentals tools are useful in isolation but underperform their potential because every call starts from scratch. Adding a cache layer and eight new cross-symbol tools converts them into a portfolio-wide analytical surface that can rank, compare, alert, and track — all with zero additional network calls once the cache is populated. The implementation is self-contained, follows the existing project patterns, and requires no changes to `stock_price_server.py` or any other existing server.

The most impactful near-term outcome is `get_upcoming_earnings` combined with `get_full_fundamental_profile` — together they give the team a practical pre-earnings review workflow for the first time. The most strategically valuable long-term outcome is `get_fundamental_score_changes`, which turns the cache's append-only design into an early-warning system for fundamental deterioration.
