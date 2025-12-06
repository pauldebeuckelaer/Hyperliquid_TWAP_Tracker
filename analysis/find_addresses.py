#!/usr/bin/env python3
"""
TWAP Address Tracker - Hunt specific addresses across all data
Usage: python twap_tracker.py <address> [--coin COIN]

Examples:
    python twap_tracker.py 0xa23190           # Search all coins for address
    python twap_tracker.py 0xa23190 --coin HYPE  # Search only HYPE
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DEFAULT_LOG_DIR = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\allcoins_json_logs")

# The whales we're hunting
WHALE_WATCHLIST = [
    "0xa23190",  # Top seller - 470K HYPE
    "0x45ab58",  # Sold 200K, bought 172K back - smart money
    "0x25d747",  # 50K seller
    "0x4880e1",  # 35K seller
    "0xaf0fdd",  # 25K seller
]


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


def find_address_activity(snapshots: list[dict], address_prefix: str) -> list[dict]:
    """Find all activity for an address (partial match)."""
    activity = []
    seen_orders = set()

    for s in snapshots:
        ts = s.get('timestamp', '')
        price = s.get('current_price', 0)
        symbol = s.get('symbol', '')

        # Check new orders
        for order in s.get('new_orders', []):
            addr = order.get('address', '')
            order_hash = order.get('order_hash', '')

            if address_prefix.lower() in addr.lower() and order_hash not in seen_orders:
                seen_orders.add(order_hash)
                activity.append({
                    'timestamp': ts,
                    'symbol': symbol,
                    'price': price,
                    'event': 'NEW',
                    'side': order.get('side'),
                    'size': order.get('size', 0),
                    'duration_minutes': order.get('duration_minutes', 0),
                    'address': addr,
                    'order_hash': order_hash,
                })

        # Check completed orders
        for order in s.get('completed_orders', []):
            addr = order.get('address', '')
            order_hash = order.get('order_hash', '')

            if address_prefix.lower() in addr.lower():
                activity.append({
                    'timestamp': ts,
                    'symbol': symbol,
                    'price': price,
                    'event': 'COMPLETED',
                    'side': order.get('side'),
                    'size': order.get('size', 0),
                    'duration_minutes': order.get('duration_minutes', 0),
                    'address': addr,
                    'order_hash': order_hash,
                })

        # Check canceled orders
        for order in s.get('canceled_orders', []):
            addr = order.get('address', '')
            order_hash = order.get('order_hash', '')

            if address_prefix.lower() in addr.lower():
                activity.append({
                    'timestamp': ts,
                    'symbol': symbol,
                    'price': price,
                    'event': 'CANCELED',
                    'side': order.get('side'),
                    'size': order.get('size', 0),
                    'duration_minutes': order.get('duration_minutes', 0),
                    'address': addr,
                    'order_hash': order_hash,
                })

    return activity


def scan_all_files(address_prefix: str, coin_filter: str = None) -> list[dict]:
    """Scan all JSONL files for address activity."""
    all_activity = []

    # Get all coin directories
    coin_dirs = sorted(DEFAULT_LOG_DIR.iterdir())

    for coin_dir in coin_dirs:
        if not coin_dir.is_dir():
            continue

        coin = coin_dir.name

        # Apply coin filter if specified
        if coin_filter and coin.upper() != coin_filter.upper():
            continue

        # Process all files for this coin
        for filepath in sorted(coin_dir.glob("*.jsonl")):
            snapshots = load_jsonl(filepath)
            activity = find_address_activity(snapshots, address_prefix)

            if activity:
                print(f"  Found {len(activity)} events in {filepath.name}")
                all_activity.extend(activity)

    return all_activity


def print_activity_report(activity: list[dict], address_prefix: str) -> None:
    """Print detailed activity report."""
    if not activity:
        print(f"\nNo activity found for address: {address_prefix}")
        return

    # Sort by timestamp
    activity = sorted(activity, key=lambda x: x['timestamp'])

    # Get full address from first activity
    full_address = activity[0]['address']

    print(f"\n{'=' * 120}")
    print(f" WHALE TRACKER: {full_address[:20]}...{full_address[-10:]}")
    print(f"{'=' * 120}")

    # Summary by coin
    coins = defaultdict(lambda: {'buy_vol': 0, 'sell_vol': 0, 'orders': 0})
    for a in activity:
        if a['event'] == 'NEW':
            coins[a['symbol']]['orders'] += 1
            if a['side'] == 'BUY':
                coins[a['symbol']]['buy_vol'] += a['size']
            else:
                coins[a['symbol']]['sell_vol'] += a['size']

    print(f"\n📊 SUMMARY BY COIN")
    print(f"{'Coin':<12} {'Buy Volume':>14} {'Sell Volume':>14} {'Net Flow':>14} {'Orders':>8}")
    print("-" * 70)

    total_buy = total_sell = 0
    for coin in sorted(coins.keys()):
        c = coins[coin]
        net = c['buy_vol'] - c['sell_vol']
        print(f"{coin:<12} {c['buy_vol']:>14,.0f} {c['sell_vol']:>14,.0f} {net:>+14,.0f} {c['orders']:>8}")
        total_buy += c['buy_vol']
        total_sell += c['sell_vol']

    print("-" * 70)
    print(f"{'TOTAL':<12} {total_buy:>14,.0f} {total_sell:>14,.0f} {total_buy - total_sell:>+14,.0f}")

    # Detailed timeline
    print(f"\n{'=' * 120}")
    print(f" 📅 ACTIVITY TIMELINE")
    print(f"{'=' * 120}")
    print(f"\n{'Timestamp':<26} {'Coin':<10} {'Event':<10} {'Side':<6} {'Size':>14} {'Price':>12} {'Duration':>10}")
    print("-" * 120)

    for a in activity:
        ts_short = a['timestamp'][:19]
        duration = f"{a['duration_minutes']}min" if a['duration_minutes'] else "-"
        print(
            f"{ts_short:<26} {a['symbol']:<10} {a['event']:<10} {a['side']:<6} {a['size']:>14,.0f} ${a['price']:>11,.2f} {duration:>10}")

    print(f"\n{'=' * 120}\n")


def track_watchlist(coin_filter: str = None) -> None:
    """Track all addresses in watchlist."""
    print(f"\n{'#' * 120}")
    print(f" WHALE WATCHLIST SCAN")
    print(f"{'#' * 120}")

    for addr in WHALE_WATCHLIST:
        print(f"\n🔍 Scanning for {addr}...")
        activity = scan_all_files(addr, coin_filter)
        print_activity_report(activity, addr)


def main():
    # Parse arguments
    coin_filter = None
    address = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--coin' and i + 1 < len(args):
            coin_filter = args[i + 1]
            i += 2
        elif args[i] == '--watchlist':
            track_watchlist(coin_filter)
            return
        elif not args[i].startswith('--'):
            address = args[i]
            i += 1
        else:
            i += 1

    if not address:
        print(__doc__)
        print("\nOr use --watchlist to scan all known whales:")
        print("    python twap_tracker.py --watchlist")
        print("    python twap_tracker.py --watchlist --coin HYPE")
        print(f"\nCurrent watchlist: {WHALE_WATCHLIST}")
        sys.exit(0)

    print(f"🔍 Scanning for address: {address}")
    if coin_filter:
        print(f"   Filtering to coin: {coin_filter}")

    activity = scan_all_files(address, coin_filter)
    print_activity_report(activity, address)


if __name__ == "__main__":
    main()