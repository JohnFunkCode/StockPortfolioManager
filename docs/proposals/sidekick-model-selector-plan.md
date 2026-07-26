# Sidekick model selector — user-selectable Anthropic model (issue #124) — implementation plan

> Status: **PROPOSAL — no code written yet.** This is the canonical, self-contained implementation
> plan for GitHub issue #124. It is written to be executed by **Claude Sonnet 5**: every work
> packet names exact files, anchor lines, the change to make, and how to verify it. Follow the
> packets in order; each is a single logical commit. Progress comments go on issue #124 at packet
> boundaries. **Ask before committing** (repo rule) and remember `main` requires PR review.

## Executive summary

Today the Sidekick chat runs on a single **server-side constant** model (`claude-fable-5`), set
once at registry construction and baked into the chat client. This plan makes the model
**user-selectable** among three Anthropic models, **persisted server-side per user**, chosen from
**two UI surfaces** (a Settings dropdown and a chat-header quick-switch bound to the same value),
defaulting to **Sonnet 5**, with the keyproxy **allow-listing** the three models so a tampered
client cannot run an arbitrary/expensive model on the user's key.

The model becomes a value that travels **per turn** from the browser through `/api/chat` →
`TurnContext` → `ChatService` (which resolves + validates it) → the chat client
(`KeyProxyChatClient` for BYOK, `AnthropicChatClient` for the env-key path) → and, for BYOK, the
keyproxy `StreamTurnRequest.model` → `provider.stream_turn`. **No cryptography changes**: `model`
is a plain request field, never part of the sealed envelope or its AAD.

## Decisions (locked with the issue owner, 2026-07-25)

| Question | Decision |
| --- | --- |
| Where does the selector live? | **Both** — canonical dropdown on the Settings page *and* a quick-switch in the Sidekick chat header, both bound to the same stored value. |
| How is the choice persisted? | **Server-side, per user** (keyed by the JWT-derived `owner`). |
| Table shape | **Dedicated `user_settings(owner PK, chat_model, updated_at)` column** — purpose-built, not a generic key/value store. |
| Default model | **`claude-sonnet-5`** for anyone who has not chosen; also flips the `CHAT_MODEL` server default from `claude-fable-5`. |
| Keyproxy hardening | **Allow-list** `{claude-fable-5, claude-opus-4-8, claude-sonnet-5}`; reject anything else with the existing generic `400 "invalid request"`. |

## Model catalog — the single source of truth

Three models. IDs are confirmed. Descriptions come from issue #124. **Pricing strings are
display-only** and should be confirmed against Anthropic's pricing page before merge — they do not
affect behavior.

| Display name | Model ID | Default? | One-line description (UI) |
| --- | --- | --- | --- |
| Claude Sonnet 5 | `claude-sonnet-5` | ✅ default | Recommended daily driver — balances speed and capability. |
| Claude Opus 4.8 | `claude-opus-4-8` | | Heavy-lifting flagship for complex tasks and deep reasoning. |
| Claude Fable 5 | `claude-fable-5` | | Most capable general model; best for long-horizon, multi-file agentic work. |

This catalog is duplicated in three places by necessity (the `quantcore` core, the Python keyproxy
— a separate deployable service — and the TypeScript frontend). **Keep them in sync.** Each packet
below that defines a copy references this table.

- **Backend core:** a **single neutral constants module** `quantcore/chat_models.py` — pure data,
  no I/O, no service, no DB. Holds `CHAT_MODELS` (list of `{id, name, description}`), `MODEL_IDS`
  (frozenset), and `DEFAULT_CHAT_MODEL`. The **registry** imports this and injects the values into
  both `SettingsService` and `ChatService` (constructor injection). This is deliberate: it keeps the
  services layer **acyclic** — `chat.py` and `settings.py` never import each other for these values
  (see §Compliance). The frontend gets the catalog from the `GET /api/settings` response (server is
  authoritative); it keeps a small typed fallback only for labels.
