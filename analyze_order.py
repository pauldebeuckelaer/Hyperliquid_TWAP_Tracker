#!/usr/bin/env python3
"""
Analyze order lifecycle for a specific address in TWAP tracking logs.
Validates that order state transitions are logical and consistent.
"""

import json
from datetime import datetime
from collections import defaultdict

TARGET_ADDRESS = "0x202db88213dec0f8994284f2d85e41e3ff479219"
LOG_FILE = "json_logs/HYPE_20251122.jsonl"


def analyze_address_orders(address):
    """Track all order activity for a specific address."""

    # Track orders by a unique identifier (address + side + size)
    orders_seen = {}  # order_key -> list of states
    order_events = []  # chronological list of events

    print(f"Analyzing orders for address: {address}")
    print("=" * 80)

    with open(LOG_FILE, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                data = json.loads(line)
                timestamp = data['timestamp']
                update_num = data['update_number']

                # Check for orders from this address
                address_orders = [o for o in data.get('active_orders', [])
                                  if o['address'].lower() == address.lower()]

                if address_orders:
                    print(f"\n--- Update {update_num} at {timestamp} ---")
                    for order in address_orders:
                        order_key = f"{order['side']}_{order['size']}_{order['product_type']}"

                        # Track state
                        if order_key not in orders_seen:
                            orders_seen[order_key] = []

                        state_info = {
                            'timestamp': timestamp,
                            'update': update_num,
                            'status': order['status'],
                            'is_active': order['is_active'],
                            'duration_hours': order['duration_hours']
                        }
                        orders_seen[order_key].append(state_info)

                        print(f"  Order: {order['side']} {order['size']} {order['product_type']}")
                        print(f"    Status: {order['status']}, Active: {order['is_active']}")
                        print(f"    Duration: {order['duration_hours']}h")

                # Check events
                events = data.get('events', {})
                new = data.get('new_orders', [])
                completed = data.get('completed_orders', [])
                canceled = data.get('canceled_orders', [])

                # Check if this address appears in any events
                address_in_new = [o for o in new if o.get('address', '').lower() == address.lower()]
                address_in_completed = [o for o in completed if o.get('address', '').lower() == address.lower()]
                address_in_canceled = [o for o in canceled if o.get('address', '').lower() == address.lower()]

                if address_in_new:
                    print(f"\n*** NEW ORDER EVENT at update {update_num} ***")
                    for order in address_in_new:
                        print(f"  {order['side']} {order['size']} {order['product_type']}")
                        order_events.append({
                            'type': 'NEW',
                            'timestamp': timestamp,
                            'update': update_num,
                            'order': order
                        })

                if address_in_completed:
                    print(f"\n*** COMPLETED ORDER EVENT at update {update_num} ***")
                    for order in address_in_completed:
                        print(f"  {order['side']} {order['size']} {order['product_type']}")
                        order_events.append({
                            'type': 'COMPLETED',
                            'timestamp': timestamp,
                            'update': update_num,
                            'order': order
                        })

                if address_in_canceled:
                    print(f"\n*** CANCELED ORDER EVENT at update {update_num} ***")
                    for order in address_in_canceled:
                        print(f"  {order['side']} {order['size']} {order['product_type']}")
                        order_events.append({
                            'type': 'CANCELED',
                            'timestamp': timestamp,
                            'update': update_num,
                            'order': order
                        })

            except json.JSONDecodeError:
                print(f"Warning: Could not parse line {line_num}")
                continue
            except Exception as e:
                print(f"Warning: Error processing line {line_num}: {e}")
                continue

    # Summary analysis
    print("\n" + "=" * 80)
    print("SUMMARY ANALYSIS")
    print("=" * 80)

    if not orders_seen and not order_events:
        print(f"\n⚠️  No orders found for address {address}")
        return

    print(f"\nTotal unique orders tracked: {len(orders_seen)}")
    print(f"Total events detected: {len(order_events)}")

    # Analyze each order's lifecycle
    print("\n--- ORDER LIFECYCLES ---")
    for order_key, states in orders_seen.items():
        print(f"\nOrder: {order_key}")
        print(f"  Total state snapshots: {len(states)}")
        print(f"  First seen: {states[0]['timestamp']} (update {states[0]['update']})")
        print(f"  Last seen: {states[-1]['timestamp']} (update {states[-1]['update']})")

        # Check for status transitions
        status_sequence = [s['status'] for s in states]
        unique_statuses = list(dict.fromkeys(status_sequence))  # Preserve order
        print(f"  Status sequence: {' → '.join(unique_statuses)}")

        # Check for inconsistencies
        active_flags = [s['is_active'] for s in states]
        if True in active_flags and False in active_flags:
            print(f"  ⚠️  is_active changed from True to False")

        # Look for the bug: status changes but is_active doesn't match
        for i, state in enumerate(states):
            if state['status'] in ['completed', 'canceled'] and state['is_active']:
                print(f"  🐛 BUG DETECTED: Status '{state['status']}' but is_active=True at update {state['update']}")
            if state['status'] == 'active' and not state['is_active']:
                print(f"  🐛 BUG DETECTED: Status 'active' but is_active=False at update {state['update']}")

    # Check for the critical double-event bug
    print("\n--- CHECKING FOR DOUBLE-EVENT BUG ---")
    updates_with_events = defaultdict(list)
    for event in order_events:
        updates_with_events[event['update']].append(event['type'])

    for update, event_types in updates_with_events.items():
        if 'COMPLETED' in event_types and 'CANCELED' in event_types:
            print(f"🐛 CRITICAL BUG: Update {update} has BOTH completed and canceled events!")
        elif len(event_types) > 1:
            print(f"⚠️  Multiple events in update {update}: {event_types}")

    if not any('COMPLETED' in types and 'CANCELED' in types for types in updates_with_events.values()):
        print("✅ No double-event bug detected (order appearing in both completed and canceled)")


if __name__ == "__main__":
    analyze_address_orders(TARGET_ADDRESS)