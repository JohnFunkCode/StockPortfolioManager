#!/usr/bin/env python3
"""Refuse a ref that deploy.yml cannot safely roll out to TEST (issue #120).

deploy.yml deploys main on every push, and since #120 any branch, tag or SHA on a
manual dispatch. The workflow's own machinery (this script, the inventory, the roll-out
scripts) always comes from main; only the build comes from the dispatched ref, which the
deploy job checks out into a subdirectory:

    check_deploy_ref.py --ref-dir ref --base origin/main

Two checks, each a ::error line and exit 1:

1. **No migration main doesn't have.** The ref's quantcore-migrate image would apply it
   to the shared test database, and the next push to main would then fail Flyway's
   validate ("applied migration not resolved locally"), with no way back: schema changes
   are forward-fix only. Every file under db/migrations and db/baseline that the ref adds
   or changes relative to main is refused. A ref BEHIND main is fine: Flyway ignores
   applied migrations newer than its own (ignoreMigrationPatterns *:future), and the
   app's verify mode never fails on EXTRA objects.
2. **Every image the roll-out deploys is built by the ref's cloudbuild.yaml.** A ref
   that predates an image (quantcore-migrate before #200, keyproxy before BYOK) would
   build, then fail half-way through the roll-out. This fails it before the build.

Stdlib only, like cloudrun_services.py, so the runner's stock python3 runs it.
Plan and gotchas: docs/proposals/deploy-ref-to-test-plan.md.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cloudrun_services  # noqa: E402

# Images deployed by deploy.yml outside the inventory: the migrate step and the two Jobs.
JOB_IMAGES = {"quantcore-migrate", "quantcore-report", "quantcore-news"}
MIGRATION_DIRS = ("db/migrations", "db/baseline")
BUILT_RE = re.compile(r"/(quantcore-[a-z]+):\$\{_TAG\}")


def required_images(env_name: str = "test") -> set[str]:
    return {s["image"] for s in cloudrun_services.load(env_name)} | JOB_IMAGES


def built_images(ref_dir: Path) -> set[str]:
    path = ref_dir / "cloudbuild.yaml"
    if not path.is_file():
        return set()
    return set(BUILT_RE.findall(path.read_text()))


def unmerged_migrations(ref_dir: Path, base: str) -> list[str]:
    """Migration files the ref adds or changes relative to `base` ("A path" / "M path")."""
    r = subprocess.run(
        ["git", "-C", str(ref_dir), "diff", "--name-status", "--no-renames", base, "HEAD",
         "--", *MIGRATION_DIRS],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git diff against {base} failed: {r.stderr.strip()}")
    out = []
    for line in r.stdout.splitlines():
        status, _, path = line.partition("\t")
        if status in ("A", "M"):
            out.append(f"{status} {path}")
    return out


def check(ref_dir: Path, base: str) -> list[str]:
    problems = []
    for entry in unmerged_migrations(ref_dir, base):
        problems.append(
            f"migration not on {base}: {entry}. Deploying it would apply it to the shared "
            f"test database ahead of main. Merge it first, or try it on a local database.")
    missing = required_images() - built_images(ref_dir)
    for img in sorted(missing):
        problems.append(
            f"the ref's cloudbuild.yaml does not build {img}, which the roll-out deploys. "
            f"The ref is older than the deploy machinery; pick a newer one.")
    return problems


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ref-dir", type=Path, required=True,
                   help="checkout of the ref to deploy (with history back to --base)")
    p.add_argument("--base", default="origin/main")
    a = p.parse_args(argv)
    try:
        problems = check(a.ref_dir, a.base)
    except (RuntimeError, cloudrun_services.ManifestError) as e:
        print(f"::error title=deploy ref check failed::{e}")
        return 1
    for msg in problems:
        print(f"::error title=ref cannot deploy to test::{msg}")
    if not problems:
        print(f"ref ok: no migrations beyond {a.base}; cloudbuild.yaml builds every image.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
