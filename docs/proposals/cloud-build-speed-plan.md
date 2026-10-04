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
- **On `main` the cache alternates between warm and cold (open).** These are the three
  merge builds of 2026-10-04, all in the same ISO week, with the same `python:3.12-slim` digest and
  nothing else pushing `:latest` between them:

  | Build | Commit | Cached api layers | Workflow step time |
  |---|---|---|---|
  | `667b7216` | 22d5eca | 0 | 6:17 |
  | `e9493d66` | ba7dca2 | 10 | 2:11 |
  | `5dd78831` | 77219a9 | 0 | 5:50 |

  The cold builds rebuild even the first builder layer (apt), so the inline-cache metadata in
  `:latest` is missing the builder stage, and missing it only *after* a warm build. The likely
  cause, unconfirmed, is that the inline cache is `mode=min`: it records only the layers it is
  re-exporting. The `pr278c` trial does not fit this pattern, because it was warm after a warm
  build. That trial had no source change, though, so its final stage was fully cached as well.
  Candidate fix: push the builder stage as its own cache image, `--target builder`, or use buildx
  registry cache with `mode=max`.

## Follow-up (not in this PR)

The `deploy` job's `gcloud run deploy` steps also run one after another (api 1:57, wrappers 1:39,
…), about 5 minutes in total. Now that the build is ~2 minutes, that is the larger share of the
loop. Done in #296: [`parallel-rollout-plan.md`](parallel-rollout-plan.md).

## Checkpoint log

| Step | Commit | Result | Gotcha |
|---|---|---|---|
| Baseline | — | Cloud Build 9:30 (BUILD 5:19 / PUSH 4:07); deploy job 15:54 | — |
| Cache + parallel + DEPS_EPOCH + checker | _this PR_ | Cold 5:33, warm 1:43; checker 7/7 tests | Current `:latest` has no inline cache, so the first build is cold |
| news fully parallel | _this PR_ | Warm, no source change: 0:49 | Cold double-torch install not re-measured |
