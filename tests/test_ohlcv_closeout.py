"""The daily job's last tail step: the OHLCV close-out (#518 PR 4).

The step re-fetches recent daily bars so today's forming ``OPEN`` bars are
stored ``CLOSED``, then alarms on any bar from an earlier session that is
still ``OPEN``. Yahoo is stubbed throughout; the finalizing tests run against
the real database, because the ``OPEN`` -> ``CLOSED`` flip is ``store_bars``'s.
"""
import datetime
import unittest
from contextlib import closing
from unittest import mock

import pandas as pd

import main
from quantcore.db import get_connection
from quantcore.repositories.ohlcv_repository import OhlcvRepository
from quantcore.services.prices import CLOSEOUT_MIN_DAYS, PricesService

SYM = "ZZCLOSEOUT"
TODAY = datetime.date(2026, 7, 14)          # a Tuesday
SATURDAY = datetime.date(2026, 7, 18)


def ts_of(day: datetime.date) -> int:
    return int(datetime.datetime.combine(
        day, datetime.time(0), tzinfo=datetime.timezone.utc).timestamp())


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_open_bars_alert(self, bar_count, symbols, symbol_count):
        self.sent.append((bar_count, symbols, symbol_count))


class _Clock:
    """Advances by ``step`` on every read, so a budget is spent deterministically."""

    def __init__(self, step=1.0):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


def fake_prices(plan=None, leftover=None, fail=(), empty=()):
    prices = mock.Mock()
    prices.closeout_plan.return_value = plan if plan is not None else {"A": 10, "B": 10}
    prices.open_daily_bars_before.return_value = leftover or {}

    def close_out(sym, days):
        if sym in fail:
            raise RuntimeError("secret detail")
        return sym not in empty

    prices.close_out_daily_bars.side_effect = close_out
    return prices


class CloseoutPlanTest(unittest.TestCase):
    """PricesService.closeout_plan: which symbols, and how far back."""

    def setUp(self):
        self.repo = mock.Mock()
        self.repo.open_daily_bars.return_value = {}
        self.repo.latest_closed_daily_ts.return_value = {}
        self.service = PricesService(
            ohlcv_repository=self.repo, yfinance_gateway=mock.Mock(),
            options_repository=mock.Mock(), sentiment_repository=mock.Mock())

    def test_open_bars_come_first_on_or_off_the_list(self):
        self.repo.open_daily_bars.return_value = {"OFFLIST": ts_of(TODAY), "AAA": ts_of(TODAY)}
        self.repo.latest_closed_daily_ts.return_value = {
            "AAA": ts_of(TODAY - datetime.timedelta(days=1)),
            "BBB": ts_of(TODAY - datetime.timedelta(days=1)),
        }
        plan = self.service.closeout_plan(["bbb", "AAA"], TODAY)
        self.assertEqual(list(plan), ["AAA", "OFFLIST", "BBB"])
        self.assertEqual(set(plan.values()), {CLOSEOUT_MIN_DAYS})
        self.repo.latest_closed_daily_ts.assert_called_once_with(["BBB", "AAA"])

    def test_window_reaches_back_to_an_old_open_bar(self):
        old = TODAY - datetime.timedelta(days=30)
        self.repo.open_daily_bars.return_value = {"AAA": ts_of(old)}
        days = self.service.closeout_plan([], TODAY)["AAA"]
        self.assertGreater(days, CLOSEOUT_MIN_DAYS)
        # fetch_history's window is [today + 1 - days, today + 1).
        window_start = TODAY + datetime.timedelta(days=1) - datetime.timedelta(days=days)
        self.assertLessEqual(window_start, old)

    def test_a_symbol_closed_through_today_is_left_out(self):
        self.repo.latest_closed_daily_ts.return_value = {"AAA": ts_of(TODAY)}
        self.assertEqual(self.service.closeout_plan(["AAA"], TODAY), {})

    def test_a_symbol_with_no_cached_bars_is_left_out(self):
        # A 10-day window stored for it would stop get_history's cold-start
        # warm (count_cached == 0) from ever running.
        self.assertEqual(self.service.closeout_plan(["NEW"], TODAY), {})

    def test_close_out_stores_what_it_fetched_and_reports_an_empty_reply(self):
        frame = pd.DataFrame({"Close": [1.0]})
        self.service._yf.fetch_history.return_value = frame
        self.assertTrue(self.service.close_out_daily_bars("AAA", 12))
        self.service._yf.fetch_history.assert_called_once_with("AAA", "1d", 12)
        self.repo.store_bars.assert_called_once_with("AAA", "1d", frame)

        self.repo.store_bars.reset_mock()
        self.service._yf.fetch_history.return_value = pd.DataFrame()
        self.assertFalse(self.service.close_out_daily_bars("AAA", 12))
        self.repo.store_bars.assert_not_called()

    def test_open_bars_before_cuts_at_midnight_utc_of_today(self):
        self.repo.open_daily_bars_before.return_value = {"AAA": 2}
        self.assertEqual(self.service.open_daily_bars_before(TODAY), {"AAA": 2})
        self.repo.open_daily_bars_before.assert_called_once_with(ts_of(TODAY))


