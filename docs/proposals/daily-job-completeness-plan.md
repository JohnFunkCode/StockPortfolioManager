# Daily job completeness (issue #68)

Closes the gaps between #68's cache-warming requirements and what the daily job (`main.py`) did
after #147 and #234. Decisions, as settled with John:

1. News collection is a **separate Job** (`news_job.py`, `Dockerfile.news`, `quantcore-news`).
2. **Every** step, notifications included, is skipped on NYSE-closed days.
3. Microstructure persistence and the put/call series are out of scope — a separate issue.

## Checkpoint log

| Step | What landed |
|---|---|
| 1 | `is_trading_day` / `nyse_holidays` in `quantcore/analytics/market_time.py`; `main.py` exits early on closed days. |
| 2 | `OptionsStore.capture_counts` + `OptionsService.capture_counts` (chains, gamma wall, GEX rows for a day). |
| 3 | `main.py` split into `capture_options_chains`, `record_gamma_and_gex`, `run_capture_tail`, each with its own budget (`OPTIONS_CAPTURE_BUDGET_SECONDS`, `GEX_RECORD_BUDGET_SECONDS`) clamped to the task deadline. |
| 4 | `check_capture_health` + `Notifier.send_capture_gap_alert` — coverage floor and failure ceiling alarms. |
| 5 | `news_job.py`, `Dockerfile.news`, `build-news` in `cloudbuild.yaml`, news Job steps in `deploy.yml` / `prod-rollout.yml`, `Notifier.send_news_gap_alert`, `SentimentService.score_unscored`. |
| 6 | Docs: `CLAUDE.md`, `readme.md`, `Dockerfile.report` header. |

## Gotchas

- **Chains are keyed on the Eastern market day, gamma/GEX on the UTC date.** They disagree after
  ~8pm ET, so a test (or health check) must ask each question with its own key.
- **Alert titles carry the date** because `Notifier.send_notifications` dedupes on title via
  `notification.log`; an undated title would suppress tomorrow's alarm.
- **The workflows skip the news step until the Job exists.** Creating the Job and its Cloud
  Scheduler entry is manual, once per project (trading days, after the close). Until then a green
  deploy does not mean news is being collected.
- **`prod-rollout.yml` now tolerates a missing source image** for keyproxy and news; a typo in
  `quantcore-news` therefore skips rather than fails. The digest env var is
  `QUANTCORE_NEWS_DIGEST`.
- **DB-backed tests could not be run in the authoring sandbox** (port 5434 blocked), so
  `tests/test_options_repository.py::test_capture_counts_*` was only syntax-checked there; the
  same sandbox accounts for the other DB-test failures in a local full run. CI is the first
  real run.
- **"collected" meant "no exception", not "got articles".** Both news fetchers catch their own
  errors and return `[]`, so a dead feed looked like success: the first test smoke run reported
  230/230 collected, 0 new, and the test DB's newest article was 3.5 months old (issue #275).
  The Job now counts a symbol as *empty* when nothing was fetched and alarms on the empty
  fraction (`NEWS_EMPTY_CEILING`); RSS failures log at `warning`. `Ticker.news` returns `[]` (even on
  yfinance 1.7.0) and the RSS endpoint 404s, but `yf.Search(sym, news_count=10).news` works, so
  `YFinanceGateway.news` now uses it. Search is a text match and returns stories about other
  companies, hence the `relatedTickers` filter; coverage of non-US tickers is uneven (VWS.CO
  returned 0). Cloud Run egress still to be confirmed by a test-Job run (#275).
