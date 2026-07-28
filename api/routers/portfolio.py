"""Portfolio / watchlist / securities routes.

Ports the Flask handlers (api/app.py) for:
  GET/POST  /api/portfolio
  DELETE    /api/portfolio/{ticker}
  POST      /api/portfolio/import    (multipart file OR JSON path)
  GET/POST  /api/watchlist
  GET       /api/securities
  GET       /api/securities/lookup

The owner is resolved from the authenticated principal via ``require_owner``
(issue #126 decision #1) — there is no ``?owner=`` query param; there is no
legitimate cross-user read path on this endpoint.

These routes returned the bare ``{"error": message}`` body (no ``status`` key)
on validation/not-found, so they use ``route_error_plain`` rather than the
harvester routes' ``route_error``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, File, Request, UploadFile

from ..auth import require_owner
from ..deps import (
    PROJECT_ROOT,
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
    CloseLotRequest,
    CloseLotResponse,
    CreateLotRequest,
    CreateLotResponse,
    DeleteLotResponse,
    ImportResult,
    LotsResponse,
    RemovePositionResponse,
    SecuritiesResponse,
    SymbolLookupResponse,
    SymbolRowsResponse,
    UpdateLotRequest,
    UpdateLotResponse,
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


@router.post("/watchlist", response_model=AddSecurityResponse, status_code=201)
def add_to_watchlist(body: AddWatchlistRequest) -> QuantCoreJSONResponse:
    """Append a new entry to ./watchlist.yaml."""
    symbol = (body.symbol or "").strip().upper()
    if not symbol:
        return route_error_plain("symbol is required", 400)

    existing = {s["symbol"] for s in load_watchlist()}
    if symbol in existing:
        return route_error_plain(f"{symbol} is already in the watchlist", 409)

    wl_path = PROJECT_ROOT / "watchlist.yaml"
    name = (body.name or "").strip()
    currency = (body.currency or "USD").strip().upper()
    tags = [t.strip() for t in (body.tags or []) if str(t).strip()]

    entry: dict = {"name": name or symbol, "symbol": symbol, "currency": currency}
    if tags:
        entry["tags"] = tags

    existing_text = wl_path.read_text() if wl_path.exists() else ""
    new_block = yaml.dump([entry], default_flow_style=False, allow_unicode=True)
    with open(wl_path, "a") as fh:
        if existing_text and not existing_text.endswith("\n"):
            fh.write("\n")
        fh.write(new_block)

    return QuantCoreJSONResponse({"symbol": symbol, "destination": "watchlist"}, status_code=201)


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
