# MCP transport timeout plan

Issue: [#241](https://github.com/JohnFunkCode/StockPortfolioManager/issues/241)

## Decision

The reported 301-second truncated `/mcp` responses are treated as a Cloud Run request-timeout
mismatch, with an additional keepalive requirement for long-lived streamable HTTP sessions.
MCP wrapper requests use a 900-second Cloud Run timeout. The shared launcher installs FastMCP's
30-second `PingMiddleware`, and the REST seam translates bounded upstream transport failures into
safe 503/504 errors.

The report Job timeout in issue #234 is a separate execution model and is not changed here.

## Checkpoint log

| Date | Step | Result |
|---|---|---|
| 2026-09-01 | Repository audit | Confirmed FastMCP 3.2.3 streamable HTTP launcher, shared MCP image, image-only Cloud Run rollouts, and 60-second REST upstream timeout. |
| 2026-09-01 | Runtime hardening | Added launcher keepalive and REST transport-error translation; added unit coverage. |
| 2026-09-01 | Deployment policy | Added `MCP_REQUEST_TIMEOUT=900s` to test and production rollouts and documented first-deploy verification. |

## Verification after rollout

1. Confirm every test and production MCP wrapper reports a 900-second request timeout.
2. Initialize each `/mcp` endpoint and hold the session past the old 300-second boundary.
3. Invoke a lightweight tool and confirm a complete response.
4. Confirm slow REST calls surface a bounded MCP error and do not expose credentials or exception
   payloads.
5. Monitor Cloud Run truncated-response warnings and request latency after promotion.
