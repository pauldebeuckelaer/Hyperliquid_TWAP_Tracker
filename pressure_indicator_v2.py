#!/usr/bin/env python3
"""
HYPE Whale Timing Analysis
Track when specific whales enter/exit positions to detect coordination or conflict
"""

import json
from datetime import datetime, timedelta
from collections import defaultdict
import statistics


def load_jsonl(filepath):
    records = []
    with open(filepath, 'r') as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except:
                continue
    return records


def analyze_whale_timing(records):
    """
    Analyze timing patterns of key HYPE whales
    Focus on the big SPOT seller vs buyer battle
    """

    print("=" * 80)
    print("HYPE WHALE TIMING ANALYSIS")
    print("=" * 80)
    print()

    # Key whales to track
    whales = {
        'SPOT_SELLER': '0x11cce0fe35628e2666556c2637e3425ab3cfce67',  # -3.49M SPOT
        'SPOT_BUYER': '0x7e5b07c55a810efebf038b40e0faa9a83b697e3e',  # +3.47M SPOT
        'PERP_BULL_1': '0xca230e816bdb34a46960c2f978a30a563d1ae9e0',  # +1.18M PERP
        'PERP_BULL_2': '0xbc149b172da4c5da0bfc3a24a876f754e501b782',  # +617k PERP
        'PERP_BEAR': '0x5846ac5619d2a762751b1663d86a402085505765',  # -865k PERP
    }

    print("TRACKING THESE WHALES:")
    for name, addr in whales.items():
        print(f"  {name:15s} {addr}")
    print()

    # Extract HYPE records
    hype_records = [r for r in records if r['symbol'] == 'HYPE']

    # Track whale activity over time
    whale_timeline = {name: [] for name in whales.keys()}

    for record in hype_records:
        timestamp = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))

        for order in record.get('active_orders', []):
            addr = order['address']

            # Check if this is one of our tracked whales
            for whale_name, whale_addr in whales.items():
                if addr == whale_addr:
                    whale_timeline[whale_name].append({
                        'time': timestamp,
                        'side': order['side'],
                        'size': order['size'],
                        'duration': order['duration_hours'],
                        'product_type': order['product_type']
                    })
                    break

    # Analyze presence over time (when are they active?)
    print("=" * 80)
    print("WHALE ACTIVITY WINDOWS")
    print("=" * 80)
    print()

    for whale_name in whales.keys():
        orders = whale_timeline[whale_name]
        if orders:
            times = [o['time'] for o in orders]
            first_seen = min(times)
            last_seen = max(times)
            duration = last_seen - first_seen

            print(f"{whale_name}:")
            print(f"  First active:  {first_seen.strftime('%H:%M:%S')}")
            print(f"  Last active:   {last_seen.strftime('%H:%M:%S')}")
            print(f"  Active period: {duration}")
            print(f"  Total orders:  {len(orders)}")

            # Calculate hourly activity
            hourly_counts = defaultdict(int)
            for order in orders:
                hour = order['time'].hour
                hourly_counts[hour] += 1

            active_hours = sorted(hourly_counts.keys())
            print(f"  Active hours:  {', '.join(str(h) for h in active_hours)}")
            print()

    # Time overlap analysis - do they trade at the same times?
    print("=" * 80)
    print("OVERLAP ANALYSIS: SPOT SELLER vs SPOT BUYER")
    print("=" * 80)
    print()

    seller_times = [o['time'] for o in whale_timeline['SPOT_SELLER']]
    buyer_times = [o['time'] for o in whale_timeline['SPOT_BUYER']]

    # Create hour-by-hour comparison
    seller_hours = defaultdict(list)
    buyer_hours = defaultdict(list)

    for order in whale_timeline['SPOT_SELLER']:
        hour = order['time'].hour
        seller_hours[hour].append(order)

    for order in whale_timeline['SPOT_BUYER']:
        hour = order['time'].hour
        buyer_hours[hour].append(order)

    print("HOUR-BY-HOUR ACTIVITY:")
    print(f"{'Hour':>6s} {'Seller Orders':>14s} {'Seller Vol':>12s} | "
          f"{'Buyer Orders':>13s} {'Buyer Vol':>12s} | {'Status':>15s}")
    print("-" * 90)

    all_hours = sorted(set(seller_hours.keys()) | set(buyer_hours.keys()))

    for hour in all_hours:
        seller_count = len(seller_hours[hour])
        seller_vol = sum(o['size'] for o in seller_hours[hour])
        buyer_count = len(buyer_hours[hour])
        buyer_vol = sum(o['size'] for o in buyer_hours[hour])

        # Determine status
        if seller_count > 0 and buyer_count > 0:
            status = "BOTH ACTIVE"
        elif seller_count > 0:
            status = "SELLER ONLY"
        elif buyer_count > 0:
            status = "BUYER ONLY"
        else:
            status = "NEITHER"

        if seller_count > 0 or buyer_count > 0:
            print(f"{hour:6d} {seller_count:14d} {seller_vol:12,.0f} | "
                  f"{buyer_count:13d} {buyer_vol:12,.0f} | {status:>15s}")

    print()

    # Calculate overlap percentage
    seller_active_hours = set(seller_hours.keys())
    buyer_active_hours = set(buyer_hours.keys())
    overlap_hours = seller_active_hours & buyer_active_hours

    if seller_active_hours:
        overlap_pct = len(overlap_hours) / len(seller_active_hours) * 100
        print(f"Temporal overlap: {len(overlap_hours)}/{len(seller_active_hours)} hours "
              f"({overlap_pct:.1f}% of seller's active hours)")
    print()

    # Detect lead-lag relationship
    print("=" * 80)
    print("LEAD-LAG ANALYSIS")
    print("=" * 80)
    print()

    # Compare minute-by-minute for overlapping hours
    simultaneous_count = 0
    seller_leads_count = 0
    buyer_leads_count = 0

    for record in hype_records:
        timestamp = record['timestamp']

        seller_active = False
        buyer_active = False

        for order in record.get('active_orders', []):
            if order['address'] == whales['SPOT_SELLER']:
                seller_active = True
            if order['address'] == whales['SPOT_BUYER']:
                buyer_active = True

        if seller_active and buyer_active:
            simultaneous_count += 1
        elif seller_active:
            seller_leads_count += 1
        elif buyer_active:
            buyer_leads_count += 1

    total_active = simultaneous_count + seller_leads_count + buyer_leads_count

    print(
        f"Snapshots where BOTH active:       {simultaneous_count:4d} ({simultaneous_count / total_active * 100:5.1f}%)")
    print(
        f"Snapshots where SELLER ONLY:       {seller_leads_count:4d} ({seller_leads_count / total_active * 100:5.1f}%)")
    print(f"Snapshots where BUYER ONLY:        {buyer_leads_count:4d} ({buyer_leads_count / total_active * 100:5.1f}%)")
    print()

    if simultaneous_count > 200:
        print("⚠️  HIGH SIMULTANEITY - Suggests:")
        print("   - Same entity operating both addresses")
        print("   - Or coordinated market making")
        print("   - Or they're reacting to each other in real-time")
    elif seller_leads_count > buyer_leads_count * 1.5:
        print("📊 SELLER LEADS - Suggests:")
        print("   - Seller initiates, buyer responds")
        print("   - Buyer may be defending against dumps")
    elif buyer_leads_count > seller_leads_count * 1.5:
        print("📊 BUYER LEADS - Suggests:")
        print("   - Buyer initiates, seller responds")
        print("   - Seller may be providing exit liquidity")
    else:
        print("⚖️  BALANCED - Suggests:")
        print("   - Independent actors")
        print("   - Natural market dynamics")

    print()

    # Order size patterns over time
    print("=" * 80)
    print("ORDER SIZE EVOLUTION")
    print("=" * 80)
    print()

    for whale_name in ['SPOT_SELLER', 'SPOT_BUYER']:
        orders = whale_timeline[whale_name]
        if orders:
            # Sort by time
            orders_sorted = sorted(orders, key=lambda x: x['time'])

            # Split into quartiles by time
            n = len(orders_sorted)
            q1 = orders_sorted[:n // 4]
            q2 = orders_sorted[n // 4:n // 2]
            q3 = orders_sorted[n // 2:3 * n // 4]
            q4 = orders_sorted[3 * n // 4:]

            print(f"{whale_name}:")
            for i, quartile in enumerate([q1, q2, q3, q4], 1):
                if quartile:
                    sizes = [o['size'] for o in quartile]
                    avg_size = statistics.mean(sizes)
                    med_size = statistics.median(sizes)
                    print(f"  Q{i} (orders {len(quartile):3d}): "
                          f"Avg={avg_size:8,.0f}, Med={med_size:8,.0f}")
            print()

    # Event correlation analysis
    print("=" * 80)
    print("EVENT CORRELATION")
    print("=" * 80)
    print()

    # Track new orders, completions, cancellations for our whales
    whale_events = {name: {'new': 0, 'completed': 0, 'canceled': 0}
                    for name in whales.keys()}

    for record in hype_records:
        for order in record.get('new_orders', []):
            for whale_name, whale_addr in whales.items():
                if order['address'] == whale_addr:
                    whale_events[whale_name]['new'] += 1

        for order in record.get('completed_orders', []):
            for whale_name, whale_addr in whales.items():
                if order['address'] == whale_addr:
                    whale_events[whale_name]['completed'] += 1

        for order in record.get('canceled_orders', []):
            for whale_name, whale_addr in whales.items():
                if order['address'] == whale_addr:
                    whale_events[whale_name]['canceled'] += 1

    print("WHALE ORDER LIFECYCLE:")
    print(f"{'Whale':15s} {'New':>8s} {'Completed':>10s} {'Canceled':>10s} {'Success Rate':>13s}")
    print("-" * 58)

    for whale_name, events in whale_events.items():
        total_finished = events['completed'] + events['canceled']
        success_rate = events['completed'] / total_finished * 100 if total_finished > 0 else 0
        print(f"{whale_name:15s} {events['new']:8d} {events['completed']:10d} "
              f"{events['canceled']:10d} {success_rate:12.1f}%")

    print()


def main():
    filepath = "twap_snapshots/all_coins_2025-11-16.jsonl"

    print(f"Loading data from: {filepath}")
    print()

    records = load_jsonl(filepath)
    print(f"Loaded {len(records):,} snapshots")
    print()

    analyze_whale_timing(records)

    print("=" * 80)
    print("TIMING ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()