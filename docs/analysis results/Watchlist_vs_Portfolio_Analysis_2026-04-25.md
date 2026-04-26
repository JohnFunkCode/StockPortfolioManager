# Watchlist vs Portfolio Replacement Analysis
**Date:** 2026-04-25 | **Horizon:** ~1 month | **Model:** Claude Sonnet 4.6

---

## 1. Executive Summary

Three portfolio positions show meaningful replacement opportunities where watchlist candidates score 20+ points higher on the multi-signal rubric:

| Priority | Action | Exit | Entry | Score Δ | Primary Rationale |
|----------|--------|------|-------|---------|-------------------|
| 🔴 High | SELL → BUY | GEV | AMZN | +46 (52→98) | GEV P/C=1.67 bearish + RSI 80.6; AMZN 43 institutional call sweeps, 18 HC, earnings catalyst |
| 🔴 High | SELL → BUY | WDC | APLD | +51 (32→83) | WDC fresh put buying dominant; APLD $7.5B hyperscaler lease + 30.4% short float squeeze |
| 🟡 Mod | SELL → BUY | CAT | SIMO | +22 (52→74) | CAT RSI 70.6 + Q1 earnings risk Apr 30; SIMO P/C=0.09 (lowest in 161-stock scan) |
| ⚪ Spec | ADD | — | SOUN | — | 37.6% short float + 6.2 DTC = HIGH squeeze; RSI 59 healthy entry, MACD bullish |

**Hold:** NVDA, MU, GLW all remain above available replacement scores. LITE MACD is bearish — monitor but no compelling swap identified.

---

## 2. Methodology

### Data Sources
| Phase | Tool | Purpose |
|-------|------|---------|
| 1 | `analyze_options_symbol` × 7 | Portfolio baseline: BB position, P/C, long/put score, IV, earnings proximity |
| 2 | `analyze_options_watchlist` | Bulk scan of 161 USD-listed watchlist symbols ranked by options score |
| 3 | Curation | Top-30 options score + sector proximity filter → 10 finalists |
| 4 | `get_news`, `get_unusual_calls`, `get_short_interest`, `get_rsi`, `get_macd` | Deep dive per finalist, parallel batches |

### Scoring Rubric (0–100 base + up to +15 bonuses)

| Signal | Source | Max | Criteria |
|--------|--------|-----|---------|
| News sentiment | `get_news` (FinBERT) | 20 | 8–10 pos=20; 5–7=14; 3–4=8; 0–2=2 |
| Unusual call sweep | `get_unusual_calls` | 25 | Strong+≥10 HC=25; Strong=18; Moderate=10; Weak=3; None=0 |
| P/C ratio | `analyze_options_symbol` | 15 | <0.7=15; 0.7–0.9=11; 0.9–1.1=7; >1.1=3 |
| Options spread/IV | bid-ask spread proxy via IV | 15 | <20%=15; 20–35%=10; 35–50%=6; >50%=2 |
| Gamma wall/BB position | BB position proxy | 15 | Below lower BB=15; Mid-band=8; Above upper BB=3 |
| Short interest | `get_short_interest` | 10 | <2%=5; 2–10%=7; >10%+≥3 DTC=10 |
| **Penalty** | options flow | −5 | MM sell_on_rally OR fresh put buying dominant |
| **Bonus: RSI** | `get_rsi` | +5 | RSI 40–60 (momentum room before overbought) |
| **Bonus: MACD** | `get_macd` | +5 | Bullish crossover confirmed |
| **Bonus: 30d return** | live portfolio report | +5 | Top quartile vs. watchlist peers |

> Note: Options spread and gamma wall use BB position and IV as proxies where direct `get_bid_ask_spread`/`get_delta_adjusted_oi` data was not collected. Treat P/C as directional for portfolio stocks where exact figures were not confirmed by `analyze_options_symbol` — confirmed values are indicated.

---

## 3. Portfolio Baseline

