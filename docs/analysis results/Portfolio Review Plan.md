# Plan: Watchlist vs Portfolio Replacement Analysis (2026-04-25)

## Context

Identify which watchlist stocks could deliver higher near-term (~1 month) gains than the current 7 portfolio holdings. Output a single ranked markdown report to `analysis results/`.

The draft plan proposed 6–9 individual MCP tool calls per stock (~150–200 calls total). The codebase already has `analyze_options_watchlist` (options-analysis-server) which runs a full scored analysis across the entire USD-listed watchlist in one call — use this as the fast Phase 2 screening pass and `analyze_options_symbol` for per-stock baseline, collapsing the tool call count dramatically.

---

## Corrections from the Draft Plan

| Draft assumption | Reality |
|-----------------|---------|
| 6 portfolio stocks | 7 holdings — WDC (Western Digital, $294.37 → $404.70, +52% 30d) is the 7th |
| Watchlist at `watchlist.yaml` (repo root) | Watchlist at `fastMCPTest/watchlist.yaml` (~130 stocks, ~110 USD-listed) |
| CIEN in watchlist | CIEN is **not** in `fastMCPTest/watchlist.yaml` — analyze via `analyze_options_symbol` directly |
| 6–9 individual tools per stock | Use `analyze_options_symbol` (one call, replaces 6+ tools) + 5 supplemental tools for finalists |
| No `portfolio.csv` in repo | File is local; plan carries the known data verbatim |

---

## Portfolio Holdings (Baseline)

From `Stock Portfolio Report.html` (pre-computed, no API call needed):

| Symbol | Cost Basis | Current | 30d Ret | 90d Ret | Price vs 50d MA |
|--------|-----------|---------|---------|---------|-----------------|
| NVDA | $5.41 | $208.10 | +13.16% | +15.00% | Above ✓ |
| CAT | $590.30 | $832.00 | +19.88% | +32.71% | Above ✓ |
| MU | $370.55 | $494.36 | +21.15% | +94.28% | Above ✓ |
| LITE | $695.76 | $875.42 | +37.00% | +143.17% | Above ✓ |
| GEV | $887.11 | $1,148.06 | +36.14% | +64.68% | Above ✓ |
| GLW | $139.12 | $177.85 | +35.09% | +84.27% | Above ✓ |
| WDC | $294.37 | $404.70 | +52.07% | +119.16% | Above ✓ |

Weakest 30-day performers — most likely to have replaceable alternatives: **NVDA (+13%), CAT (+20%), MU (+21%)**.

---

## Execution Flow

```
Phase 1  analyze_options_symbol × 7       (portfolio baseline, parallel)
            ↓
Phase 2  analyze_options_watchlist         (single call, all ~110 USD stocks)
            ↓
Phase 3  Curate ~15–17 finalists
         Filter: top-30 options score OR 30d return > ~30%
                 AND price > 50d MA
                 AND sector tag proximity to a portfolio holding
            ↓
Phase 4  Per-finalist: get_news + get_unusual_calls + get_short_interest
                       + get_rsi + get_macd  (parallel batches of 6–8)
            ↓
Phase 5  Score rubric → write markdown report
```

---

## Phase 1: Portfolio Baseline

Call `analyze_options_symbol` for all 7 portfolio stocks **in parallel**: NVDA, CAT, LITE, GEV, GLW, WDC, MU. One call per stock returns: BB position, P/C ratio, unusual calls flag, IV analysis, earnings proximity, long_score, put_score, long_reason, put_reason.

---

## Phase 2: Watchlist Screening (Single Call)

```python
analyze_options_watchlist(
    watchlist_path="fastMCPTest/watchlist.yaml",
    include_non_us=False
)
```

Returns ranked `long_candidates` and `put_candidates` across all ~110 USD-listed watchlist stocks. Use the `long_candidates` list as the primary filter input for Phase 3.

---

## Phase 3: Curate ~15–17 Finalists

