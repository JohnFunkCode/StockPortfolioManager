# Curated arbitrage universe on the Arbitrage page (#415)

**Status:** planned, not started. Issue [#415](https://github.com/JohnFunkCode/StockPortfolioManager/issues/415).
Related: #388 (whole-watchlist pair-trading engine). Design of the scanner itself:
[`docs/architecture/arbitrage.md`](../architecture/arbitrage.md).

## Why

`GET /api/arbitrage/universe` lists every curated link in `arb_universe.yaml`: the pair, its
family, and why the link exists. Only MCP and Sidekick can see it. The Arbitrage page shows scan
results, so it cannot answer "what is being watched, and why".

What the code looked like on 2026-11-13, which narrows the issue as written:

- **The scan already returns every curated pair, rejects included.** The universe has 10 entries
  and the page scans with `top_n=20`. "Show all pairs whether or not they qualify" is what
  `ScanTable` does today.
- **The rationale is shown nowhere.** Each entry's `notes`, `source` and capital-structure inputs
  are in the universe response, but no component renders them (`ArbitrageCard` doesn't either).
- **An errored pair disappears.** A pair whose workup fails is only counted in an "N errored"
  chip; it is not named.
- **The page is blank while the scan runs.** A cold scan is ten full workups and takes tens of
  seconds. The universe endpoint is a YAML read with no price fetches.
- **No new analytics are needed.** `ArbitrageService.get_universe()` already computes
  `holdings_age_days`, `holdings_stale` and `hedge_available`, so the front end only renders.

## Decisions (2026-11-13)

- **Placement: tabs, `Scan | Universe`.** Scan stays the default tab and is unchanged. The
  Universe tab loads from the universe endpoint, independently of the scan.
- **Read-only.** Adding a pair stays a reviewed change to `arb_universe.yaml`. There is no write
  path and this work adds none.
- **An expanded row links to the pair's workup**, the same card and charts a scan row opens. A
  pair is then reachable even when its scan errored.
- **NAV vehicles show their hand-maintained inputs** in the expanded row (units held, senior
  claims, diluted shares, source). They are what goes stale.
- **The Scan tab names its errored pairs** instead of only counting them.

The last three were recommendations John did not object to, not explicit answers; confirm them
in review.

## Mock-up

Three placements were mocked up. The pairs, families, hedges, MSTR's figures and the 122-day
holdings age are real (`arb_universe.yaml`, as of 2026-11-13). Scores, verdicts, "what breaks it"
text and the UNG error are invented for illustration.

### A · Scan and universe tabs (chosen)

```
Arbitrage                                                   [ Discovery ]
  Scan    [Universe · 10]
  ----------------------------------------------------------------------
```

| | Symbol | Family | Underlying | Hedge | Convergence | Holdings as of |
|---|---|---|---|---|---|---|
| ▾ | MSTR | nav vehicle | BTC-USD | IBIT | buyback | 2026-07-14 `122 days old` |
| | | | | | | |
| ▸ | GBTC | nav vehicle | BTC-USD | IBIT | redemption | 2026-07-14 `122 days old` |
| ▸ | USO | commodity etf | CL=F | `futures only` | redemption | — |
| ▸ | GDX | producer | GC=F | GLD | none | — |
| | *6 more pairs* | | | | | |

The expanded MSTR row holds:

> **Why this link exists**
> Converts (~$6.7B) plus four preferred series. Quoting a discount against gross BTC overstates
> it by ~27 points. The $1B common buyback is the only accretion mechanism…
>
> | Units held | Senior claims | Diluted shares | Source |
> |---|---|---|---|
> | 843,775 BTC | $16.5B | 350M | 10-Q Q1 FY2026 |
>
> Open workup →

- `122 days old` is the stale chip (warning colour), shown when `holdings_stale` is true.
- `futures only` is the existing warning chip from `ScanTable`, shown when `hedge_available` is
  false.
- Holdings is `—` for a pair with no `holdings_as_of` (commodity ETFs and producers).
- The capital-figures grid appears only for `nav_vehicle` rows.

**Why chosen:** it answers "what is configured, and why" while the scan is running or has failed,
it gives the full `notes` text room, and it leaves `ScanTable` and the Sidekick's
`arbitrage_scan` card untouched. Its cost is that rationale and score are on different tabs.

### B · Collapsible panel above the scan (rejected)

```
Arbitrage                                  [Universe · 10]  [ Discovery ]
```

| Symbol | Family | Underlying | Hedge | Why this link exists |
|---|---|---|---|---|
| MSTR | nav vehicle | BTC-USD | IBIT | Converts plus four preferred series. Quoting a discount… |
| GBTC | nav vehicle | BTC-USD | IBIT | Included as the control case: post-ETF-conversion it has… |
| USO | commodity etf | CL=F | `futures only` | *8 more, scroll or collapse* |

followed by the scan table as it is today (`9 of 10 pairs` · `1 errored`).

**Why rejected:** the panel is closed by default, so an empty-looking page still doesn't say that
ten pairs are configured. Open, it pushes the scan table down the page, and the rationale has to
be truncated to fit. It was the least code.

### C · One merged table (rejected)

```
Arbitrage                                                   [ Discovery ]
10 curated pairs · 9 scored · 1 errored
```

| Symbol | Family | Score | Verdict | Net disc. | Hedge | Why this link exists |
|---|---|---|---|---|---|---|
| GBTC | nav vehicle | 41.0 | `watch` | 0.12% | IBIT | Included as the control case… |
| MSTR | nav vehicle | 28.5 | `reject` | -9.40% | IBIT | Converts plus four preferred… |
| GDX | producer | 17.2 | `reject` | — | GLD | Miners carry operating leverage… |
| UNG | commodity etf | — | `errored` | — | `futures only` | No price history for NG=F |

All ten rows render at once; scores fill in when the scan finishes.

**Why rejected:** it is the largest change. The page would join two requests, and `ScanTable` is
shared with the Sidekick card, so it would grow or fork. The table is already nine columns wide,
so the rationale is cut to a few words and the capital figures still need an expanding row. The
row order jumps when scores arrive and the table re-sorts. Its one advantage, naming errored
pairs in context, is kept by the last decision above.

## Work (one PR, `Closes #415`)

1. **Client and hook.** `arbitrageApi.getUniverse()` and a `UniverseEntry` type in
   `frontend/src/api/arbitrage.ts`; `useArbitrageUniverse()` in `frontend/src/hooks/useArbitrage.ts`.
   Fix that file's header comment, which still says only the single-pair workup is wired.
2. **`UniverseTable`** in `frontend/src/components/arbitrage/`. It takes fetched data as a prop,
   like `ScanTable`. Colours through `var(--qc-*)` and `tint()` only.
3. **`ArbUniverseCard`** in `frontend/src/components/chat/ArbitrageChartCards.tsx`: the
   self-fetching, no-props wrapper with loading, error and success states.
4. **Register `arbitrage_universe`** with an empty prop spec in both
   `BACKEND_COMPONENT_REGISTRY` (`quantcore/services/chat_tools.py`) and
   `frontend/src/chat/componentRegistry.tsx`. Add it to the `show_component` description and
   point the `list_arbitrage_universe` tool description at it. No new tool, so `READ_ONLY_TOOLS`
   is unchanged.
5. **Page.** Tabs in `ArbitragePage.tsx`; the Universe tab renders `UniverseTable`; "Open workup"
   opens the existing `PairDetail`; the Scan tab lists errored pairs by name.
6. **Tests.** Vitest for `UniverseTable` and `ArbUniverseCard` (loading, error, success, key
   values, stale chip, futures-only chip, NAV-only figures), updated `ArbitragePage` tests, and
   registry parity cases on both sides.
7. **Docs.** `docs/capabilities-matrix.md` (the Tier 3 row leaves, the universe row gains a WebUI
   consumer, the appendix count of operations with no WebUI consumer drops by one, Sidekick
   components go from 17 to 18), `docs/architecture/quantui.md`, and
   `docs/arbitrage-scanner-usage.md`.

No backend route, service, schema or MCP change is planned.

## Review notes (arch-v2 Rule 11)

- The component adds no tool and no write path.
- `notes` and `source` are curated YAML, reviewed by PR, and render as escaped text. No row in
  [`security.md`](../architecture/security.md) is expected to change.

## Out of scope

- A curation write path ("add this discovered pair to the universe").
- Surfacing entries that `ArbitrageRepository._normalise` drops as malformed.

## Checkpoint log

*(append as each step lands)*
