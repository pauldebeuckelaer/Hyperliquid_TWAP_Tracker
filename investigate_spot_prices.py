#!/usr/bin/env python3
"""
Test Script: Investigate Hyperliquid Spot Token Pricing
"""
import requests
import json

API_URL = "https://api.hyperliquid.xyz/info"


def test_endpoint(endpoint_type, params=None):
    """Test a Hyperliquid API endpoint"""
    payload = {"type": endpoint_type}
    if params:
        payload.update(params)

    print(f"\n{'=' * 70}")
    print(f"Testing: {endpoint_type}")
    print(f"Params: {params or 'None'}")
    print('=' * 70)

    try:
        response = requests.post(
            API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        # Pretty print first few items or structure
        print("Response structure:")
        if isinstance(data, dict):
            print(f"Keys: {list(data.keys())}")
            for key, value in list(data.items())[:3]:
                if isinstance(value, list) and value:
                    print(f"  {key}: [{type(value[0]).__name__}] (length: {len(value)})")
                    print(f"    Sample: {value[0]}")
                else:
                    print(f"  {key}: {type(value).__name__}")
        elif isinstance(data, list):
            print(f"List with {len(data)} items")
            if data:
                print(f"First item: {data[0]}")
        else:
            print(f"Type: {type(data)}")
            print(data)

        return data

    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP Error: {e}")
        print(f"Response: {e.response.text if e.response else 'No response'}")
    except Exception as e:
        print(f"❌ Error: {e}")

    return None


def main():
    print("\n" + "=" * 70)
    print("HYPERLIQUID SPOT TOKEN PRICING INVESTIGATION")
    print("=" * 70)

    # Test 1: Check spotMeta endpoint
    print("\n### TEST 1: spotMeta ###")
    spot_meta = test_endpoint("spotMeta")

    # Test 2: Check spotMetaAndAssetCtxs
    print("\n### TEST 2: spotMetaAndAssetCtxs ###")
    spot_meta_ctx = test_endpoint("spotMetaAndAssetCtxs")

    # Test 3: Get all mids (we know this works for perps)
    print("\n### TEST 3: allMids (perps) ###")
    all_mids = test_endpoint("allMids")
    if all_mids:
        print(f"\nSample prices from allMids:")
        for coin, price in list(all_mids.items())[:5]:
            print(f"  {coin}: ${price}")

    # Test 4: Check if there's a spot clearinghouse state for a known address
    print("\n### TEST 4: spotClearinghouseState (sample address) ###")
    # Using the address that had JEFF and LATINA tokens
    test_address = "0xbfdf0afc2c4777ce97618e4d626f92659011b5a6"
    spot_state = test_endpoint("spotClearinghouseState", {"user": test_address})

    if spot_state and "balances" in spot_state:
        print(f"\nFound {len(spot_state['balances'])} token balances")
        for balance in spot_state['balances'][:3]:
            print(f"\n  Token: {balance.get('coin')}")
            print(f"    Fields: {list(balance.keys())}")
            print(f"    Data: {balance}")

    # Test 5: Try to find token price info
    print("\n### TEST 5: Check for token/universe info ###")
    test_endpoint("meta")

    # Test 6: Try metaAndAssetCtxs
    print("\n### TEST 6: metaAndAssetCtxs ###")
    test_endpoint("metaAndAssetCtxs")

    # Test 7: Check if there's a tokens list endpoint
    print("\n### TEST 7: Trying 'tokens' ###")
    test_endpoint("tokens")

    # Test 8: Check if there's a spot tokens endpoint
    print("\n### TEST 8: Trying 'spotTokens' ###")
    test_endpoint("spotTokens")

    print("\n" + "=" * 70)
    print("INVESTIGATION COMPLETE")
    print("=" * 70)
    print("\n💡 Look for:")
    print("  1. Any endpoint that returns spot token prices")
    print("  2. Fields in spotClearinghouseState that might have price info")
    print("  3. Token metadata that includes current market price")
    print("  4. A universe/token list with pricing data")


if __name__ == "__main__":
    main()