import csv
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

# DB-backed tests run against the test database only. Swap the test DSN in
# BEFORE quantcore.db is imported (it freezes DB_DSN at import time), then let
# the guard abort if this process would still reach production. When .env is
# absent (e.g. CI), keep whatever QUANTCORE_DB_DSN the environment set.
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
            os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
            break

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.services.portfolio import (  # noqa: E402
    DuplicateSymbolError,
    PortfolioService,
)

# Synthetic owners/symbols that won't collide with real positions in the
# configured QuantCore database.
OWNER_A = "zz_owner_a"
OWNER_B = "zz_owner_b"
TEST_SYMBOLS = ["ZZTEST", "ZZTST2", "ZZTST3"]


class PortfolioServiceTest(unittest.TestCase):
    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.service = PortfolioService(PortfolioRepository())

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM positions WHERE owner IN (%s, %s)", (OWNER_A, OWNER_B)
            )
            conn.commit()

    def _write_csv(self, rows):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        fields = [
            "name", "symbol", "purchase_price", "quantity",
            "purchase_date", "currency", "sale_price", "sale_date", "current_price",
        ]
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})
        return path

    def _sample_rows(self):
        return [
            {
                "name": "Test One", "symbol": "ZZTEST", "purchase_price": "10.00",
                "quantity": "5", "purchase_date": "2026-01-02", "currency": "USD",
            },
            {
                "name": "Test Two", "symbol": "ZZTST2", "purchase_price": "20.50",
                "quantity": "3", "purchase_date": "2026-02-03", "currency": "USD",
            },
        ]

    # ------------------------------------------------------------------
    def test_import_csv_returns_count_and_lists_positions(self):
        path = self._write_csv(self._sample_rows())
        count = self.service.import_csv(path, OWNER_A)
        self.assertEqual(count, 2)

        positions = self.service.list_positions(OWNER_A)
        self.assertEqual(len(positions), 2)
        symbols = sorted(p["symbol"] for p in positions)
        self.assertEqual(symbols, ["ZZTEST", "ZZTST2"])
        first = next(p for p in positions if p["symbol"] == "ZZTEST")
        self.assertEqual(first["purchase_price"], 10.0)
        self.assertEqual(first["quantity"], 5)
        self.assertEqual(first["source"], "portfolio")
        self.assertEqual(first["tags"], [])

    def test_import_csv_skips_empty_symbol_rows(self):
        rows = self._sample_rows() + [{"name": "Blank", "symbol": ""}]
        path = self._write_csv(rows)
        count = self.service.import_csv(path, OWNER_A)
        self.assertEqual(count, 2)

    def test_import_csv_is_full_sync_replace(self):
        first_path = self._write_csv(self._sample_rows())
        self.service.import_csv(first_path, OWNER_A)

        # Re-import with a single, different row — the prior rows must be gone.
        replacement = [{
            "name": "Replacement", "symbol": "ZZTST3", "purchase_price": "99.00",
            "quantity": "1", "purchase_date": "2026-03-04", "currency": "USD",
        }]
        second_path = self._write_csv(replacement)
        count = self.service.import_csv(second_path, OWNER_A)
        self.assertEqual(count, 1)

        positions = self.service.list_positions(OWNER_A)
        self.assertEqual([p["symbol"] for p in positions], ["ZZTST3"])

    def test_reimport_is_idempotent(self):
        path = self._write_csv(self._sample_rows())
        self.assertEqual(self.service.import_csv(path, OWNER_A), 2)
        self.assertEqual(self.service.import_csv(path, OWNER_A), 2)
        self.assertEqual(len(self.service.list_positions(OWNER_A)), 2)

    def test_owner_isolation(self):
        path = self._write_csv(self._sample_rows())
        self.service.import_csv(path, OWNER_A)
        self.service.import_csv(path, OWNER_B)

        # Replacing owner A leaves owner B untouched.
        replacement = self._write_csv([{
            "name": "Only", "symbol": "ZZTST3", "purchase_price": "1.00",
            "quantity": "1", "purchase_date": "2026-04-05", "currency": "USD",
        }])
        self.service.import_csv(replacement, OWNER_A)

        self.assertEqual([p["symbol"] for p in self.service.list_positions(OWNER_A)], ["ZZTST3"])
        self.assertEqual(
            sorted(p["symbol"] for p in self.service.list_positions(OWNER_B)),
            ["ZZTEST", "ZZTST2"],
        )

        owners = self.service.list_owners()
        self.assertIn(OWNER_A, owners)
        self.assertIn(OWNER_B, owners)

    def test_add_position_then_duplicate_raises(self):
        result = self.service.add_position(
            OWNER_A, name="Added", symbol="zztest", purchase_price="12.5",
            quantity="4", purchase_date="2026-01-10", currency="usd",
        )
        self.assertEqual(result, {"symbol": "ZZTEST"})

        positions = self.service.list_positions(OWNER_A)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["symbol"], "ZZTEST")
        self.assertEqual(positions[0]["currency"], "USD")

        with self.assertRaises(DuplicateSymbolError):
            self.service.add_position(
                OWNER_A, name="Dup", symbol="ZZTEST", purchase_price="13",
                quantity="1", purchase_date="2026-05-11",
            )

    def test_add_position_without_symbol_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.add_position(OWNER_A, name="No Symbol", purchase_price="1")

    def test_remove_position_returns_rows_removed(self):
        path = self._write_csv(self._sample_rows())
        self.service.import_csv(path, OWNER_A)

        removed = self.service.remove_position(OWNER_A, "zztest")
        self.assertEqual(removed, 1)
        self.assertEqual(
            [p["symbol"] for p in self.service.list_positions(OWNER_A)], ["ZZTST2"]
        )

    def test_remove_missing_position_returns_zero(self):
        self.assertEqual(self.service.remove_position(OWNER_A, "ZZTEST"), 0)


if __name__ == "__main__":
    unittest.main()
