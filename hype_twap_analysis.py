#!/usr/bin/env python3
"""
HYPE TWAP Analysis Script
Analyzes whale behavior and accumulation patterns from Nov 1-17, 2025
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import matplotlib.pyplot as plt
import seaborn as sns

# Configuration
DATA_DIR = Path("json_logs")
START_DATE = "20251101"
END_DATE = "20251117"
WHALE_THRESHOLD = 10000  # Orders above this size are considered "whale orders"


def load_daily_data(date_str):
    """Load a single day's JSONL file"""
    filepath = DATA_DIR / f"HYPE_{date_str}.jsonl"

    if not filepath.exists():
        print(f"Warning: {filepath} not found, skipping...")
        return []

    snapshots = []
    with open(filepath, 'r') as f:
        for line in f:
            try:
                snapshot = json.loads(line.strip())
                snapshots.append(snapshot)
            except json.JSONDecodeError as e:
                print(f"Error parsing line in {filepath}: {e}")
                continue

    print(f"Loaded {len(snapshots)} snapshots from {date_str}")
    return snapshots


def generate_date_range(start_date, end_date):
    """Generate list of date strings between start and end"""
    from datetime import datetime, timedelta

    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")

    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)

    return dates


def load_all_data():
    """Load all TWAP data from Nov 1-17"""
    print("Loading TWAP data from November 1-17...")

    dates = generate_date_range(START_DATE, END_DATE)
    all_snapshots = []

    for date_str in dates:
        daily_snapshots = load_daily_data(date_str)
        all_snapshots.extend(daily_snapshots)

    print(f"\nTotal snapshots loaded: {len(all_snapshots)}")
    return all_snapshots


def extract_order_events(snapshots):
    """Extract all unique orders and their lifecycle events"""
    orders_db = {}  # order_id -> order details with history

    print("\nProcessing order events...")

    for snapshot in snapshots:
        timestamp = snapshot['timestamp']

        # Process all active orders in this snapshot
        for order in snapshot.get('active_orders', []):
            address = order['address']
            side = order['side']
            size = order['size']
            status = order['status']
            product_type = order.get('product_type', 'UNKNOWN')

            # Create unique order ID
            order_id = f"{address}_{side}_{size}_{product_type}"

            # Initialize or update order in database
            if order_id not in orders_db:
                orders_db[order_id] = {
                    'address': address,
                    'side': side,
                    'size': size,
                    'product_type': product_type,
                    'first_seen': timestamp,
                    'last_seen': timestamp,
                    'duration_hours': order.get('duration_hours', 0),
                    'final_status': status,
                    'is_whale': size >= WHALE_THRESHOLD,
                    'status_history': [status]
                }
            else:
                # Update existing order
                orders_db[order_id]['last_seen'] = timestamp
                orders_db[order_id]['final_status'] = status
                if status not in orders_db[order_id]['status_history']:
                    orders_db[order_id]['status_history'].append(status)

    print(f"Processed {len(orders_db)} unique orders")
    return orders_db


def calculate_address_stats(orders_db):
    """Calculate statistics per address"""
    address_stats = defaultdict(lambda: {
        'buy_volume': 0,
        'sell_volume': 0,
        'net_volume': 0,
        'buy_count': 0,
        'sell_count': 0,
        'completed_buy': 0,
        'completed_sell': 0,
        'canceled_buy': 0,
        'canceled_sell': 0,
        'whale_orders': 0,
        'total_orders': 0
    })

    print("\nCalculating per-address statistics...")

    for order_id, order in orders_db.items():
        address = order['address']
        side = order['side']
        size = order['size']
        status = order['final_status']

        stats = address_stats[address]
        stats['total_orders'] += 1

        if order['is_whale']:
            stats['whale_orders'] += 1

        if side == 'BUY':
            stats['buy_volume'] += size
            stats['buy_count'] += 1
            stats['net_volume'] += size

            if status == 'completed':
                stats['completed_buy'] += 1
            elif status == 'canceled':
                stats['canceled_buy'] += 1

        elif side == 'SELL':
            stats['sell_volume'] += size
            stats['sell_count'] += 1
            stats['net_volume'] -= size

            if status == 'completed':
                stats['completed_sell'] += 1
            elif status == 'canceled':
                stats['canceled_sell'] += 1

    print(f"Calculated stats for {len(address_stats)} unique addresses")
    return address_stats


def analyze_temporal_patterns(snapshots):
    """Analyze how net flow evolved over time"""
    print("\nAnalyzing temporal patterns...")

    timeline = []

    for snapshot in snapshots:
        timeline.append({
            'timestamp': datetime.fromisoformat(snapshot['timestamp']),
            'buy_volume': snapshot['summary']['buy_volume'],
            'sell_volume': snapshot['summary']['sell_volume'],
            'net_flow': snapshot['summary']['net_flow'],
            'buy_pressure_per_min': snapshot['summary']['buy_pressure_per_min'],
            'sell_pressure_per_min': snapshot['summary']['sell_pressure_per_min'],
            'net_pressure_per_min': snapshot['summary']['net_pressure_per_min'],
            'whale_orders': snapshot['summary']['whale_orders'],
            'total_orders': snapshot['summary']['total_orders'],
            'active_orders': snapshot['summary']['active_orders']
        })

    df = pd.DataFrame(timeline)
    df.set_index('timestamp', inplace=True)

    return df


