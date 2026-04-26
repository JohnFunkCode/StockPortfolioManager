# Plan: `get_trade_recommendation` MCP Tool

## Context

The codebase has 20+ MCP tools across four servers that each produce a narrow analytical signal (RSI, MACD, Bollinger Bands, P/C ratio, dark pool, short interest, FinBERT sentiment, etc.). None of them synthesize all signals into a single, concrete, actionable trade. The user wants one tool that takes a symbol, runs the full analysis suite, and returns a structured recommendation they can act on immediately: trade type, entry, target, stop, position size, and risk/reward.

## Signal inventory

```
stock_price_server.py:
  get_stock_price()       → price, BB position, P/C ratio (nearest exp)
  get_rsi()               → oversold / overbought label
  get_macd()              → crossover direction
  get_stochastic()        → %K/%D, oversold/overbought
  get_volume_analysis()   → capitulation events, OBV divergence, bottom_signal
  get_candlestick_patterns() → bullish/bearish reversal patterns
  get_higher_lows()       → price structure
  get_unusual_calls()     → unusual call activity
  get_delta_adjusted_oi() → gamma wall strike
  get_stop_loss_analysis()→ technical_stop, trailing_stop_pct

market_analysis_server.py:
  get_short_interest()    → squeeze_potential (HIGH / MEDIUM / LOW)
  get_dark_pool()         → net_signal (accumulation / distribution / mixed / none)
  get_bid_ask_spread()    → spread_vs_norm (narrowing / widening / elevated / normal)
```

## Architecture

```
get_trade_recommendation(symbol, capital=5000.0)
         │
         ├─ [parallel conceptually, sequential in code with try/except per call]
         │
         ├── get_stock_price()      ──→ bb_pos, p/c ratio
         ├── get_rsi()              ──→ rsi, signal
         ├── get_macd()             ──→ crossover
         ├── get_stochastic()       ──→ %K, signal
         ├── get_volume_analysis()  ──→ bottom_signal, obv_divergence
         ├── get_candlestick_patterns() → bullish/bearish patterns found
         ├── get_unusual_calls()    ──→ unusual activity flag
         ├── get_stop_loss_analysis() → technical_stop, trailing_stop_pct
         ├── get_short_interest()   ──→ squeeze_potential
         ├── get_dark_pool()        ──→ net_signal
         └── get_bid_ask_spread()   ──→ spread_vs_norm
                    │
              _aggregate_signals()
                    │
              bull_score / bear_score / net_score
                    │
              _select_trade_type()   (rules table below)
                    │
              _size_position()       (1-2% capital at risk)
                    │
              structured recommendation dict
```

## Scoring rules

**Bull points** (applied when signal fires):
| Signal | Points |
|---|---|
| RSI < 35 | +2 |
| RSI < 30 | +1 extra |
| MACD bullish_crossover | +2 |
| MACD bullish (no cross) | +1 |
| Stochastic %K < 25 | +2 |
| BB pos ≤ 0 (at/below lower band) | +2 |
| volume bottom_signal contains "strong" or "moderate" | +2 |
| OBV divergence | +1 |
| dark_pool accumulation | +2 |
| squeeze_potential HIGH | +1 |
| spread_vs_norm narrowing | +1 |
| unusual call activity | +1 |
| bullish candlestick reversal | +1 |

**Bear points** (symmetric):
| Signal | Points |
|---|---|
| RSI > 65 | +2 |
| RSI > 70 | +1 extra |
| MACD bearish_crossover | +2 |
| MACD bearish | +1 |
| Stochastic %K > 75 | +2 |
| BB pos ≥ 1 (at/above upper band) | +2 |
| volume exhaustion top | +2 |
| dark_pool distribution | +2 |
| spread_vs_norm widening | +1 |
| P/C ratio > 2.0 | +1 |

**net_score = bull_score − bear_score**

## Trade type selection

| net_score | IV context | Trade type |
|---|---|---|
| ≥ 5 | low IV (avg_iv < 40%) | `LONG_CALL` |
| ≥ 5 | high IV (avg_iv ≥ 40%) | `BULL_CALL_SPREAD` |
| 3–4 | — | `LONG_STOCK` |
| 1–2 | — | `WEAK_LONG` (small position, flag as low confidence) |
| -2 to +2 | — | `SKIP` |
| -3 to -4 | — | `LONG_PUT` |
| ≤ -5 | high IV | `BEAR_PUT_SPREAD` |
| ≤ -5 | low IV | `LONG_PUT` |

Squeeze override: if squeeze_potential == "HIGH" AND net_score ≥ 3, force `LONG_CALL` regardless of IV (short squeeze play).

## Position sizing

- Risk budget = `capital × 0.02` (2% risk per trade)
- **Stock trades**: `shares = floor(risk_budget / (price − technical_stop))`; `estimated_cost = shares × price`
- **Options trades**: use ATM contract ask from `get_stock_price()` options data; `contracts = floor(risk_budget / (ask × 100))` (minimum 1)
- Cap estimated_cost at 100% of capital; always show 1 contract minimum for options

## Output structure

```python
{
  "symbol": str,
  "price": float,
  "trade_type": str,        # LONG_CALL | BULL_CALL_SPREAD | LONG_STOCK | WEAK_LONG | LONG_PUT | BEAR_PUT_SPREAD | SKIP
  "action": str,            # BUY | SELL | HOLD
  "confidence": str,        # HIGH (|net|≥5) | MEDIUM (3-4) | LOW (1-2)
  "bull_score": int,
  "bear_score": int,
  "net_score": int,
  "entry": float,           # current price
  "target": float,          # BB upper (longs) or BB lower (shorts)
  "stop_loss": float,       # technical_stop from get_stop_loss_analysis
  "risk_reward_ratio": float,
  "position_size": int,     # shares or contracts
  "estimated_cost": float,
  "drivers": [str],         # "RSI 28 — oversold", "MACD bullish crossover", etc.
  "warnings": [str],        # contradictions and notable risks
  "signals_collected": int, # how many sub-calls succeeded
  "options_context": dict | None  # ATM call/put data when relevant
}
```

## Files to change

| File | Change |
|---|---|
| `fastMCPTest/stock_price_server.py` | Add import of `get_short_interest`, `get_dark_pool`, `get_bid_ask_spread` from `market_analysis_server`; add `get_trade_recommendation()` function at the bottom before `if __name__ == "__main__"` |

No new files needed. No changes to `market_analysis_server.py`.

**Import line to add** (top of `stock_price_server.py`, after existing imports):
```python
from market_analysis_server import get_short_interest, get_dark_pool, get_bid_ask_spread
```

Each sub-call is wrapped in `try/except Exception` and silently omitted from scoring if it fails, so the tool degrades gracefully for symbols missing options data, etc.

## Verification

```bash
cd /home/user/repo/fastMCPTest
python - <<'EOF'
import json, sys
sys.path.insert(0, ".")
from stock_price_server import get_trade_recommendation
result = get_trade_recommendation("AAPL", 5000.0)
print(json.dumps(result, indent=2))
EOF
```

Check that:
1. `trade_type` is one of the 7 valid strings
2. `drivers` list is non-empty and contains signal names
3. `stop_loss` < `entry` for long trades, > `entry` for short
4. `risk_reward_ratio` > 0
5. `signals_collected` ≥ 5 (most sub-calls succeeded)