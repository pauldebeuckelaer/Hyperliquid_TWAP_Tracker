import json, urllib.request, collections, datetime
U  = "0xf33b8b3c2559930ed028ef2591d85be57620d163"
T0 = 1795240800000   # Sat 2026-11-21 06:00 UTC
T1 = 1795269600000   # Sat 2026-11-21 14:00 UTC
r = urllib.request.Request("https://api.hyperliquid.xyz/info",
    data=json.dumps({"type":"userFillsByTime","user":U,"startTime":T0,"endTime":T1}).encode(),
    headers={"Content-Type":"application/json"})
f = json.load(urllib.request.urlopen(r, timeout=20))
ts = lambda ms: datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc).strftime("%H:%M:%S")
agg = collections.defaultdict(lambda:[0,0.0,0.0])
for x in f:
    k = (x["coin"], x["dir"], "taker" if x["crossed"] else "maker",
         "LIQ" if x.get("liquidation") else "vol")
    n = float(x["sz"])*float(x["px"])
    agg[k][0]+=1; agg[k][1]+=n; agg[k][2]+=float(x.get("closedPnl",0))
print("FILLS", len(f), "(2000 = capped)")
for k,v in sorted(agg.items(), key=lambda kv:-kv[1][1])[:20]:
    print(" ", k, "fills", v[0], "ntl_musd", round(v[1]/1e6,2), "closedPnl_k", round(v[2]/1e3,1))
if f:
    print("first", ts(f[0]["time"]), "| last", ts(f[-1]["time"]))