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

from typing import List, Optional

from fastapi import APIRouter, Query

from ..deps import load_portfolio, load_watchlist, route_error_plain, services
from ..json_response import QuantCoreJSONResponse
from ..schemas.options import VerticalSpreadRequest

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


# --------------------------------------------------------------------------- #
# Step 7 surface-gap endpoints (previously MCP-only) — contract/spread pricing
# --------------------------------------------------------------------------- #
@router.get("/securities/{ticker}/options/contracts")
def get_option_contracts(
    ticker: str,
    expirations: List[str] = Query(..., description="Expiration dates, YYYY-MM-DD (repeatable)"),
    strikes: List[float] = Query(..., description="Option strikes to retrieve (repeatable)"),
    kind: str = "call",
) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().options.get_option_contracts(
                ticker, expirations=expirations, strikes=strikes, kind=kind
            )
        )
    except Exception as exc:  # noqa: BLE001 — plain-error parity with the other GETs
        return route_error_plain(str(exc), 500)


@router.post("/securities/{ticker}/options/vertical-spread")
def price_vertical_spread(ticker: str, body: VerticalSpreadRequest) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().options.price_vertical_spread(
                ticker,
                expiration=body.expiration,
                long_strike=body.long_strike,
                short_strike=body.short_strike,
                kind=body.kind,
                max_snapshot_age_minutes=body.max_snapshot_age_minutes,
                allow_live_fetch=body.allow_live_fetch,
                include_curves=body.include_curves,
            )
        )
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
# Phase 3 Step 1 surface-gap endpoints (previously MCP-only) — full-chain fetch,
# unusual-call sweeps, delta-adjusted OI, gamma-wall history, and the
# OptionsScreeningService watchlist/symbol scorers. Each is one service call deep
# and ships the dict verbatim, mirroring the MCP tool signatures.
# --------------------------------------------------------------------------- #
@router.get("/securities/{ticker}/options/full-chain")
def get_full_options_chain(ticker: str) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().options.get_full_options_chain(ticker))
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/securities/{ticker}/options/unusual-calls")
def get_unusual_calls(
    ticker: str,
    min_volume: int = 100,
    min_vol_oi_ratio: float = 0.5,
    max_expirations: int = 3,
) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().options.get_unusual_calls(
                ticker, min_volume, min_vol_oi_ratio, max_expirations
            )
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/securities/{ticker}/options/delta-adjusted-oi")
def get_delta_adjusted_oi(
    ticker: str, max_expirations: int = 3, risk_free_rate: float = 0.045
) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().options.get_delta_adjusted_oi(ticker, max_expirations, risk_free_rate)
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/securities/{ticker}/options/gamma-wall-history")
def get_gamma_wall_history(ticker: str, since_days: int = 90) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().options.get_gamma_wall_history(ticker, since_days)
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/securities/{ticker}/options/screen")
def screen_options_symbol(
    ticker: str, puts_budget: float = 1000.0, top_n: int = 10
) -> QuantCoreJSONResponse:
    """Rule-based long/put scoring for a single symbol (OptionsScreeningService)."""
    try:
        return QuantCoreJSONResponse(
            services().options_screening.analyze_symbol(
                ticker, puts_budget=puts_budget, top_n=top_n
            )
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/options/screen-watchlist")
def screen_options_watchlist(
    puts_budget: float = 1000.0, top_n: int = 10, include_non_us: bool = False
) -> QuantCoreJSONResponse:
    """Score the server-side watchlist.yaml. The MCP tool's ``watchlist_path`` arg is
    intentionally not exposed over REST (no arbitrary server filesystem paths)."""
    try:
        return QuantCoreJSONResponse(
            services().options_screening.analyze_watchlist(
                puts_budget=puts_budget, top_n=top_n, include_non_us=include_non_us
            )
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
