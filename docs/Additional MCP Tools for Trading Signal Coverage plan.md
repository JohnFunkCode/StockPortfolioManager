# Plan: Additional MCP Tools for Trading Signal Coverage

## Context

`get_trade_recommendation` (stock_price_server.py:2437) runs 13 signals across technicals and options flow but has no awareness of earnings risk, company fundamentals, or relative market strength. This causes two documented failure modes: (1) entering options positions without knowing earnings is imminent (IV crush risk), and (2) treating technically identical setups on fundamentally strong vs. deteriorating businesses the same way.

Three experiment scripts in `experiments/` already contain working logic ready to lift into a production MCP server. The goal is a new server (`company_fundamentals_server.py`) plus a new `get_relative_strength` tool in the existing server, then wiring 5 new signals into `get_trade_recommendation`.

---

## Architecture

```
experiments/  (reference only — logic understood from here, not imported)
  CompositScoreExperiment.py
  RevenueGrowthExperiment1.py
  EarningsAccelerationExperiment.py

fastMCPTest/options_analysis.py
  fetch_earnings_proximity()    ← already implemented (lines 513-559), reuse directly

fastMCPTest/fundamentals_cache.py            [NEW]
  SQLite-backed append-only historical store (TTL for freshness, full history retained)
  fundamentals_history table in unified QuantCore database (data/quantcore.sqlite)

fastMCPTest/company_fundamentals_server.py   [NEW]
  FastMCP("company-fundamentals-server")
  ├── get_earnings_calendar(symbol)                   — reimplemented; cached
  ├── get_fundamental_score(symbol)                   — reimplemented; cached
  ├── get_revenue_growth(symbol)                      — reimplemented; cached
  ├── get_earnings_acceleration(symbol)               — reimplemented; cached
  └── get_fundamental_history(symbol, data_type, ...) — reads historical record from cache

fastMCPTest/stock_price_server.py            [MODIFIED]
  line 12: add import from company_fundamentals_server
  new @mcp.tool(): get_relative_strength(symbol)
  get_trade_recommendation(): add Signals 14–18 + earnings blackout override

.mcp.json                                    [MODIFIED]
  add "company-fundamentals-server" entry
```

**Import chain (mirrors the market_analysis_server pattern at line 12):**
```python
from company_fundamentals_server import (
    get_earnings_calendar, get_fundamental_score,
    get_revenue_growth, get_earnings_acceleration,
)
```

**Experiment files are reference only.** The logic from each is reimplemented directly in `company_fundamentals_server.py` — no imports from `experiments/`. This keeps the production server self-contained and removes the dependency on experimental code.

---

## File 1: `fastMCPTest/fundamentals_cache.py` (new)

Mirrors the structure of `ohlcv_cache.py`. Single SQLite file at project root (not in `fastMCPTest/`), configurable via env vars.

### Configuration

```python
from quantcore.db import get_connection
import os

_CACHE_TTL_HOURS = int(os.environ.get("FUNDAMENTALS_CACHE_TTL_HOURS", "24"))

# Database connection via shared factory:
# get_connection() → data/quantcore.sqlite (from QUANTCORE_DB_PATH env var)
```

### Schema

```sql
CREATE TABLE IF NOT EXISTS fundamentals_history (
    symbol      TEXT    NOT NULL,
    data_type   TEXT    NOT NULL,
    fetched_at  INTEGER NOT NULL,   -- UTC Unix timestamp; natural sort key
    payload     TEXT    NOT NULL,   -- JSON string; schema varies per data_type
    PRIMARY KEY (symbol, data_type, fetched_at)
);
CREATE INDEX IF NOT EXISTS idx_fundamentals_latest
    ON fundamentals_history (symbol, data_type, fetched_at DESC);
```

**Append-only design:** every cache miss triggers a new INSERT, building a daily time series. The primary key `(symbol, data_type, fetched_at)` prevents exact duplicate inserts within the same second. Old rows are never updated — each refresh adds a new row. Data types: `"fundamental_score"`, `"revenue_growth"`, `"earnings_acceleration"`, `"earnings_calendar"`.

This design means the database grows over time at the TTL cadence (default: 1 row/symbol/data_type/day) and can be queried as a historical record without any ETL.

### Public API

