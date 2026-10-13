# Stateless MCP wrappers (#367)

**Status:** Step 1 (test-only trial) in review. Steps 2–4 are pending.

## Problem

Almost all billed time on the seven MCP wrapper services is the optional `GET /mcp`
server→client SSE stream:

- Streamable HTTP clients (Codex's `codex-mcp-client`, `claude-code`) open the stream to every configured server and hold it until Cloud Run's 900 s timeout, then reopen it, for as long as the client runs.
- Under request-based billing an instance is billed while any request is open on it.
- Tool calls are POSTs and are cheap.

Prod wrapper logs, 2026-10-06 → 10-13:

| Request type | Hours held open |
|---|---|
| GET streams | ~670 |
| POSTs | 0.6 |

That's ≈ $160/mo at the October rate. Sidekick isn't part of this; it calls services in-process inside `quantcore-api`.

The `PingMiddleware` added by #241 is not the cause. That change raised the timeout from 300 s to 900 s to stop cosmetic truncation warnings. The streams were billed before it too, in 300 s pieces. See [`mcp-timeout-plan.md`](mcp-timeout-plan.md).

## Decision

Set `FASTMCP_STATELESS_HTTP=true` on every wrapper. FastMCP then serves `/mcp` for POST and DELETE only, and a GET gets `405`. The MCP spec allows this, and clients carry on without a push channel.

- **Nothing is lost.** No tool uses `Context`, progress, client logging, elicitation, sampling or list-changed notifications.
- **It's config only.** The setting goes in `deploy/cloudrun-services.toml`; the code doesn't change.
- **All wrappers carry the same value.** `tests/test_cloudrun_services.py::test_wrappers_agree_on_stateless_http` enforces this, because one stateful wrapper would keep billing.

### Considered and rejected

- **A shorter `timeout`.** Clients would reconnect sooner, but the stream would still be open the whole time the client runs.
- **Merging the wrappers (#265).** It divides the stream count by seven but doesn't remove the streams. Stateless removes them. #265 is re-judged after step 4.

## Steps and checkpoint log

| Step | What | State |
|---|---|---|
| 1 | Trial PR: `{ test = "true", prod = "false" }` on all seven wrappers, plus the parity test. Merge rolls test; prod gets an explicit `"false"`, the default, so promoting this commit is a no-op there. | in review |
| 2 | Client check on test (John): Claude Code and Codex against the test wrapper URLs; `scripts/mcp_http_smoke.py`; test logs show POSTs only, or GETs ending at once with 405. | pending |
| 3 | Prod PR: `prod = "true"`; remove `PingMiddleware` / `MCP_PING_INTERVAL_MS` from `mcp_gateway/serve.py` (dead in stateless mode); update `docs/operations/prod-promotion.md`; promote. | pending |
| 4 | Measure a week after prod: billed instance time per wrapper (and the BigQuery export). Record it here and on #265. | pending |

### Step 1 checkpoint (2026-10-13)

- **Local probe** (fastmcp 4.0.11, the locked version; stateless set through the env var):
  - `GET /mcp` → `405`, `allow: POST, DELETE`, plain-text body.
  - A FastMCP client listed tools and made two calls across 2 s ping intervals without error.
  - The same server without the flag answers a bare GET with `400`.
- **Drift check.** `python scripts/cloudrun_services.py check --env test` against the branch reported only `env.FASTMCP_STATELESS_HTTP: live '<unset>', manifest 'true'` on the seven wrappers, so the roll-out changes nothing else.

## Risks

- **Client handling of a 405 on GET.**
  - Claude Code has a reported bug with a 405 that carries a **JSON-RPC** body (the server's tools fail to load). Ours is plain text, so it probably doesn't apply; step 2 checks it.
  - How Codex (rmcp) handles it is unknown; step 2 checks that too.
- **Cold starts.** With no stream keeping an instance busy, the first call after a quiet spell may start an instance cold. `cpu_boost` is on, and the wrappers import no ML.

## Rollback

Set the value back to `"false"` in the inventory and roll out. Mid-incident, run `gcloud run services update <svc> --project <p> --region us-central1 --update-env-vars FASTMCP_STATELESS_HTTP=false`. Use `--update-env-vars`, never `--set-env-vars`. The next roll-out re-asserts whatever the inventory says.

## Gotchas

- **The local `.venv` may be behind the lock.** It had fastmcp 3.2.3. Probe with a venv built from the lock (4.0.11), because the stateless route table is version-specific.
- **A bare `GET /mcp` answers `400` on a *stateful* server, not `405`.** So "GET returns 4xx" doesn't prove stateless; check for `405` and the `allow` header.
