"""
options_position_store.py — Tracks active options positions and drives alert logic.

Addresses GitHub issue #12: "Enhance the notifier to send alerts related to
option orders. E.g. sell when in the money, expirations, etc."

Positions are added manually (or via CLI) and checked each time the notifier
runs.  Positions are owned (#460): ``owner`` is a required keyword-only
argument on every public method, and every statement carries the ``owner``
predicate, so another owner's position reads as absent and can't be changed.

The store surfaces three kinds of alerts:

  ITM          — option just crossed into the money
  EXPIRATION   — expiration is within 7 days (fires daily) or 1 day (fires daily)
  PROFIT_TARGET — estimated intrinsic value reached 2× the purchase price

Usage:
    from quantcore.repositories.options_position_repository import OptionsPositionStore

    store = OptionsPositionStore()
    pos_id = store.add_position(
        symbol="AMD", kind="put", strike=230.0, expiration="2026-05-16",
        contracts=1, purchase_price=4.10, purchase_date="2026-04-09",
        target_price=187.89, owner="john",
    )

    alerts = store.get_pending_alerts(current_prices={"AMD": 215.0}, owner="john")
    store.close_position(pos_id, reason="sold", owner="john")
"""

from contextlib import closing
from datetime import date, datetime, timezone, timedelta
from typing import NamedTuple, Optional

from quantcore.db import get_connection
from quantcore.analytics.market_time import market_date

# Alert type constants
ALERT_ITM           = "ITM"
ALERT_EXPIRATION_7D = "EXPIRATION_7D"
ALERT_EXPIRATION_1D = "EXPIRATION_1D"
ALERT_PROFIT_TARGET = "PROFIT_TARGET"

# Profit target multiplier — alert when intrinsic value reaches this × purchase cost
PROFIT_TARGET_MULTIPLIER = 2.0


