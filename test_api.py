#!/usr/bin/env python3
"""
Quick test to see what the API returns for TWAP orders
"""
import requests
import json

print("Fetching TWAP data from API...")
print("=" * 60)

response = requests.get("https://api.hypurrscan.io/twap/HYPE")
data = response.json()

print(f"Total orders returned: {len(data)}")
print("=" * 60)

# Show all orders
for i, order in enumerate(data, 1):
    user = order.get('user', 'unknown')
    action = order.get('action', {})
    twap = action.get('twap', {})

    size = twap.get('s', 0)
    duration = twap.get('m', 0)
    buy = twap.get('b', True)
    ended = order.get('ended', None)

    side = 'BUY' if buy else 'SELL'
    status = ended if ended else 'ACTIVE'

    print(f"Order {i}:")
    print(f"  Address: {user}")
    print(f"  Side: {side}")
    print(f"  Size: {size}")
    print(f"  Duration: {duration} minutes")
    print(f"  Status: {status}")
    print()

print("=" * 60)

# Look specifically for your address
target_address = "0x425069cb2e47d3cc8d50a1d3139db25a226ecdad"
print(f"\nLooking for orders from {target_address}:")
print("-" * 60)

matching_orders = [o for o in data if o.get('user', '').lower() == target_address.lower()]
print(f"Found {len(matching_orders)} orders for this address:")

for i, order in enumerate(matching_orders, 1):
    action = order.get('action', {})
    twap = action.get('twap', {})
    size = twap.get('s', 0)
    ended = order.get('ended', None)
    status = ended if ended else 'ACTIVE'

    print(f"  {i}. Size: {size}, Status: {status}")

print("=" * 60)