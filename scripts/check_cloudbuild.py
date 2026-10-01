"""Fail fast if cloudbuild.yaml is not a valid Cloud Build config.

`gcloud builds submit` is the first thing that reads this file, and in
deploy.yml that happens *after* merge — a mis-indented line in `images:` once
reached `main` and blocked the test roll-out. This runs in the PR gate instead.

Checks structure only (the shape gcloud rejects), and that every image a Job or
service is rolled out from is both built by a step and listed in `images:`.
"""
import re
import sys
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parent.parent / "cloudbuild.yaml"
IMAGE_RE = re.compile(r"/(quantcore-[a-z]+):\$\{_TAG\}$")


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
        for a in args:
            m = IMAGE_RE.search(a)
            if m:
                built.add(m.group(1))
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
