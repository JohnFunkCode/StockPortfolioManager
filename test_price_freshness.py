"""Regression tests for daily-bar freshness — the current session must be fetchable.

The QuantUI Securities page showed prices that lagged Yahoo Finance. Root cause:
``YFinanceGateway.fetch_history`` passed ``end=utcnow().date()`` to ``yf.download``,
whose ``end`` bound is **exclusive**. Today's daily bar therefore fell outside every
request window and could never enter the OHLCV cache, so every cache-backed surface
(``/ohlcv``, ``/technicals`` and the Securities grid's ``last_close``) served the
previous session's close.

Two independent defects are pinned here:

1. **Exclusive end bound** — the window must extend *past* the current session.
2. **UTC vs ET session date** — ``utcnow()`` rolls to the next calendar date at
   20:00 ET, so the bug self-healed for the last four hours of each evening and
   reappeared every morning. The window must be anchored to the ET market date.

Both are provider-API translation concerns, so they live in the gateway (standard
§5.3 / Rule 3) — no business logic is added there. Everything is mocked: no
network, no DB. The live cross-surface oracle is ``test_price_freshness_live.py``.
"""
import datetime
import unittest
from unittest.mock import patch

import pandas as pd

from quantcore.analytics.market_time import ET
from quantcore.gateways.yfinance_gateway import YFinanceGateway

# A well-formed two-bar frame; these tests only assert on the request window.
_FRAME = pd.DataFrame(
    {"Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0],
     "Close": [1.0, 2.0], "Volume": [10, 20]},
    index=pd.to_datetime(["2026-07-17", "2026-07-20"]),
)


class FetchWindowTest(unittest.TestCase):
    """The request window handed to yf.download must cover the current session."""

    def _captured_window(self, now_utc: datetime.datetime) -> tuple[datetime.date, datetime.date]:
        """Run fetch_history with a frozen clock; return the (start, end) it requested."""
        captured: dict[str, str] = {}

        def fake_download(symbol, **kwargs):
            captured.update(kwargs)
            return _FRAME

        class FrozenDateTime(datetime.datetime):
            @classmethod
            def utcnow(cls):
                return now_utc

            @classmethod
            def now(cls, tz=None):
                aware = now_utc.replace(tzinfo=datetime.timezone.utc)
                return aware.astimezone(tz) if tz else aware

        with patch("quantcore.gateways.yfinance_gateway.yf.download", fake_download), \
             patch("quantcore.gateways.yfinance_gateway.datetime.datetime", FrozenDateTime):
            YFinanceGateway().fetch_history("AAPL", "1d", 30)

        return (
            datetime.date.fromisoformat(captured["start"]),
            datetime.date.fromisoformat(captured["end"]),
        )

    def test_window_end_is_exclusive_so_must_pass_current_session(self):
        """Mid-session: yf.download's end bound is exclusive, so end must be > today."""
        # Monday 2026-07-20, 14:00 UTC == 10:00 ET — market open, today's bar exists.
        _, end = self._captured_window(datetime.datetime(2026, 7, 20, 14, 0))
        self.assertGreater(
            end, datetime.date(2026, 7, 20),
            "yf.download's `end` is exclusive — an end of today silently drops "
            "today's bar, which is exactly the stale-price bug",
        )

    def test_window_anchored_to_et_date_not_utc_date(self):
        """After 20:00 ET the UTC date is already tomorrow; the window must not shift."""
        # Monday 2026-07-20, 23:30 UTC == 19:30 ET. utcnow() still reads 07-20 here,
        # but at 00:30 UTC (20:30 ET) it reads 07-21 — the same ET session, a
        # different UTC date. Both must produce a window covering the 07-20 session.
        _, end_evening = self._captured_window(datetime.datetime(2026, 7, 20, 23, 30))
        _, end_after_rollover = self._captured_window(datetime.datetime(2026, 7, 21, 0, 30))

        self.assertGreater(end_evening, datetime.date(2026, 7, 20))
        self.assertGreater(
            end_after_rollover, datetime.date(2026, 7, 20),
            "the UTC-date rollover at 20:00 ET must not change which session is fetched",
        )

    def test_window_covers_requested_lookback(self):
        """The start bound must still honour the caller's `days` argument."""
        start, end = self._captured_window(datetime.datetime(2026, 7, 20, 14, 0))
        self.assertGreaterEqual(
            (end - start).days, 30,
            "the fetch window must span at least the requested lookback",
        )


class CurrentSessionBarSurvivesTest(unittest.TestCase):
    """A returned current-session bar must not be filtered out downstream."""

    def test_todays_bar_is_returned_to_the_caller(self):
        today_et = datetime.datetime.now(tz=ET).date()
        frame = pd.DataFrame(
            {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [99]},
            index=pd.to_datetime([today_et.isoformat()]),
        )
        with patch("quantcore.gateways.yfinance_gateway.yf.download", return_value=frame):
            out = YFinanceGateway().fetch_history("AAPL", "1d", 30)

        self.assertFalse(out.empty, "today's bar was dropped by the gateway")
        self.assertEqual(pd.Timestamp(out.index[-1]).date(), today_et)
        self.assertAlmostEqual(float(out["Close"].iloc[-1]), 1.5)


if __name__ == "__main__":
    unittest.main()
