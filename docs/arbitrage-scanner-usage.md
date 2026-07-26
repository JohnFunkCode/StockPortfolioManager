# Arbitrage Scanner — usage guide

How to *drive* the scanner. For how it's built (services, analytics, schema), see the
**Arbitrage Scanner** section of [`CLAUDE.md`](../CLAUDE.md).

---

## Read this first: it is supposed to say no

The scanner finds securities whose price has stretched against a structurally linked
underlying — a treasury company vs its coin stack, a fund vs its reference future, a miner
vs the metal it sells. It then tries to talk you out of the trade.

**Expect almost everything to score `reject`.** As of 2026-07-26 all ten curated pairs do.
That is the design working, not a broken scan. Genuine convergence trades need four things
at once — a statistically cointegrated spread, a mechanism that actually forces the gap
closed, a hedge you can execute from an equity account, and no carry bleed while you wait —
and that combination is rare.

The scoring is deliberately inverted: **spread width only qualifies a candidate; the
convergence mechanism ranks it.** A screen sorted by discount alone would have put MSTR top
in July 2026 on a 37% headline gap that was really ~10% once senior claims were netted out.

Verdict bands: `candidate` ≥ 60 · `watch` ≥ 35 · `reject` below that.

---

## The four tools

All four are on the **stock-price** MCP server, and mirror
`GET /api/arbitrage/*` on the REST tier.

### 1. `list_arbitrage_universe` — what's tracked

> *"What pairs does the arbitrage scanner track?"*

No parameters. Returns the ten curated pairs with their family, underlying, hedge
instrument, convergence mechanism, and a staleness flag on the hand-maintained holdings.

| Family | Pairs |
|---|---|
| `nav_vehicle` | MSTR·BTC-USD, GBTC·BTC-USD |
| `commodity_etf` | USO·CL=F, UNG·NG=F, GLD·GC=F, CPER·HG=F |
| `producer` | GDX·GC=F, FCX·HG=F, AR·NG=F, MARA·BTC-USD |

### 2. `analyze_arbitrage_pair` — the deep single-pair workup

> *"What's MSTR's actual discount to its bitcoin holdings?"*
> *"Analyse GDX against gold over the last 180 days"*
> *"Analyse RIOT against BTC-USD"* ← ad-hoc, not in the curated list

| Parameter | Default | Bounds |
|---|---|---|
| `security` | required | — |
| `underlying` | from the curated map | ≤ 32 chars |
| `days` | 365 | 30–3650 |
| `zscore_window` | full sample | 2–3650 |

`underlying` is the escape hatch: supply it and you can analyse any pair, curated or not.
Ad-hoc pairs get spread statistics but no NAV math and no convergence claim — nothing
declares their capital structure.

Returns four blocks — `statistics` (hedge ratio + stability, cointegration, z-score,
half-life, trend), `nav` (NAV vehicles only), `hedge`, and the score with its `factors`,
`reasons`, and `breaks_on`.

### 3. `scan_arbitrage` — ranked sweep

> *"Scan for arbitrage candidates, top 5"*
> *"Scan only the NAV vehicles over two years"*

| Parameter | Default | Bounds |
|---|---|---|
| `kinds` | all three | `nav_vehicle`, `commodity_etf`, `producer` (comma-separated) |
| `top_n` | 20 | 1–100 |
| `days` | 365 | 30–3650 |

A pair that errors (bad symbol, no history) lands in `errors` rather than sinking the scan.

### 4. `discover_arbitrage_pairs` — find links you haven't declared

> *"Sweep NEM, AEM, GOLD, FCX and SCCO against the commodity panel"*
> *"Check whether any of these names are cointegrated with natural gas"*

| Parameter | Default | Bounds |
|---|---|---|
| `symbols` | required | ≤ 25 tickers per call |
| `references` | the panel below | ≤ 10 |
| `days` | 365 | 30–3650 |
| `min_abs_correlation` | 0.4 | 0–1 |
| `require_economic_link` | `true` | — |

