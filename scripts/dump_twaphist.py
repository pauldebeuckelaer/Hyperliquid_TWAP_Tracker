import json, sys, requests
from collections import Counter

user = sys.argv[1]
r = requests.post("https://api.hyperliquid.xyz/info",
                  json={"type": "twapHistory", "user": user}, timeout=15)
r.raise_for_status()
data = r.json()
out = f"twaphist_{user[:10]}.json"
json.dump(data, open(out, "w"), indent=2)

paths, statuses = Counter(), Counter()
def walk(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items():
            paths[f"{p}.{k}"] += 1
            if k == "status" and isinstance(v, str):
                statuses[v] += 1
            walk(v, f"{p}.{k}")
    elif isinstance(o, list):
        for x in o:
            walk(x, f"{p}[]")
walk(data)

print(f"{len(data)} entries -> {out}")
print("key paths:")
for k, n in sorted(paths.items()):
    print(f"  {k}  ({n})")
print("status values:", dict(statuses))