- **Keyproxy (separate service):** its own `ALLOWED_MODELS` frozenset in the Anthropic provider —
  the keyproxy must not import from `quantcore` (independent deployable / security boundary).
- **Frontend:** a `CHAT_MODELS` fallback array with `{id, name, description}` for rendering.

## Current state — how the model flows today (anchors)

Read these before changing anything.

- **Server default:** `chat_model = os.environ.get("CHAT_MODEL", "claude-fable-5")` —
  [quantcore/services/registry.py:130](../../quantcore/services/registry.py). Injected into both the
  KeyProxy client factory ([registry.py:147](../../quantcore/services/registry.py)) and
  `ChatService(model=chat_model, …)` ([registry.py:158](../../quantcore/services/registry.py)).
- **ChatService** holds `model`/`effort` at construction
  ([quantcore/services/chat.py:216](../../quantcore/services/chat.py)); its default client factory is
  `lambda context: _default_client_factory(self._model, self._effort)`
  ([chat.py:229](../../quantcore/services/chat.py)). It builds the client **once per `stream_chat`**
  from the `TurnContext` ([chat.py:275](../../quantcore/services/chat.py)).
- **TurnContext** dataclass — `key_envelope / scope / auth_token / subject`
  ([chat.py:115](../../quantcore/services/chat.py)). **No `model` field yet.**
- **BYOK client:** `KeyProxyChatClient(model=…)` ([registry.py:147](../../quantcore/services/registry.py));
  it forwards `"model": self._model` in the `/v1/providers/anthropic/messages/stream` body
  ([quantcore/gateways/keyproxy_gateway.py:64](../../quantcore/gateways/keyproxy_gateway.py)).
- **Env-key client:** `AnthropicChatClient(model, effort)` — holds the model, passes it to
  `client.beta.messages.stream(model=self._model, …)`
  ([quantcore/gateways/anthropic_gateway.py:21](../../quantcore/gateways/anthropic_gateway.py)).
- **Keyproxy** receives `StreamTurnRequest.model`
  ([keyproxy/main.py:228](../../keyproxy/main.py)) and forwards it to `provider.stream_turn(model=…)`
  in `stream_messages` at `/v1/providers/anthropic/messages/stream`
  ([keyproxy/main.py:385](../../keyproxy/main.py)); the provider is
  [keyproxy/providers/anthropic.py:82](../../keyproxy/providers/anthropic.py). Generic rejection
  constant `_INVALID = "invalid request"` ([keyproxy/main.py:71](../../keyproxy/main.py)).
- **`/api/chat` request schema** — `messages / interactions / key_envelope / scope`, **no `model`**
  ([api/schemas/chat.py:28](../../api/schemas/chat.py)); route builds `TurnContext`
  ([api/routers/chat.py:35](../../api/routers/chat.py)).
- **Frontend chat request body** — `messages / interactions / key_envelope / scope`, **no model**
  ([frontend/src/api/chatStream.ts:101](../../frontend/src/api/chatStream.ts)); assembled/called from
  `ChatContext` ([frontend/src/chat/ChatContext.tsx:288](../../frontend/src/chat/ChatContext.tsx)).
- **Settings page** — currently only `<ApiKeysSection />`
  ([frontend/src/components/settings/SettingsPage.tsx](../../frontend/src/components/settings/SettingsPage.tsx)).
- **Identity server-side** — `Principal.owner` (prefers `sub`, falls back to `email`)
  ([api/auth.py:124](../../api/auth.py)).
- **No per-user settings store exists.** The only owner-keyed table is `positions`
  ([quantcore/db.py:95](../../quantcore/db.py)).

## Target data flow

