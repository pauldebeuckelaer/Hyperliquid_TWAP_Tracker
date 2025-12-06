#!/usr/bin/env python3
"""
Address Tracker - Track a specific address across ALL coins
Usage: python track_address.py [address]

Default: 0x45ab58a2034f03aa446baf3bb1d236706f866cbc (the swing trader)
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DEFAULT_LOG_DIR = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\allcoins_json_logs")

# Default address to track - the swing trader
DEFAULT_ADDRESS = "0x45ab58a2034f03aa446baf3bb1d236706f866cbc"


def load_jsonl(filepath: Path) -> list[dict]:
    """Load all JSON lines from file."""
    snapshots = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    snapshots.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return snapshots


def scan_all_coins(address: str) -> dict:
    """Scan all coins for activity from this address."""

    address_lower = address.lower()

    results = {
        'address': address,
        'coins': defaultdict(lambda: {
            'buy_volume': 0,
            'sell_volume': 0,
            'orders': 0,
            'files_with_activity': 0,
            'first_seen': None,
            'last_seen': None,
            'activity_log': [],
        }),
        'total_files_scanned': 0,
        'files_with_activity': 0,
    }

    # Get all coin directories
    coin_dirs = sorted([d for d in DEFAULT_LOG_DIR.iterdir() if d.is_dir()])

    print(f"Scanning {len(coin_dirs)} coins for address {address[:10]}...{address[-6:]}\n")

    for coin_dir in coin_dirs:
        coin = coin_dir.name
        files = sorted(coin_dir.glob("*.jsonl"))

        coin_has_activity = False

        for filepath in files:
            results['total_files_scanned'] += 1
            file_has_activity = False
            seen_orders = set()

            snapshots = load_jsonl(filepath)

            for s in snapshots:
                timestamp = s.get('timestamp', '')
                price = s.get('current_price', 0)

                # Check new orders
                for order in s.get('new_orders', []):
                    addr = order.get('address', '')
                    if addr.lower() != address_lower:
                        continue

                    order_hash = order.get('order_hash', '')
                    if order_hash in seen_orders:
                        continue
                    seen_orders.add(order_hash)

                    side = order.get('side', '').upper()
                    size = order.get('size', 0)
                    duration = order.get('duration_minutes', 0)

                    c = results['coins'][coin]
                    c['orders'] += 1

                    if side == 'BUY':
                        c['buy_volume'] += size
                    elif side == 'SELL':
                        c['sell_volume'] += size

                    # Track timing
                    if c['first_seen'] is None or timestamp < c['first_seen']:
                        c['first_seen'] = timestamp
                    if c['last_seen'] is None or timestamp > c['last_seen']:
                        c['last_seen'] = timestamp

                    # Log the activity
                    c['activity_log'].append({
                        'timestamp': timestamp,
                        'side': side,
                        'size': size,
                        'price': price,
                        'duration': duration,
                    })

                    file_has_activity = True
                    coin_has_activity = True

            if file_has_activity:
                results['coins'][coin]['files_with_activity'] += 1

        if coin_has_activity:
            results['files_with_activity'] += 1
            # Progress indicator for coins with activity
            c = results['coins'][coin]
            net = c['buy_volume'] - c['sell_volume']
            print(f"  ✓ {coin}: {c['orders']} orders, net flow {net:+,.0f}")

    return results


def print_report(results: dict) -> None:
    """Print comprehensive report."""

    print(f"\n{'=' * 120}")
    print(f" ADDRESS TRACKER: {results['address'][:20]}...{results['address'][-10:]}")
    print(f"{'=' * 120}")

    print(f"\n📊 SCAN SUMMARY")
    print(f"   Files scanned: {results['total_files_scanned']}")
    print(f"   Coins with activity: {len(results['coins'])}")

    if not results['coins']:
        print("\n   No activity found for this address.")
        return

    # Summary by coin
    print(f"\n{'=' * 120}")
    print(f" 📊 SUMMARY BY COIN")
    print(f"{'=' * 120}")
    print(f"\n{'Coin':<16} {'Buy Volume':>16} {'Sell Volume':>16} {'Net Flow':>16} {'Orders':>8}")
    print("-" * 80)

    coin_list = []
    total_buy = 0
    total_sell = 0

    for coin, data in results['coins'].items():
        net = data['buy_volume'] - data['sell_volume']
        coin_list.append({
            'coin': coin,
            'buy': data['buy_volume'],
            'sell': data['sell_volume'],
            'net': net,
            'orders': data['orders'],
            'activity_log': data['activity_log'],
            'first_seen': data['first_seen'],
            'last_seen': data['last_seen'],
        })
        total_buy += data['buy_volume']
        total_sell += data['sell_volume']

    # Sort by absolute net flow
    coin_list.sort(key=lambda x: abs(x['net']), reverse=True)

    for c in coin_list:
        net_str = f"+{c['net']:,.0f}" if c['net'] >= 0 else f"{c['net']:,.0f}"
        print(f"{c['coin']:<16} {c['buy']:>16,.0f} {c['sell']:>16,.0f} {net_str:>16} {c['orders']:>8}")

    print("-" * 80)
    total_net = total_buy - total_sell
    net_str = f"+{total_net:,.0f}" if total_net >= 0 else f"{total_net:,.0f}"
    print(f"{'TOTAL':<16} {total_buy:>16,.0f} {total_sell:>16,.0f} {net_str:>16}")

    # Timeline of all activity
    print(f"\n{'=' * 120}")
    print(f" 📅 ACTIVITY TIMELINE")
    print(f"{'=' * 120}")

    all_activity = []
    for c in coin_list:
        for a in c['activity_log']:
            all_activity.append({
                'coin': c['coin'],
                **a
            })

    all_activity.sort(key=lambda x: x['timestamp'])

    print(f"\n{'Timestamp':<26} {'Coin':<10} {'Side':<6} {'Size':>16} {'Price':>12} {'Duration':>8}")
    print("-" * 120)

    for a in all_activity:
        size_str = f"{a['size']:,.0f}"
        price_str = f"${a['price']:.4f}" if a['price'] else "N/A"
        dur_str = f"{a['duration']}min" if a['duration'] else ""
        print(f"{a['timestamp']:<26} {a['coin']:<10} {a['side']:<6} {size_str:>16} {price_str:>12} {dur_str:>8}")

    # Net position summary
    print(f"\n{'=' * 120}")
    print(f" 💰 NET POSITIONS")
    print(f"{'=' * 120}")

    buyers = [c for c in coin_list if c['net'] > 0]
    sellers = [c for c in coin_list if c['net'] < 0]

    if buyers:
        print(f"\n🟢 NET LONG:")
        for c in sorted(buyers, key=lambda x: x['net'], reverse=True):
            print(f"   {c['coin']}: +{c['net']:,.0f}")

    if sellers:
        print(f"\n🔴 NET SHORT:")
        for c in sorted(sellers, key=lambda x: x['net']):
            print(f"   {c['coin']}: {c['net']:,.0f}")

    print(f"\n{'=' * 120}\n")


def main():
    address = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ADDRESS

    results = scan_all_coins(address)
    print_report(results)


if __name__ == "__main__":
    main()