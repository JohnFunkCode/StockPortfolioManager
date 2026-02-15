"""
Flask REST API for the Harvester Plan Store.

Run with:  python -m api.app
"""

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from flask import Flask, current_app, jsonify, request
from flask_cors import CORS

# Ensure project root is on sys.path so experiments/ is importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.HarvesterPlanStore import (  # noqa: E402
    HarvesterController,
    HarvesterPlanDB,
    PlanBuildParams,
)


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------

class _JSONEncoder(json.JSONEncoder):
    """Handle Decimal, date/datetime, and sqlite3.Row objects."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    app = Flask(__name__)
    app.json = app.json_provider_class(app)
    app.json.ensure_ascii = False
    app.json_encoder = _JSONEncoder  # type: ignore[attr-defined]

    # CORS – allow React dev servers
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Initialise a single HarvesterPlanDB instance shared across requests.
    db = HarvesterPlanDB()
    controller = HarvesterController(db)
    app.config["db"] = db
    app.config["controller"] = controller

    # -----------------------------------------------------------------------
    # Error handlers
    # -----------------------------------------------------------------------

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(500)
    def _handle_error(exc):
        code = getattr(exc, "code", 500)
        return jsonify({"error": getattr(exc, "name", "Error"), "message": str(exc), "status": code}), code

    @app.errorhandler(ValueError)
    def _handle_value_error(exc):
        return jsonify({"error": "ValidationError", "message": str(exc), "status": 400}), 400

    # -----------------------------------------------------------------------
    # Health
    # -----------------------------------------------------------------------

    @app.route("/api/health")
    def health():
        try:
            db._connect().execute("SELECT 1;")
            return jsonify({"status": "ok", "db_connected": True})
        except Exception as exc:
            return jsonify({"status": "error", "db_connected": False, "message": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Plans
    # -----------------------------------------------------------------------

    @app.route("/api/plans", methods=["GET"])
    def list_plans():
        status = request.args.get("status", "ACTIVE").upper()
        if status not in ("ACTIVE", "SUPERSEDED", "ALL"):
            return jsonify({"error": "Invalid status filter", "status": 400}), 400
        plans = db.display_all_plans(status=status)
        return jsonify({"plans": plans})

    @app.route("/api/plans", methods=["POST"])
    def create_plan():
        data = request.get_json(silent=True) or {}
        symbol = data.get("symbol")
        if not symbol:
            return jsonify({"error": "symbol is required", "status": 400}), 400

        p = data.get("params", {})
        params = PlanBuildParams(
            history_window_days=p.get("history_window_days", 360),
            n_iterations=p.get("n_iterations", 4),
            alpha=p.get("alpha", 0.5),
            min_H=p.get("min_H", 0.05),
            max_H=p.get("max_H", 0.30),
            max_s0=p.get("max_s0", 1000),
        )
        template_name = data.get("template_name", "Default Template")

        try:
            result = db.build_plan(symbol=symbol, template_name=template_name, params=params)
        except RuntimeError as exc:
            return jsonify({"error": str(exc), "status": 422}), 422

        return jsonify(result), 201

    @app.route("/api/plans/<int:instance_id>", methods=["GET"])
    def get_plan(instance_id):
        plan = db.get_plan_by_id(instance_id)
        if not plan:
            return jsonify({"error": "Plan not found", "status": 404}), 404
        rungs = db.get_rungs_for_plan(instance_id)
        return jsonify({"plan": plan, "rungs": rungs})

    @app.route("/api/plans/<int:instance_id>", methods=["PATCH"])
    def update_plan(instance_id):
        data = request.get_json(silent=True) or {}
        notes = data.get("notes")
        metadata = data.get("metadata")
        metadata_json = json.dumps(metadata) if metadata is not None else None

        updated = db.update_plan_metadata(instance_id, notes=notes, metadata_json=metadata_json)
        if not updated:
            return jsonify({"error": "Plan not found or nothing to update", "status": 404}), 404
        return jsonify({"instance_id": instance_id, "updated": True})

    @app.route("/api/plans/<int:instance_id>", methods=["DELETE"])
    def delete_plan(instance_id):
        deleted = db.delete_plan(instance_id)
        if not deleted:
            return jsonify({"error": "Plan not found or not active", "status": 404}), 404
        return jsonify({"instance_id": instance_id, "deleted": True})

    # -----------------------------------------------------------------------
    # Rungs
    # -----------------------------------------------------------------------

    @app.route("/api/plans/<int:instance_id>/rungs", methods=["GET"])
    def list_rungs(instance_id):
        plan = db.get_plan_by_id(instance_id)
        if not plan:
            return jsonify({"error": "Plan not found", "status": 404}), 404
        rungs = db.get_rungs_for_plan(instance_id)
        return jsonify({"rungs": rungs})

    @app.route("/api/rungs/<int:rung_id>", methods=["GET"])
    def get_rung(rung_id):
        rung = db.get_rung_by_id(rung_id)
        if not rung:
            return jsonify({"error": "Rung not found", "status": 404}), 404
        return jsonify({"rung": rung})

    @app.route("/api/rungs/<int:rung_id>/achieve", methods=["POST"])
    def achieve_rung(rung_id):
        data = request.get_json(silent=True) or {}
        trigger_price = data.get("trigger_price")
        if trigger_price is None:
            return jsonify({"error": "trigger_price is required", "status": 400}), 400

        updated = db.mark_rungs_achieved(
            rung_ids=[rung_id],
            trigger_price=float(trigger_price),
            triggered_at=data.get("triggered_at"),
        )
        if updated == 0:
            return jsonify({"error": "Rung not found or not pending", "status": 404}), 404
        return jsonify({"rung_id": rung_id, "status": "ACHIEVED"})

    @app.route("/api/rungs/<int:rung_id>/execute", methods=["POST"])
    def execute_rung(rung_id):
        data = request.get_json(silent=True) or {}
        executed_price = data.get("executed_price")
        shares_sold = data.get("shares_sold")
        if executed_price is None or shares_sold is None:
            return jsonify({"error": "executed_price and shares_sold are required", "status": 400}), 400

        controller.record_execution(
            rung_id=rung_id,
            executed_price=float(executed_price),
            shares_sold=int(shares_sold),
            tax_paid=float(data.get("tax_paid", 0.0)),
            executed_at=data.get("executed_at"),
            notes=data.get("notes"),
        )
        return jsonify({"rung_id": rung_id, "status": "EXECUTED"})

    # -----------------------------------------------------------------------
    # Symbols
    # -----------------------------------------------------------------------

    @app.route("/api/symbols", methods=["GET"])
    def list_symbols():
        symbols = db.list_all_symbols()
        return jsonify({"symbols": symbols})

    @app.route("/api/symbols/<ticker>/price", methods=["GET"])
    def get_symbol_price(ticker):
        price = db._poll_latest_close(ticker.upper())
        if price is None:
            return jsonify({"error": f"Could not fetch price for {ticker}", "status": 404}), 404
        return jsonify({"ticker": ticker.upper(), "price": price})

    # -----------------------------------------------------------------------
    # Dashboard
    # -----------------------------------------------------------------------

    @app.route("/api/dashboard/stats", methods=["GET"])
    def dashboard_stats():
        stats = db.get_dashboard_stats()
        return jsonify(stats)

    return app


# ---------------------------------------------------------------------------
# Dev server entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=True)
