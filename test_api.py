#!/usr/bin/env python3
"""Quick test for get_meta_and_asset_ctxs"""

import requests
from typing import Optional, Dict


def get_meta_and_asset_ctxs() -> Optional[Dict]:
    """Get perp metadata + asset contexts"""
    url = "https://api.hyperliquid.xyz/info"

    try:
        response = requests.post(
            url,
            json={"type": "metaAndAssetCtxs"},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
    except Exception as e:
        print(f"Error: {e}")
        return None

    if not result or len(result) < 2:
        print("Invalid response")
        return None

    meta = result[0]
    raw_ctxs = result[1]
    universe = meta.get('universe', [])

    # Zip universe names with contexts
    asset_ctxs = {}
    for i, asset in enumerate(universe):
        name = asset.get('name')
        if name and i < len(raw_ctxs):
            ctx = raw_ctxs[i]
            asset_ctxs[name] = {
                'funding': float(ctx.get('funding') or 0),
                'open_interest': float(ctx.get('openInterest') or 0),
                'day_ntl_vlm': float(ctx.get('dayNtlVlm') or 0),
                'mark_px': float(ctx.get('markPx') or 0),
                'oracle_px': float(ctx.get('oraclePx') or 0),
                'mid_px': float(ctx.get('midPx') or 0) if ctx.get('midPx') else None,
                'prev_day_px': float(ctx.get('prevDayPx') or 0),
                'premium': float(ctx.get('premium') or 0) if ctx.get('premium') else None,
                'is_delisted': asset.get('isDelisted', False),
                'max_leverage': asset.get('maxLeverage', 0),
            }

    return {
        'meta': meta,
        'asset_ctxs': asset_ctxs
    }


def format_value(value: float) -> str:
    """Format USD value"""
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    elif value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.0f}"


def main():
    print("Fetching market data...")
    data = get_meta_and_asset_ctxs()

    if not data:
        print("Failed to fetch data")
        return

    asset_ctxs = data['asset_ctxs']

    # Filter active (not delisted) assets
    active = {k: v for k, v in asset_ctxs.items() if not v['is_delisted']}

    # Sort by 24h volume
    sorted_assets = sorted(
        active.items(),
        key=lambda x: x[1]['day_ntl_vlm'],
        reverse=True
    )

    print(f"\nLoaded {len(active)} active perps ({len(asset_ctxs) - len(active)} delisted)\n")
    print("=" * 105)
    print(f"{'COIN':<12} {'PRICE':>14} {'24H CHG':>10} {'FUNDING':>12} {'OPEN INT':>14} {'24H VOL':>14}")
    print("=" * 105)

    for coin, ctx in sorted_assets[:25]:
        price = ctx['mark_px']
        prev_price = ctx['prev_day_px']
        funding = ctx['funding']
        oi_usd = ctx['open_interest'] * price
        volume = ctx['day_ntl_vlm']

        # 24h change
        if prev_price > 0:
            change_pct = (price - prev_price) / prev_price * 100
            change_str = f"{change_pct:+.2f}%"
        else:
            change_str = "N/A"

        # Funding annualized
        funding_annual = funding * 3 * 365 * 100
        funding_str = f"{funding_annual:+.1f}%/y"

        # Price formatting
        if price >= 1000:
            price_str = f"${price:,.0f}"
        elif price >= 1:
            price_str = f"${price:.2f}"
        else:
            price_str = f"${price:.4f}"

        print(
            f"{coin:<12} {price_str:>14} {change_str:>10} {funding_str:>12} {format_value(oi_usd):>14} {format_value(volume):>14}")

    # Totals
    total_oi = sum(ctx['open_interest'] * ctx['mark_px'] for ctx in active.values())
    total_vol = sum(ctx['day_ntl_vlm'] for ctx in active.values())

    print("=" * 105)
    print(f"{'TOTAL':<12} {'':<14} {'':<10} {'':<12} {format_value(total_oi):>14} {format_value(total_vol):>14}")
    print("=" * 105)


if __name__ == "__main__":
    main()