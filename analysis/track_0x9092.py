#!/usr/bin/env python3
"""
Track 0x9092 - The 400M PUMP buyer with interesting timing
"""

import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DEFAULT_LOG_DIR = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\allcoins_json_logs")
TARGET_ADDRESS = "0x90924bd2a82c481170e98051196a5bde02d82b15"


def load_jsonl(filepath: Path) -> list[dict]:
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
    address_lower = address.lower()

    results = {
        'address': address,
        'coins': defaultdict(lambda: {
            'buy_volume': 0,
            'sell_volume': 0,
            'orders': 0,
            'activity_log': [],
        }),
    }

    coin_dirs = sorted([d for d in DEFAULT_LOG_DIR.iterdir() if d.is_dir()])
    print(f"Scanning {len(coin_dirs)} coins for address {address[:10]}...{address[-6:]}\n")

    for coin_dir in coin_dirs:
        coin = coin_dir.name
        files = sorted(coin_dir.glob("*.jsonl"))
        seen_orders = set()

        for filepath in files:
            snapshots = load_jsonl(filepath)

            for s in snapshots:
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
                    price = s.get('current_price', 0)
                    duration = order.get('duration_minutes', 0)

                    c = results['coins'][coin]
                    c['orders'] += 1

                    if side == 'BUY':
                        c['buy_volume'] += size
                    elif side == 'SELL':
                        c['sell_volume'] += size

                    c['activity_log'].append({
                        'timestamp': s.get('timestamp'),
                        'side': side,
                        'size': size,
                        'price': price,
                        'duration': duration,
                    })

        if results['coins'][coin]['orders'] > 0:
            c = results['coins'][coin]
            net = c['buy_volume'] - c['sell_volume']
            print(f"  ✓ {coin}: {c['orders']} orders, net flow {net:+,.0f}")

    return results


def print_report(results: dict) -> None:
    print(f"\n{'=' * 120}")
    print(f" ADDRESS TRACKER: {results['address']}")
    print(f"{'=' * 120}")

    if not results['coins']:
        print("\n   No activity found.")
        return

    # Summary by coin
    print(f"\n{'Coin':<16} {'Buy Volume':>18} {'Sell Volume':>18} {'Net Flow':>18} {'Orders':>8}")
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
        })
        total_buy += data['buy_volume']
        total_sell += data['sell_volume']

    coin_list.sort(key=lambda x: abs(x['net']), reverse=True)

    for c in coin_list:
        net_str = f"+{c['net']:,.0f}" if c['net'] >= 0 else f"{c['net']:,.0f}"
        print(f"{c['coin']:<16} {c['buy']:>18,.0f} {c['sell']:>18,.0f} {net_str:>18} {c['orders']:>8}")

    print("-" * 80)
    total_net = total_buy - total_sell
    net_str = f"+{total_net:,.0f}" if total_net >= 0 else f"{total_net:,.0f}"
    print(f"{'TOTAL':<16} {total_buy:>18,.0f} {total_sell:>18,.0f} {net_str:>18}")

    # Full timeline
    print(f"\n{'=' * 120}")
    print(f" 📅 COMPLETE ACTIVITY TIMELINE")
    print(f"{'=' * 120}")

    all_activity = []
    for c in coin_list:
        for a in c['activity_log']:
            all_activity.append({'coin': c['coin'], **a})

    all_activity.sort(key=lambda x: x['timestamp'])

    print(f"\n{'Timestamp':<26} {'Coin':<10} {'Side':<6} {'Size':>18} {'Price':>14} {'Duration':>10}")
    print("-" * 100)

    for a in all_activity:
        dur_str = f"{a['duration']}min" if a['duration'] else ""
        price_str = f"${a['price']:.4f}" if a['price'] else "N/A"
        print(f"{a['timestamp']:<26} {a['coin']:<10} {a['side']:<6} {a['size']:>18,.0f} {price_str:>14} {dur_str:>10}")

    # Net positions
    print(f"\n{'=' * 120}")
    print(f" 💰 NET POSITIONS")
    print(f"{'=' * 120}")

    longs = [c for c in coin_list if c['net'] > 0]
    shorts = [c for c in coin_list if c['net'] < 0]

    if longs:
        print(f"\n🟢 NET LONG:")
        for c in sorted(longs, key=lambda x: x['net'], reverse=True):
            print(f"   {c['coin']}: +{c['net']:,.0f}")

    if shorts:
        print(f"\n🔴 NET SHORT:")
        for c in sorted(shorts, key=lambda x: x['net']):
            print(f"   {c['coin']}: {c['net']:,.0f}")

    print(f"\n{'=' * 120}\n")


def main():
    results = scan_all_coins(TARGET_ADDRESS)
    print_report(results)


if __name__ == "__main__":
    main()