#!/usr/bin/env python3
"""Read-only audit: orders.status='active' vs Hyperliquid twapHistory."""
import json, sqlite3, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
import requests

DB = "data/twap.db"
SLEEP = 10.0          # ~6 calls/min -> stays well clear of the collector's budget
SIDE = {"BUY": "B", "SELL": "A"}

def ms(iso):
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = con.execute("""
    SELECT id, address, symbol, side, size, duration_minutes, first_seen_at, product_type
    FROM orders WHERE status = 'active'
""").fetchall()
by_addr = defaultdict(list)
for r in rows:
    by_addr[r[1]].append(r)
print(f"{len(rows)} active rows across {len(by_addr)} addresses, ~{len(by_addr)*SLEEP/60:.0f} min")

results, unmatched_types = Counter(), Counter()
per_row, n_entries, no_id = [], [], []

for i, (addr, orows) in enumerate(by_addr.items(), 1):
    try:
        r = requests.post("https://api.hyperliquid.xyz/info",
                          json={"type": "twapHistory", "user": addr}, timeout=15)
        r.raise_for_status()
        hist = r.json()
    except Exception as e:
        print(f"  {addr} FAILED: {e}")
        results["fetch_failed"] += len(orows)
        time.sleep(SLEEP)
        continue

    n_entries.append(len(hist))
    latest = {}                                   # collapse event log -> last event per twapId
    for e in hist:
        t = e.get("twapId")
        if t is None:
            no_id.append((addr, e))
            continue
        if t not in latest or e["time"] > latest[t]["time"]:
            latest[t] = e

    for (oid, _, sym, side, size, dur, fs, ptype) in orows:
        fs_ms = ms(fs)
        cands = [e for e in latest.values()
                 if e["state"]["coin"] == sym
                 and e["state"]["side"] == SIDE.get(side)
                 and int(e["state"]["minutes"]) == dur
                 and abs(float(e["state"]["sz"]) - size) <= 1e-9 * max(1.0, abs(size))
                 and e["state"]["timestamp"] <= fs_ms + 5000]
        if not cands:
            results["unmatched"] += 1
            unmatched_types[ptype] += 1
            per_row.append({"id": oid, "symbol": sym, "result": "UNMATCHED"})
            continue
        e = max(cands, key=lambda x: x["state"]["timestamp"])
        st = e["status"]["status"]
        results["still_open" if st == "activated" else st] += 1
        per_row.append({"id": oid, "symbol": sym, "result": st, "twapId": e["twapId"],
                        "executedSz": e["state"]["executedSz"], "sz": e["state"]["sz"],
                        "ended_at": e["time"] if st != "activated" else None})

    if i % 20 == 0:
        print(f"  {i}/{len(by_addr)} done")
    time.sleep(SLEEP)

out = f"audit_active_{datetime.now(timezone.utc):%Y%m%dT%H%M}.json"
json.dump(per_row, open(out, "w"), indent=2)

print("\n=== DB 'active' rows, per exchange ===")
for k, n in results.most_common():
    print(f"  {k:14s} {n:4d}  ({100*n/len(rows):.1f}%)")
if unmatched_types:
    print("unmatched by product_type:", dict(unmatched_types))
if n_entries:
    s = sorted(n_entries)
    w = sum(20 + n // 20 for n in n_entries)
    print(f"\nentries/address: min {s[0]}, median {s[len(s)//2]}, max {s[-1]}")
    print(f"weight for one full round: {w}  (-> {w/60:.0f}/min at a 60-min cadence)")
print(f"per-row detail -> {out}")
print(f"\nentries WITHOUT twapId: {len(no_id)}")
for a, e in no_id[:3]:
    print(" ", a, json.dumps(e))