| Symbol | Cost Basis | Current | 30d Ret | 90d Ret | RSI | MACD | Options Signal | **Score** |
|--------|-----------|---------|---------|---------|-----|------|---------------|-----------|
| NVDA | $5.41 | $208.27 | +13% | +15% | 71.7 ⚠️ | Bullish | Strong AI narrative; RSI extended | **69** |
| CAT | $590.30 | $830.79 | +20% | +33% | 70.6 ⚠️ | Bullish | Q1 earnings Apr 30; RSI overbought | **52** |
| MU | $415.46 | $496.72 | +21% | +94% | 68.9 | Bullish | HBM sold out 2026; P/C bullish | **71** |
| LITE | $695.76 | $881.64 | +37% | +143% | 58.2 ✅ | **Bearish** ⚠️ | RSI healthy; MACD turned bearish | **62** |
| GEV | $897.11 | $1,149.19 | +36% | +65% | 80.6 🔴 | Bullish | **P/C=1.67** 🔴; put OI>call OI; MM sell_on_rally | **52** |
| GLW | $139.12 | $175.89 | +35% | +84% | 68.4 | Bullish ✅ | MACD bullish crossover; solid all-around | **70** |
| WDC | $294.37 | $404.00 | +52% | +119% | 77.1 🔴 | Bullish | **Fresh put buying** 🔴 (vol P/C 4.71); RSI 77 | **32** |

**Key observations:**
- **WDC (32)** and **GEV (52)** show bearish options flow despite strong historical returns — both have fresh institutional hedging signals
- **CAT (52)** faces Q1 earnings binary risk April 30 with RSI at 70.6 — elevated event risk heading into report
- **MU (71)** and **GLW (70)** are the strongest-signaling current holdings — no watchlist candidate clearly beats either
- **LITE (62)**: RSI is in a healthy range (58.2) but MACD turned bearish — watch for continuation; no compelling swap found

---

## 4. Watchlist Finalists by Sector

### A. GEV / WDC / CAT Alternatives (Infrastructure, Storage, Industrial)

---

#### AMZN — Amazon.com | $263.99 | *Replaces GEV*

| Signal | Data | Pts |
|--------|------|-----|
| News sentiment | 8/10 positive: AWS cloud growth, $185B AI capex, earnings catalyst | 20 |
| Unusual calls | **43 sweeps, 18 high-conviction**, 8 fills at/above ask | 25 |
| P/C ratio | <0.7 (massive call dominance over 43 sweeps) | 15 |
| Options spread | ~27% IV near-term ATM (highly liquid) | 15 |
| BB position | RSI 80.57 — above mid-band | 8 |
| Short interest | 0.95% float, 2.0 DTC — LOW | 5 |
| RSI bonus | 80.57 overbought — no bonus | 0 |
| MACD bonus | Bullish crossover ✅ | +5 |
| 30d return bonus | Strong momentum | +5 |
| **Total** | | **98** |

**Sweep detail:** Institutional buyers piled into $265–$300 strikes (1–14% OTM) with vol/OI ratios of 2–41×. The $265 strike alone traded 32,116 contracts vs. 4,564 OI (7× fresh positioning). Earnings expected ~April 29. Smart money is betting on a beat.

---

#### APLD — Applied Digital | $34.98 | *Replaces WDC or GEV*

| Signal | Data | Pts |
|--------|------|-----|
| News sentiment | **9/10 positive**: $7.5B hyperscaler AI data center lease signed Apr 23, +12% on day | 20 |
| Unusual calls | 30 sweeps, 8 high-conviction, 4 at/above ask | 18 |
| P/C ratio | <0.7 (call sweep dominant) | 15 |
| Options spread | IV 110–118% (small-cap, volatile) | 2 |
| BB position | RSI 63.92 — mid-band | 8 |
| Short interest | **30.4% float, 3.77 DTC — MEDIUM SQUEEZE** | 10 |
| RSI bonus | 63.92 — just above 60, no bonus | 0 |
| MACD bonus | Bullish crossover ✅ | +5 |
| 30d return bonus | Top quartile | +5 |
| **Total** | | **83** |

