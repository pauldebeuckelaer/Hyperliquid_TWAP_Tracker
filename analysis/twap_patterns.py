#!/usr/bin/env python3
"""
TWAP Pattern Analyzer - Cancellation & Completion Patterns
Usage: python twap_patterns.py <COIN> [date]

Analyzes:
- What price move triggers cancellations?
- At what progress % do orders get canceled?
- Completion rate during up vs down moves
- Are we early or late to the trade?
"""

import json
import sys
from pathlib import Path
from datetime import datetime
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
                except:
                    pass
    return snapshots


def get_coin_files(coin: str, date: str = None) -> list[Path]:
    coin_dir = DEFAULT_LOG_DIR / coin
    if not coin_dir.exists():
        for d in DEFAULT_LOG_DIR.iterdir():
            if d.name.upper() == coin.upper():
                coin_dir = d
                break

    if not coin_dir.exists():
        return []

    if date:
        return sorted(coin_dir.glob(f"*{date}*.jsonl"))
    return sorted(coin_dir.glob("*.jsonl"))


def analyze_patterns(snapshots: list[dict]) -> dict:
    """Analyze cancellation and completion patterns."""

    # Track orders from new -> completed/canceled
    order_tracking = {}  # order_hash -> {start_price, start_time, side, size, ...}

    completed_orders = []
    canceled_orders = []

    for s in snapshots:
        ts = s.get('timestamp', '')
        price = s.get('current_price', 0)

        # Track new orders
        for order in s.get('new_orders', []):
            order_hash = order.get('order_hash', '')
            if order_hash and order_hash not in order_tracking:
                order_tracking[order_hash] = {
                    'start_time': ts,
                    'start_price': price,
                    'side': order.get('side'),
                    'size': order.get('size', 0),
                    'duration_minutes': order.get('duration_minutes', 0),
                    'address': order.get('address', ''),
                }

        # Track completed orders
        for order in s.get('completed_orders', []):
            order_hash = order.get('order_hash', '')
            if order_hash in order_tracking:
                start = order_tracking[order_hash]
                completed_orders.append({
                    'order_hash': order_hash,
                    'side': start['side'],
                    'size': start['size'],
                    'address': start['address'],
                    'start_price': start['start_price'],
                    'end_price': price,
                    'start_time': start['start_time'],
                    'end_time': ts,
                    'duration_minutes': start['duration_minutes'],
                    'progress_at_end': order.get('progress_percent', 100),
                    'price_change_pct': ((price - start['start_price']) / start['start_price'] * 100) if start[
                        'start_price'] else 0,
                })

        # Track canceled orders
        for order in s.get('canceled_orders', []):
            order_hash = order.get('order_hash', '')
            if order_hash in order_tracking:
                start = order_tracking[order_hash]
                canceled_orders.append({
                    'order_hash': order_hash,
                    'side': start['side'],
                    'size': start['size'],
                    'address': start['address'],
                    'start_price': start['start_price'],
                    'end_price': price,
                    'start_time': start['start_time'],
                    'end_time': ts,
                    'duration_minutes': start['duration_minutes'],
                    'progress_at_cancel': order.get('progress_percent', 0),
                    'elapsed_minutes': order.get('elapsed_minutes', 0),
                    'price_change_pct': ((price - start['start_price']) / start['start_price'] * 100) if start[
                        'start_price'] else 0,
                })

    return {
        'completed': completed_orders,
        'canceled': canceled_orders,
        'total_tracked': len(order_tracking),
    }