**First action at execution time:** Fetch and parse the live portfolio report at `https://www.johnfunk.com/portfolio/portfolio_report.html`. This file is regenerated every few hours by cron and is the authoritative source for 5/30/90-day returns, YTD, and all moving averages (10/30/50/100/200-day) for every stock in both the portfolio and the watchlist. Extract the full return/MA table for all stocks before proceeding. The stale snapshot below is only a fallback if the URL is unreachable.

Filter rule: stock must meet **at least 2 of 3 criteria**:
1. Top 30 from `analyze_options_watchlist` **OR** 30-day return > ~30% (top-quartile threshold from the live report)
2. Price > 50-day MA (confirmed uptrend — use live report data)
3. Sector/tag proximity to at least one portfolio holding (see tag mapping below)

**Tag-to-portfolio mapping** (from `fastMCPTest/watchlist.yaml` tags):

| Portfolio Stock | Relevant Watchlist Tags |
|----------------|------------------------|
| NVDA | Semiconductors, AI Factory, GPU, Fabless |
| CAT | Industrial Automation, Smart Manufacturing, Industrial Conglomerate |
| LITE | Optical Networking, Photonics, Optical Manufacturing |
| GEV | Power Grid, Energy Infrastructure, Nuclear Energy, Utilities |
| GLW | Engineered Components, Connectors, Photonics |
| WDC | Storage, Flash Storage, HDDs, Data Storage |
| MU | Semiconductors, Memory, Flash Storage, HBM |

**Stale reference snapshot** (from prior session — replace with live report data at execution time):

| Symbol | 30d Ret | 90d Ret | Replaces | Tier |
|--------|---------|---------|----------|------|
| ARM | +102.77% | +93.14% | NVDA | 1 |
| MRVL | +87.23% | +89.95% | NVDA | 1 |
| INTC | +79.55% | +119.58% | NVDA / MU | 1 |
| AMD | +75.56% | +65.59% | NVDA | 1 |
| ALAB | +75.49% | +42.11% | NVDA | 1 |
| CRDO | +72.57% | +37.04% | LITE | 1 |
| ON | +68.55% | +80.42% | NVDA / MU | 1 |
| SNDK | +56.94% | +358.31% | WDC / MU | 1 |
| STX | +55.06% | +101.38% | WDC | 1 |
| CIEN | +53.53% | +144.68% | LITE | 1 — analyze via `analyze_options_symbol` (not in watchlist.yaml) |
| TER | +44.17% | +115.51% | MU / NVDA | 2 |
| FN | +38.87% | +56.92% | LITE | 2 |
| COHR | +35.31% | +86.72% | LITE / GLW | 2 |
| AVGO | +25.20% | +21.97% | NVDA | 2 |
| VRT | +21.97% | +100.89% | CAT / GEV | 2 |
| AMAT | +21.77% | +61.54% | MU | 2 |
| TSM | +17.02% | +39.49% | MU | 2 |

CEG and VST (GEV alternatives) and APH (GLW alternative) included if they surface in top-30 from Phase 2 options scan.

---

## Phase 4: Per-Finalist Deep Dive

Run 5 tools per finalist, parallel batches of 6–8 stocks:

| Tool | Server file | What it adds |
|------|------------|-------------|
| `get_news(symbol)` | `stock_price_server.py:104` | FinBERT sentiment (10 articles) |
| `get_unusual_calls(symbol)` | `stock_price_server.py:1581` | Institutional sweep signal |
| `get_short_interest(symbol)` | `market_analysis_server.py:82` | Squeeze potential |
| `get_rsi(symbol)` | `stock_price_server.py:350` | Momentum room |
| `get_macd(symbol)` | `stock_price_server.py:401` | Trend confirmation |

Skip `get_delta_adjusted_oi` and `get_bid_ask_spread` unless needed for a top-5 finalist — `analyze_options_symbol` already covers equivalent data.

---

## Phase 5: Scoring Rubric (0–100 per stock)

