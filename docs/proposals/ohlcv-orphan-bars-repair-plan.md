# Cross-written OHLCV bars left behind by the 2026-07 repair

**Status (2026-11-06): root cause found, repair written, not run on prod.** Running it needs John's
approval. The steps are under [Repair](#repair).

## What was reported

A price-action scan of the watchlist found JCI and DELL with the same bar on 2026-05-25, Memorial
Day, when NYSE was closed: close 341.10, against 137.59 and 139.44 on either side for JCI, and
294.30 and 304.16 for DELL.

## Root cause

These bars are left over from the 2026-06-30 and 2026-07-02 incidents, and no writer is still
producing them.

1. **The original bug, fixed on 2026-07-02 (`09b0b89`).** `yf.download` passed results through
   module-global state. Threaded chain refreshes (4 workers, each computing Bollinger bands on a
   90-day `get_history`) therefore received each other's bars, and `_store_bars` saved them under
   the wrong symbol.
2. **The gap that kept them alive.** `scripts/repair_ohlcv_misalignment.py` re-fetched each symbol
   and upserted the result. That corrected every date the real symbol traded. An upsert can't
   remove a row, though, so a stolen bar survived wherever the real symbol has no bar of its own:
   its market was closed, or it hadn't listed yet. Later refreshes can't reach these rows for the
   same reason.

The 341.10 bar is ASSA-B.ST's real Stockholm session (Stockholm's neighbouring closes are 336.70
and 335.50). It landed on DELL, JCI and R, a group of four like the ones in the original incident.

## Evidence

Every watchlist symbol's stored bars (260 symbols, 128,028 bars from the prod API, 730 days) were
compared with Yahoo's chart API on 2026-11-06:

| Check | Result |
|---|---|
| Rows Yahoo has no bar for | 83 cross-writes on 21 symbols, plus 2 rows that are not (below) |
| Each row's source | 82 match another symbol's bar on date and volume, and Yahoo confirms that symbol really has the bar. The other is TSLA's 2026-07-01 bar on 2208.HK: same OHLC, volume differs by 0.4%, so it looks like an earlier snapshot of that session |
| Date range of the 83 rows | 2026-04-01 to 2026-07-01. The start is exactly 90 days before 06-30, which matches the 90-day fetch |
| Dates where Yahoo does have a bar | No stored close differs from Yahoo's adjusted close by more than 8%, so the July repair worked on these |

**Affected symbols:** 034020.KS, 112610.KS, 2208.HK, 2727.HK, 5E2.SI, 600875.SS, 601615.SS, 6971.T,
AKSO.OL, AUTO.OL, BESI.AS, CBRS, DELL, JCI, NOV, ORA, R, RIVN, STM, SU.PA, WULF. The rows are in
[`scripts/ohlcv_orphan_bars_20261106.json`](../../scripts/ohlcv_orphan_bars_20261106.json). CBRS is
the largest case: 30 of KC's bars from before CBRS's first real bar.

**Deliberately left out of the repair:**

- `5E2.SI` 2026-05-27: a flat, zero-volume bar at the symbol's own price. It is a holiday
  placeholder, not a cross-write.
- `2208.HK` 2026-11-06: the symbol's own latest session, which Yahoo's reply didn't include yet.
- `KVUE` (495 rows) and `DOMO` (474 rows): Yahoo now returns a single bar for each, so the
  comparison says nothing about them. Their rows aren't cross-writes.

**Ingest dates confirm it.** A read-only dry run against prod on 2026-11-06 found all 83 rows
ingested on the incident dates: 40 on 2026-06-30 and 43 on 2026-07-02.

**Why this looks over:** the newest cross-write is dated 2026-07-01. US symbols have no bar on the
later NYSE holidays (07-03, 09-07), and those are the dates that would show a foreign bar landing
on a US symbol. Two things now prevent the race: `_YF_DOWNLOAD_LOCK`, which
`test_architecture_guards.py` enforces, and yfinance itself, whose 1.7.0 `download` collects
results in a per-call context rather than module-global state.

**Not verified:**

- **Symbols outside the watchlist.** The dry run reports identical bars across the whole table and
  found none outside the manifest, but that only catches bars still byte-identical to another
  stored row.
- **Harvester plans** built while these rows existed. Not checked.

## Why no writer change

Nothing in the current writers produces these rows, so this PR changes no writer. Two fixes were
considered and rejected:

- **Make `_store_bars` delete rows missing from a fetch.** It would heal these rows on the next
  refresh, but it also means merging the PR silently deletes prod data with no approval. Worse, a
  gappy Yahoo reply would then delete real history. `fetch_history` already drops NaN closes and
  `_drop_invalid_bars` drops impossible bars, so a gap in a reply is routine.
- **Reject bars on NYSE holidays at write time.** The table holds foreign listings that do trade on
  those days, and CBRS's 30 rows and the foreign symbols' rows are on dates NYSE was open.

## Repair

`scripts/repair_ohlcv_orphan_bars.py` removes exactly the manifest's rows. It is a dry run unless
told otherwise. `--apply` needs `--confirm-target` to name the database, stops if any row no longer
holds the manifest's values, and runs in one transaction.

On John's approval, with the prod proxy running (`./runProxy-MAC.sh`) and `QUANTCORE_DB_DSN` on it:

```bash
python scripts/repair_ohlcv_orphan_bars.py
```

Check that all 83 rows report `match` (or `gone`), that they were ingested on 2026-06-30 or 07-02,
and read any other identical bars it lists. Then:

```bash
python scripts/repair_ohlcv_orphan_bars.py --apply --confirm-target <the target it printed>
```

A second run should report `gone=83`. Indicators are computed from `ohlcv` when read, so they
correct themselves once the rows are gone.

## Gotchas

- **The scan understated this.** It found 2 of 93 symbols because it only looked at market holidays
  on US-listed names. The real count is 21 symbols, and most rows sit on foreign holidays or before
  a listing, on dates when NYSE was open. A holiday filter can't find those; comparing with what
  Yahoo returns can.
- **Identical bars aren't enough either.** Yahoo adjusts a source symbol's own history for later
  dividends, so a stolen bar can differ from its source in price (STM against 6971.T) while the
  volume still matches. The source's volume and the bar's open-to-close ratio are the reliable
  match.
- **A duplicate doesn't say which side is wrong.** ASSA-B.ST and DELL hold the same row and only
  one is real. Removing every duplicate would have deleted ASSA-B.ST's real session.
- **`close` and `volume` are `REAL`, and the manifest is rounded.** The first prod dry run called 5
  untouched rows `changed`: 19.943926 stored against 19.9439 listed is further apart than a relative
  float4 tolerance allows. `classify` now also allows for the 4-decimal rounding.
- **In a worktree the script targets `localhost`, not prod.** `quantcore.db` reads the `.env` beside
  it and freezes `DB_DSN` at import, and a worktree has no `.env`. A `load_dotenv()` in the script's
  `main()` runs too late to help. The printed `target:` line is what showed this. Set
  `QUANTCORE_DB_DSN` in a worktree, and read that line before trusting a result.
- **The proxy can't run inside the Claude sandbox.** It starts and reports ready, then every
  connection dies with "server closed the connection" because it can't reach the Cloud SQL Admin API.
  The real error is only in `cloud-sql-proxy.log`.
