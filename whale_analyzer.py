#!/usr/bin/env python3
"""
Whale & HYPE Token Analysis
Focus on identifying and tracking whale behavior in HYPE and across tokens
"""

import json
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import statistics


def load_jsonl(filepath):
    """Load newline-delimited JSON file"""
    records = []
    with open(filepath, 'r') as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue
    return records


def analyze_hype_whales(records):
    """Deep dive into HYPE token whale behavior"""

    print("=" * 80)
    print("HYPE TOKEN - WHALE ANALYSIS")
    print("=" * 80)
    print()

    # Filter HYPE records
    hype_records = [r for r in records if r['symbol'] == 'HYPE']
    print(f"HYPE snapshots: {len(hype_records)}")
    print()

    # Collect all HYPE orders
    all_orders = []
    for record in hype_records:
        for order in record.get('active_orders', []):
            order['snapshot_time'] = record['timestamp']
            order['is_whale'] = record['summary'].get('whale_orders', 0) > 0
            all_orders.append(order)

    # Address activity on HYPE
    address_stats = defaultdict(lambda: {
        'buy_orders': 0,
        'sell_orders': 0,
        'buy_volume': 0,
        'sell_volume': 0,
        'order_sizes': [],
        'durations': [],
        'product_types': Counter()
    })

    for order in all_orders:
        addr = order['address']
        stats = address_stats[addr]

        if order['side'] == 'BUY':
            stats['buy_orders'] += 1
            stats['buy_volume'] += order['size']
        else:
            stats['sell_orders'] += 1
            stats['sell_volume'] += order['size']

        stats['order_sizes'].append(order['size'])
        stats['durations'].append(order['duration_hours'])
        stats['product_types'][order['product_type']] += 1

    # Identify top HYPE traders
    print("TOP 15 HYPE TRADERS:")
    print(f"{'Address':45s} {'Orders':>8s} {'Buy':>10s} {'Sell':>10s} {'Net':>12s}")
    print("-" * 95)

    sorted_addresses = sorted(
        address_stats.items(),
        key=lambda x: x[1]['buy_orders'] + x[1]['sell_orders'],
        reverse=True
    )[:15]

    for addr, stats in sorted_addresses:
        total_orders = stats['buy_orders'] + stats['sell_orders']
        net_volume = stats['buy_volume'] - stats['sell_volume']
        print(f"{addr:45s} {total_orders:8,} "
              f"{stats['buy_volume']:10,.0f} {stats['sell_volume']:10,.0f} "
              f"{net_volume:12,.0f}")

    print()

    # Whale order size threshold analysis
    all_sizes = [order['size'] for order in all_orders]
    size_percentiles = {
        '50th': statistics.median(all_sizes),
        '75th': statistics.quantiles(all_sizes, n=4)[2],
        '90th': statistics.quantiles(all_sizes, n=10)[8],
        '95th': statistics.quantiles(all_sizes, n=20)[18],
        '99th': statistics.quantiles(all_sizes, n=100)[98]
    }

    print("HYPE ORDER SIZE DISTRIBUTION:")
    for pct, value in size_percentiles.items():
        print(f"  {pct:6s} percentile: {value:,.2f} HYPE")
    print()

    # Buy vs Sell pressure over time
    print("BUY/SELL PRESSURE ANALYSIS:")
    buy_pressure = [r['summary']['buy_pressure_per_min'] for r in hype_records]
    sell_pressure = [r['summary']['sell_pressure_per_min'] for r in hype_records]
    net_pressure = [r['summary']['net_pressure_per_min'] for r in hype_records]

    print(f"  Avg buy pressure/min:  {statistics.mean(buy_pressure):,.2f}")
    print(f"  Avg sell pressure/min: {statistics.mean(sell_pressure):,.2f}")
    print(f"  Avg net pressure/min:  {statistics.mean(net_pressure):,.2f}")
    print(f"  Max net pressure/min:  {max(net_pressure):,.2f}")
    print(f"  Min net pressure/min:  {min(net_pressure):,.2f}")
    print()

    # Time of day analysis
    hourly_stats = defaultdict(lambda: {'buy': 0, 'sell': 0, 'count': 0})
    for record in hype_records:
        hour = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')).hour
        hourly_stats[hour]['buy'] += record['summary']['buy_volume']
        hourly_stats[hour]['sell'] += record['summary']['sell_volume']
        hourly_stats[hour]['count'] += 1

    print("HYPE ACTIVITY BY HOUR (UTC):")
    print(f"{'Hour':>6s} {'Snapshots':>10s} {'Buy Vol':>12s} {'Sell Vol':>12s} {'Net':>12s}")
    print("-" * 54)
    for hour in sorted(hourly_stats.keys()):
        stats = hourly_stats[hour]
        net = stats['buy'] - stats['sell']
        print(f"{hour:6d} {stats['count']:10d} {stats['buy']:12,.0f} "
              f"{stats['sell']:12,.0f} {net:12,.0f}")

    print()

    return address_stats, sorted_addresses


