# Plan: deploy any ref to test (issue #120)

## Context

Before #120, test only ever ran main: `deploy.yml` deployed on a push to main, and its
`workflow_dispatch` re-ran that path for main's HEAD. To try a branch on test, someone had to
merge it, or open a revert PR to park main at an older state (PR #119 did exactly that). Both
use main as a staging area.

Goal: build and roll out any branch, tag or SHA to **test**, through the same gates and the same
roll-out as a merge, without touching main or prod.

## Design

- **Input.** `workflow_dispatch.inputs.ref` (required, default `main`).
- **Run from main only.** The preflight job fails a dispatch whose `GITHUB_REF` isn't
  `refs/heads/main`. So the workflow file, `scripts/`, and the service inventory that drive the
  deploy are always main's; the dispatched ref only supplies the code that is tested and built.
  It also keeps the concurrency group (`deploy-${{ github.ref }}`) equal to main's, so a dispatch
  and a merge can never roll out at the same time.
- **Gates run on the ref.** `gate`, `lean-import`, `frontend-gate` and `secret-scan` check out
  `ref: ${{ inputs.ref }}`, which is empty (the event's commit) on push and PR runs. `gate`
  outputs the SHA it tested (`git rev-parse HEAD`), and `deploy` builds exactly that SHA. A
  branch that moves while the run is in flight can't slip an untested commit into the build.
- **deploy job.** Two checkouts: main at the root (the machinery) and the gated SHA under `ref/`
  with full history. Then:
  1. a step-summary line, `Deploying to TEST: <ref> @ <sha>`, plus who dispatched it;
  2. `scripts/check_deploy_ref.py --ref-dir ref --base origin/main`;
  3. `gcloud builds submit ref --config ref/cloudbuild.yaml` with `_TAG=<sha7>`;
  4. the migrate Job and the roll-out, both on `<sha7>`.
- **Tags.** A push uses `_LATEST_TAG=latest`, `_CACHE_TAG=main` (unchanged). A dispatch uses
  `_LATEST_TAG=dispatch-latest`, `_CACHE_TAG=dispatch`.
- **`check_deploy_ref.py`** refuses (exit 1, `::error`) a ref that:
  - adds or modifies a file under `db/migrations` or `db/baseline` relative to `origin/main`, or
  - has a `cloudbuild.yaml` that doesn't build every image the roll-out deploys (the test
    inventory's images plus the migrate, report and news Jobs).
- **Tests:** `tests/test_check_deploy_ref.py` covers the script against throwaway git repos and
  the workflow wiring (input, gate checkouts, preflight refusal, SHA hand-off, tags, summary, and
  that `inputs.ref` never appears inside a `run:`).

## Gotchas (record of what would mislead)

1. **`:latest` is prod's default.** `prod-rollout.yml` promotes `image_tag=latest` unless told
   otherwise, and `cloudbuild.yaml`'s `tag-latest` step moves `:${_LATEST_TAG}` to every build.
   Dispatching a branch with the push defaults would have made an unmerged build the next prod
   promotion's default. Hence the scratch `dispatch-latest`/`dispatch` tags, and a test that pins
   them. The cache tag is scratch too, so a branch build doesn't overwrite main's warm layer cache.
2. **Dispatching "from" a branch runs that branch's workflow.** GitHub takes the workflow file
   from the ref the dispatch is run on, not from an input. A run from a branch would therefore
   use the branch's deploy scripts against test, and sit in a different concurrency group from
   main, racing merges. The preflight refusal closes both.
3. **One pending run per concurrency group.** GitHub keeps at most one *pending* run per group;
   a newer queued run cancels the older queued one (`cancel-in-progress: false` protects only the
   running one). A dispatch queued behind a merge can be replaced by the next merge, and vice
   versa. Re-dispatch if yours vanished.
4. **A ref behind main is safe for the schema.** Flyway's default `ignoreMigrationPatterns`
   includes `*:future`, so an older migrate image doesn't fail on migrations it doesn't know, and
   the app's `verify` mode never fails on `EXTRA` objects. Code that predates a migration may
   still not *work* against the newer schema, but nothing breaks at startup.
5. **A ref ahead of main on migrations is refused, with no override.** Its migrate image would
   apply the migration to the shared test database, and the next push to main would then fail
   Flyway validation ("applied migration not resolved locally"). Schema changes are forward-fix
   only, so there is no clean way back. An override flag is **deliberately deferred**: try a
   migration on a local database (`docs/local-unit-test-db.md`) or merge it first.
6. **Old refs lack images.** A ref from before `quantcore-migrate` (#200) or keyproxy would build
   fine and then fail half-way through the roll-out. The image check fails it before the build.
   An old ref without a `.gcloudignore` would also upload its `.git` directory; harmless, slower.
7. **`inputs.ref` is free text.** Interpolating `${{ inputs.ref }}` into a `run:` block is a shell
   injection. It reaches shells only through `env:` (`DEPLOY_REF`), and through `with: ref:` on
   checkouts. A test fails if `${{ inputs.` ever appears in a `run:`.
8. **Two-dot diff, not three.** `git diff origin/main HEAD` compares trees, so a migration main
   gained after the ref was cut shows as `D` (ignored), and only `A`/`M` are the ref's own.
9. **Test differs from main after a dispatch** until the next push to main redeploys main. The
   step summary is the attribution; this doesn't replace a revert PR when main itself must be
   parked.

## Checkpoint log

| Step | Result |
|---|---|
| `check_deploy_ref.py` + `deploy.yml` wiring | Done. `test_check_deploy_ref`, `test_ci_parallel`, `test_check_cloudbuild` pass (34 tests); `check_cloudbuild.py` OK. |
| Docs | CLAUDE.md QuantUI deploy bullet, readme "Trying a branch on test before merging", prod-promotion.md (`:latest` is main only), quantui.md. |
| First real dispatch | Pending: after merge, dispatch a branch from main and confirm the summary, the tags (`:dispatch-latest`, `:latest` unmoved) and the roll-out. |
