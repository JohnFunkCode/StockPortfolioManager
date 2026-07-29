## What changed

<!-- One or two sentences. What does this PR do, and why? -->

## Documentation

Docs ship **with** the change, not after it. Tick what applies:

- [ ] No doc change needed — this PR alters nothing a reader of the docs would be told
- [ ] `CLAUDE.md` — architecture, constraints, or a rule agents must follow
- [ ] `readme.md` — the human tour (install, config, endpoints, UI, containers, MCP)
- [ ] `docs/proposals/*.md` — checkpoint log appended for the step this PR lands
- [ ] Gotchas recorded — anything that cost real time to figure out is written down

> Update the docs when you add/remove/rename a service, repository, gateway, or analytics module;
> add or change a REST route group, MCP tool, or UI page; change the schema, deployment, CI/CD,
> environment, or auth wiring; add an operational script; or change a default or config file's
> role. Full rule: "Documentation is part of the change" in `CLAUDE.md`.

## Checks

- [ ] Backend tests pass — `python -m unittest discover -s tests -t .`
- [ ] Front-end tests pass (if `frontend/` changed) — `cd frontend && npx vitest run --coverage`
- [ ] Schema changes ship **twice** — a Flyway file under `db/migrations/` *and* `init_schema()`
- [ ] No credentials, keys, `Authorization` headers, envelopes, or request bodies reach any log

## Verification

<!-- How did you prove this works? Commands run, output, screenshots, the test/prod URL you hit. -->
