# Daily jobs: report, capture tail, news, FinBERT

> Moved verbatim from `CLAUDE.md`, which keeps the rules an agent must not break as a short
> summary and links here for the explanation. Edit the detail here, the rule there.

## The daily job (`main.py`) and the legacy report script

`main.py` is the daily Cloud Run Job (`quantcore-report` — the service name kept the old
spelling; the work did not). It loads John's positions and the shared watchlist from the database
(via `get_services().portfolio` / `.watchlist` — never from `portfolio.csv` or `watchlist.yaml`),
fetches prices/metrics, and then does three things **in this order, which is a deliberate
isolation property** — the cheap, high-value side effects land before anything that can run long:

1. **Notifications** — `Notifier(portfolio).calculate_and_send_notifications()`, plus
   `alert_if_watchlist_empty()`.
2. **Options capture** — a full options chain snapshot per symbol (in-process
   `OptionsService.get_full_options_chain`, capped expirations, per-symbol try/except) so
   open-interest history accumulates daily for `get_oi_change_analysis`. The universe
   (`capture_symbols`) is John's positions + the global watchlist + **every other owner's**
   positions (issue #126 decision #5). **This capture also feeds the watchlist options screen**
   (`analyze_options_watchlist`, `OptionsScreeningService` with `source="cache"`), which never
   calls Yahoo. A capture gap therefore shows up there as a `stale` list (symbols with no full
   chain from the last trading day), not as a slow or failing screen. Non-US listings and OTC ADRs
   that the capture can't chain always come back stale. That is expected, not an alarm.
3. **Fundamentals warming** — `run_fundamentals_warming(capture_symbols, …)` refreshes the
   fundamentals cache over that same universe, **oldest-`fetched_at` first**, under a wall-clock
   budget. A cold pass is ~10 serial yfinance calls per symbol and is *expected* not to finish;
   the universe converges over a few nights. Ordering comes from
   `FundamentalsService.cache_freshness()`; the pass is wrapped in an outer `try/except` that
   never raises, so a warmer failure cannot retroactively fail a run whose notifications already
   went out. That silence is then made loud by `alert_if_fundamentals_stale()`, which re-reads
   freshness **after** warming and fires a Discord alarm on either trigger — coverage below a
   floor, or an oldest-age above a ceiling. Tunable per-deployment without a code change:

   | Env var | Default | Meaning |
   |---|---|---|
   | `REPORT_TASK_TIMEOUT_SECONDS` | `1800` | report Job task deadline used to cap the warming pass |
   | `FUNDAMENTALS_WARM_BUDGET_SECONDS` | `900` | wall-clock budget for the warming pass; capped 60 seconds before the task deadline |
   | `FUNDAMENTALS_STALE_COVERAGE_FLOOR` | `0.80` | alarm below this in-TTL fraction |
   | `FUNDAMENTALS_STALE_MAX_AGE_HOURS` | `168` | alarm above this oldest age |
   | `OPTIONS_CAPTURE_BUDGET_SECONDS` | `600` | wall-clock budget for the options-chain capture; also clamped to the task deadline |
   | `GEX_RECORD_BUDGET_SECONDS` | `300` | wall-clock budget for the gamma-wall/GEX recording step |
   | `OPTIONS_CAPTURE_COVERAGE_FLOOR` | `0.90` | alarm when fewer than this fraction of the universe's chains landed today |
   | `OPTIONS_CAPTURE_FAILURE_CEILING` | `0.50` | alarm when more than this fraction of a step's attempts failed |

   An unparseable value logs a warning and falls back to the default — a typo in a Cloud Run env
   var must not silently disarm the alarm.

   **Closed-market days and the capture tail.** `main.py` exits 0 before doing anything on a day
   the NYSE is closed (`is_trading_day`, holidays computed from rules in
   `quantcore/analytics/market_time.py` — no calendar dependency; Cloud Scheduler fires Mon–Fri, so
   this guard is for weekday holidays). Notifications are skipped too, deliberately. After
   notifications, `run_capture_tail` runs the options capture, then `record_gamma_and_gex` (the only
   scheduled caller of `get_delta_adjusted_oi`/`get_gex_profile`, so `gamma_wall_history` and
   `gex_history` accumulate daily; its walk rotates with the date so a budget that can't cover the
   universe still visits everyone over a few nights), then `check_capture_health`. The health check
   reads what actually **landed in the database** (`OptionsService.capture_counts`) rather than
   trusting the loop's own tally, and sends one Discord alarm (`send_capture_gap_alert`) on low
   chain coverage, a high failure rate, or an exhausted capture budget. Every step budget is
   clamped to what is left of the task deadline (`_remaining_budget`), so budgets can't add up past
   the timeout. The tail is wrapped in an outer `try/except` that never raises.

   The fundamentals batch endpoint accepts at most 25 unique symbols. It trims, uppercases, and
   deduplicates before enforcing that limit, and rejects blank or oversized batches with HTTP 422
   before provider/database work begins. Larger universes must be split across requests.

The job exits immediately on NYSE-closed days (`is_trading_day`), notifications included.

**News collection is a separate Job** (issue #68): `news_job.py`, image `quantcore-news` from
`Dockerfile.news`, because FinBERT needs `requirements-ml.txt` (torch), which the lean report image
must not carry — and so a slow scoring pass cannot delay the notifications. Per-symbol try/except,
a shared wall-clock budget, one `score_unscored` pass at the end (so a truncated collection is still
scored), and one Discord alarm (`send_news_gap_alert`) on a high failure rate, an exhausted budget
a scoring error, or **empty sources** (both fetchers returned nothing for more than the ceiling of
symbols, once enough were attempted — fetchers swallow their own errors, so "collected" alone
only means no exception; issue #275). The yfinance source is `YFinanceGateway.news`, which reads
`yf.Search(symbol).news` filtered by `relatedTickers` — `Ticker.news` and Yahoo's RSS feed both
return nothing now, silently. Env: `NEWS_TASK_TIMEOUT_SECONDS` (1800),
`NEWS_COLLECT_BUDGET_SECONDS` (900), `NEWS_FAILURE_CEILING` (0.50), `NEWS_EMPTY_CEILING` (0.90), `NEWS_EMPTY_MIN_ATTEMPTS` (10). The Job and its Cloud Scheduler entry are **one-time infra** per project,
created with `scripts/ensure_news_job.sh [--prod]` (idempotent; copies service account, Cloud SQL and
secrets from the `quantcore-report` Job). The `deploy.yml` and `prod-rollout.yml` roll-outs skip its image update until the
Job exists, but now emit a `::warning::` annotation instead of a silent `echo`. Two more alarms close the
remaining silent paths: `news_job.py` flags new articles stored with **zero** scored (`score_unscored`
returns 0 when FinBERT won't load), and the daily **report Job** runs `alert_if_news_stale`
(`main.py`, via `SentimentService.news_freshness`) — hosted there because a news Job that never runs
cannot report its own absence. Ceiling: `NEWS_STALE_MAX_AGE_HOURS` (120, sized for a Monday-holiday weekend). The age is read from a per-symbol `fetch_log` heartbeat (`interval='news'`, written by `collect_news` via `NewsStore.record_collection`) rather than `MAX(news_articles.fetched_at)`, which only moves on new inserts and would false-alarm on a quiet news stretch.

**FinBERT weights are baked into the images, never downloaded at runtime** (issue #280). The
builder stage of `Dockerfile.api` and `Dockerfile.news` runs `scripts/bake_finbert.py`, which
downloads a **pinned** `ProsusAI/finbert` revision (`DEFAULT_REVISION` in the script), re-saves it
as a single safetensors file in `/opt/models/finbert`, and verifies it loads offline; both images
set `FINBERT_MODEL_PATH=/opt/models/finbert` and `HF_HUB_OFFLINE=1`. `_ensure_finbert`
(`quantcore/services/sentiment.py`) loads from that path with `local_files_only=True` when it is
set and falls back to the Hub id when it is not (local dev), under a lock so concurrent first
callers share one load. The news Job warms the model on a background thread
(`SentimentService.warm()`) while collecting; the API deliberately stays lazy, because Cloud Run
throttles CPU outside requests and a startup preload would tax every cold start. Rules that follow:

- **Bumping the model is a reviewed one-line change** to `DEFAULT_REVISION` (or
  `--build-arg FINBERT_REVISION=<sha>`), never a follow of Hub `main` — a new checkpoint shifts the
  score distribution and puts a step into stored sentiment history.
- The bake downloads with `use_safetensors=False` on purpose: left alone, `transformers` also
  fetches the Hub bot's safetensors-conversion PR ref and pulls **both** formats (836 MB, extra Hub
  calls, more 429 exposure). Don't "simplify" that flag away.

Since issue #147 `main.py` **does not render the HTML report**. That moved verbatim to
**`scripts/generate_portfolio_report.py`** (`--output PATH`, or `--publish` to upload to S3),
which the Raspberry Pi runs via `runOnPi.sh`. Two consequences worth keeping straight:

- The Pi runs the script and **not** `main.py`, because the Cloud Run Job already sends the
  notifications and captures the snapshots — running both would double every alert.
- `--publish` now **fails loudly** when `BUCKET_NAME`/`BUCKET_KEY` are missing, checked up front
  before minutes of price fetching. The old code returned `None` and let the caller report
  success, which is how the public page could stop updating unnoticed (the original #147 defect).
