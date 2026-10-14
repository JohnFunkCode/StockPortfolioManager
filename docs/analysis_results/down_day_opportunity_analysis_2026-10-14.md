# Down Day Opportunity Analysis

**Analysis date:** October 14, 2026  
**Universe:** 257 watchlist symbols  
**Purpose:** Preserve the research funnel, point-in-time metrics, and backtest plan for review one, two, and four weeks after the analysis.

## Purpose and conclusion

This analysis searched for potential buying opportunities during a significant market down day. The objective was not simply to buy the largest decliners. It was to identify companies where market-wide or stock-specific selling might have created a favorable risk-reward setup while testing whether the decline represented temporary pressure or genuine business or trend deterioration.

The strongest combined candidates at the end of the session were **BKR, MRK, GLW, CLS, CAT, TSM, QCOM, LRCX, FLS, and GE**. Higher-volatility rebound candidates were **VRT, WDC, COHR, DOCN, AGX, DELL, STX, and 000660.KS**. These were research candidates, not executed trades or guarantees.

Prices, options measures, and recommendation outputs are time-sensitive. The options watchlist capture was generally as of October 13, 2026, while several live trade-recommendation calls were run on October 14, 2026. Earnings-calendar results frequently returned `UNKNOWN` and should be verified before any future trade.

## Why search for opportunities on a down day

A broad selloff can create temporary dislocations through forced selling, volatility spikes, ETF redemptions, stop-loss cascades, and indiscriminate de-risking. Strong companies may trade below levels that longer-term buyers defend. The same decline can also expose a broken thesis, so the process was designed to distinguish temporary pressure from structural weakness.

- A market-wide decline can create better entry prices in companies whose relative strength and business quality remain intact.
- Oversold indicators can identify exhaustion, but they are not sufficient evidence of a bottom.
- Options positioning can show where dealers, put writers, and call buyers may provide mechanical support or resistance.
- ATR and drawdown data can prevent an apparently attractive entry from being sized too aggressively.
- The desired output is a repeatable, documented candidate list rather than a one-day prediction.

## Universe and initial screening

The shared watchlist contained 257 securities across technology, semiconductors, industrials, energy, healthcare, software, infrastructure, and other sectors. The fundamentals service scored the full watchlist; 110 names had composite fundamentals scores of at least 6 with complete or adequate metric coverage.

| Initial screen | Result | Interpretation |
|---|---:|---|
| Watchlist size | 257 symbols | Full shared watchlist |
| Fundamental score universe | 110 names | Composite score ≥6 |
| Options watchlist scan | 257 scanned; 227 fetched | Long and put candidates ranked from options capture |
| Earnings calendar | Frequently `UNKNOWN` | Earnings risk was not cleared automatically |

## Techniques considered

### Fundamental quality and business momentum

Composite fundamental scores, revenue growth trajectory, EPS acceleration, operating margins, free-cash-flow margins, valuation metrics, and momentum were used to avoid treating every oversold stock as equally investable.

### Bollinger and oversold mean reversion

Price relative to the 20-day Bollinger range identified stocks near or below the lower band. RSI and Stochastic readings helped identify more extreme selling. A stock below the lower band could be capitulation or a persistent breakdown.

### Relative strength and sector rotation

Relative strength versus SPY, QQQ, and the sector ETF distinguished leaders pulled down with the market from laggards falling for company-specific reasons. Recent relative-strength history was important because some 12-month leaders had begun underperforming over the most recent month.

### Volume, OBV, and capitulation

Volume analysis, OBV divergence, candlestick behavior, and proxy dark-pool analysis were used to look for seller exhaustion or hidden accumulation. A high-volume down day closing away from the low was treated differently from a stock closing at the low on expanding volume.

### VWAP, anchored VWAP, and support confluence

Rolling VWAP and anchored VWAPs from earnings, gaps, and swing points identified institutional cost bases. Support confluence combined gamma walls, GEX, put-OI builds, volume-profile nodes, anchored VWAPs, moving averages, Bollinger levels, ATR bands, prior highs and lows, and Fibonacci levels into support zones.

### Options positioning

