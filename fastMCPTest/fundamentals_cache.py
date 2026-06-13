"""Transitional shim — module moved to
quantcore/repositories/fundamentals_repository.py (architectural standard v2,
Phase 1 Step 0). Import from quantcore.repositories.fundamentals_repository
instead. This shim is deleted in Step 10.
"""

from quantcore.repositories.fundamentals_repository import *  # noqa: F401,F403
from quantcore.repositories.fundamentals_repository import (  # noqa: F401
    _get_ttl_seconds,
    cache_get,
    cache_get_all_latest,
    cache_history,
    cache_invalidate,
    cache_set,
    cache_stats,
)
