"""RecommendationsService — the cross-domain synthesis layer.

Architectural standard v2 §5.2: this is the one service that composes *other
service instances* (Prices, Options, Microstructure, Sentiment, Fundamentals)
rather than only repositories/gateways. It replaces the former
``stock_price_server`` tools that reached across MCP servers by importing each
other's decorated tool functions — a fragile pattern that broke whenever a
server was loaded in isolation.

It owns three capabilities relocated verbatim (behavioral parity is the
contract) from ``fastMCPTest/stock_price_server.py``:

  * ``get_stop_loss_analysis``  — synthesises a technical + trailing stop
  * ``get_relative_strength``   — 1/3/6/12-month RS vs SPY/QQQ/sector ETF
  * ``get_relative_strength_history`` — daily RS series from cached OHLCV
  * ``get_trade_recommendation`` — the 19-signal scoring engine

The sector-ETF map and lookup helper move here with them, since RS is their
only consumer.
"""

from __future__ import annotations

import math

import pandas as pd

from quantcore.gateways.yfinance_gateway import YFinanceGateway
from quantcore.repositories.ohlcv_repository import OhlcvRepository
from quantcore.services.fundamentals import FundamentalsService
from quantcore.services.microstructure import MicrostructureService
from quantcore.services.options import OptionsService
from quantcore.services.prices import PricesService
from quantcore.services.sentiment import SentimentService

_SECTOR_ETF_MAP: dict[str, str] = {
    "Technology":             "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples":       "XLP",
    "Energy":                 "XLE",
    "Financials":             "XLF",
    "Health Care":            "XLV",
    "Industrials":            "XLI",
    "Materials":              "XLB",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
}


