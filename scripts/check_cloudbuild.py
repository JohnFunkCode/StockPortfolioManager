"""Fail fast if cloudbuild.yaml is not a valid Cloud Build config.

`gcloud builds submit` is the first thing that reads this file, and in
deploy.yml that happens *after* merge — a mis-indented line in `images:` once
reached `main` and blocked the test roll-out. This runs in the PR gate instead.

Checks structure only (the shape gcloud rejects), and that every image a Job or
service is rolled out from is both built by a step and listed in `images:`.

It also checks each build step keeps its layer cache wired up (#278): BuildKit
on, `--cache-from` its own image at ${_CACHE_TAG}, and inline cache metadata
written. Dropping any one of them still builds a correct image, just a slow
one, and the only symptom would be a merge that takes ten minutes again.
"""
import re
import sys
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parent.parent / "cloudbuild.yaml"
IMAGE_RE = re.compile(r"/(quantcore-[a-z]+):\$\{_TAG\}$")


def _cache_problems(step, args, images) -> list[str]:
    problems = []
    if "DOCKER_BUILDKIT=1" not in (step.get("env") or []):
        problems.append("env must set DOCKER_BUILDKIT=1 (layer cache, #278)")
    if "BUILDKIT_INLINE_CACHE=1" not in args:
        problems.append("missing --build-arg BUILDKIT_INLINE_CACHE=1 (#278)")
    sources = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--cache-from"]
    for img in sorted(images):
        if not any(s.endswith(f"/{img}:${{_CACHE_TAG}}") for s in sources):
            problems.append(f"missing --cache-from {img}:${{_CACHE_TAG}} (#278)")
    return problems


def check(doc) -> list[str]:
    problems = []
    if not isinstance(doc, dict):
        return ["top level is not a mapping"]
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["`steps` is missing or empty"]
    built = set()
    for i, step in enumerate(steps):
        name = step.get("id", f"#{i}") if isinstance(step, dict) else f"#{i}"
        if not isinstance(step, dict):
            problems.append(f"step {name}: not a mapping")
            continue
        args = step.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            problems.append(f"step {name}: `args` must be a list of strings")
            continue
        step_images = {m.group(1) for a in args if (m := IMAGE_RE.search(a))}
        built |= step_images
        if args[:1] == ["build"]:
            problems += [f"step {name}: {p}" for p in _cache_problems(step, args, step_images)]
    images = doc.get("images", [])
    if not isinstance(images, list) or not all(isinstance(a, str) for a in images):
        problems.append("`images` must be a list of strings")
        return problems
    listed = {m.group(1) for a in images if (m := IMAGE_RE.search(a))}
    for img in sorted(built - listed):
        problems.append(f"{img} is built but missing from `images:`")
    for img in sorted(listed - built):
        problems.append(f"{img} is in `images:` but no step builds it")
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
