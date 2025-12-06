#!/usr/bin/env python3
"""
MON Rotation Analysis - Analyze MON activity during HYPE dump window
Usage: python mon_rotation_analysis.py

Analyzes Nov 30 and Dec 1 to see:
1. MON price and flow during the rotation window
2. Whether whale 0xa231... was dominant buyer
3. Other addresses buying MON during same period
"""

import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Adjust this to your setup
DEFAULT_LOG_DIR = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\allcoins_json_logs")

# The whale we're tracking
WHALE_ADDRESS = "0xa23190045c4aebeb724844ce622465475e539bae"


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


def get_coin_files(coin: str, dates: list[str]) -> list[Path]:
    """Get JSONL files for a coin across multiple dates."""
    coin_dir = DEFAULT_LOG_DIR / coin
    if not coin_dir.exists():
        for d in DEFAULT_LOG_DIR.iterdir():
            if d.name.upper() == coin.upper():
                coin_dir = d
                break

    if not coin_dir.exists():
        return []

    files = []
    for date in dates:
        found = list(coin_dir.glob(f"*{date}*.jsonl"))
        files.extend(found)

    return sorted(files)


def parse_timestamp(ts: str) -> datetime | None:
    """Parse timestamp string."""
    try:
        return datetime.fromisoformat(ts)
    except:
        return None


def analyze_rotation_window(snapshots: list[dict], coin: str) -> dict:
    """Analyze activity focusing on the rotation window (Nov 30 23:00 - Dec 1 18:00)."""

    results = {
        'coin': coin,
        'total_snapshots': len(snapshots),
        'hourly': defaultdict(lambda: {
            'prices': [],
            'buy_volume': 0,
            'sell_volume': 0,
            'new_orders': 0,
            'whale_buy': 0,
            'whale_sell': 0,
            'addresses': set(),
        }),
        'whale_activity': [],
        'all_addresses': defaultdict(lambda: {
            'buy_volume': 0,
            'sell_volume': 0,
            'orders': 0,
            'hours_active': set(),
        }),
    }

    seen_orders = set()

    for s in snapshots:
        ts = parse_timestamp(s.get('timestamp', ''))
        if not ts:
            continue

        # Create hour key like "Nov30-23" or "Dec01-14"
        hour_key = ts.strftime("%b%d-%H")
        h = results['hourly'][hour_key]

        # Price
        price = s.get('current_price', 0)
        if price:
            h['prices'].append(price)

        # Summary volumes
        summary = s.get('summary', {})
        h['buy_volume'] += summary.get('buy_volume', 0)
        h['sell_volume'] += summary.get('sell_volume', 0)

        # Process new orders
        for order in s.get('new_orders', []):
            order_hash = order.get('order_hash', '')
            if order_hash in seen_orders:
                continue
            seen_orders.add(order_hash)

            addr = order.get('address', '')
            side = order.get('side', '').upper()
            size = order.get('size', 0)

            h['new_orders'] += 1

            # Track per-address activity
            a = results['all_addresses'][addr]
            a['orders'] += 1
            a['hours_active'].add(hour_key)

            if side == 'BUY':
                a['buy_volume'] += size
                if addr.lower() == WHALE_ADDRESS.lower():
                    h['whale_buy'] += size
                    results['whale_activity'].append({
                        'timestamp': s.get('timestamp'),
                        'side': 'BUY',
                        'size': size,
                        'price': price,
                    })
            elif side == 'SELL':
                a['sell_volume'] += size
                if addr.lower() == WHALE_ADDRESS.lower():
                    h['whale_sell'] += size
                    results['whale_activity'].append({
                        'timestamp': s.get('timestamp'),
                        'side': 'SELL',
                        'size': size,
                        'price': price,
                    })

            h['addresses'].add(addr)

    return results


