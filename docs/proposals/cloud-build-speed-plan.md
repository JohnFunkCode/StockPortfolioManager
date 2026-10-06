# Cloud Build image step speed (issue #278)

## Context

Every merge to `main` runs `deploy.yml`, whose `deploy` job builds and pushes all six images with
`gcloud builds submit --config cloudbuild.yaml`. That step alone took 9–12 minutes and was the
largest single slice of the PR → test iteration loop.

Baseline, Cloud Build `9958a5d5` (2026-10-04); earlier runs were 8:35, 9:51, 9:39 and 12:16:

| Phase | Time |
|---|---|
| Total (excl. ~59 s queued) | **9:30** |
| BUILD | 5:19: api 2:46, mcp 0:42, news 0:30, report 0:10, ui 0:48, keyproxy 0:22, run **one after another** |
| PUSH | 4:07 |

In the same `deploy.yml` run (37209801132) the whole deploy job took 15:54, and build+push was
10:36 of it.

## Root cause

1. **Sequential steps.** No step had `waitFor`, so Cloud Build ran the six builds in order.
2. **No layer cache.** Each build worker starts empty, so torch, the FinBERT bake and every pip
   install were rebuilt from scratch on every merge. Each rebuilt layer got a new digest, so the
   push re-uploaded the ~GB of torch layers as well. That is why PUSH was as long as BUILD.

## Fix

