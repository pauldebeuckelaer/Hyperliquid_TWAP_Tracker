#!/usr/bin/env python3
"""
Divergence Deep Dive - ZEREBRO (long) vs ZEC (short)
Analyze the two opposite signals in detail
"""

import json
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
                except json.JSONDecodeError:
                    pass
    return snapshots


def analyze_coin_detailed(coin: str) -> dict:
    """Detailed hourly analysis of a coin."""

    coin_dir = DEFAULT_LOG_DIR / coin
    if not coin_dir.exists():
        return None

    files = sorted(coin_dir.glob("*.jsonl"))
    all_snapshots = []
    for f in files:
        all_snapshots.extend(load_jsonl(f))

    all_snapshots.sort(key=lambda x: x.get('timestamp', ''))

    seen_orders = set()

    results = {
        'coin': coin,
        'hourly': defaultdict(lambda: {
            'prices': [],
            'buy_volume': 0,
            'sell_volume': 0,
            'buy_orders': 0,
            'sell_orders': 0,
            'addresses': set(),
        }),
        'all_orders': [],
        'addresses': defaultdict(lambda: {
            'buy_volume': 0,
            'sell_volume': 0,
            'orders': 0,
        }),
    }

    for s in all_snapshots:
        ts = s.get('timestamp', '')
        try:
            dt = datetime.fromisoformat(ts)
            hour_key = dt.strftime("%b%d-%H")
        except:
            continue

        h = results['hourly'][hour_key]

        price = s.get('current_price', 0)
        if price:
            h['prices'].append(price)

        for order in s.get('new_orders', []):
            order_hash = order.get('order_hash', '')
            if order_hash in seen_orders:
                continue
            seen_orders.add(order_hash)

            addr = order.get('address', '')
            side = order.get('side', '').upper()
            size = order.get('size', 0)
            duration = order.get('duration_minutes', 0)

            order_data = {
                'timestamp': ts,
                'hour': hour_key,
                'address': addr,
                'side': side,
                'size': size,
                'price': price,
                'duration': duration,
            }
            results['all_orders'].append(order_data)

            h['addresses'].add(addr)

            a = results['addresses'][addr]
            a['orders'] += 1

            if side == 'BUY':
                h['buy_volume'] += size
                h['buy_orders'] += 1
                a['buy_volume'] += size
            elif side == 'SELL':
                h['sell_volume'] += size
                h['sell_orders'] += 1
                a['sell_volume'] += size

    return results


