# WMT $120/$125 Bull Call Spread — Trade Assessment

**Date:** 2026-06-05
**Source:** `WMT Bull Call Spread.pages` (monitor script output from 2026-06-04 16:38 UTC, by John Funk)
**Underlying:** Walmart Inc. (WMT)

---

## What the source file says

| Field | Value |
|---|---|
| Trade | Buy 25× Jun 12 $120 calls, Sell 25× Jun 12 $125 calls |
| Net debit | $0.58/share = **$1,450 total** |
| Spread width | $5.00 |
| Max loss | $1,450 (debit) |
| Max profit | $11,050 (at $125+) |
| Breakeven | $120.58 |
| Max profit ROI | 762% |
| Stock price at run | $118.13 |
| Status reported | **DEFENSIVE** (price below long strike) |
| Script's probabilities | 50–55% above BE, 35–40% max profit, **45–50% total loss** |

---

## Trade construction — assessment

The structure itself is sound:

- Defined-risk debit spread, max loss capped at $1,450
- $5 width, $0.58 debit = paying 11.6% of width. That's cheap for an ATM-ish spread — priced in the market's expectation of NOT clearing the short strike
- 25 contracts is meaningful size for the $1,450 budget — uses the full intended risk
- Aggressive: 8 calendar days to expiration when opened, needing +2.5% just to hit breakeven and +5.8% for max profit

The script's stated probabilities are roughly consistent with a stock at $118 needing to clear $120.58 in 8 days — about a coin flip on breakeven, lower odds of max profit. **Reasonable framing at trade entry.**

---

## What's changed since the script ran (24 hours later)

| Metric | At script run (6/4) | Now (6/5) |
|---|---|---|
| WMT spot | $118.13 | **$120.41** (+1.9%) |
| Long strike status | Below | **At/above** (status flipped DEFENSIVE → ATM-favorable) |
| Days to expiration | 8 | **7** (5 trading days) |
| Distance to breakeven | +2.1% | **−0.14%** (essentially at breakeven) |

---

## Current market state (2026-06-05)

- **Price** $120.41 · BB lower $109.73 / mid $124.39 / upper $139.04 · bb_pos 0.50 (mid-range)
- **VWAP** $122.97 — price is −2.09% below VWAP, **10 consecutive bars below** (downtrend intact)
- **MACD** bearish, hist −1.13 (still bearish but slope inflecting)
- **Stochastic** K 22.34 / D 14.12, **bullish crossover** from oversold
- **RSI** 38.5 — neutral
- **5/21 bearish capitulation bar** flagged (potential bounce base)
- **Volume** 0.93× avg — normal (no urgency either direction)
- **DAOI** net +52,728 — MM net LONG delta → **sell_on_rally** with **gamma wall at $120** (exactly where price sits — mechanical resistance)
- **13 unusual call sweeps detected** including **4 high-conviction at $122/$123/$124 Jun 12 strikes** — institutions actively positioning in the spread's profit zone
- **Trade engine:** BULL_CALL_SPREAD, **HIGH confidence**, net_score +5 — recommending the exact same trade structure NOW

---

## Estimated current spread value

Based on near-term chain quotes (live $125 strike pricer errored — known module bug):

- Long $120 call mid ≈ $1.62 (intrinsic $0.41 + ~$1.21 time value)
- Short $125 call mid ≈ $0.20 (extrapolated from $124 @ $0.45)
- **Spread mid ≈ $1.40/share** × 100 × 25 = **~$3,500 mark value**

**Unrealized P&L: approximately +$2,050 (+141% on the $1,450 debit)**

The position has flipped from DEFENSIVE to in-the-money. **The win is on paper right now.**

---

## Revised probability estimate (vs the script's)

The script's numbers were calibrated to WMT at $118. With WMT now at $120.41 and momentum/positioning shifting:

