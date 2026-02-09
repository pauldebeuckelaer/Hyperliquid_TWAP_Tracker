#!/usr/bin/env python3
"""
Backtester v1 — TWAP Signal Edge Analysis
==========================================
Tests whether TWAP order detection and pressure signals predict price moves.

Hypotheses tested:
1. TWAP ORDER DIRECTION: When a TWAP BUY is detected, price increases over next N hours
2. NET PRESSURE: When net_pressure spikes, price follows the direction
3. PRESSURE + VOLUME: Pressure signals with more unique addresses are stronger

Usage:
    python3 backtest.py                    # Run all tests
    python3 backtest.py --test orders      # Test TWAP orders only
    python3 backtest.py --test pressure    # Test pressure only
    python3 backtest.py --coin BTC         # Filter by coin
    python3 backtest.py --min-size 1.0     # Min order size (BTC units)
"""
import sys
import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

DB_PATH = Path('data/twap.db')

# Timeframes to measure (in minutes)
TIMEFRAMES = [10, 30, 60, 240, 720, 1440, 2880]  # 10m, 30m, 1h, 4h, 12h, 24h, 48h
TIMEFRAME_LABELS = ['10m', '30m', '1h', '4h', '12h', '24h', '48h']

# Filter out non-crypto symbols
EXCLUDE_PREFIXES = ('xyz:', 'UNKNOWN', 'vntl:')


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_price_at_time(cursor, symbol, target_time):
    """
    Get price from snapshots table closest to (but not after) target_time.
    Returns price or None.
    """
    cursor.execute("""
        SELECT price FROM snapshots
        WHERE symbol = ? AND timestamp <= ? AND price > 0
        ORDER BY timestamp DESC
        LIMIT 1
    """, (symbol, target_time))
    row = cursor.fetchone()
    return row[0] if row else None


def get_price_after(cursor, symbol, target_time):
    """
    Get price from snapshots table closest to (but not before) target_time.
    Returns price or None.
    """
    cursor.execute("""
        SELECT price FROM snapshots
        WHERE symbol = ? AND timestamp >= ? AND price > 0
        ORDER BY timestamp ASC
        LIMIT 1
    """, (symbol, target_time))
    row = cursor.fetchone()
    return row[0] if row else None


# =============================================================================
# HYPOTHESIS 1: TWAP ORDER DIRECTION
# =============================================================================

def test_twap_orders(conn, coin_filter=None, min_size=None, min_duration=None):
    """
    Test: Do TWAP orders predict price direction?

    For each TWAP order detected:
    1. Get price at detection time (first_seen_at)
    2. Get price at various intervals after
    3. Check if BUY orders → price up, SELL orders → price down
    """
    cursor = conn.cursor()

    print("=" * 80)
    print("HYPOTHESIS 1: TWAP ORDER DIRECTION")
    print("Do TWAP BUY orders predict price increases?")
    print("=" * 80)

    # Build query
    query = """
        SELECT symbol, side, size, first_seen_at, duration_minutes, product_type, address
        FROM orders
        WHERE first_seen_at >= (SELECT MIN(timestamp) FROM snapshots)
        AND first_seen_at <= (SELECT MAX(timestamp) FROM snapshots)
    """
    params = []

    # Exclude non-crypto
    for prefix in EXCLUDE_PREFIXES:
        query += f" AND symbol NOT LIKE ?"
        params.append(f"{prefix}%")

    if coin_filter:
        query += " AND symbol = ?"
        params.append(coin_filter)

    if min_size:
        query += " AND size >= ?"
        params.append(min_size)

    if min_duration:
        query += " AND duration_minutes >= ?"
        params.append(min_duration)

    query += " ORDER BY first_seen_at"

    cursor.execute(query, params)
    orders = cursor.fetchall()

    print(f"\nTotal orders to test: {len(orders)}")

    # Results storage: {timeframe_label: {'buy': [pct_changes], 'sell': [pct_changes]}}
    results = {label: {'buy': [], 'sell': []} for label in TIMEFRAME_LABELS}

    # Per-coin results
    coin_results = defaultdict(lambda: {label: {'buy': [], 'sell': []} for label in TIMEFRAME_LABELS})

    tested = 0
    skipped_no_price = 0

    for order in orders:
        symbol = order['symbol']
        side = order['side'].lower()
        signal_time = order['first_seen_at']

        # Get entry price
        entry_price = get_price_at_time(cursor, symbol, signal_time)
        if not entry_price or entry_price <= 0:
            skipped_no_price += 1
            continue

        tested += 1
        signal_dt = datetime.fromisoformat(signal_time.replace('+00:00', ''))

        for i, minutes in enumerate(TIMEFRAMES):
            target_time = (signal_dt + timedelta(minutes=minutes)).isoformat()
            future_price = get_price_at_time(cursor, symbol, target_time)

            if future_price and future_price > 0:
                pct_change = ((future_price - entry_price) / entry_price) * 100
                results[TIMEFRAME_LABELS[i]][side].append(pct_change)
                coin_results[symbol][TIMEFRAME_LABELS[i]][side].append(pct_change)

        # Progress
        if tested % 2000 == 0:
            print(f"  Processed {tested}/{len(orders)} orders...")

    print(f"\nTested: {tested} orders, Skipped (no price): {skipped_no_price}")

    # Print results
    print_order_results(results)
    print_top_coins(coin_results)

    return results, coin_results


