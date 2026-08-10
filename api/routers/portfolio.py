"""Portfolio / watchlist / securities routes.

Ports the Flask handlers (api/app.py) for:
  GET/POST  /api/portfolio
  DELETE    /api/portfolio/{ticker}
  POST      /api/portfolio/import    (multipart file OR JSON path)
  GET/POST  /api/watchlist
  PATCH     /api/watchlist/{ticker}   (tags only — issue #147 Part C)
  DELETE    /api/watchlist/{ticker}
  GET       /api/securities
  GET       /api/securities/lookup

The owner is resolved from the authenticated principal via ``require_owner``
(issue #126 decision #1) — there is no ``?owner=`` query param; there is no
legitimate cross-user read path on this endpoint.

The watchlist routes are the exception: that list is global (issue #83), so
its writes take ``require_principal`` instead — authenticated, but not
owner-scoped, since there is no owner to scope them to.

These routes returned the bare ``{"error": message}`` body (no ``status`` key)
on validation/not-found, so they use ``route_error_plain`` rather than the
harvester routes' ``route_error``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile

from ..auth import Principal, require_owner, require_principal
from ..deps import (
    load_portfolio,
    load_watchlist,
    route_error_plain,
    services,
)
from ..json_response import QuantCoreJSONResponse
from ..schemas.portfolio import (
    AddPositionRequest,
    AddSecurityResponse,
    AddWatchlistRequest,
    AddWatchlistResponse,
    CloseLotRequest,
    CloseLotResponse,
    CreateLotRequest,
    CreateLotResponse,
    DeleteLotResponse,
    ImportResult,
    LotsResponse,
    RemovePositionResponse,
    RemoveWatchlistResponse,
    SecuritiesResponse,
    SymbolLookupResponse,
    SymbolRowsResponse,
    UpdateLotRequest,
    UpdateLotResponse,
    UpdateWatchlistTagsRequest,
    UpdateWatchlistTagsResponse,
    WatchlistFundamentalsResponse,
)
from quantcore.services.portfolio import DuplicateSymbolError

router = APIRouter(prefix="/api", tags=["securities"])


# --------------------------------------------------------------------------- #
# Portfolio
# --------------------------------------------------------------------------- #
@router.get("/portfolio", response_model=SecuritiesResponse)
def get_portfolio(owner: str = Depends(require_owner)) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse({"securities": load_portfolio(owner)})


@router.post("/portfolio", response_model=AddSecurityResponse, status_code=201)
def add_to_portfolio(
    body: AddPositionRequest, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    symbol = (body.symbol or "").strip().upper()
    if not symbol:
        return route_error_plain("symbol is required", 400)

    try:
        services().portfolio.add_position(
            owner,
            name=(body.name or "").strip(),
            symbol=symbol,
            purchase_price=body.purchase_price,
            quantity=body.quantity,
            purchase_date=body.purchase_date,
            currency=body.currency,
        )
    except DuplicateSymbolError as exc:
        return route_error_plain(str(exc), 409)
    except ValueError as exc:
        return route_error_plain(str(exc), 422)

    return QuantCoreJSONResponse({"symbol": symbol, "destination": "portfolio"}, status_code=201)


@router.delete("/portfolio/{ticker}", response_model=RemovePositionResponse)
def remove_from_portfolio(
    ticker: str, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    ticker = ticker.upper()
    removed = services().portfolio.remove_position(owner, ticker)
    if removed == 0:
        return route_error_plain(f"{ticker} not found in portfolio", 404)
    return QuantCoreJSONResponse({"symbol": ticker, "removed": True})


@router.post("/portfolio/import", response_model=ImportResult)
async def import_portfolio(
    request: Request,
    owner: str = Depends(require_owner),
    file: Optional[UploadFile] = File(default=None),
) -> QuantCoreJSONResponse:
    """Full-sync replace of the owner's positions from an uploaded CSV.

    Accepts either a multipart file upload (form field ``file``) or a JSON body
    with a server-side ``path``.
    """
    if file is not None:
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=True) as tmp:
            tmp.write(await file.read())
            tmp.flush()
            count = services().portfolio.import_csv(tmp.name, owner)
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        path = (body or {}).get("path")
        if not path:
            return route_error_plain("a CSV file upload or 'path' is required", 400)
        if not Path(path).exists():
            return route_error_plain(f"CSV not found: {path}", 404)
        count = services().portfolio.import_csv(path, owner)

    return QuantCoreJSONResponse({"owner": owner, "imported": count})


# --------------------------------------------------------------------------- #
# Lots (issue #126 Step 4.5) — owner isolation is enforced at the SQL layer
# (PortfolioRepository filters WHERE owner=... on every lot lookup/mutation),
# so these routes trust a None/False/empty-rowcount result rather than doing
# their own fetch-then-check.
# --------------------------------------------------------------------------- #
@router.get("/portfolio/lots", response_model=LotsResponse)
def get_lots(
    force: bool = False, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    lots = services().portfolio.list_lots(owner, status="OPEN", force=force)
    return QuantCoreJSONResponse({"lots": lots})


@router.get("/portfolio/symbols", response_model=SymbolRowsResponse)
def get_symbol_rows(
    force: bool = False, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    rows = services().portfolio.symbol_rows(owner, force=force)
    totals = services().portfolio.portfolio_totals(rows)
    return QuantCoreJSONResponse({"symbols": rows, "totals": totals})


@router.post("/portfolio/lots", response_model=CreateLotResponse, status_code=201)
def create_lot(
    body: CreateLotRequest, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    symbol = (body.symbol or "").strip().upper()
    if not symbol:
        return route_error_plain("symbol is required", 400)

    try:
        result = services().portfolio.add_position(
            owner,
            name=(body.name or "").strip(),
            symbol=symbol,
            purchase_price=body.purchase_price,
            quantity=body.quantity,
            trade_date=body.trade_date,
            currency=body.currency,
            fees=body.fees,
            acquisition_type=body.acquisition_type,
            account=body.account,
            notes=body.notes,
        )
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(result, status_code=201)


@router.patch("/portfolio/lots/{lot_id}", response_model=UpdateLotResponse)
def update_lot(
    lot_id: int, body: UpdateLotRequest, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return route_error_plain("no fields to update", 400)
    try:
        updated = services().portfolio.update_lot(owner, lot_id, **fields)
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    if not updated:
        return route_error_plain(f"lot {lot_id} not found", 404)
    return QuantCoreJSONResponse({"lot_id": lot_id, "updated": True})


@router.delete("/portfolio/lots/{lot_id}", response_model=DeleteLotResponse)
def delete_lot(lot_id: int, owner: str = Depends(require_owner)) -> QuantCoreJSONResponse:
    deleted = services().portfolio.delete_lot(owner, lot_id)
    if not deleted:
        return route_error_plain(f"lot {lot_id} not found", 404)
    return QuantCoreJSONResponse({"lot_id": lot_id, "deleted": True})


@router.post("/portfolio/lots/{lot_id}/close", response_model=CloseLotResponse)
def close_lot(
    lot_id: int, body: CloseLotRequest, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    try:
        result = services().portfolio.close_lot(
            owner,
            lot_id,
            shares=body.shares,
            sale_price=body.sale_price,
            sale_trade_date=body.sale_trade_date,
            method=body.method,
            lots=body.lots,
            fees=body.fees,
        )
    except ValueError as exc:
        message = str(exc)
        if "not found" in message:
            status = 404
        elif "is not open" in message:
            status = 409
        else:
            status = 422
        return route_error_plain(message, status)
    return QuantCoreJSONResponse(result)


# --------------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------------- #
@router.get("/watchlist", response_model=SecuritiesResponse)
def get_watchlist() -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse({"securities": load_watchlist()})


# Declared before /watchlist/{ticker} out of habit, not necessity: that path
# template carries no GET (PATCH and DELETE only), and Starlette treats a path
# match with the wrong method as a *partial* match and keeps scanning, so this
# GET is found either way today.
# What would make the order load-bearing is adding a GET on /watchlist/{ticker}
# — a plausible detail endpoint — after which the reverse order swallows
# "fundamentals" as a ticker and returns a 404 for a symbol nobody holds. The
# guard against that is behavioural, in test_api_smoke.py, not this comment.
@router.get("/watchlist/fundamentals", response_model=WatchlistFundamentalsResponse)
def get_watchlist_fundamentals() -> QuantCoreJSONResponse:
    """Returns + cached fundamentals for the whole watchlist, in six queries
    and no network calls (issue #147 Part B3/B5).

    This is the page that replaces the nightly HTML report. No ``?owner=`` —
    the watchlist is global (issue #83).
    """
    return QuantCoreJSONResponse(services().watchlist.returns_and_fundamentals())


@router.post("/watchlist", response_model=AddWatchlistResponse, status_code=201)
def add_to_watchlist(
    body: AddWatchlistRequest, principal: Principal = Depends(require_principal)
) -> QuantCoreJSONResponse:
    """Add a symbol to the global watchlist.

    ``require_principal`` rather than ``require_owner`` (decision 5): the list
    is shared, so any authenticated caller may write to it; the principal is
    recorded in ``added_by`` for audit only.

    ``body.currency`` is passed through as a fallback only — the service reads
    the real one off the exchange — so the response echoes what was stored.
    """
    try:
        result = services().watchlist.add_entry(
            symbol=body.symbol or "",
            name=body.name,
            currency=body.currency,
            tags=body.tags,
            added_by=principal.owner,
        )
    except DuplicateSymbolError as exc:
        return route_error_plain(str(exc), 409)
    except ValueError as exc:
        return route_error_plain(str(exc), 400)

    return QuantCoreJSONResponse(
        {
            "symbol": result["symbol"],
            "destination": "watchlist",
            "currency": result["currency"],
        },
        status_code=201,
    )


@router.patch("/watchlist/{ticker}", response_model=UpdateWatchlistTagsResponse)
def update_watchlist_tags(
    ticker: str,
    body: UpdateWatchlistTagsRequest,
    principal: Principal = Depends(require_principal),
) -> QuantCoreJSONResponse:
    """Replace a watched symbol's tags (issue #147 Part C).

    PATCH, not PUT: tags are the only editable field, and the rest of the entry
    — name, currency, added_by — is deliberately not addressable here. The
    currency in particular is resolved from the exchange and must not become
    something a client can overwrite.

    ``require_principal`` for the same reason as the POST: the list is shared,
    so any authenticated caller may retag it.
    """
    ticker = ticker.strip().upper()
    try:
        stored = services().watchlist.set_tags(ticker, body.tags)
    except ValueError as exc:
        return route_error_plain(str(exc), 400)
    if stored is None:
        return route_error_plain(f"{ticker} not found in watchlist", 404)
    return QuantCoreJSONResponse({"symbol": ticker, "tags": stored})


@router.delete("/watchlist/{ticker}", response_model=RemoveWatchlistResponse)
def remove_from_watchlist(
    ticker: str, principal: Principal = Depends(require_principal)
) -> QuantCoreJSONResponse:
    """Remove a symbol from the global watchlist.

    New in issue #83 — the YAML-backed watchlist had no delete path at all.
    """
    ticker = ticker.strip().upper()
    removed = services().watchlist.remove_entry(ticker)
    if removed == 0:
        return route_error_plain(f"{ticker} not found in watchlist", 404)
    return QuantCoreJSONResponse({"symbol": ticker, "removed": True})


# --------------------------------------------------------------------------- #
# Securities — combined portfolio + watchlist
# --------------------------------------------------------------------------- #
@router.get("/securities", response_model=SecuritiesResponse)
def get_securities(owner: str = Depends(require_owner)) -> QuantCoreJSONResponse:
    portfolio = {s["symbol"]: s for s in load_portfolio(owner)}
    watchlist = {s["symbol"]: s for s in load_watchlist()}
    combined: dict[str, dict] = {}
    for sym, s in portfolio.items():
        combined[sym] = s
    for sym, s in watchlist.items():
        if sym in combined:
            combined[sym]["source"] = "both"
            combined[sym]["tags"] = s["tags"]
        else:
            combined[sym] = s
    return QuantCoreJSONResponse({"securities": list(combined.values())})


@router.get("/securities/lookup", response_model=SymbolLookupResponse)
def lookup_security(symbol: str = "") -> QuantCoreJSONResponse:
    symbol = symbol.strip().upper()
    if not symbol:
        return route_error_plain("symbol is required", 400)
    try:
        info = services().yfinance_gateway.ticker_info(symbol) or {}
        name = info.get("longName") or info.get("shortName") or ""
        sector = info.get("sector") or ""
        industry = info.get("industry") or ""
        suggested_tags = [t for t in [sector, industry] if t]
        return QuantCoreJSONResponse(
            {"symbol": symbol, "name": name, "suggested_tags": suggested_tags}
        )
    except Exception as exc:
        return route_error_plain(str(exc), 500)
