# Top 60 Return + Fundamentals Ranking

Generated: 2026-04-29

Universe:
- Top 60 symbols by 30-day return from `docs/analysis results/portfolio_report.html` (snapshot dated 2026-04-26 9:54am)
- Symbols sourced from `portfolio.csv` and `watchlist.yaml`

Method:
- `30D Return Rank`: rank by highest 30-day return
- `Fundamental Rank`: rank by highest `composite_score` from live `get_fundamental_score` calls on 2026-04-29
- `Combined Score`: equal-weight average of return percentile rank and fundamentals percentile rank
- `Combined Rank`: final stack rank used for ordering below

Fundamental Label Legend:
- `strong_compounder`: `composite_score >= 8`
- `solid`: `composite_score 4 to 7`
- `average`: `composite_score 0 to 3`
- `weak`: `composite_score -1 to -3`
- `deteriorating`: `composite_score <= -4`

The `composite_score` is built from seven factors:
- `RevCAGR3Y`: 3-year revenue growth
- `RevAccel`: revenue acceleration or deceleration
- `OpMargin3Y`: average operating margin
- `OpMarginTrend`: margin expansion or contraction
- `FCFMargin3Y`: free cash flow margin
- `ValMetric`: valuation level, where cheaper scores better
- `Mom12_1`: momentum from about 12 months ago to 1 month ago

