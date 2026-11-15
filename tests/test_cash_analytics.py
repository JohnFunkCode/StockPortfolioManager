"""`analytics.cash`: which entries count and the running balance (#431 step 3.1).
Pure functions, no database."""

import unittest
from decimal import Decimal

from quantcore.analytics.cash import (
    _totals_by_kind,
    _with_running_balance,
    cash_ledger,
    signed_amount,
    to_cents,
)

D = Decimal


def _entry(entry_id, kind, entry_date, amount, **extra):
    return {
        "entry_id": entry_id, "kind": kind, "entry_date": entry_date,
        "amount": D(amount) if amount is not None else None, **extra,
    }


OPENING = _entry(1, "opening_balance", "2026-01-10", "1000")


class CashLedgerTest(unittest.TestCase):
    def test_no_opening_balance_means_no_balance_and_nothing_listed(self):
        ledger = cash_ledger([
            _entry(2, "buy", "2026-01-12", "-200"), _entry(3, "deposit", "2026-01-13", "50"),
        ])
        self.assertEqual(ledger["entries"], [])
        self.assertEqual((ledger["entry_count"], ledger["earlier_entry_count"]), (0, 2))
        for key in ("opening_date", "opening_balance", "balance",
                    "deposits", "withdrawals", "bought", "sold"):
            self.assertIsNone(ledger[key], key)
        self.assertFalse(ledger["negative"])
        self.assertEqual(ledger["fx_missing_count"], 0)

    def test_an_empty_ledger(self):
        ledger = cash_ledger([])
        self.assertIsNone(ledger["balance"])
        self.assertEqual((ledger["entry_count"], ledger["earlier_entry_count"]), (0, 0))

    def test_the_balance_runs_from_the_opening_balance(self):
        ledger = cash_ledger([
            _entry(5, "sell", "2026-03-01", "330.10"),
            _entry(2, "buy", "2026-01-12", "-200.25"),
            OPENING,
            _entry(4, "withdrawal", "2026-02-01", "-75"),
            _entry(3, "deposit", "2026-01-13", "50"),
        ])
        self.assertEqual((ledger["opening_date"], ledger["opening_balance"]),
                         ("2026-01-10", D("1000")))
        self.assertEqual(ledger["balance"], D("1104.85"))
        self.assertFalse(ledger["negative"])
        self.assertEqual(
            (ledger["deposits"], ledger["withdrawals"], ledger["bought"], ledger["sold"]),
            (D("50"), D("-75"), D("-200.25"), D("330.10")),
        )
        # Newest first, each with the balance once it is applied.
        self.assertEqual(
            [(e["kind"], e["balance_after"]) for e in ledger["entries"]],
            [("sell", D("1104.85")), ("withdrawal", D("774.75")), ("deposit", D("849.75")),
             ("buy", D("799.75")), ("opening_balance", D("1000"))],
        )
        self.assertEqual((ledger["entry_count"], ledger["earlier_entry_count"]), (5, 0))

    def test_the_balance_is_the_sum_of_the_amounts_shown(self):
        ledger = cash_ledger([
            OPENING, _entry(2, "buy", "2026-01-12", "-33.33"), _entry(3, "buy", "2026-01-12", "-33.33"),
            _entry(4, "sell", "2026-01-13", "0.01"),
        ])
        self.assertEqual(ledger["balance"], sum(e["amount"] for e in ledger["entries"]))

    def test_an_entry_before_the_opening_date_is_not_counted_or_listed(self):
        ledger = cash_ledger([
            _entry(2, "buy", "2026-01-09", "-500"),
            OPENING,
            _entry(3, "buy", "2026-01-10", "-100"),
        ])
        self.assertEqual(ledger["balance"], D("900"))
        self.assertEqual([e["entry_id"] for e in ledger["entries"]], [3, 1])
        self.assertEqual((ledger["entry_count"], ledger["earlier_entry_count"]), (2, 1))
        self.assertEqual(ledger["bought"], D("-100"))

    def test_on_the_opening_date_the_opening_balance_is_applied_first(self):
        # The buy has the lower id and the same date. It still comes after.
        ledger = cash_ledger([_entry(1, "buy", "2026-01-10", "-100"),
                              _entry(2, "opening_balance", "2026-01-10", "1000")])
        self.assertEqual(
            [(e["kind"], e["balance_after"]) for e in ledger["entries"]],
            [("buy", D("900")), ("opening_balance", D("1000"))],
        )

    def test_same_day_entries_are_applied_in_the_order_they_were_written(self):
        ledger = cash_ledger([
            OPENING, _entry(7, "withdrawal", "2026-02-01", "-10"), _entry(6, "deposit", "2026-02-01", "5"),
        ])
        self.assertEqual([e["entry_id"] for e in ledger["entries"]], [7, 6, 1])

    def test_an_entry_with_no_amount_is_flagged_and_left_out(self):
        ledger = cash_ledger([
            OPENING, _entry(2, "buy", "2026-01-12", None), _entry(3, "buy", "2026-01-13", "-100"),
        ])
        self.assertEqual(ledger["balance"], D("900"))
        self.assertEqual(ledger["fx_missing_count"], 1)
        self.assertEqual(ledger["bought"], D("-100"))
        flagged = ledger["entries"][1]
        self.assertTrue(flagged["fx_missing"])
        # It moves nothing: the balance after it is the balance before it.
        self.assertEqual(flagged["balance_after"], D("1000"))
        self.assertFalse(ledger["entries"][0]["fx_missing"])

    def test_a_balance_below_zero_is_reported_not_refused(self):
        ledger = cash_ledger([OPENING, _entry(2, "buy", "2026-01-12", "-1000.01")])
        self.assertEqual(ledger["balance"], D("-0.01"))
        self.assertTrue(ledger["negative"])
        self.assertFalse(cash_ledger([OPENING, _entry(2, "buy", "2026-01-12", "-1000")])["negative"])

    def test_a_negative_opening_balance_is_a_fact_too(self):
        ledger = cash_ledger([_entry(1, "opening_balance", "2026-01-10", "-250")])
        self.assertEqual((ledger["opening_balance"], ledger["balance"]), (D("-250"), D("-250")))
        self.assertTrue(ledger["negative"])

    def test_other_keys_pass_through_and_the_input_is_not_changed(self):
        buy = _entry(2, "buy", "2026-01-12", "-10", symbol="ZZ", shares=D("1"), notes=None)
        before = dict(buy)
        ledger = cash_ledger([OPENING, buy])
        self.assertEqual(ledger["entries"][0]["symbol"], "ZZ")
        self.assertEqual(buy, before)


