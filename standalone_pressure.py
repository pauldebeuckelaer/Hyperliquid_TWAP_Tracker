#!/usr/bin/env python3
"""
STANDALONE TWAP Pressure Analysis
No imports from other scripts - completely independent
"""

import json
from datetime import datetime
from collections import defaultdict
import statistics

# Load data
filepath = "twap_snapshots/all_coins_2025-11-16.jsonl"
print(f"Loading: {filepath}\n")

records = []
with open(filepath, 'r') as f:
    for line in f:
        try:
            records.append(json.loads(line.strip()))
        except:
            continue

print(f"Loaded {len(records):,} snapshots\n")

# Filter HYPE only
hype_data = [r for r in records if r['symbol'] == 'HYPE']
print(f"HYPE snapshots: {len(hype_data)}\n")

# Calculate hourly pressure
hourly = defaultdict(lambda: {'buy': [], 'sell': [], 'net': []})

for record in hype_data:
    hour = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')).hour
    s = record['summary']

    hourly[hour]['buy'].append(s.get('buy_pressure_per_min', 0))
    hourly[hour]['sell'].append(s.get('sell_pressure_per_min', 0))
    hourly[hour]['net'].append(s.get('net_pressure_per_min', 0))

# Print results
print("=" * 80)
print("HYPE PRESSURE BY HOUR")
print("=" * 80)
print()
print(f"{'Hour':>4} {'Avg Buy':>10} {'Avg Sell':>10} {'Net':>10} {'Signal':>15}")
print("-" * 55)

for hour in sorted(hourly.keys()):
    avg_buy = statistics.mean(hourly[hour]['buy'])
    avg_sell = statistics.mean(hourly[hour]['sell'])
    avg_net = statistics.mean(hourly[hour]['net'])

    if avg_net > 100:
        signal = "STRONG BUY"
    elif avg_net > 50:
        signal = "BUY"
    elif avg_net > 0:
        signal = "WEAK BUY"
    elif avg_net > -50:
        signal = "WEAK SELL"
    elif avg_net > -100:
        signal = "SELL"
    else:
        signal = "STRONG SELL"

    print(f"{hour:4d} {avg_buy:10.2f} {avg_sell:10.2f} {avg_net:10.2f} {signal:>15}")

print()
print("=" * 80)
print("DONE")
print("=" * 80)