class OptionsPositionStore:
    """Manages active options positions and computes pending alerts."""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def add_position(
        self,
        symbol: str,
        kind: str,
        strike: float,
        expiration: str,
        contracts: int,
        purchase_price: float,
        purchase_date: str,
        target_price: Optional[float] = None,
        notes: Optional[str] = None,
        *,
        owner: str,
    ) -> int:
        """
        Add an active options position.

        Returns the new position_id.

        Parameters
        ----------
        symbol         : ticker symbol, e.g. "AMD"
        kind           : "call" or "put"
        strike         : option strike price
        expiration     : expiration date as "YYYY-MM-DD"
        contracts      : number of contracts (each covers 100 shares)
        purchase_price : per-share premium paid (ask), e.g. 4.10
        purchase_date  : date purchased as "YYYY-MM-DD"
        target_price   : expected stock price at profit target (optional)
        notes          : free-text notes (optional)
        owner          : whose position this is
        """
        kind = kind.lower()
        if kind not in ("call", "put"):
            raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")

        with closing(get_connection()) as conn:
            cur = conn.execute(
                """
                INSERT INTO options_positions
                    (owner, symbol, kind, strike, expiration, contracts,
                     purchase_price, purchase_date, target_price, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING position_id
                """,
                (owner, symbol.upper(), kind, strike, expiration, contracts,
                 purchase_price, purchase_date, target_price, notes),
            )
            pos_id = cur.fetchone()[0]
            conn.commit()
            return pos_id

    def close_position(
        self, position_id: int, reason: str = "closed", *, owner: str
    ) -> None:
        """Mark a position as CLOSED (manually sold or closed out)."""
        with closing(get_connection()) as conn:
            conn.execute(
                """
                UPDATE options_positions
                SET status = 'CLOSED',
                    closed_at = %s,
                    notes = COALESCE(notes || ' | ', '') || %s
                WHERE position_id = %s AND owner = %s
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    f"closed: {reason}",
                    position_id,
                    owner,
                ),
            )
            conn.commit()

    def expire_position(self, position_id: int, *, owner: str) -> None:
        """Mark a position as EXPIRED (option expiration date passed)."""
        with closing(get_connection()) as conn:
            conn.execute(
                """
                UPDATE options_positions
                SET status = 'EXPIRED', closed_at = %s
                WHERE position_id = %s AND owner = %s
                """,
                (datetime.now(timezone.utc).isoformat(), position_id, owner),
            )
            conn.commit()

    def auto_expire_past_positions(
        self, *, owner: str, now: datetime | None = None
    ) -> list[dict]:
        """
        Mark the owner's ACTIVE positions whose expiration date has passed as EXPIRED.
        Returns the list of positions that were expired.
        """
        today_str = market_date(now).isoformat()
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM options_positions
                WHERE status = 'ACTIVE' AND expiration < %s AND owner = %s
                """,
                (today_str, owner),
            ).fetchall()
            if rows:
                ids = [r["position_id"] for r in rows]
                conn.execute(
                    """
                    UPDATE options_positions
                    SET status = 'EXPIRED', closed_at = %s
                    WHERE position_id = ANY(%s) AND owner = %s
                    """,
                    [datetime.now(timezone.utc).isoformat(), ids, owner],
                )
                conn.commit()
            return [dict(r) for r in rows]

    def get_active_positions(self, *, owner: str) -> list[dict]:
        """Return the owner's ACTIVE positions ordered by expiration date."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM options_positions
                WHERE status = 'ACTIVE' AND owner = %s
                ORDER BY expiration ASC, symbol ASC
                """,
                (owner,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_position(self, position_id: int, *, owner: str) -> Optional[dict]:
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT * FROM options_positions WHERE position_id = %s AND owner = %s",
                (position_id, owner),
            ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Alert logic
    # ------------------------------------------------------------------

    def get_pending_alerts(
        self,
        current_prices: dict[str, float],
        *,
        owner: str,
        now: datetime | None = None,
    ) -> list[dict]:
        """
        Evaluate the owner's ACTIVE positions against current market prices and
        return a list of alert dicts.  The caller is responsible for
        deduplicating and sending.

        Each alert dict contains:
            position_id, symbol, kind, strike, expiration, contracts,
            purchase_price, target_price, alert_type, current_price,
            days_to_expiry, intrinsic_value, roi_pct, message

        alert_type values:
            ITM            — option just became in-the-money
            EXPIRATION_7D  — expiration ≤ 7 days away (fires every day)
            EXPIRATION_1D  — expiration ≤ 1 day away (fires every day)
            PROFIT_TARGET  — intrinsic value ≥ PROFIT_TARGET_MULTIPLIER × cost
        """
        today = market_date(now)
        alerts: list[dict] = []
        for pos in self.get_active_positions(owner=owner):
            current_price = current_prices.get(pos["symbol"])
            if current_price is None:
                continue
            alerts.extend(alerts_for_position(pos, current_price, today))
        return alerts

    # ------------------------------------------------------------------
    # Inventory helpers
    # ------------------------------------------------------------------

    def position_count(self, status: str = "ACTIVE", *, owner: str) -> int:
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM options_positions WHERE status = %s AND owner = %s",
                (status, owner),
            ).fetchone()
            return row[0]


# ----------------------------------------------------------------------
# Alert evaluation -- pure functions, no I/O (one position at a time)
# ----------------------------------------------------------------------

class _Valuation(NamedTuple):
    """One position valued at one price; ``base`` is the shared alert payload."""
    base: dict
    is_itm: bool
    intrinsic: float
    roi_pct: float
    total_cost: float
    total_intrinsic: float


