"""Request models for the Step 7 options surface-gap endpoints.

Only the POST body needs a request model; the GET contracts endpoint takes
typed query params directly in the route signature.
"""

from __future__ import annotations

from pydantic import BaseModel


class VerticalSpreadRequest(BaseModel):
    """Body for POST /api/securities/{ticker}/options/vertical-spread.

    Mirrors OptionsService.price_vertical_spread (minus ``symbol``, which is the
    path param). ``kind`` defaults to a call (debit) spread.
    """

    expiration: str
    long_strike: float
    short_strike: float
    kind: str = "call"
    max_snapshot_age_minutes: int = 15
    allow_live_fetch: bool = True
