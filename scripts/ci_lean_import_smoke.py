"""Lean-install import smoke — does everything still *import* on requirements-base.txt?

The two CI jobs disagree about dependencies, deliberately: ``deploy.yml`` installs
``requirements-dev.txt`` (base + report + coverage tooling, because that is where the
suite runs and is measured), while ``prod-rollout.yml`` installs ``requirements-base.txt``
alone — the lean set the containers actually ship. The consequence is a blind spot: a
module that imports matplotlib/jinja2/boto3 (or anything else outside the lean set)
passes every PR and then fails the **prod promotion**, which is the worst possible place
to learn it. That is not hypothetical; it is what happened on 2026-08-10.

This script is how ``deploy.yml`` closes that gap at PR time. It imports — and only
imports — every test module plus the entry points that run inside a container, so it
needs no database, no network, and no test run. A module that raises ``SkipTest`` at
import (the guarded-import pattern in ``tests/test_generate_portfolio_report.py``) is
reported as skipped, because that is precisely the sanctioned way to depend on the
report-only packages. Anything else that fails to import is a red build.

It deliberately does not run the tests: a second full execution would cost minutes to
tell us something the ``gate`` job already covers. Import safety is the whole question.

Run: ``PYTHONPATH=. python scripts/ci_lean_import_smoke.py``
Exit 0 = every module imported (or skipped on a report-only dependency); non-zero =
something in the lean install cannot be imported.
"""

from __future__ import annotations

import importlib
import sys
import traceback
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def module_names() -> list[str]:
    """Everything a lean install has to be able to import.

    The wrapper list is imported from the existing smoke rather than copied, so a
    newly added MCP server is covered here for free.
    """
    from scripts.ci_wrapper_smoke import WRAPPERS

    modules = ["main", "api.main"]
    modules += [import_path for import_path, _, _ in WRAPPERS]
    modules += sorted(
        f"tests.{path.stem}" for path in (REPO_ROOT / "tests").glob("test_*.py")
    )
    return modules


def main() -> int:
    failures: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []

    names = module_names()
    for name in names:
        try:
            importlib.import_module(name)
        except unittest.SkipTest as exc:
            # A guarded import declaring a report-only dependency. Sanctioned.
            skipped.append((name, str(exc)))
        except BaseException:  # noqa: BLE001 - a bare SystemExit here is still a failure
            failures.append((name, traceback.format_exc()))

    for name, reason in skipped:
        print(f"SKIP {name}: {reason}")
    for name, tb in failures:
        print(f"\nFAIL {name}\n{tb}", file=sys.stderr)

    print(
        f"\nlean import smoke: {len(names)} modules, "
        f"{len(failures)} failed, {len(skipped)} skipped"
    )
    if failures:
        print(
            "A module above cannot be imported on requirements-base.txt — the lean set "
            "the containers ship and prod-rollout.yml tests against. Either move the "
            "dependency into requirements-base.txt deliberately, or guard the import "
            "(see tests/test_generate_portfolio_report.py).",
            file=sys.stderr,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
