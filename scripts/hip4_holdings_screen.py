#!/usr/bin/env python3
"""Screen tracked addresses for HIP-4 outcome token balances.

Outcome tokens appear in spotClearinghouseState as '+<enc>' (enc = 10*outcome+side).
'o<n>' entries are of unknown class and collected separately. Zero-balance entries
are included, so touched-but-flat wallets are visible too.

Resumable: appends to /tmp/hip4_screen.jsonl, skips addresses already done.
"""
import json
import os
import sqlite3
import time

import requests

DB = "data/twap.db"
URL = "https://api.hyperliquid.xyz/info"
OUT = "/tmp/hip4_screen.jsonl"
SLEEP = 0.25

done = set()
if os.path.exists(OUT):
    for line in open(OUT):
        try:
            done.add(json.loads(line)["addr"])
        except Exception:
            pass
print(f"already done: {len(done)}")

con = sqlite3.connect(DB)
addrs = [r[0] for r in con.execute(
    "SELECT DISTINCT address FROM whale_addresses ORDER BY address")]
con.close()
todo = [a for a in addrs if a not in done]
print(f"total {len(addrs)}, remaining {len(todo)}", flush=True)

bad = 0
with open(OUT, "a") as f:
    for i, a in enumerate(todo, 1):
        rows, err = [], None
        for attempt in range(3):
            try:
                r = requests.post(URL,
                                  json={"type": "spotClearinghouseState", "user": a},
                                  timeout=20).json()
                if not isinstance(r, dict):
                    err = f"non-dict: {type(r).__name__}"
                    time.sleep(1.0)
                    continue
                err = None
                for b in r.get("balances") or []:
                    c = str(b.get("coin", ""))
                    if c.startswith("+") or (c.startswith("o") and c[1:].isdigit()):
                        rows.append([c, b.get("total"), b.get("entryNtl")])
                break
            except Exception as e:
                err = repr(e)
                time.sleep(1.0)

        if err:
            bad += 1
            print(f"  BAD {a}: {err}", flush=True)
        f.write(json.dumps({"addr": a, "rows": rows, "err": err}) + "\n")
        f.flush()

        if i % 100 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(SLEEP)

print(f"\ndone. unresolved addresses: {bad}")
