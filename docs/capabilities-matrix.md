# Stock Portfolio Manager — Capabilities Surface Matrix

This document is a comprehensive inventory of every user-facing capability in the StockPortfolioManager project, mapped to the surface(s) through which it can be accessed.

**Last Updated:** 2026-07-19
**MCP Tools:** 49 | **REST Endpoints:** 87 operations (see `docs/openapi-surface.txt`) | **WebUI Pages:** 7 (+ Sidekick chat rail) | **CLI Tools:** 1 | **Standalone Scripts:** ~10

> **Refactor status:** Phases 1–3 of [`proposals/architectural-standard-v2.md`](proposals/architectural-standard-v2.md) are **complete**, prod rollout is **complete** (`quantcore-prod-20260606`, promoted by digest via `prod-rollout.yml`), QuantUI is deployed behind IAP in both projects, and **BYOK is live as of 2026-07-18** (browser key vault + Settings page + `keyproxy` credential-isolation service; per-user ES256 JWTs replaced the static UI→API token). Phase 3 Step 1 closed the residual MCP-tool→REST-endpoint gaps, so **every MCP tool now has a REST equivalent** — the surface-parity problem this document originally tracked has moved entirely to the WebUI layer. See the section immediately below.

---

## ⭐ Built But Not Yet Surfaced in the WebUI

**This is the headline finding of the 2026-07-19 refresh.** The REST tier now exposes 87 operations, but the React frontend calls only ~35 of them. Everything listed here is fully built, tested, and reachable over REST today — surfacing it is **frontend-only work** (no backend changes needed). Items are grouped by likely user value.

### Tier 1 — High-value analysis synthesis (flagship tools, invisible to UI users)

| Capability | REST endpoint (live today) | What the UI needs |
|---|---|---|
| **Composite trade recommendation** (19 signals → BUY/SELL/HOLD + confidence + suggested position size) | `GET /api/securities/{ticker}/recommendation?capital=` | A "Recommendation" tab or card on Security Detail — arguably the single most valuable unsurfaced feature |
| **Stop-loss synthesis** (7 sub-analyses: BB, VWAP, MACD, RSI, DAOI, drawdown, short interest → concrete stop price) | `GET /api/securities/{ticker}/stop-loss` | Panel on Security Detail (pairs naturally with the recommendation) |
| **Fundamentals profile** (composite score −14..+14, revenue growth/CAGR, earnings acceleration, signal) | `GET /api/securities/{ticker}/fundamentals` (+ `/score`, `/revenue-growth`, `/earnings-acceleration`, `/history`) | A "Fundamentals" tab on Security Detail. The UI currently shows earnings dates only |
| **Market microstructure panel** (short interest + squeeze potential, dark-pool proxy, bid/ask fear gauge) | `GET /api/securities/{ticker}/microstructure` (fan-out; also `/short-interest`, `/dark-pool`, `/bid-ask-spread` individually) | A "Microstructure" section on the Signals tab |
| **Relative strength vs SPY/QQQ/sector** (+ history series for trend) | `GET /api/securities/{ticker}/relative-strength` and `/relative-strength/history` | Chart overlay or Signals-tab card |

### Tier 2 — Options depth

| Capability | REST endpoint (live today) | What the UI needs |
|---|---|---|
| **Covered call / cash-secured put / long setup screening** (rule-based scoring, whole watchlist or one symbol) | `GET /api/options/screen-watchlist`, `GET /api/securities/{ticker}/options/screen` | An "Options Screener" page or Securities-page tab — today this exists only as CLI (`fastMCPTest/options_analysis.py`) and MCP |
| **Exact contract lookup + vertical-spread builder** | `GET .../options/contracts`, `POST .../options/vertical-spread` | A spread-builder form. Note: spread pricing *is* reachable via the Sidekick chat (`spread_payoff` card) but there is no direct UI form |
| **Full options chain browser** (all strikes × all expirations) | `GET .../options/chain`, `GET .../options/full-chain` | The Options Chain tab today renders only the latest snapshot's nearest expiries |
| **Gamma wall history** (daily gamma-wall strike + MM hedge-bias trend) | `GET .../options/gamma-wall-history` | Time-series chart on Options Analytics tab |
| **Standalone unusual-calls / delta-adjusted-OI detail** | `GET .../options/unusual-calls`, `.../options/delta-adjusted-oi` | Partially surfaced (aggregated) via the Signals tab's options-flow section; the detail views (per-contract sweeps, full DAOI ladder) are not |

### Tier 3 — Chart overlays, screeners, and workflow

