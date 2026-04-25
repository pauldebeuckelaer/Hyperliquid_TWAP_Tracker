#!/usr/bin/env python3
"""
migrate_cet_to_utc.py
=====================
One-time migration: convert ALL timestamps in twap.db from CET (UTC+1) to UTC.

All historical data was written with datetime.now() on a Europe/Brussels (CET) system.
This script subtracts 1 hour from every timestamp column in every table.

Usage:
    1. Stop the TWAP tracker first!
    2. python3 migrate_cet_to_utc.py
    3. Verify output
    4. Fix tracker code to use datetime.now(timezone.utc)
    5. Restart tracker

Author: Paul (generated with Claude)
Date: 2026-03-04
"""

import sqlite3
import shutil
import os
import sys
from datetime import datetime, timedelta

DB_PATH = "/home/pauldb46/Hyperliquid_TWAP_Tracker/data/twap.db"
BACKUP_PATH = "/home/pauldb46/Hyperliquid_TWAP_Tracker/data/twap_backup_pre_utc.db"

# Every table and its timestamp columns
# Format: (table_name, [list of timestamp columns])
TABLES_TO_MIGRATE = [
    ("snapshots",              ["timestamp"]),
    ("orders",                 ["first_seen_at", "last_seen_at", "completed_at", "canceled_at"]),
    ("events",                 ["timestamp"]),
    ("portfolio_snapshots",    ["snapshot_time"]),
    ("perp_snapshots",         ["snapshot_time"]),
    ("spot_snapshots",         ["snapshot_time"]),
    ("vault_snapshots",        ["snapshot_time"]),
    ("market_snapshots",       ["snapshot_time"]),
    ("liquidation_snapshots",  ["snapshot_time"]),
    ("whale_events",           ["timestamp"]),
    ("orderbook_snapshots",    ["snapshot_time"]),
    ("market_candles",         ["candle_time"]),
    ("addresses",              ["first_seen_at", "last_seen_at"]),
    ("whale_addresses",        ["first_seen", "last_updated", "last_tier_update"]),
    ("vip_addresses",          ["added_date"]),
]


def subtract_one_hour(ts_string):
    """Parse ISO timestamp string, subtract 1 hour, return as string in same format."""
    if ts_string is None or ts_string.strip() == "":
        return ts_string
    
    try:
        # Handle different formats we've seen in the DB
        # Full microseconds: 2026-02-01T00:00:13.666070
        # No seconds fraction: 2026-02-16T20:43:00
        # Minutes only: 2026-02-02T00:00
        
        formats = [
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ]
        
        dt = None
        used_format = None
        for fmt in formats:
            try:
                dt = datetime.strptime(ts_string.strip(), fmt)
                used_format = fmt
                break
            except ValueError:
                continue
        
        if dt is None:
            print(f"  WARNING: Could not parse timestamp: '{ts_string}'")
            return ts_string
        
        # Subtract 1 hour (CET -> UTC)
        dt_utc = dt - timedelta(hours=1)
        
        # Return in the same format it came in
        return dt_utc.strftime(used_format)
    
    except Exception as e:
        print(f"  ERROR processing '{ts_string}': {e}")
        return ts_string


def verify_sample(conn, table, column, label=""):
    """Print a few sample values for verification."""
    cursor = conn.execute(
        f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != '' LIMIT 3"
    )
    rows = cursor.fetchall()
    if rows:
        print(f"  {label} samples: {[r[0] for r in rows]}")


