# Plan: security review triage (issue #297)

## Context

#297 asked to verify a security review's premise — that the MCP wrappers' Cloud Run IAM is
restricted to named users — and to triage its findings. Acceptance: Step 0 results posted on the
issue; each finding fixed, filed separately, or accepted with a reason; docs updated.

## Step 0: what is actually deployed (2026-10-05, read-only, both projects)

| Service | Invoker (`roles/run.invoker`) | Ingress |
|---|---|---|
| 7 MCP wrappers | `allUsers` | `all` |
| `quantcore-api` | `allUsers` | `all` |
| `keyproxy` | its runtime SA only | as inventoried |
| `quantui` | the IAP service agent only | as inventoried |

Test and prod agree. **The review's premise is false, by design**: the wrappers and the api are
public, and authentication rests on the REST JWT check in `api/auth.py`. The IAM model is in
[`cloudrun-services.md`](../architecture/cloudrun-services.md#who-can-call-the-services-runtime-auth).

One unauthenticated probe of the prod api (no token, GET only): `/api/health` 200,
`/api/portfolio` and `/api/watchlist` 401, **`/docs` and `/openapi.json` 200**.

## Findings and triage

| # | Finding | Outcome |
|---|---|---|
| 1 | Local runs bind all interfaces | **Fixed.** The wrappers' `__main__` blocks bind `mcp_gateway.serve.local_host()` (`127.0.0.1`, override `MCP_HOST`). `docker-compose.yml` publishes every port on `127.0.0.1`, which matters because the local api runs `AUTH_DISABLED=1`. The container entrypoint (`serve.main()`) still binds `0.0.0.0`, as Cloud Run requires. |
| 2 | Pin `--no-allow-unauthenticated` on the wrappers | **John's decision; not applied.** It conflicts with the inventory: public wrappers must be `allUsers`, because AI clients present a JWT Google can't verify. Doing it would break every MCP client. |
| 3 | The MCP layer verifies no identity | **Accepted, documented.** By design (Rule 6): the wrapper forwards the caller's token and `quantcore-api` is the single enforcement point. Verified by the 401s above. |
| 4 | Health checks disclose host details | **Fixed.** `mcp_health_check` (arbitrage, portfolio, options) returns identity and version only — no `platform`, interpreter, internal REST URL or file path. `/api/health` returns a generic `database unavailable` on a DB failure and logs only the exception type, never its text (which can carry the host). |
| 5 | Symbols are formatted straight into REST paths | **Fixed.** `rest_client._path` rejects an empty path, `.`, `..`, and any segment outside `[A-Za-z0-9._\-^=]` (so `?`, `#`, `%`, whitespace) with a 400 `INVALID_PATH`, before any connection is opened. Residual: see gotcha 4. |
| 6 | Branch protection / who can deploy | **Mostly answered by #313** (WIF narrowed to main and, for prod, `prod-rollout.yml` in the `prod` environment). Anything further is a repo setting, John's. |

Further items for John (decisions, not changes made here). **Still open:** #297 closed on
2026-10-05 with none of these decided and no issue filed for any of them, so this list is their
only record:

- **Public `/docs` and `/openapi.json` on the prod api.** API-surface disclosure, no data. Options:
  disable them in prod (`docs_url=None, openapi_url=None` behind an env flag) or accept.
- **Rate limiting** on the public services (Cloud Armor or app-level); none today.
- **Who can read the JWT signing key** in Secret Manager, in both projects.
- **Token rotation:** the 90-day MCP JWTs are already documented; whether to shorten them.

## Gotchas

1. **There are two bind paths, and only one is the container's.** `mcp_gateway/serve.py`'s
   `main()` is the image entrypoint and must bind `0.0.0.0`; the wrappers' `__main__` blocks are
   only local-dev runs. A blanket "bind loopback" fix to `serve.py` would have taken every wrapper
   down on the next roll-out (Cloud Run routes traffic to the container's external interface).
2. **The review's premise was wrong, and checking it first changed the triage.** Finding 2 reads
   as hardening but is an outage under the actual auth model.
3. **`/docs` is public** even though every data route returns 401 — found only by probing, not by
   reading the routers.
4. **The path guard can't tell a bare `/` inside a symbol from a separator.** `"a/b"` is two valid
   segments. Accepted residual: a `/` can only add segments between the tool's fixed prefix and
   suffix, `..` is refused, and the caller's own token is what gets forwarded, so it reaches no
   route the caller couldn't call directly. Closing it fully means validating the symbol in each
   wrapper before formatting.
5. **Postgres.app blocks the DB tests locally from a sandboxed agent.** It refuses `trust`
   authentication for an app it hasn't been allowed ("You did not allow Claude to connect without
   a password"), so 179 DB tests error locally with no assertion failures. That's a local setting
   (John's); CI's `gate` runs them.

## Checkpoint log

| Step | Result |
|---|---|
| Step 0 read-only checks (2026-10-05) | Done; table above. Posted on #297. |
| Fixes 1, 4, 5 + tests + docs | [#323](https://github.com/JohnFunkCode/StockPortfolioManager/pull/323). New tests in `tests/test_mcp_seam.py` (path guard, no-network on rejection, loopback default, `__main__` binds, health disclosure, compose loopback) and `tests/test_api_smoke.py` (`/api/health` hides the driver error). Locally: non-DB modules pass; DB modules blocked by gotcha 5. |
