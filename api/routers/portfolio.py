"""Portfolio / watchlist / securities routes.

Ports the Flask handlers (api/app.py) for:
  GET/POST  /api/portfolio          (+ ?owner=, default "john")
  DELETE    /api/portfolio/{ticker}
  POST      /api/portfolio/import    (multipart file OR JSON path)
  GET/POST  /api/watchlist
  GET       /api/securities
  GET       /api/securities/lookup

These routes returned the bare ``{"error": message}`` body (no ``status`` key)
on validation/not-found, so they use ``route_error_plain`` rather than the
harvester routes' ``route_error``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, File, Request, UploadFile

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
    ImportResult,
    RemovePositionResponse,
    SecuritiesResponse,
    SymbolLookupResponse,
)
from quantcore.services.portfolio import DuplicateSymbolError

router = APIRouter(prefix="/api", tags=["securities"])


# --------------------------------------------------------------------------- #
# Portfolio
# --------------------------------------------------------------------------- #
@router.get("/portfolio", response_model=SecuritiesResponse)
def get_portfolio(owner: str = "john") -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse({"securities": load_portfolio(owner)})


@router.post("/portfolio", response_model=AddSecurityResponse, status_code=201)
def add_to_portfolio(body: AddPositionRequest, owner: str = "john") -> QuantCoreJSONResponse:
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

    return QuantCoreJSONResponse({"symbol": symbol, "destination": "portfolio"}, status_code=201)


@router.delete("/portfolio/{ticker}", response_model=RemovePositionResponse)
def remove_from_portfolio(ticker: str, owner: str = "john") -> QuantCoreJSONResponse:
    ticker = ticker.upper()
    removed = services().portfolio.remove_position(owner, ticker)
    if removed == 0:
        return route_error_plain(f"{ticker} not found in portfolio", 404)
    return QuantCoreJSONResponse({"symbol": ticker, "removed": True})


@router.post("/portfolio/import", response_model=ImportResult)
async def import_portfolio(
    request: Request,
    owner: str = "john",
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
def get_securities() -> QuantCoreJSONResponse:
    portfolio = {s["symbol"]: s for s in load_portfolio()}
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