def print_top_whales(address_stats, n=20):
    """Print top N whales by net volume"""
    print(f"\n{'=' * 80}")
    print(f"TOP {n} WHALES BY NET VOLUME (BUY - SELL)")
    print(f"{'=' * 80}\n")

    # Sort by net volume
    sorted_addresses = sorted(
        address_stats.items(),
        key=lambda x: x[1]['net_volume'],
        reverse=True
    )

    for i, (address, stats) in enumerate(sorted_addresses[:n], 1):
        net_vol = stats['net_volume']
        direction = "🟢 NET BUYER" if net_vol > 0 else "🔴 NET SELLER"

        print(f"{i}. {address[:10]}...{address[-6:]}")
        print(f"   {direction}")
        print(f"   Net Volume: {net_vol:,.2f} HYPE")
        print(f"   Buy Volume: {stats['buy_volume']:,.2f} HYPE ({stats['buy_count']} orders)")
        print(f"   Sell Volume: {stats['sell_volume']:,.2f} HYPE ({stats['sell_count']} orders)")
        print(f"   Whale Orders: {stats['whale_orders']}")
        print(f"   Completed: {stats['completed_buy']} buys, {stats['completed_sell']} sells")
        print(f"   Canceled: {stats['canceled_buy']} buys, {stats['canceled_sell']} sells")
        print()


def print_market_summary(address_stats, timeline_df):
    """Print overall market summary"""
    print(f"\n{'=' * 80}")
    print("MARKET SUMMARY (November 1-17, 2025)")
    print(f"{'=' * 80}\n")

    total_net_buyers = sum(1 for stats in address_stats.values() if stats['net_volume'] > 0)
    total_net_sellers = sum(1 for stats in address_stats.values() if stats['net_volume'] < 0)

    total_buy_vol = sum(stats['buy_volume'] for stats in address_stats.values())
    total_sell_vol = sum(stats['sell_volume'] for stats in address_stats.values())
    total_net_vol = total_buy_vol - total_sell_vol

    print(f"Total Unique Addresses: {len(address_stats)}")
    print(f"Net Buyers: {total_net_buyers}")
    print(f"Net Sellers: {total_net_sellers}")
    print()
    print(f"Total Buy Volume: {total_buy_vol:,.2f} HYPE")
    print(f"Total Sell Volume: {total_sell_vol:,.2f} HYPE")
    print(f"Net Flow: {total_net_vol:,.2f} HYPE")
    print()

    avg_net_flow = timeline_df['net_flow'].mean()
    print(f"Average Net Flow per Snapshot: {avg_net_flow:,.2f} HYPE")
    print(f"Maximum Net Flow: {timeline_df['net_flow'].max():,.2f} HYPE")
    print(f"Minimum Net Flow: {timeline_df['net_flow'].min():,.2f} HYPE")
    print()

    # Accumulation analysis
    accumulation_snapshots = (timeline_df['net_flow'] > 0).sum()
    distribution_snapshots = (timeline_df['net_flow'] < 0).sum()

    print(
        f"Accumulation Snapshots (net flow > 0): {accumulation_snapshots} ({accumulation_snapshots / len(timeline_df) * 100:.1f}%)")
    print(
        f"Distribution Snapshots (net flow < 0): {distribution_snapshots} ({distribution_snapshots / len(timeline_df) * 100:.1f}%)")


