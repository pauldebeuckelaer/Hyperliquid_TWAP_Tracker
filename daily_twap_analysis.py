#!/usr/bin/env python3
"""
HYPE TWAP Daily Analysis Script
Analyzes a full day of TWAP monitoring data from JSONL format

Usage:
    python3 daily_twap_analysis.py [filepath]

If no filepath provided, tries: json_logs/HYPE_20251118.jsonl
"""

import json
from datetime import datetime
from collections import defaultdict
import sys
import os

# Default file path
DEFAULT_FILE_PATH = "json_logs/HYPE_20251118.jsonl"


def get_file_path():
    """Get file path from command line args or use default"""
    if len(sys.argv) > 1:
        return sys.argv[1]

    # Try common locations
    possible_paths = [
        DEFAULT_FILE_PATH,
        os.path.join(os.getcwd(), "json_logs/HYPE_20251119.jsonl"),
        "/home/claude/json_logs/HYPE_20251118.jsonl",
        "HYPE_20251118.jsonl"
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return path

    return DEFAULT_FILE_PATH  # Return default even if not found


def parse_timestamp(ts_str):
    """Parse ISO timestamp string to datetime object"""
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))


def analyze_daily_twap_data(filepath):
    """Main analysis function"""

    print("=" * 100)
    print("HYPE TWAP DAILY ANALYSIS")
    print("=" * 100)

    # Load all updates
    updates = []
    try:
        with open(filepath, 'r') as f:
            for line in f:
                if line.strip():
                    updates.append(json.loads(line))
        print(f"\n✓ Loaded {len(updates)} updates from {filepath}")
    except FileNotFoundError:
        print(f"\n✗ ERROR: File not found at {filepath}")
        print("  Please ensure the file exists at the specified path")
        return
    except Exception as e:
        print(f"\n✗ ERROR: {str(e)}")
        return

    if not updates:
        print("No data to analyze!")
        return

    # Basic info
    first_update = updates[0]
    last_update = updates[-1]
    start_time = parse_timestamp(first_update['timestamp'])
    end_time = parse_timestamp(last_update['timestamp'])
    duration_hours = (end_time - start_time).total_seconds() / 3600

    print(f"\nDate: {start_time.strftime('%Y-%m-%d')}")
    print(f"Time Range: {start_time.strftime('%H:%M:%S')} → {end_time.strftime('%H:%M:%S')}")
    print(f"Duration: {duration_hours:.2f} hours")
    print(f"Updates: #{first_update['update_number']} → #{last_update['update_number']}")

    # ============================================================================
    # SECTION 1: SUMMARY METRICS OVER TIME
    # ============================================================================
    print("\n" + "=" * 100)
    print("MARKET METRICS EVOLUTION")
    print("=" * 100)

    # Track key metrics
    active_orders_history = []
    buy_pressure_history = []
    sell_pressure_history = []
    net_flow_history = []
    timestamps = []

    for update in updates:
        timestamps.append(parse_timestamp(update['timestamp']))
        summary = update['summary']
        active_orders_history.append(summary['active_orders'])
        buy_pressure_history.append(summary['buy_pressure_per_min'])
        sell_pressure_history.append(summary['sell_pressure_per_min'])
        net_flow_history.append(summary['net_flow'])

    # Calculate statistics
    print(f"\nActive Orders:")
    print(f"  Peak: {max(active_orders_history)}")
    print(f"  Low:  {min(active_orders_history)}")
    print(f"  Avg:  {sum(active_orders_history) / len(active_orders_history):.1f}")

    print(f"\nBuy Pressure (HYPE/min):")
    print(f"  Peak: {max(buy_pressure_history):.2f}")
    print(f"  Low:  {min(buy_pressure_history):.2f}")
    print(f"  Avg:  {sum(buy_pressure_history) / len(buy_pressure_history):.2f}")

    print(f"\nSell Pressure (HYPE/min):")
    print(f"  Peak: {max(sell_pressure_history):.2f}")
    print(f"  Low:  {min(sell_pressure_history):.2f}")
    print(f"  Avg:  {sum(sell_pressure_history) / len(sell_pressure_history):.2f}")

    print(f"\nNet Flow (cumulative buy - sell):")
    print(f"  Peak: {max(net_flow_history):.2f} HYPE")
    print(f"  Low:  {min(net_flow_history):.2f} HYPE")
    print(f"  Final: {net_flow_history[-1]:.2f} HYPE")

    # ============================================================================
    # SECTION 2: ORDER EVENTS TIMELINE
    # ============================================================================
    print("\n" + "=" * 100)
    print("ORDER EVENTS TIMELINE")
    print("=" * 100)

    total_new = 0
    total_completed = 0
    total_canceled = 0
    total_status_changes = 0

    new_orders_list = []
    completed_orders_list = []
    canceled_orders_list = []

    for update in updates:
        events = update['events']
        total_new += events['new_orders']
        total_completed += events['completed_orders']
        total_canceled += events['canceled_orders']
        total_status_changes += events['status_changes']

        # Collect orders
        if 'new_orders' in update and update['new_orders']:
            for order in update['new_orders']:
                order['timestamp'] = update['timestamp']
                new_orders_list.append(order)

        if 'completed_orders' in update and update['completed_orders']:
            for order in update['completed_orders']:
                order['timestamp'] = update['timestamp']
                completed_orders_list.append(order)

        if 'canceled_orders' in update and update['canceled_orders']:
            for order in update['canceled_orders']:
                order['timestamp'] = update['timestamp']
                canceled_orders_list.append(order)

    print(f"\nTotal Events:")
    print(f"  New Orders:      {total_new}")
    print(f"  Completed:       {total_completed}")
    print(f"  Canceled:        {total_canceled}")
    print(f"  Status Changes:  {total_status_changes}")

    # Show hourly breakdown
    print(f"\n{'Hour':<6} {'New':<6} {'Completed':<12} {'Canceled':<10} {'Changes':<10}")
    print("-" * 50)

    hourly_events = defaultdict(lambda: {'new': 0, 'completed': 0, 'canceled': 0, 'changes': 0})
    for update in updates:
        hour = parse_timestamp(update['timestamp']).strftime('%H:00')
        events = update['events']
        hourly_events[hour]['new'] += events['new_orders']
        hourly_events[hour]['completed'] += events['completed_orders']
        hourly_events[hour]['canceled'] += events['canceled_orders']
        hourly_events[hour]['changes'] += events['status_changes']

    for hour in sorted(hourly_events.keys()):
        e = hourly_events[hour]
        if any([e['new'], e['completed'], e['canceled'], e['changes']]):
            print(f"{hour:<6} {e['new']:<6} {e['completed']:<12} {e['canceled']:<10} {e['changes']:<10}")

    # ============================================================================
    # SECTION 3: NEW ORDERS ANALYSIS
    # ============================================================================
    print("\n" + "=" * 100)
    print("NEW ORDERS PLACED TODAY")
    print("=" * 100)

    if new_orders_list:
        buy_new = [o for o in new_orders_list if o['side'] == 'BUY']
        sell_new = [o for o in new_orders_list if o['side'] == 'SELL']

        print(f"\nBuy Orders Placed: {len(buy_new)}")
        print(f"Sell Orders Placed: {len(sell_new)}")

        if buy_new:
            print(f"\nTop 10 Buy Orders by Size:")
            print(f"{'Time':<10} {'Address':<12} {'Size (HYPE)':<15} {'Duration':<10} {'Type':<8}")
            print("-" * 65)
            for order in sorted(buy_new, key=lambda x: x['size'], reverse=True)[:10]:
                time = parse_timestamp(order['timestamp']).strftime('%H:%M:%S')
                addr = order['address'][:10] + "..."
                print(
                    f"{time:<10} {addr:<12} {order['size']:<15.2f} {order['duration_hours']:<10.1f}h {order['product_type']:<8}")

        if sell_new:
            print(f"\nTop 10 Sell Orders by Size:")
            print(f"{'Time':<10} {'Address':<12} {'Size (HYPE)':<15} {'Duration':<10} {'Type':<8}")
            print("-" * 65)
            for order in sorted(sell_new, key=lambda x: x['size'], reverse=True)[:10]:
                time = parse_timestamp(order['timestamp']).strftime('%H:%M:%S')
                addr = order['address'][:10] + "..."
                print(
                    f"{time:<10} {addr:<12} {order['size']:<15.2f} {order['duration_hours']:<10.1f}h {order['product_type']:<8}")
    else:
        print("\nNo new orders detected in this dataset")

    # ============================================================================
    # SECTION 4: COMPLETED ORDERS ANALYSIS
    # ============================================================================
    print("\n" + "=" * 100)
    print("COMPLETED ORDERS")
    print("=" * 100)

    if completed_orders_list:
        buy_completed = [o for o in completed_orders_list if o['side'] == 'BUY']
        sell_completed = [o for o in completed_orders_list if o['side'] == 'SELL']

        print(f"\nBuy Orders Completed: {len(buy_completed)} (Total: {sum(o['size'] for o in buy_completed):.2f} HYPE)")
        print(
            f"Sell Orders Completed: {len(sell_completed)} (Total: {sum(o['size'] for o in sell_completed):.2f} HYPE)")

        print(f"\nAll Completed Orders:")
        print(f"{'Time':<10} {'Side':<6} {'Address':<12} {'Size (HYPE)':<15} {'Duration':<10}")
        print("-" * 60)
        for order in completed_orders_list:
            time = parse_timestamp(order['timestamp']).strftime('%H:%M:%S')
            addr = order['address'][:10] + "..."
            print(
                f"{time:<10} {order['side']:<6} {addr:<12} {order['size']:<15.2f} {order.get('duration_hours', 'N/A')}")
    else:
        print("\nNo completed orders detected in this dataset")

    # ============================================================================
    # SECTION 5: CANCELED ORDERS ANALYSIS
    # ============================================================================
    print("\n" + "=" * 100)
    print("CANCELED ORDERS")
    print("=" * 100)

    if canceled_orders_list:
        buy_canceled = [o for o in canceled_orders_list if o['side'] == 'BUY']
        sell_canceled = [o for o in canceled_orders_list if o['side'] == 'SELL']

        print(f"\nBuy Orders Canceled: {len(buy_canceled)} (Total: {sum(o['size'] for o in buy_canceled):.2f} HYPE)")
        print(f"Sell Orders Canceled: {len(sell_canceled)} (Total: {sum(o['size'] for o in sell_canceled):.2f} HYPE)")

        print(f"\nAll Canceled Orders:")
        print(f"{'Time':<10} {'Side':<6} {'Address':<12} {'Size (HYPE)':<15} {'Duration':<10}")
        print("-" * 60)
        for order in canceled_orders_list:
            time = parse_timestamp(order['timestamp']).strftime('%H:%M:%S')
            addr = order['address'][:10] + "..."
            print(
                f"{time:<10} {order['side']:<6} {addr:<12} {order['size']:<15.2f} {order.get('duration_hours', 'N/A')}")
    else:
        print("\nNo canceled orders detected in this dataset")

    # ============================================================================
    # SECTION 6: ADDRESS ACTIVITY ANALYSIS
    # ============================================================================
    print("\n" + "=" * 100)
    print("ADDRESS ACTIVITY ANALYSIS")
    print("=" * 100)

    # Track all unique addresses and their activity
    address_activity = defaultdict(lambda: {
        'new_orders': 0,
        'completed_orders': 0,
        'canceled_orders': 0,
        'total_buy_volume': 0,
        'total_sell_volume': 0,
        'orders': []
    })

    for order in new_orders_list:
        addr = order['address']
        address_activity[addr]['new_orders'] += 1
        address_activity[addr]['orders'].append(('NEW', order))
        if order['side'] == 'BUY':
            address_activity[addr]['total_buy_volume'] += order['size']
        else:
            address_activity[addr]['total_sell_volume'] += order['size']

    for order in completed_orders_list:
        addr = order['address']
        address_activity[addr]['completed_orders'] += 1
        address_activity[addr]['orders'].append(('COMPLETED', order))

    for order in canceled_orders_list:
        addr = order['address']
        address_activity[addr]['canceled_orders'] += 1
        address_activity[addr]['orders'].append(('CANCELED', order))

    print(f"\nTotal Unique Addresses: {len(address_activity)}")

    # Most active addresses
    if address_activity:
        print(f"\nTop 10 Most Active Addresses:")
        print(f"{'Address':<14} {'New':<6} {'Done':<6} {'Cancel':<8} {'Buy Vol':<12} {'Sell Vol':<12}")
        print("-" * 70)

        sorted_addresses = sorted(
            address_activity.items(),
            key=lambda x: x[1]['new_orders'] + x[1]['completed_orders'] + x[1]['canceled_orders'],
            reverse=True
        )

        for addr, activity in sorted_addresses[:10]:
            addr_short = addr[:12] + "..."
            print(f"{addr_short:<14} {activity['new_orders']:<6} {activity['completed_orders']:<6} "
                  f"{activity['canceled_orders']:<8} {activity['total_buy_volume']:<12.2f} "
                  f"{activity['total_sell_volume']:<12.2f}")

    # ============================================================================
    # SECTION 7: FINAL STATE SNAPSHOT
    # ============================================================================
    print("\n" + "=" * 100)
    print("FINAL STATE (Last Update)")
    print("=" * 100)

    final_state = last_update
    active_orders = [o for o in final_state['active_orders'] if o['is_active']]

    print(f"\nActive Orders: {len(active_orders)}")

    if active_orders:
        buy_active = [o for o in active_orders if o['side'] == 'BUY']
        sell_active = [o for o in active_orders if o['side'] == 'SELL']

        print(f"\nActive Buy Orders ({len(buy_active)}):")
        print(f"{'Address':<14} {'Size (HYPE)':<15} {'Duration':<12} {'Status':<10} {'Type':<8}")
        print("-" * 70)
        for order in sorted(buy_active, key=lambda x: x['size'], reverse=True):
            addr = order['address'][:12] + "..."
            print(f"{addr:<14} {order['size']:<15.2f} {order['duration_hours']:<12.1f}h "
                  f"{order['status']:<10} {order['product_type']:<8}")

        print(f"\nActive Sell Orders ({len(sell_active)}):")
        print(f"{'Address':<14} {'Size (HYPE)':<15} {'Duration':<12} {'Status':<10} {'Type':<8}")
        print("-" * 70)
        for order in sorted(sell_active, key=lambda x: x['size'], reverse=True):
            addr = order['address'][:12] + "..."
            print(f"{addr:<14} {order['size']:<15.2f} {order['duration_hours']:<12.1f}h "
                  f"{order['status']:<10} {order['product_type']:<8}")

    # ============================================================================
    # SECTION 8: KEY INSIGHTS
    # ============================================================================
    print("\n" + "=" * 100)
    print("KEY INSIGHTS & PATTERNS")
    print("=" * 100)

    # Calculate some key metrics
    avg_buy_pressure = sum(buy_pressure_history) / len(buy_pressure_history)
    avg_sell_pressure = sum(sell_pressure_history) / len(sell_pressure_history)
    buy_sell_ratio = avg_buy_pressure / avg_sell_pressure if avg_sell_pressure > 0 else float('inf')

    total_buy_new = sum(o['size'] for o in new_orders_list if o['side'] == 'BUY')
    total_sell_new = sum(o['size'] for o in new_orders_list if o['side'] == 'SELL')

    print(f"\n1. Market Sentiment:")
    print(f"   - Avg Buy/Sell Pressure Ratio: {buy_sell_ratio:.2f}x")
    print(
        f"   - Final Net Flow: {net_flow_history[-1]:.2f} HYPE ({'Bullish' if net_flow_history[-1] > 0 else 'Bearish'})")
    print(f"   - New Orders: {total_buy_new:.2f} HYPE buy vs {total_sell_new:.2f} HYPE sell")

    print(f"\n2. Order Lifecycle:")
    print(
        f"   - Completion Rate: {total_completed}/{total_new} new orders completed ({100 * total_completed / total_new if total_new > 0 else 0:.1f}%)")
    print(
        f"   - Cancellation Rate: {total_canceled}/{total_new} new orders canceled ({100 * total_canceled / total_new if total_new > 0 else 0:.1f}%)")

    print(f"\n3. Activity Pattern:")
    print(f"   - Peak Active Orders: {max(active_orders_history)}")
    print(
        f"   - Most Active Hour: {max(hourly_events.items(), key=lambda x: sum(x[1].values()))[0] if hourly_events else 'N/A'}")
    print(f"   - Unique Traders: {len(address_activity)}")

    print(f"\n4. Volume Analysis:")
    if completed_orders_list:
        buy_completed_vol = sum(o['size'] for o in completed_orders_list if o['side'] == 'BUY')
        sell_completed_vol = sum(o['size'] for o in completed_orders_list if o['side'] == 'SELL')
        print(f"   - Completed Buy Volume: {buy_completed_vol:.2f} HYPE")
        print(f"   - Completed Sell Volume: {sell_completed_vol:.2f} HYPE")
        print(f"   - Net Completed: {buy_completed_vol - sell_completed_vol:.2f} HYPE")
    else:
        print(f"   - No completed orders to analyze")

    print("\n" + "=" * 100)
    print("ANALYSIS COMPLETE")
    print("=" * 100)


if __name__ == "__main__":
    filepath = get_file_path()
    analyze_daily_twap_data(filepath)