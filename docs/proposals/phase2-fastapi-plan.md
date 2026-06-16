# Phase 2 Migration Plan — FastAPI REST Tier

## Checkpoint Log

Update this table as part of every step-commit. Any dev machine can resume by reading the last DONE row, running `git pull`, and continuing with the next step.

### Status: Phase 2 IN PROGRESS — Step 1 DONE (2026-06-15)

Phase 2 of [`architectural-standard-v2.md`](architectural-standard-v2.md) §11 rebuilds the REST tier on **FastAPI + Pydantic**, preserving every route path and JSON shape so the React front end (`frontend/`) runs unmodified on port 5001. The services layer (`quantcore/services/`) and the `get_services()` composition root are reused **unchanged**. Flask (`api/app.py`) stays runnable alongside the new FastAPI app until the Step 6 cutover.

**Locked decisions (with the user):**
1. **Auth → deferred to Phase 3.** Phase 2 preserves today's no-auth contract.
2. **Pydantic depth → Option A (pragmatic split).** Pydantic *request* models everywhere; Pydantic *response* models only for the ~15 frontend-consumed CRUD/dashboard endpoints pinned in `frontend/src/api/types.ts`; the ~25 heavy analytics endpoints **pass through verbatim** via `QuantCoreJSONResponse` (preserves `Decimal→float`, `datetime/date→ISO`), guaranteeing byte-for-byte parity on volatile dicts.
3. **Surface gaps → included** as a dedicated checkpoint (Step 7) after parity is proven.

**No schema changes in Phase 2** — no Flyway migration needed; production DB (`QUANTCORE_DB_DSN`) is never touched.

**To work against the test DB on any machine** (the proxy does not survive a reboot): `~/.local/bin/cloud-sql-proxy quantcore-test-20260606:us-central1:quantcore --port=5434 --quota-project=quantcore-test-20260606 &` (prod proxy is `:5433` — leave it), then prefix commands with:
`TEST_DSN="$(grep '^QUANTCORE_TEST_DB_DSN=' .env | cut -d= -f2-)" && env -u DISCORD_WEBHOOK_URL -u BUCKET_NAME -u BUCKET_KEY QUANTCORE_DB_DSN="$TEST_DSN" PYTHONPATH=. .venv/bin/python ...`

**Golden-master parity:** `scripts/capture_flask_golden.py` dumps the Flask JSON for the deterministic DB-backed endpoints into `tests/golden/flask/`. After each ported route group, diff FastAPI output against these fixtures. yfinance/Polygon-backed analytics routes are diffed *structurally* and *live* (keys/types), not value-exact.

