#!/usr/bin/env python3
"""Resolve and check what a prod roll-out would ship, before anyone approves it (#346).

prod-rollout.yml's `preflight` job runs this with TEST-project credentials and no
`environment:`, so it finishes in seconds and the approval prompt appears right after it.
The approver reads its job summary and approves exactly what it lists:

1. **Resolve and bind.** The digest of every promoted image at `<test AR>/<img>:<tag>`.
   quantcore-keyproxy and quantcore-news may be absent (tags that predate them); every
   other image is required. The digests become job outputs, and the prod job copies
   `<img>@<digest>`, never the tag again, so a tag that moves after approval can't
   change what ships.
2. **Tag -> commit.** cloudbuild.yaml tags every build `:<7-hex SHA>`; the SHA tag that
   shares quantcore-api's digest names the commit. Not quantcore-migrate's: that image
   holds only db/, so many commits share one migrate digest. Several SHA tags can share
   the api digest too (a docs-only rebuild); identical api images mean identical
   migrations, so any of them is right, and the newest one git knows is chosen.
3. **Refused migrations.** The migrations added or changed since the last promoted
   commit (the `promoted-sha` artifact of the latest successful prod-rollout run) go
   through the migrate image's own scanner (db/migrate-entrypoint.sh `check_pending`).
   A refusal fails the preflight, unless --allow-refused-migrations: the expected case
   after `./scripts/flyway.sh --prod migrate` applied it by hand. With no artifact (the
   first run, or one past retention) the check is skipped with a warning; the migrate
   Job still refuses after approval.

Stdlib only, like check_deploy_ref.py, so the runner's stock python3 runs it.
Plan and gotchas: docs/proposals/single-approval-rollout-plan.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "db" / "migrate-entrypoint.sh"

# The order and set of prod-rollout.yml's promote loop; tests/test_prod_preflight.py checks.
IMAGES = ("quantcore-api", "quantcore-mcp", "quantcore-report", "quantcore-ui",
          "quantcore-keyproxy", "quantcore-news", "quantcore-migrate")
OPTIONAL = {"quantcore-keyproxy", "quantcore-news"}
SHA_IMAGE = "quantcore-api"
SHA_RE = re.compile(r"^[0-9a-f]{7}$")
ARTIFACT = "promoted-sha"
ARTIFACT_FILE = "promoted-sha.txt"


class PreflightError(Exception):
    pass


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """The one subprocess seam; tests patch it."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def output_key(img: str) -> str:
    """quantcore-keyproxy -> keyproxy_digest (a job output name)."""
    return img.removeprefix("quantcore-").replace("-", "_") + "_digest"


def resolve_digests(registry: str, tag: str) -> dict[str, str]:
    """{image: digest}, "" for an absent optional image. Raises on a missing required one."""
    out, missing = {}, []
    for img in IMAGES:
        r = _run(["gcloud", "artifacts", "docker", "images", "describe",
                  f"{registry}/{img}:{tag}", "--format=value(image_summary.digest)"])
        digest = r.stdout.strip() if r.returncode == 0 else ""
        if not digest and img not in OPTIONAL:
            missing.append(img)
        out[img] = digest
    if missing:
        raise PreflightError(f"{', '.join(missing)} not found at :{tag} in {registry}")
    return out


def sha_tags(registry: str, digest: str) -> list[str]:
    """The 7-hex tags on `digest` in quantcore-api's package."""
    r = _run(["gcloud", "artifacts", "docker", "tags", "list", f"{registry}/{SHA_IMAGE}",
              f"--filter=version~{digest}", "--format=json"])
    if r.returncode != 0:
        raise PreflightError(f"could not list {SHA_IMAGE} tags: {r.stderr.strip()}")
    tags = [t["tag"].rsplit("/", 1)[-1] for t in json.loads(r.stdout or "[]")]
    return [t for t in tags if SHA_RE.match(t)]


def _commit_time(sha: str, repo: Path) -> int | None:
    r = _run(["git", "log", "-1", "--format=%ct", f"{sha}^{{commit}}"], cwd=repo)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def pick_sha(tags: list[str], repo: Path) -> str | None:
    """The newest tag git resolves to a commit; else any tag; else None."""
    known = [(t, ts) for t in tags if (ts := _commit_time(t, repo)) is not None]
    if known:
        return max(known, key=lambda kv: kv[1])[0]
    return sorted(tags)[0] if tags else None


def previous_sha(limit: int = 20) -> str | None:
    """The SHA the latest successful prod-rollout run recorded, or None."""
    r = _run(["gh", "run", "list", "--workflow", "prod-rollout.yml", "--status", "success",
              "--branch", "main", "--limit", str(limit), "--json", "databaseId"])
    if r.returncode != 0:
        print(f"::warning title=gh run list failed::{r.stderr.strip()}")
        return None
    for run in json.loads(r.stdout or "[]"):
        with tempfile.TemporaryDirectory() as d:
            got = _run(["gh", "run", "download", str(run["databaseId"]), "-n", ARTIFACT, "-D", d])
            path = Path(d) / ARTIFACT_FILE
            if got.returncode == 0 and path.is_file():
                sha = path.read_text().strip()
                if re.fullmatch(r"[0-9a-f]{7,40}", sha):
                    return sha
    return None