```
Browser (Settings dropdown ⇄ chat-header quick-switch, same state)
  │  on mount:  GET  /api/settings            → { chat_model }
  │  on change: PUT  /api/settings {chat_model} (both surfaces write here)
  │  per turn:  POST /api/chat { …, model }     (current selection rides along)
  ▼
api/routers/chat.py  →  TurnContext(model=body.model, subject=owner, …)
  ▼
ChatService.stream_chat
  • resolve_model(requested=context.model, owner=context.subject):
       requested if in CHAT_MODELS
       else stored (SettingsService.get_chat_model(owner)) if in CHAT_MODELS
       else DEFAULT_CHAT_MODEL
  • context.model := resolved
  • client = client_factory(context)          ← factory reads context.model
  ▼
BYOK: KeyProxyChatClient(model=context.model) → keyproxy /v1/…/messages/stream {model}
                                                  → provider.stream_turn(model)   ← allow-list gate
Env-key: AnthropicChatClient(model=context.model) → Anthropic SDK
```

The **server is the source of truth** (stored setting); the request body carries the current
selection so a just-clicked quick-switch takes effect on the very next turn with no round-trip
race. The server still validates every requested model and falls back safely.

---

## Work packets

Each packet is one commit. Backend packets (1–5) are independently testable with `python -m
unittest`. Frontend packets (6–8) with `cd frontend && npx vitest run`. Do them in order —
later packets import earlier ones.

### WP1 — `user_settings` table + repository

**Files:** `quantcore/db.py`, new `quantcore/repositories/user_settings_repository.py`, new
`test_user_settings_repository.py` (or add to an existing repo test module).

1. In [quantcore/db.py](../../quantcore/db.py) `init_schema()` DDL, add (mirror the `positions`
   idempotent style — `CREATE TABLE IF NOT EXISTS`):
   ```sql
   CREATE TABLE IF NOT EXISTS user_settings (
       owner       TEXT PRIMARY KEY,
       chat_model  TEXT NOT NULL,
       updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
   );
   ```
   Bump the "N tables" count in the module docstring/comments and CLAUDE.md is updated in WP9.
2. New `UserSettingsRepository` (SQL only, no analytics — follows the repository rule). Methods:
   - `get_chat_model(owner: str) -> str | None` — `SELECT chat_model FROM user_settings WHERE owner=%s`.
   - `set_chat_model(owner: str, chat_model: str) -> None` — `INSERT … ON CONFLICT (owner) DO UPDATE
     SET chat_model = EXCLUDED.chat_model, updated_at = now()`.
   Use `from quantcore.db import get_connection` exactly like the other repositories.
3. **Test:** round-trip set→get; upsert overwrites; unknown owner → `None`.

### WP2 — catalog constants module + `SettingsService` + registry wiring

**Files:** new `quantcore/chat_models.py`, new `quantcore/services/settings.py`,
`quantcore/services/registry.py`, a service test.

1. **Neutral constants module** `quantcore/chat_models.py` (pure data — no I/O, no service, no DB;
   this is what keeps the services layer acyclic):
   ```python
   DEFAULT_CHAT_MODEL = "claude-sonnet-5"
   CHAT_MODELS = [
       {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "description": "Recommended daily driver — balances speed and capability."},
       {"id": "claude-opus-4-8", "name": "Claude Opus 4.8", "description": "Heavy-lifting flagship for complex tasks and deep reasoning."},
       {"id": "claude-fable-5",  "name": "Claude Fable 5",  "description": "Most capable general model; best for long-horizon, multi-file agentic work."},
   ]
   MODEL_IDS = frozenset(m["id"] for m in CHAT_MODELS)
   ```
2. `SettingsService(repo, *, allowed: frozenset[str], default: str, catalog: list[dict])`:
   - `get_settings(owner) -> SettingsView` — **single method** returning the current model (stored
     value **if still in `allowed`**, else `default`) *and* the catalog, so the route stays exactly
     one service call deep.
   - `set_chat_model(owner, model) -> None` — raise `ValueError` if `model not in allowed`; else
     persist via the repository. The route maps `ValueError` → `400`.
   - `get_chat_model(owner) -> str` — the default-safe resolver used internally (and reused by the
     chat path if ever needed); `get_settings` wraps it.
