# Stock Portfolio Manager — Capabilities Surface Matrix

This document is a comprehensive inventory of every user-facing capability in the StockPortfolioManager project, mapped to the surface(s) through which it can be accessed.

**Last Updated:** 2026-07-15  
**MCP Tools:** 49 | **REST Endpoints:** 85 routes (see `docs/openapi-surface.txt`) | **WebUI Pages:** 6 | **CLI Tools:** 2 | **Standalone Scripts:** 8

> **Refactor status (2026-06-17):** This inventory is the evidence base for [`proposals/architectural-standard-v2.md`](proposals/architectural-standard-v2.md). Phase 1 (extraction of all business logic into `quantcore/services/`, with MCP tools and REST routes reduced to one-call-deep adapters) is **complete** — see `proposals/phase1-migration-plan.md`. Phase 2 (FastAPI/Pydantic REST tier) is **complete** — see `proposals/phase2-fastapi-plan.md`: the Flask app (`api/app.py`) was rebuilt on FastAPI (`api/main.py`) preserving every route path and JSON shape, then retired; OpenAPI is published at `/docs`; and 12 previously MCP-only capabilities were exposed over REST (50 method-distinct operations across 45 paths). Phase 3 (AI gateway + GCP deployment) is **complete on the test project** — see `proposals/phase3-gateway-plan.md`: Step 1 closed the residual tool→endpoint gaps (32 thin routes + 3 param fixes, the surface is now 82 method+path operations), then all five MCP servers were inverted into thin HTTP gateway wrappers calling the REST tier through `mcp_gateway/rest_client.py` (Rule 6), JWT auth was added (`api/auth.py`, inert until configured), and the whole system was containerized and deployed to GCP Cloud Run (`quantcore-api` + 5 wrappers + a daily report Cloud Run Job) with CI/CD in `.github/workflows/deploy.yml`. Prod cutover (test→prod DSN) stays a supervised manual step.

---

## Overview: The Five Surfaces

The project exposes capabilities through five distinct surfaces:

| Surface | How to Access | Protocol | Live Server | Notes |
|---|---|---|---|---|
| **MCP Tool** | Claude Code / LLM integration via fastMCP | Model Context Protocol | `fastmcp run server.py` | Tools available to an LLM as callable functions |
| **REST Endpoint** | HTTP request to FastAPI tier | JSON over HTTP (OpenAPI at `/docs`) | `uvicorn api.main:app --port 5001` | Programmatic access; enables the WebUI and external integrations |
| **WebUI** | Browser at `http://localhost:5173` | React SPA | `npm run dev` (in `frontend/`) | Interactive dashboards, DataGrids, charts; Vite proxies `/api/*` to the FastAPI tier |
| **CLI Tool** | `python script.py --flags` | Command-line arguments (argparse) | N/A | Two full-featured tools: `collect_options.py` (currently broken — see below), `options_analysis.py` (hybrid: also runs as an MCP server) |
| **Standalone Script** | `python script.py` | Direct Python execution; hardcoded symbols | N/A | No arguments; experiments, legacy reports, prototypes; often with hardcoded tickers |

---

## Capability Summary by Surface

| Surface | Count | Examples |
|---|---|---|
| MCP Tools (5 servers) | 47 | `get_stock_price`, `price_vertical_spread`, `get_fundamental_score`, `get_news_sentiment`, `get_short_interest`, `analyze_options_watchlist` |
| REST Endpoints | 50 | `GET /api/securities/<ticker>/technicals`, `POST /api/plans`, `GET /api/securities/screen`, `GET /api/securities/<ticker>/fundamentals`, `GET /api/securities/<ticker>/microstructure` |
| WebUI Pages | 6 | Dashboard, Securities, Security Detail (6 tabs), Plans, Plan Detail, Symbols |
| CLI Tools | 2 | `collect_options.py` (EOD snapshot; broken), `options_analysis.py` (strategy analysis; hybrid CLI + MCP) |
| Standalone Scripts | 8 | Portfolio reports, watchlist fundamentals report, spread monitors (6 superseded experiments deleted in Phase 1 Step 10) |

**MCP tool count by server:** stock-price 25 · options-analysis 5 · company-fundamentals 12 · market-analysis 3 · news-sentiment 4. `get_option_contracts` and `price_vertical_spread` are exposed on both stock-price and options-analysis (shared implementation in `quantcore/services/options_contracts.py`).

**New since 2026-05-19:** `get_vwap_history`, `get_relative_strength_history`, `get_gamma_wall_history` (stock-price); `analyze_options_watchlist`, `analyze_options_symbol`, `mcp_health_check` (options-analysis — the `options_analysis.py` CLI is now also a FastMCP server); REST `GET /api/rungs/<rung_id>`; scripts `scripts/generate_watchlist_fundamentals_report.py`, `experiments/INTC_bear_call_spread_monitor.py`, `experiments/WMT_bull_call_spread_monitor.py`, `scripts/migrate_sqlite_to_postgres.py`.

---

## Complete Capability Inventory

Capabilities are organized by domain. A row with empty cells in the surface columns means that capability is **not accessible** through that surface.

### Domain: Price & Technical Analysis