```python
def cache_get(symbol: str, data_type: str) -> dict | None:
    """Return the most recent payload if age < TTL, else None."""
    # SELECT payload, fetched_at FROM fundamentals_history
    # WHERE symbol=? AND data_type=?
    # ORDER BY fetched_at DESC LIMIT 1

def cache_set(symbol: str, data_type: str, payload: dict) -> None:
    """INSERT new row (append; never overwrites history)."""

def cache_history(symbol: str, data_type: str, since_days: int = 365) -> list[dict]:
    """Return all rows newer than since_days as [{fetched_at, payload}, ...]."""
    # SELECT fetched_at, payload FROM fundamentals_history
    # WHERE symbol=? AND data_type=? AND fetched_at>=?
    # ORDER BY fetched_at ASC

def cache_invalidate(symbol: str, data_type: str | None = None) -> None:
    """Delete rows. Pass data_type=None to clear all types for a symbol."""
```

Staleness check in `cache_get`:
```python
age_hours = (datetime.datetime.utcnow().timestamp() - fetched_at) / 3600
if age_hours > _CACHE_TTL_HOURS:
    return None  # stale — caller will re-fetch and INSERT a new row
```

Connection setup: WAL mode + NORMAL synchronous, identical to `ohlcv_cache.py`. Thread-safe init with a module-level lock.

---

## File 2: `fastMCPTest/company_fundamentals_server.py` (new)

Each tool follows this pattern:
```python
@mcp.tool()
def get_fundamental_score(symbol: str) -> dict:
    sym = symbol.upper()
    cached = cache_get(sym, "fundamental_score")
    if cached is not None:
        return cached
    result = _compute_fundamental_score(sym)
    cache_set(sym, "fundamental_score", result)
    return result
```

The `_compute_*` private functions contain the actual yfinance calls and computation. The `@mcp.tool()` wrapper handles the cache layer. This keeps each tool testable independently of the cache.

---

### `get_earnings_calendar(symbol: str) -> dict`

