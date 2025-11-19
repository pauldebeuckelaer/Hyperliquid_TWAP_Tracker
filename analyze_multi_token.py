#!/usr/bin/env python3
"""
Multi-Token TWAP Analyzer
Analyze 3 days of all-token snapshots to find:
1. Coordinated whale activity across tokens
2. HYPE-specific manipulation during BTC weakness
3. Address patterns over time
4. Real pump/dump mechanics
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

DATA_DIR = Path("twap_snapshots")
DATES = ["2025-11-16", "2025-11-17", "2025-11-18"]

# Known manipulator from earlier analysis
VALIDATOR_WHALE = "0x5aeb1821f596d2d9ffe182d3f914b274a80511cc"


def load_all_data():
    """Load all 3 days of snapshot data"""
    print("=" * 80)
    print("LOADING 3-DAY MULTI-TOKEN DATA")
    print("=" * 80)
    print()

    all_data = defaultdict(list)  # {date: [snapshots]}

    for date_str in DATES:
        filepath = DATA_DIR / f"all_coins_{date_str}.jsonl"

        if not filepath.exists():
            print(f"⚠️  {filepath} not found, skipping...")
            continue

        print(f"Loading {date_str}...")
        with open(filepath, 'r') as f:
            count = 0
            for line in f:
                try:
                    snapshot = json.loads(line.strip())
                    all_data[date_str].append(snapshot)
                    count += 1
                except:
                    continue

        print(f"  ✅ Loaded {count:,} snapshots")

    print()
    return all_data


def find_cross_token_whales(all_data):
    """Find addresses that appear across multiple tokens"""
    print("=" * 80)
    print("CROSS-TOKEN WHALE DETECTION")
    print("=" * 80)
    print()

    address_tokens = defaultdict(set)  # {address: {tokens}}
    address_volume = defaultdict(lambda: defaultdict(float))  # {address: {token: volume}}

    for date_str, snapshots in all_data.items():
        for snapshot in snapshots:
            token = snapshot['symbol']

            for order in snapshot.get('active_orders', []):
                address = order['address']
                size = order['size']

                address_tokens[address].add(token)
                address_volume[address][token] += size

    # Find multi-token whales
    multi_token_whales = {addr: tokens for addr, tokens in address_tokens.items()
                          if len(tokens) >= 3}

    print(f"Found {len(multi_token_whales)} addresses trading 3+ tokens")
    print()

    # Sort by number of tokens
    ranked = sorted(multi_token_whales.items(), key=lambda x: len(x[1]), reverse=True)

    print("Top 20 Multi-Token Traders:")
    print("-" * 80)

    for i, (addr, tokens) in enumerate(ranked[:20], 1):
        is_validator = "🚨 VALIDATOR!" if addr.lower() == VALIDATOR_WHALE.lower() else ""

        total_vol = sum(address_volume[addr].values())

        print(f"\n{i}. {addr[:10]}...{addr[-6:]} {is_validator}")
        print(f"   Tokens: {len(tokens)} - {', '.join(sorted(tokens)[:5])}...")
        print(f"   Total Volume: {total_vol:,.0f}")

        # Show top 3 tokens by volume
        top_tokens = sorted(address_volume[addr].items(), key=lambda x: x[1], reverse=True)[:3]
        for token, vol in top_tokens:
            print(f"     {token}: {vol:,.0f}")

    return multi_token_whales, address_volume


def analyze_hype_vs_btc(all_data):
    """Compare HYPE behavior during BTC movements"""
    print("\n" + "=" * 80)
    print("HYPE vs BTC BEHAVIOR ANALYSIS")
    print("=" * 80)
    print()

    # Extract HYPE and BTC data by timestamp
    hype_by_time = {}
    btc_by_time = {}

    for date_str, snapshots in all_data.items():
        for snapshot in snapshots:
            timestamp = snapshot['timestamp']
            symbol = snapshot['symbol']

            if symbol == 'HYPE':
                hype_by_time[timestamp] = snapshot['summary']
            elif symbol == 'BTC':
                btc_by_time[timestamp] = snapshot['summary']

    # Find Nov 18 key moments
    print("Nov 18 Key Moments (during 38.5→41→38.5 move):")
    print("-" * 80)

    # Parse timestamps and filter for Nov 18, 10:00-16:00 UTC
    for timestamp in sorted(hype_by_time.keys()):
        dt = datetime.fromisoformat(timestamp)

        if dt.date() != datetime(2025, 11, 18).date():
            continue

        if not (10 <= dt.hour <= 16):
            continue

        hype_data = hype_by_time.get(timestamp, {})
        btc_data = btc_by_time.get(timestamp, {})

        hype_net = hype_data.get('net_flow', 0)
        btc_net = btc_data.get('net_flow', 0)

        hype_direction = "🟢 BUY" if hype_net > 0 else "🔴 SELL"
        btc_direction = "🟢 BUY" if btc_net > 0 else "🔴 SELL"

        # Highlight divergences
        divergence = ""
        if (hype_net > 0 and btc_net < 0) or (hype_net < 0 and btc_net > 0):
            divergence = "⚠️  DIVERGENCE"

        print(f"\n{dt.strftime('%H:%M')} {divergence}")
        print(f"  HYPE: {hype_direction} Net={hype_net:,.0f}")
        print(f"  BTC:  {btc_direction} Net={btc_net:,.2f}")


def track_pump_dump_addresses(all_data):
    """Track specific addresses during the Nov 18 pump"""
    print("\n" + "=" * 80)
    print("NOV 18 PUMP ADDRESSES (11:00-13:00)")
    print("=" * 80)
    print()

    pump_addresses = defaultdict(lambda: {
        'orders': [],
        'first_seen': None,
        'last_seen': None,
        'total_buy': 0,
        'total_sell': 0,
        'completed': 0,
        'canceled': 0,
        'active': 0
    })

    for date_str, snapshots in all_data.items():
        for snapshot in snapshots:
            if snapshot['symbol'] != 'HYPE':
                continue

            dt = datetime.fromisoformat(snapshot['timestamp'])

            # Focus on Nov 18, 11:00-13:00 (the pump hours)
            if dt.date() != datetime(2025, 11, 18).date():
                continue

            if not (11 <= dt.hour <= 13):
                continue

            for order in snapshot.get('active_orders', []):
                address = order['address']
                stats = pump_addresses[address]

                # Track timing
                if stats['first_seen'] is None or dt < stats['first_seen']:
                    stats['first_seen'] = dt
                if stats['last_seen'] is None or dt > stats['last_seen']:
                    stats['last_seen'] = dt

                # Track order
                stats['orders'].append({
                    'time': dt,
                    'side': order['side'],
                    'size': order['size'],
                    'status': order['status']
                })

                # Accumulate volumes
                if order['side'] == 'BUY':
                    stats['total_buy'] += order['size']
                else:
                    stats['total_sell'] += order['size']

                # Track status
                if order['status'] == 'completed':
                    stats['completed'] += 1
                elif order['status'] == 'canceled':
                    stats['canceled'] += 1
                elif order['status'] == 'active':
                    stats['active'] += 1

    # Rank by net volume
    ranked = []
    for address, stats in pump_addresses.items():
        net_volume = stats['total_buy'] - stats['total_sell']
        total_orders = len(stats['orders'])
        completion_rate = stats['completed'] / total_orders if total_orders > 0 else 0

        ranked.append({
            'address': address,
            'net_volume': net_volume,
            'total_buy': stats['total_buy'],
            'total_sell': stats['total_sell'],
            'orders': total_orders,
            'completed': stats['completed'],
            'canceled': stats['canceled'],
            'active': stats['active'],
            'completion_rate': completion_rate,
            'first_seen': stats['first_seen'],
            'last_seen': stats['last_seen']
        })

    ranked.sort(key=lambda x: abs(x['net_volume']), reverse=True)

    print("Top 20 Addresses During the Pump:")
    print("-" * 80)

    for i, data in enumerate(ranked[:20], 1):
        direction = "🟢 NET BUYER" if data['net_volume'] > 0 else "🔴 NET SELLER"

        is_validator = "🚨 VALIDATOR!" if data['address'].lower() == VALIDATOR_WHALE.lower() else ""

        print(f"\n{i}. {data['address'][:10]}...{data['address'][-6:]} {is_validator}")
        print(f"   {direction}")
        print(f"   Net Volume: {data['net_volume']:,.0f} HYPE")
        print(f"   Buy: {data['total_buy']:,.0f} | Sell: {data['total_sell']:,.0f}")
        print(f"   Orders: {data['orders']} total")
        print(f"   Status: {data['completed']} completed | {data['canceled']} canceled | {data['active']} active")
        print(f"   Completion Rate: {data['completion_rate'] * 100:.1f}%")
        print(f"   Active: {data['first_seen'].strftime('%H:%M')} - {data['last_seen'].strftime('%H:%M')}")


def analyze_3day_pattern(all_data):
    """Find patterns over 3 days"""
    print("\n" + "=" * 80)
    print("3-DAY PATTERN ANALYSIS")
    print("=" * 80)
    print()

    daily_hype_stats = defaultdict(lambda: {
        'net_flow': 0,
        'buy_volume': 0,
        'sell_volume': 0,
        'unique_addresses': set(),
        'whale_orders': 0
    })

    for date_str, snapshots in all_data.items():
        for snapshot in snapshots:
            if snapshot['symbol'] != 'HYPE':
                continue

            summary = snapshot['summary']
            stats = daily_hype_stats[date_str]

            stats['net_flow'] += summary.get('net_flow', 0)
            stats['buy_volume'] += summary.get('buy_volume', 0)
            stats['sell_volume'] += summary.get('sell_volume', 0)
            stats['whale_orders'] += summary.get('whale_orders', 0)

            for order in snapshot.get('active_orders', []):
                stats['unique_addresses'].add(order['address'])

    print("HYPE Daily Summary:")
    print("-" * 80)

    for date_str in sorted(daily_hype_stats.keys()):
        stats = daily_hype_stats[date_str]

        day_name = datetime.strptime(date_str, '%Y-%m-%d').strftime('%A')
        direction = "🟢 ACCUMULATION" if stats['net_flow'] > 0 else "🔴 DISTRIBUTION"

        print(f"\n{date_str} ({day_name})")
        print(f"  {direction}")
        print(f"  Net Flow: {stats['net_flow']:,.0f} HYPE")
        print(f"  Buy Volume: {stats['buy_volume']:,.0f} HYPE")
        print(f"  Sell Volume: {stats['sell_volume']:,.0f} HYPE")
        print(f"  Unique Addresses: {len(stats['unique_addresses'])}")
        print(f"  Whale Orders: {stats['whale_orders']}")


def main():
    """Run complete analysis"""

    # Load data
    all_data = load_all_data()

    if not all_data:
        print("❌ No data found! Check twap_snapshots/ directory")
        return

    # Run analyses
    multi_token_whales, address_volume = find_cross_token_whales(all_data)

    analyze_hype_vs_btc(all_data)

    track_pump_dump_addresses(all_data)

    analyze_3day_pattern(all_data)

    # Summary
    print("\n" + "=" * 80)
    print("KEY FINDINGS SUMMARY")
    print("=" * 80)
    print()
    print("✅ Loaded multi-day, multi-token data")
    print(f"✅ Found {len(multi_token_whales)} cross-token traders")
    print("✅ Identified addresses active during Nov 18 pump")
    print("✅ Tracked HYPE behavior vs BTC")
    print()
    print("💡 Next steps:")
    print("   - Check if pump addresses are still active now")
    print("   - Compare their behavior across different tokens")
    print("   - Track completion rates for spoofing detection")
    print()


if __name__ == "__main__":
    main()