| Capability | MCP Tool | REST Endpoint | WebUI | CLI | Standalone |
|---|---|---|---|---|---|
| Current stock price + Bollinger Bands | `get_stock_price` (stock_price) | `GET /api/securities/<ticker>/ohlcv` | Securities Detail → Price & MAs tab | — | — |
| RSI (Relative Strength Index, 14-period) | `get_rsi` (stock_price) | `GET /api/securities/<ticker>/technicals` | Securities Detail → Technical Analysis tab | — | — |
| MACD (12/26/9 with crossovers) | `get_macd` (stock_price) | `GET /api/securities/<ticker>/technicals` | Securities Detail → Technical Analysis tab | — | — |
| Stochastic Oscillator (%K/%D) | `get_stochastic` (stock_price) | `GET /api/securities/<ticker>/signals/technical` | Securities Detail → Technical Analysis tab | — | — |
| Moving averages (10/30/50/100/200-day) | via `get_stock_price` | `GET /api/securities/<ticker>/technicals` | Securities Detail → Price & MAs tab | — | `portfolio/metrics.py` |
| Volume climax / capitulation / OBV divergence | `get_volume_analysis` (stock_price) | `GET /api/securities/<ticker>/signals/technical` | Securities Detail → Signals tab | — | — |
| On-Balance Volume trend + divergence detection | `get_obv` (stock_price) | `GET /api/securities/<ticker>/signals/technical` | Securities Detail → Signals tab | — | — |
| VWAP + reclaim signal strength | `get_vwap` (stock_price) | `GET /api/securities/<ticker>/signals/technical` | Securities Detail → Signals tab | — | — |
| VWAP history (multi-day series) | `get_vwap_history` (stock_price) **NEW** | — | — | — | — |
| Candlestick patterns (hammer, doji, shooting star, gravestone) | `get_candlestick_patterns` (stock_price) | `GET /api/securities/<ticker>/signals/technical` | Securities Detail → Signals tab | — | — |
| Higher-low swing structure (reversal signal) | `get_higher_lows` (stock_price) | `GET /api/securities/<ticker>/signals/technical` | Securities Detail → Signals tab | — | — |
| Gap detection (gap-up/gap-down, fill status) | `get_gap_analysis` (stock_price) | `GET /api/securities/<ticker>/signals/technical` | Securities Detail → Signals tab | — | — |
| Relative strength vs SPY/QQQ/sector ETF | `get_relative_strength` (stock_price) | `GET /api/securities/<ticker>/relative-strength` | — | — | — |
| Relative strength history (trend over time) | `get_relative_strength_history` (stock_price) **NEW** | `GET /api/securities/<ticker>/relative-strength/history` | — | — | — |
| Historical drawdown (worst 1d/5d, trailing stop %) | `get_historical_drawdown` (stock_price) | `GET /api/securities/<ticker>/signals/risk` | Securities Detail → Signals tab | — | `experiments/MaxDrawDownAnalyzer.py` |
| ATR bands + chandelier trailing stop (volatility-calibrated) | `get_atr_bands` (stock_price) **NEW (issue #93)** | `GET /api/securities/<ticker>/atr-bands` | — | — | — |
| Anchored VWAP (auto-anchors: earnings, 52w H/L, gaps, swings) | `get_anchored_vwap` (stock_price) **NEW (issue #93)** | `GET /api/securities/<ticker>/anchored-vwap` | — | — | — |
| Composite trade recommendation (19 signals) | `get_trade_recommendation` (stock_price) | `GET /api/securities/<ticker>/recommendation?capital=` | — | — | — |
| Stop-loss synthesis (7 sub-analyses: BB, VWAP, MACD, RSI, DAOI, drawdown, short interest) | `get_stop_loss_analysis` (stock_price) | `GET /api/securities/<ticker>/stop-loss` | — | — | — |

**REST-exposed (Phase 2 Step 7):** `get_relative_strength`, `get_relative_strength_history`, `get_trade_recommendation`, and `get_stop_loss_analysis` now have thin REST endpoints (still no dedicated WebUI page).

---

### Domain: Options Analysis

| Capability | MCP Tool | REST Endpoint | WebUI | CLI | Standalone |
|---|---|---|---|---|---|
| Latest options snapshot (price, P/C ratio, nearest-expiry chains) | `get_stock_price` (embedded) | `GET /api/securities/<ticker>/options/latest` | Securities Detail → Options Chain tab | — | — |
| Full options chain (all strikes, all expirations; returns persistence status) | `get_full_options_chain` (stock_price) | `GET /api/securities/<ticker>/options/chain` | Securities Detail → Options Chain tab | `collect_options.py --symbols AAPL,MSFT` | — |
| Exact option contract lookup by expiry/strike | `get_option_contracts` (stock_price, options_analysis) | `GET /api/securities/<ticker>/options/contracts?expirations=&strikes=&kind=` | — | — | — |
| Vertical spread pricing (debit, max profit/loss, breakeven, liquidity) | `price_vertical_spread` (stock_price, options_analysis) | `POST /api/securities/<ticker>/options/vertical-spread` | — | — | — |
| Unusual call sweep detection (vol/OI, aggressive fill, OTM scoring) | `get_unusual_calls` (stock_price) | `GET /api/securities/<ticker>/signals/options-flow` | Securities Detail → Signals tab | — | — |
| Delta-Adjusted Open Interest (DAOI, gamma wall, delta flip) | `get_delta_adjusted_oi` (stock_price) | `GET /api/securities/<ticker>/signals/options-flow` | Securities Detail → Signals tab | — | — |
| Gamma wall history (daily snapshots, MM hedge bias trend) | `get_gamma_wall_history` (stock_price) **NEW** | — | — | — | — |
| IV Rank + IV Percentile (365-day) | — | `GET /api/securities/<ticker>/options/iv-rank` | Securities Detail → Options Analytics tab | — | — |
| Max pain + expected move per expiration | — | `GET /api/securities/<ticker>/options/analytics` | Securities Detail → Options Analytics tab | — | — |
| P/C ratio history (daily aggregated) | — | `GET /api/securities/<ticker>/options/history` | Securities Detail → Options Performance tab | — | — |
| Backfill historical P/C via Polygon.io | — | `POST /api/securities/<ticker>/options/history/backfill` | — | — | — |
| Bulk options snapshot refresh (all watchlist symbols) | — | `POST /api/securities/refresh-options-snapshots` | Securities page → bulk controls | — | — |
| Covered call / put candidate screening (full watchlist) | `analyze_options_watchlist` (options_analysis) **NEW** | — | — | `options_analysis.py --puts-budget 1000` | — |
| Covered call / put / long setup analysis (single symbol) | `analyze_options_symbol` (options_analysis) **NEW** | — | — | `options_analysis.py` | — |
| Long call setup analysis | `analyze_options_watchlist` (options_analysis) **NEW** | — | — | `options_analysis.py --puts-budget 1000` | — |
| MCP server health check (DB + watchlist readability) | `mcp_health_check` (options_analysis) **NEW** | — | — | — | — |
| Portfolio delta exposure (aggregated across positions) | — | `GET /api/portfolio/delta-exposure` | Dashboard → market maker delta table | — | — |

**Gaps:** `get_option_contracts` and `price_vertical_spread` are now REST-exposed (Phase 2 Step 7); `get_delta_adjusted_oi` standalone still has no REST endpoint, and none of these have a dedicated WebUI page yet.

---

### Domain: Fundamental Analysis

| Capability | MCP Tool | REST Endpoint | WebUI | CLI | Standalone |
|---|---|---|---|---|---|
| Earnings calendar (next date, days to earnings, risk level, historical avg move) | `get_earnings_calendar` (fundamentals) | `GET /api/securities/<ticker>/earnings` | Securities Detail (earnings column) | — | — |
| Composite fundamental score (-14 to +14, 7 metrics) | `get_fundamental_score` (fundamentals) | `GET /api/securities/<ticker>/fundamentals/score` | — | — | `experiments/CompositScoreExperiment.py` |
| Batch fundamental scoring (multiple symbols, ranked) | `get_fundamental_scores_batch` (fundamentals) | — | — | — | — |
| Full fundamental profile (earnings + score + revenue + acceleration + signal) | `get_full_fundamental_profile` (fundamentals) | `GET /api/securities/<ticker>/fundamentals` | — | — | — |
| Revenue growth (5 quarters, QoQ rates, CAGR, trajectory label) | `get_revenue_growth` (fundamentals) | `GET /api/securities/<ticker>/fundamentals/revenue-growth` | — | — | `experiments/RevenueGrowthExperiment.py`, `RevenueGrowthExperiment1.py` |
| Earnings acceleration (CAN SLIM "A" criterion, 5 quarters net income) | `get_earnings_acceleration` (fundamentals) | `GET /api/securities/<ticker>/fundamentals/earnings-acceleration` | — | — | `experiments/EarningsAccelerationExperiment.py` |
| Top-N stocks by fundamental score (from cache, per sector) | `get_top_fundamental_stocks` (fundamentals) | — | — | — | — |
| Upcoming earnings within N days (from cache) | `get_upcoming_earnings` (fundamentals) | — | — | — | — |
| Sector fundamental breakdown | `get_sector_fundamental_breakdown` (fundamentals) | — | — | — | — |
| Fundamental score change tracking (improving/deteriorating over 90 days) | `get_fundamental_score_changes` (fundamentals) | — | — | — | — |
| Historical fundamental score snapshots + trend | `get_fundamental_history` (fundamentals) | `GET /api/securities/<ticker>/fundamentals/history?data_type=&since_days=` | — | — | — |
| Cache statistics (symbols, date ranges, DB size) | `get_cache_stats` (fundamentals) | — | — | — | — |

**Critical Gap:** **All fundamental analysis is MCP-only.** Zero REST endpoints, zero WebUI panels. The React dashboard cannot display fundamental scores, revenue growth, or earnings acceleration. This is a significant feature gap if fundamentals-based screening is desired in the WebUI.

---

### Domain: News & Sentiment

| Capability | MCP Tool | REST Endpoint | WebUI | CLI | Standalone |
|---|---|---|---|---|---|
| Fetch news + FinBERT sentiment per article (ephemeral) | `get_news` (stock_price) | `GET /api/securities/<ticker>/news` | Securities Detail → news panel | — | — |
| Collect + store news articles in the database (with FinBERT scoring) | `collect_news` (news_sentiment) | — | — | `options_analysis.py` (opt-out: `--no-news`) | — |
| Aggregate sentiment signal (BULLISH/BEARISH/MIXED/NEUTRAL) | `get_news_sentiment` (news_sentiment) | `GET /api/securities/<ticker>/news` | Securities Detail → sentiment badge | — | — |
| Per-day sentiment trend (30-day breakdown, net score) | `get_sentiment_trend` (news_sentiment) | — | — | — | — |
| Bulk sentiment dashboard (all tracked securities, aggregated) | — | `GET /api/securities/news/sentiment-summary` | Securities page → FinBERT sentiment tab | — | — |
| List all symbols with articles in the database | `list_news_symbols` (news_sentiment) | — | — | — | — |
| RSS feed scraping + JSON export (Yahoo Finance) | — | — | — | — | `experiments/YahooNewsReader/RSSReaderExperiment.py` |

**Partial Duplication:** `get_news` (stock_price) fetches news ad-hoc; `collect_news` (news_sentiment) persists articles to a database for historical queries. Both are accessible via MCP, and both surface to REST.

---

### Domain: Market Microstructure

| Capability | MCP Tool | REST Endpoint | WebUI | CLI | Standalone |
|---|---|---|---|---|---|
| Short interest metrics (shares short, float %, days-to-cover) + squeeze potential (HIGH/MEDIUM/LOW) | `get_short_interest` (market_analysis) | `GET /api/securities/<ticker>/microstructure` (fan-out) | — | — | — |
| Dark pool / block trade activity (price-volume divergence proxy, accumulation/distribution) | `get_dark_pool` (market_analysis) | `GET /api/securities/<ticker>/microstructure` (fan-out) | — | — | — |
| Bid/ask spread signal (widening vs norm, fear gauge) | `get_bid_ask_spread` (market_analysis) | `GET /api/securities/<ticker>/microstructure` (fan-out) | — | — | — |

**Critical Gap:** All 3 market microstructure signals are **MCP-only, with zero REST or WebUI presence.** These are valuable indicators for institutional activity detection but are completely unavailable in the dashboard.

---

### Domain: Harvest Ladder (Systematic Profit-Taking Strategy)

| Capability | MCP Tool | REST Endpoint | WebUI | CLI | Standalone |
|---|---|---|---|---|---|
| Build volatility-based harvest ladder plan for a symbol | — | `POST /api/plans` | Plans page → create plan dialog | — | `experiments/HarvesterExperiment.py` (DELL hardcoded) |
| View / list all harvest plans (active + superseded) | — | `GET /api/plans` | Plans page (DataGrid with filters) | — | `experiments/HarvesterPlanStore.py` (MSFT demo) |
| View plan detail with rungs (price targets, expected harvest) | — | `GET /api/plans/<instance_id>` | Plan Detail page | — | — |
| Edit plan notes / metadata | — | `PATCH /api/plans/<instance_id>` | Plan Detail page → edit dialog | — | — |
| Delete / deactivate an active plan | — | `DELETE /api/plans/<instance_id>` | Plans page → delete action | — | — |
| Get rungs for a plan (price targets, status) | — | `GET /api/plans/<instance_id>/rungs` | Plan Detail page (rungs table) | — | — |
| Get single rung detail | — | `GET /api/rungs/<rung_id>` **NEW** | Plan Detail page | — | — |
| Mark rung as achieved (price hit the target) | — | `POST /api/rungs/<rung_id>/achieve` | Plan Detail page → achieve dialog | — | — |
| Record rung execution (shares sold, actual price) | — | `POST /api/rungs/<rung_id>/execute` | Plan Detail page → execute dialog | — | — |
| Scan all active plans for rung hits, fire alerts | — | — | — | — | `experiments/HarvesterPlanStore.py` (HarvesterController) |
| Discord notifications on rung trigger | — | — | — | — | `main.py` (called at report run time) |

**Status:** Fully accessible via REST + WebUI. Experiments are prototypes for development reference.

---

### Domain: Portfolio Management

| Capability | MCP Tool | REST Endpoint | WebUI | CLI | Standalone |
|---|---|---|---|---|---|
| View portfolio positions (holdings from `portfolio.csv`) | — | `GET /api/portfolio` | Dashboard, Securities page (filter) | — | `main.py`, `html_summary.py`, `simple_text_summary.py` |
| Add portfolio position | — | `POST /api/portfolio` (appends to CSV) | — | — | — |
| Remove portfolio position | — | `DELETE /api/portfolio/<ticker>` | — | — | — |
| Generate HTML portfolio report (charts, MAs, gain/loss, returns) | — | — | — | — | `main.py` (generates `portfolio_report.html`) |
| Upload report to S3 | — | — | — | — | `main.py` (if `BUCKET_NAME` env var set) |
| Legacy HTML report from `sample_stocks.csv` | — | — | — | — | `html_summary.py` (superseded) |
| Plain-text portfolio summary with matplotlib | — | — | — | — | `simple_text_summary.py` (superseded) |

**Note:** WebUI has no add/remove position forms; only REST endpoints support these operations.

---

### Domain: Watchlist Management

| Capability | MCP Tool | REST Endpoint | WebUI | CLI | Standalone |
|---|---|---|---|---|---|
| View watchlist (from `watchlist.yaml`) | — | `GET /api/watchlist` | Securities page (watchlist filter) | — | `main.py` |
| Add watchlist entry | — | `POST /api/watchlist` | — | — | — |
| Combined portfolio + watchlist view | — | `GET /api/securities` | Securities page (DataGrid, all symbols) | — | — |
| Technical screener (RSI, MA, Bollinger Bands, MACD, sentiment filters) | — | `GET /api/securities/screen?rsi_max=70&above_ma50=1&...` | Securities page → screener presets | — | — |
| Symbol lookup (name, sector, industry from yfinance) | — | `GET /api/securities/lookup?symbol=AAPL` | — | — | — |

---

### Domain: Notifications

| Capability | MCP Tool | REST Endpoint | WebUI | CLI | Standalone |
|---|---|---|---|---|---|
| Discord alert: moving average violation (30/50/100/200-day) | — | — | — | — | `main.py` + `notifier.py` |
| Discord alert: price below purchase price | — | — | — | — | `main.py` + `notifier.py` |
| Discord alert: harvest rung hit (price trigger) | — | — | — | — | `main.py` + `notifier.py` |

**Status:** All notifications are triggered by the standalone `main.py` script running on a schedule. No REST endpoints for notification management.

---

### Domain: Admin / Utility

| Capability | MCP Tool | REST Endpoint | WebUI | CLI | Standalone |
|---|---|---|---|---|---|
| API health check (DB connectivity) | — | `GET /api/health` | — | — | — |
| Dashboard stats (plan counts, rung counts, symbol counts) | — | `GET /api/dashboard/stats` | Dashboard page (stats cards) | — | — |
| Symbol list from Harvester DB | — | `GET /api/symbols` | Symbols page (DataGrid) | — | — |
| Latest price for a tracked symbol | — | `GET /api/symbols/<ticker>/price` | — | — | — |
| Unit tests (Money, Portfolio) | — | — | — | — | `test_money.py`, `test_stock_portfolio_manager.py` |

---

## Duplication & Redundancy Analysis

### Full Duplications (One Surface is Canonical, Others Are Legacy/Superseded)

| What | Canonical Surface | Duplicated In | Recommendation |
|---|---|---|---|
| Revenue growth analysis | `get_revenue_growth` (MCP) + caching | `experiments/RevenueGrowthExperiment.py`, `RevenueGrowthExperiment1.py` | Delete experiments; use MCP + REST wrapper if needed |
| Earnings acceleration (CAN SLIM "A") | `get_earnings_acceleration` (MCP) + caching | `experiments/EarningsAccelerationExperiment.py` | Delete experiment |
| Composite fundamental score | `get_fundamental_score` (MCP) + batch support + cache | `experiments/CompositScoreExperiment.py` | Delete experiment; expose MCP tool to REST/WebUI |
| Historical max drawdown | `get_historical_drawdown` (MCP) + trailing stop | `experiments/MaxDrawDownAnalyzer.py` | Delete experiment |
| Harvest ladder demo | REST `POST /api/plans` + WebUI Plans page | `experiments/HarvesterExperiment.py` (DELL only) | Delete experiment; use API for production |
| Harvester persistence demo | REST + WebUI plans CRUD | `experiments/HarvesterPlanStore.py` (MSFT only) | Delete experiment; use API for production |

### Partial Duplications (Same Data, Different Scope/Persistence)

| Capability | Surface A | Surface B | Difference | Issue |
|---|---|---|---|---|
| News + FinBERT sentiment | `get_news` (MCP, ephemeral) | `collect_news` + `get_news_sentiment` (MCP, persisted to database) | Ephemeral vs persisted; no trend queries on first | Two MCP tools doing similar things; unclear which to use |
| News fetch + persist | `collect_news` (MCP) | `experiments/YahooNewsReader/RSSReaderExperiment.py` | JSON file vs database; experiment is outdated | Experiment is legacy; use MCP tool instead |
| Options chain snapshot | `get_full_options_chain` (MCP, fetches + stores) | REST `GET /api/securities/<ticker>/options/chain` (reads stored snapshot) | MCP fetches & persists; REST reads from DB | Clean separation: MCP writes, REST reads — no duplication |
| Exact option contract/spread pricing | `get_option_contracts` + `price_vertical_spread` (MCP, cache-first/live-refresh) | Direct SQL or ad-hoc yfinance commands | MCP tools expose exact contracts and spread math | Use MCP tools; no direct database inspection needed |
| Options flow signals | `get_unusual_calls` + `get_delta_adjusted_oi` (MCP) | REST `/signals/options-flow` | MCP tools are standalone; REST aggregates both signals | REST is a convenience wrapper; no redundancy |
| Technical signals | MCP tools (RSI, MACD, VWAP, OBV, etc.) | REST `/signals/technical` | MCP tools are per-symbol; REST returns time series | REST is a convenience wrapper; no redundancy |
| Portfolio HTML report | `main.py` (reads `portfolio.csv`) | `html_summary.py` (reads `sample_stocks.csv`), `simple_text_summary.py` | Different input files; legacy versions use old CSV | Legacy scripts should be deleted |

---

### Experiments Using Hardcoded Symbols (Development Reference Only)

These standalone scripts hardcode specific tickers and are likely intended as development reference, not production utilities. They re-implement analytics now available via MCP tools.

| Script | Hardcoded Tickers | Superseded By | Status |
|---|---|---|---|
| `HarvesterExperiment.py` | DELL | `get_full_fundamental_profile` + REST `POST /api/plans` | Development reference |
| `HarvesterPlanStore.py` | MSFT | Full REST + WebUI plans CRUD | Development reference / integration test |
| `EarningsAccelerationExperiment.py` | NVDA, CAT, GLW, WDC, GOOGL, AAPL, QCOM, GEV, TER | `get_earnings_acceleration` (MCP) + `get_fundamental_scores_batch` | Delete; analytics now available in MCP |
| `RevenueGrowthExperiment.py` | Same hardcoded set | `get_revenue_growth` (MCP) | Delete; analytics now available in MCP |
| `RevenueGrowthExperiment1.py` | Same hardcoded set | `get_revenue_growth` (MCP) — variant | Delete; also duplicates the above experiment |
| `CompositScoreExperiment.py` | From `watchlist.yaml` or defaults | `get_fundamental_scores_batch` (MCP) | Delete; analytics now available in MCP + caching |
| `MaxDrawDownAnalyzer.py` | 15 hardcoded tickers from Jan 2025 | `get_historical_drawdown` (MCP) | Delete; analytics now available in MCP |
| `YahooNewsReader/RSSReaderExperiment.py` | N/A (RSS feed) | `collect_news` (MCP) | Delete; RSS collection now available in MCP with FinBERT scoring |

### Active Standalone Scripts (added since 2026-05-19)

| Script | Purpose | Status |
|---|---|---|
| `scripts/generate_watchlist_fundamentals_report.py` | HTML report of watchlist returns + fundamentals (outputs to `docs/analysis results/`) | Active; repointed at the services layer (`get_services()`) in Phase 1 |
| `experiments/INTC_bear_call_spread_monitor.py` | Monitors an open INTC bear call spread position (pickled state) | Active position monitor — keep |
| `experiments/WMT_bull_call_spread_monitor.py` | Monitors an open WMT bull call spread position (pickled state) | Active position monitor — keep |
| `scripts/migrate_sqlite_to_postgres.py` | One-shot legacy SQLite → PostgreSQL migration (16 tables) | Operational utility — keep |

---

### Capabilities Accessible Via Only One Surface (No Duplication, But Limited Discoverability)

These capabilities exist in only one surface, which can make them hard to find or use:

| Capability | Only Surface | Observation |
|---|---|---|
| Relative strength vs SPY/QQQ/sector | MCP only | Not available via REST or WebUI; LLM-accessible only |
| Composite trade recommendation (19 signals) | MCP only | Most powerful synthesis tool; locked out of REST/WebUI |
| Stop-loss synthesis (7 sub-analyses) | MCP only | Not exposed to REST or WebUI |
| Dark pool / block trade detection | MCP only | No REST or WebUI equivalent |
| Short interest + squeeze potential | MCP only | No REST or WebUI equivalent |
| Bid/ask spread signal | MCP only | No REST or WebUI equivalent |
| Exact contract lookup and vertical spread pricing | MCP only | No REST or WebUI equivalent; exposed on both stock-price and options-analysis MCP servers |
| Covered call / put / long setup analysis | CLI + MCP (`options_analysis.py`, `analyze_options_watchlist`/`analyze_options_symbol`) | Not exposed to REST or WebUI |
| Sentiment trend (30-day per-day breakdown) | MCP only | No REST or WebUI equivalent |
| List symbols with news in DB | MCP only | No REST equivalent |
| IV Rank + IV Percentile (365-day) | REST only | No MCP equivalent; WebUI-only |
| P/C ratio history backfill (Polygon.io) | REST only | No MCP or CLI equivalent |
| Sector fundamental breakdown | MCP only | No REST or WebUI equivalent |
| Fundamental score change tracking | MCP only | No REST or WebUI equivalent |
| Fundamental history snapshots | MCP only | No REST or WebUI equivalent |
| Portfolio position add/remove | REST only | No WebUI forms to support these operations |
| Watchlist entry add | REST only | No WebUI form to support this operation |

---

## Database Structure & Duplication Analysis

### Database Consolidation

The project uses **1 unified PostgreSQL database** (codename **QuantCore**, accessed via `psycopg2` and a connection string in `QUANTCORE_DB_DSN`), with its schema automatically created on startup via `quantcore/db.init_schema()` called from every application entry point (main.py, REST API, MCP servers). All database access uses a shared connection factory in `quantcore/db.py`:

| Table Category | Tables | Primary Writers | Purpose |
|---|---|---|---|
| **Price Data** | `ohlcv`, `fetch_log` | `fastMCPTest/ohlcv_cache.py` | Shared OHLCV bar cache for all MCP servers; supports daily/intraday intervals; tracks yfinance fetch times |
| **Harvester** | `symbols`, `plan_templates`, `positions`, `plan_instances`, `plan_rungs`, `alerts` | `experiments/HarvesterPlanStore.py` | Harvest plans, rungs, alerts, positions for the Harvester strategy; shares symbol registry with OHLCV |
| **Options** | `options_snapshots`, `options_expirations`, `options_contracts`, `gamma_wall_history`, `options_positions` | `fastMCPTest/options_store.py`, `options_position_store.py` | Options chain snapshots (ATM + full), active positions, gamma wall history |
| **News & Sentiment** | `news_articles`, `sentiment_snapshots` | `fastMCPTest/news_store.py`, `sentiment_store.py` | Individual news articles with FinBERT scores; aggregated sentiment summaries |
| **Fundamentals** | `fundamentals_history` | `fastMCPTest/fundamentals_cache.py` | Append-only cache for earnings/fundamentals data (TTL-based) |

**All modules** use `from quantcore.db import get_connection()` instead of managing individual database connections. **Schema initialization is automatic** — `init_schema()` creates all 16 tables on-demand against whatever PostgreSQL database `QUANTCORE_DB_DSN` points to (the database and its `quantcore` user must already exist).

### Unified Schema

All 16 tables live in the unified QuantCore PostgreSQL database. All store modules use the shared `quantcore/db.get_connection()` factory, which connects via `psycopg2` using the `QUANTCORE_DB_DSN` connection string.

#### Price Data (2 tables)
- **`ohlcv`** — OHLCV bars per (symbol, interval, ts): supports '1d', '1h', '30m', '15m', '1wk', '1mo'; status field ('OPEN', 'CLOSED', 'GAP', 'CORRECTED'); primary key (symbol, interval, ts)
- **`fetch_log`** — Last yfinance fetch time per (symbol, interval)

#### Harvester (6 tables)
- **`symbols`** — Ticker registry (ticker, name, exchange, currency, created_at)
- **`plan_templates`** — Algorithm templates (dynamic H, history window, n_iterations, volatility method, drift method)
- **`positions`** — Brokerage positions (entry_price, shares, cost_basis, account; FK → symbols)
- **`plan_instances`** — Computed harvest plans (price_asof, volatility, h_threshold, status ACTIVE/SUPERSEDED; FK → template, symbol, position)
- **`plan_rungs`** — Individual price targets (target_price, shares_to_sell, status PENDING/ACHIEVED/EXECUTED, actuals; FK → instance)
- **`alerts`** — One per pending rung (threshold_price, status, notification config, fired_price; FK → rung, symbol, instance)

#### Options (5 tables)
- **`options_snapshots`** — One row per (symbol, capture-time) with price and Bollinger Bands
- **`options_expirations`** — Per-expiry aggregate OI, volume, IV, put/call ratio (FK → snapshots)
- **`options_contracts`** — Individual strikes (kind, strike, bid/ask, IV, volume, OI, ITM; FK → expiration)
- **`gamma_wall_history`** — Daily snapshots of gamma wall strike and MM hedge bias
- **`options_positions`** — Active options positions (symbol, kind, strike, expiration, contracts, purchase_price, target, status)

#### News & Sentiment (2 tables)
- **`news_articles`** — Article per row: title, summary, publisher, url, published_at, source ('rss'/'yfinance'), sentiment label, sentiment_score, raw FinBERT probabilities; unique (symbol, url)
- **`sentiment_snapshots`** — Aggregated summary per (symbol, captured_at): article_count, positive/negative/neutral counts, overall_sentiment signal

#### Fundamentals (1 table)
- **`fundamentals_history`** — Append-only rows per (symbol, data_type, fetched_at); data_type is one of 'earnings_calendar', 'fundamental_score', 'revenue_growth', 'earnings_acceleration'; payload is JSON; TTL-based freshness

#### `collect_options.py` References Non-Existent Schema

`collect_options.py` imports `OptionsRepository`, `SnapshotService`, `MarketDataFetcher`, and `create_pricer` from `options_store.py` — but the current `options_store.py` only exports the `OptionsStore` class. These imported classes do not exist in the codebase.

The CLI also defaults its `--db` path to `options_store.db`, but all data now goes to the unified QuantCore PostgreSQL database.

**Problem:** `collect_options.py` **cannot be run** without import errors. It appears to reference a refactored version of `options_store.py` that no longer exists.

**Recommendation:** Fix imports in `collect_options.py` and update the `--db` default to use the unified QuantCore database via `quantcore.db.get_connection()`.

### OHLCV Merge Completed

**Status:** ✅ **RESOLVED** — The two separate OHLCV tables have been merged into a single `ohlcv` table in the unified QuantCore database with unified schema:
- **`symbol`** — TEXT (plain ticker, no FK)
- **`interval`** — TEXT DEFAULT '1d' (supports 1d, 1h, 30m, 15m, 1wk, 1mo)
- **`ts`** — INTEGER (Unix timestamp in seconds)
- **`open`, `high`, `low`, `close`, `volume`** — Standard OHLCV columns
- **`adj_close`** — REAL (NULL for intraday; from original `price_bars_daily`)
- **`status`** — TEXT DEFAULT 'CLOSED' (supports OPEN/CLOSED/GAP/CORRECTED for split detection)
- **`data_vendor`** — TEXT DEFAULT 'yfinance'
- **`ingested_at`** — INTEGER (Unix timestamp when row was written)
- **PRIMARY KEY** — (symbol, interval, ts)

Both the Harvester (`HarvesterPlanStore.py`) and MCP servers (`ohlcv_cache.py`) now write to the same table. Historical data has been migrated via `scripts/migrate_to_unified_db.py`.

### Remaining Critical Database Gaps

#### `options_positions` Table Has No REST, WebUI, or MCP Surface

The `options_positions` table (in the unified QuantCore database) stores active options positions (strike, expiration, contracts, purchase price, target price, status). There is **no REST endpoint, no WebUI page, and no MCP tool** that reads from or writes to this table. It can only be accessed by directly instantiating `OptionsPositionStore` in Python code.

**Recommendation:** Either add REST CRUD endpoints + WebUI form, or delete the table if it's not in use.

#### Harvester `positions` Table vs `portfolio.csv` — Two Parallel Representations

The Harvester's `positions` table stores brokerage positions (entry_price, shares, cost_basis). **Code scan (2026-06-12): the table is dead schema — no code reads from or writes to it.** All live position data flows through `portfolio.csv` (`main.py`, the report, `POST/DELETE /api/portfolio`, the Harvester scan). The table exists only in `init_schema()` DDL.

**Resolution (adopted in Phase 1 plan):** Migrate to the database as the source of truth with multi-owner support; `portfolio.csv` becomes a per-owner import format with full-sync/replace semantics (`scripts/import_portfolio.py --csv <file> --owner <name>`).

#### Fundamentals Data Has No REST or WebUI Surface

The `fundamentals_history` table in the unified QuantCore database is read **exclusively by MCP tools** in `company_fundamentals_server.py`. None of the 35+ REST endpoints in `api/app.py` query this table. The WebUI therefore cannot display fundamental scores, revenue growth, earnings acceleration, or sector breakdowns.

**Recommendation:** Add REST wrapper endpoints (e.g., `GET /api/securities/<ticker>/fundamentals`) to expose MCP tool results to the REST API and WebUI.

#### Short Interest, Dark Pool, Bid/Ask Data Is Never Persisted

`get_short_interest`, `get_dark_pool`, and `get_bid_ask_spread` (MCP: market_analysis_server) compute signals in real time and return results — nothing is stored. Historical trend data for these microstructure signals is not available.

**Recommendation:** Create a new table in one of the cache databases (e.g., `market_structure.db`) to store snapshots of these signals for historical trend analysis.

#### Sentiment Data Sources Unified in QuantCore

Both sentiment data sources now use the unified QuantCore database:
- `get_sentiment_trend` (MCP) queries `news_articles` table for per-day counts
- Aggregated summaries stored in `sentiment_snapshots` table

All writes and reads use the same unified database via `quantcore.db.get_connection()`.

**Status:** ✅ **RESOLVED** — Single consolidated source of truth.

---

## Summary: Key Insights

### What Works Well
- **REST + WebUI integration:** Technical signals, options data, portfolio/watchlist management are well-surfaced via both REST and WebUI.
- **OHLCV caching:** the `ohlcv` table in the unified QuantCore database is shared across all MCP servers, eliminating redundant yfinance calls.
- **Harvest ladder:** Fully accessible via REST + WebUI; no gaps.
- **Options data flow:** MCP tools fetch & store, exact-contract tools can use cache-first/live-refresh, and REST reads from the store.
- **Unified database:** All 16 tables consolidated into a single QuantCore PostgreSQL database with automatic schema initialization on startup.

### Completed Improvements
- ✅ **OHLCV duplication resolved:** Merged `price_bars_daily` + `ohlcv` into single unified `ohlcv` table (symbol, interval, ts) with status tracking and adj_close support.
- ✅ **Single database:** Consolidated 6 separate SQLite databases into a unified QuantCore PostgreSQL database with shared connection factory (`quantcore/db.get_connection()`).
- ✅ **Standardized PRAGMA settings:** All connections use WAL mode, NORMAL sync, FK ON, Row factory, 30s timeout.

### Remaining Critical Gaps
> **Phase 2 Step 7 update (2026-06-15):** Gaps 1–4 below are now **REST-exposed** — the FastAPI tier added thin endpoints for the 5 core fundamentals tools (`/fundamentals`, `/fundamentals/score`, `/fundamentals/revenue-growth`, `/fundamentals/earnings-acceleration`, `/fundamentals/history`), microstructure (`/microstructure`, fanning the 3 signals), `recommendation`, `stop-loss`, `relative-strength` (+`/history`), and exact contract/spread pricing (`/options/contracts`, `POST /options/vertical-spread`). What remains open is **WebUI** surfacing (no dashboard panels yet) and the few cache/batch fundamentals tools (`get_top_fundamental_stocks`, `get_upcoming_earnings`, `get_sector_fundamental_breakdown`, `get_fundamental_score_changes`, `get_fundamental_scores_batch`, `get_cache_stats`) that remain MCP-only by design.

1. ~~**Fundamentals are MCP-only.**~~ The 5 core fundamentals tools are now REST-exposed; the dashboard still lacks fundamental-analysis panels (WebUI gap).
2. ~~**Market microstructure is MCP-only.**~~ Short interest, dark pool, and bid/ask spread are now served by `GET /api/securities/<ticker>/microstructure`; WebUI panels remain.
3. ~~**Most powerful MCP tools are not surfaced to REST.**~~ `get_trade_recommendation`, `get_stop_loss_analysis`, and `get_relative_strength` now have REST endpoints (WebUI still pending).
4. ~~**Exact spread tools are MCP-only.**~~ `get_option_contracts` and `price_vertical_spread` are now REST-exposed (`/options/contracts`, `POST /options/vertical-spread`).
5. **News articles still duplicated.** `news_articles` (per-article) and `sentiment_snapshots` (JSON blobs) both store article data; no FK cross-reference.
6. **`collect_options.py` is broken.** Imports non-existent classes (`OptionsRepository`, `SnapshotService`, `MarketDataFetcher`, `create_pricer` — `options_store.py` exports only `OptionsStore`); references wrong database file.
7. **Position registry.** Harvester's `positions` table is dead schema (zero readers/writers); `portfolio.csv` is the only live registry. Phase 1 plan migrates positions to the DB with multi-owner support and CSV import.

### Recommended Quick Wins
1. Delete legacy experiments (`RevenueGrowthExperiment.py`, `EarningsAccelerationExperiment.py`, `MaxDrawDownAnalyzer.py`, etc.). They are fully superseded by MCP tools.
2. Wrap MCP tools with REST endpoints (e.g., `GET /api/securities/<ticker>/fundamentals`, `GET /api/securities/<ticker>/microstructure`). This surfaces them to the WebUI with minimal effort.
3. Fix `collect_options.py` imports or refactor `options_store.py` to match the expected interface.
4. Consolidate news articles: refactor `sentiment_snapshots` to store only aggregated counts + FK to articles, not re-embedded JSON.

---

**Document Version:** 1.1  
**Last Updated:** 2026-06-12 (code-scan refresh: +6 MCP tools, +1 REST endpoint, +4 scripts, positions-table finding)  
**Maintained By:** John Funk
