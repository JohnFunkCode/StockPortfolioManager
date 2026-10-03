# QuantUI front end on Cloud Run (behind IAP)

> Moved verbatim from `CLAUDE.md`, which keeps the rules an agent must not break as a short
> summary and links here for the explanation. Edit the detail here, the rule there.

## QuantUI

The React SPA (`frontend/`) is deployed as the **QuantUI** Cloud Run service in both projects,
gated by **Identity-Aware Proxy (IAP)** so the team reaches the real UI from anywhere with no auth
code in the app — see [`docs/proposals/quantui-iap-plan.md`](../../docs/proposals/quantui-iap-plan.md)
(status: **COMPLETE, Steps 1–8**). Live URLs:

- **Test:** `https://quantui-493357101423.us-central1.run.app` (`quantcore-test-20260606`)
- **Prod:** `https://quantui-127961694257.us-central1.run.app` (`quantcore-prod-20260606`)

**Pages are declared once, in `frontend/src/navigation.tsx`** — one array that both `<Routes>` and
the nav bar map over, so adding a page is a one-object append rather than two edits that can
disagree. Two fields carry decisions rather than data: `nav: false` marks a drill-down
(`/plans/:id`) that must never get a button, and `group` names which of the two dropdown menus the
page sits under — `MY_POSITIONS` (what you own) or `RESEARCH` (what you're looking at), with an
ungrouped entry staying a top-level button (`Settings` is neither, and costs one click rather than
two). Grouping is keyed on the **label, in first-appearance order** (`buildNavSections`), not on
adjacency, so a page appended mid-array joins the existing menu instead of opening a second one
with the same name. The bar itself is `frontend/src/components/layout/NavBar.tsx` and not `App.tsx`,
because each group owns a menu-anchor `useState`. The active page is marked with **`aria-current`**
— `'page'` on the link, plain `true` on a group trigger that is not itself the page — which is both
the accessible signal and what the tests assert on, so don't replace it with a CSS-only highlight.
The readme's Pages table is the human-facing tour of the same array (issue #147 Part G).

The **Watchlist page** (`/watchlist`, issue #147 Part C) renders the whole shared list ranked by
fundamental score off the single `GET /api/watchlist/fundamentals` call, and is the replacement for
the nightly `generate_watchlist_fundamentals_report.py` HTML. The server does the ranking and the
stale/unscored counts (Rule 8.4); the page sorts on `market_cap_usd` and leaves the native
`market_cap` column deliberately **unsortable**, because ordering mixed currencies ranks exchange
rates rather than companies.

The **Fundamentals page** (`/fundamentals`, issue #147 Part D) answers "what's good?" across the
**tracked universe** — the shared watchlist plus *every* owner's positions, deliberately the same
set `main.py` captures options for and warms fundamentals over. Five panels
(`frontend/src/components/fundamentals/`), five independent cache-backed reads, and **no page-level
loading gate**: each panel owns its loading and error state, so one slow or failed query leaves the
other four standing. All four ranked reads pass `scope=tracked`; `cache-stats` takes no scope
because it reports on the cache, not on a roster — and a failure there renders *nothing* rather than
an alert, since it is the one panel whose absence costs no analysis. Two of the panels are also
sidekick components (`fundamentals_top`, `fundamentals_score_changes`), registered with **empty**
prop specs and rendered as the panels' own `variant="rail"` — a card and the open page then share
one hook and one react-query key rather than duplicating the fetch.

`scope` (`all` | `tracked`) is threaded through `FundamentalsService`'s four collection methods and
their routes. Two things about it are load-bearing: the filter runs **before** the ranking (top-N of
the cache is not top-N of the roster), and the roster is supplied to `FundamentalsService` as a
late-bound `tracked_symbols` **callable** wired in `registry.py` — `WatchlistService` already
composes `FundamentalsService`, so injecting the services directly would close a construction cycle.
At the route boundary it is a `Literal["all","tracked"]`, so a bad value is a 422 rather than a
service `ValueError` escaping over HTTP.

The security detail page's Technical Analysis tab includes the **Support Confluence card**
(`frontend/src/components/securities/SupportConfluenceCard.tsx`, issue #93 Phase 7), rendering the
`GET /api/securities/{ticker}/support-confluence` composite support/resistance zones.

**Serving model:** `Dockerfile.ui` builds `frontend/dist/` and runs a tiny Express server
(`frontend/server/server.mjs`) that serves the static bundle (SPA fallback, plus CSP + Trusted
Types headers) and **reverse-proxies `/api/*` to `quantcore-api`, attaching a per-user token
server-side**: it verifies the Google-signed IAP assertion (`x-goog-iap-jwt-assertion`) and mints
a 15-min **ES256 JWT** (`sub` = the IAP email, `aud: ['quantcore-api','quantcore-keyproxy']`) in
`frontend/server/auth.mjs`, signed with the `quantui-signing-key` secret (public half in
`quantui-signing-pub`, given to the verifiers). Fallback ladder keyed on configuration:
`QUANTUI_SIGNING_KEY` set → per-user mint (missing/invalid IAP assertion = hard 401); else
`QUANTCORE_API_TOKEN` (legacy static `quantui-api-token` secret) → else no header (compose,
`AUTH_DISABLED=1`). The browser stays same-origin (no CORS) and never sees any bearer — the
production equivalent of the Vite dev proxy. IAP gates *who can load the UI*; the minted JWT
authenticates the UI→API hop and carries user identity to the BYOK keyproxy. Each project has its
own signing keypair + OAuth client (standalone projects can't auto-provision one; attach via
`scripts/attach_quantui_iap_oauth.sh`).

**Deploy workflow for a UI change:** edit `frontend/` → PR → merge to `main`. `deploy.yml` (no path
filters) builds `quantcore-ui` (`build-ui` step in `cloudbuild.yaml`) and rolls it onto
the **test** `quantui` service automatically (IAP/secret/env config preserved; CPU/memory
re-asserted from the workflow's sizing env block — see **Cloud Run sizing** below). Verify on the test
URL, then promote to **prod** by manually dispatching `prod-rollout.yml` (`workflow_dispatch`) with
the commit's 7-char SHA — it copies the image **by digest** test→prod and deploys prod
`quantui` the same way. Prod is never auto-deployed.

**Granting a new user:** while the OAuth consent screen is in "Testing", an account must be on BOTH
(1) the consent screen **Audience** test-user list and (2) hold `roles/iap.httpsResourceAccessor`
on `quantui`. Add the email to the `USERS=( … )` array in `scripts/grant_quantui_iap_access.sh` and
run it per project (`./scripts/grant_quantui_iap_access.sh` for test;
`./scripts/grant_quantui_iap_access.sh quantcore-prod-20260606` for prod), plus add them to the
Audience tab in Console. Both are required — only one results in a blocked login.
