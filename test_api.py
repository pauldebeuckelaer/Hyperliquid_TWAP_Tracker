import requests
import json

# Check a whale with active positions
address = "0xaf0fdd39e5d92499b0ed9f68693da99c0ec1e92e"

payload = {"type": "clearinghouseState", "user": address}
response = requests.post("https://api.hyperliquid.xyz/info", json=payload)
state = response.json()

print("MARGIN SUMMARY:")
print(json.dumps(state.get('marginSummary'), indent=2))

print("\nCROSS MARGIN SUMMARY:")
print(json.dumps(state.get('crossMarginSummary'), indent=2))

print("\nPOSITIONS (first 3):")
for pos in state.get('assetPositions', [])[:3]:
    print(json.dumps(pos, indent=2))