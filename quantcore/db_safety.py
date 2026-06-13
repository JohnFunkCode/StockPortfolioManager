"""Guard against accidentally pointing tests or one-off scripts at production.

Phase 1 refactor convention (docs/proposals/phase1-migration-plan.md): all
development and test processes run with the test DSN exported as
QUANTCORE_DB_DSN. Because the application code reads a single env var, a
forgotten override would silently run against production. Call
``assert_not_production()`` at the top of test bootstrap and one-off scripts
to make that failure mode impossible.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse


def _read_env_dsn(env_path: Path, key: str) -> str | None:
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def _endpoint(dsn: str) -> tuple[str | None, int | None, str | None]:
    parsed = urlparse(dsn)
    return parsed.hostname, parsed.port, parsed.path


def assert_not_production(env_path: str | Path | None = None) -> None:
    """Abort if the effective QUANTCORE_DB_DSN matches the production DSN in .env.

    The production DSN is whatever the checked-out .env file records as
    QUANTCORE_DB_DSN; the effective DSN is what this process would actually
    connect with (os.environ). Matching host+port+database means a test or
    script is about to touch production — refuse to run.
    """
    env_file = Path(env_path) if env_path else Path(__file__).resolve().parent.parent / ".env"
    prod_dsn = _read_env_dsn(env_file, "QUANTCORE_DB_DSN")
    effective_dsn = os.environ.get("QUANTCORE_DB_DSN")

    if not effective_dsn:
        raise SystemExit(
            "QUANTCORE_DB_DSN is not set in the environment. Export the test "
            "DSN (QUANTCORE_TEST_DB_DSN from .env) before running."
        )
    if prod_dsn and _endpoint(effective_dsn) == _endpoint(prod_dsn):
        raise SystemExit(
            "Refusing to run: the effective QUANTCORE_DB_DSN matches the "
            f"production database recorded in {env_file}. Export the test DSN "
            "(QUANTCORE_TEST_DB_DSN) for this process instead."
        )

    # quantcore.db freezes DB_DSN at import time. If it was imported before
    # the env override took effect, the env check above passes while
    # get_connection() still targets production — catch that too.
    db_module = sys.modules.get("quantcore.db")
    if db_module is not None and prod_dsn and _endpoint(db_module.DB_DSN) == _endpoint(prod_dsn):
        raise SystemExit(
            "Refusing to run: quantcore.db was imported before the test DSN "
            "override, so its frozen DB_DSN still points at production. "
            "Ensure the override runs before any quantcore.db import."
        )