**Key signal:** $7.5B lease pushes total contracted revenue to >$23B. With 30.4% of float short and 3.77 DTC, a continued positive news flow creates squeeze pressure. 30 call sweeps confirm institutional positioning on the long side into this event.

---

#### SIMO — Silicon Motion Technology | $153.46 | *Replaces WDC / MU*

| Signal | Data | Pts |
|--------|------|-----|
| News sentiment | 8/10 positive: AI-driven SSD demand surge, IBD fresh buy zone designation | 20 |
| Unusual calls | 4 sweeps — vol/OI **35.6×** on $160 call (4.3% OTM), sweep_score=6 | 10 |
| P/C ratio | **0.09** — most extreme bullish signal in entire 161-stock watchlist scan | 15 |
| Options spread | IV 81–85% | 6 |
| BB position | RSI 75.7 — above mid-band | 8 |
| Short interest | 0.27% float — LOW | 5 |
| RSI bonus | 75.7 overbought — no bonus | 0 |
| MACD bonus | Bullish crossover ✅ | +5 |
| 30d return bonus | Top quartile | +5 |
| **Total** | | **74** |

**Key signal:** P/C=0.09 means for every put option, 11 calls were bought — unprecedented in this analysis. The $160 call had 35.6× more volume than open interest existed. Q1 earnings upcoming = near-term catalyst. AI SSD demand accelerating.

---

### B. Speculative / AI Theme Candidates

---

#### SOUN — SoundHound AI | $8.19 | *New speculative position*

| Signal | Data | Pts |
|--------|------|-----|
| News sentiment | 4/10 positive: LivePerson acquisition ($43M), Wedbush buy rating maintained | 8 |
| Unusual calls | 13 sweeps, 3 high-conviction, 4 at/above ask | 18 |
| P/C ratio | <0.7 (call sweep dominant) | 15 |
| Options spread | IV 97–141% (high risk, small-cap) | 2 |
| BB position | RSI 59.11 — mid-band | 8 |
| Short interest | **37.6% float, 6.21 DTC — HIGH SQUEEZE** | 10 |
| RSI bonus | **59.11 — in 40–60 range** ✅ | +5 |
| MACD bonus | Bullish crossover ✅ | +5 |
| 30d return bonus | Moderate — no bonus | 0 |
| **Total** | | **71** |

**Key signal:** 37.6% of float is short with 6.2 DTC — highest short interest in this entire analysis. RSI at 59 means momentum room exists before hitting overbought. A single catalyst (beat on any revenue announcement) could trigger a covering cascade. Use stock only or very near-dated calls; size to <2% of portfolio.

---

### C. NVDA / MU Alternatives (Semiconductors, AI Chips)

---

#### ARM — Arm Holdings | $234.81 | *Replaces NVDA*
**Score: 57** | RSI 87.33 🔴 | MACD bullish | Moderate call sweep (18, 0 aggressive fills) | bb_pos=**1.243** (11% above upper BB) | Short: 11.46% float, 1.9 DTC

**Assessment:** Extraordinary momentum (+40% in 7 days, all-time high, first proprietary AGI CPU announced) but trading 11% above upper Bollinger Band with RSI 87.33. Momentum is exceptional; mean-reversion risk is very high at this level. Not a recommended entry here — better to wait for RSI to cool to 60–70 range.

---

#### NET — Cloudflare | $207.07 | *Replaces NVDA (AI networking)*
**Score: 64** | RSI 53.77 ✅ | MACD bullish crossover | Moderate sweep (4 calls) | P/C 0.7–0.9 | Short: 3.0% float

**Assessment:** Healthy entry zone (RSI 53.77), MACD bullish, options showing moderate interest. Downside concern: $29M insider selling recently reported. Score of 64 doesn't clear the bar to displace NVDA (69). Hold as watchlist item.

---

### D. CAT / Industrial Alternatives

---

#### LHX — L3Harris Technologies | $317.51 | *Replaces CAT*
**Score: 59** | RSI **26.64** 🔵 | MACD bearish | **Zero call sweeps** | Below lower BB | Short: 1.56%

