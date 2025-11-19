#!/usr/bin/env python3
"""
Check if same orders appear in both completed and canceled lists
"""

import json
from collections import defaultdict

FILE_PATH = "json_logs/HYPE_20251118.jsonl"


def check_duplicate_events():
    """Check for orders appearing in multiple event types"""

    completed_orders = {}  # address -> list of (timestamp, size, side)
    canceled_orders = {}  # address -> list of (timestamp, size, side)

    with open(FILE_PATH, 'r') as f:
        for line in f:
            if not line.strip():
                continue

            update = json.loads(line)

            # Track completed orders
            if 'completed_orders' in update and update['completed_orders']:
                for order in update['completed_orders']:
                    addr = order['address']
                    if addr not in completed_orders:
                        completed_orders[addr] = []
                    completed_orders[addr].append({
                        'timestamp': update['timestamp'],
                        'size': order['size'],
                        'side': order['side'],
                        'duration': order.get('duration_hours', 'N/A')
                    })

            # Track canceled orders
            if 'canceled_orders' in update and update['canceled_orders']:
                for order in update['canceled_orders']:
                    addr = order['address']
                    if addr not in canceled_orders:
                        canceled_orders[addr] = []
                    canceled_orders[addr].append({
                        'timestamp': update['timestamp'],
                        'size': order['size'],
                        'side': order['side'],
                        'duration': order.get('duration_hours', 'N/A')
                    })

    print("=" * 80)
    print("CHECKING FOR ORDERS IN BOTH COMPLETED AND CANCELED LISTS")
    print("=" * 80)

    # Find addresses that appear in both
    both_addresses = set(completed_orders.keys()) & set(canceled_orders.keys())

    if both_addresses:
        print(f"\n⚠️  Found {len(both_addresses)} addresses with BOTH completed and canceled orders:")
        print("\nThis is NORMAL if:")
        print("  - Different orders from same address (one completed, another canceled)")
        print("  - Same order partially filled then canceled (TWAP behavior)")
        print("\nThis is a BUG if:")
        print("  - Exact same order (size, side, time) appears in both lists")

        print("\n" + "=" * 80)
        print("DETAILED ANALYSIS")
        print("=" * 80)

        for addr in sorted(both_addresses):
            print(f"\n{addr[:10]}...")

            print(f"\n  Completed Orders ({len(completed_orders[addr])}):")
            for order in completed_orders[addr]:
                print(
                    f"    {order['timestamp'][11:19]} - {order['side']} {order['size']:.2f} HYPE ({order['duration']}h)")

            print(f"\n  Canceled Orders ({len(canceled_orders[addr])}):")
            for order in canceled_orders[addr]:
                print(
                    f"    {order['timestamp'][11:19]} - {order['side']} {order['size']:.2f} HYPE ({order['duration']}h)")

            # Check for exact duplicates (size + side match)
            print("\n  Checking for exact duplicates...")
            duplicates_found = False
            for comp in completed_orders[addr]:
                for canc in canceled_orders[addr]:
                    if (comp['size'] == canc['size'] and
                            comp['side'] == canc['side'] and
                            comp['duration'] == canc['duration']):
                        print(f"    🚨 POTENTIAL BUG: {comp['side']} {comp['size']:.2f} HYPE appears in BOTH lists!")
                        duplicates_found = True

            if not duplicates_found:
                print(f"    ✓ No exact duplicates - different orders from same address")

    else:
        print("\n✓ No addresses appear in both completed and canceled lists")
        print("  This means: no order is recorded as BOTH completed AND canceled")

    # Additional stats
    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)
    print(f"\nTotal addresses with completed orders: {len(completed_orders)}")
    print(f"Total addresses with canceled orders: {len(canceled_orders)}")
    print(f"Addresses with both: {len(both_addresses)}")
    print(
        f"Percentage with both: {100 * len(both_addresses) / len(set(completed_orders.keys()) | set(canceled_orders.keys())):.1f}%")


if __name__ == "__main__":
    check_duplicate_events()