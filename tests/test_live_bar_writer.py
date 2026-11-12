"""The live cache writer (#518 PR 5): quotes written through to ``ohlcv`` as
today's forming ``OPEN`` daily bars.

Real PricesService, repository and database; only the Yahoo gateway is
stubbed. The rules under test are the writer's: the bar's ``ts``, regular
session fields only, never overwrite a ``CLOSED`` bar, skip unusable quotes.
"""
import datetime
import math
import unittest
from contextlib import closing
from unittest import mock

import pandas as pd
import pytz

from quantcore.analytics.market_time import session_bar_ts
from quantcore.db import get_connection
from quantcore.gateways.yfinance_gateway import Quote
from quantcore.repositories.ohlcv_repository import OhlcvRepository
from quantcore.services.prices import PricesService, _live_daily_bar

SYM = "ZZLIVEBAR"
OTHER = "ZZLIVEBAR2"


def midnight_utc(day: datetime.date) -> int:
    return int(datetime.datetime.combine(
        day, datetime.time(0), tzinfo=datetime.timezone.utc).timestamp())


def at(day: datetime.date, hour: int, minute: int = 0, tz: str = "America/New_York") -> int:
    """Epoch seconds of a local wall-clock time on ``day`` in ``tz``."""
    local = pytz.timezone(tz).localize(
        datetime.datetime.combine(day, datetime.time(hour, minute)))
    return int(local.timestamp())


def quote(regular_time, *, open_=10.0, high=12.0, low=9.0, price=11.0, volume=5000,
          tz="America/New_York", pre=None, post=None):
    return Quote(
        regular_open=open_, regular_high=high, regular_low=low,
        regular_price=price, regular_volume=volume, regular_time=regular_time,
        previous_close=10.5, regular_change_pct=1.0,
        pre_price=pre, pre_change_pct=None, pre_time=regular_time - 3600 if pre else None,
        post_price=post, post_change_pct=None, post_time=regular_time + 3600 if post else None,
        market_state="REGULAR", currency="USD", exchange_tz=tz,
    )


class SessionBarTsTest(unittest.TestCase):
    """The ``ts`` rule: midnight UTC of the session's date on its exchange."""

    DAY = datetime.date(2026, 11, 10)

    def test_us_session(self):
        # 4pm ET is 21:00 UTC the same day.
        self.assertEqual(session_bar_ts(at(self.DAY, 16), "America/New_York"),
                         midnight_utc(self.DAY))
        self.assertEqual(midnight_utc(self.DAY), 1794268800)

    def test_seoul_session(self):
        self.assertEqual(session_bar_ts(at(self.DAY, 15, 30, "Asia/Seoul"), "Asia/Seoul"),
                         midnight_utc(self.DAY))

    def test_session_whose_utc_date_is_the_day_before(self):
        # 10:30 in Sydney is 23:30 UTC on the previous day. The bar belongs to
        # the local date; dating it by UTC would file it under the wrong day.
        instant = at(self.DAY, 10, 30, "Australia/Sydney")
        self.assertEqual(
            datetime.datetime.fromtimestamp(instant, tz=datetime.timezone.utc).date(),
            self.DAY - datetime.timedelta(days=1))
        self.assertEqual(session_bar_ts(instant, "Australia/Sydney"), midnight_utc(self.DAY))

    def test_unknown_timezone_is_none(self):
        self.assertIsNone(session_bar_ts(at(self.DAY, 16), "Mars/Olympus"))
        self.assertIsNone(session_bar_ts(at(self.DAY, 16), None))


class LiveDailyBarTest(unittest.TestCase):
    """``_live_daily_bar``: which quotes become a bar, and from which fields."""

    DAY = datetime.date(2026, 11, 10)

    def test_regular_session_fields_only(self):
        bar = _live_daily_bar("AAA", quote(at(self.DAY, 16), pre=500.0, post=999.0))
        self.assertEqual(bar, {"symbol": "AAA", "ts": midnight_utc(self.DAY), "open": 10.0,
                               "high": 12.0, "low": 9.0, "close": 11.0, "volume": 5000})

    def test_unusable_quotes_are_skipped(self):
        when = at(self.DAY, 16)
        for label, q in {
            "no quote": None,
            "no time": quote(None),
            "zero open (foreign listing before its open)": quote(when, open_=0.0),
            "zero high": quote(when, high=0.0),
            "zero low": quote(when, low=0.0),
            "zero price": quote(when, price=0.0),
            "negative price": quote(when, price=-4.08),
            "missing open": quote(when, open_=None),
            "NaN high": quote(when, high=math.nan),
            "infinite low": quote(when, low=math.inf),
            "negative volume": quote(when, volume=-1),
            "unknown timezone": quote(when, tz="Mars/Olympus"),
            "no timezone": quote(when, tz=None),
        }.items():
            with self.subTest(label):
                self.assertIsNone(_live_daily_bar("AAA", q))

    def test_missing_volume_is_stored_as_zero(self):
        self.assertEqual(_live_daily_bar("AAA", quote(at(self.DAY, 10), volume=None))["volume"], 0)


