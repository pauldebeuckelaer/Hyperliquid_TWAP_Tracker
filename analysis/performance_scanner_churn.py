#!/usr/bin/env python3
"""
Performance Scanner v2 - With Churn Detection
Usage: python performance_scanner_churn.py

Scans all coins for divergences and flags unreliable signals due to wash trading.
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


def analyze_coin(coin_dir: Path) -> dict:
    """Analyze a single coin's data."""

    files = sorted(coin_dir.glob("*.jsonl"))
    if not files:
        return None

    all_snapshots = []
    seen_orders = {}

    for f in files:
        snapshots = load_jsonl(f)
        all_snapshots.extend(snapshots)

        for snap in snapshots:
            # Track unique orders
            for order in snap.get('active_orders', []):
                order_hash = order.get('order_hash')
                if order_hash and order_hash not in seen_orders:
                    seen_orders[order_hash] = {
                        'address': order.get('address', 'unknown'),
                        'side': order.get('side', 'unknown'),
                        'size': order.get('size', 0),
                    }

            for order in snap.get('new_orders', []):
                order_hash = order.get('order_hash')
                if order_hash and order_hash not in seen_orders:
                    seen_orders[order_hash] = {
                        'address': order.get('address', 'unknown'),
                        'side': order.get('side', 'unknown'),
                        'size': order.get('size', 0),
                    }

    if not all_snapshots:
        return None

    # Price data
    prices = [s.get('current_price', 0) for s in all_snapshots if s.get('current_price')]
    if not prices:
        return None

    start_price = prices[0]
    end_price = prices[-1]
    price_change = ((end_price - start_price) / start_price * 100) if start_price else 0

    # Address analysis for churn detection
    address_stats = defaultdict(lambda: {'buy_volume': 0, 'sell_volume': 0})

    for order_hash, order in seen_orders.items():
        addr = order['address']
        if order['side'] == 'BUY':
            address_stats[addr]['buy_volume'] += order['size']
        elif order['side'] == 'SELL':
            address_stats[addr]['sell_volume'] += order['size']

    # Calculate totals and churn
    total_buy = sum(v['buy_volume'] for v in address_stats.values())
    total_sell = sum(v['sell_volume'] for v in address_stats.values())
    total_volume = total_buy + total_sell

    # Two-way trader analysis
    two_way_volume = 0
    high_churn_addresses = 0

    for addr, stats in address_stats.items():
        if stats['buy_volume'] > 0 and stats['sell_volume'] > 0:
            two_way_volume += stats['buy_volume'] + stats['sell_volume']
            # Check if high churn (>70%)
            addr_total = stats['buy_volume'] + stats['sell_volume']
            min_side = min(stats['buy_volume'], stats['sell_volume'])
            churn_ratio = (min_side * 2) / addr_total if addr_total > 0 else 0
            if churn_ratio > 0.7:
                high_churn_addresses += 1

    two_way_pct = (two_way_volume / total_volume * 100) if total_volume > 0 else 0

    # Net flow in USD (approximate using average price)
    avg_price = (start_price + end_price) / 2
    net_flow = total_buy - total_sell
    net_flow_usd = net_flow * avg_price

    # Determine signal reliability
    reliable = True
    unreliable_reason = ""

    if two_way_pct > 70:
        reliable = False
        unreliable_reason = f"WASH:{two_way_pct:.0f}%"
    elif len(address_stats) < 5 and total_volume > 10000:
        reliable = False
        unreliable_reason = f"LOW_DIV:{len(address_stats)}addr"
    elif high_churn_addresses > 0 and high_churn_addresses >= len(address_stats) * 0.5:
        reliable = False
        unreliable_reason = f"CHURN:{high_churn_addresses}addr"

    # Determine signal type
    signal = ""
    if price_change > 5 and net_flow_usd < 0:
        signal = "🔥 HIDDEN BID"
    elif price_change > 5 and net_flow_usd > 0:
        signal = "📈 CONFIRMED"
    elif price_change < -5 and net_flow_usd > 0:
        signal = "⚠️ HIDDEN ASK"
    elif price_change < -5 and net_flow_usd < 0:
        signal = "📉 CONFIRMED"

    return {
        'symbol': coin_dir.name,
        'start_price': start_price,
        'end_price': end_price,
        'price_change': price_change,
        'net_flow': net_flow,
        'net_flow_usd': net_flow_usd,
        'total_orders': len(seen_orders),
        'unique_addresses': len(address_stats),
        'two_way_pct': two_way_pct,
        'high_churn_addresses': high_churn_addresses,
        'reliable': reliable,
        'unreliable_reason': unreliable_reason,
        'signal': signal,
    }


