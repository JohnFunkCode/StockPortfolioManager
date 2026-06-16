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
