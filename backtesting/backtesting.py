#!/usr/bin/env python3
"""
Backtester v2 — TWAP Signal Edge Analysis (Optimized)
======================================================
Tests whether TWAP order detection and pressure signals predict price moves.

KEY OPTIMIZATION: Loads all price data into memory upfront, then does
lookups via binary search. ~100x faster than per-query approach.

Hypotheses tested:
1. TWAP ORDER DIRECTION: When a TWAP BUY is detected, price increases over next N hours
2. NET PRESSURE: When net_pressure spikes, price follows the direction
3. PRESSURE + VOLUME: Pressure signals with more unique addresses are stronger

Usage:
    python3 backtesting.py                    # Run all tests
    python3 backtesting.py --test orders      # Test TWAP orders only
    python3 backtesting.py --test pressure    # Test pressure only
    python3 backtesting.py --coin BTC         # Filter by coin
"""
import sys
import argparse
import sqlite3
import bisect
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

DB_PATH = Path('data/twap.db')

# Timeframes to measure (in minutes)
TIMEFRAMES = [10, 30, 60, 240, 720, 1440, 2880]
TIMEFRAME_LABELS = ['10m', '30m', '1h', '4h', '12h', '24h', '48h']

# Filter out non-crypto symbols
EXCLUDE_PREFIXES = ('xyz:', 'UNKNOWN', 'vntl:')


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# PRICE CACHE — Load once, lookup fast
# =============================================================================

class PriceCache:
    """
    In-memory price cache with binary search lookups.
    Loads all prices from snapshots table once, then O(log n) lookups.
    """

    def __init__(self, conn, coin_filter=None):
        cursor = conn.cursor()

        print("Loading price data into memory...")

        query = "SELECT symbol, timestamp, price FROM snapshots WHERE price > 0"
        params = []

        for prefix in EXCLUDE_PREFIXES:
            query += " AND symbol NOT LIKE ?"
            params.append(f"{prefix}%")

        if coin_filter:
            query += " AND symbol = ?"
            params.append(coin_filter)

        query += " ORDER BY symbol, timestamp"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        # Build per-coin sorted arrays: {symbol: [(timestamp, price), ...]}
        self.data = defaultdict(lambda: ([], []))  # (timestamps, prices)

        for row in rows:
            ts_list, px_list = self.data[row['symbol']]
            ts_list.append(row['timestamp'])
            px_list.append(row['price'])

        total_coins = len(self.data)
        total_prices = sum(len(v[0]) for v in self.data.values())
        print(f"Loaded {total_prices:,} prices for {total_coins} coins")

    def get_price(self, symbol, target_time):
        """
        Get price at or just before target_time. O(log n) via binary search.
        Returns price or None.
        """
        if symbol not in self.data:
            return None

        timestamps, prices = self.data[symbol]

        # Binary search: find rightmost timestamp <= target_time
        idx = bisect.bisect_right(timestamps, target_time) - 1

        if idx < 0:
            return None

        return prices[idx]

    def get_price_range(self, symbol):
        """Get (first_time, last_time) for a symbol."""
        if symbol not in self.data:
            return None, None
        timestamps = self.data[symbol][0]
        return timestamps[0], timestamps[-1]


# =============================================================================
# PRESSURE CACHE — Load snapshot data for pressure analysis
# =============================================================================

class PressureCache:
    """In-memory cache for pressure signals."""

    def __init__(self, conn, coin_filter=None):
        cursor = conn.cursor()

        print("Loading pressure data into memory...")

        query = """
            SELECT symbol, timestamp, price, net_pressure, unique_addresses
            FROM snapshots
            WHERE price > 0 AND net_pressure != 0
        """
        params = []

        for prefix in EXCLUDE_PREFIXES:
            query += " AND symbol NOT LIKE ?"
            params.append(f"{prefix}%")

        if coin_filter:
            query += " AND symbol = ?"
            params.append(coin_filter)

        query += " ORDER BY symbol, timestamp"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        # Group by symbol
        self.data = defaultdict(list)
        for row in rows:
            self.data[row['symbol']].append({
                'timestamp': row['timestamp'],
                'price': row['price'],
                'net_pressure': row['net_pressure'],
                'unique_addresses': row['unique_addresses'],
            })

        total = sum(len(v) for v in self.data.values())
        print(f"Loaded {total:,} pressure snapshots for {len(self.data)} coins")


