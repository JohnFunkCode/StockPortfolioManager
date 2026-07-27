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
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from quantcore.repositories.portfolio_repository import PortfolioRepository

logger = logging.getLogger(__name__)


class DuplicateSymbolError(Exception):
    """Kept for import-compatibility. No longer raised by add_position — a
    symbol may have any number of open lots (issue #126 Step 3.3)."""


def _money(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v not in (None, "") else None


def _quantity(v) -> Optional[Decimal]:
    return Decimal(str(v)).quantize(Decimal("0.000001")) if v not in (None, "") else None


def _normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a raw CSV/body dict into the canonical position shape.

    `trade_date` is the lot's real identity; `purchase_date` is a deprecated
    alias accepted for backward compatibility (issue #126 Step 3.3).
    """
    return {
        "name": (raw.get("name") or "").strip(),
        "symbol": (raw.get("symbol") or "").strip().upper(),
        "purchase_price": _money(raw.get("purchase_price")),
        "quantity": _quantity(raw.get("quantity")),
        "purchase_date": (raw.get("purchase_date") or None),
        "trade_date": (raw.get("trade_date") or raw.get("purchase_date") or None),
        "currency": (raw.get("currency") or "USD").strip().upper(),
        "sale_price": _money(raw.get("sale_price")),
        "sale_date": (raw.get("sale_date") or None),
        "fees": _money(raw.get("fees")),
        "acquisition_type": (raw.get("acquisition_type") or None),
        "account": (raw.get("account") or None),
        "notes": (raw.get("notes") or None),
    }


class PortfolioService:
    def __init__(self, portfolio_repository: PortfolioRepository) -> None:
        self._repo = portfolio_repository

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_positions(self, owner: str) -> List[Dict[str, Any]]:
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
        warned_deprecated_alias = False
        with open(path, newline="") as fh:
            for raw in csv.DictReader(fh):
                row = _normalize_row(raw)
                if not row["symbol"]:
                    continue
                if not raw.get("trade_date") and raw.get("purchase_date") and not warned_deprecated_alias:
                    logger.warning(
                        "import_csv(owner=%s, path=%s): 'purchase_date' is a deprecated "
                        "alias for 'trade_date' — update the CSV to include 'trade_date'",
                        owner, path,
                    )
                    warned_deprecated_alias = True
                rows.append(row)
        return self._repo.replace_owner_positions(owner, rows)

    def add_position(self, owner: str = "john", **fields: Any) -> Dict[str, Any]:
        """Add a single lot for `owner`. Multiple lots per symbol are allowed —
        each call creates a new lot rather than upserting (issue #126 Step 3.3).
        """
        row = _normalize_row(fields)
        if not row["symbol"]:
            raise ValueError("symbol is required")
        self._repo.add_position(owner, row)
        return {"symbol": row["symbol"]}

    def remove_position(self, owner: str, symbol: str) -> int:
        """Remove every lot of `symbol` for `owner`. Returns rows removed."""
        return self._repo.remove_position(owner, symbol)
