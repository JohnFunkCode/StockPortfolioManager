"""Test package.

This package initializer runs ONCE, before any ``tests.test_*`` module is
imported by ``unittest discover -s tests -t .``. It swaps in the isolated
test-database DSN *before* ``quantcore.db`` is first imported anywhere (that
module freezes ``DB_DSN`` at import time), so DB-backed suites never reach
production. Previously this preamble was duplicated at the top of ~14 test
modules; centralizing it here is the single source of truth.

When ``.env`` is absent (e.g. CI), whatever ``QUANTCORE_DB_DSN`` the
environment already set is left untouched.
"""
import os
from pathlib import Path

_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
            os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
            break