def print_order_results(results):
    """Print formatted results for order direction test."""
    print("\n" + "-" * 80)
    print(
        f"{'TIMEFRAME':<10} {'BUY→':>6} {'avg%':>8} {'med%':>8} {'win%':>7} | {'SELL→':>6} {'avg%':>8} {'med%':>8} {'win%':>7} | {'EDGE':>7}")
    print("-" * 80)

    for label in TIMEFRAME_LABELS:
        buy_data = results[label]['buy']
        sell_data = results[label]['sell']

        if not buy_data and not sell_data:
            print(
                f"{label:<10} {'--':>6} {'--':>8} {'--':>8} {'--':>7} | {'--':>6} {'--':>8} {'--':>8} {'--':>7} | {'--':>7}")
            continue

        # Buy stats
        buy_n = len(buy_data)
        buy_avg = sum(buy_data) / len(buy_data) if buy_data else 0
        buy_med = sorted(buy_data)[len(buy_data) // 2] if buy_data else 0
        buy_win = (sum(1 for x in buy_data if x > 0) / len(buy_data) * 100) if buy_data else 0

        # Sell stats (for sells, a price DECREASE is a win)
        sell_n = len(sell_data)
        sell_avg = sum(sell_data) / len(sell_data) if sell_data else 0
        sell_med = sorted(sell_data)[len(sell_data) // 2] if sell_data else 0
        sell_win = (sum(1 for x in sell_data if x < 0) / len(sell_data) * 100) if sell_data else 0

        # Edge: buy_avg should be positive, sell_avg should be negative
        # Combined edge = buy_avg - sell_avg (both directions working = bigger edge)
        edge = buy_avg - sell_avg

        print(
            f"{label:<10} {buy_n:>6} {buy_avg:>+8.3f} {buy_med:>+8.3f} {buy_win:>6.1f}% | {sell_n:>6} {sell_avg:>+8.3f} {sell_med:>+8.3f} {sell_win:>6.1f}% | {edge:>+7.3f}")


def print_top_coins(coin_results, timeframe='4h', top_n=15):
    """Print per-coin breakdown for a specific timeframe."""
    print(f"\n{'=' * 60}")
    print(f"PER-COIN BREAKDOWN @ {timeframe}")
    print(f"{'=' * 60}")

    coin_stats = []
    for coin, tf_data in coin_results.items():
        data = tf_data[timeframe]
        buy_data = data['buy']
        sell_data = data['sell']
        total = len(buy_data) + len(sell_data)

        if total < 5:  # Skip coins with too few orders
            continue

        buy_avg = sum(buy_data) / len(buy_data) if buy_data else 0
        sell_avg = sum(sell_data) / len(sell_data) if sell_data else 0
        edge = buy_avg - sell_avg

        buy_win = (sum(1 for x in buy_data if x > 0) / len(buy_data) * 100) if buy_data else 0
        sell_win = (sum(1 for x in sell_data if x < 0) / len(sell_data) * 100) if sell_data else 0

        coin_stats.append({
            'coin': coin,
            'total': total,
            'buys': len(buy_data),
            'sells': len(sell_data),
            'buy_avg': buy_avg,
            'sell_avg': sell_avg,
            'edge': edge,
            'buy_win': buy_win,
            'sell_win': sell_win,
        })

    # Sort by edge (strongest signal first)
    coin_stats.sort(key=lambda x: abs(x['edge']), reverse=True)

    print(f"{'COIN':<12} {'N':>5} {'BUY avg%':>9} {'BUY win':>8} {'SELL avg%':>10} {'SELL win':>9} {'EDGE':>8}")
    print("-" * 65)
    for s in coin_stats[:top_n]:
        print(
            f"{s['coin']:<12} {s['total']:>5} {s['buy_avg']:>+9.3f} {s['buy_win']:>7.1f}% {s['sell_avg']:>+10.3f} {s['sell_win']:>8.1f}% {s['edge']:>+8.3f}")


# =============================================================================
# HYPOTHESIS 2: NET PRESSURE SIGNALS
# =============================================================================

def test_pressure_signals(conn, coin_filter=None, threshold_pct=90):
    """
    Test: Does extreme net_pressure predict price direction?

    Approach:
    1. For each coin, compute pressure distribution
    2. Flag snapshots where pressure exceeds the Nth percentile (positive or negative)
    3. Check what price did after those extreme pressure moments
    """
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print(f"HYPOTHESIS 2: NET PRESSURE SIGNALS (threshold: {threshold_pct}th percentile)")
    print("Does extreme buy/sell pressure predict price moves?")
    print("=" * 80)

    # Get coins with enough data
    query = """
        SELECT symbol, COUNT(*) as cnt
        FROM snapshots
        WHERE price > 0 AND net_pressure != 0
    """
    params = []

    for prefix in EXCLUDE_PREFIXES:
        query += f" AND symbol NOT LIKE ?"
        params.append(f"{prefix}%")

    if coin_filter:
        query += " AND symbol = ?"
        params.append(coin_filter)

    query += " GROUP BY symbol HAVING cnt >= 100 ORDER BY cnt DESC"

    cursor.execute(query, params)
    coins = cursor.fetchall()

    print(f"\nCoins with sufficient data: {len(coins)}")

    # Results
    results = {label: {'buy_pressure': [], 'sell_pressure': []} for label in TIMEFRAME_LABELS}
    coin_results = defaultdict(lambda: {label: {'buy_pressure': [], 'sell_pressure': []} for label in TIMEFRAME_LABELS})

    total_signals = 0

    for coin_row in coins:
        symbol = coin_row['symbol']

        # Get all pressure values to compute percentiles
        cursor.execute("""
            SELECT net_pressure FROM snapshots
            WHERE symbol = ? AND net_pressure != 0 AND price > 0
            ORDER BY net_pressure
        """, (symbol,))
        pressures = [r[0] for r in cursor.fetchall()]

        if len(pressures) < 100:
            continue

        # Compute thresholds
        high_idx = int(len(pressures) * threshold_pct / 100)
        low_idx = int(len(pressures) * (100 - threshold_pct) / 100)

        high_threshold = pressures[high_idx]
        low_threshold = pressures[low_idx]

        # Skip if thresholds are too close to zero (no real signal)
        if abs(high_threshold) < 0.001 and abs(low_threshold) < 0.001:
            continue

        # Get extreme pressure snapshots
        cursor.execute("""
            SELECT timestamp, price, net_pressure, unique_addresses
            FROM snapshots
            WHERE symbol = ? AND price > 0
            AND (net_pressure >= ? OR net_pressure <= ?)
            ORDER BY timestamp
        """, (symbol, high_threshold, low_threshold))

        signals = cursor.fetchall()

        # Deduplicate: skip signals within 10 minutes of each other (same event)
        last_signal_time = None

        for signal in signals:
            signal_time = signal['timestamp']
            signal_dt = datetime.fromisoformat(signal_time.replace('+00:00', ''))

            if last_signal_time:
                if (signal_dt - last_signal_time).total_seconds() < 600:
                    continue

            last_signal_time = signal_dt
            entry_price = signal['price']
            pressure = signal['net_pressure']
            direction = 'buy_pressure' if pressure > 0 else 'sell_pressure'

            total_signals += 1

            for i, minutes in enumerate(TIMEFRAMES):
                target_time = (signal_dt + timedelta(minutes=minutes)).isoformat()
                future_price = get_price_at_time(cursor, symbol, target_time)

                if future_price and future_price > 0:
                    pct_change = ((future_price - entry_price) / entry_price) * 100
                    results[TIMEFRAME_LABELS[i]][direction].append(pct_change)
                    coin_results[symbol][TIMEFRAME_LABELS[i]][direction].append(pct_change)

    print(f"Total pressure signals found: {total_signals}")

    # Print results
    print_pressure_results(results)
    print_top_pressure_coins(coin_results)

    return results, coin_results


def print_pressure_results(results):
    """Print formatted results for pressure test."""
    print("\n" + "-" * 80)
    print(
        f"{'TIMEFRAME':<10} {'BUY_P→':>6} {'avg%':>8} {'med%':>8} {'win%':>7} | {'SELL_P→':>7} {'avg%':>8} {'med%':>8} {'win%':>7} | {'EDGE':>7}")
    print("-" * 80)

    for label in TIMEFRAME_LABELS:
        buy_data = results[label]['buy_pressure']
        sell_data = results[label]['sell_pressure']

        if not buy_data and not sell_data:
            print(
                f"{label:<10} {'--':>6} {'--':>8} {'--':>8} {'--':>7} | {'--':>7} {'--':>8} {'--':>8} {'--':>7} | {'--':>7}")
            continue

        buy_n = len(buy_data)
        buy_avg = sum(buy_data) / len(buy_data) if buy_data else 0
        buy_med = sorted(buy_data)[len(buy_data) // 2] if buy_data else 0
        buy_win = (sum(1 for x in buy_data if x > 0) / len(buy_data) * 100) if buy_data else 0

        sell_n = len(sell_data)
        sell_avg = sum(sell_data) / len(sell_data) if sell_data else 0
        sell_med = sorted(sell_data)[len(sell_data) // 2] if sell_data else 0
        sell_win = (sum(1 for x in sell_data if x < 0) / len(sell_data) * 100) if sell_data else 0

        edge = buy_avg - sell_avg

        print(
            f"{label:<10} {buy_n:>6} {buy_avg:>+8.3f} {buy_med:>+8.3f} {buy_win:>6.1f}% | {sell_n:>7} {sell_avg:>+8.3f} {sell_med:>+8.3f} {sell_win:>6.1f}% | {edge:>+7.3f}")


def print_top_pressure_coins(coin_results, timeframe='4h', top_n=15):
    """Print per-coin breakdown for pressure test."""
    print(f"\n{'=' * 60}")
    print(f"PER-COIN PRESSURE BREAKDOWN @ {timeframe}")
    print(f"{'=' * 60}")

    coin_stats = []
    for coin, tf_data in coin_results.items():
        data = tf_data[timeframe]
        buy_data = data['buy_pressure']
        sell_data = data['sell_pressure']
        total = len(buy_data) + len(sell_data)

        if total < 5:
            continue

        buy_avg = sum(buy_data) / len(buy_data) if buy_data else 0
        sell_avg = sum(sell_data) / len(sell_data) if sell_data else 0
        edge = buy_avg - sell_avg

        coin_stats.append({
            'coin': coin,
            'total': total,
            'buy_n': len(buy_data),
            'sell_n': len(sell_data),
            'buy_avg': buy_avg,
            'sell_avg': sell_avg,
            'edge': edge,
        })

    coin_stats.sort(key=lambda x: abs(x['edge']), reverse=True)

    print(f"{'COIN':<12} {'N':>5} {'BUY_P avg%':>11} {'SELL_P avg%':>12} {'EDGE':>8}")
    print("-" * 52)
    for s in coin_stats[:top_n]:
        print(f"{s['coin']:<12} {s['total']:>5} {s['buy_avg']:>+11.3f} {s['sell_avg']:>+12.3f} {s['edge']:>+8.3f}")


# =============================================================================
# HYPOTHESIS 3: PRESSURE + MULTI-ADDRESS CONFIRMATION
# =============================================================================

def test_pressure_with_addresses(conn, coin_filter=None, min_addresses=3):
    """
    Test: Are pressure signals stronger when multiple addresses contribute?

    Compare:
    - Single-address pressure spikes (1 whale acting alone)
    - Multi-address pressure spikes (consensus among whales)
    """
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print(f"HYPOTHESIS 3: MULTI-ADDRESS PRESSURE (min addresses: {min_addresses})")
    print("Are pressure signals stronger when multiple whales agree?")
    print("=" * 80)

    # Results: single vs multi address
    single = {label: {'buy': [], 'sell': []} for label in TIMEFRAME_LABELS}
    multi = {label: {'buy': [], 'sell': []} for label in TIMEFRAME_LABELS}

    # Get snapshots with extreme pressure AND address count
    query = """
        SELECT symbol, timestamp, price, net_pressure, unique_addresses
        FROM snapshots
        WHERE price > 0 AND net_pressure != 0
    """
    params = []

    for prefix in EXCLUDE_PREFIXES:
        query += f" AND symbol NOT LIKE ?"
        params.append(f"{prefix}%")

    if coin_filter:
        query += " AND symbol = ?"
        params.append(coin_filter)

    query += " ORDER BY symbol, timestamp"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    print(f"Total snapshots to analyze: {len(rows)}")

    # Group by symbol to compute per-coin percentiles
    by_symbol = defaultdict(list)
    for row in rows:
        by_symbol[row['symbol']].append(row)

    total_single = 0
    total_multi = 0

    for symbol, snapshots in by_symbol.items():
        if len(snapshots) < 100:
            continue

        # Compute 90th percentile thresholds
        pressures = sorted([s['net_pressure'] for s in snapshots])
        high_threshold = pressures[int(len(pressures) * 0.9)]
        low_threshold = pressures[int(len(pressures) * 0.1)]

        if abs(high_threshold) < 0.001 and abs(low_threshold) < 0.001:
            continue

        last_signal_time = None

        for snap in snapshots:
            pressure = snap['net_pressure']
            if abs(pressure) < abs(high_threshold) and abs(pressure) < abs(low_threshold):
                # Not extreme enough
                if pressure > 0 and pressure < high_threshold:
                    continue
                if pressure < 0 and pressure > low_threshold:
                    continue

            # Check if this is actually extreme
            if not (pressure >= high_threshold or pressure <= low_threshold):
                continue

            signal_dt = datetime.fromisoformat(snap['timestamp'].replace('+00:00', ''))

            if last_signal_time and (signal_dt - last_signal_time).total_seconds() < 600:
                continue

            last_signal_time = signal_dt
            entry_price = snap['price']
            direction = 'buy' if pressure > 0 else 'sell'
            addresses = snap['unique_addresses']

            target = single if addresses < min_addresses else multi

            if addresses >= min_addresses:
                total_multi += 1
            else:
                total_single += 1

            for i, minutes in enumerate(TIMEFRAMES):
                target_time = (signal_dt + timedelta(minutes=minutes)).isoformat()
                future_price = get_price_at_time(cursor, symbol, target_time)

                if future_price and future_price > 0:
                    pct_change = ((future_price - entry_price) / entry_price) * 100
                    target[TIMEFRAME_LABELS[i]][direction].append(pct_change)

    print(f"Single-address signals: {total_single}")
    print(f"Multi-address signals ({min_addresses}+): {total_multi}")

    print(f"\n--- SINGLE ADDRESS (1-{min_addresses - 1} whales) ---")
    print_pressure_results_simple(single)

    print(f"\n--- MULTI ADDRESS ({min_addresses}+ whales) ---")
    print_pressure_results_simple(multi)


def print_pressure_results_simple(results):
    """Simplified pressure results printer."""
    print(f"{'TF':<6} {'BUY→ n':>7} {'avg%':>8} {'win%':>7} | {'SELL→ n':>8} {'avg%':>8} {'win%':>7}")
    print("-" * 60)

    for label in TIMEFRAME_LABELS:
        buy_data = results[label]['buy']
        sell_data = results[label]['sell']

        buy_n = len(buy_data)
        buy_avg = sum(buy_data) / len(buy_data) if buy_data else 0
        buy_win = (sum(1 for x in buy_data if x > 0) / len(buy_data) * 100) if buy_data else 0

        sell_n = len(sell_data)
        sell_avg = sum(sell_data) / len(sell_data) if sell_data else 0
        sell_win = (sum(1 for x in sell_data if x < 0) / len(sell_data) * 100) if sell_data else 0

        print(
            f"{label:<6} {buy_n:>7} {buy_avg:>+8.3f} {buy_win:>6.1f}% | {sell_n:>8} {sell_avg:>+8.3f} {sell_win:>6.1f}%")


# =============================================================================
# BASELINE COMPARISON
# =============================================================================

def compute_baseline(conn, coin_filter=None):
    """
    Compute random baseline: pick random times and measure price change.
    This tells us what "no signal" looks like.
    """
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print("BASELINE: Random entry (no signal)")
    print("What does price do over these timeframes with NO signal?")
    print("=" * 80)

    # Sample every 60th snapshot (1 per hour roughly) for random baseline
    query = """
        SELECT symbol, timestamp, price FROM snapshots
        WHERE price > 0 AND id % 60 = 0
    """
    params = []

    for prefix in EXCLUDE_PREFIXES:
        query += f" AND symbol NOT LIKE ?"
        params.append(f"{prefix}%")

    if coin_filter:
        query += " AND symbol = ?"
        params.append(coin_filter)

    cursor.execute(query, params)
    samples = cursor.fetchall()

    print(f"Baseline samples: {len(samples)}")

    results = {label: [] for label in TIMEFRAME_LABELS}

    for sample in samples:
        entry_price = sample['price']
        signal_dt = datetime.fromisoformat(sample['timestamp'].replace('+00:00', ''))

        for i, minutes in enumerate(TIMEFRAMES):
            target_time = (signal_dt + timedelta(minutes=minutes)).isoformat()
            future_price = get_price_at_time(cursor, sample['symbol'], target_time)

            if future_price and future_price > 0:
                pct_change = ((future_price - entry_price) / entry_price) * 100
                results[TIMEFRAME_LABELS[i]].append(pct_change)

    print(f"\n{'TF':<6} {'N':>7} {'avg%':>8} {'med%':>8} {'win%':>7} {'std%':>8}")
    print("-" * 45)

    for label in TIMEFRAME_LABELS:
        data = results[label]
        if not data:
            continue

        n = len(data)
        avg = sum(data) / n
        med = sorted(data)[n // 2]
        win = sum(1 for x in data if x > 0) / n * 100

        # Standard deviation
        variance = sum((x - avg) ** 2 for x in data) / n
        std = variance ** 0.5

        print(f"{label:<6} {n:>7} {avg:>+8.3f} {med:>+8.3f} {win:>6.1f}% {std:>8.3f}")

    return results


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='Backtest TWAP signals')
    parser.add_argument('--test', choices=['orders', 'pressure', 'addresses', 'baseline', 'all'],
                        default='all', help='Which test to run')
    parser.add_argument('--coin', type=str, default=None, help='Filter by specific coin')
    parser.add_argument('--min-size', type=float, default=None, help='Min order size')
    parser.add_argument('--min-duration', type=int, default=None, help='Min order duration in minutes')
    parser.add_argument('--threshold', type=int, default=90, help='Pressure percentile threshold')
    parser.add_argument('--min-addresses', type=int, default=3, help='Min addresses for multi-signal test')

    args = parser.parse_args()

    print("=" * 80)
    print(f"TWAP SIGNAL BACKTESTER v1")
    print(f"Database: {DB_PATH}")
    print(f"Run time: {datetime.now().isoformat()}")
    if args.coin:
        print(f"Coin filter: {args.coin}")
    print("=" * 80)

    conn = get_db()

    # Show data overview
    cursor = conn.cursor()
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM snapshots WHERE price > 0")
    row = cursor.fetchone()
    print(f"Price data range: {row[0][:16]} → {row[1][:16]}")

    cursor.execute("""
        SELECT COUNT(*) FROM orders 
        WHERE first_seen_at >= (SELECT MIN(timestamp) FROM snapshots)
    """)
    print(f"Orders in range: {cursor.fetchone()[0]}")
    print()

    if args.test in ('baseline', 'all'):
        compute_baseline(conn, coin_filter=args.coin)

    if args.test in ('orders', 'all'):
        test_twap_orders(conn, coin_filter=args.coin,
                         min_size=args.min_size, min_duration=args.min_duration)

    if args.test in ('pressure', 'all'):
        test_pressure_signals(conn, coin_filter=args.coin, threshold_pct=args.threshold)

    if args.test in ('addresses', 'all'):
        test_pressure_with_addresses(conn, coin_filter=args.coin,
                                     min_addresses=args.min_addresses)

    conn.close()
    print("\n" + "=" * 80)
    print("Backtest complete.")
    print("=" * 80)


if __name__ == '__main__':
    main()