class CloseOutOhlcvTest(unittest.TestCase):
    def test_walks_the_plan_and_counts_outcomes(self):
        prices = fake_prices(plan={"A": 10, "B": 14, "C": 10, "D": 10},
                             fail={"C"}, empty={"D"})
        with mock.patch("builtins.print") as printed:
            s = main.close_out_ohlcv(["A"], prices, budget_seconds=1000, today=TODAY,
                                     sleep=mock.Mock())
        prices.closeout_plan.assert_called_once_with(["A"], TODAY)
        self.assertEqual(
            prices.close_out_daily_bars.call_args_list,
            [mock.call("A", 10), mock.call("B", 14), mock.call("C", 10), mock.call("D", 10)])
        self.assertEqual((s["planned"], s["attempted"], s["stored"], s["empty"], s["failed"]),
                         (4, 4, 2, 1, 1))
        self.assertEqual(s["failures"], ["C"])
        self.assertFalse(s["budget_exhausted"])
        # Never-log policy: the exception's class, not its text.
        output = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn("RuntimeError", output)
        self.assertNotIn("secret detail", output)

    def test_budget_stops_the_walk(self):
        prices = fake_prices(plan={s: 10 for s in "ABCDEFGH"})
        s = main.close_out_ohlcv([], prices, budget_seconds=3, clock=_Clock(),
                                 today=TODAY, sleep=mock.Mock())
        self.assertEqual(s["attempted"], 2)
        self.assertTrue(s["budget_exhausted"])

    def test_pauses_between_chunks_only(self):
        n = main.OHLCV_CLOSEOUT_CHUNK_SIZE * 2 + 1
        prices = fake_prices(plan={f"S{i}": 10 for i in range(n)})
        sleep = mock.Mock()
        main.close_out_ohlcv([], prices, budget_seconds=10_000, today=TODAY, sleep=sleep)
        self.assertEqual(sleep.call_args_list,
                         [mock.call(main.OHLCV_CLOSEOUT_PAUSE_SECONDS)] * 2)

    def test_budget_comes_from_the_environment(self):
        prices = fake_prices(plan={s: 10 for s in "ABCDEFGH"})
        with mock.patch.dict("os.environ", {main.OHLCV_CLOSEOUT_BUDGET_SECONDS_ENV: "3"}):
            s = main.close_out_ohlcv([], prices, clock=_Clock(), today=TODAY,
                                     sleep=mock.Mock())
        self.assertEqual(s["attempted"], 2)

    def test_unparseable_budget_falls_back_to_the_default(self):
        prices = fake_prices(plan={s: 10 for s in "ABCDEFGH"})
        with mock.patch.dict("os.environ", {main.OHLCV_CLOSEOUT_BUDGET_SECONDS_ENV: "soon"}), \
                mock.patch("builtins.print") as printed:
            s = main.close_out_ohlcv([], prices, clock=_Clock(), today=TODAY,
                                     sleep=mock.Mock())
        self.assertEqual(s["attempted"], 8)   # the 300 s default, not a zero budget
        self.assertIn(f"budget {main.DEFAULT_OHLCV_CLOSEOUT_BUDGET_SECONDS:.0f}s",
                      " ".join(str(c) for c in printed.call_args_list))

    def test_budget_is_clamped_to_what_is_left_of_the_job(self):
        # 1800 s task timeout - 60 s margin - 1735 s already spent = 5 s left.
        left = main._remaining_budget(
            main.DEFAULT_OHLCV_CLOSEOUT_BUDGET_SECONDS, job_started=0.0, clock=lambda: 1735.0)
        self.assertEqual(left, 5.0)
        prices = fake_prices(plan={s: 10 for s in "ABCDEFGH"})
        s = main.close_out_ohlcv([], prices, budget_seconds=left, clock=_Clock(),
                                 today=TODAY, sleep=mock.Mock())
        self.assertEqual(s["attempted"], 4)
        self.assertTrue(s["budget_exhausted"])