**Assessment:** Classic oversold bounce setup — RSI 26.64, below lower Bollinger Band, `analyze_options_symbol` long_score=11 (second-highest of all candidates). However, zero institutional call sweep activity means smart money is not confirming the dip buy (yet). Defense sector headwinds from ongoing geopolitical uncertainty. Citi says selloff "gone too far" — contrarian thesis, but wait for MACD to turn bullish before entering.

#### CWEN — Clearway Energy | $39.58 | *Replaces GEV*
**Score: 57** | RSI 52.06 ✅ | MACD bearish | No call sweeps | P/C neutral | Short: 9.82%, 7.92 DTC MEDIUM

**Assessment:** Strong news (8/10 positive — 2GW new AI-linked power PPAs, PT raised) and healthy RSI, but MACD bearish and zero call sweep activity limits near-term conviction. Better as a 3-month thesis than 1-month.

#### EMR — Emerson Electric | $141.35 | Score: 49
Weak sweep (2 deep ITM calls = hedges). 4/10 news with insider selling warning. RSI 52.38, MACD bullish. Defensive industrial with no near-term catalyst. Pass.

#### GD — General Dynamics | $313.21 | Score: 45
RSI 25.57 (deeply oversold), but MACD bearish, zero call sweeps, 5/10 negative news. Defense sector selloff. Value thesis requires patience — not a 1-month trade.

---

## 5. Master Comparison Table

| Rank | Symbol | Type | Price | RSI | MACD | Call Sweep | P/C | Short Float | **Score** |
|------|--------|------|-------|-----|------|-----------|-----|-------------|-----------|
| 1 | **AMZN** | Candidate | $263.99 | 80.6 ⚠️ | Bullish | Strong (43, 18 HC) | <0.7 | 0.95% LOW | **98** |
| 2 | **APLD** | Candidate | $34.98 | 63.9 | Bullish | Strong (30, 8 HC) | <0.7 | 30.4% SQZ | **83** |
| 3 | **SIMO** | Candidate | $153.46 | 75.7 ⚠️ | Bullish | Moderate (4, 1 HC) | **0.09** | 0.27% | **74** |
| 4 | MU | Portfolio | $496.72 | 68.9 | Bullish | Moderate | <0.7 | ~3% | **71** |
| 4 | **SOUN** | Candidate | $8.19 | 59.1 ✅ | Bullish | Strong (13, 3 HC) | <0.7 | 37.6% SQZ | **71** |
| 6 | GLW | Portfolio | $175.89 | 68.4 | Bullish ✅ | Moderate | 0.7–0.9 | ~3% | **70** |
| 7 | NVDA | Portfolio | $208.27 | 71.7 ⚠️ | Bullish | Moderate | 0.7–0.9 | <1% | **69** |
| 8 | NET | Candidate | $207.07 | 53.8 ✅ | Bullish | Moderate (4) | 0.7–0.9 | 3.0% | **64** |
| 9 | LITE | Portfolio | $881.64 | 58.2 ✅ | **Bearish** ⚠️ | Moderate | 0.9–1.1 | ~3% | **62** |
| 10 | LHX | Candidate | $317.51 | 26.6 🔵 | Bearish | **None** | <0.7 | 1.56% | **59** |
| 11 | CWEN | Candidate | $39.58 | 52.1 ✅ | Bearish | **None** | 0.9–1.1 | 9.8% | **57** |
| 11 | ARM | Candidate | $234.81 | 87.3 🔴 | Bullish | Moderate (18) | 0.7–0.9 | 11.5% | **57** |
| 13 | CAT | Portfolio | $830.79 | 70.6 ⚠️ | Bullish | Weak | 0.9–1.1 | <1% | **52** |
| 13 | GEV | Portfolio | $1,149.19 | 80.6 🔴 | Bullish | Moderate | **1.67** 🔴 | <1% | **52** |
| 15 | EMR | Candidate | $141.35 | 52.4 ✅ | Bullish | Weak | 0.9–1.1 | 2.2% | **49** |
| 16 | GD | Candidate | $313.21 | 25.6 🔵 | Bearish | **None** | ~0.9 | 1.1% | **45** |
| 17 | WDC | Portfolio | $404.00 | 77.1 🔴 | Bullish | **Bearish flow** 🔴 | **>1.1** 🔴 | ~5% | **32** |