def migrate_table(conn, table, columns):
    """Migrate all timestamp columns in a single table."""
    
    # Get row count
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if count == 0:
        print(f"  {table}: empty, skipping")
        return 0
    
    print(f"\n  {table}: {count:,} rows, columns: {columns}")
    
    # Show before samples
    for col in columns:
        verify_sample(conn, table, col, label=f"BEFORE {col}")
    
    # Process each column
    total_updated = 0
    for col in columns:
        # Fetch all non-null values with their rowids
        cursor = conn.execute(
            f"SELECT rowid, {col} FROM {table} WHERE {col} IS NOT NULL AND {col} != ''"
        )
        
        batch = []
        batch_size = 10000
        updated = 0
        
        for row in cursor:
            rowid, old_val = row
            new_val = subtract_one_hour(old_val)
            if new_val != old_val:
                batch.append((new_val, rowid))
                
                if len(batch) >= batch_size:
                    conn.executemany(
                        f"UPDATE {table} SET {col} = ? WHERE rowid = ?",
                        batch
                    )
                    updated += len(batch)
                    batch = []
        
        # Flush remaining
        if batch:
            conn.executemany(
                f"UPDATE {table} SET {col} = ? WHERE rowid = ?",
                batch
            )
            updated += len(batch)
        
        total_updated += updated
        print(f"    {col}: {updated:,} rows updated")
    
    # Show after samples
    for col in columns:
        verify_sample(conn, table, col, label=f"AFTER  {col}")
    
    return total_updated


def main():
    print("=" * 70)
    print("  TWAP Database Migration: CET (UTC+1) → UTC")
    print("=" * 70)
    
    # Check DB exists
    if not os.path.exists(DB_PATH):
        print(f"\nERROR: Database not found at {DB_PATH}")
        sys.exit(1)
    
    db_size = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f"\nDatabase: {DB_PATH}")
    print(f"Size: {db_size:.1f} MB")
    
    # Step 1: Backup
    print(f"\n[1/4] Creating backup → {BACKUP_PATH}")
    if os.path.exists(BACKUP_PATH):
        print(f"  Backup already exists! Remove it first if you want to re-run.")
        response = input("  Continue anyway? (y/n): ").strip().lower()
        if response != 'y':
            print("  Aborted.")
            sys.exit(0)
    else:
        shutil.copy2(DB_PATH, BACKUP_PATH)
        backup_size = os.path.getsize(BACKUP_PATH) / (1024 * 1024)
        print(f"  Backup created: {backup_size:.1f} MB")
    
    # Step 2: WAL checkpoint (flush any pending writes)
    print(f"\n[2/4] Checkpointing WAL...")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print("  WAL checkpointed and truncated")
    
    # Step 3: Migrate
    print(f"\n[3/4] Migrating timestamps (subtracting 1 hour)...")
    
    grand_total = 0
    for table, columns in TABLES_TO_MIGRATE:
        try:
            updated = migrate_table(conn, table, columns)
            grand_total += updated
        except Exception as e:
            print(f"\n  ERROR on {table}: {e}")
            print("  Rolling back ALL changes...")
            conn.rollback()
            conn.close()
            print("  Rollback complete. Database unchanged.")
            sys.exit(1)
    
    # Commit all changes at once
    print(f"\n  Committing all changes...")
    conn.commit()
    print(f"  COMMITTED. Total rows updated: {grand_total:,}")
    
    # Step 4: Verify
    print(f"\n[4/4] Verification...")
    
    # Check snapshots range
    row = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM snapshots").fetchone()
    print(f"  snapshots range: {row[0]} → {row[1]}")
    
    # Check orders range  
    row = conn.execute("SELECT MIN(first_seen_at), MAX(first_seen_at) FROM orders").fetchone()
    print(f"  orders range:    {row[0]} → {row[1]}")
    
    # Check events range
    row = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM events").fetchone()
    print(f"  events range:    {row[0]} → {row[1]}")
    
    # Check market_snapshots range
    row = conn.execute("SELECT MIN(snapshot_time), MAX(snapshot_time) FROM market_snapshots").fetchone()
    print(f"  market range:    {row[0]} → {row[1]}")
    
    # Final checkpoint
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    
    print(f"\n{'=' * 70}")
    print(f"  MIGRATION COMPLETE")
    print(f"  All timestamps shifted from CET (UTC+1) → UTC")
    print(f"  Total rows updated: {grand_total:,}")
    print(f"  Backup at: {BACKUP_PATH}")
    print(f"{'=' * 70}")
    print(f"\n  NEXT STEPS:")
    print(f"  1. Fix tracker code: datetime.now() → datetime.now(timezone.utc)")
    print(f"  2. Restart the tracker")
    print(f"  3. Verify new data is written in UTC")


if __name__ == "__main__":
    main()
