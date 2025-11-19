#!/usr/bin/env python3
"""
Analyze HYPE TWAP activity for Nov 17-18, 2025
Focus: HYPE moved 38.5 → 41 → 38.5 while BTC touched 89.5k

Key Questions:
1. Who was buying during the pump to 41?
2. Were those real buys or spoofs?
3. Who sold on the way back down?
4. Any familiar addresses (like our validator whale)?
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

# Data files
DATA_DIR = Path("json_logs")
DATES = ["20251117", "20251118"]

# Our known manipulator
VALIDATOR_WHALE = "0x5aeb1821f596d2d9ffe182d3f914b274a80511cc"


def load_recent_data():
    """Load Nov 17-18 data"""
    print("=" * 80)
    print("HYPE TWAP ANALYSIS: November 17-18, 2025")
    print("Price Action: 38.5 → 41 → 38.5 (while BTC hit 89.5k)")
    print("=" * 80)
    print()

    all_snapshots = []

    for date_str in DATES:
        filepath = DATA_DIR / f"HYPE_{date_str}.jsonl"

        if not filepath.exists():
            print(f"⚠️  {filepath} not found, skipping...")
            continue

        with open(filepath, 'r') as f:
            daily_data = []
            for line in f:
                try:
                    snapshot = json.loads(line.strip())
                    daily_data.append(snapshot)
                except:
                    continue

            all_snapshots.extend(daily_data)
            print(f"✅ Loaded {len(daily_data)} snapshots from {date_str}")

    print(f"\n📊 Total snapshots: {len(all_snapshots)}")
    return all_snapshots


def analyze_by_hour(snapshots):
    """Break down activity by hour to find the pump timing"""
    print("\n" + "=" * 80)
    print("HOURLY BREAKDOWN")
    print("=" * 80)

    hourly_stats = defaultdict(lambda: {
        'net_flow': [],
        'buy_volume': [],
        'sell_volume': [],
        'buy_pressure': [],
        'sell_pressure': [],
        'whale_orders': [],
        'unique_addresses': []
    })

    for snapshot in snapshots:
        timestamp = datetime.fromisoformat(snapshot['timestamp'])
        hour_key = timestamp.strftime('%Y-%m-%d %H:00')

        summary = snapshot['summary']
        hourly_stats[hour_key]['net_flow'].append(summary['net_flow'])
        hourly_stats[hour_key]['buy_volume'].append(summary['buy_volume'])
        hourly_stats[hour_key]['sell_volume'].append(summary['sell_volume'])
        hourly_stats[hour_key]['buy_pressure'].append(summary['buy_pressure_per_min'])
        hourly_stats[hour_key]['sell_pressure'].append(summary['sell_pressure_per_min'])
        hourly_stats[hour_key]['whale_orders'].append(summary['whale_orders'])
        hourly_stats[hour_key]['unique_addresses'].append(summary['unique_addresses'])

    # Calculate averages
    hourly_summary = []
    for hour, stats in sorted(hourly_stats.items()):
        avg_net_flow = sum(stats['net_flow']) / len(stats['net_flow'])
        avg_buy_vol = sum(stats['buy_volume']) / len(stats['buy_volume'])
        avg_sell_vol = sum(stats['sell_volume']) / len(stats['sell_volume'])
        avg_buy_pressure = sum(stats['buy_pressure']) / len(stats['buy_pressure'])
        avg_sell_pressure = sum(stats['sell_pressure']) / len(stats['sell_pressure'])
        avg_whale_orders = sum(stats['whale_orders']) / len(stats['whale_orders'])

        hourly_summary.append({
            'hour': hour,
            'avg_net_flow': avg_net_flow,
            'avg_buy_vol': avg_buy_vol,
            'avg_sell_vol': avg_sell_vol,
            'avg_buy_pressure': avg_buy_pressure,
            'avg_sell_pressure': avg_sell_pressure,
            'avg_whale_orders': avg_whale_orders,
            'snapshots': len(stats['net_flow'])
        })

    # Print the breakdown
    print("\nHour-by-Hour Analysis:")
    print("-" * 80)

    for h in hourly_summary:
        direction = "🟢 ACCUMULATION" if h['avg_net_flow'] > 0 else "🔴 DISTRIBUTION"
        pressure = "BUY" if h['avg_buy_pressure'] > h['avg_sell_pressure'] else "SELL"

        print(f"\n{h['hour']} ({h['snapshots']} snapshots)")
        print(f"  {direction}")
        print(f"  Net Flow: {h['avg_net_flow']:,.2f} HYPE")
        print(f"  Buy Volume: {h['avg_buy_vol']:,.2f} HYPE")
        print(f"  Sell Volume: {h['avg_sell_vol']:,.2f} HYPE")
        print(
            f"  Pressure: {h['avg_buy_pressure']:.1f} buy/min vs {h['avg_sell_pressure']:.1f} sell/min ({pressure} dominated)")
        print(f"  Whale Orders: {h['avg_whale_orders']:.1f}")

    return hourly_summary


def find_top_players(snapshots):
    """Identify who was most active during this period"""
    print("\n" + "=" * 80)
    print("TOP PLAYERS (Nov 17-18)")
    print("=" * 80)

    address_activity = defaultdict(lambda: {
        'total_buy_size': 0,
        'total_sell_size': 0,
        'buy_orders': 0,
        'sell_orders': 0,
        'appearances': 0,
        'whale_orders': 0,
        'completed': 0,
        'canceled': 0,
        'active': 0
    })

    # Track all unique orders
    seen_orders = set()

    for snapshot in snapshots:
        for order in snapshot.get('active_orders', []):
            address = order['address']
            side = order['side']
            size = order['size']
            status = order['status']

            # Create unique order ID
            order_id = f"{address}_{side}_{size}_{order.get('duration_hours', 0)}"

            if order_id not in seen_orders:
                seen_orders.add(order_id)

                stats = address_activity[address]

                if side == 'BUY':
                    stats['total_buy_size'] += size
                    stats['buy_orders'] += 1
                else:
                    stats['total_sell_size'] += size
                    stats['sell_orders'] += 1

                if size >= 10000:
                    stats['whale_orders'] += 1

                if status == 'completed':
                    stats['completed'] += 1
                elif status == 'canceled':
                    stats['canceled'] += 1
                elif status == 'active':
                    stats['active'] += 1

                stats['appearances'] += 1

    # Calculate net positioning
    ranked_addresses = []
    for address, stats in address_activity.items():
        net_volume = stats['total_buy_size'] - stats['total_sell_size']
        completion_rate = stats['completed'] / (stats['buy_orders'] + stats['sell_orders']) if (stats['buy_orders'] +
                                                                                                stats[
                                                                                                    'sell_orders']) > 0 else 0

        ranked_addresses.append({
            'address': address,
            'net_volume': net_volume,
            'buy_volume': stats['total_buy_size'],
            'sell_volume': stats['total_sell_size'],
            'buy_orders': stats['buy_orders'],
            'sell_orders': stats['sell_orders'],
            'whale_orders': stats['whale_orders'],
            'completed': stats['completed'],
            'canceled': stats['canceled'],
            'active': stats['active'],
            'completion_rate': completion_rate
        })

    # Sort by absolute net volume
    ranked_addresses.sort(key=lambda x: abs(x['net_volume']), reverse=True)

    print("\nTop 20 Most Active Addresses:")
    print("-" * 80)

    for i, addr in enumerate(ranked_addresses[:20], 1):
        direction = "🟢 NET BUYER" if addr['net_volume'] > 0 else "🔴 NET SELLER"

        # Check if it's our validator whale
        is_whale = "🚨 VALIDATOR WHALE!" if addr['address'].lower() == VALIDATOR_WHALE.lower() else ""

        print(f"\n{i}. {addr['address'][:10]}...{addr['address'][-6:]} {is_whale}")
        print(f"   {direction}")
        print(f"   Net Volume: {addr['net_volume']:,.2f} HYPE")
        print(f"   Buy: {addr['buy_volume']:,.2f} HYPE ({addr['buy_orders']} orders)")
        print(f"   Sell: {addr['sell_volume']:,.2f} HYPE ({addr['sell_orders']} orders)")
        print(f"   Whale Orders (>10k): {addr['whale_orders']}")
        print(f"   Completed: {addr['completed']} | Canceled: {addr['canceled']} | Active: {addr['active']}")
        print(f"   Completion Rate: {addr['completion_rate'] * 100:.1f}%")

    return ranked_addresses


def detect_pump_period(hourly_summary):
    """Try to identify when the pump to 41 happened"""
    print("\n" + "=" * 80)
    print("PUMP DETECTION")
    print("=" * 80)

    print("\nLooking for strongest accumulation periods (likely the pump to 41)...")

    # Find hours with strongest buying
    accumulation_hours = [h for h in hourly_summary if h['avg_net_flow'] > 0]
    accumulation_hours.sort(key=lambda x: x['avg_net_flow'], reverse=True)

    if accumulation_hours:
        print("\n🚀 STRONGEST ACCUMULATION PERIODS:")
        for h in accumulation_hours[:5]:
            print(f"\n  {h['hour']}")
            print(f"    Net Flow: {h['avg_net_flow']:,.2f} HYPE")
            print(f"    Buy Pressure: {h['avg_buy_pressure']:.1f}/min")
            print(f"    Whale Orders: {h['avg_whale_orders']:.1f}")

    # Find hours with strongest selling (the dump back to 38.5)
    distribution_hours = [h for h in hourly_summary if h['avg_net_flow'] < 0]
    distribution_hours.sort(key=lambda x: x['avg_net_flow'])

    if distribution_hours:
        print("\n📉 STRONGEST DISTRIBUTION PERIODS:")
        for h in distribution_hours[:5]:
            print(f"\n  {h['hour']}")
            print(f"    Net Flow: {h['avg_net_flow']:,.2f} HYPE")
            print(f"    Sell Pressure: {h['avg_sell_pressure']:.1f}/min")


def check_for_manipulation_patterns(snapshots, top_addresses):
    """Look for patterns similar to Nov 7th manipulation"""
    print("\n" + "=" * 80)
    print("MANIPULATION PATTERN CHECK")
    print("=" * 80)

    print("\nLooking for suspicious patterns...")

    suspicious_count = 0

    for addr_data in top_addresses[:10]:
        address = addr_data['address']

        # Red flags
        red_flags = []

        # High cancellation rate
        total_orders = addr_data['buy_orders'] + addr_data['sell_orders']
        cancel_rate = addr_data['canceled'] / total_orders if total_orders > 0 else 0

        if cancel_rate > 0.5:
            red_flags.append(f"High cancel rate: {cancel_rate * 100:.1f}%")

        # Large orders but low completion
        if addr_data['whale_orders'] > 2 and addr_data['completion_rate'] < 0.3:
            red_flags.append(f"Many whale orders ({addr_data['whale_orders']}) but low completion")

        # One-sided activity
        if addr_data['buy_orders'] > 0 and addr_data['sell_orders'] == 0 and cancel_rate > 0.3:
            red_flags.append("Only buy orders, many canceled (potential spoof)")

        if red_flags:
            suspicious_count += 1
            print(f"\n⚠️  SUSPICIOUS: {address[:10]}...{address[-6:]}")
            for flag in red_flags:
                print(f"     - {flag}")

    if suspicious_count == 0:
        print("\n✅ No obvious manipulation patterns detected")
        print("   (But that doesn't mean there wasn't any - just more subtle)")
    else:
        print(f"\n🚨 Found {suspicious_count} addresses with suspicious patterns")


def main():
    """Run the analysis"""

    # Load data
    snapshots = load_recent_data()

    if not snapshots:
        print("\n❌ No data found! Make sure json_logs/ contains HYPE_20251117.jsonl and HYPE_20251118.jsonl")
        return

    # Hourly breakdown
    hourly_summary = analyze_by_hour(snapshots)

    # Top players
    top_addresses = find_top_players(snapshots)

    # Identify pump timing
    detect_pump_period(hourly_summary)

    # Check for manipulation
    check_for_manipulation_patterns(snapshots, top_addresses)

    # Final summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    total_net_flow = sum(s['summary']['net_flow'] for s in snapshots) / len(snapshots)

    print(f"\nAverage Net Flow: {total_net_flow:,.2f} HYPE")

    if total_net_flow > 0:
        print("📊 VERDICT: Net accumulation during this period")
        print("   The pump may have been driven by real buying")
    else:
        print("📊 VERDICT: Net distribution during this period")
        print("   The pump may have been a bull trap / manipulation")

    print("\n💡 KEY INSIGHTS:")
    print("   - Check the hourly breakdown to see exactly when the pump happened")
    print("   - Look at top addresses to see who was driving the action")
    print("   - Watch for high cancel rates = potential spoofing")
    print("   - Compare completion rates: <30% = likely fake orders")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()