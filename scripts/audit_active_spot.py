#!/usr/bin/env python3
"""Read-only audit: SPOT orders.status='active' vs Hyperliquid twapHistory."""
import json, sqlite3, time, statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timezone
import requests

DB = "data/twap.db"
API = "https://api.hyperliquid.xyz/info"
SLEEP = 10.0
SIDE = {"BUY": "B", "SELL": "A"}
WINDOW_MS = 10 * 60 * 1000          # placement at most 10 min before first_seen

def ms(iso):
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)

# spot naming: universe index -> twapHistory coin name, base token -> coin names
meta = requests.post(API, json={"type": "spotMeta"}, timeout=15).json()
tok = {t["index"]: t["name"] for t in meta["tokens"]}
idx_coin, pair_name, by_base = {}, {}, defaultdict(list)
for u in meta["universe"]:
    base, quote = tok[u["tokens"][0]], tok[u["tokens"][1]]
    idx_coin[u["index"]] = u["name"]
    pair_name[u["name"]] = f"{base}/{quote}"
    by_base[base].append(u["name"])

def coins_for(sym):
    if sym.startswith("UNKNOWN_"):
        n = int(sym.split("_")[1]) - 10000
        return [idx_coin.get(n, f"@{n}")]
    return by_base.get(sym, [])

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = con.execute("""
    SELECT id, address, symbol, side, size, duration_minutes, first_seen_at
    FROM orders WHERE status = 'active' AND product_type = 'SPOT'
""").fetchall()
by_addr = defaultdict(list)
for r in rows:
    by_addr[r[1]].append(r)
print(f"{len(rows)} active SPOT rows across {len(by_addr)} addresses, ~{len(by_addr)*SLEEP/60:.0f} min")

results, by_kind, gaps, unk_map = Counter(), Counter(), [], Counter()
per_row = []

for i, (addr, orows) in enumerate(by_addr.items(), 1):
    try:
        r = requests.post(API, json={"type": "twapHistory", "user": addr}, timeout=15)
        r.raise_for_status()
        hist = r.json()
    except Exception as e:
        print(f"  {addr} FAILED: {e}")
        results["fetch_failed"] += len(orows)
        time.sleep(SLEEP)
        continue

    latest = {}
    for e in hist:
        t = e.get("twapId")
        if t is None:
            continue
        if t not in latest or e["time"] > latest[t]["time"]:
            latest[t] = e

    for (oid, _, sym, side, size, dur, fs) in orows:
        kind = "UNKNOWN_*" if sym.startswith("UNKNOWN_") else "named"
        coins = coins_for(sym)
        if not coins:
            res = "no_pair"
            per_row.append({"id": oid, "symbol": sym, "result": res})
        else:
            fs_ms = ms(fs)
            cands = [e for e in latest.values()
                     if e["state"]["coin"] in coins
                     and e["state"]["side"] == SIDE.get(side)
                     and int(e["state"]["minutes"]) == dur
                     and abs(float(e["state"]["sz"]) - size) <= 1e-9 * max(1.0, abs(size))
                     and fs_ms - WINDOW_MS <= e["state"]["timestamp"] <= fs_ms + 5000]
            if not cands:
                res = "unmatched"
                per_row.append({"id": oid, "symbol": sym, "result": res, "tried": coins})
            elif len(cands) > 1:
                res = "ambiguous"
                per_row.append({"id": oid, "symbol": sym, "result": res,
                                "twapIds": [c["twapId"] for c in cands]})
            else:
                e = cands[0]
                st_ = e["status"]["status"]
                res = "still_open" if st_ == "activated" else st_
                gap = (fs_ms - e["state"]["timestamp"]) / 1000
                gaps.append(gap)
                if kind == "UNKNOWN_*":
                    unk_map[(sym, pair_name.get(e["state"]["coin"], e["state"]["coin"]))] += 1
                per_row.append({"id": oid, "symbol": sym, "coin": e["state"]["coin"],
                                "pair": pair_name.get(e["state"]["coin"]), "result": res,
                                "twapId": e["twapId"], "executedSz": e["state"]["executedSz"],
                                "sz": e["state"]["sz"], "gap_s": gap,
                                "ended_at": e["time"] if st_ != "activated" else None})
        results[res] += 1
        by_kind[(kind, res)] += 1

    if i % 10 == 0:
        print(f"  {i}/{len(by_addr)} done")
    time.sleep(SLEEP)

out = f"audit_spot_{datetime.now(timezone.utc):%Y%m%dT%H%M}.json"
json.dump(per_row, open(out, "w"), indent=2)

print("\n=== SPOT 'active' rows, per exchange ===")
for k, n in results.most_common():
    print(f"  {k:18s} {n:4d}  ({100*n/len(rows):.1f}%)")
print("\n=== by symbol kind ===")
for (k, res), n in sorted(by_kind.items()):
    print(f"  {k:10s} {res:18s} {n}")
if gaps:
    print(f"\nplacement -> first_seen gap (s): min {min(gaps):.0f}  "
          f"median {st.median(gaps):.0f}  max {max(gaps):.0f}")
if unk_map:
    print("\nUNKNOWN_* resolved to:")
    for (s, p), n in unk_map.most_common():
        print(f"  {s:16s} -> {p:16s} {n}")
print(f"\nper-row detail -> {out}")