# =============================================================================
# HYPOTHESIS 1: TWAP ORDER DIRECTION
# =============================================================================

def test_twap_orders(conn, price_cache, coin_filter=None, min_size=None, min_duration=None):
    cursor = conn.cursor()

    print("=" * 80)
    print("HYPOTHESIS 1: TWAP ORDER DIRECTION")
    print("Do TWAP BUY orders predict price increases?")
    print("=" * 80)

    query = """
        SELECT symbol, side, size, first_seen_at, duration_minutes, address
        FROM orders
        WHERE first_seen_at >= (SELECT MIN(timestamp) FROM snapshots)
        AND first_seen_at <= (SELECT MAX(timestamp) FROM snapshots)
    """
    params = []

    for prefix in EXCLUDE_PREFIXES:
        query += " AND symbol NOT LIKE ?"
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

    results = {label: {'buy': [], 'sell': []} for label in TIMEFRAME_LABELS}
    coin_results = defaultdict(lambda: {label: {'buy': [], 'sell': []} for label in TIMEFRAME_LABELS})

    tested = 0
    skipped = 0

    for order in orders:
        symbol = order['symbol']
        side = order['side'].lower()
        signal_time = order['first_seen_at']

        entry_price = price_cache.get_price(symbol, signal_time)
        if not entry_price:
            skipped += 1
            continue

        tested += 1
        signal_dt = datetime.fromisoformat(signal_time.replace('+00:00', ''))

        for i, minutes in enumerate(TIMEFRAMES):
            target_time = (signal_dt + timedelta(minutes=minutes)).isoformat()
            future_price = price_cache.get_price(symbol, target_time)

            if future_price:
                pct_change = ((future_price - entry_price) / entry_price) * 100
                results[TIMEFRAME_LABELS[i]][side].append(pct_change)
                coin_results[symbol][TIMEFRAME_LABELS[i]][side].append(pct_change)

    print(f"Tested: {tested}, Skipped (no price): {skipped}")

    print_order_results(results)
    print_top_coins(coin_results)

    return results, coin_results


# =============================================================================
# HYPOTHESIS 2: NET PRESSURE SIGNALS
# =============================================================================

def test_pressure_signals(conn, price_cache, pressure_cache, coin_filter=None, threshold_pct=90):
    print("\n" + "=" * 80)
    print(f"HYPOTHESIS 2: NET PRESSURE SIGNALS (threshold: {threshold_pct}th percentile)")
    print("Does extreme buy/sell pressure predict price moves?")
    print("=" * 80)

    results = {label: {'buy_pressure': [], 'sell_pressure': []} for label in TIMEFRAME_LABELS}
    coin_results = defaultdict(lambda: {label: {'buy_pressure': [], 'sell_pressure': []} for label in TIMEFRAME_LABELS})

    total_signals = 0

    for symbol, snapshots in pressure_cache.data.items():
        if len(snapshots) < 100:
            continue

        # Compute percentile thresholds
        pressures = sorted([s['net_pressure'] for s in snapshots])
        high_threshold = pressures[int(len(pressures) * threshold_pct / 100)]
        low_threshold = pressures[int(len(pressures) * (100 - threshold_pct) / 100)]

        if abs(high_threshold) < 0.001 and abs(low_threshold) < 0.001:
            continue

        last_signal_time = None

        for snap in snapshots:
            pressure = snap['net_pressure']

            if not (pressure >= high_threshold or pressure <= low_threshold):
                continue

            signal_dt = datetime.fromisoformat(snap['timestamp'].replace('+00:00', ''))

            # Dedupe: skip within 10 min of last signal
            if last_signal_time and (signal_dt - last_signal_time).total_seconds() < 600:
                continue

            last_signal_time = signal_dt
            entry_price = snap['price']
            direction = 'buy_pressure' if pressure > 0 else 'sell_pressure'

            total_signals += 1

            for i, minutes in enumerate(TIMEFRAMES):
                target_time = (signal_dt + timedelta(minutes=minutes)).isoformat()
                future_price = price_cache.get_price(symbol, target_time)

                if future_price:
                    pct_change = ((future_price - entry_price) / entry_price) * 100
                    results[TIMEFRAME_LABELS[i]][direction].append(pct_change)
                    coin_results[symbol][TIMEFRAME_LABELS[i]][direction].append(pct_change)

    print(f"Total pressure signals found: {total_signals}")

    print_pressure_results(results)
    print_top_pressure_coins(coin_results)

    return results, coin_results


