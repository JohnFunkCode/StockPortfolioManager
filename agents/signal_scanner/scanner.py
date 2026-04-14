"""
Signal Scanner — conviction scorer for portfolio positions.

Calls 6 indicator tools and scores each symbol from -9 to +9.
Threshold ±4 triggers a Discord alert and persists to agent_signals.

Scoring rules:
  RSI:           +2 oversold,        -2 overbought
  MACD:          +2 bullish_crossover, -2 bearish_crossover
  VWAP:          +1 reclaim,          -1 below_vwap (no reclaim)
  Bollinger:     +1 near lower band,  -1 near upper band
  Unusual calls: +2 strong/moderate sweep
  DAOI:          +1 buy_on_rally,     -1 sell_on_rally

NOTE: Tool functions are imported directly from fastMCPTest/ (monorepo).
      In production each tool runs as a separate Cloud Run MCP server.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "fastMCPTest"))

from stock_price_server import (  # noqa: E402
    get_delta_adjusted_oi,
    get_macd,
    get_rsi,
    get_stock_price,
    get_unusual_calls,
    get_vwap,
)

from agents.agent_notifier import AgentNotifier
from agents.pubsub import publish_escalation
from db.database import get_db

DEFAULT_THRESHOLD = 4


# ---------------------------------------------------------------------------
# Per-indicator scorers
# ---------------------------------------------------------------------------

def _score_rsi(symbol: str) -> tuple[int, dict]:
    try:
        data = get_rsi(symbol)
        signal = data.get("signal", "neutral")
        score = 2 if signal == "oversold" else (-2 if signal == "overbought" else 0)
        return score, {"rsi": data.get("rsi"), "signal": signal}
    except Exception as exc:
        return 0, {"error": str(exc)}


def _score_macd(symbol: str) -> tuple[int, dict]:
    try:
        data = get_macd(symbol)
        crossover = data.get("crossover", "")
        if crossover == "bullish_crossover":
            score = 2
        elif crossover == "bearish_crossover":
            score = -2
        else:
            score = 0
        return score, {"crossover": crossover, "histogram": data.get("histogram")}
    except Exception as exc:
        return 0, {"error": str(exc)}


def _score_vwap(symbol: str) -> tuple[int, dict]:
    try:
        data = get_vwap(symbol)
        position = data.get("position", "")
        reclaim = bool(data.get("reclaim_signal", False))
        if reclaim:
            score = 1
        elif position == "below_vwap":
            score = -1
        else:
            score = 0
        return score, {
            "position": position,
            "reclaim_signal": reclaim,
            "distance_pct": data.get("distance_pct"),
        }
    except Exception as exc:
        return 0, {"error": str(exc)}


def _score_bollinger(symbol: str) -> tuple[int, dict]:
    try:
        data = get_stock_price(symbol)
        price = data.get("price", 0.0)
        bb = data.get("bollinger_bands")
        if not bb or not price:
            return 0, {"note": "no bollinger data"}
        lower = bb.get("lower", 0.0)
        upper = bb.get("upper", 0.0)
        bb_range = upper - lower
        if bb_range > 0:
            position_pct = (price - lower) / bb_range  # 0.0 = at lower, 1.0 = at upper
        else:
            position_pct = 0.5
        if position_pct <= 0.15:
            score = 1
        elif position_pct >= 0.85:
            score = -1
        else:
            score = 0
        return score, {
            "price": price,
            "bb_lower": lower,
            "bb_upper": upper,
            "bb_position_pct": round(position_pct, 3),
        }
    except Exception as exc:
        return 0, {"error": str(exc)}


def _score_unusual_calls(symbol: str) -> tuple[int, dict]:
    try:
        data = get_unusual_calls(symbol)
        sweep = data.get("sweep_signal", "none")
        score = 2 if sweep in ("strong", "moderate") else 0
        return score, {
            "sweep_signal": sweep,
            "unusual_call_count": data.get("unusual_call_count", 0),
        }
    except Exception as exc:
        return 0, {"error": str(exc)}


def _score_daoi(symbol: str) -> tuple[int, dict]:
    try:
        data = get_delta_adjusted_oi(symbol)
        bias = data.get("mm_hedge_bias", "")
        score = 1 if bias == "buy_on_rally" else (-1 if bias == "sell_on_rally" else 0)
        return score, {
            "mm_hedge_bias": bias,
            "net_daoi_shares": data.get("net_daoi_shares"),
        }
    except Exception as exc:
        return 0, {"error": str(exc)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_threshold(tenant_id: str) -> int:
    """Fetch conviction_threshold from tenant_config; fall back to DEFAULT_THRESHOLD."""
    try:
        with get_db(tenant_id=tenant_id) as conn:
            row = conn.execute(
                text(
                    "SELECT conviction_threshold FROM tenant_config "
                    "WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            ).fetchone()
        return int(row[0]) if row else DEFAULT_THRESHOLD
    except Exception:
        return DEFAULT_THRESHOLD


def _build_trigger_list(indicators: dict) -> list[str]:
    """Build human-readable trigger strings for the Discord embed."""
    triggers = []

    rsi = indicators.get("rsi", {})
    if rsi.get("score", 0) != 0:
        triggers.append(f"RSI {rsi.get('rsi', '?')} ({rsi.get('signal', '?')})")

    macd = indicators.get("macd", {})
    if macd.get("score", 0) != 0:
        triggers.append(f"MACD {macd.get('crossover', '?')}")

    vwap = indicators.get("vwap", {})
    if vwap.get("score", 0) != 0:
        if vwap.get("reclaim_signal"):
            triggers.append("VWAP reclaim")
        else:
            triggers.append(
                f"VWAP {vwap.get('position', '?')} ({vwap.get('distance_pct', '?')}%)"
            )

    bb = indicators.get("bollinger", {})
    if bb.get("score", 0) != 0:
        side = "lower" if bb.get("bb_position_pct", 0.5) <= 0.15 else "upper"
        triggers.append(f"Near BB {side} band")

    calls = indicators.get("unusual_calls", {})
    if calls.get("score", 0) != 0:
        triggers.append(
            f"Unusual call sweeps ({calls.get('sweep_signal', '?')})"
        )

    daoi = indicators.get("daoi", {})
    if daoi.get("score", 0) != 0:
        triggers.append(f"DAOI {daoi.get('mm_hedge_bias', '?')}")

    return triggers


def _write_signal(
    tenant_id: str,
    symbol: str,
    score: int,
    direction: str,
    triggers: list[str],
) -> None:
    """Persist a fired signal to agent_signals."""
    with get_db(tenant_id=tenant_id) as conn:
        conn.execute(
            text("""
                INSERT INTO agent_signals (tenant_id, symbol, score, direction, triggers)
                VALUES (:tid, :sym, :score, :dir, :triggers::jsonb)
            """),
            {
                "tid": tenant_id,
                "sym": symbol,
                "score": score,
                "dir": direction,
                "triggers": json.dumps(triggers),
            },
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_symbol(tenant_id: str, symbol: str) -> dict:
    """
    Score one symbol across all 6 indicators.

    Returns a result dict including:
      - score: total conviction score
      - direction: 'buy' or 'sell'
      - triggered: True if |score| >= threshold
      - indicators: per-indicator breakdown
    """
    rsi_score,   rsi_data   = _score_rsi(symbol)
    macd_score,  macd_data  = _score_macd(symbol)
    vwap_score,  vwap_data  = _score_vwap(symbol)
    bb_score,    bb_data    = _score_bollinger(symbol)
    calls_score, calls_data = _score_unusual_calls(symbol)
    daoi_score,  daoi_data  = _score_daoi(symbol)

    total = rsi_score + macd_score + vwap_score + bb_score + calls_score + daoi_score
    direction = "buy" if total >= 0 else "sell"

    indicators = {
        "rsi":           {"score": rsi_score,   **rsi_data},
        "macd":          {"score": macd_score,  **macd_data},
        "vwap":          {"score": vwap_score,  **vwap_data},
        "bollinger":     {"score": bb_score,    **bb_data},
        "unusual_calls": {"score": calls_score, **calls_data},
        "daoi":          {"score": daoi_score,  **daoi_data},
    }

    threshold = _get_threshold(tenant_id)
    triggered = abs(total) >= threshold

    if triggered:
        triggers = _build_trigger_list(indicators)

        try:
            _write_signal(tenant_id, symbol, total, direction, triggers)
        except Exception as exc:
            print(
                f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                f"Failed to persist signal for {symbol}: {exc}"
            )

        notifier = AgentNotifier(tenant_id)
        notifier.send_signal_alert(
            symbol=symbol,
            score=total,
            direction=direction,
            triggers=triggers,
        )

        # Publish to Pub/Sub escalation queue — Deep Analysis subscribes
        publish_escalation(
            tenant_id=tenant_id,
            symbol=symbol,
            source="signal_scanner",
            priority="P3",
        )

    return {
        "symbol":     symbol,
        "score":      total,
        "direction":  direction,
        "threshold":  threshold,
        "triggered":  triggered,
        "indicators": indicators,
    }


def scan_tenant(tenant_id: str) -> list[dict]:
    """
    Load all open positions for a tenant and run scan_symbol on each.

    Open positions are those where sale_date IS NULL.
    Returns a list of result dicts for all symbols scanned.
    """
    with get_db(tenant_id=tenant_id) as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT symbol FROM positions "
                "WHERE tenant_id = :tid AND sale_date IS NULL"
            ),
            {"tid": tenant_id},
        ).fetchall()

    symbols = [row[0] for row in rows]

    if not symbols:
        print(
            f"{datetime.now():%Y-%m-%d %H:%M:%S} "
            f"[{tenant_id[:8]}] No open positions to scan."
        )
        return []

    results = []
    for symbol in symbols:
        try:
            result = scan_symbol(tenant_id, symbol)
            results.append(result)
            print(
                f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                f"[{tenant_id[:8]}] {symbol}: score={result['score']:+d} "
                f"triggered={result['triggered']}"
            )
        except Exception as exc:
            print(
                f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                f"Error scanning {symbol} for tenant {tenant_id[:8]}: {exc}"
            )
            results.append({"symbol": symbol, "error": str(exc)})

    return results
