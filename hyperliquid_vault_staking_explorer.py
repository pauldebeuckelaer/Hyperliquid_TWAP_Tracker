#!/usr/bin/env python3
"""
Hyperliquid Vault & Staking Explorer
Tests various API endpoints to find vault and staked amounts for addresses

USAGE: python3 hyperliquid_vault_staking_explorer.py

This script will test multiple API endpoints to discover where vault and
staking data is located in the Hyperliquid API responses.
"""

import requests
import json
from typing import Dict, Any, Optional
import sys

# Hyperliquid API endpoint
API_URL = "https://api.hyperliquid.xyz/info"

# Test addresses from your data (using a few samples)
# You can add your own address here to test with your actual data
TEST_ADDRESSES = [
    "0x525232cb6ed5030d2b052b36bfc0baec96986ee3",  # From your data - has staking
    "0x88b6addc407b2b809443aea0cf54221c6149b5c0",  # Largest account
    "0x4ed4760a09e9adf799528ce457ae5c8876594da5",  # First address
]

# Flag to control verbose output
VERBOSE = True


def query_hyperliquid(request_type: str, data: Dict[str, Any]) -> Optional[Dict]:
    """Query the Hyperliquid API"""
    payload = {
        "type": request_type,
        **data
    }

    try:
        response = requests.post(API_URL, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error querying {request_type}: {e}")
        return None


def search_for_fields(data: Any, search_terms: list, path: str = "") -> list:
    """Recursively search for fields containing vault/staking keywords"""
    findings = []

    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key

            # Check if key contains any search term
            if any(term.lower() in key.lower() for term in search_terms):
                findings.append({
                    "path": current_path,
                    "key": key,
                    "value": value,
                    "type": type(value).__name__
                })

            # Recurse into nested structures
            findings.extend(search_for_fields(value, search_terms, current_path))

    elif isinstance(data, list):
        for i, item in enumerate(data):
            findings.extend(search_for_fields(item, search_terms, f"{path}[{i}]"))

    return findings


def print_findings(findings: list, title: str):
    """Pretty print the findings"""
    if findings:
        print(f"\n🎯 {title}")
        print("-" * 80)
        for finding in findings:
            print(f"  📍 Path: {finding['path']}")
            print(f"     Type: {finding['type']}")
            if isinstance(finding['value'], (str, int, float, bool)):
                print(f"     Value: {finding['value']}")
            elif isinstance(finding['value'], dict):
                print(f"     Value: {{{len(finding['value'])} keys}}")
            elif isinstance(finding['value'], list):
                print(f"     Value: [{len(finding['value'])} items]")
            print()
    else:
        print(f"\n❌ No {title.lower()} found")


def test_clearinghouse_state(address: str):
    """Test clearinghouseState - likely contains vault data"""
    print(f"\n{'=' * 80}")
    print(f"🏦 Testing clearinghouseState (PERPS DATA + possibly VAULT)")
    print(f"{'=' * 80}")

    result = query_hyperliquid("clearinghouseState", {"user": address})
    if result:
        print("✅ Success! Searching for vault/staking fields...")

        # Search for relevant fields
        vault_findings = search_for_fields(result, ["vault", "hlp", "equity"])
        staking_findings = search_for_fields(result, ["stak", "stake", "staked"])

        print_findings(vault_findings, "VAULT-RELATED FIELDS FOUND")
        print_findings(staking_findings, "STAKING-RELATED FIELDS FOUND")

        if VERBOSE:
            print("\n📄 Full Response Keys:")
            if isinstance(result, dict):
                print(f"   {list(result.keys())}")
            print("\n📄 Full Response (first 2000 chars):")
            print(json.dumps(result, indent=2)[:2000] + "...\n")
    else:
        print("❌ Failed to get clearinghouseState")


def test_spot_clearinghouse_state(address: str):
    """Test spotClearinghouseState - likely contains staking data"""
    print(f"\n{'=' * 80}")
    print(f"💎 Testing spotClearinghouseState (SPOT DATA + possibly STAKING)")
    print(f"{'=' * 80}")

    result = query_hyperliquid("spotClearinghouseState", {"user": address})
    if result:
        print("✅ Success! Searching for vault/staking fields...")

        # Search for relevant fields
        vault_findings = search_for_fields(result, ["vault", "hlp"])
        staking_findings = search_for_fields(result, ["stak", "stake", "staked"])

        print_findings(vault_findings, "VAULT-RELATED FIELDS FOUND")
        print_findings(staking_findings, "STAKING-RELATED FIELDS FOUND")

        if VERBOSE:
            print("\n📄 Full Response Keys:")
            if isinstance(result, dict):
                print(f"   {list(result.keys())}")
            print("\n📄 Full Response (first 2000 chars):")
            print(json.dumps(result, indent=2)[:2000] + "...\n")
    else:
        print("❌ Failed to get spotClearinghouseState")


def test_additional_endpoints(address: str):
    """Test additional endpoints that might have vault/staking data"""
    print(f"\n{'=' * 80}")
    print(f"🔍 Testing Additional Endpoints")
    print(f"{'=' * 80}")

    # List of endpoints to try
    endpoints_to_try = [
        ("userFees", {}),
        ("userFunding", {"startTime": 0}),
        ("userNonFundingLedgerUpdates", {"startTime": 0}),
        ("userTokenBalances", {}),  # Might show staked tokens differently
    ]

    for endpoint_name, extra_params in endpoints_to_try:
        print(f"\n📡 Trying '{endpoint_name}'...")
        params = {"user": address, **extra_params}
        result = query_hyperliquid(endpoint_name, params)

        if result:
            print(f"   ✅ Success!")
            vault_findings = search_for_fields(result, ["vault", "hlp"])
            staking_findings = search_for_fields(result, ["stak", "stake", "staked"])

            if vault_findings or staking_findings:
                print_findings(vault_findings, f"VAULT fields in {endpoint_name}")
                print_findings(staking_findings, f"STAKING fields in {endpoint_name}")
        else:
            print(f"   ❌ Failed or no data")


def main():
    """Main execution"""
    print("=" * 80)
    print("🚀 Hyperliquid Vault & Staking API Explorer")
    print("=" * 80)
    print(f"\nTesting {len(TEST_ADDRESSES)} addresses...")
    print("\nLooking for fields containing:")
    print("  🏦 VAULT: vault, hlp, equity")
    print("  💰 STAKING: stak, stake, staked")
    print()

    # Allow user to add custom address
    if len(sys.argv) > 1:
        custom_address = sys.argv[1]
        print(f"📝 Using custom address from command line: {custom_address}")
        TEST_ADDRESSES.insert(0, custom_address)

    for i, address in enumerate(TEST_ADDRESSES, 1):
        print(f"\n\n{'#' * 80}")
        print(f"# ADDRESS {i}/{len(TEST_ADDRESSES)}: {address}")
        print(f"{'#' * 80}")

        # Test main endpoints
        test_clearinghouse_state(address)
        test_spot_clearinghouse_state(address)
        test_additional_endpoints(address)

        # Don't spam the API
        if i < len(TEST_ADDRESSES):
            print("\n⏳ Waiting 2 seconds before next address...")
            import time
            time.sleep(2)

    print("\n\n" + "=" * 80)
    print("✅ Exploration Complete!")
    print("=" * 80)
    print("\n📊 SUMMARY:")
    print("=" * 80)
    print("""
Based on the Hyperliquid API structure, vault and staking data is likely in:

1. clearinghouseState response:
   - Look for: vaultEquity, crossMaintenanceMarginUsed, etc.

2. spotClearinghouseState response:
   - Look for: staking object, stakedAmount, balances array

If you found vault/staking fields above:
✅ Note which endpoint and field path they're in
✅ Share this with me so I can update your collection script

If you didn't find them:
❌ The API might require authentication
❌ Or vault/staking might be in a different API endpoint
❌ Check Hyperliquid's official API docs
    """)
    print("\n💡 TIP: Run with your address as argument:")
    print("   python3 hyperliquid_vault_staking_explorer.py 0xYourAddress")


if __name__ == "__main__":
    main()