| Capability | REST endpoint (live today) | What the UI needs |
|---|---|---|
| **ATR bands + chandelier trailing stop** (issue #93) | `GET /api/securities/{ticker}/atr-bands` | Overlay on the Price & MAs chart |
| **Anchored VWAP** (auto-anchors: earnings, 52w H/L, gaps, swings) (issue #93) | `GET /api/securities/{ticker}/anchored-vwap` | Overlay on the Price & MAs chart |
| **Cross-symbol fundamentals screeners** — top-N by score, upcoming earnings (N days), sector breakdown, 90-day score-change movers, batch scoring | `GET /api/securities/fundamentals/top`, `/upcoming-earnings`, `/sector-breakdown`, `/score-changes`, `POST /fundamentals/scores-batch` | Securities-page screener presets or a dashboard widget (e.g. "earnings this week") |
| **30-day sentiment trend** (per-day breakdown, net score) | `GET /api/securities/{ticker}/news/trend` | Sparkline/chart next to the existing sentiment badge |
| **On-demand news collection** (fetch + FinBERT + persist) | `POST /api/securities/{ticker}/news/collect` | A "refresh news" button (mirrors the existing options-snapshot refresh button) |
| **Portfolio CSV import** (full-sync per-owner) | `POST /api/portfolio/import` | Upload dialog on Dashboard/Securities — today it's the `scripts/import_portfolio.py` CLI only |
| **VWAP history** (multi-day series) | `GET /api/securities/{ticker}/vwap/history` | Chart overlay |

**Sidekick partially mitigates Tier 1:** the chat rail's tool vocabulary (`quantcore/services/chat_tools.py`) includes `get_fundamental_score`, `get_technical_signals`, `get_news_sentiment`, and `price_vertical_spread`, so a UI user *can* ask Sidekick for these. But chat is discoverable-on-demand, not glanceable — the dashboard/detail-page panels above remain the durable fix.

---

## Overview: The Six Surfaces

| Surface | How to Access | Protocol | Live Server | Notes |
|---|---|---|---|---|
| **MCP Tool** | Claude Code / AI clients via `.mcp.json` | Model Context Protocol | Prod Cloud Run wrappers (`https://quantcore-<svc>-…run.app/mcp`) or `*-local` compose stack | Thin HTTP gateway wrappers (`mcp_gateway/rest_client.py`) — every tool call becomes one REST request |
| **REST Endpoint** | HTTP to the FastAPI tier | JSON over HTTP (OpenAPI at `/docs`) | `uvicorn api.main:app --port 5001`; prod `quantcore-api` (JWT-enforced) | The canonical surface — all business logic reachable here; enables WebUI, MCP, and external integrations |
| **WebUI** | Browser (IAP-gated QuantUI on Cloud Run; `npm run dev` locally) | React SPA | Test/prod `quantui` services; Vite dev proxy locally | 7 pages: Dashboard, Securities, Security Detail (6 tabs), Plans, Plan Detail, Symbols, Settings |
| **Sidekick Chat** | Chat rail on every WebUI page | SSE via `POST /api/chat` → keyproxy → Anthropic (BYOK) | Same as WebUI | LLM with 7 data tools + `show_component` (renders `signals`, `live_price`, `price_chart`, `spread_payoff` cards inline) |
| **CLI Tool** | `python fastMCPTest/options_analysis.py --flags` | argparse | N/A | Hybrid: also runs as the options-analysis MCP server |
| **Standalone Script** | `python script.py` | Direct execution | `main.py` runs daily as a prod Cloud Run Job | Report generation, position monitors, operational utilities |

---

## Capability Summary by Surface

| Surface | Count | Examples |
|---|---|---|
| MCP Tools (5 servers) | 49 | `get_stock_price`, `price_vertical_spread`, `get_fundamental_score`, `get_news_sentiment`, `get_short_interest`, `analyze_options_watchlist` |
| REST Endpoints | 87 operations | `GET /api/securities/{ticker}/technicals`, `POST /api/plans`, `GET /api/securities/screen`, `GET /api/securities/{ticker}/recommendation`, `POST /api/chat` |
| WebUI Pages | 7 + chat rail | Dashboard, Securities, Security Detail (Price & MAs · Technical Analysis · Options Chain · Options Performance · Options Analytics · Signals), Plans, Plan Detail, Symbols, Settings (BYOK keys) |
| Sidekick chat tools | 7 + 4 components | `get_stock_price`, `get_technical_signals`, `get_rsi`, `get_macd`, `get_fundamental_score`, `get_news_sentiment`, `price_vertical_spread`; renders `signals`/`live_price`/`price_chart`/`spread_payoff` |
| CLI Tools | 1 | `fastMCPTest/options_analysis.py` (strategy screening; hybrid CLI + MCP server). `collect_options.py` has been **deleted** |
| Standalone Scripts | ~10 | `main.py` (daily report Job), watchlist fundamentals report, INTC/WMT spread monitors, `import_portfolio.py`, migration/ops scripts |

**MCP tool count by server:** stock-price 25 · company-fundamentals 12 · options-analysis 5 · news-sentiment 4 · market-analysis 3. `get_option_contracts` and `price_vertical_spread` appear on both stock-price and options-analysis (shared `quantcore/services/options_contracts.py`).

**New since the 2026-06-12 code scan:** the FastAPI tier grew from 50 → 87 operations (Phase 3 Step 1 added 32 thin routes mirroring every remaining MCP-only tool, plus fundamentals batch/screener routes); `POST /api/chat` + `GET/POST /api/keyproxy/*` (BYOK); `POST /api/portfolio/import` (DB-backed multi-owner positions); the Sidekick chat rail and Settings page in the UI; `collect_options.py` and the legacy experiments deleted.

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
| **⚠ Exact contract lookup by expiry/strike** | `get_option_contracts` (both servers) | `GET .../options/contracts` | **—** | — |
| Vertical spread pricing (debit, max P/L, breakeven, liquidity) | `price_vertical_spread` (both servers) | `POST .../options/vertical-spread` | Sidekick only (`spread_payoff` card, interactive strike repricing) — **no direct UI form** | — |
| Unusual call sweep detection | `get_unusual_calls` | `GET .../options/unusual-calls`, `/signals/options-flow` | Signals tab (aggregated) | — |
| Delta-Adjusted OI (DAOI, gamma wall, delta flip) | `get_delta_adjusted_oi` | `GET .../options/delta-adjusted-oi`, `/signals/options-flow` | Signals tab (aggregated) | — |
| **⚠ Gamma wall history (daily MM hedge-bias trend)** | `get_gamma_wall_history` | `GET .../options/gamma-wall-history` | **—** | — |
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
| **⚠ Top-N stocks by score (per sector, from cache)** | `get_top_fundamental_stocks` | `GET /api/securities/fundamentals/top` | **—** | — |
| **⚠ Upcoming earnings within N days** | `get_upcoming_earnings` | `GET /api/securities/fundamentals/upcoming-earnings` | **—** | — |
| **⚠ Sector fundamental breakdown** | `get_sector_fundamental_breakdown` | `GET /api/securities/fundamentals/sector-breakdown` | **—** | — |
| **⚠ Score change tracking (90-day movers)** | `get_fundamental_score_changes` | `GET /api/securities/fundamentals/score-changes` | **—** | — |
| Cache statistics | `get_cache_stats` | `GET /api/securities/fundamentals/cache-stats` | — (admin/debug) | — |

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

Positions are DB-backed with multi-owner support (`positions` table, `owner` column); `portfolio.csv` is now a per-owner import format.

| Capability | REST Endpoint | WebUI | CLI/Script |
|---|---|---|---|
| View portfolio positions (`?owner=`, default `john`) | `GET /api/portfolio` | Dashboard, Securities page | `main.py` (report) |
| Add / remove position | `POST /api/portfolio`, `DELETE /api/portfolio/{ticker}` | AddSecurityDialog / remove action | — |
| **⚠ Bulk CSV import (full-sync per owner)** | `POST /api/portfolio/import` | **—** | `scripts/import_portfolio.py --csv portfolio.csv --owner john` |
| View / add watchlist | `GET`/`POST /api/watchlist` | Securities page + AddSecurityDialog | — |
| Combined portfolio + watchlist view | `GET /api/securities` | Securities page DataGrid | — |
| Symbol lookup (name/sector/industry) | `GET /api/securities/lookup` | AddSecurityDialog autocomplete | — |
| HTML portfolio report (charts, gain/loss, S3 upload) | — | — | `main.py` (daily prod Cloud Run Job) |
| Watchlist returns + fundamentals HTML report | — | — | `scripts/generate_watchlist_fundamentals_report.py` |

---

### Domain: Sidekick Chat & BYOK (new since 2026-07)

| Capability | Surface | Notes |
|---|---|---|
| Conversational analysis (streaming, tool-using LLM) | `POST /api/chat` (SSE) → Sidekick rail on every page | Tools: stock price, technical signals, RSI, MACD, fundamental score, news sentiment, vertical-spread pricing |
| Inline rendered components in chat | `show_component` directive | `signals`, `live_price`, `price_chart`, `spread_payoff` (interactive: strike select/reprice backchannel) |
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

**Deleted since the last revision:** `collect_options.py` (was broken — imports referenced classes that no longer existed) and the six superseded analytics experiments (`RevenueGrowthExperiment*.py`, `EarningsAccelerationExperiment.py`, `CompositScoreExperiment.py`, `MaxDrawDownAnalyzer.py`, `YahooNewsReader/RSSReaderExperiment.py`, `HarvesterPlanStore.py`) — all functionality lives in `quantcore/` services now.

---

## Database Structure

One unified **QuantCore** PostgreSQL database (16 tables, `psycopg2` via `QUANTCORE_DB_DSN`; schema auto-created by `quantcore/db.init_schema()` from every entry point). Local dev and Cloud SQL (via Auth Proxy) are interchangeable. All access goes through `quantcore/db.get_connection()`; writers are the repositories in `quantcore/repositories/`.

| Table Category | Tables | Primary Writers | Purpose |
|---|---|---|---|
| **Price Data** | `ohlcv`, `fetch_log` | `OhlcvRepository` | Shared OHLCV bar cache (daily + intraday intervals); yfinance fetch tracking |
| **Harvester + Positions** | `symbols`, `plan_templates`, `positions`, `plan_instances`, `plan_rungs`, `alerts` | `HarvesterPlanDB`, `PortfolioRepository` | Harvest plans/rungs/alerts; **`positions` is now the live multi-owner position registry** (resolved: was dead schema pre-Phase-1) |
| **Options** | `options_snapshots`, `options_expirations`, `options_contracts`, `gamma_wall_history`, `options_positions` | `OptionsStore`, `OptionsPositionStore` | Chain snapshots (ATM + full), gamma wall history, active options positions |
| **News & Sentiment** | `news_articles`, `sentiment_snapshots` | `NewsStore`, `SentimentStore` | FinBERT-scored articles; aggregated sentiment summaries |
| **Fundamentals** | `fundamentals_history` | `FundamentalsRepository` | Append-only TTL cache (earnings_calendar, fundamental_score, revenue_growth, earnings_acceleration payloads) |

### Remaining Database Gaps

1. **`options_positions` has no REST/WebUI/MCP surface.** Only the standalone INTC/WMT monitors and direct `OptionsPositionStore` use touch it. Either add REST CRUD + a UI positions panel, or fold it into the monitors' pickled state and drop the table.
2. **Microstructure signals are never persisted.** `get_short_interest` / `get_dark_pool` / `get_bid_ask_spread` compute in real time; no historical trend is possible. Add a snapshot table if trend analysis is wanted.
3. **News article data partially duplicated.** `sentiment_snapshots` re-embeds aggregate data derivable from `news_articles`; no FK cross-reference. Low priority.

---

## Summary: Key Insights

### What Works Well
- **Backend surface parity is done.** Every MCP tool has a REST twin (87 operations); MCP wrappers are one-call-deep HTTP adapters; adapters and services are cleanly layered per architectural-standard-v2.
- **Harvest ladder, options analytics (IV rank, max pain, P/C history), technical signals, portfolio/watchlist CRUD, and the sentiment dashboard** are all fully surfaced in the WebUI.
- **Sidekick + BYOK** gives UI users conversational access to a meaningful subset of the analysis stack with zero server-held credentials.
- **Ops maturity:** CI/CD to test, gated digest-promotion to prod, IAP-gated UI, per-user JWTs, daily report Job.

### The One Big Gap: WebUI Coverage (~35 of 87 REST operations wired)
The React frontend has kept pace with options analytics and harvest workflows but not with the analysis synthesis tools. In priority order (full detail in the ⭐ section at top):

1. **Trade recommendation + stop-loss panels** — the two most powerful synthesis endpoints, invisible in the UI.
2. **A Fundamentals tab** — the entire 12-tool fundamentals domain surfaces only an earnings date.
3. **Microstructure section on the Signals tab** — 3 ready endpoints, zero UI.
4. **Options screener page** — covered-call/put screening exists as CLI/MCP/REST but not UI.
5. **Chart overlays** — ATR bands, anchored VWAP, relative strength; all one GET away.
6. **Workflow buttons** — news collect, portfolio CSV import.

### Recommended Quick Wins
1. Add a **Recommendation card** to Security Detail (`GET .../recommendation` + `.../stop-loss`) — highest value-to-effort ratio in the codebase.
2. Add a **Fundamentals tab** to Security Detail reusing the existing tab pattern (`.../fundamentals` fan-out already aggregates score/revenue/acceleration).
3. Append a **Microstructure section** to the existing Signals tab (`.../microstructure` returns all three signals in one call).
4. Add **"Upcoming earnings" and "Top fundamentals" widgets** to the Dashboard (`/fundamentals/upcoming-earnings`, `/fundamentals/top`).
5. Delete `html_summary.py` / `simple_text_summary.py` (superseded legacy reports).

---

**Document Version:** 2.0
**Last Updated:** 2026-07-19 (full code-scan refresh: REST 50→87 ops, Sidekick/BYOK surfaces added, UI-gap analysis promoted to headline section, deleted scripts/experiments purged)
**Maintained By:** John Funk