| Combined Rank | Symbol | Name | 30D Return | Fundamental Score | Fundamental Label | Return Rank | Fundamental Rank | Combined Score |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | ---: |
| 1 | [`MRVL`](https://finance.yahoo.com/quote/MRVL/) | Marvell Technology | 87.23% | 7 | solid | 3.0 | 17.0 | 0.8475 |
| 2 | [`CLS`](https://finance.yahoo.com/quote/CLS/) | Celestica | 55.38% | 8 | strong_compounder | 12.5 | 7.5 | 0.8475 |
| 3 | [`AMD`](https://finance.yahoo.com/quote/AMD/) | Advanced Micro Devices | 75.56% | 7 | solid | 5.0 | 17.0 | 0.8305 |
| 4 | [`CRDO`](https://finance.yahoo.com/quote/CRDO/) | Credo Technologies | 72.57% | 7 | solid | 8.0 | 17.0 | 0.8051 |
| 5 | [`ARM`](https://finance.yahoo.com/quote/ARM/) | Arm Holdings plc | 102.77% | 6 | solid | 2.0 | 25.0 | 0.7881 |
| 6 | [`POWL`](https://finance.yahoo.com/quote/POWL/) | Powell Industries | 46.26% | 8 | strong_compounder | 20.0 | 7.5 | 0.7839 |
| 7 | [`TER`](https://finance.yahoo.com/quote/TER/) | Teradyne | 44.17% | 8 | strong_compounder | 22.0 | 7.5 | 0.7669 |
| 8 | [`DOCN`](https://finance.yahoo.com/quote/DOCN/) | DigitalOcean | 41.89% | 8 | strong_compounder | 25.0 | 7.5 | 0.7415 |
| 9 | [`000660.KS`](https://finance.yahoo.com/quote/000660.KS/) | SK hynix | 37.15% | 9 | strong_compounder | 31.0 | 1.5 | 0.7415 |
| 10 | [`AGX`](https://finance.yahoo.com/quote/AGX/) | Argan | 41.15% | 8 | strong_compounder | 27.0 | 7.5 | 0.7246 |
| 11 | [`KLAC`](https://finance.yahoo.com/quote/KLAC/) | KLA | 36.54% | 8 | strong_compounder | 32.0 | 7.5 | 0.6822 |
| 12 | [`ON`](https://finance.yahoo.com/quote/ON/) | onsemi | 68.55% | 5 | solid | 9.0 | 32.0 | 0.6695 |
| 13 | [`COHR`](https://finance.yahoo.com/quote/COHR/) | Coherent | 35.31% | 8 | strong_compounder | 34.0 | 7.5 | 0.6653 |
| 14 | [`GLW`](https://finance.yahoo.com/quote/GLW/) | Corning | 35.09% | 8 | strong_compounder | 35.0 | 7.5 | 0.6568 |
| 15 | [`INTC`](https://finance.yahoo.com/quote/INTC/) | Intel | 79.55% | 4 | solid | 4.0 | 41.0 | 0.6356 |
| 16 | [`STX`](https://finance.yahoo.com/quote/STX/) | Seagate Technology | 55.06% | 5 | solid | 14.0 | 32.0 | 0.6271 |
| 17 | [`ALAB`](https://finance.yahoo.com/quote/ALAB/) | Astera Labs | 75.49% | 4 | solid | 6.0 | 41.0 | 0.6186 |
| 18 | [`DELL`](https://finance.yahoo.com/quote/DELL/) | Dell Technologies | 43.11% | 6 | solid | 24.0 | 25.0 | 0.6017 |
| 19 | [`AIXXF`](https://finance.yahoo.com/quote/AIXXF/) | AIXTRON SE | 50.07% | 5 | solid | 18.0 | 32.0 | 0.5932 |
| 20 | [`AAOI`](https://finance.yahoo.com/quote/AAOI/) | Applied Optoelectronics | 55.38% | 4 | solid | 12.5 | 41.0 | 0.5636 |
| 21 | [`FN`](https://finance.yahoo.com/quote/FN/) | Fabrinet | 38.87% | 6 | solid | 29.0 | 25.0 | 0.5593 |
| 22 | [`PSIX`](https://finance.yahoo.com/quote/PSIX/) | Power Solutions International | 26.71% | 8 | strong_compounder | 48.0 | 7.5 | 0.5466 |
| 23 | [`ACLS`](https://finance.yahoo.com/quote/ACLS/) | Axcelis Technologies | 73.49% | 3 | average | 7.0 | 49.5 | 0.5381 |
| 24 | [`CIEN`](https://finance.yahoo.com/quote/CIEN/) | Ciena | 53.53% | 4 | solid | 16.0 | 41.0 | 0.5339 |
| 25 | [`LRCX`](https://finance.yahoo.com/quote/LRCX/) | Lam Research | 26.11% | 8 | strong_compounder | 50.0 | 7.5 | 0.5297 |
| 26 | [`PL`](https://finance.yahoo.com/quote/PL/) | Planet Labs | 41.87% | 5 | solid | 26.0 | 32.0 | 0.5254 |
| 27 | [`SMTC`](https://finance.yahoo.com/quote/SMTC/) | Semtech | 29.61% | 7 | solid | 42.0 | 17.0 | 0.5169 |
| 28 | [`AEHR`](https://finance.yahoo.com/quote/AEHR/) | Aehr Test Systems | 149.83% | 0 | average | 1.0 | 58.5 | 0.5127 |
| 29 | [`ONTO`](https://finance.yahoo.com/quote/ONTO/) | Onto Innovation | 64.70% | 3 | average | 10.0 | 49.5 | 0.5127 |
| 30 | [`VRT`](https://finance.yahoo.com/quote/VRT/) | Vertiv | 21.97% | 9 | strong_compounder | 58.0 | 1.5 | 0.5127 |
| 31 | [`CAMT`](https://finance.yahoo.com/quote/CAMT/) | Camtek | 29.51% | 7 | solid | 43.0 | 17.0 | 0.5085 |
| 32 | [`GEV`](https://finance.yahoo.com/quote/GEV/) | GE Vernova | 37.96% | 5 | solid | 30.0 | 32.0 | 0.4915 |
| 33 | [`SNDK`](https://finance.yahoo.com/quote/SNDK/) | Sandisk | 56.94% | 1 | average | 11.0 | 56.5 | 0.4449 |
| 34 | [`AMZN`](https://finance.yahoo.com/quote/AMZN/) | Amazon | 25.94% | 7 | solid | 51.0 | 17.0 | 0.4407 |
| 35 | [`R`](https://finance.yahoo.com/quote/R/) | Ryder System | 33.55% | 5 | solid | 36.0 | 32.0 | 0.4407 |
| 36 | [`LITE`](https://finance.yahoo.com/quote/LITE/) | Lumentum | 38.88% | 4 | solid | 28.0 | 41.0 | 0.4322 |
| 37 | [`AVGO`](https://finance.yahoo.com/quote/AVGO/) | Broadcom | 25.20% | 7 | solid | 52.0 | 17.0 | 0.4322 |
| 38 | [`ADI`](https://finance.yahoo.com/quote/ADI/) | Analog Devices | 28.94% | 6 | solid | 45.0 | 25.0 | 0.4237 |
| 39 | [`BE`](https://finance.yahoo.com/quote/BE/) | Bloom Energy | 44.32% | 3 | average | 21.0 | 49.5 | 0.4195 |
| 40 | [`HPE`](https://finance.yahoo.com/quote/HPE/) | Hewlett Packard Enterprise | 28.70% | 6 | solid | 46.0 | 25.0 | 0.4153 |
| 41 | [`FIX`](https://finance.yahoo.com/quote/FIX/) | Comfort Systems | 23.65% | 7 | solid | 55.0 | 17.0 | 0.4068 |
| 42 | [`TGEN`](https://finance.yahoo.com/quote/TGEN/) | Tecogen | 52.67% | 1 | average | 17.0 | 56.5 | 0.3941 |
| 43 | [`CRWV`](https://finance.yahoo.com/quote/CRWV/) | CoreWeave | 35.86% | 4 | solid | 33.0 | 41.0 | 0.3898 |
| 44 | [`HUT`](https://finance.yahoo.com/quote/HUT/) | HUT 8 | 53.74% | -1 | weak | 15.0 | 60.0 | 0.3814 |
| 45 | [`AMAT`](https://finance.yahoo.com/quote/AMAT/) | Applied Materials | 21.77% | 7 | solid | 59.5 | 17.0 | 0.3686 |
| 46 | [`LUNR`](https://finance.yahoo.com/quote/LUNR/) | Intuitive Machines | 43.35% | 2 | average | 23.0 | 54.0 | 0.3644 |
| 47 | [`FLY`](https://finance.yahoo.com/quote/FLY/) | Firefly Aerospace | 47.36% | 0 | average | 19.0 | 58.5 | 0.3602 |
| 48 | [`U`](https://finance.yahoo.com/quote/U/) | Unity Software | 32.30% | 4 | solid | 39.0 | 41.0 | 0.3390 |
| 49 | [`ACMR`](https://finance.yahoo.com/quote/ACMR/) | ACM Research | 23.03% | 6 | solid | 56.0 | 25.0 | 0.3305 |
| 50 | [`CIFR`](https://finance.yahoo.com/quote/CIFR/) | Cipher Digital | 29.81% | 4 | solid | 41.0 | 41.0 | 0.3220 |
| 51 | [`CMI`](https://finance.yahoo.com/quote/CMI/) | Cummins | 21.77% | 6 | solid | 59.5 | 25.0 | 0.3008 |
| 52 | [`GEO`](https://finance.yahoo.com/quote/GEO/) | GEO Group | 33.45% | 3 | average | 37.0 | 49.5 | 0.2839 |
| 53 | [`NBIS`](https://finance.yahoo.com/quote/NBIS/) | Nebius Group | 33.25% | 3 | average | 38.0 | 49.5 | 0.2754 |
| 54 | [`OUST`](https://finance.yahoo.com/quote/OUST/) | Ouster | 22.96% | 5 | solid | 57.0 | 32.0 | 0.2627 |
| 55 | [`SIMO`](https://finance.yahoo.com/quote/SIMO/) | Silicon Motion | 26.46% | 4 | solid | 49.0 | 41.0 | 0.2542 |
| 56 | [`BESI.AS`](https://finance.yahoo.com/quote/BESI.AS/) | BE Semiconductor Industries | 29.33% | 3 | average | 44.0 | 49.5 | 0.2246 |
| 57 | [`BRZE`](https://finance.yahoo.com/quote/BRZE/) | Braze | 25.04% | 4 | solid | 53.0 | 41.0 | 0.2203 |
| 58 | [`WULF`](https://finance.yahoo.com/quote/WULF/) | TeraWulf | 32.25% | 2 | average | 40.0 | 54.0 | 0.2203 |
| 59 | [`APLD`](https://finance.yahoo.com/quote/APLD/) | Applied Digital | 24.48% | 4 | solid | 54.0 | 41.0 | 0.2119 |
| 60 | [`IONQ`](https://finance.yahoo.com/quote/IONQ/) | IonQ | 27.13% | 2 | average | 47.0 | 54.0 | 0.1610 |

## Reproduction Formula

Use this process to rebuild the report:

1. Start with the combined symbol universe from `portfolio.csv` and `watchlist.yaml`.
2. Pull `30 day Return` for each symbol from `docs/analysis results/portfolio_report.html`.
3. Sort all symbols by `30 day Return` descending.
4. Keep the top 60 unique symbols by `30 day Return`.
5. For each of those 60 symbols, fetch live `composite_score` and `fundamental_label` from `get_fundamental_score(symbol)`.
6. Rank the 60 symbols on both measures:
   - `Return Rank`: rank of `30 day Return`, descending, using average rank for ties
   - `Fundamental Rank`: rank of `composite_score`, descending, using average rank for ties
7. Convert each rank into a percentile-style score:

```text
N = number of ranked symbols

Return Percentile = 1 - ((Return Rank - 1) / (N - 1))
Fundamental Percentile = 1 - ((Fundamental Rank - 1) / (N - 1))
```

8. Compute the final combined score:

```text
Combined Score = 0.5 * Return Percentile + 0.5 * Fundamental Percentile
```

9. Sort by:
   - `Combined Score` descending
   - then `30 day Return` descending
   - then `composite_score` descending

10. For the HTML scatter plot:
   - plot each symbol as one point with:
     - `x = composite_score`
     - `y = 30 day Return`
   - run k-means clustering on the 2D points `(composite_score, 30 day Return)`
   - color each point by its assigned cluster
   - draw centroid markers for each cluster
   - use the median `composite_score` and median `30 day Return` as dashed reference lines

Practical notes:
- `N` was `60` in this report.
- `30 day Return` came from the local HTML snapshot dated `2026-04-26 9:54am`.
- Fundamentals were fetched live on `2026-04-29`.
- `Fundamental Label` is the bucket derived from `composite_score`, not a separate formula.
- The HTML report currently lets you switch between `k = 3, 4, 5, 6` clusters.
