"""Fail fast if cloudbuild.yaml is not a valid Cloud Build config.

`gcloud builds submit` is the first thing that reads this file, and in
deploy.yml that happens *after* merge — a mis-indented line in `images:` once
reached `main` and blocked the test roll-out. This runs in the PR gate instead.

Checks structure only (the shape gcloud rejects), and that every image a step
builds also gets the rolling tag in the `tag-latest` step, and the other way
round. The steps push their own images (`buildx --push`), so an `images:` block
must not come back: Cloud Build would look for the images in a daemon that never
held them.

It also checks each build step keeps its layer cache wired up (#278, then the
#296 follow-up): `buildx build` on the shared docker-container builder,
`--cache-from` and `--cache-to` its own image's `:buildcache-${_CACHE_TAG}` with
`mode=max`, and `--provenance=false`. Dropping the cache wiring still builds a
correct image, just a cold one, and the only symptom would be a merge that takes
ten minutes again. Dropping `--provenance=false` turns each image into an index
with an attestation, which is not what prod-rollout copies by digest.
"""
import re
import sys
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parent.parent / "cloudbuild.yaml"
IMAGE_RE = re.compile(r"/(quantcore-[a-z]+):\$\{_TAG\}$")
LATEST_RE = re.compile(r"/(quantcore-[a-z]+):\$\{_TAG\}\s+\S+/\1:\$\{_LATEST_TAG\}")
BUILDER = "quantcore"


def _flag_values(args, flag) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == flag]


def _cache_problems(args, images) -> list[str]:
    problems = []
    if BUILDER not in _flag_values(args, "--builder"):
        problems.append(f"must build on --builder {BUILDER} (registry cache needs "
                        "the docker-container driver)")
    if "--provenance=false" not in args:
        problems.append("missing --provenance=false (prod-rollout copies a plain manifest)")
    if "--push" not in args:
        problems.append("missing --push (there is no `images:` block to push it)")
    sources = _flag_values(args, "--cache-from")
    sinks = _flag_values(args, "--cache-to")
    for img in sorted(images):
        ref = f"ref=${{_REGION}}-docker.pkg.dev/${{_PROJECT}}/${{_REPO}}/{img}:buildcache-${{_CACHE_TAG}}"
        if not any(s == f"type=registry,{ref}" for s in sources):
            problems.append(f"missing --cache-from type=registry,{ref}")
        if not any(s.startswith(f"type=registry,{ref},") and "mode=max" in s.split(",")
                   for s in sinks):
            problems.append(f"missing --cache-to type=registry,{ref},mode=max")
    return problems


def check(doc) -> list[str]:
    problems = []
    if not isinstance(doc, dict):
        return ["top level is not a mapping"]
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["`steps` is missing or empty"]
    built, build_ids, tagged, tag_step = set(), set(), set(), None
    for i, step in enumerate(steps):
        name = step.get("id", f"#{i}") if isinstance(step, dict) else f"#{i}"
        if not isinstance(step, dict):
            problems.append(f"step {name}: not a mapping")
            continue
        args = step.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            problems.append(f"step {name}: `args` must be a list of strings")
            continue
        if args[:1] == ["build"]:
            problems.append(f"step {name}: `docker build` writes only inline cache, "
                            "which alternates warm/cold on main; use `buildx build`")
        step_images = {m.group(1) for a in args if (m := IMAGE_RE.search(a))}
        if args[:2] == ["buildx", "build"]:
            built |= step_images
            build_ids.add(name)
            problems += [f"step {name}: {p}" for p in _cache_problems(args, step_images)]
        if name == "tag-latest":
            tag_step = step
            tagged = {m.group(1) for a in args for m in LATEST_RE.finditer(a)}
    if doc.get("images"):
        problems.append("`images:` must be empty: the build steps push with "
                        "--push, so the images are never in the local daemon")
    if tag_step is None:
        problems.append("no `tag-latest` step: nothing moves ${_LATEST_TAG}")
        return problems
    for img in sorted(built - tagged):
        problems.append(f"{img} is built but `tag-latest` never tags it")
    for img in sorted(tagged - built):
        problems.append(f"{img} is tagged by `tag-latest` but no step builds it")
    waits = set(tag_step.get("waitFor") or [])
    for sid in sorted(build_ids - waits):
        problems.append(f"`tag-latest` must waitFor {sid}: the rolling tag moves "
                        "only once every image has built")
    return problems


def main() -> int:
    problems = check(yaml.safe_load(PATH.read_text()))
    for p in problems:
        print(f"cloudbuild.yaml: {p}")
    if not problems:
        print("cloudbuild.yaml OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
