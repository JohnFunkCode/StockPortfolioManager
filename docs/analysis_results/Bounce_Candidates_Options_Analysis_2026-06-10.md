# Bounce Candidates & Options Plays — Watchlist Screen (2026-06-10)

**Source:** `Watchlist_Returns_Fundamentals_2026-06-10.html`
**Filter:** Composite Score > 1 AND 30-Day Return > 20% → **59 symbols matched**

## Methodology

1. Ran `get_trade_recommendation` (synthesizes RSI, MACD, Stochastic, Bollinger Bands, OBV, volume, candlestick patterns, dark pool, unusual call sweeps, delta-adjusted OI / MM hedge flows, options positioning, and FinBERT news sentiment into a `net_score` and trade type) on all 59 symbols.
2. Selected the **top 12** by signal strength, prioritizing classic "bounce in the making" technicals: oversold Stochastic (<22), Bollinger Band lower-band touches, bullish reversal candles (hammer/inverted-hammer/dragonfly doji), and OBV bullish divergence.
3. For each of the 12, pulled the live/cached options chain and priced a near-the-money **bull call spread** (~5 weeks out, exp **2026-07-17**) to evaluate concrete options plays.

**Market backdrop:** Almost every name in this list is *down over the trailing 5 days* despite huge 30/60-day gains — this is a broad semiconductor/tech pullback after a massive run, which is exactly the setup where "bounce" signals matter most.

---

## TL;DR — Ranked Picks

| Rank | Symbol | Composite | Net Score | Why |
|---|---|---|---|---|
| 1 | **QCOM** | 8 (strong_compounder) | 7 | Deepest oversold reading (Stoch 1.6) + OBV bullish divergence on a top-tier compounder, with **good** options liquidity and the lowest IV (86%) of the group |
| 2 | **AMAT** | 6 (solid) | **10** | MACD just turned bullish, strong EPS acceleration, accelerating revenue, strong call sweeps |
| 3 | **AKAM** | 5 (solid) | 9 | Deepest BB/Stochastic oversold of any large-cap (BB -0.07, Stoch 1.8) + OBV divergence |
| 4 | **WOLF** | 2 (average) | **14 (highest of all 59)** | Textbook capitulation bottom: BB lower band, Stoch 1.1, OBV divergence, volume-bottom signal — but weak fundamentals & thin options |
| 5 | **UMC** | 4 (solid) | 9 | Oversold + dragonfly doji, cheapest/most efficient spread (debit $1.20, R/R 2.33) |
| 6 | **APLD** | 4 (solid) | 8 | Oversold (Stoch 9.4) + hammer candle + strong call sweeps, decent liquidity |
| 7 | **MXL** | 2 (average) | 9 | BB lower band + Stoch 10.8 + OBV divergence + inverted hammer; thin liquidity |
| 8 | **SIMO** | 3 (average) | 10 | Oversold + inverted hammer, but options are thin |
| 9 | **POET** | 2 (average) | 8 | Oversold + bid/ask narrowing, excellent options liquidity for a sub-$11 stock |
| 10 | **WDC** | 4 (solid) | 9 | Oversold (Stoch 21.8) but candle is a bearish shooting star — mixed |
| 11 | **ORA** | 8 (strong_compounder) | 9 | Best fundamentals in the group, but already near its BB upper band — limited bounce room |
| 12 | **CRDO** | 7 (solid) | 9 | Strong fundamentals/momentum but shooting-star candle + poor spread economics at the strikes priced |

---

## Deep Dive: Top 12 Bounce Candidates

### 1. QCOM — Qualcomm ⭐ Best overall combination
- **Price** $191.20 | 30D +27.94% | Composite **8 (strong_compounder)**
- **Bounce signals:** Stochastic %K **1.6** (deeply oversold), OBV bullish divergence (accumulation beneath the surface), strong unusual call sweep (aggressive institutional buying), DAOI shows moderate MM "buy-on-rally" flow (dealers must *buy* as price rises — tailwind, not headwind)
- **Caution:** Revenue inflecting negative, news sentiment moderately negative (40% bearish articles)
- **Options play:** 195/230 call spread, exp 2026-07-17 — debit **$11.65** (mid $10.45), max profit $23.35, max loss $11.65, breakeven $206.65, **R/R 2.0**. Liquidity **good** (short leg: 1,584 vol / 6,372 OI).
- **Take:** The combination of a top-tier compounder, the single most oversold stochastic reading of any liquid name in the screen, dealer flow that supports a rally, and the lowest IV (86%) in this whole group (vs. 150–500% elsewhere) makes this the highest-quality bounce/options setup.

