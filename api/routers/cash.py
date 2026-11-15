"""Cash ledger routes (#431 step 3.1).

  GET     /api/portfolio/cash                      a book's cash ledger
  PUT     /api/portfolio/cash/opening-balance      set the real book's opening balance
  POST    /api/portfolio/cash/entries              record a deposit or withdrawal
  DELETE  /api/portfolio/cash/entries/{entry_id}   remove a deposit or withdrawal

Exactly one service call deep. The owner comes from the authenticated
principal (``require_owner``); there is no ``?owner=``. Only the read takes a
``book_id``: without it, the real book, and a book that isn't the owner's
answers 404. The writes are for the real book only, like the lot writes. A
paper book's cash moves through its orders, which arrive in a later step.

A buy or sell entry has no route of its own. It is written with its lot or
sale and goes when that goes.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from quantcore.services.cash import BookNotFoundError, CashEntryNotFoundError

from ..auth import require_owner
from ..deps import route_error_plain, services
from ..json_response import QuantCoreJSONResponse
from ..schemas.cash import AddCashEntryRequest, CashLedgerResponse, SetOpeningBalanceRequest

router = APIRouter(prefix="/api/portfolio/cash", tags=["cash"])


@router.get("", response_model=CashLedgerResponse)
def get_cash_ledger(
    book_id: Optional[int] = None, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    """The opening balance, the current balance and every entry since, newest
    first. USD. Buys and sells are gross of fees."""
    try:
        ledger = services().cash.ledger(owner=owner, book_id=book_id)
    except BookNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    return QuantCoreJSONResponse(ledger)


@router.put("/opening-balance", response_model=CashLedgerResponse)
def set_opening_balance(
    body: SetOpeningBalanceRequest, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    try:
        ledger = services().cash.set_opening_balance(
            owner=owner, entry_date=body.date, amount=body.amount, notes=body.notes
        )
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(ledger)


@router.post("/entries", response_model=CashLedgerResponse, status_code=201)
def add_cash_entry(
    body: AddCashEntryRequest, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    try:
        ledger = services().cash.add_entry(
            owner=owner, kind=body.kind, entry_date=body.date, amount=body.amount,
            notes=body.notes,
        )
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(ledger, status_code=201)


@router.delete("/entries/{entry_id}", response_model=CashLedgerResponse)
def delete_cash_entry(entry_id: int, owner: str = Depends(require_owner)) -> QuantCoreJSONResponse:
    try:
        ledger = services().cash.delete_entry(owner=owner, entry_id=entry_id)
    except CashEntryNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    return QuantCoreJSONResponse(ledger)
