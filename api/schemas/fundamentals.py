"""Request models for the Phase 3 Step 1 fundamentals surface-gap endpoints.

Only the batch scorer needs a request body; the ranking/cache GET endpoints take
typed query params directly in the route signatures.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, field_validator

from quantcore.services.batch_limits import (
    MAX_FUNDAMENTAL_BATCH_SYMBOLS,
    normalize_symbol_batch,
)


class ScoresBatchRequest(BaseModel):
    """Body for POST /api/securities/fundamentals/scores-batch.

    Mirrors FundamentalsService.get_fundamental_scores_batch — a list of ticker
    symbols scored in one call, cache-first.
    """

    symbols: List[str]

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_and_bound_symbols(cls, value):
        return normalize_symbol_batch(
            value, max_symbols=MAX_FUNDAMENTAL_BATCH_SYMBOLS
        )
