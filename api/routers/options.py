"""Options analytics surface (pass-through).

Ports the Flask options routes verbatim (Option A — no response_model, service
dicts ship byte-for-byte via QuantCoreJSONResponse):
  GET  /api/securities/{ticker}/options/{latest,history,analytics,chain,iv-rank}
  GET  /api/securities/{ticker}/signals/options-flow
  GET  /api/portfolio/delta-exposure
  POST /api/securities/{ticker}/options/history/backfill   (preserves 202)
  POST /api/securities/refresh-options-snapshots

Error parity: the GET analytics routes wrap exceptions as the plain
``{"error": str}`` 500; options-flow and refresh-options-snapshots have no
try/except (framework handler), matching Flask. ``backfill`` returns the
service's ``(payload, status)`` so long-running 202 semantics are preserved.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from ..deps import load_portfolio, load_watchlist, route_error_plain, services
from ..json_response import QuantCoreJSONResponse

router = APIRouter(prefix="/api", tags=["options"])


# --------------------------------------------------------------------------- #
# Per-security options data
# --------------------------------------------------------------------------- #
@router.get("/securities/{ticker}/options/latest")
def get_options_latest(ticker: str) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().options.get_options_latest(ticker))
    except Exception as exc:  # noqa: BLE001 — parity with Flask
        return route_error_plain(str(exc), 500)


@router.get("/securities/{ticker}/options/history")
def get_options_history(ticker: str, days: int = 30) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().options.get_options_history(ticker, days=days))
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/securities/{ticker}/options/analytics")
def get_options_analytics(ticker: str) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().options.get_options_analytics(ticker))
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/securities/{ticker}/options/chain")
def get_options_chain(ticker: str, expiration: Optional[str] = None) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().options.get_options_chain(ticker, expiration=expiration)
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/securities/{ticker}/options/iv-rank")
def get_iv_rank(ticker: str) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().options.get_iv_rank(ticker))
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/securities/{ticker}/signals/options-flow")
def get_signals_options_flow(ticker: str) -> QuantCoreJSONResponse:
    # No try/except — matches Flask; errors fall through to the framework handler.
    return QuantCoreJSONResponse(services().options.get_options_flow_signals(ticker))


# --------------------------------------------------------------------------- #
# Portfolio delta exposure
# --------------------------------------------------------------------------- #
@router.get("/portfolio/delta-exposure")
def get_portfolio_delta_exposure() -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().options.get_portfolio_delta_exposure(load_portfolio())
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


# --------------------------------------------------------------------------- #
# Long-running POSTs (preserve 202 / service-internal threading)
# --------------------------------------------------------------------------- #
@router.post("/securities/{ticker}/options/history/backfill")
def backfill_options_history(
    ticker: str,
    days: int = 90,
    skip_existing: str = "true",
) -> QuantCoreJSONResponse:
    skip = skip_existing.lower() != "false"
    try:
        payload, status = services().options.backfill_options_history(
            ticker, days=days, skip_existing=skip
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)
    return QuantCoreJSONResponse(payload, status_code=status)


@router.post("/securities/refresh-options-snapshots")
def refresh_options_snapshots(
    source: str = "portfolio",
    chain_type: str = "atm",
    batch_size: int = 10,
    max_workers: int = 4,
    batch_delay: float = 1.5,
) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().options.refresh_options_snapshots(
            load_portfolio(),
            load_watchlist(),
            source=source,
            chain_type=chain_type,
            batch_size=batch_size,
            max_workers=max_workers,
            batch_delay=batch_delay,
        )
    )