> **Superseded.** The inline cache below alternated warm and cold on `main`. It was replaced by a
> buildx registry cache with `mode=max`; see
> [Follow-up: registry cache, `mode=max`](#follow-up-registry-cache-modemax). This section records
> what #278 shipped.

In `cloudbuild.yaml`, on every build step:

- `env: ['DOCKER_BUILDKIT=1']`, `--cache-from <the image's own repo>:${_CACHE_TAG}` and
  `--build-arg BUILDKIT_INLINE_CACHE=1`. The pushed image carries its own cache metadata, so the
  next build pulls only the manifest and reuses every unchanged layer. The push then skips those
  layers too, because their digests already exist in Artifact Registry.
- `waitFor: ['-']`, so all six builds run in parallel.
- `_CACHE_TAG` defaults to `latest`. It is a substitution only so a trial can warm from a scratch
  tag.

This works on the stock `gcr.io/cloud-builders/docker` builder. Neither buildx nor Kaniko was
needed.

### `DEPS_EPOCH`: a cached pip layer must still move

> **Superseded by #218** ([`pin-deps-plan.md`](pin-deps-plan.md)): every install now uses a
> hash-pinned `.lock`, so the pip layer is keyed on the lock itself and `DEPS_EPOCH` was removed.
> The section below is the historical record.

The requirements files use `>=` floors (exact pins are #218). A cached `pip install` layer would
freeze whatever versions it first resolved, indefinitely. Each Python Dockerfile therefore declares
`ARG DEPS_EPOCH=` just before its install. `deploy.yml` passes the ISO week
(`date -u +%G-W%V`), so the first build of each week re-resolves, and staleness is bounded to 7
days. `Dockerfile.ui` doesn't need it because `npm ci` follows the lockfile.

### Guard

`scripts/check_cloudbuild.py` (the `gate` job) now also fails a build step that lacks the BuildKit
env, the inline-cache arg, or a `--cache-from` of its own image at `${_CACHE_TAG}`. Dropping any of
them still builds a correct image, just a slow one, so nothing else would notice.

Prod promotion is unchanged. `prod-rollout.yml` copies the test image for each service at the
promoted SHA, by digest.

## Results

Measured on the test project with a copy of the config that tags no `:latest` (see Gotchas).

| Build | Total (excl. queue) | BUILD | PUSH | Per step |
|---|---|---|---|---|
| Baseline `9958a5d5` | 9:30 | 5:19 | 4:07 | sequential, see above |
| Cold `3d20eb3a`: parallel, no usable cache | 5:33 | 2:49 | 2:40 | api 2:27, mcp 0:58, news 0:21 (waited for api), report 0:58, ui 0:41, keyproxy 0:27 |
| Warm `aafc2948`: cache from the cold build | **1:43** | 1:15 | 0:23 | api 0:41, mcp 0:19, news 0:33 (after api), report 0:20, ui 0:18, keyproxy 0:16 |
| Warm `1d6bacda`: news parallel too, no source change | 0:49 | 0:35 | 0:10 | api 0:35, news 0:34, others ~0:21 |

The warm log confirms `importing cache manifest from …`. On the warm api build, apt, the venv, the
`requirements-ml.txt` install, `bake_finbert` and both `COPY --from=builder` layers were all
`CACHED`; only `COPY . .` re-ran.

At first news waited for api, so that a cold build could share api's identical builder stage. The
warm trials show news restores its own cache in ~30 s when it runs alongside api, so waiting only
serialized it. Every step is parallel now. A cold build installs torch twice concurrently. That
case is rare (first build after merge, then once a week via `DEPS_EPOCH`), and wasn't re-measured.

Queue time (~50 s) is outside the config's control.

## Gotchas

- **The first build after this merges is cold.** The `:latest` images in AR were pushed without
  inline cache metadata, so `--cache-from` finds nothing to reuse. Expect ~5.5 min once, then
  ~1–2 min after that. The first build of each ISO week re-runs the pip layers by design.
- **Never warm a trial from `:latest`, or tag one as it.** `prod-rollout.yml`'s default tag is
  `latest`, so a branch build that moved it would change what a default prod promotion picks up.
  The trials here used a scratch config with the `:latest` tags removed, plus `_TAG=pr278a/b/c` and
  `_CACHE_TAG=<previous scratch tag>`. Those three tags are left in the test repo; the AR cleanup
  policy ages them out.
- **The default `python` here is anaconda 3.11 without psycopg2.** `tests/__init__.py` imports it
  for the suite lock, so even `tests.test_check_cloudbuild` fails to import. Use
  `.venv/bin/python`.
- **The `1d6bacda` row is a best case.** It reused the same source as `aafc2948`, so `COPY . .`
  hit the cache too. A real merge lands between that row and the `aafc2948` row.
- **On `main` the cache alternated between warm and cold (resolved; see the next section).** These
  are the three merge builds of 2026-10-04, all in the same ISO week, with the same
  `python:3.12-slim` digest and nothing else pushing `:latest` between them:

  | Build | Commit | Cached api layers | Workflow step time |
  |---|---|---|---|
  | `667b7216` | 22d5eca | 0 | 6:17 |
  | `e9493d66` | ba7dca2 | 10 | 2:11 |
  | `5dd78831` | 77219a9 | 0 | 5:50 |

  The first guess here was the inline cache's `mode=min`. The logs disproved that. See below.

## Follow-up: registry cache, `mode=max`

### Cause, confirmed from the images

`gcr.io/cloud-builders/docker` runs Docker 20.10.24, whose embedded BuildKit is v0.8 (the
`docker` driver). Its inline cache **writes cache records only for the layers it actually
executed in that build**. Layers it restored from `--cache-from` are not re-exported. So:

1. A cold build runs every layer, and `:latest` describes all of them.
2. A warm build re-runs only `COPY . .`, so the new `:latest` describes **only** `COPY . .`.
3. The next merge changes the source, `COPY . .` misses, and nothing else is described, so every
   layer rebuilds. That is a cold build, and step 1 again.

Decoding the `moby.buildkit.cache.v0` config label of each `:latest` that main pushed shows how
many layers the cache metadata covered:

| Image | after cold `22d5eca` | after warm `ba7dca2` | after cold `77219a9` |
|---|---|---|---|
| api | 5 (layers 4–8) | 1 (layer 8) | 5 |
| news | 6 | 2 | 6 |
| mcp | 4 | 1 | 4 |

The #278 trials follow the same pattern: `pr278a` (cold) covered layers 4–8, and `pr278b` covered
layer 8 only. `pr278c` was warm after a warm build only because it had **no** source change.
`COPY . .` itself hit, and its digest is the same as `pr278b`'s.

Pushing `--target builder` as a second inline-cache image would hit the same bug, because a warm
build re-exports none of the builder's layers. Upgrading the builder's BuildKit was the only real
fix.

### Fix

- A `builder` step runs `docker buildx create --driver docker-container`. It runs a pinned BuildKit
  (`_BUILDKIT_IMAGE`, `moby/buildkit:v0.23.2` from `mirror.gcr.io`, to avoid Docker Hub pull
  limits).
- Each image builds with `buildx build --builder quantcore`, plus:
  - `--cache-from` and `--cache-to` at `type=registry,ref=<image>:buildcache-${_CACHE_TAG}`, with
    `mode=max,image-manifest=true,oci-mediatypes=true`. `mode=max` records every layer of every
    stage, including the ones it restored, so warm-after-warm stays warm.
    `image-manifest=true` stores the cache as an ordinary OCI image manifest, so AR accepts it in
    the same package as the image.
  - `--provenance=false`. Without it buildx wraps the image in an index with an attestation
    manifest, which is a different shape from what `prod-rollout.yml` copies by digest today.
  - `--push`. The image is pushed from inside the builder, so the `images:` block is gone.
- `_CACHE_TAG` now defaults to `main` and names the cache tag (`buildcache-main`), never an image
  tag.
- A final `tag-latest` step (`cloud-sdk:slim`) waits for all six builds, then runs
  `gcloud artifacts docker tags add <img>:${_TAG} <img>:${_LATEST_TAG}`. That moves the rolling tag
  to the **same digest**. As before, `:latest` moves only once every image has built.
  `_LATEST_TAG` is a substitution so that a trial can move a scratch tag instead.
- Six `tags add` calls are not one transaction, and `prod-rollout.yml` promotes `:latest` by
  default, so a run that moved three tags and then failed would leave a mixed set for the next
  default promotion (PR #302 review). The step therefore records each `:latest` digest first,
  retries every move three times (re-pointing a tag is idempotent), and on a failure that survives
  the retries re-points the tags it already moved back at their recorded digests (or deletes a tag
  that did not exist before), then fails the build. What remains is the few seconds between the
  first and last move, during which a reader could see a mixed set; prod promotions are dispatched
  by hand. The old `images:` push was not atomic across images either; it just never said so.
  Checked against a stub `gcloud` that fails one move: the four earlier tags were restored by digest,
  the one with no prior tag was deleted, and the step exited 1.

The guard in `scripts/check_cloudbuild.py` now requires all of that on every build step, and also
checks three more things:

- no plain `docker build` step;
- no `images:` block;
- the `tag-latest` step tags exactly the built images and waits for every build.

### Results (test project, scratch tags `t296-*`)

| Build | BUILD phase | Per step |
|---|---|---|
| Seed, cold `382855f3` (`_TAG=t296-a`) | 5:48 | builder 0:08, api 5:11, news 5:13, mcp 1:30, report 1:30, ui 0:59, keyproxy 0:39, tag-latest 0:27 |
| Warm #1 `f35bfbdd` (`t296-b`), source changed | **1:18** | builder 0:07, api 0:36, news 0:36, mcp 0:22, report 0:21, ui 0:44, keyproxy 0:14, tag-latest 0:28 |
| Warm #2 `b7587501` (`t296-c`), source changed again | **1:19** | builder 0:07, api 0:36, news 0:37, mcp 0:20, report 0:20, ui 0:43, keyproxy 0:13, tag-latest 0:29 |

Both warm builds had a different `t296_marker.txt` (root and `frontend/`), so `COPY . .`
missed in every image, as it does on a real merge. In both warm builds every other layer was
`CACHED` in all six images. In warm #2, which is the warm-after-warm case that used to go cold,
api had 10 `CACHED` steps, including apt, the venv, the torch install and the FinBERT bake. Only
`COPY . .` re-ran. `:t296-latest` and `:t296-c` share one digest, and that digest is a plain
`application/vnd.docker.distribution.manifest.v2+json`, not an index.

The cold seed is slower than #278's cold build (`3d20eb3a`, 5:33 including push) because api and
news each install torch at the same time, and both now also export a full `mode=max` cache. That
happens only on the first build of each ISO week.

### Gotchas (follow-up)

- **The first merge build after this lands is cold.** No `buildcache-main` exists yet. Expect about
  6 minutes once. After that, every merge is warm until the weekly `DEPS_EPOCH` bump.
- **Each merge now adds two versions per image package**: the image, and a new cache manifest that
  leaves the previous one untagged. Under the AR cleanup policy (keep the 15 newest, delete the
  rest after 30 days), only about the 7 newest *images* past 30 days survive, not 15. Nothing
  within 30 days is affected. If rollbacks further back than that matter, raise `keepCount` in
  `scripts/ar_cleanup_policy.json`. Keep the policy version-count based: the "never delete
  untagged" rule still holds.
- **About 25 s of `tag-latest` is pulling `cloud-sdk:slim`**, which is now about a third of a warm
  build. A prefetch step for that image with `waitFor: ['-']` could hide the pull. Not done here.
- **The trials used `_TAG=t296-a/b/c`, `_CACHE_TAG=t296` and `_LATEST_TAG=t296-latest`.** That
  leaves `:latest` and `buildcache-main` untouched. Those tags remain in the test repo; the AR
  cleanup policy ages them out.

## Follow-up (not in this PR)

The `deploy` job's `gcloud run deploy` steps also run one after another (api 1:57, wrappers 1:39,
…), about 5 minutes in total. Now that the build is ~2 minutes, that is the larger share of the
loop. Done in #296: [`parallel-rollout-plan.md`](parallel-rollout-plan.md).

## Checkpoint log

| Step | Commit | Result | Gotcha |
|---|---|---|---|
| Baseline | — | Cloud Build 9:30 (BUILD 5:19 / PUSH 4:07); deploy job 15:54 | — |
| Cache + parallel + DEPS_EPOCH + checker | [#295](https://github.com/JohnFunkCode/StockPortfolioManager/pull/295) | Cold 5:33, warm 1:43; checker 7/7 tests | Current `:latest` has no inline cache, so the first build is cold |
| news fully parallel | [#295](https://github.com/JohnFunkCode/StockPortfolioManager/pull/295) | Warm, no source change: 0:49 | Cold double-torch install not re-measured |
| buildx registry cache, `mode=max` + `tag-latest` | [#302](https://github.com/JohnFunkCode/StockPortfolioManager/pull/302) | Cold 5:48; warm 1:18, then warm-after-warm 1:19, source changed both times | The inline cache's BuildKit v0.8 re-exports only executed layers; `mode=min` was the wrong guess |
