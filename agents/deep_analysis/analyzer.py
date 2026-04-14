"""
Deep Analysis Agent — 6-phase, 20-tool conviction pipeline.

Triggered by:
  - Pub/Sub push from Signal Scanner (score ≥ ±4) or Portfolio Monitor (AT RISK / INST EXIT)
  - Manual API call to POST /run-deep-analysis

Pipeline phases:
  1. Price Structure   — get_stock_price, get_candlestick_patterns, get_higher_lows, get_gap_analysis
  2. Momentum         — get_rsi, get_macd, get_stochastic
  3. Volume/Inst.     — get_vwap, get_volume_analysis, get_obv
  4. Options Intel.   — get_delta_adjusted_oi, get_full_options_chain, analyze_options_symbol, get_unusual_calls
  5. Market Structure — get_dark_pool, get_short_interest, get_bid_ask_spread
  6. Risk & Sentiment — get_historical_drawdown, get_stop_loss_analysis, get_news

Synthesis maps aggregate score to BUY / SELL / HOLD / AVOID recommendation.
Result written to agent_recommendations and sent as Discord embed.

NOTE: Tool functions imported directly from fastMCPTest/ (monorepo).
      In production each tool runs as a separate Cloud Run MCP server.
"""
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "fastMCPTest"))

from market_analysis_server import (  # noqa: E402
    get_bid_ask_spread,
    get_dark_pool,
    get_short_interest,
)
from options_analysis import analyze_options_symbol  # noqa: E402
from stock_price_server import (  # noqa: E402
    get_candlestick_patterns,
    get_delta_adjusted_oi,
    get_full_options_chain,
    get_gap_analysis,
    get_higher_lows,
    get_historical_drawdown,
    get_macd,
    get_news,
    get_obv,
    get_rsi,
    get_stochastic,
    get_stock_price,
    get_stop_loss_analysis,
    get_unusual_calls,
    get_volume_analysis,
    get_vwap,
)

from agents.agent_notifier import AgentNotifier
from agents.structured_logging import get_logger
from db.database import get_db

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Concurrency limiter — cap concurrent Deep Analysis runs (Decision 9)
# ---------------------------------------------------------------------------
# Default: 3 concurrent runs per Cloud Run instance.
# Set DEEP_ANALYSIS_MAX_CONCURRENT env var to override.
_MAX_CONCURRENT = int(os.environ.get("DEEP_ANALYSIS_MAX_CONCURRENT", "3"))
_semaphore = threading.Semaphore(_MAX_CONCURRENT)

# ---------------------------------------------------------------------------
# Score boundaries → recommendation
# ---------------------------------------------------------------------------
# Maximum theoretical: ±27 across all phases.

def _score_to_recommendation(score: int) -> tuple[str, str]:
    """Return (recommendation, conviction) for a total score."""
    if score >= 12:
        return "BUY", "HIGH"
    if score >= 7:
        return "BUY", "MEDIUM"
    if score >= 3:
        return "BUY", "LOW"
    if score <= -12:
        return "SELL", "HIGH"
    if score <= -7:
        return "SELL", "MEDIUM"
    if score <= -3:
        return "SELL", "LOW"
    return "HOLD", "MEDIUM"


# ---------------------------------------------------------------------------
# Phase runners — each returns (score_delta, data_dict)
# ---------------------------------------------------------------------------

def _phase1_price_structure(symbol: str) -> tuple[int, dict]:
    """Phase 1: Price, candlestick patterns, higher lows, gap analysis."""
    score = 0
    data: dict[str, Any] = {}

    try:
        data["price"] = get_stock_price(symbol)
    except Exception as exc:
        data["price_error"] = str(exc)

    try:
        cp = get_candlestick_patterns(symbol)
        data["candlesticks"] = cp
        patterns = cp.get("patterns_found", [])
        bullish = [p for p in patterns if p.get("bias") == "bullish"]
        bearish = [p for p in patterns if p.get("bias") == "bearish"]
        if bullish:
            best = max(bullish, key=lambda p: p.get("strength_score", 0))
            score += 2 if best["strength"] == "strong" else 1
        elif bearish:
            best = max(bearish, key=lambda p: p.get("strength_score", 0))
            score -= 2 if best["strength"] == "strong" else 1
    except Exception as exc:
        data["candlesticks_error"] = str(exc)

    try:
        hl = get_higher_lows(symbol)
        data["higher_lows"] = hl
        strength = hl.get("pattern_strength", "none")
        if strength in ("strong", "moderate"):
            score += 2 if strength == "strong" else 1
    except Exception as exc:
        data["higher_lows_error"] = str(exc)

    try:
        ga = get_gap_analysis(symbol)
        data["gaps"] = ga
        price_now = (data.get("price") or {}).get("price", 0)
        if price_now:
            unfilled = ga.get("gaps", [])
            # Unfilled gap-down zones at or below price = support (bullish)
            support_gaps = [
                g for g in unfilled
                if g.get("direction") == "gap_down"
                and g.get("fill_status") == "unfilled"
                and g.get("gap_top", 0) <= price_now
            ]
            if support_gaps:
                score += 1
    except Exception as exc:
        data["gaps_error"] = str(exc)

    return score, data