The analysis used GEX regime, put-support strikes, zero-gamma levels, delta-adjusted open interest, OI changes, put/call ratios, unusual call activity, gamma walls, and dealer hedge bias. Options signals were treated as confirmation rather than proof of fundamental support.

### Risk calibration

ATR bands, chandelier stops, historical one-day and five-day drawdowns, recent volatility, and earnings proximity were used to determine whether a candidate could be sized responsibly. Attractive theoretical risk-reward was discounted when the technical stop was already above price, volatility was extreme, or the target lacked nearby resistance.

### Trade synthesis

The trade-recommendation service combined technical, options, sentiment, and fundamental signals into directional recommendations with indicative entries, targets, stops, position sizes, and risk-reward ratios. Those model outputs should be preserved as the original projection rather than updated retroactively.

## Candidate results

### Primary combined shortlist

| Symbol | Why it surfaced | Main risk or qualification |
|---|---|---|
| BKR | Score 6; positive MACD; sector leading; outperforming SPY; bullish calls; support zone near 54.43–55.00; high-confidence BUY | Sell-on-rally dealer bias; high IV |
| MRK | Score 7; hammer; put unwinding; relative-strength leader; strongest support confluence, 9 methods around 143.96–146.19 | R/R 1.58; sell-on-rally dealer bias |
| GLW | Score 8; oversold; bullish OBV; positive news; DAOI support; 12-month relative-strength leadership | Recent 21-day relative strength weakened; ATR about 6% |
| CLS | Score 8; oversold; bullish OBV; accelerating revenue; support near high-volume node | No VWAP reclaim at scan time |
| CAT | Score 6; RSI near 33; bullish OBV; DAOI support; positive news; ATR near 3.6% | Model R/R distorted by an unusually tight stop |
| TSM | Score 10; strong compounder; relative-strength outperformance; accelerating revenue; options support | High IV; earnings date unavailable |
| QCOM | Score 8; hammer; capitulation and OBV divergence; oversold; supportive DAOI | Wide historical drawdown; weaker recent relative strength |
| LRCX | Score 8; strong EPS acceleration; outperforming; oversold; call activity and DAOI support | High IV; multi-day drawdown risk |
| FLS | Score 6; oversold; bullish OBV; hammer; relative-strength leader; high-confidence BUY | EPS deceleration; sell-on-rally dealer bias |
| GE | Score 8; bullish MACD; strong compounder; bullish calls and DAOI; support around 305–307 | Recent relative-strength laggard |

### Higher-volatility rebound candidates

| Symbol | Evidence supporting a rebound | Risk profile |
|---|---|---|
| VRT | Score 9; below lower band; RSI near 33; strong long-term relative strength; call activity and DAOI support | Historical average drawdown about 21.6%; technical stop already breached |
| WDC | Score 8; deeply oversold; bullish reversal candle; long-term relative-strength leader | Negative news; EPS deceleration; ATR about 6.9%; drawdown about 17.3% |
| COHR | Score 7; bullish OBV; moderate VWAP reclaim; long-term leader; options support | ATR about 8.5%; EPS deceleration; recent negative news |
| DOCN | Score 7; large one-day decline; accelerating revenue; short float about 11.5%; VWAP reclaim attempt | ATR about 8.6%; high IV; earnings momentum deceleration |
| AGX | Score 9; bullish MACD; RSI near 15; accelerating revenue; strong fundamentals | Very high IV; recent relative-strength deterioration |
| DELL | Large one-day drop; 12-month relative-strength leader; unusual calls and DAOI support | Negative news and revenue deceleration |
| STX | Score 8; strong relative strength; accelerating revenue; oversold; call and DAOI support | ATR about 7%; high drawdown; negative news |
| 000660.KS | Score 9; deeply oversold; very strong long-term relative strength; VWAP reclaim attempt | Revenue deceleration; no useful options data; technical stop fallback |

### Additional names surfaced

The broader options and quality screens also surfaced:

`GFS, RMBS, MSI, AAPL, GOOG, IBM, ABBV, ADBE, CSCO, GD, JCI, META, OKTA, PSTL, RNG, TEAM, TENB, ZETA, AMD, AMZN, ASX, BALL, CRVL, DVA, EOG, FTNT, GEO, GWW, ESE, MITK, ROK, POWL, SNDK, NVDA, SMTC, GDS, ACMR, CAMT, 2208.HK, NFLX, KN, CACI, ASML`

