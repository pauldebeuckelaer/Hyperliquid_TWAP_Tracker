#!/usr/bin/env python3
"""
SQLite Database Validator
=========================
Check if your TWAP data is being captured correctly.

Usage:
    python validate_db.py
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path('data/twap.db')


def validate():
    if not DB_PATH.exists():
        print(f"❌ Database not found: {DB_PATH}")
        return False

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("=" * 60)
    print("SQLite Database Validation")
    print("=" * 60)

    # 1. Basic stats
    print("\n📊 TABLE COUNTS:")
    tables = ['orders', 'snapshots', 'events', 'addresses']
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"   {table}: {count:,} rows")

    # 2. Database size
    size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    print(f"\n💾 Database size: {size_mb:.2f} MB")

    # 3. Time range
    print("\n⏰ TIME RANGE:")
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM snapshots")
    row = cursor.fetchone()
    if row[0]:
        print(f"   First snapshot: {row[0]}")
        print(f"   Last snapshot:  {row[1]}")
    else:
        print("   No snapshots yet!")

    # 4. Unique symbols
    cursor.execute("SELECT COUNT(DISTINCT symbol) FROM snapshots")
    symbol_count = cursor.fetchone()[0]
    print(f"\n🪙 Unique symbols tracked: {symbol_count}")

    # 5. Top 10 symbols by snapshot count
    print("\n📈 TOP 10 SYMBOLS (by snapshots):")
    cursor.execute("""
        SELECT symbol, COUNT(*) as cnt 
        FROM snapshots 
        GROUP BY symbol 
        ORDER BY cnt DESC 
        LIMIT 10
    """)
    for row in cursor.fetchall():
        print(f"   {row['symbol']}: {row['cnt']:,} snapshots")

    # 6. Order status breakdown
    print("\n📦 ORDER STATUS:")
    cursor.execute("""
        SELECT status, COUNT(*) as cnt 
        FROM orders 
        GROUP BY status
    """)
    for row in cursor.fetchall():
        print(f"   {row['status']}: {row['cnt']:,}")

    # 7. Events breakdown
    print("\n🎯 EVENTS:")
    cursor.execute("""
        SELECT event_type, COUNT(*) as cnt 
        FROM events 
        GROUP BY event_type
    """)
    for row in cursor.fetchall():
        print(f"   {row['event_type']}: {row['cnt']:,}")

    # 8. Sample recent orders
    print("\n🔍 RECENT ORDERS (last 5):")
    cursor.execute("""
        SELECT symbol, side, size, status, address, last_seen_at
        FROM orders
        ORDER BY last_seen_at DESC
        LIMIT 5
    """)
    for row in cursor.fetchall():
        addr_short = row['address'][:10] + "..." if row['address'] else "?"
        print(f"   {row['symbol']:8} {row['side']:4} {row['size']:>12,.2f} {row['status']:10} {addr_short}")

    # 9. Sample recent events
    print("\n🎬 RECENT EVENTS (last 5):")
    cursor.execute("""
        SELECT timestamp, event_type, symbol, side, size, address
        FROM events
        ORDER BY timestamp DESC
        LIMIT 5
    """)
    for row in cursor.fetchall():
        addr_short = row['address'][:10] + "..." if row['address'] else "?"
        time_short = row['timestamp'][11:19] if row['timestamp'] else "?"
        print(
            f"   {time_short} {row['event_type']:10} {row['symbol']:8} {row['side']:4} {row['size']:>10,.2f} {addr_short}")

    # 10. Check for recent activity (last hour)
    one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
    cursor.execute("SELECT COUNT(*) FROM snapshots WHERE timestamp > ?", (one_hour_ago,))
    recent_snapshots = cursor.fetchone()[0]
    print(f"\n⏱️ Snapshots in last hour: {recent_snapshots}")

    if recent_snapshots > 0:
        expected = 60  # ~1 per minute
        health = "✅ HEALTHY" if recent_snapshots > 30 else "⚠️ LOW"
        print(f"   Status: {health} (expected ~{expected})")

    conn.close()

    print("\n" + "=" * 60)
    print("Validation complete!")
    print("=" * 60)

    return True


if __name__ == '__main__':
    validate()