class LiveBarWriterTest(unittest.TestCase):
    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.repo = OhlcvRepository()
        self.gateway = mock.Mock()
        self.service = self._service()
        # Five CLOSED business-day bars ending three business days ago.
        end = pd.Timestamp.now(tz="UTC").normalize() - pd.offsets.BDay(3)
        self.history = pd.bdate_range(end=end, periods=5, tz="UTC")
        prices = [100.0 + i for i in range(5)]
        self.repo.store_bars(SYM, "1d", pd.DataFrame(
            {"Open": prices, "High": [p + 1 for p in prices], "Low": [p - 1 for p in prices],
             "Close": prices, "Volume": [1_000_000] * 5}, index=self.history))
        self.last_closed = self.history[-1].date()
        self.session = (self.history[-1] + pd.offsets.BDay(1)).date()   # no bar yet

    def _service(self):
        # A fresh service has an empty quote cache, so every call fetches.
        return PricesService(
            ohlcv_repository=self.repo, yfinance_gateway=self.gateway,
            options_repository=mock.Mock(), sentiment_repository=mock.Mock())

    @staticmethod
    def _purge():
        with closing(get_connection()) as conn:
            for sym in (SYM, OTHER):
                conn.execute("DELETE FROM ohlcv WHERE symbol = %s", (sym,))
                conn.execute("DELETE FROM fetch_log WHERE symbol = %s", (sym,))
            conn.commit()

    def _rows(self, symbol=SYM, day=None):
        sql = ("SELECT ts, open, high, low, close, volume, status, adj_close, ingested_at "
               "FROM ohlcv WHERE symbol = %s AND interval = '1d'")
        args = [symbol]
        if day is not None:
            sql += " AND ts = %s"
            args.append(midnight_utc(day))
        with closing(get_connection()) as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY ts", args).fetchall()]

    def _quotes(self, quotes, service=None):
        self.gateway.batch_quotes.return_value = quotes
        return (service or self._service()).get_batch_quotes(list(quotes))

    def test_writes_an_open_bar_at_the_session_ts_from_regular_fields(self):
        out = self._quotes({SYM: quote(at(self.session, 11), pre=500.0, post=999.0)})
        # The quote the caller gets is the latest price; the bar is the regular session's.
        self.assertEqual(out[SYM]["price"], 999.0)
        row, = self._rows(day=self.session)
        self.assertEqual(
            (row["open"], row["high"], row["low"], row["close"], row["volume"], row["status"]),
            (10.0, 12.0, 9.0, 11.0, 5000, "OPEN"))
        self.assertIsNone(row["adj_close"])
        self.assertEqual(len(self._rows()), 6)

    def test_foreign_symbol_is_dated_on_its_own_exchange(self):
        instant = at(self.session, 10, 30, "Australia/Sydney")   # the day before, in UTC
        self._quotes({SYM: quote(instant, tz="Australia/Sydney")})
        self.assertEqual(len(self._rows(day=self.session)), 1)
        self.assertEqual(len(self._rows()), 6)   # and no bar on the UTC date
        self._quotes({SYM: quote(at(self.session, 14, 0, "Asia/Seoul"), tz="Asia/Seoul",
                                 price=11.5)})
        row, = self._rows(day=self.session)
        self.assertEqual(row["close"], 11.5)
        self.assertEqual(len(self._rows()), 6)

    def test_a_closed_bar_is_never_overwritten(self):
        # A weekend or after-close-out read: the quote's session is one whose
        # bar is already final.
        before = self._rows(day=self.last_closed)
        self._quotes({SYM: quote(at(self.last_closed, 16), price=1.23, volume=7)})
        self.assertEqual(self._rows(day=self.last_closed), before)
        self.assertEqual(before[0]["status"], "CLOSED")
        self.assertEqual(len(self._rows()), 5)

    def test_bar_is_dated_by_the_quote_not_by_the_clock(self):
        # On a holiday the quote still carries the last session's time. The
        # bar goes on that session's date, whatever day the write happens.
        with mock.patch("quantcore.repositories.ohlcv_repository.time",
                        return_value=at(self.session, 12) + 5 * 86400):
            self._quotes({SYM: quote(at(self.session, 16))})
        rows = self._rows()
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[-1]["ts"], midnight_utc(self.session))

    def test_unusable_quotes_write_nothing(self):
        when = at(self.session, 11)
        self._quotes({SYM: quote(when, open_=0.0, high=0.0, low=0.0)})
        self._quotes({SYM: quote(when, high=math.nan)})
        self._quotes({SYM: None})
        self.assertEqual(len(self._rows()), 5)

    def test_one_bad_quote_does_not_block_the_others(self):
        self.repo.store_bars(OTHER, "1d", pd.DataFrame(
            {"Open": [50.0], "High": [51.0], "Low": [49.0], "Close": [50.0],
             "Volume": [10]}, index=self.history[-1:]))
        when = at(self.session, 11)
        self._quotes({SYM: quote(when, low=0.0), OTHER: quote(when)})
        self.assertEqual(self._rows(day=self.session), [])
        self.assertEqual(len(self._rows(OTHER, day=self.session)), 1)

    def test_repeat_write_updates_the_same_row_and_advances_ingested_at(self):
        with mock.patch("quantcore.repositories.ohlcv_repository.time", return_value=1000):
            self._quotes({SYM: quote(at(self.session, 10), price=11.0, volume=100)})
        with mock.patch("quantcore.repositories.ohlcv_repository.time", return_value=1060):
            self._quotes({SYM: quote(at(self.session, 11), price=11.8, high=12.5, volume=900)})
        row, = self._rows(day=self.session)
        self.assertEqual((row["close"], row["high"], row["volume"], row["ingested_at"]),
                         (11.8, 12.5, 900, 1060))
        self.assertEqual(len(self._rows()), 6)
        self.assertEqual(self.repo.open_bar_state(SYM, "1d"),
                         (midnight_utc(self.session), 1060))

    def test_a_harvester_adj_close_survives_a_live_write(self):
        self._quotes({SYM: quote(at(self.session, 10))})
        with closing(get_connection()) as conn:
            conn.execute(
                "UPDATE ohlcv SET adj_close = 123.45 "
                "WHERE symbol = %s AND interval = '1d' AND ts = %s",
                (SYM, midnight_utc(self.session)))
            conn.commit()
        self._quotes({SYM: quote(at(self.session, 11), price=11.9)})
        row, = self._rows(day=self.session)
        self.assertEqual(row["close"], 11.9)
        self.assertAlmostEqual(float(row["adj_close"]), 123.45, places=2)

    def test_no_bar_for_a_symbol_with_no_daily_history(self):
        # A lone live bar would make count_cached non-zero and stop
        # get_history's cold-start warm of the full history.
        self._quotes({OTHER: quote(at(self.session, 11))})
        self.assertEqual(self._rows(OTHER), [])
        self.assertEqual(self.repo.count_cached(OTHER, "1d"), 0)

    def test_closed_only_readers_do_not_see_the_live_bar(self):
        self._quotes({SYM: quote(at(self.session, 11))})
        self.assertEqual(len(self.repo.get_bars(SYM, "1d", days=30, include_open=True)), 6)
        self.assertEqual(len(self.repo.get_bars(SYM, "1d", days=30, include_open=False)), 5)

    def test_a_cached_quote_writes_nothing(self):
        service = self._service()
        with mock.patch.object(self.repo, "upsert_live_daily_bars",
                               wraps=self.repo.upsert_live_daily_bars) as write:
            self._quotes({SYM: quote(at(self.session, 11))}, service)
            self._quotes({SYM: quote(at(self.session, 11))}, service)   # inside the TTL
        self.assertEqual(write.call_count, 1)

    def test_a_failed_write_never_fails_the_quote(self):
        with mock.patch.object(self.repo, "upsert_live_daily_bars",
                               side_effect=RuntimeError("secret detail")), \
                self.assertLogs("quantcore.services.prices", "WARNING") as logs:
            out = self._quotes({SYM: quote(at(self.session, 11))})
        self.assertEqual(out[SYM]["price"], 11.0)
        self.assertFalse(out[SYM]["stale"])
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("secret detail", logs.output[0])


if __name__ == "__main__":
    unittest.main()
