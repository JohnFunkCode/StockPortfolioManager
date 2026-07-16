"""OptionsService — options chains, sweeps, market-maker hedging, and the
options-related REST endpoints.

Phase 1 Step 5 (docs/proposals/phase1-migration-plan.md). Absorbs the options
MCP tools from fastMCPTest/stock_price_server.py (get_full_options_chain,
get_option_contracts, price_vertical_spread, get_unusual_calls,
get_delta_adjusted_oi, get_gamma_wall_history) and the options REST routes from
api/app.py (latest/history/analytics/chain/iv-rank/options-flow/delta-exposure/
backfill/refresh-snapshots).

Black-Scholes greeks, max-pain, expected-move and full-chain summarisation come
from quantcore.analytics.options_math (the single home — duplicates that used to
live in stock_price_server.py and app.py are gone). Network access goes through
the injected YFinanceGateway / PolygonGateway; persistence through OptionsStore.

Response dicts are copied verbatim from the original tool/route bodies —
behavioural parity is the contract.
"""

from __future__ import annotations

import datetime
import math
import time as _time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from quantcore.analytics.options_math import (
    bs_charm,
    bs_d1,
    bs_delta,
    bs_gamma,
    bs_vanna,
    chain_side_full,
    compute_expected_move,
    compute_max_pain,
    safe_int as _safe_int,
)
from quantcore.gateways.polygon_gateway import PolygonGateway, PolygonPlanError
from quantcore.gateways.yfinance_gateway import YFinanceGateway
from quantcore.repositories.ohlcv_repository import OhlcvRepository
from quantcore.repositories.options_repository import OptionsStore
from quantcore.services.options_contracts import (
    get_option_contracts_data,
    price_vertical_spread_data,
)