3. Register in [registry.py](../../quantcore/services/registry.py): import `quantcore.chat_models`,
   construct `UserSettingsRepository` + `SettingsService(repo, allowed=MODEL_IDS,
   default=DEFAULT_CHAT_MODEL, catalog=CHAT_MODELS)`, add `settings: SettingsService` to the frozen
   `Services` dataclass, and **inject `settings` + `allowed=MODEL_IDS` into `ChatService`** (WP4).
   The registry is the composition root — it is the one place allowed to import everything; service
   modules receive values via constructor injection only (same pattern as `RecommendationsService`,
   which the registry wires from its sibling services).
4. **Test:** stored-but-retired model → default; set with bad model → `ValueError`; set/get happy
   path; `get_settings` returns the catalog + current value.

### WP3 — REST `/api/settings` (GET + PUT)

**Files:** new `api/routers/settings.py`, new `api/schemas/settings.py`, register the router in
`api/main.py`, a route test under the existing API test suite.

1. Schema `api/schemas/settings.py` — **Pydantic models drive serialization** (anti-pattern 7: no
   hand-rolled JSON):
   ```python
   class ModelInfo(BaseModel):
       id: str
       name: str
       description: str
   class SettingsResponse(BaseModel):
       chat_model: str
       models: list[ModelInfo]     # server is authoritative for the picker
   class UpdateSettingsRequest(BaseModel):
       chat_model: str
   ```
2. Router (thin adapter, **exactly one service call deep**, `require_principal` like the other
   routers). Match the neighboring **sync `def`** convention for DB-backed routes — e.g.
   `get_portfolio` ([portfolio.py:51](../../api/routers/portfolio.py)) is `def`, not `async def`,
   because `psycopg2` is blocking and FastAPI runs sync routes in a threadpool. Use
   `response_model=` like `dashboard.py` (`response_model=DashboardStats`):
   - `GET /api/settings` (`response_model=SettingsResponse`) → `services().settings
     .get_settings(principal.owner)` — the single call returns both current value and catalog.
   - `PUT /api/settings` (`response_model=SettingsResponse`) → `services().settings
     .set_chat_model(principal.owner, body.chat_model)`; catch `ValueError` →
     `HTTPException(status_code=400)`; return the fresh `get_settings` view.
3. Register the router in [api/main.py](../../api/main.py) next to the other `include_router` calls.
4. **Test:** GET default when unset; PUT valid → persists → GET reflects it; PUT invalid model → 400;
   two different owners are isolated.

### WP4 — thread `model` through the chat turn (per-turn resolution)

**Files:** `api/schemas/chat.py`, `api/routers/chat.py`, `quantcore/services/chat.py`,
`quantcore/services/registry.py`, chat service tests.

1. **Schema** [api/schemas/chat.py](../../api/schemas/chat.py) — add to `ChatRequest`:
   ```python
   model: str | None = None   # current UI selection; server validates + falls back
   ```
2. **Route** [api/routers/chat.py](../../api/routers/chat.py) — pass it into `TurnContext`:
   ```python
   context = TurnContext(..., subject=principal.subject, model=body.model)
   ```
   (Adapter stays one service call deep — `TurnContext` construction is not a service call.)
3. **TurnContext** [chat.py:115](../../quantcore/services/chat.py) — add `model: str | None = None`.
4. **ChatService** [chat.py:207](../../quantcore/services/chat.py):
   - Constructor: accept `settings=None` (the `SettingsService` instance) and
     `allowed: frozenset[str] = frozenset()`, and store both. Keep `model=` as the **fallback
     default** (registry passes `DEFAULT_CHAT_MODEL`). **Do not import `settings.py` or
     `chat_models.py` for these values** — take them by injection so the services layer stays
     acyclic (see §Compliance).
   - Add `_resolve_model(context) -> str`:
     ```python
     req = context.model
     if req in self._allowed:
         return req
     if self._settings is not None:
         stored = self._settings.get_chat_model(context.subject)  # already default-safe
         if stored in self._allowed:
             return stored
     return self._model
     ```
   - In `stream_chat`, before building the client: `context.model = self._resolve_model(context)`
     (mutate the dataclass field, or `dataclasses.replace`), **then** call the factory.
   - Default factory lambda ([chat.py:229](../../quantcore/services/chat.py)) → use the resolved
     model: `lambda context: _default_client_factory(context.model or self._model, self._effort)`.
