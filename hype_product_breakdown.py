#!/usr/bin/env python3
"""
Quick check: HYPE PERP vs SPOT analysis
"""

import json
from collections import Counter


def load_jsonl(filepath):
    records = []
    with open(filepath, 'r') as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except:
                continue
    return records


def analyze_hype_product_types(records):
    print("=" * 80)
    print("HYPE PRODUCT TYPE BREAKDOWN")
    print("=" * 80)
    print()

    hype_records = [r for r in records if r['symbol'] == 'HYPE']

    product_type_stats = {
        'PERP': {'buy_orders': 0, 'sell_orders': 0, 'buy_volume': 0, 'sell_volume': 0},
        'SPOT': {'buy_orders': 0, 'sell_orders': 0, 'buy_volume': 0, 'sell_volume': 0}
    }

    # Analyze all HYPE orders
    for record in hype_records:
        for order in record.get('active_orders', []):
            ptype = order.get('product_type', 'UNKNOWN')
            side = order.get('side')
            size = order.get('size', 0)

            if ptype in product_type_stats:
                if side == 'BUY':
                    product_type_stats[ptype]['buy_orders'] += 1
                    product_type_stats[ptype]['buy_volume'] += size
                elif side == 'SELL':
                    product_type_stats[ptype]['sell_orders'] += 1
                    product_type_stats[ptype]['sell_volume'] += size

    print("OVERALL BREAKDOWN:")
    print()
    for ptype, stats in product_type_stats.items():
        total_orders = stats['buy_orders'] + stats['sell_orders']
        total_volume = stats['buy_volume'] + stats['sell_volume']
        net_volume = stats['buy_volume'] - stats['sell_volume']

        print(f"{ptype}:")
        print(f"  Total orders:  {total_orders:,}")
        print(f"  Buy orders:    {stats['buy_orders']:,} ({stats['buy_volume']:,.0f} HYPE)")
        print(f"  Sell orders:   {stats['sell_orders']:,} ({stats['sell_volume']:,.0f} HYPE)")
        print(f"  Total volume:  {total_volume:,.0f} HYPE")
        print(f"  Net flow:      {net_volume:,.0f} HYPE")
        print()

    # Now break down by address and product type
    print("=" * 80)
    print("TOP HYPE TRADERS - PERP vs SPOT BREAKDOWN")
    print("=" * 80)
    print()

    address_product_stats = {}

    for record in hype_records:
        for order in record.get('active_orders', []):
            addr = order['address']
            ptype = order.get('product_type', 'UNKNOWN')
            side = order.get('side')
            size = order.get('size', 0)

            if addr not in address_product_stats:
                address_product_stats[addr] = {
                    'PERP': {'buy': 0, 'sell': 0, 'buy_vol': 0, 'sell_vol': 0},
                    'SPOT': {'buy': 0, 'sell': 0, 'buy_vol': 0, 'sell_vol': 0}
                }

            if side == 'BUY':
                address_product_stats[addr][ptype]['buy'] += 1
                address_product_stats[addr][ptype]['buy_vol'] += size
            elif side == 'SELL':
                address_product_stats[addr][ptype]['sell'] += 1
                address_product_stats[addr][ptype]['sell_vol'] += size

    # Sort by total activity
    sorted_addrs = sorted(
        address_product_stats.items(),
        key=lambda x: sum(s['buy'] + s['sell'] for s in x[1].values()),
        reverse=True
    )[:15]

    for addr, stats in sorted_addrs:
        total_orders = sum(s['buy'] + s['sell'] for s in stats.values())
        print(f"\n{addr} ({total_orders} total orders):")

        for ptype in ['PERP', 'SPOT']:
            perp_spot_stats = stats[ptype]
            orders = perp_spot_stats['buy'] + perp_spot_stats['sell']
            if orders > 0:
                net_vol = perp_spot_stats['buy_vol'] - perp_spot_stats['sell_vol']
                print(f"  {ptype}: {orders:3d} orders | "
                      f"Buy: {perp_spot_stats['buy_vol']:10,.0f} | "
                      f"Sell: {perp_spot_stats['sell_vol']:10,.0f} | "
                      f"Net: {net_vol:10,.0f}")


if __name__ == "__main__":
    filepath = "twap_snapshots/all_coins_2025-11-16.jsonl"
    records = load_jsonl(filepath)
    analyze_hype_product_types(records)