# News sentiment trend on the Signals tab (#411)

**Status:** #411 shipped to prod (#435, #436, #438); #433 (Score Changes cue) in review. Issue [#411](https://github.com/JohnFunkCode/StockPortfolioManager/issues/411).
Related: #282 (relevance filtering), #372 / #375 (the October collection and scoring outages),
#258 (article text).

## Why

`GET /api/securities/{ticker}/news/trend` has returned a per-day sentiment breakdown since the news
store landed, but the UI shows only the current aggregate badge on the Signals tab. You can see what
the news says today, not whether it has been souring or improving.

## What the data looks like today (prod, 2026-10-24)

This shaped every decision below, so it is recorded first.

- **History starts around 2026-09-30.** A 120-day request returns about 25 days. Any fixed window
  longer than that is mostly empty, and will stay that way until history accumulates.
- **About 10 articles per symbol per day, at most.** That is the collector's per-run limit, not
  how much news there was. `article_count` measures collection, not news volume, so it can mark a
  bucket as thin but cannot stand in for a volume bar.
- **Oct 3–12 is empty for every symbol sampled (WDC, AVGO, NVDA).** It was **not collected**.
  All 98 WDC articles in the window are scored, so this is not a scoring backlog that
  `scripts/backfill_news_sentiment.py` could fill. It is the #372 Scheduler outage (code 7 nightly
  until 8066884). Yahoo's search returns only recent items, so those days cannot be recovered.
