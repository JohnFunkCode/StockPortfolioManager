w#!/usr/bin/env python3
"""
Migrate all 6 separate SQLite databases into a single unified QuantCore database.

This script:
1. Initializes the new QuantCore schema (data/quantcore.sqlite)
2. Attaches and migrates data from all 6 old databases
3. Handles the special OHLCV merge (price_bars_daily + ohlcv -> ohlcv)
4. Reports before/after row counts for verification
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path
import time

# Import the new shared DB factory
from quantcore.db import get_connection, init_schema, DB_PATH

# Paths to old databases
OLD_DB_PATHS = {
    "harvester": Path(__file__).parent.parent / "harvester.sqlite",
    "options_chain": Path(__file__).parent.parent / "fastMCPTest" / "options_chain.db",
    "ohlcv_cache": Path(__file__).parent.parent / "fastMCPTest" / "ohlcv_cache.db",
    "fundamentals": Path(__file__).parent.parent / "fastMCPTest" / "fundamentals_history.db",
    "sentiment": Path(__file__).parent.parent / "fastMCPTest" / "sentiment.sqlite",
    "news": Path(__file__).parent.parent / "fastMCPTest" / "news_sentiment.db",
}


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    """Count rows in a table."""
    try:
        cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
        return cursor.fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def migrate_table(new_conn: sqlite3.Connection, old_conn: sqlite3.Connection,
                  table_name: str, alias: str = None, use_direct_insert: bool = False) -> tuple[int, int]:
    """
    Migrate a table from old DB to new DB.
    If use_direct_insert=True, uses old_conn directly (not ATTACH alias).
    Returns (old_count, new_count).
    """
    old_count = count_rows(old_conn, table_name)

    if old_count == 0:
        return 0, 0

    # Get column info from old table
    old_cursor = old_conn.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in old_cursor.fetchall()]
    col_list = ", ".join(columns)

    # Copy rows (INSERT OR IGNORE to handle duplicates gracefully)
    if use_direct_insert:
        # Fetch from old_conn and insert into new_conn
        select_sql = f"SELECT {col_list} FROM {table_name}"
        rows = old_conn.execute(select_sql).fetchall()
        placeholders = ", ".join(["?"] * len(columns))
        insert_sql = f"INSERT OR IGNORE INTO {table_name} ({col_list}) VALUES ({placeholders})"
        for row in rows:
            new_conn.execute(insert_sql, row)
        new_conn.commit()
    else:
        # Use ATTACH alias (for harvester/ohlcv_cache)
        insert_sql = f"INSERT OR IGNORE INTO {table_name} ({col_list}) SELECT {col_list} FROM {alias}.{table_name}"
        new_conn.execute(insert_sql)
        new_conn.commit()

    new_count = count_rows(new_conn, table_name)
    return old_count, new_count


def migrate_ohlcv(new_conn: sqlite3.Connection) -> tuple[int, int]:
    """
    Special handling for OHLCV merge:
    - Migrate price_bars_daily from harvester → ohlcv (interval='1d')
    - Migrate ohlcv from ohlcv_cache → ohlcv (as-is, already in correct format)

    Returns (total_old_count, new_count).
    """
    harvester_conn = sqlite3.connect(str(OLD_DB_PATHS["harvester"]))
    ohlcv_cache_conn = sqlite3.connect(str(OLD_DB_PATHS["ohlcv_cache"]))

    old_total = 0

    try:
        # Count old rows
        price_bars_count = count_rows(harvester_conn, "price_bars_daily")
        ohlcv_count = count_rows(ohlcv_cache_conn, "ohlcv")
        old_total = price_bars_count + ohlcv_count

        print(f"  Migrating price_bars_daily ({price_bars_count} rows)...")
        if price_bars_count > 0:
            # price_bars_daily has: symbol_id, bar_date, open, high, low, close, adj_close, volume, data_vendor, ingested_at
            # Need to convert bar_date (TEXT YYYY-MM-DD) → ts (INTEGER unix timestamp at midnight UTC)
            # and add interval='1d', status='CLOSED'

            # First, build a temp table with the converted data
            new_conn.execute("""
                CREATE TEMPORARY TABLE price_bars_temp AS
                SELECT
                    (SELECT ticker FROM harvester.symbols WHERE symbol_id = p.symbol_id) as symbol,
                    '1d' as interval,
                    cast(strftime('%s', p.bar_date) as integer) as ts,
                    p.open,
                    p.high,
                    p.low,
                    p.close,
                    p.adj_close,
                    p.volume,
                    'CLOSED' as status,
                    p.data_vendor,
                    p.ingested_at
                FROM harvester.price_bars_daily p
            """)

            new_conn.execute("""
                INSERT OR IGNORE INTO ohlcv
                (symbol, interval, ts, open, high, low, close, adj_close, volume, status, data_vendor, ingested_at)
                SELECT symbol, interval, ts, open, high, low, close, adj_close, volume, status, data_vendor, ingested_at
                FROM price_bars_temp
            """)
            new_conn.execute("DROP TABLE price_bars_temp")
            new_conn.commit()

        print(f"  Migrating ohlcv_cache.ohlcv ({ohlcv_count} rows)...")
        if ohlcv_count > 0:
            new_conn.execute("""
                INSERT OR IGNORE INTO ohlcv
                (symbol, interval, ts, open, high, low, close, adj_close, volume, status, data_vendor, ingested_at)
                SELECT symbol, interval, ts, open, high, low, close, NULL, volume, status, 'yfinance',
                       CAST(datetime('now', 'utc') AS INTEGER)
                FROM ohlcv_cache.ohlcv
            """)
            new_conn.commit()

        new_count = count_rows(new_conn, "ohlcv")
        return old_total, new_count

    finally:
        harvester_conn.close()
        ohlcv_cache_conn.close()


def main():
    # Ensure data directory exists
    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    print(f"Data directory: {data_dir}")
    print(f"Target database: {DB_PATH}")

    if DB_PATH.exists():
        print(f"\n⚠️  Database already exists at {DB_PATH}")
        response = input("Overwrite? (yes/no): ").strip().lower()
        if response != "yes":
            print("Migration cancelled.")
            return
        DB_PATH.unlink()

    print("\nInitializing QuantCore schema...")
    init_schema()
    print("✓ Schema initialized")

    new_conn = get_connection()

    # Track migration results
    results = {}

    # Attach old databases for migration
    print("\nAttaching legacy databases...")
    if OLD_DB_PATHS["harvester"].exists():
        new_conn.execute("ATTACH DATABASE ? AS harvester", (str(OLD_DB_PATHS["harvester"]),))
        print("  ✓ harvester.sqlite attached")
    if OLD_DB_PATHS["ohlcv_cache"].exists():
        new_conn.execute("ATTACH DATABASE ? AS ohlcv_cache", (str(OLD_DB_PATHS["ohlcv_cache"]),))
        print("  ✓ ohlcv_cache.db attached")

    # Migrate from harvester.sqlite
    print("\nMigrating from harvester.sqlite...")
    if OLD_DB_PATHS["harvester"].exists():
        harvester_conn = sqlite3.connect(str(OLD_DB_PATHS["harvester"]))
        harvester_conn.row_factory = sqlite3.Row

        for table in ["symbols", "plan_templates", "positions", "plan_instances", "plan_rungs", "alerts"]:
            print(f"  {table}...", end=" ")
            old_count, new_count = migrate_table(new_conn, harvester_conn, table, "harvester")
            results[table] = (old_count, new_count)
            print(f"✓ ({old_count} → {new_count})")

        harvester_conn.close()
    else:
        print("  ⚠️  harvester.sqlite not found, skipping")

    # Migrate OHLCV (special merged table)
    print("\nMigrating OHLCV (merged from price_bars_daily + ohlcv)...")
    old_ohlcv_count, new_ohlcv_count = migrate_ohlcv(new_conn)
    results["ohlcv"] = (old_ohlcv_count, new_ohlcv_count)
    print(f"  ohlcv ✓ ({old_ohlcv_count} → {new_ohlcv_count})")

    # Migrate fetch_log from ohlcv_cache
    print("\nMigrating from ohlcv_cache.db...")
    print(f"  fetch_log...", end=" ")
    old_count, new_count = migrate_table(new_conn, sqlite3.connect(str(OLD_DB_PATHS["ohlcv_cache"])), "fetch_log", "ohlcv_cache")
    results["fetch_log"] = (old_count, new_count)
    print(f"✓ ({old_count} → {new_count})")

    # Migrate from options_chain.db
    print("\nMigrating from options_chain.db...")
    if OLD_DB_PATHS["options_chain"].exists():
        options_conn = sqlite3.connect(str(OLD_DB_PATHS["options_chain"]))
        options_conn.row_factory = sqlite3.Row

        for table in ["options_snapshots", "options_expirations", "options_contracts", "gamma_wall_history", "options_positions"]:
            print(f"  {table}...", end=" ")
            old_count, new_count = migrate_table(new_conn, options_conn, table, use_direct_insert=True)
            results[table] = (old_count, new_count)
            print(f"✓ ({old_count} → {new_count})")

        options_conn.close()
    else:
        print("  ⚠️  options_chain.db not found, skipping")

    # Migrate from news_sentiment.db
    print("\nMigrating from news_sentiment.db...")
    if OLD_DB_PATHS["news"].exists():
        news_conn = sqlite3.connect(str(OLD_DB_PATHS["news"]))
        news_conn.row_factory = sqlite3.Row

        print(f"  news_articles...", end=" ")
        old_count, new_count = migrate_table(new_conn, news_conn, "news_articles", use_direct_insert=True)
        results["news_articles"] = (old_count, new_count)
        print(f"✓ ({old_count} → {new_count})")

        news_conn.close()
    else:
        print("  ⚠️  news_sentiment.db not found, skipping")

    # Migrate from sentiment.sqlite
    print("\nMigrating from sentiment.sqlite...")
    if OLD_DB_PATHS["sentiment"].exists():
        sentiment_conn = sqlite3.connect(str(OLD_DB_PATHS["sentiment"]))
        sentiment_conn.row_factory = sqlite3.Row

        print(f"  sentiment_snapshots...", end=" ")
        old_count, new_count = migrate_table(new_conn, sentiment_conn, "sentiment_snapshots", use_direct_insert=True)
        results["sentiment_snapshots"] = (old_count, new_count)
        print(f"✓ ({old_count} → {new_count})")

        sentiment_conn.close()
    else:
        print("  ⚠️  sentiment.sqlite not found, skipping")

    # Migrate from fundamentals_history.db
    print("\nMigrating from fundamentals_history.db...")
    if OLD_DB_PATHS["fundamentals"].exists():
        fundamentals_conn = sqlite3.connect(str(OLD_DB_PATHS["fundamentals"]))
        fundamentals_conn.row_factory = sqlite3.Row

        print(f"  fundamentals_history...", end=" ")
        old_count, new_count = migrate_table(new_conn, fundamentals_conn, "fundamentals_history", use_direct_insert=True)
        results["fundamentals_history"] = (old_count, new_count)
        print(f"✓ ({old_count} → {new_count})")

        fundamentals_conn.close()
    else:
        print("  ⚠️  fundamentals_history.db not found, skipping")

    new_conn.close()

    # Summary report
    print("\n" + "="*70)
    print("MIGRATION SUMMARY")
    print("="*70)

    total_old = sum(old for old, _ in results.values())
    total_new = sum(new for _, new in results.values())

    for table, (old_count, new_count) in sorted(results.items()):
        status = "✓" if old_count == new_count else "⚠️"
        print(f"{status} {table:30} {old_count:6d} → {new_count:6d}")

    print("-"*70)
    print(f"  {'TOTAL':30} {total_old:6d} → {total_new:6d}")
    print("="*70)

    if total_old == total_new:
        print("\n✓ Migration successful! All rows migrated.")
    else:
        print(f"\n⚠️  Migration complete, but some rows may have been deduplicated.")
        print(f"   Check the results above for any mismatches.")

    print(f"\nNew database: {DB_PATH}")
    print(f"File size: {DB_PATH.stat().st_size / (1024*1024):.1f} MB")


if __name__ == "__main__":
    main()