def _phase2_momentum(symbol: str) -> tuple[int, dict]:
    """Phase 2: RSI, MACD, Stochastic."""
    score = 0
    data: dict[str, Any] = {}

    try:
        rsi = get_rsi(symbol)
        data["rsi"] = rsi
        sig = rsi.get("signal", "neutral")
        score += 2 if sig == "oversold" else (-2 if sig == "overbought" else 0)
    except Exception as exc:
        data["rsi_error"] = str(exc)

    try:
        macd = get_macd(symbol)
        data["macd"] = macd
        cross = macd.get("crossover", "")
        score += 2 if cross == "bullish_crossover" else (-2 if cross == "bearish_crossover" else 0)
    except Exception as exc:
        data["macd_error"] = str(exc)

    try:
        stoch = get_stochastic(symbol)
        data["stochastic"] = stoch
        sig   = stoch.get("signal", "neutral")
        cross = stoch.get("crossover", "")
        if sig == "oversold" and cross == "bullish_crossover":
            score += 2
        elif sig == "overbought" and cross == "bearish_crossover":
            score -= 2
        elif sig == "oversold":
            score += 1
        elif sig == "overbought":
            score -= 1
    except Exception as exc:
        data["stochastic_error"] = str(exc)

    return score, data


def _phase3_volume_institutional(symbol: str) -> tuple[int, dict]:
    """Phase 3: VWAP, volume analysis, OBV."""
    score = 0
    data: dict[str, Any] = {}

    try:
        vwap = get_vwap(symbol)
        data["vwap"] = vwap
        reclaim  = vwap.get("reclaim_signal", False)
        strength = vwap.get("reclaim_strength", "none")
        position = vwap.get("position", "")
        bars_below = vwap.get("consecutive_bars_below", 0)
        if reclaim and strength == "strong":
            score += 2
        elif reclaim:
            score += 1
        elif position == "below_vwap" and bars_below >= 3:
            score -= 2
        elif position == "below_vwap":
            score -= 1
    except Exception as exc:
        data["vwap_error"] = str(exc)

    try:
        vol = get_volume_analysis(symbol)
        data["volume"] = vol
        bottom = vol.get("bottom_signal", "")
        if "strong" in bottom:
            score += 2
        elif "moderate" in bottom:
            score += 1
    except Exception as exc:
        data["volume_error"] = str(exc)

    try:
        obv = get_obv(symbol)
        data["obv"] = obv
        div      = obv.get("divergence", "none")
        strength = obv.get("divergence_strength", "none")
        if div == "bullish" and strength in ("strong", "moderate"):
            score += 2
        elif div == "bullish":
            score += 1
        elif div == "bearish" and strength in ("strong", "moderate"):
            score -= 2
        elif div == "bearish":
            score -= 1
    except Exception as exc:
        data["obv_error"] = str(exc)

    return score, data


def _phase4_options_intelligence(symbol: str) -> tuple[int, dict]:
    """Phase 4: DAOI, full options chain, options analysis, unusual calls."""
    score = 0
    data: dict[str, Any] = {}

    try:
        daoi = get_delta_adjusted_oi(symbol)
        data["daoi"] = daoi
        bias   = daoi.get("mm_hedge_bias", "")
        signal = daoi.get("signal", "none")
        if bias == "buy_on_rally" and signal in ("strong", "moderate"):
            score += 2
        elif bias == "buy_on_rally":
            score += 1
        elif bias == "sell_on_rally" and signal == "none":
            score -= 2
        elif bias == "sell_on_rally":
            score -= 1
    except Exception as exc:
        data["daoi_error"] = str(exc)

    try:
        # get_full_options_chain is informational — use put/call ratio for scoring
        foc = get_full_options_chain(symbol)
        data["full_chain"] = {"expiration_count": foc.get("expiration_count"), "total_contracts": foc.get("total_contracts")}
    except Exception as exc:
        data["full_chain_error"] = str(exc)

    # analyze_options_symbol: check whether symbol appears as long or put candidate
    try:
        opts = analyze_options_symbol(symbol)
        data["options_analysis"] = opts
        long_syms = [c.get("symbol") for c in opts.get("long_candidates", [])]
        put_syms  = [c.get("symbol") for c in opts.get("put_candidates", [])]
        if symbol in long_syms:
            score += 1
        if symbol in put_syms:
            score -= 1
    except Exception as exc:
        data["options_analysis_error"] = str(exc)

    try:
        uc = get_unusual_calls(symbol)
        data["unusual_calls"] = uc
        sweep = uc.get("sweep_signal", "none")
        score += 2 if sweep == "strong" else (1 if sweep == "moderate" else 0)
    except Exception as exc:
        data["unusual_calls_error"] = str(exc)

    return score, data


