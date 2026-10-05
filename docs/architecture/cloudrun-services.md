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
4. Optionally, add the remote to `.mcp.json` for AI clients, and update the tool count in
   `CLAUDE.md`.

Then:

- **Merge → test.** `deploy.yml`'s phase 2 finds the service missing and creates it.
- **`prod-rollout.yml` → prod.** The same create runs behind the `prod` environment's
  required-reviewer gate. Dispatching it is the owner's call.

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
| `run.services.setIamPolicy` (makes a newly created wrapper public) | ❌ **not granted** | ❌ **not granted** |

The other roles each workflow needs (Cloud Build, logging, the migrate Job's SA) are listed in the
workflow headers and in [`prod-promotion.md`](../operations/prod-promotion.md).

`run.developer` can create a service but cannot set its IAM policy. That is why the create path
never passes `--allow-unauthenticated`, which would fail. Instead it runs a separate
`add-iam-policy-binding` after the create.

**Before it creates anything, the script checks the permission.** It calls Resource Manager's
`projects.testIamPermissions` for `run.services.create` and `run.services.setIamPolicy`, as the
deployer, with `gcloud auth print-access-token` (the token goes only into the request header).
If either is missing, nothing is created: the step fails with `::error title=<name> not
created::`, naming the missing permission and printing the bind command for an owner to run after
creating the service by hand. Until the grant below exists, that is what onboarding a wrapper
does. If the check itself fails (no token, API unreachable), the step fails closed and creates
nothing. Without the check, run.developer would create a wrapper it could not make public, and
leave a service behind that answers every client with 403.

**Existing services are unaffected.** The update path makes no permission check and only *reads*
IAM.

The narrow grant, a custom role holding only the two Cloud Run IAM-policy permissions (to be
applied by a project owner, once per project):

```bash
gcloud iam roles create runServiceIamSetter --project quantcore-test-20260606 --title "Cloud Run service IAM setter" --permissions run.services.getIamPolicy,run.services.setIamPolicy
```

```bash
gcloud projects add-iam-policy-binding quantcore-test-20260606 --member serviceAccount:quantcore-deployer@quantcore-test-20260606.iam.gserviceaccount.com --role projects/quantcore-test-20260606/roles/runServiceIamSetter --condition None
```

```bash
gcloud iam roles create runServiceIamSetter --project quantcore-prod-20260606 --title "Cloud Run service IAM setter" --permissions run.services.getIamPolicy,run.services.setIamPolicy
```

```bash
gcloud projects add-iam-policy-binding quantcore-prod-20260606 --member serviceAccount:quantcore-deployer@quantcore-prod-20260606.iam.gserviceaccount.com --role projects/quantcore-prod-20260606/roles/runServiceIamSetter --condition None
```

What this allows: the deployer can change the invoker policy of any service in the project,
including making the api or keyproxy public. That is the same power `roles/run.admin` would give,
without the rest of run.admin. Keyproxy's protection does not depend on the invoker policy alone,
because it also requires a user JWT. The roll-out code never changes IAM on an existing service.
If that trade-off is not acceptable, leave the grant out. Each new wrapper is then created and
made public by hand, using the command the refused step prints, and the next roll-out takes it
over as an existing service.

A new service that runs as a **new** runtime SA also needs `iam.serviceAccountUser` for the
deployer on that SA before its first roll-out. Otherwise the create fails with
`iam.serviceaccounts.actAs` denied, as keyproxy's did on 2026-07-18.
