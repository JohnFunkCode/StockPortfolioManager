# Per-user external portfolios with lot tracking — implementation plan

**Source issue:** [#126](https://github.com/JohnFunkCode/StockPortfolioManager/issues/126)
**Design decisions:** [#126 consolidated comment](https://github.com/JohnFunkCode/StockPortfolioManager/issues/126#issuecomment-5087339742)
**Status:** COMPLETE — PR 1-4 merged (#149, #150, #151, #153, #154); see checkpoint log
**Related:** #83 (watchlist persistence), #145 (realized gain/loss view), #146 (MM delta exposure placement), #147 (decouple `main.py` report)

---

## How to use this plan

This plan is written to be executed **one step at a time**, in order, by an
implementing agent. It follows the convention of the other plans in this
directory (`phase1-migration-plan.md`, `phase2-fastapi-plan.md`,
`phase3-gateway-plan.md`, `prod-rollout-plan.md`, `quantui-iap-plan.md`,
`byok-key-proxy-plan.md`): numbered steps, each independently verifiable, with a
checkpoint log at the bottom.

Every step has the same shape:

- **Files** — exact paths, with `file:line` anchors where a specific site
  matters. Line numbers were captured on 2026-07-26 against branch
  `chore/gitignore-claude-local-settings`; if a number has drifted, search for
  the quoted code rather than trusting the number.
- **Do** — the change, stated concretely.
- **Don't** — the specific wrong turns available at this step.
- **Verify** — a runnable command.
- **Done when** — the observable acceptance criterion.

**Rules for the executing agent:**

1. **Do not make design decisions.** Every open question from the #126
   discussion is already answered — in this document or the linked comment. If
   a step seems to require a decision that isn't written down, stop and ask.
2. **Do not commit or open a PR without explicit authorization.** The repo owner
   has a standing rule: "open a PR" is not implicit commit authorization. `main`
   requires review + approval and cannot be self-merged.
3. **Finish one PR's steps completely, verify, then stop** and report. Each PR
   is a deploy boundary — `deploy.yml` auto-deploys test on merge to `main`;
   prod is manual dispatch only.
4. **Compact context between PRs.** Each PR's merge is the saved state.
5. Run the full backend suite before declaring any PR complete:
   ```bash
   python -m unittest discover -s tests -t .
   ```

---

## Global guardrails

Things that are true for every step in this plan:

- **Never widen `mcp_gateway/rest_client.py` beyond `get`.** MCP is read-only by
  decision (#126 Q19). See PR 4 Step 8.
- **Never add an `owner` parameter to anything the model or the browser can
  set.** Owner derives from the authenticated principal, server-side, always.
  This is the whole point of PR 2.
- **BYOK never-log policy applies.** No API keys, `Authorization` headers,
  envelopes, decrypted payloads, request bodies, or exception dumps containing
  credentials may reach a log or a print. Any new failure path must add the
  corresponding log assertion in tests. (Security *events* — "unmapped principal
  attempted access" — are fine and required; a principal's email is not a
  credential.)
- **Every schema change is a Flyway-style file under `db/migrations/` AND is
  mirrored into `quantcore/db.py init_schema()`.** Both, always. `init_schema()`
  is what actually runs on startup; the migration file is the record.
  `db/migrations/` currently contains only `V2__positions_multi_owner.sql`.
- **Money and share counts are `Decimal`, never `float`.** The existing
  `positions` columns are `REAL`; that is a known wart this plan narrows rather
  than widens.
- **Arch-v2 layering** (`docs/proposals/architectural-standard-v2.md`):
  gateways → repositories (SQL only, no analytics) → analytics (pure functions,
  no I/O) → services (business logic) → adapters (routers/MCP, exactly one
  service call deep). Services never import each other or the registry.
- **Rules 8–9**: any component displaying analytical data must be
  sidekick-renderable — scalar self-contained props, registered with matching
  strict specs in BOTH `quantcore/services/chat_tools.py:17` and
  `frontend/src/chat/componentRegistry.tsx:31`, rendered via `DirectiveRenderer`,
  displayed math in `quantcore/analytics`, vitest tests in the same PR, coverage
  thresholds ratchet upward only.
- **Adding, renaming, or removing any REST route fails CI** until the snapshot
  is regenerated and committed in the same PR:
  ```bash
  PYTHONPATH=. python scripts/check_openapi_snapshot.py --update
  ```

---

## Current-state reference

Read this before starting; it is the map of what the code does today.

| Concern | Where | Today's behaviour |
|---|---|---|
| Positions DDL | `quantcore/db.py:70-145` | `position_id SERIAL PK`, `quantity INTEGER`, `purchase_date TEXT`, plus a **UNIQUE** `idx_positions_owner_symbol_date` |
| Harvester FK | `quantcore/db.py:137` | `plan_instances.position_id` → `positions(position_id) ON DELETE SET NULL` |
| Position SQL | `quantcore/repositories/portfolio_repository.py:70` | `SQL_INSERT_POSITION` upserts `ON CONFLICT(owner, symbol_id, purchase_date) DO UPDATE` — silently overwrites |
| Row shape | `portfolio_repository.py:98` | `_row_to_dict()` emits CSV-parity keys (`name, symbol, purchase_price, quantity, purchase_date, currency, sale_price, sale_date, source, tags`) |
| Delete | `portfolio_repository.py:204` | `remove_position(owner, ticker)` deletes **every** lot for the symbol |
| Second lot blocked | `quantcore/services/portfolio.py:87` | `if self._repo.count_for_symbol(...) > 0: raise DuplicateSymbolError` |
| Integer shares | `quantcore/services/portfolio.py:33,40` | `_i()` → `int()` on `quantity` |
| Owner as query param | `api/routers/portfolio.py:51,56,78,89` | `owner: str = "john"` — **no principal check (IDOR)** |
| Portfolio loader | `api/deps.py:48` | `load_portfolio(owner="john")` |
| Principal | `api/auth.py:107,124,139,205` | `Principal.owner` = `subject or email or "unknown"`; `require_principal` used only by chat/settings/keyproxy |
| Multi-lot collapse | `portfolio/portfolio.py:18` | `self.stocks[stock.symbol] = stock` — a second lot silently replaces the first |
| Only lot-dependent alert | `notifier.py:97` | `if stock.current_price.amount < stock.purchase_price.amount:` |
| App title | `frontend/src/App.tsx:57`, `frontend/index.html:6` | "Harvest Ladder" / "Harvest Ladder Dashboard" |
| Nav + routes | `frontend/src/App.tsx:32`, `:166-174` | `/` → `DashboardPage`; no `/harvester`, no `/portfolio` |
| MCP wrapper list | `scripts/ci_wrapper_smoke.py:26-30`, `.github/workflows/deploy.yml:226` | five wrappers, one shared `Dockerfile.mcp` selected by `SERVER_MODULE`/`PORT` |
| REST client verbs | `mcp_gateway/rest_client.py:121,134` | `get` and `post` only |
| Analytics modules | `quantcore/analytics/` | `indicators.py`, `market_time.py`, `options_math.py`, `volume_profile.py` — **no `portfolio_math.py`** |

---

# PR 1 — QuantUI rename, navigation, and the Portfolio route shell

**Goal:** land the information-architecture change on its own, with **zero data
exposure**. The Portfolio page is a placeholder in this PR.

**Why this is first:** making `/` the Portfolio landing route before per-user
scoping exists would show John's holdings to every logged-in user. Shipping the
shell first means PR 2 lands into a route that already exists, and PR 4 fills a
page that is already wired.

### Step 1.1 — Rename the application to QuantUI

**Files**
- `frontend/src/App.tsx:57` — the string `Harvest Ladder`
- `frontend/index.html:6` — `<title>Harvest Ladder Dashboard</title>`

**Do** — change both to `QuantUI` (title tag: `QuantUI`). Keep the existing
Orbitron font and gradient styling on the header — this is a text change only.

**Don't** — do not touch `CreatePlanDialog.tsx:51` ("Create Harvest Plan") or
`RungsTable.tsx:41` (headerName `'Harvest'`). Those refer to the harvester
strategy, not the app name, and are correct as they stand.

**Verify**
```bash
grep -rn "Harvest Ladder" frontend/src frontend/index.html
```

**Done when** the grep returns nothing.

### Step 1.2 — Move the dashboard to `/harvester`

**Files**
- `frontend/src/App.tsx:32` (`navItems`), `:166-174` (routes)
- `frontend/src/components/dashboard/DashboardPage.tsx` — the `<Typography variant="h4">Dashboard</Typography>` heading

**Do**
- Route `/harvester` → `DashboardPage`.
- Nav item label `Harvester`, path `/harvester`, keep the existing
  `<DashboardIcon />`.
- Change the page heading to `Harvester Dashboard`.
- Add a redirect from any existing bookmark: `/` currently means "dashboard" to
  existing users, but `/` is being reassigned — so **do not** redirect `/`.
  Instead accept that `/` now means Portfolio; that is the decision.

**Don't**
- Do not delete or move the **"Portfolio — Market Maker Delta Exposure"** panel.
  It stays on the Harvester Dashboard (decision #18; longer-term placement is
  #146).
- Do not rename `DashboardPage.tsx` or its directory. A file rename here churns
  the diff and its test siblings for no behavioural gain; the route and label
  are what users see.

**Verify**
```bash
cd frontend && npx vitest run && npx tsc --noEmit
```

**Done when** the suite passes and `/harvester` renders the former dashboard.

### Step 1.3 — Add the Portfolio route as a placeholder

**Files**
- new: `frontend/src/components/portfolio/PortfolioPage.tsx`
- new: `frontend/src/components/portfolio/PortfolioPage.test.tsx`
- `frontend/src/App.tsx` — nav + routes

**Do**
- `PortfolioPage` renders a heading (`Portfolio`) and a short "coming soon"
  empty state. **No data fetching in this PR.**
- Route `/` → `PortfolioPage`. Nav item: label `Portfolio`, path `/`, icon
  `AccountBalanceWalletIcon` (from `@mui/icons-material`), positioned **first**
  in `navItems`.
- Vitest test asserting the heading renders.

**Don't**
- Do not call `/api/portfolio` from this component yet. Per-user scoping does
  not exist until PR 2; a real table here is a data leak.
- Do not register a `portfolio_table` sidekick component yet — an empty
  placeholder is not analytical data, and registry parity tests would assert a
  component that shows nothing.

**Verify**
```bash
cd frontend && npx vitest run --coverage && npx tsc --noEmit
```

**Done when** `/` renders the placeholder, `/harvester` renders the dashboard,
nav shows Portfolio first, and coverage has not regressed.

### PR 1 acceptance

```bash
python -m unittest discover -s tests -t .
cd frontend && npx vitest run --coverage && npx tsc --noEmit
```

Backend is untouched, so the backend suite is a regression check only. Merge,
let `deploy.yml` roll the test `quantui` service, and **verify on the test URL**
(`https://quantui-uikpdb55ea-uc.a.run.app`) before starting PR 2.

---

# PR 2 — Owner scoping from the authenticated principal

**Goal:** every portfolio read and write derives its owner from the
authenticated principal. `?owner=` is gone. Unmapped identities get a 403 and a
logged security event.

**This is the security PR.** It is deliberately separate from the lot model so
that if anything about it needs to be reverted, no schema change is entangled.

### Step 2.1 — `owner_identities` migration

**Files**
- new: `db/migrations/V3__owner_identities.sql`
- `quantcore/db.py` — mirror into `init_schema()` alongside the other DDL

**Do** — create the mapping table:

```sql
CREATE TABLE IF NOT EXISTS owner_identities (
    identity   TEXT PRIMARY KEY,      -- IAP email OR MCP token sub, lowercased
    owner      TEXT NOT NULL,         -- canonical short handle, e.g. 'john'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes      TEXT
);
CREATE INDEX IF NOT EXISTS idx_owner_identities_owner ON owner_identities(owner);
```

Seed the identities that already work today so nothing breaks on deploy: the
handle `john` (the HS256 MCP default `sub`) and John's IAP email, both mapping
to owner `john`. Seed the other provisioned users' identities too — check the
`USERS=( … )` array in `scripts/grant_quantui_iap_access.sh` for the current
list, and map each to a short handle.

**Don't**
- Do not migrate `positions` data. `owner='john'` remains the canonical value;
  this table maps *identities to* that value, it does not change it.
- Do not make `identity` case-sensitive in practice — normalize to lowercase on
  both write and lookup. Google emails are case-insensitive and a case mismatch
  here produces a 403 that looks like an intrusion attempt.

**Verify**
```bash
./scripts/with-test-db.sh python -c "from quantcore.db import init_schema, get_connection; init_schema(); c=get_connection(); cur=c.cursor(); cur.execute('SELECT identity, owner FROM owner_identities ORDER BY identity'); print(cur.fetchall())"
```

**Done when** the seeded rows are present in the test database.

### Step 2.2 — `OwnerIdentityRepository` + resolution in `SettingsService`… no: a dedicated service

**Files**
- new: `quantcore/repositories/owner_identity_repository.py`
- new: `quantcore/services/identity.py` — `IdentityService`
- `quantcore/services/registry.py:29,45,69,79,96,190,216` — the six wiring sites
  (import, import, field, field, construction, `Services(...)` kwarg)

**Do**
- `OwnerIdentityRepository`: SQL only — `resolve(identity) -> str | None`,
  `upsert(identity, owner, notes=None)`, `list_all()`.
- `IdentityService.resolve_owner(identity) -> str` raises a new
  `UnknownIdentityError` when there is no mapping. Lowercase the identity before
  lookup.
- Wire both through the registry with constructor injection, mirroring how
  `PortfolioRepository`/`PortfolioService` are wired at the listed line numbers.

**Don't**
- Do not fall back to "use the identity as the owner" when the mapping is
  missing. That silently auto-provisions and defeats decision #2.
- Do not import `IdentityService` from `PortfolioService` or vice versa —
  services never import each other. Composition happens in the router/dependency
  layer or in the registry.

**Verify**
```bash
python -m unittest discover -s tests -t . -p "test_identity*"
```

**Done when** unit tests cover: known identity → owner; unknown identity →
`UnknownIdentityError`; mixed-case identity resolves.

### Step 2.3 — A FastAPI dependency that yields the owner

**Files**
- `api/auth.py:107` (`Principal`), `:124` (`owner`), `:205` (`require_principal`)
- `api/deps.py:48` (`load_portfolio`)

**Do**
- Add `require_owner(principal = Depends(require_principal)) -> str` — resolves
  `principal.owner` through `IdentityService`, and on `UnknownIdentityError`:
  1. logs a **security event** at WARNING with the identity and a UTC timestamp
     and a stable marker string (e.g. `SECURITY unmapped_principal`), and
  2. raises `HTTPException(403)` with a body the frontend can key on —
     `{"detail": "not_provisioned"}`. **No identifying information in the
     response body.**
- This is enforced identically for ES256 UI tokens and HS256 MCP tokens — the
  decision is explicit that an MCP token whose `sub` is unmapped also 403s and
  logs (decision #4).
- Update `load_portfolio` to take the resolved owner (no default).

**Don't**
- Do not log the token, any header, or the raw claims dict. Log the identity
  string and the timestamp — nothing else.
- Do not return a different status for "no such user" vs "not provisioned".
  One 403, one body, no enumeration oracle.
- Do not leave `Principal.owner`'s `"unknown"` fallback reachable as an owner
  value — it must resolve through `owner_identities` like everything else, and
  `"unknown"` will not be in the table.

**Verify**
```bash
python -m unittest discover -s tests -t . -p "test_auth*"
```

**Done when** tests assert: mapped ES256 principal → owner; unmapped ES256 → 403
+ log record present; unmapped HS256 → 403 + log record present; no credential
material appears in any captured log.

### Step 2.4 — Remove `?owner=` from every route

**Files**
- `api/routers/portfolio.py:51,56,78,89` — the four `owner: str = "john"` params
- `api/routers/portfolio.py:4` — the module docstring that documents `?owner=`
- `docs/openapi-surface.txt` — regenerate

**Do**
- Replace each `owner: str = "john"` with `owner: str = Depends(require_owner)`.
- Apply the same to `/api/securities` and the screener and delta-exposure
  surfaces (decision #5) — grep for other `load_portfolio(` callers:
  ```bash
  grep -rn "load_portfolio\|list_positions(" api/ quantcore/ --include=*.py
  ```
- Leave `main.py`'s report/notification path pinned to owner `john` explicitly —
  it is a job, not a request, and has no principal.
- **Options-snapshot capture must walk the union of all owners' symbols**
  (decision #5). `PortfolioRepository.list_owners()` already exists; iterate it
  in `main.py`'s capture loop and de-duplicate the symbol set.

**Don't**
- Do not keep `?owner=` "for debugging". The decision is that there is no
  legitimate cross-user read path; an admin need is served by direct DB access,
  not an internet-facing query param.
- Do not change any response *shape* in this PR — the frontend must keep working
  unmodified. Only the owner *source* changes.

**Verify**
```bash
grep -rn 'owner: str = "john"' api/
PYTHONPATH=. python scripts/check_openapi_snapshot.py --update
python -m unittest discover -s tests -t .
```

**Done when** the grep is empty and the full suite passes.

### Step 2.5 — Migrate the API smoke tests off `?owner=`

**Files**
- `tests/test_api_smoke.py:233,243,251,255,257,260` — the only `?owner=`
  consumers in the suite

**Do** — replace query-param usage with
`app.dependency_overrides[require_principal] = lambda: <fake principal>`, and
add cases for the unmapped-principal 403.

**Don't** — do not override `require_owner` directly in tests that are meant to
exercise the resolution logic; override `require_principal` so the real
`IdentityService` path runs.

**Verify**
```bash
python -m unittest tests.test_api_smoke
```

**Done when** the smoke suite passes with no `?owner=` string remaining in it.

### Step 2.6 — The restricted-access screen

**Files**
- new: `frontend/src/components/access/RestrictedAccess.tsx`
- new: `frontend/src/components/access/RestrictedAccess.test.tsx`
- new: `frontend/src/components/portfolio/EmptyPortfolio.tsx` (+ test)
- `frontend/src/App.tsx:166` — must render **outside** `<Route element={<Layout />}>`

**Do**
- On a 403 with `detail === "not_provisioned"` from any API call, render
  `RestrictedAccess` as a **full-page route outside `Layout`** — no header, no
  nav, no `ChatRail`, no email echoed back.
- Exact copy, verbatim:
  > This system is restricted to authorized users. Individuals who attempt
  > unauthorized access will be prosecuted. If you are unauthorized, terminate
  > access now.
- After **10 seconds**, redirect to `https://www.fbi.gov/investigate/cyber`.
- Separately: a **provisioned** user whose portfolio is empty gets
  `EmptyPortfolio` — friendly, inside the normal `Layout`, with instructions for
  adding a first position. These two states must never be confused; they are
  distinguished by the 403 vs a successful empty response.

**Don't**
- Do not display the user's email, the identity string, a support contact, or
  any hint about how to get provisioned. The decision is explicitly "no
  identifying information."
- Do not use this screen for network errors or 500s — only the
  `not_provisioned` 403.
- Do not make the redirect immediate; 10 seconds is the decision, so the message
  is readable.

**Verify**
```bash
cd frontend && npx vitest run --coverage
```

**Done when** tests assert the exact copy renders, no email appears in the DOM,
the component is not wrapped in `Layout`, and the redirect fires on a faked
10-second timer.

### Step 2.7 — Make onboarding atomic

**Files**
- `scripts/grant_quantui_iap_access.sh` — the `USERS=( … )` array

**Do** — extend the script so that granting IAP access **also** writes the
`owner_identities` row in the same run, against the target project's database.
Accept an owner handle per user (e.g. `USERS=( "thomas@zoidbergfolio.com:thomas" )`)
and make the DB write idempotent (`ON CONFLICT (identity) DO NOTHING`).

**Don't** — do not let the script succeed with a partial result. If the IAP
grant lands and the DB write fails, exit non-zero and say which half succeeded;
a drifted pair is exactly the state that makes the harsh screen appear for a
legitimate user.

**Verify** — dry-run against test, then confirm the row:
```bash
./scripts/with-test-db.sh python -c "from quantcore.db import get_connection; c=get_connection(); cur=c.cursor(); cur.execute('SELECT identity, owner FROM owner_identities ORDER BY identity'); print(cur.fetchall())"
```

**Done when** a single script invocation produces both the IAP binding and the
`owner_identities` row.

### PR 2 acceptance

```bash
python -m unittest discover -s tests -t .
cd frontend && npx vitest run --coverage && npx tsc --noEmit
PYTHONPATH=. python scripts/check_openapi_snapshot.py
```

Merge, let test deploy, then **verify on the test URL with two identities** —
one provisioned (sees their own data), one deliberately unmapped (sees the
restricted screen, and the security event appears in Cloud Run logs). This
verification is the point of the PR; do not skip it.

---

# PR 3 — The lot schema migration and the multi-lot reader fix

**Goal:** the database can represent multiple open lots per symbol, partial
sales, and lot lineage — and `main.py`'s reader stops silently dropping lots.

**Why these ship together:** `portfolio/portfolio.py:18` collapses lots by
symbol. Once multi-lot data exists, that reader is wrong. The exposure is narrow
(see "Blast radius" below), but there is no reason to leave a known-wrong reader
in `main` across a merge boundary when the fix is small.

### Step 3.1 — `V4__portfolio_lots.sql`

**Files**
- new: `db/migrations/V4__portfolio_lots.sql`
- `quantcore/db.py:70-145` — mirror every change into `init_schema()`

**Do**

```sql
-- Drop the index that forbids #126's own split rule. A partial sale creates a
-- second lot with the SAME (owner, symbol, trade_date) as its parent.
DROP INDEX IF EXISTS idx_positions_owner_symbol_date;

ALTER TABLE positions
  ADD COLUMN IF NOT EXISTS status            TEXT NOT NULL DEFAULT 'OPEN',
  ADD COLUMN IF NOT EXISTS parent_lot_id     INTEGER REFERENCES positions(position_id) ON DELETE SET NULL,
  -- 'trade_date', not 'purchase_date': cost basis and holding period run from
  -- the TRADE date, not the settlement date, and "days held" counts from it.
  ADD COLUMN IF NOT EXISTS trade_date        DATE,
  ADD COLUMN IF NOT EXISTS fees              NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS acquisition_type  TEXT NOT NULL DEFAULT 'BUY',
  ADD COLUMN IF NOT EXISTS covered           BOOLEAN,
  ADD COLUMN IF NOT EXISTS fx_rate_at_purchase NUMERIC(18,8),
  -- Nullable now, populated when tax work lands (#126 decision 10).
  ADD COLUMN IF NOT EXISTS basis_adjustment      NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS wash_sale_disallowed  NUMERIC(18,6);

-- Fractional shares: Schwab Stock Slices = 4dp, E*TRADE = 3dp on the ticket.
ALTER TABLE positions ALTER COLUMN quantity TYPE NUMERIC(18,6);

-- Backfill (#126 decision 11).
UPDATE positions SET status = 'OPEN'          WHERE status IS NULL;
UPDATE positions SET acquisition_type = 'BUY' WHERE acquisition_type IS NULL;
UPDATE positions SET trade_date = purchase_date::date
  WHERE trade_date IS NULL AND purchase_date IS NOT NULL AND purchase_date <> '';

CREATE INDEX IF NOT EXISTS idx_positions_owner_status ON positions(owner, status);
CREATE INDEX IF NOT EXISTS idx_positions_parent       ON positions(parent_lot_id);

CREATE TABLE IF NOT EXISTS lot_sales (
    sale_id        SERIAL PRIMARY KEY,
    lot_id         INTEGER NOT NULL REFERENCES positions(position_id) ON DELETE CASCADE,
    shares_sold    NUMERIC(18,6) NOT NULL,
    sale_price     NUMERIC(18,6) NOT NULL,
    sale_trade_date DATE NOT NULL,
    fees           NUMERIC(18,6),
    allocation_method TEXT,      -- FIFO | LIFO | HIFO | MANUAL
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes          TEXT
);
CREATE INDEX IF NOT EXISTS idx_lot_sales_lot ON lot_sales(lot_id);
```

**Don't**
- Do not drop `purchase_date`. It stays as the CSV-parity field and the
  deprecated import alias; `trade_date` is the typed column the code reads.
- Do not add a UNIQUE constraint of any kind to `positions` on
  (owner, symbol, date). That is precisely what broke.
- Do not `CASCADE` the `parent_lot_id` FK — deleting a parent lot must not
  vaporize its children. `ON DELETE SET NULL` preserves the child with broken
  lineage, which is recoverable; a cascade is not.
- Do not forget that `plan_instances.position_id` (`quantcore/db.py:137`) FKs to
  `positions` with `ON DELETE SET NULL` — a lot split leaves the harvester plan
  attached to the *parent*. That is acceptable and intentional for this PR; do
  not attempt to re-parent plans here.

**Verify**
```bash
./scripts/with-test-db.sh python -c "from quantcore.db import init_schema; init_schema(); print('ok')"
./scripts/with-test-db.sh psql -c "\d positions"
./scripts/with-test-db.sh psql -c "\d lot_sales"
```

**Done when** the new columns and table exist on the test DB, the UNIQUE index
is gone, and existing rows show `status='OPEN'`, `acquisition_type='BUY'`, and a
populated `trade_date`.

### Step 3.2 — Repository: stop overwriting, start returning lot identity

**Files**
- `quantcore/repositories/portfolio_repository.py:70` (`SQL_INSERT_POSITION`),
  `:98` (`_row_to_dict`), `:127` (`_legacy_values`), `:139` (`_insert_position`),
  `:158` (`list_positions`), `:204` (`remove_position`)

**Do**
- `SQL_INSERT_POSITION`: drop the `ON CONFLICT … DO UPDATE` clause entirely and
  `RETURNING position_id`. Every insert now creates a new lot.
- `_row_to_dict()`: add `lot_id` (from `position_id`), `status`, `parent_lot_id`,
  `trade_date`, `fees`, `acquisition_type`, `account`, `notes`. **Keep every
  existing key** — CSV parity and the frontend both depend on the current shape.
- `list_positions(owner)` gains a `status` filter defaulting to `'OPEN'`
  (decision #12: the portfolio view shows open lots only).
- Add `get_lot(owner, lot_id)`, `update_lot(owner, lot_id, fields)`,
  `delete_lot(owner, lot_id)`, `insert_sale(...)`, `list_lots_for_symbol(owner, ticker, status)`.
  **Every one takes `owner` and filters on it in SQL** — a lot id alone must
  never be sufficient to read or mutate a row.
- Cast `quantity`, prices, and fees to `Decimal` on read.

**Don't**
- Do not put allocation logic (FIFO/LIFO/HIFO) here. Repositories are SQL only;
  allocation is analytics (Step 4.2).
- Do not change `remove_position(owner, ticker)`'s semantics — it is the
  symbol-level delete used by the existing `DELETE /api/portfolio/{ticker}`
  route. `delete_lot` is the new, narrower operation.

**Verify**
```bash
python -m unittest discover -s tests -t . -p "test_portfolio*"
```

**Done when** a test inserts **two lots of the same symbol on the same date**
and reads back two distinct `lot_id`s. This is the assertion that proves the
UNIQUE index and the upsert are actually gone.

### Step 3.3 — Service: allow multiple lots, allow fractional shares

**Files**
- `quantcore/services/portfolio.py:33,40` (`_i` / `quantity`), `:87` (the
  `DuplicateSymbolError` guard)

**Do**
- Delete the `count_for_symbol(...) > 0 → DuplicateSymbolError` guard at `:87`.
  Multiple lots per symbol is the feature.
- Change `quantity` normalization from `_i()`/`int()` to `Decimal`, quantized to
  6 places.
- Accept `trade_date` and treat `purchase_date` as a **deprecated alias** on
  import (log a deprecation note once per import, not per row).
- Keep `DuplicateSymbolError` defined and exported — removing the symbol may
  break importers; it simply stops being raised from `add_position`.

**Don't**
- Do not accept dollar-based entry and derive share counts (decision #8) — the
  user supplies share counts directly to avoid rounding drift.
- Do not use `float` anywhere in the money path.

**Verify**
```bash
python -m unittest discover -s tests -t . -p "test_portfolio*"
```

**Done when** tests cover: two lots of the same symbol both persist; a
fractional quantity (`0.0625`) round-trips exactly; `purchase_date` on import
still works and populates `trade_date`.

### Step 3.4 — Fix the multi-lot reader in the daily job

**Files**
- `portfolio/portfolio.py:18` — `self.stocks[stock.symbol] = stock`
- `portfolio/portfolio.py:179` — `read_stocks_from_records`
- `portfolio/stock.py`
- `notifier.py:97` — `if stock.current_price.amount < stock.purchase_price.amount:`

**Blast radius (verified — do not re-derive):** MA alerts (`notifier.py:86`),
harvester rung hits (`notifier.py:47`, `harvest_hit_for_symbol(symbol=…)`),
sentiment flips (`notifier.py:242,257`), options alerts (`notifier.py:120`),
options-chain capture (`main.py:643-659`), and OHLCV caching are **all
symbol-keyed and unaffected** by the collapse. The **only** lot-dependent
consumer is the loss alert at `notifier.py:97`. The generated HTML report is not
consumed by anything today (#147).

**Do**
- Make `Portfolio` hold **all** lots. The minimum change that preserves every
  symbol-keyed consumer: keep `self.stocks` keyed by symbol for the symbol-level
  consumers, and add `self.lots` (a list, or `dict[symbol, list[Stock]]`) that
  retains every row.
- Change `notifier.py:97` to evaluate the loss condition **per lot**, and
  de-duplicate the resulting alerts so two lots underwater on the same symbol
  produce one notification, not two. `notification.log` already deduplicates
  within a run — confirm the dedup key includes the symbol and the alert type,
  not the lot.

**Don't**
- Do not restructure `notifier.py`'s other checks to be lot-aware. They are
  correct as symbol-level checks; changing them adds risk with no benefit.
- Do not touch the HTML report / S3 path (`main.py:19,27,130,227,553`). That is
  #147's scope, it is not consumed, and pulling it in here widens the diff.

**Verify**
```bash
python -m unittest discover -s tests -t . -p "test_*notif*"
python -m unittest discover -s tests -t .
```

**Done when** a **two-lot fixture** (same symbol, different purchase prices, one
underwater and one not) produces the expected alert set. Single-lot fixtures
cannot distinguish working code from broken code — the two-lot fixture *is* the
regression net.

### PR 3 acceptance

```bash
python -m unittest discover -s tests -t .
```

Then, on **test** (not prod): seed a two-lot symbol, run the report job, and
compare the **Discord alert set** against expectations. Do not verify by
diffing the HTML report — it is not consumed and is not the contract.

Merge only after that comparison is clean, so prod retains a clean rollback
point before the migration reaches it.

---

# PR 4 — The Portfolio page, the write path, and the read-only MCP server

**Goal:** the feature #126 actually asked for.

This PR is large. Execute its steps in order; the backend steps (4.1–4.4) are
independently testable before any UI work begins.

### Step 4.1 — `quantcore/analytics/portfolio_math.py`

**Files**
- new: `quantcore/analytics/portfolio_math.py`
- new: `tests/test_portfolio_math.py`

**Do** — pure functions, `Decimal` in / `Decimal` out, **no I/O, no DB, no
network**:

- `gain_loss(current_price, cost_basis_per_share, quantity) -> Decimal`
- `gain_loss_pct(...) -> Decimal`
- `days_held(trade_date, as_of) -> int` — **calendar days**, and the caller
  passes `as_of` explicitly rather than the function reading the clock (so #145
  can stop the clock at the sale date).
- `dollars_per_day(gain_loss, days) -> Decimal | None` — **`None` when days is
  0**; the presentation layer renders `—`. Never divide by zero.
- `period_return(current_price, price_n_days_ago) -> Decimal` — the **security's
  price return** (decision #13), not the position's value change.
- `summary_totals(lots) -> dict` — Total Investment, Total Current Value, Total
  Gain/Loss $, Total Gain/Loss %, Dollars Per Day. **USD** (decision #16).

**Don't**
- Do not compute period returns from position value. Reading A is locked: a
  30-day column shows what the *stock* did.
- Do not import anything from `quantcore/services/` or `quantcore/repositories/`
  here. Analytics is pure by rule.
- Do not put any of this math in the frontend (Rule 8.4). If the UI needs a
  number, the service returns it.

**Verify**
```bash
python -m unittest tests.test_portfolio_math
```

**Done when** every function has a test including the zero-days case, a
fractional-share case, and a negative-return case.

### Step 4.2 — Lot allocation

**Files**
- `quantcore/analytics/portfolio_math.py` (or a sibling
  `quantcore/analytics/lot_allocation.py` if the first file grows past ~250
  lines)

**Do** — `allocate(lots, shares_to_sell, method) -> list[tuple[lot_id, shares]]`
supporting `FIFO` (default), `LIFO`, `HIFO`, and `MANUAL` (caller supplies the
pairs; the function validates them). A sale may span multiple lots → multiple
`lot_sales` rows (decision #9).

Validation the function owns: total allocated equals `shares_to_sell`; no lot is
over-allocated; every referenced lot is OPEN and belongs to the caller's set.

**Don't**
- Do not perform the split here. Allocation decides *which lots and how many
  shares*; the service performs the DB effects.
- Do not silently clamp an over-sell to the available shares — raise. Selling
  more than you hold is a data-entry error the user must see.

**Verify**
```bash
python -m unittest discover -s tests -t . -p "test_*alloc*"
```

**Done when** each method is tested against a 3-lot fixture, plus the
spans-multiple-lots case and the over-sell rejection.

### Step 4.3 — `PortfolioService`: lot lifecycle

**Files**
- `quantcore/services/portfolio.py`

**Do**
- `list_lots(owner, status='OPEN') -> list[dict]` — lots enriched with current
  price, gain/loss, days held, $/day via `portfolio_math`.
- `symbol_rows(owner) -> list[dict]` — the per-symbol roll-up carrying the
  period-return columns and the MM hedge bias, each with its child lots
  (decision #14).
- `update_lot(owner, lot_id, **fields)` — field corrections.
- `delete_lot(owner, lot_id)` — a mistaken entry.
- `close_lot(owner, lot_id, shares, sale_price, sale_trade_date, method='FIFO', lots=None, fees=None)`:
  1. resolve the allocation,
  2. write `lot_sales` rows,
  3. **if shares sold < shares in the lot, create a child lot for the remainder,
     preserving the original `trade_date` and `purchase_price`, with
     `parent_lot_id` set** — this is the issue's stated rule,
  4. mark fully-sold lots `status='CLOSED'`,
  5. all of it in **one transaction**.

**Don't**
- Do not let any of these accept an owner from the caller's request body. Owner
  comes from `require_owner` and is passed down.
- Do not leave a partial split committed. If step 3 fails, step 2 must roll back
  — a lot that has recorded a sale but not created its remainder child has
  silently destroyed shares.
- Do not attempt to re-parent harvester plans (`plan_instances.position_id`).
  Out of scope; the FK is `ON DELETE SET NULL` and the plan stays on the parent.

**Verify**
```bash
python -m unittest discover -s tests -t . -p "test_portfolio*"
```

**Done when** tests cover: full-lot close → CLOSED, no child; partial close →
parent CLOSED + child OPEN with the original trade date and price and correct
`parent_lot_id`; multi-lot FIFO close; a mid-transaction failure leaves the DB
unchanged.

### Step 4.4 — Prices: batched quotes behind a TTL cache

**Files**
- `quantcore/services/prices.py:148` (`get_fast_price`)
- `quantcore/repositories/ohlcv_repository.py` — `daily_bars_for_symbols` (bulk
  read, already exists)

**Do** — implement the hybrid (decision #15): bulk DB read for history and
period returns; **one batched live quote call** for current prices behind a
short server-side TTL cache shared across users. Expose a `force: bool` that
**bypasses the TTL with a ~10 second floor** (so a held-down refresh button
cannot hammer the upstream). Return an **`as_of` timestamp** with the data. On
quote failure, **degrade to last close** with an honest `as_of` rather than
failing the request.

**Don't**
- Do not add polling anywhere. Manual refresh only.
- Do not call `get_fast_price` once per symbol in a loop for the portfolio page —
  that is N upstream calls per page load and is the thing this step exists to
  avoid.

**Verify**
```bash
python -m unittest discover -s tests -t . -p "test_prices*"
```

**Done when** tests assert: a second call within the TTL makes no upstream call;
`force=True` after 10s does; a raised upstream error yields last-close data plus
a stale `as_of`, not an exception.

### Step 4.5 — REST routes for lots

**Files**
- `api/routers/portfolio.py`
- `api/schemas/portfolio.py`
- `docs/openapi-surface.txt` — regenerate

**Do** — add, all `Depends(require_owner)`:

| Route | Purpose |
|---|---|
| `GET /api/portfolio/lots` | open lots for the principal |
| `GET /api/portfolio/symbols` | per-symbol rows with child lots + period returns |
| `POST /api/portfolio/lots` | create a lot |
| `PATCH /api/portfolio/lots/{lot_id}` | correct fields |
| `DELETE /api/portfolio/lots/{lot_id}` | remove a mistaken lot |
| `POST /api/portfolio/lots/{lot_id}/close` | **record a sale** |

Pydantic schemas use `Decimal`, not `float`, for money and quantities.

**Why `POST …/close` and not `PATCH`:** closing a lot is a **domain event**, not
a field assignment — it may split the lot, create a child with preserved
lineage, and write `lot_sales` rows. This is the controller-resource idiom
(Google AIP-136 custom methods); Alpaca and Stripe use the same split. Do not
"simplify" it into a `PATCH status=CLOSED`.

**Don't**
- Do not accept `owner` in any path, query, or body.
- Do not let a lot id from one owner resolve for another — the repository filters
  on owner in SQL (Step 3.2); the route must not bypass it with a
  fetch-then-check.
- Do not forget the snapshot regeneration; CI fails otherwise.

**Verify**
```bash
PYTHONPATH=. python scripts/check_openapi_snapshot.py --update
python -m unittest tests.test_api_smoke
```

**Done when** each route has a smoke test, including a **cross-owner 404/403
test**: owner A cannot read, patch, delete, or close owner B's lot.

### Step 4.6 — The Portfolio page

**Files**
- `frontend/src/api/portfolio.ts` (+ `.test.ts`)
- `frontend/src/hooks/usePortfolio.ts`
- `frontend/src/components/portfolio/PortfolioPage.tsx` — replace the PR 1
  placeholder
- `frontend/src/components/portfolio/` — `PortfolioSummary`, `LotRow`,
  `AddLotDialog`, `CloseLotDialog` (+ tests for each)

**Do**
- **Summary section above the rows**: Total Investment, Total Current Value,
  Total Gain/Loss $, Total Gain/Loss %, Dollars Per Day.
- **Per-symbol rows with lots as expandable child rows** (decision #14).
  - Symbol row: Symbol, Current Price, total shares, aggregate Gain/Loss $ and
    %, Today's / 5-day / 30-day / 90-day / 1-year return, **MM Hedge Bias with
    its `captured_at` date** (decision #18).
  - Lot child row: Price Paid, Number of Shares, Gain/Loss %, Gain/Loss $, Days
    Held, Dollars Per Day, plus edit / delete / close actions.
- **Sortable by any column.**
- Clicking a symbol navigates to `/securities/{symbol}` (the existing
  `DashboardPage` rows already do this — match that behaviour).
- **Refresh control** with the **"as of HH:MM"** timestamp beside it.
- Fractional shares displayed to 4 decimals, trailing zeros trimmed.
- `—` for dollars-per-day when days held is 0.
- Empty-but-provisioned state renders `EmptyPortfolio` from Step 2.6.

**Don't**
- Do not compute gain/loss, percentages, days held, $/day, or totals in
  TypeScript. Rule 8.4: displayed math lives in `quantcore/analytics`. The API
  returns the numbers; the UI formats them.
- Do not auto-refresh on an interval.
- Do not show the MM hedge bias `captured_at` as though the refresh button
  updates it — it is daily snapshot data and will not change.

**Verify**
```bash
cd frontend && npx vitest run --coverage && npx tsc --noEmit
```

**Done when** every new component has loading / error / success tests plus
key-value assertions, and the coverage thresholds in
`frontend/vitest.config.ts` have ratcheted **up**, never down.

### Step 4.7 — Sidekick registry parity

**Files**
- `quantcore/services/chat_tools.py:17` (`BACKEND_COMPONENT_REGISTRY`), `:253`
  (the `show_component` enum is derived from it — confirm it picks up the new
  entries)
- `frontend/src/chat/componentRegistry.tsx:31` (`COMPONENT_REGISTRY`)

**Do** — register exactly two components, in **both** registries with matching
specs:

```python
"portfolio_table": {},                 # zero props — fetches the caller's own data
"symbol_lots": {"ticker": str},
```

An empty spec validates cleanly under `_check_fields()` (extras rejected, no
required fields). Render both through `DirectiveRenderer`.

**Don't**
- **Never give `portfolio_table` an `owner` prop.** A model-settable owner is a
  cross-user read. Scoping is automatic because the directive fetches through
  the browser's own authenticated session.
- Do not register any write/mutating directive or interaction. Sidekick is
  read-only (decision #20).

**Verify**
```bash
python -m unittest discover -s tests -t . -p "test_chat_tools*"
cd frontend && npx vitest run
```

**Done when** the registry-parity test asserts both registries contain the same
component names with the same prop specs, and a directive carrying an `owner`
prop is **rejected**.

### Step 4.8 — The `portfolio-server` MCP wrapper (read-only)

**Files**
- new: `fastMCPTest/portfolio_server.py`
- `mcp_gateway/rest_client.py:121,134` — add the comment, **not** new verbs
- `cloudbuild.yaml` — the mcp image is shared; no new build step needed, confirm
- `.github/workflows/deploy.yml:226` — the wrapper loop list
- `scripts/ci_wrapper_smoke.py:26-30` — the `WRAPPERS` tuple list
- `docker-compose.yml` — a local service on the next free port
- `.mcp.json` — four entries (remote + `-local`, both projects)

**Do**
- A thin gateway (Rule 6) exposing **read tools only**:
  `get_portfolio`, `get_symbol_lots(ticker)`, `get_portfolio_summary`,
  `mcp_health_check`.
- Reuse `Dockerfile.mcp` — `SERVER_MODULE=fastMCPTest.portfolio_server` and a
  new `PORT` chosen at run time; no new image.
- Add `portfolio` to the deploy loop at `deploy.yml:226` and to the
  `ci_wrapper_smoke.py` `WRAPPERS` list with an appropriate tool-count floor.
- In `mcp_gateway/rest_client.py`, add a comment recording that `put`, `patch`,
  and `delete` are **deliberately absent** per #126 Q19, and that adding them is
  the first step whenever MCP writes are revisited.

**Don't**
- **Do not add `put`/`patch`/`delete` to `rest_client.py`.** The wrapper being
  *physically unable* to issue a write is defense in depth that does not depend
  on nobody adding a tool later.
- Do not expose `create_lot`, `close_lot`, or `delete_lot` as MCP tools.
- Do not deploy this to prod as part of the merge. Prod is manual dispatch
  (`prod-rollout.yml`) only, and a new Cloud Run service's **first** deploy in
  each project needs the service to be created with its IAM/env config before
  the image-only roll-out path applies.

**Verify**
```bash
python scripts/ci_wrapper_smoke.py
grep -n "def put\|def patch\|def delete" mcp_gateway/rest_client.py
```

**Done when** the smoke script boots six wrappers and the grep returns nothing.

### PR 4 acceptance

```bash
python -m unittest discover -s tests -t . && coverage report
cd frontend && npx vitest run --coverage && npx tsc --noEmit
PYTHONPATH=. python scripts/check_openapi_snapshot.py
python scripts/ci_wrapper_smoke.py
```

Then on **test**: create two lots of one symbol, verify the expandable row and
the summary totals, partially close one lot and confirm the remainder child
carries the original trade date and price, hit refresh and watch the `as_of`
change, and ask the Sidekick to show the portfolio.

---

## Deferred / explicitly out of scope

Do not implement these as part of this plan:

- **Realized gain/loss view** → #145
- **MM delta exposure placement refactor** → #146
- **Decoupling the HTML report + S3 from `main.py`** → #147
- **DB-backed, owner-scoped watchlist** → #83 (land *after* PR 2 so it reuses
  the principal plumbing)
- **Tax logic** — cost-basis reporting, wash sales, holding-period
  classification. The columns are provisioned; no logic.
- **Per-user display currency** — totals are USD.
- **A full immutable buy-side transaction ledger** — `positions` + `lot_sales`
  is the model for now.
- **MCP write tools** — read-only by decision.
- **Re-parenting harvester plans across lot splits** — the FK is
  `ON DELETE SET NULL`; the plan stays on the parent lot.

---

## Checkpoint log

Append one entry per completed step. Convention matches the other plans in this
directory: date, step, what landed, what was verified, anything surprising.

| Date | Step | Status | Notes |
|---|---|---|---|
| 2026-07-26 | — | Plan written | Decisions consolidated on #126; no code written |
| 2026-07-27 | PR 1 (Steps 1.1-1.3) | Merged (#149) | QuantUI rebrand, dashboard moved to `/harvester`, Portfolio route placeholder added |
| 2026-07-27 | PR 2 (Steps 2.1-2.7) | Merged (#150) | `owner_identities` migration, owner resolved from authenticated principal, `?owner=` removed from every route, restricted-access screen, atomic onboarding |
| 2026-07-27 | PR 3 (Steps 3.1-3.4) | Merged (#151) | `V4__portfolio_lots.sql`, repository returns lot identity instead of overwriting, service allows multiple/fractional lots, multi-lot reader fix in the daily job |
| 2026-07-28 | PR 4 (Steps 4.1-4.6) | Merged (#153) | `portfolio_math.py`, lot allocation, `PortfolioService` lot lifecycle, batched-quote TTL cache, REST lot routes, Portfolio page. Follow-up fix in same PR: validate lot writes and guard `portfolio_math` against missing fields |
| 2026-07-28 | PR 4 (Steps 4.7-4.8) | Merged (#154) | Sidekick registry parity + read-only `portfolio-server` MCP wrapper. Review fix in same PR: normalize ticker case in `SymbolLotsCard` lookup, added lowercase-ticker regression test |