def cross_token_whale_analysis(records, top_hype_addresses):
    """Analyze where HYPE whales are active across other tokens"""

    print("=" * 80)
    print("CROSS-TOKEN WHALE ACTIVITY")
    print("=" * 80)
    print()

    # Take top 5 HYPE addresses
    whale_addresses = [addr for addr, _ in top_hype_addresses[:5]]

    print("Tracking these HYPE whales across all tokens:")
    for i, addr in enumerate(whale_addresses, 1):
        print(f"  {i}. {addr}")
    print()

    # Track their activity across all tokens
    whale_token_activity = {addr: defaultdict(lambda: {
        'orders': 0, 'buy_volume': 0, 'sell_volume': 0
    }) for addr in whale_addresses}

    for record in records:
        symbol = record['symbol']
        for order in record.get('active_orders', []):
            if order['address'] in whale_addresses:
                addr = order['address']
                stats = whale_token_activity[addr][symbol]
                stats['orders'] += 1

                if order['side'] == 'BUY':
                    stats['buy_volume'] += order['size']
                else:
                    stats['sell_volume'] += order['size']

    # Report findings
    for i, addr in enumerate(whale_addresses, 1):
        print(f"\nWhale #{i} ({addr[:10]}...{addr[-8:]}):")
        token_stats = whale_token_activity[addr]

        # Sort by number of orders
        sorted_tokens = sorted(
            token_stats.items(),
            key=lambda x: x[1]['orders'],
            reverse=True
        )[:10]

        print(f"  Active in {len(token_stats)} different tokens")
        print(f"  Top 10 tokens by activity:")
        print(f"    {'Token':15s} {'Orders':>8s} {'Buy Vol':>12s} {'Sell Vol':>12s}")

        for token, stats in sorted_tokens:
            print(f"    {token:15s} {stats['orders']:8,} "
                  f"{stats['buy_volume']:12,.0f} {stats['sell_volume']:12,.0f}")

    print()


def order_completion_analysis(records):
    """Analyze order completion patterns"""

    print("=" * 80)
    print("ORDER COMPLETION ANALYSIS")
    print("=" * 80)
    print()

    # Collect completion data
    completed_orders = []
    canceled_orders = []

    for record in records:
        for order in record.get('completed_orders', []):
            order['symbol'] = record['symbol']
            order['completion_time'] = record['timestamp']
            completed_orders.append(order)

        for order in record.get('canceled_orders', []):
            order['symbol'] = record['symbol']
            order['cancel_time'] = record['timestamp']
            canceled_orders.append(order)

    print(f"Total completed orders: {len(completed_orders):,}")
    print(f"Total canceled orders:  {len(canceled_orders):,}")

    if completed_orders:
        completion_rate = len(completed_orders) / (len(completed_orders) + len(canceled_orders)) * 100
        print(f"Completion rate:        {completion_rate:.1f}%")
    print()

    # Completion by token
    completions_by_token = Counter(o['symbol'] for o in completed_orders)
    cancels_by_token = Counter(o['symbol'] for o in canceled_orders)

    print("TOP 15 TOKENS BY COMPLETED ORDERS:")
    for token, count in completions_by_token.most_common(15):
        cancel_count = cancels_by_token.get(token, 0)
        total = count + cancel_count
        rate = count / total * 100 if total > 0 else 0
        print(f"  {token:15s} {count:6,} completed, {cancel_count:5,} canceled "
              f"({rate:5.1f}% success)")

    print()

    # Duration analysis for completed orders
    if completed_orders:
        durations = [o['duration_hours'] for o in completed_orders if 'duration_hours' in o]
        if durations:
            print("COMPLETION DURATION STATS:")
            print(f"  Median duration: {statistics.median(durations):.2f} hours")
            print(f"  Mean duration:   {statistics.mean(durations):.2f} hours")
            print(f"  Min duration:    {min(durations):.2f} hours")
            print(f"  Max duration:    {max(durations):.2f} hours")
            print()


def main():
    filepath = "twap_snapshots/all_coins_2025-11-16.jsonl"

    print(f"Loading data from: {filepath}")
    print()

    records = load_jsonl(filepath)
    print(f"Loaded {len(records):,} snapshots")
    print()

    # 1. HYPE whale analysis
    hype_stats, top_hype_traders = analyze_hype_whales(records)

    # 2. Cross-token activity
    cross_token_whale_analysis(records, top_hype_traders)

    # 3. Order completion patterns
    order_completion_analysis(records)

    print("=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()