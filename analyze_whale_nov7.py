#!/usr/bin/env python3
"""
Analyze the suspicious whale 0x5aeb1821...0511cc on November 7th
This whale placed 1.92M HYPE in buy orders, all canceled, only active on Nov 7
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime

# The suspicious whale address
WHALE_ADDRESS = "0x5aeb1821f596d2d9ffe182d3f914b274a80511cc"

# Data file for Nov 7
DATA_FILE = Path("json_logs/HYPE_20251107.jsonl")


def analyze_whale_activity():
    """Analyze the whale's activity minute by minute on Nov 7"""

    print("=" * 80)
    print(f"ANALYZING WHALE: {WHALE_ADDRESS}")
    print("DATE: November 7, 2025")
    print("=" * 80)
    print()

    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE} not found!")
        return

    # Track all orders from this whale
    whale_orders = []
    timeline = []

    # Load the entire day
    snapshots = []
    with open(DATA_FILE, 'r') as f:
        for line in f:
            snapshot = json.loads(line.strip())
            snapshots.append(snapshot)

    print(f"Loaded {len(snapshots)} snapshots from Nov 7th")
    print()

    # First pass: find all unique orders from this whale
    seen_orders = set()

    for snapshot in snapshots:
        timestamp = snapshot['timestamp']

        for order in snapshot.get('active_orders', []):
            if order['address'].lower() == WHALE_ADDRESS.lower():
                order_id = f"{order['side']}_{order['size']}_{order['duration_hours']}_{order['product_type']}"

                if order_id not in seen_orders:
                    seen_orders.add(order_id)
                    whale_orders.append({
                        'order_id': order_id,
                        'first_seen': timestamp,
                        'side': order['side'],
                        'size': order['size'],
                        'duration_hours': order['duration_hours'],
                        'product_type': order['product_type'],
                        'status': order['status'],
                        'last_seen': timestamp,
                        'snapshots_seen': 1
                    })
                else:
                    # Update existing order
                    for wo in whale_orders:
                        if wo['order_id'] == order_id:
                            wo['last_seen'] = timestamp
                            wo['snapshots_seen'] += 1
                            wo['status'] = order['status']
                            break

        # Track whale presence in timeline
        whale_present = any(
            o['address'].lower() == WHALE_ADDRESS.lower()
            for o in snapshot.get('active_orders', [])
        )

        if whale_present:
            whale_total_size = sum(
                o['size'] for o in snapshot.get('active_orders', [])
                if o['address'].lower() == WHALE_ADDRESS.lower() and o['side'] == 'BUY'
            )

            timeline.append({
                'timestamp': datetime.fromisoformat(timestamp),
                'whale_buy_size': whale_total_size,
                'total_buy_volume': snapshot['summary']['buy_volume'],
                'total_sell_volume': snapshot['summary']['sell_volume'],
                'net_flow': snapshot['summary']['net_flow'],
                'total_orders': snapshot['summary']['total_orders']
            })

    # Print order summary
    print(f"TOTAL UNIQUE ORDERS: {len(whale_orders)}")
    print()
    print("=" * 80)
    print("ORDER DETAILS:")
    print("=" * 80)

    total_buy_volume = 0
    total_sell_volume = 0

    for i, order in enumerate(sorted(whale_orders, key=lambda x: x['size'], reverse=True), 1):
        first_seen = datetime.fromisoformat(order['first_seen'])
        last_seen = datetime.fromisoformat(order['last_seen'])
        duration_minutes = (last_seen - first_seen).total_seconds() / 60

        if order['side'] == 'BUY':
            total_buy_volume += order['size']
        else:
            total_sell_volume += order['size']

        print(f"\n{i}. {order['side']} Order:")
        print(f"   Size: {order['size']:,.2f} HYPE")
        print(f"   Product: {order['product_type']}")
        print(f"   Duration Setting: {order['duration_hours']} hours")
        print(f"   Final Status: {order['status']}")
        print(f"   First Seen: {first_seen.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Last Seen: {last_seen.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Active For: {duration_minutes:.1f} minutes ({order['snapshots_seen']} snapshots)")

        if order['status'] == 'canceled':
            print(f"   ⚠️  CANCELED - Order never filled!")
        elif order['status'] == 'completed':
            print(f"   ✅ COMPLETED")
        else:
            print(f"   🟡 Status: {order['status']}")

    print()
    print("=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print(f"Total Buy Volume: {total_buy_volume:,.2f} HYPE")
    print(f"Total Sell Volume: {total_sell_volume:,.2f} HYPE")
    print(f"Net Volume: {total_buy_volume - total_sell_volume:,.2f} HYPE")
    print()

    canceled_count = sum(1 for o in whale_orders if o['status'] == 'canceled')
    completed_count = sum(1 for o in whale_orders if o['status'] == 'completed')

    print(f"Canceled Orders: {canceled_count}/{len(whale_orders)} ({canceled_count / len(whale_orders) * 100:.1f}%)")
    print(f"Completed Orders: {completed_count}/{len(whale_orders)} ({completed_count / len(whale_orders) * 100:.1f}%)")

    # Timeline analysis
    if timeline:
        df = pd.DataFrame(timeline)

        print()
        print("=" * 80)
        print("TIMELINE ANALYSIS:")
        print("=" * 80)

        start_time = df['timestamp'].min()
        end_time = df['timestamp'].max()
        duration_hours = (end_time - start_time).total_seconds() / 3600

        print(f"Whale Active Period: {start_time.strftime('%H:%M:%S')} to {end_time.strftime('%H:%M:%S')}")
        print(f"Total Duration: {duration_hours:.2f} hours")
        print()

        avg_whale_size = df['whale_buy_size'].mean()
        max_whale_size = df['whale_buy_size'].max()

        print(f"Average Whale Buy Size During Activity: {avg_whale_size:,.2f} HYPE")
        print(f"Maximum Whale Buy Size: {max_whale_size:,.2f} HYPE")
        print()

        # Market impact analysis
        avg_total_buy = df['total_buy_volume'].mean()
        whale_market_share = (avg_whale_size / avg_total_buy * 100) if avg_total_buy > 0 else 0

        print(f"Whale's Share of Total Buy Volume: {whale_market_share:.1f}%")
        print()

        # Check if market behavior changed when whale appeared/disappeared
        print("MARKET BEHAVIOR WHEN WHALE WAS ACTIVE:")
        print(f"Average Net Flow: {df['net_flow'].mean():,.2f} HYPE")
        print(f"Average Total Buy Volume: {df['total_buy_volume'].mean():,.2f} HYPE")
        print(f"Average Total Sell Volume: {df['total_sell_volume'].mean():,.2f} HYPE")

    print()
    print("=" * 80)
    print("SPOOFING INDICATORS:")
    print("=" * 80)

    # Check for spoofing patterns
    spoofing_score = 0
    indicators = []

    if canceled_count == len(whale_orders):
        spoofing_score += 3
        indicators.append("🚨 ALL ORDERS CANCELED (High suspicion)")
    elif canceled_count / len(whale_orders) > 0.8:
        spoofing_score += 2
        indicators.append("⚠️  >80% orders canceled")

    avg_duration = sum(o['snapshots_seen'] for o in whale_orders) / len(whale_orders)
    if avg_duration < 60:  # Less than 1 hour average
        spoofing_score += 2
        indicators.append(f"⚠️  Short-lived orders (avg {avg_duration:.1f} snapshots)")

    if total_buy_volume > 1000000:  # Over 1M HYPE
        spoofing_score += 2
        indicators.append(f"⚠️  Massive size ({total_buy_volume:,.0f} HYPE)")

    if duration_hours < 12:  # Active less than half day
        spoofing_score += 1
        indicators.append(f"⚠️  Brief activity period ({duration_hours:.1f} hours)")

    large_orders = [o for o in whale_orders if o['size'] > 50000]
    if len(large_orders) > 5:
        spoofing_score += 1
        indicators.append(f"⚠️  Multiple large orders (>50k HYPE): {len(large_orders)} orders")

    print()
    for indicator in indicators:
        print(indicator)

    print()
    print(f"SPOOFING SCORE: {spoofing_score}/11")

    if spoofing_score >= 7:
        print("🚨 VERDICT: HIGHLY LIKELY SPOOFING")
        print("This looks like market manipulation - fake buy walls to prop up price")
    elif spoofing_score >= 4:
        print("⚠️  VERDICT: SUSPICIOUS BEHAVIOR")
        print("Could be legitimate market making, but patterns suggest manipulation")
    else:
        print("✅ VERDICT: Possibly legitimate, but unusual")

    print()
    print("=" * 80)
    print("HYPOTHESIS:")
    print("=" * 80)
    print("This whale likely placed massive fake buy orders on Nov 7th to:")
    print("1. Create artificial buy-side liquidity")
    print("2. Prevent price from falling during broader market weakness")
    print("3. Attract retail buyers (\"look at the bid support!\")")
    print("4. Then canceled everything without actually buying")
    print()
    print("This is textbook spoofing behavior and explains why HYPE held up")
    print("despite BTC weakness - the 'support' was artificial.")


if __name__ == "__main__":
    analyze_whale_activity()