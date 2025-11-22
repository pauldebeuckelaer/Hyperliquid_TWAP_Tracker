#!/usr/bin/env python3
"""
Diagnostic: Investigate canceled -> active transitions
"""
import json
from pathlib import Path
from glob import glob

# Target the problematic order
TARGET_ADDRESS = "0x28f0233472b6a44e170e002a72845ca100be4a7e"

print("Investigating canceled -> active transitions")
print("=" * 80)
print(f"Target address: {TARGET_ADDRESS}")
print()

# Find latest log
log_files = sorted(glob('json_logs/HYPE_*.jsonl'), reverse=True)
log_file = log_files[0]

print(f"Analyzing: {log_file}")
print()

# Track this order's lifecycle
order_states = []

with open(log_file, 'r') as f:
    for line in f:
        try:
            data = json.loads(line)
            update_num = data.get('update_number')

            # Find orders for this address
            for order in data.get('active_orders', []):
                addr = order.get('address') or order.get('full_address')

                if addr and addr.lower() == TARGET_ADDRESS.lower():
                    # Extract key fields
                    order_hash = order.get('order_hash') or order.get('hash')
                    status = order.get('status')
                    size = order.get('size')
                    side = order.get('side')
                    ended = order.get('ended')
                    error = order.get('error')

                    order_states.append({
                        'update': update_num,
                        'order_hash': order_hash,
                        'status': status,
                        'size': size,
                        'side': side,
                        'ended': ended,
                        'error': error,
                        'raw': order
                    })

        except:
            continue

print(f"Found {len(order_states)} appearances of this address")
print()

# Look for status flips
print("STATUS TRANSITIONS:")
print("-" * 80)

prev_state = None
for state in order_states:
    if prev_state:
        if prev_state['status'] != state['status']:
            print(f"Update {state['update']}: {prev_state['status']} → {state['status']}")
            print(f"  Order hash: {state['order_hash']}")
            print(f"  Size: {state['size']} {state['side']}")
            print(f"  ended field: {state['ended']}")
            print(f"  error field: {state['error']}")
            print()

    prev_state = state

# Check for multiple order hashes
unique_hashes = set(s['order_hash'] for s in order_states if s['order_hash'])
print("=" * 80)
print(f"UNIQUE ORDER HASHES: {len(unique_hashes)}")
print()

if len(unique_hashes) > 1:
    print("⚠️  MULTIPLE ORDER HASHES DETECTED!")
    print("This address has multiple different orders, not one order flipping status.")
    print()

    # Group by hash
    by_hash = {}
    for state in order_states:
        h = state['order_hash']
        if h not in by_hash:
            by_hash[h] = []
        by_hash[h].append(state)

    for hash_val, states in by_hash.items():
        print(f"Hash: {hash_val}")
        print(f"  Appears in updates: {states[0]['update']} to {states[-1]['update']}")
        print(f"  Status sequence: {' → '.join(set(s['status'] for s in states))}")
        print()
else:
    print("✓ Single order hash - this is truly one order flipping status")
    print(f"  Hash: {list(unique_hashes)[0]}")

print()
print("=" * 80)
print("DIAGNOSIS:")
print("=" * 80)

if len(unique_hashes) > 1:
    print("❌ PROBLEM: Order hash collision or multiple orders from same address")
    print()
    print("The issue is that you're using order_hash as the unique key,")
    print("but this address is placing multiple orders with different hashes.")
    print()
    print("SOLUTION: Your order key should include more than just hash.")
    print("Try: f'{address}_{side}_{size}_{order_hash}'")
else:
    print("❌ PROBLEM: Single order actually flipping from canceled to active")
    print()
    print("This shouldn't be possible. Let's check the raw API data...")
    print()
    print("Sample raw order data:")
    print(json.dumps(order_states[0]['raw'], indent=2))