**Legend:** ✅ favorable | ⚠️ elevated/watch | 🔴 bearish signal | 🔵 oversold (bounce potential) | HC = high-conviction sweeps | SQZ = squeeze potential

---

## 6. Recommended Swaps

### Swap 1 (High Conviction): SELL GEV → BUY AMZN
**Score delta: +46 | GEV 52 → AMZN 98**

**Why exit GEV now:**
- P/C ratio = **1.67** (confirmed): put open interest 5,250 >> call OI 3,147 — institutions are actively hedging
- RSI 80.6 + at/above upper Bollinger Band = momentum exhaustion signal post Q1 earnings surge
- Market maker hedge bias: **sell_on_rally** — mechanical headwind on any further up moves
- The +14.6% post-earnings week is priced in; new buyers are absorbing what existing holders are hedging

**Why enter AMZN:**
- **43 unusual call sweeps, 18 high-conviction** — the largest single-day institutional positioning event in this analysis
- 8 sweeps at/above ask (urgency): $265/$270/$272/$280/$285/$290/$300 strikes, all OTM
- $265 strike: 32,116 contracts traded vs. 4,564 OI (7.04× = fresh positioning, not rolling)
- Earnings catalyst expected ~April 29 — sweep timing confirms smart money is ahead of the print
- MACD bullish crossover; options spread tight (liquid mega-cap, ~27% ATM IV)

**Execution:** Stock entry at $264. Or $265/$275 call spread (May 1 expiry, ~$3 debit).
**Stop:** $252 (4.5% below current, below the $255 options cluster).

---

### Swap 2 (High Conviction): SELL WDC → BUY APLD
**Score delta: +51 | WDC 32 → APLD 83**

**Why exit WDC now:**
- Fresh put buying signal: vol P/C **4.71 ≥ 1.5×** OI P/C 1.22 — this is the definition of institutional put accumulation
- RSI 77.1 — extended; the 90d return of +119% and 30d of +52% mean most of the upside is captured
- Smart money is buying puts against WDC, not adding calls

**Why enter APLD:**
- **$7.5B hyperscaler AI data center lease** (April 23): total contracted revenue now >$23B. Stock +12% on announcement — this is not a rumor.
- **30.4% of float is short + 3.77 DTC** = significant squeeze fuel. Any continued positive catalyst triggers a covering cascade.
- 30 call sweeps across May expirations (8 high-conviction); $37–$39 strikes (5–12% OTM) with vol/OI 1–4.1×
- MACD bullish crossover; RSI 63.9 (room to run)
- Two independent signals: (1) fundamental lease catalyst, (2) institutional call positioning + short squeeze setup

**Execution:** Stock preferred (volatile small-cap). Options: May $37 calls (~$1.35) — IV is elevated at 118% so consider spread to reduce premium.
**Stop:** $30.50 (below the May $32.50 call cluster).

---

### Swap 3 (Moderate Conviction): SELL CAT → BUY SIMO
**Score delta: +22 | CAT 52 → SIMO 74**

**Why exit CAT before April 30:**
- Q1 earnings April 30 — RSI 70.6 heading into a binary event with tariff/supply-chain exposure
- Even a neutral earnings print could see profit-taking given the run from $590 → $831
- Pre-analysis RSI was 85.51 (now partially cooled but still extended); unusual call sweep is weak

