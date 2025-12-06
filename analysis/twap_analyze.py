#!/usr/bin/env python3
"""
TWAP Analyzer - Single coin analysis
Usage: python twap_analyze.py <COIN> [date]

Examples:
    python twap_analyze.py HYPE              # Analyze all HYPE files
    python twap_analyze.py HYPE 20251201     # Analyze specific date
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# Adjust this to your setup
DEFAULT_LOG_DIR = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\allcoins_json_logs")


def load_jsonl(filepath: Path) -> list[dict]:
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


def get_coin_files(coin: str, date: str = None) -> list[Path]:
    """Get JSONL files for a coin, optionally filtered by date."""
    coin_dir = DEFAULT_LOG_DIR / coin
    if not coin_dir.exists():
        # Try uppercase/lowercase variations
        for d in DEFAULT_LOG_DIR.iterdir():
            if d.name.upper() == coin.upper():
                coin_dir = d
                break

    if not coin_dir.exists():
        return []

    if date:
        # Specific date
        pattern = f"*{date}*.jsonl"
    else:
        # All files
        pattern = "*.jsonl"

    return sorted(coin_dir.glob(pattern))


def analyze(snapshots: list[dict], label: str = "") -> dict:
    """Analyze snapshots and return stats dict."""
    if not snapshots:
        return {}

    first = snapshots[0]
    last = snapshots[-1]
    symbol = first.get('symbol', 'UNKNOWN')

    # Time range
    first_ts = first.get('timestamp', '')
    last_ts = last.get('timestamp', '')

    # Price stats
    prices = [s.get('current_price', 0) for s in snapshots if s.get('current_price')]
    price_min = min(prices) if prices else 0
    price_max = max(prices) if prices else 0
    price_start = prices[0] if prices else 0
    price_end = prices[-1] if prices else 0
    price_change_pct = ((price_end - price_start) / price_start * 100) if price_start else 0

    # Aggregate events
    total_new = sum(s.get('events', {}).get('new_orders', 0) for s in snapshots)
    total_completed = sum(s.get('events', {}).get('completed_orders', 0) for s in snapshots)
    total_canceled = sum(s.get('events', {}).get('canceled_orders', 0) for s in snapshots)

    # Unique addresses
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

    # Volume stats
    total_buy_vol = sum(s.get('summary', {}).get('buy_volume', 0) for s in snapshots)
    total_sell_vol = sum(s.get('summary', {}).get('sell_volume', 0) for s in snapshots)

    # Whale orders
    total_whale_orders = sum(s.get('summary', {}).get('whale_orders', 0) for s in snapshots)

    return {
        'symbol': symbol,
        'label': label,
        'snapshots': len(snapshots),
        'first_ts': first_ts,
        'last_ts': last_ts,
        'price_start': price_start,
        'price_end': price_end,
        'price_min': price_min,
        'price_max': price_max,
        'price_change_pct': price_change_pct,
        'total_new': total_new,
        'total_completed': total_completed,
        'total_canceled': total_canceled,
        'completion_rate': (total_completed / total_new * 100) if total_new else 0,
        'unique_addresses': len(all_addresses),
        'total_buy_vol': total_buy_vol,
        'total_sell_vol': total_sell_vol,
        'net_flow': total_buy_vol - total_sell_vol,
        'whale_orders': total_whale_orders,
    }


def print_report(stats: dict) -> None:
    """Print formatted report."""
    if not stats:
        print("No data to report.")
        return

    print(f"\n{'=' * 60}")
    print(f" {stats['symbol']} - {stats['label']}")
    print(f"{'=' * 60}")

    print(f"\n📊 DATASET")
    print(f"   Snapshots: {stats['snapshots']:,}")
    print(f"   From: {stats['first_ts']}")
    print(f"   To:   {stats['last_ts']}")

    print(f"\n💰 PRICE")
    print(f"   Start:  ${stats['price_start']:,.4f}")
    print(f"   End:    ${stats['price_end']:,.4f}")
    print(f"   Low:    ${stats['price_min']:,.4f}")
    print(f"   High:   ${stats['price_max']:,.4f}")
    print(f"   Change: {stats['price_change_pct']:+.2f}%")

    print(f"\n📋 ORDERS")
    print(f"   New:       {stats['total_new']:,}")
    print(f"   Completed: {stats['total_completed']:,}")
    print(f"   Canceled:  {stats['total_canceled']:,}")
    print(f"   Completion rate: {stats['completion_rate']:.1f}%")

    print(f"\n🐋 WHALES")
    print(f"   Whale orders: {stats['whale_orders']:,}")
    print(f"   Unique addresses: {stats['unique_addresses']:,}")

    print(f"\n📈 VOLUME")
    print(f"   Buy:  {stats['total_buy_vol']:,.0f}")
    print(f"   Sell: {stats['total_sell_vol']:,.0f}")
    print(f"   Net:  {stats['net_flow']:+,.0f}")

    print(f"\n{'=' * 60}\n")


def main():
    if len(sys.argv) < 2:
        print(__doc__)

        # Show available coins
        coins = sorted([d.name for d in DEFAULT_LOG_DIR.iterdir() if d.is_dir()])
        print(f"\nAvailable coins ({len(coins)}):")
        print(", ".join(coins[:20]) + ("..." if len(coins) > 20 else ""))
        sys.exit(0)

    coin = sys.argv[1].upper()
    date = sys.argv[2] if len(sys.argv) > 2 else None

    files = get_coin_files(coin, date)

    if not files:
        print(f"No files found for {coin}" + (f" on {date}" if date else ""))
        sys.exit(1)

    print(f"Found {len(files)} file(s) for {coin}")

    # Load and analyze each file
    all_snapshots = []
    for f in files:
        print(f"Loading: {f.name}...")
        snapshots = load_jsonl(f)
        print(f"  → {len(snapshots):,} snapshots")

        # Individual file report
        stats = analyze(snapshots, label=f.stem)
        print_report(stats)

        all_snapshots.extend(snapshots)

    # Combined report if multiple files
    if len(files) > 1:
        print(f"\n{'#' * 60}")
        print(f" COMBINED ANALYSIS")
        print(f"{'#' * 60}")
        combined_stats = analyze(all_snapshots, label=f"All files ({len(files)})")
        print_report(combined_stats)


if __name__ == "__main__":
    main()