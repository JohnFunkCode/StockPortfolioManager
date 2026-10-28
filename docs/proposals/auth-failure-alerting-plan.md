# Auth-failure alerting (Security gap G6, issue #464)

**Status:** plan, not started. Gap **G6** in [`security.md`](../architecture/security.md) (OWASP
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
  The line includes `sub` only for `expired`, because only that case has verified claims. For
  `invalid` the claims are attacker-controlled, so they stay out.
- Add tests in the same style as step 1, including a negative assertion that the token is absent.
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
scripts/monitoring/auth_burst.policy.json          # alert policy
scripts/monitoring/unmapped_identity.policy.json   # alert policy
scripts/apply_auth_alerting.sh                     # [--prod] [--check] [--channel-email ADDR]
```

**Metrics.** Labels stay low-cardinality. Identity is **never** a label, for privacy and because
it would make cardinality unbounded.

| Metric | Filter | Labels |
|---|---|---|
| `auth_rejections` | `resource.type="cloud_run_revision" AND log_id("run.googleapis.com/requests") AND resource.labels.service_name=("quantcore-api" OR "quantcore-keyproxy") AND httpRequest.status=(401 OR 403)` | `service_name`, `status` |
| `security_events` | `resource.type="cloud_run_revision" AND textPayload:"quantcore.security: SECURITY "` | `event` (regex extract: `unmapped_principal`, `unmapped_principal_soft`, `auth_rejected`), `reason` |

Use `log_id()` rather than a `logName` that contains the project id, so one file serves both
projects.

**Policies** (the thresholds are placeholders until step 0 fills them in):

| Policy | Condition | Why |
|---|---|---|
| Auth-failure burst | `auth_rejections` summed per `service_name` over 5 min > *N* | Guessing, replay, or a broken client |
| Unmapped identity | `security_events{event=unmapped_principal*}` ≥ *M* in 15 min | Either someone with a valid token we never granted, or an onboarding that missed the `owner_identities` row. Both are worth knowing about. |
| *(optional)* Expired token in use | `security_events{reason=expired}` > 0 in 1 h | Tells you a client's 90-day token lapsed, which ties in with the rotation reminder |

Each policy carries `documentation.content` that says what the alert means and what to check
first, so the email can be acted on without opening this doc.

**Script behaviour.** It follows the conventions of `apply_ar_cleanup_policy.sh`. Default is
test, `--prod` prompts, and the target is echoed before anything runs. It is idempotent: it
creates a metric or policy that's missing, updates one that exists (looking policies up by
`displayName`), and creates the email channel once and reuses it after that. `--check` is a
read-only drift report, like `cloudrun_services.py check`. Confirm at implementation time whether
`gcloud monitoring policies` is GA or still `alpha`/`beta`, and record which it was here.

**Test:** `tests/test_monitoring_config.py` parses every file and checks:
- each policy references a metric defined in `scripts/monitoring/`
- the filters cover 401 and 403 and both services
- no metric has an identity-like label
- no project id or email address is hard-coded

### Step 4: Prove it on test, then prod

1. Apply on test. Send a synthetic burst at **test's** `quantcore-api`: more than *N* requests
   with a garbage bearer, plus one expired token. Confirm both metrics move, the burst policy
   opens an incident, the email arrives, and the incident closes on its own.
2. Run the script's `--check` and confirm it reports no drift.
3. Prod: John runs `./scripts/apply_auth_alerting.sh --prod`. Don't run a burst against prod;
   check `--check` and the metric's first data points instead.

Append a checkpoint line to this doc for each, with what actually happened.

### Step 5: Docs, in the same PR as step 3

- `security.md` A09: the G6 row becomes **Enforced** (guard: the metrics and policies, plus
  `test_monitoring_config.py`). Remove G6 from the open-gaps list. Add an `auth_rejected` row if
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
