"""
migrate_sqlite_to_postgres.py — One-shot migration from quantcore.sqlite to PostgreSQL.

Usage:
    # Migrate to dev database (reads QUANTCORE_DB_DSN from .env)
    python scripts/migrate_sqlite_to_postgres.py

    # Migrate to test database
    python scripts/migrate_sqlite_to_postgres.py --dsn "$QUANTCORE_TEST_DB_DSN"

    # Explicit paths
    python scripts/migrate_sqlite_to_postgres.py \
        --sqlite data/quantcore.sqlite \
        --dsn "postgresql://quantcore:changeme@localhost:5432/quantcore"
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Ensure project root is on sys.path so quantcore package is importable
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Load .env so QUANTCORE_DB_PATH / QUANTCORE_DB_DSN are available
load_dotenv()

from quantcore.db import init_schema

# ---------------------------------------------------------------------------
# Tables in insertion order (parents before children)
# ---------------------------------------------------------------------------
TIER1 = [
    "symbols",
    "plan_templates",
    "ohlcv",
    "fetch_log",
    "fundamentals_history",
    "gamma_wall_history",
    "options_snapshots",
    "options_positions",
    "news_articles",
    "sentiment_snapshots",
]
TIER2 = ["positions"]           # → symbols
TIER3 = ["plan_instances"]      # → plan_templates, symbols, positions
TIER4 = ["plan_rungs", "options_expirations"]   # → plan_instances, options_snapshots
TIER5 = ["alerts", "options_contracts"]         # → plan_rungs+, options_expirations

ALL_TABLES = TIER1 + TIER2 + TIER3 + TIER4 + TIER5

# Tables that have a single-column SERIAL primary key in PostgreSQL
# (we must reset the sequence after bulk-insert)
SERIAL_PK = {
    "symbols":           "symbol_id",
    "plan_templates":    "template_id",
    "positions":         "position_id",
    "plan_instances":    "instance_id",
    "plan_rungs":        "rung_id",
    "alerts":            "alert_id",
    "options_snapshots": "snapshot_id",
    "options_expirations": "expiration_id",
    "options_contracts": "contract_id",
    "gamma_wall_history": "id",
    "options_positions": "position_id",
    "news_articles":     "article_id",
    "sentiment_snapshots": "id",
}


def _migrate_table(src: sqlite3.Connection, dst: psycopg2.extensions.connection, table: str) -> int:
    src.row_factory = sqlite3.Row
    cur_src = src.execute(f"SELECT * FROM {table}")
    rows = cur_src.fetchall()
    if not rows:
        return 0

    cols = [description[0] for description in cur_src.description]
    col_names = ", ".join(cols)
    sql = f"INSERT INTO {table} ({col_names}) VALUES %s ON CONFLICT DO NOTHING"

    values = [tuple(row) for row in rows]
    with dst.cursor() as cur_dst:
        psycopg2.extras.execute_values(cur_dst, sql, values, page_size=1000)

    dst.commit()
    return len(rows)


def _reset_sequence(dst: psycopg2.extensions.connection, table: str, pk_col: str) -> None:
    with dst.cursor() as cur:
        cur.execute(f"SELECT MAX({pk_col}) FROM {table}")
        max_val = cur.fetchone()[0]
        if max_val is not None:
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{pk_col}'), %s)",
                (max_val,),
            )
    dst.commit()


def migrate(sqlite_path: str, pg_dsn: str) -> None:
    print(f"\nSource:      {sqlite_path}")
    print(f"Destination: {pg_dsn}\n")

    if not Path(sqlite_path).exists():
        print(f"ERROR: SQLite file not found: {sqlite_path}")
        sys.exit(1)

    # Initialize PostgreSQL schema
    print("Initializing PostgreSQL schema...")
    init_schema(pg_dsn)

    src = sqlite3.connect(sqlite_path)
    dst = psycopg2.connect(pg_dsn)

    total_rows = 0
    print(f"{'Table':<30} {'SQLite rows':>12} {'Migrated':>10}")
    print("-" * 55)

    for table in ALL_TABLES:
        # Check if table exists in SQLite
        check = src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not check:
            print(f"  {table:<28} {'(not in SQLite)':>12}")
            continue

        src_count = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        migrated = _migrate_table(src, dst, table)
        total_rows += migrated
        print(f"  {table:<28} {src_count:>12,} {migrated:>10,}")

    # Reset SERIAL sequences
    print("\nResetting sequences...")
    for table, pk_col in SERIAL_PK.items():
        check = src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if check:
            _reset_sequence(dst, table, pk_col)
            print(f"  Reset sequence for {table}.{pk_col}")

    src.close()
    dst.close()

    print(f"\nDone. {total_rows:,} total rows migrated.")

    # Verify row counts
    print("\nVerification (PostgreSQL row counts):")
    dst2 = psycopg2.connect(pg_dsn)
    with dst2.cursor() as cur:
        for table in ALL_TABLES:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            pg_count = cur.fetchone()[0]
            if pg_count > 0:
                print(f"  {table:<30} {pg_count:>10,}")
    dst2.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate quantcore.sqlite to PostgreSQL")
    parser.add_argument(
        "--sqlite",
        default=os.getenv("QUANTCORE_DB_PATH", "data/quantcore.sqlite"),
        help="Path to SQLite database file",
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("QUANTCORE_DB_DSN", "postgresql://quantcore:changeme@localhost:5432/quantcore"),
        help="PostgreSQL DSN URI",
    )
    args = parser.parse_args()
    migrate(args.sqlite, args.dsn)


if __name__ == "__main__":
    main()
