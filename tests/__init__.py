"""Test package.

This package initializer runs ONCE, before any ``tests.test_*`` module is
imported by ``unittest discover -s tests -t .``. It swaps in the isolated
test-database DSN *before* ``quantcore.db`` is first imported anywhere (that
module freezes ``DB_DSN`` at import time), so DB-backed suites never reach
production. Previously this preamble was duplicated at the top of ~14 test
modules; centralizing it here is the single source of truth. Which ``.env``
key supplies that DSN -- a local Postgres by preference, Cloud SQL test as the
fallback -- is decided by ``_select_test_dsn`` below.

When ``.env`` is absent (e.g. CI), whatever ``QUANTCORE_DB_DSN`` the
environment already set is left untouched.

It also makes every real yfinance HTTP request fail fast (see the end of this
file): no test may depend on Yahoo being reachable, fast, or answering the
same way twice. Stub at ``YFinanceGateway`` (or ``yfinance.download`` for the
legacy ``portfolio/`` layer) instead.
"""
import os
from pathlib import Path

def _select_test_dsn(env_text: str, opt_in: str | None) -> str | None:
    """Pick the suite's DSN out of ``.env`` text (issue #289).

    ``QUANTCORE_UNITTEST_DB_DSN`` -- a local Postgres, the same shape CI's
    throwaway service has -- wins. ``QUANTCORE_TEST_DB_DSN`` (Cloud SQL test,
    through the proxy on :5434) is the fallback, so a ``.env`` without the new
    key behaves exactly as before. ``QUANTCORE_UNITTEST_DB=cloudsql`` in the
    environment forces the fallback for one run.

    The suite does not repoint QUANTCORE_TEST_DB_DSN itself because that key
    means "Cloud SQL test" to flyway.sh, with-test-db.sh, schema_check.py and
    the import scripts. Every round trip through the proxy costs ~29 ms, which
    is what made local runs take ~17 min (measured) against CI's ~1 min.
    """
    found = {}
    for line in env_text.splitlines():
        line = line.strip()
        for key in ("QUANTCORE_UNITTEST_DB_DSN", "QUANTCORE_TEST_DB_DSN"):
            if key not in found and line.startswith(f"{key}="):
                found[key] = line.split("=", 1)[1].strip()
    if opt_in != "cloudsql" and found.get("QUANTCORE_UNITTEST_DB_DSN"):
        return found["QUANTCORE_UNITTEST_DB_DSN"]
    return found.get("QUANTCORE_TEST_DB_DSN")


_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    _dsn = _select_test_dsn(
        _env_file.read_text(), os.environ.get("QUANTCORE_UNITTEST_DB")
    )
    if _dsn:
        os.environ["QUANTCORE_DB_DSN"] = _dsn

# Same reasoning, one layer up: the suite's bootstrap must not depend on
# whether the target database happens to carry a Flyway ledger. The local test
# database does (scripts/flyway.sh defaults to test), CI's throwaway Postgres
# does not, and QUANTCORE_SCHEMA_MODE=auto resolves those two worlds
# differently -- create vs warn. Pinning it keeps every DB-backed suite on the
# create path it has always had. The mode logic itself is exercised explicitly
# in tests/test_schema_bootstrap.py, which sets the variable per case.
os.environ["QUANTCORE_SCHEMA_MODE"] = "create"


# yfinance fetches over curl_cffi (libcurl), which socket-level patches never
# see; every request it makes does go through YfData._make_request, so that is
# the seam. Raising here turns a forgotten stub into an immediate, named
# failure (or a soft miss, where the code under test degrades on errors)
# instead of a slow, flaky call to Yahoo.
try:
    from yfinance.data import YfData as _YfData
except ImportError:  # yfinance absent or reorganized: nothing to guard
    _YfData = None


class YahooNetworkBlocked(RuntimeError):
    """A test reached Yahoo for real; stub the call instead."""


def _blocked_request(self, url, *args, **kwargs):
    raise YahooNetworkBlocked(
        f"tests must not call Yahoo ({url}); stub YFinanceGateway "
        "(see tests/__init__.py)"
    )


if _YfData is not None:
    _YfData._make_request = _blocked_request
