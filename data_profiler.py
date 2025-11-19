#!/usr/bin/env python3
"""
TWAP Snapshot Data Profiler
Analyzes TWAP order tracking data from Hyperliquid
"""

import json
from datetime import datetime
from collections import defaultdict, Counter
import sys


def load_jsonl(filepath):
    """Load newline-delimited JSON file"""
    records = []
    with open(filepath, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                record = json.loads(line.strip())
                records.append(record)
            except json.JSONDecodeError as e:
                print(f"Warning: Failed to parse line {line_num}: {e}")
                continue
    return records


def profile_data(records):
    """Generate comprehensive data profile"""

    print("=" * 80)
    print("TWAP SNAPSHOT DATA PROFILE")
    print("=" * 80)
    print()

    # Basic counts
    print(f"Total snapshots loaded: {len(records):,}")
    print()

    # Time range analysis
    timestamps = [datetime.fromisoformat(r['timestamp'].replace('Z', '+00:00')) for r in records]
    print("TIME COVERAGE:")
    print(f"  First snapshot: {min(timestamps)}")
    print(f"  Last snapshot:  {max(timestamps)}")
    print(f"  Duration:       {max(timestamps) - min(timestamps)}")
    print()

    # Symbol analysis
    symbols = [r['symbol'] for r in records]
    symbol_counts = Counter(symbols)
    unique_symbols = len(symbol_counts)

    print(f"TOKENS TRACKED: {unique_symbols}")
    print()
    print("Top 20 most frequently tracked tokens:")
    for symbol, count in symbol_counts.most_common(20):
        print(f"  {symbol:20s} {count:6,} snapshots")
    print()

    # Update number analysis (shows how many updates per token)
    update_nums_by_symbol = defaultdict(list)
    for r in records:
        update_nums_by_symbol[r['symbol']].append(r['update_number'])

    print("SNAPSHOT FREQUENCY (by max update_number):")
    top_update_symbols = sorted(update_nums_by_symbol.items(),
                                key=lambda x: max(x[1]),
                                reverse=True)[:10]
    for symbol, updates in top_update_symbols:
        print(f"  {symbol:20s} update #{max(updates):5,}")
    print()

    # Address analysis
    all_addresses = set()
    for r in records:
        # Extract from all order lists
        for order in r.get('active_orders', []):
            all_addresses.add(order['address'])
        for order in r.get('new_orders', []):
            all_addresses.add(order['address'])
        for order in r.get('completed_orders', []):
            all_addresses.add(order['address'])
        for order in r.get('canceled_orders', []):
            all_addresses.add(order['address'])

    print(f"UNIQUE ADDRESSES: {len(all_addresses):,}")
    print()

    # Volume analysis
    total_buy_volume = sum(r['summary'].get('buy_volume', 0) for r in records)
    total_sell_volume = sum(r['summary'].get('sell_volume', 0) for r in records)

    print("CUMULATIVE VOLUMES (across all snapshots):")
    print(f"  Total buy volume:  {total_buy_volume:,.2f}")
    print(f"  Total sell volume: {total_sell_volume:,.2f}")
    print(f"  Net flow:          {total_buy_volume - total_sell_volume:,.2f}")
    print()

    # Order statistics
    total_orders = sum(r['summary'].get('total_orders', 0) for r in records)
    total_active = sum(r['summary'].get('active_orders', 0) for r in records)
    total_whale = sum(r['summary'].get('whale_orders', 0) for r in records)

    print("ORDER STATISTICS:")
    print(f"  Total orders tracked:  {total_orders:,}")
    print(f"  Total active orders:   {total_active:,}")
    print(f"  Total whale orders:    {total_whale:,}")
    print()

    # Event analysis
    new_orders = sum(r['events'].get('new_orders', 0) for r in records)
    completed = sum(r['events'].get('completed_orders', 0) for r in records)
    canceled = sum(r['events'].get('canceled_orders', 0) for r in records)
    status_changes = sum(r['events'].get('status_changes', 0) for r in records)

    print("EVENT COUNTS:")
    print(f"  New orders:        {new_orders:,}")
    print(f"  Completed orders:  {completed:,}")
    print(f"  Canceled orders:   {canceled:,}")
    print(f"  Status changes:    {status_changes:,}")
    print()

    # Product type analysis
    product_types = Counter()
    sides = Counter()
    for r in records:
        for order in r.get('active_orders', []):
            product_types[order.get('product_type', 'UNKNOWN')] += 1
            sides[order.get('side', 'UNKNOWN')] += 1

    print("PRODUCT TYPES:")
    for ptype, count in product_types.most_common():
        print(f"  {ptype:10s} {count:,} orders")
    print()

    print("ORDER SIDES:")
    for side, count in sides.most_common():
        print(f"  {side:10s} {count:,} orders")
    print()

    # Find most active tokens by order activity
    orders_by_symbol = defaultdict(int)
    for r in records:
        orders_by_symbol[r['symbol']] += len(r.get('active_orders', []))

    print("TOP 15 TOKENS BY TOTAL ACTIVE ORDERS:")
    for symbol, order_count in sorted(orders_by_symbol.items(),
                                      key=lambda x: x[1],
                                      reverse=True)[:15]:
        print(f"  {symbol:20s} {order_count:,} orders")
    print()

    # Data quality checks
    print("DATA QUALITY:")
    missing_fields = 0
    records_with_errors = 0

    for r in records:
        # Check for essential fields
        if not all(k in r for k in ['timestamp', 'symbol', 'summary', 'events']):
            missing_fields += 1

        # Check for error status in orders
        for order in r.get('active_orders', []) + r.get('completed_orders', []):
            if order.get('status') == 'error':
                records_with_errors += 1
                break

    print(f"  Records with missing fields: {missing_fields}")
    print(f"  Records with error status:   {records_with_errors}")
    print()

    # Address behavior preview (top addresses by activity)
    address_activity = Counter()
    for r in records:
        for order in r.get('active_orders', []):
            address_activity[order['address']] += 1

    print("TOP 10 MOST ACTIVE ADDRESSES:")
    for addr, count in address_activity.most_common(10):
        print(f"  {addr} - {count:,} active orders")
    print()

    print("=" * 80)
    print("PROFILE COMPLETE")
    print("=" * 80)

    return {
        'total_records': len(records),
        'unique_symbols': unique_symbols,
        'unique_addresses': len(all_addresses),
        'time_range': (min(timestamps), max(timestamps)),
        'symbol_counts': symbol_counts,
        'total_whale_orders': total_whale
    }


if __name__ == "__main__":
    filepath = "twap_snapshots/all_coins_2025-11-16.jsonl"

    print(f"Loading data from: {filepath}")
    print()

    try:
        records = load_jsonl(filepath)
        stats = profile_data(records)

        # Save summary stats
        with open('/home/claude/data_profile_summary.json', 'w') as f:
            json.dump({
                'total_records': stats['total_records'],
                'unique_symbols': stats['unique_symbols'],
                'unique_addresses': stats['unique_addresses'],
                'time_range_start': str(stats['time_range'][0]),
                'time_range_end': str(stats['time_range'][1]),
                'total_whale_orders': stats['total_whale_orders']
            }, f, indent=2)

        print()
        print(f"Summary saved to: /home/claude/data_profile_summary.json")

    except FileNotFoundError:
        print(f"ERROR: File not found: {filepath}")
        print("Please check the file path and try again.")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)