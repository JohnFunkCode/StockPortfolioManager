# Phase 1 Migration Plan — Services Layer Extraction

## Checkpoint Log

Update this table as part of every step-commit. Any dev machine can resume by reading the last DONE row.

| Step | Description | Status | Commit | Date | Notes |
|---|---|---|---|---|---|
| A | Capabilities matrix refresh | DONE | d14b885 | 2026-06-12 | Evidence base updated |
| -1 | Safety setup (prod backup, tag, test DB seed, DSN guard, flyway) | DONE | — | 2026-06-12 | Prod backup backups/quantcore_prod_2026-06-12.dump (5.6 MB) restore-verified into local scratch DB quantcore_restore_check AND used to reseed the test DB (exact prod parity: 16 tables, 176,998 options_contracts, 114,221 ohlcv). Test Cloud SQL instance quantcore-test-20260606 started; proxy on 5434. Flyway 12.8.1 installed; db/flyway.conf + db/migrations/ created; flyway info connects (baseline V1 on first migrate). db_safety guard hardened (also catches quantcore.db imported pre-override) and wired into test_options_contract_tools.py — NOTE: bare `python -m unittest discover` previously hit PROD via default DSN; guard now redirects/refuses. Worktree skipped per user — working in main checkout |
| 0 | Scaffolding (packages, repositories move, registry) | DONE | — | 2026-06-12 | quantcore/{gateways,repositories,analytics,services} created; 6 stores moved to repositories (OhlcvRepository/FundamentalsRepository OO facades added); re-export shims at old fastMCPTest paths; services/registry.py composition root (lazy lru_cache); notifier.py + test_options_contract_tools.py import from quantcore directly. Verified: 30 tests green vs test DB, 5 MCP servers import, Flask 38 routes, registry wires. Note: the file renames were accidentally staged into the Step -1 commit (613cdee) — that single commit is transiently broken; this commit restores coherence |
| 1 | Microstructure service | DONE | — | 2026-06-12 | Template step. quantcore/gateways/yfinance_gateway.py (YFinanceGateway: ticker_info w/ 15s timeout, fast_info, expirations, option_chain) + quantcore/services/microstructure.py (MicrostructureService: get_short_interest/get_dark_pool/get_bid_ask_spread, bodies verbatim); registry wires gateway+service; market_analysis_server.py now a thin adapter (3 one-line tools, docstrings verbatim); stock_price_server.py cross-import repointed at the service (lines ~3179/3195/3212). fastmcp 3.2.3 confirmed: @mcp.tool() returns the original fn, so the old cross-calls were plain delegation — repoint is exact. Parity vs /tmp/micro_parity_before.json (AAPL): all fields identical incl. live-quote ones. ValueError contract preserved. 30 tests green vs test DB; 5 MCP servers import; Flask 38 routes |
| 2 | Sentiment/News service | DONE | — | 2026-06-12 | quantcore/services/sentiment.py: NewsCollector moved wholesale from fastMCPTest/news_collector.py (RSS + yfinance fetch, FinBERT AutoModel loader) + SentimentService (collect_news/get_news_sentiment/get_sentiment_trend/list_news_symbols from news_sentiment_server; get_news from stock_price_server; get_security_news + get_sentiment_dashboard from REST routes). FinBERT duplicate KILLED: stock_price_server pipeline loader deleted; single loader in the service (scores byte-identical in parity check). YFinanceGateway.news() added. news_sentiment_server.py thin adapter (4 one-line tools); news_collector.py is a shim (options_analysis imports NewsCollector until Step 8); api/app.py /news + /news/sentiment-summary one call deep; stock_price_server internal news call repointed. Parity vs /tmp/sentiment_parity_before.json: only drift = snapshot captured_at (advances per call by design). 30 tests green; 5 servers import; Flask 38 routes |
| 3 | Fundamentals service | PENDING | — | — | |
| 4 | Prices/technicals service + analytics | PENDING | — | — | |
| 5 | Options service + PolygonGateway | PENDING | — | — | |
| 6 | Harvester + Portfolio (DB positions, multi-owner) | PENDING | — | — | |
| 7 | Recommendations service | PENDING | — | — | |
| 8 | Options screening split | PENDING | — | — | |
| 9 | Fix collect_options.py in place | PENDING | — | — | |
| 10 | Cleanup + audit | PENDING | — | — | |