class RecommendationsService:
    def __init__(
        self,
        prices: PricesService,
        options: OptionsService,
        microstructure: MicrostructureService,
        sentiment: SentimentService,
        fundamentals: FundamentalsService,
        ohlcv_repository: OhlcvRepository,
        yfinance_gateway: YFinanceGateway,
    ):
        self._prices = prices
        self._options = options
        self._microstructure = microstructure
        self._sentiment = sentiment
        self._fundamentals = fundamentals
        self._ohlcv = ohlcv_repository
        self._yf = yfinance_gateway

    # ------------------------------------------------------------------
    def _get_sector_etf(self, symbol: str) -> str:
        """Determine the sector ETF for a symbol. Defaults to 'XLK' if not found."""
        try:
            info = self._yf.info(symbol.upper()) or {}
            sector = info.get("sector")
            return _SECTOR_ETF_MAP.get(sector or "", "XLK")
        except Exception:
            return "XLK"

    # ------------------------------------------------------------------
    # Stop-loss synthesis
    # ------------------------------------------------------------------
    def get_stop_loss_analysis(
        self,
        symbol: str,
        cost_basis: float = 0.0,
        shares: int = 0,
        max_expirations: int = 4,
    ) -> dict:
        sym = symbol.upper()

        # ── 1. Price + Bollinger Bands ────────────────────────────────────────
        price_data = self._prices.get_stock_price(sym)
        price      = price_data["price"]
        bb         = price_data["bollinger_bands"]
        bb_upper   = bb["upper"]
        bb_middle  = bb["middle"]   # 20-day SMA
        bb_lower   = bb["lower"]

        # ── 2. VWAP ──────────────────────────────────────────────────────────
        vwap_data  = self._prices.get_vwap(sym)
        vwap       = vwap_data["vwap"]
        above_vwap = vwap_data["position"] == "above_vwap"
        vwap_bars  = vwap_data.get(
            "consecutive_bars_above" if above_vwap else "consecutive_bars_below", 0
        )

        # ── 3. Momentum ───────────────────────────────────────────────────────
        macd_data  = self._prices.get_macd(sym)
        macd_cross = macd_data["crossover"]

        rsi_data   = self._prices.get_rsi(sym)
        rsi        = rsi_data["rsi"]

        # ── 4. Options — gamma wall ───────────────────────────────────────────
        gamma_wall = None
        try:
            daoi_data  = self._options.get_delta_adjusted_oi(sym, max_expirations=max_expirations)
            gamma_wall = daoi_data.get("gamma_wall_strike")
        except Exception:
            pass

        # ── 5. Historical drawdown ────────────────────────────────────────────
        dd               = self._prices.get_historical_drawdown(sym)
        base_trailing    = dd["trailing_stop_pct"]
        max_1day_pct     = abs(dd["max_1day_drawdown_pct"])
        max_5day_pct     = abs(dd["max_5day_drawdown_pct"])
        max_intraday_pct = abs(dd["max_intraday_drop_pct"])
        recent_1day_pct  = abs(dd["recent_max_1day_pct"])

        # ── 6. Short interest ─────────────────────────────────────────────────
        short_float_pct = 0.0
        short_ratio     = 0.0
        short_available = False
        try:
            t_info          = self._yf.info(sym)
            raw_si          = float(t_info.get("shortPercentOfFloat") or 0)
            short_float_pct = round(raw_si * 100 if raw_si <= 1.0 else raw_si, 2)
            short_ratio     = round(float(t_info.get("shortRatio") or 0), 2)
            short_available = True
        except Exception:
            pass

        if short_float_pct >= 20 and short_ratio >= 5:
            squeeze = "HIGH"
        elif short_float_pct >= 10 or short_ratio >= 3:
            squeeze = "MEDIUM"
        else:
            squeeze = "LOW"

        # ── 7. Support levels below current price ─────────────────────────────
        # Gamma wall takes priority; VWAP only if price is above it (otherwise
        # VWAP is resistance); then 20-day SMA; then lower BB as last resort.
        supports = {}
        if gamma_wall and float(gamma_wall) < price:
            supports["gamma_wall"] = float(gamma_wall)
        if vwap < price:
            supports["vwap"] = float(vwap)
        if bb_middle < price:
            supports["sma_20"] = float(bb_middle)
        if bb_lower < price:
            supports["bb_lower"] = float(bb_lower)

        sorted_supports = sorted(supports.items(), key=lambda x: x[1], reverse=True)

        # Buffer below primary support — tighter for options-defined levels,
        # slightly wider for statistical levels that get tested more frequently.
        _buf = {"gamma_wall": 0.008, "vwap": 0.014, "sma_20": 0.014, "bb_lower": 0.020}

        if sorted_supports:
            primary_name, primary_level = sorted_supports[0]
            technical_stop = round(primary_level * (1 - _buf.get(primary_name, 0.014)), 2)
        else:
            primary_name   = "bb_lower"
            primary_level  = bb_lower
            technical_stop = round(bb_lower * 0.980, 2)

        technical_dist_pct = round((technical_stop - price) / price * 100, 2)
        stop_inside_noise  = abs(technical_dist_pct) < max_1day_pct

        # ── 8. Trailing stop — calibrate and adjust for short interest ─────────
        if short_float_pct >= 15 and not above_vwap:
            # High SI in a downtrend: shorts add selling pressure on breakdown
            adj_trailing = round(base_trailing * 0.90, 2)
            si_impact    = "tightened — high short float in downtrend adds breakdown pressure"
        elif short_float_pct >= 20 and above_vwap:
            # High SI in an uptrend: squeeze cushions pullbacks
            adj_trailing = round(base_trailing * 1.10, 2)
            si_impact    = "widened — high short float with upward momentum adds squeeze cushion"
        else:
            adj_trailing = base_trailing
            si_impact    = "neutral"

        trailing_stop_price = round(price * (1 - adj_trailing / 100), 2)

        # ── 9. Position P&L at each stop ──────────────────────────────────────
        position = {}
        if cost_basis > 0:
            pnl_per_share = round(price - cost_basis, 2)
            pnl_pct       = round((price - cost_basis) / cost_basis * 100, 2)
            n             = shares if shares > 0 else 1
            position = {
                "cost_basis":               round(cost_basis, 2),
                "shares":                   shares,
                "unrealized_pnl_per_share": pnl_per_share,
                "unrealized_pnl_pct":       pnl_pct,
                "total_unrealized_pnl":     round(pnl_per_share * n, 2),
                "pnl_at_technical_stop":    round((technical_stop - cost_basis) * n, 2),
                "pnl_at_trailing_stop":     round((trailing_stop_price - cost_basis) * n, 2),
            }

        # ── 10. Flags ──────────────────────────────────────────────────────────
        flags = []
        flags.append(f"{vwap_bars}_bars_{'above' if above_vwap else 'below'}_vwap")
        flags.append(
            "bearish_macd"          if "bearish" in macd_cross else
            "bullish_macd_crossover" if macd_cross == "bullish_crossover" else
            "bullish_macd"
        )
        if rsi < 35:
            flags.append(f"rsi_oversold_{rsi:.0f}")
        elif rsi > 70:
            flags.append(f"rsi_overbought_{rsi:.0f}")
        if stop_inside_noise:
            flags.append("technical_stop_inside_noise_floor")
        if squeeze == "HIGH":
            flags.append("squeeze_potential_high")
        elif squeeze == "MEDIUM":
            flags.append("squeeze_potential_medium")
        if recent_1day_pct > max_1day_pct * 1.4:
            flags.append("elevated_recent_volatility")

        # ── 11. Human-readable summary ─────────────────────────────────────────
        trend = "uptrend" if above_vwap else "downtrend"
        mom   = "bullish" if "bullish" in macd_cross else "bearish"
        lines = [
            f"{sym} at ${price:.2f} — {trend} "
            f"({vwap_bars} bars {'above' if above_vwap else 'below'} VWAP ${vwap:.2f}).",
            f"Momentum: MACD {mom}, RSI {rsi:.0f}.",
            f"Primary support: {primary_name.replace('_', ' ')} at ${primary_level:.2f}.",
            f"Technical stop: ${technical_stop:.2f} ({technical_dist_pct:.1f}% from price).",
        ]
        if stop_inside_noise:
            lines.append(
                f"WARNING — technical stop is inside the {max_1day_pct:.1f}% "
                "historical 1-day noise floor and will false-trigger on a bad session."
            )
        lines.append(
            f"Broker trailing stop: {adj_trailing:.1f}% → ${trailing_stop_price:.2f}."
        )
        if cost_basis > 0 and shares > 0:
            lines.append(
                f"Position: {shares} shares @ ${cost_basis:.2f}. "
                f"P&L at trailing stop: ${position['pnl_at_trailing_stop']:+.2f}."
            )

        return {
            "symbol":   sym,
            "price":    price,
            "position": position,
            "technical": {
                "above_vwap":            above_vwap,
                "vwap":                  round(float(vwap), 2),
                "consecutive_bars":      vwap_bars,
                "vwap_direction":        "above" if above_vwap else "below",
                "sma_20":                round(bb_middle, 2),
                "bb_upper":              round(bb_upper, 2),
                "bb_lower":              round(bb_lower, 2),
                "rsi":                   round(rsi, 1),
                "macd_crossover":        macd_cross,
                "gamma_wall":            gamma_wall,
                "primary_support":       primary_name,
                "primary_support_price": round(primary_level, 2),
                "support_levels":        {k: round(v, 2) for k, v in sorted_supports},
            },
            "short_interest": {
                "short_float_pct":   short_float_pct,
                "short_ratio_days":  short_ratio,
                "squeeze_potential": squeeze,
                "stop_impact":       si_impact,
                "data_available":    short_available,
            },
            "drawdown": {
                "max_1day_pct":           -round(max_1day_pct, 2),
                "max_5day_pct":           -round(max_5day_pct, 2),
                "max_intraday_pct":       -round(max_intraday_pct, 2),
                "recent_max_1day_pct":    -round(recent_1day_pct, 2),
                "base_trailing_stop_pct": base_trailing,
            },
            "stops": {
                "technical_stop":                    technical_stop,
                "technical_stop_distance_pct":       technical_dist_pct,
                "technical_stop_inside_noise_floor":  stop_inside_noise,
                "trailing_stop_pct":                 adj_trailing,
                "trailing_stop_price":               trailing_stop_price,
            },
            "flags":   flags,
            "summary": " ".join(lines),
        }

    # ------------------------------------------------------------------
    # Relative strength
    # ------------------------------------------------------------------
    def get_relative_strength_history(
        self, symbol: str, since_days: int = 90, rs_period: int = 21, interval: str = "1d"
    ) -> dict:
        symbol = symbol.upper().strip()
        fetch_days = since_days + rs_period + 10
        sym_df  = self._prices.get_history(symbol, interval=interval, days=fetch_days)
        spy_df  = self._prices.get_history("SPY",  interval=interval, days=fetch_days)
        qqq_df  = self._prices.get_history("QQQ",  interval=interval, days=fetch_days)
        if sym_df is None or sym_df.empty or spy_df is None or spy_df.empty:
            return {"symbol": symbol, "error": "Insufficient OHLCV data in cache", "history": []}

        # Determine sector ETF
        sector_etf = self._get_sector_etf(symbol)
        sec_df = self._prices.get_history(sector_etf, interval=interval, days=fetch_days) if sector_etf else None

        def rolling_return(df):
            closes = df["Close"]
            return closes.pct_change(rs_period) * 100   # trailing rs_period-bar % return

        sym_ret  = rolling_return(sym_df)
        spy_ret  = rolling_return(spy_df)
        qqq_ret  = rolling_return(qqq_df)
        sec_ret  = rolling_return(sec_df) if sec_df is not None and not sec_df.empty else None

        aligned = sym_ret.to_frame("sym").join(spy_ret.rename("spy")).join(qqq_ret.rename("qqq"))
        if sec_ret is not None:
            aligned = aligned.join(sec_ret.rename("sec"))
        aligned = aligned.dropna(subset=["sym", "spy", "qqq"]).tail(since_days)

        def rs_label(vs_spy, vs_qqq):
            avg = (vs_spy + vs_qqq) / 2
            if avg >= 5:   return "leader"
            if avg >= 1:   return "outperforming"
            if avg >= -1:  return "neutral"
            if avg >= -5:  return "laggard"
            return "weak"

        history = []
        for idx, row in aligned.iterrows():
            close = sym_df.loc[sym_df.index <= idx, "Close"].iloc[-1] if idx in sym_df.index else None
            vs_spy = round(row["sym"] - row["spy"], 2)
            vs_qqq = round(row["sym"] - row["qqq"], 2)
            vs_sec = round(row["sym"] - row.get("sec"), 2) if "sec" in row and pd.notna(row.get("sec")) else None
            history.append({
                "date": idx.strftime("%Y-%m-%d"),
                "close": round(close, 2) if close else None,
                "return_pct": round(row["sym"], 2),
                "rs_vs_spy": vs_spy,
                "rs_vs_qqq": vs_qqq,
                "rs_vs_sector": vs_sec,
                "sector_etf": sector_etf,
                "rs_label": rs_label(vs_spy, vs_qqq),
            })
        return {
            "symbol": symbol,
            "rs_period_bars": rs_period,
            "sector_etf": sector_etf,
            "data_points": len(history),
            "history": history,
        }

    def get_relative_strength(self, symbol: str) -> dict:
        sym = symbol.upper()

        result: dict = {
            "symbol":                   sym,
            "sector":                   None,
            "sector_etf":               None,
            "returns": {
                "stock":  {"1m": None, "3m": None, "6m": None, "12m": None},
                "spy":    {"1m": None, "3m": None, "6m": None, "12m": None},
                "qqq":    {"1m": None, "3m": None, "6m": None, "12m": None},
                "sector": {"1m": None, "3m": None, "6m": None, "12m": None},
            },
            "rs_ratio_vs_spy":          None,
            "rs_ratio_vs_qqq":          None,
            "rs_ratio_vs_sector":       None,
            "relative_strength_label":  "unknown",
            "sector_momentum":          "unknown",
        }

        try:
            info = self._yf.info(sym) or {}
            sector = info.get("sector")
            result["sector"] = sector
            sector_etf = _SECTOR_ETF_MAP.get(sector or "", "XLK")
            result["sector_etf"] = sector_etf
        except Exception:
            sector_etf = "XLK"

        tickers_to_fetch = list(dict.fromkeys([sym, "SPY", "QQQ", sector_etf]))

        try:
            data = self._yf.download(
                tickers_to_fetch,
                period="400d",
                auto_adjust=True,
            )
            if "Close" in data.columns:
                closes = data["Close"]
            else:
                closes = data  # single-ticker fallback

            def _pct_return(series: "pd.Series", trading_days: int) -> "float | None":
                s = series.dropna()
                if len(s) < trading_days + 1:
                    return None
                start = float(s.iloc[-trading_days - 1])
                end = float(s.iloc[-1])
                if start <= 0:
                    return None
                return round((end / start - 1) * 100, 2)

            periods = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
            ticker_keys = {
                "stock":  sym,
                "spy":    "SPY",
                "qqq":    "QQQ",
                "sector": sector_etf,
            }

            for key, ticker in ticker_keys.items():
                col = closes[ticker] if ticker in closes.columns else None
                if col is None and closes.ndim == 1:
                    col = closes
                if col is None:
                    continue
                for period_name, days in periods.items():
                    result["returns"][key][period_name] = _pct_return(col, days)

            s12   = result["returns"]["stock"]["12m"]
            spy12 = result["returns"]["spy"]["12m"]
            qqq12 = result["returns"]["qqq"]["12m"]
            sec12 = result["returns"]["sector"]["12m"]

            if s12 is not None and spy12 is not None:
                result["rs_ratio_vs_spy"] = round(s12 - spy12, 2)
            if s12 is not None and qqq12 is not None:
                result["rs_ratio_vs_qqq"] = round(s12 - qqq12, 2)
            if s12 is not None and sec12 is not None:
                result["rs_ratio_vs_sector"] = round(s12 - sec12, 2)

            rs = result["rs_ratio_vs_spy"]
            if rs is not None:
                if rs >= 20:
                    result["relative_strength_label"] = "leader"
                elif rs >= 5:
                    result["relative_strength_label"] = "outperforming"
                elif rs >= -5:
                    result["relative_strength_label"] = "neutral"
                elif rs >= -20:
                    result["relative_strength_label"] = "laggard"
                else:
                    result["relative_strength_label"] = "weak"

            spy3 = result["returns"]["spy"]["3m"]
            sec3 = result["returns"]["sector"]["3m"]
            if spy3 is not None and sec3 is not None:
                diff = sec3 - spy3
                if diff >= 5:
                    result["sector_momentum"] = "sector_leading"
                elif diff >= -5:
                    result["sector_momentum"] = "sector_neutral"
                else:
                    result["sector_momentum"] = "sector_lagging"

        except Exception:
            pass

        return result

    # ------------------------------------------------------------------
    # Full 19-signal trade recommendation
    # ------------------------------------------------------------------
    def get_trade_recommendation(self, symbol: str, capital: float = 5000.0) -> dict:
        sym = symbol.upper()
        signals_collected = 0
        drivers: list[str] = []
        warnings: list[str] = []

        bull_score = 0
        bear_score = 0

        # ── 1. Price + Bollinger Bands + Options ──────────────────────────────
        price      = None
        bb_upper   = None
        bb_lower   = None
        bb_pos     = None
        avg_iv     = None
        put_call_ratio = None
        atm_call_ask   = None
        atm_put_ask    = None

        try:
            price_data = self._prices.get_stock_price(sym)
            price = price_data["price"]
            bb = price_data.get("bollinger_bands")
            if bb:
                bb_upper  = bb["upper"]
                bb_lower  = bb["lower"]
                if bb_upper != bb_lower:
                    bb_pos = round((price - bb_lower) / (bb_upper - bb_lower), 3)

                if bb_pos is not None:
                    if bb_pos <= 0:
                        bull_score += 2
                        drivers.append(f"BB position {bb_pos:.2f} — at/below lower band (oversold)")
                    elif bb_pos >= 1:
                        bear_score += 2
                        drivers.append(f"BB position {bb_pos:.2f} — at/above upper band (overbought)")

            options = price_data.get("options")
            if options:
                put_call_ratio = options.get("put_call_ratio")
                calls_data = options.get("calls", {})
                puts_data  = options.get("puts",  {})
                avg_iv     = calls_data.get("avg_iv_pct", 0.0)

                atm_calls = calls_data.get("atm_contracts", [])
                if atm_calls:
                    atm_c = min(atm_calls, key=lambda c: abs(c["strike"] - price))
                    atm_call_ask = atm_c.get("ask", 0.0) or 0.0

                atm_puts = puts_data.get("atm_contracts", [])
                if atm_puts:
                    atm_p = min(atm_puts, key=lambda c: abs(c["strike"] - price))
                    atm_put_ask = atm_p.get("ask", 0.0) or 0.0

                if put_call_ratio is not None and put_call_ratio > 2.0:
                    bear_score += 1
                    drivers.append(f"P/C ratio {put_call_ratio:.2f} — elevated put activity (bearish)")

            signals_collected += 1
        except Exception:
            pass

        if price is None:
            return {
                "symbol":     sym,
                "error":      f"Could not retrieve price for {sym}. Cannot generate recommendation.",
                "trade_type": "SKIP",
                "action":     "HOLD",
            }

        # ── 2. RSI ────────────────────────────────────────────────────────────
        rsi_val = None
        try:
            rsi_data = self._prices.get_rsi(sym)
            rsi_val  = rsi_data["rsi"]

            if rsi_val < 30:
                bull_score += 3   # +2 for <35, +1 extra for <30
                drivers.append(f"RSI {rsi_val:.1f} — deeply oversold")
            elif rsi_val < 35:
                bull_score += 2
                drivers.append(f"RSI {rsi_val:.1f} — oversold")
            elif rsi_val > 70:
                bear_score += 3   # +2 for >65, +1 extra for >70
                drivers.append(f"RSI {rsi_val:.1f} — deeply overbought")
            elif rsi_val > 65:
                bear_score += 2
                drivers.append(f"RSI {rsi_val:.1f} — overbought")

            signals_collected += 1
        except Exception:
            pass

        # ── 3. MACD ───────────────────────────────────────────────────────────
        macd_crossover = None
        try:
            macd_data      = self._prices.get_macd(sym)
            macd_crossover = macd_data["crossover"]

            if macd_crossover == "bullish_crossover":
                bull_score += 2
                drivers.append("MACD bullish crossover — momentum turning up")
            elif macd_crossover == "bullish":
                bull_score += 1
                drivers.append("MACD bullish — positive momentum")
            elif macd_crossover == "bearish_crossover":
                bear_score += 2
                drivers.append("MACD bearish crossover — momentum turning down")
            elif macd_crossover == "bearish":
                bear_score += 1
                drivers.append("MACD bearish — negative momentum")

            signals_collected += 1
        except Exception:
            pass

        # ── 4. Stochastic ─────────────────────────────────────────────────────
        stoch_k = None
        try:
            stoch_data = self._prices.get_stochastic(sym)
            stoch_k    = stoch_data["k"]

            if stoch_k < 25:
                bull_score += 2
                drivers.append(f"Stochastic %K {stoch_k:.1f} — oversold")
            elif stoch_k > 75:
                bear_score += 2
                drivers.append(f"Stochastic %K {stoch_k:.1f} — overbought")

            signals_collected += 1
        except Exception:
            pass

        # ── 5. Volume Analysis ────────────────────────────────────────────────
        obv_divergence = False
        try:
            vol_data       = self._prices.get_volume_analysis(sym)
            bottom_signal  = vol_data.get("bottom_signal", "none")
            obv_divergence = vol_data.get("obv_divergence", False)
            climax_events  = vol_data.get("climax_events", [])

            bs_lower = bottom_signal.lower()
            if "strong" in bs_lower or "moderate" in bs_lower:
                bull_score += 2
                drivers.append(f"Volume bottom signal: {bottom_signal}")

            if obv_divergence:
                bull_score += 1
                drivers.append("OBV bullish divergence — accumulation beneath the surface")

            recent_up_climax = any(
                e.get("direction") == "up" for e in climax_events[-3:]
            ) if climax_events else False
            if recent_up_climax:
                bear_score += 2
                drivers.append("Volume exhaustion top — high-volume climax on up day, potential reversal")

            signals_collected += 1
        except Exception:
            pass

        # ── 6. Candlestick Patterns ───────────────────────────────────────────
        try:
            cs_data   = self._prices.get_candlestick_patterns(sym)
            patterns  = cs_data.get("patterns_found", [])

            bullish_pats = [p for p in patterns if p.get("bias") == "bullish"]
            bearish_pats = [p for p in patterns if p.get("bias") == "bearish"]

            if bullish_pats:
                best = max(bullish_pats, key=lambda p: p.get("strength_score", 0))
                bull_score += 1
                drivers.append(
                    f"Candlestick: {best['pattern']} ({best['strength']} bullish reversal)"
                )
            elif bearish_pats:
                best = max(bearish_pats, key=lambda p: p.get("strength_score", 0))
                bear_score += 1
                drivers.append(
                    f"Candlestick: {best['pattern']} ({best['strength']} bearish signal)"
                )

            signals_collected += 1
        except Exception:
            pass

        # ── 7. Unusual Calls ─────────────────────────────────────────────────
        unusual_call_activity = False
        try:
            uc_data      = self._options.get_unusual_calls(sym)
            sweep_signal = uc_data.get("sweep_signal", "none")

            if sweep_signal == "strong":
                unusual_call_activity = True
                bull_score += 2
                drivers.append("Unusual call activity: strong sweep signal — aggressive institutional buying")
            elif sweep_signal == "moderate":
                unusual_call_activity = True
                bull_score += 1
                drivers.append("Unusual call activity: moderate sweep signal")

            signals_collected += 1
        except Exception:
            pass

        # ── 8. Stop Loss Analysis ─────────────────────────────────────────────
        technical_stop    = None
        trailing_stop_pct = None
        try:
            sl_data           = self.get_stop_loss_analysis(sym)
            stops             = sl_data.get("stops", {})
            technical_stop    = stops.get("technical_stop")
            trailing_stop_pct = stops.get("trailing_stop_pct")

            signals_collected += 1
        except Exception:
            pass

        # ── 9. Short Interest ─────────────────────────────────────────────────
        squeeze_potential = "LOW"
        try:
            si_data           = self._microstructure.get_short_interest(sym)
            squeeze_potential = si_data.get("squeeze_potential", "LOW")

            if squeeze_potential == "HIGH":
                bull_score += 1
                drivers.append(
                    f"Short squeeze potential HIGH — {si_data.get('squeeze_note', '')}"
                )

            signals_collected += 1
        except Exception:
            pass

        # ── 10. Dark Pool ─────────────────────────────────────────────────────
        dark_pool_signal = "none"
        try:
            dp_data          = self._microstructure.get_dark_pool(sym)
            dark_pool_signal = dp_data.get("net_signal", "none")

            if dark_pool_signal == "accumulation":
                bull_score += 2
                drivers.append("Dark pool: accumulation — institutions absorbing sell pressure")
            elif dark_pool_signal == "distribution":
                bear_score += 2
                drivers.append("Dark pool: distribution — institutions absorbing buy pressure")

            signals_collected += 1
        except Exception:
            pass

        # ── 11. Bid/Ask Spread ────────────────────────────────────────────────
        spread_vs_norm = "unknown"
        try:
            bas_data       = self._microstructure.get_bid_ask_spread(sym)
            spread_vs_norm = bas_data.get("spread_vs_norm", "unknown")

            if spread_vs_norm == "narrowing":
                bull_score += 1
                drivers.append("Bid/ask spread narrowing — liquidity returning (bounce setup)")
            elif spread_vs_norm == "widening":
                bear_score += 1
                drivers.append("Bid/ask spread widening — liquidity stress / fear elevated")

            signals_collected += 1
        except Exception:
            pass

        # ── 12. Delta-Adjusted OI (MM Hedge Flows) ───────────────────────────
        daoi_signal     = "none"
        mm_hedge_bias   = None
        gamma_wall      = None
        delta_flip      = None
        net_daoi_shares = None
        try:
            daoi_data       = self._options.get_delta_adjusted_oi(sym)
            daoi_signal     = daoi_data.get("signal", "none")
            mm_hedge_bias   = daoi_data.get("mm_hedge_bias")
            gamma_wall      = daoi_data.get("gamma_wall_strike")
            delta_flip      = daoi_data.get("delta_flip_strike")
            net_daoi_shares = daoi_data.get("net_daoi_shares")

            if daoi_signal == "strong":
                bull_score += 2
                drivers.append("DAOI: strong MM buy-on-rally flow — mechanical support amplifies upside")
            elif daoi_signal == "moderate":
                bull_score += 2
                drivers.append("DAOI: moderate MM buy-on-rally flow — hedging supports rally")
            elif daoi_signal == "weak":
                bull_score += 1
                drivers.append("DAOI: weak MM buy-on-rally bias")

            if mm_hedge_bias == "sell_on_rally":
                warnings.append(
                    "MM hedge bias: sell_on_rally — market makers must sell stock as price rises (mechanical resistance)"
                )

            signals_collected += 1
        except Exception:
            pass

        # ── 13. Options Market Directional Positioning ───────────────────────
        if net_daoi_shares is not None:
            if net_daoi_shares > 50_000:
                bull_score += 1
                drivers.append(
                    f"Options positioning: {net_daoi_shares:,.0f} net call delta — institutions overwhelmingly long"
                )
            elif net_daoi_shares < -50_000:
                bear_score += 1
                drivers.append(
                    f"Options positioning: {net_daoi_shares:,.0f} net delta — heavy put/defensive hedging"
                )
            signals_collected += 1

        # ── 14. Earnings Calendar ─────────────────────────────────────────────
        earnings_days_out = None
        earnings_risk = "UNKNOWN"
        pre_earnings_setup = False
        try:
            ec_data = self._fundamentals.get_earnings_calendar(sym)
            earnings_days_out = ec_data.get("days_to_earnings")
            earnings_risk = ec_data.get("risk_level", "UNKNOWN")
            pre_earnings_setup = ec_data.get("pre_earnings_setup", False)
            avg_move_pct = ec_data.get("historical_avg_move_pct")

            if earnings_days_out is not None:
                if earnings_days_out < 7:
                    warnings.append(
                        f"CRITICAL: Earnings in {earnings_days_out} days — avoid new options positions (IV crush imminent)"
                    )
                elif earnings_days_out < 14:
                    warnings.append(
                        f"Earnings in {earnings_days_out} days — options positions carry IV crush risk post-earnings"
                    )
                elif pre_earnings_setup:
                    bull_score += 1
                    note = f"{earnings_days_out} days to earnings"
                    if avg_move_pct:
                        note += f", avg historical move ±{avg_move_pct:.1f}%"
                    drivers.append(f"Pre-earnings IV expansion: {note} — long calls benefit from IV buildup")

            signals_collected += 1
        except Exception:
            pass

        # ── 15. Fundamental Score ─────────────────────────────────────────────
        fund_composite = None
        try:
            fund_data = self._fundamentals.get_fundamental_score(sym)
            fund_composite = fund_data.get("composite_score", 0)
            fund_label = fund_data.get("fundamental_label", "average")

            if fund_composite >= 8:
                bull_score += 2
                drivers.append(f"Fundamentals: {fund_label} — strong compounder (score {fund_composite})")
            elif fund_composite >= 4:
                bull_score += 1
                drivers.append(f"Fundamentals: {fund_label} (score {fund_composite})")
            elif fund_composite <= -4:
                bear_score += 2
                drivers.append(f"Fundamentals: {fund_label} — deteriorating business (score {fund_composite})")
            elif fund_composite < 0:
                bear_score += 1
                drivers.append(f"Fundamentals: {fund_label} (score {fund_composite})")

            signals_collected += 1
        except Exception:
            pass

        # ── 16. Revenue Growth Trajectory ────────────────────────────────────
        try:
            rev_data = self._fundamentals.get_revenue_growth(sym)
            trajectory = rev_data.get("trajectory", "")

            if trajectory in ("accelerating", "inflecting_positive"):
                bull_score += 1
                drivers.append(f"Revenue: {trajectory} — fundamental momentum building")
            elif trajectory in ("decelerating", "inflecting_negative"):
                bear_score += 1
                drivers.append(f"Revenue: {trajectory} — fundamental headwind")

            signals_collected += 1
        except Exception:
            pass

        # ── 17. Earnings Acceleration (CAN SLIM 'A') ─────────────────────────
        try:
            ea_data = self._fundamentals.get_earnings_acceleration(sym)
            ea_score = ea_data.get("acceleration_score", 0)
            ea_label = ea_data.get("acceleration_label", "")

            if ea_score > 0:
                bull_score += ea_score
                drivers.append(f"EPS acceleration: {ea_label} — institutional accumulation signal")
            elif ea_score < 0:
                bear_score += abs(ea_score)
                drivers.append(f"EPS acceleration: {ea_label} — earnings deceleration warning")

            signals_collected += 1
        except Exception:
            pass

        # ── 18. Relative Strength vs Market & Sector ─────────────────────────
        try:
            rs_data = self.get_relative_strength(sym)
            rs_label = rs_data.get("relative_strength_label", "")
            rs_vs_spy = rs_data.get("rs_ratio_vs_spy")
            sector_momentum = rs_data.get("sector_momentum", "")

            if rs_label == "leader":
                bull_score += 2
                drivers.append(f"Relative strength: market leader ({rs_vs_spy:+.1f}% vs SPY over 12m)")
            elif rs_label == "outperforming":
                bull_score += 1
                drivers.append(f"Relative strength: outperforming SPY ({rs_vs_spy:+.1f}% over 12m)")
            elif rs_label == "laggard":
                bear_score += 1
                drivers.append(f"Relative strength: laggard ({rs_vs_spy:+.1f}% vs SPY over 12m)")
            elif rs_label == "weak":
                bear_score += 1
                drivers.append(f"Relative strength: weak vs SPY ({rs_vs_spy:+.1f}% over 12m)")

            if sector_momentum == "sector_leading" and bull_score > bear_score:
                bull_score += 1
                drivers.append(
                    f"Sector momentum: {rs_data.get('sector_etf', 'sector ETF')} leading market — macro tailwind"
                )

            signals_collected += 1
        except Exception:
            pass

        # ── 19. News Sentiment (FinBERT) ──────────────────────────────────────
        news_sentiment = None
        try:
            news_data = self._sentiment.get_news(sym)
            sentiment_summary = news_data.get("sentiment_summary")
            if sentiment_summary:
                scored_count = sentiment_summary.get("scored_count", 0)
                if scored_count > 0:
                    pos_pct = sentiment_summary["positive_count"] / scored_count
                    neg_pct = sentiment_summary["negative_count"] / scored_count

                    if pos_pct >= 0.60:
                        bull_score += 2
                        drivers.append(f"News sentiment: strongly positive ({pos_pct:.0%} of articles bullish)")
                    elif pos_pct >= 0.40:
                        bull_score += 1
                        drivers.append(f"News sentiment: moderately positive ({pos_pct:.0%} of articles bullish)")
                    elif neg_pct >= 0.60:
                        bear_score += 2
                        drivers.append(f"News sentiment: strongly negative ({neg_pct:.0%} of articles bearish)")
                    elif neg_pct >= 0.40:
                        bear_score += 1
                        drivers.append(f"News sentiment: moderately negative ({neg_pct:.0%} of articles bearish)")

                news_sentiment = sentiment_summary
            signals_collected += 1
        except Exception:
            pass

        # ── Aggregate Scores ──────────────────────────────────────────────────
        net_score = bull_score - bear_score

        if abs(net_score) >= 5:
            confidence = "HIGH"
        elif abs(net_score) >= 3:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        avg_iv_val = avg_iv if avg_iv is not None else 0.0
        high_iv    = avg_iv_val >= 40.0

        # Squeeze override: HIGH short interest + sufficient bull score → force LONG_CALL
        squeeze_override = squeeze_potential == "HIGH" and net_score >= 3

        # ── Trade Type Selection ──────────────────────────────────────────────
        if squeeze_override:
            trade_type = "LONG_CALL"
            action     = "BUY"
        elif net_score >= 5:
            trade_type = "BULL_CALL_SPREAD" if high_iv else "LONG_CALL"
            action     = "BUY"
        elif net_score >= 3:
            trade_type = "LONG_STOCK"
            action     = "BUY"
        elif net_score >= 1:
            trade_type = "WEAK_LONG"
            action     = "BUY"
            warnings.append("Low confidence — consider reducing position size")
        elif net_score >= -2:
            trade_type = "SKIP"
            action     = "HOLD"
            warnings.append("Signals conflicting or neutral — no clear directional edge")
        elif net_score >= -4:
            trade_type = "LONG_PUT"
            action     = "BUY"
        else:   # net_score <= -5
            trade_type = "BEAR_PUT_SPREAD" if high_iv else "LONG_PUT"
            action     = "BUY"

        is_options_trade = trade_type in ("LONG_CALL", "BULL_CALL_SPREAD", "LONG_PUT", "BEAR_PUT_SPREAD")
        if earnings_days_out is not None and earnings_days_out < 7 and is_options_trade and net_score < 7:
            trade_type = "SKIP"
            action = "HOLD"
            warnings.append(
                f"Earnings override: {earnings_days_out} days to earnings — options trade suppressed to avoid IV crush"
            )

        is_long  = trade_type in ("LONG_CALL", "BULL_CALL_SPREAD", "LONG_STOCK", "WEAK_LONG")
        is_short = trade_type in ("LONG_PUT", "BEAR_PUT_SPREAD")

        # ── Target ────────────────────────────────────────────────────────────
        target = None
        if is_long and bb_upper is not None:
            target = bb_upper
        elif is_short and bb_lower is not None:
            target = bb_lower

        # ── Stop Loss ─────────────────────────────────────────────────────────
        stop_loss = None
        if is_long:
            stop_loss = technical_stop   # get_stop_loss_analysis places this below price
            if stop_loss is None or stop_loss >= price:
                if trailing_stop_pct is not None:
                    stop_loss = round(price * (1 - trailing_stop_pct / 100), 2)
                else:
                    stop_loss = round(price * 0.95, 2)
                if technical_stop is not None and technical_stop >= price:
                    warnings.append(
                        f"Technical stop ${technical_stop:.2f} was above entry — using trailing stop fallback"
                    )
        elif is_short:
            if trailing_stop_pct is not None:
                stop_loss = round(price * (1 + trailing_stop_pct / 100), 2)
            else:
                stop_loss = round(price * 1.05, 2)

        # ── Risk/Reward ───────────────────────────────────────────────────────
        risk_reward_ratio = None
        if target is not None and stop_loss is not None:
            reward = abs(target - price)
            risk   = abs(price - stop_loss)
            if risk > 0:
                risk_reward_ratio = round(reward / risk, 2)

        # ── Position Sizing (2% risk budget) ─────────────────────────────────
        risk_budget    = capital * 0.02
        position_size  = 0
        estimated_cost = 0.0

        is_options = trade_type in ("LONG_CALL", "BULL_CALL_SPREAD", "LONG_PUT", "BEAR_PUT_SPREAD")

        if trade_type == "SKIP":
            position_size  = 0
            estimated_cost = 0.0
        elif is_options:
            option_ask = atm_call_ask if is_long else atm_put_ask
            if option_ask and option_ask > 0:
                contracts     = math.floor(risk_budget / (option_ask * 100))
                position_size = max(1, contracts)
                estimated_cost = min(position_size * option_ask * 100, capital)
            else:
                position_size  = 1
                estimated_cost = 0.0
        else:
            if stop_loss is not None:
                risk_per_share = abs(price - stop_loss)
                if risk_per_share > 0:
                    position_size  = max(0, math.floor(risk_budget / risk_per_share))
                    estimated_cost = min(position_size * price, capital)

        # ── Contradiction Warnings ────────────────────────────────────────────
        if bull_score > 0 and bear_score > 0:
            warnings.append(
                f"Mixed signals: {bull_score} bull pts vs {bear_score} bear pts — some signals contradict"
            )
        if rsi_val is not None and macd_crossover is not None:
            if rsi_val < 30 and "bearish" in str(macd_crossover):
                warnings.append("RSI deeply oversold but MACD still bearish — wait for momentum confirmation")
            elif rsi_val > 70 and "bullish" in str(macd_crossover):
                warnings.append("RSI deeply overbought with MACD still bullish — caution, late in the move")
        if unusual_call_activity and mm_hedge_bias == "sell_on_rally":
            warnings.append(
                "Strong call sweeps vs MM sell-on-rally: smart money buying but structure caps upside — confirm with price action"
            )
        if net_daoi_shares is not None and net_daoi_shares > 50_000 and net_score < -2:
            warnings.append(
                "Heavy call positioning conflicts with bearish technical signals — options market disagrees with price action"
            )

        # ── Options Context ───────────────────────────────────────────────────
        options_context = None
        if is_options:
            options_context = {
                "avg_iv_pct":      avg_iv_val,
                "high_iv":         high_iv,
                "atm_call_ask":    atm_call_ask,
                "atm_put_ask":     atm_put_ask,
                "put_call_ratio":  put_call_ratio,
                "mm_hedge_bias":   mm_hedge_bias,
                "gamma_wall":      gamma_wall,
                "delta_flip":      delta_flip,
                "net_daoi_shares": net_daoi_shares,
            }

        return {
            "symbol":            sym,
            "price":             price,
            "trade_type":        trade_type,
            "action":            action,
            "confidence":        confidence,
            "bull_score":        bull_score,
            "bear_score":        bear_score,
            "net_score":         net_score,
            "entry":             price,
            "target":            target,
            "stop_loss":         stop_loss,
            "risk_reward_ratio": risk_reward_ratio,
            "position_size":     position_size,
            "estimated_cost":    round(estimated_cost, 2),
            "drivers":           drivers,
            "warnings":          warnings,
            "signals_collected": signals_collected,
            "options_context":   options_context,
            "news_sentiment":    news_sentiment,
        }
