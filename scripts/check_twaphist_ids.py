#!/usr/bin/env python3
import sqlite3, time, requests
from datetime import datetime, timezone
f = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%Y-%m-%d %H:%M")
con = sqlite3.connect("file:data/twap.db?mode=ro", uri=True)
addrs = [r[0] for r in con.execute("""SELECT address FROM orders WHERE status='active'
    GROUP BY address ORDER BY COUNT(*) DESC LIMIT 5""")]
for a in addrs:
    h = requests.post("https://api.hyperliquid.xyz/info",
                      json={"type": "twapHistory", "user": a}, timeout=15).json()
    ids   = [e["time"] for e in h if "twapId" in e]
    noids = [e["time"] for e in h if "twapId" not in e]
    print(f"{a}  n={len(h)}  first={f(h[0]['time'])}  last={f(h[-1]['time'])}")
    if ids:   print(f"   with id: {len(ids):5d}  {f(min(ids))} -> {f(max(ids))}")
    if noids: print(f"   no id:   {len(noids):5d}  {f(min(noids))} -> {f(max(noids))}")
    time.sleep(10)