def _phase5_market_structure(symbol: str) -> tuple[int, dict]:
    """Phase 5: Dark pool, short interest, bid/ask spread."""
    score = 0
    data: dict[str, Any] = {}

    try:
        dp = get_dark_pool(symbol)
        data["dark_pool"] = dp
        net = dp.get("net_signal", "none")
        score += 2 if net == "accumulation" else (-2 if net == "distribution" else 0)
    except Exception as exc:
        data["dark_pool_error"] = str(exc)

    try:
        si = get_short_interest(symbol)
        data["short_interest"] = si
        squeeze = si.get("squeeze_potential", "LOW")
        # HIGH short interest + price recovering = contrarian bullish (squeeze fuel)
        score += 1 if squeeze == "HIGH" else 0
    except Exception as exc:
        data["short_interest_error"] = str(exc)

    try:
        spread = get_bid_ask_spread(symbol)
        data["spread"] = spread
        vs_norm = spread.get("spread_vs_norm", "")
        score += 1 if vs_norm == "narrowing" else (-1 if vs_norm == "widening" else 0)
    except Exception as exc:
        data["spread_error"] = str(exc)

    return score, data


def _phase6_risk_sentiment(symbol: str) -> tuple[int, dict]:
    """Phase 6: Historical drawdown, stop loss analysis, news sentiment."""
    score = 0
    data: dict[str, Any] = {}

    try:
        dd = get_historical_drawdown(symbol)
        data["drawdown"] = dd
    except Exception as exc:
        data["drawdown_error"] = str(exc)

    stop_data = None
    try:
        stop_data = get_stop_loss_analysis(symbol)
        data["stop_loss"] = stop_data
    except Exception as exc:
        data["stop_loss_error"] = str(exc)

    try:
        news = get_news(symbol, max_articles=10)
        data["news"] = news
        sentiment_summary = news.get("sentiment_summary", {})
        overall = sentiment_summary.get("overall", "neutral")
        score += 1 if overall == "positive" else (-1 if overall == "negative" else 0)
    except Exception as exc:
        data["news_error"] = str(exc)

    return score, data


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------

