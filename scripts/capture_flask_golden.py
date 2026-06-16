"""Capture Flask golden-master fixtures for the Phase 2 FastAPI port.

Runs the legacy Flask app (api/app.py) through its in-process test client and
dumps the JSON responses for a representative set of *deterministic*,
DB-backed endpoints into ``tests/golden/flask/``. These fixtures are the
parity oracle: after each route group is ported, the FastAPI output is diffed
against the captured Flask output for the same inputs.

yfinance-/Polygon-backed analytics routes are intentionally NOT captured here
(their values drift with the market and require network); those are diffed
*structurally* and *live* at port time, per the Phase 2 plan.

DB SAFETY: run against the test DB only, e.g.

  TEST_DSN="$(grep '^QUANTCORE_TEST_DB_DSN=' .env | cut -d= -f2-)" \\
    env -u DISCORD_WEBHOOK_URL -u BUCKET_NAME -u BUCKET_KEY \\
    QUANTCORE_DB_DSN="$TEST_DSN" PYTHONPATH=. \\
    .venv/bin/python scripts/capture_flask_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = PROJECT_ROOT / "tests" / "golden" / "flask"

# (slug, method, path) — deterministic, DB-backed endpoints only.
ENDPOINTS = [
    ("health", "GET", "/api/health"),
    ("plans_all", "GET", "/api/plans?status=ALL"),
    ("plans_active", "GET", "/api/plans?status=ACTIVE"),
    ("symbols", "GET", "/api/symbols"),
    ("dashboard_stats", "GET", "/api/dashboard/stats"),
    ("portfolio", "GET", "/api/portfolio"),
    ("watchlist", "GET", "/api/watchlist"),
    ("securities", "GET", "/api/securities"),
]


def main() -> None:
    from api.app import create_app

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    app = create_app()
    client = app.test_client()

    for slug, method, path in ENDPOINTS:
        resp = client.open(path, method=method)
        record = {
            "method": method,
            "path": path,
            "status": resp.status_code,
            "json": resp.get_json(silent=True),
        }
        out = GOLDEN_DIR / f"{slug}.json"
        out.write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n")
        print(f"captured {slug:18s} {resp.status_code}  -> {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