def main():
    print(f"Scanning coins with churn detection...\n")

    results = []
    coin_dirs = sorted([d for d in DEFAULT_LOG_DIR.iterdir() if d.is_dir()])

    for coin_dir in coin_dirs:
        result = analyze_coin(coin_dir)
        if result:
            status = "✓" if result['reliable'] else "⚠"
            print(
                f"  {status} {result['symbol']}: {result['price_change']:+.1f}% | {result['total_orders']} orders | {result['unique_addresses']} addr | 2way:{result['two_way_pct']:.0f}%")
            results.append(result)

    # Summary reports
    print(f"\n{'=' * 140}")
    print(f" 🚀 TOP PERFORMERS - RELIABLE SIGNALS ONLY")
    print(f"{'=' * 140}\n")

    reliable_results = [r for r in results if r['reliable']]
    unreliable_results = [r for r in results if not r['reliable']]

    # Top performers (reliable)
    top_performers = sorted([r for r in reliable_results if r['price_change'] > 5],
                            key=lambda x: x['price_change'], reverse=True)[:15]

    print(
        f"{'Coin':<12} {'Price Δ':>10} {'Start':>12} {'End':>12} {'Net Flow USD':>15} {'Addr':>6} {'2way%':>7} {'Signal':<20}")
    print("-" * 130)

    for r in top_performers:
        print(
            f"{r['symbol']:<12} {r['price_change']:>+9.1f}% ${r['start_price']:>10.4f} ${r['end_price']:>10.4f} ${r['net_flow_usd']:>+14,.0f} {r['unique_addresses']:>6} {r['two_way_pct']:>6.0f}% {r['signal']:<20}")

    # Worst performers (reliable)
    print(f"\n{'=' * 140}")
    print(f" 📉 WORST PERFORMERS - RELIABLE SIGNALS ONLY")
    print(f"{'=' * 140}\n")

    worst_performers = sorted([r for r in reliable_results if r['price_change'] < -5],
                              key=lambda x: x['price_change'])[:15]

    print(
        f"{'Coin':<12} {'Price Δ':>10} {'Start':>12} {'End':>12} {'Net Flow USD':>15} {'Addr':>6} {'2way%':>7} {'Signal':<20}")
    print("-" * 130)

    for r in worst_performers:
        print(
            f"{r['symbol']:<12} {r['price_change']:>+9.1f}% ${r['start_price']:>10.4f} ${r['end_price']:>10.4f} ${r['net_flow_usd']:>+14,.0f} {r['unique_addresses']:>6} {r['two_way_pct']:>6.0f}% {r['signal']:<20}")

    # Divergences (reliable only)
    print(f"\n{'=' * 140}")
    print(f" 🔥 RELIABLE DIVERGENCES (price vs TWAP flow)")
    print(f"{'=' * 140}\n")

    print("🟢 PUMPED despite TWAP SELLING (hidden demand):")
    hidden_bids = [r for r in reliable_results if r['price_change'] > 5 and r['net_flow_usd'] < 0]
    hidden_bids.sort(key=lambda x: x['price_change'], reverse=True)
    for r in hidden_bids[:10]:
        print(
            f"   {r['symbol']:<12} {r['price_change']:>+6.1f}% but ${r['net_flow_usd']:>+12,.0f} TWAP flow | {r['unique_addresses']} addresses")

    print("\n🔴 DUMPED despite TWAP BUYING (hidden supply):")
    hidden_asks = [r for r in reliable_results if r['price_change'] < -5 and r['net_flow_usd'] > 0]
    hidden_asks.sort(key=lambda x: x['price_change'])
    for r in hidden_asks[:10]:
        print(
            f"   {r['symbol']:<12} {r['price_change']:>+6.1f}% but ${r['net_flow_usd']:>+12,.0f} TWAP flow | {r['unique_addresses']} addresses")

    # Unreliable signals (flagged)
    print(f"\n{'=' * 140}")
    print(f" ⚠️  UNRELIABLE SIGNALS (high churn / wash trading suspected)")
    print(f"{'=' * 140}\n")

    print(f"{'Coin':<12} {'Price Δ':>10} {'Net Flow USD':>15} {'Addr':>6} {'2way%':>7} {'Reason':<20}")
    print("-" * 90)

    # Sort unreliable by absolute price change
    unreliable_sorted = sorted(unreliable_results, key=lambda x: abs(x['price_change']), reverse=True)
    for r in unreliable_sorted[:20]:
        print(
            f"{r['symbol']:<12} {r['price_change']:>+9.1f}% ${r['net_flow_usd']:>+14,.0f} {r['unique_addresses']:>6} {r['two_way_pct']:>6.0f}% {r['unreliable_reason']:<20}")

    # Summary stats
    print(f"\n{'=' * 140}")
    print(f" 📊 SUMMARY")
    print(f"{'=' * 140}\n")

    print(f"   Total coins analyzed: {len(results)}")
    print(f"   Reliable signals: {len(reliable_results)} ({len(reliable_results) / len(results) * 100:.0f}%)")
    print(f"   Unreliable (flagged): {len(unreliable_results)} ({len(unreliable_results) / len(results) * 100:.0f}%)")
    print(f"   ")
    print(f"   Reliable divergences found:")
    print(f"     Hidden bids (pump + TWAP selling): {len(hidden_bids)}")
    print(f"     Hidden asks (dump + TWAP buying): {len(hidden_asks)}")

    print(f"\n{'=' * 140}\n")


if __name__ == "__main__":
    main()