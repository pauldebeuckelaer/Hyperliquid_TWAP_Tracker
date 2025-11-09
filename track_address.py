#!/usr/bin/env python3
"""
Track address activity over time from HYPE order book data
"""

import json
from datetime import datetime
from collections import defaultdict
import sys
import glob
import os


def track_address_activity(file_pattern, target_address):
    """
    Track all activity for a specific address across snapshots

    Args:
        file_pattern: Path pattern to the JSONL data files (e.g., "json_logs/HYPE_*.jsonl")
        target_address: The Ethereum address to track (case-insensitive)

    Returns:
        Dictionary with activity history
    """
    target_address = target_address.lower()

    # Get all matching files
    files = sorted(glob.glob(file_pattern))

    if not files:
        print(f"ERROR: No files found matching pattern: {file_pattern}")
        sys.exit(1)

    print(f"Found {len(files)} files to process:")
    for f in files:
        print(f"  - {f}")
    print()

    # Store activity
    activity_timeline = []
    order_history = {}  # Track order states over time: {(side, size): [events]}
    order_lifecycle = {}  # Track individual order lifecycles

    # Track first/last appearance
    first_seen = None
    last_seen = None

    # Statistics
    stats = {
        'total_snapshots_with_address': 0,
        'new_orders': 0,
        'completed_orders': 0,
        'canceled_orders': 0,
        'status_changes': 0,
        'max_simultaneous_orders': 0,
        'total_buy_volume': 0,
        'total_sell_volume': 0,
        'both_completed_and_canceled': 0,  # NEW: track dual events
        'unique_order_sizes': set(),  # NEW: track unique order sizes
    }

    print(f"Tracking address: {target_address}")
    print("Processing snapshots...\n")

    snapshot_count = 0

    # Process each file
    for file_path in files:
        print(f"Processing: {os.path.basename(file_path)}")

        with open(file_path, 'r') as f:
            for line in f:
                if not line.strip():
                    continue

                snapshot_count += 1
                if snapshot_count % 1000 == 0:
                    print(f"Processed {snapshot_count} snapshots...")

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                timestamp = data.get('timestamp', '')
                update_num = data.get('update_number', '')

                # Check if address appears in active orders
                active_orders = data.get('active_orders', [])
                address_orders = [order for order in active_orders
                                  if order.get('address', '').lower() == target_address]

                if address_orders:
                    if first_seen is None:
                        first_seen = timestamp
                    last_seen = timestamp
                    stats['total_snapshots_with_address'] += 1

                    # Track max simultaneous orders
                    active_count = sum(1 for o in address_orders if o.get('is_active'))
                    if active_count > stats['max_simultaneous_orders']:
                        stats['max_simultaneous_orders'] = active_count

                    # Track order lifecycle
                    for order in address_orders:
                        order_key = (order.get('side'), order.get('size'))
                        stats['unique_order_sizes'].add(order_key)

                        if order_key not in order_lifecycle:
                            order_lifecycle[order_key] = {
                                'first_seen': timestamp,
                                'last_seen': timestamp,
                                'status_history': [],
                                'duration_hours': order.get('duration_hours', 0)
                            }

                        order_lifecycle[order_key]['last_seen'] = timestamp
                        current_status = order.get('status')
                        is_active = order.get('is_active')

                        # Track status changes in lifecycle
                        last_status = order_lifecycle[order_key]['status_history'][-1] if order_lifecycle[order_key][
                            'status_history'] else None
                        if last_status != (current_status, is_active):
                            order_lifecycle[order_key]['status_history'].append((current_status, is_active))

                # Check new orders
                new_orders = data.get('new_orders', [])
                for order in new_orders:
                    if order.get('address', '').lower() == target_address:
                        stats['new_orders'] += 1
                        side = order.get('side', 'UNKNOWN')
                        size = order.get('size', 0)

                        if side == 'BUY':
                            stats['total_buy_volume'] += size
                        elif side == 'SELL':
                            stats['total_sell_volume'] += size

                        activity_timeline.append({
                            'timestamp': timestamp,
                            'update_number': update_num,
                            'event': 'NEW_ORDER',
                            'side': side,
                            'size': size,
                            'duration_hours': order.get('duration_hours', 0),
                            'product_type': order.get('product_type', 'UNKNOWN')
                        })

                # Check completed orders
                completed_orders = data.get('completed_orders', [])
                completed_in_this_snapshot = []
                for order in completed_orders:
                    if order.get('address', '').lower() == target_address:
                        stats['completed_orders'] += 1
                        order_key = (order.get('side'), order.get('size'))
                        completed_in_this_snapshot.append(order_key)
                        activity_timeline.append({
                            'timestamp': timestamp,
                            'update_number': update_num,
                            'event': 'COMPLETED',
                            'side': order.get('side', 'UNKNOWN'),
                            'size': order.get('size', 0)
                        })

                # Check canceled orders
                canceled_orders = data.get('canceled_orders', [])
                canceled_in_this_snapshot = []
                for order in canceled_orders:
                    if order.get('address', '').lower() == target_address:
                        stats['canceled_orders'] += 1
                        order_key = (order.get('side'), order.get('size'))
                        canceled_in_this_snapshot.append(order_key)
                        activity_timeline.append({
                            'timestamp': timestamp,
                            'update_number': update_num,
                            'event': 'CANCELED',
                            'side': order.get('side', 'UNKNOWN'),
                            'size': order.get('size', 0)
                        })

                # Check for orders that are both completed AND canceled at same time
                both_events = set(completed_in_this_snapshot) & set(canceled_in_this_snapshot)
                if both_events:
                    stats['both_completed_and_canceled'] += len(both_events)
                    for order_key in both_events:
                        activity_timeline.append({
                            'timestamp': timestamp,
                            'update_number': update_num,
                            'event': 'DUAL_EVENT',
                            'side': order_key[0],
                            'size': order_key[1],
                            'note': 'Order appears in BOTH completed and canceled lists'
                        })

                # Check status changes
                status_changes = data.get('status_changes', [])
                for change in status_changes:
                    full_addr = change.get('full_address', '').lower()
                    if full_addr == target_address or change.get('address', '').lower() == target_address:
                        stats['status_changes'] += 1
                        activity_timeline.append({
                            'timestamp': timestamp,
                            'update_number': update_num,
                            'event': 'STATUS_CHANGE',
                            'old_status': change.get('old_status', 'UNKNOWN'),
                            'new_status': change.get('new_status', 'UNKNOWN'),
                            'side': change.get('side', 'UNKNOWN'),
                            'size': change.get('size', 0)
                        })

    print(f"\nProcessed {snapshot_count} total snapshots across {len(files)} files")

    # Convert set to list for JSON serialization
    stats['unique_order_sizes'] = list(stats['unique_order_sizes'])

    return {
        'address': target_address,
        'first_seen': first_seen,
        'last_seen': last_seen,
        'statistics': stats,
        'activity_timeline': activity_timeline,
        'order_lifecycle': order_lifecycle
    }


