# Daily jobs: report, capture tail, news, FinBERT

> Moved verbatim from `CLAUDE.md`, which keeps the rules an agent must not break as a short
> summary and links here for the explanation. Edit the detail here, the rule there.

## The daily job (`main.py`) and the legacy report script

`main.py` is the daily Cloud Run Job (`quantcore-report` — the service name kept the old
spelling; the work did not). It loads John's positions and the shared watchlist from the database
(via `get_services().portfolio` / `.watchlist` — never from `portfolio.csv` or `watchlist.yaml`),
fetches prices/metrics, and then runs **in this order, which is a deliberate isolation property**
— the cheap, high-value side effects land before anything that can run long: notifications →
options capture → gamma/GEX (from the snapshot) → capture health check → news staleness →
fundamentals warming. The three steps that carry the weight:

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
3. **Fundamentals warming** (last, deliberately — #373) — `run_fundamentals_warming(capture_symbols, …)` refreshes the
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
   | `OPTIONS_CAPTURE_COVERAGE_FLOOR` | `0.90` | alarm when fewer than this fraction of the *optionable* universe's chains landed today (symbols with no listed options are excluded) |
   | `OPTIONS_CAPTURE_FAILURE_CEILING` | `0.50` | alarm when more than this fraction of a step's attempts failed, or of the capture's attempts were captured only partially |
   | `OPTIONS_CAPTURE_NO_OPTIONS_CEILING` | `0.25` | alarm when more than this fraction of the universe returned no options chain |

   An unparseable value logs a warning and falls back to the default — a typo in a Cloud Run env
   var must not silently disarm the alarm.

   **Why warming stays last (#373).** When Yahoo rate-limits the run, whatever runs last starves.
   The end-of-day chain capture can't be redone the next day — an OI snapshot missed is missed
   for good — while fundamentals can be re-warmed any night and converge anyway. So the capture
   tail keeps priority, and the fix was to make it cheaper rather than to move it.

   **Closed-market days and the capture tail.** `main.py` exits 0 before doing anything on a day
   the NYSE is closed (`is_trading_day`, holidays computed from rules in
   `quantcore/analytics/market_time.py` — no calendar dependency; Cloud Scheduler fires Mon–Fri, so
   this guard is for weekday holidays). Notifications are skipped too, deliberately. After
   notifications, `run_capture_tail` runs the options capture, then `record_gamma_and_gex` (which is
   how `gamma_wall_history` and `gex_history` accumulate daily; its walk rotates with the date so a budget that can't cover the
   universe still visits everyone over a few nights), then `check_capture_health`. The health check
   reads what actually **landed in the database** (`OptionsService.capture_counts`) rather than
   trusting the loop's own tally, and sends one Discord alarm (`send_capture_gap_alert`) on low
   chain coverage, a high failure rate, an exhausted capture budget, or too many symbols with no
   listed options.

   **Symbols with no listed options (#374).** About 29 universe symbols (foreign listings, small
   caps, a few possibly delisted) have no options at all; when Yahoo lists no expirations,
   `get_full_options_chain` returns `no_listed_options: true` and persists nothing. The capture
   summary counts them as `no_options` (and names them in `no_options_symbols`), **not**
   `duplicate` — a `duplicate` is a non-empty chain the store declined to persist again. Only the
   explicit flag qualifies; `expiration_count: 0` alone is not proof. When expirations are listed
   but **every** chain fetch fails (e.g. 429s), the service raises instead of persisting an empty
   snapshot, so the symbol is a `failed` capture, not a silent "captured" one; expirations that
   fail inside an otherwise-captured chain are reported as `failed_expirations` and summed into the
   summary's `partial_expirations_failed`/`partial_symbols`. Coverage is `chains / (universe − no_options)`; before
   this, those symbols sat in the denominator and held coverage at ~76% every night. The GEX step
   is passed the same list as `skip`, so it spends no Yahoo calls re-fetching chains that don't
   exist. Excluding them is only safe because of the fourth trigger: when more than
   `OPTIONS_CAPTURE_NO_OPTIONS_CEILING` of the universe reports no options, that is Yahoo returning
   empty expirations, not a wave of delistings, and it alarms with sample symbols. Every step budget is
   clamped to what is left of the task deadline (`_remaining_budget`), so budgets can't add up past
   the timeout. The tail is wrapped in an outer `try/except` that never raises.

   **GEX reads the persisted snapshot, not Yahoo (#373).** `record_gamma_and_gex` calls
   `OptionsService.record_gex_from_snapshot`, which computes delta-adjusted OI (first 3
   expirations) and the GEX profile (up to the 6 the capture stores) from the full-chain snapshot
   the capture just wrote, and saves them through `save_gamma_wall`/`save_gex_summary` — **zero
   Yahoo calls**. Before this, the step re-fetched every chain the capture had just persisted,
   roughly doubling the run's Yahoo traffic. It returns `None` (counted as `no_snapshot`, symbol
   skipped) when there is no full snapshot from today's **Eastern** trading day or its price is
   unusable. The live MCP/REST `get_delta_adjusted_oi`/`get_gex_profile` are unchanged; both
   paths share the same per-row math.

   **A partial capture gets no GEX row.** The snapshot doesn't record that an expiration failed,
   so a GEX computed from it would silently understate exposure, often by the near-dated
   expiration that carries most of the gamma. The capture's `partial_symbols` are passed to the
   GEX step as `partial` and counted as `partial_skipped`: that day's row is missing rather than
   wrong. When partials exceed `OPTIONS_CAPTURE_FAILURE_CEILING` of the attempts, the health check
   alarms with sample symbols and names Yahoo rate-limiting when most of them saw a 429
   (`rate_limited_partials`). A partial chain still persists, so without this trigger a
   rate-limit that only cost some expirations would never reach `failed` and would stay silent
   (#378 review).

   *Gotcha:* the snapshot stores IV as a **percent rounded to 1 decimal** (`31.4`, not
   `0.314`); `_snapshot_rows` divides by 100. The rounding moves the Greeks in the 4th
   significant figure, accepted, and `tests/test_gex_profile.py`'s parity test pins the tolerance
   against the live path.

   *Gotcha:* the capture's in-memory contract key is `iv`, but it is saved to the
   `implied_vol` column, and `get_latest_snapshot` (`SELECT *`) returns rows keyed
   `implied_vol`. `_snapshot_rows` reads `implied_vol` first. Until that fix it read `iv`
   alone, found nothing on every real row, and every contract silently took the 30% fallback
   in `_usable_rows`; the parity test missed it because its fixture was keyed `iv` too. It is
   now keyed `implied_vol`, the real shape. Gamma-wall and GEX history written between #373
   (2026-10-14) and the fix used a flat 30% IV.

   **429 backoff (#373).** `YFinanceGateway.expirations` and `.option_chain` retry a rate-limit
   error (HTTP 429 / "Too Many Requests" / `YFRateLimitError`) at most twice, sleeping 2 s then
   6 s; any other error is raised at once. Every 429 bumps the gateway's thread-safe
   `rate_limited` counter. The capture attributes counter deltas to symbols, so its summary
   reports `rate_limited_failures` (failed symbols that saw a 429), `rate_limited_partials`
   (partially captured symbols that saw one) and `rate_limited_calls` (total 429 responses). When rate-limited failures are more than half of the failures,
   `check_capture_health` says **"Yahoo rate-limited N symbols (M 429 responses)"** instead of the
   generic failure line, so the alarm names the cause. The backoff fits the budget because the
   capture loop checks its deadline before each symbol.

   **Test runs 45 minutes after prod (#373).** Prod keeps its slots (report 17:00, news 17:30
   ET); test's `quantcore-report-daily` runs at **17:45** and `quantcore-news-daily` at
   **18:15**, so neither of test's Yahoo-heavy jobs overlaps prod's. The news slot is
   reproducible with `scripts/ensure_news_job.sh --schedule "15 18 * * 1-5"`; the report
   scheduler has no script, so its slot is set with
   `gcloud scheduler jobs update http quantcore-report-daily --project quantcore-test-20260606 --location us-central1 --schedule "45 17 * * 1-5" --time-zone America/New_York`.
   *Caveat:* this only helps if Yahoo counts the two projects as one client (shared egress).
   Cloud Run in two projects without a NAT may not share IPs; compare prod's
   `rate_limited_calls` for a week before and after to find out.

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
created with `scripts/ensure_news_job.sh [--prod] [--schedule "CRON"]` (idempotent; `--schedule` sets the cron on create and re-asserts it on an existing entry, which is otherwise left alone; copies service account, Cloud SQL and
secrets from the `quantcore-report` Job). Re-running it is also the repair for a Scheduler that fails with **code 7 (PERMISSION_DENIED)**: the Scheduler's OIDC service account needs `roles/run.invoker` on the `quantcore-news` Job, and the script re-asserts that binding on every run — it once granted it only when it created the Scheduler entry, so an entry created another way failed every night with no execution started (#372). The `deploy.yml` and `prod-rollout.yml` roll-outs skip its image update until the
Job exists, but now emit a `::warning::` annotation instead of a silent `echo`. Two more alarms close the
remaining silent paths: `news_job.py` flags new articles stored with **zero** scored (`score_unscored`
returns 0 when FinBERT won't load), and the daily **report Job** runs `alert_if_news_stale`
(`main.py`, via `SentimentService.news_freshness`) — hosted there because a news Job that never runs
cannot report its own absence. Ceiling: `NEWS_STALE_MAX_AGE_HOURS` (120, sized for a Monday-holiday weekend). The age is read from a per-symbol `fetch_log` heartbeat (`interval='news'`, written by `collect_news` via `NewsStore.record_collection`) rather than `MAX(news_articles.fetched_at)`, which only moves on new inserts and would false-alarm on a quiet news stretch. An age of `None` means **no** heartbeat rows exist. On test, the usual cause was the unit suite: `test_no_heartbeat_reads_none` has to delete every `interval='news'` row, and before it restored them, a suite run against Cloud SQL test wiped the real Job's heartbeat. The next report then alarmed with `age Noneh` (2026-10-14). The test now restores the rows; any new test that clears shared rows must do the same.

**FinBERT weights are baked into the images, never downloaded at runtime** (issue #280). The
builder stage of `Dockerfile.api` and `Dockerfile.news` runs `scripts/bake_finbert.py`, which
downloads a **pinned** `ProsusAI/finbert` revision (`DEFAULT_REVISION` in the script) — an
allow-list of five files, each checked against `EXPECTED_SHA256` before anything parses it — re-saves
it as a single safetensors file in `/opt/models/finbert`, and verifies it loads offline; both images
set `FINBERT_MODEL_PATH=/opt/models/finbert` and `HF_HUB_OFFLINE=1`. `_ensure_finbert`
(`quantcore/services/sentiment.py`) loads from that path with `local_files_only=True` when it is
set and falls back to the Hub id pinned to `FINBERT_REVISION` when it is not (local dev), under a lock so concurrent first
callers share one load. The news Job warms the model on a background thread
(`SentimentService.warm()`) while collecting; the API deliberately stays lazy, because Cloud Run
throttles CPU outside requests and a startup preload would tax every cold start. Rules that follow:

- **Bumping the model is a reviewed change** to `DEFAULT_REVISION`, `EXPECTED_SHA256` (the bake)
  and `FINBERT_REVISION` (`sentiment.py`) together, never a follow of Hub `main` — a new checkpoint
  shifts the score distribution and puts a step into stored sentiment history.
  `--build-arg FINBERT_REVISION=<sha>` still reaches the bake, but a revision whose files don't
  match the committed hashes fails the build. Show the bump scores the same with
  `scripts/finbert_parity.py OLD_DIR NEW_DIR`.
- **No pickle-format model is ever fetched or loaded** (#381). Unpickling a `.bin`/`.pt`/`.ckpt`
  runs code. The bake's `snapshot_download` uses an explicit `allow_patterns` list
  (`model.safetensors`, config, tokenizer files), so `pytorch_model.bin` is never requested — which
  also keeps the old 429 protection: no bot-PR lookup, one format, ~438 MB. Pickles are refused in
  four places: `verify_hashes()` on the download, the bake's `verify()` on the output,
  `_ensure_finbert` on a local `FINBERT_MODEL_PATH`, and the Dockerfiles' `appuser` check. Every
  model `from_pretrained` passes `use_safetensors=True`, and `tests/test_no_pickle_model_loads.py`
  fails CI on `torch.load`, `pickle.load`, `trust_remote_code` or a model load without it. (Until
  #381 the bake passed `use_safetensors=False` and deserialized the upstream `.bin` — a 429
  workaround, not a security choice; plan and gotchas in
  [`finbert-safetensors-plan.md`](../proposals/finbert-safetensors-plan.md).)
- **Baked weights must be readable by the runtime user** (#375). The bake runs as root in the
  builder stage and `save_pretrained` wrote `model.safetensors` as `0600 root`; the final stage
  runs as `appuser` (uid 1000), so every load failed — and **safetensors reports that `EACCES` as
  "No such file or directory"**, which sent the first diagnosis looking for a missing file. Scoring
  silently returned 0 for every article from 2026-10-02 until the fix. Three guards now: the bake
  chmods the tree `a+r` (directories `a+rx`); `verify()` refuses any file or directory the
  runtime user can't read before it loads anything (a root-run reload can't see the problem —
  root reads `0600`); and both Dockerfiles read one byte of every baked file **as `appuser`**
  after `USER appuser`, so an unreadable layer fails the build. When a local load still fails,
  `_ensure_finbert` logs each file's mode and `os.access` result next to the error.

**The nightly scorer is newest-first, so an outage leaves a backlog it never reaches.**
`score_unscored` takes the newest unscored articles up to its limit (500), which is right for
freshness, but once each night brings that many new articles the rows left unscored by a FinBERT
outage stay unscored for good. After any outage, drain them oldest-first with
`scripts/backfill_news_sentiment.py --since YYYY-MM-DD [--batch 500] [--dry-run]`. It needs
FinBERT, so on a deployed project it runs as a one-off execution of the `quantcore-news` Job on the
news image (invocation in `readme.md`). It stops with exit 1 when a batch scores nothing while rows
remain (FinBERT didn't load, or the rows have no text), so a broken model can't loop. Nothing else
needs re-deriving: `get_news_sentiment` and `get_sentiment_trend` aggregate `news_articles` at read
time.

Since issue #147 `main.py` **does not render the HTML report**. That moved verbatim to
**`scripts/generate_portfolio_report.py`** (`--output PATH`, or `--publish` to upload to S3),
which the Raspberry Pi runs via `runOnPi.sh`. Two consequences worth keeping straight:

- The Pi runs the script and **not** `main.py`, because the Cloud Run Job already sends the
  notifications and captures the snapshots — running both would double every alert.
- `--publish` now **fails loudly** when `BUCKET_NAME`/`BUCKET_KEY` are missing, checked up front
  before minutes of price fetching. The old code returned `None` and let the caller report
  success, which is how the public page could stop updating unnoticed (the original #147 defect).
