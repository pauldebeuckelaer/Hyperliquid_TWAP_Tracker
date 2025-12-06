#!/usr/bin/env python3
"""
TWAP JSON Log Loader - Basic summary stats
Usage: python twap_loader.py [path_to_jsonl_file]

If no file specified, will scan default directory for available coins.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# Adjust this to your setup
DEFAULT_LOG_DIR = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\allcoins_json_logs")


def list_available_files(log_dir: Path) -> list[Path]:
    """Find all JSONL files in coin subdirectories."""
    if not log_dir.exists():
        return []
    # Each coin has its own subdirectory
    return sorted(log_dir.glob("*/*.jsonl"))


def load_jsonl(filepath: str) -> list[dict]:
    """Load all JSON lines from file."""
    snapshots = []
    with open(filepath, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"Warning: Failed to parse line {line_num}: {e}")
    return snapshots


def analyze(snapshots: list[dict]) -> None:
    """Print summary statistics."""
    if not snapshots:
        print("No snapshots found.")
        return

    # Basic counts
    total = len(snapshots)
    first = snapshots[0]
    last = snapshots[-1]
    symbol = first.get('symbol', 'UNKNOWN')

    # Time range
    first_ts = first.get('timestamp', '')
    last_ts = last.get('timestamp', '')

    # Price range
    prices = [s.get('current_price', 0) for s in snapshots if s.get('current_price')]
    price_min = min(prices) if prices else 0
    price_max = max(prices) if prices else 0
    price_start = prices[0] if prices else 0
    price_end = prices[-1] if prices else 0

    # Aggregate events
    total_new = sum(s.get('events', {}).get('new_orders', 0) for s in snapshots)
    total_completed = sum(s.get('events', {}).get('completed_orders', 0) for s in snapshots)
    total_canceled = sum(s.get('events', {}).get('canceled_orders', 0) for s in snapshots)

    # Unique addresses (from all active orders across all snapshots)
    all_addresses = set()
    for s in snapshots:
        for order in s.get('active_orders', []):
            addr = order.get('address')
            if addr:
                all_addresses.add(addr)
        for order in s.get('new_orders', []):
            addr = order.get('address')
            if addr:
                all_addresses.add(addr)

    # Net flow stats
    net_flows = [s.get('summary', {}).get('net_flow', 0) for s in snapshots]
    total_buy_vol = sum(s.get('summary', {}).get('buy_volume', 0) for s in snapshots)
    total_sell_vol = sum(s.get('summary', {}).get('sell_volume', 0) for s in snapshots)

    # Print report
    print(f"\n{'=' * 60}")
    print(f" TWAP SUMMARY: {symbol}")
    print(f"{'=' * 60}")
    print(f"\n📊 DATASET")
    print(f"   Snapshots: {total:,}")
    print(f"   Time range: {first_ts} → {last_ts}")

    print(f"\n💰 PRICE")
    print(f"   Start:  ${price_start:,.4f}")
    print(f"   End:    ${price_end:,.4f}")
    print(f"   Low:    ${price_min:,.4f}")
    print(f"   High:   ${price_max:,.4f}")
    print(f"   Change: {((price_end - price_start) / price_start * 100) if price_start else 0:+.2f}%")

    print(f"\n📋 ORDERS")
    print(f"   New:       {total_new:,}")
    print(f"   Completed: {total_completed:,}")
    print(f"   Canceled:  {total_canceled:,}")
    print(f"   Completion rate: {(total_completed / total_new * 100) if total_new else 0:.1f}%")

    print(f"\n🐋 ADDRESSES")
    print(f"   Unique: {len(all_addresses):,}")

    print(f"\n📈 VOLUME (cumulative across snapshots)")
    print(f"   Buy volume:  {total_buy_vol:,.0f}")
    print(f"   Sell volume: {total_sell_vol:,.0f}")
    print(f"   Net flow:    {total_buy_vol - total_sell_vol:+,.0f}")

    print(f"\n{'=' * 60}\n")


def main():
    if len(sys.argv) < 2:
        # No file specified - show available files
        print(f"Scanning: {DEFAULT_LOG_DIR}\n")
        files = list_available_files(DEFAULT_LOG_DIR)

        if not files:
            print(f"No .jsonl files found in {DEFAULT_LOG_DIR}")
            print("Check the DEFAULT_LOG_DIR path in the script.")
            sys.exit(1)

        # Group by coin
        coins = {}
        for f in files:
            coin = f.parent.name
            if coin not in coins:
                coins[coin] = []
            coins[coin].append(f)

        print(f"Found {len(files)} files across {len(coins)} coins:\n")
        for coin in sorted(coins.keys()):
            coin_files = coins[coin]
            total_mb = sum(f.stat().st_size for f in coin_files) / (1024 * 1024)
            print(f"  {coin}: {len(coin_files)} files ({total_mb:.1f} MB)")

        print(f"\nUsage: python {Path(__file__).name} <COIN/filename.jsonl>")
        print(f"Example: python {Path(__file__).name} HYPE/HYPE_20251201.jsonl")
        sys.exit(0)

    # File specified - load and analyze
    filepath = Path(sys.argv[1])

    # If relative path given, look in default dir
    if not filepath.exists() and not filepath.is_absolute():
        filepath = DEFAULT_LOG_DIR / sys.argv[1]

    if not filepath.exists():
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    print(f"Loading: {filepath}")
    snapshots = load_jsonl(str(filepath))
    print(f"Loaded {len(snapshots):,} snapshots")
    analyze(snapshots)


if __name__ == "__main__":
    main()