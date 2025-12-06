#!/usr/bin/env python3
"""
PUMP Pre-Dump Accumulation Analysis
Who bought 41B tokens on Nov 30 13:00-14:00?
"""

import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DEFAULT_LOG_DIR = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\allcoins_json_logs")


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


def analyze_window(snapshots: list[dict]) -> dict:
    """Analyze the accumulation window in detail."""

    results = {
        'by_minute': defaultdict(lambda: {
            'price': 0,
            'buy_volume': 0,
            'sell_volume': 0,
            'orders': [],
        }),
        'all_orders': [],
        'addresses': defaultdict(lambda: {
            'buy_volume': 0,
            'sell_volume': 0,
            'orders': [],
        }),
    }

    seen_orders = set()

    for s in snapshots:
        ts = s.get('timestamp', '')
        try:
            dt = datetime.fromisoformat(ts)
        except:
            continue

        # Only Nov 30, 12:00 - 15:00 window (expanded to catch everything)
        if dt.month != 11 or dt.day != 30:
            continue
        if dt.hour < 12 or dt.hour > 15:
            continue

        minute_key = dt.strftime("%H:%M")
        m = results['by_minute'][minute_key]

        price = s.get('current_price', 0)
        if price:
            m['price'] = price

        # Get summary volumes
        summary = s.get('summary', {})
        m['buy_volume'] += summary.get('buy_volume', 0)
        m['sell_volume'] += summary.get('sell_volume', 0)

        # Process all new orders
        for order in s.get('new_orders', []):
            order_hash = order.get('order_hash', '')
            if order_hash in seen_orders:
                continue
            seen_orders.add(order_hash)

            addr = order.get('address', '')
            side = order.get('side', '').upper()
            size = order.get('size', 0)
            duration = order.get('duration_minutes', 0)

            order_data = {
                'timestamp': ts,
                'address': addr,
                'side': side,
                'size': size,
                'price': price,
                'duration': duration,
                'order_hash': order_hash,
            }

            results['all_orders'].append(order_data)
            m['orders'].append(order_data)

            a = results['addresses'][addr]
            a['orders'].append(order_data)
            if side == 'BUY':
                a['buy_volume'] += size
            elif side == 'SELL':
                a['sell_volume'] += size

    return results


def print_report(results: dict) -> None:
    """Print detailed analysis."""

    print(f"\n{'=' * 120}")
    print(f" PUMP ACCUMULATION WINDOW - Nov 30 12:00-15:00 UTC")
    print(f"{'=' * 120}")

    # Minute by minute
    print(f"\n📊 MINUTE-BY-MINUTE BREAKDOWN (only minutes with activity)")
    print(f"\n{'Time':<8} {'Price':>12} {'Buy Vol':>18} {'Sell Vol':>18} {'Net Flow':>18} {'Orders':>8}")
    print("-" * 90)

    total_buy = 0
    total_sell = 0

    for minute_key in sorted(results['by_minute'].keys()):
        m = results['by_minute'][minute_key]
        if m['buy_volume'] == 0 and m['sell_volume'] == 0:
            continue

        net = m['buy_volume'] - m['sell_volume']
        total_buy += m['buy_volume']
        total_sell += m['sell_volume']

        net_str = f"+{net:,.0f}" if net >= 0 else f"{net:,.0f}"

        print(
            f"{minute_key:<8} ${m['price']:.6f} {m['buy_volume']:>18,.0f} {m['sell_volume']:>18,.0f} {net_str:>18} {len(m['orders']):>8}")

    print("-" * 90)
    total_net = total_buy - total_sell
    net_str = f"+{total_net:,.0f}" if total_net >= 0 else f"{total_net:,.0f}"
    print(f"{'TOTAL':<8} {'':>12} {total_buy:>18,.0f} {total_sell:>18,.0f} {net_str:>18}")

    # All orders in detail
    print(f"\n{'=' * 120}")
    print(f" 📋 ALL ORDERS IN WINDOW ({len(results['all_orders'])} orders)")
    print(f"{'=' * 120}")

    print(f"\n{'Timestamp':<26} {'Side':<6} {'Size':>20} {'Price':>12} {'Duration':>10} {'Address':<44}")
    print("-" * 130)

    # Sort by size descending
    sorted_orders = sorted(results['all_orders'], key=lambda x: x['size'], reverse=True)

    for o in sorted_orders:
        dur_str = f"{o['duration']}min" if o['duration'] else ""
        print(
            f"{o['timestamp']:<26} {o['side']:<6} {o['size']:>20,.0f} ${o['price']:.6f} {dur_str:>10} {o['address']:<44}")

    # Address summary
    print(f"\n{'=' * 120}")
    print(f" 🐋 ADDRESSES BY NET FLOW")
    print(f"{'=' * 120}")

    addr_list = []
    for addr, data in results['addresses'].items():
        net = data['buy_volume'] - data['sell_volume']
        addr_list.append({
            'address': addr,
            'buy': data['buy_volume'],
            'sell': data['sell_volume'],
            'net': net,
            'orders': len(data['orders']),
        })

    addr_list.sort(key=lambda x: x['net'], reverse=True)

    print(f"\n{'Address':<44} {'Buy Vol':>20} {'Sell Vol':>20} {'Net Flow':>20} {'Orders':>8}")
    print("-" * 120)

    for a in addr_list:
        net_str = f"+{a['net']:,.0f}" if a['net'] >= 0 else f"{a['net']:,.0f}"
        print(f"{a['address']:<44} {a['buy']:>20,.0f} {a['sell']:>20,.0f} {net_str:>20} {a['orders']:>8}")

    # Check if addresses appear elsewhere
    print(f"\n{'=' * 120}")
    print(f" 🔍 TOP ACCUMULATOR DETAILS")
    print(f"{'=' * 120}")

    if addr_list:
        top = addr_list[0]
        print(f"\nTop accumulator: {top['address']}")
        print(f"Net bought: {top['net']:,.0f} PUMP")
        print(f"\nTheir orders:")

        for o in results['addresses'][top['address']]['orders']:
            dur_str = f"{o['duration']}min" if o['duration'] else ""
            print(f"   {o['timestamp']} - {o['side']} {o['size']:,.0f} @ ${o['price']:.6f} ({dur_str})")

    print(f"\n{'=' * 120}\n")


def main():
    coin = "PUMP"
    filepath = DEFAULT_LOG_DIR / coin / "PUMP_20251130.jsonl"

    if not filepath.exists():
        print(f"File not found: {filepath}")
        return

    print(f"Loading: {filepath}")
    snapshots = load_jsonl(filepath)
    print(f"Loaded {len(snapshots):,} snapshots")

    results = analyze_window(snapshots)
    print_report(results)


if __name__ == "__main__":
    main()