| Outcome | Script estimate | Revised estimate |
|---|---|---|
| Below $120 at exp (total loss) | 45–50% | **25–35%** |
| Between $120 and $120.58 (near-zero P&L) | implicit ~5% | ~10% |
| Between $120.58 and $125 (partial profit) | ~15–20% | **40–50%** |
| At or above $125 (max profit) | 35–40% | **15–25%** |

The "below $120 at expiration" case has shrunk (price is above), but **max profit has also gotten harder** — the $120 gamma wall + MM sell_on_rally creates real mechanical resistance to clearing $125 in 5 trading days.

---

## Bull vs bear ledger

### Bull for the position

- WMT has cleared the long strike ($120)
- Stochastic bullish crossover from oversold (timing entry into bounce)
- 4 high-conviction unusual calls at $122–$124 Jun 12 (smart money in the spread's profit zone)
- 5/21 capitulation bar (bounce base)
- Trade engine still HIGH confidence BULL_CALL_SPREAD
- Sector momentum: XLK leading (macro tailwind)

### Bear for the position

- MACD still bearish, hist −1.13
- 10 bars below VWAP (structural downtrend)
- DAOI sell_on_rally, gamma wall at $120 (mechanical resistance right at long strike)
- 1m return −7.29% (recent weakness)
- RS vs sector −35.38% (significantly lagging XLK)
- 5 trading days is tight — needs +3.8% from here for max profit
- Revenue trajectory inflecting negative + EPS deceleration (fundamental headwinds)

---

## Recommendation

**Take partial profits now. Don't expect max profit.**

| Action | Rationale |
|---|---|
| **Close 10–15 of 25 contracts at the current mark** (~$1.40/share) | Locks in $1,400–$2,100 profit; eliminates 25–35% total-loss risk on that portion |
| **Hold remaining 10–15 contracts** with mental stop: close all if WMT closes below $119 | Keeps optionality on the +50% middle / +20% max-profit upside |
| **Don't roll up** ($122/$127 or similar) | Adds complexity and re-introduces risk for marginal extra reward in a 5-day window |
| **Don't add to position** | Setup quality dimensions are mixed; engine HIGH but mechanical resistance at $120 is real |

---

## Concerns with the original construction

1. **5-day expiration is tight.** Aggressive directional bet on a name with weak 1-month RS. A Jun 18 or Jun 26 spread would have given more time for the bounce thesis to play out.
2. **25 contracts on a sub-3-week defined-risk trade is large.** Consider sizing relative to a budget that can absorb the 45–50% loss probability the script itself flagged.
3. **The $120 long strike is exactly at the gamma wall.** That's the most contested level. A $118/$123 or $122/$127 spread would have either started ITM (more expensive but more certain) or been more aggressive (cheaper but lower probability).

---

## Bottom line

The trade has worked so far — from DEFENSIVE to in-profit in 24 hours.

**Book at least half here.**

The original probability estimates need revision: total-loss risk has dropped, but max-profit probability has also dropped because the mechanical resistance at $120 is the dominant near-term feature. Middle-of-the-range outcomes ($120.58–$125) are now the most likely cluster, where the spread will settle somewhere between break-even and $5 × 25 contracts = $12,500 minus debit.

---

## Daily re-evaluation checklist through 2026-06-12

Each session before close:

1. Where is WMT vs $120 (long strike), $120.58 (BE), $125 (max profit)?
2. Has the spread mid value risen or fallen?
3. Is the 4-sweep institutional positioning at $122/$123/$124 still active?
4. Did MACD histogram flip positive yet (currently −1.13)?
5. Did stochastic %K cross above 50 (currently 22)?
6. Is WMT above or below VWAP $122.97?
7. **Bail trigger:** WMT closes below $119 → close all contracts

---

## Caveats

- Spread mid estimate uses extrapolated $125 strike quote; live pricer errored (known module bug)
- This is research output from a personal dashboard, **not financial advice**
- Position is held by John Funk, not the user; recommendations should be passed to him for action
- Probability estimates are heuristic — short-dated options can move sharply on macro or sector news
