"""
Flask REST API for the Harvester Plan Store and Securities Dashboard.

Run with:  python -m api.app
"""

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

from quantcore.services.harvester import PlanBuildParams  # noqa: E402
from quantcore.services.portfolio import DuplicateSymbolError  # noqa: E402
from quantcore.services.registry import get_services  # noqa: E402


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
    from quantcore.db import init_schema
    init_schema()

    app = Flask(__name__)
    app.json = app.json_provider_class(app)
    app.json.ensure_ascii = False
    app.json_encoder = _JSONEncoder  # type: ignore[attr-defined]

    # CORS – allow React dev servers
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Harvester plan/rung logic now lives in the services layer; routes are thin
    # adapters over the shared, lazily-constructed HarvesterService.
    db = get_services().harvester
    controller = get_services().harvester

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
            from contextlib import closing
            from quantcore.db import get_connection
            with closing(get_connection()) as conn:
                conn.execute("SELECT 1;")
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
        price = db.poll_latest_close(ticker.upper())
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

    def _load_portfolio(owner: str = "john") -> list[dict]:
        """Load an owner's portfolio positions from the DB-backed positions table."""
        return get_services().portfolio.list_positions(owner)

    def _load_watchlist() -> list[dict]:
        """Load watchlist from ./watchlist.yaml."""
        wl_path = PROJECT_ROOT / "watchlist.yaml"
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
        owner = request.args.get("owner", "john")
        return jsonify({"securities": _load_portfolio(owner)})

    @app.route("/api/portfolio", methods=["POST"])
    def add_to_portfolio():
        """Add a new position to the owner's DB-backed portfolio."""
        owner  = request.args.get("owner", "john")
        body   = request.get_json(silent=True) or {}
        symbol = body.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400

        try:
            get_services().portfolio.add_position(
                owner,
                name=body.get("name", "").strip(),
                symbol=symbol,
                purchase_price=body.get("purchase_price"),
                quantity=body.get("quantity"),
                purchase_date=body.get("purchase_date"),
                currency=body.get("currency"),
            )
        except DuplicateSymbolError as exc:
            return jsonify({"error": str(exc)}), 409

        return jsonify({"symbol": symbol, "destination": "portfolio"}), 201

    @app.route("/api/portfolio/<ticker>", methods=["DELETE"])
    def remove_from_portfolio(ticker: str):
        """Remove a position from the owner's DB-backed portfolio by symbol."""
        owner  = request.args.get("owner", "john")
        ticker = ticker.upper()

        removed = get_services().portfolio.remove_position(owner, ticker)
        if removed == 0:
            return jsonify({"error": f"{ticker} not found in portfolio"}), 404

        return jsonify({"symbol": ticker, "removed": True}), 200

    @app.route("/api/portfolio/import", methods=["POST"])
    def import_portfolio():
        """Full-sync replace of the owner's positions from an uploaded CSV.

        Accepts either a multipart file upload (form field ``file``) or a JSON
        body with a server-side ``path``.
        """
        import tempfile

        owner = request.args.get("owner", "john")

        upload = request.files.get("file")
        if upload is not None:
            with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=True) as tmp:
                upload.save(tmp.name)
                count = get_services().portfolio.import_csv(tmp.name, owner)
        else:
            path = (request.get_json(silent=True) or {}).get("path")
            if not path:
                return jsonify({"error": "a CSV file upload or 'path' is required"}), 400
            if not Path(path).exists():
                return jsonify({"error": f"CSV not found: {path}"}), 404
            count = get_services().portfolio.import_csv(path, owner)

        return jsonify({"owner": owner, "imported": count}), 200

    @app.route("/api/watchlist", methods=["GET"])
    def get_watchlist():
        return jsonify({"securities": _load_watchlist()})

    @app.route("/api/watchlist", methods=["POST"])
    def add_to_watchlist():
        """Append a new entry to ./watchlist.yaml."""
        body   = request.get_json(silent=True) or {}
        symbol = body.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400

        existing = {s["symbol"] for s in _load_watchlist()}
        if symbol in existing:
            return jsonify({"error": f"{symbol} is already in the watchlist"}), 409

        wl_path = PROJECT_ROOT / "watchlist.yaml"
        name     = body.get("name", "").strip()
        currency = (body.get("currency") or "USD").strip().upper()
        tags     = [t.strip() for t in (body.get("tags") or []) if str(t).strip()]

        entry: dict = {"name": name or symbol, "symbol": symbol, "currency": currency}
        if tags:
            entry["tags"] = tags

        # Read existing content as raw text to preserve comments / ordering
        existing_text = wl_path.read_text() if wl_path.exists() else ""
        new_block = yaml.dump([entry], default_flow_style=False, allow_unicode=True)
        # yaml.dump wraps in a list — strip the leading "- " and indent the rest
        # Actually we just append the block directly (it's valid YAML list syntax)
        with open(wl_path, "a") as fh:
            if existing_text and not existing_text.endswith("\n"):
                fh.write("\n")
            fh.write(new_block)

        return jsonify({"symbol": symbol, "destination": "watchlist"}), 201

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
    # Securities — symbol lookup (name + suggested tags from yfinance info)
    # -----------------------------------------------------------------------

    @app.route("/api/securities/lookup", methods=["GET"])
    def lookup_security():
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400
        try:
            info = get_services().yfinance_gateway.ticker_info(symbol) or {}
            name = info.get("longName") or info.get("shortName") or ""
            sector = info.get("sector") or ""
            industry = info.get("industry") or ""
            suggested_tags = [t for t in [sector, industry] if t]
            return jsonify({
                "symbol": symbol,
                "name": name,
                "suggested_tags": suggested_tags,
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Securities — OHLCV price history
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/ohlcv", methods=["GET"])
    def get_ohlcv(ticker: str):
        days = int(request.args.get("days", 180))
        try:
            return jsonify(get_services().prices.get_ohlcv_bars(ticker, days))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Securities — technical indicators
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/technicals", methods=["GET"])
    def get_technicals(ticker: str):
        days = int(request.args.get("days", 365))
        try:
            return jsonify(get_services().prices.get_technicals_table(ticker, days))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Securities — options data
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/options/latest", methods=["GET"])
    def get_options_latest(ticker: str):
        try:
            return jsonify(get_services().options.get_options_latest(ticker))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/securities/<ticker>/options/history", methods=["GET"])
    def get_options_history(ticker: str):
        """
        Put/call ratio history, one data point per snapshot date.

        get_pc_history() returns one row per (snapshot, expiration).  Full-chain
        snapshots have 25+ expirations, producing many rows at the same timestamp.
        We deduplicate by grouping on captured_at and averaging the P/C ratio
        across all expirations for that snapshot.
        """
        days = int(request.args.get("days", 30))
        try:
            return jsonify(get_services().options.get_options_history(ticker, days=days))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/securities/<ticker>/options/analytics", methods=["GET"])
    def get_options_analytics(ticker: str):
        """
        Per-expiration max pain and expected move computed from the most recent
        full-chain snapshot stored by get_full_options_chain.
        """
        try:
            return jsonify(get_services().options.get_options_analytics(ticker))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/securities/<ticker>/options/chain", methods=["GET"])
    def get_options_chain(ticker: str):
        """
        Full options chain (all strikes, all expirations) from the most recent
        get_full_options_chain MCP snapshot.

        Query params:
          expiration  — filter to a single expiration date (YYYY-MM-DD), optional
        """
        expiration_filter = request.args.get("expiration")
        try:
            return jsonify(get_services().options.get_options_chain(
                ticker, expiration=expiration_filter))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/securities/<ticker>/options/iv-rank", methods=["GET"])
    def get_iv_rank(ticker: str):
        """
        IV Rank and IV Percentile for a ticker over the past 365 days.

        IV Rank       = (current_iv - 52w_low) / (52w_high - 52w_low) × 100
        IV Percentile = % of past data points where composite IV < current IV

        Composite IV per snapshot = average of avg_call_iv and avg_put_iv
        across all stored expirations for that snapshot.
        """
        try:
            return jsonify(get_services().options.get_iv_rank(ticker))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Securities — earnings dates  (#3)
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/earnings", methods=["GET"])
    def get_earnings_dates(ticker: str):
        """Return past and upcoming earnings dates for a ticker (yfinance calendar)."""
        return jsonify(get_services().fundamentals.get_earnings_dates(ticker))

    # -----------------------------------------------------------------------
    # Securities — signals  (#4)
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/signals/technical", methods=["GET"])
    def get_signals_technical(ticker: str):
        """
        Momentum + structure signals computed from cached OHLCV data:
        stochastic, VWAP, OBV, volume analysis, candlestick patterns,
        higher lows, gap analysis.
        """
        return jsonify(get_services().prices.get_technical_signals(ticker))

    @app.route("/api/securities/<ticker>/signals/options-flow", methods=["GET"])
    def get_signals_options_flow(ticker: str):
        """Unusual call sweeps and delta-adjusted OI (market maker positioning)."""
        return jsonify(get_services().options.get_options_flow_signals(ticker))

    @app.route("/api/securities/<ticker>/signals/risk", methods=["GET"])
    def get_signals_risk(ticker: str):
        """Historical drawdown metrics for stop-loss calibration."""
        return jsonify(get_services().prices.get_risk_signals(ticker))

    # -----------------------------------------------------------------------
    # Securities — news with FinBERT sentiment
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/news", methods=["GET"])
    def get_security_news(ticker: str):
        """
        Recent news articles for a ticker, each scored by FinBERT sentiment
        (positive / negative / neutral + confidence score).

        Aggregate sentiment is persisted to sentiment.sqlite for trend tracking,
        flip detection, and the bulk sentiment dashboard.

        Query params:
          max_articles (int, default 10) — number of articles to return
        """
        ticker = ticker.upper()
        max_articles = int(request.args.get("max_articles", 10))
        try:
            result = get_services().sentiment.get_security_news(ticker, max_articles=max_articles)
        except Exception as exc:
            return jsonify({"ticker": ticker, "error": str(exc), "articles": []}), 500

        return jsonify(result)

    @app.route("/api/securities/news/sentiment-summary", methods=["GET"])
    def get_sentiment_summary():
        """
        Bulk sentiment dashboard: returns the latest FinBERT sentiment snapshot
        for every symbol that has been scored, merged with security metadata
        (name, source, tags) and ranked by overall_sentiment then negative_count.

        Optional query params:
          source — 'portfolio' | 'watchlist' | 'all' (default all)
        """
        src_filter = request.args.get("source", "all")

        try:
            result = get_services().sentiment.get_sentiment_dashboard(
                _load_portfolio(), _load_watchlist(), source_filter=src_filter
            )
        except Exception as exc:
            return jsonify({"error": str(exc), "items": []}), 500

        return jsonify(result)

    # -----------------------------------------------------------------------
    # Portfolio — delta exposure from stored full chains  (#5)
    # -----------------------------------------------------------------------

    @app.route("/api/portfolio/delta-exposure", methods=["GET"])
    def get_portfolio_delta_exposure():
        """
        For each portfolio security that has a stored full options chain,
        compute the net delta-adjusted OI (market maker share-equivalent exposure)
        using Black-Scholes delta on the stored contract data.
        """
        try:
            return jsonify(get_services().options.get_portfolio_delta_exposure(
                _load_portfolio()))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Securities — technical screener  (#6)
    # -----------------------------------------------------------------------

    @app.route("/api/securities/screen", methods=["GET"])
    def screen_securities():
        """
        Screen all securities against technical criteria computed from cached OHLCV.

        Query params (all optional, additive AND logic):
          rsi_max   — include only RSI ≤ N   (e.g. 30 for oversold)
          rsi_min   — include only RSI ≥ N   (e.g. 70 for overbought)
          above_ma50  — '1' to require close > MA50
          below_ma50  — '1' to require close < MA50
          above_ma200 — '1' to require close > MA200
          below_ma200 — '1' to require close < MA200
          near_bb_lower — '1' to require close within 3% of bb_lower
          near_bb_upper — '1' to require close within 3% of bb_upper
          macd_bullish  — '1' to require macd > macd_signal
          macd_bearish  — '1' to require macd < macd_signal
          source        — 'portfolio' | 'watchlist' | 'all' (default all)
        """
        filters = {
            "rsi_max":        request.args.get("rsi_max",  type=float),
            "rsi_min":        request.args.get("rsi_min",  type=float),
            "above_ma50":     request.args.get("above_ma50")    == "1",
            "below_ma50":     request.args.get("below_ma50")    == "1",
            "above_ma200":    request.args.get("above_ma200")   == "1",
            "below_ma200":    request.args.get("below_ma200")   == "1",
            "near_bb_lower":  request.args.get("near_bb_lower") == "1",
            "near_bb_upper":  request.args.get("near_bb_upper") == "1",
            "macd_bullish":   request.args.get("macd_bullish")  == "1",
            "macd_bearish":   request.args.get("macd_bearish")  == "1",
            "news_sentiment": request.args.get("news_sentiment"),
            "source":         request.args.get("source", "all"),
        }
        try:
            return jsonify(get_services().prices.screen_securities(
                filters, _load_portfolio(), _load_watchlist()))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Options history — Polygon.io historical backfill
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/options/history/backfill", methods=["POST"])
    def backfill_options_history(ticker: str):
        """
        Backfill historical P/C ratio data using the Polygon.io options snapshot API.

        Requires POLYGON_API_KEY in the environment (.env or shell).

        For each requested trading day (skipping weekends and dates already in DB):
          1. GET /v3/snapshot/options/{ticker}?date={YYYY-MM-DD} (paginates automatically)
          2. Groups contracts by expiration date
          3. Computes per-expiration: total_call_oi, total_put_oi, avg_call_iv,
             avg_put_iv, put_call_ratio
          4. Persists via OptionsStore.save_full_chain with captured_at = {date}T16:00:00Z
             (end-of-day, matching exchange close)

        Query params:
          days          — calendar days to look back (default 90, max 730)
          skip_existing — skip dates that already have a snapshot (default true)

        Plan requirement: Polygon Starter ($29/mo) includes 2+ years of options
        history. The free tier does NOT include historical options snapshots.
        """
        days = int(request.args.get("days", 90))
        skip_existing = request.args.get("skip_existing", "true").lower() != "false"
        try:
            payload, status = get_services().options.backfill_options_history(
                ticker, days=days, skip_existing=skip_existing)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify(payload), status

    # -----------------------------------------------------------------------
    # Options snapshots — bulk refresh for all tracked securities
    # -----------------------------------------------------------------------

    @app.route("/api/securities/refresh-options-snapshots", methods=["POST"])
    def refresh_options_snapshots():
        """
        Collect today's options snapshot for all (or a subset of) tracked securities.

        Query params:
          source     — 'portfolio' (default) | 'watchlist' | 'all'
          chain_type — 'atm' (default, fast, nearest expiry only via get_stock_price)
                     | 'full' (slow, all strikes + all expirations via get_full_options_chain)
          max_workers — int, default 5

        Returns per-symbol success/error plus aggregate counts and elapsed time.
        NOTE: yfinance only provides the *current* options chain, not historical
        snapshots. Running this endpoint daily is the only way to build a P/C
        ratio trend over time.
        """
        source      = request.args.get("source", "portfolio")
        chain_type  = request.args.get("chain_type", "atm")
        batch_size  = int(request.args.get("batch_size", 10))
        max_workers = int(request.args.get("max_workers", 4))
        batch_delay = float(request.args.get("batch_delay", 1.5))  # seconds between batches

        return jsonify(get_services().options.refresh_options_snapshots(
            _load_portfolio(),
            _load_watchlist(),
            source=source,
            chain_type=chain_type,
            batch_size=batch_size,
            max_workers=max_workers,
            batch_delay=batch_delay,
        ))

    return app


# ---------------------------------------------------------------------------
# Dev server entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5001, debug=True, use_reloader=False)