def print_report(results: dict) -> None:
    """Print analysis report."""
    coin = results['coin']
    hourly = results['hourly']

    print(f"\n{'=' * 100}")
    print(f" {coin} ROTATION WINDOW ANALYSIS")
    print(f"{'=' * 100}")

    # Hourly breakdown
    print(
        f"\n{'Hour':<12} {'Price':>10} {'Buy Vol':>14} {'Sell Vol':>14} {'Net Flow':>14} {'Whale Buy':>12} {'Addrs':>6}")
    print("-" * 100)

    total_buy = 0
    total_sell = 0
    total_whale_buy = 0
    total_whale_sell = 0

    for hour_key in sorted(hourly.keys()):
        h = hourly[hour_key]
        if not h['prices']:
            continue

        avg_price = sum(h['prices']) / len(h['prices'])
        net_flow = h['buy_volume'] - h['sell_volume']

        total_buy += h['buy_volume']
        total_sell += h['sell_volume']
        total_whale_buy += h['whale_buy']
        total_whale_sell += h['whale_sell']

        # Highlight rotation window hours
        highlight = ""
        if h['whale_buy'] > 0:
            highlight = " 🐋"

        net_str = f"+{net_flow:,.0f}" if net_flow >= 0 else f"{net_flow:,.0f}"
        whale_str = f"+{h['whale_buy']:,.0f}" if h['whale_buy'] > 0 else ""

        print(
            f"{hour_key:<12} ${avg_price:>9.4f} {h['buy_volume']:>14,.0f} {h['sell_volume']:>14,.0f} {net_str:>14} {whale_str:>12} {len(h['addresses']):>6}{highlight}")

    print("-" * 100)
    total_net = total_buy - total_sell
    net_str = f"+{total_net:,.0f}" if total_net >= 0 else f"{total_net:,.0f}"
    print(f"{'TOTAL':<12} {'':>10} {total_buy:>14,.0f} {total_sell:>14,.0f} {net_str:>14} {total_whale_buy:>+12,.0f}")

    # Whale dominance
    if total_buy > 0:
        whale_pct = (total_whale_buy / total_buy) * 100
        print(f"\n🐋 WHALE DOMINANCE")
        print(f"   Whale buy volume: {total_whale_buy:,.0f}")
        print(f"   Total buy volume: {total_buy:,.0f}")
        print(f"   Whale % of buys:  {whale_pct:.1f}%")

    # Whale activity log
    if results['whale_activity']:
        print(f"\n🐋 WHALE ORDER LOG")
        for w in results['whale_activity']:
            print(f"   {w['timestamp']} - {w['side']} {w['size']:,.0f} @ ${w['price']:.4f}")

    # Top buyers (excluding whale)
    print(f"\n📊 TOP BUYERS (excluding tracked whale)")
    addr_list = []
    for addr, data in results['all_addresses'].items():
        if addr.lower() == WHALE_ADDRESS.lower():
            continue
        net = data['buy_volume'] - data['sell_volume']
        if net > 0:
            addr_list.append({
                'address': addr,
                'buy_volume': data['buy_volume'],
                'sell_volume': data['sell_volume'],
                'net': net,
                'orders': data['orders'],
            })

    addr_list.sort(key=lambda x: x['net'], reverse=True)

    print(f"\n{'Address':<44} {'Buy Vol':>14} {'Sell Vol':>14} {'Net Flow':>14}")
    print("-" * 90)
    for a in addr_list[:10]:
        print(f"{a['address']:<44} {a['buy_volume']:>14,.0f} {a['sell_volume']:>14,.0f} {a['net']:>+14,.0f}")

    print(f"\n{'=' * 100}\n")


def main():
    coin = "MON"
    dates = ["20251130", "20251201", "20251202"]

    files = get_coin_files(coin, dates)

    if not files:
        print(f"No files found for {coin}")
        return

    print(f"Found {len(files)} file(s) for {coin}")

    all_snapshots = []
    for f in files:
        print(f"Loading: {f.name}...")
        snapshots = load_jsonl(f)
        print(f"  → {len(snapshots):,} snapshots")
        all_snapshots.extend(snapshots)

    # Sort by timestamp
    all_snapshots.sort(key=lambda x: x.get('timestamp', ''))

    results = analyze_rotation_window(all_snapshots, coin)
    print_report(results)


if __name__ == "__main__":
    main()