High-risk options activity names were `AEHR, QBTS, RGTI, RKLB, SAIL, SOUN, AMPX, APLD, CIFR, HIMX, JOBY, OKLO, POET`. These were identified mainly through extreme implied volatility, put unwinding, call volume, or lower-Bollinger signals and should be treated as speculative trading candidates.

Names flagged for caution were `ORCL, GDS, 2208.HK, ACMR, CAMT, MSI, and RMBS` because of conflicting signals such as weak recent relative strength, negative news, weak dealer positioning, unusually large historical drawdowns, or low-confidence recommendations.

## Point-in-time price and indicator snapshot

Captured from the remote MCP stock-price tool on October 14, 2026. Bollinger values are 20-day, 2-standard-deviation bands. P/C is the options put/call ratio returned with the quote. Values are in the quoted currency. `n/a` means the remote tool returned an error or no usable observation.

| Symbol | Price | Currency | BB lower | BB mid | BB upper | P/C | Expiry |
|---|---:|:---:|---:|---:|---:|---:|:---:|
| BKR | 55.40 | USD | 54.83 | 56.63 | 58.43 | 0.25 | 2026-10-16 |
| MRK | 147.52 | USD | 139.72 | 145.89 | 152.06 | 0.58 | 2026-10-16 |
| GLW | 140.15 | USD | 141.87 | 155.39 | 168.90 | 1.15 | 2026-10-16 |
| CLS | 322.07 | USD | 323.82 | 359.85 | 395.88 | 0.51 | 2026-10-16 |
| CAT | 747.18 | USD | 761.39 | 812.07 | 862.76 | 1.05 | 2026-10-16 |
| TSM | 428.65 | USD | 423.26 | 453.91 | 484.55 | 1.37 | 2026-10-16 |
| QCOM | 171.98 | USD | 166.81 | 184.44 | 202.06 | 0.60 | 2026-10-16 |
| LRCX | 284.95 | USD | 274.37 | 314.89 | 355.40 | 1.16 | 2026-10-16 |
| FLS | 69.58 | USD | 69.19 | 73.21 | 77.24 | 0.12 | 2026-10-16 |
| GE | 306.18 | USD | 300.48 | 312.64 | 324.81 | 0.25 | 2026-10-16 |
| VRT | 220.82 | USD | 230.25 | 245.59 | 260.93 | 1.59 | 2026-10-16 |
| WDC | 363.21 | USD | 364.10 | 428.87 | 493.65 | 0.76 | 2026-10-16 |
| COHR | 262.24 | USD | 263.97 | 305.64 | 347.31 | 1.18 | 2026-10-16 |
| DOCN | 109.87 | USD | 117.67 | 134.31 | 150.94 | 0.52 | 2026-10-16 |
| AGX | 368.73 | USD | 352.13 | 382.33 | 412.54 | 0.63 | 2026-10-16 |
| DELL | 504.77 | USD | 516.69 | 558.73 | 600.76 | 1.13 | 2026-10-16 |
| STX | 714.74 | USD | 707.93 | 851.37 | 994.80 | 0.90 | 2026-10-16 |
| 000660.KS | 1,419,000.00 | KRW | 1,486,256.11 | 1,737,850.00 | 1,989,443.89 | n/a | n/a |
| GFS | 48.55 | USD | 46.01 | 48.05 | 50.09 | 0.42 | 2026-10-16 |
| RMBS | 92.24 | USD | 87.72 | 103.17 | 118.62 | 0.41 | 2026-10-16 |
| MSI | 450.74 | USD | 441.10 | 452.28 | 463.46 | 0.57 | 2026-10-16 |
| AAPL | 343.06 | USD | 329.25 | 336.81 | 344.37 | 0.60 | 2026-10-14 |
| GOOG | 343.65 | USD | 333.84 | 343.23 | 352.62 | 0.67 | 2026-10-16 |
| IBM | 227.74 | USD | 216.40 | 226.37 | 236.34 | 0.66 | 2026-10-16 |
| ABBV | 283.56 | USD | 254.88 | 267.89 | 280.91 | 0.99 | 2026-10-16 |
| ADBE | 238.74 | USD | 228.72 | 239.69 | 250.65 | 1.69 | 2026-10-16 |
| CSCO | 113.50 | USD | 102.27 | 111.04 | 119.80 | 0.96 | 2026-10-16 |
| GD | 334.83 | USD | 318.79 | 335.56 | 352.34 | 0.80 | 2026-10-16 |
| JCI | 151.64 | USD | 140.19 | 150.90 | 161.62 | 0.49 | 2026-10-16 |
| META | 737.13 | USD | 681.13 | 730.80 | 780.48 | 0.92 | 2026-10-14 |
| OKTA | 213.31 | USD | 182.34 | 209.10 | 235.86 | 0.71 | 2026-10-16 |
| PSTL | 22.15 | USD | 21.85 | 23.04 | 24.23 | 0.06 | 2026-10-16 |
| RNG | 74.36 | USD | 72.75 | 77.12 | 81.50 | 1.59 | 2026-10-16 |
| TEAM | 203.45 | USD | 174.94 | 193.60 | 212.25 | 0.79 | 2026-10-16 |
| TENB | 38.27 | USD | 32.69 | 37.29 | 41.90 | 0.65 | 2026-10-16 |
| ZETA | 32.42 | USD | 28.14 | 31.53 | 34.92 | 0.52 | 2026-10-16 |
| AMD | 569.84 | USD | 557.38 | 611.05 | 664.71 | 1.00 | 2026-10-14 |
| AMZN | 253.41 | USD | 243.72 | 253.20 | 262.68 | 0.44 | 2026-10-14 |
| ASX | 44.43 | USD | 40.98 | 44.97 | 48.96 | 1.24 | 2026-10-16 |
| BALL | 59.98 | USD | 55.14 | 58.16 | 61.17 | 0.20 | 2026-10-16 |
| CRVL | 80.43 | USD | 69.95 | 75.00 | 80.04 | 2.00 | 2026-10-16 |
| DVA | 181.49 | USD | 173.37 | 179.50 | 185.63 | 0.23 | 2026-10-16 |
| EOG | 154.21 | USD | 135.00 | 144.18 | 153.35 | 0.65 | 2026-10-16 |
| FTNT | 184.46 | USD | 166.42 | 181.51 | 196.60 | 0.88 | 2026-10-16 |
| GEO | 31.08 | USD | 29.53 | 31.07 | 32.61 | 0.05 | 2026-10-16 |
| GWW | 1,322.56 | USD | 1,230.99 | 1,275.98 | 1,320.98 | 0.61 | 2026-10-16 |
| ESE | 257.20 | USD | 253.23 | 264.10 | 274.97 | 2.14 | 2026-10-16 |
| MITK | 15.67 | USD | 16.13 | 17.26 | 18.39 | 0.23 | 2026-10-16 |
| ROK | 422.38 | USD | 410.96 | 433.79 | 456.61 | 2.01 | 2026-10-16 |
| POWL | 170.02 | USD | 173.78 | 189.02 | 204.27 | 0.58 | 2026-10-16 |
| SNDK | 1,412.12 | USD | 1,441.88 | 1,686.41 | 1,930.94 | 0.95 | 2026-10-16 |
| NVDA | 220.85 | USD | 217.73 | 228.67 | 239.61 | 0.92 | 2026-10-14 |
| SMTC | 160.88 | USD | 161.28 | 180.81 | 200.35 | 0.44 | 2026-10-16 |
| GDS | 25.75 | USD | 26.95 | 31.10 | 35.24 | 0.36 | 2026-10-16 |
| ACMR | 61.41 | USD | 62.08 | 73.96 | 85.84 | 0.50 | 2026-10-16 |
| CAMT | 135.39 | USD | 136.58 | 150.06 | 163.53 | 0.66 | 2026-10-16 |
| 2208.HK | 7.55 | HKD | 7.87 | 8.64 | 9.40 | n/a | n/a |
| NFLX | 70.41 | USD | 66.56 | 70.51 | 74.45 | 0.45 | 2026-10-16 |
| KN | 34.88 | USD | 34.58 | 37.21 | 39.85 | 0.09 | 2026-10-16 |
| CACI | 606.17 | USD | 589.45 | 614.07 | 638.70 | 0.49 | 2026-10-16 |
| ASML | 1,676.37 | USD | 1,636.28 | 1,763.33 | 1,890.37 | 1.09 | 2026-10-16 |
| AEHR | 70.65 | USD | 73.86 | 93.63 | 113.40 | 0.70 | 2026-10-16 |
| QBTS | 12.14 | USD | 12.79 | 15.95 | 19.10 | 0.54 | 2026-10-16 |
| RGTI | 11.72 | USD | 12.44 | 15.15 | 17.86 | 0.69 | 2026-10-16 |
| RKLB | n/a | USD | n/a | n/a | n/a | n/a | n/a |
| SAIL | 18.99 | USD | 18.87 | 20.35 | 21.83 | 0.62 | 2026-10-16 |
| SOUN | n/a | USD | n/a | n/a | n/a | n/a | n/a |
| AMPX | 7.95 | USD | 8.49 | 9.42 | 10.34 | 0.52 | 2026-10-16 |
| APLD | 20.33 | USD | 20.98 | 25.15 | 29.32 | 0.45 | 2026-10-16 |
| CIFR | 10.15 | USD | 10.77 | 15.74 | 20.70 | 0.37 | 2026-10-16 |
| HIMX | 11.85 | USD | 12.29 | 13.96 | 15.62 | 0.29 | 2026-10-16 |
| JOBY | 4.64 | USD | 4.89 | 5.87 | 6.84 | 0.27 | 2026-10-16 |
| OKLO | 29.08 | USD | 30.70 | 36.52 | 42.34 | 0.33 | 2026-10-16 |
| POET | 5.84 | USD | 6.17 | 7.44 | 8.70 | 0.37 | 2026-10-16 |
| ORCL | 130.30 | USD | 129.90 | 140.78 | 151.66 | 0.54 | 2026-10-16 |

