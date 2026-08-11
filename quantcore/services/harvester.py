"""HarvesterService — harvest-ladder plan scanning, alerts, and execution.

Business logic extracted from experiments/HarvesterPlanStore.py (Phase 1
Step 6). Plan persistence (SQL) lives in
quantcore.repositories.harvester_repository.HarvesterPlanDB; this service wraps
that repository and absorbs the former ``HarvesterController`` behaviour
(``get_next_actions``, ``scan_and_fire_alerts``, ``record_execution``).

Adapters — the Flask Harvester routes in ``api/app.py``, ``notifier.py``, and
``main.py`` — call ``get_services().harvester.<method>``; the methods exposed
here preserve the previous call signatures and return shapes verbatim so
behaviour is unchanged.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any, Dict, List, Optional

from quantcore.db import get_connection
from quantcore.repositories.harvester_repository import (
    HarvesterPlanDB,
    PlanBuildParams,
    SQL_GET_ACTIVE_ALERT_FOR_RUNG,
    SQL_GET_NEXT_PENDING_RUNG,
    SQL_GET_RUNG_INSTANCE,
    SQL_LIST_ACTIVE_PLANS,
    SQL_MARK_ALERT_FIRED,
    SQL_MARK_RUNG_ACHIEVED,
    SQL_MARK_RUNG_EXECUTED,
    _utc_now_iso,
)


class HarvesterService:
    def __init__(
        self,
        harvester_repository: HarvesterPlanDB,
        yfinance_gateway=None,
        portfolio_repository=None,
    ) -> None:
        self._repo = harvester_repository
        # Price fetching goes through the gateway (single fetch seam, #74);
        # the repository only persists and queries.
        self._yf = yfinance_gateway
        # PortfolioRepository — the holding invariant (issue #147 Part H5)
        # needs to know whether the owner still holds the symbol. It is the
        # *repository*, not PortfolioService: PortfolioService already composes
        # this repository for the exit half of the same invariant, and services
        # in both directions would be a construction cycle. Optional so a
        # unit test can build the service without the portfolio stack; when it
        # is absent the invariant is not enforced.
        self._portfolio_repo = portfolio_repository

    # ------------------------------------------------------------------
    # Plan CRUD / queries (passthrough to the repository)
    # ------------------------------------------------------------------
    def build_plan(
        self,
        symbol: str,
        template_name: str,
        params: PlanBuildParams,
        *,
        owner: str,
    ) -> Dict[str, Any]:
        symbol = symbol.upper().strip()
        self._require_open_lot(symbol, owner=owner)
        days = max(params.history_window_days + 60, 420)
        bars = self._yf.fetch_history(
            symbol, "1d", days, auto_adjust=False, include_adj_close=True
        )
        if not bars.empty and "Adj Close" not in bars.columns:
            bars = bars.copy()
            bars["Adj Close"] = bars["Close"]
        return self._repo.build_plan(
            symbol=symbol, template_name=template_name, params=params, bars=bars,
            owner=owner,
        )

    def _open_symbols(self, owner: str) -> Optional[set]:
        """The symbols `owner` currently holds at least one OPEN lot of.

        ``None`` — not an empty set — when no portfolio repository was
        injected: "we cannot tell" and "they hold nothing" are different
        answers, and conflating them would flag every plan as an orphan.
        """
        if self._portfolio_repo is None:
            return None
        return {
            (row.get("symbol") or "").upper()
            for row in self._portfolio_repo.list_positions(owner, status="OPEN")
            if row.get("symbol")
        }

    def _require_open_lot(self, symbol: str, *, owner: str) -> None:
        """The entry half of the holding invariant (issue #147 Part H5).

        A harvest ladder sells shares as the price rises; building one for a
        symbol the owner does not hold produces alerts that can never be
        executed. RuntimeError because ``POST /api/plans`` already turns that
        into a 422 — the request is well-formed, the portfolio just doesn't
        support it.

        Skipped when no portfolio repository was injected (a bare unit-test
        construction); the registry always injects one.
        """
        held = self._open_symbols(owner)
        if held is None:
            return
        if symbol.upper() not in held:
            raise RuntimeError(
                f"No open position in {symbol} for {owner} — "
                "a harvest plan needs shares to sell"
            )

    def close_active_plan_for_symbol(
        self, symbol: str, *, owner: str, reason: Optional[str] = None
    ) -> int:
        return self._repo.close_active_plan_for_symbol(symbol, owner=owner, reason=reason)

    def active_plan_ids(self, *, owner: str) -> Dict[str, int]:
        return self._repo.active_plan_ids(owner=owner)

    def display_all_plans(self, status: str = "ACTIVE", *, owner: str) -> List[Dict[str, Any]]:
        """One owner's plans, each flagged with whether they still hold the symbol.

        ``in_portfolio`` is the orphan flag (issue #147 Part H7): the exit seam
        that closes a plan when the last lot goes runs on its own connection
        after the sale commits, so a crash in between leaves an ACTIVE plan
        with nothing under it. This surfaces that state loudly rather than
        letting it fire alerts unnoticed.

        ``None`` where no portfolio repository was injected and the holdings
        therefore cannot be read: "we don't know" is not "they sold out of it",
        and the flag must stay quiet on the difference.
        """
        plans = self._repo.display_all_plans(status=status, owner=owner)
        held = self._open_symbols(owner)
        for plan in plans:
            plan["in_portfolio"] = (
                None if held is None
                else (plan.get("symbol") or "").upper() in held
            )
        return plans

    def get_plan_by_id(self, instance_id: int, *, owner: str) -> Optional[Dict[str, Any]]:
        return self._repo.get_plan_by_id(instance_id, owner=owner)

    def get_rungs_for_plan(self, instance_id: int, *, owner: str) -> List[Dict[str, Any]]:
        return self._repo.get_rungs_for_plan(instance_id, owner=owner)

    def update_plan_metadata(
        self,
        instance_id: int,
        notes: Optional[str] = None,
        metadata_json: Optional[str] = None,
        *,
        owner: str,
    ) -> int:
        return self._repo.update_plan_metadata(
            instance_id, notes=notes, metadata_json=metadata_json, owner=owner
        )

    def delete_plan(self, instance_id: int, *, owner: str) -> int:
        return self._repo.delete_plan(instance_id, owner=owner)

    def get_rung_by_id(self, rung_id: int, *, owner: str) -> Optional[Dict[str, Any]]:
        return self._repo.get_rung_by_id(rung_id, owner=owner)

    def get_alerts_for_plan(self, instance_id: int, *, owner: str) -> List[Dict[str, Any]]:
        return self._repo.get_alerts_for_plan(instance_id, owner=owner)

    def purge_superseded_plans(self, *, owner: str) -> int:
        return self._repo.purge_superseded_plans(owner=owner)

    # ------------------------------------------------------------------
    # Symbols / prices / dashboard
    # ------------------------------------------------------------------
    def list_all_symbols(self, *, owner: str) -> List[Dict[str, Any]]:
        return self._repo.list_all_symbols(owner=owner)

    def get_dashboard_stats(self, *, owner: str) -> Dict[str, Any]:
        return self._repo.get_dashboard_stats(owner=owner)

    def poll_latest_close(self, ticker: str) -> Optional[float]:
        return self._latest_close(ticker)

    def _latest_close(self, ticker: str) -> Optional[float]:
        try:
            df = self._yf.fetch_history(ticker, "1d", 7)
            if df is None or df.empty:
                return None
            closes = df["Close"].dropna()
            return float(closes.iloc[-1]) if len(closes) else None
        except Exception:
            return None

    def symbols_at_harvest_points(self, *, owner: str) -> List[Dict[str, Any]]:
        return self._repo.symbols_at_harvest_points(
            price_lookup=self._latest_close, owner=owner
        )

    # ------------------------------------------------------------------
    # Notifier integration (per-symbol hit checks)
    # ------------------------------------------------------------------
    def harvest_hit_for_symbol(
        self, symbol: str, current_price: float, *, owner: str
    ) -> List[Dict[str, Any]]:
        return self._repo.harvest_hit_for_symbol(
            symbol=symbol, current_price=current_price, owner=owner
        )

    def mark_rungs_achieved(
        self,
        rung_ids: List[int],
        trigger_price: float,
        triggered_at: Optional[str] = None,
        *,
        owner: str,
    ) -> int:
        return self._repo.mark_rungs_achieved(
            rung_ids=rung_ids,
            trigger_price=trigger_price,
            triggered_at=triggered_at,
            owner=owner,
        )

    # ------------------------------------------------------------------
    # Controller behaviour (relocated from HarvesterController)
    # ------------------------------------------------------------------
    def get_next_actions(self, *, owner: str) -> List[Dict[str, Any]]:
        """Return the next pending rung for each of `owner`'s ACTIVE plans."""
        actions: List[Dict[str, Any]] = []
        with closing(get_connection()) as conn:
            active = conn.execute(SQL_LIST_ACTIVE_PLANS, {"owner": owner}).fetchall()

        for row in active:
            instance_id = int(row["instance_id"])
            with closing(get_connection()) as conn:
                rung = conn.execute(
                    SQL_GET_NEXT_PENDING_RUNG, {"instance_id": instance_id, "owner": owner}
                ).fetchone()
            if not rung:
                continue
            actions.append({
                "instance_id": instance_id,
                "rung_id": int(rung["rung_id"]),
                "rung_index": int(rung["rung_index"]),
                "target_price": float(rung["target_price"]),
                "shares_to_sell": int(rung["shares_sold_planned"]),
            })
        return actions

    def scan_and_fire_alerts(self, *, owner: str) -> List[Dict[str, Any]]:
        """Poll prices for `owner`'s active plans, and mark alert/rung when target is reached.

        Returns a list of achieved rungs with context for execution.
        """
        fired: List[Dict[str, Any]] = []
        now = _utc_now_iso()

        with closing(get_connection()) as conn:
            active = conn.execute(SQL_LIST_ACTIVE_PLANS, {"owner": owner}).fetchall()

        for row in active:
            instance_id = int(row["instance_id"])
            ticker = row["ticker"]

            with closing(get_connection()) as conn:
                rung = conn.execute(
                    SQL_GET_NEXT_PENDING_RUNG, {"instance_id": instance_id, "owner": owner}
                ).fetchone()
            if not rung:
                continue

            target_price = float(rung["target_price"])
            rung_id = int(rung["rung_id"])
            shares_to_sell = int(rung["shares_sold_planned"])

            current_price = self._latest_close(ticker)
            if current_price is None or current_price < target_price:
                continue

            with closing(get_connection()) as conn:
                try:
                    alert = conn.execute(
                        SQL_GET_ACTIVE_ALERT_FOR_RUNG,
                        {"rung_id": rung_id, "owner": owner},
                    ).fetchone()
                    if alert:
                        conn.execute(SQL_MARK_ALERT_FIRED, {
                            "alert_id": int(alert["alert_id"]),
                            "ts": now,
                            "price": current_price,
                        })
                    conn.execute(SQL_MARK_RUNG_ACHIEVED, {
                        "rung_id": rung_id,
                        "ts": now,
                        "price": current_price,
                        "owner": owner,
                    })
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

            fired.append({
                "symbol": ticker,
                "instance_id": instance_id,
                "rung_id": rung_id,
                "shares_to_sell": shares_to_sell,
                "current_price": current_price,
                "target_price": target_price,
            })

        return fired

    def record_execution(
        self,
        rung_id: int,
        executed_price: float,
        shares_sold: int,
        tax_paid: float = 0.0,
        executed_at: Optional[str] = None,
        notes: Optional[str] = None,
        *,
        owner: str,
    ) -> None:
        """Record the actual execution of a triggered rung and refresh the next-rung alert.

        A rung on another owner's plan matches nothing: the UPDATE affects no
        row and the follow-up instance lookup comes back empty, so the call is
        a no-op rather than a cross-owner write.
        """
        ts = executed_at or _utc_now_iso()
        gross = executed_price * shares_sold
        net = gross - tax_paid

        with closing(get_connection()) as conn:
            try:
                conn.execute(SQL_MARK_RUNG_EXECUTED, {
                    "rung_id": rung_id,
                    "ts": ts,
                    "price": executed_price,
                    "shares_sold": shares_sold,
                    "tax_paid": tax_paid,
                    "net_harvest": net,
                    "owner": owner,
                })
                if notes:
                    conn.execute(
                        """
                        UPDATE plan_rungs SET notes = :notes
                        WHERE rung_id = :rung_id
                          AND instance_id IN (
                              SELECT instance_id FROM plan_instances WHERE owner = :owner
                          );
                        """,
                        {"notes": notes, "rung_id": rung_id, "owner": owner},
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        with closing(get_connection()) as conn:
            row = conn.execute(
                SQL_GET_RUNG_INSTANCE, {"rung_id": rung_id, "owner": owner}
            ).fetchone()
        if row:
            self._repo._ensure_next_rung_alert(int(row["instance_id"]), owner=owner)