5. **Registry** [registry.py](../../quantcore/services/registry.py) (import `quantcore.chat_models`):
   - `chat_model = os.environ.get("CHAT_MODEL", DEFAULT_CHAT_MODEL)` (default now Sonnet 5).
   - KeyProxy factory ([registry.py:147](../../quantcore/services/registry.py)):
     `model=context.model or chat_model` instead of `model=chat_model`.
   - `ChatService(..., model=chat_model, settings=settings, allowed=MODEL_IDS, ...)`.
6. **Tests:** requested valid model wins; requested invalid → stored; no stored → default; the model
   passed to the fake client equals the resolved one; env var override still respected as fallback.

### WP5 — keyproxy allow-list

**Files:** `keyproxy/providers/anthropic.py`, `keyproxy/main.py`, keyproxy tests.

1. In [keyproxy/providers/anthropic.py](../../keyproxy/providers/anthropic.py) add:
   ```python
   ALLOWED_MODELS = frozenset({"claude-sonnet-5", "claude-opus-4-8", "claude-fable-5"})
   def supports_model(model: object) -> bool:
       return isinstance(model, str) and model in ALLOWED_MODELS
   ```
   (Keyproxy is a **separate deployable** — it must own its copy; do not import from `quantcore`.)
2. In `stream_messages` [keyproxy/main.py:385](../../keyproxy/main.py), add the gate alongside the
   existing pre-key checks (session live / sub match / classify == read), rejecting with the same
   generic 400:
   ```python
   if not provider.supports_model(body.model):
       raise HTTPException(status_code=400, detail=_INVALID)
   ```
   Note `STREAM_FALLBACKS = [{"model": "claude-opus-4-8"}]`
   ([anthropic.py:36](../../keyproxy/providers/anthropic.py)) is an allow-listed model — leave it.
3. **Never log the model value** in a way that violates the never-log policy — a bare rejected
   model string is fine to omit; keep the existing `_log_rejection(status)` pattern (status only).
4. **Tests:** allowed model streams; disallowed/missing model → 400 `invalid request`; assert no new
   sensitive logging. Add the log assertion the BYOK never-log policy requires for the new path.

### WP6 — frontend model catalog + settings API client

**Files:** new `frontend/src/api/settings.ts`, new `frontend/src/chat/models.ts` (catalog), tests.

1. `frontend/src/chat/models.ts` — a **fallback** catalog (the `GET /api/settings` `models` array is
   authoritative; this renders labels before the fetch resolves / if it fails):
   ```ts
   export interface ChatModel { id: string; name: string; description: string; }
   export const CHAT_MODELS: ChatModel[] = [ /* Sonnet 5, Opus 4.8, Fable 5 — see catalog table */ ];
   export const DEFAULT_CHAT_MODEL = 'claude-sonnet-5';
   ```
2. `frontend/src/api/settings.ts` — `getSettings()` (GET → `{ chat_model, models }`) and
   `putChatModel(id)` (PUT), following the fetch/`API_BASE`/`API_TOKEN` conventions in
   [chatStream.ts](../../frontend/src/api/chatStream.ts) and the existing `api/keyproxy.ts`.
3. **Test:** mock fetch; GET parses `chat_model` + `models`; PUT sends the right body; error → throws.

### WP7 — Settings-page dropdown (canonical surface)

**Files:** new `frontend/src/components/settings/ModelSection.tsx`,
`frontend/src/components/settings/SettingsPage.tsx`, test.

1. `ModelSection` — an MUI `Select` (match `ApiKeysSection` card/`Typography` styling
   [ApiKeysSection.tsx](../../frontend/src/components/settings/ApiKeysSection.tsx)) rendering the
   `models` from the `getSettings()` response (falling back to `CHAT_MODELS` for labels; name +
   description). Loads the current value via `getSettings()` on mount; on change calls
   `putChatModel(id)` and updates shared state (see WP8 for where state lives).
