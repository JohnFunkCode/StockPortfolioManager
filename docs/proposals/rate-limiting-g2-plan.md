# Inbound rate limiting and a per-user Sidekick budget (Security G2, #461)

Issue: [#461](https://github.com/JohnFunkCode/StockPortfolioManager/issues/461) · OWASP A06 /
LLM10 · gap **G2** in [`docs/architecture/security.md`](../architecture/security.md).

**Status: steps 0–2 built and on test, both limits in shadow mode (step 2 soaking since 2026-10-30); step 3 (enforce) not started.** D1–D4 were agreed with
John on 2026-10-29. The review's answers to Q1–Q4 are recorded under
[Review answers](#review-answers) and folded into D4 and D8–D10. Q5 is answered too: no address, just "contact the development team".

**Nobody is throttled until step 3.** Steps 1 and 2 only *measure*: shadow mode logs what a limit
would have refused and serves the request anyway. Real limits are set from that data, reviewed by
the team, and only then enforced.

## Problem, as the code stands (main @ `09cad2a`)

[architectural-standard-v2.md](architectural-standard-v2.md) §8 says the REST tier is "the single
enforcement point for auth, validation, rate limiting, and audit". The rate-limiting part was never
built. A single valid token, whether leaked or held by a runaway agent loop, can drive:

- **Unbounded Yahoo-backed calls** through `quantcore-api`, directly or through any of the 7 MCP
  wrappers. Yahoo's 429s degrade data for **every** user, and the daily job showed how sensitive
  it is to them (#373).
- **Unbounded Sidekick turns** on the user's own Anthropic key (BYOK), with no daily turn or token
  budget.

### What already bounds it

The gap is narrower than "nothing". These limits exist and stay:

| Bound | Where | What it caps | What it doesn't |
|---|---|---|---|
| Per-`sub` redemption bucket | `SubRateLimiter`, `keyproxy/replay.py` (`KEYPROXY_RATE_LIMIT_PER_MIN`, default 30) | Sidekick turns per minute per user. Global, because keyproxy runs `max_instances = 1` | Spend over a day; anything that isn't a Sidekick turn |
| Per-session token ceiling | `BudgetTracker`, `keyproxy/scopes.py` (`KEYPROXY_MAX_SESSION_TOKENS`, default 250,000) | Tokens in one turn (one envelope = one session) | Tokens across turns |
| Tool-loop limit | `ChatService(max_iterations=8)`, `quantcore/services/chat.py` | Model calls per turn | Turns |
| Instance ceilings | `deploy/cloudrun-services.toml`: api `max_instances = 3` at concurrency 160; wrappers 2–20 | Total compute | Anything per user |

So the worst case per user today is about **30 turns/min × 250k tokens ≈ 7.5M tokens a minute**
on their own key, and an unlimited REST request rate.

### Facts the design leans on

- **Every caller of the api presents a per-user identity.** QuantUI's server mints a per-user ES256
  JWT for each browser request, and the MCP wrappers forward the user's own bearer unchanged. A
  limiter keyed on the principal, placed right after `require_principal`, therefore covers both
  ingress paths. The wrappers do nothing costly before the api, so they need no limiter of their
  own.
- **The api already sees each model call's token usage.** The keyproxy's SSE `final` frame carries
  the provider's `usage` block, and `KeyProxyGateway` hands it to `ChatService` as a
  `KeyProxyMessage` (`quantcore/gateways/keyproxy_gateway.py`). The keyproxy has no database; the
  api does. That makes the api the only place a daily total can be kept durably.
- **The keyproxy already logs per-call tokens**: `turn streamed correlation_id=… sub=… provider=…
  tokens=… latency_ms=…` (`keyproxy/main.py`). Summing it by `sub` and day gives a real usage
  baseline without any new code (step 0).
- **Chat doesn't use prompt caching** (no `cache_control` anywhere), so `input_tokens +
  output_tokens` is the whole billable count today.
- **`user_settings.owner` holds the raw principal identity** (`principal.owner`: the IAP email or
  the token `sub`), not the canonical owner from `owner_identities`. See `api/routers/settings.py`.
- **The G6 metric already counts any new `SECURITY <event>` line.**
  `scripts/monitoring/security_events.metric.yaml` extracts the event name with a generic regex, so
  a new event shows up with no metric change. It needs test coverage now; alert policies wait for step 3 (D9).

## Options considered

| Option | Verdict | Why |
|---|---|---|
| **Per-principal token bucket, in-process in the api** | **Chosen (D1)** | No new infrastructure or failure dependency. Keys on the verified JWT identity. With 3 instances the effective ceiling is at most 3× the setting, which is fine for the goal: stop a runaway loop, not meter fairly. |
| Global count in Cloud SQL | Rejected | A write on every request to a new hot table, and a database outage would become a rate-limit question. |
| Global count in Memorystore (Redis) | Rejected | Exact, but it's new paid infrastructure, needs a VPC connector, and adds a failure dependency on every request, all to tighten a 3× slack nobody needs tightened. |
| Cloud Armor rate-based rules | Rejected | Needs an external HTTPS load balancer we don't have (IAP sits directly on Cloud Run), plus new cost. It also can't see the JWT `sub`, so it could only limit by IP, which is the wrong key for a leaked token. |
| A daily Sidekick budget in the keyproxy | Rejected | The keyproxy has no database by design (it holds plaintext keys in memory and nothing else). A restart would reset the budget. |
| **A daily Sidekick token budget in `ChatService`, persisted in Postgres** | **Chosen (D3)** | The api already sees usage, already has Postgres, and already owns the per-user settings the budget belongs with. |

## Decisions

**D1 — The ingress limit is an in-process per-principal token bucket.** It's keyed on
`principal.owner` and lives in each `quantcore-api` instance. The same person reaching the api
through the UI (email) and through MCP (`sub`) gets two buckets; that's acceptable, since each
path is bounded. Instance restarts reset the buckets; also acceptable.

**D2 — One bucket for all routes, for now.** Yahoo-backed routes don't get a tighter class. Tagging
each route that can reach Yahoo means auditing all 15 protected routers and keeping another list current.
Most hot reads are already cache-first (the options screen never calls Yahoo, see CLAUDE.md). If
the shadow logs show Yahoo-heavy abuse, a second class is a follow-up, not a redesign.

**D3 — The Sidekick budget is a daily token budget per user. The server sets a ceiling and each
user may set a lower cap in Settings.** It's the user's key, so the user should be able to tighten
it. The server ceiling is the breach-exposure bound that no setting can raise. Details:

- **Key:** `principal.owner`, the same raw identity `user_settings` is keyed on. The cap a user
  sets and the usage counted against it then refer to the same row key. (Keying on the canonical
  owner would split the two, and an unmapped principal has no canonical owner but can still chat.)
- **Day:** the America/New_York calendar day, matching `quantcore/analytics/market_time.py` and
  the rest of the system's sense of "today". It resets at local midnight.
- **What counts:** `input_tokens + output_tokens` from each `final` frame, the same sum the
  keyproxy charges. If prompt caching is ever added, cache-creation and cache-read tokens join the
  sum in that PR.
- **Admission is an atomic reservation in Postgres, before every model call.** A turn-start
  snapshot isn't enough: two turns running at once (a second tab, or a runaway client driving the
  API directly) would each check against usage that ignores the other, and together overshoot by
  more than one call (#483 review). So before each model call, `ChatService` reserves that call's
  worst case with **one conditional statement** that adds the reservation only if
  `input_tokens + output_tokens + reserved_tokens + reservation <= budget`. Postgres serializes
  the two writers on the row, whichever instance they run on, so of two concurrent calls that
  can't both fit, exactly one is admitted. No in-process state or lock is involved.
- **The reservation** is the call's output cap, `max_tokens` (16,384 today,
  `KeyProxyGateway`; thinking counts against it, so output can't exceed it), plus a conservative
  input estimate: the previous call's reported `input_tokens` plus the new tool results at one
  token per 2 characters, or, for a turn's first call, the request body at one token per 2
  characters. Tool results are number-heavy JSON, which tokenizes denser than prose, so 2 errs high.
- **Reconciliation:** after each `final` frame, one statement releases the reservation and adds
  the call's actual `input_tokens` and `output_tokens`.
- **A reservation that is never reconciled stays charged.** If an instance dies mid-call, or the
  call errors after it may have reached the provider, the reservation counts as spend until the
  day rolls over. That errs toward stopping, in line with D8, and needs no lease or expiry
  machinery. It's released only when the keyproxy reports it refused the call before reaching
  the provider. Leftover `reserved_tokens` on a past day is visible in the table, so the soak can
  see how often it happens.
- **The bound this gives:** stored usage plus outstanding reservations never exceeds the budget
  at admission. Actual spend can exceed it only by the amount the input estimate undercounts, summed
  over the calls in flight at that moment. Output never contributes, because `max_tokens` is a
  hard cap. If the soak shows the estimate undercounting, the ratio is a constant to tighten in
  step 3.

**D4 — Ship in shadow mode, then enforce. Shadow mode throttles nothing.** Both limits ship logging
what they *would* refuse, without refusing, delaying or slowing anything; the user sees no
difference. They soak on test and prod, the limits are set from that data and reviewed by the team,
and a separate PR flips them to `enforce`. This is the same soak-then-enforce path
`QUANTCORE_SCHEMA_MODE` took. Guessing limits and enforcing straight away risks breaking
legitimate bursts we haven't measured: an AI-client analysis session fanning out dozens of tool
calls in seconds, or a UI page firing several requests on load.

The numbers in step 1 are **shadow thresholds**: the point at which a would-be refusal is
*logged*, not a limit on the user. None of them is "one Sidekick turn per minute". A chat turn
costs one request against the ingress bucket, and the only limit on Sidekick turns in force today
is the keyproxy's existing 30 per minute, which this plan doesn't change.

**D5 — Unparseable settings fall back, and the fallback is `shadow`.** This follows CLAUDE.md's
rule that a typo must not silently disarm an alarm. `shadow` is chosen over `enforce` because a typo
made mid-incident shouldn't lock every user out, and over `off` because it keeps logging. An
unparseable number falls back to its default. Both cases log an error naming the variable, never
its value.

**D6 — Limits are off when auth is disabled.** The local principal (`Principal.local()`, under
`AUTH_DISABLED`) is never limited, so local dev, compose and the unit suite are unaffected.
`/api/health` is outside the protected routers and stays unlimited.

**D7 — Sidekick's own tool calls are not ingress.** They're in-process service calls, not HTTP, so
the ingress bucket doesn't count them. A chat turn costs the ingress bucket exactly one request
(`POST /api/chat`); its tool calls are bounded by `max_iterations` and its spend by D3.

**D8 — Once enforced, the budget fails closed and tells the user (review Q2).** If the budget
reservation can't be made before a model call, or a call can't be reconciled after it, the turn
ends with an `ErrorEvent`: "Sidekick can't check your usage budget right now, so this turn was
stopped. Please try again in a few minutes." Any partial answer already streamed stays. The
failure is logged as `SECURITY chat_budget_unavailable mode=enforce` with the exception class only,
never its text.

This applies in **enforce** mode only. In shadow mode the same failure is logged
(`mode=shadow`) and the turn carries on, because refusing turns in shadow mode would throttle
users before any limit is set, which D4 rules out. The ingress limiter has no database, so D8
doesn't touch it.

**D9 — Metrics only until limits are set (review Q3).** Steps 1 and 2 add no alert policies. The
new `SECURITY` events appear on the existing `security_events` metric, which needs no change, and
the team reads them there during the soak. Alert policies for `rate_limited` and
`chat_budget_exhausted` land in step 3, alongside the enforced limits.

**D10 — A user who needs more than the ceiling contacts the development team (review Q4).** A
user can lower their own cap in Settings, never raise it past the server ceiling. When the
ceiling isn't enough, the budget-exhausted message and the Settings budget section both tell the
user to contact the development team, the same wording the keyproxy's `TOKEN_BUDGET_MESSAGE`
already uses (Q5). The team then looks at that user's usage and decides what to change in the
system. There is deliberately
no per-user override mechanism: the first such request is the signal to revisit the ceiling,
not to add an exceptions list.

## Plan

Three PRs, plus a measurement step before the first. Each PR is independently mergeable and
leaves main green.

### Step 0 — Measure (read-only, no PR)

From prod logs over the last 30 days:

1. **Sidekick:** sum the keyproxy's `turn streamed … sub=… tokens=…` lines by `sub` and ET day.
   Record the p50, p95 and maximum daily total per user, and the largest single turn.
2. **Ingress:** Cloud Run request logs don't carry the principal, so this can't be measured
   exactly before PR 1. Record per-instance request-rate peaks for `quantcore-api` as an upper
   bound. PR 1's shadow logs are what really sets this default.

The numbers go in the checkpoint log and set the shadow-mode starting values below.

**Results (2026-10-29, prod, 2026-09-29 → 2026-10-29):**

| Measure | Value |
|---|---|
| Sidekick model calls logged by the keyproxy | 9 (5 `POST /api/chat` turns), all one user |
| Busiest day, one user | ~112k tokens (2026-10-27) |
| Largest single model call | 51.5k tokens |
| `quantcore-api` requests | ~3,100 in 30 days |
| Busiest minute, whole api | 36 requests |
| Busiest minute, one source (user agent + IP) | 24 requests (p99 22) |
| Busiest 10 s window, one source | 24 requests (p99 12) |

Sidekick use is too light to set a budget from; the step 2 soak has to supply that. The ingress
shadow thresholds stay at 120/min with a burst of 60: 5× and 2.5× the busiest source seen.

### Step 1 — Ingress limiter, shadow mode (one PR)

New `api/rate_limit.py`:

- `PrincipalRateLimiter`, a thread-safe token bucket keyed on principal identity. Its shape copies
  `SubRateLimiter`: an injectable clock, a size-capped bucket map that prunes idle buckets, then
  refuses a *new* principal if the map is still full (fail closed). It's a copy, not an import,
  because the keyproxy image doesn't ship `quantcore` and shouldn't start to.
- `enforce_rate_limit(principal = Depends(require_principal))`, a dependency added next to
  `protected` in `api/main.py`, so it runs on every protected router and on nothing else.
- Settings, read at call time:

  | Variable | Values | Default |
  |---|---|---|
  | `QUANTCORE_RATE_LIMIT_MODE` | `off` \| `shadow` \| `enforce` | `shadow` |
  | `QUANTCORE_RATE_LIMIT_PER_MIN` | shadow threshold: sustained requests/min per principal before a would-be refusal is logged | from step 0 (placeholder 120) |
  | `QUANTCORE_RATE_LIMIT_BURST` | shadow threshold: bucket capacity | from step 0 (placeholder 60) |

  In shadow mode these only decide when a line is logged (D4). They become limits only in step 3,
  with values set from the soak.

  Set them explicitly in `deploy/cloudrun-services.toml` for `quantcore-api`, so the deployed value
  is reviewable rather than implied.
- On an empty bucket:
  - **shadow:** log `SECURITY rate_limited mode=shadow identity=<principal> at=<route>` and serve
    the request immediately. Only the *log line* is deduplicated, to one per principal per
    minute, so a runaway loop can't flood the log; the requests themselves are never held back.
  - **enforce:** the same line with `mode=enforce`, then a **429** with a `Retry-After` header and a
    fixed body ("Too many requests. Try again in N seconds."). The body passes
    `tests/test_error_redaction.py`'s rules: no identity, no limits echoed.

How a 429 reaches people:

- **MCP:** `mcp_gateway/rest_client.py` already raises `RestError(429, payload)`, which the tool
  reports as an error. No change is needed beyond a test pinning it.
- **UI:** `frontend/src/api/client.ts` and `chatStream.ts` already surface the body's `message`,
  so a page shows "Too many requests. Try again in N seconds." with no change; a vitest case
  pins it.

Metric only, no alert (D9):

- No new alert policy. `rate_limited` lines land on the existing `security_events` metric with
  `event=rate_limited`, and the team watches that during the soak.
- `tests/test_auth_alerting.py`: run the metric regexes over the real `rate_limited` lines, so the
  metric is known to count them before anyone relies on it.

Tests (`tests/test_rate_limit.py`):

- The bucket itself (refill, burst, idle pruning, full-map refusal), on a fake clock.
- Each mode: off serves everything, shadow logs and serves, enforce logs and answers 429 with
  `Retry-After`.
- Unparseable mode and numbers fall back per D5 and log the variable's name only.
- The local principal and `/api/health` are never limited.
- The log line throttle.

Docs (same PR, per CLAUDE.md):

- This doc: step 0's numbers and the step 1 checkpoint.
- `security.md`: the A06 G2 row (linked to this plan in the proposal PR) moves to "Shadow" and
  names the guard tests.
- `CLAUDE.md`: one rule under the security map ("the api rate-limits per principal in-process; one
  bucket; shadow until G2 step 3") linking here.
- `readme.md`: the three new environment variables.

### Step 2 — Daily Sidekick token budget, shadow mode (one PR)

Schema (the three files, CI-enforced). `db/migrations/V13__chat_usage.sql`:

```sql
-- Per-user daily Sidekick token usage (G2, #461). `owner` is the raw principal identity,
-- the same key user_settings uses; usage_day is the America/New_York calendar day.
CREATE TABLE IF NOT EXISTS chat_usage (
    owner          TEXT        NOT NULL,
    usage_day      DATE        NOT NULL,
    turns          INTEGER     NOT NULL DEFAULT 0,
    input_tokens   BIGINT      NOT NULL DEFAULT 0,
    output_tokens  BIGINT      NOT NULL DEFAULT 0,
    -- Admitted model calls not yet reconciled (D3). Counts against the budget.
    reserved_tokens BIGINT     NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner, usage_day)
);
-- NULL means "use the server default".
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS chat_daily_token_budget INTEGER;
```

Both statements are expand-only, so CI applies them automatically on both projects. Mirror them in
`_SCHEMA` (the `ALTER` after `CREATE TABLE user_settings`, the V2 pattern) and regenerate
`db/schema_snapshot.json`. The table count becomes 24 (CLAUDE.md, `data.md`).

Repository: new `ChatUsageRepository` (`quantcore/repositories/chat_usage_repository.py`).

- `reserve(*, owner, day, tokens, budget, enforce, new_turn) -> Admission`: one
  `INSERT … ON CONFLICT DO UPDATE … WHERE input_tokens + output_tokens + reserved_tokens + %s <= %s
  RETURNING …`. A returned row means admitted. No row means over budget: in shadow mode the
  repository then adds the reservation unconditionally, so accounting stays true while nothing is
  refused, and reports `over_budget=True` for the log line.
- `reconcile(*, owner, day, reserved, input_tokens, output_tokens)`: releases the reservation and
  adds the actuals, in one additive update.
- `used(*, owner, day) -> int` for the Settings page.
- `owner` is required and keyword-only, and every statement filters on it, matching the G1 rule.
  The wrong-owner test lands with it.
- `UserSettingsRepository` gains `get_/set_chat_daily_token_budget`. A user who sets only a budget
  still needs a row, and `chat_model` is `NOT NULL`, so the insert writes the resolved default
  model.

Service:

- **`SettingsService`** resolves the effective budget: the user's cap if set, else the ceiling,
  and never more than the ceiling. A write must be between 1 and the ceiling, or `None` to clear
  it. A stored cap above a since-lowered ceiling resolves to the ceiling, the same fallback
  `chat_model` uses for a retired model.
- **`ChatService.stream_chat`:**
  - Before each model call, `reserve(...)` that call's worst case (D3). The turn holds no usage
    snapshot of its own; the row is the only total.
  - After each `final`, `reconcile(...)`.
  - When the budget is spent:
    - **shadow:** log `SECURITY chat_budget_exhausted mode=shadow identity=…` once per turn and
      carry on.
    - **enforce:** the same line with `mode=enforce`, then end the turn with an `ErrorEvent`: "You've
      used today's Sidekick budget (N of M tokens). It resets at midnight ET. If you need more than
      the system limit, please contact the development team." (D10). If this
      happens mid-turn, the partial answer above the error stays, as it does for `max_tokens`.
  - If a reservation or reconciliation fails: in enforce mode, end the turn with the D8 message; in
    shadow mode, log `chat_budget_unavailable` and carry on.
- The registry injects `ChatUsageRepository` and the settings resolver into `ChatService` as
  repositories and callables, never as a service, so `registry.py` stays acyclic.

Settings:

| Variable | Values | Default |
|---|---|---|
| `SIDEKICK_BUDGET_MODE` | `off` \| `shadow` \| `enforce` | `shadow` |
| `SIDEKICK_DAILY_TOKEN_BUDGET` | the server ceiling, tokens per user per ET day | from step 0 |

REST and UI:

- `GET /api/settings` adds `chat_daily_token_budget` (the user's cap or `null`),
  `chat_daily_token_budget_ceiling`, and `chat_tokens_used_today`. `PUT` accepts
  `chat_daily_token_budget`. Pydantic schemas and `docs/openapi-surface.txt` are updated.
- New `frontend/src/components/settings/BudgetSection.tsx`, beside `ModelSection.tsx`: today's
  usage against the effective budget, a number input to lower the cap, "use default", and the
  line "Need more than the system limit? Please contact the development team." (D10).
  `frontend/src/api/settings.ts` gains the fields. Vitest covers loading, error, success, and the
  validation bounds.
- The Settings page is configuration, not analytical display, so arch-v2 Rules 8–9 (GenUI
  registration) don't apply. Stating it here so the review doesn't have to ask.
- MCP: no tool. The budget is a Sidekick concern, and AI clients spend their own model budget, not
  ours. This is the written REST-only decision arch-v2 §5.5 asks for.

Metric only, no alert (D9): regex coverage for `chat_budget_exhausted` and
`chat_budget_unavailable` in `tests/test_auth_alerting.py`.

Tests:

- **`tests/test_chat_usage_repository.py`:** reserve/reconcile arithmetic, the ET day boundary,
  shadow mode recording an over-budget reservation, and a wrong-owner case.
- **Concurrent admission** (the #483 review's counterexample, against the real test database):
  - two threads, each with its **own** repository and connection (standing in for two api
    instances), released together by a `threading.Barrier`, against a budget with room for exactly
    one reservation: exactly one is admitted, and the row ends within budget;
  - the reviewer's case: stored usage 90, budget 100, two concurrent turns each wanting two calls
    of 6: at most one call is admitted, never the 114 a snapshot design allows;
  - repeated in a loop (e.g. 50 rounds), because a race test that passes once proves little.
- **`tests/test_chat_service.py`:**
  - shadow logs and completes;
  - enforce refuses at turn start;
  - enforce stops mid-turn before the next model call, keeping the partial answer;
  - every model call is reserved before it's made and reconciled after its `final`;
  - an error after the call may have reached the provider leaves the reservation charged; a
    keyproxy refusal releases it;
  - the exhausted message tells the user to contact the development team;
  - enforce, failing reservation: the turn is refused with the D8 message and no model call is
    made;
  - enforce, failing reconciliation: the turn stops after the current call, keeping the partial
    answer;
  - shadow, failing reservation or reconciliation: the turn completes and
    `chat_budget_unavailable` is logged.
- **`tests/test_settings_api.py`:** cap bounds, `null` clears it, a cap above the ceiling resolves
  to the ceiling, owners are isolated.
- The never-log policy: no budget line carries usage numbers tied to a key, an envelope, or a
  request body. Existing assertions extend to the new lines.

Docs (same PR):

- `security.md`: the LLM10 row (linked to this plan in the proposal PR) moves to "Shadow".
- `byok.md`: the budget sits beside the keyproxy's per-session ceiling, and the doc explains which
  bounds what.
- `data.md` and CLAUDE.md: the table count and `chat_usage`.
- `quantui.md`: the Settings section.
- `readme.md`: the two variables.
- This doc: the checkpoint.

### Step 3 — Enforce (one PR, after both soak)

Once each limit has soaked for at least one full week on prod with its shadow line reviewed:

1. Set `QUANTCORE_RATE_LIMIT_PER_MIN`, `_BURST` and `SIDEKICK_DAILY_TOKEN_BUDGET` from the shadow
   data, with headroom over the heaviest legitimate user observed. Record the numbers and their
   source here, and have the team agree them in the PR before it merges: this is the first change
   any user can feel.
2. Add the alert policies D9 deferred: `scripts/monitoring/rate_limited.policy.yaml` and
   `chat_budget_exhausted.policy.yaml`, applied by an operator with
   `scripts/auth_alerting.py apply`, never CI.
3. Set both modes to `enforce` in `deploy/cloudrun-services.toml`. The defaults in code stay
   `shadow`, so a deployment that forgets the variable logs rather than refuses.
4. Pace `scripts/mcp_live_smoke.py` (it runs all 63 tools back to back under one test `sub`), or
   confirm the burst covers it, before flipping test.
5. `security.md`: A06 and LLM10 rows become **Enforced**, and G2 is deleted from Gaps. Close #461.

**Rollback:** set the mode back to `shadow` with
`gcloud run services update quantcore-api --update-env-vars …` (never `--set-env-vars`), then fix
the inventory in a PR so the next roll-out doesn't revert it.

## Out of scope

- **Unauthenticated floods.** The limiter runs after auth, so a 401 flood isn't rate-limited. It
  costs one JWT verification per request, is bounded by `max_instances`, and is already alerted on
  by G6 (`auth_burst.policy.yaml`).
- **A Yahoo-backed route class** (D2). Revisit if the shadow logs show a need.
- **Retention for `chat_usage`.** It's one small row per user per day. A pruning job can come when
  it matters.
- **Exact global counts across instances** (D1). Revisit only if `max_instances` grows enough that
  the slack becomes a real exposure.

## Review answers

From the team review on PR #483 (2026-10-29):

- **Q1 — Defaults.** The worry was that the plan would cap a user at one Sidekick turn a minute.
  It doesn't. Nothing is throttled until step 3 (D4); the step 1 numbers are shadow thresholds that
  only decide when a line is logged; the one-per-minute figure was the *log line* deduplication,
  now reworded. Limits are set from the soak data and agreed by the team in the step 3 PR.
- **Q2 — Database unavailable.** Fail closed and tell the user (D8), once the budget is enforced.
  In shadow mode a failure is logged and the turn continues, so shadow mode stays throttle-free.
- **Q3 — Shadow alerting.** Metric only until the limits are set (D9). Alert policies arrive in
  step 3.
- **Q4 — More than the ceiling.** Yes: the user is pointed to the development team (D10), and the
  team decides what to change in the system. No per-user override mechanism.
- **Q5 — Support contact.** No address or link: the messages say "please contact the development
  team", the same wording as the keyproxy's existing `TOKEN_BUDGET_MESSAGE`
  (`keyproxy/scopes.py`). The public repo carries no contact address, and the keyproxy message is
  unchanged.

## Checkpoint log

| Step | Date | PR | Notes |
|---|---|---|---|
| plan | 2026-10-29 | — | D1–D4 agreed with John: in-process limiter, one bucket, daily token budget with a user cap below a server ceiling, shadow before enforce. Proposal written for team review. |
| review | 2026-10-29 | #483 | Q1–Q4 answered: nothing throttled until step 3; enforced budget fails closed and tells the user (shadow stays open); metrics only until limits are set; over-ceiling users contact the dev team. Added D8–D10 and Q5 (contact channel). |
| review | 2026-10-29 | #483 | Q5 answered: no contact address in the repo. The messages say "contact the development team", matching the keyproxy's copy; the planned `SIDEKICK_SUPPORT_CONTACT` variable was dropped. |
| review | 2026-10-29 | #483 | Guppy review: (1) a turn-start usage snapshot let concurrent turns overshoot by more than one call; D3 now admits each model call with an atomic conditional reservation in Postgres, reconciled after the call, with concurrent-admission tests. (2) #461's "Done when" asks for the map's A06/LLM10 rows now: both rows and the G2 gap entry link this plan, still Gap/Partly. |
| step 0 | 2026-10-29 | — | Measured (table under Step 0). Sidekick: 9 calls in 30 days, one user, busiest day ~112k tokens; too light to set a budget. Ingress: busiest source 24 req/min and 24 per 10 s, so 120/min, burst 60 stand. |
| step 1 | 2026-10-29 | — | `api/rate_limit.py` + `enforce_rate_limit` on every protected router, shadow by default, inventory sets shadow/120/60. Gotchas below. No alert policy (D9); the metric comment lists the new event. |
| step 1 | 2026-10-29 | #489 | Guppy review: the log-dedup map grew past the principal cap (an identity refused because the map was full still got an entry, and each overflow copied the map). Dedup now lives in each bucket, overflowed identities share one throttle, and a prune that frees nothing backs off 1 s. `CapacityBoundTests` pins it. |
| step 2 | 2026-10-30 | #491 | Daily Sidekick token budget in shadow mode: `chat_usage` table (V13, plus `user_settings.chat_daily_token_budget`), `ChatUsageRepository` (atomic conditional reservation), `chat_budget.py` (modes, `TurnBudget`), `ChatService` reserves before and settles after every model call, `KeyProxyError.reached_provider`, Settings page budget section, `GET/PUT /api/settings` budget fields. Inventory sets shadow / 2,000,000. Gotchas below. |
| step 2 review | 2026-10-30 | #491 | Guppy review: the budget branches put `ChatService.stream_chat` at cyclomatic 47 / cognitive 90 (34 / 64 before). It is split into `_model_call` (reserve, stream, release or settle), `_stop_event`, `_run_tools`/`_run_tool`/`_show_component` and `_next_estimate`, and no unit in `chat.py` is above 10 / 15 now. Added tests: a shadow reconcile failure after a good reservation, and an enforce release failure that still shows the call's own error. |
| step 2 test | 2026-10-30 | #491 | Verified on test at `77db46c` (revision `quantcore-api-00217-gkw`). The migrate Job applied V13 (`now at version v13`), the api's startup check read `mode=auto resolved=verify tables=24 missing=0 mismatch=0 extra=0`, and the revision runs `SIDEKICK_BUDGET_MODE=shadow` / `SIDEKICK_DAILY_TOKEN_BUDGET=2000000`. `GET/PUT /api/settings`: the four budget fields came back, a 500,000 cap was saved, `{}` left it in place, `null` cleared it, and 0 and 3,000,000 were refused with 400 and the development-team message. Two real Sidekick turns from the UI left one `chat_usage` row for the ET day: 2 turns, 69,668 input and 1,082 output tokens, `reserved_tokens` 0, which means every reservation was settled. No `SECURITY chat_budget_*` line was logged. The shadow soak starts here. |

## Gotchas

- **FastAPI 0.146 keeps included routers as lazy `_IncludedRouter` containers** (step 1). Their
  include-time `dependencies=` live on the include context, not on any route's `dependant`, so
  walking `app.routes` (as `tests/test_api_smoke.py` does for route-level dependencies) can't see
  the limiter at all. The wiring test is behavioural instead: it makes the limiter refuse
  everyone and requires a 429 from every operation in `app.openapi()`. A route left out of the
  schema would escape it; none is today.
- **Importing `api.main` builds the app**, which runs `ensure_schema()` and needs a database.
  The wiring test patches `quantcore.db.ensure_schema` around the import, so the whole of
  `tests/test_rate_limit.py` runs without Postgres.
- **Settings are read per request**, so a bad value would log its error on every request. It is
  reported once per variable per process instead.
- **Capping the bucket map isn't enough; every per-identity map needs the cap** (#489 review).
  The first cut kept log-dedup timestamps in a second dict, which an identity refused for a full
  map still entered. Keep per-identity state inside the bucket so it shares the bucket's bound.
- **`db/schema_snapshot.json` was hand-edited for V13** (step 2): the local Postgres refuses this
  machine's agent processes, so `check_schema_snapshot.py --update` couldn't run here. The edit
  is additions only, and CI's gate (`check_schema_snapshot.py`) and `test_schema_parity.py` check
  it against a real database. If either fails, regenerate it with `--update` rather than
  hand-fixing again.
- **No `::` casts in SQL that binds `:name` parameters.** `quantcore.db._adapt_sql` rewrites
  `:name` with a regex, so `x::date` would become `x:%(date)s`. Bind the right Python type (a
  `datetime.date` arrives as `'…'::date` from psycopg2) or use `CAST(x AS date)`.
- **The second model call's reservation is often smaller than the first's.** The first call's
  estimate counts the system prompt and tool schemas from characters at 2 per token, while the
  second builds on the first call's *reported* input tokens, which are far fewer. A test that
  wants a mid-turn refusal therefore has to make the first call report large usage; a tight
  budget alone refuses the first call or neither.
- **The budget's owner key is the raw principal (`TurnContext.subject`)**, the key
  `user_settings` already uses, not the canonical `owner` the portfolio tools resolve: a user
  without an `owner_identities` row still has a budget. One consequence surprised the test
  verification: the same person can hold **several budgets**, one per principal. A QuantUI login
  records usage under the login's email principal, so a JWT minted with `--sub john` (the MCP
  token path) read 0 used while the UI turns were counted. That is correct, not a missed write.
  Step 3 reads the soak data per principal, so a user who works through both the UI and an MCP
  client shows up as two rows.
- **No semicolon in a `_SCHEMA` comment** (step 2, first CI run of #491). `init_schema()` splits
  `_SCHEMA` on `;`, so `-- cap; NULL = …` left a comment-only chunk, and psycopg2 raised
  `can't execute an empty query` from every DB test and from `api.main`'s import. `db.py` already
  warns about this beside V12; local runs couldn't show it because local Postgres refuses this
  machine's agents.
