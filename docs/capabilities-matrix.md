# Stock Portfolio Manager — Capabilities Surface Matrix

This document is a comprehensive inventory of every user-facing capability in the StockPortfolioManager project, mapped to the surface(s) through which it can be accessed.

**Last Updated:** 2026-10-22
**MCP Tools:** 63 across 7 servers (no tool is dual-registered) | **REST Endpoints:** 111 operations (see `docs/openapi-surface.txt`) | **WebUI Pages:** 7 nav pages + 2 drill-downs (+ Sidekick chat rail) | **Sidekick:** 13 data tools + 15 components | **CLI Tools:** 1 | **Daily Jobs:** 2

> **Refactor status:** Phases 1–3 of [`proposals/architectural-standard-v2.md`](proposals/architectural-standard-v2.md) are **complete**, prod rollout is **complete** (`quantcore-prod-20260606`, promoted by digest via `prod-rollout.yml`), QuantUI is deployed behind IAP in both projects, and **BYOK is live as of 2026-07-18**. **Every MCP tool has a REST equivalent**, so the surface-parity problem this document originally tracked now lives almost entirely in the WebUI layer — see the section immediately below.

---

## ⭐ Built But Not Yet Surfaced in the WebUI

**Re-audited 2026-10-22** against what the frontend actually calls: every `apiRequest` path in
`frontend/src/api/*.ts`, traced through `frontend/src/hooks/` to the components that consume it. A
client function with no component behind it counts as **not surfaced**, and so does a standalone
endpoint whose data the UI only gets through an aggregate one. Of the 111 REST operations, the
WebUI reaches **57** (counting the fetches Sidekick cards make themselves), leaving **54** with no
UI consumer — each one is listed, by reason, in the
[WebUI consumer inventory](#appendix-rest-operations-with-no-webui-consumer) at the end. Everything
listed here is fully built, tested, and reachable over REST today, so surfacing it is **frontend-only work**. Of the gap rows from the 2026-07-20 audit, only
stop-loss synthesis has shipped a UI since (2026-10-23, #399: `StopLossCard` on the Technical
Analysis tab and the Sidekick `stop_loss` card); the rest are still open.

### Tier 1 — High-value analysis synthesis (flagship tools, invisible to UI users)

| Capability | REST endpoint (live today) | What the UI needs |
|---|---|---|
| **Composite trade recommendation** (19 signals → BUY/SELL/HOLD + confidence + suggested position size) | `GET /api/securities/{ticker}/recommendation?capital=` | A "Recommendation" tab or card on Security Detail — arguably the single most valuable unsurfaced feature |
| **Per-ticker fundamentals profile** (composite score −14..+14, revenue growth/CAGR, earnings acceleration, score history) | `GET /api/securities/{ticker}/fundamentals` (+ `/score`, `/revenue-growth`, `/earnings-acceleration`, `/history`) | A "Fundamentals" tab on Security Detail. Security Detail shows earnings dates only; the Watchlist page shows the composite score as a column, and the `/fundamentals` page is cross-symbol |
| **Market microstructure panel** (short interest + squeeze potential, dark-pool proxy, bid/ask fear gauge) | `GET /api/securities/{ticker}/microstructure` (fan-out; also `/short-interest`, `/dark-pool`, `/bid-ask-spread` individually) | A "Microstructure" section on the Signals tab |
| **Relative strength vs SPY/QQQ/sector** (+ history series for trend) | `GET /api/securities/{ticker}/relative-strength` and `/relative-strength/history` | Chart overlay or Signals-tab card |

### Tier 2 — Options depth

| Capability | REST endpoint (live today) | What the UI needs |
|---|---|---|
| **Covered call / cash-secured put / long setup screening** (rule-based scoring, whole watchlist or one symbol) | `GET /api/options/screen-watchlist`, `GET /api/securities/{ticker}/options/screen` | An "Options Screener" page or a Watchlist-page panel — today this exists only as CLI (`fastMCPTest/options_analysis.py`), MCP, and REST |
| **Exact contract lookup + vertical-spread builder** | `GET .../options/contracts`, `POST .../options/vertical-spread` | A spread-builder form. Spread pricing *is* reachable via the Sidekick chat (`spread_payoff` card) but there is no direct UI form |
| **Full options chain browser** (all strikes × all expirations) | `GET .../options/chain`, `GET .../options/full-chain` | The Options Chain tab renders only the latest snapshot's nearest expiries (`.../options/latest`) |
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
| **VWAP history** (multi-day series) | `GET /api/securities/{ticker}/vwap/history` | Chart overlay |
| **30-day sentiment trend** (per-day breakdown, net score) | `GET /api/securities/{ticker}/news/trend` | Sparkline/chart next to the existing sentiment badge |
| **On-demand news collection** (fetch + FinBERT + persist) | `POST /api/securities/{ticker}/news/collect` | A "refresh news" button (mirrors the existing options-snapshot refresh button) |
| **Sale history + editing a sale's reason** (issue #266) | `GET /api/portfolio/sales`, `PATCH /api/portfolio/sales/{id}` | The client and hooks exist (`portfolioApi.getSales`/`updateSale`, `useSales`/`useUpdateSale`) but **no component calls them** — the realized-P/L view (#235) is the intended home |
| **Portfolio CSV import** (full-sync per-owner) | `POST /api/portfolio/import` | Upload dialog on the Portfolio page — today it's the `scripts/import_portfolio.py` CLI only |
| **Batch fundamental scoring** | `POST /api/securities/fundamentals/scores-batch` | An MCP/API convenience; not obviously a page |
| **Curated arbitrage universe listing** | `GET /api/arbitrage/universe` | The Arbitrage page shows only what a scan returns; the full curated list (with each link's rationale) is MCP/Sidekick-only |

**Already surfaced:** the cross-symbol fundamentals screeners (top-N, upcoming earnings, sector
breakdown, 90-day score changes, cache freshness) are panels on the `/fundamentals` page
(#147 Part D), and `get_support_confluence` ships as `SupportConfluenceCard` on the Technical
Analysis tab (issue #93).

**Sidekick partially mitigates Tier 1:** the chat rail's tool vocabulary (`quantcore/services/chat_tools.py`) covers part of Tier 1 through `get_fundamental_score`, `get_technical_signals`, `get_news_sentiment`, and `price_vertical_spread` — so a UI user *can* ask Sidekick for these. But chat is discoverable-on-demand, not glanceable — the dashboard/detail-page panels above remain the durable fix.

---

## Overview: The Surfaces

| Surface | How to Access | Protocol | Live Server | Notes |
|---|---|---|---|---|
| **MCP Tool** | Claude Code / AI clients via `.mcp.json` | Model Context Protocol | Prod Cloud Run wrappers (`https://quantcore-<svc>-…run.app/mcp`) or `*-local` compose stack | Thin HTTP gateway wrappers (`mcp_gateway/rest_client.py`) — every tool call becomes one REST request |
| **REST Endpoint** | HTTP to the FastAPI tier | JSON over HTTP (OpenAPI at `/docs`) | `uvicorn api.main:app --port 5001`; prod `quantcore-api` (JWT-enforced) | The canonical surface — all business logic reachable here; enables WebUI, MCP, and external integrations |
| **WebUI** | Browser (IAP-gated QuantUI on Cloud Run; `npm run dev` locally) | React SPA | Test/prod `quantui` services; Vite dev proxy locally | Declared once in `frontend/src/navigation.tsx`, grouped into two nav menus: **My Positions** (Portfolio, Plans, Harvester) and **Research** (Watchlist, Fundamentals, Arbitrage), plus a top-level Settings button. Drill-downs: Plan detail (`/plans/:id`) and Security detail (`/securities/:symbol`). The Securities page merged into Watchlist (#224) — `/securities` now redirects to `/watchlist` |
| **Sidekick Chat** | Chat rail on every WebUI page | SSE via `POST /api/chat` → keyproxy → Anthropic (BYOK) | Same as WebUI | LLM with 13 data tools + `show_component` (renders 15 components inline). Tools dispatch **in-process to the services**, not over MCP |
| **CLI Tool** | `python fastMCPTest/options_analysis.py --flags` | argparse | N/A | Hybrid: also runs as the options-analysis MCP server |
| **Daily Jobs / Scripts** | Cloud Run Jobs; `python script.py` | Direct execution | `main.py` (`quantcore-report`) and `news_job.py` (`quantcore-news`) run daily in prod | Notifications, captures, report generation, operational utilities |

---

## Capability Summary by Surface

| Surface | Count | Examples |
|---|---|---|
| MCP Tools (7 servers) | 63 | `get_stock_price`, `price_vertical_spread`, `get_fundamental_score`, `get_news_sentiment`, `get_short_interest`, `analyze_options_watchlist`, `scan_arbitrage`, `get_portfolio` |
| REST Endpoints | 111 operations (57 reached by the WebUI) | `GET /api/securities/{ticker}/technicals`, `POST /api/plans`, `GET /api/watchlist/mine`, `GET /api/securities/{ticker}/recommendation`, `POST /api/chat` |
| WebUI Pages | 7 + 2 drill-downs + chat rail | Portfolio, Plans, Harvester, Watchlist (with screener, sentiment dashboard and options-refresh panels), Fundamentals, Arbitrage, Settings (BYOK keys, chat model); drill-downs: Plan Detail, Security Detail (Price & MAs · Technical Analysis · Options Chain · Options Performance · Options Analytics · Signals) |
| Sidekick chat tools | 13 + 15 components | `get_stock_price`, `get_technical_signals`, `get_rsi`, `get_macd`, `get_fundamental_score`, `get_news_sentiment`, `price_vertical_spread`, `list_arbitrage_universe`, `analyze_arbitrage_pair`, `scan_arbitrage`, `discover_arbitrage_pairs`, `get_portfolio_summary`, `get_symbol_lots`; renders `signals`, `live_price`, `price_chart`, `spread_payoff`, `arbitrage_pair`, `arbitrage_spread`, `arbitrage_premium`, `arbitrage_scan`, `arbitrage_discovery`, `portfolio_table`, `portfolio_allocation`, `symbol_lots`, `watchlist_fundamentals`, `fundamentals_top`, `fundamentals_score_changes` |
| CLI Tools | 1 | `fastMCPTest/options_analysis.py` (strategy screening; hybrid CLI + MCP server) |
| Daily Jobs | 2 | `main.py` (report Job: notifications, options capture, GEX, fundamentals warming), `news_job.py` (news Job: collection + FinBERT) |

**MCP tool count by server (verified against source, 2026-10-22):** stock-price 21 · company-fundamentals 12 · options-analysis 11 · portfolio 7 · arbitrage 5 · news-sentiment 4 · market-analysis 3 = **63 tools across 7 servers**. **No tool is dual-registered** — `60e4bcd` ("consolidate options tools onto options-analysis-server") moved every options tool off stock-price. Three of the 63 are the wrapper-local `mcp_health_check` (portfolio, arbitrage, options-analysis), which has no REST twin by design.

Count it with the **anchored** grep — `grep -c "^@mcp.tool" fastMCPTest/*.py`. The unanchored form over-counts: `fastMCPTest/options_analysis.py` mentions `@mcp.tool()` inside its module docstring, which reads as a 12th tool that does not exist. Every tool has a contract case in `scripts/mcp_tool_cases.py`, and `tests/test_mcp_tool_contracts.py` pins the 63.

**Changed since the 2026-08-14 refresh:** the Securities page merged into the Watchlist page (#224) — `GET /api/securities` was retired and `GET /api/watchlist/mine` (watchlist rows joined with the caller's holdings) became the merged page's source, keeping the operation count at 111. The nav bar was regrouped into the My Positions / Research menus (#147 Part G2). The portfolio server gained `get_symbol_sales` (issue #266, 2026-10-08), taking the MCP total from 62 to 63. The Sidekick and REST totals are unchanged.

---

## Complete Capability Inventory

Capabilities are organized by domain. **Bold ⚠ rows** are built-but-not-in-UI (see the headline section above). "Signals tab" = Security Detail → Signals.

### Domain: Price & Technical Analysis

| Capability | MCP Tool | REST Endpoint | WebUI | Sidekick |
|---|---|---|---|---|
| Current stock price + Bollinger Bands | `get_stock_price` | `GET /{ticker}/ohlcv`, `/{ticker}/price-summary`, `/api/symbols/{ticker}/price` | Price & MAs tab (via `/technicals`); Plan Detail → `LivePrice` | `get_stock_price`, `live_price` card |
| RSI (14-period) | `get_rsi` | `GET /{ticker}/rsi`, `/technicals` | Technical Analysis tab (RSIChart) | `get_rsi` |
| MACD (12/26/9 + crossovers) | `get_macd` | `GET /{ticker}/macd`, `/technicals` | Technical Analysis tab (MACDChart) | `get_macd` |
| Stochastic Oscillator (%K/%D) | `get_stochastic` | `GET /{ticker}/stochastic`, `/signals/technical` | Signals tab | via `get_technical_signals` |
| Moving averages (10/30/50/100/200-day) | via `get_stock_price` | `GET /{ticker}/technicals` | Price & MAs tab | `price_chart` card |
| Volume climax / capitulation / OBV divergence | `get_volume_analysis`, `get_obv` | `GET /{ticker}/volume`, `/obv`, `/signals/technical` | Signals tab; VolumeChart | via `get_technical_signals` |
| VWAP + reclaim signal | `get_vwap` | `GET /{ticker}/vwap`, `/signals/technical` | Signals tab | via `get_technical_signals` |
| **⚠ VWAP history (multi-day series)** | `get_vwap_history` | `GET /{ticker}/vwap/history` | **—** | — |
| Candlestick patterns (hammer, doji, shooting star, gravestone) | `get_candlestick_patterns` | `GET /{ticker}/candlestick`, `/signals/technical` | Signals tab | via `get_technical_signals` |
| Higher-low swing structure | `get_higher_lows` | `GET /{ticker}/higher-lows`, `/signals/technical` | Signals tab | via `get_technical_signals` |
| Gap detection (gap-up/down, fill status) | `get_gap_analysis` | `GET /{ticker}/gaps`, `/signals/technical` | Signals tab | via `get_technical_signals` |
| **⚠ Relative strength vs SPY/QQQ/sector (+ history)** | `get_relative_strength`, `get_relative_strength_history` | `GET /{ticker}/relative-strength`, `/relative-strength/history` | **—** | — |
| Historical drawdown (worst 1d/5d, trailing stop %) | `get_historical_drawdown` | `GET /{ticker}/drawdown`, `/signals/risk`, `/stop-loss` | **Technical Analysis tab** (`StopLossCard` drawdown replay); Signals-tab summary line | via `stop_loss` |
| **⚠ ATR bands + chandelier trailing stop** (issue #93) | `get_atr_bands` | `GET /{ticker}/atr-bands` | **—** | — |
| **⚠ Anchored VWAP (auto-anchors)** (issue #93) | `get_anchored_vwap` | `GET /{ticker}/anchored-vwap` | **—** | — |
| **⚠ Volume profile (POC, value area, HVN/LVN nodes)** (issue #93) | `get_volume_profile` | `GET /{ticker}/volume-profile` | **—** | — |
| Support confluence (14-source composite: clustered, method-weighted support/resistance zones) (issue #93) | `get_support_confluence` | `GET /{ticker}/support-confluence` | **Technical Analysis tab** (`SupportConfluenceCard`) | — |
| **⚠ Composite trade recommendation (19 signals)** | `get_trade_recommendation` | `GET /{ticker}/recommendation?capital=` | **—** | — |
| Stop-loss synthesis (7 sub-analyses → technical, trailing and confluence stops, drawdown replay, P&L at each stop) (#399) | `get_stop_loss_analysis` | `GET /{ticker}/stop-loss?confluence=&position=mine` | **Technical Analysis tab** (`StopLossCard`, one `confluence=full` request that also fills `SupportConfluenceCard`) | `stop_loss` |
| Technical screener (RSI/MA/BB/MACD/sentiment filters) | — | `GET /api/securities/screen` | Watchlist page → `ScreenerPanel` | — |

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
| Bulk options snapshot refresh (portfolio or watchlist symbols) | — | `POST /api/securities/refresh-options-snapshots` | Watchlist page → `OptionsRefreshPanel` | — |
| **⚠ Covered call / put / long setup screening (watchlist)** — cache-first; the MCP path never calls Yahoo (`source=live` remains a REST-only operator escape hatch) | `analyze_options_watchlist` | `GET /api/options/screen-watchlist` | **—** | `options_analysis.py --puts-budget 1000` |
| **⚠ Same, single symbol** | `analyze_options_symbol` | `GET .../options/screen` | **—** | `options_analysis.py` |
| Portfolio delta exposure (aggregated) | — | `GET /api/portfolio/delta-exposure` | Harvester page → MM delta table | — |
| Options-position alerts (ITM, expiration, profit target) | — | — | — | `main.py` daily Job (`Notifier.check_options_alerts`, reads `options_positions`) |
| MCP wrapper health check | `mcp_health_check` | (wrapper-local) | — | — |

---

### Domain: Arbitrage

Securities whose price has stretched against a structurally linked underlying, across three
families — **nav_vehicle** (the only one with a computable fair value), **commodity_etf**, and
**producer**. Curated links live in `arb_universe.yaml`; daily NAV inputs persist to
`arb_nav_snapshots`. Surfaced on every surface except the curated-universe listing, which has no
WebUI view. Design: [`docs/architecture/arbitrage.md`](architecture/arbitrage.md); usage:
[`docs/arbitrage-scanner-usage.md`](arbitrage-scanner-usage.md).

| Capability | MCP Tool | REST Endpoint | WebUI | Sidekick |
|---|---|---|---|---|
| **⚠ Curated pair universe (links, families, why each exists)** | `list_arbitrage_universe` | `GET /api/arbitrage/universe` | **—** (the page shows scan results only) | `list_arbitrage_universe` |
| Scan the universe (ranked by convergence mechanism, not spread width) | `scan_arbitrage` | `GET /api/arbitrage/scan` | Arbitrage page (`ScanTable`) | `scan_arbitrage`, `arbitrage_scan` card |
| Single-pair workup (NAV, `factors` breakdown, `reasons`, `breaks_on`) | `analyze_arbitrage_pair` | `GET /api/arbitrage/pairs/{security}` | Arbitrage page → selected row (`ArbitrageCard`) | `analyze_arbitrage_pair`, `arbitrage_pair` card |
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

**Every fundamentals tool is REST-exposed.** The cross-symbol half reaches the UI through the
`/fundamentals` page and the Watchlist page's score column; the per-ticker half (beyond the
earnings date) does not.

| Capability | MCP Tool | REST Endpoint | WebUI | Sidekick |
|---|---|---|---|---|
| Earnings calendar (next date, days-to-earnings, risk, avg move) | `get_earnings_calendar` | `GET /{ticker}/earnings`, `/{ticker}/earnings-calendar` | Security Detail header (via `/earnings`) | — |
| Composite fundamental score (−14..+14, 7 metrics) | `get_fundamental_score` | `GET /{ticker}/fundamentals/score` | Watchlist page → score column (from the cache, via `/api/watchlist/mine`); **⚠ no per-ticker view** | `get_fundamental_score` |
| **⚠ Full fundamental profile** | `get_full_fundamental_profile` | `GET /{ticker}/fundamentals` | **—** | — |
| **⚠ Revenue growth (5 quarters, QoQ, CAGR, trajectory)** | `get_revenue_growth` | `GET /{ticker}/fundamentals/revenue-growth` | **—** | — |
| **⚠ Earnings acceleration (CAN SLIM "A")** | `get_earnings_acceleration` | `GET /{ticker}/fundamentals/earnings-acceleration` | **—** | — |
| **⚠ Historical score snapshots + trend** | `get_fundamental_history` | `GET /{ticker}/fundamentals/history` | **—** | — |
| **⚠ Batch scoring (multi-symbol, ranked; ≤25 symbols)** | `get_fundamental_scores_batch` | `POST /api/securities/fundamentals/scores-batch` | **—** | — |
| Top-N stocks by score (per sector, from cache) | `get_top_fundamental_stocks` | `GET /api/securities/fundamentals/top` | **Fundamentals page** (`TopFundamentalsPanel`) | `fundamentals_top` card (the panel's own `variant="rail"`) |
| Upcoming earnings within N days | `get_upcoming_earnings` | `GET /api/securities/fundamentals/upcoming-earnings` | **Fundamentals page** (`UpcomingEarningsStrip`) | — |
| Sector fundamental breakdown | `get_sector_fundamental_breakdown` | `GET /api/securities/fundamentals/sector-breakdown` | **Fundamentals page** (`SectorBreakdownPanel`) | — |
| Score change tracking (90-day movers) | `get_fundamental_score_changes` | `GET /api/securities/fundamentals/score-changes` | **Fundamentals page** (`ScoreChangesPanel`) | `fundamentals_score_changes` card (same hook + react-query key as the panel) |
| Cache statistics | `get_cache_stats` | `GET /api/securities/fundamentals/cache-stats` | Fundamentals page (`CacheFreshnessBanner`) | — |
| Cache warming + staleness alarm | — | — | — | `main.py` daily Job (warming pass, Discord alarm on stale cache) |

---

### Domain: News & Sentiment

| Capability | MCP Tool | REST Endpoint | WebUI | Sidekick |
|---|---|---|---|---|
| Fetch news + FinBERT sentiment per article | `get_news` | `GET /{ticker}/news` | Signals tab → news panel | — |
| **⚠ Collect + persist news articles (FinBERT-scored)** | `collect_news` | `POST /{ticker}/news/collect` | **—** (no refresh button) | — |
| Aggregate sentiment signal (BULLISH/BEARISH/MIXED/NEUTRAL) | `get_news_sentiment` | `GET /{ticker}/news/sentiment` | Sentiment badge (via the summary endpoint) | `get_news_sentiment` |
| **⚠ Per-day sentiment trend (30-day, net score)** | `get_sentiment_trend` | `GET /{ticker}/news/trend` | **—** | — |
| Bulk sentiment dashboard (all tracked securities) | — | `GET /api/securities/news/sentiment-summary` | Watchlist page → news column + `SentimentDashboard`; screener presets | — |
| List symbols with articles in DB | `list_news_symbols` | `GET /api/securities/news/symbols` | — (admin/debug) | — |
| Scheduled collection for the whole universe | — | — | — | `news_job.py` daily Job (`quantcore-news`; FinBERT baked into the image), with a Discord gap alarm; `main.py` raises a staleness alarm |

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

Fully surfaced — REST + WebUI, no gaps. Plans are owner-scoped in SQL, and a plan may exist only while the owner holds the shares (see [`docs/architecture/harvester.md`](architecture/harvester.md)).

| Capability | REST Endpoint | WebUI | Standalone |
|---|---|---|---|
| Build volatility-based harvest plan (refused without an `OPEN` lot) | `POST /api/plans` | Plans page → create dialog (held symbols only) | `experiments/HarvesterExperiment.py` (algorithm reference) |
| List plans (active + superseded) | `GET /api/plans` | Plans page (DataGrid + filters) | — |
| Plan detail with rungs | `GET /api/plans/{id}` (rungs included), `/{id}/rungs` | Plan Detail page via `/plans/{id}` (with `LivePrice`); `/{id}/rungs` has no UI caller | — |
| Edit plan notes / delete plan | `PATCH`/`DELETE /api/plans/{id}` | Plan Detail / Plans page | — |
| Rung detail / achieve / execute | `GET /api/rungs/{id}`, `POST .../achieve`, `POST .../execute` | Plan Detail dialogs (achieve/execute); `GET /api/rungs/{id}` has no UI caller | — |
| Harvester stats (plan/rung/symbol counts) | `GET /api/dashboard/stats` | Harvester page stats cards | — |
| Auto-close a plan when the position goes flat | (side effect of lot close/delete) | — | `PortfolioService._close_plan_if_flat` |
| Scan active plans for rung hits → Discord alerts | — | — | `main.py` daily Cloud Run Job (via `HarvesterService`) |

---

### Domain: Portfolio & Watchlist Management

Positions are DB-backed with multi-owner support (`positions` table, `owner` column; sales in `lot_sales`); `portfolio.csv` is a per-owner import format. The watchlist, by contrast, is **global** — one shared list, `added_by` for audit only (issue #83). The Watchlist page is the merged home of both since the Securities page was retired (#224): it lists every watchlist entry with the caller's quantity and average price alongside.

**Nothing here lets a caller choose whose holdings it reads.** MCP tools and Sidekick tools alike take no `owner` argument: the MCP wrappers pass the caller's own identity through, and the Sidekick tools are dispatched with an owner resolved from the signed-in principal at the route boundary, with any model-supplied `owner` key discarded before dispatch (issue #126 decision #20). An identity with no portfolio mapping gets a clear "not linked" tool result rather than a guess or someone else's numbers.

| Capability | MCP Tool | REST Endpoint | WebUI | Sidekick | CLI/Script |
|---|---|---|---|---|---|
| Per-symbol position rows (quantity, basis, current value, gain/loss, `active_plan_id`) | `get_portfolio` | `GET /api/portfolio/symbols` | Portfolio page | `portfolio_table` / `portfolio_allocation` cards | `main.py` (report) |
| Portfolio totals (cost basis, current value, gain/loss $ and %, $/day) | `get_portfolio_summary` | `GET /api/portfolio/symbols` → `totals` | Portfolio page header | `get_portfolio_summary` (issue #208) | — |
| Per-symbol lots (quantity, purchase price, date, gain/loss, and the reason it was bought) | `get_symbol_lots` | `GET /api/portfolio/lots` (filtered to the ticker by the caller); the UI reads lots nested in `GET /api/portfolio/symbols` | Portfolio page → symbol drill-down (via `/symbols`) | `get_symbol_lots` (issue #208), `symbol_lots` card | — |
| **⚠ Recorded sales of a symbol, with the reason for each** (issue #266) | `get_symbol_sales` | `GET /api/portfolio/sales?symbol=` | **—** (hook exists, no component — #235) | `get_symbol_lots` (returns `sales` alongside `lots`) | — |
| **⚠ Edit a sale's reason** (issue #266) | — | `PATCH /api/portfolio/sales/{id}` | **—** (hook exists, no component — #235) | — | — |
| Flat position list (Security Detail's "do I hold this?" check, the plan dialog's held-symbol list) | — | `GET /api/portfolio` | Security Detail, Plans → create dialog | — | — |
| Add / remove position | — | `POST /api/portfolio`, `DELETE /api/portfolio/{ticker}` | AddSecurityDialog / Security Detail remove action | — | — |
| Lot lifecycle (create / edit / delete / close), with an optional reason on each purchase and sale (issue #266; `lot_notes` gives each lot of a multi-lot sale its own reason) | — | `POST`/`PATCH`/`DELETE /api/portfolio/lots[/{id}]`, `POST .../lots/{id}/close`, `POST .../lots/{id}/close/preview` (read-only: which lots a sale would touch) | Portfolio page lot dialogs, lot-row note icon + Edit note | — | — |
| **⚠ Bulk CSV import (full-sync per owner)** | — | `POST /api/portfolio/import` | **—** | — | `scripts/import_portfolio.py --csv portfolio.csv --owner john` |
| View / add watchlist (currency resolved server-side) | `list_watchlist`, `add_to_watchlist` | `GET`/`POST /api/watchlist` | AddSecurityDialog on the Watchlist page (POST only — the page lists rows from `/mine`) | — | `scripts/import_watchlist.py` |
| Merged watchlist rows: returns, fundamentals, news, plus the caller's quantity/avg price (7 queries, 0 network calls) (#224) | — | `GET /api/watchlist/mine` | **Watchlist page**; Security Detail | — | — |
| Watchlist returns + fundamentals ranking (6 queries, 0 network calls) | — | `GET /api/watchlist/fundamentals` | — (the page moved to `/mine` in #224) | `watchlist_fundamentals` card | `scripts/generate_watchlist_fundamentals_report.py` |
| Remove from watchlist (**UI-only by design** — the MCP seam has no `delete` verb, so no agent can drop symbols off a shared list) | — | `DELETE /api/watchlist/{ticker}` | Watchlist page / Security Detail | — | — |
| Retag a watchlist entry (replaces the tag set, does not merge) | — | `PATCH /api/watchlist/{ticker}` | Watchlist page chips + tag filter | — | — |
| Symbol lookup (name/sector/industry) | — | `GET /api/securities/lookup` | AddSecurityDialog / AddLotDialog autocomplete | — | — |
| HTML portfolio report (charts, gain/loss, S3 upload) | — | — | — | — | `scripts/generate_portfolio_report.py` (Pi, via `runOnPi.sh`) |
| MCP wrapper health check | `mcp_health_check` | (wrapper-local) | — | — | — |

---

### Domain: Sidekick Chat, Settings & BYOK

| Capability | Surface | Notes |
|---|---|---|
| Conversational analysis (streaming, tool-using LLM) | `POST /api/chat` (SSE) → Sidekick rail on every page | **13 data tools:** `get_stock_price`, `get_technical_signals`, `get_rsi`, `get_macd`, `get_fundamental_score`, `get_news_sentiment`, `price_vertical_spread`, `list_arbitrage_universe`, `analyze_arbitrage_pair`, `scan_arbitrage`, `discover_arbitrage_pairs`, `get_portfolio_summary`, `get_symbol_lots`. They dispatch **in-process to the services** — Sidekick is not an MCP client, so a tool here is a separate design decision from the MCP tool of the same name |
| Inline rendered components in chat | `show_component` directive | **15 components**, registered on both sides (`chat_tools.BACKEND_COMPONENT_REGISTRY` ↔ `frontend/src/chat/componentRegistry.tsx`, strict prop specs, extra props rejected): `signals`, `live_price`, `price_chart`, `spread_payoff` (interactive: strike select/reprice backchannel), `arbitrage_pair`, `arbitrage_spread`, `arbitrage_premium`, `arbitrage_scan`, `arbitrage_discovery`, `portfolio_table`, `portfolio_allocation`, `symbol_lots`, `watchlist_fundamentals`, `fundamentals_top`, `fundamentals_score_changes` |
| Owner scoping for the portfolio tools | resolved at the route from the signed-in principal | No tool takes an `owner`; a model-supplied one is discarded at dispatch. An unlinked identity degrades to a recoverable per-tool error, **not** a 403 on the whole turn |
| Per-user chat model (`claude-sonnet-5` default, `claude-opus-4-8`, `claude-fable-5`) | `GET`/`PUT /api/settings` → Settings page + Sidekick header quick-switch | One server-side source of truth (`user_settings`); a retired stored value falls back to the default |
| BYOK key vault (add/rotate/unlock Anthropic key) | Settings page (`frontend/src/vault/`) | IndexedDB, passphrase PBKDF2 + AES-GCM; single-use envelope per turn |
| Keyproxy handshake | `GET /api/keyproxy/publickey`, `POST /api/keyproxy/validate` | Envelope encryption pin + key validation; keyproxy itself is a separate IAM-locked Cloud Run service (`keyproxy/`) |

---

### Domain: Notifications

All Discord, sent by `notifier.py`; `send_notifications()` dedupes on the embed title against
`notification.log`. No REST management surface (by design so far).

| Alert | Raised by | Notes |
|---|---|---|
| Moving-average violations (30/50/100/200-day) and price below purchase price | `main.py` daily Job | Per holding, from the DB-backed positions |
| Harvest rung hits | `main.py` daily Job | Via `HarvesterService` |
| Options-position alerts (ITM, expiration, profit target); auto-expires past positions | `main.py` daily Job | Reads `options_positions` |
| Sentiment flips | `main.py` daily Job | `Notifier.check_sentiment_flips` |
| Empty watchlist (no YAML fallback, by design) | `main.py` daily Job | Dated title so a persisting condition alarms every day |
| Options capture gap | `main.py` daily Job | Re-reads what actually landed in the database |
| Stale fundamentals cache | `main.py` daily Job | After the warming pass |
| News staleness / news collection gap | `main.py` / `news_job.py` | Defaults and thresholds in [`architecture/daily-jobs.md`](architecture/daily-jobs.md) |

---

### Domain: Admin / Utility

| Capability | REST Endpoint | WebUI |
|---|---|---|
| API health check (DB connectivity) | `GET /api/health` | — |
| Symbol registry list | `GET /api/symbols` | — (the Symbols page was retired in #147 Part G1) |
| Symbol latest price | `GET /api/symbols/{ticker}/price` | Plan Detail → `LivePrice`; Sidekick `live_price` / `spread_payoff` polling |

---

## CLI, Jobs & Scripts

| Script | Purpose | Status |
|---|---|---|
| `main.py` | Daily report Job (`quantcore-report`): notifications → options capture → GEX from the persisted snapshot → capture health check → news staleness → fundamentals warming; exits 0 on NYSE-closed days. Does **not** render the HTML report | Active — prod Cloud Run Job |
| `news_job.py` | Daily news Job (`quantcore-news`): collection + FinBERT scoring, gap alarm | Active — prod Cloud Run Job |
| `fastMCPTest/options_analysis.py` | Covered-call/put/long screening — hybrid CLI + options-analysis MCP server (11 tools) | Active |
| `scripts/generate_portfolio_report.py` | HTML portfolio report with charts, optional S3 upload — run by the Pi, never alongside `main.py` | Active |
| `scripts/generate_watchlist_fundamentals_report.py` | Watchlist returns + fundamentals HTML report | Active |
| `scripts/import_portfolio.py`, `scripts/import_watchlist.py` | Per-owner CSV → `positions`; `watchlist.yaml` → global `watchlist` (both full-sync replace) | Active |
| `scripts/repair_watchlist_currency.py`, `scripts/backfill_news_sentiment.py`, `scripts/repair_ohlcv_misalignment.py` | Data repair / backfill | Operational utilities |
| `scripts/mint_prod_jwt.py` | Mint 90-day prod JWTs for MCP clients | Active — ops |
| `scripts/grant_quantui_iap_access.sh` | Grant a user QuantUI (IAP role + `owner_identities` row) | Active — ops |
| `scripts/flyway.sh`, `scripts/schema_check.py`, `scripts/check_schema_snapshot.py` | Migrations and schema-drift checks | Active — ops/CI |
| `scripts/cloudrun_services.py` | Deploy / drift-check every Cloud Run service from `deploy/cloudrun-services.toml` | Active — CI |
| `scripts/generate_keyproxy_keypair.py` | BYOK envelope keypair generation | Ops |
| `scripts/migrate_sqlite_to_postgres.py`, `scripts/migrate_to_unified_db.py` | One-shot historical migrations | Operational utilities |
| `experiments/INTC_bear_call_spread_monitor.py`, `WMT_bull_call_spread_monitor.py` | Open-position monitors (pickled state) | Active — keep |
| `experiments/HarvesterExperiment.py` | Harvest-ladder algorithm reference (DELL hardcoded) | Development reference |
| `html_summary.py`, `simple_text_summary.py` | Legacy report variants (old CSV format) | Superseded — candidates for deletion |

The rest of `scripts/` is CI and deployment plumbing (`ci_*.sh`/`.py`, `lock_deps.sh`, `audit_*.sh`, AR cleanup, migrate-Job and DB-role setup); see [`CLAUDE.md`](../CLAUDE.md) and `docs/operations/`.

---

## Database Structure

One unified **QuantCore** PostgreSQL database (**23 tables**, `psycopg2` via `QUANTCORE_DB_DSN`). Deployed databases are migrated by Flyway (`db/migrations/`, run by the `quantcore-migrate` Job before each roll-out); every entry point calls `ensure_schema()`, which creates the schema only where Flyway is absent and otherwise verifies it against `db/schema_snapshot.json`. Services connect as the DML-only `quantcore_app` role. All access goes through `quantcore/db.get_connection()`; writers are the repositories in `quantcore/repositories/`.

| Table Category | Tables | Primary Writers | Purpose |
|---|---|---|---|
| **Price Data** | `ohlcv`, `fetch_log` | `OhlcvRepository` | Shared OHLCV bar cache (daily + intraday intervals); yfinance fetch tracking |
| **Harvester** | `symbols`, `plan_templates`, `plan_instances`, `plan_rungs`, `alerts` | `HarvesterPlanDB` | Owner-scoped harvest plans, rungs, and alerts |
| **Positions & Watchlist** | `positions`, `lot_sales`, `watchlist` | `PortfolioRepository`, `WatchlistRepository` | Per-owner lots and recorded sales (with reasons); the global shared watchlist |
| **Identity & Settings** | `owner_identities`, `user_settings` | `OwnerIdentityRepository`, `UserSettingsRepository` | Principal → portfolio-owner mapping; per-owner chat model |
| **Options** | `options_snapshots`, `options_expirations`, `options_contracts`, `options_capture_claims`, `gamma_wall_history`, `gex_history`, `options_positions` | `OptionsStore`, `OptionsPositionStore` | Chain snapshots (ATM + full), daily-capture claims, gamma wall and signed-GEX history, active options positions |
| **News & Sentiment** | `news_articles`, `sentiment_snapshots` | `NewsStore`, `SentimentStore` | FinBERT-scored articles; aggregated sentiment summaries |
| **Fundamentals** | `fundamentals_history` | `FundamentalsRepository` | Append-only TTL cache (earnings_calendar, fundamental_score, revenue_growth, earnings_acceleration payloads) |
| **Arbitrage** | `arb_nav_snapshots` | `ArbitrageRepository` | Daily NAV inputs for `nav_vehicle` pairs |

Full table notes and the `ohlcv` writer rules: [`docs/architecture/data.md`](architecture/data.md).

### Remaining Database Gaps

1. **`options_positions` has no REST/WebUI/MCP surface.** It is read by the daily Job's options-position alerts and by the standalone INTC/WMT monitors, but positions can only be written directly through `OptionsPositionStore`. Either add REST CRUD + a UI positions panel, or document the store as the only write path.
2. **Microstructure signals are never persisted.** `get_short_interest` / `get_dark_pool` / `get_bid_ask_spread` compute in real time; no historical trend is possible. Add a snapshot table if trend analysis is wanted.
3. **News article data partially duplicated.** `sentiment_snapshots` re-embeds aggregate data derivable from `news_articles`; no FK cross-reference. Low priority.

---

## Summary: Key Insights

### What Works Well
- **Backend surface parity is done.** Every MCP tool has a REST twin (111 operations); MCP wrappers are one-call-deep HTTP adapters; adapters and services are cleanly layered per architectural-standard-v2.
- **Harvest ladder, portfolio lots, the merged Watchlist page (screener, sentiment, options refresh), cross-symbol fundamentals, arbitrage, and options analytics (IV rank, max pain, P/C history)** are all surfaced in the WebUI.
- **Sidekick + BYOK** gives UI users conversational access to a meaningful subset of the analysis stack with zero server-held credentials.
- **Ops maturity:** CI/CD to test, single-approval digest promotion to prod, CI-run Flyway migrations, IAP-gated UI, per-user JWTs, two daily Jobs with loud alarms.

### The One Big Gap: WebUI Coverage
The WebUI reaches 57 of 111 REST operations. It has kept pace with portfolio, watchlist, harvest, and arbitrage workflows but not with the per-ticker analysis synthesis tools. In priority order (full detail in the ⭐ section at top):

1. **Trade recommendation panel** — the most powerful synthesis endpoint, invisible in the UI (its stop-loss sibling shipped in #399).
2. **A Fundamentals tab on Security Detail** — composite score breakdown, revenue growth, earnings acceleration, score history.
3. **Microstructure section on the Signals tab** — 3 ready endpoints, zero UI.
4. **Options screener** — covered-call/put screening exists as CLI/MCP/REST but not UI.
5. **Chart overlays** — ATR bands, anchored VWAP, volume profile, VWAP history, relative strength; all one GET away.
6. **Options Analytics additions** — OI-change analysis, signed GEX profile, gamma-wall history.
7. **Workflow gaps** — news collect button, portfolio CSV import, the sale-history view (#235, hooks already written).

### Recommended Quick Wins
1. Add a **Recommendation card** to Security Detail (`GET .../recommendation`), beside the stop-loss card — highest value-to-effort ratio in the codebase.
2. Add a **Fundamentals tab** to Security Detail reusing the existing tab pattern (`.../fundamentals` fan-out already aggregates score/revenue/acceleration).
3. Append a **Microstructure section** to the existing Signals tab (`.../microstructure` returns all three signals in one call).
4. Wire the existing `useSales` / `useUpdateSale` hooks into a sale-history view (#235).
5. Delete `html_summary.py` / `simple_text_summary.py` (superseded legacy reports), and the unused `useOHLCV` hook (also referenced by `frontend/src/hooks/useSecurities.test.tsx`, so the test changes with it).

---

## Appendix: REST operations with no WebUI consumer

The 54 operations in `docs/openapi-surface.txt` that no production frontend component reaches
(2026-10-22; `stop-loss` left the list 2026-10-23). To refresh it, list each `apiRequest` path in `frontend/src/api/*.ts` plus
`chatStream.ts`, keep only the ones whose client method has a non-test caller in `hooks/`,
`components/` or `chat/`, and diff that set against the surface file. Three client methods look
like coverage and aren't: `portfolioApi.getLots`, `rungsApi.list` and `rungsApi.get` have no
callers, and nor do `useOHLCV`, `useSales` and `useUpdateSale`.

**Gaps — listed in the ⭐ section above (33):**
`GET /api/arbitrage/universe` · `GET /api/options/screen-watchlist` · `GET /api/portfolio/sales` ·
`PATCH /api/portfolio/sales/{sale_id}` · `POST /api/portfolio/import` ·
`POST /api/securities/fundamentals/scores-batch` · and under `/api/securities/{ticker}/`:
`anchored-vwap`, `atr-bands`, `volume-profile`, `vwap/history`, `relative-strength`,
`relative-strength/history`, `recommendation`, `microstructure`, `short-interest`,
`dark-pool`, `bid-ask-spread`, `fundamentals`, `fundamentals/score`, `fundamentals/revenue-growth`,
`fundamentals/earnings-acceleration`, `fundamentals/history`, `news/trend`, `POST news/collect`,
`options/chain`, `options/full-chain`, `options/contracts`, `options/screen`,
`options/gamma-wall-history`, `options/oi-change`, `options/gex-profile`, `options/unusual-calls`,
`options/delta-adjusted-oi`.

**The capability is in the UI, through an aggregate endpoint (18)** — these need no UI of their own:

| Standalone operation(s) | Reached instead through |
|---|---|
| `/{ticker}/rsi`, `/macd`, `/ohlcv`, `/price-summary` | `/{ticker}/technicals` |
| `/{ticker}/stochastic`, `/volume`, `/obv`, `/vwap`, `/candlestick`, `/higher-lows`, `/gaps` | `/{ticker}/signals/technical` |
| `/{ticker}/drawdown` | `/{ticker}/signals/risk` |
| `/{ticker}/news/sentiment` | `/api/securities/news/sentiment-summary` |
| `/{ticker}/earnings-calendar` | `/{ticker}/earnings` |
| `GET /api/portfolio/lots` | `GET /api/portfolio/symbols` (lots nested per symbol) |
| `GET /api/plans/{instance_id}/rungs`, `GET /api/rungs/{rung_id}` | `GET /api/plans/{instance_id}` (rungs included) |
| `GET /api/watchlist` | `GET /api/watchlist/mine` |

**Admin / debug — no UI intended (3):** `GET /api/health` · `GET /api/securities/news/symbols` ·
`GET /api/symbols`.

---

**Document Version:** 2.4
**Last Updated:** 2026-10-23 (#399: stop-loss synthesis surfaced on the Technical Analysis tab and as the Sidekick `stop_loss` card — 57 of 111 operations reached, 54 left, 33 gaps)
**Prior version:** 2.3, 2026-10-22 (full re-audit of WebUI coverage against `frontend/src/api` → hooks → components: 56 of 111 operations reached, the other 55 inventoried in an appendix. Reflects the Securities → Watchlist merge (#224: `GET /api/securities` retired, `GET /api/watchlist/mine` added, `/securities` redirects), the My Positions / Research nav menus, `get_symbol_sales` (#266), the 23-table schema under Flyway, the news Job, and the full notification list. Corrected stale WebUI claims: the arbitrage universe, `GET /api/symbols`, and the sales endpoints have no UI consumer; `LivePrice` lives on Plan Detail; delta exposure and dashboard stats are on the Harvester page)
**Prior version:** 2.2, 2026-08-14 (issue #208 — the Sidekick tool audit: added `arbitrage-server` and `portfolio-server`, corrected the tool count to 62 across 7, deleted the dual-registration claim)
**Maintained By:** John Funk
