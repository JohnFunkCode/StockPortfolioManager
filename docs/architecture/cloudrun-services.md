# Cloud Run service inventory (issue #161)

Every Cloud Run **service** in both projects is described in one file,
[`deploy/cloudrun-services.toml`](../../deploy/cloudrun-services.toml). Both roll-out workflows
(`deploy.yml` → test, `prod-rollout.yml` → prod) deploy from it through
[`scripts/cloudrun_services.py`](../../scripts/cloudrun_services.py). Jobs (`quantcore-report`,
`-news`, `-migrate`) are not in it. They are still `gcloud run jobs update` steps in the workflows.

The plan, the IAM finding and the gotchas are in
[`service-inventory-plan.md`](../proposals/service-inventory-plan.md).

## What a roll-out does to each service

| Live state | Inventory says | Result |
|---|---|---|
| exists | anything | Rolls out the new image. Re-asserts every scalar (`port`, `cpu`, `memory`, `cpu_boost`, `timeout`, `concurrency`, `max_instances`, `ingress`, `service_account`). Applies only the `env`/`secrets`/`cloudsql` entries that differ, with `--update-env-vars` / `--update-secrets` / `--add-cloudsql-instances`. A key the service holds that the inventory doesn't list is **kept**. IAM is checked, never changed. A `public` service without the `allUsers` invoker binding fails the step and prints the grant command. |
| missing | `first_create = "auto"` | Creates the service with its full config, then grants `allUsers` → `roles/run.invoker`. |
| missing | `first_create = "manual"` | **Fails the roll-out** with `::error` naming the `runbook`. These services need setup a deployer can't or shouldn't do: Cloud SQL and DSN secrets for the api, a private invoker for keyproxy, IAP for quantui. |

So existing config is preserved unless the inventory deliberately changes it. To change a
setting, edit the inventory in a PR, and the next roll-out applies it. Don't run a one-off
`gcloud run services update`: the next roll-out reverts the scalars, though a key left out of
`env` survives.

Rules the script enforces (as `ManifestError`, exit 2) and the tests guard:

- `PORT` is never an env var. Cloud Run sets it from `port` (the container port) and rejects
  setting it directly.
- Values may not contain commas, because gcloud's `--update-env-vars` splits on them.
- A secret is a `name:version` reference. Never put a secret value in the inventory.
- Templates `{project} {project_number} {region} {runtime_sa} {api_url}` come from
  `[environments.<env>]`. Any string may be `{ test = "…", prod = "…" }` where the two
  environments differ.

## Adding an MCP wrapper (the onboarding flow)

A new wrapper is a PR that touches only code and the inventory, and needs no GCP permissions
from its author:

1. Write the server module, `fastMCPTest/<name>_server.py`, served by the shared `quantcore-mcp`
   image.
2. Add it to `WRAPPERS` in `scripts/ci_wrapper_smoke.py`, so CI boots it and counts its tools.
3. Add a `[[services]]` block to `deploy/cloudrun-services.toml`. Copy a standard wrapper, then set
   `name`, `SERVER_MODULE`, `phase = 2` and `first_create = "auto"`.
   `tests/test_cloudrun_services.py` fails if the inventory's wrappers and `WRAPPERS` disagree.
