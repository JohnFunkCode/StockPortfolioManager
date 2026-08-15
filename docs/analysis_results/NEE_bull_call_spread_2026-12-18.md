# NEE Bull Call Spread — Dec 18, 2026

**Analysis date:** 2026-05-26
**Budget:** $1,000
**Horizon:** Year-end 2026 (~7 months)
**Risk profile:** Defined-risk options spread

---

## The Trade

| Field | Value |
|---|---|
| Underlying | **NEE** (NextEra Energy, Inc.) |
| Strategy | Bull call spread (vertical debit) |
| Expiration | **December 18, 2026** |
| Long leg | Buy **2** Dec 18 **$90** Calls |
| Short leg | Sell **2** Dec 18 **$100** Calls |
| Net debit (current) | ~$4.50 per share × 200 = **$900** |
| Target entry | **$4.10–$4.20** net debit (work limit up from mid) |

### Economics at the target entry of $4.10

| Metric | Value |
|---|---|
| Total cost (debit paid) | $820 |
| Max profit | $1,180 (+144% on debit) |
| Max loss | $820 (entire debit) |
| Breakeven at expiry | NEE = **$94.10** |
| Full payoff at expiry | NEE ≥ **$100** by Dec 18 |
| Cash reserve | ~$180 |

---

## What is NEE?

**NextEra Energy, Inc.** — largest U.S. electric utility by market cap (~$180B), based in Juno Beach, FL.

Two businesses:
1. **Florida Power & Light** — regulated electric utility serving ~12M Floridians (steady cash flow)
2. **NextEra Energy Resources** — world's largest wind/solar generator, plus battery storage and emerging nuclear (Duane Arnold restart, SMRs)

**Why it matters:** Hyperscalers (Microsoft, Google, Amazon, Meta) need carbon-free power at massive scale for AI data centers. NEE is the dominant U.S. supplier. Looks like a sleepy utility; behaves like an AI infrastructure play.

---

## Why this trade

| Signal | Reading |
|---|---|
| Price | $88.04 — right at the lower Bollinger Band ($87.75) → oversold |
| Stochastic %K | 18.4 — oversold |
| Unusual calls | **Strong sweep signal** — aggressive institutional buying |
| DAOI hedge bias | **buy_on_rally** — market makers mechanically buy as price rises |
| Relative strength | +5.5% vs SPY over 12m — outperforming |
| Fundamentals | Solid (composite score 7) |
| Revenue | Inflecting positive |
| News sentiment | Moderately positive (40% bullish) |
| Trade engine | **HIGH confidence BULL_CALL_SPREAD**, net_score +7 (bull 9 / bear 2) |

### Watchlist screen ranking

- **long_score: 10** (3rd of 163 names screened)
- P/C ratio 0.31 (very bullish)
- Put unwinding signal (fear fading)

### Risks

- MACD bearish — price may chop lower before reversing
- EPS deceleration in fundamentals
- ~18% bid/ask spreads on each leg — slippage on entry/exit
- Total loss possible if NEE finishes below $90 on Dec 18

---

## How the trade makes money

Two things at once:
1. **Buy** the right to buy NEE at **$90** (pay ~$7.10/share)
2. **Sell** someone else the right to buy NEE at **$100** (collect ~$2.60/share)

Net cost per share: $4.50. Two contracts × 100 shares = **$900 outlay**.

### Payoff at expiry (Dec 18, 2026)

| NEE price on Dec 18 | Your $900 becomes | P&L |
|---|---|---|
| Below $90 | $0 | **−$900** |
| $94.50 | $900 | **breakeven** |
| $97 | $1,400 | **+$500** |
| **$100 or higher** | **$2,000** | **+$1,100** (max) |

Above $100, the $90 right and the $100 right cancel out — that's why the profit caps at the $10 strike difference.

---

## Limit order setup

Use the broker's **vertical / bull call spread builder** — never leg in separately (NEE moves between clicks = lost money).

### Order ticket

| Field | Value |
|---|---|
| Strategy | Vertical / Bull Call Spread / Debit Spread |
| Symbol | NEE |
| Expiration | Dec 18, 2026 |
| Long leg | **Buy +2** $90 Call |
| Short leg | **Sell −2** $100 Call |
| Order type | **Limit** |
| Limit price | **$4.10 net debit** |
| Time in force | Day |

### Price reference

- $90 call: bid $5.90 / ask $7.10 / mid $6.50
- $100 call: bid $2.60 / ask $3.10 / mid $2.85
- Spread mid: $3.65 · Spread ask: $4.50

**Start at $4.10. Work up by $0.10 every 5 minutes if no fill. Hard ceiling: $4.50.** Each $0.10 = $20 more cost on 2 contracts.

### Sanity check before submitting

Ticket should show approximately:
- Net debit: $4.10
- Total cost: $820
- Max loss: $820
- Max gain: $1,180
- Breakeven: $94.10

### Timing

- **Market hours only:** 9:30 AM – 4:00 PM ET
- **Best window:** 10:00 AM – 3:00 PM ET
- **Avoid:** first 15 min and last 15 min (wider spreads)
- **Bonus:** place on a flat or red NEE day for cheaper entry

