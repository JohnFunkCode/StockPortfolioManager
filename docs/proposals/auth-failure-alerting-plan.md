# Auth-failure alerting (Security gap G6, issue #464)

**Status:** steps 0–3 and 5 done (#468, and the PR adding `scripts/monitoring/`); step 4, applying the config and proving it, is next. Gap **G6** in [`security.md`](../architecture/security.md) (OWASP
A09), found by the security map in #458.

## Problem

`quantcore-api` and the 7 MCP wrappers are `allUsers` by design (#297), so `api/auth.py` is the
only thing between the internet and the data. It rejects bad requests correctly, but **nothing
notices when rejections pile up**. Token-guessing, a leaked expired token being replayed, or a
client whose 90-day token lapsed would all go unseen. The daily job raises a loud Discord alarm
when data fails to land (the bilge-pump pattern, [daily-jobs.md](../architecture/daily-jobs.md)).
Auth has nothing like it.

A second, smaller gap: the A09 row "a missing `owner_identities` mapping logs a security event" is
marked **Convention** because no test asserts the log line.

## What exists today (verified 2026-10-28)

| Signal | Where | Logged? |
|---|---|---|
| 401 missing bearer | `require_principal`, `api/auth.py:259` | **No app log line**. Only Cloud Run's request log (`httpRequest.status=401`). |
| 401 invalid/expired token | `_decode` `except jwt.PyJWTError`, `api/auth.py:239` | **No app log line**. Request log only. |
| 403 unmapped identity (hard) | `require_owner`, `api/auth.py:290` | `WARNING quantcore.security: SECURITY unmapped_principal identity=… at=…` |
| unmapped identity (soft, Sidekick) | `resolve_owner_or_none`, `api/auth.py:326` | `SECURITY unmapped_principal_soft …`. No 403, so no request-log signal at all. |
| auth inert on Cloud Run (#459) | `assert_auth_enforced_on_cloud_run` | `ERROR quantcore.security: refusing to start …`. Already loud, because the roll-out fails. |

Facts that shape the design:

- **Every public auth decision is made in `quantcore-api`.** The wrappers forward the bearer
  unchanged (`mcp_gateway/rest_client.py`) and turn the api's 401 into a tool error. So the api's
  request log sees every rejection, including those that come in through MCP. The wrappers' own
  request logs show 200s for MCP traffic and can be left out.
- `keyproxy` is IAM-locked. Its 403s come from Cloud Run's IAM front end, and they also appear in
  its request log. quantui sits behind IAP, which rejects requests before Cloud Run sees them, so
  it is out of scope.
- App logs reach Cloud Logging as plain `textPayload` lines, in `basicConfig`'s
  `%(asctime)s %(levelname)s %(name)s: %(message)s` format (`api/main.py:26`), not as structured
  JSON. A log-based metric matches on `textPayload`.
- **There are no Cloud Monitoring alert policies or notification channels in either project yet.**
  `cloud-run-observability-plan.md` describes alerting only as an aspiration. This would be the
  first policy, so the channel has to be created as well.
- The CI deployer is `run.developer` and cannot write logging metrics or monitoring policies, and
  we have declined to widen its IAM before (2026-10-05, [cloudrun-services.md](../architecture/cloudrun-services.md)).
  An operator applies this, the same way the AR cleanup policy is applied.

## Decision needed before step 3: where the alert goes

| Option | Cost | Notes |
|---|---|---|
| **A. Email channel** *(recommended to start)* | No code. A native Cloud Monitoring channel. | Works on the day it lands. Quieter than Discord. |
| B. Discord through a relay | A new small Cloud Run service: a Pub/Sub channel feeds it, it reformats the incident for Discord, and the webhook URL lives in Secret Manager. That means an inventory entry and an IAM review. | Cloud Monitoring's webhook channel posts its own fixed JSON. Discord's webhook expects `content`/`embeds`, so pointing the channel straight at Discord should fail. **Verify this before ruling it out.** |

**Decided 2026-10-28: A, email first.** Run it for a few weeks, and build **B** as a follow-up
only if email proves too easy to miss. If B is built, keep email as a backstop channel so a broken
relay can't swallow alerts silently. Note for B: no Cloud Run service carries the Discord webhook
today (it isn't in `deploy/cloudrun-services.toml`), so B also needs a new Secret Manager secret. The plan below keeps the channel a parameter, so moving to B later
changes no metric or policy.

## Steps

### Step 0: Baseline (read-only, both projects)

Thresholds come from real traffic, not guesses. Count 401/403 per service per 5-minute window
over the last 30 days:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND log_id("run.googleapis.com/requests")
   AND httpRequest.status=(401 OR 403)' \
  --project quantcore-prod-20260606 --freshness 30d \
  --format 'value(timestamp,resource.labels.service_name,httpRequest.status)'
```

Repeat for `textPayload:"SECURITY unmapped_principal"`. Expect noise from CI smoke runs (on test),
from MCP clients after a token expires, and from scanners probing the public URLs. Record the p99
and the maximum 5-minute count per service in this doc. **Done when** each threshold in step 3 is
written down along with the number it came from.

**Result (2026-10-28, 30 days, the full `_Default` retention):** traffic is very quiet, too quiet
for a p99 to mean anything, so the maximum 5-minute count sets the thresholds.

| Project | 401/403 in 30 d | Services | Max in one 5 min | `unmapped_principal` |
|---|---|---|---|---|
| prod | 3 (all 401) | quantcore-api | 2 | 0 |
| test | 9 (6×401, 3×403) | quantcore-api, quantcore-keyproxy (one 403) | 3 | 3, from one onboarding on 2026-10-05 |

There was no scanner noise, and no wrapper ever returned 401/403, which confirms that only the
api and keyproxy decide auth. Thresholds: burst **> 10 per 5 min per service** (more than three
times the worst window seen); unmapped identity **> 0 in 15 min**; expired token **> 0 in 1 h**.

### Step 1: Close the Convention row (small PR, can land first)

- Add `tests/test_auth.py` cases using `assertLogs("quantcore.security", level="WARNING")`:
  - `require_owner` with an unmapped principal logs `SECURITY unmapped_principal identity=<id>`
    and raises 403 `not_provisioned`.
  - `resolve_owner_or_none` logs `unmapped_principal_soft` and returns `None`.
  - Both lines carry the identity and **never** the token or `Authorization` value. This follows
    the never-log policy: build the principal from a real token and assert the token string is
    absent from every record.
- `security.md` A09: move that row from **Convention** to **Enforced**, with the test as its guard.

### Step 2: Give the 401s a reason (app change)

Request logs can't tell an expired token from a forged one, and the issue cares about that
difference: replay versus guessing. Add one line per rejection:

```
WARNING quantcore.security: SECURITY auth_rejected reason=<missing|expired|invalid>
```

- `reason` comes from the exception **class**: `ExpiredSignatureError` → `expired`, any other
  `PyJWTError` → `invalid`, no credentials → `missing`. Never the token, the claims or `str(exc)`.
  Only `expired` adds `identity=`, and **expiry alone does not make the claims ours**. PyJWT
  checks `exp` *before* `iss`, `aud` and `sub`, so `ExpiredSignatureError` proves only the
  signature. A token minted for another service, or carrying a malformed `sub`, raises it too.
  The identity therefore comes from a **second verification** with the same pinned key,
  algorithm, issuer, audience and leeway, and with only `exp` disabled. Only a string
  `sub`/`email` is read from it, never a full `Principal`, whose roles/scopes parsing can raise.
  If any part of that fails, the line says `identity=unknown`, and the request is still a 401.
  For `invalid` the claims are attacker-controlled, so they stay out.
- Add tests in the same style as step 1, including a negative assertion that the token is absent.
  The tests must also cover the attribution negatives: expired with the wrong audience, expired
  with the wrong issuer, and expired with a non-string `sub` all log `identity=unknown`. An
  expired token with malformed `roles`/`scope` still gets a 401 and exactly one log line.
- Volume: each rejected request already produces a request-log entry, so this at most doubles the
  log lines for rejected traffic. That's acceptable.
- Noticed in passing, not fixed here: the 401 `detail` echoes `str(exc)` from PyJWT to the caller
  (`api/auth.py:242`). It's harmless today, because PyJWT messages carry no secrets, but it is the
  pattern A10 forbids elsewhere. Note it on G6's row and decide separately.

### Step 3: Monitoring as code (operator-applied)

New files next to the AR policy:

```
scripts/monitoring/auth_rejections.metric.yaml     # log-based metric
scripts/monitoring/security_events.metric.yaml     # log-based metric
scripts/monitoring/auth_burst.policy.yaml          # alert policy
scripts/monitoring/unmapped_identity.policy.yaml   # alert policy
scripts/monitoring/expired_token.policy.yaml       # alert policy
scripts/auth_alerting.py                           # apply|check --env test|prod [--email ADDR]
```

**Metrics.** Labels stay low-cardinality. Identity is **never** a label, for privacy and because
it would make cardinality unbounded.

| Metric | Filter | Labels |
|---|---|---|
| `auth_rejections` | `resource.type="cloud_run_revision" AND log_id("run.googleapis.com/requests") AND resource.labels.service_name=("quantcore-api" OR "quantcore-keyproxy") AND httpRequest.status=(401 OR 403)` | `status`; `service_name` comes with the resource |
| `security_events` | `resource.type="cloud_run_revision" AND textPayload:"quantcore.security: SECURITY "` | `event` (regex extract: `unmapped_principal`, `unmapped_principal_soft`, `auth_rejected`), `reason` |

Use `log_id()` rather than a `logName` that contains the project id, so one file serves both
projects.

**Policies** (thresholds from step 0):

| Policy | Condition | Why |
|---|---|---|
| Auth-failure burst | `auth_rejections` summed per `service_name` over 5 min > 10 | Guessing, replay, or a broken client |
| Unmapped identity | `security_events{event=unmapped_principal*}` > 0 in 15 min | Either someone with a valid token we never granted, or an onboarding that missed the `owner_identities` row. Both are worth knowing about. |
| Expired token in use | `security_events{reason=expired}` > 0 in 1 h | Tells you a client's 90-day token lapsed, which ties in with the rotation reminder |

Each policy carries `documentation.content` that says what the alert means and what to check
first, so the email can be acted on without opening this doc.

**Script behaviour.** It is written in Python rather than shell, like `cloudrun_services.py`:
merging the channel into each policy and comparing nested policy fields is awkward in shell.
The project comes from `deploy/cloudrun-services.toml`'s `environments`. The target is echoed
before anything runs, and `--env prod` prompts. It is idempotent: it creates a metric or policy
that's missing, updates one that exists (looking policies up by `displayName`), and creates the
email channel once and reuses it after that. `check` is a read-only drift report. It compares
only the fields a file sets, so fields the API adds don't count as drift. `enabled` is one
of them: every policy file sets `enabled: true`, so a policy disabled in the console shows as
drift and the next `apply` turns it back on. As of SDK 588,
`gcloud monitoring policies` is GA, but `channels` exists only under `gcloud beta`.

**Test:** `tests/test_auth_alerting.py` parses every file and checks:
- each policy references a metric defined in `scripts/monitoring/`, and every label it filters
  on exists
- the filters cover 401 and 403 and both services
- no metric has an identity-like label
- no project id, email address or `notificationChannels` is committed
- the `security_events` regexes, run over the exact lines `api/auth.py` emits, produce the right
  `event`/`reason`
- `apply` attaches the channel on both create and update, refuses without `--email` when no
  channel exists, and asks before prod

All of this runs against a fake `gcloud`.

### Step 4: Prove it on test, then prod

1. Apply on test. Send a synthetic burst at **test's** `quantcore-api`: more than *N* requests
   with a garbage bearer, plus one expired token. Confirm both metrics move, the burst policy
   opens an incident, the email arrives, and the incident closes on its own.
2. Run `python scripts/auth_alerting.py check --env test` and confirm it reports no drift.
   This is also the first test of `check`'s comparison against real API output; until now it has
   only seen the fake.
3. Prod: John runs `python scripts/auth_alerting.py apply --env prod`. Don't run a burst against
   prod; look at `check --env prod` and the metric's first data points instead.

Append a checkpoint line to this doc for each, with what actually happened.

### Step 5: Docs, in the same PR as step 3

- `security.md` A09: the G6 row becomes **Enforced** (guard: the metrics and policies, plus
  `test_auth_alerting.py`). Remove G6 from the open-gaps list. Add an `auth_rejected` row if
  step 2 landed.
- `CLAUDE.md`, Security review map paragraph: change "open gaps (G1–G4, G6)" to drop G6. Add a
  one-line rule next to the AR policy: monitoring config lives in `scripts/monitoring/` and is
  changed by editing files and re-running the script, never with a one-off `gcloud` command.
- `readme.md`: one line under operations for the new script.

## PR shape

1. **PR A:** steps 1 and 2 (tests and the `auth_rejected` line). App code only, deployed by the
   normal pipeline.
2. **PR B:** steps 3 and 5 (monitoring as code, the test, docs). Step 4 is operator work after
   PR B merges, and its checkpoint goes in a small docs follow-up.

## Considered and rejected

- **Alerting from `main.py`'s daily run.** It already posts to Discord, but it runs once a day,
  after market close. A burst would be hours old before anyone saw it.
- **Alert on the #459 startup refusal.** A refused revision already fails the roll-out in CI,
  which is loud enough.
- **Counting at the wrappers.** They forward the bearer and never decide anything themselves, so
  the api's log sees everything and counting at the wrappers would double-count.
- **Rate limiting.** That's G2's job (#461), not alerting. The two complement each other.

## Checkpoints

*(append as each step lands)*

- **2026-10-28, steps 1–2 opened as #468** (not merged yet): the `auth_rejected` line plus the
  unmapped-identity log tests. Gotcha: the DB-backed test modules can't run from a Claude Code
  session on this Mac, because Postgres.app refuses the session's "trust" connection; CI's gate
  is their first run.
- **2026-10-28, #468 review** found a regression. `_expired_identity` built a full `Principal`,
  so an expired, correctly signed token with `roles=42` answered **500** with no log line. The
  fix reads only the string `sub`/`email` and falls back to `unknown` on any failure. **Gotcha:**
  the plan had said that expired tokens have verified claims. They don't, because PyJWT checks
  `exp` before `iss`/`aud`/`sub` (see step 2).
- **2026-10-28, step 0 done.** The numbers and thresholds are under step 0. **Gotcha:** a
  `quantcore.security` line arrives as a plain `textPayload` on `run.googleapis.com/stderr`
  with **no severity**, so the metric matches on the `quantcore.security: SECURITY ` text, not
  on a severity.
- **2026-10-28, steps 3 and 5 written (not yet applied).** Before anything was applied, both
  projects had **no** log metrics, alert policies or channels. Both metric filters were checked
  read-only with `gcloud logging read` and matched real test logs. Gotchas:
  - `gcloud monitoring channels` doesn't exist; channels are beta-only (`gcloud beta monitoring
    channels`), while policies are GA. `gcloud <group> --help` exits non-zero for some groups,
    so probing for a command that way gives false negatives; read the help text instead.
  - `policies update --policy-from-file` replaces the whole policy, so a file without
    `notificationChannels` would detach the email channel on every re-apply. The script merges
    the channel in, and a test guards it.
  - Alert-policy `severity` has no `INFO`; WARNING is the lowest.
  - The extractor regexes contain `: `, which breaks unquoted YAML. They are single-quoted and
    written without backslashes (`[.]`, `[a-z_]+`), so nobody has to work out how YAML, the
    extractor string and RE2 each read an escape.
  - A log metric created moments before a policy that uses it may not exist yet when the policy
    is created. If the policy create fails in the same run, the script says to re-run.
    **Unverified** until step 4.
- **2026-10-28, #469 review.** `check` reported a **disabled** policy as `ok`, because
  `enabled` had been left out of the compared fields as API-owned. It isn't: a disabled policy
  can't alert, which is the worst drift there is. Each policy file now sets `enabled: true`, and
  `check` compares it; a test drives a disabled live policy and expects drift. The review also
  flagged `cmd_check`, `cmd_apply` and the test fake's `__call__` as over the complexity bands
  (13/18, 13/20, 14/20). They were split into per-object helpers and a handler table, now 6/6,
  7/8 and 6/5 (Radon 6.0.1 cyclomatic / complexipy 8.3.0 cognitive), with tests for the gcloud
  failure paths and the just-created-metric warning.
