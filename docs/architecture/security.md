# Security map: OWASP Top 10 and LLM Top 10

This doc maps each OWASP category to the rule this repo already follows, the test or CI step that
enforces it, and the gaps. It is a **review lens and a gap list**, not a second copy of the rules:
each rule's home stays where it is (`CLAUDE.md`, the architecture docs, the standard), and this
page links to it.

Editions used: [OWASP Top 10:2025](https://owasp.org/Top10/) for the web tier and the
[OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/) for Sidekick and the
MCP tools. The second list matters as much as the first here: Sidekick feeds third-party text
(news headlines) to a model that can call tools.

Status vocabulary:

- **Enforced** — a test or CI step fails when the rule is broken.
- **Convention** — a written rule that only a reviewer checks.
- **Gap** — not covered; listed under [Gaps](#gaps) with an id.

Mapped against `main` at 2026-10-27. When you add a control or close a gap, update its row in the
same PR (see "Documentation is part of the change" in `CLAUDE.md`).

## Web tier — OWASP Top 10:2025

### A01 Broken Access Control

| Rule | Guard | Status |
|---|---|---|
| Owner-scoped routes resolve the owner with `Depends(require_owner)`; no `?owner=` parameter exists (`api/auth.py`). An identity with no `owner_identities` row gets a 403, never auto-provisioning. | `tests/test_auth.py`, `tests/test_identity_service.py` | Enforced |
| Owner isolation is in SQL: every Harvester statement, private helpers included, carries an `owner` predicate ([harvester.md](harvester.md)). | `tests/test_harvester_repository_db.py` (`test_wrong_owner_*`) | Enforced |
| Positions are per-owner the same way. | `tests/test_portfolio_repository.py` (`test_*_is_owner_scoped`) | Enforced |
| Sidekick's owner-scoped tools take the owner from the turn context; their schemas have no `owner` parameter, so the model can't pick whose data it sees (`OWNER_SCOPED_TOOLS` in `quantcore/services/chat.py`). | `tests/test_chat_tools.py`, `tests/test_chat_protocol.py` | Enforced |
| The UI is behind IAP; a user needs the audience entry, the IAP role **and** an `owner_identities` row ([quantui.md](quantui.md)). | manual grant script | Convention |
| The SQL-isolation rule is written down for Harvester only, not as a rule for every per-owner table. `options_positions` has no `owner` column. | — | **Gap G1** |

### A02 Security Misconfiguration

| Rule | Guard | Status |
|---|---|---|
| All Cloud Run config lives in `deploy/cloudrun-services.toml`; roll-outs use `--update-*`, never `--set-*` ([cloudrun-services.md](cloudrun-services.md)). | `tests/test_cloudrun_services.py`; `cloudrun_services.py check` drift report | Enforced |
| Public services (`allUsers`, by design, #297) return only identity and status from health checks: no OS, interpreter, internal URL or driver error. | `tests/test_mcp_seam.py` (`test_health_checks_disclose_no_host_details`) | Enforced |
| QuantUI sends a strict CSP (`default-src 'self'`, `frame-ancestors 'none'`, Trusted Types) from `frontend/server/server.mjs`. | none | Convention |
| `docker-compose.yml` publishes ports on `127.0.0.1` only; the local api runs `AUTH_DISABLED=1`. | none | Convention |
| CI cannot set IAM on an existing service; it refuses to create a public wrapper. | `scripts/cloudrun_services.py` | Enforced |

### A03 Software Supply Chain Failures

| Rule | Guard | Status |
|---|---|---|
| Every Python install uses a hash-pinned lock (`--require-hashes`); a stale lock fails CI. | `lock_deps.sh --check` in `gate`; `tests/test_dependency_locks.py` | Enforced |
| pip-audit (OSV) and npm audit of both frontend locks run on lock changes and daily; npm runtime findings fail at moderate+ ([pin-deps-plan.md](../proposals/pin-deps-plan.md)). | `dep-audit.yml`; `tests/test_audit_npm.py` | Enforced (goes red; not a roll-out gate, by design) |
| gitleaks scans every PR. | `lean-import` job | Enforced |
| FinBERT weights are baked into images at a pinned revision with pinned sha256s, never pulled from Hub `main` at runtime. | `scripts/bake_finbert.py`; revision-equality test | Enforced |
| Container builds go through Cloud Build with checked cache wiring. | `scripts/check_cloudbuild.py` | Enforced |

### A04 Cryptographic Failures

| Rule | Guard | Status |
|---|---|---|
| UI tokens are ES256-only, minted server-side per user; the browser never holds a bearer ([byok.md](byok.md)). | `tests/test_auth.py` | Enforced |
| BYOK: the user's Anthropic key travels in a single-use envelope that `quantcore-api` never decrypts; only the IAM-locked keyproxy can. | `tests/test_keyproxy_crypto.py`, `tests/test_keyproxy_auth.py` | Enforced |
| MCP clients use HS256 service tokens with a 90-day expiry; rotation is quarterly and manual. | none | Convention |

### A05 Injection

| Rule | Guard | Status |
|---|---|---|
| SQL values are always bound parameters. Dynamic SQL (`portfolio_repository._apply_lot_fields`, `harvester_repository`, `news_repository`) interpolates only code-built fragments: column allow-lists and placeholder lists. | none | Convention — **Gap G3** |
| The MCP→REST seam rejects path segments that reshape the request (`rest_client._path`). | `tests/test_mcp_seam.py` | Enforced |
| The LLM never writes SQL or provider calls; it reaches data only through curated tools with strict input schemas. | `tests/test_chat_tools.py` | Enforced |
| Outbound hosts are fixed in code (Polygon, open.er-api, Yahoo RSS); no route fetches a caller-supplied URL. | none | Convention |

### A06 Insecure Design

| Rule | Guard | Status |
|---|---|---|
| Thin adapters, exactly one service call deep; business rules live in one service layer ([architectural-standard-v2.md](../proposals/architectural-standard-v2.md) Rules 1–6). | `tests/test_mcp_tool_contracts.py`; review | Partly enforced |
| A plan exists only while the owner holds the shares. | `tests/test_harvester_service.py` | Enforced |
| Watchlist removal is UI-only: the MCP seam has no `delete` verb. | `mcp_gateway/rest_client.py` | Convention |
| The keyproxy rate-limits envelope redemption per `sub` (`SubRateLimiter` in `keyproxy/replay.py`, token bucket, `KEYPROXY_RATE_LIMIT_PER_MIN`, default 30), answering 429; a full bucket map refuses new subs (fail closed). | `tests/test_keyproxy_replay.py` (`TestSubRateLimiter`), `tests/test_keyproxy_service.py` (`test_third_redemption_in_a_minute_is_429`) | Enforced |
| The public REST tier (`quantcore-api`) and the MCP wrappers have no inbound rate limit; the only bounds are Cloud Run `max_instances` and concurrency. Nothing caps Sidekick turns or tokens per user, and they spend the user's own Anthropic key. The keyproxy limiter above caps redemptions per minute, not a spending budget. | — | **Gap G2** |

### A07 Authentication Failures

| Rule | Guard | Status |
|---|---|---|
| `api/auth.py`'s JWT check is the **single** enforcement point for the public services (#297). | `tests/test_auth.py` | Enforced |
| UI identity: `frontend/server/` verifies the IAP assertion before minting a token. | `frontend/server/auth.test.mjs`, `proxy.test.mjs` | Enforced |
| Auth is **inert until configured** locally (no verification key, or `AUTH_DISABLED`, resolves every call to `john`), but **fails closed on Cloud Run** (#459): when `K_SERVICE` is set, `create_app()` in `api/main.py` and `keyproxy/main.py` raises `AuthConfigurationError` without a key or with the disable flag, so the revision never takes traffic. | `tests/test_auth.py` (`CloudRunFailClosedTests`), `tests/test_keyproxy_auth.py` (`TestCloudRunFailClosed`) | Enforced |

### A08 Software or Data Integrity Failures

| Rule | Guard | Status |
|---|---|---|
| Model weights load only from hash-verified safetensors. `torch.load`, `pickle.load` and `trust_remote_code` are banned (#381). | `tests/test_no_pickle_model_loads.py` (AST scan) | Enforced |
| Prod promotes the digests preflight bound, never a moving tag. | `prod-rollout.yml` | Enforced |
| Schema has a single owner: Flyway, with three-file parity. | `tests/test_schema_parity.py`, `check_schema_snapshot.py` | Enforced |
| A refresh may fill `ohlcv.adj_close`, never blank it. | `tests/test_repositories_db.py` | Enforced |

### A09 Security Logging and Alerting Failures

| Rule | Guard | Status |
|---|---|---|
| Never-log policy: no keys, `Authorization` headers, envelopes, decrypted payloads, request bodies or DSNs in any log **or response** ([byok.md](byok.md)). | `tests/test_dsn_redaction.py`, keyproxy log assertions | Enforced |
| A missing `owner_identities` mapping logs a security event on the `quantcore.security` logger: `SECURITY unmapped_principal` (403) or `unmapped_principal_soft` (Sidekick), identity only. | `tests/test_auth.py` `UnmappedIdentityLogTests` | Enforced |
| Every 401 logs `SECURITY auth_rejected reason=missing\|expired\|invalid`, with the reason taken from the exception class. Only an expired token adds `identity=`, and only after a second verification with just `exp` skipped (PyJWT checks `exp` before `iss`/`aud`, so expiry proves only the signature); otherwise `identity=unknown`. The token never appears. | `tests/test_auth.py` `SecurityEventLogTests` | Enforced |
| Discord alarms re-read what actually landed in the database (bilge-pump pattern, [daily-jobs.md](daily-jobs.md)). | `tests/test_daily_job_capture.py`, `tests/test_empty_watchlist_alarm.py` | Enforced |
| A burst of 401/403s, an unmapped identity, or an expired token in use raises an email alert. Log-based metrics and alert policies are kept as code in `scripts/monitoring/` and applied by an operator with `scripts/auth_alerting.py`. | `tests/test_auth_alerting.py` (config, and the metric regexes run over the real log lines); `auth_alerting.py check` (live drift) | **Gap G6**: applied and proven on test (2026-10-28); not yet applied to prod |

### A10 Mishandling of Exceptional Conditions

| Rule | Guard | Status |
|---|---|---|
| A caught unexpected exception never reaches the caller as text: `route_internal_error`, class-only log lines, the same rule for the chat SSE error frame. | `tests/test_error_redaction.py` (also fails on an unexercised call site) | Enforced |
| Non-finite floats serialize as `null`; options tables store NULL, never NaN. | `tests/test_json_response.py` | Enforced |
| Schema drift fails startup (`SchemaDriftError`), so a bad revision never takes traffic. | `tests/test_schema_bootstrap.py` | Enforced |
| The daily job's late steps never raise; failures are made loud by alarms. | `tests/test_daily_job_capture.py` | Enforced |

## Sidekick and MCP — OWASP Top 10 for LLM Applications 2025

| Category | What applies here | Status |
|---|---|---|
| LLM01 Prompt Injection | News headlines from Yahoo RSS reach the model through `get_news_sentiment`. The system prompt in `quantcore/services/chat.py` does not tell the model to treat tool results as data, not instructions. Impact is bounded by LLM06 below. | **Gap G4** |
| LLM02 Sensitive Information Disclosure | Never-log/never-respond policy; owner-scoped tools take the owner from context. | Enforced |
| LLM03 Supply Chain | Pinned FinBERT revision + sha256; the chat model catalog is an allow-list (`quantcore/chat_models.py`). | Enforced |
| LLM04 Data and Model Poisoning | Only FinBERT is used, as-is from a pinned revision; no fine-tuning or training on user data. | N/A today |
| LLM05 Improper Output Handling | Model output becomes UI only through `show_component` directives validated against strict prop specs on **both** sides (`validate_directive`, `componentRegistry.tsx`) and rendered by `DirectiveRenderer`; no HTML from the model. Trusted Types backstops the DOM. | Enforced (`tests/test_chat_protocol.py`, `frontend/src/chat/componentRegistry.test.ts`) |
| LLM06 Excessive Agency | Every Sidekick tool is read-only. Of the MCP tools, `add_to_watchlist` and `collect_news` write. Any new write tool must follow the standard's §8 write gating, which isn't implemented yet. | Enforced for Sidekick; **Convention** for MCP |
| LLM07 System Prompt Leakage | The system prompt holds no secrets; it is in the repo. | N/A |
| LLM08 Vector and Embedding Weaknesses | No RAG or vector store. | N/A today |
| LLM09 Misinformation | The prompt bars numbers the model hasn't fetched and forbids outside data; refusals must name the rule. | Convention (prompt) |
| LLM10 Unbounded Consumption | Arbitrage tool caps mirror the service's caps (tested); but there is no per-user turn or token budget. | Partly — see **G2** |

## Gaps

Each gap has a backlog issue (label `security`). Close it by changing the row above and deleting it here.

- **G1 ([#460](https://github.com/JohnFunkCode/StockPortfolioManager/issues/460)) — Owner isolation isn't a general rule.** The SQL-predicate rule and its wrong-owner tests
  exist for Harvester and positions. `options_positions` has no `owner` column. It has a live
  consumer: the daily job's notification pass (`Notifier.check_options_alerts` in `notifier.py`,
  called from `calculate_and_send_notifications`, which `main.py` runs) expires past positions and reads every active position and
  pending alert, across all rows, posting to the one shared Discord webhook. The registry also
  constructs `OptionsPositionStore`. No REST route, MCP tool or Sidekick tool reads it, so no
  *user-facing* surface exposes it today, but that rests on the consumer list staying short, not on
  any owner predicate. Promote the rule to "every per-owner table", and give `options_positions` an
  owner (and the notifier an owner-aware read) before anything else consumes it.
- **G2 ([#461](https://github.com/JohnFunkCode/StockPortfolioManager/issues/461)) — No inbound rate limiting or per-user budget.** The standard's §8 says the REST tier rate
  limits every call. It doesn't. A valid token can drive unbounded Yahoo traffic through the public
  REST/MCP ingress (429s hurt every user) and unbounded Sidekick turns on the user's own key. The
  one limiter that exists, the keyproxy's per-`sub` redemption bucket, is Enforced (A06 row) and
  stays; it bounds envelope redemptions per minute, which is neither an ingress limit nor a
  per-user spending budget.
- **G3 ([#462](https://github.com/JohnFunkCode/StockPortfolioManager/issues/462)) — Bound parameters aren't enforced.** The dynamic SQL interpolates only code-built
  fragments today, but nothing would catch a value interpolated tomorrow. An AST test like the
  pickle ban could flag f-string/`%`/`.format` SQL whose fields aren't on an allow-list.
- **G4 ([#463](https://github.com/JohnFunkCode/StockPortfolioManager/issues/463)) — Indirect prompt injection via news text.** Add a system-prompt rule that tool results are
  data, never instructions, and a `CHAT_FAKE` test with a hostile headline. Keep Sidekick tools
  read-only (LLM06) — that is the containment that makes this a low-impact gap.
- **G6 ([#464](https://github.com/JohnFunkCode/StockPortfolioManager/issues/464)) — No alert on auth-failure spikes.** Public services log 401/403s, but nothing turns a burst
  into an alarm the way the daily job's Discord alarms do for data failures. The log lines the alert
  will count are in place (the two `Enforced` A09 rows above), and so are the metrics and
  policies as code (`scripts/monitoring/`). They are applied on test, where a synthetic burst
  and an expired token each raised an emailed incident (2026-10-28). What remains is applying
  them on prod (step 4 of
  [`auth-failure-alerting-plan.md`](../proposals/auth-failure-alerting-plan.md)). The row
  becomes Enforced after that.

## Reviewing a PR with this map

Open the categories that match what the PR touches; skip the rest. Ten checkboxes on every PR
become ticked-without-reading.

| The PR touches… | Check |
|---|---|
| a route, a service method taking `owner`, or a per-owner table | A01 — `require_owner`, owner predicate in every statement, a wrong-owner test |
| SQL | A05 — values bound; any interpolated fragment comes from code |
| auth, tokens, IAP, keyproxy | A04, A07 |
| a lock, a Dockerfile, model loading | A03, A08 |
| a new `except`, a log line, an error response | A09, A10 — class-only logs, `route_internal_error` |
| Cloud Run config, CSP, health checks | A02 |
| Sidekick prompt, chat tools, MCP tools, rendered directives | LLM01, LLM05, LLM06 |

A finding that maps to a category should name it (e.g. "A01: this statement has no owner
predicate") so the vocabulary stays shared.
