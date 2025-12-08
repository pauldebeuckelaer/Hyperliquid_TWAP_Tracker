#!/usr/bin/env python3
"""
TWAP SQLite Loader - Basic summary stats
Usage: python sqlite_loader.py [database_path] [coin]
"""

import sqlite3
import sys
from pathlib import Path

DEFAULT_DB_PATH = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\data\twap.db")


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def list_available_coins(conn: sqlite3.Connection) -> list[dict]:
    query = """
        SELECT 
            symbol,
            COUNT(*) as snapshot_count,
            MIN(timestamp) as first_snapshot,
            MAX(timestamp) as last_snapshot,
            MIN(price) as price_low,
            MAX(price) as price_high
        FROM snapshots
        GROUP BY symbol
        ORDER BY symbol
    """
    cursor = conn.execute(query)
    return [dict(row) for row in cursor.fetchall()]


def get_table_info(conn: sqlite3.Connection) -> dict:
    tables = {}
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    for row in cursor:
        table_name = row[0]
        count_cursor = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
        tables[table_name] = count_cursor.fetchone()[0]
    return tables


def analyze_coin(conn: sqlite3.Connection, symbol: str) -> None:
    # Basic snapshot stats
    snapshot_query = """
        SELECT 
            COUNT(*) as total,
            MIN(timestamp) as first_ts,
            MAX(timestamp) as last_ts,
            MIN(price) as price_min,
            MAX(price) as price_max,
            AVG(active_orders) as avg_active_orders,
            MAX(active_orders) as max_active_orders,
            SUM(buy_volume) as total_buy_volume,
            SUM(sell_volume) as total_sell_volume,
            AVG(net_pressure) as avg_net_pressure
        FROM snapshots
        WHERE symbol = ?
    """
    stats = dict(conn.execute(snapshot_query, (symbol,)).fetchone())

    if stats['total'] == 0:
        print(f"No data found for {symbol}")
        return

    # First and last prices
    first_price = conn.execute(
        "SELECT price FROM snapshots WHERE symbol = ? ORDER BY timestamp ASC LIMIT 1",
        (symbol,)
    ).fetchone()[0]

    last_price = conn.execute(
        "SELECT price FROM snapshots WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
        (symbol,)
    ).fetchone()[0]

    # Order events from events table
    events_query = """
        SELECT 
            event_type,
            COUNT(*) as count
        FROM events
        WHERE symbol = ?
        GROUP BY event_type
    """
    events = {row['event_type']: row['count'] for row in conn.execute(events_query, (symbol,))}

    # Unique addresses from orders table
    addr_query = "SELECT COUNT(DISTINCT address) FROM orders WHERE symbol = ?"
    unique_addresses = conn.execute(addr_query, (symbol,)).fetchone()[0]

    # Print report
    print(f"\n{'=' * 60}")
    print(f" TWAP SUMMARY: {symbol}")
    print(f"{'=' * 60}")

    print(f"\nDATASET")
    print(f"   Snapshots: {stats['total']:,}")
    print(f"   Time range: {stats['first_ts']} -> {stats['last_ts']}")

    print(f"\nPRICE")
    print(f"   Start:  ${first_price:,.4f}")
    print(f"   End:    ${last_price:,.4f}")
    print(f"   Low:    ${stats['price_min']:,.4f}")
    print(f"   High:   ${stats['price_max']:,.4f}")
    if first_price:
        change = (last_price - first_price) / first_price * 100
        print(f"   Change: {change:+.2f}%")

    print(f"\nORDERS")
    print(f"   Avg active: {stats['avg_active_orders']:.1f}")
    print(f"   Max active: {stats['max_active_orders']}")
    if events:
        for event_type, count in sorted(events.items()):
            print(f"   {event_type}: {count:,}")

    print(f"\nADDRESSES")
    print(f"   Unique traders: {unique_addresses:,}")

    print(f"\nVOLUME & PRESSURE")
    print(f"   Buy volume:  {stats['total_buy_volume']:,.0f}")
    print(f"   Sell volume: {stats['total_sell_volume']:,.0f}")
    print(f"   Net flow:    {stats['total_buy_volume'] - stats['total_sell_volume']:+,.0f}")
    print(f"   Avg net pressure: {stats['avg_net_pressure']:+.2f}")

    print(f"\n{'=' * 60}\n")


def main():
    if len(sys.argv) >= 2:
        db_path = Path(sys.argv[1])
    else:
        db_path = DEFAULT_DB_PATH

    if not db_path.exists():
        print(f"Error: Database not found: {db_path}")
        sys.exit(1)

    print(f"Database: {db_path}")
    print(f"Size: {db_path.stat().st_size / (1024 * 1024):.2f} MB\n")

    conn = get_connection(db_path)

    tables = get_table_info(conn)
    print("Tables:")
    for table, count in tables.items():
        print(f"  {table}: {count:,} rows")
    print()

    if len(sys.argv) >= 3:
        symbol = sys.argv[2].upper()
        analyze_coin(conn, symbol)
    else:
        coins = list_available_coins(conn)

        if not coins:
            print("No snapshot data found in database.")
            sys.exit(1)

        print(f"Found {len(coins)} coins:\n")
        for coin in coins:
            price_low = coin['price_low']
            price_high = coin['price_high']
            if price_low is not None and price_high is not None:
                price_str = f"${price_low:>10,.4f} - ${price_high:>10,.4f}"
            else:
                price_str = "  (no price data)"
            print(f"  {coin['symbol']:12} {coin['snapshot_count']:>8,} snapshots  {price_str}")

    conn.close()


if __name__ == "__main__":
    main()