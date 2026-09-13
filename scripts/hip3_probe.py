import requests
r = requests.post("https://api.hyperliquid.xyz/info",
                  json={"type": "metaAndAssetCtxs"}).json()
marks = {a["name"]: float(c["markPx"]) for a, c in zip(r[0]["universe"], r[1])}

TARGET_USD = 50_000
for c in ["HYPE", "BTC", "ETH", "ZEC", "SOL", "XRP", "PUMP", "FARTCOIN"]:
    print(f'    "{c}": {TARGET_USD / marks[c]:.6g},')