def print_summary(results):
    """Print a summary of the address activity"""
    print("\n" + "=" * 80)
    print(f"ADDRESS ACTIVITY SUMMARY")
    print("=" * 80)
    print(f"\nAddress: {results['address']}")
    print(f"First Seen: {results['first_seen']}")
    print(f"Last Seen: {results['last_seen']}")

    stats = results['statistics']
    print(f"\nSTATISTICS:")
    print(f"  Snapshots with address: {stats['total_snapshots_with_address']}")
    print(f"  New orders: {stats['new_orders']}")
    print(f"  Completed orders: {stats['completed_orders']}")
    print(f"  Canceled orders: {stats['canceled_orders']}")
    print(f"  Status changes: {stats['status_changes']}")
    print(f"  Max simultaneous orders: {stats['max_simultaneous_orders']}")
    print(f"  Total BUY volume: {stats['total_buy_volume']:.2f}")
    print(f"  Total SELL volume: {stats['total_sell_volume']:.2f}")
    print(f"  Net flow: {stats['total_buy_volume'] - stats['total_sell_volume']:.2f}")
    print(f"\n  ⚠️  Orders that appeared as BOTH completed AND canceled: {stats['both_completed_and_canceled']}")
    print(f"  Unique order sizes tracked: {len(stats['unique_order_sizes'])}")

    # Show order lifecycle summary
    order_lifecycle = results.get('order_lifecycle', {})
    if order_lifecycle:
        print(f"\nORDER LIFECYCLE SUMMARY:")
        print("-" * 80)
        for order_key, lifecycle in list(order_lifecycle.items())[:10]:
            side, size = order_key
            print(f"\n  {side} {size:.2f} (duration: {lifecycle['duration_hours']}h)")
            print(f"    First seen: {lifecycle['first_seen']}")
            print(f"    Last seen: {lifecycle['last_seen']}")
            print(f"    Status changes: {len(lifecycle['status_history'])}")
            if lifecycle['status_history']:
                status_prog = ' → '.join(
                    [f"{s[0]}({'active' if s[1] else 'inactive'})" for s in lifecycle['status_history'][:5]])
                print(f"    Status progression: {status_prog}")

        if len(order_lifecycle) > 10:
            print(f"\n  ... and {len(order_lifecycle) - 10} more orders")

    timeline = results['activity_timeline']
    if timeline:
        print(f"\nACTIVITY TIMELINE ({len(timeline)} events):")
        print("-" * 80)

        # Count dual events
        dual_events = [e for e in timeline if e.get('event') == 'DUAL_EVENT']
        if dual_events:
            print(f"\n⚠️  DUAL EVENTS (completed + canceled at same time): {len(dual_events)}")
            print("-" * 80)
            for event in dual_events[:5]:
                print(f"{event['timestamp']} | ⚠️  DUAL: {event['side']} {event['size']:.2f}")
                print(f"  → {event.get('note', 'N/A')}")
            if len(dual_events) > 5:
                print(f"... and {len(dual_events) - 5} more dual events")
            print("-" * 80)

        print("\nFull timeline (first 20 events):")
        for event in timeline[:20]:
            ts = event['timestamp']
            evt = event['event']

            if evt == 'NEW_ORDER':
                print(f"{ts} | NEW ORDER: {event['side']} {event['size']:.2f} "
                      f"({event['duration_hours']}h, {event['product_type']})")
            elif evt == 'COMPLETED':
                print(f"{ts} | COMPLETED: {event['side']} {event['size']:.2f}")
            elif evt == 'CANCELED':
                print(f"{ts} | CANCELED: {event['side']} {event['size']:.2f}")
            elif evt == 'STATUS_CHANGE':
                print(f"{ts} | STATUS CHANGE: {event['old_status']} -> {event['new_status']} "
                      f"({event['side']} {event['size']:.2f})")
            elif evt == 'DUAL_EVENT':
                print(f"{ts} | ⚠️  DUAL EVENT: {event['side']} {event['size']:.2f}")

        if len(timeline) > 20:
            print(f"\n... and {len(timeline) - 20} more events")