`RKLB` returned a permission-review timeout from the remote tool. `SOUN` returned a REST serialization error involving a non-finite value. Both remain in the record as unavailable rather than being replaced with another source.

## Supporting metrics for main candidates

| Symbol | Score | Technical / flow evidence | Risk metric or qualification |
|---|---:|---|---|
| BKR | 6 | Positive MACD; sector-leading; bullish calls; support 54.43–55.00 | High IV; sell-on-rally dealer bias |
| MRK | 7 | Hammer; put unwinding; relative-strength leader; support 143.96–146.19 from 9 methods | Model R/R 1.58; sell-on-rally dealer bias |
| GLW | 8 | Oversold; bullish OBV; DAOI support; 12-month RS leadership | ATR about 6%; recent 21-day RS weakened |
| CLS | 8 | Oversold; bullish OBV; revenue accelerating; high-volume support node | No VWAP reclaim at scan time |
| CAT | 6 | RSI near 33; bullish OBV; DAOI support; positive news | ATR near 3.6%; unusually tight model stop |
| TSM | 10 | Revenue acceleration; strong compounder; RS outperformance; options support | High IV; earnings date unavailable |
| QCOM | 8 | Hammer; capitulation and OBV divergence; oversold; DAOI support | Wide historical drawdown; weaker recent RS |
| LRCX | 8 | EPS acceleration; outperforming; oversold; calls and DAOI support | High IV; multi-day drawdown risk |
| FLS | 6 | Oversold; bullish OBV; hammer; RS leader; high-confidence BUY | EPS deceleration; sell-on-rally dealer bias |
| GE | 8 | Bullish MACD; strong compounder; calls and DAOI; support 305–307 | Recent RS laggard |
| VRT | 9 | Below lower band; RSI near 33; long-term RS; calls and DAOI support | Average drawdown about 21.6%; stop breached |
| WDC | 8 | Deeply oversold; bullish reversal candle; long-term RS leader | ATR about 6.9%; drawdown about 17.3%; negative news |
| COHR | 7 | Bullish OBV; moderate VWAP reclaim; long-term leader; options support | ATR about 8.5%; EPS deceleration |
| DOCN | 7 | Large one-day decline; revenue acceleration; short float about 11.5%; VWAP attempt | ATR about 8.6%; high IV |
| AGX | 9 | Bullish MACD; RSI near 15; revenue acceleration; strong fundamentals | Very high IV; recent RS deterioration |
| DELL | 6 | Large one-day drop; 12-month RS leader; unusual calls and DAOI | Negative news; revenue deceleration |
| STX | 8 | Strong RS; revenue acceleration; oversold; calls and DAOI support | ATR about 7%; high drawdown; negative news |
| 000660.KS | 9 | Deeply oversold; very strong long-term RS; VWAP reclaim attempt | Revenue deceleration; no useful options data |

