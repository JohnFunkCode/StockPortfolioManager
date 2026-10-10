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

**Deploy workflow for a UI change:** edit `frontend/` → PR → merge to `main`. `deploy.yml` (whose only path
filter skips changes that touch nothing but `*.md` files, #351) builds `quantcore-ui` (`build-ui` step in `cloudbuild.yaml`) and rolls it onto
the **test** `quantui` service automatically (IAP preserved; CPU/memory, env and secrets
re-asserted from `deploy/cloudrun-services.toml` — see [cloudrun-services.md](cloudrun-services.md)).
quantui is `first_create = "manual"` there: if the service is missing, the roll-out fails rather
than creating it without IAP. Verify on the test
URL, then promote to **prod** by manually dispatching `prod-rollout.yml` (`workflow_dispatch`) with
the commit's 7-char SHA — it copies the image **by digest** test→prod and deploys prod
`quantui` the same way. Prod is never auto-deployed. To try an unmerged UI branch on test first,
dispatch `deploy.yml` from main with `ref` set to the branch (readme "Trying a branch on test
before merging").

**Granting a new user:** QuantUI-only access needs no project-level IAM roles (those are for
minting MCP tokens and the Cloud SQL proxy, see
[`team-access.md`](../operations/team-access.md)). While the OAuth consent screen is in "Testing",
an account needs all **three** of:

1. an entry on the consent screen **Audience** test-user list (Console → APIs & Services → OAuth
   consent screen → Audience → Add users) — manual, no script does this;
2. `roles/iap.httpsResourceAccessor` on the `quantui` service (not the project);
3. an `owner_identities` row mapping the email to its owner handle (e.g. `thomas`).

Missing (1) or (2) is a blocked login; missing (3) gets the user through IAP and onto the
RestrictedAccess screen. Granting the IAP role by hand with `gcloud` produces exactly that state,
so use `scripts/grant_quantui_iap_access.sh`, which does (2) and (3) together and verifies the
row. The address must be a real, active Google account — otherwise IAM silently drops the binding
while `gcloud` reports success.

Procedure:

1. **Add the user to the script.** The script takes no email argument; it reads the hard-coded
   `USERS=( … )` array. Append an `"email:handle"` entry, where the handle is the user's owner
   partition (the `positions.owner` value, e.g. `"thomas@zoidbergfolio.com:thomas"`). Commit the
   edit — the array is the record of who has been granted.
2. **Add them to the Audience list** in Console (requirement 1 above), in each project.
3. **Start the Cloud SQL Auth Proxy** for the target project — the script writes the
   `owner_identities` row straight to the database: `./runProxy-MAC.sh --test` (5434) or
   `./runProxy-MAC.sh` (5433, prod). `.env` must hold the matching DSN (`QUANTCORE_TEST_DB_DSN` /
   `QUANTCORE_DB_DSN`). The write needs `psycopg2`, so the script runs `.venv/bin/python` when the
   project venv exists and falls back to `python` on `PATH` otherwise. Gotcha: before that change,
   running it without `source .venv/bin/activate` picked up a system Python (Anaconda) with no
   `psycopg2`. Every IAP grant still landed, but every row write failed, which left exactly the
   RestrictedAccess state. If you see `ModuleNotFoundError: No module named 'psycopg2'`, create
   the venv and re-run; re-running is safe.
4. **Run the script** per project: `./scripts/grant_quantui_iap_access.sh` for test,
   `./scripts/grant_quantui_iap_access.sh quantcore-prod-20260606` for prod. It re-processes every
   entry in the array, which is safe: the IAP binding is idempotent and the insert is
   `ON CONFLICT DO NOTHING`. An email already mapped to a *different* handle fails loudly and is
   left untouched — fix that row by hand.
