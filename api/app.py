"""
Flask REST API for the Harvester Plan Store and Securities Dashboard.

Run with:  python -m api.app
"""

import csv
import json
import math
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml
from flask import Flask, current_app, jsonify, request
from flask_cors import CORS

# Ensure project root is on sys.path so experiments/ and fastMCPTest/ are importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FAST_MCP_DIR = PROJECT_ROOT / "fastMCPTest"
if str(FAST_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_MCP_DIR))

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
        rungs = db.get_rungs_for_plan(instance_id) if plan["status"] == "ACTIVE" else []
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
        rungs = db.get_rungs_for_plan(instance_id) if plan["status"] == "ACTIVE" else []
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

    # -----------------------------------------------------------------------
    # Securities — portfolio & watchlist
    # -----------------------------------------------------------------------

    def _load_portfolio() -> list[dict]:
        """Load portfolio positions from portfolio.csv (or sample_stocks.csv)."""
        for candidate in ("portfolio.csv", "sample_stocks.csv"):
            csv_path = PROJECT_ROOT / candidate
            if csv_path.exists():
                rows = []
                with open(csv_path, newline="") as fh:
                    for row in csv.DictReader(fh):
                        rows.append({
                            "name": row.get("name", "").strip(),
                            "symbol": row.get("symbol", "").strip().upper(),
                            "purchase_price": float(row["purchase_price"]) if row.get("purchase_price") else None,
                            "quantity": int(row["quantity"]) if row.get("quantity") else None,
                            "purchase_date": row.get("purchase_date") or None,
                            "currency": (row.get("currency") or "USD").strip().upper(),
                            "sale_price": float(row["sale_price"]) if row.get("sale_price") else None,
                            "sale_date": row.get("sale_date") or None,
                            "source": "portfolio",
                            "tags": [],
                        })
                return rows
        return []

    def _load_watchlist() -> list[dict]:
        """Load watchlist from fastMCPTest/watchlist.yaml."""
        wl_path = FAST_MCP_DIR / "watchlist.yaml"
        if not wl_path.exists():
            return []
        with open(wl_path) as fh:
            entries = yaml.safe_load(fh) or []
        rows = []
        for e in entries:
            rows.append({
                "name": e.get("name", ""),
                "symbol": str(e.get("symbol", "")).upper(),
                "currency": str(e.get("currency", "USD")).upper(),
                "purchase_price": None,
                "quantity": None,
                "purchase_date": None,
                "sale_price": None,
                "sale_date": None,
                "source": "watchlist",
                "tags": e.get("tags") or [],
            })
        return rows

    @app.route("/api/portfolio", methods=["GET"])
    def get_portfolio():
        return jsonify({"securities": _load_portfolio()})

    @app.route("/api/watchlist", methods=["GET"])
    def get_watchlist():
        return jsonify({"securities": _load_watchlist()})

    @app.route("/api/securities", methods=["GET"])
    def get_securities():
        portfolio = {s["symbol"]: s for s in _load_portfolio()}
        watchlist = {s["symbol"]: s for s in _load_watchlist()}
        combined: dict[str, dict] = {}
        for sym, s in portfolio.items():
            combined[sym] = s
        for sym, s in watchlist.items():
            if sym in combined:
                combined[sym]["source"] = "both"
                combined[sym]["tags"] = s["tags"]
            else:
                combined[sym] = s
        return jsonify({"securities": list(combined.values())})

    # -----------------------------------------------------------------------
    # Securities — OHLCV price history
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/ohlcv", methods=["GET"])
    def get_ohlcv(ticker: str):
        ticker = ticker.upper()
        days = int(request.args.get("days", 180))
        try:
            from ohlcv_cache import get_history
            df = get_history(ticker, "1d", days)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        if df.empty:
            return jsonify({"ticker": ticker, "bars": []})

        df = df.tail(days)
        bars = []
        for ts, row in df.iterrows():
            ts_str = pd.Timestamp(ts).strftime("%Y-%m-%d")
            bars.append({
                "date": ts_str,
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low":  round(float(row["Low"]),  4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            })
        return jsonify({"ticker": ticker, "bars": bars})

    # -----------------------------------------------------------------------
    # Securities — technical indicators
    # -----------------------------------------------------------------------

    def _safe_float(val) -> Optional[float]:
        if val is None:
            return None
        try:
            f = float(val)
            return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
        except (TypeError, ValueError):
            return None

    def _compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        return 100 - (100 / (1 + rs))

    def _compute_macd(closes: pd.Series):
        ema12 = closes.ewm(span=12, min_periods=12).mean()
        ema26 = closes.ewm(span=26, min_periods=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, min_periods=9).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @app.route("/api/securities/<ticker>/technicals", methods=["GET"])
    def get_technicals(ticker: str):
        ticker = ticker.upper()
        days = int(request.args.get("days", 365))
        try:
            from ohlcv_cache import get_history
            df = get_history(ticker, "1d", max(days, 400))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        if df.empty:
            return jsonify({"ticker": ticker, "indicators": []})

        closes = df["Close"]

        # Moving averages
        ma10  = closes.rolling(10).mean()
        ma30  = closes.rolling(30).mean()
        ma50  = closes.rolling(50).mean()
        ma100 = closes.rolling(100).mean()
        ma200 = closes.rolling(200).mean()

        # Bollinger Bands (20-day)
        sma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20

        # RSI (14-day)
        rsi = _compute_rsi(closes)

        # MACD
        macd_line, signal_line, histogram = _compute_macd(closes)

        df_out = df.tail(days)
        indicators = []
        for ts, row in df_out.iterrows():
            idx = closes.index.get_loc(ts)
            indicators.append({
                "date":      pd.Timestamp(ts).strftime("%Y-%m-%d"),
                "close":     _safe_float(row["Close"]),
                "volume":    int(row["Volume"]),
                "ma10":      _safe_float(ma10.iloc[idx]),
                "ma30":      _safe_float(ma30.iloc[idx]),
                "ma50":      _safe_float(ma50.iloc[idx]),
                "ma100":     _safe_float(ma100.iloc[idx]),
                "ma200":     _safe_float(ma200.iloc[idx]),
                "bb_upper":  _safe_float(bb_upper.iloc[idx]),
                "bb_middle": _safe_float(sma20.iloc[idx]),
                "bb_lower":  _safe_float(bb_lower.iloc[idx]),
                "rsi":       _safe_float(rsi.iloc[idx]),
                "macd":      _safe_float(macd_line.iloc[idx]),
                "macd_signal": _safe_float(signal_line.iloc[idx]),
                "macd_hist": _safe_float(histogram.iloc[idx]),
            })
        return jsonify({"ticker": ticker, "indicators": indicators})

    # -----------------------------------------------------------------------
    # Securities — options data
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/options/latest", methods=["GET"])
    def get_options_latest(ticker: str):
        ticker = ticker.upper()
        try:
            from options_store import OptionsStore
            store = OptionsStore()
            snap = store.get_latest_snapshot(ticker)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        if snap is None:
            return jsonify({"ticker": ticker, "snapshot": None})
        return jsonify({"ticker": ticker, "snapshot": snap})

    @app.route("/api/securities/<ticker>/options/history", methods=["GET"])
    def get_options_history(ticker: str):
        ticker = ticker.upper()
        days = int(request.args.get("days", 30))
        try:
            from options_store import OptionsStore
            store = OptionsStore()
            history = store.get_pc_history(ticker, days=days)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        return jsonify({"ticker": ticker, "history": history})

    @app.route("/api/securities/<ticker>/options/chain", methods=["GET"])
    def get_options_chain(ticker: str):
        """
        Full options chain (all strikes, all expirations) from the most recent
        get_full_options_chain MCP snapshot.

        Query params:
          expiration  — filter to a single expiration date (YYYY-MM-DD), optional
        """
        ticker = ticker.upper()
        expiration_filter = request.args.get("expiration")
        try:
            from options_store import OptionsStore
            store = OptionsStore()
            chain = store.get_full_chain(ticker)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        if chain is None:
            return jsonify({
                "ticker":  ticker,
                "chain":   None,
                "message": "No full chain data found. Call get_full_options_chain via MCP first.",
            })

        if expiration_filter:
            chain["expirations"] = [
                e for e in chain.get("expirations", [])
                if e["expiration"] == expiration_filter
            ]

        return jsonify({"ticker": ticker, "chain": chain})

    return app


# ---------------------------------------------------------------------------
# Dev server entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=True)
