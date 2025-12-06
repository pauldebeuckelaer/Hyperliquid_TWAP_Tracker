#!/usr/bin/env python3
"""
Address Analyzer v2 - Net positions and two-way trader detection
Usage: python address_analyzer_v2.py <COIN> [date]

Shows net position per address and flags suspicious two-way activity.
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
                        'product_type': order.get('product_type', 'unknown'),
                        'timestamp': snap.get('timestamp', '')
                    }

            # Check new_orders
            for order in snap.get('new_orders', []):
                order_hash = order.get('order_hash')
                if order_hash and order_hash not in seen_orders:
                    seen_orders[order_hash] = {
                        'address': order.get('address', 'unknown'),
                        'side': order.get('side', 'unknown'),
                        'size': order.get('size', 0),
                        'product_type': order.get('product_type', 'unknown'),
                        'timestamp': snap.get('timestamp', '')
                    }

    print(f"\nTotal unique orders: {len(seen_orders)}")

    # Aggregate by address
    address_stats = defaultdict(lambda: {
        'buy_volume': 0,
        'sell_volume': 0,
        'buy_orders': 0,
        'sell_orders': 0,
        'first_order': None,
        'last_order': None
    })

    for order_hash, order in seen_orders.items():
        addr = order['address']
        ts = order['timestamp']

        if order['side'] == 'BUY':
            address_stats[addr]['buy_volume'] += order['size']
            address_stats[addr]['buy_orders'] += 1
        elif order['side'] == 'SELL':
            address_stats[addr]['sell_volume'] += order['size']
            address_stats[addr]['sell_orders'] += 1

        # Track timing
        if address_stats[addr]['first_order'] is None or ts < address_stats[addr]['first_order']:
            address_stats[addr]['first_order'] = ts
        if address_stats[addr]['last_order'] is None or ts > address_stats[addr]['last_order']:
            address_stats[addr]['last_order'] = ts

    # Calculate net positions
    for addr, stats in address_stats.items():
        stats['net_position'] = stats['buy_volume'] - stats['sell_volume']
        stats['total_volume'] = stats['buy_volume'] + stats['sell_volume']
        stats['is_two_way'] = stats['buy_volume'] > 0 and stats['sell_volume'] > 0

        # Churn ratio: how much of their volume is "cancelled out"
        if stats['total_volume'] > 0:
            min_side = min(stats['buy_volume'], stats['sell_volume'])
            stats['churn_ratio'] = (min_side * 2) / stats['total_volume']  # 0 = one-way, 1 = perfectly balanced
        else:
            stats['churn_ratio'] = 0

    # Calculate totals
    total_buy = sum(v['buy_volume'] for v in address_stats.values())
    total_sell = sum(v['sell_volume'] for v in address_stats.values())

    # Sort by net position (most bullish to most bearish)
    sorted_by_net = sorted(
        address_stats.items(),
        key=lambda x: x[1]['net_position'],
        reverse=True
    )

    # Identify two-way traders
    two_way_traders = [(addr, stats) for addr, stats in address_stats.items() if stats['is_two_way']]
    two_way_volume = sum(s['total_volume'] for _, s in two_way_traders)
    total_volume = total_buy + total_sell

    # Print report
    print(f"\n{'=' * 120}")
    print(f" {coin} ADDRESS ANALYSIS v2 - NET POSITIONS")
    print(f"{'=' * 120}")

    print(f"\n📊 OVERVIEW")
    print(f"   Unique addresses: {len(address_stats)}")
    print(f"   Total BUY volume: {total_buy:,.0f}")
    print(f"   Total SELL volume: {total_sell:,.0f}")
    print(f"   Market Net: {total_buy - total_sell:+,.0f}")

    # Two-way trader summary
    print(f"\n🔄 TWO-WAY TRADER ANALYSIS")
    print(
        f"   Addresses trading both sides: {len(two_way_traders)} of {len(address_stats)} ({len(two_way_traders) / len(address_stats) * 100:.0f}%)")
    print(
        f"   Two-way trader volume: {two_way_volume:,.0f} of {total_volume:,.0f} ({two_way_volume / total_volume * 100:.1f}%)" if total_volume > 0 else "")

    if len(two_way_traders) > 0 and two_way_volume / total_volume > 0.5:
        print(f"   ⚠️  WARNING: Majority of volume is two-way trading!")

    # Net position leaderboard
    print(f"\n{'=' * 120}")
    print(f" 🐂 NET BUYERS (bullish positioning)")
    print(f"{'=' * 120}")
    print(f"{'Address':<24} {'Net Position':>15} {'Buy Vol':>15} {'Sell Vol':>15} {'Churn':>8} {'Orders':>8}")
    print("-" * 100)

    net_buyers = [(addr, stats) for addr, stats in sorted_by_net if stats['net_position'] > 0]
    for addr, stats in net_buyers[:10]:
        short_addr = addr[:10] + "..." + addr[-6:]
        churn_pct = f"{stats['churn_ratio'] * 100:.0f}%"
        total_orders = stats['buy_orders'] + stats['sell_orders']
        print(
            f"{short_addr:<24} {stats['net_position']:>+15,.0f} {stats['buy_volume']:>15,.0f} {stats['sell_volume']:>15,.0f} {churn_pct:>8} {total_orders:>8}")

    print(f"\n{'=' * 120}")
    print(f" 🐻 NET SELLERS (bearish positioning)")
    print(f"{'=' * 120}")
    print(f"{'Address':<24} {'Net Position':>15} {'Buy Vol':>15} {'Sell Vol':>15} {'Churn':>8} {'Orders':>8}")
    print("-" * 100)

    net_sellers = [(addr, stats) for addr, stats in sorted_by_net if stats['net_position'] < 0]
    net_sellers.reverse()  # Most bearish first
    for addr, stats in net_sellers[:10]:
        short_addr = addr[:10] + "..." + addr[-6:]
        churn_pct = f"{stats['churn_ratio'] * 100:.0f}%"
        total_orders = stats['buy_orders'] + stats['sell_orders']
        print(
            f"{short_addr:<24} {stats['net_position']:>+15,.0f} {stats['buy_volume']:>15,.0f} {stats['sell_volume']:>15,.0f} {churn_pct:>8} {total_orders:>8}")

    # Suspicious activity flags
    print(f"\n{'=' * 120}")
    print(f" 🚨 SUSPICIOUS ACTIVITY FLAGS")
    print(f"{'=' * 120}")

    flags = []

    # Flag 1: High churn traders (>70% churn ratio with significant volume)
    high_churn = [(addr, stats) for addr, stats in address_stats.items()
                  if stats['churn_ratio'] > 0.7 and stats['total_volume'] > total_volume * 0.05]
    if high_churn:
        flags.append(f"HIGH CHURN: {len(high_churn)} address(es) with >70% volume cancelling out")
        for addr, stats in high_churn:
            short_addr = addr[:10] + "..." + addr[-6:]
            flags.append(
                f"   → {short_addr}: {stats['buy_volume']:,.0f} buy / {stats['sell_volume']:,.0f} sell = {stats['churn_ratio'] * 100:.0f}% churn")

    # Flag 2: Small address count with high concentration
    if len(address_stats) < 10 and total_volume > 100000:
        flags.append(f"LOW DIVERSITY: Only {len(address_stats)} addresses - easy to coordinate")

    # Flag 3: Two-way dominance
    if two_way_volume / total_volume > 0.7 if total_volume > 0 else False:
        flags.append(
            f"TWO-WAY DOMINANCE: {two_way_volume / total_volume * 100:.0f}% of all volume is from two-way traders")

    # Flag 4: Neutral net despite high volume
    net_ratio = abs(total_buy - total_sell) / total_volume if total_volume > 0 else 0
    if net_ratio < 0.1 and total_volume > 100000:
        flags.append(f"SUSPICIOUSLY BALANCED: Net is only {net_ratio * 100:.1f}% of total volume")

    if flags:
        for flag in flags:
            print(f"   ⚠️  {flag}")
    else:
        print(f"   ✓ No obvious suspicious patterns detected")

    # True directional pressure
    print(f"\n{'=' * 120}")
    print(f" 📈 TRUE DIRECTIONAL ANALYSIS")
    print(f"{'=' * 120}")

    pure_buyers = sum(s['buy_volume'] for _, s in address_stats.items() if not s['is_two_way'] and s['buy_volume'] > 0)
    pure_sellers = sum(
        s['sell_volume'] for _, s in address_stats.items() if not s['is_two_way'] and s['sell_volume'] > 0)

    net_from_two_way = sum(s['net_position'] for _, s in two_way_traders)

    print(f"   Pure one-way buyers: {pure_buyers:,.0f}")
    print(f"   Pure one-way sellers: {pure_sellers:,.0f}")
    print(f"   Net from two-way traders: {net_from_two_way:+,.0f}")
    print(f"   ")
    print(f"   TRUE NET PRESSURE: {pure_buyers - pure_sellers + net_from_two_way:+,.0f}")

    print(f"\n{'=' * 120}\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: python address_analyzer_v2.py <COIN> [date]")
        print("Example: python address_analyzer_v2.py MERL")
        print("Example: python address_analyzer_v2.py ZEREBRO 20251204")
        sys.exit(1)

    coin = sys.argv[1].upper()
    date_filter = sys.argv[2] if len(sys.argv) > 2 else None

    analyze_addresses(coin, date_filter)


if __name__ == "__main__":
    main()