# =============================================================================
# HYPOTHESIS 3: MULTI-ADDRESS PRESSURE
# =============================================================================

def test_pressure_with_addresses(conn, price_cache, pressure_cache, coin_filter=None, min_addresses=3):
    print("\n" + "=" * 80)
    print(f"HYPOTHESIS 3: MULTI-ADDRESS PRESSURE (min addresses: {min_addresses})")
    print("Are pressure signals stronger when multiple whales agree?")
    print("=" * 80)

    single = {label: {'buy': [], 'sell': []} for label in TIMEFRAME_LABELS}
    multi = {label: {'buy': [], 'sell': []} for label in TIMEFRAME_LABELS}

    total_single = 0
    total_multi = 0

    for symbol, snapshots in pressure_cache.data.items():
        if len(snapshots) < 100:
            continue

        pressures = sorted([s['net_pressure'] for s in snapshots])
        high_threshold = pressures[int(len(pressures) * 0.9)]
        low_threshold = pressures[int(len(pressures) * 0.1)]

        if abs(high_threshold) < 0.001 and abs(low_threshold) < 0.001:
            continue

        last_signal_time = None

        for snap in snapshots:
            pressure = snap['net_pressure']

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
                future_price = price_cache.get_price(symbol, target_time)

                if future_price:
                    pct_change = ((future_price - entry_price) / entry_price) * 100
                    target[TIMEFRAME_LABELS[i]][direction].append(pct_change)

    print(f"Single-address signals: {total_single}")
    print(f"Multi-address signals ({min_addresses}+): {total_multi}")

    print(f"\n--- SINGLE ADDRESS (1-{min_addresses - 1} whales) ---")
    print_simple_results(single)

    print(f"\n--- MULTI ADDRESS ({min_addresses}+ whales) ---")
    print_simple_results(multi)


# =============================================================================
# BASELINE
# =============================================================================

def compute_baseline(conn, price_cache, coin_filter=None):
    cursor = conn.cursor()

    print("=" * 80)
    print("BASELINE: Random entry (no signal)")
    print("What does price do over these timeframes with NO signal?")
    print("=" * 80)

    # Sample every 60th snapshot
    query = """
        SELECT symbol, timestamp, price FROM snapshots
        WHERE price > 0 AND id % 60 = 0
    """
    params = []

    for prefix in EXCLUDE_PREFIXES:
        query += " AND symbol NOT LIKE ?"
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
            future_price = price_cache.get_price(sample['symbol'], target_time)

            if future_price:
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
        variance = sum((x - avg) ** 2 for x in data) / n
        std = variance ** 0.5
        print(f"{label:<6} {n:>7} {avg:>+8.3f} {med:>+8.3f} {win:>6.1f}% {std:>8.3f}")

    return results


# =============================================================================
# FORMATTERS
# =============================================================================

