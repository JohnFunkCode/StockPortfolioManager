# Stock Portfolio Manager — Capabilities Surface Matrix

This document is a comprehensive inventory of every user-facing capability in the StockPortfolioManager project, mapped to the surface(s) through which it can be accessed.

**Last Updated:** 2026-08-14
**MCP Tools:** 62 across 7 servers (no tool is dual-registered) | **REST Endpoints:** 91 operations (see `docs/openapi-surface.txt`) | **WebUI Pages:** 8 nav pages + 2 drill-downs (+ Sidekick chat rail) | **CLI Tools:** 1 | **Standalone Scripts:** ~10

> **Refactor status:** Phases 1–3 of [`proposals/architectural-standard-v2.md`](proposals/architectural-standard-v2.md) are **complete**, prod rollout is **complete** (`quantcore-prod-20260606`, promoted by digest via `prod-rollout.yml`), QuantUI is deployed behind IAP in both projects, and **BYOK is live as of 2026-07-18** (browser key vault + Settings page + `keyproxy` credential-isolation service; per-user ES256 JWTs replaced the static UI→API token). Phase 3 Step 1 closed the residual MCP-tool→REST-endpoint gaps, so **every MCP tool now has a REST equivalent**. Issue #93 ("Recover Phases 3-7: Support-Level Analysis Tools", PR #108, merged 2026-07-20) added 4 more analysis tools (volume profile, support confluence, OI-change analysis, signed GEX profile) — all REST-exposed, one (`get_support_confluence`) is also WebUI-surfaced. The surface-parity problem this document originally tracked has moved almost entirely to the WebUI layer. See the section immediately below.

---

## ⭐ Built But Not Yet Surfaced in the WebUI

**Headline finding, refreshed 2026-07-20.** The REST tier exposes 91 operations, but the React frontend calls only a subset of them. Everything listed here is fully built, tested, and reachable over REST today — surfacing it is **frontend-only work** (no backend changes needed). Items are grouped by likely user value.

