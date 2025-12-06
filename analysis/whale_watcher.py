import json
import glob
import os

# --- CONFIGURATION ---
FILE_PATTERN = 'allcoins_json_logs/BTC/*.jsonl'
WHALE_THRESHOLD = 100000  # Only show orders worth more than this (Price * Size) OR Size > X


def scan_for_whales(file_pattern, threshold_value=50000):
    files = glob.glob(file_pattern)
    whales_found = []
    seen_order_hashes = set()  # Set to track unique order hashes

    print(f"Scanning for orders > ${threshold_value} value...")

    for file in files:
        with open(file, 'r') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    price = data.get('current_price')

                    # Skip if price is null (can't calculate USD value)
                    if not price:
                        continue

                    # Check Active Orders
                    if 'active_orders' in data and data['active_orders']:
                        for order in data['active_orders']:
                            order_hash = order.get('order_hash')

                            # Skip if we have already seen this specific order hash
                            if order_hash in seen_order_hashes:
                                continue

                            size = order.get('size', 0)

                            # Calculate approximate USD value of the order
                            usd_value = size * price

                            if usd_value > threshold_value:
                                whale_entry = {
                                    'time': data['timestamp'],
                                    'side': order.get('side'),
                                    'size': size,
                                    'usd_value': round(usd_value, 2),
                                    'address': order.get('address'),
                                    'price_at_time': price,
                                    'order_hash': order_hash
                                }
                                whales_found.append(whale_entry)
                                seen_order_hashes.add(order_hash)  # Mark as seen

                                # Print immediately when found (Real-time feel)
                                print(f"[WHALE ALERT] {data['timestamp']} | "
                                      f"{order.get('side')} ${round(usd_value, 2)} "
                                      f"({size} coins) | Addr: {order.get('address')[:8]}...")

                except json.JSONDecodeError:
                    continue

    return whales_found


if __name__ == "__main__":
    # You can adjust the threshold based on the coin price.
    # Since BTC is ~85k in your logs, a size of 1.0 is $85k.
    whales = scan_for_whales(FILE_PATTERN, threshold_value=100000)

    print(f"\nTotal Unique Whale Orders Found: {len(whales)}")