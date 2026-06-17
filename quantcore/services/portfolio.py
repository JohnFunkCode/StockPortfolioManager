"""PortfolioService — DB-backed, multi-owner portfolio holdings.

Phase 1 Step 6 (docs/proposals/phase1-migration-plan.md). Positions live in the
`positions` table (owner-scoped); portfolio.csv becomes an import format with
full-sync/replace semantics. Adapters (REST routes in api/app.py, the report
build in main.py, scripts/import_portfolio.py) call
``get_services().portfolio.<method>``.

list_positions() returns the same dict shape api/app.py previously produced from
the CSV, so the WebUI and downstream callers are unaffected.
"""

from __future__ import annotations

import csv
from typing import Any, Dict, List, Optional

from quantcore.repositories.portfolio_repository import PortfolioRepository


class DuplicateSymbolError(Exception):
    """Raised by add_position when the owner already holds the symbol."""


def _normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a raw CSV/body dict into the canonical position shape.

    Mirrors the parsing api/app.py._load_portfolio() applied to portfolio.csv.
    """
    def _f(v):
        return float(v) if v not in (None, "") else None

    def _i(v):
        return int(v) if v not in (None, "") else None

    return {
        "name": (raw.get("name") or "").strip(),
        "symbol": (raw.get("symbol") or "").strip().upper(),
        "purchase_price": _f(raw.get("purchase_price")),
        "quantity": _i(raw.get("quantity")),
        "purchase_date": (raw.get("purchase_date") or None),
        "currency": (raw.get("currency") or "USD").strip().upper(),
        "sale_price": _f(raw.get("sale_price")),
        "sale_date": (raw.get("sale_date") or None),
    }


class PortfolioService:
    def __init__(self, portfolio_repository: PortfolioRepository) -> None:
        self._repo = portfolio_repository

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_positions(self, owner: str = "john") -> List[Dict[str, Any]]:
        return self._repo.list_positions(owner)

    def list_owners(self) -> List[str]:
        return self._repo.list_owners()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def import_csv(self, path: str, owner: str) -> int:
        """Full-replace `owner`'s positions with the rows in the CSV at `path`.

        Returns the number of positions imported.
        """
        rows: List[Dict[str, Any]] = []
        with open(path, newline="") as fh:
            for raw in csv.DictReader(fh):
                row = _normalize_row(raw)
                if not row["symbol"]:
                    continue
                rows.append(row)
        return self._repo.replace_owner_positions(owner, rows)

    def add_position(self, owner: str = "john", **fields: Any) -> Dict[str, Any]:
        """Add a single position for `owner`.

        Raises DuplicateSymbolError if the owner already holds the symbol —
        preserving the REST 409-on-duplicate behaviour.
        """
        row = _normalize_row(fields)
        if not row["symbol"]:
            raise ValueError("symbol is required")
        if self._repo.count_for_symbol(owner, row["symbol"]) > 0:
            raise DuplicateSymbolError(f"{row['symbol']} is already in the portfolio")
        self._repo.add_position(owner, row)
        return {"symbol": row["symbol"]}

    def remove_position(self, owner: str, symbol: str) -> int:
        """Remove every lot of `symbol` for `owner`. Returns rows removed."""
        return self._repo.remove_position(owner, symbol)