---

## Exit plan

You can close any trading day before Dec 18 by selling the spread back as one combined order. Same mechanics as entry, reversed.

### Profit-taking rule

**Close at 60–70% of max profit** (~$715 profit, exit value ~$1,615). Don't hold to expiry for the last 30% — it's gamma-trapped and the marginal reward isn't worth the marginal risk.

### Indicative exit values

| Scenario | NEE price | When | Exit value | P&L |
|---|---|---|---|---|
| Quick pop | $94 | 1 month in | ~$1,300 | +$400 |
| Slow grind | $96 | 4 months in | ~$1,500 | +$600 |
| Near max early | $99 | 5 months in | ~$1,700 | +$800 |
| Drops fast | $84 | 1 month in | ~$500 | −$400 |
| Sideways | $88 | 4 months in | ~$700 | −$200 |

### Bail rule

**Close at ~50% loss if NEE closes below $85 on rising volume** (loss of lower BB on confirmation). Better to take a $400 loss than ride it to a full $900 wipeout.

### Reassess at $94

That's breakeven. Decision point: lock partial profit, or hold for the full payoff toward $100?

### Exit mechanics

Always use a **limit order at or above mid-price**, not the bid. Spread bid/asks are wide (~18%); patience saves $50–$100 on exit.

---

## Caveats

- "Maximize profits" with $1,000 over 7 months requires concentration — total loss is the realistic floor
- The IV-rank "extreme fear" signal in the screener is degenerate today (fires on nearly all names); other signals weighted higher
- This is research output from a personal dashboard, **not financial advice**
- Earnings deceleration is a real fundamental headwind worth monitoring

---

## Position tracker (fill in after entry)

| Field | Value |
|---|---|
| Entry date | _pending_ |
| Fill price (net debit) | _pending_ |
| Total cost | _pending_ |
| Exit date | _pending_ |
| Exit price | _pending_ |
| Net P&L | _pending_ |
| Notes | _pending_ |

---

## Reevaluation — 2026-05-28

Price essentially unchanged ($88.04 → $88.10) but technical state under the price has shifted.

### Signal delta vs entry-day reading

| Signal | 2026-05-27 | 2026-05-28 | Δ |
|---|---|---|---|
| Price | $88.04 | $88.10 | flat |
| Bollinger lower | $87.75 | $86.12 | Band widened down (volatility expansion) |
| BB position | 0.017 (at lower band) | 0.29 (mid-range) | Less "clean oversold" |
| VWAP(20) | $87.41 (+2.89%, 1 bar above) | $91.82 (**−4.54%, 8 bars below**) | Structural downtrend confirmed |
| MACD histogram | −0.062 | **−0.79** | 12× more bearish |
| Stochastic %K | 18.4 | **11.12** | Deeper oversold (capitulation forming) |
| Volume capitulation | n/a | **5/18 capitulation bar** (3.8× vol) | New bottom signal |
| Bid/ask spread | n/a | **narrowing**, "strong bottom signal" | Liquidity returning |
| Unusual calls | strong sweep | **3 sweeps, 1 very-high conviction** at Jun 5 $101 (+14.6% OTM, paid above ask) | Stronger institutional bull flow |
| DAOI bias | buy_on_rally moderate | buy_on_rally **weak** (−139 net) | Hedging tailwind weaker |
| Relative strength vs SPY | +5.5% (12m) | +4.27% (12m), **−8.72% (1m)** | Recent slide |
| Trade engine | BULL_CALL_SPREAD HIGH +7 | BULL_CALL_SPREAD HIGH **+6** | Essentially unchanged |

### Interpretation

The thesis is intact but more stressed. NEE has been quietly bleeding — 8 straight days below VWAP, MACD histogram expanded sharply bearish. The "at the lower BB" entry from yesterday became a "bandwidth-expanding downtrend" today.

But the bullish setup signals also strengthened:
- 5/18 capitulation bar — selling-exhaustion event that often marks bottoms
- Bid/ask narrowing — fear receding
- Institutional unusual-call flow intensified — very-high-conviction sweep at $101 strike, +14.6% OTM, paid above ask. Smart money is positioning for exactly what our thesis predicts.
- Stochastic deeper oversold

This is the classic "capitulation before bounce" pattern.

### Verdict

**Thesis intact. Entry timing marginally better today than yesterday.**

- Price unchanged → debit target unchanged (~$4.10 limit)
- Capitulation + smart-money positioning = better entry timing than buying into an extended setup
- Risk: slide may continue a few more days before bouncing (MACD says yes); saved bail rule ($85 close on rising volume) sits at the right level

### Concerns

1. MACD deterioration is real — histogram −0.79 is meaningful
2. VWAP at $91.82 is now overhead resistance; spread needs +13.5% climb to $100 from below VWAP rather than already-above
3. Sector momentum: XLU still lagging
4. News server returned 0 articles today — coverage gap

### Confirmation trigger

If you want to wait for confirmation rather than enter into the capitulation, the cleanest trigger is:

> **NEE closes > $89 (gamma wall) on volume > 12M shares**

That marks the bottom complete and bounce confirmed. Costs a slightly higher debit but de-risks the timing.
