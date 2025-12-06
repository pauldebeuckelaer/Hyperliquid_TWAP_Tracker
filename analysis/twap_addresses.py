#!/usr/bin/env python3
"""
TWAP Address Analyzer - Who's buying/selling?
Usage: python twap_addresses.py <COIN> <date>

Example:
    python twap_addresses.py HYPE 20251201
"""

import json
import sys
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


def get_coin_file(coin: str, date: str) -> Path | None:
    """Get specific JSONL file for coin and date."""
    coin_dir = DEFAULT_LOG_DIR / coin
    if not coin_dir.exists():
        for d in DEFAULT_LOG_DIR.iterdir():
            if d.name.upper() == coin.upper():
                coin_dir = d
                break

    if not coin_dir.exists():
        return None

    files = list(coin_dir.glob(f"*{date}*.jsonl"))
    return files[0] if files else None


def parse_hour(timestamp: str) -> int:
    """Extract hour from timestamp."""
    try:
        return datetime.fromisoformat(timestamp).hour
    except:
        return -1


def analyze_addresses(snapshots: list[dict]) -> dict:
    """Analyze activity per address."""
    addresses = defaultdict(lambda: {
        'buy_orders': 0,
        'sell_orders': 0,
        'buy_volume': 0,
        'sell_volume': 0,
        'completed': 0,
        'canceled': 0,
        'first_seen_hour': 24,
        'last_seen_hour': -1,
        'hours_active': set(),
        'order_hashes': set(),
    })

    # Track orders we've seen to avoid double counting
    seen_orders = set()

    for s in snapshots:
        hour = parse_hour(s.get('timestamp', ''))

        # Process new orders
        for order in s.get('new_orders', []):
            addr = order.get('address', '')
            order_hash = order.get('order_hash', '')

            if not addr or order_hash in seen_orders:
                continue
            seen_orders.add(order_hash)

            a = addresses[addr]
            side = order.get('side', '').upper()
            size = order.get('size', 0)

            if side == 'BUY':
                a['buy_orders'] += 1
                a['buy_volume'] += size
            elif side == 'SELL':
                a['sell_orders'] += 1
                a['sell_volume'] += size

            a['order_hashes'].add(order_hash)
            a['hours_active'].add(hour)
            a['first_seen_hour'] = min(a['first_seen_hour'], hour)
            a['last_seen_hour'] = max(a['last_seen_hour'], hour)

        # Process completed orders
        for order in s.get('completed_orders', []):
            addr = order.get('address', '')
            if addr:
                addresses[addr]['completed'] += 1

        # Process canceled orders
        for order in s.get('canceled_orders', []):
            addr = order.get('address', '')
            if addr:
                addresses[addr]['canceled'] += 1

    return addresses