### 2. AMAT — Applied Materials
- **Price** $497.01 | 30D +30.57% | Composite **6 (solid)**
- **Signals:** MACD just turned **bullish** (momentum inflection, not just oversold), EPS acceleration "strong", revenue accelerating, strong call sweep, news strongly positive (70% bullish), highest net_score among quality names (10)
- **Caution:** Candlestick is a shooting star (moderate bearish) — possible near-term chop before continuation
- **Options play:** 500/520 call spread, exp 2026-07-17 — debit **$11.00** (mid $7.87), max profit $9.00, max loss $11.00, breakeven $511, R/R 0.82 (mid-priced R/R better at ~1.14). Liquidity **good**.
- **Take:** Best "trend resuming" story in the group rather than a pure oversold bounce. The 500/520 spread is a bit rich at ask — consider a wider spread (e.g., 500/530) or working the mid for better R/R.

### 3. AKAM — Akamai
- **Price** $129.97 | 30D +36.19% | Composite **5 (solid)**
- **Signals:** BB position **-0.07** (trading *below* the lower band), Stochastic %K **1.8** (most oversold large-cap in the screen besides QCOM), OBV bullish divergence, EPS acceleration moderate, moderate call sweep
- **Caution:** Revenue inflecting negative
- **Options play:** 130/150 call spread, exp 2026-07-17 — debit **$7.00** (mid $6.70), max profit $13.00, max loss $7.00, breakeven $137, **R/R 1.86**. Liquidity thin but tradeable.
- **Take:** One of the cleanest "stretched-to-the-downside, accumulation underneath" setups in the entire list.

### 4. WOLF — Wolfspeed ⭐ Highest raw signal score (speculative)
- **Price** $43.42 | 30D +67.97% | Composite **2 (average)**
- **Signals:** BB position **-0.09**, Stochastic %K **1.1** (most oversold of all 59), OBV bullish divergence, **volume-bottom/capitulation signal**, bid/ask spread narrowing (liquidity returning), MM hedge bias is **buy-on-rally** (favorable structure — most other names are sell-on-rally), news strongly positive (70%)
- **Caution:** Fundamentals weak (composite 2, "average"), the tool's own technical stop ($45.04) sits *above* current price (used a trailing stop fallback instead) — i.e., this is already broken on a pure technical-stop basis, so size accordingly
- **Options play:** 45/60 call spread, exp 2026-07-17 — debit **$4.50** (mid $3.43), max profit $10.50, max loss $4.50, breakeven $49.50, **R/R 2.33**. Liquidity **thin**.
- **Take:** Highest net_score (14) of any of the 59 — a textbook capitulation/reversal pattern stack — but it's a speculative momentum/technical trade on a name with weak fundamentals, not a quality-compounder bounce. Treat as a smaller, higher-risk position.

### 5. UMC — United Microelectronics
- **Price** $18.90 | 30D +62.09% | Composite **4 (solid)**
- **Signals:** Stochastic %K 16.1 (oversold), dragonfly doji (bullish reversal), 86,005 net call delta (institutions long), news strongly positive (70%)
- **Options play:** 19/23 call spread, exp 2026-07-17 — debit **$1.20** (mid $1.00), max profit $2.80, max loss $1.20, breakeven $20.20, **R/R 2.33**. Liquidity **acceptable** (short leg OI 4,235).
- **Take:** Cheapest, most capital-efficient play in the list — a $120/contract bet with 2.3x R/R on a name with strong relative strength (+152.6% vs SPY over 12m).

