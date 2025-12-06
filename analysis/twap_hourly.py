#!/usr/bin/env python3
"""
TWAP Hourly Analyzer - Hour-by-hour breakdown
Usage: python twap_hourly.py <COIN> <date>

Example:
    python twap_hourly.py HYPE 20251201
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Adjust this to your setup
DEFAULT_LOG_DIR = Path(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\allcoins_json_logs")


def load_jsonl(filepath: Path) -> list[dict]:
    """Load all JSON lines from file."""
    snapshots = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return snapshots


def get_coin_file(coin: str, date: str) -> Path | None:
    """Get specific JSONL file for coin and date."""
    coin_dir = DEFAULT_LOG_DIR / coin
    if not coin_dir.exists():
        for d in DEFAULT_LOG_DIR.iterdir():
            if d.name.upper() == coin.upper():
                coin_dir = d
                break

    if not coin_dir.exists():
        return None

    files = list(coin_dir.glob(f"*{date}*.jsonl"))
    return files[0] if files else None


def parse_hour(timestamp: str) -> int:
    """Extract hour from timestamp."""
    try:
        dt = datetime.fromisoformat(timestamp)
        return dt.hour
    except:
        return -1


def analyze_hourly(snapshots: list[dict]) -> dict:
    """Group snapshots by hour and compute stats."""
    hourly = defaultdict(lambda: {
        'snapshots': [],
        'prices': [],
        'new_orders': 0,
        'completed': 0,
        'canceled': 0,
        'buy_volume': 0,
        'sell_volume': 0,
        'whale_orders': 0,
        'addresses': set(),
    })

    for s in snapshots:
        hour = parse_hour(s.get('timestamp', ''))
        if hour < 0:
            continue

        h = hourly[hour]
        h['snapshots'].append(s)
        h['prices'].append(s.get('current_price', 0))

        events = s.get('events', {})
        h['new_orders'] += events.get('new_orders', 0)
        h['completed'] += events.get('completed_orders', 0)
        h['canceled'] += events.get('canceled_orders', 0)

        summary = s.get('summary', {})
        h['buy_volume'] += summary.get('buy_volume', 0)
        h['sell_volume'] += summary.get('sell_volume', 0)
        h['whale_orders'] += summary.get('whale_orders', 0)

        for order in s.get('new_orders', []):
            addr = order.get('address')
            if addr:
                h['addresses'].add(addr)

    return hourly


def print_hourly_report(hourly: dict, symbol: str, date: str) -> None:
    """Print hour-by-hour breakdown."""
    print(f"\n{'=' * 100}")
    print(f" {symbol} HOURLY BREAKDOWN - {date}")
    print(f"{'=' * 100}")

    print(f"\n{'Hour':<6} {'Price':>10} {'Δ%':>8} {'New':>6} {'Done':>6} {'Cancel':>6} {'Net Flow':>14} {'Whales':>8}")
    print("-" * 100)

    prev_price = None
    total_net = 0

    for hour in range(24):
        if hour not in hourly:
            continue

        h = hourly[hour]
        prices = h['prices']
        if not prices:
            continue

        price_avg = sum(prices) / len(prices)
        price_low = min(prices)
        price_high = max(prices)

        # Price change from previous hour
        if prev_price:
            price_change = ((price_avg - prev_price) / prev_price) * 100
        else:
            price_change = 0
        prev_price = price_avg

        net_flow = h['buy_volume'] - h['sell_volume']
        total_net += net_flow

        # Format net flow with color indicator
        if net_flow >= 0:
            net_str = f"+{net_flow:,.0f}"
        else:
            net_str = f"{net_flow:,.0f}"

        # Highlight significant hours
        highlight = ""
        if abs(net_flow) > 10_000_000:
            highlight = " ⚠️" if net_flow < 0 else " 🟢"

        print(
            f"{hour:02d}:00  ${price_avg:>9.2f} {price_change:>+7.2f}% {h['new_orders']:>6} {h['completed']:>6} {h['canceled']:>6} {net_str:>14} {h['whale_orders']:>8}{highlight}")

    print("-" * 100)

    # Summary stats
    all_prices = []
    total_new = total_completed = total_canceled = total_whale = 0
    total_buy = total_sell = 0

    for h in hourly.values():
        all_prices.extend(h['prices'])
        total_new += h['new_orders']
        total_completed += h['completed']
        total_canceled += h['canceled']
        total_whale += h['whale_orders']
        total_buy += h['buy_volume']
        total_sell += h['sell_volume']

    print(
        f"{'TOTAL':<6} {'':>10} {'':>8} {total_new:>6} {total_completed:>6} {total_canceled:>6} {total_net:>+14,.0f} {total_whale:>8}")

    # Find extremes
    print(f"\n📊 KEY MOMENTS:")

    # Biggest sell hour
    worst_hour = min(hourly.keys(), key=lambda h: hourly[h]['buy_volume'] - hourly[h]['sell_volume'])
    worst_flow = hourly[worst_hour]['buy_volume'] - hourly[worst_hour]['sell_volume']
    print(f"   Heaviest selling: {worst_hour:02d}:00 UTC (Net flow: {worst_flow:,.0f})")

    # Biggest buy hour
    best_hour = max(hourly.keys(), key=lambda h: hourly[h]['buy_volume'] - hourly[h]['sell_volume'])
    best_flow = hourly[best_hour]['buy_volume'] - hourly[best_hour]['sell_volume']
    print(f"   Heaviest buying:  {best_hour:02d}:00 UTC (Net flow: +{best_flow:,.0f})")

    # Price low
    lowest_price = min(all_prices)
    for hour in hourly:
        if lowest_price in hourly[hour]['prices']:
            print(f"   Price bottom:     {hour:02d}:00 UTC (${lowest_price:.4f})")
            break

    # Most whale activity
    whale_hour = max(hourly.keys(), key=lambda h: hourly[h]['whale_orders'])
    print(f"   Peak whale activity: {whale_hour:02d}:00 UTC ({hourly[whale_hour]['whale_orders']} whale orders)")

    print(f"\n{'=' * 100}\n")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    coin = sys.argv[1].upper()
    date = sys.argv[2]

    filepath = get_coin_file(coin, date)

    if not filepath:
        print(f"No file found for {coin} on {date}")
        sys.exit(1)

    print(f"Loading: {filepath}")
    snapshots = load_jsonl(filepath)
    print(f"Loaded {len(snapshots):,} snapshots")

    hourly = analyze_hourly(snapshots)
    print_hourly_report(hourly, coin, date)


if __name__ == "__main__":
    main()