def _build_bull_bear_case(phases: dict) -> tuple[list[str], list[str]]:
    """Extract human-readable bull and bear case bullet points from phase data."""
    bull: list[str] = []
    bear: list[str] = []

    # Momentum
    p2 = phases.get("momentum", {})
    if (p2.get("rsi") or {}).get("signal") == "oversold":
        bull.append(f"RSI {p2['rsi']['rsi']:.0f} — oversold, mean reversion likely")
    if (p2.get("rsi") or {}).get("signal") == "overbought":
        bear.append(f"RSI {p2['rsi']['rsi']:.0f} — overbought, pullback risk")
    macd = p2.get("macd") or {}
    if "bullish" in macd.get("crossover", ""):
        bull.append(f"MACD {macd['crossover'].replace('_', ' ')}")
    if "bearish" in macd.get("crossover", ""):
        bear.append(f"MACD {macd['crossover'].replace('_', ' ')}")

    # Volume
    p3 = phases.get("volume", {})
    vwap = p3.get("vwap") or {}
    if vwap.get("reclaim_signal"):
        bull.append(f"VWAP reclaim ({vwap.get('reclaim_strength', '')})")
    elif vwap.get("position") == "below_vwap":
        bear.append(f"Below VWAP — {vwap.get('consecutive_bars_below', 0)} sessions")
    obv = p3.get("obv") or {}
    if obv.get("divergence") == "bullish":
        bull.append(f"OBV bullish divergence ({obv.get('divergence_strength', '')})")
    if obv.get("divergence") == "bearish":
        bear.append(f"OBV bearish divergence ({obv.get('divergence_strength', '')})")

    # Options
    p4 = phases.get("options", {})
    daoi = p4.get("daoi") or {}
    if daoi.get("mm_hedge_bias") == "buy_on_rally":
        bull.append("MM net short delta — mechanical buy pressure on rallies")
    if daoi.get("mm_hedge_bias") == "sell_on_rally":
        bear.append("MM net long delta — sell pressure on rallies (resistance)")
    uc = p4.get("unusual_calls") or {}
    if uc.get("sweep_signal") in ("strong", "moderate"):
        bull.append(f"Unusual call sweeps ({uc['sweep_signal']}) — smart money positioning bullish")

    # Market structure
    p5 = phases.get("market", {})
    dp = p5.get("dark_pool") or {}
    if dp.get("net_signal") == "accumulation":
        bull.append("Dark pool accumulation signal — institutional buyers absorbing sellers")
    if dp.get("net_signal") == "distribution":
        bear.append("Dark pool distribution signal — institutional sellers absorbing buyers")
    si = p5.get("short_interest") or {}
    if si.get("squeeze_potential") == "HIGH":
        bull.append(f"High short interest ({si.get('short_float_pct', 0):.1f}% float) — squeeze fuel")

    # Price structure
    p1 = phases.get("price", {})
    cp = p1.get("candlesticks") or {}
    patterns = [p for p in cp.get("patterns_found", []) if p.get("bias") == "bullish"]
    if patterns:
        best = max(patterns, key=lambda p: p.get("strength_score", 0))
        bull.append(f"Bullish candlestick: {best['pattern']} ({best['strength']})")
    bearish_pats = [p for p in cp.get("patterns_found", []) if p.get("bias") == "bearish"]
    if bearish_pats:
        best = max(bearish_pats, key=lambda p: p.get("strength_score", 0))
        bear.append(f"Bearish candlestick: {best['pattern']} ({best['strength']})")

    # Sentiment
    p6 = phases.get("risk", {})
    news = p6.get("news") or {}
    overall = (news.get("sentiment_summary") or {}).get("overall", "neutral")
    if overall == "positive":
        bull.append("News sentiment positive (FinBERT)")
    if overall == "negative":
        bear.append("News sentiment negative (FinBERT)")

    return bull[:5], bear[:5]


def _build_options_play(
    recommendation: str,
    phases: dict,
) -> str | None:
    """Generate a one-line options play description from phase 4 data."""
    p4 = phases.get("options", {})
    opts = p4.get("options_analysis") or {}

    if recommendation in ("BUY", "HOLD"):
        candidates = opts.get("long_candidates", [])
        if candidates:
            c = candidates[0]
            call_trade = opts.get("call_trades", [{}])
            if call_trade:
                ct = call_trade[0] if isinstance(call_trade, list) and call_trade else {}
                if ct.get("strike"):
                    spread = " (consider spread — IV elevated)" if ct.get("suggest_spread") else ""
                    return (
                        f"${ct['strike']:.0f}C exp {ct.get('expiration', 'N/A')} — "
                        f"ask ${ct.get('ask', 0):.2f}, target ${ct.get('target_price', 0):.2f}, "
                        f"ROI {ct.get('roi_at_target_pct', 0):.0f}%{spread}"
                    )
    elif recommendation == "SELL":
        trades = opts.get("put_trades", [])
        if trades:
            pt = trades[0]
            spread = " (consider spread — IV elevated)" if pt.get("suggest_spread") else ""
            return (
                f"${pt['strike']:.0f}P exp {pt.get('expiration', 'N/A')} — "
                f"ask ${pt.get('ask', 0):.2f}, target ${pt.get('target_price', 0):.2f}, "
                f"ROI {pt.get('roi_at_target_pct', 0):.0f}%{spread}"
            )
    return None


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

class ConcurrencyLimitExceeded(RuntimeError):
    """Raised when all Deep Analysis slots are occupied."""


def analyze(tenant_id: str, symbol: str, source: str = "manual") -> dict:
    """
    Run the full 6-phase deep analysis pipeline for one symbol.

    At most DEEP_ANALYSIS_MAX_CONCURRENT (default 3) calls may run
    simultaneously per Cloud Run instance.  Excess callers receive
    ConcurrencyLimitExceeded; the Pub/Sub subscription will NACK
    the message and retry later via the dead-letter policy.

    Returns a result dict with:
      recommendation, conviction, score, entry_low, entry_high,
      price_target, stop_loss, details (bull_case, bear_case, options_play),
      phases (raw tool data)
    """
    acquired = _semaphore.acquire(blocking=False)
    if not acquired:
        raise ConcurrencyLimitExceeded(
            f"Deep Analysis at capacity ({_MAX_CONCURRENT} concurrent runs). "
            "Message will be retried via Pub/Sub dead-letter policy."
        )
    try:
        return _run_pipeline(tenant_id, symbol, source)
    finally:
        _semaphore.release()


