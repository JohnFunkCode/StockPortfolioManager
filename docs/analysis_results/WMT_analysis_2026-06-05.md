# WMT Full Analysis — 2026-06-05

**Market cap:** $958B · **Sector:** Consumer Defensive · **Earnings:** 2026-08-20 (76d out, LOW risk)

Focus: full-stack signal review with emphasis on impact to the open $120/$125 Jun 12 bull call spread (25 contracts, $0.58 entry debit / $1,450 total).

---

## Critical context (vs spread position)

Since yesterday's $120.41 close, WMT has slipped back to **$119.82** — back *below* the $120 long strike. Yet the **spread mark has actually risen** to ~$1.53–1.57/share (vs ~$1.40 yesterday), implying IV expansion is offsetting the spot move. At $1.53 mid × 25 × 100, the mark sits ~**$3,825 (+$2,375 / +164% on debit)** — slightly *better* than yesterday's estimate, despite the price weakness.

But the trade engine has **downgraded**: yesterday HIGH/BULL_CALL_SPREAD/+5 → today **MEDIUM/LONG_STOCK/+3**. That is the key signal change of the day.

---

## Price / structure

| Level | Value | Significance |
|---|---|---|
| Spot | $119.82 | Back below long strike, between BE and bail |
| 20-day SMA | $124.39 | Mean-reversion gravity ~+3.8% above |
| BB upper / lower | $139.04 / $109.73 | Wide envelope — no extension stress |
| VWAP (20d) | $122.97 | **10 bars below**, -4.26% — trend headwind intact |
| Unfilled gap above | $124.41–$130.85 | First overhead resistance (gap from 5/21) |
| Unfilled gap up today | $116.89–$119.90 | Yesterday's gap — would need to fail this to bail |
| Gamma wall | $120 | **Long strike sits on it** — unchanged |

VWAP reclaim signal fired *moderate* but price has since fallen back. The hourly higher-low pattern is **not** established — downtrend structure on intraday remains intact.

---

## Momentum

