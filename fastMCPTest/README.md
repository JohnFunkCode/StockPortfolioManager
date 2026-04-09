# Stock Portfolio MCP Analysis Tools

A suite of technical analysis tools built on [FastMCP](https://github.com/jlowin/fastmcp) and [yfinance](https://pypi.org/project/yfinance/), designed to identify **bounce bottoms**, **bearish setups**, and **put/call trade opportunities** from a watchlist.

---

## Quick Start

```bash
# Start Claude Code from this directory — servers start automatically via .mcp.json
cd fastMCPTest
claude

# Run the options analysis CLI directly
python options_analysis.py --watchlist ../watchlist.yaml --puts-budget 1000 --top-n 15
```

**Requirements:** `fastmcp`, `yfinance`, `numpy`, `pandas`, `pyyaml`

```bash
pip install fastmcp yfinance numpy pandas pyyaml
```

---

## Architecture

| File | Type | Purpose |
|------|------|---------|
| `stock_price_server.py` | MCP Server | Price, momentum, structure, options flow, and stop loss tools |
| `market_analysis_server.py` | MCP Server | Market microstructure: short interest, dark pool, spreads |
| `options_analysis.py` | CLI Script | Watchlist-level put/call scoring and trade recommendations |
| `options_store.py` | Shared Library | SQLite persistence for options chain snapshots; enables backtesting |
| `ohlcv_cache.py` | Shared Library | SQLite-backed OHLCV bar cache; eliminates redundant yfinance calls |
| `.mcp.json` | Config | Registers both MCP servers for auto-start |

---

## Options Chain Persistence (`options_store.py`)

Saves a full options chain snapshot to SQLite every time `options_analysis.py` runs or `get_stock_price` is called via MCP. Snapshots accumulate over time and enable backtesting of put/call ratio trends, IV rank history, and trade outcome validation.

### How it works

Each snapshot captures the full state of a security at a point in time: price, Bollinger Bands, the nearest expiration's call and put chains (all ATM contracts), and aggregate metrics (P/C ratio, total OI, total volume, average IV). Snapshots are idempotent — a duplicate `(symbol, captured_at)` pair is silently ignored, so running the analysis multiple times on the same day will not create duplicates.

### Auto-integration

Both entry points save snapshots automatically with no extra configuration:

- **`options_analysis.py`** — saves one snapshot per symbol after fetching live data. Pass `--no-persist` to skip, or `--db /path/to/file.db` to use a custom database path.
- **`stock_price_server.py` `get_stock_price()`** — saves a snapshot after every MCP tool call. Failures are fire-and-forget so the tool never fails due to database issues.

### SQLite schema

Three tables with WAL mode and cascading foreign key deletes:

```sql
-- One row per symbol per fetch
CREATE TABLE options_snapshots (
    snapshot_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT    NOT NULL,
    captured_at  TEXT    NOT NULL,   -- ISO-8601 UTC timestamp
    price        REAL    NOT NULL,
    bb_upper     REAL,
    bb_middle    REAL,
    bb_lower     REAL,
    bb_period    INTEGER,
    UNIQUE (symbol, captured_at)
);

-- One row per expiration per snapshot
CREATE TABLE options_expirations (
    expiration_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id      INTEGER NOT NULL REFERENCES options_snapshots(snapshot_id) ON DELETE CASCADE,
    expiration       TEXT    NOT NULL,
    put_call_ratio   REAL,
    total_call_oi    INTEGER,
    total_put_oi     INTEGER,
    total_call_vol   INTEGER,
    total_put_vol    INTEGER,
    avg_call_iv_pct  REAL,
    avg_put_iv_pct   REAL
);

-- One row per ATM contract per expiration
CREATE TABLE options_contracts (
    contract_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    expiration_id  INTEGER NOT NULL REFERENCES options_expirations(expiration_id) ON DELETE CASCADE,
    kind           TEXT    NOT NULL CHECK(kind IN ('call','put')),
    strike         REAL    NOT NULL,
    last_price     REAL,
    bid            REAL,
    ask            REAL,
    implied_vol    REAL,
    volume         INTEGER,
    open_interest  INTEGER,
    in_the_money   INTEGER
);
```

### Public API

```python
from options_store import OptionsStore

store = OptionsStore()                          # default: options_chain.db next to script
store = OptionsStore("/path/to/custom.db")      # custom path

# Save a snapshot (returns snapshot_id, or None if duplicate)
snapshot_id = store.save_snapshot(symbol, price, bollinger_bands_dict, options_dict)

# P/C ratio time series for backtesting (last N days)
history = store.get_pc_history("AMD", days=30)
# → [{"captured_at": "2026-04-09T...", "put_call_ratio": 1.4, "price": 231.82, ...}, ...]

# Full snapshot with all expirations and contracts
snapshot = store.get_latest_snapshot("AMD")

# Filtered snapshot history
snapshots = store.get_snapshots("AMD", since="2026-03-01", limit=50)

# Inventory helpers
symbols = store.get_symbols()           # → ["AAPL", "AMD", ...]
count   = store.snapshot_count()        # → 153
```

### CLI flags

```bash
# Use a custom database path
python options_analysis.py --db /data/options_history.db

# Skip persistence entirely (quick one-off run)
python options_analysis.py --no-persist --symbol AAPL
```

### Data limitations

- Only ATM contracts (5 nearest strikes each side) are stored per expiration. Full-chain storage is not implemented.
- Only the nearest expiration is stored per snapshot (the expiration used by the scoring engine).
- Timestamps are captured at fetch time. During off-hours, the price reflects the last close.

---

## OHLCV Cache (`ohlcv_cache.py`)

All three MCP tools previously called `ticker.history()` independently for each analysis function. For a 7-symbol deep analysis (RSI + MACD + stochastic + OBV + VWAP + candlesticks + higher lows + volume + gap analysis × 7 symbols) this produced ~63 redundant HTTP requests. The cache reduces this to ~7 on subsequent runs.

### How it works

On first call for a symbol/interval pair (cold start), the cache fetches the maximum useful history from yfinance and stores it in SQLite (`ohlcv_cache.db`). Subsequent calls for the same symbol are served entirely from the database unless a bar needs refreshing.

The cache fetches from yfinance only when:
1. No cached data exists for the symbol/interval — cold start
2. An OPEN bar exists (today's bar during market hours needs refreshing)
3. The most recent CLOSED bar is at least 1 trading day old — incremental update

### Bar status

Each stored bar carries a `BarStatus` that tracks its lifecycle:

| Status | Meaning |
|--------|---------|
| `OPEN` | Bar is currently forming — data is live and will change |
| `CLOSED` | Bar interval has ended — data is final and will not change |
| `GAP` | No trades occurred during this time slot — synthetic placeholder, excluded from results |
| `CORRECTED` | A previously-CLOSED bar was re-fetched with a materially different close price (>0.1%), indicating a split adjustment or exchange correction |

`CORRECTED` detection catches split adjustments automatically: if yfinance returns adjusted prices that differ from the cached close by more than 0.1%, the bar status is upgraded to `CORRECTED` and the stored price is updated.

### Public API

```python
from ohlcv_cache import get_history, period_to_days

# Returns pd.DataFrame matching yfinance ticker.history() format
# columns: Open, High, Low, Close, Volume
# index: DatetimeIndex (UTC)
hist = get_history("AAPL", "1d", days=180)

# Convert yfinance period string to calendar days
days = period_to_days("6mo")   # → 182
days = period_to_days("2y")    # → 730
```

### Warm-up window

On cold start the cache is pre-populated with the maximum useful history so all downstream tools are fully served from cache on the second run:

| Interval | Warm-up window |
|----------|---------------|
| `1d` | 730 days (2 years) |
| `1wk` | 1825 days (5 years) |
| `1mo` | 3650 days (10 years) |
| `1h` / `30m` / `15m` | 59 days (yfinance limit) |

### SQLite schema

```sql
CREATE TABLE ohlcv (
    symbol   TEXT    NOT NULL,
    interval TEXT    NOT NULL,
    ts       INTEGER NOT NULL,   -- UTC Unix seconds
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   INTEGER NOT NULL,
    status   TEXT    NOT NULL    -- OPEN | CLOSED | GAP | CORRECTED
        CHECK(status IN ('OPEN','CLOSED','GAP','CORRECTED')),
    PRIMARY KEY (symbol, interval, ts)
);

-- Fast lookups by symbol/interval
CREATE INDEX idx_ohlcv_lookup ON ohlcv (symbol, interval, ts DESC);

-- Partial index for bars that need attention
CREATE INDEX idx_ohlcv_needs_action ON ohlcv (symbol, interval)
    WHERE status IN ('OPEN', 'CORRECTED');

CREATE TABLE fetch_log (
    symbol     TEXT    NOT NULL,
    interval   TEXT    NOT NULL,
    fetched_at INTEGER NOT NULL,   -- UTC Unix seconds
    PRIMARY KEY (symbol, interval)
);
```

### Integration

All `ticker.history()` calls in `stock_price_server.py`, `market_analysis_server.py`, and `options_analysis.py` route through `get_history()`. Live-only calls (fast_info, options chains, news) still use `yf.Ticker()` directly.

---

## Server 1: `stock-price-server`

**File:** `stock_price_server.py`

Provides 15 tools covering price data, momentum indicators, volume analysis, options flow, price structure patterns, historical drawdown analysis, and synthesised stop loss recommendations. All data sourced from Yahoo Finance via yfinance.

---

### `get_stock_price(symbol)`

**What it does:** Returns current price, 20-day Bollinger Bands (2σ), and the full ATM options chain summary for the nearest expiration.

**Returns:**
- `price` — current last price
- `bollinger_bands` — `upper`, `middle` (20-day SMA), `lower`, `period`, `std_dev`
- `options` — `expiration`, `put_call_ratio`, `calls` (ATM 5 strikes), `puts` (ATM 5 strikes), avg IV %

**Application notes:**
- Bollinger Band position is the primary oversold/overbought gauge. Price at or below the lower band (pos ≤ 0) is a technical bounce setup.
- The put/call ratio from this tool is OI-based from the nearest expiry only. For richer P/C analysis use `get_unusual_calls` or the options_analysis.py CLI.
- Use as the first call in any analysis — establishes the price anchor all other tools reference.
- Every call to this tool automatically saves a snapshot to `options_chain.db` via `OptionsStore`. Snapshots accumulate passively over time and enable backtesting without any extra steps.

---

### `get_rsi(symbol, period=14, interval='1d')`

**What it does:** Calculates the Relative Strength Index using Wilder's exponential moving average method.

**Parameters:**
- `period` — lookback window (default 14; common alternatives: 9, 21)
- `interval` — `'1d'` daily, `'1wk'` weekly, `'1mo'` monthly

**Returns:**
- `rsi` — current RSI value (0–100)
- `signal` — `'oversold'` (≤30), `'overbought'` (≥70), or `'neutral'`
- `last_close`, `interval`, `period`

**Application notes:**
- RSI < 30 = classic oversold; RSI < 20 = extreme oversold, often coincides with bounce bottoms.
- **RSI divergence** is a stronger signal than level alone: if price makes a new low but RSI does not, accumulation is occurring beneath the surface. This tool returns a snapshot; track across multiple calls to detect divergence.
- Weekly RSI < 40 on a stock with daily RSI < 30 = multi-timeframe oversold confirmation — high-confidence bounce candidate.
- RSI > 70 while Bollinger position > 0.85 = double overbought — put candidate.

---

### `get_macd(symbol, interval='1d')`

**What it does:** Calculates MACD using standard EMA parameters (fast=12, slow=26, signal=9).

**Parameters:**
- `interval` — `'1d'` daily (default), `'1wk'` weekly, `'1mo'` monthly

**Returns:**
- `macd` — MACD line value
- `signal` — signal line value
- `histogram` — MACD minus signal (positive = bullish momentum)
- `crossover` — `'bullish_crossover'`, `'bearish_crossover'`, `'bullish'`, or `'bearish'`
- `last_close`

**Application notes:**
- `bullish_crossover` = MACD line just crossed above signal line — momentum turning positive, often precedes a multi-day bounce.
- A **positive and expanding histogram** (increasing each bar) = accelerating upward momentum. Most reliable when it begins expanding near the lower Bollinger Band.
- MACD divergence: price makes a new low but MACD histogram makes a higher low = hidden bullish momentum.
- For short-dated options trades (Apr 2 expiry), daily MACD crossover is the entry trigger. For longer-dated positions, use weekly MACD.

---

### `get_stochastic(symbol, k_period=14, d_period=3, interval='1d')`

**What it does:** Calculates the Stochastic Oscillator (%K fast line and %D signal line).

**Parameters:**
- `k_period` — lookback window for %K calculation (default 14)
- `d_period` — SMA period for %D signal line (default 3)
- `interval` — `'1d'`, `'1wk'`, or `'1mo'`

**Returns:**
- `k` — %K value (0–100)
- `d` — %D value (0–100)
- `signal` — `'oversold'` (≤20), `'overbought'` (≥80), or `'neutral'`
- `crossover` — `'bullish_crossover'`, `'bearish_crossover'`, `'bullish'`, or `'bearish'`

**Application notes:**
- **Most reliable bounce signal:** %K crosses above %D while both are below 20 (oversold zone). This two-bar confirmation is the stochastic's strongest buy signal.
- A **bearish crossover** (%K drops below %D) while RSI and MACD are still bullish = early warning of short-term momentum fade before a deeper pullback or consolidation. This divergence often precedes 3–7 day consolidations.
- Stochastic resets to oversold faster than RSI, making it better for timing short-term entries after a bounce begins.
- Use weekly stochastic to confirm the longer trend isn't breaking down before acting on daily oversold signals.

---

### `get_volume_analysis(symbol, lookback=20, interval='1d')`

**What it does:** Detects volume climax (capitulation) bars and OBV divergence to identify exhaustion bottoms.

**Parameters:**
- `lookback` — rolling window for averages (default 20)
- `interval` — `'1d'` or `'1wk'`

**Returns:**
- `climax_events` — list of bars where volume ≥ 2× average AND bar range ≥ 1.5× average range, each with `direction`, `volume_ratio`, `bar_range_pct`, `quiet_follow_through`, `interpretation`
- `obv_divergence` — `true` if price made a new low but OBV did not
- `bottom_signal` — `'strong'`, `'moderate'`, `'weak'`, or `'none'` with description
- `last_volume_ratio` — today's volume vs 20-day average

**Climax detection thresholds:**
- Volume ≥ 2× rolling average
- Bar range ≥ 1.5× average bar range

**Application notes:**
- A **down-day climax bar** (high volume + wide range + red candle) = sellers exhausted. The single most reliable single-bar bottom signal.
- **Quiet follow-through** = the bar immediately after a climax has volume ≤ 0.6× average. Sellers are gone. Classic two-bar capitulation bottom pattern.
- `bottom_signal = 'strong'` requires all three: climax + quiet follow-through + OBV divergence. This combination has historically high accuracy for identifying multi-day bounce lows.
- A **climax on an up day** signals exhaustion of buyers — useful as a bearish signal when price is near the upper Bollinger Band.

---

### `get_obv(symbol, lookback=20, interval='1d')`

**What it does:** Calculates On-Balance Volume, detects trend, and identifies bullish/bearish divergence with strength scoring.

**Parameters:**
- `lookback` — window for trend and divergence calculation (default 20)
- `interval` — `'1d'`, `'1wk'`, or `'1mo'`

**Returns:**
- `last_obv` — current OBV value
- `obv_trend` — `'rising'`, `'falling'`, or `'flat'`
- `price_trend` — same for price
- `divergence` — `'bullish'`, `'bearish'`, or `'none'`
- `divergence_strength` — `'strong'` (slope diff >0.6), `'moderate'` (>0.3), `'weak'`, or `'none'`
- `interpretation` — plain-English summary
- `recent_bars` — last 10 bars with date, close, volume, OBV value, direction

**Application notes:**
- **Bullish divergence** (OBV rising while price falls) = institutions buying quietly while retail sells. The strongest accumulation signal available from public data.
- `divergence_strength = 'strong'` means the OBV trend is clearly rising while the price trend is clearly falling — high confidence that smart money is positioned for a reversal.
- OBV confirming a price uptrend = healthy trend; watch for OBV to flatten or fall while price still rises (bearish divergence — distribution).
- OBV is superior to raw volume for trend analysis because it's cumulative and directional. A rising OBV during a sideways/declining price is more reliable than any single volume spike.

---

### `get_vwap(symbol, lookback=20, interval='1d')`

**What it does:** Calculates rolling VWAP using typical price `(H+L+C)/3 × volume` and detects reclaim events.

**Parameters:**
- `lookback` — rolling window for VWAP calculation (default 20)
- `interval` — `'1d'`, `'1wk'`, or `'1mo'`

**Returns:**
- `vwap` — current rolling VWAP value
- `position` — `'above_vwap'` or `'below_vwap'`
- `distance_pct` — % distance from VWAP (negative = below)
- `consecutive_bars_above/below` — streak length in current position
- `reclaim_signal` — `true` if price crossed above VWAP in last 3 bars
- `reclaim_strength` — `'strong'`, `'moderate'`, `'weak'`, or `'none'`
- `crossover_events` — all recent VWAP crossovers with date, type, volume ratio, high-volume flag

**Reclaim strength criteria:**
- **Strong:** reclaim bar + held ≥2 consecutive bars above VWAP + above-average volume
- **Moderate:** reclaim + either above-average volume OR ≥2 bars held
- **Weak:** reclaim bar only, light volume — unconfirmed

**Application notes:**
- A **strong VWAP reclaim** is one of the highest-probability intraday-to-swing bounce entry signals used by institutional traders. It means buyers retook the average cost basis of all participants over the lookback window.
- Price extended >3% above VWAP = extended, reversion risk. Not an ideal long entry; wait for a pullback toward VWAP.
- Price more than 5% below VWAP = deeply discounted relative to recent fair value. Watch for the reclaim cross as an entry trigger.
- `consecutive_bars_above` ≥ 5 with VWAP trending up = confirmed uptrend. `consecutive_bars_below` ≥ 10 = persistent downtrend, wait for reclaim before buying.

---

### `get_candlestick_patterns(symbol, lookback=10, interval='1d')`

**What it does:** Scans recent bars for Hammer, Doji, and reversal candlestick patterns with strength scoring.

**Parameters:**
- `lookback` — bars to scan (default 10)
- `interval` — `'1d'`, `'1wk'`, or `'1mo'`

**Patterns detected:**

| Pattern | Bias | Shape criteria |
|---------|------|---------------|
| `hammer` | Bullish | Body ≤35% of range, lower wick ≥55%, upper wick ≤10% — after downtrend |
| `dragonfly_doji` | Bullish | Doji with lower wick ≥40%, close near high |
| `inverted_hammer` | Bullish | Body ≤35%, upper wick ≥55%, lower wick ≤10% — after downtrend |
| `doji` | Neutral | Body ≤10% of range |
| `long_legged_doji` | Neutral | Doji with long wicks both sides |
| `gravestone_doji` | Bearish | Doji with upper wick ≥40%, close near low |
| `shooting_star` | Bearish | Body ≤35%, upper wick ≥55% — after uptrend |
| `hanging_man` | Bearish | Same as hammer shape but after uptrend |

**Strength scoring (+points):**
- Base pattern shape: 1–3 pts
- Near lower Bollinger Band (bullish): +2 pts
- Above-average volume: +1 pt
- ≥3 consecutive down days prior: +1 pt

**Strength levels:** `strong` (≥6), `moderate` (4–5), `weak` (2–3), `minimal` (<2)

**Application notes:**
- A **strong hammer** (score ≥6) at the lower Bollinger Band after 3+ down days on above-average volume is one of the highest-conviction single-bar reversal signals in technical analysis.
- Doji patterns alone are weak signals — they indicate indecision, not direction. Their value comes from context: a dragonfly doji after a sustained downtrend near a support level is meaningful; a doji in the middle of a range is noise.
- **Never act on a single candlestick signal in isolation.** Combine with RSI oversold, MACD or stochastic crossover, and VWAP reclaim for confirmation.
- The `prior_down_days` field is key — hammer patterns after 1–2 down days are far less reliable than those after 4–5 consecutive declining closes.

---

### `get_higher_lows(symbol, swing_bars=3, lookback_swings=6, interval='1h')`

**What it does:** Detects higher-low price structure — the first structural sign of a downtrend reversing.

**Parameters:**
- `swing_bars` — bars on each side required for a pivot low (default 3)
- `lookback_swings` — number of recent swing lows to evaluate (default 6)
- `interval` — `'15m'`, `'30m'`, `'1h'` (default), or `'1d'`

**Returns:**
- `higher_low_pattern` — `true` if recent swing lows form a rising series
- `consecutive_higher_lows` — count of consecutive higher lows
- `min_rise_between_lows_pct` — smallest rise between adjacent lows
- `pattern_strength` — `'strong'`, `'moderate'`, `'weak'`, or `'none'`
- `trend_before_lows` — `'downtrend'`, `'uptrend'`, or `'sideways'`
- `swing_lows` — list of detected pivots with date, low, close, high

**Strength criteria:**
- **Strong:** ≥3 consecutive higher lows, each rising >0.3%
- **Moderate:** 2 consecutive higher lows rising >0.3%
- **Weak:** 2 higher lows with <0.3% separation

**Application notes:**
- Defaults to `'1h'` interval because higher lows form on intraday charts before they appear on daily charts — this tool catches the reversal earlier.
- Higher lows are **only meaningful after a downtrend**. The `trend_before_lows` field makes this explicit — a strong pattern after a `'downtrend'` is far more significant than the same pattern after a sideways period.
- This is the **first structural reversal signal** — it confirms that sellers are losing control even before price makes a higher high. Combine with a daily MACD crossover and VWAP reclaim for a complete setup.
- The right-side confirmation gap means the last `swing_bars` bars are excluded — this avoids premature signals from unconfirmed pivots.

---

### `get_gap_analysis(symbol, min_gap_pct=0.5, lookback=60, interval='1d')`

**What it does:** Detects price gaps and tracks fill status (filled/partially filled/unfilled) to identify support/resistance magnets.

**Parameters:**
- `min_gap_pct` — minimum gap size as % of prior close (default 0.5%)
- `lookback` — bars to scan (default 60)
- `interval` — `'1d'` or `'1h'`

**Returns:**
- `nearest_gap_above` — first unfilled gap above current price (overhead resistance)
- `nearest_gap_below` — nearest unfilled gap below current price (support)
- `bounce_targets` — annotated list with distance % and interpretation
- `all_gaps` — complete list with `direction`, `gap_top`, `gap_bottom`, `fill_status`, `fill_date`
- `unfilled_count`, `partial_count`, `filled_count`

**Fill status logic:**
- `filled` — a later bar's high ≥ gap_top AND low ≤ gap_bottom
- `partially_filled` — later bar entered but didn't close the gap zone
- `unfilled` — no subsequent bar has entered the zone

**Application notes:**
- Markets have a strong statistical tendency to return and fill gaps. **Unfilled gap-down zones above current price** are the first overhead targets when a bounce begins — price often rallies to fill the gap before pulling back.
- An **unfilled gap-down below current price** is a support magnet. A stock sitting just above an unfilled gap-down often bounces from that level.
- **Partially filled gaps** indicate active buyer/seller interest in a zone — the market is already working through that level.
- Gap analysis is most powerful on the **daily chart** where gaps represent genuine overnight supply/demand imbalances. Hourly gaps are less significant but useful for intraday targets.
- Avoid buying into a large unfilled gap overhead — the gap fill will act as resistance and may cap the bounce.

---

### `get_unusual_calls(symbol, min_volume=100, min_vol_oi_ratio=0.5, max_expirations=3)`

**What it does:** Detects unusual call option activity using volume/OI ratio and aggressive-fill proxies as sweep indicators.

**Parameters:**
- `min_volume` — minimum contract volume to consider (default 100)
- `min_vol_oi_ratio` — minimum vol/OI ratio to flag (default 0.5)
- `max_expirations` — expirations to scan (default 3, nearest)

**Sweep score per contract (0–10):**

| Signal | Points |
|--------|--------|
| vol/OI ≥ 2.0 | +3 |
| vol/OI 1.0–2.0 | +2 |
| vol/OI 0.5–1.0 | +1 |
| last ≥ ask (aggressive fill) | +2 |
| last ≥ mid (above midpoint) | +1 |
| 5–15% OTM (directional bet) | +2 |
| 1–5% OTM (near-money) | +1 |
| In-the-money | −1 |

**Returns:**
- `sweep_signal` — `'strong'`, `'moderate'`, `'weak'`, or `'none'`
- `unusual_calls` — top 20 contracts sorted by sweep score, each with all pricing/flow fields
- `interpretation` — plain-English summary

**Application notes:**
- **vol/OI > 1.0** is the most important signal: more contracts traded today than exist in open interest. This definitively means new positioning — buyers are not closing existing puts, they are opening new bullish bets.
- **last ≥ ask** (aggressive fill) is the clearest sweep proxy available without tape access. Someone paid up — they were urgent. Urgency implies conviction.
- **OTM calls with sweep scores ≥7** are the highest-conviction signal. Institutions buying out-of-the-money calls are making a directional bet with defined risk — they expect a move.
- This tool scans the nearest 3 expirations. An unusual call sweep on a near-dated expiry (days away) implies a catalyst is expected imminently. Sweeps on 30–60 day expirations suggest a medium-term repositioning.
- Combine with OBV bullish divergence for confirmation: smart money buying calls AND accumulating shares = high-probability long setup.

---

### `get_delta_adjusted_oi(symbol, max_expirations=3, risk_free_rate=0.045)`

**What it does:** Calculates delta-adjusted open interest (DAOI) using Black-Scholes deltas to measure market-maker directional exposure and identify mechanical hedging flows.

**Parameters:**
- `max_expirations` — expirations to include (default 3)
- `risk_free_rate` — annualised risk-free rate as decimal (default 0.045)

**Returns:**
- `net_daoi_shares` — net share-equivalent delta across all options
- `call_daoi_shares` / `put_daoi_shares` — contribution from each side
- `mm_hedge_bias` — `'buy_on_rally'` or `'sell_on_rally'`
- `mm_note` — plain-English description of MM hedge direction
- `delta_flip_strike` — price level where net delta crosses zero
- `dist_to_flip_pct` — current distance from flip as %
- `gamma_wall_strike` — strike with highest concentration of delta hedging
- `signal` — `'strong'`, `'moderate'`, `'weak'`, or `'none'`
- `by_expiration` — per-expiry DAOI breakdown

**Application notes:**
- **MM hedge bias = `buy_on_rally`** means market makers are net short delta — they must buy stock as price rises. This creates mechanical self-reinforcing buying that **amplifies any bounce**.
- The **delta flip strike** is the single most important price level in this analysis. If current price is within 5% of the flip, MM hedging flows will intensify dramatically as price moves through it — expect acceleration.
- The **gamma wall** is the strike where the most hedging activity is concentrated. Price often gravitates toward the gamma wall (it acts as a magnet) and then stalls there as MM hedges neutralise.
- `signal = 'strong'` requires: MM buy_on_rally + price within 5% of flip + magnitude >10,000 share equivalents. This combination means a mechanical bid is in place right at current price levels.
- Delta-adjusted OI is most powerful near options expiration (weekly Friday) when gamma is at its highest and hedging flows are most concentrated and price-moving.

---

### `get_news(symbol, max_articles=10)`

**What it does:** Fetches recent news articles for a ticker from Yahoo Finance.

**Returns:**
- `articles` — list of `{title, publisher, published, summary, url}`
- `article_count`

**Application notes:**
- Use to identify upcoming earnings, analyst upgrades/downgrades, product announcements, or macro events that could act as catalysts for a bounce.
- Cross-reference the published date against price action — if a negative article caused a selloff but price is now stabilising, the news may be fully priced in.
- Watch for a **golden cross** or **analyst upgrade** story appearing while the stock is technically oversold — narrative + technicals aligning is the highest-confidence bounce setup.

---

### `get_historical_drawdown(symbol, lookback_days=252)`

**What it does:** Calculates historical max drawdown metrics from the SQLite OHLCV cache to establish the noise floor for trailing stop calibration.

**Parameters:**
- `lookback_days` — trading days to look back (default 252 = 1 year)

**Returns:**
- `max_1day_drawdown_pct` — worst single close-to-close drop (negative value)
- `worst_1day_date` — date of the worst 1-day drop
- `max_5day_drawdown_pct` — worst 5-trading-day rolling drop (negative value)
- `worst_5day_start` / `worst_5day_end` — date range of the worst 5-day window
- `max_intraday_drop_pct` — worst single-day High→Low intraday drop
- `recent_max_1day_pct` — worst 1-day drop in the most recent 30 bars (current volatility regime)
- `avg_drawdown_pct` — average of `|max_1day|` and `|max_5day|` — the derived trailing stop floor
- `trailing_stop_pct` — recommended minimum trailing stop % to survive historical volatility
- `lookback_trading_days`, `last_close`

**Noise floor logic:**

The trailing stop percentage is computed as `avg(|max_1day_drawdown|, |max_5day_drawdown|)`. This is the minimum distance a trailing stop must be set to avoid being triggered by a single bad session or a normal 5-day correction. Any trailing stop tighter than this will statistically false-trigger on routine volatility.

**Application notes:**
- Use this tool first before setting any broker trailing stop order. If your intended stop distance is smaller than `trailing_stop_pct`, it will be triggered by noise, not by a genuine trend breakdown.
- `recent_max_1day_pct` vs `max_1day_drawdown_pct` is a regime gauge — if recent volatility is significantly higher than the 1-year baseline, the stock is in an elevated-risk regime and stops should be widened accordingly.
- This tool is the foundation of `get_stop_loss_analysis` — it supplies the noise-floor-calibrated baseline trailing stop that the synthesis tool refines with options and short interest data.
- All data is served from the OHLCV cache; no live yfinance calls are made for historical data.

---

### `get_stop_loss_analysis(symbol, cost_basis=0.0, shares=0, max_expirations=4)`

**What it does:** Synthesises a complete stop loss recommendation by combining price/momentum indicators, options-derived support levels, historical drawdown noise floor, and short interest regime into a structured two-stop output (technical watch level + broker trailing stop).

**Parameters:**
- `symbol` — ticker symbol
- `cost_basis` — average purchase price per share (optional; enables position P&L output)
- `shares` — number of shares held (optional; enables total P&L calculations)
- `max_expirations` — options expirations to include when locating gamma wall (default 4)

**Returns:**

| Section | Key fields |
|---------|-----------|
| `position` | `cost_basis`, `shares`, `unrealized_pnl_per_share`, `unrealized_pnl_pct`, `total_unrealized_pnl`, `pnl_at_technical_stop`, `pnl_at_trailing_stop` |
| `technical` | `above_vwap`, `vwap`, `consecutive_bars`, `sma_20`, `bb_upper/lower`, `rsi`, `macd_crossover`, `gamma_wall`, `primary_support`, `primary_support_price`, `support_levels` |
| `short_interest` | `short_float_pct`, `short_ratio_days`, `squeeze_potential` (`LOW`/`MEDIUM`/`HIGH`), `stop_impact` |
| `drawdown` | `max_1day_pct`, `max_5day_pct`, `max_intraday_pct`, `recent_max_1day_pct`, `base_trailing_stop_pct` |
| `stops` | `technical_stop`, `technical_stop_distance_pct`, `technical_stop_inside_noise_floor`, `trailing_stop_pct`, `trailing_stop_price` |
| `flags` | Array of warning strings (see below) |
| `summary` | Human-readable one-paragraph narrative |

**Support level priority:**

Candidates are evaluated in this priority order; only levels below current price qualify as support (levels above price are resistance and are excluded):

| Priority | Level | Buffer below support |
|----------|-------|---------------------|
| 1 | Gamma wall | 0.8% |
| 2 | VWAP | 1.4% |
| 3 | 20-day SMA | 1.4% |
| 4 | BB lower band | 2.0% |

The highest-priority level below price becomes the `primary_support`. The technical stop = `primary_support × (1 − buffer)`.

**Trailing stop calibration:**

Base trailing stop = `get_historical_drawdown` → `trailing_stop_pct` (noise floor). Adjusted for short interest regime:

| Condition | Adjustment | Rationale |
|-----------|-----------|-----------|
| Short float ≥ 15% AND price below VWAP | −10% (tighter) | High short float in downtrend amplifies breakdowns |
| Short float ≥ 20% AND price above VWAP | +10% (wider) | Potential squeeze provides cushion during dips |
| All other cases | Neutral | No adjustment |

**Flags generated:**

| Flag | Meaning |
|------|---------|
| `technical_stop_inside_noise_floor` | Technical stop distance < max 1-day drawdown — will false-trigger on a bad session; use trailing stop for broker orders |
| `rsi_oversold_NN` | RSI ≤ 35 — potential bounce territory |
| `rsi_overbought_NN` | RSI ≥ 70 — extended, reversion risk |
| `squeeze_potential_high/medium` | Elevated short float — squeeze fuel in place |
| `elevated_recent_volatility` | Recent 30-bar volatility is 40%+ above the 1-year baseline |
| `bullish/bearish_macd` / `bullish_macd_crossover` | Current MACD momentum regime |
| `N_bars_above/below_vwap` | Streak length in current VWAP position |

**Two-stop system:**

This tool returns two distinct stops with different purposes:

- **Technical stop** (`stops.technical_stop`) — the conceptual floor. The price level below the nearest support structure where the bullish thesis is broken. Monitor manually; do not place as a broker order if it is inside the noise floor (see flag).
- **Trailing stop** (`stops.trailing_stop_pct` + `stops.trailing_stop_price`) — the broker order level. Calibrated to the historical noise floor so it survives normal daily/weekly volatility. Place this as a trailing stop order with your broker.

**Application notes:**
- Always run this tool before refreshing a stop loss order. The gamma wall location changes as expirations roll and OI shifts — the nearest support level may have moved since the stop was last set.
- If `technical_stop_inside_noise_floor` is flagged, the technical stop is for monitoring only. Place the wider trailing stop with your broker to avoid false triggers.
- Re-run weekly or after any significant price move (≥5%) in either direction — both the support levels and the short interest regime can shift meaningfully over that timeframe.
- For positions with elevated `recent_max_1day_pct` (elevated volatility regime flag), consider widening the trailing stop manually beyond the tool's recommendation until the regime normalises.
- Combine with `get_news` to verify no binary catalyst (earnings, FDA, macro announcement) is scheduled within the trailing stop's loss window before placing the order.

---

## Server 2: `market-analysis-server`

**File:** `market_analysis_server.py`

Three market microstructure tools focused on institutional positioning, off-exchange activity, and liquidity conditions. All serve as additional confirmation signals for bounce-bottom identification.

---

### `get_short_interest(symbol)`

**What it does:** Returns short interest, days-to-cover, float percentage, and squeeze potential assessment.

**Returns:**
- `shares_short` — total shares sold short
- `short_float_pct` — short interest as % of tradeable float
- `short_ratio_days` — days-to-cover = shares_short ÷ avg daily volume
- `shares_outstanding`, `float_shares`, `avg_daily_volume`
- `short_interest_date` — as-of date (may lag up to 2 weeks)
- `squeeze_potential` — `'HIGH'`, `'MEDIUM'`, or `'LOW'`
- `squeeze_note`, `borrow_note`

**Squeeze potential thresholds:**
- **HIGH:** short_float_pct ≥ 20% AND short_ratio ≥ 5 days
- **MEDIUM:** short_float_pct ≥ 10% OR short_ratio ≥ 3 days
- **LOW:** below those thresholds

**Application notes:**
- High short interest is **both a risk and a fuel source**. High short float = lots of sellers who will eventually need to buy back shares. A catalyst that forces covering creates a **short squeeze** — one of the most violent upward price moves in markets.
- **Days-to-cover** is more important than raw short float. A 25% short float with 2 days-to-cover is less dangerous than a 15% float with 10 days-to-cover — the latter means short sellers are trapped in an illiquid stock.
- Short interest data from Yahoo Finance lags by **up to 2 weeks** (FINRA bi-monthly settlement cycle). Treat as a medium-term structural factor, not a real-time signal.
- Combine with unusual call sweeps: if short interest is HIGH and `get_unusual_calls` shows aggressive OTM calls being bought, someone is positioning for a squeeze catalyst.
- The `borrow_note` field estimates whether borrow is likely tight (hard to short). When borrow is unavailable, short sellers cannot add new positions even if they want to — supply of sellers is capped.

---

### `get_dark_pool(symbol, lookback=20, interval='1d')`

**What it does:** Proxies dark pool / block trade activity using price-volume divergence patterns from public OHLCV data.

**Parameters:**
- `lookback` — bars to scan (default 20)
- `interval` — `'1d'` or `'1h'`

**Detection patterns:**

| Pattern | Criteria | Meaning |
|---------|----------|---------|
| **Price absorption** | Volume ≥2× avg AND bar range ≤0.5× avg range | Large blocks crossing quietly off-exchange |
| **Two-sided flow** | Volume ≥2× avg AND close within 30% of bar midpoint | Institutional two-way flow, indecisive result |

**Returns:**
- `net_signal` — `'accumulation'`, `'distribution'`, `'mixed'`, or `'none'`
- `absorption_events` — list of absorption bars with direction, ratios, interpretation
- `two_sided_events` — list of two-sided flow bars
- `interpretation`, `data_note`

**Application notes:**
- **Accumulation signal** (absorption on down days) = large buyers are absorbing sell pressure without moving price. The stock is being "held up" while institutions quietly build positions off-exchange.
- **Distribution signal** (absorption on up days) = large sellers are absorbing buy pressure, capping rallies. A warning to avoid long entries.
- This is a **proxy only** — true dark pool data requires FINRA ATS or Bloomberg. The tool includes a `data_note` field that makes this limitation explicit. Treat signals as confirming evidence, not definitive proof.
- The most powerful combination: OBV bullish divergence (`get_obv`) + dark pool accumulation signal = two independent methods both pointing to institutional buying. High-confidence bounce setup.
- Use `interval='1h'` for intraday dark pool proxy analysis on active trading days.

---

### `get_bid_ask_spread(symbol, lookback=20)`

**What it does:** Measures current equity bid/ask spread, ATM options spread, and high-low range ratio vs rolling norm as a composite liquidity/fear gauge.

**Three measurement layers:**

| Layer | Source | Best for |
|-------|--------|---------|
| Equity spread | `fast_info` bid/ask | Current quote spread (most accurate) |
| Options spread | ATM chain bid/ask % | Fear/volatility premium indicator |
| H/L range ratio | `(H-L)/Close` vs 20-day avg | Intraday volatility proxy |

**Returns:**
- `equity_spread`, `equity_spread_pct` — raw and % spread
- `options_spread_pct` — average ATM options spread %
- `hl_range_ratio` — today's H/L range vs rolling 20-day average
- `spread_vs_norm` — `'widening'`, `'elevated'`, `'normal'`, or `'narrowing'`
- `bottom_signal` — `'strong'`, `'forming'`, or `'none'`
- `bottom_note` — interpretation of the bottom signal
- `spread_history` — last 10 bars with H/L range % and close

**Spread vs norm thresholds:**
- `widening` — H/L range ≥ 1.5× rolling average
- `elevated` — 1.2–1.5× average
- `normal` — 0.8–1.2× average
- `narrowing` — ≤ 0.8× average

**Application notes:**
- **Spread widening is a fear gauge.** At capitulation bottoms, market makers widen spreads to protect themselves from order flow — this is when equity and options spreads are at their maximum. It signals peak uncertainty.
- The **transition from widening to narrowing** is the key signal: when spreads begin to compress after a period of widening, liquidity is returning and the panic is fading. This often precedes a multi-day bounce by 1–2 bars.
- `bottom_signal = 'strong'` (spread narrowing) combined with a hammer candlestick (`get_candlestick_patterns`) and OBV divergence (`get_obv`) is a three-signal confirmation of a capitulation bottom.
- Options spread % is a better real-time fear indicator than equity spread — it reflects IV and demand for protection directly. Options spreads typically widen 2–3 bars before equity spreads follow.
- High-low range ratio >2.0 (today's range is double the norm) = intraday capitulation. If this occurs with a close near the high of the range (hammer-like), it is one of the strongest bottom signals available.

---

## CLI Tool: `options_analysis.py`

**Type:** Standalone command-line script (not an MCP server)

Scans an entire watchlist, scores each security on bearish and bullish signals, and recommends put trade allocations for a given budget.

### Usage

```bash
# Full watchlist analysis with $1,000 put budget, top 15 candidates
python options_analysis.py --watchlist ../watchlist.yaml --puts-budget 1000 --top-n 15

# Single symbol analysis
python options_analysis.py --symbol AMD --puts-budget 1000

# Custom watchlist
python options_analysis.py --watchlist /path/to/custom.yaml --puts-budget 5000 --top-n 20

# Use a custom SQLite database path
python options_analysis.py --db /data/options_history.db

# Skip saving snapshots (quick one-off run)
python options_analysis.py --no-persist --symbol AAPL
```

### Scoring system

**Long score drivers** (bounce/accumulation signals):

| Signal | Points |
|--------|--------|
| Price below lower Bollinger Band | +3 |
| Price within 2% of lower BB | +2 |
| P/C ratio < 0.5 (very bullish) | +3 |
| P/C ratio 0.5–0.8 (bullish) | +2 |
| Large call volume (>10K) | +1 |
| Huge call volume (>50K) | +1 |
| IV rank ≥ 80% (extreme fear) | +3 |
| IV rank 60–80% (elevated fear) | +2 |
| IV rank 40–60% (above average) | +1 |
| Put unwinding (vol P/C < OI P/C) | +2 |
| Near-term fear spike (near > mid P/C) | +1 |
| ATM P/C lower than total P/C | +1 |

**Put score drivers** (bearish/put trade signals):

| Signal | Points |
|--------|--------|
| Price above upper Bollinger Band | +3 |
| Price within 2% of upper BB | +2 |
| P/C ratio > 2.0 (very bearish) | +3 |
| P/C ratio 1.5–2.0 (bearish) | +2 |
| Put OI >> Call OI (>2× ratio) | +1 |
| Massive put OI (>50K) | +1 |
| IV rank ≤ 10% (very cheap puts) | +3 |
| IV rank ≤ 20% (cheap puts) | +2 |
| Fresh put buying (vol P/C ≥ 1.5× OI P/C) | +2 |
| ATM P/C higher than total P/C | +1 |

### Portfolio summary ranking

The put portfolio summary uses a **blended conviction + ROI score** to select trades:

```
rank_score = 0.60 × (put_score / 11) + 0.40 × min(roi%, 200%) / 200%
```

ROI is capped at 200% to prevent high-ROI / low-conviction outliers from monopolising the budget ahead of better-supported trades. Trades are filled greedily from highest rank_score until the budget is exhausted.

### Trade guardrails

Three filters are applied before any put trade is built. Each guardrail addresses a real failure mode identified from post-mortem analysis of losing trades:

| Guardrail | Condition | Reason |
|-----------|-----------|--------|
| **Earnings blackout** | Earnings within 14 days | Earnings events gap stocks in either direction; a pure put thesis becomes a coin-flip against the event. Applies to both put and call trades. |
| **Catalyst cooldown** | Analyst upgrade, raised price target, partnership, or beat in news within 5 days | A positive catalyst may be exactly what drove put-heavy OI — institutions hedging long positions, not directional bets. Applies to put trades only; positive catalysts support call trades. |
| **Contradiction guard** | `long_score ≥ 3` AND `put_score ≥ 3` simultaneously | When both oversold bounce signals and overbought put signals are both elevated, the market is genuinely ambiguous. Ambiguous setups have poor risk/reward since the true direction is unclear. Applies to put trades only. |

When a guardrail triggers, the output shows a `*** GUARDRAIL: ... ***` annotation on the candidate with the specific reason (e.g. `earnings in 4d`, `positive catalyst: "AMD upgraded to Buy"`, or `ambiguous signal`). The security still appears in the scored candidates list for awareness.

Guardrail thresholds are configurable at the top of `options_analysis.py`:

```python
EARNINGS_BLACKOUT_DAYS    = 14   # days before earnings to block trades
CATALYST_LOOKBACK_DAYS    = 5    # days of news to scan for positive catalysts
CONTRADICTION_LONG_MIN    = 3    # long_score threshold for contradiction guard
CONTRADICTION_PUT_MIN     = 3    # put_score threshold for contradiction guard
```

### Call trade analysis

Both the LONG CANDIDATES and PUT CANDIDATES sections now show **both a call trade and a put trade** for each security, enabling direct comparison of cost, target, and ROI in each direction.

**Long candidates section** — primary recommendation is a call trade (target: upper BB), with a put trade shown for comparison.

**Put candidates section** — primary recommendation is a put trade (target: lower BB), with a call trade shown for comparison.

**Call trade target:** always the upper Bollinger Band. If the price is already above the upper BB (confirmed breakout), the target is set 5% above the upper BB.

**Put trade target:** always the lower Bollinger Band. If the price is already below the lower BB (confirmed breakdown), the target is set 5% below the lower BB.

Both sections end with their own portfolio summary ranked by a blended conviction + ROI score:

```
# Put portfolio
rank_score = 0.60 × (put_score / MAX_PUT_SCORE) + 0.40 × min(roi%, 200%) / 200%

# Call portfolio
rank_score = 0.60 × (long_score / MAX_PUT_SCORE) + 0.40 × min(roi%, 200%) / 200%
```

A spread is suggested (debit spread for puts, bull call spread for calls) when ATM IV exceeds 65%, to reduce the cost of high-IV entries.

### IV Rank methodology

IV Rank and IV Percentile are computed from 252 days of rolling 30-day historical volatility (HV30) as a proxy for historical implied volatility. The current IV is taken from the live ATM options chain.

- **IV Rank** = `(current_iv − 52w_low_hv) / (52w_high_hv − 52w_low_hv) × 100`
- **IV Percentile** = % of past-year days where HV30 < current IV

High IV rank (≥80%) = fear/capitulation = potential bounce bottom (expensive options signal maximum uncertainty, which historically mean-reverts). Low IV rank (≤20%) = complacency = ideal time to buy cheap puts before a move down.

### Watchlist format

```yaml
- name: Advanced Micro Devices
  symbol: AMD
  currency: USD
  tags:
    - AI Factory
    - Semiconductors
```

Non-US-listed symbols (suffixes `.PA`, `.OL`, `.AS`, `.SG`, `.KS`, `.ST`, `.DE`) are automatically skipped as options data is unavailable.

---

## Signal Combination Guide

### Bounce Bottom Checklist

Use the following tools in sequence to build a multi-signal bounce confirmation:

| Step | Tool | Signal to look for |
|------|------|--------------------|
| 1 | `get_stock_price` | Price at or below lower Bollinger Band |
| 2 | `get_rsi` | RSI ≤ 30 (oversold) |
| 3 | `get_stochastic` | %K crossing above %D below 20 |
| 4 | `get_macd` | Histogram turning less negative (decelerating downtrend) |
| 5 | `get_volume_analysis` | Climax bar on down day + quiet follow-through |
| 6 | `get_obv` | Bullish divergence (OBV rising while price falling) |
| 7 | `get_candlestick_patterns` | Hammer or dragonfly doji at the low |
| 8 | `get_higher_lows` | First higher low forming (1h interval) |
| 9 | `get_vwap` | Reclaim of VWAP on above-average volume |
| 10 | `get_unusual_calls` | Unusual call sweeps at current price level |
| 11 | `get_short_interest` | High short float (squeeze fuel) |
| 12 | `get_dark_pool` | Accumulation signal on down bars |
| 13 | `get_bid_ask_spread` | Spreads narrowing from elevated levels |
| 14 | `get_gap_analysis` | Unfilled gap-down above current price (bounce target) |
| 15 | `get_delta_adjusted_oi` | MM buy_on_rally near delta flip strike |

**Minimum confirmation threshold:** Steps 1 + 2 or 3 + 4 or 5 = three independent signals aligned.
**High-confidence setup:** 6 or more signals aligned.

### Bearish / Put Setup Checklist

| Step | Tool | Signal to look for |
|------|------|--------------------|
| 1 | `get_stock_price` | Price above upper Bollinger Band, P/C > 2.0 |
| 2 | `options_analysis.py` | High put score (≥4), ranked in PUT CANDIDATES section |
| 3 | `get_rsi` | RSI ≥ 70 (overbought) |
| 4 | `get_stochastic` | %K crossing below %D above 80 |
| 5 | `get_macd` | Histogram turning less positive (decelerating uptrend) |
| 6 | `get_bid_ask_spread` | Options spread elevated (IV premium) |
| 7 | `get_delta_adjusted_oi` | MM sell_on_rally (net long delta → mechanical cap) |

---

## Data Limitations

| Tool | Limitation |
|------|-----------|
| All tools | Yahoo Finance data; may have 15-min delay for options |
| `ohlcv_cache.py` | SQLite cache (`ohlcv_cache.db`) in the same directory; delete the file to force a full re-fetch |
| `options_store.py` | Stores ATM contracts only (5 nearest strikes per side); full-chain storage not implemented. Only the nearest expiration is persisted per snapshot. |
| `get_short_interest` | FINRA bi-monthly update; lags up to 2 weeks |
| `get_dark_pool` | Proxy only — true dark pool requires paid feed (FINRA ATS, Bloomberg) |
| `get_bid_ask_spread` | Equity spread from fast_info; not tick-level data |
| `get_unusual_calls` | vol/OI and last≥ask proxies for sweeps; individual prints not available |
| `get_delta_adjusted_oi` | Uses live IV snapshot; delta is approximate (Black-Scholes, no dividends) |
| `options_analysis.py` | IV Rank uses HV30 as IV proxy; true historical IV requires paid feed. Catalyst cooldown keyword list may not capture all positive catalysts. |
| Non-US symbols | Options data unavailable; automatically skipped in watchlist scan |