4. Add a contract case for each of its tools to `CASES` in `tests/test_mcp_tool_contracts.py`
   (#44): the arguments and the exact REST call each tool should make. The test's completeness
   guard fails on any tool without a case and on a total that no longer matches the documented
   count. Adding a tool to an *existing* wrapper is the same step on its own. Design and gotchas:
   [`mcp-tool-regression-plan.md`](../proposals/mcp-tool-regression-plan.md).
5. Update the tool count in `CLAUDE.md` and the constant in that test. Optionally, add the remote
   to `.mcp.json` for AI clients.

Then:

- **Merge → test.** `deploy.yml`'s phase 2 finds the service missing. The deployer doesn't hold
  `run.services.setIamPolicy`, and the grant is declined (see [IAM model](#iam-model)), so the
  step creates nothing and fails. Expect that red run. An owner then creates the service with
  [two commands](#onboarding-a-wrapper-by-hand).
- **`prod-rollout.yml` → prod.** The same thing happens behind the `prod` environment's
  required-reviewer gate. Dispatching it and the by-hand create are both John's.
- After the create, every later roll-out treats the wrapper as an existing service.

Check either environment read-only at any time:

```bash
python scripts/cloudrun_services.py check --env test
```

```bash
python scripts/cloudrun_services.py check --env prod
```

Each service prints `ok`, `DRIFT` (with the differing lines), or `MISSING` (with what a roll-out
would do). Comparing against live state needs `run.services.get` in the project.

## IAM model

Team members (for example Thomas) need **no** deploy permissions: their path to production is a
PR, which John reviews. Everything below is held by the CI deployer,
`quantcore-deployer@<project>.iam.gserviceaccount.com`, and is reached only through
Workload Identity Federation from this repo's workflows.

What the deployer needs, as verified read-only on 2026-10-04:

| Need | Test (`quantcore-test-20260606`) | Prod (`quantcore-prod-20260606`) |
|---|---|---|
| `roles/run.developer` (deploy, describe, create) | project ✅ | project ✅ |
| Artifact Registry | `artifactregistry.writer` (project) ✅ | `artifactregistry.writer` on prod AR, `artifactregistry.reader` on test AR ✅ |
| `iam.serviceAccountUser` on each runtime SA the inventory names | `493357101423-compute@developer…` ✅, `keyproxy-runtime@…` ✅ | `quantcore-run@…` ✅, `keyproxy-runtime@…` ✅ |
| `run.services.setIamPolicy` (makes a newly created wrapper public) | ❌ **declined** (2026-10-05) | ❌ **declined** (2026-10-05) |

The other roles each workflow needs (Cloud Build, logging, the migrate Job's SA) are listed in the
workflow headers and in [`prod-promotion.md`](../operations/prod-promotion.md).

`run.developer` can create a service but cannot set its IAM policy. That is why the create path
never passes `--allow-unauthenticated`, which would fail. Instead it runs a separate
`add-iam-policy-binding` after the create.

**Before it creates anything, the script checks the permission.** It calls Resource Manager's
`projects.testIamPermissions` for `run.services.create` and `run.services.setIamPolicy`, as the
deployer, with `gcloud auth print-access-token` (the token goes only into the request header).
If either is missing, nothing is created: the step fails with `::error title=<name> not
created::`, naming the missing permission and pointing to the by-hand onboarding below. There the owner
runs the same deploy under their own login, which creates the service and binds `allUsers` in
one go, so there is no separate grant to run. That refusal is the normal onboarding path, because the grant is
declined (below). If the check itself fails (no token, API unreachable), the step fails closed and creates
nothing. Without the check, run.developer would create a wrapper it could not make public, and
leave a service behind that answers every client with 403.

**Existing services are unaffected.** The update path makes no permission check and only *reads*
IAM.

**The grant is declined (John, 2026-10-05).** A custom role holding `run.services.setIamPolicy`
would let CI onboard a wrapper unattended. But the binding is project-wide, so it would also let
whoever holds the deployer:

- make any service public, including keyproxy;
- strip invoker bindings, which is an outage that leaves the services looking healthy;
- bind an outside identity to a role on a service. That access survives rotating the deployer's
  credentials, so a single compromise becomes a persistent one.

New wrappers are rare, and the manual step is two commands, so the trade isn't worth it. **Don't
re-propose the grant without revisiting that reasoning.**

The deployer was also reachable from any workflow run in the repo, because both WIF providers were
conditioned on the repository alone. [#313](https://github.com/JohnFunkCode/StockPortfolioManager/issues/313)
narrows them: test to runs on `main`, and prod to `prod-rollout.yml` on `main` in the `prod`
environment. The runbook, and whether each step has been applied yet, are in
[`wif-trust-plan.md`](../proposals/wif-trust-plan.md).

### Who can call the services (runtime auth)

**The 7 wrappers and `quantcore-api` are `allUsers` invokers with `ingress = all`, by design.**
Cloud Run IAM is not the authentication layer. AI clients (Claude Code, Claude Desktop) send a
bearer JWT that Google can't verify, so a wrapper restricted to named Google identities would
refuse them all. Authentication happens in one place: `api/auth.py`'s JWT check in
`quantcore-api`. A wrapper verifies nothing; it forwards the caller's `Authorization` header
unchanged (`mcp_gateway/rest_client.py`), so an unauthenticated call reaches the api and is
refused there with 401. keyproxy's invoker is only its runtime SA; quantui's is only the IAP
service agent. Checked read-only in both projects on 2026-10-05
([#297](https://github.com/JohnFunkCode/StockPortfolioManager/issues/297), detail in
[`security-review-297-plan.md`](../proposals/security-review-297-plan.md)).

Two consequences of being public: a wrapper's `mcp_health_check` and the api's `/api/health`
answer anyone, so they return identity and status only — no host OS, interpreter, internal URL,
path, or driver error text. And `rest_client` rejects a path segment that could reshape the
request (`..`, `?`, `#`, `%`, whitespace) before any connection is opened.

### Onboarding a wrapper by hand

1. Merge the PR that adds the `[[services]]` block, the module and the `WRAPPERS` entry.
   - The roll-out fails the new service's step with `::error title=<name> not created::`.
   - Every other service still rolls, so the existing wrappers end up on the new
     `quantcore-mcp` image, which holds the new module.
2. An owner then runs the same script under their **own** gcloud login. The preflight passes for
   an owner, so the script creates the service with its full inventory config and binds `allUsers`.
   The image is the digest an existing wrapper is serving. Test:

   ```bash
   export QUANTCORE_MCP_DIGEST="$(gcloud run revisions describe "$(gcloud run services describe quantcore-portfolio --project quantcore-test-20260606 --region us-central1 --format='value(status.latestReadyRevisionName)')" --project quantcore-test-20260606 --region us-central1 --format='value(status.imageDigest)' | cut -d@ -f2)"
   ```

   ```bash
   python3 scripts/cloudrun_services.py deploy <new-wrapper> --env test --registry us-central1-docker.pkg.dev/quantcore-test-20260606/quantcore --by-digest
   ```

   For prod, run the same two commands with `quantcore-prod-20260606` and `--env prod`, after
   prod-rollout has promoted the image. Prod is John's to apply.
3. The next roll-out takes the service over as an existing one. `cloudrun_services.py check --env
   test|prod` should then report it `ok`.

A new service that runs as a **new** runtime SA also needs `iam.serviceAccountUser` for the
deployer on that SA before its first roll-out. Otherwise the create fails with
`iam.serviceaccounts.actAs` denied, as keyproxy's did on 2026-07-18.
