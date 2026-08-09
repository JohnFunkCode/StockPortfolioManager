"""Unit tests for quantcore.schema_introspect's pure functions.

All cases here are hand-built dicts or plain strings -- no database
connection, no scratch database. describe_schema()/scratch_database()/
snapshot_from_dsn() need a live Postgres; those are covered by
tests/test_schema_introspect_live.py (runs against the CI Postgres service /
QUANTCORE_DB_DSN), per PR 1 Step 2 of docs/proposals/schema-ownership-plan.md.
"""
import unittest

from quantcore.schema_introspect import IGNORED_TABLES, _column_type, _with_database, diff_schemas


def _col(type_="integer", nullable=False, default=None):
    return {"type": type_, "nullable": nullable, "default": default}


def _table(columns=None, indexes=None, constraints=None):
    return {
        "columns": columns or {},
        "indexes": indexes or {},
        "constraints": constraints or {},
    }


class DiffSchemasTests(unittest.TestCase):
    def test_identical_schemas_produce_no_diff(self):
        schema = {
            "tables": {
                "watchlist": _table(
                    columns={"watchlist_id": _col(), "tags": _col("ARRAY", default="'{}'::text[]")},
                    indexes={"watchlist_pkey": "CREATE UNIQUE INDEX watchlist_pkey ..."},
                    constraints={"watchlist_symbol_id_fkey": "FOREIGN KEY (symbol_id) ..."},
                )
            }
        }
        # Compare equal-but-distinct dict objects, not the same object twice.
        import copy

        self.assertEqual(diff_schemas(schema, copy.deepcopy(schema)), [])

    def test_missing_table(self):
        expected = {"tables": {"watchlist": _table(), "positions": _table()}}
        actual = {"tables": {"positions": _table()}}
        self.assertEqual(diff_schemas(expected, actual), ["MISSING  table watchlist"])

    def test_extra_table(self):
        expected = {"tables": {"positions": _table()}}
        actual = {"tables": {"positions": _table(), "zzz_drift": _table()}}
        self.assertEqual(diff_schemas(expected, actual), ["EXTRA    table zzz_drift"])

    def test_missing_column(self):
        expected = {"tables": {"positions": _table(columns={"quantity": _col(), "trade_date": _col("date")})}}
        actual = {"tables": {"positions": _table(columns={"quantity": _col()})}}
        self.assertEqual(diff_schemas(expected, actual), ["MISSING  column positions.trade_date"])

    def test_extra_column(self):
        expected = {"tables": {"positions": _table(columns={"quantity": _col()})}}
        actual = {"tables": {"positions": _table(columns={"quantity": _col(), "legacy_note": _col("text")})}}
        self.assertEqual(diff_schemas(expected, actual), ["EXTRA    column positions.legacy_note"])

    def test_column_type_mismatch(self):
        expected = {"tables": {"positions": _table(columns={"quantity": _col("numeric(18,6)")})}}
        actual = {"tables": {"positions": _table(columns={"quantity": _col("integer")})}}
        self.assertEqual(
            diff_schemas(expected, actual),
            ["MISMATCH column positions.quantity  expected numeric(18,6)  actual integer"],
        )

    def test_column_nullability_mismatch(self):
        expected = {"tables": {"positions": _table(columns={"trade_date": _col("date", nullable=False)})}}
        actual = {"tables": {"positions": _table(columns={"trade_date": _col("date", nullable=True)})}}
        self.assertEqual(
            diff_schemas(expected, actual),
            [
                "MISMATCH column positions.trade_date  expected nullable=False"
                "  actual nullable=True"
            ],
        )

    def test_column_default_mismatch(self):
        expected = {"tables": {"watchlist": _table(columns={"currency": _col("text", default="'USD'::text")})}}
        actual = {"tables": {"watchlist": _table(columns={"currency": _col("text", default=None)})}}
        self.assertEqual(
            diff_schemas(expected, actual),
            [
                "MISMATCH column watchlist.currency  expected default=\"'USD'::text\""
                "  actual default=None"
            ],
        )

    def test_index_added_and_removed(self):
        expected = {
            "tables": {
                "positions": _table(indexes={"positions_pkey": "CREATE UNIQUE INDEX positions_pkey ..."})
            }
        }
        actual = {
            "tables": {
                "positions": _table(
                    indexes={"idx_positions_legacy": "CREATE INDEX idx_positions_legacy ..."}
                )
            }
        }
        self.assertEqual(
            diff_schemas(expected, actual),
            [
                "EXTRA    index positions.idx_positions_legacy",
                "MISSING  index positions.positions_pkey",
            ],
        )

    def test_constraint_added_and_removed(self):
        expected = {
            "tables": {
                "watchlist": _table(
                    constraints={"watchlist_symbol_id_fkey": "FOREIGN KEY (symbol_id) REFERENCES symbols(symbol_id)"}
                )
            }
        }
        actual = {
            "tables": {
                "watchlist": _table(constraints={"watchlist_check_extra": "CHECK (currency IS NOT NULL)"})
            }
        }
        self.assertEqual(
            diff_schemas(expected, actual),
            [
                "EXTRA    constraint watchlist.watchlist_check_extra",
                "MISSING  constraint watchlist.watchlist_symbol_id_fkey",
            ],
        )

    def test_flyway_schema_history_is_a_known_ignored_table(self):
        # describe_schema() is what actually filters it out (needs a live DB
        # connection to exercise); this test just pins the constant so a
        # future edit that drops flyway_schema_history from the ignore set
        # doesn't slip through silently.
        self.assertEqual(IGNORED_TABLES, frozenset({"flyway_schema_history"}))

    def test_flyway_schema_history_present_on_both_sides_is_not_a_diff(self):
        # If describe_schema() ever were called without filtering, a table
        # present identically on both sides should still produce no diff --
        # diff_schemas itself is table-name-agnostic; the ignoring is
        # describe_schema()'s job, not diff_schemas()'s.
        schema = {"tables": {"flyway_schema_history": _table(columns={"version": _col("text")})}}
        import copy

        self.assertEqual(diff_schemas(schema, copy.deepcopy(schema)), [])

    def test_multiple_diffs_are_sorted(self):
        expected = {"tables": {"a_table": _table(), "z_table": _table()}}
        actual = {"tables": {}}
        self.assertEqual(
            diff_schemas(expected, actual),
            ["MISSING  table a_table", "MISSING  table z_table"],
        )

    def test_index_definition_mismatch(self):
        expected = {
            "tables": {
                "positions": _table(
                    indexes={"positions_pkey": "CREATE UNIQUE INDEX positions_pkey ON positions (position_id)"}
                )
            }
        }
        actual = {
            "tables": {
                "positions": _table(
                    indexes={"positions_pkey": "CREATE UNIQUE INDEX positions_pkey ON positions (owner, position_id)"}
                )
            }
        }
        self.assertEqual(
            diff_schemas(expected, actual),
            [
                "MISMATCH index positions.positions_pkey"
                "  expected 'CREATE UNIQUE INDEX positions_pkey ON positions (position_id)'"
                "  actual 'CREATE UNIQUE INDEX positions_pkey ON positions (owner, position_id)'"
            ],
        )

    def test_constraint_definition_mismatch(self):
        expected = {
            "tables": {
                "watchlist": _table(
                    constraints={"watchlist_symbol_id_fkey": "FOREIGN KEY (symbol_id) REFERENCES symbols(symbol_id)"}
                )
            }
        }
        actual = {
            "tables": {
                "watchlist": _table(
                    constraints={
                        "watchlist_symbol_id_fkey": "FOREIGN KEY (symbol_id) REFERENCES symbols(id)"
                    }
                )
            }
        }
        self.assertEqual(
            diff_schemas(expected, actual),
            [
                "MISMATCH constraint watchlist.watchlist_symbol_id_fkey"
                "  expected 'FOREIGN KEY (symbol_id) REFERENCES symbols(symbol_id)'"
                "  actual 'FOREIGN KEY (symbol_id) REFERENCES symbols(id)'"
            ],
        )