def create_visualizations(timeline_df, address_stats):
    """Create visualization plots"""
    print("\nGenerating visualizations...")

    # Set style
    plt.style.use('seaborn-v0_8-darkgrid')
    sns.set_palette("husl")

    # Create figure with subplots
    fig, axes = plt.subplots(3, 2, figsize=(16, 12))
    fig.suptitle('HYPE TWAP Analysis: November 1-17, 2025', fontsize=16, fontweight='bold')

    # 1. Net Flow Over Time
    ax1 = axes[0, 0]
    timeline_df['net_flow'].plot(ax=ax1, linewidth=1.5, color='blue', alpha=0.7)
    ax1.axhline(y=0, color='red', linestyle='--', alpha=0.5)
    ax1.set_title('Net Flow Over Time')
    ax1.set_ylabel('Net Flow (HYPE)')
    ax1.set_xlabel('Date')
    ax1.grid(True, alpha=0.3)

    # 2. Buy vs Sell Pressure
    ax2 = axes[0, 1]
    timeline_df['buy_pressure_per_min'].plot(ax=ax2, label='Buy Pressure', color='green', alpha=0.6)
    timeline_df['sell_pressure_per_min'].plot(ax=ax2, label='Sell Pressure', color='red', alpha=0.6)
    ax2.set_title('Buy vs Sell Pressure per Minute')
    ax2.set_ylabel('Pressure (HYPE/min)')
    ax2.set_xlabel('Date')
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    # 3. Rolling Average Net Flow (24h window)
    ax3 = axes[1, 0]
    timeline_df['net_flow'].rolling(window=60 * 24, min_periods=1).mean().plot(
        ax=ax3, linewidth=2, color='purple'
    )
    ax3.axhline(y=0, color='red', linestyle='--', alpha=0.5)
    ax3.set_title('24-Hour Rolling Average Net Flow')
    ax3.set_ylabel('Net Flow (HYPE)')
    ax3.set_xlabel('Date')
    ax3.grid(True, alpha=0.3)

    # 4. Whale Orders Over Time
    ax4 = axes[1, 1]
    timeline_df['whale_orders'].plot(ax=ax4, linewidth=1, color='orange', alpha=0.7)
    ax4.set_title('Whale Orders Over Time')
    ax4.set_ylabel('Number of Whale Orders')
    ax4.set_xlabel('Date')
    ax4.grid(True, alpha=0.3)

    # 5. Top 15 Whales by Net Volume
    ax5 = axes[2, 0]
    sorted_whales = sorted(
        address_stats.items(),
        key=lambda x: abs(x[1]['net_volume']),
        reverse=True
    )[:15]

    whale_labels = [f"{addr[:6]}..." for addr, _ in sorted_whales]
    whale_volumes = [stats['net_volume'] for _, stats in sorted_whales]
    colors = ['green' if v > 0 else 'red' for v in whale_volumes]

    ax5.barh(whale_labels, whale_volumes, color=colors, alpha=0.7)
    ax5.set_xlabel('Net Volume (HYPE)')
    ax5.set_title('Top 15 Whales by Net Volume')
    ax5.axvline(x=0, color='black', linestyle='-', linewidth=0.5)
    ax5.grid(True, alpha=0.3, axis='x')

    # 6. Daily Net Flow Summary
    ax6 = axes[2, 1]
    daily_netflow = timeline_df['net_flow'].resample('D').sum()
    daily_netflow.plot(kind='bar', ax=ax6, color=['green' if v > 0 else 'red' for v in daily_netflow])
    ax6.set_title('Daily Net Flow Summary')
    ax6.set_ylabel('Net Flow (HYPE)')
    ax6.set_xlabel('Date')
    ax6.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
    ax6.grid(True, alpha=0.3, axis='y')
    ax6.tick_params(axis='x', rotation=45)

    plt.tight_layout()

    # Save the plot
    output_path = Path('hype_twap_analysis.png')
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"Visualization saved to: {output_path.absolute()}")

    return output_path


def analyze_btc_crash_period(timeline_df):
    """Analyze behavior during BTC crash (Nov 10-17)"""
    print(f"\n{'=' * 80}")
    print("BTC CRASH PERIOD ANALYSIS (November 10-17)")
    print(f"{'=' * 80}\n")

    # Filter for crash period
    crash_start = pd.Timestamp('2025-11-10')
    crash_data = timeline_df[timeline_df.index >= crash_start]

    if len(crash_data) == 0:
        print("No data available for crash period")
        return

    avg_net_flow_crash = crash_data['net_flow'].mean()
    accumulation_rate = (crash_data['net_flow'] > 0).sum() / len(crash_data) * 100

    print(f"Average Net Flow during crash: {avg_net_flow_crash:,.2f} HYPE")
    print(f"Accumulation rate: {accumulation_rate:.1f}% of snapshots had positive net flow")
    print()

    if avg_net_flow_crash > 0:
        print("🟢 FINDING: Net buying pressure during BTC crash!")
        print("   This suggests whales accumulated HYPE while BTC tanked.")
    else:
        print("🔴 FINDING: Net selling pressure during BTC crash")
        print("   Whales were distributing despite relative HYPE strength.")


def main():
    """Main analysis pipeline"""
    print("=" * 80)
    print("HYPE TWAP ANALYSIS")
    print("November 1-17, 2025")
    print("=" * 80)

    # Load data
    snapshots = load_all_data()

    if not snapshots:
        print("\nERROR: No data loaded. Check that json_logs/ directory exists")
        print("Expected format: json_logs/HYPE_YYYYMMDD.jsonl")
        return

    # Extract orders and events
    orders_db = extract_order_events(snapshots)

    # Calculate address statistics
    address_stats = calculate_address_stats(orders_db)

    # Create timeline dataframe
    timeline_df = analyze_temporal_patterns(snapshots)

    # Print analysis
    print_market_summary(address_stats, timeline_df)
    print_top_whales(address_stats, n=20)
    analyze_btc_crash_period(timeline_df)

    # Create visualizations
    viz_path = create_visualizations(timeline_df, address_stats)

    print(f"\n{'=' * 80}")
    print("ANALYSIS COMPLETE")
    print(f"{'=' * 80}")
    print(f"\nVisualization saved to: {viz_path}")
    print("\nKey insights:")
    print("1. Check top whales - are they net buyers or sellers?")
    print("2. Look at the Nov 10-17 period - accumulation or distribution?")
    print("3. Compare whale behavior to retail (small orders)")
    print("4. Check completion rates - serious orders or just probing?")


if __name__ == "__main__":
    main()