def print_order_results(results):
    print("\n" + "-" * 90)
    print(
        f"{'TF':<6} {'BUY n':>6} {'avg%':>8} {'med%':>8} {'win%':>7} | {'SELL n':>6} {'avg%':>8} {'med%':>8} {'win%':>7} | {'EDGE':>7}")
    print("-" * 90)

    for label in TIMEFRAME_LABELS:
        buy = results[label]['buy']
        sell = results[label]['sell']

        if not buy and not sell:
            continue

        bn = len(buy)
        ba = sum(buy) / bn if buy else 0
        bm = sorted(buy)[bn // 2] if buy else 0
        bw = (sum(1 for x in buy if x > 0) / bn * 100) if buy else 0

        sn = len(sell)
        sa = sum(sell) / sn if sell else 0
        sm = sorted(sell)[sn // 2] if sell else 0
        sw = (sum(1 for x in sell if x < 0) / sn * 100) if sell else 0

        edge = ba - sa

        print(
            f"{label:<6} {bn:>6} {ba:>+8.3f} {bm:>+8.3f} {bw:>6.1f}% | {sn:>6} {sa:>+8.3f} {sm:>+8.3f} {sw:>6.1f}% | {edge:>+7.3f}")


def print_top_coins(coin_results, timeframe='4h', top_n=15):
    print(f"\n{'=' * 70}")
    print(f"PER-COIN BREAKDOWN @ {timeframe}")
    print(f"{'=' * 70}")

    stats = []
    for coin, tf_data in coin_results.items():
        d = tf_data[timeframe]
        buy = d['buy']
        sell = d['sell']
        total = len(buy) + len(sell)

        if total < 5:
            continue

        ba = sum(buy) / len(buy) if buy else 0
        sa = sum(sell) / len(sell) if sell else 0
        edge = ba - sa
        bw = (sum(1 for x in buy if x > 0) / len(buy) * 100) if buy else 0
        sw = (sum(1 for x in sell if x < 0) / len(sell) * 100) if sell else 0

        stats.append({'coin': coin, 'total': total, 'buys': len(buy), 'sells': len(sell),
                      'buy_avg': ba, 'sell_avg': sa, 'edge': edge, 'buy_win': bw, 'sell_win': sw})

    stats.sort(key=lambda x: abs(x['edge']), reverse=True)

    print(f"{'COIN':<12} {'N':>5} {'BUY avg%':>9} {'BUY win':>8} {'SELL avg%':>10} {'SELL win':>9} {'EDGE':>8}")
    print("-" * 65)
    for s in stats[:top_n]:
        print(
            f"{s['coin']:<12} {s['total']:>5} {s['buy_avg']:>+9.3f} {s['buy_win']:>7.1f}% {s['sell_avg']:>+10.3f} {s['sell_win']:>8.1f}% {s['edge']:>+8.3f}")


def print_pressure_results(results):
    print("\n" + "-" * 90)
    print(
        f"{'TF':<6} {'BUY_P n':>7} {'avg%':>8} {'med%':>8} {'win%':>7} | {'SELL_P n':>8} {'avg%':>8} {'med%':>8} {'win%':>7} | {'EDGE':>7}")
    print("-" * 90)

    for label in TIMEFRAME_LABELS:
        buy = results[label]['buy_pressure']
        sell = results[label]['sell_pressure']

        if not buy and not sell:
            continue

        bn = len(buy)
        ba = sum(buy) / bn if buy else 0
        bm = sorted(buy)[bn // 2] if buy else 0
        bw = (sum(1 for x in buy if x > 0) / bn * 100) if buy else 0

        sn = len(sell)
        sa = sum(sell) / sn if sell else 0
        sm = sorted(sell)[sn // 2] if sell else 0
        sw = (sum(1 for x in sell if x < 0) / sn * 100) if sell else 0

        edge = ba - sa

        print(
            f"{label:<6} {bn:>7} {ba:>+8.3f} {bm:>+8.3f} {bw:>6.1f}% | {sn:>8} {sa:>+8.3f} {sm:>+8.3f} {sw:>6.1f}% | {edge:>+7.3f}")


def print_top_pressure_coins(coin_results, timeframe='4h', top_n=15):
    print(f"\n{'=' * 60}")
    print(f"PER-COIN PRESSURE BREAKDOWN @ {timeframe}")
    print(f"{'=' * 60}")

    stats = []
    for coin, tf_data in coin_results.items():
        d = tf_data[timeframe]
        buy = d['buy_pressure']
        sell = d['sell_pressure']
        total = len(buy) + len(sell)

        if total < 5:
            continue

        ba = sum(buy) / len(buy) if buy else 0
        sa = sum(sell) / len(sell) if sell else 0
        edge = ba - sa

        stats.append({'coin': coin, 'total': total, 'buy_avg': ba, 'sell_avg': sa, 'edge': edge})

    stats.sort(key=lambda x: abs(x['edge']), reverse=True)

    print(f"{'COIN':<12} {'N':>5} {'BUY_P avg%':>11} {'SELL_P avg%':>12} {'EDGE':>8}")
    print("-" * 52)
    for s in stats[:top_n]:
        print(f"{s['coin']:<12} {s['total']:>5} {s['buy_avg']:>+11.3f} {s['sell_avg']:>+12.3f} {s['edge']:>+8.3f}")


def print_simple_results(results):
    print(f"{'TF':<6} {'BUY n':>7} {'avg%':>8} {'win%':>7} | {'SELL n':>8} {'avg%':>8} {'win%':>7}")
    print("-" * 55)

    for label in TIMEFRAME_LABELS:
        buy = results[label]['buy']
        sell = results[label]['sell']

        bn = len(buy)
        ba = sum(buy) / bn if buy else 0
        bw = (sum(1 for x in buy if x > 0) / bn * 100) if buy else 0

        sn = len(sell)
        sa = sum(sell) / sn if sell else 0
        sw = (sum(1 for x in sell if x < 0) / sn * 100) if sell else 0

        print(f"{label:<6} {bn:>7} {ba:>+8.3f} {bw:>6.1f}% | {sn:>8} {sa:>+8.3f} {sw:>6.1f}%")


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
    parser.add_argument('--min-addresses', type=int, default=3, help='Min addresses for multi-signal')

    args = parser.parse_args()

    print("=" * 80)
    print("TWAP SIGNAL BACKTESTER v2 (optimized)")
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

    # Load caches ONCE
    import time
    t0 = time.time()
    price_cache = PriceCache(conn, coin_filter=args.coin)
    print(f"Price cache loaded in {time.time() - t0:.1f}s")

    if args.test in ('pressure', 'addresses', 'all'):
        t0 = time.time()
        pressure_cache = PressureCache(conn, coin_filter=args.coin)
        print(f"Pressure cache loaded in {time.time() - t0:.1f}s")
    else:
        pressure_cache = None

    print()

    # Run tests
    t0 = time.time()

    if args.test in ('baseline', 'all'):
        compute_baseline(conn, price_cache, coin_filter=args.coin)

    if args.test in ('orders', 'all'):
        test_twap_orders(conn, price_cache, coin_filter=args.coin,
                         min_size=args.min_size, min_duration=args.min_duration)

    if args.test in ('pressure', 'all'):
        test_pressure_signals(conn, price_cache, pressure_cache,
                              coin_filter=args.coin, threshold_pct=args.threshold)

    if args.test in ('addresses', 'all'):
        test_pressure_with_addresses(conn, price_cache, pressure_cache,
                                     coin_filter=args.coin, min_addresses=args.min_addresses)

    conn.close()

    print(f"\n{'=' * 80}")
    print(f"Backtest complete in {time.time() - t0:.1f}s")
    print("=" * 80)


if __name__ == '__main__':
    main()