| Signal | Source | Max | Criteria |
|--------|--------|-----|---------|
| News sentiment | `get_news` | 20 | 8–10 positive=20; 5–7=14; 3–4=8; 0–2=2 |
| Unusual call sweep | `get_unusual_calls` | 25 | Strong+≥10 high-conviction=25; Strong=18; Moderate=10; Weak=3 |
| P/C ratio | `analyze_options_symbol` | 15 | <0.7=15; 0.7–0.9=11; 0.9–1.1=7; >1.1=3 |
| Options spread | `get_bid_ask_spread`* | 15 | <20%=15; 20–35%=10; 35–50%=6; >50%=2 |
| Gamma wall | `get_delta_adjusted_oi`* | 15 | Cleared below=15; At price=8; Above (capping)=3 |
| Short interest | `get_short_interest` | 10 | <2% float=5; 2–10%=7; >10%+≥3 DTC=10 |
| **Penalty** | `get_delta_adjusted_oi`* | –5 | MM hedge bias = sell_on_rally + high net DAOI |
| **Bonus** | `get_rsi` | +5 | RSI 40–60 (momentum room) |
| **Bonus** | `get_macd` | +5 | MACD bullish crossover |
| **Bonus** | HTML report | +5 | 30d return in top quartile of watchlist |

*`get_bid_ask_spread` and `get_delta_adjusted_oi` are called only for top-5 finalists where the spread/gamma wall decision is score-decisive.

---

## Phase 6: Output File

**Path:** `analysis results/Watchlist_vs_Portfolio_Analysis_2026-04-25.md`

**Structure:**
1. **Executive Summary** — top 3–5 swap recommendations with one-line rationale each
2. **Methodology** — scoring rubric, data sources, phase summary
3. **Portfolio Baseline Table** — all 7 holdings with return/MA data + `analyze_options_symbol` scores
4. **Watchlist Candidates by Sector** — sub-sections per portfolio stock, scored alternatives
5. **Master Comparison Table** — all ~24 stocks ranked by score (portfolio + finalists)
6. **Recommended Swaps** — "sell X, buy Y" with ≥2 independent data signals per recommendation
7. **Risk Considerations** — top 2–3 risks per top pick

---

## Key Files

| File / Source | Role |
|------|------|
| `https://www.johnfunk.com/portfolio/portfolio_report.html` | **Live report** — fetch first; authoritative 5/30/90d returns + all MAs for every stock (regenerated every few hours by cron) |
| `fastMCPTest/watchlist.yaml` | Candidate source (~130 stocks, ~110 USD) |
| `fastMCPTest/options_analysis.py:1441` | `analyze_options_watchlist` — bulk screening MCP tool |
| `fastMCPTest/options_analysis.py:1457` | `analyze_options_symbol` — per-stock deep analysis MCP tool |
| `fastMCPTest/stock_price_server.py:1581` | `get_unusual_calls` |
| `fastMCPTest/stock_price_server.py:1819` | `get_delta_adjusted_oi` |
| `fastMCPTest/stock_price_server.py:104` | `get_news` |
| `fastMCPTest/stock_price_server.py:350` | `get_rsi` |
| `fastMCPTest/stock_price_server.py:401` | `get_macd` |
| `fastMCPTest/market_analysis_server.py:82` | `get_short_interest` |
| `analysis results/APPL_Analysis_2026-04-14.md` | Reference format for output style |
| `analysis results/Watchlist_vs_Portfolio_Analysis_2026-04-25.md` | **New output file** |

---

## Verification

After generating the markdown:
- All 7 portfolio stocks (NVDA, CAT, LITE, GEV, GLW, WDC, MU) appear in the baseline table with return/MA data from the HTML report
- All ~15–17 finalists appear in the master comparison table
- Every "sell X, buy Y" recommendation cites ≥2 independent data signals
- WDC appears in portfolio baseline AND as context for SNDK/STX candidates
- File renders cleanly as markdown (headers, tables, bullets)
- Top recommendation score is higher than the corresponding portfolio stock it replaces