**Why enter SIMO:**
- **P/C ratio = 0.09** — for every put, 11 calls were purchased. This is the most extreme bullish options positioning in the 161-stock scan.
- $160 call sweep: vol/OI = **35.6×** (3,948 contracts traded vs. 111 OI) — someone bought a fresh, large directional position
- 8/10 positive news: AI SSD demand surge confirmed, IBD fresh buy zone — Q1 earnings upcoming as a near-term catalyst
- MACD bullish crossover; storage semiconductor cycle recovering alongside AI capex
- Two independent signals: (1) extreme P/C ratio (flow-based), (2) positive news/earnings catalyst

**Execution:** Stock or May $155 call ($11.43 at the money, capture earnings move).
**Stop:** $140 (below the $150 call cluster that has existing 3,935 OI — that level has options gravity).

---

### Optional: ADD SOUN (Speculative, <2% of Portfolio)
**Score 71 | RSI 59 ✅ | 37.6% short float + 6.21 DTC = HIGH squeeze**

Do not swap a core holding for SOUN. This is a defined-risk speculative addition.

**Thesis:** 37.6% short float with 6.2 DTC is the highest in this analysis. RSI 59 gives momentum room. 3 high-conviction call sweeps + 4 at-ask fills confirm institutional longs are positioning ahead of a potential covering event. LivePerson acquisition ($43M) + Wedbush buy are in-queue catalysts.

**Execution:** Stock only (IV 97–141% makes options premium extremely expensive for the hold time). Size to ≤2% of portfolio. Stop: $7.00 (below the $7.50 call cluster).

---

## 7. Risk Considerations

### AMZN
- **Earnings miss (~Apr 29):** A revenue or AWS margin miss gaps through all OTM call positions immediately. The sweep could be front-running a beat that doesn't arrive.
- **Macro tariff risk:** AMZN retail segment is exposed to consumer goods tariffs; enterprise AWS spending could soften if Q1 guidance is cautious.
- **Extended RSI (80.6):** You are buying after the move, not ahead of it. Earnings must validate the run.

### APLD
- **Small-cap volatility:** $5B market cap with 30% short float means moves of ±15% in a single session are possible. A secondary equity offering would crush the squeeze thesis.
- **High IV options:** 110–118% IV means option premium decays aggressively. Stock is safer than options for this trade.
- **Contract concentration risk:** The $7.5B hyperscaler lease is from an unnamed customer. Any sign of renegotiation, delay, or cancellation = severe selloff.
- **Short squeeze is a double-edged sword:** The same well-funded bear camp watching for cracks is also watching for a relief pop to add short.

### SIMO
- **Earnings risk:** P/C=0.09 means the market is priced for a strong beat. A miss from this positioning = violent put buying and rapid unwind.
- **Liquidity:** Average daily volume ~600K shares. The 35.6× vol/OI on the $160 strike may reflect a single large player — if they exit, price follows.
- **Memory cycle correlation:** SIMO competes in SSD/NAND flash — same macro headwinds as WDC. If consumer storage demand softens (especially enterprise SSD), the thesis weakens alongside the sector.

### SOUN (Speculative)
- **Squeeze timing is uncertain:** 37.6% short float doesn't guarantee an immediate squeeze. Shorts may be patient, adding on rallies.
- **Pre-profitability fundamentals:** Any broader risk-off move hits speculative small-cap AI names disproportionately.
- **IV decay is punishing:** At 97–141% IV, holding calls through a quiet period loses value rapidly. Use stock.

---

*Data sourced from live MCP tool calls on 2026-04-25: `get_rsi`, `get_macd`, `get_unusual_calls`, `get_short_interest` (all 17 stocks); `analyze_options_symbol` (7 portfolio stocks + selected finalists); `analyze_options_watchlist` (161-symbol bulk scan); `get_news` (FinBERT, 10 articles per stock). Portfolio 30d/90d returns from `johnfunk.com/portfolio/portfolio_report.html`. P/C ratios for portfolio stocks are directional estimates based on options flow signals where `analyze_options_symbol` was not individually confirmed; GEV (P/C=1.67) and WDC (vol P/C 4.71) are confirmed values. Eliminated candidates: SMCI (Oracle $1.4B contract cancelled + co-founder indictment), CIEN (long_score=4, fresh put buying).*
