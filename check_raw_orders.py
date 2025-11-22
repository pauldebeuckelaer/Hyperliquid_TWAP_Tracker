#!/usr/bin/env python3
"""
Better Diagnostic: Show RAW order data with hashes
"""
import json
from pathlib import Path
from glob import glob

TARGET_ADDRESS = "0x28f0233472b6a44e170e002a72845ca100be4a7e"

# Find latest log
log_files = sorted(glob('json_logs/HYPE_*.jsonl'), reverse=True)
log_file = log_files[0]

print("Checking raw order data from API")
print("=" * 80)
print(f"Target address: {TARGET_ADDRESS}")
print(f"Log file: {log_file}")
print()

# Look at a specific update where we see the problem
TARGET_UPDATE = 1001

with open(log_file, 'r') as f:
    for line in f:
        try:
            data = json.loads(line)
            update_num = data.get('update_number')

            if update_num == TARGET_UPDATE:
                print(f"UPDATE {update_num}")
                print("=" * 80)

                all_orders = data.get('active_orders', [])

                # Filter for target address
                matching_orders = [
                    o for o in all_orders
                    if (o.get('address') or o.get('full_address', '')).lower() == TARGET_ADDRESS.lower()
                ]

                print(f"Found {len(matching_orders)} orders for this address")
                print()

                for i, order in enumerate(matching_orders, 1):
                    print(f"ORDER #{i}:")
                    print("-" * 80)

                    # Show key fields
                    print(f"  address: {order.get('address') or order.get('full_address')}")
                    print(f"  side: {order.get('side')}")
                    print(f"  size: {order.get('size')}")
                    print(f"  status: {order.get('status')}")
                    print(f"  is_active: {order.get('is_active')}")
                    print(f"  order_hash: {order.get('order_hash')}")
                    print(f"  hash: {order.get('hash')}")
                    print(f"  duration_hours: {order.get('duration_hours')}")
                    print(f"  product_type: {order.get('product_type')}")

                    # Show raw data structure
                    print()
                    print("  RAW ORDER DATA (first 500 chars):")
                    raw_str = json.dumps(order, indent=2)
                    print("  " + "\n  ".join(raw_str[:500].split("\n")))
                    print()

                break

        except:
            continue

print()
print("=" * 80)
print("ANALYSIS:")
print("=" * 80)
print()
print("Question: Do the orders have 'order_hash' or 'hash' fields?")
print("Question: Are there multiple orders with different hashes but same size/side?")
print("Question: What fields uniquely identify each order?")