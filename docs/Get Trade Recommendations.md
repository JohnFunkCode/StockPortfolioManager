# Get Trade Recommendations
### Feature Summary & Implementation Overview

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Problem Statement](#problem-statement)
3. [What Was Built](#what-was-built)
4. [Signal Scoring System](#signal-scoring-system)
5. [Options Chain Integration](#options-chain-integration)
6. [Trade Type Decision Logic](#trade-type-decision-logic)
7. [Position Sizing](#position-sizing)
8. [Output Structure](#output-structure)
9. [Real-World Validation: AMZN Case Study](#real-world-validation-amzn-case-study)
10. [Technical Implementation](#technical-implementation)

---

## Executive Summary

This document describes the design and implementation of the `get_trade_recommendation` MCP tool — a synthesis engine that runs the full suite of available technical and market signals for a given stock and produces a single, actionable trade recommendation.

The tool eliminates the need to manually cross-reference 13 independent signals by aggregating them into a scored output that includes trade type, entry price, target, stop loss, position size, risk/reward ratio, and plain-English explanations of every contributing signal.

A key enhancement completed in this session added options chain intelligence — delta-adjusted open interest (DAOI) and upgraded unusual call sweep scoring — which allows the tool to detect when institutional options positioning contradicts technical signals, preventing false-negative recommendations.

---

## Problem Statement

Active trade decision-making requires integrating a large number of independent signals simultaneously:

- **Momentum indicators** (RSI, MACD, Stochastic) indicate whether a move is extended or just beginning
- **Price structure** (Bollinger Bands, VWAP, moving averages) identifies where price is relative to key levels
- **Volume and flow** (dark pool, bid/ask spread, short interest) reveals whether institutional money is accumulating or distributing
- **Options market data** (unusual call sweeps, put/call ratios, market maker hedging flows) shows how sophisticated market participants are positioned

Individually, each signal is ambiguous. Together, they tell a story. The problem is that synthesizing 13 signals manually for every trade candidate is time-consuming and prone to confirmation bias. The `get_trade_recommendation` tool automates this synthesis into a consistent, repeatable scoring framework.

---

## What Was Built

The `get_trade_recommendation` function is a tool registered on the `stock-price-server` MCP server. It accepts a stock symbol and an available capital amount, runs 13 sub-analyses in sequence, scores each signal as bullish or bearish, and returns a concrete trade recommendation.

**File:** `fastMCPTest/stock_price_server.py`

**Tool signature:**
```python
get_trade_recommendation(symbol: str, capital: float = 5000.0) -> dict
```

---

## Signal Scoring System

Thirteen independent signals are scored and aggregated into a net score. Each signal is collected in a separate `try/except` block so a failure in one data source does not invalidate the entire recommendation.

| # | Signal | Source Tool | Max Bull | Max Bear |
|---|--------|-------------|----------|----------|
| 1 | Bollinger Band position | `get_stock_price` | +2 | +2 |
| 2 | Put/Call ratio | `get_stock_price` | +1 | +1 |
| 3 | RSI (14-period) | `get_rsi` | +3 | +3 |
| 4 | MACD crossover | `get_macd` | +2 | +2 |
| 5 | Stochastic %K | `get_stochastic` | +2 | +2 |
| 6 | Volume / OBV analysis | `get_volume_analysis` | +3 | +2 |
| 7 | Candlestick patterns | `get_candlestick_patterns` | +1 | +1 |
| 8 | Unusual call sweeps | `get_unusual_calls` | +2 | — |
| 9 | Stop loss / support levels | `get_stop_loss_analysis` | — | — |
| 10 | Short interest / squeeze | `get_short_interest` | +1 | — |
| 11 | Dark pool flow | `get_dark_pool` | +2 | +2 |
| 12 | Bid/ask spread | `get_bid_ask_spread` | +1 | +1 |
| 13 | DAOI — MM hedge flows | `get_delta_adjusted_oi` | +2 | — |
| 14 | Options market positioning | (from DAOI data) | +1 | +1 |

The `net_score = bull_score - bear_score` determines both trade type and confidence level.

---

## Options Chain Integration

This session added two new signals and upgraded one existing signal to give the tool a complete picture of options market intelligence. Prior to this work, the tool only used a single +1 point for any call sweep activity — insufficient to reflect the weight of large institutional positioning.

### Upgrade: Unusual Call Sweep Scoring (Signal 8)

Previously both "strong" and "moderate" sweep signals scored +1. This underweighted institutional conviction.

**New scoring:**
- `strong` sweep signal → **+2 bull** (aggressive fills at/above ask, multiple high-conviction contracts)
- `moderate` sweep signal → **+1 bull** (unchanged)

### New Signal: DAOI / MM Hedge Flows (Signal 13)

Calls `get_delta_adjusted_oi()` to identify the direction market makers must trade to stay hedged as price moves.

**Scoring logic:**
- MM `buy_on_rally` signal `strong` or `moderate` → **+2 bull** (MM forced to buy stock as price rises, mechanically amplifies upside)
- MM `buy_on_rally` signal `weak` → **+1 bull**
- MM `sell_on_rally` → **warning added** (mechanical resistance without scoring as a bear point, since this reflects customer bullishness, not a bearish directional signal)

### New Signal: Options Market Directional Positioning (Signal 14)

Uses the `net_daoi_shares` value from the DAOI tool to detect when the options market overall is overwhelmingly positioned long or short.

**Scoring logic:**
- `net_daoi_shares > 50,000` → **+1 bull** (institutions net long calls by a meaningful margin)
- `net_daoi_shares < -50,000` → **+1 bear** (heavy put/defensive hedging)

The 50,000 share-equivalent threshold was chosen to filter noise — at a $260 stock price this represents approximately $13M in notional directional commitment.

### New Contradiction Warnings

Two new automated warnings were added to flag when options flow conflicts with the technical picture:

1. **Strong sweeps + MM sell_on_rally:** Smart money is buying calls but the MM structure creates upside resistance — requires price action confirmation before entry.
2. **Heavy call positioning + negative net_score:** The options market disagrees with bearish technical signals — a reason to avoid entering a put trade.

---

## Trade Type Decision Logic

Net score maps to a trade type via a decision tree. IV (implied volatility) determines whether to use a spread or outright option when IV is elevated.

| Net Score | Trade Type | Notes |
|-----------|-----------|-------|
| ≥ 5 | LONG_CALL or BULL_CALL_SPREAD | High IV (≥40%) → spread to offset premium cost |
| 3 – 4 | LONG_STOCK | Moderate bull; direct equity exposure |
| 1 – 2 | WEAK_LONG | Marginal signal; reduced position size |
| -2 – 0 | SKIP | Conflicting or neutral; no position |
| -3 – -4 | LONG_PUT | Moderate bear signal |
| ≤ -5 | LONG_PUT or BEAR_PUT_SPREAD | High IV → spread |

**Squeeze override:** If `squeeze_potential == HIGH` and `net_score >= 3`, the recommendation forces `LONG_CALL` regardless of IV, since a short squeeze amplifies directional moves.

---

## Position Sizing

The tool uses a **2% risk budget** — a standard institutional risk management principle that limits the capital at risk on any single trade to 2% of total available capital.

```
risk_budget = capital × 0.02
```

**For stock trades:**
```
shares = floor(risk_budget / (price − technical_stop))
```

**For options trades:**
```
contracts = max(1, floor(risk_budget / (atm_ask × 100)))
```

This ensures position size scales down automatically when stop distances are wide (volatile stocks) and scales up when stops are tight (clear technical levels).

---

## Output Structure

The tool returns a single dict with everything needed to place the trade:

```json
{
  "symbol":            "AMZN",
  "price":             263.99,
  "trade_type":        "SKIP",
  "action":            "HOLD",
  "confidence":        "LOW",
  "bull_score":        4,
  "bear_score":        6,
  "net_score":         -2,
  "entry":             263.99,
  "target":            null,
  "stop_loss":         null,
  "risk_reward_ratio": null,
  "position_size":     0,
  "estimated_cost":    0,
  "drivers":           ["...list of signals that fired with explanations..."],
  "warnings":          ["...contradictions and edge case flags..."],
  "signals_collected": 13,
  "options_context": {
    "avg_iv_pct":      128.3,
    "high_iv":         true,
    "atm_call_ask":    1.68,
    "atm_put_ask":     2.86,
    "put_call_ratio":  0.52,
    "mm_hedge_bias":   "sell_on_rally",
    "gamma_wall":      255,
    "delta_flip":      355,
    "net_daoi_shares": 85128
  }
}
```

The `drivers` list provides plain-English explanations for every signal that contributed to the score. The `warnings` list surfaces contradictions, edge cases, and caveats the trader should consider before entering. The `options_context` block (populated for options trade types) includes the full IV and MM positioning context needed to select an appropriate expiration and strike.

---

## Real-World Validation: AMZN Case Study

The AMZN analysis conducted during this session demonstrated exactly why the options chain integration was needed.

**Before the enhancement:**

| Signal | Value | Score |
|--------|-------|-------|
| RSI 80.5 | Deeply overbought | Bear +3 |
| Stochastic 99.1 | Overbought | Bear +2 |
| Hanging man candle | Bearish topping pattern | Bear +1 |
| MACD bullish | Positive momentum | Bull +1 |
| Unusual calls | Strong sweep | Bull +1 |
| **Net score** | | **-4 → LONG_PUT** |

The tool recommended buying a put. But a manual review of the options chain revealed 47 institutional call sweeps, including 32,000+ contracts traded in the $265 strike alone, and 85,000 net call-delta equivalents in DAOI — overwhelming institutional bullish positioning that directly contradicted the put recommendation.

**After the enhancement:**

| Signal | Value | Score |
|--------|-------|-------|
| RSI 80.5 | Deeply overbought | Bear +3 |
| Stochastic 99.1 | Overbought | Bear +2 |
| Hanging man candle | Bearish topping pattern | Bear +1 |
| MACD bullish | Positive momentum | Bull +1 |
| Unusual calls (upgraded) | Strong sweep | Bull **+2** |
| Options positioning (new) | 85,128 net call delta | Bull **+1** |
| MM hedge bias (new) | sell_on_rally | Warning only |
| **Net score** | | **-2 → SKIP** |

The recommendation correctly shifted to **SKIP** — recognizing that smart money is positioned long while technicals are overbought, and that no clear directional edge exists. The new warnings ("Strong call sweeps vs MM sell-on-rally") give the trader the full context to understand the conflict.

---

## Technical Implementation

**Primary file:** `fastMCPTest/stock_price_server.py`

**Lines modified:** 2438–2945 (the `get_trade_recommendation` function)

**Key changes:**
- Docstring updated to document all 13 signals and new scoring rules
- Signal 7 (unusual calls): upgraded strong sweep from +1 to +2
- Signals 12–13 added after Signal 11 (bid/ask spread), before score aggregation
- Two new contradiction warnings added after existing warning block
- `options_context` dict extended with `mm_hedge_bias`, `gamma_wall`, `delta_flip`, `net_daoi_shares`

**Dependencies added:** `get_delta_adjusted_oi` (already on the same MCP server, no new dependencies)

**Tests:** All 23 existing unit tests pass with no regressions.
