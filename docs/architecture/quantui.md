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

Since issue #224 it is also the only research list: the **Securities page is retired** and
`/securities` is a `nav: false` redirect to `/watchlist`, so old links still land. The page now
reads `GET /api/watchlist/mine` — the same rows plus, for the caller, `held`, `quantity` and
`avg_price` (quantity-weighted over open lots, `position_rollup` in
`quantcore/analytics/portfolio_math.py`). The team's rule is that the portfolio is a subset of the
watchlist, so there are no held-only rows: a held symbol that is off the list is left out and
logged as a warning. A held row gets a Held chip, and the Show filter is All / Held. The screener,
the sentiment dashboard and the options-snapshot refresh moved over as
`components/watchlist/ScreenerPanel.tsx`, `SentimentDashboard.tsx` and `OptionsRefreshPanel.tsx`.
The per-symbol drill-down keeps its `/securities/:symbol` URL (its Back button returns to
`/watchlist`), and `GET /api/securities` — the old list route — is gone; `/api/securities/lookup`
and every `/api/securities/{ticker}/*` route stay.

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

The Score Changes panel's **News** column (#433) shows whether the news over each move's own
window (first → last snapshot) agreed with it: `agrees`, `diverges`, `mixed`, `thin` or `—` for no
articles. `FundamentalsService` gets every mover's articles in **two** queries, the articles and
each symbol's first-article date, through an injected `NewsStore`, never a per-symbol loop. The
verdict is `window_sentiment` in `quantcore/analytics/sentiment.py`. It is decided on the window's
**net score** with a ±0.10 dead band, not on the majority label, because a busy window is usually
majority neutral. A window needs 10+ articles and a quarter of its days inside news history, or it
reads `thin`. The response's `news_rule` carries those thresholds for the tooltip, which states the
counts and the coverage ("covers 23 of 75 days").

The security detail page's Technical Analysis tab ends with two cards that share **one request**
(#399, [`stop-loss-card-plan.md`](../proposals/stop-loss-card-plan.md)): Support Confluence, then the
Stop-Loss card, which proposes a stop under one of its zones. The order is display only; the
request is owned by the page, not by either card:

- The **Stop-Loss card** (`frontend/src/components/securities/StopLossCard.tsx`) shows the
  technical, trailing and confluence stops side by side, a ladder replaying the worst 1-day,
  intraday, 5-day and recent drops from today's price against them (bars on a shared price scale,
  each stop a line, and under each drop the stops it would trigger, in words), the context (VWAP, RSI/MACD,
  short interest), and — with a Held chip — the P&L at each stop. It asks for
  `GET /api/securities/{ticker}/stop-loss?confluence=full&position=mine`: the server reads the
  caller's own open lots, so the browser never sends a cost basis. Every number is server-side; the
  ladder only maps the given prices onto a track.
