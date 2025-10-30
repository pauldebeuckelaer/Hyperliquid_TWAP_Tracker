#!/usr/bin/env python3
"""
Production-ready diagnostic script with comprehensive testing
"""
import logging
from api_client.hyperliquid_client import HyperliquidClient
import json

logging.basicConfig(
    level=logging.INFO,  # Use INFO in production, DEBUG for troubleshooting
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_token_pricing():
    """Comprehensive token pricing test"""

    print("=" * 80)
    print("HYPERLIQUID TOKEN PRICING - PRODUCTION TEST")
    print("=" * 80)

    client = HyperliquidClient()

    # Test cases organized by category
    test_cases = {
        "Stablecoins": ["USDC", "USDT", "FEUSD"],
        "Major Perps": ["BTC", "ETH", "SOL", "HYPE", "PURR"],
        "Scaled Perps": ["kBONK", "kPEPE"],
        "Renamed Perps": ["FARTCOIN"],
        "Bridged - Should Work": ["UBTC", "UETH", "USOL", "UPUMP"],
        "Bridged - k-prefix": ["UBONK", "UPEPE"],
        "Bridged - Fuzzy": ["UFART"],
        "Spot Only": ["LATINA", "NEKO", "SENT", "STAR"],
        "Illiquid/Delisted": ["LICKO", "WOW", "HYENA"]
    }

    results = {}

    for category, tokens in test_cases.items():
        print(f"\n{'=' * 80}")
        print(f"{category}")
        print("=" * 80)

        results[category] = {}

        for token in tokens:
            price = client.get_token_price(token)

            if price:
                results[category][token] = {"status": "✅", "price": price}
                print(f"✅ {token:15s} ${price:,.6f}")
            else:
                results[category][token] = {"status": "❌", "price": None}
                print(f"❌ {token:15s} NO PRICE")

    # Cache statistics
    print(f"\n{'=' * 80}")
    print("CACHE STATISTICS")
    print("=" * 80)

    cache_stats = client.get_cache_stats()
    print(json.dumps(cache_stats, indent=2))

    # Summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print("=" * 80)

    total_tests = sum(len(tokens) for tokens in test_cases.values())
    successful = sum(
        1 for category in results.values()
        for result in category.values()
        if result["status"] == "✅"
    )

    print(f"Total tests: {total_tests}")
    print(f"Successful: {successful}")
    print(f"Failed: {total_tests - successful}")
    print(f"Success rate: {successful / total_tests * 100:.1f}%")

    return results


if __name__ == "__main__":
    results = test_token_pricing()