def print_address_report(addresses: dict, symbol: str, date: str) -> None:
    """Print address analysis report."""

    # Calculate net flow per address
    addr_list = []
    for addr, data in addresses.items():
        net_flow = data['buy_volume'] - data['sell_volume']
        total_orders = data['buy_orders'] + data['sell_orders']
        addr_list.append({
            'address': addr,
            'net_flow': net_flow,
            'total_orders': total_orders,
            **data
        })

    # Sort by net flow (biggest sellers first)
    sellers = sorted([a for a in addr_list if a['net_flow'] < 0], key=lambda x: x['net_flow'])
    buyers = sorted([a for a in addr_list if a['net_flow'] > 0], key=lambda x: x['net_flow'], reverse=True)

    print(f"\n{'=' * 120}")
    print(f" {symbol} ADDRESS ANALYSIS - {date}")
    print(f"{'=' * 120}")

    # Summary
    total_sell_vol = sum(a['sell_volume'] for a in addr_list)
    total_buy_vol = sum(a['buy_volume'] for a in addr_list)

    print(f"\n📊 SUMMARY")
    print(f"   Total addresses: {len(addr_list)}")
    print(f"   Net sellers: {len(sellers)}")
    print(f"   Net buyers: {len(buyers)}")
    print(f"   Total sell volume: {total_sell_vol:,.0f}")
    print(f"   Total buy volume: {total_buy_vol:,.0f}")

    # Top sellers
    print(f"\n{'=' * 120}")
    print(f" 🔴 TOP SELLERS")
    print(f"{'=' * 120}")
    print(f"\n{'Address':<44} {'Sell Vol':>14} {'Buy Vol':>14} {'Net Flow':>14} {'Orders':>8} {'Hours Active':<20}")
    print("-" * 120)

    top_seller_volume = 0
    for a in sellers[:15]:
        addr_short = a['address']
        hours = sorted(a['hours_active'])
        if len(hours) > 5:
            hours_str = f"{hours[0]:02d}-{hours[-1]:02d} ({len(hours)}h)"
        else:
            hours_str = ",".join(f"{h:02d}" for h in hours)

        print(
            f"{addr_short:<44} {a['sell_volume']:>14,.0f} {a['buy_volume']:>14,.0f} {a['net_flow']:>+14,.0f} {a['total_orders']:>8} {hours_str:<20}")
        top_seller_volume += a['sell_volume']

    if sellers:
        concentration = (sellers[0]['sell_volume'] / total_sell_vol * 100) if total_sell_vol else 0
        top5_concentration = (
                    sum(s['sell_volume'] for s in sellers[:5]) / total_sell_vol * 100) if total_sell_vol else 0
        print(f"\n   Top seller concentration: {concentration:.1f}% of all sells")
        print(f"   Top 5 sellers concentration: {top5_concentration:.1f}% of all sells")

    # Top buyers
    print(f"\n{'=' * 120}")
    print(f" 🟢 TOP BUYERS")
    print(f"{'=' * 120}")
    print(f"\n{'Address':<44} {'Buy Vol':>14} {'Sell Vol':>14} {'Net Flow':>14} {'Orders':>8} {'Hours Active':<20}")
    print("-" * 120)

    for a in buyers[:15]:
        addr_short = a['address']
        hours = sorted(a['hours_active'])
        if len(hours) > 5:
            hours_str = f"{hours[0]:02d}-{hours[-1]:02d} ({len(hours)}h)"
        else:
            hours_str = ",".join(f"{h:02d}" for h in hours)

        print(
            f"{addr_short:<44} {a['buy_volume']:>14,.0f} {a['sell_volume']:>14,.0f} {a['net_flow']:>+14,.0f} {a['total_orders']:>8} {hours_str:<20}")

    if buyers:
        concentration = (buyers[0]['buy_volume'] / total_buy_vol * 100) if total_buy_vol else 0
        top5_concentration = (sum(b['buy_volume'] for b in buyers[:5]) / total_buy_vol * 100) if total_buy_vol else 0
        print(f"\n   Top buyer concentration: {concentration:.1f}% of all buys")
        print(f"   Top 5 buyers concentration: {top5_concentration:.1f}% of all buys")

    # Timing analysis for top seller
    if sellers:
        print(f"\n{'=' * 120}")
        print(f" ⏰ TIMING ANALYSIS")
        print(f"{'=' * 120}")

        # Check if sellers were coordinated
        early_sellers = [s for s in sellers if s['first_seen_hour'] <= 4]
        late_sellers = [s for s in sellers if s['first_seen_hour'] >= 12]

        print(f"\n   Sellers active 00:00-04:00 UTC: {len(early_sellers)} addresses")
        print(f"   Sellers active after 12:00 UTC: {len(late_sellers)} addresses")

        # Check overlap in timing
        if len(sellers) >= 2:
            s1_hours = sellers[0]['hours_active']
            s2_hours = sellers[1]['hours_active']
            overlap = s1_hours & s2_hours
            print(f"\n   Top 2 sellers hour overlap: {len(overlap)} hours ({sorted(overlap) if overlap else 'none'})")

    print(f"\n{'=' * 120}\n")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    coin = sys.argv[1].upper()
    date = sys.argv[2]

    filepath = get_coin_file(coin, date)

    if not filepath:
        print(f"No file found for {coin} on {date}")
        sys.exit(1)

    print(f"Loading: {filepath}")
    snapshots = load_jsonl(filepath)
    print(f"Loaded {len(snapshots):,} snapshots")

    addresses = analyze_addresses(snapshots)
    print_address_report(addresses, coin, date)


if __name__ == "__main__":
    main()