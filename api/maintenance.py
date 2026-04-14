"""
Maintenance endpoints — data retention and system health.

Blueprint routes:
  POST /maintenance/prune
    Deletes expired records across all tenants:
      agent_signals older than 90 days
      agent_recommendations older than 1 year
    Requires admin role. Cloud Scheduler calls this nightly.

  GET /maintenance/health
    Returns current circuit breaker state and Deep Analysis semaphore count.
    No auth required — used by Cloud Run health checks and uptime monitors.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import text

from agents.circuit_breaker import breaker_status, is_market_open
from agents.structured_logging import get_logger
from api.middleware import require_role
from db.database import get_db

log = get_logger(__name__)

maintenance_bp = Blueprint("maintenance", __name__)

# Retention windows (days)
_SIGNALS_RETENTION_DAYS          = 90
_RECOMMENDATIONS_RETENTION_DAYS  = 365


@maintenance_bp.route("/maintenance/prune", methods=["POST"])
@require_role("admin")
def prune():
    """
    Delete expired agent data across all tenants.

    Intended to be called by a nightly Cloud Scheduler job.
    Also callable manually by an admin user.

    Returns row counts for each pruned table.
    """
    started_at = datetime.now()

    deleted = {}
    try:
        # Prune without RLS — maintenance runs as a superuser query.
        # get_db() with no tenant_id skips SET LOCAL app.tenant_id.
        with get_db() as conn:
            r1 = conn.execute(
                text(
                    "DELETE FROM agent_signals "
                    "WHERE fired_at < NOW() - (:days * INTERVAL '1 day')"
                ),
                {"days": _SIGNALS_RETENTION_DAYS},
            )
            deleted["agent_signals"] = r1.rowcount

            r2 = conn.execute(
                text(
                    "DELETE FROM agent_recommendations "
                    "WHERE fired_at < NOW() - (:days * INTERVAL '1 day')"
                ),
                {"days": _RECOMMENDATIONS_RETENTION_DAYS},
            )
            deleted["agent_recommendations"] = r2.rowcount

    except Exception as exc:
        log.error("Prune failed", extra={"error": str(exc)})
        return jsonify({"error": str(exc)}), 500

    elapsed = round((datetime.now() - started_at).total_seconds(), 2)
    log.info(
        "Prune complete",
        extra={"deleted": deleted, "elapsed": elapsed},
    )
    return jsonify({
        "pruned_at":       started_at.isoformat(),
        "elapsed_seconds": elapsed,
        "deleted":         deleted,
        "retention": {
            "agent_signals_days":         _SIGNALS_RETENTION_DAYS,
            "agent_recommendations_days": _RECOMMENDATIONS_RETENTION_DAYS,
        },
    })


@maintenance_bp.route("/maintenance/health", methods=["GET"])
def health():
    """
    System health check — no authentication required.

    Returns:
      status        — "ok" if database is reachable, "degraded" otherwise
      market_open   — whether the market is currently open
      circuit_breakers — per-tool breaker states (open/closed + error counts)
      timestamp     — current server time
    """
    db_ok = False
    try:
        with get_db() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    return jsonify({
        "status":           "ok" if db_ok else "degraded",
        "market_open":      is_market_open(),
        "circuit_breakers": breaker_status(),
        "timestamp":        datetime.now().isoformat(),
    }), 200 if db_ok else 503
