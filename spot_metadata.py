#!/usr/bin/env python3
"""
Verify the exact mapping between universe index and tokens
"""
import logging
from api_client.hyperliquid_client import HyperliquidClient

logging.basicConfig(level=logging.INFO, format='%(message)s')

hl = HyperliquidClient()

meta = hl.get_spot_meta()
spot_ctx = hl._make_request("spotMetaAndAssetCtxs", {})

if meta and spot_ctx:
    contexts = spot_ctx[1]
    token_map = {t.get("index"): t.get("name") for t in meta.get("tokens", [])}
    universe = meta.get("universe", [])

    print("=" * 80)
    print("VERIFYING MAPPING: Universe Index -> Token Name")
    print("=" * 80)

    # Check @142 specifically (the $70M volume one)
    print("\nChecking @142 (shows $70M volume in API):")
    print(f"  Universe length: {len(universe)}")

    if 142 < len(universe):
        entry = universe[142]
        tokens = entry.get("tokens", [])
        name = entry.get("name", "")

        print(f"  Universe entry: {entry}")

        if tokens and len(tokens) >= 2:
            base_idx = tokens[0]
            quote_idx = tokens[1]
            base_name = token_map.get(base_idx, f"#{base_idx}")
            quote_name = token_map.get(quote_idx, f"#{quote_idx}")

            print(f"  Base token index: {base_idx} = {base_name}")
            print(f"  Quote token index: {quote_idx} = {quote_name}")
            print(f"  Pair: {base_name}/{quote_name}")

            # Now find this in contexts
            for ctx in contexts:
                if ctx.get("coin") == "@142":
                    volume = float(ctx.get("dayNtlVlm", 0))
                    price = float(ctx.get("markPx", 0))
                    print(f"\n  Context data:")
                    print(f"    Volume: ${volume:,.2f}")
                    print(f"    Price: ${price:.6f}")

    # Now check where VORTX actually is
    print("\n" + "=" * 80)
    print("Finding VORTX in the universe:")
    print("=" * 80)

    vortx_token_idx = None
    for t in meta.get("tokens", []):
        if t.get("name") == "VORTX":
            vortx_token_idx = t.get("index")
            print(f"VORTX token index: {vortx_token_idx}")
            break

    if vortx_token_idx:
        print(f"\nSearching for universe entries with VORTX (token {vortx_token_idx}):")
        for i, entry in enumerate(universe):
            tokens = entry.get("tokens", [])
            if tokens and tokens[0] == vortx_token_idx:
                quote_idx = tokens[1] if len(tokens) > 1 else None
                quote_name = token_map.get(quote_idx, f"#{quote_idx}")

                # Get volume
                for ctx in contexts:
                    if ctx.get("coin") == f"@{i}":
                        volume = float(ctx.get("dayNtlVlm", 0))
                        price = float(ctx.get("markPx", 0))
                        print(f"  @{i}: VORTX/{quote_name} - ${volume:,.2f} volume, ${price:.6f} price")