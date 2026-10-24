# News sentiment trend on the Signals tab (#411)

**Status:** planned 2026-10-24; nothing built yet. Issue [#411](https://github.com/JohnFunkCode/StockPortfolioManager/issues/411).
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
  whose label flips carries `flip: "to_negative" | "to_positive"`, and the chart marks it.
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
   - `classify_flip(prev_label, new_label) -> "to_negative" | "to_positive" | None` — the
     notifier's rule, verbatim: an unchanged label is no flip, and a flip involving neutral counts
     only when negative is on one side.
   - `weekly_buckets(daily_rows, *, today, weeks, history_start)` — fills every Monday-start week
     in the window and sums the counts. It sets `net_score`, `label`, `thin`, `in_progress`,
     `before_history` and `flip`. It also returns the coverage figures: `weeks_with_articles`,
     `total_articles` and `history_start`.
2. **`NewsStore`:**
   - Filter and group on the same date expression. Today it filters on `fetched_at` but groups
     on `COALESCE(published_at, fetched_at)`, so an old article fetched recently lands in a bucket
     outside the window.
   - Bucket the date in America/New_York, not the session's UTC.
   - Add `first_article_date(symbol)`.
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

## Checkpoint log

- 2026-10-24 — Placement chosen (Signals, option B) from two mock-ups; plan written (#432).
- 2026-10-24 — Score Changes sentiment cue filed as #433; it depends on PR 1's analytics module.
