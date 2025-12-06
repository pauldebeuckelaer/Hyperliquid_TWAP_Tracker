#!/usr/bin/env python3
"""
Address Analyzer - Who's doing all the buying/selling?
Usage: python address_analyzer.py <COIN> [date]

Shows volume breakdown by address to identify whale concentration.
"""

import json
import sys
from pathlib import Path
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


def analyze_addresses(coin: str, date_filter: str = None):
    """Analyze address concentration for a coin."""

    coin_dir = DEFAULT_LOG_DIR / coin
    if not coin_dir.exists():
        print(f"No directory found for {coin}")
        return

    # Find files
    if date_filter:
        files = list(coin_dir.glob(f"*{date_filter}*.jsonl"))
    else:
        files = list(coin_dir.glob("*.jsonl"))

    if not files:
        print(f"No files found for {coin}" + (f" on {date_filter}" if date_filter else ""))
        return

    print(f"Loading {len(files)} file(s) for {coin}...")

    # Track unique orders by order_hash to avoid double counting
    seen_orders = {}  # order_hash -> order_data

    for f in sorted(files):
        snapshots = load_jsonl(f)
        print(f"  {f.name}: {len(snapshots)} snapshots")

        for snap in snapshots:
            # Check active_orders
            for order in snap.get('active_orders', []):
                order_hash = order.get('order_hash')
                if order_hash and order_hash not in seen_orders:
                    seen_orders[order_hash] = {
                        'address': order.get('address', 'unknown'),
                        'side': order.get('side', 'unknown'),
                        'size': order.get('size', 0),
                        'product_type': order.get('product_type', 'unknown')
                    }

            # Check new_orders
            for order in snap.get('new_orders', []):
                order_hash = order.get('order_hash')
                if order_hash and order_hash not in seen_orders:
                    seen_orders[order_hash] = {
                        'address': order.get('address', 'unknown'),
                        'side': order.get('side', 'unknown'),
                        'size': order.get('size', 0),
                        'product_type': order.get('product_type', 'unknown')
                    }

    print(f"\nTotal unique orders: {len(seen_orders)}")

    # Aggregate by address
    address_stats = defaultdict(lambda: {'buy_volume': 0, 'sell_volume': 0, 'buy_orders': 0, 'sell_orders': 0})

    for order_hash, order in seen_orders.items():
        addr = order['address'] # Shortened for display
        full_addr = order['address']

        if order['side'] == 'BUY':
            address_stats[full_addr]['buy_volume'] += order['size']
            address_stats[full_addr]['buy_orders'] += 1
        elif order['side'] == 'SELL':
            address_stats[full_addr]['sell_volume'] += order['size']
            address_stats[full_addr]['sell_orders'] += 1

        address_stats[full_addr]['short'] = addr

    # Sort by total volume
    sorted_addresses = sorted(
        address_stats.items(),
        key=lambda x: x[1]['buy_volume'] + x[1]['sell_volume'],
        reverse=True
    )

    # Calculate totals
    total_buy = sum(v['buy_volume'] for v in address_stats.values())
    total_sell = sum(v['sell_volume'] for v in address_stats.values())

    # Print report
    print(f"\n{'=' * 100}")
    print(f" {coin} ADDRESS BREAKDOWN")
    print(f"{'=' * 100}")

    print(f"\n📊 TOTALS")
    print(f"   Unique addresses: {len(address_stats)}")
    print(f"   Total BUY volume: {total_buy:,.0f}")
    print(f"   Total SELL volume: {total_sell:,.0f}")
    print(f"   Net: {total_buy - total_sell:+,.0f}")

    print(f"\n{'=' * 100}")
    print(f" TOP SELLERS")
    print(f"{'=' * 100}")
    print(f"{'Address':<24} {'Sell Vol':>15} {'% of Total':>12} {'Orders':>8}")
    print("-" * 60)

    sellers = [(addr, stats) for addr, stats in sorted_addresses if stats['sell_volume'] > 0]
    sellers.sort(key=lambda x: x[1]['sell_volume'], reverse=True)

    for addr, stats in sellers[:10]:
        pct = (stats['sell_volume'] / total_sell * 100) if total_sell > 0 else 0
        print(f"{stats['short']:<24} {stats['sell_volume']:>15,.0f} {pct:>11.1f}% {stats['sell_orders']:>8}")

    print(f"\n{'=' * 100}")
    print(f" TOP BUYERS")
    print(f"{'=' * 100}")
    print(f"{'Address':<24} {'Buy Vol':>15} {'% of Total':>12} {'Orders':>8}")
    print("-" * 60)

    buyers = [(addr, stats) for addr, stats in sorted_addresses if stats['buy_volume'] > 0]
    buyers.sort(key=lambda x: x[1]['buy_volume'], reverse=True)

    for addr, stats in buyers[:10]:
        pct = (stats['buy_volume'] / total_buy * 100) if total_buy > 0 else 0
        print(f"{stats['short']:<24} {stats['buy_volume']:>15,.0f} {pct:>11.1f}% {stats['buy_orders']:>8}")

    # Concentration analysis
    print(f"\n{'=' * 100}")
    print(f" 🎯 CONCENTRATION ANALYSIS")
    print(f"{'=' * 100}")

    if sellers:
        top_seller_pct = (sellers[0][1]['sell_volume'] / total_sell * 100) if total_sell > 0 else 0
        top3_sell = sum(s[1]['sell_volume'] for s in sellers[:3])
        top3_sell_pct = (top3_sell / total_sell * 100) if total_sell > 0 else 0
        print(f"\n   SELL side:")
        print(f"   Top 1 address: {top_seller_pct:.1f}% of all selling")
        print(f"   Top 3 addresses: {top3_sell_pct:.1f}% of all selling")
        if top_seller_pct > 50:
            print(f"   ⚠️  WHALE ALERT: Single address dominates selling!")
        elif top3_sell_pct > 80:
            print(f"   ⚠️  CONCENTRATED: Top 3 control most selling")
        else:
            print(f"   ✓ Distributed selling across multiple addresses")

    if buyers:
        top_buyer_pct = (buyers[0][1]['buy_volume'] / total_buy * 100) if total_buy > 0 else 0
        top3_buy = sum(b[1]['buy_volume'] for b in buyers[:3])
        top3_buy_pct = (top3_buy / total_buy * 100) if total_buy > 0 else 0
        print(f"\n   BUY side:")
        print(f"   Top 1 address: {top_buyer_pct:.1f}% of all buying")
        print(f"   Top 3 addresses: {top3_buy_pct:.1f}% of all buying")
        if top_buyer_pct > 50:
            print(f"   ⚠️  WHALE ALERT: Single address dominates buying!")
        elif top3_buy_pct > 80:
            print(f"   ⚠️  CONCENTRATED: Top 3 control most buying")
        else:
            print(f"   ✓ Distributed buying across multiple addresses")

    print(f"\n{'=' * 100}\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: python address_analyzer.py <COIN> [date]")
        print("Example: python address_analyzer.py ZEREBRO")
        print("Example: python address_analyzer.py ZEREBRO 20251204")
        sys.exit(1)

    coin = sys.argv[1].upper()
    date_filter = sys.argv[2] if len(sys.argv) > 2 else None

    analyze_addresses(coin, date_filter)


if __name__ == "__main__":
    main()