def changed_migrations(prev: str, sha: str, repo: Path) -> list[str] | None:
    """db/migrations files added or modified in prev..sha; None if git can't diff them."""
    r = _run(["git", "diff", "--name-only", "--no-renames", "--diff-filter=AM",
              prev, sha, "--", "db/migrations"], cwd=repo)
    if r.returncode != 0:
        return None
    return [p for p in r.stdout.splitlines() if p.strip()]


def scan_migrations(paths: list[str], sha: str, repo: Path) -> tuple[list[str], int]:
    """Run the migrate image's scanner on each file as of `sha`. (lines, check_pending rc)."""
    with tempfile.TemporaryDirectory() as d:
        files = []
        for p in paths:
            r = _run(["git", "show", f"{sha}:{p}"], cwd=repo)
            if r.returncode != 0:
                raise PreflightError(f"git show {sha}:{p} failed: {r.stderr.strip()}")
            dest = Path(d) / Path(p).name
            dest.write_text(r.stdout)
            files.append(str(dest))
        r = _run(["bash", "-c", f'source "{ENTRYPOINT}"; check_pending "$@"', "_", *files])
    return [l for l in (r.stdout + r.stderr).splitlines() if l.strip()], r.returncode


def _write(path: str | None, text: str) -> None:
    if path:
        with open(path, "a") as f:
            f.write(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", required=True, help="tag in the test AR (e.g. latest, or a 7-char SHA)")
    ap.add_argument("--registry", default="{}-docker.pkg.dev/{}/{}".format(
        os.environ.get("REGION", "us-central1"),
        os.environ.get("TEST_PROJECT", "quantcore-test-20260606"),
        os.environ.get("AR_REPO", "quantcore")))
    ap.add_argument("--previous-sha", help="skip the artifact lookup (local runs, tests)")
    ap.add_argument("--allow-refused-migrations", action="store_true",
                    help="warn, don't fail, on a refused migration (applied by hand already)")
    ap.add_argument("--repo", type=Path, default=ROOT)
    a = ap.parse_args(argv)

    try:
        digests = resolve_digests(a.registry, a.tag)
        if SHA_RE.match(a.tag):
            sha = a.tag
        else:
            sha = pick_sha(sha_tags(a.registry, digests[SHA_IMAGE]), a.repo)
            if not sha:
                raise PreflightError(f"no 7-hex commit tag shares {SHA_IMAGE}:{a.tag}'s digest")
    except PreflightError as e:
        print(f"::error title=prod preflight::{e}")
        return 1

    for img in IMAGES:
        print(f"{img}: {digests[img] or '(absent, skipped)'}")
    print(f"commit: {sha}")

    rc, migration_note = 0, ""
    prev = a.previous_sha or previous_sha()
    if not prev:
        migration_note = "not checked: no `promoted-sha` artifact (first run, or past retention)"
        print(f"::warning title=migrations not pre-checked::{migration_note}; "
              "the migrate Job still refuses a contract migration after approval")
    else:
        paths = changed_migrations(prev, sha, a.repo)
        if paths is None:
            migration_note = f"not checked: git can't diff {prev}..{sha}"
            print(f"::warning title=migrations not pre-checked::{migration_note}")
        elif not paths:
            migration_note = f"0 since {prev}"
        else:
            lines, scan_rc = scan_migrations(paths, sha, a.repo)
            names = ", ".join(Path(p).name for p in paths)
            migration_note = f"{len(paths)} since {prev}: {names}"
            if scan_rc == 0:
                print(f"migrations since {prev}: {names} (none refused)")
            else:
                level = "warning" if a.allow_refused_migrations else "error"
                for l in lines:
                    if "REFUSED" in l:
                        print(f"::{level} title=refused migration::{l}")
                print("\n".join(lines))
                if a.allow_refused_migrations:
                    migration_note += " (REFUSED, allowed: applied by hand)"
                else:
                    migration_note += " (REFUSED)"
                    print("::error title=prod preflight::a pending migration is one the migrate "
                          "Job refuses. Apply it by hand (./scripts/flyway.sh --prod migrate), "
                          "then re-dispatch with allow_refused_migrations: true")
                    rc = 1

    outputs = "".join(f"{output_key(img)}={digests[img]}\n" for img in IMAGES) + f"sha={sha}\n"
    _write(os.environ.get("GITHUB_OUTPUT"), outputs)
    rows = "".join(f"| `{img}` | `{digests[img] or 'absent, skipped'}` |\n" for img in IMAGES)
    _write(os.environ.get("GITHUB_STEP_SUMMARY"),
           f"## Prod roll-out preflight: `{a.tag}`\n\n"
           f"**Commit:** `{sha}`  \n**Migrations:** {migration_note}\n\n"
           f"| Image | Digest promoted |\n|---|---|\n{rows}\n"
           "Approving promotes exactly these digests, even if the tag moves.\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