def print_analysis(results: dict, signal_type: str) -> None:
    """Print detailed analysis."""

    coin = results['coin']
    hourly = results['hourly']

    print(f"\n{'=' * 130}")
    if signal_type == "LONG":
        print(f" 🟢 {coin} - LONG OPPORTUNITY ANALYSIS")
        print(f"    (Price pumped despite TWAP selling = hidden demand)")
    else:
        print(f" 🔴 {coin} - SHORT OPPORTUNITY ANALYSIS")
        print(f"    (Price dumped despite TWAP buying = hidden supply/distribution)")
    print(f"{'=' * 130}")

    # Hourly breakdown
    print(f"\n{'Hour':<12} {'Price':>12} {'Δ%':>8} {'Buy Vol':>16} {'Sell Vol':>16} {'Net Flow':>16} {'Signal':<15}")
    print("-" * 110)

    total_buy = 0
    total_sell = 0
    prev_price = None
    first_price = None

    for hour_key in sorted(hourly.keys()):
        h = hourly[hour_key]
        if not h['prices']:
            continue

        avg_price = sum(h['prices']) / len(h['prices'])
        if first_price is None:
            first_price = avg_price

        net = h['buy_volume'] - h['sell_volume']
        total_buy += h['buy_volume']
        total_sell += h['sell_volume']

        # Price change from previous hour
        if prev_price:
            pct = ((avg_price - prev_price) / prev_price) * 100
            pct_str = f"{pct:+.1f}%"
        else:
            pct_str = ""
        prev_price = avg_price

        # Cumulative price change from start
        cum_pct = ((avg_price - first_price) / first_price) * 100

        # Determine signal
        signal = ""
        if signal_type == "LONG":
            # For long: look for selling + price up
            if net < 0 and pct_str and float(pct_str.replace('%', '').replace('+', '')) > 0:
                signal = "🔥 BUY SIGNAL"
            elif net < 0:
                signal = "📉 selling"
        else:
            # For short: look for buying + price down
            if net > 0 and pct_str and float(pct_str.replace('%', '').replace('+', '')) < 0:
                signal = "⚠️ SHORT SIGNAL"
            elif net > 0:
                signal = "📈 buying"

        net_str = f"+{net:,.0f}" if net >= 0 else f"{net:,.0f}"

        print(
            f"{hour_key:<12} ${avg_price:>11.4f} {pct_str:>8} {h['buy_volume']:>16,.0f} {h['sell_volume']:>16,.0f} {net_str:>16} {signal:<15}")

    print("-" * 110)
    total_net = total_buy - total_sell
    net_str = f"+{total_net:,.0f}" if total_net >= 0 else f"{total_net:,.0f}"
    print(f"{'TOTAL':<12} {'':>12} {'':>8} {total_buy:>16,.0f} {total_sell:>16,.0f} {net_str:>16}")

    # Calculate overall stats
    all_prices = []
    for h in hourly.values():
        all_prices.extend(h['prices'])

    if all_prices:
        price_start = all_prices[0]
        price_end = all_prices[-1]
        price_change = ((price_end - price_start) / price_start) * 100
        avg_price = sum(all_prices) / len(all_prices)

        print(f"\n📊 SUMMARY")
        print(f"   Price: ${price_start:.4f} → ${price_end:.4f} ({price_change:+.1f}%)")
        print(f"   TWAP Buy Volume:  {total_buy:>16,.0f} (${total_buy * avg_price:,.0f})")
        print(f"   TWAP Sell Volume: {total_sell:>16,.0f} (${total_sell * avg_price:,.0f})")
        print(f"   TWAP Net Flow:    {total_net:>+16,.0f} (${total_net * avg_price:+,.0f})")

        if signal_type == "LONG":
            print(f"\n   💡 INSIGHT: Price went UP {price_change:+.1f}% while TWAP was NET SELLING")
            print(f"      → Hidden buyers absorbed ${abs(total_net * avg_price):,.0f} of TWAP selling")
            print(f"      → Plus additional buying to push price up")
        else:
            print(f"\n   💡 INSIGHT: Price went DOWN {price_change:.1f}% while TWAP was NET BUYING")
            print(f"      → Hidden sellers distributed INTO ${total_net * avg_price:,.0f} of TWAP buying")
            print(f"      → TWAP buyers got trapped")

    # Top addresses
    addr_list = []
    for addr, data in results['addresses'].items():
        net = data['buy_volume'] - data['sell_volume']
        addr_list.append({
            'address': addr,
            'buy': data['buy_volume'],
            'sell': data['sell_volume'],
            'net': net,
            'orders': data['orders'],
        })

    # Show buyers
    buyers = sorted([a for a in addr_list if a['net'] > 0], key=lambda x: x['net'], reverse=True)
    sellers = sorted([a for a in addr_list if a['net'] < 0], key=lambda x: x['net'])

    print(f"\n{'=' * 130}")
    print(f" 🟢 TOP TWAP BUYERS")
    print(f"{'=' * 130}")
    print(f"\n{'Address':<44} {'Buy Vol':>18} {'Sell Vol':>18} {'Net Flow':>18}")
    print("-" * 100)
    for a in buyers[:10]:
        print(f"{a['address']:<44} {a['buy']:>18,.0f} {a['sell']:>18,.0f} {a['net']:>+18,.0f}")

    print(f"\n{'=' * 130}")
    print(f" 🔴 TOP TWAP SELLERS")
    print(f"{'=' * 130}")
    print(f"\n{'Address':<44} {'Sell Vol':>18} {'Buy Vol':>18} {'Net Flow':>18}")
    print("-" * 100)
    for a in sellers[:10]:
        print(f"{a['address']:<44} {a['sell']:>18,.0f} {a['buy']:>18,.0f} {a['net']:>18,.0f}")

    # Order timeline
    print(f"\n{'=' * 130}")
    print(f" 📅 ORDER TIMELINE (all {len(results['all_orders'])} orders)")
    print(f"{'=' * 130}")
    print(f"\n{'Timestamp':<26} {'Side':<6} {'Size':>18} {'Price':>12} {'Duration':>10} {'Address':<20}")
    print("-" * 100)

    for o in sorted(results['all_orders'], key=lambda x: x['timestamp']):
        addr_short = o['address'][:8] + "..." + o['address'][-6:]
        dur_str = f"{o['duration']}min" if o['duration'] else ""
        print(
            f"{o['timestamp']:<26} {o['side']:<6} {o['size']:>18,.0f} ${o['price']:>11.4f} {dur_str:>10} {addr_short:<20}")

    print(f"\n{'=' * 130}\n")


def main():
    print("=" * 130)
    print(" DIVERGENCE ANALYSIS: LONG vs SHORT OPPORTUNITIES")
    print("=" * 130)

    # Analyze ZEREBRO (LONG opportunity)
    print("\n\n" + "█" * 130)
    print(" PART 1: ZEREBRO - THE LONG OPPORTUNITY")
    print("█" * 130)

    zerebro = analyze_coin_detailed("ZEREBRO")
    if zerebro:
        print_analysis(zerebro, "LONG")
    else:
        print("Could not load ZEREBRO data")

    # Analyze ZEC (SHORT opportunity)
    print("\n\n" + "█" * 130)
    print(" PART 2: ZEC - THE SHORT OPPORTUNITY")
    print("█" * 130)

    zec = analyze_coin_detailed("ZEC")
    if zec:
        print_analysis(zec, "SHORT")
    else:
        print("Could not load ZEC data")

    # Trading insights
    print("\n" + "█" * 130)
    print(" 📈 TRADING INSIGHTS")
    print("█" * 130)
    print("""
    LONG SIGNAL (like ZEREBRO):
    ─────────────────────────────
    • TWAP net flow is NEGATIVE (selling)
    • Price is RISING or STABLE
    • = Hidden demand absorbing visible supply
    • Action: BUY - follow the hidden demand

    SHORT SIGNAL (like ZEC):
    ─────────────────────────────
    • TWAP net flow is POSITIVE (buying)
    • Price is FALLING
    • = Hidden supply distributing into TWAP bids
    • Action: SHORT or AVOID - don't be the exit liquidity

    The key metric: DIVERGENCE between TWAP flow direction and price direction
    """)


if __name__ == "__main__":
    main()