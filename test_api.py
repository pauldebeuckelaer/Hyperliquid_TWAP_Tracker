#!/usr/bin/env python3
"""
Investigate potential liquidation victims
"""
from api_client.hyperliquid_client import HyperliquidClient
import requests
import json
from datetime import datetime


def investigate_potential_liquidation(address: str):
    """
    Deep dive into an address that might have been liquidated.
    """
    client = HyperliquidClient({})

    print(f"\n{'=' * 60}")
    print(f"Investigating: {address}")
    print('=' * 60)

    # 1. Current state
    state = client.get_user_state(address)
    if state:
        margin = state.get('marginSummary', {})
        print(f"\nCurrent Account:")
        print(f"  Account Value: ${float(margin.get('accountValue', 0)):,.2f}")
        print(f"  Position Value: ${float(margin.get('totalNtlPos', 0)):,.2f}")
        print(f"  Margin Used: ${float(margin.get('totalMarginUsed', 0)):,.2f}")
        print(f"  Withdrawable: ${float(state.get('withdrawable', 0)):,.2f}")

        # Current positions
        positions = state.get('assetPositions', [])
        active = [p for p in positions if float(p.get('position', {}).get('szi', 0)) != 0]
        print(f"  Open Positions: {len(active)}")

        for pos in active:
            p = pos.get('position', {})
            coin = p.get('coin', '')
            size = float(p.get('szi', 0))
            pnl = float(p.get('unrealizedPnl', 0))
            liq = p.get('liquidationPx', 'N/A')
            print(f"    {coin}: {size:+.4f} | PnL: ${pnl:,.2f} | Liq: {liq}")

    # 2. Recent fills - look for liquidation patterns
    fills = client.get_user_fills(address)
    if fills:
        print(f"\nRecent Fills ({len(fills)} total):")

        # Direction breakdown
        dirs = {}
        for f in fills:
            d = f['dir']
            dirs[d] = dirs.get(d, 0) + 1
        print(f"  Directions: {dirs}")

        # Recent fills with PnL
        print(f"\n  Last 10 fills:")
        for f in fills[:10]:
            ts = datetime.fromtimestamp(f['time'] / 1000).strftime('%m-%d %H:%M:%S')
            pnl = float(f['closedPnl']) if f['closedPnl'] else 0
            size = float(f['sz'])
            price = float(f['px'])
            value = size * price

            pnl_str = f"${pnl:+,.2f}" if pnl else ""
            print(f"    {ts} | {f['coin']:8} {f['dir']:12} ${value:>8,.0f} {pnl_str}")

        # Total closed PnL
        total_pnl = sum(float(f['closedPnl']) for f in fills if f['closedPnl'])
        print(f"\n  Total Closed PnL (recent fills): ${total_pnl:,.2f}")

        # Look for large losses
        big_losses = [f for f in fills if float(f.get('closedPnl', 0)) < -100]
        if big_losses:
            print(f"\n  ⚠️ Large losses found ({len(big_losses)}):")
            for f in big_losses[:5]:
                ts = datetime.fromtimestamp(f['time'] / 1000).strftime('%m-%d %H:%M:%S')
                pnl = float(f['closedPnl'])
                print(f"    {ts} | {f['coin']} {f['dir']} | Loss: ${pnl:,.2f}")

    # 3. Ledger updates - look for liquidation type
    try:
        resp = requests.post(
            'https://api.hyperliquid.xyz/info',
            json={
                "type": "userNonFundingLedgerUpdates",
                "user": address,
                "startTime": 0
            },
            timeout=10
        )

        if resp.status_code == 200:
            ledger = resp.json()

            # Check for liquidation type
            types = {}
            for update in ledger:
                t = update.get('delta', {}).get('type', 'unknown')
                types[t] = types.get(t, 0) + 1

            print(f"\n  Ledger Update Types: {types}")

            if 'liquidation' in types:
                print(f"\n  ⚠️ LIQUIDATION EVENTS FOUND!")
                for update in ledger:
                    if update.get('delta', {}).get('type') == 'liquidation':
                        print(f"    {json.dumps(update, indent=2)}")

    except Exception as e:
        print(f"  Error fetching ledger: {e}")


def main():
    # Investigate the two low-balance accounts
    potential_victims = [
        '0x1128d44265107674af76bd8021695256805a67c9',
        '0x542b8d0cb2509aeff0da83bb6de60df95544c950',
    ]

    for addr in potential_victims:
        investigate_potential_liquidation(addr)


if __name__ == '__main__':
    main()