Default reference panel: `GC=F`, `HG=F`, `CL=F`, `NG=F`, `BTC-USD`, `DX-Y.NYB`.

**Leave `require_economic_link` on.** Over a wide enough sweep, cointegration tests
reliably find statistically significant pairs with no causal relationship at all. The gate
checks the symbol's sector/industry plausibly connects to the reference before keeping it.
Turning it off is for exploration, not for trade generation.

Each symbol costs a price-history fetch plus a profile lookup, which is why the list is
capped at 25.

---

## Reading the output

The score is a **product of named factors**, all returned so any number is attributable:

```
opportunity × evidence × convergence × hedge × carry × trend × freshness
```

| Factor | Range | What moves it |
|---|---|---|
| `opportunity` | 0–100 | Gap size — NAV discount if computable, else spread z-score |
| `evidence` | 0.35–1.0 | Cointegration level; halved with no measurable half-life, ×0.6 if the half-life exceeds the 180-day horizon, ×0.8 if the hedge ratio is unstable |
| `convergence` | 0.35–1.0 | `redemption`/`conversion`/`deal_terms` 1.0 · `buyback`/`index_event` 0.7 · `none` **0.35** |
| `hedge` | 0.5 or 1.0 | Halved when the only clean hedge is a futures contract |
| `carry` | 0–1 | Fraction of the gap surviving the wait, given the bleed |
| `trend` | 0.75 or 1.0 | 0.75 when the spread is *widening* — and only when that trend is statistically significant |
| `freshness` | 0.85 or 1.0 | 0.85 when curated holdings are over 45 days old |

Alongside them, `breaks_on` lists what kills the trade, and `reasons` explains each factor
in prose.

### Worked example — MSTR (live prod, 2026-07-26)

```
score 9.7  verdict reject  basis nav_discount
nav:     premium_discount_pct  -16.92     ← the real number
         gross_premium_discount_pct  -41.79  ← the headline that misleads
         carry_drag_pct  2.21   exposure_ratio  1.72
factors: opportunity 42.3 · evidence 0.35 · convergence 0.7
         hedge 1.0 · carry 0.936 · trend 1.0 · freshness 1.0
breaks_on: ["Negative carry (senior claims serviced out of NAV)"]
```

The two discount figures are the whole point. **−41.2% is the gap to gross assets;
−15.8% is what survives netting out the converts and four preferred series that sit senior
to the common.** Always read `premium_discount_pct`; `gross_premium_discount_pct` is
reported only so the difference is visible.

`exposure_ratio 1.70` says each $1 of MSTR carries $1.70 of BTC — so a 1:1 dollar short
leaves the position materially net long.

The reasoning behind every penalty is written up in
`docs/analysis results/MSTR_BTC_arbitrage_assessment_2026-07-14.md`.

### Example scan output (live prod, 2026-07-26)

```
17.0  reject  GLD   commodity_etf   conv=redemption   hedge=false
 9.7  reject  MSTR  nav_vehicle     conv=buyback      hedge=true
 8.7  reject  USO   commodity_etf   conv=redemption   hedge=false
 8.5  reject  CPER  commodity_etf   conv=redemption   hedge=false
 4.9  reject  UNG   commodity_etf   conv=redemption   hedge=false
```

GLD tops it on gap size and a real redemption mechanism, then loses half its score because
the hedge is futures-only. Values move with the market; treat these as shape, not truth.

### Discovery: expect a lot of nothing

A sweep of the obvious miner names returns **zero pairs** today:

```
GET /api/arbitrage/discover?symbols=NEM,AEM,FCX,SCCO   →   count 0
```

That is not a bug, and it is worth understanding why before you conclude the tool is
broken. Pull the near-miss apart with `analyze_arbitrage_pair`:

```
NEM vs GC=F   correlation 0.733   half_life 6.4d   beta 1.45   r² 0.93
              cointegration statistic -2.87  vs  -3.04 critical (10%)  →  false
```