| Step | Description | Status | Commit | Date | Notes |
|---|---|---|---|---|---|
| 0 | Scaffolding + deps | DONE | — | 2026-06-15 | requirements.txt += fastapi>=0.110 / uvicorn[standard]>=0.29 / pydantic>=2 (installed: fastapi 0.137.1; Flask/flask-cors retained until Step 6). `api/` package skeleton: `json_response.py` (QuantCoreJSONResponse — ports `_JSONEncoder`: Decimal→float, datetime/date→ISO, +numpy/pandas-NaN→null, ensure_ascii=False, allow_nan=False), `errors.py` (exception handlers → `{"error","message","status"}`: ValueError→400, DuplicateSymbolError→409, RuntimeError→422, RequestValidationError→422, HTTPException passthrough, Exception→500), `deps.py` (`services()` seam over get_services + `load_portfolio`/`load_watchlist` helpers ported from the Flask inline loaders), `main.py` (`create_app()`: init_schema() on startup, CORS allow-all, default_response_class=QuantCoreJSONResponse, register_exception_handlers; `__main__`→uvicorn 127.0.0.1:5001), empty `routers/` + `schemas/` packages. `scripts/capture_flask_golden.py` NEW → captured 8 golden fixtures into `tests/golden/flask/` (health/plans_all/plans_active/symbols/dashboard_stats/portfolio/watchlist/securities, all 200) against the test DB. VERIFIED: FastAPI app boots against test DB, `/openapi.json` 200 (0 routes yet — expected). No routes ported yet; Flask app untouched and still the live server. Tag `pre-phase2`. |
| 1 | Harvester CRUD (response-modeled) | DONE | — | 2026-06-15 | Ported the React app's core surface to FastAPI: `routers/system.py` (`/api/health`), `routers/plans.py` (GET/POST `/api/plans`, GET/PATCH/DELETE `/api/plans/{id}`, GET `/api/plans/{id}/rungs`), `routers/rungs.py` (GET `/api/rungs/{id}`, POST `/api/rungs/{id}/{achieve,execute}`), `routers/symbols.py` (GET `/api/symbols`, GET `/api/symbols/{ticker}/price`), `routers/dashboard.py` (GET `/api/dashboard/stats`) — all wired in `main.py`. `schemas/harvester.py` NEW: Pydantic **request** models (PlanBuildParamsRequest, CreatePlanRequest, UpdatePlanRequest, AchieveRungRequest, ExecuteRungRequest) + **response** models mirroring types.ts (Plan/Rung with `extra="allow"`, PlanWithRungs, *ListResponse, envelopes, DashboardStats, HealthResponse, acks). **Parity idiom:** routes declare `response_model=` for OpenAPI docs but handlers RETURN `QuantCoreJSONResponse` verbatim — FastAPI documents the schema yet ships bytes uncoerced (no key-stripping / Decimal-datetime miscast). Flask's exact per-route error bodies `{"error","status"}` (no "message") preserved via `route_error`; CreatePlanRequest.symbol kept Optional + manual "symbol is required" 400 check to match Flask's custom 400s over FastAPI's 422. `test_api_smoke.py` NEW (FastAPI TestClient, offline SQL plan-seed like test_harvester_service.py, ZZAPISMOKE/zz_api_smoke_template purge): health 200, plan round-trip GET-list/GET/rungs/single-rung/DELETE asserting the API key set EQUALS the service dict's (proves no stripping), dashboard shape, legacy-400 (missing symbol + invalid status filter), 404. VERIFIED: full suite **86 green** (80 + 6 smoke) against test DB; golden-master diff OK for all 5 Step-1 deterministic endpoints (health/plans_all/plans_active/symbols/dashboard_stats) — byte-parity with Flask. Flask app untouched and still live. |
| 2 | Portfolio / watchlist / securities | TODO | | | /api/portfolio* (UploadFile import, ?owner=), /api/watchlist*, /api/securities, /lookup |
| 3 | Prices & screener (pass-through) | TODO | | | /ohlcv, /technicals, /signals/technical, /signals/risk, /screen |
| 4 | Options (pass-through) | TODO | | | options latest/history/analytics/chain/iv-rank, options-flow, delta-exposure, backfill+refresh (202) |
| 5 | Fundamentals + Sentiment | TODO | | | /earnings, /news, /news/sentiment-summary |
| 6 | Cut over + retire Flask | TODO | | | repoint runUI-MAC.sh/docs → uvicorn api.main:app:5001; delete api/app.py; drop Flask/flask-cors; frontend click-through. **Phase 2 exit-criteria checkpoint.** |
| 7 | Close surface gaps (NEW endpoints) | TODO | | | ~11 thin endpoints: fundamentals/microstructure/recommendation/stop-loss/relative-strength/contracts/vertical-spread |
| 8 | Docs + audit | TODO | | | CLAUDE.md/capabilities-matrix/standard roadmap → Phase 2 done; grep-audit api/ one-call-deep, no yfinance/psycopg2/repo imports |

---

## Target structure

```text
api/
  __init__.py
  main.py            # create_app(): FastAPI app, CORS, init_schema() on startup,
                     #   register_exception_handlers, default_response_class; __main__ -> uvicorn 127.0.0.1:5001
  json_response.py   # QuantCoreJSONResponse: Decimal->float, datetime/date->ISO, numpy/NaN->null (replaces _JSONEncoder)
  errors.py          # exception handlers -> {"error","message","status"}
  deps.py            # services() injection seam; load_portfolio / load_watchlist helpers
  schemas/           # Pydantic request + (frontend-pinned) response models
  routers/           # one APIRouter per route group, each one service call deep
```

`quantcore/services/registry.py` and `get_services()` are reused unchanged — the composition root does not move.

## New endpoints (Step 7) — all over ready services

| Endpoint | Service method |
|---|---|
| `GET /api/securities/{t}/fundamentals` | `fundamentals.get_full_fundamental_profile` |
| `GET /api/securities/{t}/fundamentals/score` | `fundamentals.get_fundamental_score` |
| `GET /api/securities/{t}/fundamentals/revenue-growth` | `fundamentals.get_revenue_growth` |
| `GET /api/securities/{t}/fundamentals/earnings-acceleration` | `fundamentals.get_earnings_acceleration` |
| `GET /api/securities/{t}/fundamentals/history?data_type=&since_days=` | `fundamentals.get_fundamental_history` |
| `GET /api/securities/{t}/microstructure` | fans out `microstructure.{get_short_interest,get_dark_pool,get_bid_ask_spread}` |
| `GET /api/securities/{t}/recommendation?capital=` | `recommendations.get_trade_recommendation` |
| `GET /api/securities/{t}/stop-loss` | `recommendations.get_stop_loss_analysis` |
| `GET /api/securities/{t}/relative-strength` | `recommendations.get_relative_strength` |
| `GET /api/securities/{t}/relative-strength/history?days=` | `recommendations.get_relative_strength_history` |
| `GET /api/securities/{t}/options/contracts?expiration=&strike_min=&strike_max=&kind=` | `options.get_option_contracts` |
| `POST /api/securities/{t}/options/vertical-spread` | `options.price_vertical_spread` |

## Exit criteria (standard §11 Phase 2)

Front end runs unmodified against FastAPI on 5001; OpenAPI published at `/docs`; surface gaps exposed; Flask app (`api/app.py`) deleted; production DB untouched.
