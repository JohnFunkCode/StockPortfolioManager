# Harvester system

> Moved verbatim from `CLAUDE.md`, which keeps the rules an agent must not break as a short
> summary and links here for the explanation. Edit the detail here, the rule there.

## Harvester System

An experimental "harvest ladder" strategy for systematically selling shares as prices rise:

- **`experiments/HarvesterExperiment.py`** — Core algorithm: computes volatility-based harvest thresholds (H), builds forward price target ladders, and backtests harvest plans. (`experiments/INTC_bear_call_spread_monitor.py` and `WMT_bull_call_spread_monitor.py` are standalone position monitors kept alongside it.)
- **`quantcore/repositories/harvester_repository.py`** — `HarvesterPlanDB` + `PlanBuildParams` persist plans in the unified **QuantCore** PostgreSQL database (plan templates/instances/rungs/alerts). SQL only.
- **`quantcore/services/harvester.py`** — `HarvesterService` wraps the repository and scans prices against active plan rungs, firing alerts (the former `HarvesterController` behaviour).

The Harvester integrates with the notification system: when `main.py` runs, it checks each portfolio stock against active harvest plan rungs (via `HarvesterService`) and sends Discord alerts for any hits.

**Plans are owned (#147 Part H1, `V7`).** `plan_instances.owner` matches `positions.owner`, and the
database invariant moved with it: `ux_one_active_plan_per_symbol` was dropped for
`ux_one_active_plan_per_owner_symbol`, so two owners may each run a ladder on the same ticker.
Consequences to keep straight:

- `owner` is a **required keyword-only argument** on every public repository and service method that
  touches a plan, a rung, or an alert — not a defaulted one. Forgetting it is a `TypeError` at the
  call site rather than a silent read of John's ladders.
- **Isolation is enforced in SQL, never in a route.** Every statement carries an `owner` predicate,
  so another owner's plan reads as `None` and mutates zero rows; the routes turn that into a 404 —
  the same answer as an id that never existed, which is what keeps the endpoint from leaking which
  ids are taken. **No exceptions, including private helpers.** `_ensure_next_rung_alert` and the
  two SQL constants the scan path executes directly (`SQL_GET_NEXT_PENDING_RUNG`,
  `SQL_GET_ACTIVE_ALERT_FOR_RUNG`) all carry the predicate, even though every caller reaches them
  with an id already resolved through a scoped query. The required keyword catches the caller that
  forgot to scope; the predicate contains the damage when one is wrong anyway — and those two
  failures are not equally bad, since the alternative to a no-op is a write onto another owner's
  ladder.
- Routes resolve the owner from the authenticated principal via `Depends(require_owner)`; there is
  no `?owner=` on the plans/rungs/dashboard routes. `GET /api/symbols/{ticker}/price` reads no owned
  data and deliberately takes no owner at all.
- `Notifier(portfolio, owner=…)` defaults to `"john"` because the daily job runs on John's
  portfolio; the argument exists so a second owner's run scopes to their own ladders.
- The status vocabulary is `ACTIVE` | `SUPERSEDED` | `CLOSED`.

**A plan may only exist while the owner holds the shares (#147 Part H5, `V8`).** A harvest ladder
sells into strength; one running on a symbol nobody holds fires alerts that can never be executed.
The invariant is enforced at both ends, and the two ends are wired with **repositories, not
services** — `HarvesterService` takes `portfolio_repository`, `PortfolioService` takes
`harvester_repository`, because services in both directions would close a construction cycle in
`registry.py`. Both are optional (`None`) so a unit test can build either stack alone.

- **Entry.** `HarvesterService.build_plan` refuses a symbol with no `OPEN` lot, *before* the
  yfinance fetch, with a `RuntimeError` that `POST /api/plans` already maps to **422** — the
  request is well-formed, the portfolio just doesn't support it. `CreatePlanDialog` narrows its
  symbol picker to the caller's holdings, but the picker is the courtesy and the 422 is the rule.
- **Exit.** `PortfolioService._close_plan_if_flat` closes the owner's ACTIVE plan to **`CLOSED`**
  (not `SUPERSEDED` — nothing replaced it) once no `OPEN` lot remains, on the three paths that can
  empty a position: `close_lot`, `delete_lot`, `remove_position`. It runs **after** the write has
  committed, on its own connection, inside a `try/except` that only logs — a harvester outage must
  not fail a sale that already landed. A **partial** close leaves lots open and therefore leaves
  the plan ACTIVE; that off-by-one is the difference between finishing a harvest and silently
  killing a live ladder.
- **The backstop.** Because the exit seam is eventually consistent by design, `GET /api/plans`
  carries `in_portfolio` per plan and `/plans` flags an ACTIVE row whose shares are gone. `None`
  where the holdings could not be read — "we can't tell" is not "they sold out of it", and the
  page only flags an explicit `False`.
- The Portfolio page's Plan chip reads `active_plan_id` off **`GET /api/portfolio/symbols`**
  (`PortfolioService._active_plan_ids` → `HarvesterPlanDB.active_plan_ids`), looked up **once per
  request** and degrading to `None` rather than failing the table. The Harvester-era
  `GET /api/symbols` list independently carries the same field (its own owner-scoped join in
  `HarvesterPlanDB`) and is now **MCP/API-only** — no page reads it, since `/symbols` was retired
  from the front end in #147 Part G1.
- `V8__close_orphan_plans.sql` closes rows that predate the invariant. It is a **data** migration:
  deliberately *not* mirrored into `_SCHEMA` or the snapshot, because `init_schema()` runs on every
  startup and a re-running backfill would close plans built moments earlier.
