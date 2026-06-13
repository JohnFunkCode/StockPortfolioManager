"""
options_position_store.py — Tracks active options positions and drives alert logic.

Addresses GitHub issue #12: "Enhance the notifier to send alerts related to
option orders. E.g. sell when in the money, expirations, etc."

Positions are added manually (or via CLI) and checked each time the notifier
runs.  The store surfaces three kinds of alerts:

  ITM          — option just crossed into the money
  EXPIRATION   — expiration is within 7 days (fires daily) or 1 day (fires daily)
  PROFIT_TARGET — estimated intrinsic value reached 2× the purchase price

Usage:
    from options_position_store import OptionsPositionStore

    store = OptionsPositionStore()
    pos_id = store.add_position(
        symbol="AMD", kind="put", strike=230.0, expiration="2026-05-16",
        contracts=1, purchase_price=4.10, purchase_date="2026-04-09",
        target_price=187.89,
    )

    alerts = store.get_pending_alerts(current_prices={"AMD": 215.0})
    store.close_position(pos_id, reason="sold")
"""

from contextlib import closing
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from quantcore.db import get_connection

# Alert type constants
ALERT_ITM           = "ITM"
ALERT_EXPIRATION_7D = "EXPIRATION_7D"
ALERT_EXPIRATION_1D = ALERT_EXPIRATION_1D = "EXPIRATION_1D"
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
        """
        kind = kind.lower()
        if kind not in ("call", "put"):
            raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")

        with closing(get_connection()) as conn:
            cur = conn.execute(
                """
                INSERT INTO options_positions
                    (symbol, kind, strike, expiration, contracts,
                     purchase_price, purchase_date, target_price, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING position_id
                """,
                (symbol.upper(), kind, strike, expiration, contracts,
                 purchase_price, purchase_date, target_price, notes),
            )
            pos_id = cur.fetchone()[0]
            conn.commit()
            return pos_id

    def close_position(self, position_id: int, reason: str = "closed") -> None:
        """Mark a position as CLOSED (manually sold or closed out)."""
        with closing(get_connection()) as conn:
            conn.execute(
                """
                UPDATE options_positions
                SET status = 'CLOSED',
                    closed_at = %s,
                    notes = COALESCE(notes || ' | ', '') || %s
                WHERE position_id = %s
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    f"closed: {reason}",
                    position_id,
                ),
            )
            conn.commit()

    def expire_position(self, position_id: int) -> None:
        """Mark a position as EXPIRED (option expiration date passed)."""
        with closing(get_connection()) as conn:
            conn.execute(
                """
                UPDATE options_positions
                SET status = 'EXPIRED', closed_at = %s
                WHERE position_id = %s
                """,
                (datetime.now(timezone.utc).isoformat(), position_id),
            )
            conn.commit()

    def auto_expire_past_positions(self) -> list[dict]:
        """
        Mark all ACTIVE positions whose expiration date has passed as EXPIRED.
        Returns the list of positions that were expired.
        """
        today_str = date.today().isoformat()
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM options_positions
                WHERE status = 'ACTIVE' AND expiration < ?
                """,
                (today_str,),
            ).fetchall()
            if rows:
                ids = [r["position_id"] for r in rows]
                conn.execute(
                    """
                    UPDATE options_positions
                    SET status = 'EXPIRED', closed_at = %s
                    WHERE position_id = ANY(%s)
                    """,
                    [datetime.now(timezone.utc).isoformat(), ids],
                )
                conn.commit()
            return [dict(r) for r in rows]

    def get_active_positions(self) -> list[dict]:
        """Return all ACTIVE positions ordered by expiration date."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM options_positions
                WHERE status = 'ACTIVE'
                ORDER BY expiration ASC, symbol ASC
                """,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_position(self, position_id: int) -> Optional[dict]:
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT * FROM options_positions WHERE position_id = ?",
                (position_id,),
            ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Alert logic
    # ------------------------------------------------------------------

    def get_pending_alerts(self, current_prices: dict[str, float]) -> list[dict]:
        """
        Evaluate every ACTIVE position against current market prices and
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
        today = date.today()
        alerts = []

        for pos in self.get_active_positions():
            symbol      = pos["symbol"]
            kind        = pos["kind"]
            strike      = pos["strike"]
            expiry_str  = pos["expiration"]
            contracts   = pos["contracts"]
            purch_price = pos["purchase_price"]
            target      = pos["target_price"]

            current_price = current_prices.get(symbol)
            if current_price is None:
                continue

            try:
                expiry_date = date.fromisoformat(expiry_str)
            except ValueError:
                continue

            days_to_expiry = (expiry_date - today).days

            # Intrinsic value per share at current price
            if kind == "put":
                intrinsic = max(0.0, strike - current_price)
                is_itm    = current_price <= strike
            else:  # call
                intrinsic = max(0.0, current_price - strike)
                is_itm    = current_price >= strike

            cost_per_share = purch_price
            roi_pct = ((intrinsic - cost_per_share) / cost_per_share * 100) if cost_per_share > 0 else 0.0
            total_cost     = purch_price * 100 * contracts
            total_intrinsic = intrinsic * 100 * contracts

            base = {
                "position_id":    pos["position_id"],
                "symbol":         symbol,
                "kind":           kind,
                "strike":         strike,
                "expiration":     expiry_str,
                "contracts":      contracts,
                "purchase_price": purch_price,
                "target_price":   target,
                "current_price":  current_price,
                "days_to_expiry": days_to_expiry,
                "intrinsic_value": round(intrinsic, 2),
                "roi_pct":        round(roi_pct, 1),
                "total_cost":     round(total_cost, 2),
                "total_intrinsic": round(total_intrinsic, 2),
            }

            # --- Alert: In The Money ---
            if is_itm:
                direction = "below" if kind == "put" else "above"
                alerts.append({
                    **base,
                    "alert_type": ALERT_ITM,
                    "message": (
                        f"{symbol} ${strike:.2f} {kind.upper()} is IN THE MONEY  "
                        f"(price ${current_price:.2f} is {direction} strike ${strike:.2f})  "
                        f"intrinsic ${intrinsic:.2f}/share  "
                        f"est. value ${total_intrinsic:.0f}  ROI {roi_pct:+.0f}%"
                    ),
                })

            # --- Alert: Expiration warnings ---
            if 0 <= days_to_expiry <= 1:
                label = "TOMORROW" if days_to_expiry == 1 else "TODAY"
                alerts.append({
                    **base,
                    "alert_type": ALERT_EXPIRATION_1D,
                    "message": (
                        f"{symbol} ${strike:.2f} {kind.upper()} EXPIRES {label} ({expiry_str})  "
                        f"current price ${current_price:.2f}  "
                        f"intrinsic ${intrinsic:.2f}/share  "
                        f"{'ITM — consider closing' if is_itm else 'OTM — will expire worthless'}"
                    ),
                })
            elif 2 <= days_to_expiry <= 7:
                alerts.append({
                    **base,
                    "alert_type": ALERT_EXPIRATION_7D,
                    "message": (
                        f"{symbol} ${strike:.2f} {kind.upper()} expires in {days_to_expiry}d ({expiry_str})  "
                        f"current price ${current_price:.2f}  "
                        f"intrinsic ${intrinsic:.2f}/share  "
                        f"{'ITM' if is_itm else 'OTM'}"
                    ),
                })

            # --- Alert: Profit target (intrinsic ≥ 2× cost) ---
            if intrinsic > 0 and cost_per_share > 0:
                if intrinsic >= PROFIT_TARGET_MULTIPLIER * cost_per_share:
                    alerts.append({
                        **base,
                        "alert_type": ALERT_PROFIT_TARGET,
                        "message": (
                            f"{symbol} ${strike:.2f} {kind.upper()} hit PROFIT TARGET  "
                            f"intrinsic ${intrinsic:.2f} ≥ {PROFIT_TARGET_MULTIPLIER:.0f}× cost ${cost_per_share:.2f}  "
                            f"est. value ${total_intrinsic:.0f} vs cost ${total_cost:.0f}  "
                            f"ROI {roi_pct:+.0f}%"
                        ),
                    })

        return alerts

    # ------------------------------------------------------------------
    # Inventory helpers
    # ------------------------------------------------------------------

    def position_count(self, status: str = "ACTIVE") -> int:
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM options_positions WHERE status = ?",
                (status,),
            ).fetchone()
            return row[0]
