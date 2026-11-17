"""PortfolioCheckService — the Portfolio Check for one owner's own lots.

Step 3 of docs/proposals/portfolio-check-plan.md. It only *gathers*; every
number comes from ``quantcore.analytics.portfolio_check``:

* the owner's open lots, with live prices, from ``PortfolioService.list_lots``
  (the owner-scoped SQL every portfolio page already uses: this service adds no
  SQL of its own and can only ever see the owner it is asked about);
* each held stock's close at the market state's two window dates, in one
  ``OhlcvRepository.daily_history_for_symbols`` read, falling back to
  ``PricesService.get_history`` (cache-first, Yahoo through the gateway) only
  for a stock whose stored history doesn't reach back to the first date;
* the monthly market-state file (``quantcore/data/portfolio_check_market_state.json``),
  validated once and cached for the life of the process.

Holdings outside the file's classes (outside the S&P 500, unless the refresh
pre-classified them) come back as ``Review``: classifying them at request time is
decision D2, still open.
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import pandas as pd

from quantcore.analytics import portfolio_check as pc
from quantcore.analytics.market_time import market_date

logger = logging.getLogger(__name__)

DEFAULT_MARKET_STATE_PATH = Path(__file__).resolve().parents[1] / "data" / "portfolio_check_market_state.json"
ET = "America/New_York"
HISTORY_SLACK_DAYS = 15     # extra calendar days fetched before the first window date


def _file_loader(path: Path) -> Callable[[], Mapping[str, Any]]:
    return lambda: json.loads(path.read_text(encoding="utf-8"))


def _trading_dates(index: pd.Index) -> list[datetime.date]:
    """A bars frame's index as ET trading dates (bars are stored as UTC timestamps)."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert(ET)
    return [ts.date() for ts in idx]


class PortfolioCheckService:
    def __init__(self, *, portfolio, ohlcv_repository, prices,
                 market_state_loader: Optional[Callable[[], Mapping[str, Any]]] = None,
                 clock: Callable[[], datetime.date] = market_date):
        self._portfolio = portfolio
        self._ohlcv = ohlcv_repository
        self._prices = prices
        self._load = market_state_loader or _file_loader(DEFAULT_MARKET_STATE_PATH)
        self._clock = clock
        self._state: Optional[Mapping[str, Any]] = None
        self._state_lock = threading.Lock()

    def market_state(self) -> Mapping[str, Any]:
        """The validated market state, loaded once (it changes only with a new image)."""
        with self._state_lock:
            if self._state is None:
                self._state = pc.validate_market_state(self._load())
            return self._state

    def _window_prices(self, tickers: list[str], a: datetime.date, b: datetime.date,
                       today: datetime.date) -> dict[str, tuple[float, float]]:
        days = (today - a).days + HISTORY_SLACK_DAYS
        stored = self._ohlcv.daily_history_for_symbols(tickers, days) if tickers else {}
        out = {}
        for t in tickers:
            hist = stored.get(t)
            if hist is None or hist.empty or _trading_dates(hist.index)[0] > a:
                try:
                    hist = self._prices.get_history(t, "1d", days)
                except Exception:
                    # One stock's history failing costs it its peer measure, not the report.
                    logger.warning("portfolio check: history fetch failed for %s", t, exc_info=True)
                    continue
            if hist is None or hist.empty:
                continue
            closes = pd.Series(hist["Close"].to_numpy(dtype=float), index=_trading_dates(hist.index))
            pa, pb = pc.close_on_or_before(closes, a), pc.close_on_or_before(closes, b)
            if pa is not None and pb is not None:
                out[t] = (pa, pb)
        return out

    def check(self, owner: str, *, params: pc.CheckParams = pc.CheckParams()) -> dict[str, Any]:
        """``owner``'s Portfolio Check: every holding's action, reasons and tax note, plus ideas.

        Lots without a live price are listed in ``unpriced`` and count as zero value,
        as in the prototype. Costs are purchase price × quantity, the same basis the
        portfolio pages use for gain/loss.
        """
        state = self.market_state()
        market = pc.market_state_inputs(state)
        today = self._clock()
        rows = self._portfolio.list_lots(owner, status="OPEN")

        lots, unpriced = [], set()
        for r in rows:
            qty, buy, price = r.get("quantity"), r.get("purchase_price"), r.get("current_price")
            if price is None:
                unpriced.add(r["symbol"])
            lots.append({
                "ticker": r["symbol"],
                "shares": float(qty) if qty is not None else None,
                "price": float(price) if price is not None else None,
                "cost": float(buy * qty) if buy is not None and qty is not None else None,
                "acquired": r.get("trade_date") or r.get("purchase_date"),
            })
        tickers = sorted({lot["ticker"] for lot in lots})
        a, b = market["window"]
        window = self._window_prices(tickers, a, b, today)

        if lots:
            result = pc.check_portfolio(lots, market["industries"], market["peers"], market["codes"], window,
                                        today=today, sell_edge=market["sell_edge"],
                                        sell_edge_period=market["sell_edge_period"], params=params)
        else:
            result = {"total_value": 0.0, "holdings": [], "ideas": []}
        classes = state["classes"]
        for h in result["holdings"]:
            h["company"] = (classes.get(h["ticker"], {}).get("name") or "").title()
        return {
            "as_of": today.isoformat(),
            "params": asdict(params),
            "market_state": {
                "generated": state["generated"],
                "industries_asof": state["industries_asof"],
                "window": dict(state["window"]),
                "sell_edge": dict(state["sell_edge"]),
            },
            "total_value": result["total_value"],
            "holdings": result["holdings"],
            "ideas": result["ideas"],
            "industries": [dict(i) for i in state["industries"]],
            "unpriced": sorted(unpriced),
        }
