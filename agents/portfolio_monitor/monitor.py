"""
Portfolio Monitor — daily health check for all open positions.

Runs two Cloud Scheduler jobs per tenant (0935 ET and 1555 ET):
  • Per-position health check across 7 indicators
  • Morning/closing report via AgentNotifier.send_morning_report()
  • AT RISK and INSTITUTIONAL EXIT alerts via AgentNotifier.send_portfolio_alert()
  • AT RISK and INST. EXIT signals persisted to agent_signals for Pub/Sub escalation (Phase 6)

Alert classification:
  AT RISK          — price within 3% of technical stop (→ Discord alert + agent_signals)
  DRAWDOWN WARNING — position loss > 75% of historical trailing stop % (→ report only)
  TREND DEGRADING  — 3+ consecutive sessions below VWAP (→ report only)
  INST. EXIT       — dark pool net_signal == "distribution" (→ Discord alert + agent_signals)
  SQUEEZE WATCH    — short float > 15% with MEDIUM/HIGH squeeze potential (→ report only)
  CAPITULATION     — bid/ask spread narrowing from elevated (→ report only)
  MM BIAS REVERSAL — DAOI mm_hedge_bias == "sell_on_rally" (→ report only)

NOTE: Tool functions are imported directly from fastMCPTest/ (monorepo).
      In production each tool runs as a separate Cloud Run MCP server.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "fastMCPTest"))

from market_analysis_server import get_bid_ask_spread, get_dark_pool  # noqa: E402
from stock_price_server import (  # noqa: E402
    get_delta_adjusted_oi,
    get_stop_loss_analysis,
)

from agents.agent_notifier import AgentNotifier
from agents.pubsub import publish_escalation
from db.database import get_db

# ---------------------------------------------------------------------------
# Per-position health check
# ---------------------------------------------------------------------------

def _check_position(position: dict) -> dict:
    """
    Run health checks on one open position.

    `position` must have: symbol, purchase_price, quantity.

    Returns a dict with:
      symbol, price, cost_basis, alerts (list), raw (tool outputs)
    """
    symbol     = position["symbol"]
    cost_basis = float(position["purchase_price"])
    quantity   = float(position["quantity"])

    alerts: list[dict] = []
    raw: dict[str, Any] = {}

    # ── 1–3 + short_interest: get_stop_loss_analysis bundles price, VWAP,
    #    MACD, RSI, DAOI gamma wall, historical drawdown, and short interest
    # ────────────────────────────────────────────────────────────────────────
    try:
        stop = get_stop_loss_analysis(
            symbol,
            cost_basis=cost_basis,
            shares=int(quantity),
        )
        raw["stop"] = stop
        price = stop["price"]

        # ── 1. AT RISK: price within 3% of technical stop ─────────────────
        tech_dist_pct = stop["stops"]["technical_stop_distance_pct"]
        tech_stop     = stop["stops"]["technical_stop"]
        if tech_dist_pct < 0 and abs(tech_dist_pct) < 3.0:
            alerts.append({
                "type":       "at_risk",
                "price":      price,
                "stop":       tech_stop,
                "gap_pct":    round(abs(tech_dist_pct), 2),
                "detail":     (
                    f"Price ${price:.2f} within {abs(tech_dist_pct):.1f}% "
                    f"of technical stop ${tech_stop:.2f}"
                ),
            })

        # ── 2. DRAWDOWN WARNING: current loss > 75% of trailing stop pct ──
        trailing_pct = stop["drawdown"]["base_trailing_stop_pct"]
        if cost_basis > 0 and price < cost_basis:
            loss_pct = abs((price - cost_basis) / cost_basis * 100)
            if loss_pct > 0.75 * trailing_pct:
                alerts.append({
                    "type":   "drawdown_warning",
                    "detail": (
                        f"Position down {loss_pct:.1f}% from cost basis ${cost_basis:.2f} — "
                        f"exceeds 75% of the {trailing_pct:.1f}% historical noise floor"
                    ),
                })

        # ── 3. TREND DEGRADING: 3+ sessions below VWAP ───────────────────
        tech     = stop["technical"]
        if not tech["above_vwap"]:
            bars_below = tech["consecutive_bars"]
            if bars_below >= 3:
                alerts.append({
                    "type":   "trend_degrading",
                    "detail": (
                        f"{bars_below} consecutive sessions below VWAP "
                        f"${tech['vwap']:.2f}"
                    ),
                })

        # ── 5. SQUEEZE WATCH: short float > 15% + MEDIUM/HIGH squeeze ─────
        si = stop["short_interest"]
        if (
            si.get("data_available")
            and si["short_float_pct"] > 15
            and si["squeeze_potential"] in ("MEDIUM", "HIGH")
        ):
            alerts.append({
                "type":   "squeeze_watch",
                "detail": (
                    f"Short float {si['short_float_pct']:.1f}%, "
                    f"{si['short_ratio_days']:.1f} days-to-cover — "
                    f"{si['squeeze_potential']} squeeze potential"
                ),
            })

    except Exception as exc:
        raw["stop_error"] = str(exc)
        price = 0.0

    # ── 4. INSTITUTIONAL EXIT: dark pool distribution signal ───────────────
    try:
        dp = get_dark_pool(symbol)
        raw["dark_pool"] = dp
        if dp["net_signal"] == "distribution":
            alerts.append({
                "type":   "inst_exit",
                "detail": (
                    f"Dark pool: distribution signal — "
                    f"{dp['absorption_count']} absorption event(s) on up days"
                ),
            })
    except Exception as exc:
        raw["dark_pool_error"] = str(exc)

    # ── 6. CAPITULATION BOTTOM: spread narrowing from elevated ─────────────
    try:
        spread = get_bid_ask_spread(symbol)
        raw["spread"] = spread
        if spread["spread_vs_norm"] == "narrowing":
            alerts.append({
                "type":   "capitulation",
                "detail": spread["bottom_note"],
            })
    except Exception as exc:
        raw["spread_error"] = str(exc)

    # ── 7. MM BIAS REVERSAL: DAOI sell_on_rally ────────────────────────────
    try:
        daoi = get_delta_adjusted_oi(symbol)
        raw["daoi"] = daoi
        if daoi.get("mm_hedge_bias") == "sell_on_rally":
            alerts.append({
                "type":   "mm_bias_reversal",
                "detail": daoi.get("mm_note", "MM net long delta — sells on rallies"),
            })
    except Exception as exc:
        raw["daoi_error"] = str(exc)

    return {
        "symbol":     symbol,
        "price":      price,
        "cost_basis": cost_basis,
        "alerts":     alerts,
        "raw":        raw,
    }


# ---------------------------------------------------------------------------
# Signal persistence
# ---------------------------------------------------------------------------

def _write_signal(
    tenant_id: str,
    symbol: str,
    alert_type: str,
    detail: str,
) -> None:
    """Persist a fired AT RISK or INST. EXIT signal to agent_signals."""
    with get_db(tenant_id=tenant_id) as conn:
        conn.execute(
            text("""
                INSERT INTO agent_signals (tenant_id, symbol, score, direction, triggers)
                VALUES (:tid, :sym, 0, 'neutral', :triggers::jsonb)
            """),
            {
                "tid":      tenant_id,
                "sym":      symbol,
                "triggers": json.dumps([detail]),
            },
        )


# ---------------------------------------------------------------------------
# Tenant-level scan
# ---------------------------------------------------------------------------

def _load_positions(tenant_id: str) -> list[dict]:
    """Fetch open positions for a tenant (sale_date IS NULL)."""
    with get_db(tenant_id=tenant_id) as conn:
        rows = conn.execute(
            text(
                "SELECT symbol, purchase_price, quantity "
                "FROM positions "
                "WHERE tenant_id = :tid AND sale_date IS NULL"
            ),
            {"tid": tenant_id},
        ).mappings().fetchall()
    return [dict(r) for r in rows]


def monitor_tenant(tenant_id: str, report_type: str = "Morning") -> dict:
    """
    Run a full portfolio health check for one tenant and send alerts + report.

    `report_type` — "Morning" (0935 ET) or "Closing" (1555 ET).

    Returns a summary dict with alert counts.
    """
    positions = _load_positions(tenant_id)

    if not positions:
        print(
            f"{datetime.now():%Y-%m-%d %H:%M:%S} "
            f"[{tenant_id[:8]}] No open positions."
        )
        return {"tenant_id": tenant_id, "positions": 0, "alerts": 0}

    notifier = AgentNotifier(tenant_id)

    # Accumulators for the report
    at_risk_items:   list[dict] = []
    degrading_items: list[dict] = []
    squeeze_items:   list[dict] = []
    cap_items:       list[dict] = []
    escalated:       list[str]  = []

    for pos in positions:
        symbol = pos["symbol"]
        print(
            f"{datetime.now():%Y-%m-%d %H:%M:%S} "
            f"[{tenant_id[:8]}] Checking {symbol}..."
        )
        try:
            result = _check_position(pos)
        except Exception as exc:
            print(f"{datetime.now():%Y-%m-%d %H:%M:%S} Error checking {symbol}: {exc}")
            continue

        for alert in result["alerts"]:
            atype  = alert["type"]
            detail = alert["detail"]

            if atype == "at_risk":
                at_risk_items.append({
                    "symbol": symbol,
                    "price":  result["price"],
                    "stop":   alert["stop"],
                    "gap_pct": alert["gap_pct"],
                })
                # Per-position Discord alert + persist signal
                notifier.send_portfolio_alert(
                    alert_type="portfolio_at_risk",
                    symbol=symbol,
                    details={"detail": detail, "escalate": True},
                )
                try:
                    _write_signal(tenant_id, symbol, "portfolio_at_risk", detail)
                except Exception as exc:
                    print(
                        f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                        f"Failed to persist at_risk signal for {symbol}: {exc}"
                    )
                publish_escalation(tenant_id, symbol, "portfolio_monitor", priority="P1")
                escalated.append(symbol)

            elif atype == "inst_exit":
                notifier.send_portfolio_alert(
                    alert_type="portfolio_inst_exit",
                    symbol=symbol,
                    details={"detail": detail, "escalate": True},
                )
                try:
                    _write_signal(tenant_id, symbol, "portfolio_inst_exit", detail)
                except Exception as exc:
                    print(
                        f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                        f"Failed to persist inst_exit signal for {symbol}: {exc}"
                    )
                publish_escalation(tenant_id, symbol, "portfolio_monitor", priority="P2")
                escalated.append(symbol)

            elif atype == "trend_degrading":
                degrading_items.append({"symbol": symbol, "detail": detail})

            elif atype == "squeeze_watch":
                squeeze_items.append({"symbol": symbol, "detail": detail})

            elif atype == "capitulation":
                cap_items.append({"symbol": symbol, "detail": detail})

            # drawdown_warning and mm_bias_reversal go to report only (no per-item alert)

    # ── Morning / Closing Report ───────────────────────────────────────────
    report = {
        "type":                  report_type,
        "at_risk":               at_risk_items,
        "trend_degrading":       degrading_items,
        "squeeze_watch":         squeeze_items,
        "capitulation":          cap_items,
        "watchlist_opportunities": [],   # Phase 6: add analyze_options_watchlist
        "escalated":             list(dict.fromkeys(escalated)),  # dedupe, preserve order
    }
    notifier.send_morning_report(report)

    total_alerts = len(at_risk_items) + len(degrading_items) + len(squeeze_items) + len(cap_items)
    print(
        f"{datetime.now():%Y-%m-%d %H:%M:%S} "
        f"[{tenant_id[:8]}] {report_type} report sent — "
        f"{len(positions)} positions, {total_alerts} alerts, "
        f"{len(escalated)} escalated"
    )

    return {
        "tenant_id":       tenant_id,
        "report_type":     report_type,
        "positions_checked": len(positions),
        "at_risk":         len(at_risk_items),
        "degrading":       len(degrading_items),
        "squeeze_watch":   len(squeeze_items),
        "capitulation":    len(cap_items),
        "escalated":       list(dict.fromkeys(escalated)),
    }