def analyze_active_orders(snapshots: list[dict]) -> dict:
    """Analyze current active orders to determine if early/late."""
    if not snapshots:
        return {}

    last_snapshot = snapshots[-1]
    active_orders = last_snapshot.get('active_orders', [])

    buy_orders = [o for o in active_orders if o.get('side') == 'BUY']
    sell_orders = [o for o in active_orders if o.get('side') == 'SELL']

    buy_volume = sum(o.get('size', 0) for o in buy_orders)
    sell_volume = sum(o.get('size', 0) for o in sell_orders)

    # Average progress of active orders
    all_progress = [o.get('progress_percent', 0) for o in active_orders]
    avg_progress = sum(all_progress) / len(all_progress) if all_progress else 0

    # Time remaining
    buy_remaining = sum(o.get('time_remaining_minutes', 0) for o in buy_orders)
    sell_remaining = sum(o.get('time_remaining_minutes', 0) for o in sell_orders)

    return {
        'timestamp': last_snapshot.get('timestamp', ''),
        'price': last_snapshot.get('current_price', 0),
        'active_count': len(active_orders),
        'buy_orders': len(buy_orders),
        'sell_orders': len(sell_orders),
        'buy_volume': buy_volume,
        'sell_volume': sell_volume,
        'net_volume': buy_volume - sell_volume,
        'avg_progress': avg_progress,
        'buy_remaining_min': buy_remaining,
        'sell_remaining_min': sell_remaining,
    }