class ColumnTypeTests(unittest.TestCase):
    """_column_type() is a pure formatter; describe_schema() (the only other
    caller) needs a live database, so it's exercised separately in
    tests/test_schema_introspect_live.py."""

    def _col(self, **overrides):
        base = {
            "data_type": "integer",
            "character_maximum_length": None,
            "numeric_precision": None,
            "numeric_scale": None,
        }
        base.update(overrides)
        return base

    def test_plain_type_has_no_suffix(self):
        self.assertEqual(_column_type(self._col(data_type="integer")), "integer")

    def test_character_type_includes_max_length(self):
        self.assertEqual(
            _column_type(self._col(data_type="character varying", character_maximum_length=8)),
            "character varying(8)",
        )

    def test_numeric_type_includes_precision_and_scale(self):
        self.assertEqual(
            _column_type(
                self._col(data_type="numeric", numeric_precision=18, numeric_scale=6)
            ),
            "numeric(18,6)",
        )

    def test_numeric_type_defaults_missing_scale_to_zero(self):
        self.assertEqual(
            _column_type(self._col(data_type="numeric", numeric_precision=10, numeric_scale=None)),
            "numeric(10,0)",
        )

    def test_integer_type_ignores_numeric_precision(self):
        # numeric_precision is populated for integer types too (e.g. 32), but
        # only numeric/decimal render it -- an int column shouldn't grow a
        # spurious "(32,0)" suffix.
        self.assertEqual(
            _column_type(self._col(data_type="integer", numeric_precision=32, numeric_scale=0)),
            "integer",
        )


class WithDatabaseTests(unittest.TestCase):
    def test_swaps_path_keeps_everything_else(self):
        dsn = "postgresql://quantcore:secret@localhost:5432/quantcore?sslmode=disable"
        self.assertEqual(
            _with_database(dsn, "scratch_db"),
            "postgresql://quantcore:secret@localhost:5432/scratch_db?sslmode=disable",
        )

    def test_original_dsn_is_unchanged(self):
        dsn = "postgresql://quantcore:secret@localhost:5432/quantcore"
        _with_database(dsn, "postgres")
        self.assertEqual(dsn, "postgresql://quantcore:secret@localhost:5432/quantcore")


if __name__ == "__main__":
    unittest.main()