2. Add `<ModelSection />` to [SettingsPage.tsx](../../frontend/src/components/settings/SettingsPage.tsx)
   under `<ApiKeysSection />`.
3. **Test:** renders three options; selecting one calls `putChatModel`; shows the loaded value.

### WP8 — chat-header quick-switch + send model per turn (shared state)

**Files:** `frontend/src/chat/ChatContext.tsx`, `frontend/src/api/chatStream.ts`, the chat header
component (find under `frontend/src/components/chat/` — the panel header), tests.

1. **Shared state:** hold the selected model in `ChatContext` (a `selectedModel` +
   `setSelectedModel`), seeded from `getSettings()` on first mount, defaulting to
   `DEFAULT_CHAT_MODEL` until loaded. Both the Settings dropdown (WP7) and the header switch read/
   write this same value; `setSelectedModel` also `putChatModel`s. (If wiring Settings into
   ChatContext is awkward, an equally acceptable design is a tiny dedicated `ModelContext`/hook that
   both surfaces consume — pick one, document it, keep a single source of truth.)
2. **Header switch:** a compact `Select`/menu in the chat panel header showing the current model,
   options from the same shared-state catalog seeded in WP8.1 (server's `models`, `CHAT_MODELS` only
   as fallback) — do not read `CHAT_MODELS` directly, to stay consistent with WP7's server-authoritative
   catalog.
3. **Send it:** add `model?: string` to `streamChat` and include it in the POST body
   ([chatStream.ts:101](../../frontend/src/api/chatStream.ts)):
   ```ts
   body: JSON.stringify({ messages, ...(model ? { model } : {}), ...interactions, ...keyMaterial }),
   ```
   Pass `selectedModel` from the `streamChat(...)` call site in
   [ChatContext.tsx:288](../../frontend/src/chat/ChatContext.tsx).
4. **Tests:** switching the header updates the shared value and PUTs; the next `streamChat` body
   includes `model`; Settings and header stay in sync.

### WP9 — default flip, docs, compose/env

**Files:** `quantcore/services/registry.py` (done in WP4), `CLAUDE.md`, `.env.example` (if present),
`docker-compose.yml` / `runUI-CONTAINERS.sh` only if they pin `CHAT_MODEL`.

1. Confirm the `CHAT_MODEL` default is `claude-sonnet-5` everywhere it appears; grep for the old
   `claude-fable-5` default and update stale references (leave `STREAM_FALLBACKS` opus fallback).
2. Update CLAUDE.md: note the new `user_settings` table (table count), the `/api/settings` routes,
   the SettingsService, and that the Sidekick model is user-selectable (default Sonnet 5).
3. If any compose/env file hardcodes `CHAT_MODEL=claude-fable-5`, update or remove it.

---

## Compliance with architectural-standard-v2

This plan was checked against [`architectural-standard-v2.md`](architectural-standard-v2.md). Mapping:

| Rule / principle | How this plan satisfies it |
| --- | --- |
| **P1 / Rule 1 — all business logic in the services layer** | Model *resolution* (requested → stored → default, allow-list-aware) lives in `ChatService._resolve_model`; *validation on write* lives in `SettingsService.set_chat_model`. Routes contain none of it. |
| **P4 / Rule 7 — capabilities born as a service** | The settings capability is a new `SettingsService` first, exposed via REST (`/api/settings`), then surfaced to the WebUI. Not built surface-first. |
| **Rule 2 — no direct gateway/DB access from adapters** | The `/api/settings` and `/api/chat` routes call services only. All SQL is in `UserSettingsRepository`; no `psycopg2` in any route. |
| **§5.4 — thin routes, Pydantic contracts, no hand-rolled JSON (anti-pattern 7)** | WP3 uses `response_model=SettingsResponse`/`UpdateSettingsRequest`; each route is exactly one service call deep (`get_settings` / `set_chat_model`). |
| **Services acyclic / composition root = registry** | Catalog constants live in the neutral `quantcore/chat_models.py` (pure data, not a service). The registry — the one place that imports everything — injects `allowed`/`default`/`catalog` and the `SettingsService` instance into `ChatService`. `chat.py` and `settings.py` never import each other for these values. `ChatService` composing a settings dependency mirrors the sanctioned `RecommendationsService` pattern (which the registry wires from its sibling services). |
| **§9 — sync `def` for blocking DB routes** | WP3 follows the real convention (`get_portfolio` is sync `def`) rather than the aspirational "prefer async", because `psycopg2` is blocking; FastAPI threadpools the sync route. |
| **§8 — security / argument validation at the front door** | The user-supplied `model` is validated server-side (`ChatService` allow-list resolution) **and** independently at the keyproxy (`supports_model`), so a tampered client cannot run an off-list model on the user's key. Defense in depth. |