class AlertIfOpenBarsRemainTest(unittest.TestCase):
    def test_quiet_at_zero(self):
        n = FakeNotifier()
        prices = fake_prices()
        self.assertFalse(main.alert_if_open_bars_remain(prices, n, today=TODAY))
        self.assertEqual(n.sent, [])
        prices.open_daily_bars_before.assert_called_once_with(TODAY)

    def test_alarm_carries_the_count_and_at_most_ten_symbols(self):
        leftover = {f"S{i:02d}": 1 for i in range(12)}
        leftover["S00"] = 3
        n = FakeNotifier()
        self.assertTrue(main.alert_if_open_bars_remain(fake_prices(leftover=leftover), n,
                                                       today=TODAY))
        bar_count, symbols, symbol_count = n.sent[0]
        self.assertEqual((bar_count, symbol_count), (14, 12))
        self.assertEqual(symbols, [f"S{i:02d}" for i in range(10)])

    def test_dead_webhook_does_not_raise(self):
        n = FakeNotifier()
        n.send_open_bars_alert = mock.Mock(side_effect=RuntimeError("webhook"))
        self.assertTrue(main.alert_if_open_bars_remain(fake_prices(leftover={"A": 1}), n,
                                                       today=TODAY))


class RunOhlcvCloseoutTest(unittest.TestCase):
    def test_closes_out_then_alarms_on_what_is_left(self):
        prices = fake_prices(plan={"A": 10}, leftover={"A": 1})
        n = FakeNotifier()
        main.run_ohlcv_closeout(["A"], prices, n, budget_seconds=100, today=TODAY,
                                sleep=mock.Mock())
        self.assertEqual(
            [c[0] for c in prices.method_calls],
            ["closeout_plan", "close_out_daily_bars", "open_daily_bars_before"])
        self.assertEqual(n.sent, [(1, ["A"], 1)])

    def test_never_raises(self):
        prices = mock.Mock()
        prices.closeout_plan.side_effect = RuntimeError("secret detail")
        with mock.patch("builtins.print") as printed:
            main.run_ohlcv_closeout(["A"], prices, FakeNotifier(), today=TODAY)
        output = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn("RuntimeError", output)
        self.assertNotIn("secret detail", output)

    def test_does_nothing_on_a_closed_day(self):
        prices = fake_prices()
        n = FakeNotifier()
        main.run_ohlcv_closeout(["A"], prices, n, today=SATURDAY)
        self.assertEqual(prices.method_calls, [])
        self.assertEqual(n.sent, [])


class NotifierOpenBarsAlertTest(unittest.TestCase):
    def test_embed_names_the_count_the_sample_and_the_rest(self):
        from notifier import Notifier

        notifier = Notifier.__new__(Notifier)
        notifier.send_notifications = mock.Mock()
        notifier.send_open_bars_alert(14, ["AAA", "BBB"], 12)
        description = notifier.send_notifications.call_args.args[0]["embeds"][0]["description"]
        self.assertIn("14 daily bar(s)", description)
        self.assertIn("12 symbol(s): AAA, BBB and 10 more", description)


