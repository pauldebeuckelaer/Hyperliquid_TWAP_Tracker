#!/usr/bin/env python3
"""
Diagnostic: Check why address isn't classified as whale
"""
import json

# Load the rank data we just got
RANK_DATA = {
    'HYPE': 33,
    'USDC': 28,
    'LATINA': 760,
    'UFART': 2,
    'UPUMP': 3,
    'stakedHYPE': 1064
}

# Your current rank tiers
RANK_TIERS = {
    'mega_whale': (1, 100),  # Top 100 holders
    'whale': (101, 500),  # Top 500 holders
    'dolphin': (501, 2000),  # Top 2000 holders
    'fish': (2001, 10000),  # Top 10k holders
    'shrimp': (10001, 999999)  # Everyone else
}


def classify_by_rank(hype_rank):
    """Classify based on HYPE holder rank"""
    if hype_rank is None:
        return 'unknown', False

    # Find tier based on rank
    for tier, (min_rank, max_rank) in RANK_TIERS.items():
        if min_rank <= hype_rank <= max_rank:
            is_whale = (tier in ['mega_whale', 'whale'])
            return tier, is_whale

    # Default to shrimp
    return 'shrimp', False


def main():
    print("=" * 80)
    print("WHALE CLASSIFICATION DIAGNOSTIC")
    print("=" * 80)

    address = "0xf545003323da8419ce95dd4137ec90577d420ea1"
    hype_rank = RANK_DATA.get('HYPE')

    print(f"\nAddress: {address}")
    print(f"HYPE Rank: #{hype_rank}")
    print(f"USDC Rank: #{RANK_DATA.get('USDC')}")

    classification, is_whale = classify_by_rank(hype_rank)

    print("\n" + "=" * 80)
    print("CLASSIFICATION RESULT:")
    print("=" * 80)
    print(f"Classification: {classification}")
    print(f"Is Whale: {is_whale}")
    print(f"Tier Range: {RANK_TIERS.get(classification, 'N/A')}")

    # Show why this classification
    print("\n" + "=" * 80)
    print("TIER BREAKDOWN:")
    print("=" * 80)
    for tier, (min_rank, max_rank) in RANK_TIERS.items():
        in_tier = "✅ ADDRESS HERE" if tier == classification else ""
        emoji = {'mega_whale': '🐋🐋', 'whale': '🐋', 'dolphin': '🐬',
                 'fish': '🐟', 'shrimp': '🦐'}.get(tier, '')
        print(f"{emoji} {tier:12}: Ranks #{min_rank:6} - #{max_rank:6} {in_tier}")

    # Check against your actual data file
    print("\n" + "=" * 80)
    print("CHECKING YOUR address_ranks.json FILE:")
    print("=" * 80)

    try:
        with open('address_ranks.json', 'r') as f:
            data = json.load(f)

        if address in data:
            addr_data = data[address]
            print(f"✅ Address found in database")
            print(f"   Classification: {addr_data.get('classification')}")
            print(f"   Is Whale: {addr_data.get('is_whale')}")
            print(f"   HYPE Rank: {addr_data.get('hype_rank')}")
            print(f"   Last Rank Check: {addr_data.get('last_rank_check')}")
            print(f"   TWAP Count: {addr_data.get('twap_count')}")

            if addr_data.get('hype_rank') is None:
                print("\n   ⚠️ PROBLEM: Rank is None - fetch_and_update_rank() never called!")
            elif addr_data.get('classification') == 'unknown':
                print("\n   ⚠️ PROBLEM: Classification is 'unknown' despite having rank")
            elif not addr_data.get('is_whale'):
                print("\n   ⚠️ PROBLEM: Not marked as whale despite high rank")
        else:
            print(f"❌ Address NOT found in database")
            print("   → This address has never been added to the tracker")
            print("   → Call tracker.add_address() and tracker.fetch_and_update_rank()")

    except FileNotFoundError:
        print("❌ File 'address_ranks.json' not found")
        print("   → The tracker hasn't saved any data yet")
    except Exception as e:
        print(f"❌ Error reading file: {e}")

    print("\n" + "=" * 80)
    print("CONCLUSION:")
    print("=" * 80)

    if is_whale:
        print(f"✅ With rank #{hype_rank}, this address SHOULD be classified as: {classification.upper()}")
        print("✅ The classification logic is CORRECT")
        print("\n💡 If it's not showing as whale in your system, the issue is:")
        print("   1. fetch_and_update_rank() was never called for this address")
        print("   2. The rank data hasn't been saved to address_ranks.json")
        print("   3. The TWAP tracker is using a different/old classification")
    else:
        print(f"❌ Something is wrong - rank #{hype_rank} should be whale tier!")


if __name__ == "__main__":
    main()