- **MACD:** -3.44 / signal -2.31 / hist **-1.13** — bearish crossover, histogram has **not flipped** (daily-check item #4 still unmet)
- **RSI:** 38.5 — neutral, recovering from oversold
- **Stochastic:** %K 22.3, %D 14.1 — **bullish crossover from oversold** (daily-check item #5 now MET — first of the daily triggers to fire)
- **Candlesticks:** 5/28 dragonfly doji (strong bullish reversal, 5 down-days prior, near lower BB) — still the dominant pattern

---

## Volume / accumulation

- Last volume 0.93× avg — normal
- **OBV trend: falling** — no accumulation yet, confirms downtrend
- Capitulation bar 5/21 (2.87× volume) — bounce setup but no follow-through volume
- Dark pool proxy: no absorption / no two-sided flow

This is the weakest part of the bull case. Smart money is not accumulating in size — the bounce off $112.73 (6/2 low) has been on declining volume.

---

## Options positioning

- **Gamma wall: $120** — exactly the long-strike pin (unchanged)
- **Net DAOI: +40,059 sh, sell_on_rally** — MMs net long delta, must sell into rallies (mechanical resistance unchanged)
- **Delta flip: $180** — way out of reach; no mechanical tailwind
- **P/C ratio: 0.42** — bullish skew
- **Unusual calls: STRONG sweep signal, 15 unusual contracts**
  - High-conviction sweeps on Jun 12 expiry: $122 (vol/OI 1.78), $124 (vol/OI 2.43)
  - Plus moderate at $121 and $123 Jun 12
  - Plus high-conviction $121 / $122 Jun 5 (today)
  - Institutional flow **still concentrated in the $121–$124 profit zone** of the spread

Avg IV on the calls ≈ 28–30% — IV did expand somewhat, which is what is propping up the spread value as spot pulled back.

---

## Relative strength

| Window | WMT | SPY | Excess |
|---|---|---|---|
| 1m | **-7.7%** | +1.75% | -9.4pp |
| 3m | -2.81% | +11.36% | -14.2pp |
| 6m | +5.38% | +9.8% | -4.4pp |
| 12m | +21.63% | +26.73% | -5.1pp |

**Laggard** label. Consumer Defensive sector is *leading* (+57% 12m), but WMT is failing to participate — 1-month -7.7% is the standout problem. Short interest 1.84%, 4.9 DTC — MEDIUM squeeze potential but nothing fires that on its own.

---

## Fundamentals

Composite **5/14 → solid.** FCF margin +9% (positive), valuation cheap (log EV/Sales 0.33), 12-1 momentum +33.8%. But revenue CAGR only 5.3% (slow) and op margin 4.2% (thin/flat). No earnings risk for 76 days.

---

## Trade engine output

```
LONG_STOCK · MEDIUM confidence · net +3 (bull 7 / bear 4)
Entry $119.80 · Target $139.04 · Stop $107.54 · R/R 1.57
```

**Drivers:** stochastic oversold, dragonfly doji, strong sweep signal, solid fundamentals, sector tailwind.
**Warnings:** MM sell_on_rally, revenue inflecting negative, EPS decelerating, RS laggard, mixed signals.

Engine no longer recommends a *new* bull call spread structure — confidence and net score both dropped. The existing position should be managed, not added to.

---

## Bull vs bear ledger

| Bull | Bear |
|---|---|
| Strong sweep signal (15 contracts, 4 high-conviction at $121–$124 Jun 12) | MM sell_on_rally + gamma wall at $120 |
| Stochastic bullish cross from oversold (22 > 14) | 10 bars below VWAP, OBV falling |
| Dragonfly doji 5/28, near lower BB | MACD bearish, histogram has not flipped |
| RSI 38 recovering | No hourly higher-low pattern yet |
| Solid fundamentals, sector leading | RS laggard (-5.1pp 12m, -9.4pp 1m) |
| Unfilled gap up today ($116.89–$119.90) | Unfilled gap-down resistance at $124.41–$130.85 |
| Spread mark continues to rise via IV expansion | Engine downgraded HIGH→MEDIUM, +5→+3 |

---

## Impact on the WMT $120/$125 Jun 12 spread

**Where the position stands now:**

- Mark ~$1.53/share × 25 × 100 = **~$3,825** (vs $3,500 yesterday, $1,450 entry)
- Unrealized **~+$2,375 / +164%** on debit
- 5 trading days remaining (today + Mon–Thu next week)

**Key changes vs yesterday:**

1. **Engine downgrade is the biggest signal change.** Going HIGH→MEDIUM and +5→+3 in 24h, while spot pulled back below the long strike, validates the "take partial profits" call. The engine no longer rates this *as a fresh spread setup* — that means the risk/reward for the *remaining* contracts has degraded vs yesterday even though the dollar mark is up.

2. **Daily checklist scoreboard:**
    - WMT vs $120 / $120.58 / $125: **below long strike, below BE** — worse than yesterday
    - Spread mid risen or fallen: **risen** (IV expansion) — better than yesterday
    - 4-sweep institutional positioning at $122/$123/$124: **still active** (now also $121) — unchanged
    - MACD histogram flipped positive: **NO**, still -1.13 — unchanged
    - Stochastic %K above 50: **NO** (22), but **bullish cross fired** — first improvement
    - Above VWAP $122.97: **NO**, 10 bars below — unchanged
    - Bail trigger (close < $119): **$119.82 — within $0.82 of trigger** — danger zone

3. **The asymmetric calculus has gotten worse for the held portion:**
    - To reach max profit ($11,050 total / $5.00 spread): WMT needs +4.3% in 5 days *through* the $120 gamma wall + MM sell-on-rally + VWAP at $122.97 + unfilled gap at $124.41
    - To reach $120.58 BE: just +0.6%
    - To hit bail: just -0.7%
    - That is roughly a 6:1 ratio of room-to-loss vs room-to-max-profit *from here*

4. **Why the spread mark is up despite spot down:** call IV on the Jun 12 $120 expanded to ~28.6% — Friday-to-Monday weekend vol + nearing expiry gamma. This is a **good moment to realize** the IV-inflated mark before theta + a flat Monday open compress it.

---

## Recommendation update

**Yesterday's call to close 10–15 contracts is *more* warranted today, not less.** The dollar mark improved while the underlying signal set degraded — that is the textbook moment to harvest partial profit on a debit spread.

Concrete suggestion:

1. **Close 15 of 25 contracts at the current ~$1.53 mid** → ~$2,295 proceeds on that portion (~$1,425 profit vs $870 cost basis); locks in ~$1,400+ of the unrealized gain regardless of the next 5 days.
2. **Hold 10 contracts** with the **bail trigger tightened to a close below $119.80** (today's level) rather than $119 — the structure is leaning bearish and the bulk of profit is already booked.
3. **Re-evaluate Monday at midday:**
    - If WMT > $120.58 AND MACD histogram flipping → hold all 10 toward $125
    - If WMT < $119 AND no new sweep activity → close the rest
    - If sideways $119.50–$120.50 → close another 5, let 5 ride into Thu/Fri gamma

---

## Caveats

- News sentiment server returned INSUFFICIENT_DATA for WMT (coverage gap, not "no news") — confirm independently before any large size decision
- DAOI flip strike at $180 is a data artifact (low far-OTM OI) — do not read mechanical-tailwind signals from it
- IV rank not surfaced, but Jun 12 $120 IV ≈28.6% is on the high end for WMT — short premium has some edge here, which is partly why the *spread* (long premium on the lower strike, short premium on the higher) has gained
