#!/usr/bin/env python3
"""
PUMP Corrected Flow Analysis
Uses actual order sizes from new_orders, not summary volumes

The summary.buy_volume/sell_volume fields show ACTIVE order volume (snapshot),
not executed volume per interval. This script counts each order only once.
"""

import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DEFAULT_LOG_DIR = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\allcoins_json_logs")

TRACKED_ADDRESS = "0x45ab58a2034f03aa446baf3bb1d236706f866cbc"


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


def analyze_corrected(snapshots: list[dict]) -> dict:
    """Analyze using actual order sizes, counting each order only once."""

    tracked_lower = TRACKED_ADDRESS.lower()
    seen_orders = set()

    results = {
        'hourly': defaultdict(lambda: {
            'prices': [],
            'buy_volume': 0,
            'sell_volume': 0,
            'buy_orders': 0,
            'sell_orders': 0,
            'tracked_buy': 0,
            'tracked_sell': 0,
            'completed': 0,
            'canceled': 0,
        }),
        'all_orders': [],
        'addresses': defaultdict(lambda: {
            'buy_volume': 0,
            'sell_volume': 0,
            'buy_orders': 0,
            'sell_orders': 0,
        }),
        'tracked_orders': [],
    }

    for s in snapshots:
        ts = s.get('timestamp', '')
        try:
            dt = datetime.fromisoformat(ts)
            hour_key = dt.strftime("%b%d-%H")
        except:
            continue

        h = results['hourly'][hour_key]

        price = s.get('current_price', 0)
        if price:
            h['prices'].append(price)

        # Count completions and cancellations
        h['completed'] += len(s.get('completed_orders', []))
        h['canceled'] += len(s.get('canceled_orders', []))

        # Process NEW orders only - this is the actual flow
        for order in s.get('new_orders', []):
            order_hash = order.get('order_hash', '')

            # Skip if we've seen this order before
            if order_hash in seen_orders:
                continue
            seen_orders.add(order_hash)

            addr = order.get('address', '')
            side = order.get('side', '').upper()
            size = order.get('size', 0)
            duration = order.get('duration_minutes', 0)

            # Record the order
            order_data = {
                'timestamp': ts,
                'hour': hour_key,
                'address': addr,
                'side': side,
                'size': size,
                'price': price,
                'duration': duration,
            }
            results['all_orders'].append(order_data)

            # Hourly aggregation
            if side == 'BUY':
                h['buy_volume'] += size
                h['buy_orders'] += 1
                if addr.lower() == tracked_lower:
                    h['tracked_buy'] += size
                    results['tracked_orders'].append(order_data)
            elif side == 'SELL':
                h['sell_volume'] += size
                h['sell_orders'] += 1
                if addr.lower() == tracked_lower:
                    h['tracked_sell'] += size
                    results['tracked_orders'].append(order_data)

            # Address aggregation
            a = results['addresses'][addr]
            if side == 'BUY':
                a['buy_volume'] += size
                a['buy_orders'] += 1
            elif side == 'SELL':
                a['sell_volume'] += size
                a['sell_orders'] += 1

    return results


