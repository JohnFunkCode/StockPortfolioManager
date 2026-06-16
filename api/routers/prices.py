"""Prices, technicals, technical/risk signals, and the securities screener.

Heavy analytics surface — Option A pass-through: handlers return the service
dict verbatim via ``QuantCoreJSONResponse`` with no response_model, so the
volatile shapes ship byte-for-byte. Error handling mirrors Flask exactly:
ohlcv/technicals/screen wrap exceptions as the plain ``{"error": str}`` 500;
the two signals routes have no try/except and fall through to the framework
exception handler.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from ..deps import load_portfolio, load_watchlist, route_error_plain, services
from ..json_response import QuantCoreJSONResponse

router = APIRouter(prefix="/api/securities", tags=["prices"])


@router.get("/{ticker}/ohlcv")
def get_ohlcv(ticker: str, days: int = 180) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().prices.get_ohlcv_bars(ticker, days))
    except Exception as exc:  # noqa: BLE001 — parity with Flask
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/technicals")
def get_technicals(ticker: str, days: int = 365) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().prices.get_technicals_table(ticker, days))
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/signals/technical")
def get_signals_technical(ticker: str) -> QuantCoreJSONResponse:
    # No try/except — matches Flask; errors fall through to the framework handler.
    return QuantCoreJSONResponse(services().prices.get_technical_signals(ticker))


@router.get("/{ticker}/signals/risk")
def get_signals_risk(ticker: str) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().prices.get_risk_signals(ticker))


# --------------------------------------------------------------------------- #
# Phase 3 Step 1 surface-gap endpoints (previously MCP-only) — granular
# per-indicator analytics. Each route is one PricesService call deep and ships
# the service dict verbatim, mirroring the stock-price MCP tool signatures so the
# wrappers convert to a single rest_client call with no payload drift.
# --------------------------------------------------------------------------- #
@router.get("/{ticker}/price-summary")
def get_price_summary(ticker: str) -> QuantCoreJSONResponse:
    """Current price, Bollinger Bands (20d, 2σ), and options-chain summary."""
    try:
        return QuantCoreJSONResponse(services().prices.get_stock_price(ticker))
    except Exception as exc:  # noqa: BLE001 — parity with the other analytics GETs
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/rsi")
def get_rsi(ticker: str, period: int = 14, interval: str = "1d") -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().prices.get_rsi(ticker, period, interval))
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/macd")
def get_macd(ticker: str, interval: str = "1d") -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().prices.get_macd(ticker, interval))
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/stochastic")
def get_stochastic(
    ticker: str, k_period: int = 14, d_period: int = 3, interval: str = "1d"
) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().prices.get_stochastic(ticker, k_period, d_period, interval)
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/volume")
def get_volume_analysis(
    ticker: str, lookback: int = 20, interval: str = "1d"
) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().prices.get_volume_analysis(ticker, lookback, interval)
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/obv")
def get_obv(ticker: str, lookback: int = 20, interval: str = "1d") -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().prices.get_obv(ticker, lookback, interval))
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


# /vwap/history is declared before /vwap so the literal sub-path is never shadowed.
@router.get("/{ticker}/vwap/history")
def get_vwap_history(
    ticker: str, since_days: int = 90, lookback: int = 20, interval: str = "1d"
) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().prices.get_vwap_history(ticker, since_days, lookback, interval)
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/vwap")
def get_vwap(ticker: str, lookback: int = 20, interval: str = "1d") -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(services().prices.get_vwap(ticker, lookback, interval))
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/candlestick")
def get_candlestick_patterns(
    ticker: str, lookback: int = 10, interval: str = "1d"
) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().prices.get_candlestick_patterns(ticker, lookback, interval)
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/higher-lows")
def get_higher_lows(
    ticker: str, swing_bars: int = 3, lookback_swings: int = 6, interval: str = "1h"
) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().prices.get_higher_lows(ticker, swing_bars, lookback_swings, interval)
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/gaps")
def get_gap_analysis(
    ticker: str, min_gap_pct: float = 0.5, lookback: int = 60, interval: str = "1d"
) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().prices.get_gap_analysis(ticker, min_gap_pct, lookback, interval)
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/{ticker}/drawdown")
def get_historical_drawdown(ticker: str, lookback_days: int = 252) -> QuantCoreJSONResponse:
    try:
        return QuantCoreJSONResponse(
            services().prices.get_historical_drawdown(ticker, lookback_days)
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)


@router.get("/screen")
def screen_securities(
    rsi_max: Optional[float] = None,
    rsi_min: Optional[float] = None,
    above_ma50: Optional[str] = None,
    below_ma50: Optional[str] = None,
    above_ma200: Optional[str] = None,
    below_ma200: Optional[str] = None,
    near_bb_lower: Optional[str] = None,
    near_bb_upper: Optional[str] = None,
    macd_bullish: Optional[str] = None,
    macd_bearish: Optional[str] = None,
    news_sentiment: Optional[str] = None,
    source: str = "all",
) -> QuantCoreJSONResponse:
    filters = {
        "rsi_max": rsi_max,
        "rsi_min": rsi_min,
        "above_ma50": above_ma50 == "1",
        "below_ma50": below_ma50 == "1",
        "above_ma200": above_ma200 == "1",
        "below_ma200": below_ma200 == "1",
        "near_bb_lower": near_bb_lower == "1",
        "near_bb_upper": near_bb_upper == "1",
        "macd_bullish": macd_bullish == "1",
        "macd_bearish": macd_bearish == "1",
        "news_sentiment": news_sentiment,
        "source": source,
    }
    try:
        return QuantCoreJSONResponse(
            services().prices.screen_securities(filters, load_portfolio(), load_watchlist())
        )
    except Exception as exc:  # noqa: BLE001
        return route_error_plain(str(exc), 500)
