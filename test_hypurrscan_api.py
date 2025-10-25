#!/usr/bin/env python3
"""
Standalone Hypurrscan API Test Script
======================================

Fetches raw data from Hypurrscan API to understand the response structure.
"""

import requests
import json
from pprint import pprint

# API Base URL
BASE_URL = "https://api.hypurrscan.io"


def fetch_twap_orders(token_or_address: str):
    """
    Fetch TWAP orders for a token or address

    Args:
        token_or_address: Token symbol (e.g., 'HYPE') or wallet address
    """
    url = f"{BASE_URL}/twap/{token_or_address}"

    print(f"\n{'=' * 80}")
    print(f"Fetching TWAP orders from: {url}")
    print(f"{'=' * 80}\n")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        data = response.json()

        print(f"✅ Success! Status Code: {response.status_code}")
        print(f"📊 Response Type: {type(data)}")

        if isinstance(data, list):
            print(f"📋 Number of orders: {len(data)}\n")

            if len(data) > 0:
                print(f"{'=' * 80}")
                print("FIRST ORDER (RAW JSON):")
                print(f"{'=' * 80}")
                print(json.dumps(data[0], indent=2))

                if len(data) > 1:
                    print(f"\n{'=' * 80}")
                    print("SECOND ORDER (RAW JSON):")
                    print(f"{'=' * 80}")
                    print(json.dumps(data[1], indent=2))
        else:
            print(f"\n{'=' * 80}")
            print("FULL RESPONSE (RAW JSON):")
            print(f"{'=' * 80}")
            print(json.dumps(data, indent=2))

        return data

    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching data: {e}")
        return None


def fetch_address_details(address: str):
    """
    Fetch detailed information for a specific address

    Args:
        address: Wallet address
    """
    url = f"{BASE_URL}/addressDetails/{address}"

    print(f"\n{'=' * 80}")
    print(f"Fetching address details from: {url}")
    print(f"{'=' * 80}\n")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        data = response.json()

        print(f"✅ Success! Status Code: {response.status_code}")
        print(f"\n{'=' * 80}")
        print("ADDRESS DETAILS (RAW JSON):")
        print(f"{'=' * 80}")
        print(json.dumps(data, indent=2))

        return data

    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching data: {e}")
        return None


def fetch_address_rank(address: str):
    """
    Fetch rank information for a specific address

    Args:
        address: Wallet address
    """
    url = f"{BASE_URL}/rank/{address}"

    print(f"\n{'=' * 80}")
    print(f"Fetching address rank from: {url}")
    print(f"{'=' * 80}\n")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        data = response.json()

        print(f"✅ Success! Status Code: {response.status_code}")
        print(f"\n{'=' * 80}")
        print("ADDRESS RANK (RAW JSON):")
        print(f"{'=' * 80}")
        print(json.dumps(data, indent=2))

        return data

    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching data: {e}")
        return None


def main():
    """Main test function"""

    print("\n" + "=" * 80)
    print("HYPURRSCAN API TEST SCRIPT")
    print("=" * 80)

    # Test 1: Fetch TWAP orders for HYPE
    print("\n\n🔍 TEST 1: Fetch TWAP orders for HYPE token")
    twap_data = fetch_twap_orders("HYPE")

    # Test 2: Fetch details for a specific address (the whale we've been discussing)
    whale_address = "0xb82359e0fdf3095dd8471df3f3c26aba5b369763"
    print("\n\n🔍 TEST 2: Fetch address details for whale")
    address_data = fetch_address_details(whale_address)

    # Test 3: Fetch rank for the whale address
    print("\n\n🔍 TEST 3: Fetch address rank for whale")
    rank_data = fetch_address_rank(whale_address)

    # Summary
    print("\n\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    if twap_data:
        print(f"✅ TWAP orders fetched successfully")
        if isinstance(twap_data, list):
            print(f"   - Found {len(twap_data)} orders")

            # Analyze order structure
            if len(twap_data) > 0:
                first_order = twap_data[0]
                print(f"\n📋 Order data structure (keys in first order):")
                for key in first_order.keys():
                    print(f"   - {key}: {type(first_order[key]).__name__}")
    else:
        print(f"❌ TWAP orders fetch failed")

    if address_data:
        print(f"\n✅ Address details fetched successfully")
        if isinstance(address_data, dict):
            print(f"   Keys available: {list(address_data.keys())}")
    else:
        print(f"\n❌ Address details fetch failed")

    if rank_data:
        print(f"\n✅ Address rank fetched successfully")
        if isinstance(rank_data, dict):
            print(f"   Keys available: {list(rank_data.keys())}")
    else:
        print(f"\n❌ Address rank fetch failed")

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()