## Techniques not fully available

The following professional techniques were considered but could not be applied comprehensively with the available remote MCP data:

- Market breadth, VIX structure, credit spreads, and index-level risk regime.
- Insider purchases, buybacks, institutional ownership changes, and ETF flow data.
- Analyst estimate revisions, forward estimate dispersion, and guidance revisions.
- Full balance-sheet credit-risk analysis and debt-maturity stress.
- A formal beta- and factor-adjusted residual model across every watchlist name.
- Arbitrary peer-pair cointegration and relative-value spreads across the whole watchlist.

## Tools that would improve the workflow

Potential additions to the tool set, prioritized by expected value:

1. **Market breadth and volatility regime:** advance/decline breadth, VIX term structure, index futures, realized volatility, and sector breadth would distinguish broad liquidation from isolated weakness.
2. **Analyst estimate revisions:** forward revenue, EPS, margin, and guidance revisions would identify whether future fundamentals are being cut or improved.
3. **Insider and buyback activity:** open-market purchases and active buybacks would provide fundamental confirmation after a sharp decline.
4. **Credit and balance-sheet risk:** credit spreads, debt maturities, refinancing needs, leverage, interest coverage, and convertible dilution would reveal hidden downside.
5. **Full implied-volatility surface:** skew, term structure, expected move, implied-versus-realized volatility, and volatility percentile would improve stock-versus-spread selection.
6. ETF and institutional flow data.
7. Peer valuation and pair-trading engine.
8. Order-book and closing-auction data.
9. Borrow, lending, and short-flow data.
10. Corporate-action and event monitor.
11. Macro and cross-asset context.
12. Alternative operating data such as web traffic, shipments, chip lead times, rig counts, cloud demand, and hiring.

