"""The small helpers behind the lot row shapes and the lot-field checks.

They were extracted in plan step 2.4 (docs/proposals/paper-books-realized-pnl-plan.md)
so that `_row_to_dict`, `_normalize_row` and `_validate_lot_fields` read as a
list of fields. No database: every case here is a value in and a value out.
"""
import unittest
from datetime import date
from decimal import Decimal

from quantcore.repositories import portfolio_repository as repo
from quantcore.services import portfolio as svc


class TextAndNoneHelpersTest(unittest.TestCase):
    """The repository and the service each keep their own copy (a service
    doesn't import a repository's private names), so both are held to one
    set of cases."""

    def test_text_trims_and_fills_in_the_default(self):
        for mod in (repo, svc):
            with self.subTest(module=mod.__name__):
                self.assertEqual(mod._text("  Apple Inc "), "Apple Inc")
                self.assertEqual(mod._text(None), "")
                self.assertEqual(mod._text(""), "")
                self.assertEqual(mod._text(None, "USD"), "USD")
                self.assertEqual(mod._text("", "USD"), "USD")
                self.assertEqual(mod._text(" cad ", "USD"), "cad")

    def test_text_does_not_treat_blanks_as_empty(self):
        # Whitespace is a value, so the default is not used: the result is "".
        for mod in (repo, svc):
            with self.subTest(module=mod.__name__):
                self.assertEqual(mod._text("   ", "USD"), "")

    def test_or_none_turns_an_empty_value_into_none(self):
        for mod in (repo, svc):
            with self.subTest(module=mod.__name__):
                self.assertIsNone(mod._or_none(None))
                self.assertIsNone(mod._or_none(""))
                self.assertEqual(mod._or_none("IRA"), "IRA")
                self.assertEqual(mod._or_none(date(2026, 1, 2)), date(2026, 1, 2))
                # Not trimmed: a note keeps its spacing.
                self.assertEqual(mod._or_none(" hi "), " hi ")


class IntOrNoneTest(unittest.TestCase):
    def test_none_stays_none_and_zero_stays_zero(self):
        self.assertIsNone(repo._int_or_none(None))
        self.assertEqual(repo._int_or_none(0), 0)
        self.assertEqual(repo._int_or_none(7), 7)
        self.assertIsInstance(repo._int_or_none(Decimal("7")), int)


class RowToDictTest(unittest.TestCase):
    def _row(self, **over):
        row = {
            "name": " Apple Inc ", "symbol": " aapl ", "purchase_price": Decimal("1.5"),
            "quantity": Decimal("0.000001"), "purchase_date": "2026-01-02",
            "currency": None, "sale_price": None, "sale_date": "", "position_id": 7,
            "status": "OPEN", "parent_lot_id": None, "trade_date": date(2026, 1, 2),
            "fees": None, "acquisition_type": "", "account": None, "notes": "",
        }
        row.update(over)
        return row

    def test_a_row_with_blanks(self):
        self.assertEqual(
            repo._row_to_dict(self._row()),
            {
                "name": "Apple Inc", "symbol": "AAPL", "purchase_price": Decimal("1.5"),
                "quantity": Decimal("0.000001"), "purchase_date": "2026-01-02",
                "currency": "USD", "sale_price": None, "sale_date": None,
                "source": "portfolio", "tags": [], "lot_id": 7, "status": "OPEN",
                "parent_lot_id": None, "trade_date": date(2026, 1, 2), "fees": None,
                "acquisition_type": None, "account": None, "notes": None,
            },
        )

    def test_the_key_order_is_the_csv_order(self):
        self.assertEqual(
            list(repo._row_to_dict(self._row())),
            [
                "name", "symbol", "purchase_price", "quantity", "purchase_date",
                "currency", "sale_price", "sale_date", "source", "tags", "lot_id",
                "status", "parent_lot_id", "trade_date", "fees", "acquisition_type",
                "account", "notes",
            ],
        )

    def test_a_null_name_and_symbol_read_as_empty(self):
        row = repo._row_to_dict(self._row(name=None, symbol=None))
        self.assertEqual((row["name"], row["symbol"]), ("", ""))