class CloseoutAgainstTheDatabaseTest(unittest.TestCase):
    """Real repository and PricesService; only the Yahoo fetch is stubbed."""

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.repo = OhlcvRepository()
        self.gateway = mock.Mock()
        self.prices = PricesService(
            ohlcv_repository=self.repo, yfinance_gateway=self.gateway,
            options_repository=mock.Mock(), sentiment_repository=mock.Mock())
        self.today = pd.Timestamp.now(tz="UTC").normalize().date()

    @staticmethod
    def _purge():
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM ohlcv WHERE symbol = %s", (SYM,))
            conn.execute("DELETE FROM fetch_log WHERE symbol = %s", (SYM,))
            conn.commit()

    def _bars(self, n, back):
        """``n`` business-day bars ending ``back`` business days ago: all CLOSED."""
        end = pd.Timestamp.now(tz="UTC").normalize() - pd.offsets.BDay(back)
        idx = pd.bdate_range(end=end, periods=n, tz="UTC")
        prices = [100.0 + i for i in range(n)]
        return pd.DataFrame(
            {"Open": prices, "High": [p + 1 for p in prices],
             "Low": [p - 1 for p in prices], "Close": prices,
             "Volume": [1_000_000] * n}, index=idx)

    def _leave_open(self, frame, position):
        ts = int(frame.index[position].timestamp())
        with closing(get_connection()) as conn:
            conn.execute(
                "UPDATE ohlcv SET status = 'OPEN' "
                "WHERE symbol = %s AND interval = '1d' AND ts = %s", (SYM, ts))
            conn.commit()
        return ts

    def _status(self, ts):
        with closing(get_connection()) as conn:
            return conn.execute(
                "SELECT status FROM ohlcv WHERE symbol = %s AND interval = '1d' AND ts = %s",
                (SYM, ts)).fetchone()["status"]

    def test_repository_reads(self):
        frame = self._bars(5, back=3)
        self.repo.store_bars(SYM, "1d", frame)
        self.assertNotIn(SYM, self.repo.open_daily_bars())
        self.assertEqual(self.repo.latest_closed_daily_ts([SYM.lower(), "ZZNONE"]),
                         {SYM: int(frame.index[-1].timestamp())})
        self.assertEqual(self.repo.latest_closed_daily_ts([]), {})

        self._leave_open(frame, -1)
        oldest = self._leave_open(frame, -3)
        self.assertEqual(self.repo.open_daily_bars()[SYM], oldest)
        self.assertEqual(self.repo.open_daily_bars_before(ts_of(self.today))[SYM], 2)
        self.assertNotIn(SYM, self.repo.open_daily_bars_before(oldest))
        self.assertEqual(self.repo.open_daily_bars_before(oldest + 1)[SYM], 1)

    def test_close_out_finalizes_an_open_bar_and_quiets_the_alarm(self):
        frame = self._bars(5, back=3)
        self.repo.store_bars(SYM, "1d", frame)
        ts = self._leave_open(frame, -1)
        self.gateway.fetch_history.return_value = frame
        notifier = FakeNotifier()
        self.assertEqual(self.prices.open_daily_bars_before(self.today)[SYM], 1)

        # Only this symbol's own rows are asserted on: the shared test
        # database may hold other symbols' OPEN bars.
        plan = self.prices.closeout_plan([], self.today)
        self.assertIn(SYM, plan)
        self.assertTrue(self.prices.close_out_daily_bars(SYM, plan[SYM]))

        self.assertEqual(self._status(ts), "CLOSED")
        self.assertNotIn(SYM, self.prices.open_daily_bars_before(self.today))
        self.assertEqual(notifier.sent, [])

    def test_an_open_bar_older_than_ten_days_is_inside_the_requested_window(self):
        frame = self._bars(30, back=3)
        self.repo.store_bars(SYM, "1d", frame)
        ts = self._leave_open(frame, 0)             # about six weeks back
        bar_date = frame.index[0].date()
        self.assertGreater((self.today - bar_date).days, CLOSEOUT_MIN_DAYS)

        def fetch(symbol, interval, days):
            # What Yahoo would return for the window fetch_history asks for.
            start = self.today + datetime.timedelta(days=1) - datetime.timedelta(days=days)
            return frame[frame.index.date >= start]

        self.gateway.fetch_history.side_effect = fetch
        days = self.prices.closeout_plan([], self.today)[SYM]
        self.assertTrue(self.prices.close_out_daily_bars(SYM, days))
        self.assertEqual(self._status(ts), "CLOSED")


if __name__ == "__main__":
    unittest.main()
