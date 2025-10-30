import requests
import json

# Test HYPE spot
url = "https://api.hyperliquid.xyz/info"
payload = {
    "type": "l2Book",
    "coin": "HYPE"  # Try without /USDC
}

response = requests.post(url, json=payload)
print(f"Test 1 - HYPE (no pair):")
print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")
print()

# Try with @150 index format
payload2 = {
    "type": "l2Book",
    "coin": "@150"  # HYPE's index from spotMeta
}

response2 = requests.post(url, json=payload2)
print(f"Test 2 - @150 (HYPE's index):")
print(f"Status: {response2.status_code}")
print(f"Response: {response2.json()}")