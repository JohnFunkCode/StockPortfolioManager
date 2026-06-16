# Phase 3 Migration Plan — AI Gateway + Containers + GCP Deployment

## Checkpoint Log

Update this table as part of every step-commit. Any dev machine can resume by reading the last DONE row, running `git pull`, and continuing with the next step.

### Status: Phase 3 IN PROGRESS — Step 0 DONE (scaffolding + HTTP toolkit; 2026-06-16)

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
| 1 | Tool→endpoint coverage audit + close residual gaps | TODO | | | enumerate every @mcp.tool() in the 5 servers; map to REST endpoint via capabilities-matrix; add thin routes for service-backed gaps or drop from curation; commit coverage table |
| 2 | Convert 3 small wrappers (market_analysis, company_fundamentals, news_sentiment) | TODO | | | bodies → rest_client; streamable HTTP transport + distinct ports; drop get_services/init_schema; listTools + 1 call each vs uvicorn |
| 3 | Convert 2 large wrappers (stock_price, options_analysis) | TODO | | | same; options_analysis keeps CLI main()/print_* in-process (imports both rest_client + get_services) |
| 4 | Dockerfiles (api / mcp / report) | TODO | | | slim non-root multi-stage; .dockerignore; isolate torch/transformers to sentiment+report images |
| 5 | docker-compose local stack | TODO | | | **HARD CHECKPOINT** — proxy + api(AUTH_DISABLED) + 5 wrappers; /health, frontend, Claude client tool e2e; document workflow |
| 6 | JWT auth + identity passthrough | TODO | | | api/auth.py verify dep + AUTH_DISABLED bypass; rest_client forwards token; per-user audit hook in services |
| 7 | Artifact Registry + build/push | TODO | | | enable APIs; AR repo us-central1; build+push api/5 wrappers/report |
| 8 | Deploy Cloud Run services | TODO | | | api public+JWT+Cloud SQL connector; 5 wrappers internal ingress; health gating; listTools smoke |
| 9 | Report job + Cloud Scheduler | TODO | | | main.py as Cloud Run Job (in-process, not HTTP); daily trigger; one manual run |
| 10 | CI/CD + point clients + docs/audit | TODO | | | **Phase 3 exit** — deploy.yml; repoint .mcp.json; CLAUDE.md/matrix/standard → done; grep-audit; prod-flip last |

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
