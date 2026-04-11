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
# Options analytics helpers
# ---------------------------------------------------------------------------

def _compute_max_pain(contracts: list[dict]):
    """
    Return (max_pain_strike, pain_by_strike) where pain_by_strike maps
    strike → total dollar pain if the stock settled at that strike.
    """
    calls: dict[float, int] = {}
    puts:  dict[float, int] = {}
    for c in contracts:
        oi = int(c.get("open_interest") or 0)
        s  = float(c.get("strike") or 0)
        if s <= 0 or oi <= 0:
            continue
        if c["kind"] == "call":
            calls[s] = calls.get(s, 0) + oi
        else:
            puts[s]  = puts.get(s, 0)  + oi

    all_strikes = sorted(set(list(calls) + list(puts)))
    if not all_strikes:
        return None, {}

    min_pain = float("inf")
    max_pain_strike = all_strikes[0]
    pain_by_strike: dict[float, float] = {}

    for test_s in all_strikes:
        pain  = sum((test_s - k) * oi * 100 for k, oi in calls.items() if test_s > k)
        pain += sum((k - test_s) * oi * 100 for k, oi in puts.items()  if test_s < k)
        pain_by_strike[test_s] = pain
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = test_s

    return max_pain_strike, pain_by_strike


def _bs_delta_local(S: float, K: float, T: float, sigma: float,
                    r: float, is_call: bool) -> float:
    """Black-Scholes delta — mirror of stock_price_server._bs_delta (no import needed)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.5 if is_call else -0.5
    try:
        import math as _math
        d1 = (_math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * _math.sqrt(T))
        cdf = lambda x: (1.0 + _math.erf(x / _math.sqrt(2.0))) / 2.0  # noqa: E731
        return cdf(d1) if is_call else cdf(d1) - 1.0
    except (ValueError, ZeroDivisionError):
        return 0.5 if is_call else -0.5


def _compute_expected_move(contracts: list[dict], current_price: float):
    """
    Estimate expected move as the ATM straddle price (call last + put last).
    Returns (em_dollar, em_pct, atm_strike).
    """
    calls = {float(c["strike"]): c for c in contracts if c["kind"] == "call"}
    puts  = {float(c["strike"]): c for c in contracts if c["kind"] == "put"}

    all_strikes = sorted(set(list(calls) + list(puts)))
    if not all_strikes or current_price <= 0:
        return 0.0, 0.0, None

    atm_strike = min(all_strikes, key=lambda s: abs(s - current_price))
    call_last  = float((calls.get(atm_strike) or {}).get("last_price") or 0)
    put_last   = float((puts.get(atm_strike)  or {}).get("last_price") or 0)
    straddle   = call_last + put_last
    em_pct     = (straddle / current_price * 100) if current_price > 0 else 0.0
    return straddle, em_pct, atm_strike


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

    @app.route("/api/portfolio", methods=["POST"])
    def add_to_portfolio():
        """Append a new position to portfolio.csv (or sample_stocks.csv)."""
        body   = request.get_json(silent=True) or {}
        symbol = body.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400

        existing = {s["symbol"] for s in _load_portfolio()}
        if symbol in existing:
            return jsonify({"error": f"{symbol} is already in the portfolio"}), 409

        # Resolve write target (portfolio.csv preferred, else sample_stocks.csv)
        for candidate in ("portfolio.csv", "sample_stocks.csv"):
            csv_path = PROJECT_ROOT / candidate
            if csv_path.exists():
                break
        else:
            csv_path = PROJECT_ROOT / "portfolio.csv"

        name           = body.get("name", "").strip()
        purchase_price = body.get("purchase_price") or ""
        quantity       = body.get("quantity") or ""
        purchase_date  = body.get("purchase_date") or ""
        currency       = (body.get("currency") or "USD").strip().upper()

        write_header = not csv_path.exists()
        with open(csv_path, "a", newline="") as fh:
            writer = csv.writer(fh)
            if write_header:
                writer.writerow([
                    "name", "symbol", "purchase_price", "quantity",
                    "purchase_date", "currency", "sale_price", "sale_date", "current_price",
                ])
            writer.writerow([name, symbol, purchase_price, quantity,
                             purchase_date, currency, "", "", ""])

        return jsonify({"symbol": symbol, "destination": "portfolio"}), 201

    @app.route("/api/portfolio/<ticker>", methods=["DELETE"])
    def remove_from_portfolio(ticker: str):
        """Remove a position from portfolio.csv (or sample_stocks.csv) by symbol."""
        ticker = ticker.upper()

        for candidate in ("portfolio.csv", "sample_stocks.csv"):
            csv_path = PROJECT_ROOT / candidate
            if csv_path.exists():
                break
        else:
            return jsonify({"error": "Portfolio file not found"}), 404

        rows: list[dict] = []
        with open(csv_path, newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = reader.fieldnames or []
            for row in reader:
                rows.append(row)

        original_count = len(rows)
        rows = [r for r in rows if r.get("symbol", "").strip().upper() != ticker]

        if len(rows) == original_count:
            return jsonify({"error": f"{ticker} not found in portfolio"}), 404

        with open(csv_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return jsonify({"symbol": ticker, "removed": True}), 200

    @app.route("/api/watchlist", methods=["GET"])
    def get_watchlist():
        return jsonify({"securities": _load_watchlist()})

    @app.route("/api/watchlist", methods=["POST"])
    def add_to_watchlist():
        """Append a new entry to fastMCPTest/watchlist.yaml."""
        body   = request.get_json(silent=True) or {}
        symbol = body.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400

        existing = {s["symbol"] for s in _load_watchlist()}
        if symbol in existing:
            return jsonify({"error": f"{symbol} is already in the watchlist"}), 409

        wl_path = FAST_MCP_DIR / "watchlist.yaml"
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
        """
        Put/call ratio history, one data point per snapshot date.

        get_pc_history() returns one row per (snapshot, expiration).  Full-chain
        snapshots have 25+ expirations, producing many rows at the same timestamp.
        We deduplicate by grouping on captured_at and averaging the P/C ratio
        across all expirations for that snapshot.
        """
        ticker = ticker.upper()
        days = int(request.args.get("days", 30))
        try:
            from options_store import OptionsStore
            store = OptionsStore()
            raw = store.get_pc_history(ticker, days=days)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        # Aggregate: one entry per captured_at (group by date prefix to collapse
        # intra-day duplicates from full-chain snapshots with many expirations).
        from collections import defaultdict
        groups: dict[str, list] = defaultdict(list)
        for row in raw:
            date_key = row["captured_at"][:10]   # YYYY-MM-DD
            groups[date_key].append(row)

        history = []
        for date_key in sorted(groups):
            rows = groups[date_key]
            # Use the latest captured_at for this date
            latest_row = max(rows, key=lambda r: r["captured_at"])
            pc_values = [r["put_call_ratio"] for r in rows if r.get("put_call_ratio") is not None]
            avg_pc = round(sum(pc_values) / len(pc_values), 4) if pc_values else None
            history.append({
                "captured_at":    latest_row["captured_at"],
                "price":          latest_row["price"],
                "put_call_ratio": avg_pc,
                "bb_upper":       latest_row.get("bb_upper"),
                "bb_middle":      latest_row.get("bb_middle"),
                "bb_lower":       latest_row.get("bb_lower"),
            })

        return jsonify({"ticker": ticker, "history": history})

    @app.route("/api/securities/<ticker>/options/analytics", methods=["GET"])
    def get_options_analytics(ticker: str):
        """
        Per-expiration max pain and expected move computed from the most recent
        full-chain snapshot stored by get_full_options_chain.
        """
        ticker = ticker.upper()
        try:
            from options_store import OptionsStore
            store = OptionsStore()
            chain = store.get_full_chain(ticker)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        if chain is None:
            return jsonify({
                "ticker":    ticker,
                "analytics": None,
                "message":   "No full chain data. Run get_full_options_chain via MCP first.",
            })

        price  = float(chain.get("price") or 0)
        result = []

        for exp in chain.get("expirations", []):
            contracts = exp.get("contracts", [])
            if not contracts:
                continue

            max_pain_strike, pain_by_strike = _compute_max_pain(contracts)
            em_dollar, em_pct, atm_strike   = _compute_expected_move(contracts, price)

            result.append({
                "expiration":           exp["expiration"],
                "max_pain":             max_pain_strike,
                "expected_move_dollar": round(em_dollar, 2),
                "expected_move_pct":    round(em_pct, 2),
                "atm_strike":           atm_strike,
                "upper_bound":          round(price + em_dollar, 2),
                "lower_bound":          round(price - em_dollar, 2),
                "total_call_oi":        exp.get("total_call_oi") or 0,
                "total_put_oi":         exp.get("total_put_oi") or 0,
                "put_call_ratio":       exp.get("put_call_ratio"),
                "pain_curve": [
                    {"strike": s, "pain": round(p)}
                    for s, p in sorted(pain_by_strike.items())
                ],
            })

        return jsonify({"ticker": ticker, "price": price, "analytics": result})

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

    @app.route("/api/securities/<ticker>/options/iv-rank", methods=["GET"])
    def get_iv_rank(ticker: str):
        """
        IV Rank and IV Percentile for a ticker over the past 365 days.

        IV Rank       = (current_iv - 52w_low) / (52w_high - 52w_low) × 100
        IV Percentile = % of past data points where composite IV < current IV

        Composite IV per snapshot = average of avg_call_iv and avg_put_iv
        across all stored expirations for that snapshot.
        """
        ticker = ticker.upper()
        try:
            from options_store import OptionsStore
            store = OptionsStore()
            history = store.get_iv_history(ticker, days=365)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        iv_values = [row["composite_iv"] for row in history if row["composite_iv"] is not None]

        if len(iv_values) < 2:
            return jsonify({
                "ticker":        ticker,
                "current_iv":    iv_values[-1] if iv_values else None,
                "iv_rank":       None,
                "iv_percentile": None,
                "iv_52w_high":   max(iv_values) if iv_values else None,
                "iv_52w_low":    min(iv_values) if iv_values else None,
                "data_points":   len(iv_values),
                "history":       history,
            })

        current_iv   = iv_values[-1]
        iv_52w_high  = max(iv_values)
        iv_52w_low   = min(iv_values)
        iv_range     = iv_52w_high - iv_52w_low
        iv_rank      = round((current_iv - iv_52w_low) / iv_range * 100, 1) if iv_range > 0 else 0.0
        past         = iv_values[:-1]
        iv_percentile = round(sum(1 for v in past if v < current_iv) / len(past) * 100, 1) if past else None

        return jsonify({
            "ticker":        ticker,
            "current_iv":    round(current_iv, 2),
            "iv_rank":       iv_rank,
            "iv_percentile": iv_percentile,
            "iv_52w_high":   round(iv_52w_high, 2),
            "iv_52w_low":    round(iv_52w_low, 2),
            "data_points":   len(iv_values),
            "history":       history,
        })

    # -----------------------------------------------------------------------
    # Securities — earnings dates  (#3)
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/earnings", methods=["GET"])
    def get_earnings_dates(ticker: str):
        """Return past and upcoming earnings dates for a ticker (yfinance calendar)."""
        ticker = ticker.upper()
        import yfinance as yf
        dates: list[str] = []
        try:
            t = yf.Ticker(ticker)
            # earnings_dates: DataFrame with DatetimeTZDtype index (past + future)
            try:
                ed = t.earnings_dates
                if ed is not None and not ed.empty:
                    for ts in ed.index:
                        try:
                            dates.append(str(ts.date())[:10])
                        except Exception:
                            pass
            except Exception:
                pass
            # calendar: may have next "Earnings Date" key
            try:
                cal = t.calendar
                if isinstance(cal, dict):
                    next_ed = cal.get("Earnings Date")
                    if next_ed is not None:
                        candidates = next_ed if hasattr(next_ed, "__iter__") and not isinstance(next_ed, str) else [next_ed]
                        for d in candidates:
                            s = str(d)[:10]
                            if s and len(s) == 10:
                                dates.append(s)
            except Exception:
                pass
            dates = sorted(set(d for d in dates if d and len(d) == 10))
        except Exception as exc:
            return jsonify({"ticker": ticker, "earnings_dates": [], "error": str(exc)})
        return jsonify({"ticker": ticker, "earnings_dates": dates})

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
        ticker = ticker.upper()
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from stock_price_server import (
            get_stochastic, get_vwap, get_obv, get_volume_analysis,
            get_candlestick_patterns, get_higher_lows, get_gap_analysis,
        )

        tasks = {
            "stochastic":           lambda: get_stochastic(ticker),
            "vwap":                 lambda: get_vwap(ticker),
            "obv":                  lambda: get_obv(ticker),
            "volume_analysis":      lambda: get_volume_analysis(ticker),
            "candlestick_patterns": lambda: get_candlestick_patterns(ticker),
            "higher_lows":          lambda: get_higher_lows(ticker, interval="1d"),
            "gap_analysis":         lambda: get_gap_analysis(ticker),
        }

        results: dict = {}
        errors: dict = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(fn): key for key, fn in tasks.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    results[key] = None
                    errors[key] = str(e)

        return jsonify({"ticker": ticker, "_errors": errors if errors else None, **results})

    @app.route("/api/securities/<ticker>/signals/options-flow", methods=["GET"])
    def get_signals_options_flow(ticker: str):
        """Unusual call sweeps and delta-adjusted OI (market maker positioning)."""
        ticker = ticker.upper()
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from stock_price_server import get_unusual_calls, get_delta_adjusted_oi

        tasks = {
            "unusual_calls":      lambda: get_unusual_calls(ticker),
            "delta_adjusted_oi":  lambda: get_delta_adjusted_oi(ticker),
        }

        results: dict = {}
        errors: dict = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(fn): key for key, fn in tasks.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    results[key] = None
                    errors[key] = str(e)

        return jsonify({"ticker": ticker, "_errors": errors if errors else None, **results})

    @app.route("/api/securities/<ticker>/signals/risk", methods=["GET"])
    def get_signals_risk(ticker: str):
        """Historical drawdown metrics for stop-loss calibration."""
        ticker = ticker.upper()
        from stock_price_server import get_historical_drawdown
        try:
            dd = get_historical_drawdown(ticker)
        except Exception as exc:
            return jsonify({"ticker": ticker, "drawdown": None, "error": str(exc)})
        # Derive a simple stop-loss recommendation from drawdown stats
        price_data: dict = {}
        try:
            from stock_price_server import get_vwap
            vd = get_vwap(ticker)
            price_data = {"vwap": vd.get("vwap"), "vwap_position": vd.get("position")}
        except Exception:
            pass
        return jsonify({"ticker": ticker, "drawdown": dd, **price_data})

    # -----------------------------------------------------------------------
    # Securities — news with FinBERT sentiment
    # -----------------------------------------------------------------------

    @app.route("/api/securities/<ticker>/news", methods=["GET"])
    def get_security_news(ticker: str):
        """
        Recent news articles for a ticker, each scored by FinBERT sentiment
        (positive / negative / neutral + confidence score).

        Query params:
          max_articles (int, default 10) — number of articles to return
        """
        ticker = ticker.upper()
        max_articles = int(request.args.get("max_articles", 10))
        try:
            from stock_price_server import get_news
            result = get_news(ticker, max_articles=max_articles)
        except Exception as exc:
            return jsonify({"ticker": ticker, "error": str(exc), "articles": []}), 500
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
        import datetime as _dt
        from options_store import OptionsStore

        portfolio = _load_portfolio()
        store = OptionsStore()
        today = _dt.date.today()
        RISK_FREE = 0.045

        exposure_list = []
        total_net_daoi = 0.0

        for sec in portfolio:
            sym = sec["symbol"]
            chain = store.get_full_chain(sym)
            if chain is None:
                continue

            price = float(chain.get("price") or 0)
            if price <= 0:
                continue

            net_call_daoi = 0.0
            net_put_daoi  = 0.0

            for exp in chain.get("expirations", []):
                exp_str = exp.get("expiration")
                if not exp_str:
                    continue
                try:
                    exp_date = _dt.date.fromisoformat(exp_str)
                    T = max((exp_date - today).days / 365.0, 1 / 365.0)
                except Exception:
                    continue

                for c in exp.get("contracts", []):
                    K      = float(c.get("strike") or 0)
                    oi     = int(c.get("open_interest") or 0)
                    raw_iv = float(c.get("implied_vol") or 0)
                    sigma  = raw_iv / 100.0 if raw_iv > 1 else raw_iv  # stored as pct or decimal
                    if sigma <= 0:
                        sigma = 0.30
                    is_call = c.get("kind") == "call"

                    if K <= 0 or oi <= 0:
                        continue

                    delta = _bs_delta_local(price, K, T, sigma, RISK_FREE, is_call)
                    daoi  = delta * oi
                    if is_call:
                        net_call_daoi += daoi
                    else:
                        net_put_daoi  += daoi

            net_daoi = net_call_daoi + net_put_daoi
            total_net_daoi += net_daoi

            # Stock position delta (1.0 per share)
            shares = sec.get("quantity") or 0
            stock_delta = float(shares)

            mm_hedge_bias = "buy_on_rally" if (-net_daoi) > 0 else "sell_on_rally"

            exposure_list.append({
                "symbol":          sym,
                "name":            sec.get("name", sym),
                "price":           round(price, 2),
                "shares":          shares,
                "stock_delta":     stock_delta,
                "net_daoi_shares": round(net_daoi, 0),
                "call_daoi":       round(net_call_daoi, 0),
                "put_daoi":        round(net_put_daoi, 0),
                "mm_hedge_bias":   mm_hedge_bias,
                "captured_at":     chain.get("captured_at"),
            })

        return jsonify({
            "portfolio_net_daoi": round(total_net_daoi, 0),
            "positions":          exposure_list,
        })

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
        import sqlite3 as _sqlite3

        rsi_max      = request.args.get("rsi_max",      type=float)
        rsi_min      = request.args.get("rsi_min",      type=float)
        above_ma50   = request.args.get("above_ma50")   == "1"
        below_ma50   = request.args.get("below_ma50")   == "1"
        above_ma200  = request.args.get("above_ma200")  == "1"
        below_ma200  = request.args.get("below_ma200")  == "1"
        near_bb_low  = request.args.get("near_bb_lower") == "1"
        near_bb_high = request.args.get("near_bb_upper") == "1"
        macd_bull    = request.args.get("macd_bullish") == "1"
        macd_bear    = request.args.get("macd_bearish") == "1"
        src_filter   = request.args.get("source", "all")

        # Load all securities
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

        if src_filter == "portfolio":
            symbols = [s for s in combined if combined[s]["source"] in ("portfolio", "both")]
        elif src_filter == "watchlist":
            symbols = [s for s in combined if combined[s]["source"] in ("watchlist", "both")]
        else:
            symbols = list(combined.keys())

        if not symbols:
            return jsonify({"results": [], "count": 0})

        # Pull last 250 daily bars for each symbol in one SQL query
        OHLCV_DB = FAST_MCP_DIR / "ohlcv_cache.db"
        try:
            conn = _sqlite3.connect(str(OHLCV_DB))
            conn.row_factory = _sqlite3.Row
            placeholders = ",".join("?" for _ in symbols)
            rows = conn.execute(
                f"""
                SELECT symbol, ts, close, volume, high, low, open
                FROM ohlcv
                WHERE interval = '1d'
                  AND symbol IN ({placeholders})
                  AND status != 'GAP'
                ORDER BY symbol, ts ASC
                """,
                symbols,
            ).fetchall()
            conn.close()
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        # Group rows by symbol and compute indicators
        from collections import defaultdict
        bars_by_sym: dict[str, list] = defaultdict(list)
        for r in rows:
            bars_by_sym[r["symbol"]].append(r)

        results = []
        for sym in symbols:
            bars = bars_by_sym.get(sym, [])
            if len(bars) < 30:
                continue

            closes  = np.array([b["close"] for b in bars], dtype=float)
            volumes = np.array([b["volume"] for b in bars], dtype=float)

            # Use pandas Series for rolling computation
            cs = pd.Series(closes)
            ma50  = cs.rolling(50).mean().iloc[-1]   if len(closes) >= 50  else None
            ma200 = cs.rolling(200).mean().iloc[-1]  if len(closes) >= 200 else None
            sma20 = cs.rolling(20).mean()
            std20 = cs.rolling(20).std()
            bb_upper_s = (sma20 + 2 * std20).iloc[-1]
            bb_lower_s = (sma20 - 2 * std20).iloc[-1]

            # RSI
            rsi_val = _compute_rsi(cs).iloc[-1]

            # MACD
            macd_line, signal_line, _ = _compute_macd(cs)
            macd_val   = float(macd_line.iloc[-1])   if not pd.isna(macd_line.iloc[-1])   else None
            macd_sig   = float(signal_line.iloc[-1]) if not pd.isna(signal_line.iloc[-1]) else None

            last_close = float(closes[-1])
            rsi        = float(rsi_val)   if rsi_val is not None and not pd.isna(rsi_val) else None
            ma50_f     = float(ma50)      if ma50 is not None and not pd.isna(ma50)       else None
            ma200_f    = float(ma200)     if ma200 is not None and not pd.isna(ma200)     else None
            bb_upper_f = float(bb_upper_s) if not pd.isna(bb_upper_s)                    else None
            bb_lower_f = float(bb_lower_s) if not pd.isna(bb_lower_s)                    else None

            # Apply filters
            if rsi_max  is not None and (rsi is None or rsi > rsi_max):       continue
            if rsi_min  is not None and (rsi is None or rsi < rsi_min):       continue
            if above_ma50  and (ma50_f  is None or last_close <= ma50_f):     continue
            if below_ma50  and (ma50_f  is None or last_close >= ma50_f):     continue
            if above_ma200 and (ma200_f is None or last_close <= ma200_f):    continue
            if below_ma200 and (ma200_f is None or last_close >= ma200_f):    continue
            if near_bb_low and (bb_lower_f is None or
                                abs(last_close - bb_lower_f) / bb_lower_f > 0.03): continue
            if near_bb_high and (bb_upper_f is None or
                                 abs(last_close - bb_upper_f) / bb_upper_f > 0.03): continue
            if macd_bull and (macd_val is None or macd_sig is None or macd_val <= macd_sig): continue
            if macd_bear and (macd_val is None or macd_sig is None or macd_val >= macd_sig): continue

            sec = combined[sym]
            results.append({
                **sec,
                "last_close":  round(last_close, 4),
                "rsi":         round(rsi, 1) if rsi is not None else None,
                "ma50":        round(ma50_f, 2) if ma50_f is not None else None,
                "ma200":       round(ma200_f, 2) if ma200_f is not None else None,
                "bb_upper":    round(bb_upper_f, 2) if bb_upper_f is not None else None,
                "bb_lower":    round(bb_lower_f, 2) if bb_lower_f is not None else None,
                "macd":        round(macd_val, 4) if macd_val is not None else None,
                "macd_signal": round(macd_sig, 4) if macd_sig is not None else None,
            })

        results.sort(key=lambda x: x["rsi"] if x["rsi"] is not None else 50)
        return jsonify({"results": results, "count": len(results)})

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
        import os
        import requests as _requests

        ticker = ticker.upper()
        days_back     = min(int(request.args.get("days", 90)), 730)
        skip_existing = request.args.get("skip_existing", "true").lower() != "false"

        api_key = os.environ.get("POLYGON_API_KEY", "").strip()
        if not api_key:
            return jsonify({
                "error": "POLYGON_API_KEY not set in environment. "
                         "Sign up at polygon.io and add POLYGON_API_KEY=... to your .env file."
            }), 400

        from options_store import OptionsStore
        from datetime import timedelta
        store = OptionsStore()

        # Determine which dates to fetch (weekdays only)
        today = date.today()
        existing_dates = store.get_snapshot_dates(ticker, days=days_back + 7) if skip_existing else set()

        trading_days: list[date] = []
        for offset in range(days_back, 0, -1):
            d = today - timedelta(days=offset)
            if d.weekday() >= 5:          # skip Sat/Sun
                continue
            if d.isoformat() in existing_dates:
                continue
            trading_days.append(d)

        if not trading_days:
            return jsonify({
                "ticker":   ticker,
                "skipped":  0,
                "fetched":  0,
                "stored":   0,
                "failed":   0,
                "results":  [],
                "note":     "All dates in range already have snapshots.",
            })

        BASE_URL = "https://api.polygon.io/v3/snapshot/options/{ticker}"
        results: list[dict] = []
        stored = 0

        for d in trading_days:
            date_str = d.isoformat()   # YYYY-MM-DD
            contracts_all: list[dict] = []

            # Paginate through all contracts for this date
            url: str | None = (
                f"https://api.polygon.io/v3/snapshot/options/{ticker}"
                f"?date={date_str}&limit=250&apiKey={api_key}"
            )
            try:
                while url:
                    resp = _requests.get(url, timeout=30)
                    if resp.status_code == 403:
                        return jsonify({
                            "error": "Polygon API key is invalid or the account plan does not "
                                     "include historical options snapshots. "
                                     "A Starter plan ($29/mo) or higher is required.",
                            "polygon_status": resp.status_code,
                        }), 402
                    if resp.status_code == 404:
                        # No data for this date (holiday, pre-listing, etc.)
                        results.append({"date": date_str, "status": "no_data"})
                        url = None
                        continue
                    resp.raise_for_status()
                    body = resp.json()
                    contracts_all.extend(body.get("results") or [])
                    # Follow pagination cursor — Polygon returns next_url directly
                    next_url = body.get("next_url")
                    url = f"{next_url}&apiKey={api_key}" if next_url else None
            except _requests.RequestException as exc:
                results.append({"date": date_str, "status": "error", "error": str(exc)})
                continue

            if not contracts_all:
                results.append({"date": date_str, "status": "no_data"})
                continue

            # Group contracts by expiration and compute aggregates
            from collections import defaultdict
            exps: dict[str, dict] = defaultdict(lambda: {
                "calls": {"oi": 0, "vol": 0, "iv_sum": 0.0, "iv_count": 0},
                "puts":  {"oi": 0, "vol": 0, "iv_sum": 0.0, "iv_count": 0},
                "price": 0.0,
            })

            underlying_price: float = 0.0
            for c in contracts_all:
                details = c.get("details") or {}
                kind    = (details.get("contract_type") or "").lower()   # "call" | "put"
                exp     = details.get("expiration_date") or ""
                if kind not in ("call", "put") or not exp:
                    continue

                oi  = int(c.get("open_interest") or 0)
                iv  = c.get("implied_volatility")          # Polygon: decimal (0.25 = 25%)
                day = c.get("day") or {}
                vol = int(day.get("volume") or 0)

                side = exps[exp][kind + "s"]
                side["oi"]  += oi
                side["vol"] += vol
                if iv is not None and iv > 0:
                    side["iv_sum"]   += float(iv) * 100   # convert to pct
                    side["iv_count"] += 1

                ua = c.get("underlying_asset") or {}
                if ua.get("price"):
                    underlying_price = float(ua["price"])

            # Build expirations_data for save_full_chain
            expirations_data = []
            for exp, sides in sorted(exps.items()):
                call_side = sides["calls"]
                put_side  = sides["puts"]
                call_oi   = call_side["oi"]
                put_oi    = put_side["oi"]
                pc_ratio  = round(put_oi / call_oi, 4) if call_oi > 0 else None
                avg_call_iv = (
                    round(call_side["iv_sum"] / call_side["iv_count"], 2)
                    if call_side["iv_count"] > 0 else None
                )
                avg_put_iv = (
                    round(put_side["iv_sum"] / put_side["iv_count"], 2)
                    if put_side["iv_count"] > 0 else None
                )
                expirations_data.append({
                    "expiration":     exp,
                    "put_call_ratio": pc_ratio,
                    "calls": {
                        "total_open_interest": call_oi,
                        "total_volume":        call_side["vol"],
                        "avg_iv_pct":          avg_call_iv,
                        "contracts":           [],   # contracts not stored for backfill
                    },
                    "puts": {
                        "total_open_interest": put_oi,
                        "total_volume":        put_side["vol"],
                        "avg_iv_pct":          avg_put_iv,
                        "contracts":           [],
                    },
                })

            if not expirations_data:
                results.append({"date": date_str, "status": "no_expirations"})
                continue

            # Use 16:00 ET close timestamp for the backfilled snapshot
            captured_at = f"{date_str}T21:00:00Z"   # 16:00 ET = 21:00 UTC
            snap_id = store.save_full_chain(
                symbol          = ticker,
                price           = underlying_price,
                bollinger_bands = None,
                expirations_data= expirations_data,
                captured_at     = captured_at,
            )

            if snap_id is not None:
                stored += 1
                results.append({
                    "date":        date_str,
                    "status":      "stored",
                    "expirations": len(expirations_data),
                    "contracts":   len(contracts_all),
                    "price":       round(underlying_price, 2),
                })
            else:
                results.append({"date": date_str, "status": "duplicate"})

        skipped = sum(1 for r in results if r.get("status") == "duplicate")
        failed  = sum(1 for r in results if r.get("status") == "error")
        no_data = sum(1 for r in results if r.get("status") in ("no_data", "no_expirations"))

        return jsonify({
            "ticker":          ticker,
            "days_requested":  days_back,
            "dates_attempted": len(trading_days),
            "stored":          stored,
            "skipped":         skipped,
            "no_data":         no_data,
            "failed":          failed,
            "results":         results,
        })

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
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time as _time

        source      = request.args.get("source", "portfolio")
        chain_type  = request.args.get("chain_type", "atm")
        batch_size  = int(request.args.get("batch_size", 10))
        max_workers = int(request.args.get("max_workers", 4))
        batch_delay = float(request.args.get("batch_delay", 1.5))  # seconds between batches

        portfolio  = _load_portfolio()
        watchlist  = _load_watchlist()

        if source == "portfolio":
            securities = portfolio
        elif source == "watchlist":
            securities = watchlist
        else:  # "all"
            seen = {s["symbol"] for s in portfolio}
            securities = list(portfolio)
            for s in watchlist:
                if s["symbol"] not in seen:
                    securities.append(s)
                    seen.add(s["symbol"])

        symbols = [s["symbol"] for s in securities if s.get("symbol")]

        if chain_type == "full":
            from stock_price_server import get_full_options_chain as _fetch
        else:
            from stock_price_server import get_stock_price as _fetch

        start = _time.monotonic()
        results_list = []

        def _fetch_one(sym: str) -> dict:
            """Fetch with one automatic retry on failure."""
            for attempt in range(2):
                try:
                    _fetch(sym)
                    return {"symbol": sym, "status": "ok"}
                except Exception as exc:
                    last_exc = exc
                    if attempt == 0:
                        _time.sleep(2)  # brief pause before retry
            return {"symbol": sym, "status": "error", "error": str(last_exc)}

        # Use a single executor for the entire run so threads are reused across
        # batches.  Creating a new executor per batch spawns fresh threads each
        # time, and yfinance's peewee cache opens one DB connection per thread
        # (tkr-tz.db, cookies.db) that is never closed — exhausting file
        # descriptors after enough batches.
        batches = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for idx, batch in enumerate(batches):
                    futures = {executor.submit(_fetch_one, sym): sym for sym in batch}
                    for future in as_completed(futures):
                        results_list.append(future.result())
                    # Pause between batches (skip delay after the last batch)
                    if idx < len(batches) - 1:
                        _time.sleep(batch_delay)
        finally:
            # Close yfinance's peewee cache DB connections that are held open
            # in each worker thread's thread-local storage.
            try:
                from yfinance.cache import _TzDBManager, _CookieDBManager
                _TzDBManager.close_db()
                _CookieDBManager.close_db()
            except Exception:
                pass

        elapsed = round(_time.monotonic() - start, 1)
        results_list.sort(key=lambda r: r["symbol"])
        succeeded = sum(1 for r in results_list if r["status"] == "ok")
        failed    = len(results_list) - succeeded

        return jsonify({
            "source":           source,
            "chain_type":       chain_type,
            "total":            len(symbols),
            "succeeded":        succeeded,
            "failed":           failed,
            "duration_seconds": elapsed,
            "results":          results_list,
            "note": (
                "yfinance provides the current options chain only — not historical snapshots. "
                "Run this endpoint once per trading day to build a P/C ratio trend over time."
            ),
        })

    return app


# ---------------------------------------------------------------------------
# Dev server entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=True)