Reuse `fetch_earnings_proximity()` from `options_analysis.py` (the one import from production code that is justified — it's already in the same `fastMCPTest/` package, not an experiment). Adds historical earnings move data:

```python
from options_analysis import fetch_earnings_proximity

# Historical moves: yf.Ticker(sym).earnings_dates — last 4 rows, compute |pct change|
# via yf.Ticker(sym).history around each earnings date (or use earnings_dates['Surprise(%)'])
```

Returns:
```json
{
  "days_to_earnings": 22,
  "earnings_risk": "PRE_EARNINGS_ZONE",   // "BLACKOUT"(<14d), "PRE_EARNINGS_ZONE"(14-30d), "SAFE"(>30d), "UNKNOWN"
  "avg_historical_move_pct": 6.2,         // avg |move| last 4 quarters
  "blackout": false
}
```

Earnings risk labels:
- `days_to_earnings < 7` → `"BLACKOUT"`, `blackout: true`
- `< 14` → `"EARNINGS_RISK_HIGH"`, `blackout: true`
- `14-30` → `"PRE_EARNINGS_ZONE"` (IV expansion tailwind for long calls)
- `> 30` → `"SAFE"`

### `get_fundamental_score(symbol: str) -> dict`

**Key design note:** `compute_fundamental_rank()` in the experiment requires a universe for cross-sectional z-scores — doesn't work for a single symbol. Reimplement as a self-contained set of private helper functions using absolute thresholds instead.

Reimplement these helpers directly in `company_fundamentals_server.py` (algorithm understood from `CompositScoreExperiment.py`, code written fresh):
- `_get_annual_revenue_and_op(t)` — annual income statement, Total Revenue + Operating Income
- `_get_annual_cfo_and_capex(t)` — annual cashflow statement
- `_get_quarterly_revenue(t)` — quarterly financials
- `_rev_cagr_3y(annual_rev)` — 3-year CAGR from annual revenue series
- `_rev_accel(annual_rev)` — YoY growth this year minus prior year
- `_op_margin_3y_and_trend(annual_rev, annual_op)` — 3Y avg + trend delta
- `_fcf_margin_3y(annual_rev, cfo, capex)` — 3Y avg (CFO − capex) / revenue
- `_valuation_metric(info)` — log(EV/Sales) or log(P/E), lower is better
- `_mom_12_1(t)` — 12-month minus 1-month price return

Absolute scoring thresholds (each component → -1, -0.5, 0, +0.5, or +1):

| Metric | +1 | +0.5 | −0.5 | −1 |
|---|---|---|---|---|
| RevCAGR3Y | >20% | >10% | <2% | <0% |
| OpMargin3Y | >15% | >8% | <2% | <0% |
| FCFMargin3Y | >10% | >3% | <0% | <-5% |
| RevAccel | >5pp | >1pp | <-1pp | <-5pp |
| OpMarginTrend | >2pp | >0 | <-1pp | <-3pp |
| ValMetric (EV/Sales) | <2x | <5x | >15x | >30x |
| Mom12_1 | >20% | >5% | <-5% | <-20% |

Composite = weighted mean of component scores (same WEIGHTS dict from CompositScoreExperiment).

Returns:
```json
{
  "composite_score": 0.68,
  "label": "strong compounder",
  "rev_cagr_3y": 0.22,
  "op_margin_3y": 0.18,
  "fcf_margin_3y": 0.14,
  "rev_accel": 0.03,
  "mom_12_1": 0.31,
  "val_metric": 8.4,
  "val_type": "EV/Sales"
}
```

Qualitative labels: `composite > 0.5` → "strong compounder", `> 0` → "above average", `< 0` → "below average", `< -0.5` → "deteriorating".

### `get_revenue_growth(symbol: str, periods: int = 4) -> dict`

Reimplement the quarterly revenue QoQ scoring logic (algorithm from `RevenueGrowthExperiment1.py`, code written fresh in the new server):
- Fetch `t.quarterly_financials["Total Revenue"]`, sort chronologically
- Compute last `periods` QoQ pct changes
- Weighted score = `sum(max(0, r)) / sum(|r|)` over the growth rates

Add acceleration detection:
```python
# 2+ consecutive negative QoQ growth rates in last 3 quarters = decelerating
decel_flag = sum(1 for r in growth_rates[-3:] if r < 0) >= 2
```

Returns:
```json
{
  "weighted_qoq_score": 0.73,
  "growth_rates": [0.04, 0.12, 0.08, 0.11],
  "quarters_used": 4,
  "trend": "accelerating",        // "accelerating", "decelerating", "mixed"
  "consecutive_decel_quarters": 0
}
```

### `get_earnings_acceleration(symbol: str) -> dict`

Reimplement the EPS acceleration scoring logic (algorithm from `EarningsAccelerationExperiment.py`, code written fresh):
- Fetch `t.quarterly_income_stmt["Net Income"]`, take 5 most recent quarters oldest-first
- Compute 4 QoQ growth rates using `(curr − prev) / abs(prev)`
- Compute 3 acceleration deltas between adjacent rates
- `accel_score = accel_count / accel_total`

---

### `get_fundamental_history(symbol: str, data_type: str, since_days: int = 365) -> dict`

New tool exposing the historical record for any data type. Uses `cache_history()`:

```python
@mcp.tool()
def get_fundamental_history(symbol: str, data_type: str, since_days: int = 365) -> dict:
    """
    Return historical snapshots of a fundamentals data type for trend analysis.

    data_type: "fundamental_score" | "revenue_growth" | "earnings_acceleration" | "earnings_calendar"
    since_days: how far back to look (default 365)
    """
```

Returns:
```json
{
  "symbol": "NVDA",
  "data_type": "fundamental_score",
  "since_days": 365,
  "snapshots": [
    {"fetched_at": "2025-01-15", "composite_score": 0.52, "rev_cagr_3y": 0.35, ...},
    {"fetched_at": "2025-02-14", "composite_score": 0.61, "rev_cagr_3y": 0.41, ...},
    ...
  ],
  "trend": "improving",       // "improving" | "deteriorating" | "stable" | "insufficient_data"
  "snapshot_count": 12
}
```

Trend is computed from the first and last `composite_score` in the window (or `weighted_qoq_score` for revenue_growth, `accel_score` for earnings_acceleration).

Returns:
```json
{
  "accel_count": 2,
  "accel_total": 3,
  "avg_accel_delta": 0.08,
  "qoq_rates": [0.12, 0.18, 0.25, 0.31],
  "accel_score": 0.67,            // accel_count / accel_total (0-1)
  "trend": "accelerating"
}
```

---

## File 3: `fastMCPTest/stock_price_server.py` (modify)

### New import (line 12 area, after existing market_analysis_server import):
```python
from company_fundamentals_server import (
    get_earnings_calendar, get_fundamental_score,
    get_revenue_growth, get_earnings_acceleration,
)
```

### New tool: `get_relative_strength(symbol: str, benchmark: str = "SPY") -> dict`

Add before `get_trade_recommendation` (~line 2436). Uses `get_history()` from `ohlcv_cache` (already imported).

```python
@mcp.tool()
def get_relative_strength(symbol: str, benchmark: str = "SPY") -> dict:
    # Fetch 400d of history for symbol + SPY + QQQ
    # Compute 1m/3m/6m/12m returns for each
    # alpha_12m = stock 12m return − SPY 12m return
    # RS label: alpha > 10% = "leader", alpha < -10% = "laggard", else "neutral"
```

Returns:
```json
{
  "return_1m": 0.04,
  "return_3m": 0.12,
  "return_6m": 0.18,
  "return_12m": 0.35,
  "spy_return_12m": 0.18,
  "alpha_12m": 0.17,
  "rs_label": "leader",    // "leader" | "neutral" | "laggard"
  "sector_etf": "SMH"      // from yf.Ticker(sym).info["sector"] → ETF mapping
}
```

Sector → ETF mapping (hardcoded dict, covers major sectors): Technology→XLK, Semiconductors→SMH, Energy→XLE, Healthcare→XLV, Financials→XLF, Industrials→XLI, Consumer Discretionary→XLY, Utilities→XLU, Materials→XLB, Real Estate→XLRE, Communication→XLC.

### Modify `get_trade_recommendation` (lines 2437–2946)

**After Signal 13 block (line ~2784), add 5 new signal blocks:**

**Signal 14 — Earnings Calendar** (fetch early for blackout override):
```python
# ── 14. Earnings Calendar ─────────────────────────────────────────────
earnings_data = None
days_to_earnings = None
try:
    earnings_data = get_earnings_calendar(sym)
    days_to_earnings = earnings_data.get("days_to_earnings")
    blackout = earnings_data.get("blackout", False)
    avg_move = earnings_data.get("avg_historical_move_pct")

    if blackout and days_to_earnings is not None and days_to_earnings < 7:
        warnings.append(f"EARNINGS IN {days_to_earnings}d — IV crush risk. Avoid options unless net_score ≥ 7.")
    elif blackout:
        warnings.append(f"Earnings in {days_to_earnings}d — elevated IV risk zone.")
    elif earnings_data.get("earnings_risk") == "PRE_EARNINGS_ZONE":
        bull_score += 1
        drivers.append(f"Pre-earnings zone ({days_to_earnings}d out) — IV expansion tailwind for long calls")

    if avg_move:
        warnings.append(f"Historical earnings move: avg ±{avg_move:.1f}%")

    signals_collected += 1
except Exception:
    pass
```

**Signal 15 — Fundamental Score:**
```python
try:
    fund_data = get_fundamental_score(sym)
    fund_score = fund_data.get("composite_score")
    if fund_score is not None:
        if fund_score > 0.5:
            bull_score += 2
            drivers.append(f"Fundamentals: {fund_data.get('label')} (score {fund_score:.2f})")
        elif fund_score < -0.5:
            bear_score += 2
            drivers.append(f"Fundamentals: {fund_data.get('label')} (score {fund_score:.2f}) — avoid longs")
    signals_collected += 1
except Exception:
    pass
```

**Signal 16 — Revenue Growth:**
```python
try:
    rev_data = get_revenue_growth(sym)
    if rev_data.get("trend") == "accelerating" and rev_data.get("weighted_qoq_score", 0) > 0.6:
        bull_score += 1
        drivers.append(f"Revenue accelerating (QoQ score {rev_data['weighted_qoq_score']:.2f})")
    elif rev_data.get("consecutive_decel_quarters", 0) >= 2:
        bear_score += 1
        drivers.append(f"Revenue decelerating — {rev_data['consecutive_decel_quarters']} consecutive down quarters")
    signals_collected += 1
except Exception:
    pass
```

**Signal 17 — EPS Acceleration:**
```python
try:
    eps_data = get_earnings_acceleration(sym)
    accel_score = eps_data.get("accel_score")
    if accel_score is not None:
        if accel_score >= 0.67 and eps_data.get("avg_accel_delta", 0) > 0:
            bull_score += 1
            drivers.append(f"EPS accelerating ({eps_data['accel_count']}/{eps_data['accel_total']} qtrs, avg +{eps_data['avg_accel_delta']:.1%}/qtr)")
        elif accel_score < 0.34:
            bear_score += 1
            drivers.append(f"EPS decelerating ({eps_data['accel_count']}/{eps_data['accel_total']} qtrs accelerating)")
    signals_collected += 1
except Exception:
    pass
```

**Signal 18 — Relative Strength:**
```python
try:
    rs_data = get_relative_strength(sym)
    rs_label = rs_data.get("rs_label")
    alpha = rs_data.get("alpha_12m", 0)
    if rs_label == "leader":
        bull_score += 2
        drivers.append(f"RS leader: +{alpha:.1%} alpha vs SPY (12m)")
    elif rs_label == "laggard":
        bear_score += 1
        drivers.append(f"RS laggard: {alpha:.1%} alpha vs SPY (12m) — weak relative strength")
    signals_collected += 1
except Exception:
    pass
```

**Earnings blackout override** — add after Trade Type Selection block (~line 2825), before the Options Context section:
```python
# Earnings blackout override: < 7 days to earnings AND options trade AND weak conviction
if (days_to_earnings is not None and days_to_earnings < 7
        and is_options and net_score < 7):
    trade_type = "SKIP"
    action = "HOLD"
    warnings.append(
        f"EARNINGS BLACKOUT: {days_to_earnings}d to earnings — forced SKIP to avoid IV crush risk"
    )
```

---

## File 4: `.mcp.json` (modify)

Add to `mcpServers` object (adjust path prefix to match repo location):
```json
"company-fundamentals-server": {
  "command": "/path/to/.venv/bin/fastmcp",
  "args": ["run", "/path/to/fastMCPTest/company_fundamentals_server.py"]
}
```

The path prefix must match the existing entries in the file (currently `/Users/thomasfowler/source/...`). On the implementation machine this will differ.

---

## Updated Signal Table

| # | Signal | Max Bull | Max Bear | Status |
|---|---|---|---|---|
| 1 | BB position + P/C ratio | +2/+1 | +2/+1 | existing |
| 2 | RSI | +3 | +3 | existing |
| 3 | MACD | +2 | +2 | existing |
| 4 | Stochastic | +2 | +2 | existing |
| 5 | Volume / OBV | +3 | +2 | existing |
| 6 | Candlestick patterns | +1 | +1 | existing |
| 7 | Unusual calls | +2 | — | existing |
| 8 | Stop loss | — | — | existing |
| 9 | Short interest | +1 | — | existing |
| 10 | Dark pool | +2 | +2 | existing |
| 11 | Bid/ask spread | +1 | +1 | existing |
| 12 | DAOI — MM hedge flows | +2 | — | existing |
| 13 | Options market positioning | +1 | +1 | existing |
| 14 | Earnings calendar | +1 | override SKIP | **NEW** |
| 15 | Fundamental score | +2 | +2 | **NEW** |
| 16 | Revenue growth | +1 | +1 | **NEW** |
| 17 | EPS acceleration | +1 | +1 | **NEW** |
| 18 | Relative strength | +2 | +1 | **NEW** |

Max net_score rises from ~22 to ~29. Existing SKIP/BUY/SELL thresholds (−2 to +5) remain unchanged — the new signals shift the distribution but don't require recalibration.

---

## Verification

1. Start new server: `cd fastMCPTest && python company_fundamentals_server.py` — confirm it lists 4 tools and exits cleanly.
2. Spot-check each tool:
   - `get_earnings_calendar("AAPL")` — `days_to_earnings` should be a plausible near-future number; `earnings_risk` should match that distance.
   - `get_fundamental_score("NVDA")` — `rev_cagr_3y` should be large positive (NVDA ~120% CAGR in recent years); `composite_score` should be strongly positive.
   - `get_revenue_growth("NVDA")` — `weighted_qoq_score` near 1.0 (nearly all positive quarters).
   - `get_relative_strength("NVDA")` — `alpha_12m` strongly positive vs SPY, `rs_label = "leader"`.
3. **Cache verification:** Call `get_fundamental_score("NVDA")` twice — second call should return instantly (no yfinance network request). Confirm `data/quantcore.sqlite` exists with rows in `fundamentals_history` table with rows in `fundamentals_history` table. Set `FUNDAMENTALS_CACHE_TTL_HOURS=0` and confirm the second call re-fetches and adds a second row. Call `get_fundamental_history("NVDA", "fundamental_score")` — should return both snapshots with `snapshot_count: 2`.
4. Call `get_trade_recommendation("AMZN", 5000)` — verify `signals_collected` is now 18 (was 13).
5. Call `get_trade_recommendation` on a stock within 7 days of earnings — confirm `EARNINGS BLACKOUT` warning and `trade_type: SKIP`.
6. Run `python -m unittest discover` from repo root — all existing tests must still pass.