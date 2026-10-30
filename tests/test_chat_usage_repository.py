"""ChatUsageRepository against the test database (G2, #461).

The reservation arithmetic, shadow mode's record-anyway write, owner
isolation, and the property the design rests on: two concurrent reservations
that can't both fit are serialized by Postgres and exactly one is admitted
(docs/proposals/rate-limiting-g2-plan.md D3).
"""

import datetime
import threading
import unittest
from contextlib import closing

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.chat_usage_repository import ChatUsageRepository  # noqa: E402

OWNER = "zz_chat_usage_owner"
OTHER = "zz_chat_usage_other"
DAY = datetime.date(2026, 10, 30)


def _row(owner, day=DAY):
    with closing(get_connection()) as conn:
        return conn.execute(
            "SELECT turns, input_tokens, output_tokens, reserved_tokens FROM chat_usage "
            "WHERE owner = %s AND usage_day = %s",
            (owner, day),
        ).fetchone()


class ChatUsageRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.repo = ChatUsageRepository()
        self._purge()
        self.addCleanup(self._purge)

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM chat_usage WHERE owner IN (%s, %s)", (OWNER, OTHER))
            conn.commit()

    def _reserve(self, tokens, budget, *, owner=OWNER, new_turn=True, enforce=True):
        return self.repo.reserve(
            owner=owner,
            usage_day=DAY,
            tokens=tokens,
            budget=budget,
            new_turn=new_turn,
            enforce=enforce,
        )

    def test_first_reservation_inserts_the_row(self):
        self.assertEqual(self._reserve(40, 100), (True, 40))
        row = _row(OWNER)
        self.assertEqual(
            (row["turns"], row["input_tokens"], row["output_tokens"], row["reserved_tokens"]),
            (1, 0, 0, 40),
        )

    def test_reservations_add_up_and_count_turns(self):
        self._reserve(40, 100)
        self.assertEqual(self._reserve(30, 100, new_turn=False), (True, 70))
        self.assertEqual(_row(OWNER)["turns"], 1)

    def test_a_reservation_that_does_not_fit_writes_nothing_in_enforce(self):
        self._reserve(90, 100)
        self.assertEqual(self._reserve(20, 100), (False, None))
        self.assertEqual(_row(OWNER)["reserved_tokens"], 90)
        self.assertEqual(_row(OWNER)["turns"], 1)

    def test_a_first_reservation_over_budget_writes_nothing_in_enforce(self):
        self.assertEqual(self._reserve(150, 100), (False, None))
        self.assertIsNone(_row(OWNER))

    def test_shadow_records_an_over_budget_reservation(self):
        self._reserve(90, 100, enforce=False)
        self.assertEqual(self._reserve(20, 100, enforce=False), (False, 110))
        self.assertEqual(_row(OWNER)["reserved_tokens"], 110)

    def test_reconcile_releases_the_reservation_and_adds_actual_usage(self):
        self._reserve(80, 1000)
        self.repo.reconcile(
            owner=OWNER, usage_day=DAY, reserved=80, input_tokens=25, output_tokens=10
        )
        row = _row(OWNER)
        self.assertEqual(
            (row["input_tokens"], row["output_tokens"], row["reserved_tokens"]), (25, 10, 0)
        )
        self.assertEqual(self.repo.used(owner=OWNER, usage_day=DAY), 35)

    def test_reconcile_never_drives_reservations_negative(self):
        self._reserve(10, 1000)
        self.repo.reconcile(owner=OWNER, usage_day=DAY, reserved=50, input_tokens=0, output_tokens=0)
        self.assertEqual(_row(OWNER)["reserved_tokens"], 0)

    def test_used_counts_outstanding_reservations(self):
        self._reserve(60, 1000)
        self.repo.reconcile(owner=OWNER, usage_day=DAY, reserved=60, input_tokens=20, output_tokens=5)
        self._reserve(100, 1000, new_turn=False)
        self.assertEqual(self.repo.used(owner=OWNER, usage_day=DAY), 125)

    def test_used_is_zero_for_a_day_with_no_row(self):
        self.assertEqual(self.repo.used(owner=OWNER, usage_day=DAY), 0)

    def test_days_are_separate(self):
        self._reserve(90, 100)
        self.assertEqual(
            self.repo.reserve(
                owner=OWNER,
                usage_day=DAY + datetime.timedelta(days=1),
                tokens=90,
                budget=100,
                new_turn=True,
                enforce=True,
            ),
            (True, 90),
        )

    def test_wrong_owner_reads_and_writes_touch_nothing(self):
        self._reserve(90, 100)
        # Another owner's budget is their own: a fresh row, not this one.
        self.assertEqual(self._reserve(90, 100, owner=OTHER), (True, 90))
        self.assertEqual(self.repo.used(owner=OTHER, usage_day=DAY), 90)
        self.repo.reconcile(
            owner=OTHER, usage_day=DAY, reserved=90, input_tokens=1, output_tokens=1
        )
        row = _row(OWNER)
        self.assertEqual(
            (row["input_tokens"], row["output_tokens"], row["reserved_tokens"]), (0, 0, 90)
        )

    def test_concurrent_reservations_admit_exactly_one_when_one_fits(self):
        # Room for one 60-token call against a budget of 100, two callers at
        # once on separate connections. Repeated, since a race is a chance.
        for _ in range(50):
            self._purge()
            barrier = threading.Barrier(2)
            results = []

            def go():
                repo = ChatUsageRepository()
                barrier.wait()
                results.append(
                    repo.reserve(
                        owner=OWNER, usage_day=DAY, tokens=60, budget=100,
                        new_turn=True, enforce=True,
                    )[0]
                )

            threads = [threading.Thread(target=go) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sorted(results), [False, True])
            self.assertEqual(_row(OWNER)["reserved_tokens"], 60)

    def test_concurrent_reservations_on_an_existing_row_cannot_overshoot(self):
        # The reviewer's case on #483: 90 of 100 used, two 10-token calls at once.
        for _ in range(50):
            self._purge()
            self._reserve(90, 100)
            barrier = threading.Barrier(2)
            results = []

            def go():
                repo = ChatUsageRepository()
                barrier.wait()
                results.append(
                    repo.reserve(
                        owner=OWNER, usage_day=DAY, tokens=10, budget=100,
                        new_turn=True, enforce=True,
                    )[0]
                )

            threads = [threading.Thread(target=go) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sorted(results), [False, True])
            self.assertEqual(self.repo.used(owner=OWNER, usage_day=DAY), 100)


if __name__ == "__main__":
    unittest.main()