def _run_pipeline(tenant_id: str, symbol: str, source: str) -> dict:
    """Internal pipeline — called only when a semaphore slot is held."""
    log.info("Deep Analysis started", extra={"symbol": symbol, "tenant": tenant_id[:8], "source": source})

    p1_score, p1_data = _phase1_price_structure(symbol)
    p2_score, p2_data = _phase2_momentum(symbol)
    p3_score, p3_data = _phase3_volume_institutional(symbol)
    p4_score, p4_data = _phase4_options_intelligence(symbol)
    p5_score, p5_data = _phase5_market_structure(symbol)
    p6_score, p6_data = _phase6_risk_sentiment(symbol)

    total_score = p1_score + p2_score + p3_score + p4_score + p5_score + p6_score
    recommendation, conviction = _score_to_recommendation(total_score)

    phases = {
        "price":    p1_data,
        "momentum": p2_data,
        "volume":   p3_data,
        "options":  p4_data,
        "market":   p5_data,
        "risk":     p6_data,
    }

    bull_case, bear_case = _build_bull_bear_case(phases)
    options_play = _build_options_play(recommendation, phases)

    # Entry / target / stop from stop_loss_analysis
    stop = (p6_data.get("stop_loss") or {})
    price = (p1_data.get("price") or {}).get("price") or (stop.get("price"))
    tech  = stop.get("technical") or {}
    stops = stop.get("stops") or {}

    entry_low    = tech.get("primary_support_price")
    entry_high   = round(price * 1.01, 2) if price else None     # 1% above current
    price_target = (p1_data.get("price") or {}).get("bollinger_bands", {}).get("upper") if p1_data.get("price") else None
    stop_loss    = stops.get("trailing_stop_price")

    details = {
        "bull_case":    bull_case,
        "bear_case":    bear_case,
        "options_play": options_play,
        "phase_scores": {
            "price_structure":   p1_score,
            "momentum":          p2_score,
            "volume_inst":       p3_score,
            "options_intel":     p4_score,
            "market_structure":  p5_score,
            "risk_sentiment":    p6_score,
        },
    }

    result = {
        "tenant_id":      tenant_id,
        "symbol":         symbol,
        "source":         source,
        "score":          total_score,
        "recommendation": recommendation,
        "conviction":     conviction,
        "entry_low":      entry_low,
        "entry_high":     entry_high,
        "price_target":   price_target,
        "stop_loss":      stop_loss,
        "details":        details,
    }

    # Persist recommendation
    try:
        _write_recommendation(result)
    except Exception as exc:
        log.error("Failed to persist recommendation", extra={"symbol": symbol, "error": str(exc)})

    # Send Discord embed
    try:
        notifier = AgentNotifier(tenant_id)
        notifier.send_recommendation(
            symbol=symbol,
            recommendation=recommendation,
            conviction=conviction,
            entry_low=entry_low,
            entry_high=entry_high,
            price_target=price_target,
            stop_loss=stop_loss,
            details={"bull_case": bull_case, "bear_case": bear_case, "options_play": options_play},
        )
    except Exception as exc:
        log.error("Failed to send Discord recommendation", extra={"symbol": symbol, "error": str(exc)})

    log.info(
        "Deep Analysis complete",
        extra={"symbol": symbol, "recommendation": recommendation, "conviction": conviction, "score": total_score},
    )

    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _write_recommendation(result: dict) -> None:
    """Write a recommendation to agent_recommendations."""
    tid = result["tenant_id"]
    with get_db(tenant_id=tid) as conn:
        conn.execute(
            text("""
                INSERT INTO agent_recommendations
                  (tenant_id, symbol, recommendation, conviction,
                   entry_low, entry_high, price_target, stop_loss, details)
                VALUES
                  (:tid, :sym, :rec, :conv,
                   :elo, :ehi, :pt, :sl, :det::jsonb)
            """),
            {
                "tid":  tid,
                "sym":  result["symbol"],
                "rec":  result["recommendation"],
                "conv": result["conviction"],
                "elo":  result["entry_low"],
                "ehi":  result["entry_high"],
                "pt":   result["price_target"],
                "sl":   result["stop_loss"],
                "det":  json.dumps(result["details"]),
            },
        )