- The **Support Confluence card** (`SupportConfluenceCard.tsx`, issue #93 Phase 7) renders the
  composite support/resistance zones. `useStopLoss` seeds its `['support-confluence', ticker]`
  query from the confluence result the stop-loss response embeds, so the 14 level sources are
  gathered once. While that embedded result exists, the stop-loss query is its only owner: the
  card's own query stays disabled (so it never refetches on focus by itself) and its Retry refetches
  the stop-loss request. A standalone `/support-confluence` response carries no stop-zone marker,
  so letting the card refresh itself would drop the marker while the stop card still shows the
  stop. The card fetches `/support-confluence` itself only once the stop-loss request has settled
  without an embedded result: it failed, or came back without one. The zone the confluence
  stop sits under carries a "stop under this zone" chip; a stop zone outside the top 5 is appended
  as a trailing row marked as such. Keep the two cards' confluence defaults (tolerance 1%, 5 zones)
  equal to the embedded result's, or the seeded data stops matching what the card would fetch.

The card is also the Sidekick component `stop_loss` (`{ticker}` only, `confluence=stop`) — never
give it a cost-basis, shares or owner prop. The Signals tab's old *Risk & Stop-Loss Calibration*
panel was retired into it; `/signals/risk` still feeds the Signals summary line.

The Signals tab's *Recent News & Sentiment* panel carries `SentimentTrendChart` (#411): weekly net
sentiment from `/news/trend?bucket=week`, with a 4w / 13w / 26w window (13w by default). The server
computes every bucket and flag, including weeks before the news history began, weeks with no
articles, thin weeks, the in-progress week, and flips by the shared `classify_flip` rule. The chart
only draws them, keeping the three empty states visibly apart. It is also the Sidekick component
`sentiment_trend` (`{ticker}` only). Plan: [`sentiment-trend-plan.md`](../proposals/sentiment-trend-plan.md).

**Light and dark themes.** Components were written against the dark theme with literal Tailwind greys (`#f9fafb`, `#9ca3af`, `#374151`, `#1f2937`, …) and dark tints (`#1e3a2f`, …), so in the light theme text went white-on-white and panels became dark slabs. Those values are now CSS custom properties, `--qc-text`, `--qc-text-soft`, `--qc-muted`, `--qc-faint`, `--qc-dim`, `--qc-line`, `--qc-surface`, `--qc-surface-sunk`, `--qc-tint-{green,red,amber,blue}[-strong]`, `--qc-tint-amber-deep`, `--qc-on-tint-*` and `--qc-ink-{green,red,amber,blue}`, set on `:root` by each theme's `MuiCssBaseline` override in `frontend/src/themes.ts`. The dark values are the original hex colours (two near-identical amber and blue tint shades were merged), so the dark theme looks unchanged. A CSS variable works anywhere CSS does — `sx`, inline `style`, a helper's return value — but **not** in an SVG presentation attribute, so D3 code sets these colours with `.style('fill' | 'stroke' | 'color', …)`. The hex-alpha idiom `color + '22'` doesn't work on a variable, so it is `tint(color)` (a `color-mix`). Semantic colours (green, red, amber, blue) stay literal, **except on a `tint-*` background**: there the bright hue that read on a dark tint is ~2:1 on the pale light-mode tint, so a status chip's text and border use `--qc-ink-*` (the bright hue in dark mode, a darker shade in light). `frontend/src/themes.test.ts` checks every such pair reaches 4.5:1 in light mode and scans the components for a `tint-*` background beside a literal status colour, or a constant bound to one (defined in the file or imported) — the half-converted pair the #421 review caught. The same override sets `color-scheme` to the theme's mode, because native controls ignore the palette: without it Chromium drew the `<input type="date">` calendar icon dark on the dark dialogs (#263). Don't restyle `::-webkit-calendar-picker-indicator` per component instead.

**Environment badge (#437).** On test, the top bar shows an amber **TEST** chip beside the
`QuantUI` wordmark; prod shows nothing. The value has to be read at runtime: `prod-rollout.yml`
promotes the image built for test without rebuilding it, so a Vite `import.meta.env` flag would say
"test" in prod too. It is `QUANTUI_ENVIRONMENT` in quantui's `[services.env]`
(`deploy/cloudrun-services.toml`), which `frontend/server/indexHtml.mjs` turns into
`<meta name="quantui-environment" content="test">` in `index.html`; `frontend/src/environment.ts`
reads it and `NavBar.tsx` shows the chip only for exactly `test`. It **fails closed**: an unset or
unknown value injects no tag, and no tag (Vite dev, compose) shows no badge. Three gotchas:

- **A `<meta>`, not an inline `<script>`.** The CSP has no `script-src` beyond `'self'`, so an
  injected `window.__QUANTUI_ENV__` script would silently never run. Don't loosen the CSP for it.
- **Every route to `index.html` must go through the handler.** `express.static` would serve the
  untouched `dist/index.html` for `/` (hence `{ index: false }`) and for `/index.html` (hence a
  route for it ahead of the static), and either page would then have no badge. `mountSpa` in
  `frontend/server/indexHtml.mjs` does all three mounts; `server.mjs` and
  `indexHtml.test.mjs` both use it, and the test requests `/`, `/index.html` and a deep link.
- **`Cache-Control: no-cache` on the HTML** so a browser never shows a stale copy; the hashed
  `/assets/*` keep their normal caching.

**Serving model:** `Dockerfile.ui` builds `frontend/dist/` and runs a tiny Express server
(`frontend/server/server.mjs`) that serves the static bundle (SPA fallback, plus CSP + Trusted
Types headers) and **reverse-proxies `/api/*` to `quantcore-api`, attaching a per-user token
server-side** through a small dependency-free `node:http`/`node:https` streaming proxy
(`frontend/server/proxy.mjs`, #357). The proxy keeps the `/api` prefix, streams both bodies (chat is
SSE), and forwards only the minted bearer. It drops the browser's `authorization`, the IAP
assertion, cookies and hop-by-hop headers, and answers a terse 502 when the api is unreachable. The
server verifies the Google-signed IAP assertion (`x-goog-iap-jwt-assertion`) and mints
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
