# Plan: Cloud Run service inventory (issue #161)

## Context

Before #161, each Cloud Run service was a hand-written `gcloud run deploy` function in **both**
workflows. Each carried only the image plus sizing. Everything else (env, secrets, Cloud SQL,
port, the runtime SA, public access) was set once by hand at first deploy and then inherited. So a
new MCP wrapper needed someone with broad GCP rights to create it, plus edits to two workflow
files that had to stay in agreement.

Goal: a wrapper is onboarded by a PR that touches only code and an inventory. CI creates a missing
service with its full config: test on merge, prod behind the `prod` environment gate. Services
that need a human (api, keyproxy, quantui) fail loudly with their runbook instead.

**Revised 2026-10-05:** creating a *public* wrapper needs `run.services.setIamPolicy`, and that
grant was declined (gotcha 1). CI still holds all the config and takes the service over after it
exists, but the first create of a wrapper is two commands run by an owner.

Reference doc: [`docs/architecture/cloudrun-services.md`](../architecture/cloudrun-services.md).

## Design

- `deploy/cloudrun-services.toml` holds `[environments.test|prod]` plus one `[[services]]` block
  per service. Per-env values are tables, and templates are resolved from the environment block.
- `scripts/cloudrun_services.py` has three subcommands: `names` (feeds the workflow's two
  `run_parallel` phases), `deploy` (one service) and `check` (read-only drift report). It uses
  stdlib only (`tomllib`), so the runner's stock `python3` (3.12) runs it with no install.
- Both workflows' roll-out step reads names per phase from the script and runs
  `cloudrun_services.py deploy` for each through `run_parallel`. The sizing env blocks and the
  per-service functions are gone from both files, so there is nothing left for them to disagree
  about.
- `tests/test_cloudrun_services.py` covers:
  - deploy behaviour against a stub `gcloud` on PATH (update, create, manual-missing, IAM check,
    by-digest, failures)
  - validation
  - the real inventory against `cloudbuild.yaml`, the prod promote loop, `ci_wrapper_smoke`'s
    `WRAPPERS`, the runbook paths, the sizing rules and secret-name-only refs

## Gotchas (record of what cost time or would mislead)

1. **`run.developer` cannot set IAM policy.** Neither project's deployer holds
   `run.services.setIamPolicy` (verified read-only on 2026-10-04: test has artifactregistry.writer,
   cloudbuild.builds.editor, logging.viewer, run.developer, serviceusage.serviceUsageConsumer and
   storage.admin; prod has logging.viewer and run.developer, plus resource-level AR and
   serviceAccountUser grants).
   - `gcloud run deploy --allow-unauthenticated` on a create would therefore fail *after* creating
     the service.
   - So the create omits the flag and runs a separate `add-iam-policy-binding`.
   - **A bind that fails after the create is still the wrong order** (PR #309 review): the
     service is left behind answering 403. So the create path first asks
     `projects.testIamPermissions` (Resource Manager v1, which needs no role of its own) for
     `run.services.create` and `run.services.setIamPolicy`, and creates nothing if either is
     missing. gcloud has no command for that call, so the script POSTs it with
     `gcloud auth print-access-token`. A failed check fails closed. Verified on 2026-10-05 that
     the Resource Manager API is enabled in both projects.
   - The update path never passes either `--allow-unauthenticated` or `--no-allow-unauthenticated`,
     because each tries to write IAM. It only reads the policy, and fails on a public service
     that has lost its `allUsers` binding.
   - **The grant is declined (John, 2026-10-05).** `setIamPolicy` is not scoped to adding
     `allUsers`: a deployer holding it could make any service public (api, keyproxy), strip
     bindings, or bind an outside identity for persistent access, and the deployer is reachable
     from any workflow run in the repo. Onboarding a wrapper is rare and costs two commands, so
     the trade isn't worth it. The by-hand path is in `cloudrun-services.md`
     ("Onboarding a wrapper by hand"). The WIF conditions being repo-only, not branch- or
     environment-scoped, is issue #313.
2. **A missing service shows up only as text.** `gcloud run services describe` exits 1 for "not
   found" and for every other failure alike. The script treats `Cannot find service` in stderr as
   missing, and anything else as a hard error. Treating every failure as missing would turn a
   permissions blip into a create attempt.
3. **The live config was not uniform, and the inventory records it as it is.**
   - `quantcore-portfolio` and `quantcore-arbitrage` listen on ports 6006/6007, use 256Mi and
     max 20, and have a trailing `/` on `QUANTCORE_REST_URL`. They were created by hand
     (portfolio-lots-plan 4.8a).
   - quantui's `QUANTCORE_REST_URL` is the api's legacy `-uc.a.run.app` form.
   - Changing any of these would mint a new revision for no gain, so the inventory keeps them.
4. **The live services already had cpu boost on.** Before #161 only the api's deploy passed
   `--cpu-boost`, yet every live service already had startup boost enabled. The inventory says
   `cpu_boost = true` everywhere, and the script passes `--no-cpu-boost` explicitly when false,
   so the field means what it says.
5. **Timeouts that weren't pinned.** api, keyproxy and quantui never passed `--timeout`, and all
   three run at Cloud Run's default of 300 s. The inventory now pins 300.
6. **A missing manual service now fails the roll-out.** It used to be a warning, or an unnoticed
   gap. That is the "loud" half of the bilge-pump design: a missing api is not something to roll
   past.
7. **`ManifestError` goes to stderr.** The workflow captures `names`'s stdout as the service
   list, so an error printed there would be read as a service name.
8. **The script prints every gcloud command it runs.** These carry secret *names* only. The
   inventory validator refuses anything that isn't `name:version`.
9. **The "no `--set-*`" test must strip comments and docstrings** before matching, because the
   script's own docstring explains why it never uses `--set-*`.
10. **`python3` ≥ 3.11 is required** for `tomllib`. `ubuntu-latest` ships 3.12. On a runner image
    downgrade, the import error names the module.

## Checkpoint log

| Step | Result |
|---|---|
| Inventory + script + tests | Done. The 47 tests in `test_cloudrun_services` + `test_ci_parallel` pass. |
| Workflows switched to the script | Done. `tests/test_ci_parallel.py` updated for the new wiring. |
| `check --env test` / `--env prod` (2026-10-04) | 10/10 `ok` in both. The first manifest-driven roll-out is image-only. |
| Deployer IAM audit (read-only) | run.developer ✅, AR ✅, serviceAccountUser on all 4 runtime SAs ✅, **setIamPolicy ❌ in both projects**. The grant is documented in `cloudrun-services.md`. |
| Docs | `cloudrun-services.md` added. CLAUDE.md, readme, prod-promotion.md, quantui.md and prod-rollout-plan P11 updated. |
| PR #309 review (2026-10-05) | setIamPolicy preflight before any create (refuses, creates nothing, prints the grant). `load`, `_validate`, `deploy_args` and `cmd_deploy` split; every function now scores under complexipy's 15. 5 new tests. |
| setIamPolicy grant (2026-10-05) | **Declined.** Wrappers are created by hand, then CI takes them over. Docs updated in the follow-up PR. WIF gap → #313. |