- **A daily net score swings on a handful of headlines.** WDC went from −0.57 to +0.14 between
  adjacent days. Many articles are market-wide pieces that mention the ticker (#282).

## Decisions (2026-10-24)

- **Placement: the Signals tab, inside *Recent News & Sentiment***, between the *Overall* chip and
  the article list. The current label, how it got there, and the articles behind it then read top to
  bottom. Two placements were mocked up with real WDC data: this one, and a strip under Volume on the
  Technical Analysis tab
  ([canvas](https://claude.ai/artifact/2k3XgMJwK2zTk5TuE4aCyo), private). The Technical Analysis
  strip would line up with RSI/MACD, but on a 90-day period about three quarters of it would be empty.
- **Weekly buckets, not daily.** Weeks start Monday and are in America/New_York time. A week is
  tens of articles; a day is at most ten.
- **The window is chosen in the panel: 4w / 13w / 26w, default 13w.** Signals has no Period
  selector to follow. The toggle is local view state, not a chat gesture: it never sends anything to
  the conversation, so the interaction registries don't apply.
- **Weeks before history began are shaded and labelled**, not drawn as zero. History start is the
  symbol's first stored article, returned by the server.
- **A week with no articles is an explicit bucket** (`article_count: 0`), drawn as a dashed
  outline labelled "0 articles". The server fills every week in the window, so the front end never
  works out which weeks are missing.
- **A thin week is drawn paler.** Thin means fewer than `THIN_WEEK_ARTICLES = 10` articles. The
  server returns the flag; the colour mapping is presentation.
- **The current week is marked `in_progress`.**
- **One definition of a flip, shared with the Discord alert.** Move the rule out of
  `notifier.check_sentiment_flips` into `quantcore/analytics/sentiment.py`. The notifier calls it on
  consecutive snapshots, and the trend calls it on consecutive weekly majority labels. The unit
  differs (snapshot vs week), but the rule is the same function, so they can't drift apart. A week
  whose label flips carries `flip: "deteriorating" | "improving"`, and the chart marks it. (The plan
  first said `to_negative`/`to_positive`, but negative → neutral is a flip the alert colours green,
  and calling that "to positive" would be wrong. The words match Score Changes' `direction`.)
- **The daily contract stays.** `bucket=day` remains the default on the route, and the MCP tool
  `get_sentiment_trend` is untouched (tool count stays 63, contract case unchanged). Weekly is
  `bucket=week`.
- **Out of scope:**
  - The sentiment cue on the Fundamentals page's Score Changes panel (window-matched
    agree/diverge marker). It is tracked in
    [#433](https://github.com/JohnFunkCode/StockPortfolioManager/issues/433), built on the same
    analytics function.
  - Telling "not collected" apart from "collected, nothing found". The news heartbeat in
    `fetch_log` is one upserted row per symbol, so it keeps no history. Doing this needs a per-run
    collection log, which is a separate change.

## Plan

### PR 1: backend

1. **`quantcore/analytics/sentiment.py`** (new, pure):
   - `net_score(pos, neg, total)` — the single home for that formula; the repository's inline
     copy goes.
   - `majority_label(pos, neg, neu)` — the same tie-breaking as `get_news`'s `overall`.
   - `classify_flip(prev_label, new_label) -> "deteriorating" | "improving" | None` — the
     notifier's rule, verbatim: an unchanged label is no flip, and a flip involving neutral counts
     only when negative is on one side.
   - `article_date(published_at, fetched_at)` — the Eastern calendar date an article belongs to,
     from its publish time, falling back to its fetch time.
   - `daily_counts(articles, *, start, end)` — the daily shape the route has always returned.
   - `weekly_buckets(articles, *, today, weeks, history_start)` — fills every Monday-start week
     in the window and sums the counts. It sets `net_score`, `label`, `thin`, `in_progress`,
     `before_history` and `flip`. It also returns the coverage figures: `weeks_with_articles`,
     `total_articles` and `history_start`.
2. **`NewsStore`:** the trend SQL is replaced by a plain read, and the bucketing moves to
   analytics. That fixes two bugs: the old query filtered on `fetched_at` but grouped on
   `COALESCE(published_at, fetched_at)`, so an old article fetched recently landed in a bucket
   outside the window; and it bucketed by UTC date, not Eastern.
   - `get_scored_articles_since(symbol, since)` returns `published_at`, `fetched_at` and
     `sentiment` for scored articles **fetched** since `since`. That set always contains the
     window, because an article is published before it is fetched. The service passes a day of
     slack, and analytics trims the window exactly.
   - `first_article_fetched_at(symbol)` gives where the symbol's history starts.
   - `get_sentiment_trend` on the repository is gone (its only caller was the service).
3. **`SentimentService.get_sentiment_trend(symbol, days=30, bucket="day")`:**
   - With `bucket="week"`, `days` is read as `weeks * 7`, and the response adds `buckets` plus the
     coverage figures.
   - With `bucket="day"`, the output is unchanged apart from the date-filter fix.
4. **Route:** `GET /{ticker}/news/trend?bucket=day|week`. Any other value answers 422. The
   path is unchanged, so `docs/openapi-surface.txt` does not move.
5. **`notifier.check_sentiment_flips`** calls `classify_flip`. Its alert output is unchanged
   (a test pins it).
6. **Tests:**
   - analytics:
     - a week boundary at Sunday 23:00 ET vs Monday 00:30 ET;
     - empty weeks filled;
     - the in-progress week;
     - the thin threshold;
     - weeks before history;
     - flip cases matching the notifier's table;
   - repository: the date-filter fix;
   - service: day vs week;
   - router: 422 on a bad `bucket`;
   - notifier: unchanged output.
7. **Docs:**
   - `fastMCPTest/README.md`'s trend section, if it describes the formula. The readme doesn't
     list this route, so it doesn't change;
   - this plan's checkpoint log.

### PR 2: UI

1. **`frontend/src/api/securities.ts`** gains `getSentimentTrend(ticker, weeks)` calling
   `?bucket=week`. `useSecurities.ts` gains `useSentimentTrend`.
2. **`SentimentTrendChart`** in `frontend/src/components/securities/`:
   - prop `{ticker}` only; holds the 4w/13w/26w window state itself;
   - D3, coloured with `.style()` and `var(--qc-*)` / `tint()`; no literal greys;
   - shows the coverage line ("3 of 13 weeks with articles · 98 articles · history since Sep 30");
   - draws flip markers;
   - has an `aria-label` that states the series in words.

   It does no arithmetic on the series.
3. **`SignalsTab`** renders it inside *Recent News & Sentiment*, under the *Overall* row. It
   renders even when today's article list is empty, because history can exist without current
   articles.
4. **Sidekick:** register `sentiment_trend` with `{ticker: str}` in
   `BACKEND_COMPONENT_REGISTRY` and the `show_component` description (`chat_tools.py`), and with
   `TICKER_ONLY, titled: true` in `componentRegistry.tsx`. It also renders through the existing
   `signals` entry.
5. **Tests:**
   - vitest for loading, error and success;
   - the coverage text;
   - the "0 articles" week;
   - the in-progress label;
   - a flip marker;
   - window toggle refetch;
   - SignalsTab placement;
   - registry parity in `componentRegistry.test.ts` and `tests/test_chat_tools.py`.

   Coverage thresholds only go up.
6. **Docs:**
   - `docs/capabilities-matrix.md`: the Tier 3 row leaves; `news/trend` leaves the gap list;
     54 → 53 and 33 → 32;
   - `docs/architecture/quantui.md`: the Signals tab section;
   - the readme's UI tour.

## Gotchas

- **The Oct 3–12 hole is an outage, not quiet news, and it can't be backfilled.** Check
  `scored_only=false` before assuming a gap is a scoring backlog. If every stored article is scored,
  the articles were never fetched.
- **`article_count` is capped by the collector**, so it's not a measure of news volume. Use it for
  thinness only.
- **`published_at` is free-form text.** Yahoo's `pubDate` comes through as given (`…Z` or
  `…+00:00`), and the column is `TEXT`. That's why the date is parsed in Python, with a fallback
  to `fetched_at`, rather than cast in SQL: one malformed value would fail a SQL cast for the whole
  query. **Don't prefilter on `published_at` either**, not even as text. PR 1 first filtered on
  `COALESCE(published_at, fetched_at) >= since`, and review (#435) showed that `10/23/2026` or a
  leading space sorts before an ISO cutoff, which silently dropped articles the parser would have
  dated from `fetched_at`. The prefilter is now `fetched_at` alone, which is always our own ISO
  UTC stamp.
- **The local unit database is Postgres.app, which refuses a sandboxed Claude process** ("You
  did not allow Claude to connect without a password"). Unless Claude is added under Postgres.app's
  App Permissions, the DB-backed suites (`test_repositories_db`, `test_api_smoke`) run only in CI's
  `gate` job.

## Checkpoint log

- 2026-10-24 — Placement chosen (Signals, option B) from two mock-ups; plan written (#432).
- 2026-10-24 — Score Changes sentiment cue filed as #433; it depends on PR 1's analytics module.
- 2026-10-24 — PR 1 (backend) opened. `quantcore/analytics/sentiment.py`, `bucket=week` on the
  route, `NewsStore.get_scored_articles_since` + `first_article_fetched_at`, and the notifier on
  `classify_flip`. Flip words are `improving`/`deteriorating` (see Decisions). The daily response
  shape is unchanged; its dates are now Eastern and windowed on the article date.
- 2026-10-24 — #435 review addressed:
  - the prefilter moved to `fetched_at` (see Gotchas);
  - `weekly_buckets` split into `_week_bucket` plus a flip pass, taking it from CC 12 / CogC 16 to
    7 / 6 (radon 6.0.1, complexipy 8.1.0);
  - the weekly window is capped at `MAX_TREND_WEEKS = 104`;
  - new tests pin 9 vs 10 articles, a one-week window, and history starting on a week edge.
- 2026-10-24 — #435 merged and deployed to test. PR 2 (UI) opened:
  - `SentimentTrendChart` sits in the Signals tab's news panel between the Overall row and the
    articles. It also renders when there is no current news, since an empty week is information.
  - It is the Sidekick `sentiment_trend` card (`{ticker}` only; the window is view state).
  - The chart is MUI `Box` columns, not D3: a dozen fixed-width bars need no scales, and plain
    elements resolve `var(--qc-*)` without the `.style()` rule D3 needs.
  - Verified against the test DB in both themes. WDC shows 4 of 13 weeks with articles and a real
    `improving` flip in the Oct 5 week, which prod didn't collect.
  - Gotcha: the before-history shading first used `--qc-surface-sunk`, which is the card's own
    background, so it was invisible. A flex `gap` then tiled the shaded run into stripes. The
    shade is `--qc-surface` and the columns have no gap.
  - Gotcha: existing tests mocked `'/news'` by substring, which also caught `/news/trend` and
    handed the chart an articles payload. The trend route is mocked first, and the chart treats a
    response without `buckets` as an error rather than crashing.
- 2026-10-24 — #436 review addressed:
  - `WeekColumn` (CC 17, over the 15 limit) is split into `weekCaption`, `FlipMarker`, `NetBar`
    and `WeekPlot`, each a few branches;
  - `shortDate` returns a string it can't parse unchanged rather than "undefined";
  - a lone before-history column stays unlabelled on purpose (the label can't fit one column, and
    the coverage line gives the history start), now said in a comment;
  - new tests pin positive, negative and zero bar geometry and the improving flip's ▲.
  - Gotcha: jsdom reports an unset `top`/`bottom` as `auto`, not `''`, so the geometry tests
    assert the anchored side only.
- 2026-10-24 — #436 deployed to test and checked there: the API, the Signals-tab chart and the
  Sidekick `sentiment_trend` card all show WDC's 4 of 13 weeks and the Oct 5 flip. Follow-up PR
  for cut-off captions:
  - Gotcha: the window (4w/13w/26w) doesn't decide whether a caption fits; the container's width
    does. The same 13w chart has ~67px columns in a wide page panel, ~38px in a narrow one, and
    ~23px in the ~325px Sidekick card, where every caption read "−0.2…".
  - Each week column is now a CSS container (`container-type: inline-size`). Its caption is a
    net span plus a detail span: the count drops below 56px and the net below 30px, so a number
    is either whole or absent. Measured in Inter 10px: "−1.00 · 100" is 51px and "−0.25" is 28px.
  - An empty week's "0 articles" is detail only, because a bare "0" would read as a net score.
  - jsdom can't evaluate container queries, so the tests pin the span structure, and the cutoffs
    were checked in a browser at 325, 520 and 900px for each window.
- 2026-10-24 — #438 review addressed:
  - Guppy found a four-digit count still clipped: the article count is unbounded, so a fixed
    cutoff can't bound the caption. Counts now use compact notation (at most 4 characters:
    `999`, `12K`, `999K`, `1M`; the tooltip keeps the exact number), and captions use tabular
    digits.
  - The cutoffs come from that worst case plus a fallback-font margin, which also answers
    Clippy's thin-margin note. Measured in Chromium at 10px: "−1.00 · 999K" is 63.1px in Inter
    and 63.7px in system-ui, and "−1.00" is 28.6px. The cutoffs are now 68px and 32px.
  - Cost: a 13w chart on a ~900px card now shows net only (67px columns), since it reserves
    room for a count that real data (≤ ~70 a week under the collector cap) never reaches.
  - New `frontend/e2e/sentiment-trend-captions.spec.ts` sweeps card widths from 200 to 2000px
    in 13px steps for each window, against boundary fixtures. It fails if any visible caption
    overruns its column. Reverting either the 56px cutoff or `container-type` turns it red.
  - Gotcha: the e2e suite isn't in CI and needs the test proxy (uvicorn boots against the test
    DB). Locally, `npx playwright test` failed because this Playwright version's Chromium wasn't
    downloaded. A throwaway config spreading `playwright.config` with `use.channel: 'chrome'` ran
    it on the installed Chrome instead.
- 2026-10-25 — Promoted to prod (e1f00ad) and checked there. WDC shows 3 of 13 weeks and a dashed
  Oct 5 week (the #372 gap), as expected. Two findings:
  - **The trend waited on the article list.** Prod's live `/news` took 64 s for WDC while
    `/news/trend` answered in under 1 s, and the chart only rendered once `/news` finished.
    Fixed in its own PR: the chart now renders from its own query, and the spinner covers only
    the article list.
  - **A label flip can point against the net.** WDC's Oct 12 week is marked `improving` while its
    net fell from −0.20 to −0.38. Sep 28 tied 6 negative to 6 neutral (ties go negative); Oct 12
    was majority neutral (19 vs 14). That is `classify_flip` as specified, and the Discord alert
    shares it, but busy weeks tend to be majority neutral. So #433's verdict uses the net with a
    dead band rather than the majority label (decided 2026-10-25). Whether the trend's flip
    marker should follow is an open question, not changed here.
- 2026-10-25 — #439 review addressed. Guppy measured `SignalsTab`'s single render unit at CC 83
  (81 before #439). The news panel is now its own `NewsSentimentPanel.tsx`, with `OverallRow`,
  `ArticleRow` and `ArticleList`, and one tone table in place of three ternary chains per chip.
  `SectionHeader` moved to its own module. `SignalsTab` keeps about 60 CC from its other three
  sections. That is recorded as a pre-existing exception, with the rest of the split proposed as
  separate work. Clippy noticed the lift had started showing the Overall row with no articles;
  it is back to showing only alongside articles, with a test.
- 2026-10-25 — #433 PR opened: the Score Changes News column.
  - The verdict is `window_sentiment`: net score with a ±0.10 dead band (`agrees` / `diverges` /
    `mixed`), `thin` under 10 articles or under 25% coverage, and `none` with no articles.
  - Gotcha: the coverage cutoff was going to be a half. Prod's score windows all start Aug 10 and
    run about 75 days, while news history starts Oct 1, so every window is about 30% covered and
    a half would read `thin` for every mover until mid-November. A quarter gives a verdict now,
    and the tooltip always states the coverage.
  - Two set-based news queries per request: one over `symbol IN (...)` with the `fetched_at`
    prefilter, and one `MIN(fetched_at) ... GROUP BY symbol`. Against the test DB through the
    proxy, 30 movers took about 1 s for both. The 116 s the panel took locally is the existing
    per-symbol history loop (235 × ~0.47 s through the proxy), not this change.
  - Test DB: 30 movers came out as 6 agrees, 12 diverges, 9 mixed and 3 none. The none rows are
    foreign tickers (112610.KS, NIB.F) the collector doesn't cover.
- 2026-10-25 — #440 review addressed. Guppy measured `ScoreChangesPanel` at CC 25 and
  `get_fundamental_score_changes` at CC 13 / cognitive 20. The panel is split into header,
  delta cell, row, table and body components. The service's per-symbol work moved into
  `_score_move` and `_is_reported_move`, which keep insufficient history, an unusable score
  and a below-threshold move as three distinct outcomes. Tests were added for each.