def print_report(results: dict, coin: str) -> None:
    """Print corrected analysis."""

    hourly = results['hourly']

    print(f"\n{'=' * 120}")
    print(f" {coin} CORRECTED FLOW ANALYSIS")
    print(f" (Counting each order ONCE at placement time)")
    print(f"{'=' * 120}")

    print(
        f"\n{'Hour':<12} {'Price':>10} {'Buy Vol':>16} {'Sell Vol':>16} {'Net Flow':>16} {'Orders':>8} {'Done/Can':>10}")
    print("-" * 120)

    total_buy = 0
    total_sell = 0
    total_orders = 0
    prev_price = None

    for hour_key in sorted(hourly.keys()):
        h = hourly[hour_key]

        # Skip hours with no price data
        if not h['prices']:
            continue

        avg_price = sum(h['prices']) / len(h['prices'])
        net = h['buy_volume'] - h['sell_volume']
        num_orders = h['buy_orders'] + h['sell_orders']

        total_buy += h['buy_volume']
        total_sell += h['sell_volume']
        total_orders += num_orders

        # Price change
        if prev_price and prev_price > 0:
            pct = ((avg_price - prev_price) / prev_price) * 100
            pct_str = f"({pct:+.1f}%)"
        else:
            pct_str = ""
        prev_price = avg_price

        net_str = f"+{net:,.0f}" if net >= 0 else f"{net:,.0f}"
        done_can = f"{h['completed']}/{h['canceled']}"

        # Highlight significant hours
        highlight = ""
        if h['tracked_sell'] > 0:
            highlight = " 🔴"
        elif h['tracked_buy'] > 0:
            highlight = " 🟢"
        elif abs(net) > 100_000_000:
            highlight = " ⚠️" if net < 0 else " 📈"

        print(
            f"{hour_key:<12} ${avg_price:.6f} {pct_str:>8} {h['buy_volume']:>16,.0f} {h['sell_volume']:>16,.0f} {net_str:>16} {num_orders:>8} {done_can:>10}{highlight}")

    print("-" * 120)
    total_net = total_buy - total_sell
    net_str = f"+{total_net:,.0f}" if total_net >= 0 else f"{total_net:,.0f}"
    print(f"{'TOTAL':<12} {'':>10} {'':>8} {total_buy:>16,.0f} {total_sell:>16,.0f} {net_str:>16} {total_orders:>8}")

    # Summary stats
    print(f"\n📊 CORRECTED TOTALS")
    print(f"   Total buy orders placed:    {total_buy:>20,.0f} tokens")
    print(f"   Total sell orders placed:   {total_sell:>20,.0f} tokens")
    print(f"   Net order flow:             {total_net:>+20,.0f} tokens")
    print(f"   Total orders:               {total_orders:>20,}")

    if total_buy > 0:
        print(f"   Est. buy value:             ${total_buy * 0.003:>19,.0f}")
    if total_sell > 0:
        print(f"   Est. sell value:            ${total_sell * 0.003:>19,.0f}")

    # Tracked address
    if results['tracked_orders']:
        print(f"\n🔴 TRACKED ADDRESS (0x45ab...) ORDERS")
        for o in results['tracked_orders']:
            print(f"   {o['timestamp']} - {o['side']} {o['size']:,.0f} @ ${o['price']:.6f}")

        tracked_sell = sum(o['size'] for o in results['tracked_orders'] if o['side'] == 'SELL')
        tracked_buy = sum(o['size'] for o in results['tracked_orders'] if o['side'] == 'BUY')
        if total_sell > 0 and tracked_sell > 0:
            pct = (tracked_sell / total_sell) * 100
            print(f"\n   Their sell volume: {tracked_sell:,.0f} ({pct:.1f}% of total)")

    # Top addresses
    print(f"\n{'=' * 120}")
    print(f" 🟢 TOP BUYERS")
    print(f"{'=' * 120}")

    buyers = []
    for addr, data in results['addresses'].items():
        net = data['buy_volume'] - data['sell_volume']
        if net > 0:
            buyers.append({
                'address': addr,
                'buy': data['buy_volume'],
                'sell': data['sell_volume'],
                'net': net,
                'orders': data['buy_orders'] + data['sell_orders'],
            })

    buyers.sort(key=lambda x: x['net'], reverse=True)

    print(f"\n{'Address':<44} {'Buy Vol':>18} {'Sell Vol':>18} {'Net Flow':>18} {'Orders':>8}")
    print("-" * 110)

    for b in buyers[:15]:
        print(f"{b['address']:<44} {b['buy']:>18,.0f} {b['sell']:>18,.0f} {b['net']:>+18,.0f} {b['orders']:>8}")

    # Top sellers
    print(f"\n{'=' * 120}")
    print(f" 🔴 TOP SELLERS")
    print(f"{'=' * 120}")

    sellers = []
    for addr, data in results['addresses'].items():
        net = data['buy_volume'] - data['sell_volume']
        if net < 0:
            sellers.append({
                'address': addr,
                'buy': data['buy_volume'],
                'sell': data['sell_volume'],
                'net': net,
                'orders': data['buy_orders'] + data['sell_orders'],
            })

    sellers.sort(key=lambda x: x['net'])

    print(f"\n{'Address':<44} {'Sell Vol':>18} {'Buy Vol':>18} {'Net Flow':>18} {'Orders':>8}")
    print("-" * 110)

    for s in sellers[:15]:
        print(f"{s['address']:<44} {s['sell']:>18,.0f} {s['buy']:>18,.0f} {s['net']:>18,.0f} {s['orders']:>8}")

    print(f"\n{'=' * 120}\n")


def main():
    coin = "PUMP"
    dates = ["20251130", "20251201", "20251202", "20251203"]

    files = get_coin_files(coin, dates)

    if not files:
        print(f"No files found for {coin}")
        return

    print(f"Found {len(files)} file(s) for {coin}")
    print(f"NOTE: This analysis counts each order ONCE when it appears in new_orders")
    print(f"      Previous analysis was double-counting active orders each minute\n")

    all_snapshots = []
    for f in files:
        print(f"Loading: {f.name}...")
        snapshots = load_jsonl(f)
        print(f"  → {len(snapshots):,} snapshots")
        all_snapshots.extend(snapshots)

    all_snapshots.sort(key=lambda x: x.get('timestamp', ''))

    results = analyze_corrected(all_snapshots)
    print_report(results, coin)


if __name__ == "__main__":
    main()