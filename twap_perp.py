#!/usr/bin/env python3
"""
COMPLETE API ENDPOINT DISCOVERY
================================
Brute force discovery of ALL endpoints
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "https://api.hypurrscan.io"


def test_url(endpoint):
    """Test if URL returns data"""
    try:
        response = requests.get(f"{BASE_URL}/{endpoint}", timeout=5)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None


print("=" * 70)
print("COMPREHENSIVE ENDPOINT DISCOVERY")
print("=" * 70)
print(f"Time: {datetime.now()}")
print("=" * 70)

found_endpoints = {}

# ==================== SECTION 1: BASE PATHS ====================
print("\n[1] Testing base paths...")
base_paths = [
    '', 'api', 'v1', 'v2', 'v3', 'public', 'data',
    'twap', 'twaps', 'orders', 'order', 'trades', 'trade',
    'perp', 'perps', 'perpetual', 'perpetuals', 'futures',
    'spot', 'spots', 'derivatives', 'swaps', 'swap',
    'market', 'markets', 'assets', 'asset', 'tokens', 'token',
    'symbols', 'symbol', 'pairs', 'pair',
    'live', 'active', 'current', 'recent',
]

for path in base_paths:
    data = test_url(path)
    if data:
        found_endpoints[path] = data
        print(f"  ✓ /{path} - {type(data).__name__}")

        if isinstance(data, dict):
            keys = list(data.keys())
            print(f"      Keys: {keys[:5]}")
        elif isinstance(data, list):
            print(f"      {len(data)} items")

# ==================== SECTION 2: HYPE SPECIFIC ====================
print("\n[2] Testing HYPE-specific patterns...")
hype_patterns = [
    # Direct
    'HYPE', 'hype',

    # With verbs
    'get/HYPE', 'fetch/HYPE', 'query/HYPE',

    # With categories
    'token/HYPE', 'asset/HYPE', 'coin/HYPE', 'symbol/HYPE',
    'market/HYPE', 'markets/HYPE',

    # TWAP patterns
    'HYPE/twap', 'HYPE/twaps', 'HYPE/orders',
    'HYPE/orders/twap', 'HYPE/trades',

    # Market type combinations
    'HYPE/perp', 'HYPE/spot', 'HYPE/perpetual',
    'HYPE/perp/twap', 'HYPE/spot/twap',
    'HYPE/perp/orders', 'HYPE/spot/orders',
    'HYPE/perpetual/twap', 'HYPE/perpetual/orders',

    # Reverse patterns
    'perp/HYPE', 'spot/HYPE', 'perpetual/HYPE',
    'perp/HYPE/twap', 'spot/HYPE/twap',
    'perp/orders/HYPE', 'spot/orders/HYPE',

    # With ID
    'HYPE/10107', 'HYPE/159',
    '10107/twap', '159/twap',

    # Active/live variations
    'HYPE/active', 'HYPE/live', 'HYPE/current',
    'active/HYPE', 'live/HYPE', 'current/HYPE',
    'active/twap/HYPE', 'live/twap/HYPE',
]

for pattern in hype_patterns:
    data = test_url(pattern)
    if data:
        found_endpoints[pattern] = data
        print(f"  ✓ /{pattern}")

        # Check for asset IDs
        if isinstance(data, list) and len(data) > 0:
            asset_ids = set()
            for item in data:
                if isinstance(item, dict):
                    aid = item.get('action', {}).get('twap', {}).get('a')
                    if aid:
                        asset_ids.add(aid)

            if asset_ids:
                print(f"      Asset IDs: {sorted(asset_ids)}")
                if 159 in asset_ids:
                    print(f"      🎯🎯🎯 CONTAINS PERP!")

# ==================== SECTION 3: ASSET ID PATTERNS ====================
print("\n[3] Testing asset ID patterns...")
asset_id_patterns = [
    # Direct asset IDs
    '159', '10107',
    '0', '1', '2', '5',  # BTC, ETH, etc perps

    # With categories
    'asset/159', 'asset/10107',
    'token/159', 'token/10107',
    'id/159', 'id/10107',

    # With actions
    'twap/id/159', 'twap/id/10107',
    'orders/id/159', 'orders/id/10107',
    'twap/asset/159', 'twap/asset/10107',
]

for pattern in asset_id_patterns:
    data = test_url(pattern)
    if data:
        found_endpoints[pattern] = data
        print(f"  ✓ /{pattern}")

# ==================== SECTION 4: ALL/GLOBAL PATTERNS ====================
print("\n[4] Testing all/global patterns...")
global_patterns = [
    'all', 'all/twap', 'all/orders', 'all/twaps',
    'global', 'global/twap', 'global/orders',
    'list', 'list/twap', 'list/orders',
    'twap/list', 'orders/list',
    'twap/active', 'orders/active',
    'live/twap', 'live/orders',
    'current/twap', 'current/orders',
]

for pattern in global_patterns:
    data = test_url(pattern)
    if data:
        found_endpoints[pattern] = data
        print(f"  ✓ /{pattern}")

# ==================== SECTION 5: METADATA ====================
print("\n[5] Testing metadata/documentation endpoints...")
meta_patterns = [
    'swagger', 'swagger.json', 'swagger/ui',
    'openapi', 'openapi.json',
    'docs', 'documentation', 'api-docs',
    'schema', 'metadata', 'meta',
    'config', 'configuration',
    'info', 'about', 'version',
    'health', 'status', 'ping',
    'endpoints', 'routes', 'paths',
]

for pattern in meta_patterns:
    data = test_url(pattern)
    if data:
        found_endpoints[pattern] = data
        print(f"  ✓ /{pattern}")

        # Save metadata files
        filename = f"metadata_{pattern.replace('/', '_')}.json"
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"      Saved to {filename}")

# ==================== SECTION 6: REVERSE ENGINEER FROM UI ====================
print("\n[6] Testing patterns that might match the UI...")
ui_patterns = [
    # The UI shows TWAPs, so try variations
    'ui/twap', 'frontend/twap', 'web/twap',
    'dashboard/twap', 'app/twap',

    # Multiple market types in one call
    'twap/HYPE/all', 'orders/HYPE/all',
    'HYPE/all/twap', 'HYPE/all/orders',

    # Aggregated data
    'aggregate/HYPE', 'combined/HYPE',
    'summary/HYPE', 'overview/HYPE',
]

for pattern in ui_patterns:
    data = test_url(pattern)
    if data:
        found_endpoints[pattern] = data
        print(f"  ✓ /{pattern}")

# ==================== FINAL SUMMARY ====================
print("\n" + "=" * 70)
print("DISCOVERY COMPLETE")
print("=" * 70)
print(f"Found {len(found_endpoints)} working endpoints\n")

# Save all findings
with open('all_endpoints_found.json', 'w') as f:
    # Convert to serializable format
    serializable = {}
    for key, val in found_endpoints.items():
        if isinstance(val, (dict, list, str, int, float, bool, type(None))):
            serializable[key] = val
        else:
            serializable[key] = str(val)
    json.dump(serializable, f, indent=2)

print("Saved complete results to: all_endpoints_found.json\n")

# Show which ones have TWAP orders
print("Endpoints with TWAP orders:")
for endpoint, data in found_endpoints.items():
    if isinstance(data, list) and len(data) > 0:
        if isinstance(data[0], dict) and 'action' in data[0]:
            print(f"  /{endpoint}")

            # Check asset IDs
            asset_ids = set()
            for item in data:
                aid = item.get('action', {}).get('twap', {}).get('a')
                if aid:
                    asset_ids.add(aid)

            if asset_ids:
                print(f"    Asset IDs: {sorted(asset_ids)}")
                if 159 in asset_ids:
                    print(f"    🎯 CONTAINS PERP!")

print("\n" + "=" * 70)