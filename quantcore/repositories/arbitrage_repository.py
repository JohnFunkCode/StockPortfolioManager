"""ArbitrageRepository — the curated pair universe plus NAV snapshot history.

Persistence only: YAML load, SQL read/write, field normalisation. No spread
math, no scoring, no NAV arithmetic — those live in ``quantcore/analytics``
and ``quantcore/services/arbitrage.py``.

Two sources feed the scanner:

* ``arb_universe.yaml`` at the repo root (alongside ``watchlist.yaml``) — the
  declared links between a security and its underlying.
* ``arb_nav_snapshots`` — point-in-time holdings and capital structure for the
  NAV vehicles. The YAML carries a bootstrap snapshot per vehicle; rows
  written here supersede it, so a premium history accumulates instead of the
  scanner being permanently pinned to whatever was hand-entered.
"""

from __future__ import annotations

import time
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

from quantcore.db import get_connection

DEFAULT_UNIVERSE_PATH = Path(__file__).resolve().parents[2] / "arb_universe.yaml"

VALID_KINDS = frozenset({"nav_vehicle", "commodity_etf", "producer"})
VALID_MECHANISMS = frozenset({
    "none", "redemption", "conversion", "buyback", "index_event", "deal_terms",
})

SQL_LATEST_SNAPSHOT = """
SELECT security, as_of, underlying, units, senior_claims, annual_senior_cost,
       other_assets, diluted_shares, source
  FROM arb_nav_snapshots
 WHERE security = :security
 ORDER BY as_of DESC
 LIMIT 1;
"""

SQL_SNAPSHOT_HISTORY = """
SELECT security, as_of, underlying, units, senior_claims, annual_senior_cost,
       other_assets, diluted_shares, source
  FROM arb_nav_snapshots
 WHERE security = :security
 ORDER BY as_of DESC
 LIMIT :limit;
"""

SQL_UPSERT_SNAPSHOT = """
INSERT INTO arb_nav_snapshots
    (security, as_of, underlying, units, senior_claims, annual_senior_cost,
     other_assets, diluted_shares, source, ingested_at)
VALUES
    (:security, :as_of, :underlying, :units, :senior_claims,
     :annual_senior_cost, :other_assets, :diluted_shares, :source, :ingested_at)
ON CONFLICT (security, as_of) DO UPDATE SET
    underlying         = excluded.underlying,
    units              = excluded.units,
    senior_claims      = excluded.senior_claims,
    annual_senior_cost = excluded.annual_senior_cost,
    other_assets       = excluded.other_assets,
    diluted_shares     = excluded.diluted_shares,
    source             = excluded.source,
    ingested_at        = excluded.ingested_at;
"""


def _iso(value) -> Optional[str]:
    """Normalise a YAML date / datetime / string to an ISO date string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip() or None


def _num(value, default=None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class ArbitrageRepository:
    """Curated universe + NAV snapshot persistence for the arbitrage scanner."""

    def __init__(self, universe_path: Optional[Path] = None):
        self.universe_path = Path(universe_path) if universe_path else DEFAULT_UNIVERSE_PATH

    # ------------------------------------------------------------------ #
    # Curated universe (YAML)
    # ------------------------------------------------------------------ #
    def load_universe(self) -> list[dict]:
        """Return normalised universe entries; an unreadable file yields []."""
        try:
            raw = yaml.safe_load(self.universe_path.read_text()) or []
        except (OSError, yaml.YAMLError):
            return []
        if not isinstance(raw, list):
            return []
        return [e for e in (self._normalise(item) for item in raw) if e]

    @staticmethod
    def _normalise(item) -> Optional[dict]:
        if not isinstance(item, dict):
            return None
        security = str(item.get("security") or "").strip().upper()
        underlying = str(item.get("underlying") or "").strip()
        if not security or not underlying:
            return None

        kind = str(item.get("kind") or "").strip().lower()
        if kind not in VALID_KINDS:
            return None
        mechanism = str(item.get("convergence_mechanism") or "none").strip().lower()
        if mechanism not in VALID_MECHANISMS:
            mechanism = "none"

        hedge = item.get("hedge_instrument")
        hedge = str(hedge).strip().upper() if hedge else None

        return {
            "security": security,
            "name": (item.get("name") or security),
            "kind": kind,
            "underlying": underlying,
            "hedge_instrument": hedge,
            "convergence_mechanism": mechanism,
            "holdings_units": _num(item.get("holdings_units")),
            "holdings_as_of": _iso(item.get("holdings_as_of")),
            "senior_claims_usd": _num(item.get("senior_claims_usd"), 0.0),
            "annual_senior_cost_usd": _num(item.get("annual_senior_cost_usd"), 0.0),
            "other_assets_usd": _num(item.get("other_assets_usd"), 0.0),
            "diluted_shares": _num(item.get("diluted_shares")),
            "source": item.get("source"),
            "notes": (item.get("notes") or "").strip() or None,
        }

    def get_entry(self, security: str) -> Optional[dict]:
        target = (security or "").strip().upper()
        return next((e for e in self.load_universe() if e["security"] == target), None)

    # ------------------------------------------------------------------ #
    # NAV snapshots (SQL)
    # ------------------------------------------------------------------ #
    def latest_nav_snapshot(self, security: str) -> Optional[dict]:
        with closing(get_connection()) as conn:
            row = conn.execute(
                SQL_LATEST_SNAPSHOT, {"security": (security or "").upper()}
            ).fetchone()
        return dict(row) if row else None

    def nav_snapshot_history(self, security: str, limit: int = 90) -> list[dict]:
        with closing(get_connection()) as conn:
            rows = conn.execute(
                SQL_SNAPSHOT_HISTORY,
                {"security": (security or "").upper(), "limit": int(limit)},
            ).fetchall()
        return [dict(r) for r in rows]

    def record_nav_snapshot(self, security: str, as_of: str, underlying: str,
                            units: float, senior_claims: float = 0.0,
                            annual_senior_cost: float = 0.0,
                            other_assets: float = 0.0,
                            diluted_shares: Optional[float] = None,
                            source: Optional[str] = None) -> None:
        params = {
            "security": (security or "").upper(),
            "as_of": _iso(as_of),
            "underlying": underlying,
            "units": float(units),
            "senior_claims": float(senior_claims or 0.0),
            "annual_senior_cost": float(annual_senior_cost or 0.0),
            "other_assets": float(other_assets or 0.0),
            "diluted_shares": float(diluted_shares) if diluted_shares else None,
            "source": source,
            "ingested_at": int(time.time()),
        }
        with closing(get_connection()) as conn:
            try:
                conn.execute(SQL_UPSERT_SNAPSHOT, params)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