def print_pattern_report(patterns: dict, active: dict, symbol: str) -> None:
    """Print pattern analysis report."""

    completed = patterns['completed']
    canceled = patterns['canceled']

    print(f"\n{'=' * 100}")
    print(f" {symbol} PATTERN ANALYSIS")
    print(f"{'=' * 100}")

    # Overall stats
    total = len(completed) + len(canceled)
    comp_rate = (len(completed) / total * 100) if total else 0

    print(f"\n📊 OVERALL")
    print(f"   Total orders tracked: {total}")
    print(f"   Completed: {len(completed)} ({comp_rate:.1f}%)")
    print(f"   Canceled: {len(canceled)} ({100 - comp_rate:.1f}%)")

    # Cancellation analysis
    if canceled:
        print(f"\n{'=' * 100}")
        print(f" ❌ CANCELLATION PATTERNS")
        print(f"{'=' * 100}")

        # Progress at cancellation
        progress_values = [c['progress_at_cancel'] for c in canceled]
        avg_progress = sum(progress_values) / len(progress_values)

        early_cancel = len([c for c in canceled if c['progress_at_cancel'] < 25])
        mid_cancel = len([c for c in canceled if 25 <= c['progress_at_cancel'] < 75])
        late_cancel = len([c for c in canceled if c['progress_at_cancel'] >= 75])

        print(f"\n   Progress at cancellation:")
        print(f"   Average: {avg_progress:.1f}%")
        print(f"   Early (<25%): {early_cancel} orders")
        print(f"   Mid (25-75%): {mid_cancel} orders")
        print(f"   Late (>75%): {late_cancel} orders")

        # Price move at cancellation
        buy_cancels = [c for c in canceled if c['side'] == 'BUY']
        sell_cancels = [c for c in canceled if c['side'] == 'SELL']

        print(f"\n   Price move when canceled:")

        if buy_cancels:
            buy_price_changes = [c['price_change_pct'] for c in buy_cancels]
            avg_buy_cancel = sum(buy_price_changes) / len(buy_price_changes)
            print(f"   BUY orders canceled at avg price change: {avg_buy_cancel:+.2f}%")

            # How many buys canceled on price drop vs rise?
            buys_canceled_on_drop = len([c for c in buy_cancels if c['price_change_pct'] < -1])
            buys_canceled_on_rise = len([c for c in buy_cancels if c['price_change_pct'] > 1])
            print(f"      Canceled on >1% drop: {buys_canceled_on_drop}")
            print(f"      Canceled on >1% rise: {buys_canceled_on_rise}")

        if sell_cancels:
            sell_price_changes = [c['price_change_pct'] for c in sell_cancels]
            avg_sell_cancel = sum(sell_price_changes) / len(sell_price_changes)
            print(f"   SELL orders canceled at avg price change: {avg_sell_cancel:+.2f}%")

            sells_canceled_on_drop = len([c for c in sell_cancels if c['price_change_pct'] < -1])
            sells_canceled_on_rise = len([c for c in sell_cancels if c['price_change_pct'] > 1])
            print(f"      Canceled on >1% drop: {sells_canceled_on_drop}")
            print(f"      Canceled on >1% rise: {sells_canceled_on_rise}")

    # Completion analysis
    if completed:
        print(f"\n{'=' * 100}")
        print(f" ✅ COMPLETION PATTERNS")
        print(f"{'=' * 100}")

        buy_completes = [c for c in completed if c['side'] == 'BUY']
        sell_completes = [c for c in completed if c['side'] == 'SELL']

        print(f"\n   Completed orders: {len(buy_completes)} BUY, {len(sell_completes)} SELL")

        if buy_completes:
            buy_price_changes = [c['price_change_pct'] for c in buy_completes]
            avg_buy_complete = sum(buy_price_changes) / len(buy_price_changes)
            print(f"   BUY orders completed at avg price change: {avg_buy_complete:+.2f}%")

        if sell_completes:
            sell_price_changes = [c['price_change_pct'] for c in sell_completes]
            avg_sell_complete = sum(sell_price_changes) / len(sell_price_changes)
            print(f"   SELL orders completed at avg price change: {avg_sell_complete:+.2f}%")

    # Current state - Are we early or late?
    if active:
        print(f"\n{'=' * 100}")
        print(f" ⏰ CURRENT STATE - ARE YOU EARLY OR LATE?")
        print(f"{'=' * 100}")

        print(f"\n   Last update: {active['timestamp']}")
        print(f"   Current price: ${active['price']:.4f}")
        print(f"\n   Active orders: {active['active_count']}")
        print(f"   BUY orders: {active['buy_orders']} ({active['buy_volume']:,.0f} volume)")
        print(f"   SELL orders: {active['sell_orders']} ({active['sell_volume']:,.0f} volume)")
        print(f"   Net volume: {active['net_volume']:+,.0f}")
        print(f"\n   Average progress: {active['avg_progress']:.1f}%")

        # Interpretation
        print(f"\n   📍 INTERPRETATION:")

        if active['net_volume'] > 0:
            if active['avg_progress'] < 30:
                print(f"   🟢 EARLY - Net buying just started ({active['avg_progress']:.0f}% through)")
            elif active['avg_progress'] < 70:
                print(f"   🟡 MID - Net buying in progress ({active['avg_progress']:.0f}% through)")
            else:
                print(f"   🔴 LATE - Net buying almost done ({active['avg_progress']:.0f}% through)")
        elif active['net_volume'] < 0:
            if active['avg_progress'] < 30:
                print(f"   ⚠️  EARLY SELL PRESSURE - Consider waiting")
            elif active['avg_progress'] < 70:
                print(f"   ⚠️  SELLING IN PROGRESS - Caution")
            else:
                print(f"   🟡 SELL PRESSURE ENDING - Watch for reversal")
        else:
            print(f"   ➖ NEUTRAL - No clear direction")

        # Time remaining
        if active['buy_remaining_min'] > 0 or active['sell_remaining_min'] > 0:
            print(f"\n   Time remaining:")
            print(f"   BUY orders: {active['buy_remaining_min']} min")
            print(f"   SELL orders: {active['sell_remaining_min']} min")

    print(f"\n{'=' * 100}\n")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    coin = sys.argv[1].upper()
    date = sys.argv[2] if len(sys.argv) > 2 else None

    files = get_coin_files(coin, date)

    if not files:
        print(f"No files found for {coin}")
        sys.exit(1)

    print(f"Loading {len(files)} file(s) for {coin}...")

    all_snapshots = []
    for f in files:
        snapshots = load_jsonl(f)
        all_snapshots.extend(snapshots)
        print(f"  {f.name}: {len(snapshots)} snapshots")

    print(f"Total: {len(all_snapshots)} snapshots")

    patterns = analyze_patterns(all_snapshots)
    active = analyze_active_orders(all_snapshots)
    print_pattern_report(patterns, active, coin)


if __name__ == "__main__":
    main()