def _value_position(pos: dict, current_price: float, today: date) -> Optional[_Valuation]:
    """Value ``pos`` at ``current_price``; None when its expiration doesn't parse."""
    try:
        expiry_date = date.fromisoformat(pos["expiration"])
    except ValueError:
        return None

    strike = pos["strike"]
    if pos["kind"] == "put":
        intrinsic = max(0.0, strike - current_price)
        is_itm = current_price <= strike
    else:  # call
        intrinsic = max(0.0, current_price - strike)
        is_itm = current_price >= strike

    cost_per_share = pos["purchase_price"]
    roi_pct = ((intrinsic - cost_per_share) / cost_per_share * 100) if cost_per_share > 0 else 0.0
    total_cost = cost_per_share * 100 * pos["contracts"]
    total_intrinsic = intrinsic * 100 * pos["contracts"]

    base = {
        "position_id":    pos["position_id"],
        "symbol":         pos["symbol"],
        "kind":           pos["kind"],
        "strike":         strike,
        "expiration":     pos["expiration"],
        "contracts":      pos["contracts"],
        "purchase_price": cost_per_share,
        "target_price":   pos["target_price"],
        "current_price":  current_price,
        "days_to_expiry": (expiry_date - today).days,
        "intrinsic_value": round(intrinsic, 2),
        "roi_pct":        round(roi_pct, 1),
        "total_cost":     round(total_cost, 2),
        "total_intrinsic": round(total_intrinsic, 2),
    }
    return _Valuation(base, is_itm, intrinsic, roi_pct, total_cost, total_intrinsic)


def _itm_alert(v: _Valuation) -> Optional[dict]:
    if not v.is_itm:
        return None
    b = v.base
    direction = "below" if b["kind"] == "put" else "above"
    return {
        **b,
        "alert_type": ALERT_ITM,
        "message": (
            f"{b['symbol']} ${b['strike']:.2f} {b['kind'].upper()} is IN THE MONEY  "
            f"(price ${b['current_price']:.2f} is {direction} strike ${b['strike']:.2f})  "
            f"intrinsic ${v.intrinsic:.2f}/share  "
            f"est. value ${v.total_intrinsic:.0f}  ROI {v.roi_pct:+.0f}%"
        ),
    }


def _expiration_alert(v: _Valuation) -> Optional[dict]:
    b = v.base
    days = b["days_to_expiry"]
    head = f"{b['symbol']} ${b['strike']:.2f} {b['kind'].upper()}"
    tail = (
        f"current price ${b['current_price']:.2f}  "
        f"intrinsic ${v.intrinsic:.2f}/share  "
    )
    if 0 <= days <= 1:
        label = "TOMORROW" if days == 1 else "TODAY"
        status = "ITM — consider closing" if v.is_itm else "OTM — will expire worthless"
        return {
            **b,
            "alert_type": ALERT_EXPIRATION_1D,
            "message": f"{head} EXPIRES {label} ({b['expiration']})  {tail}{status}",
        }
    if 2 <= days <= 7:
        return {
            **b,
            "alert_type": ALERT_EXPIRATION_7D,
            "message": (
                f"{head} expires in {days}d ({b['expiration']})  "
                f"{tail}{'ITM' if v.is_itm else 'OTM'}"
            ),
        }
    return None


def _profit_target_alert(v: _Valuation) -> Optional[dict]:
    """Intrinsic value at PROFIT_TARGET_MULTIPLIER x cost or more."""
    b = v.base
    cost_per_share = b["purchase_price"]
    if v.intrinsic <= 0 or cost_per_share <= 0:
        return None
    if v.intrinsic < PROFIT_TARGET_MULTIPLIER * cost_per_share:
        return None
    return {
        **b,
        "alert_type": ALERT_PROFIT_TARGET,
        "message": (
            f"{b['symbol']} ${b['strike']:.2f} {b['kind'].upper()} hit PROFIT TARGET  "
            f"intrinsic ${v.intrinsic:.2f} ≥ {PROFIT_TARGET_MULTIPLIER:.0f}× cost ${cost_per_share:.2f}  "
            f"est. value ${v.total_intrinsic:.0f} vs cost ${v.total_cost:.0f}  "
            f"ROI {v.roi_pct:+.0f}%"
        ),
    }


def alerts_for_position(pos: dict, current_price: float, today: date) -> list[dict]:
    """Every alert ``pos`` raises at ``current_price`` on ``today``, in the order
    ITM, expiration, profit target. A position can raise several at once."""
    v = _value_position(pos, current_price, today)
    if v is None:
        return []
    candidates = (_itm_alert(v), _expiration_alert(v), _profit_target_alert(v))
    return [alert for alert in candidates if alert is not None]
