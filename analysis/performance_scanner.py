#!/usr/bin/env python3
"""
Performance Scanner - Which coins pumped hardest?
Compares price change vs TWAP flow to find divergences
"""
import sys
import json
from pathlib import Path
from collections import defaultdict

DEFAULT_LOG_DIR = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\allcoins_json_logs")


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


def analyze_coin(coin_dir: Path, target_date: str = None) -> dict | None:
    """Analyze a single coin's performance."""

    if target_date:
        target_file = coin_dir / f"{coin_dir.name}_{target_date}.jsonl"
        if not target_file.exists():
            return None
        files = [target_file]
    else:
        files = sorted(coin_dir.glob("*.jsonl"))

    all_snapshots = []
    for f in files:
        all_snapshots.extend(load_jsonl(f))

    if not all_snapshots:
        return None

    # Sort by timestamp
    all_snapshots.sort(key=lambda x: x.get('timestamp', ''))

    # Get prices
    prices = [s.get('current_price', 0) for s in all_snapshots if s.get('current_price')]
    if len(prices) < 2:
        return None

    price_start = prices[0]
    price_end = prices[-1]
    price_low = min(prices)
    price_high = max(prices)

    if price_start == 0:
        return None

    price_change_pct = ((price_end - price_start) / price_start) * 100

    # Calculate TWAP flow (counting each order once)
    seen_orders = set()
    total_buy = 0
    total_sell = 0
    num_orders = 0

    for s in all_snapshots:
        for order in s.get('new_orders', []):
            order_hash = order.get('order_hash', '')
            if order_hash in seen_orders:
                continue
            seen_orders.add(order_hash)

            side = order.get('side', '').upper()
            size = order.get('size', 0)

            num_orders += 1
            if side == 'BUY':
                total_buy += size
            elif side == 'SELL':
                total_sell += size

    net_flow = total_buy - total_sell

    # Calculate USD values (rough estimate using average price)
    avg_price = sum(prices) / len(prices)
    net_flow_usd = net_flow * avg_price
    buy_usd = total_buy * avg_price
    sell_usd = total_sell * avg_price

    return {
        'coin': coin_dir.name,
        'price_start': price_start,
        'price_end': price_end,
        'price_low': price_low,
        'price_high': price_high,
        'price_change_pct': price_change_pct,
        'total_buy': total_buy,
        'total_sell': total_sell,
        'net_flow': net_flow,
        'net_flow_usd': net_flow_usd,
        'buy_usd': buy_usd,
        'sell_usd': sell_usd,
        'num_orders': num_orders,
        'num_snapshots': len(all_snapshots),
    }


