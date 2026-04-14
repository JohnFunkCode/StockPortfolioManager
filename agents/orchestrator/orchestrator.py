"""
Orchestrator — Cloud Scheduler entry point.

Exposes a Flask Blueprint with one route:

  POST /run-signal-scanner
    Loads all active tenants, calls scan_tenant() for each, returns a JSON
    summary.  Cloud Scheduler hits this endpoint every 15 minutes during
    market hours.

Optional request body (JSON):
  {"tenant_id": "<uuid>"}   — restrict to a single tenant (useful for manual runs)
"""
from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import text

import base64
import json

from agents.circuit_breaker import MarketClosedError, require_market_open
from agents.deep_analysis.analyzer import ConcurrencyLimitExceeded, analyze
from agents.portfolio_monitor.monitor import monitor_tenant
from agents.signal_scanner.scanner import scan_tenant
from agents.structured_logging import get_logger
from db.database import get_db

log = get_logger(__name__)

orchestrator_bp = Blueprint("orchestrator", __name__)


def _get_active_tenant_ids() -> list[str]:
    """Return IDs of all active tenants (no RLS needed — superuser query)."""
    with get_db() as conn:
        rows = conn.execute(
            text("SELECT id FROM tenants WHERE active = true")
        ).fetchall()
    return [str(row[0]) for row in rows]


@orchestrator_bp.route("/run-signal-scanner", methods=["POST"])
def run_signal_scanner():
    """
    Trigger a full signal scan.

    Cloud Scheduler sends a POST to this endpoint on the configured cron.
    Returns a JSON summary of how many tenants were scanned and how many
    signals fired.
    """
    started_at = datetime.now()

    # Circuit breaker — skip outside market hours unless forced
    body = request.get_json(silent=True) or {}
    if not body.get("force"):
        try:
            require_market_open()
        except MarketClosedError as exc:
            log.info("Signal scanner skipped: market closed")
            return jsonify({"skipped": True, "reason": str(exc)}), 200

    if target_tenant := body.get("tenant_id"):
        tenant_ids = [target_tenant]
    else:
        try:
            tenant_ids = _get_active_tenant_ids()
        except Exception as exc:
            return jsonify({"error": f"Failed to load tenants: {exc}"}), 500

    tenant_summaries = []
    for tid in tenant_ids:
        try:
            results = scan_tenant(tid)
            triggered = [r for r in results if r.get("triggered")]
            tenant_summaries.append({
                "tenant_id":       tid,
                "symbols_scanned": len(results),
                "signals_fired":   len(triggered),
                "triggered":       [r["symbol"] for r in triggered],
            })
        except Exception as exc:
            log.error("Scan failed for tenant", extra={"tenant": tid[:8], "error": str(exc)})
            tenant_summaries.append({"tenant_id": tid, "error": str(exc)})

    elapsed = round((datetime.now() - started_at).total_seconds(), 2)
    log.info("Signal scan complete", extra={"tenants": len(tenant_ids), "elapsed": elapsed})
    return jsonify({
        "run_at":          started_at.isoformat(),
        "elapsed_seconds": elapsed,
        "tenants_scanned": len(tenant_ids),
        "tenants":         tenant_summaries,
    })


@orchestrator_bp.route("/run-portfolio-monitor", methods=["POST"])
def run_portfolio_monitor():
    """
    Trigger a portfolio health check.

    Cloud Scheduler calls this at 0935 ET (report_type=Morning) and
    1555 ET (report_type=Closing).

    Optional request body (JSON):
      {"tenant_id": "<uuid>", "report_type": "Morning"|"Closing"}
    """
    started_at = datetime.now()

    body        = request.get_json(silent=True) or {}
    report_type = body.get("report_type", "Morning")

    if target_tenant := body.get("tenant_id"):
        tenant_ids = [target_tenant]
    else:
        try:
            tenant_ids = _get_active_tenant_ids()
        except Exception as exc:
            return jsonify({"error": f"Failed to load tenants: {exc}"}), 500

    tenant_summaries = []
    for tid in tenant_ids:
        try:
            result = monitor_tenant(tid, report_type=report_type)
            tenant_summaries.append(result)
        except Exception as exc:
            tenant_summaries.append({"tenant_id": tid, "error": str(exc)})

    elapsed = round((datetime.now() - started_at).total_seconds(), 2)
    return jsonify({
        "run_at":          started_at.isoformat(),
        "report_type":     report_type,
        "elapsed_seconds": elapsed,
        "tenants_scanned": len(tenant_ids),
        "tenants":         tenant_summaries,
    })


@orchestrator_bp.route("/run-deep-analysis", methods=["POST"])
def run_deep_analysis():
    """
    Run the Deep Analysis pipeline for one symbol.

    Accepts two calling conventions:

    1. Direct JSON (manual trigger / testing):
       {"tenant_id": "<uuid>", "symbol": "AAPL", "source": "manual"}

    2. Pub/Sub push subscription (Cloud Run):
       The Pub/Sub push envelope wraps the payload in:
       {"message": {"data": "<base64-encoded JSON>", ...}, "subscription": "..."}
       The inner JSON must have: tenant_id, symbol, source, priority.
    """
    started_at = datetime.now()
    body = request.get_json(silent=True) or {}

    # Decode Pub/Sub push envelope if present
    if "message" in body:
        try:
            raw = base64.b64decode(body["message"]["data"]).decode("utf-8")
            body = json.loads(raw)
        except Exception as exc:
            return jsonify({"error": f"Failed to decode Pub/Sub message: {exc}"}), 400

    tenant_id = body.get("tenant_id")
    symbol    = body.get("symbol", "").upper()
    source    = body.get("source", "manual")

    if not tenant_id or not symbol:
        return jsonify({"error": "tenant_id and symbol are required"}), 400

    try:
        result = analyze(tenant_id, symbol, source=source)
    except ConcurrencyLimitExceeded as exc:
        # Return 429 so Pub/Sub NACKs and retries via dead-letter policy
        return jsonify({"error": str(exc)}), 429
    except Exception as exc:
        log.error("Deep Analysis failed", extra={"symbol": symbol, "error": str(exc)})
        return jsonify({"error": str(exc)}), 500

    elapsed = round((datetime.now() - started_at).total_seconds(), 2)
    return jsonify({
        "run_at":          started_at.isoformat(),
        "elapsed_seconds": elapsed,
        "symbol":          symbol,
        "recommendation":  result["recommendation"],
        "conviction":      result["conviction"],
        "score":           result["score"],
    })
