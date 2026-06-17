# Phase 3 Migration Plan — AI Gateway + Containers + GCP Deployment

## Checkpoint Log

Update this table as part of every step-commit. Any dev machine can resume by reading the last DONE row, running `git pull`, and continuing with the next step.

### Status: Phase 3 IN PROGRESS — Step 6 DONE (JWT auth + identity passthrough; the REST tier is now the single enforcement point, local/compose stay open; 2026-06-16)

Phase 3 of [`architectural-standard-v2.md`](architectural-standard-v2.md) §11 inverts the five MCP servers onto the REST tier (Rule 6 — `AI Agent → MCP wrapper → REST tier → Service`) and containerizes the whole system — first **locally via docker-compose** (the team's daily driver + GCP fallback), then on **GCP Cloud Run** with JWT auth, a scheduled report job, and CI/CD. The services layer (`quantcore/services/`), the `get_services()` composition root, and the FastAPI tier (`api/`) are reused **unchanged** — wrappers now reach them only through the HTTP front door.

**Locked decisions (with the user):**
1. **Wrapper conversion → hand-rewrite, preserve curation.** Keep every `@mcp.tool()` signature + rich LLM-facing docstring; swap only the body from `get_services().<svc>.<m>(...)` to a `rest_client` HTTP call. (`FastMCP.from_fastapi()` would discard the hand-written descriptions — §5.5 "never blanket-mirror".)
2. **Local containers reach the DB via the existing test Cloud SQL** through a `cloud-sql-proxy` compose service (`quantcore-test-20260606:us-central1:quantcore`). Production DB (`QUANTCORE_DB_DSN`) is untouched until the explicit prod-flip at the end.
3. **Auth → local no-auth, JWT for GCP.** Local preserves today's no-auth contract (`AUTH_DISABLED=1`); JWT + identity passthrough land in Step 6, before the Cloud Run rollout.
4. **Scope → all the way through live Cloud Run**, with the **local container stack (Step 5) as a hard mid-phase checkpoint**.

**Standing constraints:** all dev/testing + the first GCP rollout target the **test** instance; **git workflow has returned to the user** (I provide commit commands + the `Co-Authored-By: Claude Opus 4.8` footer, the user commits/pushes); **Rule 6 is sacred** — wrappers + front end go through REST, while `main.py`/cron call services **in-process** and never depend on the HTTP tier (anti-pattern 5).

**To work against the test DB on any machine** (proxy does not survive a reboot): `~/.local/bin/cloud-sql-proxy quantcore-test-20260606:us-central1:quantcore --port=5434 --quota-project=quantcore-test-20260606 &` (prod proxy is `:5433` — leave it), then prefix commands with:
`TEST_DSN="$(grep '^QUANTCORE_TEST_DB_DSN=' .env | cut -d= -f2-)" && env -u DISCORD_WEBHOOK_URL -u BUCKET_NAME -u BUCKET_KEY QUANTCORE_DB_DSN="$TEST_DSN" PYTHONPATH=. .venv/bin/python ...`

**Wrapper parity:** with `uvicorn api.main:app` running on 5001, a `fastmcp`/`httpx` client must get the same payload from each converted tool as the pre-conversion in-process tool (structural for yfinance/Polygon-backed tools).

| Step | Description | Status | Commit | Date | Notes |
|---|---|---|---|---|---|
| 0 | Scaffolding + HTTP toolkit | DONE | — | 2026-06-16 | `httpx>=0.27.0` added to requirements.txt (already installed 0.28.1). `mcp_gateway/` package NEW: `rest_client.py` — the single HTTP seam every wrapper uses: `get(path, *, auth_token=None, **params)` / `post(path, *, json=None, auth_token=None, **params)`, base URL from `QUANTCORE_REST_URL` (default `http://127.0.0.1:5001`), `QUANTCORE_REST_TIMEOUT` (default 60s), `None`-params dropped, repeatable list params pass through to httpx (matches the REST tier's `List[...] = Query(...)`), optional `Authorization: Bearer` (Step 6 wires identity passthrough), and `RestError(status_code, payload)` carrying the front door's parsed JSON error body so a wrapper can `except RestError: return e.payload` to mirror a service's error-dict return. No wrapper rewrites yet — all 5 servers still stdio/in-process and untouched. Tag `pre-phase3`. |
| 1 | Tool→endpoint coverage audit + close residual gaps | DONE | — | 2026-06-16 | All 49 tools across the 5 servers mapped (see coverage tables below). User chose **full capability parity — add every thin route, drop nothing**. **32 new routes + 3 param-gap fixes** added, each one service-call deep (Phase 2 pass-through idiom): prices.py +12 granular indicators; options.py +6 (full-chain/unusual/delta-OI/gamma-wall/2 screeners); fundamentals.py +7 (6 collection-level + earnings-calendar) + `api/schemas/fundamentals.py` (batch body); sentiment.py +4; microstructure.py +3 per-signal; recommendations.py params (stop-loss `max_expirations`, RS-history `rs_period`/`interval`). Route ordering: literal `news/symbols`, `fundamentals/*`, `vwap/history` declared before their `/{ticker}/...` templates. Security: options screen-watchlist does **not** expose `watchlist_path`. New offline test class `Phase3SurfaceGapRouteTest` (registration via OpenAPI spec + shadow-ordering via router decl order). Suite **97 green** (95 + 2). No wrapper rewrites yet. |
| 2 | Convert 3 small wrappers (market_analysis, company_fundamentals, news_sentiment) | DONE | — | 2026-06-16 | All 19 tools (3 + 12 + 4) inverted onto the REST tier: each `@mcp.tool()` keeps its signature + curated docstring verbatim; only the body swaps `get_services().<svc>.<m>(...)` → `rest_client.get/post(<mapped endpoint>)`. Imports: `from quantcore.services.registry import get_services` → `from mcp_gateway import rest_client`; added `import os`; dropped `init_schema` from `__main__`; transport → `mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", <default>)))` with `PORT`-overridable defaults market_analysis **6005**, company_fundamentals **6003**, news_sentiment **6004** (one image reusable per wrapper in Step 4). `collect_news` → `POST .../news/collect?score=`; sector-breakdown passes `sector=None` (dropped by rest_client). Header docstrings relabelled "HTTP gateway wrapper (Rule 6)". **Parity verified** vs a TEST-DB `uvicorn` (in-memory `fastmcp.Client`): listTools = 3/12/4 and one+ representative call each round-trips 200 through wrapper→api→service→Cloud SQL (short-interest/cache-stats/top/list-symbols). Suite **97 green**. |
| 3 | Convert 2 large wrappers (stock_price, options_analysis) | DONE | — | 2026-06-16 | Both large wrappers inverted onto the REST tier — **28 tools** total (stock_price **23**, options_analysis **5**). Each `@mcp.tool()` keeps its signature + curated docstring verbatim; only the body swaps `get_services().<svc>.<m>(...)` → `rest_client.get/post(<mapped endpoint>)`. **stock_price_server.py**: all 23 bodies → `rest_client`; header relabelled "HTTP gateway wrapper"; `import os`; import → `from mcp_gateway import rest_client`; `__main__` → `mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "6001")))`. Curation notes: `get_option_contracts` HTTP route accepts only `expirations/strikes/kind` — `max_snapshot_age_minutes`/`allow_live_fetch` kept in signature for stability, not forwarded; `price_vertical_spread` → `POST .../options/vertical-spread` with full 6-field JSON body. **options_analysis.py** (Rule 6 dual-purpose): only the 4 service-backed tools go HTTP (`analyze_options_watchlist` → `GET /api/options/screen-watchlist` *(watchlist_path not exposed)*, `analyze_options_symbol` → `GET .../options/screen`, `get_option_contracts` → `GET .../options/contracts`, `price_vertical_spread` → `POST .../options/vertical-spread`); `mcp_health_check` stays wrapper-local (no service call); the CLI `main()` + `print_*` helpers keep calling `get_services()` in-process (CLI bypasses HTTP, anti-pattern 5), so it imports **both** `rest_client` and `get_services`; `__main__` stays `main()` (CLI). **Parity verified** vs a TEST-DB `uvicorn` (in-memory `fastmcp.Client`): listTools = 23/5; `get_stock_price`/`get_rsi`/`analyze_options_symbol` round-trip live data through wrapper→api→service→Cloud SQL, `mcp_health_check` returns config locally. Grep-audit: stock_price has zero `get_services`/`init_schema`/`quantcore.*`/`yfinance` refs; options_analysis's `get_services` use is confined to CLI/`print_*`. Suite **97 green**. |
| 4 | Dockerfiles (api / mcp / report) | DONE | — | 2026-06-16 | Three multi-stage (builder venv → slim runtime), non-root (`appuser` uid 1000) Dockerfiles on `python:3.12-slim`. **Requirements split** to keep images lean: `requirements-base.txt` (everything except ML), `requirements-ml.txt` (`-r base` + `transformers`/`torch`, CPU-wheel `--extra-index-url`), `requirements.txt` → `-r requirements-ml.txt` (full local install unchanged). **ML-isolation refined for the inverted architecture:** post-Phase-3 FinBERT executes in the **service tier inside the API process** (the news_sentiment wrapper is now a thin HTTP client) and `main.py` never scores sentiment — so **only `Dockerfile.api` carries torch/transformers**; the wrapper + report images use the lean base set. `Dockerfile.api` → `uvicorn api.main:app` on `$PORT` (default 5001, Cloud-Run-injected PORT honoured). `Dockerfile.mcp` → **one image for all 5 wrappers**, server+port chosen at run time via `SERVER_MODULE`/`PORT` env consumed by new `mcp_gateway/serve.py` (imports the module's `mcp` object and runs `transport="http"` — uniform even for `options_analysis`, whose `__main__` is the CLI). `Dockerfile.report` → `python main.py` once-and-exit (Cloud Run Job; `MPLBACKEND=Agg`). `.dockerignore` excludes `.venv`/`.git`/`docs`/`frontend`/`tests`/`*.db`/`.env`/secrets. **Validated:** lean mcp image builds (25s deps); both `stock_price_server` and `options_analysis` boot in-container and serve streamable HTTP (`/mcp/` → 307). API image (torch) build deferred to the Step 5 compose-up. |
| 5 | docker-compose local stack | DONE | — | 2026-06-16 | **HARD CHECKPOINT — local stack live.** `docker-compose.yml` (project `quantcore-phase3`, one bridge net): `cloud-sql-proxy` (TEST instance `quantcore-test-20260606:us-central1:quantcore`, `--address 0.0.0.0 --port 5432`, host ADC mounted read-only) + `quantcore-api` (Dockerfile.api, `AUTH_DISABLED=1`, DSN → `cloud-sql-proxy:5432`, `:5001` published, python-`urllib` healthcheck on `/api/health` w/ 90s start_period for torch boot, `restart: on-failure` to self-heal the proxy race) + 5 wrappers (Dockerfile.mcp, `SERVER_MODULE`/`PORT` per server 6001–6005, `QUANTCORE_REST_URL=http://quantcore-api:5001`, `depends_on: quantcore-api: service_healthy`). **DB-safety seam:** `runUI-CONTAINERS.sh` launcher derives a git-ignored `.env.docker` (TEST conn name + creds parsed from `QUANTCORE_TEST_DB_DSN`) and runs `docker compose --env-file .env.docker`, which **suppresses** Compose's default `./.env` load — prod `QUANTCORE_DB_DSN`/`CLOUDSQL_CONNECTION_NAME` can never leak in; `.env.docker` added to `.gitignore`. **Verified end-to-end:** stack up, api `(healthy)` → `/api/health` `{"status":"ok","db_connected":true}`, `/api/portfolio?owner=john` 200, OpenAPI 77 paths; full `wrapper→api→service→Cloud SQL` path via `fastmcp.Client("http://localhost:6001/mcp")` → `get_stock_price(AAPL)` returns live data; all 5 wrappers serve listTools (23/5/12/4/3). Workflow documented in `readme.md` (new "Local Container Stack" section). **PAUSE before Step 6** for user to test the local Docker deployment. |
| 6 | JWT auth + identity passthrough | DONE | — | 2026-06-16 | **`api/auth.py` NEW** — `require_principal` FastAPI dependency: `HTTPBearer(auto_error=False)` → verify Bearer JWT via **PyJWT** (`jwt.decode`, algs from `QUANTCORE_JWT_ALGORITHMS` default `HS256`, optional `iss`/`aud`/`leeway`), returns a frozen `Principal` (`subject`/`email`/`roles`/`scopes`/`claims`/`token`/`owner`). **Activation model (chosen for back-compat):** auth is *inert until configured* — enforced only when a key is present (`QUANTCORE_JWT_SECRET` or `QUANTCORE_JWT_PUBLIC_KEY`) **and** `AUTH_DISABLED` is unset; otherwise the dependency returns `Principal.local()`. This preserves Phase 2's open local contract (bare `uvicorn`, compose, React dev server, `main.py` send no token and keep working) while Cloud Run turns enforcement **on** simply by injecting the secret; `AUTH_DISABLED=1` is an explicit force-off override (compose belt-and-suspenders). **`api/main.py`** applies `dependencies=[Depends(require_principal)]` to all 11 business routers via a loop; **`/api/health` (system router) stays open** for Cloud Run/compose liveness probes. **Identity passthrough** — `mcp_gateway/rest_client.py` `_incoming_auth_token()` lifts the inbound `Authorization` header off the active MCP request via FastMCP `get_http_headers()` and auto-forwards it to the REST tier (returns `None` for in-process/CLI/`main.py` callers, so those send no token — Rule 6 intact). `PyJWT>=2.8.0` → `requirements-base.txt` (REST tier decodes; wrappers only forward). **`test_auth.py` NEW** (10 cases, DB-free mini-app): disabled bypass, no-key-inactive, explicit-disable-override, valid token, missing/bad-sig/expired → 401, issuer/audience enforcement. Suite **107 green**. Compose rebuilt with Step 6 code; `AUTH_DISABLED=1` → stack stays open, `/api/health` 200, end-to-end tool call unchanged. **Deferred follow-ups:** wiring `Principal.owner` into the portfolio `?owner=` partition + per-user audit hook in the services layer (Rule 5); frontend token acquisition/attachment (Phase 2 sent none). |
| 7 | Artifact Registry + build/push | TODO | | | enable APIs; AR repo us-central1; build+push api/5 wrappers/report |
| 8 | Deploy Cloud Run services | TODO | | | api public+JWT+Cloud SQL connector; 5 wrappers internal ingress; health gating; listTools smoke |
| 9 | Report job + Cloud Scheduler | TODO | | | main.py as Cloud Run Job (in-process, not HTTP); daily trigger; one manual run |
| 10 | CI/CD + point clients + docs/audit | TODO | | | **Phase 3 exit** — deploy.yml; repoint .mcp.json; CLAUDE.md/matrix/standard → done; grep-audit; prod-flip last |

---

## Step 1 — Tool → endpoint coverage map

Every `@mcp.tool()` across the 5 servers, mapped to the REST endpoint its converted wrapper body (Steps 2–3) will call. Decision: **full capability parity — every service-backed tool gets a route; nothing dropped from curation.** Routes marked **NEW** were added in this step; the rest were ported in Phase 2 / Phase 2 Step 7. Every route is one `services().<svc>.<method>(...)` call deep and ships the service dict verbatim (`QuantCoreJSONResponse`).

### stock-price server → `prices` / `options` / `recommendations` / `sentiment` routers

| Tool | REST endpoint | Service method | Status |
|---|---|---|---|
| `get_stock_price` | `GET /api/securities/{t}/price-summary` | `prices.get_stock_price` | **NEW** |
| `get_rsi` | `GET /api/securities/{t}/rsi` | `prices.get_rsi` | **NEW** |
| `get_macd` | `GET /api/securities/{t}/macd` | `prices.get_macd` | **NEW** |
| `get_stochastic` | `GET /api/securities/{t}/stochastic` | `prices.get_stochastic` | **NEW** |
| `get_volume_analysis` | `GET /api/securities/{t}/volume` | `prices.get_volume_analysis` | **NEW** |
| `get_obv` | `GET /api/securities/{t}/obv` | `prices.get_obv` | **NEW** |
| `get_vwap` | `GET /api/securities/{t}/vwap` | `prices.get_vwap` | **NEW** |
| `get_vwap_history` | `GET /api/securities/{t}/vwap/history` | `prices.get_vwap_history` | **NEW** |
| `get_candlestick_patterns` | `GET /api/securities/{t}/candlestick` | `prices.get_candlestick_patterns` | **NEW** |
| `get_higher_lows` | `GET /api/securities/{t}/higher-lows` | `prices.get_higher_lows` | **NEW** |
| `get_gap_analysis` | `GET /api/securities/{t}/gaps` | `prices.get_gap_analysis` | **NEW** |
| `get_historical_drawdown` | `GET /api/securities/{t}/drawdown` | `prices.get_historical_drawdown` | **NEW** |
| `get_full_options_chain` | `GET /api/securities/{t}/options/full-chain` | `options.get_full_options_chain` | **NEW** |
| `get_unusual_calls` | `GET /api/securities/{t}/options/unusual-calls` | `options.get_unusual_calls` | **NEW** |
| `get_delta_adjusted_oi` | `GET /api/securities/{t}/options/delta-adjusted-oi` | `options.get_delta_adjusted_oi` | **NEW** |
| `get_gamma_wall_history` | `GET /api/securities/{t}/options/gamma-wall-history` | `options.get_gamma_wall_history` | **NEW** |
| `get_option_contracts` | `GET /api/securities/{t}/options/contracts` | `options.get_option_contracts` | Phase 2 §7 |
| `price_vertical_spread` | `POST /api/securities/{t}/options/vertical-spread` | `options.price_vertical_spread` | Phase 2 §7 |
| `get_news` | `GET /api/securities/{t}/news` | `sentiment.get_security_news` (= `get_news` + persist) | Phase 2 |
| `get_relative_strength` | `GET /api/securities/{t}/relative-strength` | `recommendations.get_relative_strength` | Phase 2 §7 |
| `get_relative_strength_history` | `GET /api/securities/{t}/relative-strength/history` | `recommendations.get_relative_strength_history` | Phase 2 §7 (**+`rs_period`/`interval`**) |
| `get_stop_loss_analysis` | `GET /api/securities/{t}/stop-loss` | `recommendations.get_stop_loss_analysis` | Phase 2 §7 (**+`max_expirations`**) |
| `get_trade_recommendation` | `GET /api/securities/{t}/recommendation` | `recommendations.get_trade_recommendation` | Phase 2 §7 |

### options-analysis server → `options` router

| Tool | REST endpoint | Service method | Status |
|---|---|---|---|
| `mcp_health_check` | — (local; no service call) | — | wrapper-local, no route |
| `analyze_options_symbol` | `GET /api/securities/{t}/options/screen` | `options_screening.analyze_symbol` | **NEW** |
| `analyze_options_watchlist` | `GET /api/options/screen-watchlist` | `options_screening.analyze_watchlist` | **NEW** (`watchlist_path` not exposed) |
| `get_option_contracts` | `GET /api/securities/{t}/options/contracts` | `options.get_option_contracts` | Phase 2 §7 |
| `price_vertical_spread` | `POST /api/securities/{t}/options/vertical-spread` | `options.price_vertical_spread` | Phase 2 §7 |

### company-fundamentals server → `fundamentals` router

| Tool | REST endpoint | Service method | Status |
|---|---|---|---|
| `get_full_fundamental_profile` | `GET /api/securities/{t}/fundamentals` | `fundamentals.get_full_fundamental_profile` | Phase 2 §7 |
| `get_fundamental_score` | `GET /api/securities/{t}/fundamentals/score` | `fundamentals.get_fundamental_score` | Phase 2 §7 |
| `get_revenue_growth` | `GET /api/securities/{t}/fundamentals/revenue-growth` | `fundamentals.get_revenue_growth` | Phase 2 §7 |
| `get_earnings_acceleration` | `GET /api/securities/{t}/fundamentals/earnings-acceleration` | `fundamentals.get_earnings_acceleration` | Phase 2 §7 |
| `get_fundamental_history` | `GET /api/securities/{t}/fundamentals/history` | `fundamentals.get_fundamental_history` | Phase 2 §7 |
| `get_earnings_calendar` | `GET /api/securities/{t}/earnings-calendar` | `fundamentals.get_earnings_calendar` | **NEW** |
| `get_fundamental_scores_batch` | `POST /api/securities/fundamentals/scores-batch` | `fundamentals.get_fundamental_scores_batch` | **NEW** |
| `get_top_fundamental_stocks` | `GET /api/securities/fundamentals/top` | `fundamentals.get_top_fundamental_stocks` | **NEW** |
| `get_upcoming_earnings` | `GET /api/securities/fundamentals/upcoming-earnings` | `fundamentals.get_upcoming_earnings` | **NEW** |
| `get_cache_stats` | `GET /api/securities/fundamentals/cache-stats` | `fundamentals.get_cache_stats` | **NEW** |
| `get_sector_fundamental_breakdown` | `GET /api/securities/fundamentals/sector-breakdown` | `fundamentals.get_sector_fundamental_breakdown` | **NEW** |
| `get_fundamental_score_changes` | `GET /api/securities/fundamentals/score-changes` | `fundamentals.get_fundamental_score_changes` | **NEW** |

(The pre-existing `GET /{t}/earnings` = `get_earnings_dates`, distinct from `earnings-calendar`.)

### news-sentiment server → `sentiment` router

| Tool | REST endpoint | Service method | Status |
|---|---|---|---|
| `collect_news` | `POST /api/securities/{t}/news/collect` | `sentiment.collect_news` | **NEW** |
| `get_news_sentiment` | `GET /api/securities/{t}/news/sentiment` | `sentiment.get_news_sentiment` | **NEW** |
| `get_sentiment_trend` | `GET /api/securities/{t}/news/trend` | `sentiment.get_sentiment_trend` | **NEW** |
| `list_news_symbols` | `GET /api/securities/news/symbols` | `sentiment.list_news_symbols` | **NEW** |

### market-analysis server → `microstructure` router

| Tool | REST endpoint | Service method | Status |
|---|---|---|---|
| `get_short_interest` | `GET /api/securities/{t}/short-interest` | `microstructure.get_short_interest` | **NEW** |
| `get_dark_pool` | `GET /api/securities/{t}/dark-pool` | `microstructure.get_dark_pool` | **NEW** |
| `get_bid_ask_spread` | `GET /api/securities/{t}/bid-ask-spread` | `microstructure.get_bid_ask_spread` | **NEW** |

(The Phase 2 §7 fan-out `GET /{t}/microstructure` stays for the dashboard view; the three per-signal routes above give the wrappers the full parameter sets — `dark_pool` `lookback`/`interval`, `bid_ask_spread` `lookback`.)

**Tally:** 32 new routes + 3 param-gap extensions; `mcp_health_check` stays wrapper-local. Every other tool was already covered by Phase 2. Coverage is now complete — Steps 2–3 can convert each wrapper body to a single `rest_client` call with no payload drift.

---

## Target runtime architecture

```text
            ┌─────────────── streamable HTTP (MCP) ───────────────┐
AI Agents ──┤ stock-price │ options │ fundamentals │ news │ market │  (5 wrapper containers)
            └───────────────────────┬──────────────────────────────┘
                                    │ internal HTTP + Authorization: Bearer
                                    ▼
React UI ──HTTPS+JWT──►  quantcore-api  (FastAPI, api/main.py)  ──► services ──► Cloud SQL
                                    ▲
report job (main.py) ──in-process──┘  (services directly; Cloud Run Job via Cloud Scheduler — NOT over HTTP)
```

- **Local:** docker-compose runs `cloud-sql-proxy` + `quantcore-api` + the 5 wrappers on one network; `QUANTCORE_REST_URL=http://quantcore-api:5001`; `AUTH_DISABLED=1`.
- **GCP:** `quantcore-api` = public ingress (JWT-protected); 5 wrappers = **internal ingress only**; report = Cloud Run Job on a Cloud Scheduler trigger.

## Exit criteria (standard §11 Phase 3)

No MCP server contains business logic or DB access; everything runs on GCP; local machines are optional dev environments, not infrastructure. **Plus the user's added gate:** a working local container stack (Step 5) exists as a fallback.