> **Partially superseded — the gap list below was last fully audited 2026-07-20.** Issue #147 Parts C
> and D have since shipped the **Watchlist** and **Fundamentals** pages, which closed the
> cross-symbol fundamentals gap (top-N, upcoming earnings, sector breakdown, 90-day score changes,
> cache freshness). Rows known to be closed are corrected below; the rest have **not** been
> re-verified since 2026-07-20, so read an unstruck row as "was true then", not "is true now".
> The 2026-08-14 pass (issue #208) refreshed the MCP/Sidekick inventory, not this section — a full
> UI-gap re-audit is its own piece of work.

### Tier 1 — High-value analysis synthesis (flagship tools, invisible to UI users)

| Capability | REST endpoint (live today) | What the UI needs |
|---|---|---|
| **Composite trade recommendation** (19 signals → BUY/SELL/HOLD + confidence + suggested position size) | `GET /api/securities/{ticker}/recommendation?capital=` | A "Recommendation" tab or card on Security Detail — arguably the single most valuable unsurfaced feature |
| **Stop-loss synthesis** (7 sub-analyses: BB, VWAP, MACD, RSI, DAOI, drawdown, short interest → concrete stop price) | `GET /api/securities/{ticker}/stop-loss` | Panel on Security Detail (pairs naturally with the recommendation) |
| **Fundamentals profile** (composite score −14..+14, revenue growth/CAGR, earnings acceleration, signal) | `GET /api/securities/{ticker}/fundamentals` (+ `/score`, `/revenue-growth`, `/earnings-acceleration`, `/history`) | A "Fundamentals" tab on Security Detail — still open. Security Detail shows earnings dates only; the `/fundamentals` page added in #147 Part D is cross-symbol, not per-ticker |
| **Market microstructure panel** (short interest + squeeze potential, dark-pool proxy, bid/ask fear gauge) | `GET /api/securities/{ticker}/microstructure` (fan-out; also `/short-interest`, `/dark-pool`, `/bid-ask-spread` individually) | A "Microstructure" section on the Signals tab |
| **Relative strength vs SPY/QQQ/sector** (+ history series for trend) | `GET /api/securities/{ticker}/relative-strength` and `/relative-strength/history` | Chart overlay or Signals-tab card |

### Tier 2 — Options depth

| Capability | REST endpoint (live today) | What the UI needs |
|---|---|---|
| **Covered call / cash-secured put / long setup screening** (rule-based scoring, whole watchlist or one symbol) | `GET /api/options/screen-watchlist`, `GET /api/securities/{ticker}/options/screen` | An "Options Screener" page or Securities-page tab — today this exists only as CLI (`fastMCPTest/options_analysis.py`) and MCP |
| **Exact contract lookup + vertical-spread builder** | `GET .../options/contracts`, `POST .../options/vertical-spread` | A spread-builder form. Note: spread pricing *is* reachable via the Sidekick chat (`spread_payoff` card) but there is no direct UI form |
| **Full options chain browser** (all strikes × all expirations) | `GET .../options/chain`, `GET .../options/full-chain` | The Options Chain tab today renders only the latest snapshot's nearest expiries |
| **Gamma wall history** (daily gamma-wall strike + MM hedge-bias trend) | `GET .../options/gamma-wall-history` | Time-series chart on Options Analytics tab |
| **Open-interest change analysis** (2×2 OI/price classification, put-OI support / call-wall resistance) (issue #93) | `GET .../options/oi-change` | New card on Options Analytics tab |
| **Signed GEX profile** (dealer gamma ladder, zero-gamma level, vanna/charm; daily summary persisted to `gex_history`) (issue #93) | `GET .../options/gex-profile` | Gamma-ladder chart on Options Analytics tab (pairs with gamma-wall-history) |
| **Standalone unusual-calls / delta-adjusted-OI detail** | `GET .../options/unusual-calls`, `.../options/delta-adjusted-oi` | Partially surfaced (aggregated) via the Signals tab's options-flow section; the detail views (per-contract sweeps, full DAOI ladder) are not |

### Tier 3 — Chart overlays, screeners, and workflow

| Capability | REST endpoint (live today) | What the UI needs |
|---|---|---|
| **ATR bands + chandelier trailing stop** (issue #93) | `GET /api/securities/{ticker}/atr-bands` | Overlay on the Price & MAs chart |
| **Anchored VWAP** (auto-anchors: earnings, 52w H/L, gaps, swings) (issue #93) | `GET /api/securities/{ticker}/anchored-vwap` | Overlay on the Price & MAs chart |
| **Volume profile** (POC, value area, HVN/LVN nodes) (issue #93) | `GET /api/securities/{ticker}/volume-profile` | Overlay/histogram on the Price & MAs chart |
| ~~**Cross-symbol fundamentals screeners**~~ — **CLOSED by the `/fundamentals` page** (#147 Part D): top-N, upcoming earnings, sector breakdown, and 90-day score changes each own a panel, over `scope=tracked`. Only `POST /fundamentals/scores-batch` remains unsurfaced | `GET /api/securities/fundamentals/top`, `/upcoming-earnings`, `/sector-breakdown`, `/score-changes`, `/cache-stats` | — (batch scoring is an MCP/API convenience, not obviously a page) |
| **30-day sentiment trend** (per-day breakdown, net score) | `GET /api/securities/{ticker}/news/trend` | Sparkline/chart next to the existing sentiment badge |
| **On-demand news collection** (fetch + FinBERT + persist) | `POST /api/securities/{ticker}/news/collect` | A "refresh news" button (mirrors the existing options-snapshot refresh button) |
| **Portfolio CSV import** (full-sync per-owner) | `POST /api/portfolio/import` | Upload dialog on Dashboard/Securities — today it's the `scripts/import_portfolio.py` CLI only |
| **VWAP history** (multi-day series) | `GET /api/securities/{ticker}/vwap/history` | Chart overlay |

**Already surfaced from issue #93:** `get_support_confluence` (14-source composite support/resistance zones) shipped **with** a WebUI card (`SupportConfluenceCard.tsx` on the Technical Analysis tab) in the same PR — the only issue #93 tool that isn't in the backlog above.

**Sidekick partially mitigates Tier 1:** the chat rail's tool vocabulary (`quantcore/services/chat_tools.py`) is **13 data tools + `show_component`, rendering 15 components** (issue #208), and covers Tier 1 through `get_fundamental_score`, `get_technical_signals`, `get_news_sentiment`, and `price_vertical_spread` — so a UI user *can* ask Sidekick for these. But chat is discoverable-on-demand, not glanceable — the dashboard/detail-page panels above remain the durable fix.

---

## Overview: The Six Surfaces

| Surface | How to Access | Protocol | Live Server | Notes |
|---|---|---|---|---|
| **MCP Tool** | Claude Code / AI clients via `.mcp.json` | Model Context Protocol | Prod Cloud Run wrappers (`https://quantcore-<svc>-…run.app/mcp`) or `*-local` compose stack | Thin HTTP gateway wrappers (`mcp_gateway/rest_client.py`) — every tool call becomes one REST request |
| **REST Endpoint** | HTTP to the FastAPI tier | JSON over HTTP (OpenAPI at `/docs`) | `uvicorn api.main:app --port 5001`; prod `quantcore-api` (JWT-enforced) | The canonical surface — all business logic reachable here; enables WebUI, MCP, and external integrations |
| **WebUI** | Browser (IAP-gated QuantUI on Cloud Run; `npm run dev` locally) | React SPA | Test/prod `quantui` services; Vite dev proxy locally | Declared once in `frontend/src/navigation.tsx`: Portfolio, Plans, Harvester, Securities, Watchlist, Fundamentals, Arbitrage, Settings, plus the Plan-detail and Security-detail drill-downs. The Symbols page was retired in #147 Part G1 |
| **Sidekick Chat** | Chat rail on every WebUI page | SSE via `POST /api/chat` → keyproxy → Anthropic (BYOK) | Same as WebUI | LLM with 13 data tools + `show_component` (renders 15 components inline — price/technical, spread payoff, the four arbitrage cards, portfolio/lots, watchlist + fundamentals rankings). Tools dispatch **in-process to the services**, not over MCP |
| **CLI Tool** | `python fastMCPTest/options_analysis.py --flags` | argparse | N/A | Hybrid: also runs as the options-analysis MCP server |
| **Standalone Script** | `python script.py` | Direct execution | `main.py` runs daily as a prod Cloud Run Job | Report generation, position monitors, operational utilities |

---

## Capability Summary by Surface

| Surface | Count | Examples |
|---|---|---|
| MCP Tools (7 servers) | 62 | `get_stock_price`, `price_vertical_spread`, `get_fundamental_score`, `get_news_sentiment`, `get_short_interest`, `analyze_options_watchlist`, `scan_arbitrage`, `get_portfolio` |
| REST Endpoints | 91 operations | `GET /api/securities/{ticker}/technicals`, `POST /api/plans`, `GET /api/securities/screen`, `GET /api/securities/{ticker}/recommendation`, `POST /api/chat` |
| WebUI Pages | 8 + 2 drill-downs + chat rail | Portfolio, Plans, Harvester, Securities, Watchlist, Fundamentals, Arbitrage, Settings (BYOK keys); drill-downs: Plan Detail, Security Detail (Price & MAs · Technical Analysis · Options Chain · Options Performance · Options Analytics · Signals) |
| Sidekick chat tools | 13 + 15 components | `get_stock_price`, `get_technical_signals`, `get_rsi`, `get_macd`, `get_fundamental_score`, `get_news_sentiment`, `price_vertical_spread`, `list_arbitrage_universe`, `analyze_arbitrage_pair`, `scan_arbitrage`, `discover_arbitrage_pairs`, `get_portfolio_summary`, `get_symbol_lots`; renders `signals`, `live_price`, `price_chart`, `spread_payoff`, `arbitrage_pair`, `arbitrage_spread`, `arbitrage_premium`, `arbitrage_scan`, `arbitrage_discovery`, `portfolio_table`, `portfolio_allocation`, `symbol_lots`, `watchlist_fundamentals`, `fundamentals_top`, `fundamentals_score_changes` |
| CLI Tools | 1 | `fastMCPTest/options_analysis.py` (strategy screening; hybrid CLI + MCP server). `collect_options.py` has been **deleted** |
| Standalone Scripts | ~10 | `main.py` (daily report Job), watchlist fundamentals report, INTC/WMT spread monitors, `import_portfolio.py`, migration/ops scripts |

**MCP tool count by server (verified against source, 2026-08-14 — issue #208):** stock-price 21 · company-fundamentals 12 · options-analysis 11 · portfolio 6 · arbitrage 5 · news-sentiment 4 · market-analysis 3 = **62 tools across 7 servers**. **No tool is dual-registered.** The prior revision of this document claimed `get_option_contracts` and `price_vertical_spread` appeared on both stock-price and options-analysis; they do not — `60e4bcd` ("consolidate options tools onto options-analysis-server") moved every options tool off stock-price, which is the whole of the 29→21 drop. The claim appears only in this document — `CLAUDE.md` never carried it, though it did say "5 remote MCP servers in `.mcp.json`" when there are 7, corrected in the same PR as this refresh.

Count it with the **anchored** grep — `grep -c "^@mcp.tool" fastMCPTest/*.py`. The unanchored form over-counts: `fastMCPTest/options_analysis.py` mentions `@mcp.tool()` inside its module docstring, which reads as a 12th tool that does not exist.

**New since the 2026-07-20 refresh (issue #208):** two MCP servers this document never listed — **`arbitrage-server`** (5 tools, new domain section below) and **`portfolio-server`** (6 tools) — plus the options consolidation described above. Sidekick grew from 7 data tools to 13: the three arbitrage tools that shipped with the scanner, and three added by issue #208 — `discover_arbitrage_pairs` (its `arbitrage_discovery` component was registered on both sides with no tool to feed it) and `get_portfolio_summary` / `get_symbol_lots` (portfolio-server had zero chat coverage, so Sidekick could render a portfolio card but not reason over the numbers in prose).

**New in the 2026-07-19 refresh (issue #93 / PR #108, merged 2026-07-20):** 4 new stock-price MCP tools + REST endpoints — `get_volume_profile`, `get_support_confluence`, `get_oi_change_analysis`, `get_gex_profile` (stock-price grew 25→29 at the time); new `gex_history` DB table (daily signed-GEX summary, upserted per call); new frontend `SupportConfluenceCard.tsx` on the Technical Analysis tab (the one issue #93 capability that shipped with WebUI surfacing).

---

## Complete Capability Inventory

Capabilities are organized by domain. **Bold ⚠ rows** are built-but-not-in-UI (see the headline section above). "Signals tab" = Security Detail → Signals.

### Domain: Price & Technical Analysis

| Capability | MCP Tool | REST Endpoint | WebUI | Sidekick |
|---|---|---|---|---|
| Current stock price + Bollinger Bands | `get_stock_price` | `GET /{ticker}/ohlcv`, `/{ticker}/price-summary` | Price & MAs tab; Symbols → LivePrice | `get_stock_price`, `live_price` card |
| RSI (14-period) | `get_rsi` | `GET /{ticker}/rsi`, `/technicals` | Technical Analysis tab (RSIChart) | `get_rsi` |
| MACD (12/26/9 + crossovers) | `get_macd` | `GET /{ticker}/macd`, `/technicals` | Technical Analysis tab (MACDChart) | `get_macd` |
| Stochastic Oscillator (%K/%D) | `get_stochastic` | `GET /{ticker}/stochastic`, `/signals/technical` | Signals tab | via `get_technical_signals` |
| Moving averages (10/30/50/100/200-day) | via `get_stock_price` | `GET /{ticker}/technicals` | Price & MAs tab | — |
| Volume climax / capitulation / OBV divergence | `get_volume_analysis`, `get_obv` | `GET /{ticker}/volume`, `/obv`, `/signals/technical` | Signals tab; VolumeChart | via `get_technical_signals` |
| VWAP + reclaim signal | `get_vwap` | `GET /{ticker}/vwap`, `/signals/technical` | Signals tab | via `get_technical_signals` |
| **⚠ VWAP history (multi-day series)** | `get_vwap_history` | `GET /{ticker}/vwap/history` | **—** | — |
| Candlestick patterns (hammer, doji, shooting star, gravestone) | `get_candlestick_patterns` | `GET /{ticker}/candlestick`, `/signals/technical` | Signals tab | via `get_technical_signals` |
| Higher-low swing structure | `get_higher_lows` | `GET /{ticker}/higher-lows`, `/signals/technical` | Signals tab | via `get_technical_signals` |
| Gap detection (gap-up/down, fill status) | `get_gap_analysis` | `GET /{ticker}/gaps`, `/signals/technical` | Signals tab | via `get_technical_signals` |
| **⚠ Relative strength vs SPY/QQQ/sector (+ history)** | `get_relative_strength`, `get_relative_strength_history` | `GET /{ticker}/relative-strength`, `/relative-strength/history` | **—** | — |
| Historical drawdown (worst 1d/5d, trailing stop %) | `get_historical_drawdown` | `GET /{ticker}/drawdown`, `/signals/risk` | Signals tab | — |
| **⚠ ATR bands + chandelier trailing stop** (issue #93) | `get_atr_bands` | `GET /{ticker}/atr-bands` | **—** | — |
| **⚠ Anchored VWAP (auto-anchors)** (issue #93) | `get_anchored_vwap` | `GET /{ticker}/anchored-vwap` | **—** | — |
| **⚠ Volume profile (POC, value area, HVN/LVN nodes)** (issue #93) | `get_volume_profile` | `GET /{ticker}/volume-profile` | **—** | — |
| Support confluence (14-source composite: clustered, method-weighted support/resistance zones) (issue #93) | `get_support_confluence` | `GET /{ticker}/support-confluence` | **Technical Analysis tab** (`SupportConfluenceCard`) | — |
| **⚠ Composite trade recommendation (19 signals)** | `get_trade_recommendation` | `GET /{ticker}/recommendation?capital=` | **—** | — |
| **⚠ Stop-loss synthesis (7 sub-analyses)** | `get_stop_loss_analysis` | `GET /{ticker}/stop-loss` | **—** | — |
| Technical screener (RSI/MA/BB/MACD/sentiment filters) | — | `GET /api/securities/screen` | Securities page → screener presets | — |

(REST paths abbreviated: `/{ticker}/…` = `/api/securities/{ticker}/…`.)

---

### Domain: Options Analysis

| Capability | MCP Tool | REST Endpoint | WebUI | CLI |
|---|---|---|---|---|
| Latest options snapshot (price, P/C ratio, nearest-expiry chains) | via `get_stock_price` | `GET .../options/latest` | Options Chain tab | — |
| **⚠ Full options chain (all strikes/expirations)** | `get_full_options_chain` | `GET .../options/chain`, `.../options/full-chain` | **—** (tab shows latest snapshot only) | — |
| **⚠ Exact contract lookup by expiry/strike** | `get_option_contracts` | `GET .../options/contracts` | **—** | — |
| Vertical spread pricing (debit, max P/L, breakeven, liquidity) | `price_vertical_spread` | `POST .../options/vertical-spread` | Sidekick only (`spread_payoff` card, interactive strike repricing) — **no direct UI form** | — |
| Unusual call sweep detection | `get_unusual_calls` | `GET .../options/unusual-calls`, `/signals/options-flow` | Signals tab (aggregated) | — |
| Delta-Adjusted OI (DAOI, gamma wall, delta flip) | `get_delta_adjusted_oi` | `GET .../options/delta-adjusted-oi`, `/signals/options-flow` | Signals tab (aggregated) | — |
| **⚠ Gamma wall history (daily MM hedge-bias trend)** | `get_gamma_wall_history` | `GET .../options/gamma-wall-history` | **—** | — |
| **⚠ Open-interest change analysis** (2×2 OI/price classification, put-OI support / call-wall resistance) (issue #93) | `get_oi_change_analysis` | `GET .../options/oi-change` | **—** | — |
| **⚠ Signed GEX profile** (dealer gamma ladder, zero-gamma level, vanna/charm; persisted to `gex_history`) (issue #93) | `get_gex_profile` | `GET .../options/gex-profile` | **—** | — |
| IV Rank + IV Percentile (365-day) | — | `GET .../options/iv-rank` | Options Analytics tab | — |
| Max pain + expected move per expiration | — | `GET .../options/analytics` | Options Analytics tab (MaxPainChart, IVTermStructureChart) | — |
| P/C ratio history (daily aggregated) | — | `GET .../options/history` | Options Performance tab (PCRatioChart) | — |
| Backfill historical P/C via Polygon.io | — | `POST .../options/history/backfill` | Options Performance tab (backfill button) | — |
| Bulk options snapshot refresh (all watchlist symbols) | — | `POST /api/securities/refresh-options-snapshots` | Securities page → bulk controls | — |
| **⚠ Covered call / put / long setup screening (watchlist)** | `analyze_options_watchlist` | `GET /api/options/screen-watchlist` | **—** | `options_analysis.py --puts-budget 1000` |
| **⚠ Same, single symbol** | `analyze_options_symbol` | `GET .../options/screen` | **—** | `options_analysis.py` |
| Portfolio delta exposure (aggregated) | — | `GET /api/portfolio/delta-exposure` | Dashboard → MM delta table | — |
| MCP wrapper health check | `mcp_health_check` | (wrapper-local) | — | — |

---

### Domain: Arbitrage

Securities whose price has stretched against a structurally linked underlying, across three
families — **nav_vehicle** (the only one with a computable fair value), **commodity_etf**, and
**producer**. Curated links live in `arb_universe.yaml`. Fully surfaced on all four surfaces; the
design rationale is [`docs/arbitrage-scanner-usage.md`](arbitrage-scanner-usage.md).

| Capability | MCP Tool | REST Endpoint | WebUI | Sidekick |
|---|---|---|---|---|
| Curated pair universe (links, families, why each exists) | `list_arbitrage_universe` | `GET /api/arbitrage/universe` | Arbitrage page → family filters | `list_arbitrage_universe` |
| Scan the universe (ranked by convergence mechanism, not spread width) | `scan_arbitrage` | `GET /api/arbitrage/scan` | Arbitrage page (`ScanTable`) | `scan_arbitrage`, `arbitrage_scan` card |
| Single-pair workup (NAV, `factors` breakdown, `reasons`, `breaks_on`) | `analyze_arbitrage_pair` | `GET /api/arbitrage/pairs/{security}` | Arbitrage page → selected row | `analyze_arbitrage_pair`, `arbitrage_pair` card |
| Spread history (z-score series) | — | `GET /api/arbitrage/pairs/{security}/spread-history` | Arbitrage page (`SpreadChart`) | `arbitrage_spread` card |
| Premium/discount history | — | `GET /api/arbitrage/pairs/{security}/premium-history` | Arbitrage page (`PremiumChart`) | `arbitrage_premium` card |
| Statistical sweep for undeclared cointegrated links (candidates for curation — no NAV, no convergence claim) | `discover_arbitrage_pairs` | `GET /api/arbitrage/discover` | Arbitrage page (`DiscoveryScatter`) | `discover_arbitrage_pairs`, `arbitrage_discovery` card |
| MCP wrapper health check | `mcp_health_check` | (wrapper-local) | — | — |

Two things about the discovery path are load-bearing rather than incidental. A discovered pair is a
**curation candidate, not a signal** — presenting correlation as an opportunity is exactly what the
curated universe exists to prevent, so the tool description says so and a test asserts it. And the
25-symbol / 10-reference caps live on `ArbitrageService` (`MAX_DISCOVER_SYMBOLS`,
`MAX_DISCOVER_REFERENCES`), re-asserted by **both** the REST route and the Sidekick handler —
Sidekick dispatches in-process and would otherwise bypass a cap that only guarded HTTP.

---

### Domain: Fundamental Analysis

**Every fundamentals tool is now REST-exposed; none of it (except the earnings date) reaches the UI.** This is the largest whole-domain UI gap.

| Capability | MCP Tool | REST Endpoint | WebUI | Sidekick |
|---|---|---|---|---|
| Earnings calendar (next date, days-to-earnings, risk, avg move) | `get_earnings_calendar` | `GET /{ticker}/earnings`, `/{ticker}/earnings-calendar` | Security Detail header + Securities column | — |
| **⚠ Composite fundamental score (−14..+14, 7 metrics)** | `get_fundamental_score` | `GET /{ticker}/fundamentals/score` | **—** | `get_fundamental_score` |
| **⚠ Full fundamental profile** | `get_full_fundamental_profile` | `GET /{ticker}/fundamentals` | **—** | — |
| **⚠ Revenue growth (5 quarters, QoQ, CAGR, trajectory)** | `get_revenue_growth` | `GET /{ticker}/fundamentals/revenue-growth` | **—** | — |
| **⚠ Earnings acceleration (CAN SLIM "A")** | `get_earnings_acceleration` | `GET /{ticker}/fundamentals/earnings-acceleration` | **—** | — |
| **⚠ Historical score snapshots + trend** | `get_fundamental_history` | `GET /{ticker}/fundamentals/history` | **—** | — |
| **⚠ Batch scoring (multi-symbol, ranked)** | `get_fundamental_scores_batch` | `POST /api/securities/fundamentals/scores-batch` | **—** | — |
| Top-N stocks by score (per sector, from cache) | `get_top_fundamental_stocks` | `GET /api/securities/fundamentals/top` | **Fundamentals page** (`TopFundamentalsPanel`) | `fundamentals_top` card (the panel's own `variant="rail"`) |
| Upcoming earnings within N days | `get_upcoming_earnings` | `GET /api/securities/fundamentals/upcoming-earnings` | **Fundamentals page** (`UpcomingEarningsStrip`) | — |
| Sector fundamental breakdown | `get_sector_fundamental_breakdown` | `GET /api/securities/fundamentals/sector-breakdown` | **Fundamentals page** (`SectorBreakdownPanel`) | — |
| Score change tracking (90-day movers) | `get_fundamental_score_changes` | `GET /api/securities/fundamentals/score-changes` | **Fundamentals page** (`ScoreChangesPanel`) | `fundamentals_score_changes` card (same hook + react-query key as the panel) |
| Cache statistics | `get_cache_stats` | `GET /api/securities/fundamentals/cache-stats` | Fundamentals page (`CacheFreshnessBanner`) | — |

---

### Domain: News & Sentiment

| Capability | MCP Tool | REST Endpoint | WebUI | Sidekick |
|---|---|---|---|---|
| Fetch news + FinBERT sentiment per article | `get_news` | `GET /{ticker}/news` | Signals tab → news panel | — |
| **⚠ Collect + persist news articles (FinBERT-scored)** | `collect_news` | `POST /{ticker}/news/collect` | **—** (no refresh button) | — |
| Aggregate sentiment signal (BULLISH/BEARISH/MIXED/NEUTRAL) | `get_news_sentiment` | `GET /{ticker}/news/sentiment` | Sentiment badge (via summary endpoint) | `get_news_sentiment` |
| **⚠ Per-day sentiment trend (30-day, net score)** | `get_sentiment_trend` | `GET /{ticker}/news/trend` | **—** | — |
| Bulk sentiment dashboard (all tracked securities) | — | `GET /api/securities/news/sentiment-summary` | Securities page → badge column + dialog; screener presets | — |
| List symbols with articles in DB | `list_news_symbols` | `GET /api/securities/news/symbols` | — (admin/debug) | — |

---

### Domain: Market Microstructure

**All three signals are REST-exposed (individually and as a fan-out) but have zero WebUI presence.**

| Capability | MCP Tool | REST Endpoint | WebUI |
|---|---|---|---|
| **⚠ Short interest (shares short, float %, days-to-cover) + squeeze potential** | `get_short_interest` | `GET /{ticker}/short-interest`, `/{ticker}/microstructure` | **—** |
| **⚠ Dark pool / block trade activity (accumulation/distribution proxy)** | `get_dark_pool` | `GET /{ticker}/dark-pool`, `/{ticker}/microstructure` | **—** |
| **⚠ Bid/ask spread signal (widening vs norm, fear gauge)** | `get_bid_ask_spread` | `GET /{ticker}/bid-ask-spread`, `/{ticker}/microstructure` | **—** |

---

### Domain: Harvest Ladder (Systematic Profit-Taking)

Fully surfaced — REST + WebUI, no gaps.

| Capability | REST Endpoint | WebUI | Standalone |
|---|---|---|---|
| Build volatility-based harvest plan | `POST /api/plans` | Plans page → create dialog | `experiments/HarvesterExperiment.py` (algorithm reference) |
| List plans (active + superseded) | `GET /api/plans` | Plans page (DataGrid + filters) | — |
| Plan detail with rungs | `GET /api/plans/{id}`, `/{id}/rungs` | Plan Detail page | — |
| Edit plan notes / delete plan | `PATCH`/`DELETE /api/plans/{id}` | Plan Detail / Plans page | — |
| Rung detail / achieve / execute | `GET /api/rungs/{id}`, `POST .../achieve`, `POST .../execute` | Plan Detail dialogs | — |
| Scan active plans for rung hits → Discord alerts | — | — | `main.py` daily Cloud Run Job (via `HarvesterService`) |

---

### Domain: Portfolio & Watchlist Management

Positions are DB-backed with multi-owner support (`positions` table, `owner` column); `portfolio.csv` is now a per-owner import format. The watchlist, by contrast, is **global** — one shared list, no `owner` column (issue #83).

**Nothing here lets a caller choose whose holdings it reads.** MCP tools and Sidekick tools alike take no `owner` argument: the MCP wrappers pass the caller's own identity through, and the Sidekick tools are dispatched with an owner resolved from the signed-in principal at the route boundary, with any model-supplied `owner` key discarded before dispatch (issue #126 decision #20). An identity with no portfolio mapping gets a clear "not linked" tool result rather than a guess or someone else's numbers.

| Capability | MCP Tool | REST Endpoint | WebUI | Sidekick | CLI/Script |
|---|---|---|---|---|---|
| Per-symbol position rows (quantity, basis, current value, gain/loss, `active_plan_id`) | `get_portfolio` | `GET /api/portfolio/symbols` | Portfolio, Securities pages | `portfolio_table` / `portfolio_allocation` cards | `main.py` (report) |
| Portfolio totals (cost basis, current value, gain/loss $ and %, $/day) | `get_portfolio_summary` | `GET /api/portfolio/symbols` → `totals` | Portfolio page header | `get_portfolio_summary` (issue #208) | — |
| Per-symbol lots (quantity, purchase price, date, gain/loss) | `get_symbol_lots` | `GET /api/portfolio/lots` (filtered to the ticker by the caller) | Portfolio page → symbol drill-down | `get_symbol_lots` (issue #208), `symbol_lots` card | — |
| Legacy flat position list | — | `GET /api/portfolio` | — | — | — |
| Add / remove position | — | `POST /api/portfolio`, `DELETE /api/portfolio/{ticker}` | AddSecurityDialog / remove action | — | — |
| Lot lifecycle (create / edit / delete / close) | — | `POST`/`PATCH`/`DELETE /api/portfolio/lots[/{id}]`, `POST .../lots/{id}/close` | Portfolio page lot dialogs | — | — |
| **⚠ Bulk CSV import (full-sync per owner)** | — | `POST /api/portfolio/import` | **—** | — | `scripts/import_portfolio.py --csv portfolio.csv --owner john` |
| View / add watchlist | `list_watchlist`, `add_to_watchlist` | `GET`/`POST /api/watchlist` | Securities page + AddSecurityDialog | — | `scripts/import_watchlist.py` |
| Watchlist returns + fundamentals (ranked, 6 queries, 0 network calls) | — | `GET /api/watchlist/fundamentals` | **Watchlist page** | `watchlist_fundamentals` card | — |
| Remove from watchlist (**UI-only by design** — the MCP seam has no `delete` verb, so no agent can drop symbols off a shared list) | — | `DELETE /api/watchlist/{ticker}` | Watchlist / Securities page | — | — |
| Retag a watchlist entry (replaces the tag set, does not merge) | — | `PATCH /api/watchlist/{ticker}` | Watchlist page chips | — | — |
| Combined portfolio + watchlist view | — | `GET /api/securities` | Securities page DataGrid | — | — |
| Symbol lookup (name/sector/industry) | — | `GET /api/securities/lookup` | AddSecurityDialog autocomplete | — | — |
| HTML portfolio report (charts, gain/loss, S3 upload) | — | — | — | — | `scripts/generate_portfolio_report.py` (Pi, via `runOnPi.sh`) |
| Discord alerts + options/fundamentals capture | — | — | — | — | `main.py` (daily prod Cloud Run Job) |
| MCP wrapper health check | `mcp_health_check` | (wrapper-local) | — | — | — |

---

### Domain: Sidekick Chat & BYOK (new since 2026-07)

| Capability | Surface | Notes |
|---|---|---|
| Conversational analysis (streaming, tool-using LLM) | `POST /api/chat` (SSE) → Sidekick rail on every page | **13 data tools:** `get_stock_price`, `get_technical_signals`, `get_rsi`, `get_macd`, `get_fundamental_score`, `get_news_sentiment`, `price_vertical_spread`, `list_arbitrage_universe`, `analyze_arbitrage_pair`, `scan_arbitrage`, `discover_arbitrage_pairs`, `get_portfolio_summary`, `get_symbol_lots`. They dispatch **in-process to the services** — Sidekick is not an MCP client, so a tool here is a separate design decision from the MCP tool of the same name |
| Inline rendered components in chat | `show_component` directive | **15 components**, registered on both sides (`chat_tools.BACKEND_COMPONENT_REGISTRY` ↔ `frontend/src/chat/componentRegistry.tsx`, strict prop specs, extra props rejected): `signals`, `live_price`, `price_chart`, `spread_payoff` (interactive: strike select/reprice backchannel), `arbitrage_pair`, `arbitrage_spread`, `arbitrage_premium`, `arbitrage_scan`, `arbitrage_discovery`, `portfolio_table`, `portfolio_allocation`, `symbol_lots`, `watchlist_fundamentals`, `fundamentals_top`, `fundamentals_score_changes` |
| Owner scoping for the portfolio tools | resolved at the route from the signed-in principal | No tool takes an `owner`; a model-supplied one is discarded at dispatch. An unlinked identity degrades to a recoverable per-tool error, **not** a 403 on the whole turn — one unprovisioned portfolio mapping must not fail the price and technical tools in the same conversation |
| BYOK key vault (add/rotate/unlock Anthropic key) | Settings page (`frontend/src/vault/`) | IndexedDB, passphrase PBKDF2 + AES-GCM; single-use envelope per turn |
| Keyproxy handshake | `GET /api/keyproxy/publickey`, `POST /api/keyproxy/validate` | Envelope encryption pin + key validation; keyproxy itself is a separate IAM-locked Cloud Run service (`keyproxy/`) |

---

### Domain: Notifications

| Capability | Surface | Notes |
|---|---|---|
| Discord alerts: MA violations (30/50/100/200-day), price below purchase, harvest rung hits | `main.py` + `notifier.py`, daily Cloud Run Job (Cloud Scheduler) | Dedup via `notification.log` per run. No REST management surface (by design so far) |

---

### Domain: Admin / Utility

| Capability | REST Endpoint | WebUI |
|---|---|---|
| API health check (DB connectivity) | `GET /api/health` | — |
| Dashboard stats (plan/rung/symbol counts) | `GET /api/dashboard/stats` | Dashboard stats cards |
| Symbol registry list / latest price | `GET /api/symbols`, `/api/symbols/{ticker}/price` | Symbols page + LivePrice |

---

## CLI & Standalone Scripts

| Script | Purpose | Status |
|---|---|---|
| `fastMCPTest/options_analysis.py` | Covered-call/put/long screening — hybrid CLI + MCP server (5 tools) | Active |
| `main.py` | Daily HTML report + S3 upload + Discord alerts + harvest scan | Active — prod Cloud Run Job |
| `scripts/generate_watchlist_fundamentals_report.py` | Watchlist returns + fundamentals HTML report | Active |
| `scripts/import_portfolio.py` | Per-owner CSV → `positions` table (full-sync replace) | Active |
| `scripts/mint_prod_jwt.py` | Mint 90-day prod JWTs for MCP clients | Active — ops |
| `experiments/INTC_bear_call_spread_monitor.py`, `WMT_bull_call_spread_monitor.py` | Open-position monitors (pickled state) | Active — keep |
| `experiments/HarvesterExperiment.py` | Harvest-ladder algorithm reference (DELL hardcoded) | Development reference |
| `scripts/migrate_sqlite_to_postgres.py`, `migrate_to_unified_db.py`, `repair_ohlcv_misalignment.py` | One-shot migrations / data repair | Operational utilities |
| `scripts/generate_keyproxy_keypair.py` | BYOK envelope keypair generation | Ops (packet-8b runbook) |
| `html_summary.py`, `simple_text_summary.py` | Legacy report variants (old CSV format) | Superseded — candidates for deletion |

**Deleted:** `collect_options.py` (was broken — imports referenced classes that no longer existed) and the six superseded analytics experiments (`RevenueGrowthExperiment*.py`, `EarningsAccelerationExperiment.py`, `CompositScoreExperiment.py`, `MaxDrawDownAnalyzer.py`, `YahooNewsReader/RSSReaderExperiment.py`, `HarvesterPlanStore.py`) — all functionality lives in `quantcore/` services now.

---

## Database Structure

One unified **QuantCore** PostgreSQL database (17 tables, `psycopg2` via `QUANTCORE_DB_DSN`; schema auto-created by `quantcore/db.init_schema()` from every entry point). Local dev and Cloud SQL (via Auth Proxy) are interchangeable. All access goes through `quantcore/db.get_connection()`; writers are the repositories in `quantcore/repositories/`.

| Table Category | Tables | Primary Writers | Purpose |
|---|---|---|---|
| **Price Data** | `ohlcv`, `fetch_log` | `OhlcvRepository` | Shared OHLCV bar cache (daily + intraday intervals); yfinance fetch tracking |
| **Harvester + Positions** | `symbols`, `plan_templates`, `positions`, `plan_instances`, `plan_rungs`, `alerts` | `HarvesterPlanDB`, `PortfolioRepository` | Harvest plans/rungs/alerts; `positions` is the live multi-owner position registry |
| **Options** | `options_snapshots`, `options_expirations`, `options_contracts`, `gamma_wall_history`, `gex_history`, `options_positions` | `OptionsStore`/`OptionsRepository`, `OptionsPositionStore` | Chain snapshots (ATM + full), gamma wall history, daily signed-GEX regime history, active options positions |
| **News & Sentiment** | `news_articles`, `sentiment_snapshots` | `NewsStore`, `SentimentStore` | FinBERT-scored articles; aggregated sentiment summaries |
| **Fundamentals** | `fundamentals_history` | `FundamentalsRepository` | Append-only TTL cache (earnings_calendar, fundamental_score, revenue_growth, earnings_acceleration payloads) |

### Remaining Database Gaps

1. **`options_positions` has no REST/WebUI/MCP surface.** Only the standalone INTC/WMT monitors and direct `OptionsPositionStore` use it. Either add REST CRUD + a UI positions panel, or fold it into the monitors' pickled state and drop the table.
2. **Microstructure signals are never persisted.** `get_short_interest` / `get_dark_pool` / `get_bid_ask_spread` compute in real time; no historical trend is possible. Add a snapshot table if trend analysis is wanted.
3. **News article data partially duplicated.** `sentiment_snapshots` re-embeds aggregate data derivable from `news_articles`; no FK cross-reference. Low priority.

---

## Summary: Key Insights

### What Works Well
- **Backend surface parity is done.** Every MCP tool has a REST twin (91 operations); MCP wrappers are one-call-deep HTTP adapters; adapters and services are cleanly layered per architectural-standard-v2.
- **Harvest ladder, options analytics (IV rank, max pain, P/C history), technical signals, portfolio/watchlist CRUD, and the sentiment dashboard** are all fully surfaced in the WebUI.
- **Sidekick + BYOK** gives UI users conversational access to a meaningful subset of the analysis stack with zero server-held credentials.
- **Ops maturity:** CI/CD to test, gated digest-promotion to prod, IAP-gated UI, per-user JWTs, daily report Job.

### The One Big Gap: WebUI Coverage
The React frontend has kept pace with options analytics and harvest workflows but not with the analysis synthesis tools. In priority order (full detail in the ⭐ section at top):

1. **Trade recommendation + stop-loss panels** — the two most powerful synthesis endpoints, invisible in the UI.
2. **A Fundamentals tab on Security Detail** — the cross-symbol half of the domain shipped as the `/fundamentals` page (#147 Part D), but the *per-ticker* half (composite score, revenue growth, earnings acceleration, score history) still surfaces only an earnings date.
3. **Microstructure section on the Signals tab** — 3 ready endpoints, zero UI.
4. **Options screener page** — covered-call/put screening exists as CLI/MCP/REST but not UI.
5. **Chart overlays** — ATR bands, anchored VWAP, volume profile, relative strength; all one GET away.
6. **Options Analytics additions** — OI-change analysis, signed GEX profile (both new from issue #93).
7. **Workflow buttons** — news collect, portfolio CSV import.

### Recommended Quick Wins
1. Add a **Recommendation card** to Security Detail (`GET .../recommendation` + `.../stop-loss`) — highest value-to-effort ratio in the codebase.
2. Add a **Fundamentals tab** to Security Detail reusing the existing tab pattern (`.../fundamentals` fan-out already aggregates score/revenue/acceleration).
3. Append a **Microstructure section** to the existing Signals tab (`.../microstructure` returns all three signals in one call).
4. ~~Add **"Upcoming earnings" and "Top fundamentals" widgets**~~ — **done**: both are panels on the `/fundamentals` page, and `fundamentals_top` / `fundamentals_score_changes` render as Sidekick cards off the same hooks.
5. Delete `html_summary.py` / `simple_text_summary.py` (superseded legacy reports).

---

**Document Version:** 2.2
**Last Updated:** 2026-08-14 (issue #208 — the Sidekick tool audit. Added the missing `arbitrage-server` and `portfolio-server`, a new Arbitrage domain section, and MCP/Sidekick columns on Portfolio & Watchlist; corrected the tool count from "51 unique / 53 registrations across 5 servers" to **62 across 7**; deleted the dual-registration claim, which was never true in code; refreshed the Sidekick surface from 7 tools / 4 components to 13 / 15 and the page list to `navigation.tsx`. Counts verified with `grep -c "^@mcp.tool" fastMCPTest/*.py` — anchored, because the unanchored form counts a docstring)
**Prior version:** 2.1, 2026-07-20 (merge-conflict reconciliation of the 2026-07-19 UI-gap-analysis refresh with the issue #93 support-tools recovery [PR #108])
**Maintained By:** John Funk