## Backtest plan

Freeze the original information set and evaluate each candidate without changing the rules after seeing the outcome. Review one week, two weeks, and four weeks after October 14, 2026.

| Field | What to record later |
|---|---|
| Original analysis date | October 14, 2026 |
| Entry reference | Live price or model entry recorded at the time |
| One-week close | Price, return, and maximum adverse excursion |
| Two-week close | Price, return, and maximum adverse excursion |
| Four-week close | Price, return, and maximum adverse excursion |
| Relative benchmark | SPY, QQQ, and relevant sector ETF returns |
| Risk-adjusted result | Return divided by ATR or realized volatility |
| Stop outcome | Whether the projected stop would have triggered |
| Target outcome | Whether the projected target was reached |
| Signal outcome | Which techniques were confirmed or contradicted |

### Evaluation questions

- Did the primary shortlist outperform SPY and its sector ETFs?
- Did high-quality oversold names outperform speculative options-flow names?
- Did anchored VWAP and support-confluence zones identify useful entry areas?
- Did relative-strength leaders outperform laggards after the down day?
- Did OBV divergence and volume capitulation improve hit rates?
- Did options confirmation improve outcomes, or did extreme implied volatility overwhelm the signal?
- Were the ATR and historical-drawdown stop distances realistic?
- Did names with `UNKNOWN` earnings calendars suffer event-related gaps?

## Final interpretation

The session produced a broad research funnel rather than a single definitive buy list. The most promising setups were those where several independent methods agreed: solid fundamentals, oversold price action, relative strength, accumulation evidence, a support zone confirmed by multiple methods, and options positioning that was not actively capping the rebound. The key future test is whether that convergence improves forward returns and reduces drawdowns relative to simpler screens such as buying the largest decliners or every stock below its lower Bollinger Band.

This document is a historical research record for backtesting. It is not investment advice and does not establish a recommendation to buy, sell, or hold any security.