class RunningBalanceTest(unittest.TestCase):
    def test_it_keeps_the_order_and_carries_the_balance(self):
        rows = _with_running_balance([
            _entry(1, "deposit", "2026-01-01", "10"),
            _entry(2, "buy", "2026-01-02", None),
            _entry(3, "buy", "2026-01-03", "-4"),
        ])
        self.assertEqual(
            [(r["entry_id"], r["fx_missing"], r["balance_after"]) for r in rows],
            [(1, False, D("10")), (2, True, D("10")), (3, False, D("6"))],
        )

    def test_nothing_in_nothing_out(self):
        self.assertEqual(_with_running_balance([]), [])


class TotalsByKindTest(unittest.TestCase):
    def test_each_kind_is_summed_signed_and_the_opening_is_in_none(self):
        totals = _totals_by_kind([
            OPENING,
            _entry(2, "deposit", "2026-01-11", "10"), _entry(3, "deposit", "2026-01-12", "5"),
            _entry(4, "withdrawal", "2026-01-12", "-3"),
            _entry(5, "buy", "2026-01-12", "-20"), _entry(6, "buy", "2026-01-12", None),
            _entry(7, "sell", "2026-01-13", "8"),
        ])
        self.assertEqual(totals, {
            "deposits": D("15"), "withdrawals": D("-3"), "bought": D("-20"), "sold": D("8"),
        })

    def test_a_kind_with_no_entries_is_zero(self):
        self.assertEqual(set(_totals_by_kind([]).values()), {D("0")})


class SignedAmountTest(unittest.TestCase):
    def test_the_kind_gives_the_sign(self):
        self.assertEqual(signed_amount("deposit", D("12.5")), D("12.50"))
        self.assertEqual(signed_amount("withdrawal", D("12.5")), D("-12.50"))

    def test_it_rounds_to_the_cent(self):
        self.assertEqual(signed_amount("deposit", D("10.006")), D("10.01"))
        self.assertEqual(signed_amount("withdrawal", D("10.004")), D("-10.00"))

    def test_a_half_cent_rounds_away_from_zero_like_the_database(self):
        # Decimal's default would send 10.005 to 10.00 and 10.015 to 10.02.
        self.assertEqual(to_cents(D("10.005")), D("10.01"))
        self.assertEqual(to_cents(D("10.015")), D("10.02"))
        self.assertEqual(to_cents(D("-10.005")), D("-10.01"))
        self.assertEqual(signed_amount("deposit", D("0.005")), D("0.01"))

    def test_an_amount_that_is_not_above_zero_is_refused(self):
        for amount in ("0", "-5", "0.004"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                signed_amount("deposit", D(amount))

    def test_only_a_deposit_or_withdrawal_has_a_typed_amount(self):
        for kind in ("buy", "sell", "opening_balance", ""):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                signed_amount(kind, D("1"))


if __name__ == "__main__":
    unittest.main()