---


## Context

The adopted architectural standard (`docs/proposals/architectural-standard-v2.md`) requires all business logic to move into a shared `quantcore/services/` layer (its §11 Phase 1), with MCP tools and REST routes becoming one-call-deep adapters. Today logic lives inline in surfaces: `fastMCPTest/stock_price_server.py` (3,592 lines), `api/app.py` (1,653 lines, 37 Flask routes), `fastMCPTest/options_analysis.py` (1,683-line CLI/MCP hybrid), with known duplications (`_bs_delta` ×2, `_compute_max_pain`, FinBERT loader ×2, `_chain_side_full` ×2) and superseded experiment scripts.

The evidence base, `docs/capabilities-matrix.md`, is out of date (2026-05-19) — code scan found **6 new MCP tools** (now 47 across 6 servers), **8 new REST endpoints** (now 37), a new standalone script, and other drift. So: **Task A** updates the matrix; **Task B** executes the Phase 1 extraction.

**User decisions:**
- Scope: **Phase 1 only** (Flask stays; FastAPI/Pydantic is Phase 2, design must not block it). Object-oriented services (classes + constructor injection).
- Delete the 8 superseded experiments in final cleanup; keep `HarvesterExperiment.py` and the INTC/WMT monitors.
- `collect_options.py`: **fix in place** — rewrite against the new services, keep its full CLI surface.
- Positions: **migrate to the DB `positions` table as source of truth**, multi-owner (named individuals). CSV becomes an **import format**: one CSV per owner, **full-sync/replace** semantics, `--owner` flag. Scope: **data model + API filter** (REST takes an `owner` param defaulting to John; `main.py` report/notifications and WebUI stay on John's portfolio for now).

---

## Task A — Update `docs/capabilities-matrix.md`

Single editing pass using the verified inventory (re-grep during implementation to confirm):

1. **Header counts:** MCP tools 42→**47** (6 servers incl. `options_analysis.py` as hybrid CLI+MCP), REST **37** endpoints, standalone scripts list refreshed. Bump version/date.
2. **New MCP tools to add:** `get_vwap_history`, `get_relative_strength_history`, `get_gamma_wall_history` (stock_price); `analyze_options_watchlist`, `analyze_options_symbol`, `mcp_health_check` (options_analysis — note the CLI is now also an MCP server).
3. **New REST endpoints to add** (8 flagged new vs. matrix, verify against `grep "@app.route" api/app.py`): incl. `GET /api/rungs/<id>`, `/api/securities/<ticker>/signals/*` rows updated, delta-exposure, screener filters, backfill.
4. **New scripts:** `scripts/generate_watchlist_fundamentals_report.py`, `experiments/INTC_bear_call_spread_monitor.py`, `experiments/WMT_bull_call_spread_monitor.py`; `scripts/migrate_sqlite_to_postgres.py`.
5. **Corrections:** `positions` table is dead schema (no readers/writers) — sharpen the dual-registry note; `collect_options.py` still broken; helper modules list (`options_contract_tools.py`, `news_collector.py`, `options_position_store.py`).
6. Add a short "Refactor status" note pointing at architectural-standard-v2 Phase 1.

Commit Task A separately before starting Task B (it's the evidence base).

---

## Task B — Extract the services layer

### Target package layout

```text
quantcore/
  db.py                          # unchanged factory; schema DDL edited for positions (see below)
  gateways/
    yfinance_gateway.py          # class YFinanceGateway (download, ticker_info, option_chain, news, financials)
    polygon_gateway.py           # class PolygonGateway (HTTP/pagination/retry from app.py backfill handler)
  repositories/                  # SQL only, no analytics — moved from fastMCPTest/
    ohlcv_repository.py          # class OhlcvRepository (wraps ohlcv_cache.py fns)
    options_repository.py        # OptionsStore moved as-is
    options_position_repository.py
    news_repository.py           # NewsStore
    sentiment_repository.py      # SentimentStore
    fundamentals_repository.py   # class wrapping fundamentals_cache.py fns
    harvester_repository.py      # HarvesterPlanDB + PlanBuildParams (from experiments/HarvesterPlanStore.py)
    portfolio_repository.py      # NEW: positions table CRUD, owner-scoped
  analytics/                     # PURE functions: DataFrame in, dict/float out. No I/O.
    indicators.py                # rsi, macd, stochastic, obv, vwap, bollinger, volume
    patterns.py                  # candlesticks, higher-lows, gaps, drawdown math
    options_math.py              # bs_delta, max_pain, expected_move (single home — kills both duplicates)
  services/
    registry.py                  # composition root: @lru_cache get_services() -> Services dataclass
    prices.py                    # PricesService
    options.py                   # OptionsService
    options_screening.py         # OptionsScreeningService (from options_analysis.py)
    fundamentals.py              # FundamentalsService
    sentiment.py                 # SentimentService (FinBERT lazy-loaded inside)
    microstructure.py            # MicrostructureService
    harvester.py                 # HarvesterService (HarvesterController behavior)
    portfolio.py                 # PortfolioService (multi-owner, CSV import)
    recommendations.py           # RecommendationsService (composes other services)
```

### Design rules

- **Constructor injection**: services take repositories/gateways (and, for `RecommendationsService`, other *service instances*); wiring only in `registry.py`. Service modules never import each other or the registry → no cycles.
- **Response dicts copied verbatim** from current tool/route bodies — behavioral parity is the contract. Keep the `{"error": ...}`-dict convention; Flask keeps `_JSONEncoder` until Phase 2.
- Adapters become exactly: `return get_services().prices.get_rsi(symbol, period, interval)` — docstrings kept verbatim on MCP tools (LLM-facing contract).
- During migration, old `fastMCPTest/` store modules become one-line re-export shims so unmigrated importers stay green; shims deleted in cleanup.
- `portfolio/yfinance_gateway.py` (domain layer) stays put for Phase 1 — consolidation noted as Phase 2 TODO; don't churn `main.py`'s report path.

### Service method inventory (capability merge)

| Service | Absorbs |
|---|---|
| **Prices** | 12 price/technical MCP tools + REST `/ohlcv`, `/technicals`, `/signals/technical`, `/securities/screen` (screener re-uses the same `analytics/indicators` fns — deletes app.py's third RSI/MACD copy) |
| **Options** | `get_full_options_chain`, contracts/spread (via `options_contract_tools.py`), `get_unusual_calls`, `get_delta_adjusted_oi`, `get_gamma_wall_history` + REST options latest/history/analytics/chain/iv-rank/options-flow/risk/delta-exposure/refresh-snapshots/backfill (Polygon). Deletes `_bs_delta_local`, `_compute_max_pain`, `_compute_expected_move`, dup `_chain_side_full` |
| **OptionsScreening** | `options_analysis.py` analytics: dataclasses, `fetch_security`, `score`, `build_put_trade`/`build_call_trade`, `_greedy_fill`, guardrails, `load_watchlist`, `is_us_listed`; tools `analyze_options_watchlist`/`analyze_options_symbol` |
| **Fundamentals** | all 12 fundamentals tools + REST `/earnings`; `scripts/generate_watchlist_fundamentals_report.py` repointed at it |
| **Sentiment** | `news_collector.py` + 4 news_sentiment tools + `stock_price_server.get_news` + REST `/news`, `/news/sentiment-summary`. Deletes the duplicate FinBERT loader |
| **Microstructure** | `get_short_interest`, `get_dark_pool`, `get_bid_ask_spread` |
| **Harvester** | `HarvesterController` scan/alerts + REST plans/rungs CRUD, dashboard stats, symbols |
| **Portfolio** | NEW DB-backed positions (see below) + watchlist CRUD + `OptionsPositionStore` wrap |
| **Recommendations** | `get_trade_recommendation` (19 signals), `get_stop_loss_analysis`, `get_relative_strength(+history)` — composes Prices/Options/Microstructure/Sentiment/Fundamentals services, replacing today's fragile cross-server imports of decorated tool fns |

### Plan persistence & checkpointing (resumability across machines)

- Commit this plan into the repo as **`docs/proposals/phase1-migration-plan.md`** as the very first Task B commit, with a **Checkpoint Log** table at the top: `| Step | Status | Commit | Date | Notes |`. Update the log as part of every step-commit.
- **Push the branch after every step-commit** — that, plus the checkpoint log, makes work resumable from any dev machine with just `git pull`.
- Tag the starting commit `pre-phase1` for a cheap rollback anchor; one-commit-per-step keeps any regression a single `git revert`.

### Database safety & Flyway migrations (applies to all of Task B)

- **All development and testing runs against `QUANTCORE_TEST_DB_DSN`** (defined in `.env`). The production database behind `QUANTCORE_DB_DSN` is **never touched** during the refactor — no schema changes, no writes, no test runs against it. Dev shells/scripts export the test DSN as `QUANTCORE_DB_DSN` for the process under test (the existing code reads one env var; the test DSN is swapped in at process level, not in code).
- **Schema changes ship as Flyway migrations**, not ad-hoc DDL: create `db/migrations/` with versioned scripts (e.g. `V2__positions_multi_owner.sql` for the positions columns/constraints below). Workflow: apply with `flyway migrate` against the **test** DB → develop/validate everything there → only after exit criteria pass, apply the same scripts to production (a deliberate, user-initiated step — not part of this refactor's automated work).
- `init_schema()` in `quantcore/db.py` remains the bootstrap for *fresh/empty* databases (dev/test convenience) and is updated to match the post-migration schema; Flyway is the change mechanism for *existing* databases. Add a `flyway.conf` (or document the CLI flags) pointing at `db/migrations/`; never store credentials in it — read DSN from env.
- One-time data steps (importing `portfolio.csv` as owner `john`) are written as repeatable scripts validated on the test DB first.
- **Accidental-prod-write guard:** test bootstrap and one-off scripts load `.env`, compare the effective DSN against the production `QUANTCORE_DB_DSN`, and **refuse to run** if they match (host+dbname check). This protects against a forgotten env override — the single worst failure mode of this refactor.
- **Seed the test DB from production's real shape:** `pg_dump --schema-only` from prod restored into the test DB (plus a small data sample where useful), so Flyway scripts and repository code are validated against the actual deployed schema, not just `init_schema()`'s idea of it.
- **Backup production before any work begins** (Step −1): full timestamped `pg_dump`, restore-verified into a scratch DB. Take a **fresh second backup immediately before Flyway promotion** to production. (Promotion itself remains a separate, user-initiated step.)
- **Side-effect suppression in test runs:** when exercising `main.py`/`notifier.py` against the test DB, unset or dummy `DISCORD_WEBHOOK_URL` and `BUCKET_NAME` so no real Discord alerts fire and nothing uploads to S3.
- **Protect the live tooling:** the user's MCP config and any scheduled `main.py` run point at this working tree. Do the refactor in a **git worktree** (separate directory, same repo) so the daily-driver checkout stays on a stable commit; the main checkout only advances when a step is verified.

### Portfolio → DB migration (multi-owner)

1. **Schema** (Flyway `V2__positions_multi_owner.sql`, mirrored in `init_schema()`): extend `positions` with `owner TEXT NOT NULL`, plus CSV-parity columns (`purchase_price`, `quantity`, `purchase_date`, `currency`, `sale_price`, `sale_date`, `name`); use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` guards (table exists empty in deployed DBs). Unique key `(owner, symbol, purchase_date)` — verify against real CSV rows (multiple lots per symbol exist?) during implementation. Update `scripts/migrate_sqlite_to_postgres.py` table list defensively.
2. **`PortfolioRepository`**: owner-scoped CRUD + `replace_owner_positions(owner, rows)` in one transaction (full-sync).
3. **`PortfolioService`**: `import_csv(path, owner)` (full replace of that owner's rows), `list_positions(owner="john")`, `add_position`, `remove_position`, `list_owners`, watchlist methods.
4. **CSV import surfaces**: `scripts/import_portfolio.py --csv portfolio.csv --owner john` (in-process, Rule 6) and `POST /api/portfolio/import?owner=` (multipart or path param — pick during implementation). One-time migration: import existing `portfolio.csv` as owner `john`.
5. **REST**: existing `GET/POST/DELETE /api/portfolio*` become DB-backed via the service with `?owner=` defaulting to `john` — response shapes preserved so the WebUI is unaffected.
6. **`main.py`/report**: `Portfolio` domain object built from `PortfolioService.list_positions("john")` instead of reading the CSV directly — output/report/notifications unchanged. Keep `portfolio.csv` in the repo as John's import file.

### Migration order (one commit per step, pushed + checkpoint-logged; `python -m unittest discover` + boot MCP servers + Flask after each, all against the test DB)

- **Step −1 — Safety setup.** **First action, before any other work: back up the production database** — full `pg_dump` of the DB behind `QUANTCORE_DB_DSN` to a timestamped file (e.g. `backups/quantcore_prod_<date>.dump`, gitignored), and **verify it restores** into a scratch database before proceeding. Then: tag `pre-phase1`; create the refactor worktree; commit `docs/proposals/phase1-migration-plan.md` with the checkpoint log; seed the test DB from the prod schema dump; add the prod-DSN guard helper; verify `flyway info` against the test DB.
- **Step 0 — Scaffolding.** Create packages; move stores to `repositories/` with shims at old paths; `registry.py` with repositories wired; fix imports in `test_options_contract_tools.py`, `notifier.py`.
- **Step 1 — Microstructure** (smallest, stateless) → template for the pattern. Immediately repoint `stock_price_server`'s cross-server imports at the service. ⚠ Verify early how decorated-tool cross-calls resolve in the installed fastmcp version.
- **Step 2 — Sentiment/News** (kills FinBERT duplicate; absorbs REST news routes).
- **Step 3 — Fundamentals** (12 tools + report script + `/earnings`).
- **Step 4 — Prices/technicals** — extract tool-by-tool from the 3,592-line file into `PricesService` + `analytics/`; then port REST technicals/screener onto the same functions.
- **Step 5 — Options** (+ `PolygonGateway`; deletes the bs-delta/max-pain duplicates; ThreadPoolExecutor fan-outs move inside service methods unchanged — async is Phase 2).
- **Step 6 — Harvester + Portfolio.** `HarvesterPlanDB`→repository, `HarvesterController`→`HarvesterService`; portfolio DB migration per above; update `main.py`/`notifier.py`/`api/app.py` callers; shim then delete `experiments/HarvesterPlanStore.py`.
- **Step 7 — Recommendations** (last — composes everything). `stock_price_server.py` shrinks to ~400 lines of thin decorated tools.
- **Step 8 — Options screening.** Split `options_analysis.py`: analytics → `OptionsScreeningService`; file keeps the FastMCP server (5 thin tools) + CLI `main()` + `print_*` presentation (printing is presentation, allowed in an adapter).
- **Step 9 — Fix `collect_options.py` in place**: rewrite its broken imports against `OptionsService` (snapshot collection = `refresh_snapshots`/`fetch_and_store_full_chain` paths), keep its argparse surface, drop the `--db` sqlite default.
- **Step 10 — Cleanup + audit.** Delete shims; delete the 8 superseded experiments (`CompositScoreExperiment.py`, `RevenueGrowthExperiment.py`, `RevenueGrowthExperiment1.py`, `EarningsAccelerationExperiment.py`, `MaxDrawDownAnalyzer.py`, `YahooNewsReader/`, `HarvesterPlanStore.py` shim — keep `HarvesterExperiment.py` + INTC/WMT monitors) and `fastMCPTest/server.py` sqrt demo; remove orphaned local `.db`/`.sqlite` files in `fastMCPTest/` if confirmed unused; grep-audit: no `yfinance`/`psycopg2`/repository imports outside `quantcore/` (adapters import only `quantcore.services`); update `CLAUDE.md` + matrix "Refactor status".

### Testing strategy (pragmatic)

1. **Pure-math unit tests** as indicators move into `analytics/` (synthetic DataFrames, exact values). For `bs_delta`/`max_pain`: write the test first and run it against *both* old copies once — that's the dedup parity check.
2. **Service tests vs test DB**, copying the existing `test_options_contract_tools.py` pattern (synthetic `ZZTEST`, `QUANTCORE_TEST_DB_DSN`): at minimum `test_harvester_service.py` and `test_portfolio_service.py` (new import/replace semantics deserve real coverage — owner isolation, full-sync replace, re-import idempotency).
3. **Smoke tests**: import each MCP server module and assert expected tool count via the FastMCP registry; Flask `test_client` on `/api/health` + one DB route per domain.
4. **One-time manual parity diffs** (old tool vs new service, 2–3 real symbols) at each step — throwaway, not committed (yfinance makes golden masters flaky).
5. For `RecommendationsService`, inject fake services returning canned dicts and assert scoring outcomes.

### Risks

- 3,600-line extraction → mitigated by per-tool commits and verbatim response-shape copies (no shape refactoring until Phase 2).
- fastmcp decorated cross-call semantics → verify in Step 1 before relying on interim import repoints.
- `OhlcvRepository` is shared by 6+ processes → wrap, don't rewrite; preserve `ON CONFLICT` upsert semantics.
- Schema change is additive only (`ADD COLUMN IF NOT EXISTS`); never `DROP TABLE`.
- Keep `get_services()` lazy (`lru_cache`) and FinBERT loading inside method bodies so MCP stdio startup doesn't hit client init timeouts.

### Exit criteria (per the standard §11 Phase 1)

- No analytics/thresholds/decisions in any `@mcp.tool()` body or Flask handler — every adapter is one service call deep.
- `python -m unittest discover` green; all 6 MCP servers boot and list their tools; Flask boots; WebUI works against unchanged REST shapes.
- Duplications gone: `_bs_delta` ×1, max-pain ×1, FinBERT ×1, `_chain_side_full` ×1.
- `collect_options.py` runs without import errors.
- Positions live in the DB with owner support; `scripts/import_portfolio.py --csv portfolio.csv --owner john` round-trips; report output from `main.py` unchanged.
- All of the above validated **entirely against the test database** (`QUANTCORE_TEST_DB_DSN`); production untouched. Flyway migration scripts in `db/migrations/` apply cleanly to a copy of the prod schema; promoting them to production is a separate, user-initiated step after sign-off.

### Critical files

- `fastMCPTest/stock_price_server.py` — bulk of extraction; response shapes + cross-tool call graph
- `api/app.py` — 37 routes; duplicates; screener; Polygon backfill; CSV CRUD to replace
- `fastMCPTest/options_analysis.py` — hybrid split
- `experiments/HarvesterPlanStore.py` — production Harvester classes to relocate (used by main.py, notifier.py, api/app.py)
- `fastMCPTest/ohlcv_cache.py` — shared data backbone; first repository wrap
- `quantcore/db.py` — schema DDL changes for positions
- `test_options_contract_tools.py` — the test pattern to replicate
- `docs/capabilities-matrix.md` — Task A target
