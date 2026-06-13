"""Transitional shim — module moved to quantcore/repositories/ohlcv_repository.py
(architectural standard v2, Phase 1 Step 0). Import from
quantcore.repositories.ohlcv_repository instead. This shim is deleted in Step 10.
"""

from quantcore.repositories.ohlcv_repository import *  # noqa: F401,F403
from quantcore.repositories.ohlcv_repository import (  # noqa: F401
    BarStatus,
    OHLCV,
    get_history,
    period_to_days,
)
