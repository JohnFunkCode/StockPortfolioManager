# Stock Portfolio MCP Analysis Tools

A suite of technical analysis tools built on [FastMCP](https://github.com/jlowin/fastmcp) and [yfinance](https://pypi.org/project/yfinance/), designed to identify **bounce bottoms**, **bearish setups**, and **put/call trade opportunities** from a watchlist.

---

## Quick Start

```bash
cd fastMCPTest
claude   # MCP servers start automatically via .mcp.json

# Run the options analysis CLI directly
python options_analysis.py --puts-budget 10000 --top-n 15
```

```bash
pip install fastmcp yfinance numpy pandas pyyaml feedparser

# Optional — enables FinBERT sentiment scoring (~440 MB first download):
pip install transformers torch
```

The `fastMCPTest/.mcp.json` file is set up to run from inside this directory and
uses the repo virtualenv at `../.venv/bin/fastmcp`.

---

## Architecture

| File | Type | Purpose |
|------|------|---------|
| `stock_price_server.py` | MCP Server | Price, momentum, volume, options flow, and stop loss tools |
| `market_analysis_server.py` | MCP Server | Market microstructure: short interest, dark pool, bid/ask spreads |
| `news_sentiment_server.py` | MCP Server | Financial news collection, FinBERT scoring, and sentiment querying |
| `options_analysis.py` | CLI Script | Watchlist-level put/call scoring, trade recommendations, news-aware guardrails |
| `options_store.py` | Library | PostgreSQL persistence for options chain snapshots |
| `options_position_store.py` | Library | Tracks active options positions; drives ITM, expiration, and profit-target alerts |
| `news_store.py` | Library | PostgreSQL persistence for news articles and FinBERT sentiment scores |
| `news_collector.py` | Library | RSS + yfinance news fetcher with lazy-loaded FinBERT scoring pipeline |
| `ohlcv_cache.py` | Library | PostgreSQL-backed OHLCV bar cache; eliminates redundant yfinance calls |
| `.mcp.json` | Config | Registers the FastMCP servers in this directory for auto-start |

---

## Options Chain Persistence (`options_store.py`)

Saves a full options chain snapshot to the database on every `options_analysis.py` run and every `get_stock_price` MCP call. Snapshots accumulate passively and enable backtesting of P/C ratio trends, IV rank history, and trade outcomes. Duplicate `(symbol, captured_at)` pairs are silently ignored.

Each snapshot captures: price, Bollinger Bands, nearest-expiration call/put chains (5 ATM strikes each side), and aggregate metrics (P/C ratio, total OI, total volume, average IV).

### Schema

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

### API

```python
from options_store import OptionsStore

store = OptionsStore()                           # default: unified QuantCore PostgreSQL database (QUANTCORE_DB_DSN)
store = OptionsStore("/path/to/custom.db")       # custom path

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

Pass `--no-persist` to skip saving, or `--db /path/to/file.db` for a custom path.

**Limitation:** ATM contracts only (5 strikes per side); nearest expiration only per snapshot.

---

## EOD Snapshot Collector (retired)

`collect_options.py` was retired in the yfinance-gateway consolidation (issues
#74/#77): it fetched chains directly from yfinance, bypassing the gateway.
Its capability lives in the services layer — use
`POST /api/securities/refresh-options-snapshots?source=watchlist&chain_type=full`
(or `OptionsService.refresh_options_snapshots` in-process) instead.

---

## Options Position Tracker (`options_position_store.py`)

Tracks active options positions in the unified QuantCore PostgreSQL database and surfaces alerts for crossing into the money, approaching expiration, and reaching the profit target. The notifier (`notifier.py`) calls this store automatically on each run and sends colour-coded Discord embeds per alert.

### Alert types

| Alert | Condition |
|-------|-----------|
| `ITM` | Current price crosses the strike (call: price ≥ strike; put: price ≤ strike) |
| `EXPIRATION_7D` | 2–7 days until expiration |
| `EXPIRATION_1D` | 0–1 days until expiration |
| `PROFIT_TARGET` | Estimated intrinsic value ≥ 2× purchase price per share |

Expiration alerts re-fire daily by design (they include the date in the alert title) so you receive a reminder every day the position is near expiry.

### API

```python
from options_position_store import OptionsPositionStore

store = OptionsPositionStore()   # default: options_chain.db

pos_id = store.add_position(
    symbol="AMD", kind="put", strike=230.0, expiration="2026-05-16",
    contracts=1, purchase_price=4.10, purchase_date="2026-04-09",
    target_price=187.89,
)

alerts = store.get_pending_alerts(current_prices={"AMD": 215.0})
# → [{"alert_type": "ITM", "symbol": "AMD", "strike": 230.0, ...}, ...]

store.close_position(pos_id, reason="sold")
store.expire_position(pos_id)
store.auto_expire_past_positions()     # marks all past-expiry positions as EXPIRED
store.get_active_positions()
store.get_position(pos_id)
store.position_count(status="ACTIVE")
```

---

## OHLCV Cache (`ohlcv_cache.py`)

Caches `ticker.history()` results in the unified QuantCore PostgreSQL database to avoid redundant yfinance calls. On cold start, pre-populates the maximum useful history so subsequent runs are fully cache-served.

| Interval | Warm-up window |
|----------|---------------|
| `1d` | 730 days (2 years) |
| `1wk` | 1825 days (5 years) |
| `1mo` | 3650 days (10 years) |
| `1h` / `30m` / `15m` | 59 days (yfinance limit) |

The cache fetches from yfinance only when: (1) no cached data exists (cold start), (2) an OPEN bar needs refreshing, or (3) the most recent CLOSED bar is at least 1 trading day old.

**Bar statuses:**

| Status | Meaning |
|--------|---------|
| `OPEN` | Bar is currently forming — data will change |
| `CLOSED` | Bar interval ended — data is final |
| `GAP` | No trades during this slot — synthetic placeholder, excluded from results |
| `CORRECTED` | Previously-CLOSED bar re-fetched with a materially different close (>0.1%) — split or exchange correction detected |

```python
from ohlcv_cache import get_history, period_to_days

hist = get_history("AAPL", "1d", days=180)   # pd.DataFrame matching ticker.history() format
days = period_to_days("6mo")                  # → 182
```

All `ticker.history()` calls in all three MCP servers route through this cache.

---

## News Sentiment (`news_store.py` · `news_collector.py` · `news_sentiment_server.py`)

Fetches financial news from RSS and yfinance, scores each article with [FinBERT](https://github.com/ProsusAI/finBERT) (`ProsusAI/finbert`), and surfaces sentiment signals both via MCP and as guardrail inputs to `options_analysis.py`.

**Sources:** Yahoo Finance RSS (~20 articles/symbol via `feedparser`) + yfinance `.news` (~8 articles/symbol). Articles are deduplicated by `(symbol, url)`.

**FinBERT** returns `positive / negative / neutral` probability scores. It is lazy-loaded once per process and cached locally after the first download. If `transformers`/`torch` are not installed, articles are stored without sentiment and the server still works.

**Signal logic** (applied to scored articles in a lookback window):

| Signal | Condition |
|--------|-----------|
| `BULLISH` | ≥60% of scored articles are positive |
| `BEARISH` | ≥60% of scored articles are negative |
| `MIXED` | One sentiment leads by >15% but <60% |
| `NEUTRAL` | No dominant sentiment |
| `INSUFFICIENT_DATA` | Fewer than 3 scored articles |

### Schema (unified QuantCore PostgreSQL database)

```sql
CREATE TABLE news_articles (
    article_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    summary         TEXT,
    publisher       TEXT,
    url             TEXT    NOT NULL,
    published_at    TEXT,                -- ISO-8601 (best-effort from source)
    source          TEXT    NOT NULL,    -- 'rss' or 'yfinance'
    fetched_at      TEXT    NOT NULL,    -- ISO-8601 UTC timestamp of fetch
    sentiment       TEXT    CHECK(sentiment IN ('positive','negative','neutral')),
    sentiment_score REAL,                -- confidence 0–1 for the predicted label
    positive_score  REAL,                -- raw FinBERT positive probability
    negative_score  REAL,                -- raw FinBERT negative probability
    neutral_score   REAL,                -- raw FinBERT neutral probability
    UNIQUE (symbol, url)
);
```

### API

```python
from news_store import NewsStore
from news_collector import NewsCollector

store     = NewsStore()                          # default: news_sentiment.db next to script
collector = NewsCollector(store=store)

# Fetch RSS + yfinance, store, and score with FinBERT in one call
new_counts = collector.collect(["AAPL", "MSFT"], score=True)
# → {"AAPL": 18, "MSFT": 12}  (new articles inserted)

# Score any previously unscored articles
scored = collector.score_unscored()

# Aggregate signal for a symbol
summary = store.get_sentiment_summary("AAPL", days=7)
# → {signal: "BULLISH", signal_strength: 0.72, positive_count: 13, top_positive: [...], ...}

# Per-day trend
trend = store.get_sentiment_trend("AAPL", days=30)
# → [{date: "2026-04-09", net_score: 0.4, positive_count: 6, ...}, ...]

store.article_count()           # total articles across all symbols
store.article_count("AAPL")     # articles for one symbol
store.get_symbols()             # → ["AAPL", "AMD", ...]
```

---

## Server 3: `news-sentiment-server`

**File:** `news_sentiment_server.py`

---

### `collect_news(symbol, score=True)`

Fetches the latest news for `symbol` from Yahoo Finance RSS and yfinance, stores articles in the database, and optionally runs FinBERT scoring on all unscored articles.

**Parameters:**
- `symbol` — ticker symbol, e.g. `"AAPL"`
- `score` — whether to run FinBERT after fetching (default `true`; set `false` for a faster fetch-only run)

**Returns:**
- `symbol`, `new_articles` (newly inserted this run), `total_articles` (all stored for this symbol)
- `rss_available` — whether `feedparser` is installed
- `finbert_available` — whether `transformers` + `torch` are installed

---

### `get_news_sentiment(symbol, days=7, scored_only=False)`

Returns recent articles and an aggregate sentiment signal for `symbol`. Call `collect_news` first to populate the database.

**Parameters:**
- `symbol` — ticker symbol
- `days` — how many calendar days back to look (default 7)
- `scored_only` — return only FinBERT-scored articles (default `false`)

**Returns:**
- `signal` — `BULLISH` / `BEARISH` / `MIXED` / `NEUTRAL` / `INSUFFICIENT_DATA`
- `signal_strength` — 0.0–1.0
- `total_articles`, `scored_articles`, `positive_count`, `negative_count`, `neutral_count`
- `avg_positive_score`, `avg_negative_score`
- `top_positive`, `top_negative` — up to 3 representative article titles each
- `articles` — list of `{article_id, title, publisher, published_at, url, sentiment, sentiment_score}`

**Application notes:**
- `INSUFFICIENT_DATA` means fewer than 3 articles were scored in the window — collect more data or widen `days`.
- Signal coverage improves over time as `news_sentiment.db` accumulates articles over daily runs.
- Use `top_negative` to identify what specific risk the market is pricing in for a bearish signal.

---

### `get_sentiment_trend(symbol, days=30)`

Returns a per-day sentiment breakdown for trend analysis.

**Parameters:**
- `symbol` — ticker symbol
- `days` — how many calendar days back to look (default 30)

**Returns:**
- `trend` — list of `{date, article_count, positive_count, negative_count, neutral_count, net_score}`
- `net_score` is `(positive − negative) / total`: +1.0 = all positive, −1.0 = all negative

**Application notes:**
- Use to detect sentiment momentum shifts — a stock transitioning from BEARISH to NEUTRAL over 3–5 days may be bottoming on the news cycle.
- Combine with `get_obv` bullish divergence for a technical + sentiment bottom confirmation.

---

### `list_news_symbols()`

Lists every ticker that has at least one stored article. Returns `{symbols, total_symbols}`.

---

## Server 1: `stock-price-server`

**File:** `stock_price_server.py`

15 tools covering price, momentum indicators, volume analysis, options flow, price structure patterns, drawdown analysis, and stop loss synthesis. All data sourced from Yahoo Finance via yfinance.

---

### `get_stock_price(symbol)`

Returns current price, 20-day Bollinger Bands (2σ), and the full ATM options chain summary for the nearest expiration.

**Returns:**
- `price` — current last price
- `bollinger_bands` — `upper`, `middle` (20-day SMA), `lower`, `period`, `std_dev`
- `options` — `expiration`, `put_call_ratio`, `calls` (ATM 5 strikes), `puts` (ATM 5 strikes), avg IV %

**Application notes:**
- Bollinger Band position is the primary oversold/overbought gauge. Price at or below the lower band (pos ≤ 0) is the primary bounce setup signal.
- Use as the first call in any analysis — establishes the price anchor all other tools reference.
- Every call automatically saves a snapshot to `options_chain.db` via `OptionsStore`.

---

### `get_rsi(symbol, period=14, interval='1d')`

Calculates RSI using Wilder's exponential moving average method.

**Parameters:**
- `period` — lookback window (default 14; common alternatives: 9, 21)
- `interval` — `'1d'` daily, `'1wk'` weekly, `'1mo'` monthly

**Returns:**
- `rsi` — current RSI value (0–100)
- `signal` — `'oversold'` (≤30), `'overbought'` (≥70), or `'neutral'`
- `last_close`, `interval`, `period`

**Application notes:**
- RSI < 30 = classic oversold; RSI < 20 = extreme oversold, often coincides with bounce bottoms.
- **RSI divergence** is stronger than level alone: if price makes a new low but RSI does not, accumulation is occurring beneath the surface.
- Weekly RSI < 40 with daily RSI < 30 = multi-timeframe oversold confirmation — high-confidence bounce candidate.
- RSI > 70 while Bollinger position > 0.85 = double overbought — put candidate.

---

### `get_macd(symbol, interval='1d')`

Calculates MACD using standard EMA parameters (fast=12, slow=26, signal=9).

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
- A **positive and expanding histogram** near the lower Bollinger Band is one of the most reliable entry signals.
- MACD divergence: price makes a new low but histogram makes a higher low = hidden bullish momentum.

---

### `get_stochastic(symbol, k_period=14, d_period=3, interval='1d')`

Calculates the Stochastic Oscillator (%K fast line and %D signal line).

**Parameters:**
- `k_period` — lookback window for %K (default 14)
- `d_period` — SMA period for %D signal line (default 3)
- `interval` — `'1d'`, `'1wk'`, or `'1mo'`

**Returns:**
- `k` — %K value (0–100)
- `d` — %D value (0–100)
- `signal` — `'oversold'` (≤20), `'overbought'` (≥80), or `'neutral'`
- `crossover` — `'bullish_crossover'`, `'bearish_crossover'`, `'bullish'`, or `'bearish'`

**Application notes:**
- **Strongest bounce signal:** %K crosses above %D while both are below 20. This two-bar confirmation is the stochastic's most reliable buy signal.
- A bearish crossover while RSI and MACD are still bullish = early warning of short-term momentum fade — often precedes 3–7 day consolidations.
- Stochastic resets to oversold faster than RSI, making it better for timing short-term entries.

---

### `get_volume_analysis(symbol, lookback=20, interval='1d')`

Detects volume climax (capitulation) bars and OBV divergence to identify exhaustion bottoms.

**Parameters:**
- `lookback` — rolling window for averages (default 20)
- `interval` — `'1d'` or `'1wk'`

**Returns:**
- `climax_events` — bars where volume ≥ 2× average AND range ≥ 1.5× average range; each with `direction`, `volume_ratio`, `bar_range_pct`, `quiet_follow_through`, `interpretation`
- `obv_divergence` — `true` if price made a new low but OBV did not
- `bottom_signal` — `'strong'`, `'moderate'`, `'weak'`, or `'none'` with description
- `last_volume_ratio` — today's volume vs 20-day average

**Application notes:**
- A **down-day climax bar** (high volume + wide range + red candle) = sellers exhausted. The single most reliable single-bar bottom signal.
- **Quiet follow-through** = the bar immediately after a climax has volume ≤ 0.6× average. Classic two-bar capitulation bottom.
- `bottom_signal = 'strong'` requires all three: climax + quiet follow-through + OBV divergence.

---

### `get_obv(symbol, lookback=20, interval='1d')`

Calculates On-Balance Volume, detects trend, and identifies bullish/bearish divergence with strength scoring.

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
- `divergence_strength = 'strong'` = high confidence smart money is positioned for a reversal.
- OBV flattening or falling while price still rises = bearish divergence (distribution) — warning sign.

---

### `get_vwap(symbol, lookback=20, interval='1d')`

Calculates rolling VWAP using typical price `(H+L+C)/3 × volume` and detects reclaim events.

**Parameters:**
- `lookback` — rolling window (default 20)
- `interval` — `'1d'`, `'1wk'`, or `'1mo'`

**Returns:**
- `vwap` — current rolling VWAP value
- `position` — `'above_vwap'` or `'below_vwap'`
- `distance_pct` — % distance from VWAP (negative = below)
- `consecutive_bars_above` / `consecutive_bars_below` — streak length
- `reclaim_signal` — `true` if price crossed above VWAP in last 3 bars
- `reclaim_strength` — `'strong'`, `'moderate'`, `'weak'`, or `'none'`
- `crossover_events` — all recent VWAP crossovers with date, type, volume ratio, high-volume flag

**Reclaim strength criteria:**
- **Strong:** reclaim bar + held ≥2 consecutive bars + above-average volume
- **Moderate:** reclaim + either above-average volume OR ≥2 bars held
- **Weak:** reclaim bar only, light volume — unconfirmed

**Application notes:**
- A **strong VWAP reclaim** is one of the highest-probability swing bounce entry signals used by institutional traders.
- Price >3% above VWAP = extended; wait for a pullback. Price >5% below VWAP = deeply discounted; watch for the reclaim cross as an entry trigger.

---

### `get_candlestick_patterns(symbol, lookback=10, interval='1d')`

Scans recent bars for reversal candlestick patterns with strength scoring.

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

**Strength scoring:** base shape (1–3 pts) + near lower BB (+2) + above-average volume (+1) + ≥3 consecutive down days (+1). Levels: `strong` ≥6, `moderate` 4–5, `weak` 2–3.

**Application notes:**
- A **strong hammer** at the lower BB after 3+ down days on above-average volume is one of the highest-conviction reversal signals.
- Doji patterns are weak in isolation — their value comes from context (a dragonfly doji after a sustained downtrend near support is meaningful; a doji in the middle of a range is noise).
- **Never act on a single candlestick signal.** Combine with RSI, MACD or stochastic crossover, and VWAP reclaim for confirmation.

---

### `get_higher_lows(symbol, swing_bars=3, lookback_swings=6, interval='1h')`

Detects higher-low price structure — the first structural sign of a downtrend reversing.

**Parameters:**
- `swing_bars` — bars on each side required for a pivot low (default 3)
- `lookback_swings` — number of recent swing lows to evaluate (default 6)
- `interval` — `'15m'`, `'30m'`, `'1h'` (default), or `'1d'`

**Returns:**
- `higher_low_pattern` — `true` if recent swing lows form a rising series
- `consecutive_higher_lows` — count of consecutive higher lows
- `min_rise_between_lows_pct` — smallest rise between adjacent lows
- `pattern_strength` — `'strong'` (≥3 lows, each >0.3%), `'moderate'` (2 lows >0.3%), `'weak'`, or `'none'`
- `trend_before_lows` — `'downtrend'`, `'uptrend'`, or `'sideways'`
- `swing_lows` — list of detected pivots with date, low, close, high

**Application notes:**
- Defaults to `'1h'` because higher lows form on intraday charts before daily charts — catches reversals earlier.
- Only meaningful after a `'downtrend'`. A strong pattern after a sideways period carries much less weight.
- This is the **first structural reversal signal** — sellers losing control before price makes a higher high. Combine with daily MACD crossover and VWAP reclaim for a complete setup.

---

### `get_gap_analysis(symbol, min_gap_pct=0.5, lookback=60, interval='1d')`

Detects price gaps and tracks fill status to identify support/resistance magnets.

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

**Fill status logic:** `filled` = a later bar's high ≥ gap_top AND low ≤ gap_bottom; `partially_filled` = later bar entered but didn't close the zone; `unfilled` = no subsequent bar has entered the zone.

**Application notes:**
- Markets have a strong statistical tendency to fill gaps. **Unfilled gap-downs above current price** are the first overhead targets when a bounce begins.
- An **unfilled gap-down below current price** is a support magnet — watch for a bounce from that level.
- Avoid buying into a large unfilled gap overhead — it will act as resistance and cap the bounce.

---

### `get_unusual_calls(symbol, min_volume=100, min_vol_oi_ratio=0.5, max_expirations=3)`

Detects unusual call option activity using volume/OI ratio and aggressive-fill proxies as sweep indicators.

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
- `unusual_calls` — top 20 contracts sorted by sweep score
- `interpretation` — plain-English summary

**Application notes:**
- **vol/OI > 1.0** = more contracts traded today than exist in open interest. This definitively means new positioning — buyers are not closing puts, they are opening new bullish bets.
- **last ≥ ask** (aggressive fill) = someone paid up; urgency implies conviction.
- Sweeps on near-dated expirations imply a catalyst is expected imminently. Sweeps on 30–60 day expirations suggest medium-term repositioning.

---

### `get_delta_adjusted_oi(symbol, max_expirations=3, risk_free_rate=0.045)`

Calculates delta-adjusted open interest (DAOI) using Black-Scholes deltas to measure market-maker directional exposure and identify mechanical hedging flows.

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
- **MM `buy_on_rally`** = market makers are net short delta — they must buy stock as price rises, creating mechanical self-reinforcing buying that amplifies any bounce.
- The **delta flip strike** is the single most important price level: MM hedging flows intensify dramatically as price moves through it — expect acceleration.
- The **gamma wall** acts as a magnet; price gravitates toward it and often stalls there as MM hedges neutralise.
- Most powerful near options expiration (weekly Friday) when gamma is highest.

---

### `get_news(symbol, max_articles=10)`

Fetches recent news articles from Yahoo Finance.

**Returns:**
- `articles` — list of `{title, publisher, published, summary, url}`
- `article_count`

**Application notes:**
- Use to identify upcoming earnings, analyst upgrades/downgrades, or catalysts that could drive a bounce.
- Cross-reference the published date against price action — if a negative article caused a selloff but price is now stabilising, the news may be fully priced in.
- For FinBERT-scored sentiment across multiple articles, use `get_news_sentiment` from the news-sentiment-server instead.

---

### `get_historical_drawdown(symbol, lookback_days=252)`

Calculates historical max drawdown metrics from the OHLCV cache to establish the noise floor for trailing stop calibration.

**Parameters:**
- `lookback_days` — trading days to look back (default 252 = 1 year)

**Returns:**
- `max_1day_drawdown_pct` — worst single close-to-close drop (negative value)
- `worst_1day_date` — date of the worst 1-day drop
- `max_5day_drawdown_pct` — worst 5-trading-day rolling drop
- `worst_5day_start` / `worst_5day_end` — date range of the worst 5-day window
- `max_intraday_drop_pct` — worst single-day High→Low intraday drop
- `recent_max_1day_pct` — worst 1-day drop in the most recent 30 bars (current volatility regime)
- `avg_drawdown_pct` — average of `|max_1day|` and `|max_5day|` — derived trailing stop floor
- `trailing_stop_pct` — recommended minimum trailing stop % to survive historical volatility
- `lookback_trading_days`, `last_close`

**Noise floor logic:** trailing stop % = `avg(|max_1day_drawdown|, |max_5day_drawdown|)`. Any stop tighter than this will statistically false-trigger on routine volatility.

**Application notes:**
- Run this tool before setting any broker trailing stop. If your intended stop distance is smaller than `trailing_stop_pct`, it will be triggered by noise, not a genuine trend breakdown.
- `recent_max_1day_pct` vs `max_1day_drawdown_pct` is a regime gauge — if recent volatility is significantly higher than the 1-year baseline, widen stops accordingly.
- All data is served from the OHLCV cache; no live yfinance calls are made.

---

### `get_stop_loss_analysis(symbol, cost_basis=0.0, shares=0, max_expirations=4)`

Synthesises a complete stop loss recommendation by combining price structure, options-derived support levels, historical drawdown noise floor, and short interest regime into a two-stop output.

**Parameters:**
- `cost_basis` — average purchase price per share (optional; enables P&L output)
- `shares` — number of shares held (optional; enables total P&L calculations)
- `max_expirations` — options expirations to include when locating gamma wall (default 4)

**Returns:**

| Section | Key fields |
|---------|-----------|
| `position` | `cost_basis`, `shares`, `unrealized_pnl_per_share`, `unrealized_pnl_pct`, `total_unrealized_pnl`, `pnl_at_technical_stop`, `pnl_at_trailing_stop` |
| `technical` | `above_vwap`, `vwap`, `sma_20`, `bb_upper/lower`, `rsi`, `macd_crossover`, `gamma_wall`, `primary_support`, `primary_support_price`, `support_levels` |
| `short_interest` | `short_float_pct`, `short_ratio_days`, `squeeze_potential`, `stop_impact` |
| `drawdown` | `max_1day_pct`, `max_5day_pct`, `max_intraday_pct`, `recent_max_1day_pct`, `base_trailing_stop_pct` |
| `stops` | `technical_stop`, `technical_stop_distance_pct`, `technical_stop_inside_noise_floor`, `trailing_stop_pct`, `trailing_stop_price` |
| `flags` | Array of warning strings |
| `summary` | Human-readable narrative |

**Support level priority** (highest below current price becomes the technical stop floor):

| Priority | Level | Buffer |
|----------|-------|--------|
| 1 | Gamma wall | 0.8% |
| 2 | VWAP | 1.4% |
| 3 | 20-day SMA | 1.4% |
| 4 | Lower BB | 2.0% |

**Two-stop system:**
- **Technical stop** (`stops.technical_stop`) — the conceptual floor where the bullish thesis is broken. Monitor manually; do not place as a broker order if flagged inside the noise floor.
- **Trailing stop** (`stops.trailing_stop_pct` + `stops.trailing_stop_price`) — calibrated to the historical noise floor. Place this as a trailing stop order with your broker.

**Trailing stop adjustments:**

| Condition | Adjustment | Rationale |
|-----------|-----------|-----------|
| Short float ≥ 15% AND price below VWAP | −10% (tighter) | High short float in downtrend amplifies breakdowns |
| Short float ≥ 20% AND price above VWAP | +10% (wider) | Potential squeeze provides cushion during dips |

**Application notes:**
- Re-run weekly or after any ≥5% price move — gamma wall location shifts as OI changes.
- If `technical_stop_inside_noise_floor` is flagged, use the trailing stop for broker orders only.

---

## Server 2: `market-analysis-server`

**File:** `market_analysis_server.py`

Three market microstructure tools focused on institutional positioning, off-exchange activity, and liquidity conditions.

---

### `get_short_interest(symbol)`

Returns short interest, days-to-cover, float percentage, and squeeze potential.

**Returns:**
- `shares_short` — total shares sold short
- `short_float_pct` — short interest as % of tradeable float
- `short_ratio_days` — days-to-cover = shares_short ÷ avg daily volume
- `shares_outstanding`, `float_shares`, `avg_daily_volume`
- `short_interest_date` — as-of date (may lag up to 2 weeks)
- `squeeze_potential` — `'HIGH'` (float ≥20% AND ratio ≥5d), `'MEDIUM'` (float ≥10% OR ratio ≥3d), `'LOW'`
- `squeeze_note`, `borrow_note`

**Application notes:**
- **Days-to-cover** is more important than raw short float. A 15% float with 10 days-to-cover is more dangerous than 25% float with 2 days — the latter means short sellers are trapped.
- High short interest is **both a risk and a fuel source**. A catalyst that forces covering can create a violent short squeeze.
- Short interest data lags up to 2 weeks (FINRA bi-monthly settlement cycle). Treat as a medium-term structural factor.
- If short interest is HIGH and `get_unusual_calls` shows aggressive OTM call buying, someone is positioning for a squeeze catalyst.

---

### `get_dark_pool(symbol, lookback=20, interval='1d')`

Proxies dark pool / block trade activity using price-volume divergence patterns from public OHLCV data.

**Parameters:**
- `lookback` — bars to scan (default 20)
- `interval` — `'1d'` or `'1h'`

**Detection patterns:**

| Pattern | Criteria | Meaning |
|---------|----------|---------|
| **Price absorption** | Volume ≥2× avg AND bar range ≤0.5× avg range | Large blocks crossing quietly off-exchange |
| **Two-sided flow** | Volume ≥2× avg AND close within 30% of bar midpoint | Institutional two-way flow |

**Returns:**
- `net_signal` — `'accumulation'`, `'distribution'`, `'mixed'`, or `'none'`
- `absorption_events` — list of absorption bars with direction, ratios, interpretation
- `two_sided_events` — list of two-sided flow bars
- `interpretation`, `data_note`

**Application notes:**
- **Accumulation** (absorption on down days) = large buyers absorbing sell pressure without moving price.
- **Distribution** (absorption on up days) = large sellers capping rallies. Warning to avoid long entries.
- This is a **proxy only** — true dark pool data requires a paid feed (FINRA ATS, Bloomberg). The `data_note` field makes this explicit.
- OBV bullish divergence + dark pool accumulation = two independent methods pointing to institutional buying. High-confidence setup.

---

### `get_bid_ask_spread(symbol, lookback=20)`

Measures current equity bid/ask spread, ATM options spread, and high-low range ratio vs rolling norm as a composite liquidity/fear gauge.

**Three measurement layers:**

| Layer | Source | Best for |
|-------|--------|---------|
| Equity spread | `fast_info` bid/ask | Current quote spread |
| Options spread | ATM chain bid/ask % | Fear/volatility premium indicator |
| H/L range ratio | `(H-L)/Close` vs 20-day avg | Intraday volatility proxy |

**Returns:**
- `equity_spread`, `equity_spread_pct`
- `options_spread_pct` — average ATM options spread %
- `hl_range_ratio` — today's H/L range vs rolling 20-day average
- `spread_vs_norm` — `'widening'` (≥1.5×), `'elevated'` (1.2–1.5×), `'normal'` (0.8–1.2×), `'narrowing'` (≤0.8×)
- `bottom_signal` — `'strong'` (narrowing), `'forming'`, or `'none'`
- `bottom_note`, `spread_history` (last 10 bars)

**Application notes:**
- **Spread widening** = peak fear/uncertainty. This is when spreads are at their maximum at capitulation bottoms.
- The **transition from widening to narrowing** is the key signal — liquidity returning, panic fading. Often precedes a multi-day bounce by 1–2 bars.
- Options spreads typically widen 2–3 bars before equity spreads follow — a leading indicator of fear.
- H/L range ratio >2.0 + close near the high of the range (hammer-like) = one of the strongest intraday capitulation bottom signals.

---

## CLI Tool: `options_analysis.py`

Scans an entire watchlist, scores each security on bullish and bearish signals, and recommends call and put trade allocations against a budget.

### Usage

```bash
python options_analysis.py --puts-budget 10000 --top-n 15
python options_analysis.py --symbol AMD --puts-budget 1000
python options_analysis.py --watchlist /path/to/custom.yaml --puts-budget 5000
python options_analysis.py --no-news --puts-budget 10000        # skip FinBERT
python options_analysis.py --no-persist --symbol AAPL           # skip DB save
python options_analysis.py --db /data/options_history.db
```

### Scoring system

**Long score drivers** (bounce/accumulation):

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
| FinBERT news signal: BULLISH | +2 |
| FinBERT news signal: MIXED | +1 |

**Put score drivers** (bearish/put trade):

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
| FinBERT news signal: BEARISH | +2 |

### Portfolio summary ranking

Trades are ranked by a blended conviction + ROI score and filled greedily until the budget is exhausted:

```
rank_score = 0.60 × (score / MAX_SCORE) + 0.40 × min(roi%, 200%) / 200%
```

ROI is capped at 200% to prevent high-ROI / low-conviction outliers from crowding out better-supported trades. A spread (bull call / debit put) is suggested when ATM IV exceeds 65%.

### Trade guardrails

Post-mortem-driven filters applied before any trade is built:

| Guardrail | Applies to | Condition | Reason |
|-----------|-----------|-----------|--------|
| **Earnings blackout** | Puts + Calls | Earnings within 14 days | Earnings gap stocks in either direction; a pure put or call thesis becomes a coin-flip against the event. |
| **Catalyst cooldown** | Puts only | FinBERT BULLISH within 5 days (falls back to keyword scan if INSUFFICIENT_DATA) | A positive catalyst may be exactly what drove put-heavy OI — institutions hedging long positions, not directional bets. |
| **BEARISH news** | Calls only | FinBERT BEARISH within 5 days | Bearish news undermines the bullish call thesis. |
| **Contradiction guard** | Puts only | `long_score ≥ 3` AND `put_score ≥ 3` simultaneously | When both oversold bounce signals and overbought put signals are elevated, the market is genuinely ambiguous. |

Blocked trades show `*** GUARDRAIL (PUT/CALL): reason ***` in the output but still appear in the scored list. Thresholds are configurable at the top of `options_analysis.py`:

```python
EARNINGS_BLACKOUT_DAYS    = 14   # days before earnings to block trades
CATALYST_LOOKBACK_DAYS    = 5    # days of news to scan for positive catalysts
CONTRADICTION_LONG_MIN    = 3    # long_score threshold for contradiction guard
CONTRADICTION_PUT_MIN     = 3    # put_score threshold for contradiction guard
```

### Call vs put comparison

Each candidate shows **both** a call trade and a put trade side-by-side:

- **Long candidates** — primary recommendation is a call trade (target = upper BB), with a put trade shown for comparison.
- **Put candidates** — primary recommendation is a put trade (target = lower BB), with a call trade shown for comparison.

If price has already broken through the target band, the target shifts 5% beyond it. Both sections end with their own portfolio summary.

### News sentiment integration

Without `--no-news`, the script collects and FinBERT-scores news for all watchlist symbols before the analysis loop begins. The FinBERT model is loaded once from local cache. Each candidate shows a `NEWS` line:

```
NEWS BULLISH  "Apple beats earnings, raises guidance"
NEWS MIXED    "EPAM Leads as a Top IT Service Provider in Belgium and Luxembourg"
NEWS INSUFFICIENT_DATA    ← fewer than 3 scored articles in the 5-day window
```

Signal coverage improves over daily runs as `news_sentiment.db` accumulates articles.

### IV Rank methodology

Computed from 252 days of rolling HV30 as a proxy for historical IV:

- **IV Rank** = `(current_iv − 52w_low_hv) / (52w_high_hv − 52w_low_hv) × 100`
- **IV Percentile** = % of past-year days where HV30 < current IV

High IV rank (≥80%) = fear/capitulation = potential bounce bottom. Low IV rank (≤20%) = complacency = ideal time to buy cheap puts.

### Watchlist format

```yaml
- name: Advanced Micro Devices
  symbol: AMD
  currency: USD
  tags:
    - AI Factory
    - Semiconductors
```

Non-US-listed symbols (suffixes `.PA`, `.OL`, `.AS`, `.SG`, `.KS`, `.ST`, `.DE`) are automatically skipped.

---

## Signal Combination Guide

### Bounce Bottom Checklist

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
| 16 | `get_news_sentiment` | BULLISH or MIXED signal (narrative aligning with technicals) |

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
| 8 | `get_news_sentiment` | BEARISH signal (narrative confirms downside thesis) |

---

## Data Limitations

| Source | Limitation |
|--------|-----------|
| All tools | Yahoo Finance data; options may lag up to 15 min |
| `ohlcv` table (QuantCore database) | Truncate the table to force a full re-fetch of OHLCV data (schema persists; rows are repopulated on demand) |
| `options_store.py` | ATM contracts only (5 strikes per side); nearest expiration only per snapshot |
| `get_short_interest` | FINRA bi-monthly update; lags up to 2 weeks |
| `get_dark_pool` | Proxy only — true dark pool requires paid feed (FINRA ATS, Bloomberg) |
| `get_bid_ask_spread` | Equity spread from `fast_info`; not tick-level data |
| `get_unusual_calls` | vol/OI and last≥ask proxies for sweeps; individual prints not available |
| `get_delta_adjusted_oi` | Black-Scholes delta; no dividend adjustment |
| `options_analysis.py` | IV Rank uses HV30 as IV proxy; true historical IV requires paid feed. Catalyst cooldown falls back to keyword matching when FinBERT has INSUFFICIENT_DATA. |
| `news_sentiment_server.py` | ~20 RSS + ~8 yfinance articles per symbol; INSUFFICIENT_DATA is common on first run — signal coverage improves with daily collection |
| FinBERT | Scores individual article titles/summaries; does not account for cross-article context or macro regime. Treat as a supplementary signal, not a primary thesis driver. |
| Non-US symbols | Options data unavailable; automatically skipped in watchlist scan |