def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else None

    coin_dirs = sorted([d for d in DEFAULT_LOG_DIR.iterdir() if d.is_dir()])

    print(f"Scanning {len(coin_dirs)} coins{f' for {target_date}' if target_date else ''}...\n")

    results = []
    for coin_dir in coin_dirs:
        result = analyze_coin(coin_dir, target_date)

        if result and result['num_orders'] > 0:  # Only coins with TWAP activity
            results.append(result)
            print(f"  ✓ {result['coin']}: {result['price_change_pct']:+.1f}% | {result['num_orders']} orders")

    # Sort by price change
    results.sort(key=lambda x: x['price_change_pct'], reverse=True)

    print(f"\n{'=' * 130}")
    print(f" 🚀 TOP PERFORMERS (by price change)")
    print(f"{'=' * 130}")

    print(
        f"\n{'Coin':<12} {'Price Δ':>10} {'Start':>12} {'End':>12} {'TWAP Net Flow':>18} {'Net USD':>14} {'Orders':>8} {'Signal':<12}")
    print("-" * 130)

    for r in results[:25]:
        # Determine signal type
        if r['price_change_pct'] > 5 and r['net_flow'] < 0:
            signal = "🔥 HIDDEN BID"  # Pumped despite selling
        elif r['price_change_pct'] > 5 and r['net_flow'] > 0:
            signal = "📈 CONFIRMED"  # Pumped with buying
        elif r['price_change_pct'] < -5 and r['net_flow'] > 0:
            signal = "⚠️ HIDDEN ASK"  # Dumped despite buying
        elif r['price_change_pct'] < -5 and r['net_flow'] < 0:
            signal = "📉 CONFIRMED"  # Dumped with selling
        else:
            signal = ""

        net_str = f"+{r['net_flow']:,.0f}" if r['net_flow'] >= 0 else f"{r['net_flow']:,.0f}"
        usd_str = f"${r['net_flow_usd']:+,.0f}"

        print(
            f"{r['coin']:<12} {r['price_change_pct']:>+9.1f}% ${r['price_start']:>11.4f} ${r['price_end']:>11.4f} {net_str:>18} {usd_str:>14} {r['num_orders']:>8} {signal:<12}")

    print(f"\n{'=' * 130}")
    print(f" 📉 WORST PERFORMERS")
    print(f"{'=' * 130}")

    print(
        f"\n{'Coin':<12} {'Price Δ':>10} {'Start':>12} {'End':>12} {'TWAP Net Flow':>18} {'Net USD':>14} {'Orders':>8} {'Signal':<12}")
    print("-" * 130)

    for r in results[-15:]:
        if r['price_change_pct'] > 5 and r['net_flow'] < 0:
            signal = "🔥 HIDDEN BID"
        elif r['price_change_pct'] > 5 and r['net_flow'] > 0:
            signal = "📈 CONFIRMED"
        elif r['price_change_pct'] < -5 and r['net_flow'] > 0:
            signal = "⚠️ HIDDEN ASK"
        elif r['price_change_pct'] < -5 and r['net_flow'] < 0:
            signal = "📉 CONFIRMED"
        else:
            signal = ""

        net_str = f"+{r['net_flow']:,.0f}" if r['net_flow'] >= 0 else f"{r['net_flow']:,.0f}"
        usd_str = f"${r['net_flow_usd']:+,.0f}"

        print(
            f"{r['coin']:<12} {r['price_change_pct']:>+9.1f}% ${r['price_start']:>11.4f} ${r['price_end']:>11.4f} {net_str:>18} {usd_str:>14} {r['num_orders']:>8} {signal:<12}")

    # Find most interesting divergences
    print(f"\n{'=' * 130}")
    print(f" 🔥 BIGGEST DIVERGENCES (price vs TWAP flow)")
    print(f"{'=' * 130}")

    # Pumped despite selling
    hidden_demand = [r for r in results if r['price_change_pct'] > 5 and r['net_flow_usd'] < -10000]
    hidden_demand.sort(key=lambda x: x['price_change_pct'], reverse=True)

    if hidden_demand:
        print(f"\n🟢 PUMPED despite TWAP SELLING (hidden demand):")
        for r in hidden_demand[:10]:
            print(f"   {r['coin']:<10} +{r['price_change_pct']:.1f}% but ${r['net_flow_usd']:,.0f} TWAP flow")

    # Dumped despite buying
    hidden_supply = [r for r in results if r['price_change_pct'] < -5 and r['net_flow_usd'] > 10000]
    hidden_supply.sort(key=lambda x: x['price_change_pct'])

    if hidden_supply:
        print(f"\n🔴 DUMPED despite TWAP BUYING (hidden supply):")
        for r in hidden_supply[:10]:
            print(f"   {r['coin']:<10} {r['price_change_pct']:.1f}% but ${r['net_flow_usd']:+,.0f} TWAP flow")

    # Summary stats
    print(f"\n{'=' * 130}")
    print(f" 📊 SUMMARY")
    print(f"{'=' * 130}")
    print(f"\n   Total coins analyzed: {len(results)}")
    print(f"   Coins that pumped (>5%): {len([r for r in results if r['price_change_pct'] > 5])}")
    print(f"   Coins that dumped (<-5%): {len([r for r in results if r['price_change_pct'] < -5])}")
    print(f"   Coins with net TWAP buying: {len([r for r in results if r['net_flow'] > 0])}")
    print(f"   Coins with net TWAP selling: {len([r for r in results if r['net_flow'] < 0])}")

    print(f"\n{'=' * 130}\n")


if __name__ == "__main__":
    main()