**Keyproxy note (why WP5 is not a Rule 1 violation):** the keyproxy is a *separate deployable
security-boundary service*, not a `quantcore` adapter over the services layer — standard-v2 governs
the `quantcore`/`api`/`mcp` topology. The keyproxy already owns provider-specific policy in its
provider module (`classify`, `supports_action`, `supports_scope`); `supports_model` is the same
kind of provider-local gate and belongs there, with its own catalog copy (it must not import
`quantcore`). This is consistent with the BYOK architecture, not a departure from it.

## Cross-cutting guardrails (read before each packet)

- **No crypto / envelope changes.** `model` is a plain field. Do **not** add it to the sealed
  envelope, the scope, or any AAD. The vault, `sealKeyForTurn`, `keyproxy/crypto.py`, and
  `scopes.py` are untouched.
- **Never-log policy (BYOK).** No packet may log API keys, envelopes, `Authorization` headers, or
  request bodies. WP5 adds a log assertion for its new reject path. Model IDs are not secret, but
  keep to the existing status-only rejection logging.
- **Architectural standard.** Adapters (routes, MCP) stay exactly one service call deep. Business
  logic (model resolution, allow-list-aware defaulting) lives in `SettingsService`/`ChatService`.
  Repositories are SQL-only. Services never import each other or the registry — compose via the
  registry with constructor injection (ChatService gains a `settings` dependency this way).
- **Three catalog copies stay in sync** (backend, keyproxy, frontend) — see the catalog table.
- **Fail safe on unknown models** at every layer: server resolves to stored→default; keyproxy
  rejects with generic 400. A retired model in storage degrades to the default, never errors.

## Verification

- **Backend:** `coverage run -m unittest discover && coverage report` (CI enforces a ratchet floor
  — new modules need tests). Targeted: `python -m unittest test_user_settings_repository
  test_settings_service` (names per WP1/WP2), plus the chat and keyproxy suites.
- **Frontend:** `cd frontend && npx vitest run --coverage`.
- **Manual (compose stack via `runUI-CONTAINERS.sh`):** open Settings → change model → reload →
  persists; open Sidekick header → switch model → send a turn → the turn runs on the new model;
  confirm a disallowed model (crafted request) returns `400 invalid request` from the keyproxy.
- **OpenAPI:** `deploy.yml` diffs the OpenAPI surface — the two new `/api/settings` routes and the
  new `ChatRequest.model` field are expected additions; regenerate/commit the spec if the CI gate
  requires it.

## Rollout

Standard flow: feature branch → PR → review/approve → merge to `main`. `deploy.yml` builds and
image-rolls **test** (`quantcore-api`, `quantcore-keyproxy`, `quantui`) automatically. Verify on
the test URL, then promote to **prod** by dispatching `prod-rollout.yml` with the commit SHA
(copies images by digest). The `user_settings` table is created idempotently by `init_schema()` on
API startup in each project — no manual migration step.

## Out of scope

- Per-model `effort`/thinking tuning (effort stays `medium` across all three; a future issue).
- Exposing model choice to the MCP wrappers / non-chat consumers.
- A generic key/value preferences store (explicitly rejected in favor of the dedicated column).
- Usage/cost metering or per-model budgets.