### 6. APLD — Applied Digital
- **Price** $38.92 | 30D +21.21% | Composite **4 (solid)**
- **Signals:** Stochastic %K 9.4 (oversold), **hammer** candle (classic bounce-bottom pattern), strong call sweep, news strongly positive (70%)
- **Options play:** 40/48 call spread, exp 2026-07-17 — debit **$2.54** (mid $2.32), max profit $5.46, max loss $2.54, breakeven $42.54, **R/R 2.15**. Liquidity good on long leg (vol 1,078 / OI 3,428).
- **Take:** Clean hammer-at-the-lows setup with one of the better-traded option chains among the small-caps.

### 7. MXL — MaxLinear
- **Price** $71.95 | 30D +38.34% | Composite **2 (average)**
- **Signals:** BB position -0.03 (below lower band), Stochastic %K 10.8, OBV bullish divergence, inverted hammer, news strongly positive (80% bullish — highest of the group)
- **Options play:** 75/95 call spread, exp 2026-07-17 — debit **$7.00** (mid $5.90), max profit $13.00, max loss $7.00, breakeven $82, R/R 1.86. Liquidity **thin** (short leg only 5 vol).
- **Take:** Strong technical stack and the most positive news sentiment of any name screened, but weak fundamentals and thin options — fine as a small speculative position.

### 8. SIMO — Silicon Motion
- **Price** $251.68 | 30D +69.05% | Composite **3 (average)**
- **Signals:** Stochastic %K 16.7 (oversold), inverted hammer, news strongly positive (60%), highest net_score (10) tied with AMAT
- **Options play:** 250/290 call spread, exp 2026-07-17 — debit **$18.60** (mid $14.35), max profit $21.40, max loss $18.60, breakeven $268.60, R/R 1.15. Liquidity **thin** (only 2 contracts traded on both legs) — long leg already in-the-money.
- **Take:** Good technical signal but the options chain is too thin to size meaningfully; if pursuing, use limit orders at/near mid.

### 9. POET — POET Technologies
- **Price** $10.97 | 30D +36.67% | Composite **2 (average)**
- **Signals:** Stochastic %K 14.8 (oversold), bid/ask spread narrowing (bounce setup), moderate call sweep
- **Options play:** 11/15 call spread, exp 2026-07-17 — debit **$1.21** (mid $1.07), max profit $2.79, max loss $1.21, breakeven $12.21, **R/R 2.31**. Liquidity **excellent for a sub-$11 name** (long leg 324 vol/9,631 OI; short leg 1,614 vol/11,511 OI).
- **Take:** Surprisingly deep, liquid chain for a small-cap — best liquidity-to-price ratio of any name on this list.

