# Inbound rate limiting and a per-user Sidekick budget (Security G2, #461)

Issue: [#461](https://github.com/JohnFunkCode/StockPortfolioManager/issues/461) · OWASP A06 /
LLM10 · gap **G2** in [`docs/architecture/security.md`](../architecture/security.md).

**Status: proposal, revised after team review.** Nothing here is built yet. D1–D4 were agreed with
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
- **When it's checked:** before each model call in the tool loop, against the running in-memory
  total (today's stored usage plus this turn's usage so far). So the overshoot is bounded by one
  model call, not one turn.
- **When it's charged:** after each `final` frame, one upsert that adds to today's row.

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
can't be read at the start of a turn, or a charge can't be written after a model call, the turn
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
already uses (Q5). The team
then looks at that user's usage and decides what to change in the system. There is deliberately
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
- **UI:** `frontend/src/api/client.ts` maps a 429 to that message, so a page shows "try again"
  rather than a generic failure. Vitest covers it.

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
- `security.md`: the A06 row moves from the G2 gap to "Shadow" and names the guard tests.
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

- `used_today(*, owner, day) -> int` and `charge(*, owner, day, input_tokens, output_tokens,
  new_turn)`, a single `INSERT … ON CONFLICT DO UPDATE` that adds to the row.
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
  - At the start of a turn, read `used_today` once.
  - Before each model call, compare `used_today + this_turn` with the effective budget.
  - After each `final`, `charge(...)`.
  - When the budget is spent:
    - **shadow:** log `SECURITY chat_budget_exhausted mode=shadow identity=…` once per turn and
      carry on.
    - **enforce:** the same line with `mode=enforce`, then end the turn with an `ErrorEvent`: "You've
      used today's Sidekick budget (N of M tokens). It resets at midnight ET. If you need more than
      the system limit, please contact the development team." (D10). If this
      happens mid-turn, the partial answer above the error stays, as it does for `max_tokens`.
  - If the budget can't be read or charged: in enforce mode, end the turn with the D8 message; in
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

- **`tests/test_chat_usage_repository.py`:** upsert accumulation, the ET day boundary, and a
  wrong-owner case.
- **`tests/test_chat_service.py`:**
  - shadow logs and completes;
  - enforce refuses at turn start;
  - enforce stops mid-turn before the next model call, keeping the partial answer;
  - usage is charged per `final`;
  - the exhausted message carries the support contact;
  - enforce, failing read: the turn is refused with the D8 message and no model call is made;
  - enforce, failing charge: the turn stops after the current call, keeping the partial answer;
  - shadow, failing read or charge: the turn completes and `chat_budget_unavailable` is logged.
- **`tests/test_settings_api.py`:** cap bounds, `null` clears it, a cap above the ceiling resolves
  to the ceiling, owners are isolated.
- The never-log policy: no budget line carries usage numbers tied to a key, an envelope, or a
  request body. Existing assertions extend to the new lines.

Docs (same PR):

- `security.md`: the LLM10 row moves to "Shadow".
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