class OptionsService:
    """Options chains, unusual-call detection, delta-adjusted OI, and options REST."""

    def __init__(
        self,
        ohlcv_repository: OhlcvRepository,
        yfinance_gateway: YFinanceGateway,
        options_repository: OptionsStore,
        polygon_gateway: PolygonGateway,
        prices,
    ):
        self._ohlcv = ohlcv_repository
        self._yf = yfinance_gateway
        self._options = options_repository
        self._polygon = polygon_gateway
        # PricesService — composed for the ATM-snapshot refresh path (get_stock_price).
        self._prices = prices

    # ------------------------------------------------------------------
    # Full chain + exact contracts (MCP)
    # ------------------------------------------------------------------

    def get_full_options_chain(self, symbol: str, max_expirations: int = None) -> dict:
        info = self._yf.fast_info(symbol.upper())
        price = info.last_price
        if price is None:
            raise ValueError(f"Could not retrieve price for symbol: {symbol}")

        # Bollinger Bands for context
        hist = self._prices.get_history(symbol.upper(), "1d", 90)
        close = hist["Close"].dropna()
        if len(close) >= 20:
            sma20 = float(close.rolling(window=20).mean().iloc[-1])
            std20 = float(close.rolling(window=20).std().iloc[-1])
            bollinger_bands: dict | None = {
                "upper":   round(sma20 + 2 * std20, 2),
                "middle":  round(sma20, 2),
                "lower":   round(sma20 - 2 * std20, 2),
                "period":  20,
                "std_dev": 2,
            }
        else:
            bollinger_bands = None

        expirations = self._yf.expirations(symbol.upper())
        if not expirations:
            return {
                "symbol":           symbol.upper(),
                "price":            round(price, 2),
                "bollinger_bands":  bollinger_bands,
                "expiration_count": 0,
                "total_contracts":  0,
                "expirations":      [],
            }

        # Optional cap keeps scheduled bulk captures (main.py daily job) light;
        # interactive callers get the whole chain by default.
        if max_expirations:
            expirations = expirations[:max_expirations]

        expirations_data = []
        total_contracts  = 0

        for exp_date in expirations:
            try:
                chain = self._yf.option_chain(symbol.upper(), exp_date)
            except Exception:
                continue

            calls_data = chain_side_full(chain.calls, iv_decimals=1)
            puts_data  = chain_side_full(chain.puts,  iv_decimals=1)

            total_call_oi  = calls_data["total_open_interest"]
            total_put_oi   = puts_data["total_open_interest"]
            put_call_ratio = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else None

            expirations_data.append({
                "expiration":      exp_date,
                "put_call_ratio":  put_call_ratio,
                "calls":           calls_data,
                "puts":            puts_data,
            })
            total_contracts += len(calls_data["contracts"]) + len(puts_data["contracts"])

        snapshot_id = self._options.save_full_chain(
            symbol=symbol.upper(),
            price=price,
            bollinger_bands=bollinger_bands,
            expirations_data=expirations_data,
        )
        persisted = snapshot_id is not None

        return {
            "symbol":           symbol.upper(),
            "price":            round(price, 2),
            "currency":         getattr(info, "currency", "USD"),
            "bollinger_bands":  bollinger_bands,
            "expiration_count": len(expirations_data),
            "total_contracts":  total_contracts,
            "expirations":      [e["expiration"] for e in expirations_data],
            "snapshot_id":      snapshot_id,
            "persisted":        persisted,
            "storage_warning":  None if persisted else "Snapshot was not inserted; a duplicate timestamp may already exist.",
        }

    def get_option_contracts(
        self,
        symbol: str,
        expirations: list[str],
        strikes: list[float],
        kind: str = "call",
        max_snapshot_age_minutes: int = 15,
        allow_live_fetch: bool = True,
    ) -> dict:
        return get_option_contracts_data(
            symbol=symbol,
            expirations=expirations,
            strikes=strikes,
            kind=kind,
            max_snapshot_age_minutes=max_snapshot_age_minutes,
            allow_live_fetch=allow_live_fetch,
            store=self._options,
        )

    def price_vertical_spread(
        self,
        symbol: str,
        expiration: str,
        long_strike: float,
        short_strike: float,
        kind: str = "call",
        max_snapshot_age_minutes: int = 15,
        allow_live_fetch: bool = True,
    ) -> dict:
        return price_vertical_spread_data(
            symbol=symbol,
            expiration=expiration,
            long_strike=long_strike,
            short_strike=short_strike,
            kind=kind,
            max_snapshot_age_minutes=max_snapshot_age_minutes,
            allow_live_fetch=allow_live_fetch,
            store=self._options,
        )

    # ------------------------------------------------------------------
    # Unusual call sweeps (MCP)
    # ------------------------------------------------------------------

    def get_unusual_calls(
        self,
        symbol: str,
        min_volume: int = 100,
        min_vol_oi_ratio: float = 0.5,
        max_expirations: int = 3,
    ) -> dict:
        info       = self._yf.fast_info(symbol.upper())
        price      = getattr(info, "last_price", None)
        if price is None or math.isnan(float(price)):
            raise ValueError(f"Could not retrieve price for {symbol}")
        price = float(price)

        expirations = self._yf.expirations(symbol.upper())
        if not expirations:
            return {
                "symbol": symbol.upper(),
                "price": round(price, 2),
                "sweep_signal": "none",
                "unusual_calls": [],
                "interpretation": "No options data available.",
            }

        unusual_calls = []

        for exp in expirations[:max_expirations]:
            try:
                chain    = self._yf.option_chain(symbol.upper(), exp)
                calls_df = chain.calls.copy()
            except Exception:
                continue

            if calls_df.empty:
                continue

            for _, row in calls_df.iterrows():
                volume = _safe_int(row.get("volume"))
                if volume < min_volume:
                    continue

                oi = _safe_int(row.get("openInterest"))
                if oi == 0:
                    oi = 1   # avoid /0; vol/OI will be large, which is correct

                vol_oi = round(volume / oi, 2)
                if vol_oi < min_vol_oi_ratio:
                    continue

                strike  = round(float(row.get("strike", 0)), 2)
                last    = round(float(row.get("lastPrice", 0) or 0), 2)
                bid     = round(float(row.get("bid", 0) or 0), 2)
                ask     = round(float(row.get("ask", 0) or 0), 2)
                iv      = round(float(row.get("impliedVolatility", 0) or 0) * 100, 1)
                itm     = bool(row.get("inTheMoney", False))
                mid     = round((bid + ask) / 2, 2) if ask > 0 else 0.0
                otm_pct = round((strike - price) / price * 100, 1) if price > 0 else 0.0

                # --- Sweep score ---
                score = 0

                if   vol_oi >= 2.0: score += 3
                elif vol_oi >= 1.0: score += 2
                else:               score += 1   # already ≥ min_vol_oi_ratio

                if ask > 0 and last >= ask:
                    score += 2   # paid at or above ask — aggressive fill
                elif mid > 0 and last >= mid:
                    score += 1   # paid above midpoint

                if 5.0 <= otm_pct <= 15.0:
                    score += 2   # pure directional bet
                elif 1.0 <= otm_pct < 5.0:
                    score += 1   # near-money directional
                elif itm:
                    score -= 1   # likely a hedge, not a sweep

                # Interpretation per contract
                if score >= 7:
                    conviction = "very high"
                elif score >= 5:
                    conviction = "high"
                elif score >= 3:
                    conviction = "moderate"
                else:
                    conviction = "low"

                notes = []
                if vol_oi >= 1.0:
                    notes.append(f"vol/OI {vol_oi:.1f}× — more contracts traded than exist in OI")
                if ask > 0 and last >= ask:
                    notes.append("paid AT or ABOVE ask — aggressive sweep fill")
                elif mid > 0 and last >= mid:
                    notes.append("paid above mid — motivated buyer")
                if 5.0 <= otm_pct <= 15.0:
                    notes.append(f"{otm_pct:+.1f}% OTM — pure directional bet")
                elif 1.0 <= otm_pct < 5.0:
                    notes.append(f"{otm_pct:+.1f}% OTM — near-money directional")
                elif itm:
                    notes.append(f"{otm_pct:+.1f}% ITM — possible hedge/spread leg")

                unusual_calls.append({
                    "expiration":  exp,
                    "strike":      strike,
                    "last":        last,
                    "bid":         bid,
                    "ask":         ask,
                    "mid":         mid,
                    "iv":          iv,
                    "volume":      volume,
                    "open_interest": oi if oi > 1 else 0,
                    "vol_oi_ratio": vol_oi,
                    "otm_pct":     otm_pct,
                    "in_the_money": itm,
                    "sweep_score": score,
                    "conviction":  conviction,
                    "notes":       notes,
                })

        # Sort by sweep score descending, then volume
        unusual_calls.sort(key=lambda x: (x["sweep_score"], x["volume"]), reverse=True)

        # --- Overall signal ---
        if not unusual_calls:
            sweep_signal  = "none"
            interpretation = (
                f"No unusual call activity detected above the volume ({min_volume}) "
                f"and vol/OI ({min_vol_oi_ratio}) thresholds."
            )
        else:
            top = unusual_calls[0]
            high_conviction = [c for c in unusual_calls if c["conviction"] in ("very high", "high")]
            aggressive_fills = [c for c in unusual_calls if "paid AT or ABOVE ask" in " ".join(c["notes"])]

            if len(high_conviction) >= 3 or (top["sweep_score"] >= 7 and aggressive_fills):
                sweep_signal = "strong"
                interpretation = (
                    f"{len(unusual_calls)} unusual call(s) detected across "
                    f"{len(set(c['expiration'] for c in unusual_calls))} expiry(ies). "
                    f"Strong sweep signal — {len(high_conviction)} high-conviction contract(s), "
                    f"{len(aggressive_fills)} aggressive fill(s) at/above ask. "
                    "Institutional buyers are positioning bullishly."
                )
            elif len(high_conviction) >= 1 or len(aggressive_fills) >= 1:
                sweep_signal = "moderate"
                interpretation = (
                    f"{len(unusual_calls)} unusual call(s) detected. "
                    f"Moderate sweep signal — {len(aggressive_fills)} aggressive fill(s). "
                    "Smart money showing interest; watch for follow-through volume."
                )
            else:
                sweep_signal = "weak"
                interpretation = (
                    f"{len(unusual_calls)} elevated-volume call(s) detected but no confirmed "
                    "aggressive fills at/above ask. Possible sweep activity — monitor for confirmation."
                )

        return {
            "symbol":          symbol.upper(),
            "price":           round(price, 2),
            "expirations_scanned": list(expirations[:max_expirations]),
            "sweep_signal":    sweep_signal,
            "unusual_call_count": len(unusual_calls),
            "unusual_calls":   unusual_calls[:20],   # cap output at top 20
            "interpretation":  interpretation,
        }

    # ------------------------------------------------------------------
    # Delta-adjusted OI + gamma wall (MCP)
    # ------------------------------------------------------------------

    def get_delta_adjusted_oi(
        self,
        symbol: str,
        max_expirations: int = 3,
        risk_free_rate: float = 0.045,
    ) -> dict:
        info   = self._yf.fast_info(symbol.upper())
        price  = getattr(info, "last_price", None)
        if price is None or math.isnan(float(price)):
            raise ValueError(f"Could not retrieve price for {symbol}")
        price = float(price)

        expirations = self._yf.expirations(symbol.upper())
        if not expirations:
            return {
                "symbol": symbol.upper(),
                "price": round(price, 2),
                "signal": "none",
                "interpretation": "No options data available.",
            }

        today = datetime.date.today()

        total_call_daoi = 0.0
        total_put_daoi  = 0.0

        strike_net_daoi: dict[float, float] = {}   # strike → net delta × OI
        strike_gamma_oi: dict[float, float] = {}   # strike → BS gamma × OI

        expiry_summaries = []

        for exp in expirations[:max_expirations]:
            try:
                exp_date = datetime.date.fromisoformat(exp)
                T = max((exp_date - today).days / 365.0, 1 / 365.0)
                chain    = self._yf.option_chain(symbol.upper(), exp)
                calls_df = chain.calls.copy()
                puts_df  = chain.puts.copy()
            except Exception:
                continue

            exp_call_daoi = 0.0
            exp_put_daoi  = 0.0

            for df, is_call in [(calls_df, True), (puts_df, False)]:
                if df.empty:
                    continue
                for _, row in df.iterrows():
                    K   = float(row.get("strike", 0) or 0)
                    oi  = _safe_int(row.get("openInterest"))
                    raw_iv = float(row.get("impliedVolatility", 0) or 0)
                    sigma = raw_iv if raw_iv > 0 else 0.30   # fallback 30% if IV missing

                    if K <= 0 or oi <= 0:
                        continue

                    d1     = bs_d1(price, K, T, sigma, risk_free_rate)
                    delta  = bs_delta(price, K, T, sigma, risk_free_rate, is_call, d1=d1)
                    daoi   = delta * oi

                    if is_call:
                        exp_call_daoi += daoi
                    else:
                        exp_put_daoi += daoi

                    # Aggregate by strike for flip/wall detection
                    strike_net_daoi[K] = strike_net_daoi.get(K, 0.0) + daoi
                    gamma = bs_gamma(price, K, T, sigma, risk_free_rate, d1=d1)
                    strike_gamma_oi[K] = strike_gamma_oi.get(K, 0.0) + gamma * oi

            total_call_daoi += exp_call_daoi
            total_put_daoi  += exp_put_daoi

            expiry_summaries.append({
                "expiration":      exp,
                "days_to_expiry":  (datetime.date.fromisoformat(exp) - today).days,
                "call_daoi_shares": round(exp_call_daoi, 0),
                "put_daoi_shares":  round(exp_put_daoi, 0),
                "net_daoi_shares":  round(exp_call_daoi + exp_put_daoi, 0),
            })

        if not expiry_summaries:
            return {
                "symbol": symbol.upper(),
                "price": round(price, 2),
                "signal": "none",
                "interpretation": "Could not compute delta-adjusted OI — check symbol or options availability.",
            }

        net_daoi = total_call_daoi + total_put_daoi

        # Delta flip strike — nearest strike where net DAOI crosses zero
        sorted_strikes = sorted(strike_net_daoi.keys())

        delta_flip_strike = None
        min_abs_net = float("inf")
        for k in sorted_strikes:
            abs_net = abs(strike_net_daoi[k])
            if abs_net < min_abs_net:
                min_abs_net       = abs_net
                delta_flip_strike = k

        # Also find where cumulative net DAOI changes sign (true crossing)
        flip_crossing = None
        cum = 0.0
        for k in sorted_strikes:
            prev = cum
            cum += strike_net_daoi[k]
            if prev * cum < 0:   # sign change
                flip_crossing = round(k, 2)
                break

        # Gamma wall — strike with highest gamma × OI (most hedging activity)
        gamma_wall = max(strike_gamma_oi, key=strike_gamma_oi.get) if strike_gamma_oi else None

        # Market maker hedge bias
        mm_net_delta = -net_daoi
        if mm_net_delta < 0:
            mm_hedge_bias = "sell_on_rally"   # MM long delta, sells as price rises
            mm_note       = "MM are net LONG delta — they sell stock on rallies (resistance)"
        else:
            mm_hedge_bias = "buy_on_rally"    # MM short delta, buys as price rises
            mm_note       = "MM are net SHORT delta — they buy stock on rallies (support / amplifies bounce)"

        dist_to_flip_pct = None
        if delta_flip_strike:
            dist_to_flip_pct = round((delta_flip_strike - price) / price * 100, 2)

        # Bounce signal
        magnitude = abs(net_daoi)
        near_flip  = dist_to_flip_pct is not None and abs(dist_to_flip_pct) <= 5.0

        if mm_hedge_bias == "buy_on_rally" and near_flip and magnitude > 10_000:
            signal = "strong"
            signal_note = (
                "MM are net short delta and price is within 5% of the delta flip. "
                "Mechanical buy pressure will amplify any bounce."
            )
        elif mm_hedge_bias == "buy_on_rally" and magnitude > 5_000:
            signal = "moderate"
            signal_note = (
                "MM net short delta — hedging flows will support a rally, "
                "but price is not yet near the delta flip level."
            )
        elif mm_hedge_bias == "buy_on_rally":
            signal = "weak"
            signal_note = "MM net short delta but magnitude is small — limited mechanical support."
        else:
            signal = "none"
            signal_note = "MM net long delta — selling pressure on rallies. Not a bounce setup."

        result = {
            "symbol":              symbol.upper(),
            "price":               round(price, 2),
            "expirations_scanned": [s["expiration"] for s in expiry_summaries],
            "net_daoi_shares":     round(net_daoi, 0),
            "call_daoi_shares":    round(total_call_daoi, 0),
            "put_daoi_shares":     round(total_put_daoi, 0),
            "mm_hedge_bias":       mm_hedge_bias,
            "mm_note":             mm_note,
            "delta_flip_strike":   delta_flip_strike,
            "delta_flip_crossing": flip_crossing,
            "dist_to_flip_pct":    dist_to_flip_pct,
            "gamma_wall_strike":   gamma_wall,
            # Methodology marker persisted with each gamma_wall_history row so
            # consumers can separate rows from the legacy |delta × OI| proxy era
            # ("abs_daoi") from Black-Scholes gamma × OI rows ("bs_gamma_oi").
            "gamma_wall_method":   "bs_gamma_oi",
            "signal":              signal,
            "signal_note":         signal_note,
            "by_expiration":       expiry_summaries,
        }

        self._options.save_gamma_wall(symbol.upper(), result)
        return result

    def get_gamma_wall_history(self, symbol: str, since_days: int = 90) -> dict:
        symbol = symbol.upper().strip()
        rows = self._options.get_gamma_wall_history(symbol, since_days)
        return {
            "symbol": symbol,
            "since_days": since_days,
            "data_points": len(rows),
            "history": rows,
            "note": "One row per calendar day. Data accumulates from first call after tracking was enabled." if rows else "No history yet — call get_delta_adjusted_oi() daily to build history.",
        }

    # ------------------------------------------------------------------
    # OI-change analysis (Phase 4, issue #93)
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_oi_change(oi_delta: int, price_up: bool) -> tuple[str, str]:
        """Classic 2×2 OI/price read: what kind of positioning drove the change."""
        if oi_delta > 0:
            if price_up:
                return ("new_longs",
                        "OI rising with price — fresh long positioning confirming the move")
            return ("new_shorts",
                    "OI rising as price falls — fresh bearish positioning")
        if price_up:
            return ("short_covering",
                    "OI falling as price rises — shorts closing; rally may lack fresh sponsorship")
        return ("long_liquidation",
                "OI falling with price — longs exiting")

    def get_oi_change_analysis(
        self,
        symbol: str,
        days: int = 30,
        top_n: int = 10,
        min_oi: int = 100,
        expiration: str = None,
    ) -> dict:
        symbol = symbol.upper().strip()
        rows = self._options.get_oi_timeseries(symbol, days=days, expiration=expiration)
        snapshot_dates = sorted({r["snap_date"] for r in rows})

        # Graceful degradation: OI deltas need at least two distinct snapshot
        # days; history is forward-accumulating like gamma_wall_history.
        if len(snapshot_dates) < 2:
            return {
                "symbol": symbol,
                "days": days,
                "snapshot_dates": snapshot_dates,
                "oi_changes": [],
                "note": (
                    "OI history accumulates only when full-chain snapshots are "
                    "captured — call get_full_options_chain periodically (the "
                    "daily report job captures portfolio + watchlist symbols)."
                ),
            }

        earliest, latest = snapshot_dates[0], snapshot_dates[-1]
        previous = snapshot_dates[-2]

        by_date: dict[str, dict] = {}
        price_by_date: dict[str, float] = {}
        for r in rows:
            d = r["snap_date"]
            key = (r["expiration"], r["kind"], float(r["strike"]))
            by_date.setdefault(d, {})[key] = r
            if r["underlying_price"] is not None:
                price_by_date[d] = float(r["underlying_price"])

        price_start = price_by_date.get(earliest)
        price_end   = price_by_date.get(latest)
        underlying_change_pct = None
        if price_start and price_end:
            underlying_change_pct = round((price_end - price_start) / price_start * 100, 2)
        price_up = (underlying_change_pct or 0.0) >= 0

        # Earliest-vs-latest ΔOI per (expiration, kind, strike). Contracts absent
        # from the earliest snapshot count as OI 0 (newly listed / first capture).
        early_map = by_date[earliest]
        late_map  = by_date[latest]
        movers = []
        for key, late_row in late_map.items():
            early_row = early_map.get(key)
            oi_after  = int(late_row["open_interest"] or 0)
            oi_before = int(early_row["open_interest"] or 0) if early_row else 0
            delta = oi_after - oi_before
            if abs(delta) < min_oi:
                continue
            exp, kind, strike = key
            classification, interpretation = self._classify_oi_change(delta, price_up)
            movers.append({
                "expiration":     exp,
                "kind":           kind,
                "strike":         strike,
                "oi_before":      oi_before,
                "oi_after":       oi_after,
                "oi_change":      delta,
                "oi_change_pct":  round(delta / oi_before * 100, 1) if oi_before else None,
                "classification": classification,
                "interpretation": interpretation,
            })

        builds = sorted((m for m in movers if m["oi_change"] > 0),
                        key=lambda m: -m["oi_change"])[:top_n]
        drains = sorted((m for m in movers if m["oi_change"] < 0),
                        key=lambda m: m["oi_change"])[:top_n]

        # Options overlay: big put-OI builds below spot read as put-writing
        # support; big call-OI builds above spot read as call-wall resistance.
        spot = price_end or price_start
        put_support: dict[float, int] = {}
        call_resistance: dict[float, int] = {}
        for m in movers:
            if m["oi_change"] <= 0 or spot is None:
                continue
            if m["kind"] == "put" and m["strike"] < spot:
                put_support[m["strike"]] = put_support.get(m["strike"], 0) + m["oi_change"]
            elif m["kind"] == "call" and m["strike"] > spot:
                call_resistance[m["strike"]] = call_resistance.get(m["strike"], 0) + m["oi_change"]
        put_oi_support_strikes = [
            {"strike": k, "oi_build": v}
            for k, v in sorted(put_support.items(), key=lambda kv: -kv[1])[:5]
        ]
        call_oi_resistance_strikes = [
            {"strike": k, "oi_build": v}
            for k, v in sorted(call_resistance.items(), key=lambda kv: -kv[1])[:5]
        ]

        # Latest-vs-previous day: aggregate call/put OI shift for a quick pulse.
        prev_map = by_date[previous]
        day_call = day_put = 0
        for key, late_row in late_map.items():
            prev_row = prev_map.get(key)
            delta = int(late_row["open_interest"] or 0) - (int(prev_row["open_interest"] or 0) if prev_row else 0)
            if key[1] == "call":
                day_call += delta
            else:
                day_put += delta
        latest_day_change = {
            "from_date":      previous,
            "to_date":        latest,
            "call_oi_change": day_call,
            "put_oi_change":  day_put,
            "net_oi_change":  day_call + day_put,
        }

        n_builds = len([m for m in movers if m["oi_change"] > 0])
        n_drains = len(movers) - n_builds
        direction = "rose" if price_up else "fell"
        summary = (
            f"{symbol} {direction} {abs(underlying_change_pct or 0):.1f}% between "
            f"{earliest} and {latest}; {n_builds} contract(s) built ≥{min_oi} OI and "
            f"{n_drains} drained. "
            f"{len(put_oi_support_strikes)} put-writing support strike(s) below spot, "
            f"{len(call_oi_resistance_strikes)} call-wall strike(s) above."
        )

        return {
            "symbol": symbol,
            "days": days,
            "min_oi": min_oi,
            "expiration_filter": expiration,
            "snapshot_dates": snapshot_dates,
            "snapshot_dates_used": {"earliest": earliest, "latest": latest, "previous": previous},
            "underlying_price_start": price_start,
            "underlying_price_end": price_end,
            "underlying_change_pct": underlying_change_pct,
            "top_oi_builds": builds,
            "top_oi_drains": drains,
            "put_oi_support_strikes": put_oi_support_strikes,
            "call_oi_resistance_strikes": call_oi_resistance_strikes,
            "latest_day_change": latest_day_change,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Signed GEX profile + vanna/charm (Phase 5, issue #93)
    # ------------------------------------------------------------------

    def get_gex_profile(
        self,
        symbol: str,
        max_expirations: int = 6,
        risk_free_rate: float = 0.045,
    ) -> dict:
        info  = self._yf.fast_info(symbol.upper())
        price = getattr(info, "last_price", None)
        if price is None or math.isnan(float(price)):
            raise ValueError(f"Could not retrieve price for {symbol}")
        price = float(price)

        expirations = self._yf.expirations(symbol.upper())
        if not expirations:
            return {
                "symbol": symbol.upper(),
                "price": round(price, 2),
                "signal": "none",
                "interpretation": "No options data available.",
            }

        today = datetime.date.today()

        strike_gex: dict[float, dict] = {}   # strike → {"call_gex": .., "put_gex": ..}
        net_vanna = 0.0
        net_charm = 0.0
        by_expiration = []

        for exp in expirations[:max_expirations]:
            try:
                exp_date = datetime.date.fromisoformat(exp)
                T = max((exp_date - today).days / 365.0, 1 / 365.0)
                chain    = self._yf.option_chain(symbol.upper(), exp)
                calls_df = chain.calls.copy()
                puts_df  = chain.puts.copy()
            except Exception:
                continue

            exp_net_gex = 0.0

            for df, is_call in [(calls_df, True), (puts_df, False)]:
                if df.empty:
                    continue
                for _, row in df.iterrows():
                    K   = float(row.get("strike", 0) or 0)
                    oi  = _safe_int(row.get("openInterest"))
                    raw_iv = float(row.get("impliedVolatility", 0) or 0)
                    sigma = raw_iv if raw_iv > 0 else 0.30   # fallback 30% if IV missing

                    if K <= 0 or oi <= 0:
                        continue

                    d1    = bs_d1(price, K, T, sigma, risk_free_rate)
                    gamma = bs_gamma(price, K, T, sigma, risk_free_rate, d1=d1)
                    # Dealer convention: long calls (+), short puts (−).
                    sign = 1.0 if is_call else -1.0
                    # Dollar gamma per 1% underlying move.
                    gex = sign * gamma * oi * 100 * price * price * 0.01

                    entry = strike_gex.setdefault(K, {"call_gex": 0.0, "put_gex": 0.0})
                    if is_call:
                        entry["call_gex"] += gex
                    else:
                        entry["put_gex"] += gex
                    exp_net_gex += gex

                    net_vanna += sign * bs_vanna(price, K, T, sigma, risk_free_rate, d1=d1) * oi * 100
                    net_charm += sign * bs_charm(price, K, T, sigma, risk_free_rate, is_call, d1=d1) * oi * 100

            by_expiration.append({
                "expiration":     exp,
                "days_to_expiry": (exp_date - today).days,
                "net_gex":        round(exp_net_gex, 0),
            })

        if not by_expiration:
            return {
                "symbol": symbol.upper(),
                "price": round(price, 2),
                "signal": "none",
                "interpretation": "Could not compute GEX profile — check symbol or options availability.",
            }

        sorted_strikes = sorted(strike_gex.keys())
        net_by_strike  = {k: strike_gex[k]["call_gex"] + strike_gex[k]["put_gex"]
                          for k in sorted_strikes}
        net_gex = sum(net_by_strike.values())

        # Zero-gamma flip: cumulative net GEX over ascending strikes,
        # linearly interpolated at the first sign change.
        zero_gamma_level = None
        cum = 0.0
        prev_cum = None
        prev_k = None
        for k in sorted_strikes:
            cum += net_by_strike[k]
            if prev_cum is not None and prev_cum * cum < 0:
                zero_gamma_level = round(
                    prev_k + (k - prev_k) * abs(prev_cum) / (abs(prev_cum) + abs(cum)), 2
                )
                break
            prev_cum = cum
            prev_k = k

        dist_to_zero_gamma_pct = None
        if zero_gamma_level:
            dist_to_zero_gamma_pct = round((zero_gamma_level - price) / price * 100, 2)

        positive_strikes = {k: v for k, v in net_by_strike.items() if v > 0}
        negative_strikes = {k: v for k, v in net_by_strike.items() if v < 0}
        top_positive_gex_strike = max(positive_strikes, key=positive_strikes.get) if positive_strikes else None
        top_negative_gex_strike = min(negative_strikes, key=negative_strikes.get) if negative_strikes else None

        # Ladder: top ~20 strikes by |net GEX| plus everything within ±10% of spot.
        top_by_magnitude = set(sorted(net_by_strike, key=lambda k: -abs(net_by_strike[k]))[:20])
        near_spot = {k for k in sorted_strikes if abs(k - price) / price <= 0.10}
        gex_ladder = [
            {
                "strike":   k,
                "net_gex":  round(net_by_strike[k], 0),
                "call_gex": round(strike_gex[k]["call_gex"], 0),
                "put_gex":  round(strike_gex[k]["put_gex"], 0),
            }
            for k in sorted_strikes if k in (top_by_magnitude | near_spot)
        ]

        if net_gex > 0:
            regime = "positive_gamma"
            regime_note = ("Dealers are net long gamma — hedging dampens moves "
                           "(buy dips, sell rips); expect pinning near the call wall.")
        else:
            regime = "negative_gamma"
            regime_note = ("Dealers are net short gamma — hedging amplifies moves "
                           "(sell weakness, chase strength); volatility expansion regime.")

        vanna_note = (
            "Positive dealer vanna: falling IV forces dealer buying (vol-crush tailwind); an IV spike forces selling."
            if net_vanna > 0 else
            "Negative dealer vanna: falling IV forces dealer selling; an IV spike forces buying."
        )
        charm_note = (
            "Positive dealer charm: dealer deltas drift up into expiry — systematic selling pressure toward OpEx."
            if net_charm > 0 else
            "Negative dealer charm: dealer deltas drift down into expiry — systematic buying pressure toward OpEx."
        )

        result = {
            "symbol":                  symbol.upper(),
            "price":                   round(price, 2),
            "convention":              "dealers long calls / short puts",
            "expirations_scanned":     [s["expiration"] for s in by_expiration],
            "net_gex":                 round(net_gex, 0),
            "regime":                  regime,
            "regime_note":             regime_note,
            "zero_gamma_level":        zero_gamma_level,
            "dist_to_zero_gamma_pct":  dist_to_zero_gamma_pct,
            "top_positive_gex_strike": top_positive_gex_strike,
            "top_negative_gex_strike": top_negative_gex_strike,
            "wall_note":               ("Top positive-GEX strike acts as the call wall / pin; "
                                        "top negative-GEX strike marks put support / the vol trigger."),
            "net_vanna_exposure":      round(net_vanna, 0),
            "vanna_note":              vanna_note,
            "net_charm_exposure":      round(net_charm, 0),
            "charm_note":              charm_note,
            "gex_ladder":              gex_ladder,
            "by_expiration":           by_expiration,
        }

        self._options.save_gex_summary(symbol.upper(), result)
        return result

    def get_gex_history(self, symbol: str, since_days: int = 90) -> dict:
        symbol = symbol.upper().strip()
        rows = self._options.get_gex_history(symbol, since_days)
        return {
            "symbol": symbol,
            "since_days": since_days,
            "data_points": len(rows),
            "history": rows,
            "note": "One row per calendar day, last write wins." if rows else "No history yet — call get_gex_profile() daily to build history.",
        }

    # ------------------------------------------------------------------
    # REST: options snapshots / history / analytics / chain / IV rank
    # ------------------------------------------------------------------

    def get_options_latest(self, ticker: str) -> dict:
        ticker = ticker.upper()
        snap = self._options.get_latest_snapshot(ticker)
        if snap is None:
            return {"ticker": ticker, "snapshot": None}
        return {"ticker": ticker, "snapshot": snap}

    def get_options_history(self, ticker: str, days: int = 30) -> dict:
        ticker = ticker.upper()
        raw = self._options.get_pc_history(ticker, days=days)

        # Aggregate: one entry per captured_at (group by date prefix to collapse
        # intra-day duplicates from full-chain snapshots with many expirations).
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

        return {"ticker": ticker, "history": history}

    def get_options_analytics(self, ticker: str) -> dict:
        ticker = ticker.upper()
        chain = self._options.get_full_chain(ticker)

        if chain is None:
            return {
                "ticker":    ticker,
                "analytics": None,
                "message":   "No full chain data. Run get_full_options_chain via MCP first.",
            }

        price  = float(chain.get("price") or 0)
        result = []

        for exp in chain.get("expirations", []):
            contracts = exp.get("contracts", [])
            if not contracts:
                continue

            max_pain_strike, pain_by_strike = compute_max_pain(contracts)
            em_dollar, em_pct, atm_strike   = compute_expected_move(contracts, price)

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

        return {"ticker": ticker, "price": price, "analytics": result}

    def get_options_chain(self, ticker: str, expiration: str | None = None) -> dict:
        ticker = ticker.upper()
        chain = self._options.get_full_chain(ticker)

        if chain is None:
            return {
                "ticker":  ticker,
                "chain":   None,
                "message": "No full chain data found. Call get_full_options_chain via MCP first.",
            }

        if expiration:
            chain["expirations"] = [
                e for e in chain.get("expirations", [])
                if e["expiration"] == expiration
            ]

        return {"ticker": ticker, "chain": chain}

    def get_iv_rank(self, ticker: str) -> dict:
        ticker = ticker.upper()
        history = self._options.get_iv_history(ticker, days=365)

        iv_values = [row["composite_iv"] for row in history if row["composite_iv"] is not None]

        if len(iv_values) < 2:
            return {
                "ticker":        ticker,
                "current_iv":    iv_values[-1] if iv_values else None,
                "iv_rank":       None,
                "iv_percentile": None,
                "iv_52w_high":   max(iv_values) if iv_values else None,
                "iv_52w_low":    min(iv_values) if iv_values else None,
                "data_points":   len(iv_values),
                "history":       history,
            }

        current_iv   = iv_values[-1]
        iv_52w_high  = max(iv_values)
        iv_52w_low   = min(iv_values)
        iv_range     = iv_52w_high - iv_52w_low
        iv_rank      = round((current_iv - iv_52w_low) / iv_range * 100, 1) if iv_range > 0 else 0.0
        past         = iv_values[:-1]
        iv_percentile = round(sum(1 for v in past if v < current_iv) / len(past) * 100, 1) if past else None

        return {
            "ticker":        ticker,
            "current_iv":    round(current_iv, 2),
            "iv_rank":       iv_rank,
            "iv_percentile": iv_percentile,
            "iv_52w_high":   round(iv_52w_high, 2),
            "iv_52w_low":    round(iv_52w_low, 2),
            "data_points":   len(iv_values),
            "history":       history,
        }

    # ------------------------------------------------------------------
    # REST: options-flow signals (fan-out) + portfolio delta exposure
    # ------------------------------------------------------------------

    def get_options_flow_signals(self, ticker: str) -> dict:
        ticker = ticker.upper()

        tasks = {
            "unusual_calls":      lambda: self.get_unusual_calls(ticker),
            "delta_adjusted_oi":  lambda: self.get_delta_adjusted_oi(ticker),
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

        return {"ticker": ticker, "_errors": errors if errors else None, **results}

    def get_portfolio_delta_exposure(self, portfolio: list[dict]) -> dict:
        today = datetime.date.today()
        RISK_FREE = 0.045

        exposure_list = []
        total_net_daoi = 0.0

        for sec in portfolio:
            sym = sec["symbol"]
            chain = self._options.get_full_chain(sym)
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
                    exp_date = datetime.date.fromisoformat(exp_str)
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

                    delta = bs_delta(price, K, T, sigma, RISK_FREE, is_call)
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

        return {
            "portfolio_net_daoi": round(total_net_daoi, 0),
            "positions":          exposure_list,
        }

    # ------------------------------------------------------------------
    # REST: Polygon historical backfill
    # ------------------------------------------------------------------

    def backfill_options_history(
        self, ticker: str, days: int = 90, skip_existing: bool = True,
    ) -> tuple[dict, int]:
        """Backfill historical P/C ratio data via the Polygon snapshot API.

        Returns ``(payload, http_status)`` so the thin Flask adapter preserves
        the original status codes (400 missing key, 402 plan/auth, 200 ok).
        """
        ticker = ticker.upper()
        days_back = min(int(days), 730)

        if not self._polygon.has_key:
            return {
                "error": "POLYGON_API_KEY not set in environment. "
                         "Sign up at polygon.io and add POLYGON_API_KEY=... to your .env file."
            }, 400

        # Determine which dates to fetch (weekdays only)
        today = datetime.date.today()
        existing_dates = (
            self._options.get_snapshot_dates(ticker, days=days_back + 7)
            if skip_existing else set()
        )

        trading_days: list[datetime.date] = []
        for offset in range(days_back, 0, -1):
            d = today - datetime.timedelta(days=offset)
            if d.weekday() >= 5:          # skip Sat/Sun
                continue
            if d.isoformat() in existing_dates:
                continue
            trading_days.append(d)

        if not trading_days:
            return {
                "ticker":   ticker,
                "skipped":  0,
                "fetched":  0,
                "stored":   0,
                "failed":   0,
                "results":  [],
                "note":     "All dates in range already have snapshots.",
            }, 200

        results: list[dict] = []
        stored = 0

        for d in trading_days:
            date_str = d.isoformat()   # YYYY-MM-DD

            try:
                contracts_all = self._polygon.option_snapshots(ticker, date_str)
            except PolygonPlanError as exc:
                return {
                    "error": str(exc),
                    "polygon_status": exc.status_code,
                }, 402
            except requests.RequestException as exc:
                results.append({"date": date_str, "status": "error", "error": str(exc)})
                continue

            if contracts_all is None:
                # No data for this date (holiday, pre-listing, etc.)
                results.append({"date": date_str, "status": "no_data"})
                continue

            if not contracts_all:
                results.append({"date": date_str, "status": "no_data"})
                continue

            # Group contracts by expiration and compute aggregates
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
            snap_id = self._options.save_full_chain(
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

        return {
            "ticker":          ticker,
            "days_requested":  days_back,
            "dates_attempted": len(trading_days),
            "stored":          stored,
            "skipped":         skipped,
            "no_data":         no_data,
            "failed":          failed,
            "results":         results,
        }, 200

    # ------------------------------------------------------------------
    # REST: bulk snapshot refresh for tracked securities
    # ------------------------------------------------------------------

    def refresh_options_snapshots(
        self,
        portfolio: list[dict],
        watchlist: list[dict],
        source: str = "portfolio",
        chain_type: str = "atm",
        batch_size: int = 10,
        max_workers: int = 4,
        batch_delay: float = 1.5,
    ) -> dict:
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
            _fetch = self.get_full_options_chain
        else:
            _fetch = self._prices.get_stock_price

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
            # Provider-internal cache cleanup belongs to the gateway (#75).
            self._yf.close_thread_caches()

        elapsed = round(_time.monotonic() - start, 1)
        results_list.sort(key=lambda r: r["symbol"])
        succeeded = sum(1 for r in results_list if r["status"] == "ok")
        failed    = len(results_list) - succeeded

        return {
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
        }
