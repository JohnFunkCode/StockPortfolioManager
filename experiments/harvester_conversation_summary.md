# Stock Harvesting Planner + SQLite Storage — Conversation Summary

Date generated: 2026-02-08 19:49:34Z

## Goal
Build a systematic **profit-harvesting (“ladder”) strategy** for stocks, then persist plans to **SQLite** so the app can:
- show stored plans,
- create notification/alert logic when price hits harvest targets,
- report which symbols are currently at/above a harvest point and how many shares to sell.

## Core harvesting concept
- Start with a position of **s0 shares** at a starting price.
- Define a **principal floor** `V0` (capital to preserve).
- A harvest occurs when the position value exceeds `(1 + H) * V0`.
- At a harvest trigger, sell **whole shares only** while keeping remaining position value ≥ `V0`.
- Harvest points are represented as a **price target ladder** (rungs).

## Evolution of requirements
1. Fixed growth + harvesting at fixed time intervals.
2. Constraint: **sell only whole shares** (no fractional shares).
3. Trigger by **percentage gain** (e.g., +20% over V0), not time.
4. Replace fixed growth-rate input with **360 days of historical prices**.
5. Add **effective tax rate** on harvest cashflows.
6. Produce a **price target ladder** (targets + shares to sell + expected time).
7. Combine simulator + taxes + ladder; fetch price history via `yfinance`.
8. Shift to a forward-looking ladder starting from **current price**.
9. Add per-rung summaries: projected harvested cash, cumulative harvest, remaining value, total wealth vs initial.
10. Add “starting position summary”.
11. Print/explain `H` and `n_iterations`.

## Forward ladder from 360-day history
A forward plan is built from:
- `P_current`: most recent price
- inferred drift `r_daily` from `P_start -> P_current` over the window
- `H` and `n_iterations`

### Dynamic H (volatility-adjusted)
Compute:
- daily log returns from history
- annualized volatility: `sigma_annual = sigma_daily * sqrt(252)`
- `H = clamp(alpha * sigma_annual, min_H, max_H)`
Store both `H` and the volatility used.

## yfinance implementation note
- yfinance changed `auto_adjust` default to `True`, which can remove the `Adj Close` column.
- Fix: call `yf.download(..., auto_adjust=False)` to reliably get `Adj Close`.
- If `Adj Close` is still missing, fall back: `Adj Close = Close`.

## SQLite design (store full history)
You chose to store **full OHLCV + adj_close daily bars** locally for reproducibility and to avoid reliance on repeated API calls.

### Data model layers
1. **symbols**: ticker master table
2. **price_bars_daily**: OHLCV + adj_close per symbol per date
3. **plan_templates**: strategy definition (dynamic-H params, window size, etc.)
4. **positions**: starting purchase/holdings info (separate from plan)
5. **plan_instances**: a generated executable plan (template + symbol + optional position + derived stats)
6. **plan_rungs**: ladder steps with execution state
7. **alerts**: notification rows (recommended: one active alert for next pending rung)

### Full schema (high level)
- `symbols(symbol_id, ticker, ...)`
- `price_bars_daily(symbol_id, bar_date, open, high, low, close, adj_close, volume, data_vendor, ingested_at)`
- `plan_templates(template_id, name, is_dynamic_h, history_window_days, n_iterations, alpha, min_h, max_h, fixed_h, drift_method, vol_method, stats_price_series, ...)`
- `positions(position_id, symbol_id, opened_at, entry_price, shares, cost_basis_total, ...)`
- `plan_instances(instance_id, template_id, symbol_id, position_id NULL, status, created_at, asof_date, price_asof, shares_initial, v0_floor, capital_at_risk, history_end_date, history_window_days, r_daily, annual_vol, h_threshold, n_iterations, stats_price_series, supersedes_instance_id, ...)`
- `plan_rungs(rung_id, instance_id, rung_index, target_price, shares_before, shares_sold_planned, shares_after_planned, expected_days_from_now, ... status, triggered_at, executed_at, ...)`
- `alerts(alert_id, rung_id UNIQUE, symbol_id, instance_id, threshold_price, alert_type, status, fired_at, ...)`

Recommended constraints/indexes:
- `(symbol_id, bar_date)` PRIMARY KEY for price bars (enables upsert)
- partial unique index: one ACTIVE plan per symbol
- unique index: one alert per rung
- common indexes on `(symbol_id, status)` and `(instance_id, status)`.

## SQL queries used
### Upsert daily bars
- Upsert symbol, fetch `symbol_id`.
- Upsert bar row by `(symbol_id, bar_date)` with `ON CONFLICT DO UPDATE`.

### Find active plan instance for a symbol
- Join `plan_instances` to `symbols` by ticker, filter `status='ACTIVE'`.

### Next pending rung + create alert
- Fetch next pending rung (smallest `rung_index` where `status='PENDING'`).
- Create/refresh alert for that rung (`ON CONFLICT(rung_id) DO UPDATE`).
- Disable other active alerts for that instance.

### Mark alert fired
When polled price crosses target (for `PRICE_GE`: `price >= threshold_price`):
- update `alerts` to `FIRED` (set `fired_at`, `fired_price`, `last_checked_at`)
- update rung to `TRIGGERED` (set `triggered_at`, `trigger_price`)

## Python module created: `HarvesterPlanStore.py`
A storage-backed orchestrator that:
1. Initializes SQLite schema.
2. Fetches OHLCV+Adj Close via yfinance and stores into `price_bars_daily`.
3. Builds a forward plan using stored history and `design_forward_ladder_from_history` from `HarvesterExperiment.py`.
4. Stores a new plan instance + ladder rungs + next-rung alert, superseding any prior active plan for the symbol.
5. Exposes:
   - `build_plan(symbol, template_name, params)` -> inserts plan, returns summary dict
   - `display_all_plans()` -> lists all plan instances
   - `symbols_at_harvest_points()` -> list of dicts with keys `symbol` and `shares_to_sell` (plus optional debug fields)

## Runtime issue + fix
- Initial error: `No price history returned for TER`
- Fix: update `fetch_daily_history_ohlcv()` to use `auto_adjust=False` and fall back when `Adj Close` is missing.
- After fix: plan builds, stores, lists correctly; harvest-hit scan returns `[]` if no target reached yet.

## Next (design direction)
Extend into a “controller” that dynamically adjusts plans based on daily price changes:
- Planner (creates ladder) vs Controller (monitors, triggers, replans).
- Re-plan options: harvest-driven, periodic, or threshold/error-based.
- Maintain explicit state: shares, harvested cash, current rung, drift/vol estimates.
