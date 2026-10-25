"""Custom JSON response for the FastAPI REST tier.

Ports the behaviour of the Flask app's ``_JSONEncoder`` (api/app.py) so that
analytics payloads serialize byte-for-byte the same way under FastAPI:

  * ``Decimal``        -> ``float``
  * ``datetime`` / ``date`` -> ISO-8601 string
  * ``numpy`` scalars/arrays and ``pandas`` NaN/NaT -> JSON-native

This is the "Option A" pass-through serializer: heavy analytics endpoints
return their service dicts verbatim through this class, guaranteeing parity
with the Flask contract without a Pydantic ``response_model`` silently
stripping keys.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from fastapi.responses import JSONResponse


class _QuantCoreJSONEncoder(json.JSONEncoder):
    """Handle Decimal, date/datetime, numpy, and pandas NaN/NaT values."""

    def default(self, obj: Any) -> Any:  # noqa: ANN401
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            value = float(obj)
            return value if math.isfinite(value) else None
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if obj is pd.NaT or (isinstance(obj, float) and not math.isfinite(obj)):
            return None
        return super().default(obj)


class QuantCoreJSONResponse(JSONResponse):
    """FastAPI default response class preserving the legacy JSON contract.

    Mirrors the Flask app's ``ensure_ascii = False`` and ``_JSONEncoder``
    semantics so the React front end sees identical payloads after the
    Flask -> FastAPI cutover.
    """

    media_type = "application/json"

    def render(self, content: Any) -> bytes:  # noqa: ANN401
        try:
            return _dumps(content)
        except ValueError as exc:
            # ``default()`` never sees a plain float (np.float64 is one too),
            # so a NaN/inf float reaches ``allow_nan=False`` and raises. Null
            # it and retry; the walk costs nothing on the usual clean payload.
            # Anything else (a circular reference) is not ours to rescue.
            if "Out of range float values" not in str(exc):
                raise
            return _dumps(_null_non_finite(content))


def _dumps(content: Any) -> bytes:  # noqa: ANN401
    return json.dumps(
        content,
        ensure_ascii=False,
        allow_nan=False,
        cls=_QuantCoreJSONEncoder,
        separators=(",", ":"),
    ).encode("utf-8")


def _null_non_finite(value: Any) -> Any:  # noqa: ANN401
    """The payload with every NaN/±inf float replaced by None, recursively."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _null_non_finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_null_non_finite(v) for v in value]
    return value
