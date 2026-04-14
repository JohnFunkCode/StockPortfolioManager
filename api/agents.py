"""
Agent data endpoints — read-only views into agent_signals and agent_recommendations.

Blueprint routes:
  GET /api/agents/signals
    Returns recent agent signals for the authenticated tenant.
    Query params:
      symbol    — filter to one symbol (optional)
      direction — 'buy' | 'sell' | 'neutral' (optional)
      days      — look-back window, default 30
      limit     — max rows, default 50

  GET /api/agents/recommendations
    Returns recent deep analysis recommendations for the authenticated tenant.
    Query params:
      symbol — filter to one symbol (optional)
      limit  — max rows, default 20

  GET /api/agents/health
    Returns circuit breaker states and market open status.
    No authentication required — used by the dashboard health widget.
"""
from datetime import datetime

from flask import Blueprint, g, jsonify, request
from sqlalchemy import text

from agents.circuit_breaker import breaker_status, is_market_open
from agents.structured_logging import get_logger
from api.middleware import require_auth
from db.database import get_db

log = get_logger(__name__)

agents_bp = Blueprint("agents_api", __name__)


@agents_bp.route("/api/agents/signals", methods=["GET"])
@require_auth
def list_signals():
    """
    Return recent agent_signals rows for the current tenant, newest first.
    """
    tenant_id = g.user["tenant_id"]
    symbol    = request.args.get("symbol", "").upper() or None
    direction = request.args.get("direction", "").lower() or None
    days      = int(request.args.get("days", 30))
    limit     = min(int(request.args.get("limit", 50)), 200)

    if direction and direction not in ("buy", "sell", "neutral"):
        return jsonify({"error": "direction must be buy, sell, or neutral"}), 400

    filters = [
        "tenant_id = :tid",
        "fired_at  >= NOW() - (:days * INTERVAL '1 day')",
    ]
    params: dict = {"tid": tenant_id, "days": days, "limit": limit}

    if symbol:
        filters.append("symbol = :sym")
        params["sym"] = symbol
    if direction:
        filters.append("direction = :dir")
        params["dir"] = direction

    where = " AND ".join(filters)
    sql = text(f"""
        SELECT id, symbol, score, direction, triggers, escalated, fired_at
        FROM agent_signals
        WHERE {where}
        ORDER BY fired_at DESC
        LIMIT :limit
    """)

    try:
        with get_db(tenant_id=tenant_id) as conn:
            rows = conn.execute(sql, params).mappings().all()
    except Exception as exc:
        log.error("Failed to load signals", extra={"error": str(exc)})
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "signals": [
            {
                "id":        str(r["id"]),
                "symbol":    r["symbol"],
                "score":     r["score"],
                "direction": r["direction"],
                "triggers":  r["triggers"] or {},
                "escalated": r["escalated"],
                "fired_at":  r["fired_at"].isoformat() if r["fired_at"] else None,
            }
            for r in rows
        ],
        "count": len(rows),
    })


@agents_bp.route("/api/agents/recommendations", methods=["GET"])
@require_auth
def list_recommendations():
    """
    Return recent agent_recommendations rows for the current tenant, newest first.
    """
    tenant_id = g.user["tenant_id"]
    symbol    = request.args.get("symbol", "").upper() or None
    limit     = min(int(request.args.get("limit", 20)), 100)

    filters = ["tenant_id = :tid"]
    params: dict = {"tid": tenant_id, "limit": limit}

    if symbol:
        filters.append("symbol = :sym")
        params["sym"] = symbol

    where = " AND ".join(filters)
    sql = text(f"""
        SELECT id, symbol, recommendation, conviction,
               entry_low, entry_high, price_target, stop_loss,
               details, fired_at
        FROM agent_recommendations
        WHERE {where}
        ORDER BY fired_at DESC
        LIMIT :limit
    """)

    try:
        with get_db(tenant_id=tenant_id) as conn:
            rows = conn.execute(sql, params).mappings().all()
    except Exception as exc:
        log.error("Failed to load recommendations", extra={"error": str(exc)})
        return jsonify({"error": str(exc)}), 500

    def _float(v):
        return float(v) if v is not None else None

    return jsonify({
        "recommendations": [
            {
                "id":             str(r["id"]),
                "symbol":         r["symbol"],
                "recommendation": r["recommendation"],
                "conviction":     r["conviction"],
                "entry_low":      _float(r["entry_low"]),
                "entry_high":     _float(r["entry_high"]),
                "price_target":   _float(r["price_target"]),
                "stop_loss":      _float(r["stop_loss"]),
                "details":        r["details"] or {},
                "fired_at":       r["fired_at"].isoformat() if r["fired_at"] else None,
            }
            for r in rows
        ],
        "count": len(rows),
    })


@agents_bp.route("/api/agents/health", methods=["GET"])
def agents_health():
    """
    Agent system health — no authentication required.

    Returns market open status and per-tool circuit breaker states.
    Used by the dashboard health widget and uptime monitors.
    """
    return jsonify({
        "market_open":      is_market_open(),
        "circuit_breakers": breaker_status(),
        "timestamp":        datetime.now().isoformat(),
    })
