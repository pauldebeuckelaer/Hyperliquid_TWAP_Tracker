import json, sys, urllib.request
U = sys.argv[1]
r = urllib.request.Request("https://api.hyperliquid.xyz/info",
    data=json.dumps({"type":"clearinghouseState","user":U}).encode(),
    headers={"Content-Type":"application/json"})
d = json.load(urllib.request.urlopen(r, timeout=15))
m = d["marginSummary"]
print("accountValue", round(float(m["accountValue"])/1e6,2), "M | totalNtlPos",
      round(float(m["totalNtlPos"])/1e6,2), "M | marginUsed",
      round(float(m["totalMarginUsed"])/1e6,2), "M")
ps = [p["position"] for p in d["assetPositions"]]
print("positions", len(ps))
for p in sorted(ps, key=lambda p: -float(p["positionValue"]))[:10]:
    print(p["coin"], "szi", p["szi"], "entry", p["entryPx"],
          "val_musd", round(float(p["positionValue"])/1e6,2),
          "upnl_k", round(float(p["unrealizedPnl"])/1e3,1),
          "liqPx", p.get("liquidationPx"),
          p["leverage"]["type"], p["leverage"]["value"])