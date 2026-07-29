# AGENTS.md

Guidance for coding agents (Codex and friends) working in this repository.

## Read `CLAUDE.md` first — it is the single source of truth

The full architecture, deployment topology, environment rules, and working constraints live in
[`CLAUDE.md`](CLAUDE.md). **Read it before making changes.**

This file used to carry its own copy of that guidance and silently rotted — it was still
describing a 16-table schema, a Flask `api/app.py`, and modules (`HarvesterPlanStore.py`,
`ohlcv_cache.py`) that no longer exist. Rather than maintain two documents that drift apart, this
one now points at the other. **Do not re-add architecture notes here** — put them in `CLAUDE.md`.

For the human-facing tour — installation, endpoints, the UI, the container stack, MCP setup —
see [`readme.md`](readme.md).

## Commands

```bash
# Run the application (generates HTML report + sends Discord notifications)
python main.py

# Run all tests (suites live under tests/; tests/__init__.py swaps in the test
# DSN before quantcore.db is imported)
python -m unittest discover -s tests -t .

# A single test module (dotted path from the repo root)
python -m unittest tests.test_money

# Backend coverage (CI enforces a ratchet floor)
coverage run -m unittest discover -s tests -t . && coverage report

# Frontend tests with coverage
cd frontend && npx vitest run --coverage

# REST API
uvicorn api.main:app --host 127.0.0.1 --port 5001

# Database migrations (defaults to the test database; prompts before a prod migrate)
./scripts/flyway.sh info

# Activate virtualenv / install dependencies
source .venv/bin/activate
pip install -r requirements.txt
```

## Non-negotiables

These are the constraints most likely to be violated by an agent that skipped `CLAUDE.md`:

- **Documentation is part of the change.** Any change that alters what a reader of the docs would
  be told must update the docs **in the same PR** — a new or renamed service/route/tool/UI page, a
  schema change, deployment or auth wiring, a new script, a changed default. `CLAUDE.md` holds
  architecture and constraints for agents; `readme.md` is the human tour; `docs/proposals/*.md`
  carry plans and their checkpoint logs. One fact, one home — if two docs would state it, one
  states it and the other links. See the "Documentation is part of the change" section in
  `CLAUDE.md` for the full rule.
- **Prod (`quantcore-prod-20260606`) is the system of record**; test is for development and CI
  only.
- **Adapters are one service call deep.** Business logic goes in `quantcore/services/`; REST
  routes (`api/routers/*`) and MCP tool bodies (`fastMCPTest/*`) are thin. Service modules never
  import each other or the registry.
- **Every schema change ships twice** — as a Flyway file under `db/migrations/` *and* mirrored
  into `init_schema()` in `quantcore/db.py`.
- **BYOK never-log policy:** no API keys, `Authorization` headers, envelopes, decrypted payloads,
  request bodies, or exception dumps containing credentials may reach any log or print. New
  failure paths must add the corresponding log assertion.
- **On existing Cloud Run services always use `--update-env-vars` / `--update-secrets`** — the
  `--set-*` variants replace the entire set and have broken prod before.
- **New or materially changed UI components** must be GenUI-compliant, registered in both
  component registries, and ship vitest tests in the same PR.
