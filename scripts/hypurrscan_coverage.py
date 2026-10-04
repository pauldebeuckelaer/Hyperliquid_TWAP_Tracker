#!/usr/bin/env python3
"""Hypurrscan coverage: chain truth (twapHistory dump) vs orders table for one UTC day.
Counts orders that ran >=10 min and traded. Read-only.
Usage: scripts/hypurrscan_coverage.py 2026-10-01 data/twap_history_dump/twap_history_20261002T0330.jsonl.gz
Use the dump from the NEXT day's 03:30 run."""
import gzip, json, sqlite3, collections, sys
from datetime import datetime, timezone

day, dump = sys.argv[1], sys.argv[2]
LO = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)
HI = LO + 86_400_000
db = sqlite3.connect("file:data/twap.db?mode=ro", uri=True)
have = set(db.execute("SELECT lower(address), placed_at_ms FROM orders WHERE placed_at_ms BETWEEN ? AND ?",
                      (LO, HI)).fetchall())
orders = {}
with gzip.open(dump, "rt") as f:
    for line in f:
        rec = json.loads(line)
        addr = rec["address"].lower()
        for e in rec["entries"]:
            s = e.get("state", {}); ts = s.get("timestamp", 0)
            if not (LO <= ts < HI):
                continue
            o = orders.setdefault((addr, e.get("twapId") or ts),
                                  {"addr": addr, "ts": ts, "coin": s.get("coin"), "end": None, "st": None, "ntl": 0.0})
            o["ts"] = min(o["ts"], ts)
            st = e.get("status", {}).get("status")
            if st not in ("activated", "waitingForTrigger"):
                o["end"], o["st"], o["ntl"] = e.get("time"), st, float(s.get("executedNtl", 0))

hours = collections.defaultdict(lambda: [0, 0, 0.0, 0.0])   # total, missing, total_ntl, missing_ntl
wallets = collections.defaultdict(lambda: [0, 0])            # total, missing
for o in orders.values():
    if not o["end"] or o["ntl"] <= 0 or (o["end"] - o["ts"] / 1000) / 60 < 10:
        continue
    h = datetime.fromtimestamp(o["ts"] / 1000, timezone.utc).strftime("%H")
    miss = (o["addr"], o["ts"]) not in have
    hours[h][0] += 1; hours[h][2] += o["ntl"]; wallets[o["addr"]][0] += 1
    if miss:
        hours[h][1] += 1; hours[h][3] += o["ntl"]; wallets[o["addr"]][1] += 1

print(f"{day}  hour  total  miss       total_ntl     missed_ntl")
for h in sorted(hours):
    t, m, tn, mn = hours[h]
    print(f"      {h}:00  {t:5}  {m:4}  ${tn:>14,.0f}  ${mn:>12,.0f}")
T = sum(v[0] for v in hours.values()); M = sum(v[1] for v in hours.values())
TN = sum(v[2] for v in hours.values()); MN = sum(v[3] for v in hours.values())
print(f"TOTAL by count: {M}/{T} = {100*M/max(T,1):.1f}%   by notional: ${MN:,.0f}/${TN:,.0f} = {100*MN/max(TN,1):.1f}%")
print("\nwallets with misses (missed/total that day):")
for a, (t, m) in sorted(wallets.items(), key=lambda kv: -kv[1][1]):
    if m:
        print(f"  {a[:12]}  {m}/{t}")
