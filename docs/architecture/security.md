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
- **Shadow** — the control is built and tested but only logs what it would refuse; it becomes
  Enforced when switched on. The gap it belongs to stays open until then.
- **Gap** — not covered; listed under [Gaps](#gaps) with an id.

Mapped against `main` at 2026-10-27. When you add a control or close a gap, update its row in the
same PR (see "Documentation is part of the change" in `CLAUDE.md`).

## Web tier — OWASP Top 10:2025

### A01 Broken Access Control

| Rule | Guard | Status |
|---|---|---|
| Owner-scoped routes resolve the owner with `Depends(require_owner)`; no `?owner=` parameter exists (`api/auth.py`). An identity with no `owner_identities` row gets a 403, never auto-provisioning. | `tests/test_auth.py`, `tests/test_identity_service.py` | Enforced |
| Every per-owner table (an `owner` column or an FK into one) is isolated in SQL: each statement binds the owner, a child through its parent; `owner` is never defaulted; each table has wrong-owner tests (CLAUDE.md "Unified Database", arch-v2 Rule 12). The guard derives the tables from the schema snapshot, so a new one fails until its tests are mapped; its SQL scan is a heuristic tripwire with a reviewed `ALLOWED` list. | The per-table wrong-owner tests named in `OWNER_SCOPED`; `tests/test_owner_isolation_coverage.py` keeps that inventory complete | Enforced |
| Positions are isolated by **book** as well as owner (#431): every statement on `positions`/`lot_sales` carries the real-by-default book subquery, which also binds the owner, so another owner's book id matches nothing and a caller naming no book never reaches a paper position. `book_id` is keyword-only and defaults to `None` (CLAUDE.md "Unified Database"). | The book tests named in `BOOK_SCOPED`; `tests/test_book_isolation_coverage.py` (inventory, SQL scan, signatures); `books` itself is in `OWNER_SCOPED` | Enforced |
| The cash ledger (`cash_entries`, #431 step 3.1) carries `owner` and `book_id`, and every statement binds the owner and the real-by-default book subquery, the rows a lot or sale writes included. The routes take the owner from `require_owner`; the three write routes take no book, so they can only reach the caller's real book; a book or entry that isn't the caller's answers 404. One statement is cross-owner on purpose (`SQL_LIST_BOOK_OWNERS`, the backfill's walk, owner names only) and is on the owner scan's `ALLOWED` list. | `tests/test_cash_repository.py` (`IsolationTest`), `tests/test_cash_api.py` (`test_owners_are_isolated`, `test_a_book_that_is_not_the_owners_is_not_found`); `cash_entries` in `OWNER_SCOPED` and `BOOK_SCOPED` | Enforced |
| Paper orders (`orders`, #431 step 3.3) carry `owner` and `book_id`, and every statement binds the owner and the real-by-default book subquery. A fill's rows are written through an `OrderTransaction`, which is bound to one owner and one book when it is made and locks that book's row. The routes take the owner from `require_owner`; a book or order that isn't the caller's answers 404. No route takes a price, a date or a `source`, so a caller can't set a fill or forge where a trade came from, and the real book is refused. An order leaves `pending` through one conditional `UPDATE`, and the idempotency key is unique per owner, so a repeated or concurrent request fills once (A04, A08). Seeding and the manual close (step 3.5) are two more routes under the same rules: neither takes a price, a share count or an owner, a seed reads only the caller's own real book, and a manual close is refused for a symbol that still has a quote, so it can't be used to pick a price. | `tests/test_paper_trading.py` (`RoutesTest`, `test_another_owner_cannot_seed_this_book_and_only_their_own_holdings_are_copied`), `tests/test_orders_repository.py` (`IsolationTest`, `ResolveTest`), `tests/test_orders_api.py` (`test_another_owner_gets_a_404_for_every_route`, `test_the_route_takes_no_price_source_or_owner`, `IdempotencyTest`); `orders` in `OWNER_SCOPED` and `BOOK_SCOPED` | Enforced |
| Stored stops (`stops`, #431 plan step 4.1) carry `owner` and `book_id`, and every statement binds the owner and the real-by-default book subquery. `set_stop` resolves the book as the caller's, locks its row and requires an OPEN lot of the symbol in that book before it writes. The routes take the owner from `require_owner`; a book or stop that isn't the caller's answers 404. No route takes a `source` or a status, so a caller can't say an automated trigger set a stop or mark one triggered. A stop leaves `active` through one conditional `UPDATE`, and one active stop per book and symbol is a unique index (A04). The bar-close checker (step 4.2) is a daily-job step with no route: its statements (`record_check`, `trigger_stop`, `close_flat_stops`, `mark_alerted`, `mark_approach_alerted`, and `stop_sql`'s on a trade's connection) bind the owner and the book the same way, a paper stop's sale goes through an `OrderTransaction` bound to that owner and book, and the breach and approach alerts (step 4.4) are posted for the job owner's real book only and name no other owner. A stop mirrored from a broker (step 4.5, `at_broker`) is refused on a paper book in the service, its share count is checked against the holding under the book's lock, its order type comes from a fixed list in the request model and the service, and its free-text `brokerage` (60 characters at most) reaches Discord only in the job owner's own real-book alerts. Nothing is sent to or read from a broker. | `tests/test_stops_repository.py` (`IsolationTest`), `tests/test_stops_api.py` (`test_another_owner_reaches_nothing_by_any_route`, `test_the_route_takes_no_source_status_or_owner`), `tests/test_stop_checks.py` (`test_wrong_owner_and_wrong_book_checker_writes_change_nothing`, `test_another_owner_or_book_cannot_check_it`, `test_another_owner_cannot_mark_it`), `tests/test_stop_approach.py` (`test_wrong_owner_and_wrong_book_change_nothing`), `tests/test_stops_api.py` (`BrokerMirrorTest`); `stops` in `OWNER_SCOPED` and `BOOK_SCOPED` | Enforced |
| Book-value snapshots (`book_value_snapshots`, #431 plan step 5.1) carry `owner` and `book_id`, and every statement binds the owner and the real-by-default book subquery; the upsert picks its book through that subquery, so a book that isn't the owner's writes nothing and a write never creates a book. Only the daily job writes a row, walking owner by owner through the reviewed `SQL_LIST_BOOK_OWNERS`; there is no route yet (step 5.3 adds the read). Its alarm counts books by reason and names no book or owner. | `tests/test_book_snapshots_repository.py` (`OwnerIsolationTest`, `BookIsolationTest`), `tests/test_performance_service.py` (`test_another_owners_book_is_not_found`); `book_value_snapshots` in `OWNER_SCOPED` and `BOOK_SCOPED` | Enforced |
| The books routes (`/api/books`) and the `book_id` parameter on the portfolio reads take the owner from `require_owner`, never from the request. A book that isn't the caller's answers 404, the same as one that doesn't exist, on list, archive and every read; the response never carries the `owner` column. | `tests/test_books_api.py` (`test_another_owner_cannot_see_or_archive_a_book`, `test_a_book_that_is_not_the_owners_is_404_on_every_read`, `test_another_owners_book_is_not_found`) | Enforced |
| Owner isolation is in SQL: every Harvester statement, private helpers included, carries an `owner` predicate ([harvester.md](harvester.md)). | `tests/test_harvester_repository_db.py` (`test_wrong_owner_*`) | Enforced |
| Positions are per-owner the same way. | `tests/test_portfolio_repository.py` (`test_*_is_owner_scoped`) | Enforced |
| Sidekick's owner-scoped tools take the owner from the turn context; their schemas have no `owner` parameter, so the model can't pick whose data it sees (`OWNER_SCOPED_TOOLS` in `quantcore/services/chat.py`). | `tests/test_chat_tools.py`, `tests/test_chat_protocol.py` | Enforced |
| The UI is behind IAP; a user needs the audience entry, the IAP role **and** an `owner_identities` row ([quantui.md](quantui.md)). | manual grant script | Convention |
| `options_positions` is per-owner (V12, #460): every `OptionsPositionStore` statement carries the `owner` predicate, and the notifier reads only its run's owner. | `tests/test_repositories_db.py` (`test_wrong_owner_*`), `tests/test_notifier.py` | Enforced |

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
| SQL values are always bound parameters. Dynamic SQL (`portfolio_repository._apply_lot_fields`, `harvester_repository`, `news_repository`, …) interpolates only code-built fragments: column allow-lists, placeholder lists and literal clauses picked by a bool. Every interpolation into SQL (f-string, `%`, `.format`, `+`/`+=`, or whatever reaches `execute`) must be on a reviewed allow-list keyed by file, function and expression, each with its reason; identifiers go through `psycopg2.sql.Identifier` (#462). | `tests/test_sql_bound_params.py` (AST scan; stale allow-list entries fail too) | Enforced |
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
| Every protected `quantcore-api` route counts against a per-principal token bucket (`api/rate_limit.py`, in-process, one bucket for all routes; `QUANTCORE_RATE_LIMIT_MODE`/`_PER_MIN`/`_BURST`). It runs after auth, so a 401 never counts, and covers the MCP wrappers too, which forward the caller's token. **Shadow mode**: a would-be refusal is logged as `SECURITY rate_limited` and the request is served; enforcing (429 + `Retry-After`) waits for limits set from the soak ([rate-limiting-g2-plan.md](../proposals/rate-limiting-g2-plan.md) step 3). | `tests/test_rate_limit.py` (every protected operation, via OpenAPI), `tests/test_auth_alerting.py` (`test_rate_limited_event`) | Shadow (G2) |
| Each Sidekick model call is admitted against a daily per-user token budget (`quantcore/services/chat_budget.py`, `chat_usage` table): an atomic conditional reservation of the call's worst case in Postgres, so concurrent turns on any instance can't jointly overshoot, reconciled with the provider's reported usage after the call. The budget is the user's own cap (Settings) under a server ceiling (`SIDEKICK_DAILY_TOKEN_BUDGET`). **Shadow mode** (`SIDEKICK_BUDGET_MODE`): a would-be refusal is logged as `SECURITY chat_budget_exhausted` and the turn runs; enforce stops the turn with a message and fails closed if the budget can't be read (`chat_budget_unavailable`). The keyproxy's per-session ceilings above still apply. | `tests/test_chat_budget.py` (modes, reserve/settle/release, ChatService loop), `tests/test_chat_usage_repository.py` (arithmetic, wrong owner, concurrent admission), `tests/test_auth_alerting.py` (`test_chat_budget_events`) | Shadow (G2) |

### A07 Authentication Failures

| Rule | Guard | Status |
|---|---|---|
| `api/auth.py`'s JWT check is the **single** enforcement point for the public services (#297). | `tests/test_auth.py` | Enforced |
| A rejected token gets a fixed 401 body: `expired token` (PyJWT raises it only after the signature verified) or `invalid token` for everything else, with `WWW-Authenticate: Bearer`. PyJWT's message never reaches the caller, since it says how far verification got; the reason goes to the log instead (A09) (#473). `api/errors.py`'s handler keeps an `HTTPException`'s headers when it rebuilds the body; it used to drop them, so the deployed API sent no `WWW-Authenticate` at all. | `tests/test_auth.py` (`RejectionBodyTests`, `ProductionEnvelopeTests`) | Enforced |
| A verified token's `roles`/`role` and `scope`/`scopes` must each be a string or a list of strings; any other shape (a number, an object, `true`, a list holding a non-string) is an invalid token, a 401 that logs `reason=invalid`, never a 500 (#472). | `tests/test_auth.py` (`ClaimShapeTests`, `SecurityEventLogTests`) | Enforced |
| UI identity: `frontend/server/` verifies the IAP assertion before minting a token. | `frontend/server/auth.test.mjs`, `proxy.test.mjs` | Enforced |
| Auth is **inert until configured** locally (no verification key, or `AUTH_DISABLED`, resolves every call to `john`), but **fails closed on Cloud Run** (#459): when `K_SERVICE` is set, `create_app()` in `api/main.py` and `keyproxy/main.py` raises `AuthConfigurationError` without a key or with the disable flag, so the revision never takes traffic. | `tests/test_auth.py` (`CloudRunFailClosedTests`), `tests/test_keyproxy_auth.py` (`TestCloudRunFailClosed`) | Enforced |

### A08 Software or Data Integrity Failures

| Rule | Guard | Status |
|---|---|---|
| Model weights load only from hash-verified safetensors. `torch.load`, `pickle.load` and `trust_remote_code` are banned (#381). | `tests/test_no_pickle_model_loads.py` (AST scan) | Enforced |
| Prod promotes the digests preflight bound, never a moving tag. | `prod-rollout.yml` | Enforced |
| Only a person can put unreviewed code on test: a manual `deploy.yml` dispatch started or re-run by an agent bot account (`AGENT_BOT_ACCOUNTS`) is refused in the deploy job's first step, before checkout and the GCP sign-in (#532). Agents can't remove the check: their tokens lack `workflow` scope and `deploy.yml` changes need a CODEOWNERS-approved PR. | `tests/test_check_deploy_ref.py` `DispatchRefusalTest` (runs the step under bash) | Enforced |
| Schema has a single owner: Flyway, with three-file parity. | `tests/test_schema_parity.py`, `check_schema_snapshot.py` | Enforced |
| A refresh may fill `ohlcv.adj_close`, never blank it. | `tests/test_repositories_db.py` | Enforced |

### A09 Security Logging and Alerting Failures

| Rule | Guard | Status |
|---|---|---|
| Never-log policy: no keys, `Authorization` headers, envelopes, decrypted payloads, request bodies or DSNs in any log **or response** ([byok.md](byok.md)). | `tests/test_dsn_redaction.py`, keyproxy log assertions | Enforced |
| A missing `owner_identities` mapping logs a security event on the `quantcore.security` logger: `SECURITY unmapped_principal` (403) or `unmapped_principal_soft` (Sidekick), identity only. | `tests/test_auth.py` `UnmappedIdentityLogTests` | Enforced |
| Every 401 logs `SECURITY auth_rejected reason=missing\|expired\|invalid`, with the reason taken from the exception class. Only an expired token adds `identity=`, and only after a second verification with just `exp` skipped (PyJWT checks `exp` before `iss`/`aud`, so expiry proves only the signature); otherwise `identity=unknown`. The token never appears. | `tests/test_auth.py` `SecurityEventLogTests` | Enforced |
| Discord alarms re-read what actually landed in the database (bilge-pump pattern, [daily-jobs.md](daily-jobs.md)). | `tests/test_daily_job_capture.py`, `tests/test_empty_watchlist_alarm.py` | Enforced |
| A burst of 401/403s, an unmapped identity, or an expired token in use raises an email alert. Log-based metrics and alert policies are kept as code in `scripts/monitoring/` and applied by an operator with `scripts/auth_alerting.py`. | `tests/test_auth_alerting.py` (config, and the metric regexes run over the real log lines); `auth_alerting.py check` (live drift) | Enforced (applied to test and prod, 2026-10-28/29; proven on test, #464) |

### A10 Mishandling of Exceptional Conditions

| Rule | Guard | Status |
|---|---|---|
| A caught unexpected exception never reaches the caller as text: `route_internal_error`, class-only log lines, the same rule for the chat SSE error frame. | `tests/test_error_redaction.py` (also fails on an unexercised call site) | Enforced |
| A malformed claim on a token we signed is rejected as invalid, not left to raise a `TypeError` from `Principal.from_claims`: an unhandled 500 there escaped the auth-failure alert (#472). | `tests/test_auth.py` `ClaimShapeTests` | Enforced |
| Non-finite floats serialize as `null`; options tables store NULL, never NaN. | `tests/test_json_response.py` | Enforced |
| Schema drift fails startup (`SchemaDriftError`), so a bad revision never takes traffic. | `tests/test_schema_bootstrap.py` | Enforced |
| The daily job's late steps never raise; failures are made loud by alarms. | `tests/test_daily_job_capture.py` | Enforced |

## Sidekick and MCP — OWASP Top 10 for LLM Applications 2025

| Category | What applies here | Status |
|---|---|---|
| LLM01 Prompt Injection | Third-party text (news headlines from Yahoo RSS via `get_news_sentiment`, provider strings) reaches the model inside tool results. The system prompt in `quantcore/services/chat.py` says everything in a tool result is data, never instructions, and that the model should tell the user when a result seems to contain some (#463). That rule can't be tested against a real model, so the guard is containment: a scripted model that obeys a hostile headline is blocked by the loop — an unknown (write) tool is an error, an invented `owner` is dropped for the turn's own, an off-spec directive renders nothing. Spotlighting third-party text is deferred until a write tool exists ([prompt-injection-g4-plan.md](../proposals/prompt-injection-g4-plan.md) D4). | Mitigated (prompt, `test_system_prompt_treats_tool_results_as_data`) · containment Enforced (`tests/test_chat_service.py` `TestPromptInjectionContainment`) |
| LLM02 Sensitive Information Disclosure | Never-log/never-respond policy; owner-scoped tools take the owner from context. | Enforced |
| LLM03 Supply Chain | Pinned FinBERT revision + sha256; the chat model catalog is an allow-list (`quantcore/chat_models.py`). | Enforced |
| LLM04 Data and Model Poisoning | Only FinBERT is used, as-is from a pinned revision; no fine-tuning or training on user data. | N/A today |
| LLM05 Improper Output Handling | Model output becomes UI only through `show_component` directives validated against strict prop specs on **both** sides (`validate_directive`, `componentRegistry.tsx`) and rendered by `DirectiveRenderer`; no HTML from the model. Trusted Types backstops the DOM. | Enforced (`tests/test_chat_protocol.py`, `frontend/src/chat/componentRegistry.test.ts`) |
| LLM06 Excessive Agency | Every Sidekick tool is read-only: the model's tools are exactly `READ_ONLY_TOOLS` in `quantcore/services/chat.py`, plus `show_component`, which renders a validated directive and touches no service. A Sidekick write tool needs the standard's §8 write gating first (not implemented yet), and brings spotlighting of third-party text into scope (G4 plan D2/D4). Of the MCP tools, `add_to_watchlist` and `collect_news` write. | Enforced for Sidekick (`tests/test_chat_protocol.py` `TestReadOnlyTools` — the test pins the tool list; review judges that each only reads); **Convention** for MCP |
| LLM07 System Prompt Leakage | The system prompt holds no secrets; it is in the repo. | N/A |
| LLM08 Vector and Embedding Weaknesses | No RAG or vector store. | N/A today |
| LLM09 Misinformation | The prompt bars numbers the model hasn't fetched and forbids outside data; refusals must name the rule. | Convention (prompt) |
| LLM10 Unbounded Consumption | Arbitrage tool caps mirror the service's caps (tested). A daily per-user Sidekick token budget, admitted by atomic reservation in Postgres, runs in shadow mode: it records and logs, but refuses nothing until step 3 of [rate-limiting-g2-plan.md](../proposals/rate-limiting-g2-plan.md). | Partly — see **G2** |

## Gaps

Each gap has a backlog issue (label `security`). Close it by changing the row above and deleting it here.

- **G2 ([#461](https://github.com/JohnFunkCode/StockPortfolioManager/issues/461)) — No inbound rate limiting or per-user budget.** The standard's §8 says the REST tier rate
  limits every call. It doesn't. A valid token can drive unbounded Yahoo traffic through the public
  REST/MCP ingress (429s hurt every user) and unbounded Sidekick turns on the user's own key. The
  one limiter that exists, the keyproxy's per-`sub` redemption bucket, is Enforced (A06 row) and
  stays; it bounds envelope redemptions per minute, which is neither an ingress limit nor a
  per-user spending budget. The api's per-principal limiter and the daily Sidekick token budget
  (A06 rows) now exist in shadow mode, recording and logging but not refusing; what remains is
  setting the limits from the soak and enforcing both.
  Plan: [rate-limiting-g2-plan.md](../proposals/rate-limiting-g2-plan.md) (proposed; shadow mode first, then enforce).

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