class RequirePositiveTest(unittest.TestCase):
    def test_accepts_a_positive_number_in_any_form(self):
        for value in (Decimal("0.000001"), "1.5", 3, 2.5):
            with self.subTest(value=value):
                self.assertIsNone(svc._require_positive(value, "quantity"))

    def test_zero_and_negative_name_the_field(self):
        for value in (0, "0", Decimal("-1"), "-2"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    svc._require_positive(value, "purchase_price")
                self.assertEqual(str(ctx.exception), "purchase_price must be positive")

    def test_a_non_number_is_a_value_error_not_a_type_error(self):
        for value in ("abc", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    svc._require_positive(value, "quantity")
                self.assertEqual(
                    str(ctx.exception), f"quantity is not a valid number: {value!r}"
                )


class RequireIsoDateTest(unittest.TestCase):
    def test_accepts_an_iso_string_or_a_date(self):
        self.assertIsNone(svc._require_iso_date("2026-01-02", "trade_date"))
        self.assertIsNone(svc._require_iso_date(date(2026, 1, 2), "trade_date"))

    def test_anything_else_names_the_field_and_the_value(self):
        for value in ("01/02/2026", "nope", "2026-02-30"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    svc._require_iso_date(value, "trade_date")
                self.assertEqual(
                    str(ctx.exception), f"trade_date is not a valid date: {value!r}"
                )


class ValidateLotFieldsTest(unittest.TestCase):
    def test_creation_needs_all_three(self):
        for missing in ("purchase_price", "quantity", "trade_date"):
            row = {"purchase_price": "1", "quantity": "1", "trade_date": "2026-01-02"}
            del row[missing]
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError) as ctx:
                    svc._validate_lot_fields(row, require_all=True)
                self.assertEqual(
                    str(ctx.exception), "purchase_price, quantity, and trade_date are required"
                )

    def test_an_update_checks_only_what_it_sets(self):
        self.assertIsNone(svc._validate_lot_fields({}, require_all=False))
        self.assertIsNone(svc._validate_lot_fields({"quantity": "2.5"}, require_all=False))
        with self.assertRaises(ValueError):
            svc._validate_lot_fields({"quantity": "0"}, require_all=False)

    def test_the_price_is_reported_before_the_quantity_and_the_date(self):
        row = {"purchase_price": "-1", "quantity": "x", "trade_date": "nope"}
        with self.assertRaises(ValueError) as ctx:
            svc._validate_lot_fields(row, require_all=True)
        self.assertEqual(str(ctx.exception), "purchase_price must be positive")


class NormalizeRowTest(unittest.TestCase):
    def test_an_empty_row(self):
        self.assertEqual(
            svc._normalize_row({}),
            {
                "name": "", "symbol": "", "purchase_price": None, "quantity": None,
                "purchase_date": None, "trade_date": None, "currency": "USD",
                "sale_price": None, "sale_date": None, "fees": None,
                "acquisition_type": None, "account": None, "notes": None,
            },
        )

    def test_purchase_date_stands_in_for_a_missing_trade_date(self):
        row = svc._normalize_row({"purchase_date": "2026-01-02", "trade_date": ""})
        self.assertEqual(row["trade_date"], "2026-01-02")
        row = svc._normalize_row({"purchase_date": "2026-01-02", "trade_date": "2026-02-03"})
        self.assertEqual((row["purchase_date"], row["trade_date"]), ("2026-01-02", "2026-02-03"))

    def test_text_is_trimmed_and_codes_upper_cased(self):
        row = svc._normalize_row({"name": " Shop ", "symbol": " shop.to ", "currency": " cad "})
        self.assertEqual((row["name"], row["symbol"], row["currency"]), ("Shop", "SHOP.TO", "CAD"))

    def test_the_quantity_is_rounded_to_six_places(self):
        self.assertEqual(
            svc._normalize_row({"quantity": "0.3333335"})["quantity"], Decimal("0.333334")
        )


if __name__ == "__main__":
    unittest.main()


class RealizedSaleRowTest(unittest.TestCase):
    """`_realized_sale_row` (plan step 4.3 review): the row mapping of
    `list_realized_sales`, taken out so that method stays a query and a list."""

    ROW = {
        "sale_id": 4, "lot_id": 9, "symbol": " intc ", "shares_sold": "2.5",
        "sale_price": "44.10", "sale_trade_date": date(2026, 6, 16),
        "allocation_method": "FIFO", "notes": "Sold by its stop at 45.00",
        "purchase_price": "50", "trade_date": date(2026, 6, 15), "currency": "usd",
        "account": "Sim", "fx_rate_at_purchase": None, "fx_rate_at_sale": None,
        "source": "stop", "source_ref": "stop:7", "order_id": 12,
    }

    def test_a_stop_sale_keeps_its_source_and_its_order(self):
        sale = repo._realized_sale_row(self.ROW)
        self.assertEqual(
            (sale["symbol"], sale["shares_sold"], sale["sale_trade_date"], sale["trade_date"],
             sale["currency"], sale["source"], sale["source_ref"], sale["order_id"]),
            ("INTC", Decimal("2.5"), "2026-06-16", "2026-06-15", "USD", "stop", "stop:7", 12),
        )

    def test_empty_columns_come_back_as_none_and_a_blank_currency_as_usd(self):
        sale = repo._realized_sale_row({
            **self.ROW, "allocation_method": "", "notes": None, "purchase_price": None,
            "trade_date": None, "currency": None, "account": "", "source": "manual",
            "source_ref": "", "order_id": None,
        })
        self.assertEqual(
            (sale["allocation_method"], sale["notes"], sale["purchase_price"],
             sale["trade_date"], sale["currency"], sale["account"], sale["source_ref"],
             sale["order_id"]),
            (None, None, None, None, "USD", None, None, None),
        )