### 10. WDC — Western Digital
- **Price** $490.09 | 30D +25.38% | Composite **4 (solid)**
- **Signals:** Stochastic %K 21.8 (oversold), strong relative strength (+811.8% vs SPY over 12m — largest of the entire screen), news strongly positive (70%)
- **Caution:** Candle is a moderate **shooting star** (bearish) — conflicts with the oversold reading
- **Options play:** 500/550 call spread, exp 2026-07-17 — debit **$23.95** (mid $17.90), max profit $26.05, max loss $23.95, breakeven $523.95, R/R ~1.09. Liquidity acceptable.
- **Take:** Mixed signal — oversold but a fresh top-looking candle. Wait for confirmation (e.g., a green close above the shooting star's high) before entering.

### 11. ORA — Ormat Technologies
- **Price** $136.69 | 30D +21.02% | Composite **8 (strong_compounder, tied for best fundamentals)**
- **Signals:** Bid/ask spread narrowing (liquidity returning), strong EPS acceleration, revenue accelerating, relative strength leader (+54% vs SPY)
- **Caution:** Price ($136.69) is already essentially at the BB upper band ($145.48 target) — limited room left in this leg; lower IV (~46-48%) reflects its utility/renewables profile
- **Options play:** 135/145 call spread, exp 2026-07-17 — debit **$5.60** (mid $4.30), max profit $4.40, max loss $5.60, breakeven $140.60, R/R 0.79. Thin liquidity.
- **Take:** Best fundamentals of the bunch, but this is more "still in an uptrend" than "bouncing off a low" — the spread economics aren't attractive at these strikes.

### 12. CRDO — Credo Technologies
- **Price** $237.68 | 30D +43.25% | Composite **7 (solid)**
- **Signals:** MACD bullish crossover, strong revenue/EPS acceleration, relative strength leader (+212% vs SPY), strong call sweep, news strongly positive (70%)
- **Caution:** Candle is a moderate **shooting star** (bearish), and stock-level R/R from `get_trade_recommendation` was poor (0.67)
- **Options play:** 240/250 call spread, exp 2026-07-17 — debit **$6.10** (mid $3.90), max profit $3.90, max loss $6.10, breakeven $246.10, R/R 0.64. Liquidity good.
- **Take:** Strong fundamental/momentum story, but the shooting star plus poor spread economics at these strikes argue for waiting for a better entry or using wider strikes.

---

## Exceptional Options Plays — Highlights

If narrowing to the **best risk/reward options structures** specifically (not just bounce signals):

| Symbol | Spread (exp 7/17) | Debit (mid) | Max Profit | R/R | Liquidity | Why it stands out |
|---|---|---|---|---|---|---|
| **QCOM** | 195C/230C | $10.45 | $23.35 | **2.0** | Good | Best fundamentals + deepest oversold + lowest IV (86%) of the group + good liquidity |
| **UMC** | 19C/23C | $1.00 | $2.80 | **2.33** | Acceptable | Cheapest absolute dollar risk ($120/contract), oversold + bullish reversal candle |
| **POET** | 11C/15C | $1.07 | $2.79 | **2.31** | Excellent | Surprisingly deep chain for a sub-$11 stock, oversold |
| **WOLF** | 45C/60C | $3.43 | $10.50 | **2.33** | Thin | Highest signal score of all 59 (14) — capitulation bottom + favorable MM buy-on-rally flow, but weak fundamentals |
| **AKAM** | 130C/150C | $6.70 | $13.00 | **1.86** | Thin | Deepest BB/Stochastic oversold of any large-cap + OBV accumulation |

**Note on IV:** Almost every name in this screen has IV between 150–500% (vs. QCOM's relatively tame 86% and AMAT's 73%) — these reflect speculative semiconductor/AI-infrastructure names where premium is expensive. High IV favors **spreads over outright long calls** (consistent with the tool's own BULL_CALL_SPREAD recommendations across the board).

---

## Stocks to Avoid / Fade — Despite Meeting the Screen Criteria

These matched composite > 1 and 30D return > 20%, but the technical/sentiment screen flags them as **overbought, bearish, or no-edge** — i.e., the opposite of a "bounce in the making":

| Symbol | Composite | 30D Ret | Trade Type | Net Score | Why |
|---|---|---|---|---|---|
| **GEO** | 3 | +49.52% | BEAR_PUT_SPREAD | **-8** | RSI 82.9 and Stochastic 94.2 — deeply overbought, BB position 1.15 (above upper band) |
| **MOG-A** | 6 | +24.88% | LONG_PUT | -4 | RSI 76.0 deeply overbought, Stochastic 80.2 |
| **QLYS** | 6 | +28.59% | LONG_PUT | -4 | RSI 65.4 overbought, weak relative strength (-44.6% vs SPY/12m) |
| **GTLB** | 3 | +27.05% | BEAR_PUT_SPREAD | -6 | Weak relative strength (-62.3% vs SPY), strongly negative news |
| **RPD** | 2 | +23.88% | BEAR_PUT_SPREAD | -6 | Weakest relative strength of the entire screen (-94.5% vs SPY) |
| **NTNX** | 2 | +20.24% | LONG_PUT | -3 | Bearish MACD crossover, weak relative strength |
| **IFNNY** | 6 | +42.56% | LONG_PUT | -3 | Dark-pool distribution, strongly negative news (80% bearish) |
| **ORCL** | 8 | +21.27% | WEAK_LONG | 2 | **Earnings today (0 days)** — avoid new options positions, IV crush imminent |
| **OKTA, ESTC, RBRK, TENB, FTNT, OUST, AI** | 3-7 | 20-50% | SKIP | -2 to 0 | Conflicting/neutral signals, no clear edge |

---

## Full Screen Results — All 59 Matches

Sorted by composite score (descending). ⭐ = one of the 12 deep-dived above.

| Symbol | Composite | 30D Ret | Trade Type | Net Score | Notable Signal |
|---|---|---|---|---|---|
| 000660.KS | 9 | +67.21% | LONG_CALL | 9 | Inverted hammer; no US options chain (Korea-listed) |
| LRCX | 8 | +28.09% | BULL_CALL_SPREAD | 6 | High IV (184%), MM sell-on-rally caps upside |
| **QCOM** ⭐ | 8 | +27.94% | BULL_CALL_SPREAD | 7 | Stoch 1.6 oversold + OBV bull divergence |
| ORCL | 8 | +21.27% | WEAK_LONG | 2 | Earnings today — avoid options |
| **ORA** ⭐ | 8 | +21.02% | BULL_CALL_SPREAD | 9 | Bid/ask narrowing, top fundamentals |
| DOCN | 7 | +81.68% | LONG_STOCK | 4 | Hammer candle, poor R/R |
| MRVL | 7 | +64.84% | BULL_CALL_SPREAD | 6 | MACD bull crossover, +146K net call delta |
| FTNT | 7 | +62.02% | SKIP | 0 | No clear edge |
| **CRDO** ⭐ | 7 | +43.25% | BULL_CALL_SPREAD | 9 | MACD bull crossover, but shooting star |
| AMD | 7 | +39.97% | LONG_STOCK | 4 | Stoch 18.1 oversold |
| CSCO | 7 | +36.77% | BULL_CALL_SPREAD | 7 | Bid/ask narrowing, +79K net call delta |
| DVA | 7 | +32.30% | LONG_STOCK | 4 | Poor R/R (0.61) |
| DELL | 6 | +79.59% | BULL_CALL_SPREAD | 5 | MACD bear cross, shooting star |
| SMTC | 6 | +63.43% | BULL_CALL_SPREAD | 7 | Moderate call sweep |
| HPE | 6 | +62.75% | LONG_STOCK | 4 | MACD bullish |
| ARM | 6 | +54.76% | BULL_CALL_SPREAD | 6 | Dragonfly doji, but strongly negative news |
| IFNNY | 6 | +42.56% | LONG_PUT | -3 | Bearish — dark pool distribution |
| **AMAT** ⭐ | 6 | +30.57% | BULL_CALL_SPREAD | **10** | MACD turning bullish, strong EPS accel |
| QLYS | 6 | +28.59% | LONG_PUT | -4 | Overbought — fade candidate |
| ASML | 6 | +25.25% | BULL_CALL_SPREAD | 7 | MACD bullish |
| MOG-A | 6 | +24.88% | LONG_PUT | -4 | RSI 76 deeply overbought — fade candidate |
| TKR | 6 | +24.26% | BULL_CALL_SPREAD | 5 | MACD bullish, poor R/R |
| ACMR | 5 | +61.44% | WEAK_LONG | 2 | Low confidence |
| PANW | 5 | +45.43% | BULL_CALL_SPREAD | 5 | Hammer candle |
| STX | 5 | +40.92% | BULL_CALL_SPREAD | 5 | Moderate call sweep |
| **AKAM** ⭐ | 5 | +36.19% | BULL_CALL_SPREAD | 9 | BB lower band + Stoch 1.8 + OBV divergence |
| 6971.T | 5 | +31.86% | LONG_STOCK | 3 | No US options (Japan-listed) |
| HSYDF | 5 | +30.00% | LONG_STOCK | 3 | Stoch 6.2 oversold; OTC, no options |
| ALAB | 4 | +80.49% | BULL_CALL_SPREAD | 6 | Gravestone doji (weak bearish) |
| **UMC** ⭐ | 4 | +62.09% | BULL_CALL_SPREAD | 9 | Stoch 16.1 + dragonfly doji |
| NBIS | 4 | +56.22% | BULL_CALL_SPREAD | 7 | Stoch 16.6 oversold |
| OUST | 4 | +46.92% | SKIP | 0 | No clear edge |
| TENB | 4 | +31.12% | SKIP | -2 | Strongly negative news (90%) |
| RBRK | 4 | +30.67% | SKIP | -2 | Weak relative strength |
| ESTC | 4 | +28.09% | SKIP | -2 | Weak relative strength |
| AAOI | 4 | +27.59% | BULL_CALL_SPREAD | 7 | Bid/ask narrowing, strong call sweep |
| INTC | 4 | +26.64% | LONG_STOCK | 3 | +459K net call delta (largest of screen) |
| **WDC** ⭐ | 4 | +25.38% | BULL_CALL_SPREAD | 9 | Stoch 21.8 oversold, but shooting star |
| CIFR | 4 | +21.78% | BULL_CALL_SPREAD | 7 | Stoch 14.8 oversold |
| **APLD** ⭐ | 4 | +21.21% | BULL_CALL_SPREAD | 8 | Stoch 9.4 + hammer candle |
| AIXXF | 4 | +20.84% | LONG_STOCK | 4 | OTC, no options |
| MU | 3 | +76.86% | BULL_CALL_SPREAD | 6 | Pre-earnings IV expansion (14 days out) |
| **SIMO** ⭐ | 3 | +69.05% | BULL_CALL_SPREAD | 10 | Stoch 16.7 + inverted hammer |
| SNOW | 3 | +68.28% | LONG_STOCK | 3 | RSI 66.2 overbought |
| OKTA | 3 | +50.81% | SKIP | -2 | No clear edge |
| GEO | 3 | +49.52% | BEAR_PUT_SPREAD | -8 | Deeply overbought — fade candidate |
| STM | 3 | +42.02% | BULL_CALL_SPREAD | 6 | +50K net call delta |
| IONQ | 3 | +31.45% | BULL_CALL_SPREAD | 6 | Stoch 16.5 oversold |
| QBTS | 3 | +28.38% | LONG_STOCK | 4 | Stoch 16.5 oversold |
| GTLB | 3 | +27.05% | BEAR_PUT_SPREAD | -6 | Bearish — fade candidate |
| NTNX | 2 | +20.24% | LONG_PUT | -3 | Bearish |
| **WOLF** ⭐ | 2 | +67.97% | BULL_CALL_SPREAD | **14** | Capitulation bottom: BB lower + Stoch 1.1 + OBV div + vol bottom |
| HUT | 2 | +46.55% | LONG_STOCK | 4 | Stoch 21.3 oversold |
| CRWD | 2 | +42.36% | BULL_CALL_SPREAD | 5 | Stoch 17.9 oversold |
| **MXL** ⭐ | 2 | +38.34% | BULL_CALL_SPREAD | 9 | BB lower + Stoch 10.8 + OBV div + inverted hammer |
| **POET** ⭐ | 2 | +36.67% | BULL_CALL_SPREAD | 8 | Stoch 14.8 oversold |
| RPD | 2 | +23.88% | BEAR_PUT_SPREAD | -6 | Weakest relative strength of screen — fade candidate |
| BESI.AS | 2 | +22.74% | LONG_STOCK | 3 | No US options (Amsterdam-listed) |
| AI | 2 | +20.40% | SKIP | 0 | Short squeeze potential HIGH but neutral net signal |

---

## Risk Notes & Caveats

- **MM "sell-on-rally" hedge bias** appears as a warning on most BULL_CALL_SPREAD names — dealers are short gamma above spot and must sell stock as price rises, creating mechanical resistance. This is *why* the tool favors spreads (capped upside) over outright long calls in this group. WOLF is a notable exception with a **buy-on-rally** bias (favorable).
- **IV is extremely elevated** across most of these names (150–500%+), reflecting the speculative AI/semiconductor narrative. QCOM (86%) and AMAT (73%) are relative outliers on the low side.
- **Liquidity** ranges widely — QCOM, AMAT, CRDO, UMC, POET, APLD, WDC have tradeable chains; SIMO, MXL, ORA, AKAM, WOLF are thin and should be worked with limit orders.
- **Earnings risk:** ORCL reports today (IV crush risk for any new options position); MU reports in 14 days (pre-earnings IV expansion may make calls relatively more expensive going in).
- All option prices/spreads are as of **2026-06-11 ~04:10 UTC** (after-hours snapshot for 2026-06-10 session) and will move with the next session's open.
- This is a screening/research output, not investment advice — verify pricing/liquidity live before placing any orders.