def save_results(results, output_file):
    """Save results to JSON file"""
    # Convert order_lifecycle tuple keys to strings for JSON serialization
    if 'order_lifecycle' in results:
        order_lifecycle_serializable = {}
        for (side, size), lifecycle in results['order_lifecycle'].items():
            key = f"{side}_{size}"
            order_lifecycle_serializable[key] = lifecycle
        results['order_lifecycle'] = order_lifecycle_serializable

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n\nResults saved to: {output_file}")


if __name__ == "__main__":
    # Configuration
    TARGET_ADDRESS = "0x16ef82c790ab0c14ea19b58a6c5d0880237e622f"
    DATA_FOLDER = "json_logs"
    FILE_PATTERN = f"{DATA_FOLDER}/HYPE_*.jsonl"
    OUTPUT_FILE = f"address_{TARGET_ADDRESS[-8:]}_activity.json"

    # You can also pass custom parameters via command line
    # Usage: python track_address.py [folder_path] [address]
    if len(sys.argv) > 1:
        DATA_FOLDER = sys.argv[1]
        FILE_PATTERN = f"{DATA_FOLDER}/HYPE_*.jsonl"
    if len(sys.argv) > 2:
        TARGET_ADDRESS = sys.argv[2]
        OUTPUT_FILE = f"address_{TARGET_ADDRESS[-8:]}_activity.json"

    # Track the address
    results = track_address_activity(FILE_PATTERN, TARGET_ADDRESS)

    # Print summary
    print_summary(results)

    # Save detailed results
    save_results(results, OUTPUT_FILE)