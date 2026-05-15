import requests, json
BASE = "https://api.hyperliquid.xyz/info"

def post(p):
    r = requests.post(BASE, json=p)
    return r.status_code, (r.json() if r.status_code == 200 else r.text[:200])

MM = "0xc926ddba8b7617dbc65712f20cf8e1b58b8598d3"
WHALE = "0x1050cf788aa24bb06e6da6dc789dfd49424be21e"  # top non-MM

# 1. Find the outcome-state endpoint
print("=== Position state probes ===")
for t in [
    "outcomeClearinghouseState",
    "userOutcomeState",
    "outcomeState",
    "userOutcomes",
    "outcomePositions",
    "spotClearinghouseState",   # maybe folded with spot?
]:
    s, body = post({"type": t, "user": MM})
    print(f"  {t:32s} -> {s}  {str(body)[:150]}")

# 2. Confirm MM vs directional via userFills net position
print("\n=== Net position check ===")
for label, addr in [("MM-suspect", MM), ("Whale-suspect", WHALE)]:
    s, fills = post({"type": "userFills", "user": addr})
    if not isinstance(fills, list):
        continue
    outcome_fills = [f for f in fills if str(f.get("coin","")).startswith("#")]
    nets = {}
    for f in outcome_fills:
        coin = f["coin"]
        sz = float(f["sz"]) * (1 if f["side"] == "B" else -1)
        nets[coin] = nets.get(coin, 0) + sz
    print(f"\n  {label} {addr[:10]}... ({len(outcome_fills)} outcome fills)")
    for coin, net in nets.items():
        print(f"    {coin}: net {net:+.1f} contracts")

# 3. Spot-style state — does it surface outcome balances?
s, body = post({"type": "spotClearinghouseState", "user": WHALE})
print(f"\n=== spotClearinghouseState for whale ===\n{json.dumps(body, indent=2)[:800]}")