Newmont tracks gold closely and reverts fast — but the Engle-Granger residual test misses
its loosest threshold by **0.17**. The pair sits on the boundary, so it flips between runs
as bars roll in: the same sweep on the test environment earlier the same day *did* return
it. Discovery is deliberately strict, and a near-miss is reported as a miss.

Two practical consequences:

- **Don't treat discovery output as stable.** Re-running tomorrow can add or drop
  boundary pairs. If you care about one, pin it with `analyze_arbitrage_pair` and read the
  statistic rather than the boolean.
- **`count: 0` with an empty `skipped` list means the pairs were tested and rejected** —
  not that data was missing. Symbols with no usable history appear in `skipped` instead.

Discovered pairs carry no NAV math and no convergence claim — they're candidates for
*curation*, not for trading.

---

## Limits worth knowing

- **No futures hedging.** USO, UNG, GLD and CPER all report `hedge_available: false`. Their
  gaps are information about roll bleed and tracking error, not executable spreads.
- **NAV math needs curated holdings.** GBTC currently returns `nav.available: false` — its
  coins-per-share decays with the fee and isn't in the YAML. Add it and the NAV path
  switches on.
- **Holdings go stale.** No free API publishes a treasury's coin count or its preferred
  stack, so `holdings_as_of` is hand-maintained. Past 45 days the scanner flags it and
  docks the score rather than pretending the figure is current.
- **Branch/beta caveats are surfaced, not hidden.** An unstable hedge ratio shows up in
  `breaks_on` — the ratio you size with today may not hold.

---

## Adding a pair

Edit [`arb_universe.yaml`](../arb_universe.yaml) at the repo root (same convention as
`watchlist.yaml`):

```yaml
- security: MSTR
  kind: nav_vehicle          # nav_vehicle | commodity_etf | producer
  underlying: BTC-USD        # any yfinance symbol
  hedge_instrument: IBIT     # equity/ETF only — null if futures-only
  convergence_mechanism: buyback
  # nav_vehicle only, and NAV math stays off until these are present:
  holdings_units: 843775
  holdings_as_of: 2026-07-14
  senior_claims_usd: 16_500_000_000
  annual_senior_cost_usd: 854_000_000
  diluted_shares: 350_000_000
  source: "10-Q Q1 FY2026 + The Block treasury tracker"
  notes: >-
    Free text; surfaced with the analysis.
```

`convergence_mechanism` is the ranking lever: `none` | `redemption` | `conversion` |
`buyback` | `index_event` | `deal_terms`. Be honest with it — claiming `redemption` for a
vehicle with no redemption right triples its score.

Set `hedge_instrument: null` rather than inventing a proxy; the scanner would rather tell
you a gap is unhedgeable than rank a trade you can't put on.

---

## Environment gotcha

`.mcp.json` points AI clients at **prod**, and prod only updates when
`prod-rollout.yml` is dispatched manually. Merging to `main` deploys to **test** only.

So a scanner change that is merged, green, and live on test is still invisible to Claude
Code until someone runs:

```bash
gh workflow run prod-rollout.yml -f image_tag=<7-char-sha>
```

Both the **api** image (the routes) and the **mcp** image (the tool definitions) have to
land for the MCP path to work end to end — the workflow promotes the whole set.

Prod also requires `QUANTCORE_MCP_TOKEN` to be a valid prod JWT in the environment Claude
Code launches from. If it's unset, every data tool returns `401: … Not enough segments`
while the wrapper-local health check still passes — which is misleading. Mint one with
`scripts/mint_prod_jwt.py`; they expire after 90 days.

Quick check that prod actually has the scanner:

```bash
curl -s -H "Authorization: Bearer $QUANTCORE_MCP_TOKEN" \
  "https://quantcore-api-127961694257.us-central1.run.app/api/arbitrage/universe" | jq '.count'
# 10
```
