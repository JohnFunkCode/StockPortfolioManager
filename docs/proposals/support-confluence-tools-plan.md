# Proposal: Support-Level Analysis Tools (ATR, Anchored VWAP, Volume Profile, OI-Change, GEX, Confluence)

Status: **DRAFT — for team discussion before implementation**
Date: 2026-07-15
Tracking: [Issue #93](https://github.com/JohnFunkCode/StockPortfolioManager/issues/93) — progress comments posted there at the completion of each phase

## Context

A codebase audit against a professional support-level techniques list (gamma/GEX, volume profile, anchored VWAP, OI analysis, ATR, confluence) found six high-value tools buildable from data sources the system already has. The current analysis workflow (trim-and-hold, VWAP-reclaim entries, gamma-wall supports, drawdown-calibrated trailing stops distorted by earnings gaps) manually assembles confluence across many tool calls — these tools automate that, culminating in a single `get_support_confluence` composite.

Every tool follows [`architectural-standard-v2.md`](architectural-standard-v2.md): pure math in `quantcore/analytics/`, business logic as a service method, thin FastAPI route (`QuantCoreJSONResponse` + `route_error_plain`, no Pydantic response model for analytics GETs), curated `@mcp.tool()` exactly one `rest_client.get()` deep in `fastMCPTest/stock_price_server.py` (that server hosts price AND gamma/DAOI tools — split is LLM-facing, not by service class). No registry changes needed anywhere — all methods extend existing `PricesService`, `OptionsService`, `OptionsStore`, `RecommendationsService`.

## Build order / PR grouping

- **PR A — Phase 1 (ATR bands) + Phase 2 (Anchored VWAP)** — both pure price/technicals in `PricesService`; Phase 2's swing-helper extraction is reused by Phase 6.
- **PR B — Phase 3 (Volume Profile)** — standalone analytics module.
- **PR C — Phase 4 (OI change) + Phase 5 (Vanna/Charm + signed GEX)** — both options-domain.
- **PR D — Phase 6 (Support Confluence)** — composes everything; lands after A–C.
- **PR E — Phase 7 (QuantUI front end)** — surfaces confluence in the Technical Analysis panel; needs Phase 6's endpoint merged (or at least running locally).

Each PR ends with `PYTHONPATH=. python scripts/check_openapi_snapshot.py --update` and committing `docs/openapi-surface.txt` (CI diffs it).

**Documentation pass (required at the completion of every phase):** scan `readme.md`, `CLAUDE.md`, and `docs/**` for any section describing tools, endpoints, MCP servers, schema, or the daily job, and update them for that phase's new functionality before the PR is opened — readme's MCP tool listings / API examples, CLAUDE.md's architecture sections (new analytics modules, `gex_history` as the 17th table, daily-job chain capture, frontend card), and any affected docs/proposals status notes.

---

## Phase 1 — ATR Bands + ATR trailing stop

**Analytics** — `quantcore/analytics/indicators.py` (next to `rsi_series`/`macd_series`):
- `true_range_series(high, low, close) -> pd.Series` — `TR = max(H−L, |H−C_prev|, |L−C_prev|)`; first bar = H−L.
- `atr_series(high, low, close, period=14) -> pd.Series` — Wilder: `tr.ewm(alpha=1/period, adjust=False).mean()`. Document the seed convention so exact-value tests match.

**Service** — `PricesService` (`quantcore/services/prices.py`):
```python
def get_atr_bands(self, symbol, period=14, band_mult=2.0, stop_mult=3.0,
                  interval="1d", lookback=250) -> dict
```
Fetch via `self.get_history()` (the single OHLCV seam). Return: `atr`, `atr_pct`, `upper_band`/`lower_band` (close ± band_mult·ATR), `atr_trend` (expanding/contracting vs 3-month mean), **chandelier stop** = `max(High, 22 bars) − stop_mult·ATR` with `stop_distance_pct`, last ~20 bars `{date, atr, upper, lower}` history, and an interpretation contrasting with the drawdown-based stop (ATR re-adapts after earnings gaps within ~period bars).

**REST** — `api/routers/prices.py`: `GET /api/securities/{ticker}/atr-bands` (params mirror service signature). Follow the `get_vwap_history` idiom.

**MCP** — `get_atr_bands` in `fastMCPTest/stock_price_server.py`; docstring: volatility-calibrated stop placement, "prefer over get_stop_loss_analysis trailing % when earnings gaps pollute drawdown history".

**Tests** — `test_atr_bands.py`: exact-value Wilder test on a hand-computed ~20-bar series; service test with the `make_service()` Mock-repo/gateway + synthetic DataFrame pattern from `test_prices_history_policy.py`; gap-robustness case (inject −15% gap bar, assert stop stays bounded).

---

## Phase 2 — Anchored VWAP

**Analytics** — `quantcore/analytics/indicators.py`:
- `anchored_vwap(df, anchor_idx) -> float` — `Σ(TP·V)/ΣV` from anchor to last, `TP=(H+L+C)/3` (same convention as `get_vwap`).
- `find_swings(highs, lows, swing_bars=3) -> dict` — generalizes the confirmed-swing scan in `get_higher_lows` to return `{"lows": [...], "highs": [...]}` (high at i requires `high[i] >= high[i±k]`; skip last `swing_bars` unconfirmed bars). **Refactor `get_higher_lows` to call it** — output must stay identical (keep existing `<=` low semantics), guarded by a regression test.

**Service** — `PricesService`:
```python
def get_anchored_vwap(self, symbol, anchor_date: str | None = None,
                      lookback_days=365, swing_bars=5) -> dict
```
Anchor resolution, each source in its own try/except so one failure never kills the call:
- `user` — explicit `anchor_date` (flagged first if given)
- `earnings` — `self._yf.earnings_dates(symbol)` (defensive handling per `fundamentals.py`), last 2 past dates
- `gap` — `self.get_gap_analysis(symbol)` top 2 `all_gaps` by `|gap_pct|`
- `swing` — `find_swings` on 1y daily; most recent confirmed swing high + low
- `52w_high` / `52w_low` — argmax(High)/argmin(Low) over 252 bars

Dedupe anchors within 3 trading days (priority: user > earnings > 52w > gap > swing). One daily-history fetch (respect the 730-day gateway cap). Return `price`, `anchors: [{type, date, label, avwap, distance_pct, position}]` sorted by |distance_pct|, plus `nearest_support`/`nearest_resistance` (closest AVWAP below/above).

**REST** — `GET /api/securities/{ticker}/anchored-vwap?anchor_date=&lookback_days=&swing_bars=`; declare above `/{ticker}/vwap` per the existing ordering convention.

**MCP** — `get_anchored_vwap(symbol, anchor_date=None, lookback_days=365)`; docstring explains auto-anchor types and AVWAP as institutional cost-basis level.

**Tests** — `test_anchored_vwap.py`: exact cumulative AVWAP on a tiny DataFrame; anchor resolution with stubbed gateway, dedupe/priority asserts; **`get_higher_lows` regression** (snapshot output on fixed synthetic series pre/post refactor).

---

## Phase 3 — Volume Profile

**Analytics** — new module `quantcore/analytics/volume_profile.py` (pure numpy/pandas):
```python
def build_volume_profile(highs, lows, volumes, bins=50, value_area_pct=0.70) -> dict
def find_volume_nodes(bin_centers, bin_volumes, hvn_ratio=1.25, lvn_ratio=0.60) -> dict
```
- Bin edges `linspace(min(low), max(high), bins+1)`; distribute each bar's volume **uniformly across [low, high]** by bin-overlap fraction (degenerate high==low → all to that bin). Uniform intrabar distribution is the standard approximation; close-only binning distorts daily profiles.
- **POC** = argmax bin. **Value area (70%)**: start at POC, repeatedly annex the higher-volume adjacent bin until ≥70% of total → `(val, vah)`.
- **HVN/LVN**: 3-bin centered rolling-mean smoothing; local maxima ≥ 1.25·median → HVN; local minima ≤ 0.60·median → LVN.

**Service** — `PricesService`:
```python
def get_volume_profile(self, symbol, days=365, interval="1d",
                       bins=50, value_area_pct=0.70) -> dict
```
Interval ∈ {"1d","1h"}; when `1h` + `days > 60`, surface a `note` (get_history already caps intraday) rather than erroring. Return `poc`, `value_area {low, high}`, `hvns`/`lvns`, `nearest_hvn_below/above`, `nearest_lvn_below/above` vs current close, `in_value_area`, compact `profile` array, interpretation (HVN = acceptance/support, LVN = rejection/air-pocket).

**REST** — `GET /api/securities/{ticker}/volume-profile?days=&interval=&bins=&value_area_pct=`.

**MCP** — `get_volume_profile`; docstring: daily/1y for structural levels, `interval="1h"` for the 60-day micro-profile.

**Tests** — `test_volume_profile.py`: exact 3-bar overlap math → POC/VA/HVN/LVN; degenerate high==low bar; all-in-one-bin edge; service test with `make_service()` stub asserting nearest-node selection.

---

## Phase 4 — OI-Change Analysis (2×2)

**Repository** — `quantcore/repositories/options_repository.py` (`OptionsStore`), one new method:
```python
def get_oi_timeseries(self, symbol, days=30, expiration=None) -> list[dict]
```
SQL: `DISTINCT ON (captured_at::date, expiration, kind, strike)` over `options_contracts ⋈ options_expirations ⋈ options_snapshots` filtered `symbol = %s AND chain_type = 'full'`, ordered `..., captured_at DESC` to keep the last full-chain snapshot per day. **Note: `captured_at` is TEXT** — cast `captured_at::timestamptz` (ISO strings) for the `>= now() - (%s || ' days')::interval` window and `::date` for grouping. Select `snap_date, underlying price, expiration, kind, strike, open_interest, volume, implied_vol`.

**Service** — `OptionsService` (`quantcore/services/options.py`):
```python
def get_oi_change_analysis(self, symbol, days=30, top_n=10,
                           min_oi=100, expiration=None) -> dict
```
- **Graceful degradation is required**: if < 2 distinct snapshot dates, return `{symbol, snapshot_dates, oi_changes: [], note: "OI history accumulates only when full-chain snapshots are captured — call get_full_options_chain periodically"}` — never a 500. (History is forward-accumulating like `gamma_wall_history`.)
- Compare earliest vs latest date in window (plus latest-vs-previous as `latest_day_change`). Per (expiration, kind, strike): ΔOI, ΔOI%; Δunderlying from snapshot prices.
- **2×2 classification** (movers with |ΔOI| ≥ min_oi): OI↑+price↑ → `new_longs`; OI↑+price↓ → `new_shorts`; OI↓+price↑ → `short_covering`; OI↓+price↓ → `long_liquidation`. Options overlay: large put-OI builds below spot → put-writing support; call-OI builds above → call-wall resistance.
- Return `snapshot_dates_used`, `underlying_change_pct`, `top_oi_builds`/`top_oi_drains` (classified + interpreted), `put_oi_support_strikes`, `call_oi_resistance_strikes`, summary.

**Daily capture (decision #2)** — extend `main.py`'s daily report job to call `get_full_options_chain` (in-process services, per the cron rule; cap ~6 expirations) for portfolio + watchlist symbols, so `options_contracts` accumulates the OI time series. Wrapped per-symbol in try/except — a failed chain fetch must never fail the report. This lands in PR C so history starts accumulating immediately.

**REST** — `api/routers/options.py`: `GET /api/securities/{ticker}/options/oi-change?days=&top_n=&min_oi=&expiration=`.

**MCP** — `get_oi_change_analysis` in stock_price_server.py (alongside `get_delta_adjusted_oi`); docstring states the sparse-history caveat.

**Tests** — `test_oi_change_tools.py`: DB-backed per `test_options_contract_tools.py` (QUANTCORE_TEST_DB_DSN prelude + `db_safety.assert_not_production()`, ticker `ZZOICHG`): two `save_full_chain` snapshots with shifted OI/price → assert deltas, DISTINCT-ON dedupe (two snapshots same day), classification labels; tearDown cleanup. Plus a Mock-store unit test covering the <2-dates degradation path (no DB).

---

## Phase 5 — Vanna/Charm + Signed GEX Profile

**Analytics** — `quantcore/analytics/options_math.py` (next to `bs_gamma`; q=0 like existing fns; each accepts precomputed `d1`):
```python
def bs_vega(S, K, T, sigma, r, d1=None)   # S·φ(d1)·√T
def bs_vanna(S, K, T, sigma, r, d1=None)  # −φ(d1)·d2/σ, d2 = d1 − σ√T (call == put)
def bs_charm(S, K, T, sigma, r, is_call, d1=None)
    # −φ(d1)·(2rT − d2·σ√T)/(2T·σ√T); q=0 → call/put coincide; keep is_call for forward-compat
```

**Service** — `OptionsService`, **new method (do NOT extend `get_delta_adjusted_oi`)** — DAOI persists into `gamma_wall_history` with a `gamma_wall_method` marker; overloading a persisted payload consumers parse is the wrong seam.
```python
def get_gex_profile(self, symbol, max_expirations=6, risk_free_rate=0.045) -> dict
```
- Mirror the DAOI loop: `self._yf.expirations`/`option_chain`, one `bs_d1` per contract, IV fallback 0.30, skip K≤0 / OI≤0.
- **Signed GEX** per contract: `gamma · OI · 100 · S² · 0.01` (dollar gamma per 1% move); calls +, puts − ; include `"convention": "dealers long calls / short puts"` in the payload.
- Per-strike ladder `{strike: net_gex, call_gex, put_gex}`. **Zero-gamma flip**: cumulative net GEX over ascending strikes, linear-interpolate the first sign change (same idea as the existing `flip_crossing`). Report `top_positive_gex_strike` (call wall/pin) and `top_negative_gex_strike` (put support/vol trigger).
- Aggregates: `net_gex`, `regime` (positive_gamma = dampening / negative_gamma = amplifying), `net_vanna_exposure`, `net_charm_exposure` (Σ sign·greek·OI·100) with flow-tilt interpretations.
- Return `gex_ladder` (top ~20 strikes by |net_gex| plus all within ±10% of spot), `zero_gamma_level`, `dist_to_zero_gamma_pct`, `by_expiration` summaries.

**Persistence (decision #4)** — new `gex_history` table (17th table in `quantcore/db.py` `init_schema()`): `(symbol, date_only, net_gex, zero_gamma_level, regime)` with `UNIQUE(symbol, date_only)` ON CONFLICT upsert (mirror `gamma_wall_history`). `OptionsStore.save_gex_summary()` + `get_gex_history(symbol, since_days)`; `get_gex_profile` persists its summary on each call (last-write-wins per day), so the daily chain-capture pass (Phase 4) also accumulates GEX regime history for free. The per-strike ladder is NOT persisted.

**REST** — `GET /api/securities/{ticker}/options/gex-profile?max_expirations=&risk_free_rate=`.

**MCP** — `get_gex_profile` in stock_price_server.py; docstring: GEX walls for support/resistance, zero-gamma as the volatility-regime boundary, vanna/charm as hedge-flow tilts.

**Tests** — extend `test_options_math.py` (exact hand-computed vega/vanna/charm at S=100, K=100, T=0.25, σ=0.2, r=0.045; sign checks; d1-passthrough equivalence). New `test_gex_profile.py`: OptionsService with stub gateway (synthetic calls/puts DataFrames) → assert call+/put− signs, zero-gamma interpolation on a constructed crossing, no-expirations degradation, and that `save_gex_summary` is called with the computed summary (Mock store).

---

## Phase 6 — Support Confluence (composes Phases 1–5)

**Service** — `RecommendationsService` (`quantcore/services/recommendations.py`) — it already composes `self._prices` + `self._options` (no registry change). Model on the supports-dict idiom in `get_stop_loss_analysis`.
```python
def get_support_confluence(self, symbol, tolerance_pct=1.0,
                           max_expirations=4, max_zones=5) -> dict
```
Level sources — options-dependent ones each in own try/except; failures recorded in `methods_failed`:

| Source | Call | Weight |
|---|---|---|
| Gamma wall | `_options.get_delta_adjusted_oi` | 1.0 |
| GEX strikes + zero-gamma | `_options.get_gex_profile` | 1.0 |
| Volume profile POC/VA/HVNs | `_prices.get_volume_profile` | 0.9 |
| Anchored VWAPs | `_prices.get_anchored_vwap` | 0.9 |
| Put-OI support / call-OI resistance | `_options.get_oi_change_analysis` | 0.8 |
| Expected move (spot ± EM) | `_options.get_options_analytics` | 0.7 |
| Rolling VWAP | `_prices.get_vwap` | 0.7 |
| SMA 200 / 100 / 50 | `_prices.get_technicals_table` | 0.7 / 0.6 / 0.6 |
| Prev week & month H/L | `_prices.get_history("1wk"/"1mo")`, bar −2 | 0.6 |
| Prev day H/L | `get_history("1d", 10)`, bar −2 | 0.5 |
| SMA20 + Bollinger | `_prices.get_stock_price` (bb_middle/lower/upper) | 0.5 |
| ATR bands + chandelier stop | `_prices.get_atr_bands` | 0.5 |
| Fib retracements (0.382/0.5/0.618/0.786 of last major swing) | `find_swings` on 1y daily | 0.3 |

Weights follow the professional-importance guidance (dealer positioning + volume acceptance highest, fib lowest); exposed as module-level `_CONFLUENCE_WEIGHTS` dict so they're testable and tunable.

**Clustering + scoring:**
1. Collect `{level, method, weight, detail}`; drop outside spot ± 25%.
2. Sort ascending; greedy sweep: add levels while `level ≤ weighted_center·(1 + tolerance_pct/100)`, recomputing the weight-weighted center per addition.
3. Zone score = `Σ (max weight per distinct method) + 0.2·n_extra_levels` — independent methods dominate.
4. Partition into support (center < price) / resistance (center > price); rank by score, top `max_zones` each.

Return: `{symbol, price, tolerance_pct, methods_available, methods_failed, support_zones: [{zone_low, zone_high, center, distance_pct, score, method_count, contributors}], resistance_zones, strongest_support, interpretation}`.

**REST** — `api/routers/recommendations.py`: `GET /api/securities/{ticker}/support-confluence?tolerance_pct=&max_expirations=&max_zones=`.

**MCP** — `get_support_confluence` in stock_price_server.py; docstring: THE composite support/resistance tool — prefer over individual tools when the question is "where is support".

**Tests** — `test_support_confluence.py` (per `test_recommendations_service.py`): Mock prices/options returning canned dicts for every source; assert clustering merge/split at tolerance, multi-method zones outscore single-method stacks, options-source exceptions land in `methods_failed` without failing the call, correct support/resistance partitioning.

---

## Phase 7 — Front end: Support Confluence in the Technical Analysis panel

Surface the Phase-6 endpoint in the QuantUI React app (`frontend/`), on the securities detail page's **Technical Analysis tab** (Tab 1 of `frontend/src/components/securities/SecurityDetailPage.tsx` — a `Stack` of MUI `Paper` cards).

**Types** — `frontend/src/api/securitiesTypes.ts` (model on `TechnicalSignalsResponse`):
```ts
interface SupportContributor { method: string; level: number; weight: number; detail?: string }
interface SupportZone { zone_low: number; zone_high: number; center: number;
                        distance_pct: number; score: number; method_count: number;
                        contributors: SupportContributor[] }
interface SupportConfluenceResponse { symbol: string; price: number; tolerance_pct: number;
  methods_available: string[]; methods_failed: string[];
  support_zones: SupportZone[]; resistance_zones: SupportZone[];
  strongest_support: SupportZone | null; interpretation: string }
```
Re-export via `securities.ts` like the existing types.

**API client** — `frontend/src/api/securities.ts`, add to `securitiesApi` (next to `getTechnicals`):
`getSupportConfluence: (ticker) => apiRequest<SupportConfluenceResponse>(...)`.

**Hook** — `frontend/src/hooks/useSecurities.ts`, `useSupportConfluence(ticker)` mirroring `useTechnicalSignals`: `useQuery({ queryKey: ['support-confluence', ticker], queryFn, enabled: !!ticker, staleTime: 15*60*1000 })`. The endpoint fans out to many sub-analyses server-side — long staleTime, no auto-refetch.

**Component** — new `frontend/src/components/securities/SupportConfluenceCard.tsx` (dedicated component, not inline JSX). Rendered as an additional `Paper` card in the Technical Analysis tab Stack (after "Signal Summary"). Reuse the presentation patterns from `SignalsTab.tsx`: `SectionHeader`/`MetricRow`/`SignalBadge`/`SectionError` helpers, loading/error idiom (`CircularProgress` / `SectionError` with retry). Content:
- Header row: current price + strongest-support chip (center + distance_pct, colored by proximity).
- **Support zones table** (`Table size="small"`): zone range (low–high), distance %, score, method-count chip; a secondary line listing contributor methods. Resistance zones as a second table or a toggle.
- Interpretation text as italic caption; `methods_failed` shown as a muted warning line when non-empty.

**Tests** — co-located `SupportConfluenceCard.test.tsx` (vitest + Testing Library, per `ChatRail.test.tsx` pattern): `vi.mock` the hook/api module, wrap in `QueryClientProvider` (retry: false), assert zones render with scores/contributors, loading spinner, error state with retry, methods_failed warning.

**No proxy/config changes**: the Vite dev server already proxies `/api` → `http://127.0.0.1:5001`; the deployed Express server proxies `/api/*` with the injected JWT the same way.

**Deploy**: merge → `deploy.yml` auto-builds `quantcore-ui` and rolls the test `quantui` service; verify at the test URL, then promote to prod via `prod-rollout.yml` dispatch with the 7-char SHA.

---

## Verification (per PR and final)

1. `python -m unittest discover`; check diff coverage locally (`coverage run -m unittest discover && coverage xml && diff-cover coverage.xml --fail-under=85`) — CI enforces ≥85% on changed lines, ≥37% global.
2. `PYTHONPATH=. python scripts/check_openapi_snapshot.py --update`; commit `docs/openapi-surface.txt`.
3. REST locally: `uvicorn api.main:app --port 5001`; curl each new endpoint on a liquid symbol (NVDA) + one degradation case (oi-change on a no-snapshot ticker must return the note payload, not 500).
4. MCP: `python scripts/ci_wrapper_smoke.py` (covers new tools in existing servers automatically); exercise tools end-to-end against the running REST tier.
5. `python -m unittest test_architecture_guards` — no service/DB imports leaked into MCP tool bodies.
6. OI tool: capture `get_full_options_chain` twice across two synthetic `captured_at` days in the test DB and confirm deltas.
7. Front end: `cd frontend && npx vitest run --coverage` + `npm run build`; eyeball the card locally, then on the test `quantui` service before prod promotion.
8. Documentation: confirm the phase's doc pass happened — `readme.md`, `CLAUDE.md`, and `docs/**` accurately describe the new tools/endpoints/tables before the PR is opened.

## Decisions (resolved 2026-07-15)

1. **GEX sign convention** — hardcode the standard "dealers long calls / short puts" heuristic (calls +, puts −), stated in the payload. Matches SpotGamma/SqueezeMetrics-style naive GEX so our numbers are comparable to public tools. No configurable convention until dealer-positioning data exists to justify one.
2. **OI snapshot cadence** — **yes**: the daily report job (`main.py`) captures full chains for portfolio + watchlist symbols (capped ~6 expirations), via in-process services per the cron rule. History can't be backfilled, so this ships in PR C so accumulation starts as early as possible. (Folded into Phase 4.)
3. **Confluence weights** — hardcoded module-level `_CONFLUENCE_WEIGHTS`; promoting to query params later is non-breaking if usage shows the defaults are wrong.
4. **GEX persistence** — persist a **daily summary row** per symbol — `(symbol, date, net_gex, zero_gamma_level, regime)` — in a new `gex_history` table (gamma_wall_history-style, last-write-wins per day), computed in the same daily pass as the chain capture. The per-strike ladder stays recompute-on-demand. (Folded into Phase 5.)
