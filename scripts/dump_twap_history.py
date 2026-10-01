#!/usr/bin/env python3
"""Snapshot twapHistory for heavy TWAP wallets before entries roll off the 2000-entry cap.
Usage: python3 scripts/dump_twap_history.py [min_orders]   (default 1000)"""
import sqlite3, requests, json, time, sys
from pathlib import Path
from datetime import datetime, timezone

URL = "https://api.hyperliquid.xyz/info"
min_orders = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
out = Path("data/twap_history_dump"); out.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")

con = sqlite3.connect("file:data/twap.db?mode=ro", uri=True)
addrs = [r[0] for r in con.execute(
    "SELECT address FROM orders GROUP BY address HAVING COUNT(*) > ? ORDER BY COUNT(*) DESC",
    (min_orders,))]
con.close()

path = out / f"twap_history_{stamp}.jsonl"
ok = fail = 0
with path.open("w") as fh:
    for i, a in enumerate(addrs, 1):
        try:
            h = requests.post(URL, json={"type": "twapHistory", "user": a}, timeout=30).json() or []
            ok += 1
        except Exception as e:
            print(f"  FAIL {a}: {e}"); h = None; fail += 1
        fh.write(json.dumps({"address": a, "fetched_at": stamp, "entries": h}) + "\n")
        if i % 10 == 0:
            print(f"  {i}/{len(addrs)}")
        time.sleep(3.0)   # shares the box IP's rate limit with twap.service
print(f"{ok} ok, {fail} failed -> {path}")