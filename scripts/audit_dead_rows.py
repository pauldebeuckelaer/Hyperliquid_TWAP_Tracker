#!/usr/bin/env python3
"""Break down the dead-on-exchange rows from the active audit. Read-only, no API."""
import json, sqlite3, statistics as st
from collections import Counter
from datetime import datetime, timezone

JSON = "audit_active_20260927T1629.json"
AUDIT_T = datetime(2026, 9, 27, 16, 29, tzinfo=timezone.utc).timestamp()
DEAD = {"terminated", "finished", "error", "stopped"}

rows = json.load(open(JSON))
con = sqlite3.connect("file:data/twap.db?mode=ro", uri=True)
db = {r[0]: r[1:] for r in con.execute(
    "SELECT id, first_seen_at, duration_minutes, size FROM orders")}

def fs_sec(iso):
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()

dead = [r for r in rows if r["result"] in DEAD]
print(f"dead rows: {len(dead)}\n")

# 1) How long has the DB been wrong?  (audit time - exchange end time)
print("=== hours since exchange ended it (DB still says active) ===")
for s in sorted(DEAD):
    ages = [(AUDIT_T - r["ended_at"]) / 3600 for r in dead if r["result"] == s]
    if ages:
        print(f"  {s:11s} n={len(ages):3d}  min {min(ages):6.1f}  "
              f"median {st.median(ages):6.1f}  max {max(ages):7.1f}")

# 2) How much would cleanup fabricate?  (1 - executed/size)
print("\n=== executed fraction at end (cleanup would write 100%) ===")
for s in sorted(DEAD):
    fr = [float(r["executedSz"]) / float(r["sz"]) for r in dead
          if r["result"] == s and float(r["sz"]) > 0]
    if fr:
        b = Counter("0%" if f == 0 else "<50%" if f < .5 else "<100%" if f < .999 else "~100%"
                    for f in fr)
        print(f"  {s:11s} median {st.median(fr):5.1%}  {dict(b)}")

# 3) Would cleanup even have caught them yet?  (first_seen + duration vs audit time)
print("\n=== past their nominal end (first_seen + duration) at audit time? ===")
for s in sorted(DEAD) + ["still_open"]:
    sub = [r for r in rows if r["result"] == s and r["id"] in db]
    past = sum(1 for r in sub
               if fs_sec(db[r["id"]][0]) + db[r["id"]][1] * 60 < AUDIT_T)
    if sub:
        print(f"  {s:11s} {past:3d} of {len(sub):3d} past nominal end")

# 4) Which coins?
print("\n=== dead rows by coin (top 10) ===")
for c, n in Counter(r["symbol"] for r in dead).most_common(10):
    print(f"  {c:12s} {n}")
