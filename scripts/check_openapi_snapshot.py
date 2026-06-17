"""CI OpenAPI snapshot diff — guard the REST surface (Phase 3 Step 10).

The REST tier (``api/main.py``) is the single front door every client and wrapper
depends on; an accidental route rename/removal silently breaks them. This script
distils the FastAPI app's OpenAPI spec down to its stable *surface* — the sorted set
of ``"METHOD /path  ->  operationId"`` lines — and diffs it against the committed
snapshot at ``docs/openapi-surface.txt``.

Adding, renaming, or deleting a route fails CI until the snapshot is regenerated
(``python scripts/check_openapi_snapshot.py --update``) and committed in the same PR,
making every surface change a reviewable diff.

Run (check):  ``PYTHONPATH=. python scripts/check_openapi_snapshot.py``
Run (update): ``PYTHONPATH=. python scripts/check_openapi_snapshot.py --update``
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent / "docs" / "openapi-surface.txt"


def current_surface() -> str:
    # Keep auth inert so importing the app never demands a key/secret.
    os.environ.setdefault("AUTH_DISABLED", "1")
    from api.main import app

    spec = app.openapi()
    lines: list[str] = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() in {"parameters", "servers"}:
                continue
            op_id = op.get("operationId", "") if isinstance(op, dict) else ""
            lines.append(f"{method.upper():6} {path}  ->  {op_id}")
    lines.sort()
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    surface = current_surface()
    if "--update" in argv:
        SNAPSHOT.write_text(surface)
        n = surface.strip().count("\n") + 1
        print(f"Wrote {SNAPSHOT.relative_to(SNAPSHOT.parents[1])} ({n} routes).")
        return 0
    if not SNAPSHOT.exists():
        print(f"ERROR: snapshot {SNAPSHOT} missing — run with --update and commit it.")
        return 2
    expected = SNAPSHOT.read_text()
    if surface == expected:
        n = surface.strip().count("\n") + 1
        print(f"OpenAPI surface matches snapshot ({n} routes).")
        return 0
    # Minimal line diff so the failure is actionable.
    exp = set(expected.strip().splitlines())
    cur = set(surface.strip().splitlines())
    print("OpenAPI surface DRIFT vs docs/openapi-surface.txt:")
    for line in sorted(exp - cur):
        print(f"  - removed: {line}")
    for line in sorted(cur - exp):
        print(f"  + added:   {line}")
    print("\nIf intentional: python scripts/check_openapi_snapshot.py --update  (and commit)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
